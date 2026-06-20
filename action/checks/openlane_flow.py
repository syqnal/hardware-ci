"""OpenLane flow check — runs the full RTL→GDSII pipeline for sky130 designs.

Detects an OpenLane config.json, runs the complete flow, and emits one
check object per pipeline stage: SYNTHESIS, PNR, STA, SILICON_DRC, GDSII.
Each check is independently PASS/FAIL so Syqnal can show per-stage badges.

Requires: openlane (pip), sky130A PDK at $PDK_ROOT, KLayout.
"""

import json
import os
import re
from pathlib import Path

from ._base import check_obj, run_tool


_SUPPORTED_PDKS = {"sky130a", "sky130b", "sky130", "gf180mcu"}

# ── Report parsers ─────────────────────────────────────────────────────────────

def _parse_synthesis(run_dir: Path) -> dict | None:
    """Parse Yosys stat report — cell count, flop count, area estimate."""
    rpts = sorted((run_dir / "reports" / "synthesis").glob("*.stat.rpt"), reverse=True)
    if not rpts:
        return None
    text = rpts[0].read_text(errors="replace")
    summary: dict = {"top_module": None, "cell_count": None, "flop_count": None, "estimated_area_um2": None}
    m = re.search(r"Number of cells:\s*(\d+)", text)
    if m: summary["cell_count"] = int(m.group(1))
    m = re.search(r"Flop[^:]*:\s*(\d+)", text, re.IGNORECASE)
    if m: summary["flop_count"] = int(m.group(1))
    m = re.search(r"Chip area[^:]*:\s*([\d.]+)", text, re.IGNORECASE)
    if m: summary["estimated_area_um2"] = float(m.group(1))
    m = re.search(r"Design\s+(\S+)", text)
    if m: summary["top_module"] = m.group(1)
    return summary


def _parse_pnr(run_dir: Path) -> tuple[dict, int]:
    """Parse routing DRC report — violation count, utilization, wire length."""
    summary: dict = {"routing_drc_violations": None, "utilization_pct": None, "wire_length_um": None}
    violations = []

    drc_rpts = sorted((run_dir / "reports" / "routing").glob("*drc*"), reverse=True)
    if drc_rpts:
        text = drc_rpts[0].read_text(errors="replace")
        m = re.search(r"^\s*Total\s+[Vv]iolations?:?\s*(\d+)", text, re.MULTILINE)
        if m:
            count = int(m.group(1))
            summary["routing_drc_violations"] = count
            if count > 0:
                for line in text.splitlines():
                    if re.search(r"violation|error", line, re.IGNORECASE):
                        violations.append({"type": "routing_drc", "severity": "error",
                                           "plain_text": line.strip()[:200]})
                        if len(violations) >= 20:
                            break

    # Parse utilization from OpenROAD log or summary
    for rpt in (run_dir / "reports").rglob("*utilization*"):
        text = rpt.read_text(errors="replace")
        m = re.search(r"Design\s+Utilization[^:]*:\s*([\d.]+)%", text, re.IGNORECASE)
        if m:
            summary["utilization_pct"] = float(m.group(1))
            break

    drc_count = summary.get("routing_drc_violations") or 0
    return summary, len(violations), violations


def _parse_sta(run_dir: Path) -> tuple[dict, bool]:
    """Parse OpenSTA timing report — WNS, TNS, failing endpoint count."""
    summary: dict = {"wns_ns": None, "tns_ns": None, "failing_endpoints": None, "critical_path_ns": None}
    passed = True

    sta_rpts = sorted((run_dir / "reports" / "signoff").glob("*sta*"), reverse=True)
    if not sta_rpts:
        sta_rpts = sorted(run_dir.rglob("*_sta.rpt"), reverse=True)

    for rpt in sta_rpts:
        text = rpt.read_text(errors="replace")
        m = re.search(r"wns\s*([-\d.]+)", text, re.IGNORECASE)
        if m: summary["wns_ns"] = float(m.group(1))
        m = re.search(r"tns\s*([-\d.]+)", text, re.IGNORECASE)
        if m: summary["tns_ns"] = float(m.group(1))
        m = re.search(r"(\d+)\s+failing\s+endpoint", text, re.IGNORECASE)
        if m: summary["failing_endpoints"] = int(m.group(1))
        # Critical path = data path delay from first "slack" block
        m = re.search(r"data arrival time\s+([\d.]+)", text)
        if m: summary["critical_path_ns"] = float(m.group(1))
        if summary["wns_ns"] is not None:
            break

    wns = summary.get("wns_ns")
    if wns is not None and wns < 0:
        passed = False

    return summary, passed


