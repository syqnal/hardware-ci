"""GDSII validation check — KLayout batch mode, no PDK required.

Validates that a GDS/OAS layout file is parseable and extracts structural
metadata: top cell, cell count, layer count, and bounding box. Used for
standalone GDS files (not produced by the OpenLane flow check).
"""

import json
import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_VERSION_RE = re.compile(r"KLayout\s+([\d.]+)", re.IGNORECASE)

# Inline Python script passed to klayout -b via -r flag.
# Extracts top-cell, cell count, layer count, bounding box.
_INSPECT_SCRIPT = """\
import pya
import json, sys

path = sys.argv[1]
layout = pya.Layout()
layout.read(path)

cells = list(layout.each_cell())
top_cells = layout.top_cells()
top_name = top_cells[0].name if top_cells else None

layers = set()
bbox = None
for cell in cells:
    for layer_idx in range(layout.layers()):
        linfo = layout.get_info(layer_idx)
        if not cell.shapes(layer_idx).is_empty():
            layers.add((linfo.layer, linfo.datatype))
    b = cell.bbox()
    if b.is_point() or not b.is_valid():
        continue
    if bbox is None:
        bbox = b
    else:
        bbox = bbox.__add__(b)

result = {
    "top_cell": top_name,
    "cell_count": len(cells),
    "layer_count": len(layers),
    "bbox_um": {
        "x_min": round(bbox.left  * layout.dbu, 4),
        "y_min": round(bbox.bottom * layout.dbu, 4),
        "x_max": round(bbox.right * layout.dbu, 4),
        "y_max": round(bbox.top  * layout.dbu, 4),
    } if bbox and bbox.is_valid() else None,
}
print(json.dumps(result))
"""


def _klayout_version() -> str | None:
    rc, out, err, _ = run_tool(["klayout", "--version"])
    m = _VERSION_RE.search(out + err)
    return m.group(1) if m else None


def run_gdsii(gds_file: Path) -> dict:
    version = _klayout_version()

    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as tmp:
        tmp.write(_INSPECT_SCRIPT)
        script_path = Path(tmp.name)

    try:
        rc, out, err, ms = run_tool(
            ["klayout", "-b", "-r", str(script_path), str(gds_file.resolve())],
            timeout=60,
        )
    finally:
        script_path.unlink(missing_ok=True)

    combined = out + err

    if rc != 0:
        return check_obj(
            "GDSII", gds_file, "klayout", version,
            status="ERROR", duration_ms=ms,
            violations=[{
                "type": "parse_error",
                "severity": "error",
                "plain_text": (err or combined).strip()[:500],
            }],
        )

    # Parse JSON from stdout (the last JSON line, in case klayout emits warnings first)
    summary: dict = {}
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
                break
            except json.JSONDecodeError:
                pass

    if not summary:
        return check_obj(
            "GDSII", gds_file, "klayout", version,
            status="ERROR", duration_ms=ms,
            violations=[{"type": "parse_error", "severity": "error",
                         "plain_text": "No JSON output from klayout inspect script"}],
        )

    # A GDS with no cells is corrupt / empty.
    cell_count = summary.get("cell_count", 0) or 0
    status = "PASS" if cell_count > 0 else "FAIL"
    violations = []
    if cell_count == 0:
        violations.append({
            "type": "empty_layout",
            "severity": "error",
            "plain_text": "GDS file contains no cells",
        })

    return check_obj(
        "GDSII", gds_file, "klayout", version,
        status=status,
        duration_ms=ms,
        violations=violations if violations else None,
        summary=summary,
    )
