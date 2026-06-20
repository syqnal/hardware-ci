"""RTL simulation check — iverilog + vvp → parse assertion results and capture VCD waveform."""

import base64
import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_IVERILOG_VERSION_RE = re.compile(r"Icarus Verilog version (\S+)")
_ASSERT_PASS_RE = re.compile(r"(?i)\b(PASS|PASSED|ASSERT\s+PASSED?|OK)\b")
_ASSERT_FAIL_RE = re.compile(r"(?i)\b(FAIL|FAILED|ASSERT\s+FAILED?|ERROR)\b")
_SIM_TIME_RE = re.compile(r"(\d+)\s*ns", re.IGNORECASE)
_DUMPFILE_RE = re.compile(r'\$dumpfile\s*\(\s*"([^"]+)"', re.IGNORECASE)
_VCD_VAR_RE = re.compile(r"^\$var\b", re.MULTILINE)

_VCD_MAX_BYTES = 500_000  # skip inline encoding above 500 KB


def _iverilog_version() -> str | None:
    rc, out, err, _ = run_tool(["iverilog", "-V"])
    combined = out + err
    m = _IVERILOG_VERSION_RE.search(combined)
    return m.group(1) if m else None


def _find_vcd(tb_file: Path) -> Path | None:
    """Return the VCD file produced by this testbench run, or None."""
    # Parse $dumpfile directive from the testbench source for an exact name.
    try:
        src = tb_file.read_text(errors="replace")
        m = _DUMPFILE_RE.search(src)
        if m:
            vcd_name = m.group(1)
            # vvp writes relative to CWD (repo root in CI).
            for candidate in [Path(vcd_name), tb_file.parent / vcd_name]:
                if candidate.is_file():
                    return candidate
    except OSError:
        pass

    # Fallback: first *.vcd in CWD or testbench dir.
    for vcd in list(Path(".").glob("*.vcd")) + list(tb_file.parent.glob("*.vcd")):
        if vcd.is_file():
            return vcd

    return None


def _encode_vcd(vcd_path: Path) -> tuple[str | None, int | None]:
    """Read a VCD file and return (base64_str, signal_count) or (None, None) if too large."""
    try:
        raw = vcd_path.read_bytes()
        if len(raw) > _VCD_MAX_BYTES:
            return None, None
        text = raw.decode("utf-8", errors="replace")
        signal_count = len(_VCD_VAR_RE.findall(text))
        return base64.b64encode(raw).decode("ascii"), signal_count
    except OSError:
        return None, None


def run_rtl_sim(tb_file: Path) -> dict:
    version = _iverilog_version()

    with tempfile.NamedTemporaryFile(suffix=".vvp", delete=False) as tmp:
        vvp_path = Path(tmp.name)

    # Collect all .v/.sv files in the same directory tree to include as sources.
    src_dir = tb_file.parent
    while src_dir != src_dir.parent:
        v_files = list(src_dir.rglob("*.v")) + list(src_dir.rglob("*.sv"))
        if v_files:
            break
        src_dir = src_dir.parent

    sources = [str(f) for f in v_files if f != tb_file]
    sources.append(str(tb_file))  # testbench last so it can reference other modules

    compile_rc, _, compile_err, compile_ms = run_tool([
        "iverilog", "-g2012", "-o", str(vvp_path), *sources
    ])

    if compile_rc != 0:
        vvp_path.unlink(missing_ok=True)
        return check_obj(
            "RTL_SIM", tb_file, "iverilog", version,
            status="ERROR", duration_ms=compile_ms,
            violations=[{"type": "compile_error", "severity": "error",
                         "plain_text": compile_err.strip()[:500]}],
        )

    run_rc, sim_out, sim_err, sim_ms = run_tool(["vvp", str(vvp_path)], timeout=60)
    vvp_path.unlink(missing_ok=True)

    combined = sim_out + sim_err
    assertions_passed = len(_ASSERT_PASS_RE.findall(combined))
    assertions_failed = len(_ASSERT_FAIL_RE.findall(combined))

    # Best-effort sim time extraction from $display output.
    sim_time_ns: int | None = None
    for m in _SIM_TIME_RE.finditer(combined):
        sim_time_ns = int(m.group(1))  # take last match

    status = "FAIL" if (assertions_failed > 0 or run_rc != 0) else "PASS"

    violations = []
    if assertions_failed > 0 or run_rc != 0:
        for line in combined.splitlines():
            if _ASSERT_FAIL_RE.search(line):
                violations.append({
                    "type": "assertion_failed",
                    "severity": "error",
                    "plain_text": line.strip(),
                })

    # Capture VCD waveform if the testbench produced one.
    vcd_b64: str | None = None
    signal_count: int | None = None
    vcd_path = _find_vcd(tb_file)
    if vcd_path:
        vcd_b64, signal_count = _encode_vcd(vcd_path)

    summary: dict = {
        "assertions_passed": assertions_passed,
        "assertions_failed": assertions_failed,
        "sim_time_ns": sim_time_ns,
    }
    if vcd_b64 is not None:
        summary["vcd_b64"] = vcd_b64
        summary["signal_count"] = signal_count

    return check_obj(
        "RTL_SIM", tb_file, "iverilog", version,
        status=status,
        error_count=assertions_failed,
        duration_ms=compile_ms + sim_ms,
        violations=violations if violations else None,
        summary=summary,
    )
