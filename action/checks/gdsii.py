"""GDSII/OASIS validation check — KLayout batch mode, no PDK required.

Validates that a GDS/OAS layout file is parseable, extracts structural
metadata, and generates a lightweight SVG preview from the actual layout
geometry. Used for standalone GDS/OAS files and by the OpenLane flow check.
"""

import json
import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_VERSION_RE = re.compile(r"KLayout\s+([\d.]+)", re.IGNORECASE)

# Inline Python script passed to klayout -b via -r.
# Extracts top-cell metadata and a compact SVG preview.
_INSPECT_SCRIPT = """\
import pya
import base64
import json, sys
import html

path = sys.argv[1]
layout = pya.Layout()
layout.read(path)

cells = list(layout.each_cell())
top_cells = layout.top_cells()
top = top_cells[0] if top_cells else None
top_name = top.name if top else None

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

preview_svg = None
preview_shape_count = 0
preview_truncated = False
MAX_SHAPES = 3500

def layer_color(i):
    palette = [
        "#38bdf8", "#fb7185", "#34d399", "#f59e0b", "#a78bfa",
        "#22c55e", "#f97316", "#60a5fa", "#e879f9", "#14b8a6",
    ]
    return palette[i % len(palette)]

if top is not None and bbox is not None and bbox.is_valid():
    # Flatten a copy of the top cell so the preview includes child instances.
    try:
        top.flatten(-1, True)
    except Exception:
        pass

    width = max((bbox.right - bbox.left) * layout.dbu, 0.001)
    height = max((bbox.top - bbox.bottom) * layout.dbu, 0.001)
    x0 = bbox.left * layout.dbu
    y0 = bbox.bottom * layout.dbu
    paths = []
    layer_idx_out = 0
    for layer_idx in range(layout.layers()):
        linfo = layout.get_info(layer_idx)
        shapes = top.shapes(layer_idx)
        if shapes.is_empty():
            continue
        color = layer_color(layer_idx_out)
        layer_idx_out += 1
        layer_label = html.escape(f"{linfo.layer}/{linfo.datatype}")
        paths.append(f'<g data-layer="{layer_label}" fill="{color}" fill-opacity="0.36" stroke="{color}" stroke-width="{max(width, height) * 0.0015}" stroke-opacity="0.95">')
        for shape in shapes.each():
            if preview_shape_count >= MAX_SHAPES:
                preview_truncated = True
                break
            try:
                poly = shape.polygon
                pts = []
                for pt in poly.each_point_hull():
                    x = pt.x * layout.dbu - x0
                    y = height - (pt.y * layout.dbu - y0)
                    pts.append(f"{x:.4f},{y:.4f}")
                if len(pts) >= 3:
                    paths.append(f'<polygon points="{" ".join(pts)}" />')
                    preview_shape_count += 1
            except Exception:
                try:
                    b = shape.bbox()
                    x = b.left * layout.dbu - x0
                    y = height - (b.top * layout.dbu - y0)
                    w = max((b.right - b.left) * layout.dbu, 0.0001)
                    h = max((b.top - b.bottom) * layout.dbu, 0.0001)
                    paths.append(f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" />')
                    preview_shape_count += 1
                except Exception:
                    pass
        paths.append("</g>")
        if preview_truncated:
            break

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.4f} {height:.4f}" role="img">'
        f'<rect x="0" y="0" width="{width:.4f}" height="{height:.4f}" fill="#071013" />'
        f'{"".join(paths)}'
        f'</svg>'
    )
    preview_svg = base64.b64encode(svg.encode("utf-8")).decode("ascii")

result = {
    "top_cell": top_name,
    "topCell": top_name,
    "cell_count": len(cells),
    "cellCount": len(cells),
    "layer_count": len(layers),
    "layerCount": len(layers),
    "bbox_um": {
        "x_min": round(bbox.left  * layout.dbu, 4),
        "y_min": round(bbox.bottom * layout.dbu, 4),
        "x_max": round(bbox.right * layout.dbu, 4),
        "y_max": round(bbox.top  * layout.dbu, 4),
        "xMin": round(bbox.left  * layout.dbu, 4),
        "yMin": round(bbox.bottom * layout.dbu, 4),
        "xMax": round(bbox.right * layout.dbu, 4),
        "yMax": round(bbox.top  * layout.dbu, 4),
    } if bbox and bbox.is_valid() else None,
    "layout_preview_kind": "svg",
    "layoutPreviewKind": "svg",
    "layout_preview_source": "klayout_geometry",
    "layoutPreviewSource": "klayout_geometry",
    "layout_preview_svg_b64": preview_svg,
    "layoutPreviewSvgB64": preview_svg,
    "layout_preview_shape_count": preview_shape_count,
    "layoutPreviewShapeCount": preview_shape_count,
    "layout_preview_truncated": preview_truncated,
    "layoutPreviewTruncated": preview_truncated,
}
print(json.dumps(result))
"""


def _klayout_version() -> str | None:
    rc, out, err, _ = run_tool(["klayout", "--version"])
    m = _VERSION_RE.search(out + err)
    return m.group(1) if m else None


def inspect_layout_file(gds_file: Path) -> tuple[dict, list | None, str, int, str | None]:
    """Return (summary, violations, status, duration_ms, tool_version)."""
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
        return {}, [{
                "type": "parse_error",
                "severity": "error",
                "plain_text": (err or combined).strip()[:500],
            }], "ERROR", ms, version

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
        return {}, [{"type": "parse_error", "severity": "error",
                     "plain_text": "No JSON output from klayout inspect script"}], "ERROR", ms, version

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

    return summary, violations if violations else None, status, ms, version


def run_gdsii(gds_file: Path) -> dict:
    summary, violations, status, ms, version = inspect_layout_file(gds_file)
    return check_obj(
        "GDSII", gds_file, "klayout", version,
        status=status,
        duration_ms=ms,
        violations=violations,
        summary=summary,
    )
