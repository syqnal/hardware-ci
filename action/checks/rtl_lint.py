"""RTL lint check — verilator --lint-only → parse stderr."""

import re
from pathlib import Path

from ._base import check_obj, run_tool


_MSG_RE = re.compile(
    r"%(?P<sev>Error|Warning)(?:-(?P<code>[A-Z0-9_]+))?:\s*(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+)?: (?P<msg>.+)"
)


def _verilator_version() -> str | None:
    rc, out, _, _ = run_tool(["verilator", "--version"])
    if rc == 0 and out.strip():
        # "Verilator 5.020 ..."
        parts = out.strip().split()
        return parts[1] if len(parts) >= 2 else None
    return None


def run_rtl_lint(v_file: Path) -> dict:
    version = _verilator_version()

    rc, stdout, stderr, duration_ms = run_tool([
        "verilator",
        "--lint-only",
        "--Wall",
        "-sv",           # accept SystemVerilog syntax
        str(v_file),
    ])

    combined = stdout + stderr
    violations = []
    error_count = 0
    warning_count = 0

    for line in combined.splitlines():
        m = _MSG_RE.search(line)
        if not m:
            continue
        sev = m.group("sev").lower()  # "error" | "warning"
        code = m.group("code") or "lint"
        msg = m.group("msg").strip()
        file_ref = m.group("file").strip()
        lineno = int(m.group("line"))

        if sev == "error":
            error_count += 1
        else:
            warning_count += 1

        violations.append({
            "type": code.lower(),
            "severity": sev,
            "plain_text": f"{file_ref}:{lineno}: {msg}",
        })

    status = "PASS" if error_count == 0 else "FAIL"
    return check_obj(
        "RTL_LINT", v_file, "verilator", version,
        status=status,
        error_count=error_count,
        warning_count=warning_count,
        duration_ms=duration_ms,
        violations=violations,
    )
