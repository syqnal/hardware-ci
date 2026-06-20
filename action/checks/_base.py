"""Shared helpers for all check modules."""

import subprocess
import time
from pathlib import Path


def run_tool(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str, str, int]:
    """Run a subprocess and return (returncode, stdout, stderr, duration_ms)."""
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
        ms = int((time.monotonic() - t0) * 1000)
        return result.returncode, result.stdout, result.stderr, ms
    except subprocess.TimeoutExpired:
        ms = int((time.monotonic() - t0) * 1000)
        return 1, "", f"Tool timed out after {timeout}s", ms
    except FileNotFoundError as e:
        ms = int((time.monotonic() - t0) * 1000)
        return 1, "", f"Tool not found: {e}", ms


def repo_rel(path: Path) -> str:
    """Return path relative to CWD (the repo root in CI)."""
    try:
        return str(path.relative_to(Path(".")))
    except ValueError:
        return str(path)


def check_obj(
    check_type: str,
    file_path: Path,
    tool: str,
    tool_version: str | None,
    status: str,
    error_count: int = 0,
    warning_count: int = 0,
    duration_ms: int = 0,
    violations: list | None = None,
    summary: dict | None = None,
) -> dict:
    obj: dict = {
        "type": check_type,
        "file": repo_rel(file_path),
        "tool": tool,
        "status": status,
        "error_count": error_count,
        "warning_count": warning_count,
        "duration_ms": duration_ms,
    }
    if tool_version:
        obj["tool_version"] = tool_version
    if violations is not None:
        obj["violations"] = violations
    if summary is not None:
        obj["summary"] = summary
    return obj
