"""Formal verification check — SymbiYosys (sby) on .sby project files."""

import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_VERSION_RE = re.compile(r"sby\s+(\d[\d.]+)", re.IGNORECASE)
_DONE_RE = re.compile(r"DONE\s*\((\w+),\s*rc=(\d+)\)", re.IGNORECASE)

# .sby config section parser
_SECTION_RE = re.compile(r"^\[(\w+)\]", re.MULTILINE)

# Engine first token (e.g. "smtbmc yices" → "smtbmc")
_ENGINE_FIRST_RE = re.compile(r"^\s*(\S+)", re.MULTILINE)

# Output counters
_STATUS_PASS_RE = re.compile(r"Status:\s*PASSED", re.IGNORECASE)
_STATUS_FAIL_RE = re.compile(r"(?:Status:\s*FAILED|BMC failed|Assert failed)", re.IGNORECASE)
_COVER_REACHED_RE = re.compile(r"Reached cover statement", re.IGNORECASE)


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


def run_formal(sby_file: Path) -> dict:
    version = _sby_version()

    try:
        config = _parse_sby_config(sby_file.read_text(errors="replace"))
    except OSError:
        config = {"mode": "PROVE", "depth": None, "engine": None}

    # sby writes output to a named directory; use a temp dir to keep CI clean.
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / sby_file.stem
        rc, out, err, ms = run_tool(
            ["sby", "-f", "-d", str(work_dir), str(sby_file.resolve())],
            timeout=300,  # formal proofs can be slow
        )

    combined = out + err

    # Parse final status from the DONE line (most reliable signal).
    done_m = _DONE_RE.search(combined)
    if done_m:
        done_status = done_m.group(1).upper()  # PASS / FAIL / UNKNOWN / ERROR
    else:
        done_status = "ERROR" if rc != 0 else "PASS"

    status = "PASS" if done_status == "PASS" else ("FAIL" if done_status == "FAIL" else "ERROR")

    assertions_proved = len(_STATUS_PASS_RE.findall(combined))
    assertions_failed = len(_STATUS_FAIL_RE.findall(combined))
    cover_traces = len(_COVER_REACHED_RE.findall(combined))

    violations = []
    if status != "PASS":
        for line in combined.splitlines():
            if _STATUS_FAIL_RE.search(line):
                violations.append({
                    "type": "assertion_failed",
                    "severity": "error",
                    "plain_text": line.strip(),
                })

    summary: dict = {
        "mode": config["mode"],
        "engine": config["engine"],
    }
    if config["depth"] is not None:
        summary["depth"] = config["depth"]
    if assertions_proved > 0 or assertions_failed > 0:
        summary["assertions_proved"] = assertions_proved
        summary["assertions_failed"] = assertions_failed
    if cover_traces > 0:
        summary["cover_traces"] = cover_traces
    if done_status == "UNKNOWN":
        summary["note"] = "UNKNOWN — solver could not determine result within depth"

    return check_obj(
        "FORMAL", sby_file, "sby", version,
        status=status,
        error_count=assertions_failed,
        duration_ms=ms,
        violations=violations if violations else None,
        summary=summary,
    )