def _parse_silicon_drc(run_dir: Path) -> tuple[dict, int]:
    """Parse signoff DRC report — KLayout or Magic violation count."""
    summary: dict = {"drc_violations": None, "tool": None}
    violations = []

    # KLayout DRC (OpenLane2 style)
    klayout_rpts = sorted((run_dir / "reports" / "signoff").glob("*drc*"), reverse=True)
    if not klayout_rpts:
        klayout_rpts = sorted(run_dir.rglob("*klayout*drc*"), reverse=True)

    if klayout_rpts:
        text = klayout_rpts[0].read_text(errors="replace")
        m = re.search(r"(\d+)\s+(?:total\s+)?(?:drc\s+)?violations?", text, re.IGNORECASE)
        if m:
            count = int(m.group(1))
            summary["drc_violations"] = count
            summary["tool"] = "klayout"
            for line in text.splitlines():
                if re.search(r"violation|error", line, re.IGNORECASE):
                    violations.append({"type": "silicon_drc", "severity": "error",
                                       "plain_text": line.strip()[:200]})
                    if len(violations) >= 20:
                        break
            return summary, count, violations

    # Magic DRC fallback
    magic_rpts = sorted(run_dir.rglob("*magic*drc*"), reverse=True)
    if magic_rpts:
        text = magic_rpts[0].read_text(errors="replace")
        count = len(re.findall(r"^\[ERROR\]", text, re.MULTILINE))
        summary["drc_violations"] = count
        summary["tool"] = "magic"
        return summary, count, []

    return summary, 0, []


def _parse_gdsii(run_dir: Path, config_dir: Path) -> tuple[dict, bool]:
    """Check that a final GDS was produced and is non-empty."""
    gds_files = list((run_dir / "results" / "final" / "gds").glob("*.gds"))
    if not gds_files:
        gds_files = list(run_dir.rglob("*.gds"))
        gds_files = [f for f in gds_files if "final" in str(f) or "gds" in f.parent.name]

    if not gds_files:
        return {"gds_produced": False}, False

    gds = gds_files[0]
    size_bytes = gds.stat().st_size
    return {
        "gds_produced": True,
        "gds_path": str(gds.relative_to(config_dir) if gds.is_relative_to(config_dir) else gds.name),
        "gds_size_bytes": size_bytes,
    }, size_bytes > 0


# ── OpenLane version ───────────────────────────────────────────────────────────

