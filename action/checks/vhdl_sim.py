"""VHDL simulation check — GHDL analyse/elaborate/run and capture VCD waveform."""

import base64
import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_ENTITY_RE = re.compile(r"\bentity\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\b", re.IGNORECASE)
_PACKAGE_RE = re.compile(r"\bpackage\s+(?!body\b)([A-Za-z_][A-Za-z0-9_]*)\s+is\b", re.IGNORECASE)
_ASSERT_FAIL_RE = re.compile(r"(?i)\b(assertion\s+failed|failure|error)\b")
_SIM_TIME_RE = re.compile(r"@(\d+)(?:ps|ns|us|ms|sec)\b", re.IGNORECASE)
_VCD_VAR_RE = re.compile(r"^\$var\b", re.MULTILINE)

_VCD_MAX_BYTES = 500_000
_VHDL_SUFFIXES = {".vhd", ".vhdl"}
_TB_DIR_NAMES = {"sim", "sims", "simulation", "simulations", "test", "tests", "tb", "testbench", "testbenches"}


def _ghdl_version() -> str | None:
    rc, out, err, _ = run_tool(["ghdl", "--version"])
    if rc != 0:
        return None
    first = (out + err).strip().splitlines()
    return first[0] if first else None


def is_vhdl_testbench_path(path: Path) -> bool:
    stem = path.stem.lower()
    parts = {part.lower() for part in path.parts}
    if (
        stem.startswith("tb_")
        or stem.startswith("tb-")
        or stem.endswith("_tb")
        or stem.endswith("-tb")
        or stem.endswith("_test")
        or stem.endswith("-test")
        or "testbench" in stem
    ):
        return True
    if parts & _TB_DIR_NAMES:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return False
        return bool(re.search(r"\bentity\s+tb[_A-Za-z0-9]*\s+is\b|\bassert\b|\bwait\b", text, re.IGNORECASE))
    return False


def _vhdl_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for suffix in _VHDL_SUFFIXES:
        files.extend(root.rglob(f"*{suffix}"))
    return sorted(set(files))


def _repo_root() -> Path:
    return Path(".").resolve()


def _safe_relative_to(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root)
    except ValueError:
        return path


def _source_root_for_testbench(tb_file: Path) -> Path:
    root = _repo_root()
    tb_abs = tb_file.resolve()
    current = tb_abs.parent
    while True:
        vhdl = _vhdl_files(current)
        non_tb = [f for f in vhdl if f.resolve() != tb_abs and not is_vhdl_testbench_path(_safe_relative_to(f, root))]
        if non_tb:
            return current
        if current == root or current == current.parent:
            return root
        current = current.parent


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _entity_name(path: Path) -> str | None:
    m = _ENTITY_RE.search(_read(path))
    return m.group(1).lower() if m else None


def _is_package(path: Path) -> bool:
    text = _read(path)
    return bool(_PACKAGE_RE.search(text)) or path.stem.lower().startswith(("pkg_", "package_")) or path.stem.lower().endswith(("_pkg", "_package"))


def _sources_for_testbench(tb_file: Path) -> list[Path]:
    root = _repo_root()
    source_root = _source_root_for_testbench(tb_file)
    tb_abs = tb_file.resolve()
    vhdl = _vhdl_files(source_root)
    packages = [f for f in vhdl if f.resolve() != tb_abs and _is_package(f)]
    design = [
        f for f in vhdl
        if f.resolve() != tb_abs
        and not _is_package(f)
        and not is_vhdl_testbench_path(_safe_relative_to(f, root))
    ]
    helpers = [
        f for f in vhdl
        if f.resolve() != tb_abs
        and not _is_package(f)
        and is_vhdl_testbench_path(_safe_relative_to(f, root))
    ]
    return sorted(packages) + sorted(design) + sorted(helpers) + [tb_file]


def _encode_vcd(vcd_path: Path) -> tuple[str | None, int | None]:
    try:
        raw = vcd_path.read_bytes()
        if len(raw) > _VCD_MAX_BYTES:
            return None, None
        text = raw.decode("utf-8", errors="replace")
        return base64.b64encode(raw).decode("ascii"), len(_VCD_VAR_RE.findall(text))
    except OSError:
        return None, None


def _summary(
    *,
    tool_version: str | None,
    top_entity: str | None,
    testbench_path: Path,
    design_files: list[str],
    command_sequence: list[str],
    assertions_failed: int = 0,
    compile_log: str = "",
    sim_log: str = "",
    vcd_b64: str | None = None,
    signal_count: int | None = None,
    sim_time_ns: int | None = None,
    source_count: int = 0,
    error_type: str | None = None,
    error_detail: str | None = None,
) -> dict:
    return {
        "tool": "ghdl",
        "toolVersion": tool_version,
        "language": "vhdl",
        "standard": "08",
        "topModule": top_entity,
        "testbenchPath": str(testbench_path),
        "designFiles": design_files,
        "includePaths": [],
        "commandSequence": command_sequence,
        "assertionsPassed": 0,
        "assertionsFailed": assertions_failed,
        "compileLog": compile_log[:4000],
        "simLog": sim_log[:4000],
        "vcdArtifactUrl": None,
        "vcdB64": vcd_b64,
        "signalCount": signal_count,
        "simTimeNs": sim_time_ns,
        "runner": "github_actions",
        "sourceCount": source_count,
        "errorType": error_type,
        "errorDetail": error_detail,
        "assertions_passed": 0,
        "assertions_failed": assertions_failed,
        "sim_time_ns": sim_time_ns,
        "source_count": source_count,
        "vcd_b64": vcd_b64,
        "signal_count": signal_count,
    }


