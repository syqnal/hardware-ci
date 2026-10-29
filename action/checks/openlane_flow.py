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
from .gdsii import inspect_layout_file
from .lvs import run_lvs_from_report, _netgen_version


_SUPPORTED_PDKS = {"sky130a", "sky130b", "sky130", "gf180mcu"}
_OPENLANE_ERROR_RE = re.compile(
    r"(error|exception|traceback|failed|fatal|yosys|synthesis)",
    re.IGNORECASE,
)


def _tail_lines(text: str, limit: int = 80) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])[:6000]


def _openlane_log_excerpt(run_dir: Path | None, combined_output: str) -> str:
    """Return a compact log excerpt that explains why OpenLane stopped."""
    candidates: list[Path] = []
    if run_dir is not None:
        candidates = sorted(
            [
                path for path in run_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in {".log", ".txt", ".rpt"}
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    for path in candidates[:50]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _OPENLANE_ERROR_RE.search(text):
            excerpt = _tail_lines(text)
            if excerpt:
                return f"{path.name}\n{excerpt}"
    return _tail_lines(combined_output)


_STEP_RE = re.compile(r"Running '([^']+)'")
_LAST_LINES_RE = re.compile(r"Last \d+ line\(s\):")
_ERROR_LINE_RE = re.compile(r"\b(error|exception|fatal)\b", re.IGNORECASE)


def _flow_failure(excerpt: str) -> str | None:
    """'OpenLane stopped at <step>: <first error line>' from a flow log excerpt."""
    lines = [l.strip() for l in excerpt.splitlines() if l.strip()]
    step = None
    for line in lines:
        m = _STEP_RE.search(line)
        if m:
            step = m.group(1)
    error = None
    for i, line in enumerate(lines):
        if _LAST_LINES_RE.search(line) and i + 1 < len(lines):
            error = lines[i + 1]
            break
    if error is None:
        errors = [l for l in lines if _ERROR_LINE_RE.search(l) and "clear" not in l.lower()]
        error = errors[-1] if errors else None
    if not step and not error:
        return None
    return f"OpenLane stopped at {step or 'an early step'}" + (f": {error}" if error else "")


def _missing_stage_violation(stage: str, excerpt: str, stopped_step: str | None = None) -> list[dict]:
    message = f"OpenLane did not produce a {stage} report."
    reason = _flow_failure(excerpt) if excerpt else None
    # The excerpt may come from a step's own log; flow.log names the step that stopped.
    if reason and stopped_step and "an early step" in reason:
        reason = reason.replace("an early step", stopped_step)
    if reason:
        message = f"{reason}. {message}"
    if excerpt:
        # Keep the end of the log: that's where the error is.
        message = f"{message}\n\nLog excerpt:\n{excerpt[-4000:]}"
    return [{"type": "missing_openlane_report", "severity": "error", "plain_text": message[:5000]}]

# ── OpenLane 2 metrics ─────────────────────────────────────────────────────────
# OpenLane 2 writes every stage's numbers to <run>/final/metrics.json (also
# metrics.csv): instance counts, timing, routing/Magic/KLayout DRC, LVS and
# power. The per-report parsers below are the OpenLane 1 fallback.

def _latest_state(run_dir: Path) -> dict | None:
    """state_out.json of the last step that ran: metrics and design views so far.

    OpenLane 2 only writes final/ when the whole flow passes. A flow that ends
    on deferred errors (an XOR mismatch, say) still leaves every metric here.
    """
    states = sorted(run_dir.glob("[0-9]*-*/state_out.json"), key=lambda p: int(p.parent.name.split("-", 1)[0]))
    for path in reversed(states):
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _final_metrics(run_dir: Path) -> dict | None:
    final = run_dir / "final"
    path = final / "metrics.json"
    try:
        if path.is_file():
            data = json.loads(path.read_text(errors="replace"))
            return data if isinstance(data, dict) else None
        csv_path = final / "metrics.csv"
        if csv_path.is_file():
            rows = [line.split(",", 1) for line in csv_path.read_text(errors="replace").splitlines()[1:] if "," in line]
            return {k.strip(): v.strip() for k, v in rows}
    except (OSError, ValueError):
        return None
    state = _latest_state(run_dir)
    metrics = state.get("metrics") if state else None
    return metrics if isinstance(metrics, dict) and metrics else None


def _deferred_errors(run_dir: Path) -> list[str]:
    """Problems OpenLane deferred to the end of the flow (error.log), e.g. XOR differences."""
    try:
        text = (run_dir / "error.log").read_text(errors="replace")
    except OSError:
        return []
    return [line.strip().rstrip(".").removesuffix(" - deferred").rstrip(".") for line in text.splitlines() if line.strip()]


def _num(metrics: dict, *keys: str) -> float | None:
    for key in keys:
        v = metrics.get(key)
        try:
            if v is None or v == "":
                continue
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f and f not in (float("inf"), float("-inf")):  # skip NaN / inf
            return f
    return None


def _int(metrics: dict, *keys: str) -> int | None:
    v = _num(metrics, *keys)
    return int(v) if v is not None else None


def _synthesis_from_metrics(m: dict, top: str | None) -> dict | None:
    cells = _int(m, "design__instance__count__stdcell", "design__instance__count")
    if cells is None:
        return None
    area = _num(m, "design__instance__area__stdcell", "design__instance__area")
    return {
        "top_module": top, "topModule": top,
        "cell_count": cells, "cellCount": cells,
        "flop_count": None, "flopCount": None,
        "estimated_area_um2": area, "estimatedAreaUm2": area,
        "source": "openlane final metrics (post-route standard cells)",
    }


def _pnr_from_metrics(m: dict) -> dict | None:
    drc = _int(m, "route__drc_errors")
    if drc is None:
        return None
    util = _num(m, "design__instance__utilization__stdcell", "design__instance__utilization")
    wire = _num(m, "route__wirelength")
    die = _num(m, "design__die__area")
    return {
        "routing_drc_violations": drc, "routingDrcViolations": drc,
        "utilization_pct": round(util * 100, 2) if util is not None else None,
        "utilizationPct": round(util * 100, 2) if util is not None else None,
        "wire_length_um": wire, "wireLengthUm": wire,
        "die_area_um2": die, "dieAreaUm2": die,
        "antenna_violating_nets": _int(m, "antenna__violating__nets"),
    }


def _sta_from_metrics(m: dict) -> dict | None:
    ws = _num(m, "timing__setup__ws")
    if ws is None:
        return None
    hold = _num(m, "timing__hold__ws")
    tns = _num(m, "timing__setup__tns")
    failing = _int(m, "timing__setup_vio__count")
    return {
        # wns_ns carries the worst setup slack itself (positive when met), as Syqnal shows it.
        "wns_ns": ws, "wnsNs": ws,
        "tns_ns": tns, "tnsNs": tns,
        "failing_endpoints": failing, "failingEndpoints": failing,
        "worst_hold_slack_ns": hold, "worstHoldSlackNs": hold,
        "hold_violations": _int(m, "timing__hold_vio__count"),
        "critical_path_ns": None, "criticalPathNs": None,
    }


def _drc_from_metrics(m: dict) -> dict | None:
    magic = _int(m, "magic__drc_error__count")
    klayout = _int(m, "klayout__drc_error__count")
    if magic is None and klayout is None:
        return None
    total = (magic or 0) + (klayout or 0)
    tool = "magic+klayout" if magic is not None and klayout is not None else ("magic" if magic is not None else "klayout")
    return {"drc_violations": total, "drcViolations": total, "tool": tool,
            "magic_drc_errors": magic, "klayout_drc_errors": klayout}


def _power_from_metrics(m: dict) -> dict | None:
    total = _num(m, "power__total")
    if total is None:
        return None
    mw = lambda key: round(_num(m, key) * 1000, 6) if _num(m, key) is not None else None  # noqa: E731
    s = {
        "internal_power_mw": mw("power__internal__total"),
        "switching_power_mw": mw("power__switching__total"),
        "leakage_power_mw": mw("power__leakage__total"),
        "total_power_mw": round(total * 1000, 6),
    }
    s.update({"internalPowerMw": s["internal_power_mw"], "switchingPowerMw": s["switching_power_mw"],
              "leakagePowerMw": s["leakage_power_mw"], "totalPowerMw": s["total_power_mw"]})
    return s


def _lvs_from_metrics(m: dict, config_file: Path, version: str | None) -> dict | None:
    errors = _int(m, "design__lvs_error__count")
    if errors is None:
        return None
    return check_obj(
        "LVS", config_file, "netgen", version,
        status="PASS" if errors == 0 else "FAIL",
        error_count=errors,
        duration_ms=0,
        violations=None if errors == 0 else [{
            "type": "lvs_mismatch", "severity": "error",
            "plain_text": f"LVS: {errors} difference(s) between layout and netlist "
                          f"({_int(m, 'design__lvs_device_difference__count') or 0} device, "
                          f"{_int(m, 'design__lvs_net_difference__count') or 0} net)",
        }],
        summary={"result": "match" if errors == 0 else "mismatch", "lvs_errors": errors, "lvsErrors": errors},
    )


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
    summary["topModule"] = summary.get("top_module")
    summary["cellCount"] = summary.get("cell_count")
    summary["flopCount"] = summary.get("flop_count")
    summary["estimatedAreaUm2"] = summary.get("estimated_area_um2")
    return summary


def _parse_pnr(run_dir: Path) -> tuple[dict, int]:
    """Parse routing DRC report — violation count, utilization, wire length."""
    summary: dict = {"routing_drc_violations": None, "utilization_pct": None, "wire_length_um": None}
    violations = []

    drc_rpts = sorted((p for p in (run_dir / "reports" / "routing").glob("*drc*") if p.is_file()), reverse=True)
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
    for rpt in (p for p in (run_dir / "reports").rglob("*utilization*") if p.is_file()):
        text = rpt.read_text(errors="replace")
        m = re.search(r"Design\s+Utilization[^:]*:\s*([\d.]+)%", text, re.IGNORECASE)
        if m:
            summary["utilization_pct"] = float(m.group(1))
            break

    drc_count = summary.get("routing_drc_violations") or 0
    summary["routingDrcViolations"] = summary.get("routing_drc_violations")
    summary["utilizationPct"] = summary.get("utilization_pct")
    summary["wireLengthUm"] = summary.get("wire_length_um")
    return summary, len(violations), violations


def _parse_sta(run_dir: Path) -> tuple[dict, bool]:
    """Parse OpenSTA timing report — WNS, TNS, failing endpoint count."""
    summary: dict = {"wns_ns": None, "tns_ns": None, "failing_endpoints": None, "critical_path_ns": None}
    passed = True

    sta_rpts = sorted((p for p in (run_dir / "reports" / "signoff").glob("*sta*") if p.is_file()), reverse=True)
    if not sta_rpts:
        sta_rpts = sorted((p for p in run_dir.rglob("*_sta.rpt") if p.is_file()), reverse=True)

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

    summary["wnsNs"] = summary.get("wns_ns")
    summary["tnsNs"] = summary.get("tns_ns")
    summary["failingEndpoints"] = summary.get("failing_endpoints")
    summary["criticalPathNs"] = summary.get("critical_path_ns")
    return summary, passed


def _parse_silicon_drc(run_dir: Path) -> tuple[dict, int]:
    """Parse signoff DRC report — KLayout or Magic violation count."""
    summary: dict = {"drc_violations": None, "tool": None}
    violations = []

    # KLayout DRC (OpenLane2 style)
    # Files only: OpenLane 2 step directories match these patterns too (65-checker-klayoutdrc).
    klayout_rpts = sorted((p for p in (run_dir / "reports" / "signoff").glob("*drc*") if p.is_file()), reverse=True)
    if not klayout_rpts:
        klayout_rpts = sorted((p for p in run_dir.rglob("*klayout*drc*") if p.is_file()), reverse=True)

    if klayout_rpts:
        text = klayout_rpts[0].read_text(errors="replace")
        m = re.search(r"(\d+)\s+(?:total\s+)?(?:drc\s+)?violations?", text, re.IGNORECASE)
        if m:
            count = int(m.group(1))
            summary["drc_violations"] = count
            summary["drcViolations"] = count
            summary["tool"] = "klayout"
            for line in text.splitlines():
                if re.search(r"violation|error", line, re.IGNORECASE):
                    violations.append({"type": "silicon_drc", "severity": "error",
                                       "plain_text": line.strip()[:200]})
                    if len(violations) >= 20:
                        break
            return summary, count, violations

    # Magic DRC fallback
    magic_rpts = sorted((p for p in run_dir.rglob("*magic*drc*") if p.is_file()), reverse=True)
    if magic_rpts:
        text = magic_rpts[0].read_text(errors="replace")
        count = len(re.findall(r"^\[ERROR\]", text, re.MULTILINE))
        summary["drc_violations"] = count
        summary["drcViolations"] = count
        summary["tool"] = "magic"
        return summary, count, []

    summary["drcViolations"] = summary.get("drc_violations")
    return summary, 0, []


def _parse_power(run_dir: Path) -> dict | None:
    """Parse OpenROAD power report — total/internal/switching/leakage in mW.

    OpenROAD report format (from report_power):
        Group       Internal  Switching   Leakage      Total
        ...
        Total       1.61e-03  7.07e-04   5.14e-05   2.37e-03  100%
    Units are Watts; we convert to mW for readability.
    """
    candidates = sorted((p for p in (run_dir / "reports" / "signoff").glob("*power*") if p.is_file()), reverse=True)
    if not candidates:
        candidates = sorted((p for p in run_dir.rglob("*power*.rpt") if p.is_file()), reverse=True)
    if not candidates:
        return None

    text = candidates[0].read_text(errors="replace")

    # Match the "Total" summary row: 4 scientific-notation numbers
    total_re = re.compile(
        r"^Total\s+([\d.e+\-]+)\s+([\d.e+\-]+)\s+([\d.e+\-]+)\s+([\d.e+\-]+)",
        re.MULTILINE | re.IGNORECASE,
    )
    m = total_re.search(text)
    if not m:
        return None

    def to_mw(s: str) -> float:
        return round(float(s) * 1000, 4)

    summary = {
        "internal_power_mw": to_mw(m.group(1)),
        "switching_power_mw": to_mw(m.group(2)),
        "leakage_power_mw":   to_mw(m.group(3)),
        "total_power_mw":     to_mw(m.group(4)),
    }
    summary["internalPowerMw"] = summary["internal_power_mw"]
    summary["switchingPowerMw"] = summary["switching_power_mw"]
    summary["leakagePowerMw"] = summary["leakage_power_mw"]
    summary["totalPowerMw"] = summary["total_power_mw"]
    return summary


def _parse_lvs(run_dir: Path, tool_version: str | None) -> dict | None:
    """Find and parse Netgen LVS report produced by OpenLane signoff."""
    # OpenLane2 places LVS reports under reports/signoff/ with *lvs* in the name
    candidates = sorted((p for p in (run_dir / "reports" / "signoff").glob("*lvs*") if p.is_file()), reverse=True)
    if not candidates:
        candidates = sorted((p for p in run_dir.rglob("*lvs*.rpt") if p.is_file()), reverse=True)
    if not candidates:
        return None
    return run_lvs_from_report(candidates[0], tool_version)


def _parse_gdsii(run_dir: Path, config_dir: Path) -> tuple[dict, bool]:
    """Check that a final GDS was produced and is non-empty."""
    # OpenLane 2: final/gds, or the last step's GDS view when final/ wasn't written;
    # OpenLane 1: results/final/gds
    gds_files = list((run_dir / "final" / "gds").glob("*.gds"))
    if not gds_files:
        state = _latest_state(run_dir) or {}
        view = state.get("gds")
        if isinstance(view, str):
            hits = [p for p in run_dir.rglob(Path(view).name) if p.is_file()]
            gds_files = hits[:1]
    if not gds_files:
        gds_files = list((run_dir / "results" / "final" / "gds").glob("*.gds"))
    if not gds_files:
        gds_files = list(run_dir.rglob("*.gds"))
        gds_files = [f for f in gds_files if "final" in str(f) or "gds" in f.parent.name]

    if not gds_files:
        return {"gds_produced": False}, False

    gds = gds_files[0]
    size_bytes = gds.stat().st_size
    summary, violations, status, _ms, _version = inspect_layout_file(gds)
    summary = {
        **summary,
        "gds_produced": True,
        "gdsProduced": True,
        "gds_path": str(gds.relative_to(config_dir) if gds.is_relative_to(config_dir) else gds.name),
        "gdsPath": str(gds.relative_to(config_dir) if gds.is_relative_to(config_dir) else gds.name),
        "gds_size_bytes": size_bytes,
        "gdsSizeBytes": size_bytes,
    }
    if violations:
        summary["layout_preview_error"] = violations[0].get("plain_text")
        summary["layoutPreviewError"] = violations[0].get("plain_text")
    return summary, size_bytes > 0 and status == "PASS"


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
    pdk = ""
    try:
        text = config_file.read_text(errors="replace")
        if config_file.suffix.lower() == ".json":
            cfg = json.loads(text)
            pdk = str(cfg.get("PDK", cfg.get("pdk", ""))).lower()
        else:
            m = re.search(r"set\s+(?:::env\()?PDK\)?\s+\"?([A-Za-z0-9_\\-.]+)\"?", text)
            pdk = (m.group(1) if m else "").lower()
    except Exception:
        return [check_obj(
            "SYNTHESIS", config_file, "openlane", version,
            status="ERROR", duration_ms=0,
            violations=[{"type": "config_error", "severity": "error",
                         "plain_text": f"Cannot parse {config_file.name} as an OpenLane config"}],
        )]

    if not any(p in pdk for p in _SUPPORTED_PDKS):
        return []  # Not an OpenLane project we support — skip silently

    pdk_root = os.environ.get("PDK_ROOT", "/opt/pdks")
    config_dir = config_file.parent

    rc, out, err, ms = run_tool(
        # --manual-pdk: use the PDK baked into the image, never download one.
        ["python3", "-m", "openlane", "--manual-pdk", "--pdk-root", pdk_root,
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

    flow_excerpt = _openlane_log_excerpt(run_dir, combined)
    try:
        steps = _STEP_RE.findall((run_dir / "flow.log").read_text(errors="replace"))
    except OSError:
        steps = []
    stopped_step = steps[-1] if steps else None
    results: list[dict] = []
    metrics = _final_metrics(run_dir) or {}
    try:
        design_name = json.loads(config_file.read_text(errors="replace")).get("DESIGN_NAME") if config_file.suffix == ".json" else None
    except ValueError:
        design_name = None

    # 1 — SYNTHESIS
    synth_summary = _synthesis_from_metrics(metrics, design_name) or _parse_synthesis(run_dir)
    synth_status = "PASS" if synth_summary and synth_summary.get("cell_count") else "FAIL"
    # Carry over placeholder fields the existing SYNTHESIS check already defines
    if synth_summary:
        synth_summary.setdefault("wire_count", None)
        synth_summary.setdefault("lut_utilization", None)
        synth_summary["pdk"] = pdk
        synth_summary["openlaneConfig"] = str(config_file)
        synth_summary["openlaneRunDir"] = str(run_dir.relative_to(config_dir) if run_dir.is_relative_to(config_dir) else run_dir)
    else:
        synth_summary = {
            "pdk": pdk,
            "openlaneConfig": str(config_file),
            "openlaneRunDir": str(run_dir.relative_to(config_dir) if run_dir.is_relative_to(config_dir) else run_dir),
            "openlaneLogExcerpt": flow_excerpt,
        }
    results.append(check_obj(
        "SYNTHESIS", config_file, "openlane", version,
        status=synth_status,
        duration_ms=ms,
        error_count=0 if synth_status == "PASS" else 1,
        violations=None if synth_status == "PASS" else _missing_stage_violation("synthesis", flow_excerpt, stopped_step),
        summary=synth_summary,
    ))

    # 2 — PNR (Place & Route)
    pnr_metrics = _pnr_from_metrics(metrics)
    if pnr_metrics is not None:
        count = pnr_metrics["routing_drc_violations"]
        pnr_summary, pnr_viol_count = pnr_metrics, count
        pnr_violations = [{"type": "routing_drc", "severity": "error",
                           "plain_text": f"{count} routing DRC violation(s) after detailed routing"}] if count else []
        antenna = pnr_metrics.get("antenna_violating_nets")
        if antenna:
            pnr_violations.append({"type": "antenna", "severity": "warning",
                                   "plain_text": f"{antenna} net(s) violate antenna rules (add diodes or enable antenna repair)"})
    else:
        pnr_summary, pnr_viol_count, pnr_violations = _parse_pnr(run_dir)
    pnr_status = "PASS" if pnr_viol_count == 0 and pnr_summary.get("routing_drc_violations") is not None else (
        "FAIL" if pnr_viol_count > 0 else "ERROR"
    )
    results.append(check_obj(
        "PNR", config_file, "openroad", version,
        status=pnr_status,
        error_count=pnr_viol_count if pnr_status != "ERROR" else 1,
        warning_count=sum(1 for v in pnr_violations if v.get("severity") == "warning"),
        duration_ms=ms,
        violations=pnr_violations if pnr_violations else (
            _missing_stage_violation("place-and-route", flow_excerpt, stopped_step) if pnr_status == "ERROR" else None
        ),
        summary=pnr_summary,
    ))

    # 3 — STA (Static Timing Analysis)
    sta_metrics = _sta_from_metrics(metrics)
    if sta_metrics is not None:
        sta_summary = sta_metrics
        sta_passed = sta_metrics["wns_ns"] >= 0 and (sta_metrics.get("worst_hold_slack_ns") is None or sta_metrics["worst_hold_slack_ns"] >= 0)
    else:
        sta_summary, sta_passed = _parse_sta(run_dir)
    sta_status = "PASS" if sta_passed and sta_summary.get("wns_ns") is not None else (
        "FAIL" if not sta_passed else "ERROR"
    )
    results.append(check_obj(
        "STA", config_file, "opensta", version,
        status=sta_status,
        error_count=1 if sta_status == "ERROR" else (sta_summary.get("failing_endpoints") or (1 if sta_status == "FAIL" else 0)),
        duration_ms=ms,
        violations=_missing_stage_violation("timing", flow_excerpt, stopped_step) if sta_status == "ERROR" else None,
        summary=sta_summary,
    ))

    # 4 — SILICON_DRC
    drc_metrics = _drc_from_metrics(metrics)
    if drc_metrics is not None:
        drc_summary, drc_count = drc_metrics, drc_metrics["drc_violations"]
        drc_violations = [{"type": "silicon_drc", "severity": "error",
                           "plain_text": f"{drc_count} signoff DRC violation(s) ({drc_metrics['tool']})"}] if drc_count else []
    else:
        drc_summary, drc_count, drc_violations = _parse_silicon_drc(run_dir)
    drc_status = "PASS" if drc_summary.get("drc_violations") == 0 else (
        "FAIL" if drc_count > 0 else "ERROR"
    )
    results.append(check_obj(
        "SILICON_DRC", config_file, drc_summary.get("tool") or "klayout", version,
        status=drc_status,
        error_count=drc_count if drc_status != "ERROR" else 1,
        duration_ms=ms,
        violations=drc_violations if drc_violations else (
            _missing_stage_violation("silicon DRC", flow_excerpt, stopped_step) if drc_status == "ERROR" else None
        ),
        summary=drc_summary,
    ))

    # 5 — GDSII
    gds_summary, gds_ok = _parse_gdsii(run_dir, config_dir)
    # Problems OpenLane deferred to the end (XOR differences, power-grid notes)
    # make it skip final/ but don't invalidate the layout: report them as warnings.
    deferred = _deferred_errors(run_dir)
    flow_complete = (run_dir / "final").is_dir()
    gds_summary = {**gds_summary, "flow_complete": flow_complete, "flowComplete": flow_complete}
    if deferred:
        gds_summary["deferred_errors"] = deferred
    results.append(check_obj(
        "GDSII", config_file, "openlane", version,
        status="PASS" if gds_ok else "FAIL",
        error_count=0 if gds_ok else 1,
        warning_count=len(deferred) if gds_ok else 0,
        duration_ms=ms,
        violations=[{"type": "openlane_deferred", "severity": "warning", "plain_text": f"OpenLane: {d}"} for d in deferred] or None
        if gds_ok else [{"type": "no_gds", "severity": "error",
                         "plain_text": f"OpenLane flow did not produce a final GDS.\n\nLog excerpt:\n{flow_excerpt}"[:5000]}],
        summary={**gds_summary, "openlaneLogExcerpt": flow_excerpt} if not gds_ok else gds_summary,
    ))

    # 6 — POWER (OpenROAD report_power, only if report exists)
    # Only a finished flow's power: a stopped run leaves pre-route estimates behind.
    power_summary = _power_from_metrics(metrics) or (_parse_power(run_dir) if gds_ok else None)
    if power_summary is not None:
        results.append(check_obj(
            "POWER", config_file, "openroad", version,
            status="PASS",  # power is a metric — PASS means the report was produced
            duration_ms=ms,
            summary=power_summary,
        ))

    # 7 — LVS (Netgen, only if report exists — OpenLane runs LVS as part of signoff)
    netgen_ver = _netgen_version()
    lvs_check = _lvs_from_metrics(metrics, config_file, netgen_ver) or _parse_lvs(run_dir, netgen_ver)
    if lvs_check is not None:
        results.append(lvs_check)

    return results
