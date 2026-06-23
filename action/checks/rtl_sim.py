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
_TB_DIR_NAMES = {"sim", "sims", "simulation", "simulations", "test", "tests", "tb", "testbench", "testbenches"}
_HDL_SUFFIXES = {".v", ".sv"}


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


def _is_testbench_path(path: Path) -> bool:
    stem = path.stem.lower()
    parts = {part.lower() for part in path.parts}
    filename_matches = (
        stem.startswith("tb_")
        or stem.startswith("tb-")
        or stem.endswith("_tb")
        or stem.endswith("-tb")
        or stem.endswith("_test")
        or stem.endswith("-test")
        or "testbench" in stem
    )
    if filename_matches:
        return True

    if parts & _TB_DIR_NAMES:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return False
        return bool(re.search(r"\$(?:finish|fatal|dumpfile|dumpvars)\b|\bmodule\s+tb[_A-Za-z0-9$]*", text, re.IGNORECASE))

    return False


def is_testbench_path(path: Path) -> bool:
    return _is_testbench_path(path)


def _repo_root() -> Path:
    return Path(".").resolve()


def _safe_relative_to(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root)
    except ValueError:
        return path


def _hdl_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for suffix in _HDL_SUFFIXES:
        files.extend(root.rglob(f"*{suffix}"))
    return sorted(set(files))


def _source_root_for_testbench(tb_file: Path) -> Path:
    """Pick the smallest useful RTL root for a testbench.

    Common repos place testbenches under rtl/sim or test/ while DUT modules live
    in the parent RTL directory.  Using tb_file.parent alone misses those DUTs.
    Walk upward until an ancestor contains at least one non-testbench HDL file.
    """
    root = _repo_root()
    tb_abs = tb_file.resolve()
    current = tb_abs.parent

    while True:
        hdl = _hdl_files(current)
        non_tb = [f for f in hdl if f.resolve() != tb_abs and not _is_testbench_path(_safe_relative_to(f, root))]
        if non_tb:
            return current
        if current == root or current == current.parent:
            return root
        current = current.parent


def _sources_for_testbench(tb_file: Path) -> list[str]:
    root = _repo_root()
    source_root = _source_root_for_testbench(tb_file)
    tb_abs = tb_file.resolve()
    hdl = _hdl_files(source_root)

    design_sources = [
        f for f in hdl
        if f.resolve() != tb_abs and not _is_testbench_path(_safe_relative_to(f, root))
    ]
    other_testbench_helpers = [
        f for f in hdl
        if f.resolve() != tb_abs and _is_testbench_path(_safe_relative_to(f, root))
    ]

    # Design files first, helper packages/testbench support next, target testbench last.
    ordered = sorted(design_sources) + sorted(other_testbench_helpers) + [tb_file]
    return [str(f) for f in ordered]


def design_sources_for(path: Path) -> list[str]:
    root = _repo_root()
    current = path.resolve().parent
    while True:
        hdl = _hdl_files(current)
        design_sources = [
            f for f in hdl
            if not _is_testbench_path(_safe_relative_to(f, root))
        ]
        if design_sources:
            return [str(f) for f in sorted(design_sources)]
        if current == root or current == current.parent:
            return [str(path)]
        current = current.parent


def run_rtl_sim(tb_file: Path) -> dict:
    version = _iverilog_version()

    with tempfile.NamedTemporaryFile(suffix=".vvp", delete=False) as tmp:
        vvp_path = Path(tmp.name)

    sources = _sources_for_testbench(tb_file)

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
        "source_count": len(sources),
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
