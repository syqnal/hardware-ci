"""Synthesis check — Yosys → parse JSON stat output."""

import json
import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool
from .rtl_sim import design_sources_for


_VERSION_RE = re.compile(r"Yosys (\S+)")


def _yosys_version() -> str | None:
    rc, out, _, _ = run_tool(["yosys", "--version"])
    m = _VERSION_RE.search(out)
    return m.group(1) if m else None


def _infer_top_module(v_file: Path) -> str:
    """Use filename as the top module name — reasonable convention."""
    return v_file.stem


def run_synthesis(v_file: Path) -> dict:
    version = _yosys_version()
    top = _infer_top_module(v_file)
    sources = " ".join(design_sources_for(v_file))

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        stat_path = Path(tmp.name)

    # Run: read → synth → write stat JSON
    # -p passes a script inline; stat -json writes machine-readable cell counts
    script = (
        f"read_verilog -sv {sources}; "
        f"synth -top {top} -flatten; "
        f"stat -json -outfile {stat_path}"
    )

    rc, stdout, stderr, duration_ms = run_tool(
        ["yosys", "-p", script],
        timeout=180,
    )

    combined = stdout + stderr
    error_count = sum(1 for l in combined.splitlines() if re.match(r"ERROR:", l))

    if rc != 0 or error_count > 0:
        violations = []
        for line in combined.splitlines():
            if re.match(r"ERROR:", line):
                violations.append({
                    "type": "synthesis_error",
                    "severity": "error",
                    "plain_text": line.strip()[:300],
                })
        stat_path.unlink(missing_ok=True)
        return check_obj(
            "SYNTHESIS", v_file, "yosys", version,
            status="FAIL" if error_count > 0 else "ERROR",
            error_count=error_count,
            duration_ms=duration_ms,
            violations=violations,
        )

    # Parse stat JSON
    summary: dict = {
        "top_module": top,
        "cell_count": None,
        "wire_count": None,
        "flop_count": None,
        "estimated_area_um2": None,   # populated by OpenROAD when added
        "lut_utilization": None,       # populated when targeting FPGA PDK
        "critical_path_ns": None,      # populated by OpenSTA when added
        "pdk": None,
    }

    try:
        stat = json.loads(stat_path.read_text())
        # Yosys stat JSON: {"design": {"num_cells": N, "num_wires": N, ...}}
        design = stat.get("design", stat)
        summary["cell_count"] = design.get("num_cells")
        summary["wire_count"] = design.get("num_wires")
        # DFF count lives under "num_cells_by_type" or top-level "num_dffs"
        by_type = design.get("num_cells_by_type", {})
        dff_count = design.get("num_dffs") or sum(
            v for k, v in by_type.items() if "dff" in k.lower()
        )
        summary["flop_count"] = dff_count or None
    except Exception:
        pass  # stat parse failure is non-fatal; check still PASS
    finally:
        stat_path.unlink(missing_ok=True)

    return check_obj(
        "SYNTHESIS", v_file, "yosys", version,
        status="PASS",
        error_count=0,
        duration_ms=duration_ms,
        summary=summary,
    )
