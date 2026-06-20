"""STEP validation — attempt to import via FreeCAD CLI or fall back to header check."""

import re
import time
from pathlib import Path

from ._base import check_obj, run_tool

# STEP files must start with ISO-10303-21 header
_STEP_HEADER_RE = re.compile(r"ISO-10303-21", re.IGNORECASE)


def run_step(step_file: Path) -> dict:
    t0 = time.monotonic()

    # Try FreeCAD headless first (may not be installed)
    rc, out, err, duration_ms = run_tool([
        "freecad", "--console", "--run-script",
        f"import Part; Part.read('{step_file}'); print('STEP_OK')",
    ], timeout=60)

    if rc == 0 and "STEP_OK" in out:
        return check_obj(
            "STEP_VALID", step_file, "freecad", None,
            status="PASS", duration_ms=duration_ms,
        )

    if rc != 0 and "not found" not in err.lower():
        # FreeCAD found but import failed
        return check_obj(
            "STEP_VALID", step_file, "freecad", None,
            status="FAIL", error_count=1, duration_ms=duration_ms,
            violations=[{"type": "import_error", "severity": "error",
                         "plain_text": (out + err).strip()[:300]}],
        )

    # Fallback: validate ISO-10303-21 header (FreeCAD not available)
    ms = int((time.monotonic() - t0) * 1000)
    try:
        header = step_file.read_bytes()[:512].decode("ascii", errors="ignore")
        if _STEP_HEADER_RE.search(header):
            return check_obj(
                "STEP_VALID", step_file, "header-check", "1.0.0",
                status="PASS", duration_ms=ms,
                summary={"method": "header_only"},
            )
        else:
            return check_obj(
                "STEP_VALID", step_file, "header-check", "1.0.0",
                status="FAIL", error_count=1, duration_ms=ms,
                violations=[{"type": "invalid_header", "severity": "error",
                             "plain_text": "File does not contain ISO-10303-21 header"}],
            )
    except Exception as e:
        return check_obj(
            "STEP_VALID", step_file, "header-check", "1.0.0",
            status="ERROR", duration_ms=ms,
            violations=[{"type": "read_error", "severity": "error",
                         "plain_text": str(e)[:200]}],
        )
