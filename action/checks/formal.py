"""Formal verification check — SymbiYosys (sby) on .sby project files."""

import re
import tempfile
from pathlib import Path

from ._base import check_obj, repo_rel, run_tool


# "SBY v0.66" (release) or "SBY 0.66-12-gabc" (git build)
_VERSION_RE = re.compile(r"sby\s+v?(\d[\d.]+)", re.IGNORECASE)
_DONE_RE = re.compile(r"DONE\s*\((\w+),\s*rc=(\d+)\)", re.IGNORECASE)

# .sby config section parser
_SECTION_RE = re.compile(r"^\[(\w+)\]", re.MULTILINE)

# Engine first token (e.g. "smtbmc yices" → "smtbmc")
_ENGINE_FIRST_RE = re.compile(r"^\s*(\S+)", re.MULTILINE)

# Output counters
_STATUS_FAIL_RE = re.compile(r"(?:Status:\s*FAILED|BMC failed|Assert failed)", re.IGNORECASE)
_COVER_REACHED_RE = re.compile(r"Reached cover statement", re.IGNORECASE)

# "SBY 21:50:22 [control_fsm] ERROR: ..." — sby's own reason for giving up
_SBY_ERROR_RE = re.compile(r"\bERROR:\s*(.+)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# "Writing trace to VCD file: engine_0/trace.vcd" (relative to the sby workdir)
_TRACE_RE = re.compile(r"Writing trace to VCD file:\s*(\S+\.vcd)")
# "Assert failed in control_fsm: control_fsm.v:254.13-254.45 ($assert$...)"
_ASSERT_FAILED_RE = re.compile(r"Assert failed in \S+?:\s*(?:(\S+?):(\d+)(?:\.\d+)?(?:-\d+\.\d+)?)?")
_STEP_RE = re.compile(r"Checking assertions in step (\d+)")
# Counterexamples for small designs are a few KB; past this, link to the run log instead.
_MAX_TRACE_BYTES = 256 * 1024
# assert(...) / assert property (...) statements, outside // comments
_ASSERT_STMT_RE = re.compile(r"^(?:(?!//).)*?\bassert\s*(?:property\s*)?\(", re.MULTILINE)


def _sby_version() -> str | None:
    rc, out, err, _ = run_tool(["sby", "--version"])
    m = _VERSION_RE.search(out + err)
    return m.group(1) if m else None


def _parse_sby_config(text: str) -> dict:
    """Extract mode, depth, and first engine name from a .sby file."""
    sections: dict[str, str] = {}
    boundaries = [(m.group(1).lower(), m.start()) for m in _SECTION_RE.finditer(text)]
    for i, (name, start) in enumerate(boundaries):
        end = boundaries[i + 1][1] if i + 1 < len(boundaries) else len(text)
        # Section body starts after the header line
        body_start = text.index("]", start) + 1
        sections[name] = text[body_start:end]

    options = sections.get("options", "")
    mode_m = re.search(r"^\s*mode\s+(\w+)", options, re.MULTILINE | re.IGNORECASE)
    depth_m = re.search(r"^\s*depth\s+(\d+)", options, re.MULTILINE | re.IGNORECASE)

    engine_body = sections.get("engines", "")
    engine_lines = [l.strip() for l in engine_body.splitlines() if l.strip()]
    engine = engine_lines[0] if engine_lines else None

    return {
        "mode": mode_m.group(1).upper() if mode_m else "PROVE",
        "depth": int(depth_m.group(1)) if depth_m else None,
        "engine": engine,
    }


def _source_files(text: str) -> list[str]:
    """Source paths from the [files] section ("dst src" lines name the source last)."""
    m = re.search(r"^\[files\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    paths = []
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            paths.append(line.split()[-1])
    return paths


def _pick_cwd(sby_file: Path, text: str) -> Path:
    """sby resolves [files] against its working directory, not the .sby file.

    Most projects run `cd formal && sby -f x.sby` (paths like ../rtl/x.v);
    some run it from the repo root (paths like rtl/x.v). Use whichever
    directory makes every listed source exist, preferring the .sby's own.
    """
    sources = _source_files(text)
    candidates = [sby_file.resolve().parent, Path.cwd()]
    for cwd in candidates:
        if all((cwd / Path(src).expanduser()).exists() for src in sources):
            return cwd
    return candidates[0]


def _clean(line: str) -> str:
    return _ANSI_RE.sub("", line).strip()


def _failed_assertions(output: str, by_name: dict[str, str]) -> list[dict]:
    """Each distinct failed assertion with its repo location and the step it failed at."""
    failures: list[dict] = []
    seen: set[str] = set()
    step: int | None = None
    for raw in output.splitlines():
        line = _clean(raw)
        sm = _STEP_RE.search(line)
        if sm:
            step = int(sm.group(1))
        m = _ASSERT_FAILED_RE.search(line)
        if not m:
            continue
        if m.group(1):
            location = f"{by_name.get(Path(m.group(1)).name, m.group(1))}:{m.group(2)}"
        else:
            location = "assertion"
        if location in seen:
            continue
        seen.add(location)
        failures.append({"location": location, "step": step})
    return failures


def _count_asserts(sources: list[Path]) -> int:
    """assert statements in the sources the proof reads: what "proved" covers."""
    total = 0
    for src in sources:
        try:
            if src.suffix.lower() in {".v", ".sv", ".svh", ".vh"}:
                total += len(_ASSERT_STMT_RE.findall(src.read_text(errors="replace")))
        except OSError:
            continue
    return total


def run_formal(sby_file: Path) -> dict:
    version = _sby_version()

    try:
        sby_text = sby_file.read_text(errors="replace")
    except OSError:
        sby_text = ""
    config = _parse_sby_config(sby_text)
    cwd = _pick_cwd(sby_file, sby_text)

    sources = [(cwd / Path(src).expanduser()) for src in _source_files(sby_text)]

    # sby writes output to a named directory; use a temp dir to keep CI clean.
    trace_text: str | None = None
    trace_name: str | None = None
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / sby_file.stem
        rc, out, err, ms = run_tool(
            ["sby", "-f", "-d", str(work_dir), str(sby_file.resolve())],
            cwd=cwd,
            timeout=300,  # formal proofs can be slow
        )
        combined = out + err
        # The counterexample is the reason a proof failed; read it before the dir goes.
        for m in _TRACE_RE.finditer(_ANSI_RE.sub("", combined)):
            path = work_dir / m.group(1)
            if path.name.endswith("_induct.vcd") or not path.is_file():
                continue  # an induction trace starts from an unreachable state: not a counterexample
            if path.stat().st_size <= _MAX_TRACE_BYTES:
                trace_text = path.read_text(errors="replace")
                trace_name = path.name
            break

    # Parse final status from the DONE line (most reliable signal).
    done_m = _DONE_RE.search(combined)
    if done_m:
        done_status = done_m.group(1).upper()  # PASS / FAIL / UNKNOWN / ERROR
    else:
        done_status = "ERROR" if rc != 0 else "PASS"

    status = "PASS" if done_status == "PASS" else ("FAIL" if done_status == "FAIL" else "ERROR")

    cover_traces = len(_COVER_REACHED_RE.findall(combined))
    # sby copies sources into its src/ dir by name; map them back to repo paths.
    by_name = {src.name: repo_rel(src) for src in sources}
    failures = _failed_assertions(combined, by_name)

    violations = []
    seen: set[str] = set()
    for f in failures:
        violations.append({
            "type": "assertion_failed",
            "severity": "error",
            "plain_text": f"{f['location']}: assertion failed" + (f" at step {f['step']}" if f["step"] is not None else ""),
        })
    if status == "FAIL" and not violations:
        for raw in combined.splitlines():
            line = _clean(raw)
            if _STATUS_FAIL_RE.search(line) and line not in seen:
                seen.add(line)
                violations.append({"type": "assertion_failed", "severity": "error", "plain_text": line})
    if status == "ERROR":
        # Say why sby stopped: a missing file, a solver that isn't installed, a syntax error.
        for raw in combined.splitlines():
            m = _SBY_ERROR_RE.search(_clean(raw))
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                violations.append({"type": "sby_error", "severity": "error", "plain_text": m.group(1)})
        if not violations:
            tail = [_clean(l) for l in combined.splitlines() if _clean(l)][-3:]
            if tail:
                violations.append({"type": "sby_error", "severity": "error", "plain_text": " / ".join(tail)[:500]})

    summary: dict = {
        "mode": config["mode"],
        "engine": config["engine"],
    }
    if config["depth"] is not None:
        summary["depth"] = config["depth"]
    asserts = _count_asserts(sources)
    if asserts:
        summary["assertions"] = asserts
    if failures:
        summary["assertions_failed"] = len(failures)
    if cover_traces > 0:
        summary["cover_traces"] = cover_traces
    if done_status == "UNKNOWN":
        summary["note"] = "UNKNOWN — solver could not determine result within depth"
    if status == "FAIL" and trace_text is not None:
        # Syqnal's callback moves this into storage and shows it in the waveform viewer.
        summary["counterexample_vcd"] = trace_text
        summary["counterexample_name"] = trace_name
        if failures and failures[0]["step"] is not None:
            summary["counterexample_step"] = failures[0]["step"]

    return check_obj(
        "FORMAL", sby_file, "sby", version,
        status=status,
        error_count=len(failures) if failures else (1 if status == "FAIL" else 0),
        duration_ms=ms,
        violations=violations if violations else None,
        summary=summary,
    )