def _openlane_version() -> str | None:
    rc, out, err, _ = run_tool(["python3", "-m", "openlane", "--version"])
    for line in (out + err).splitlines():
        m = re.search(r"([\d]+\.[\d]+\.[\d]+)", line)
        if m:
            return m.group(1)
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def run_openlane_flow(config_file: Path) -> list[dict]:
    """Run the full OpenLane flow and return one check dict per pipeline stage."""
    version = _openlane_version()

    # Validate this is an OpenLane config with a supported PDK
    try:
        cfg = json.loads(config_file.read_text())
    except Exception:
        return [check_obj(
            "SYNTHESIS", config_file, "openlane", version,
            status="ERROR", duration_ms=0,
            violations=[{"type": "config_error", "severity": "error",
                         "plain_text": f"Cannot parse {config_file.name} as JSON"}],
        )]

    pdk = str(cfg.get("PDK", cfg.get("pdk", ""))).lower()
    if not any(p in pdk for p in _SUPPORTED_PDKS):
        return []  # Not an OpenLane project we support — skip silently

    pdk_root = os.environ.get("PDK_ROOT", "/opt/pdks")
    config_dir = config_file.parent

    rc, out, err, ms = run_tool(
        ["python3", "-m", "openlane", "--pdk-root", pdk_root,
         "--run-tag", "syqnal_ci", str(config_file.resolve())],
        cwd=config_dir,
        timeout=3600,
    )

    combined = out + err

    # Find the run output directory
    run_dir: Path | None = None
    for candidate in sorted((config_dir / "runs").glob("syqnal_ci*"), reverse=True):
        if candidate.is_dir():
            run_dir = candidate
            break
    if run_dir is None:
        # Try OpenLane1 style
        for candidate in sorted((config_dir / "runs").glob("RUN_*"), reverse=True):
            if candidate.is_dir():
                run_dir = candidate
                break

    if run_dir is None:
        error_msg = (err or combined).strip()[:500]
        return [check_obj(
            "SYNTHESIS", config_file, "openlane", version,
            status="ERROR", duration_ms=ms,
            violations=[{"type": "flow_error", "severity": "error", "plain_text": error_msg}],
        )]

    results: list[dict] = []

    # 1 — SYNTHESIS
    synth_summary = _parse_synthesis(run_dir)
    synth_status = "PASS" if synth_summary and synth_summary.get("cell_count") else "FAIL"
    # Carry over placeholder fields the existing SYNTHESIS check already defines
    if synth_summary:
        synth_summary.setdefault("wire_count", None)
        synth_summary.setdefault("lut_utilization", None)
        synth_summary["pdk"] = pdk
    results.append(check_obj(
        "SYNTHESIS", config_file, "openlane", version,
        status=synth_status,
        duration_ms=ms,
        summary=synth_summary or {},
    ))

    # 2 — PNR (Place & Route)
    pnr_summary, pnr_viol_count, pnr_violations = _parse_pnr(run_dir)
    pnr_status = "PASS" if pnr_viol_count == 0 and pnr_summary.get("routing_drc_violations") is not None else (
        "FAIL" if pnr_viol_count > 0 else "ERROR"
    )
    results.append(check_obj(
        "PNR", config_file, "openroad", version,
        status=pnr_status,
        error_count=pnr_viol_count,
        duration_ms=ms,
        violations=pnr_violations if pnr_violations else None,
        summary=pnr_summary,
    ))

    # 3 — STA (Static Timing Analysis)
    sta_summary, sta_passed = _parse_sta(run_dir)
    sta_status = "PASS" if sta_passed and sta_summary.get("wns_ns") is not None else (
        "FAIL" if not sta_passed else "ERROR"
    )
    results.append(check_obj(
        "STA", config_file, "opensta", version,
        status=sta_status,
        duration_ms=ms,
        summary=sta_summary,
    ))

    # 4 — SILICON_DRC
    drc_summary, drc_count, drc_violations = _parse_silicon_drc(run_dir)
    drc_status = "PASS" if drc_summary.get("drc_violations") == 0 else (
        "FAIL" if drc_count > 0 else "ERROR"
    )
    results.append(check_obj(
        "SILICON_DRC", config_file, drc_summary.get("tool") or "klayout", version,
        status=drc_status,
        error_count=drc_count,
        duration_ms=ms,
        violations=drc_violations if drc_violations else None,
        summary=drc_summary,
    ))

    # 5 — GDSII
    gds_summary, gds_ok = _parse_gdsii(run_dir, config_dir)
    results.append(check_obj(
        "GDSII", config_file, "openlane", version,
        status="PASS" if gds_ok else "FAIL",
        duration_ms=ms,
        violations=None if gds_ok else [{"type": "no_gds", "severity": "error",
                                          "plain_text": "OpenLane flow did not produce a final GDS"}],
        summary=gds_summary,
    ))

    return results
