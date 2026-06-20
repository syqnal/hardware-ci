"""BOM check — parse CSV, validate required columns, count parts."""

import csv
from pathlib import Path

from ._base import check_obj

REQUIRED_COLUMNS = {"reference", "value", "quantity"}
OPTIONAL_COLUMNS = {"manufacturer", "mpn", "footprint", "description"}


def run_bom(bom_file: Path) -> dict:
    import time
    t0 = time.monotonic()

    try:
        text = bom_file.read_text(encoding="utf-8-sig")  # strip BOM if present
        reader = csv.DictReader(text.splitlines())
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return check_obj(
            "BOM_PARSE", bom_file, "syqnal-bom-parser", "1.0.0",
            status="ERROR", duration_ms=ms,
            violations=[{"type": "parse_error", "severity": "error",
                         "plain_text": str(e)[:200]}],
        )

    missing = REQUIRED_COLUMNS - headers
    violations = []
    error_count = 0

    for col in sorted(missing):
        error_count += 1
        violations.append({
            "type": "missing_column",
            "severity": "error",
            "plain_text": f"Required column '{col}' not found in BOM",
        })

    rows = list(reader)
    part_count = len(rows)

    ms = int((time.monotonic() - t0) * 1000)
    status = "PASS" if error_count == 0 else "FAIL"

    return check_obj(
        "BOM_PARSE", bom_file, "syqnal-bom-parser", "1.0.0",
        status=status,
        error_count=error_count,
        duration_ms=ms,
        violations=violations if violations else None,
        summary={
            "part_count": part_count,
            "has_mpn": "mpn" in headers,
            "has_manufacturer": "manufacturer" in headers,
            "columns_found": sorted(headers),
        },
    )