def run_vhdl_sim(tb_file: Path) -> dict:
    version = _ghdl_version()
    sources = _sources_for_testbench(tb_file)
    design_files = [str(f) for f in sources if f.resolve() != tb_file.resolve() and not is_vhdl_testbench_path(f)]
    top_entity = _entity_name(tb_file) or tb_file.stem.lower()

    with tempfile.TemporaryDirectory(prefix="syqnal-ghdl-") as work:
        workdir = Path(work)
        commands: list[str] = []
        compile_log_parts: list[str] = []
        duration_ms = 0

        for src in sources:
            cmd = ["ghdl", "-a", "--std=08", f"--workdir={workdir}", str(src)]
            commands.append(" ".join(cmd))
            rc, out, err, ms = run_tool(cmd)
            duration_ms += ms
            compile_log_parts.append((out + err).strip())
            if rc != 0:
                compile_log = "\n".join(part for part in compile_log_parts if part)
                return check_obj(
                    "RTL_SIM", tb_file, "ghdl", version,
                    status="ERROR",
                    error_count=1,
                    duration_ms=duration_ms,
                    violations=[{"type": "compile_error", "severity": "error", "plain_text": compile_log[:500]}],
                    summary=_summary(
                        tool_version=version,
                        top_entity=top_entity,
                        testbench_path=tb_file,
                        design_files=design_files,
                        command_sequence=commands,
                        compile_log=compile_log,
                        source_count=len(sources),
                        error_type="compile_error",
                        error_detail=compile_log[:500],
                    ),
                )

        elaborate_cmd = ["ghdl", "-e", "--std=08", f"--workdir={workdir}", top_entity]
        commands.append(" ".join(elaborate_cmd))
        erc, eout, eerr, ems = run_tool(elaborate_cmd)
        duration_ms += ems
        compile_log = "\n".join(part for part in [*compile_log_parts, (eout + eerr).strip()] if part)
        if erc != 0:
            return check_obj(
                "RTL_SIM", tb_file, "ghdl", version,
                status="ERROR",
                error_count=1,
                duration_ms=duration_ms,
                violations=[{"type": "elaboration_error", "severity": "error", "plain_text": compile_log[:500]}],
                summary=_summary(
                    tool_version=version,
                    top_entity=top_entity,
                    testbench_path=tb_file,
                    design_files=design_files,
                    command_sequence=commands,
                    compile_log=compile_log,
                    source_count=len(sources),
                    error_type="elaboration_error",
                    error_detail=compile_log[:500],
                ),
            )

        vcd_path = workdir / "wave.vcd"
        run_cmd = ["ghdl", "-r", "--std=08", f"--workdir={workdir}", top_entity, f"--vcd={vcd_path}"]
        commands.append(" ".join(run_cmd))
        rrc, rout, rerr, rms = run_tool(run_cmd, timeout=60)
        duration_ms += rms
        sim_log = (rout + rerr).strip()
        assertions_failed = len(_ASSERT_FAIL_RE.findall(sim_log))
        sim_time_ns = None
        for match in _SIM_TIME_RE.finditer(sim_log):
            sim_time_ns = int(match.group(1))

        vcd_b64 = None
        signal_count = None
        if vcd_path.exists():
            vcd_b64, signal_count = _encode_vcd(vcd_path)

        error_type = None
        error_detail = None
        if rrc != 0:
            error_type = "simulation_crash"
            error_detail = sim_log[:500]
        elif not vcd_path.exists():
            error_type = "no_vcd_produced"
            error_detail = "Simulation ran but GHDL did not produce a VCD."

        status = "FAIL" if rrc != 0 or assertions_failed > 0 else "PASS"
        violations = []
        if rrc != 0 or assertions_failed > 0:
            for line in sim_log.splitlines():
                if _ASSERT_FAIL_RE.search(line):
                    violations.append({"type": "assertion_failed", "severity": "error", "plain_text": line.strip()})

        return check_obj(
            "RTL_SIM", tb_file, "ghdl", version,
            status=status,
            error_count=assertions_failed if assertions_failed else (1 if rrc != 0 else 0),
            duration_ms=duration_ms,
            violations=violations if violations else None,
            summary=_summary(
                tool_version=version,
                top_entity=top_entity,
                testbench_path=tb_file,
                design_files=design_files,
                command_sequence=commands,
                assertions_failed=assertions_failed,
                compile_log=compile_log,
                sim_log=sim_log,
                vcd_b64=vcd_b64,
                signal_count=signal_count,
                sim_time_ns=sim_time_ns,
                source_count=len(sources),
                error_type=error_type,
                error_detail=error_detail,
            ),
        )
