"""Toolchain doctor for the Syqnal hardware-ci Docker image."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_COMMANDS = [
    ("python3", ["python3", "--version"]),
    ("kicad-cli", ["kicad-cli", "--version"]),
    ("verilator", ["verilator", "--version"]),
    ("iverilog", ["iverilog", "-V"]),
    ("vvp", ["vvp", "-V"]),
    ("ngspice", ["ngspice", "--version"]),
    ("yosys", ["yosys", "--version"]),
    ("sby", ["sby", "--version"]),
    ("openroad", ["openroad", "-version"]),
    ("klayout", ["klayout", "-b", "-v"]),
    ("magic", ["magic", "--version"]),
    ("netgen", ["netgen", "-version"]),
]


def _run(label: str, cmd: list[str]) -> bool:
    if shutil.which(cmd[0]) is None:
        print(f"[doctor] MISSING {label}: {cmd[0]} not on PATH", file=sys.stderr)
        return False
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as exc:
        print(f"[doctor] ERROR {label}: {exc}", file=sys.stderr)
        return False

    text = (result.stdout + result.stderr).strip().splitlines()
    first_line = text[0] if text else "(no version output)"
    if result.returncode != 0 and label not in {"netgen"}:
        print(f"[doctor] ERROR {label}: rc={result.returncode} {first_line}", file=sys.stderr)
        return False
    print(f"[doctor] OK {label}: {first_line}")
    return True


def _openlane_ok() -> bool:
    try:
        result = subprocess.run(
            ["python3", "-m", "openlane", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        print(f"[doctor] ERROR openlane: {exc}", file=sys.stderr)
        return False
    text = (result.stdout + result.stderr).strip().splitlines()
    first_line = text[0] if text else "(no version output)"
    if result.returncode != 0:
        print(f"[doctor] ERROR openlane: rc={result.returncode} {first_line}", file=sys.stderr)
        return False
    print(f"[doctor] OK openlane: {first_line}")
    return True


def _pdk_ok() -> bool:
    pdk_root = Path(os.environ.get("PDK_ROOT", "/opt/pdks"))
    expected = os.environ.get("PDK", "sky130A")
    candidates = [
        pdk_root / expected,
        *pdk_root.glob(f"**/{expected}"),
    ]
    found = next((path for path in candidates if path.exists() and path.is_dir()), None)
    if found is None:
        print(f"[doctor] MISSING PDK: expected {expected} under {pdk_root}", file=sys.stderr)
        return False
    print(f"[doctor] OK PDK: {found}")
    return True


def main() -> int:
    ok = True
    for label, cmd in REQUIRED_COMMANDS:
        ok = _run(label, cmd) and ok
    ok = _openlane_ok() and ok
    ok = _pdk_ok() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
