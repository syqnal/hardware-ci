"""SPICE simulation check — ngspice -b → parse convergence and errors."""

import re
from pathlib import Path

from ._base import check_obj, run_tool


_VERSION_RE = re.compile(r"ngspice-(\S+)", re.IGNORECASE)
_ERROR_RE = re.compile(r"(?i)^\s*(error|fatal)[\s:]")
_WARNING_RE = re.compile(r"(?i)^\s*warning[\s:]")
_CONVERGENCE_FAIL_RE = re.compile(r"(?i)(no convergence|convergence problem|did not converge)")
_TIME_STEPS_RE = re.compile(r"(\d+)\s+time[-_\s]?steps?", re.IGNORECASE)


def _ngspice_version() -> str | None:
    rc, out, err, _ = run_tool(["ngspice", "--version"])
    combined = out + err
    m = _VERSION_RE.search(combined)
    return m.group(1) if m else None


def run_spice(cir_file: Path) -> dict:
    version = _ngspice_version()

    rc, stdout, stderr, duration_ms = run_tool(
        ["ngspice", "-b", "-o", "/dev/stdout", str(cir_file)],
        timeout=120,
    )

    combined = stdout + stderr
    error_count = 0
    warning_count = 0
    violations = []
    converged = True

    for line in combined.splitlines():
        if _CONVERGENCE_FAIL_RE.search(line):
            converged = False
            error_count += 1
            violations.append({
                "type": "convergence_failure",
                "severity": "error",
                "plain_text": line.strip(),
            })
        elif _ERROR_RE.match(line):
            error_count += 1
            violations.append({
                "type": "spice_error",
                "severity": "error",
                "plain_text": line.strip()[:200],
            })
        elif _WARNING_RE.match(line):
            warning_count += 1

    # ngspice exits non-zero on convergence failure; also check exit code
    if rc != 0 and error_count == 0:
        error_count += 1
        violations.append({
            "type": "tool_error",
            "severity": "error",
            "plain_text": f"ngspice exited with code {rc}",
        })
        converged = False

    time_steps: int | None = None
    m = _TIME_STEPS_RE.search(combined)
    if m:
        time_steps = int(m.group(1))

    status = "PASS" if error_count == 0 else "FAIL"
    return check_obj(
        "SPICE_SIM", cir_file, "ngspice", version,
        status=status,
        error_count=error_count,
        warning_count=warning_count,
        duration_ms=duration_ms,
        violations=violations if violations else None,
        summary={
            "convergence": converged,
            "time_steps": time_steps,
        },
    )
