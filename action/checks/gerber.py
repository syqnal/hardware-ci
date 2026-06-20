"""Gerber check — validate required fabrication layers are present."""

import time
from pathlib import Path

from ._base import check_obj

# Minimum required layers for PCB fabrication
REQUIRED_LAYERS: dict[str, list[str]] = {
    "copper_front": [".gtl", ".f.cu.gbr", "-f_cu.gbr"],
    "copper_back":  [".gbl", ".b.cu.gbr", "-b_cu.gbr"],
    "silkscreen_front": [".gto", ".f.silks.gbr", "-f_silks.gbr"],
    "soldermask_front": [".gts", ".f.mask.gbr", "-f_mask.gbr"],
    "soldermask_back":  [".gbs", ".b.mask.gbr", "-b_mask.gbr"],
    "drill":        [".drl", ".drill.gbr", "-pth.drl"],
    "board_outline": [".gko", ".edge_cuts.gbr", "-edge_cuts.gbr"],
}


def run_gerber(gerber_dir: Path) -> dict:
    t0 = time.monotonic()

    files = {f.name.lower(): f for f in gerber_dir.iterdir() if f.is_file()}
    violations = []
    error_count = 0
    layers_found: list[str] = []
    layers_missing: list[str] = []

    for layer_name, extensions in REQUIRED_LAYERS.items():
        found = any(
            any(fname.endswith(ext) for fname in files)
            for ext in extensions
        )
        if found:
            layers_found.append(layer_name)
        else:
            layers_missing.append(layer_name)
            error_count += 1
            violations.append({
                "type": "missing_layer",
                "severity": "error",
                "plain_text": f"Required Gerber layer '{layer_name}' not found in {gerber_dir}",
            })

    ms = int((time.monotonic() - t0) * 1000)
    status = "PASS" if error_count == 0 else "FAIL"

    return check_obj(
        "GERBER", gerber_dir, "syqnal-gerber-check", "1.0.0",
        status=status,
        error_count=error_count,
        duration_ms=ms,
        violations=violations if violations else None,
        summary={
            "layers_found": layers_found,
            "layers_missing": layers_missing,
            "file_count": len(files),
        },
    )
