"""LVS (Layout vs Schematic) check — Netgen batch mode.

Standalone use: given a SPICE/CDL netlist + GDS file, runs Netgen LVS against
the sky130A setup file. Also called by openlane_flow.py to parse the LVS report
already produced by the OpenLane signoff stage.

PASS: "Netlists match uniquely." in Netgen output.
FAIL: any mismatch count > 0 or "Netlists do not match." present.
"""

import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_MATCH_RE = re.compile(r"netlists\s+match\s+uniquely", re.IGNORECASE)
_FAIL_RE = re.compile(r"netlists\s+do\s+not\s+match", re.IGNORECASE)
_DEVICE_MISMATCH_RE = re.compile(r"(\d+)\s+device[s]?\s+(?:in\s+circuit[12]\s+that\s+(?:are\s+)?not|mismatch)", re.IGNORECASE)
_NET_MISMATCH_RE = re.compile(r"(\d+)\s+net[s]?\s+(?:in\s+circuit[12]\s+that\s+(?:are\s+)?not|mismatch)", re.IGNORECASE)
_PROPERTY_ERROR_RE = re.compile(r"(\d+)\s+property\s+error", re.IGNORECASE)

_SKY130_SETUP = "/opt/pdks/sky130A/libs.tech/netgen/setup.tcl"

_NETGEN_VERSION_RE = re.compile(r"netgen\s+([\d.]+)", re.IGNORECASE)


def _netgen_version() -> str | None:
    rc, out, err, _ = run_tool(["netgen", "-batch", "exec", "set ::VERSION"])
    m = _netgen_version_RE = re.compile(r"([\d]+\.[\d]+[\w.]*)")
    m = _netgen_version_RE.search(out + err)
    return m.group(1) if m else None


def parse_lvs_report(text: str) -> tuple[dict, bool, list[dict]]:
    """Parse a Netgen LVS output text.

    Returns (summary_dict, passed, violations_list).
    """
    summary: dict = {
        "device_mismatches": 0,
        "deviceMismatches": 0,
        "net_mismatches": 0,
        "netMismatches": 0,
        "property_errors": 0,
        "propertyErrors": 0,
        "result": None,
    }
    violations: list[dict] = []

    if _MATCH_RE.search(text):
        summary["result"] = "match"
        return summary, True, violations

    if _FAIL_RE.search(text):
        summary["result"] = "mismatch"

    m = _DEVICE_MISMATCH_RE.search(text)
    if m:
        summary["device_mismatches"] = int(m.group(1))
        summary["deviceMismatches"] = summary["device_mismatches"]
    m = _NET_MISMATCH_RE.search(text)
    if m:
        summary["net_mismatches"] = int(m.group(1))
        summary["netMismatches"] = summary["net_mismatches"]
    m = _PROPERTY_ERROR_RE.search(text)
    if m:
        summary["property_errors"] = int(m.group(1))
        summary["propertyErrors"] = summary["property_errors"]

    total = summary["device_mismatches"] + summary["net_mismatches"] + summary["property_errors"]
    passed = total == 0 and summary["result"] != "mismatch"

    if not passed:
        for line in text.splitlines():
            if re.search(r"mismatch|error|not\s+match|differ", line, re.IGNORECASE):
                violations.append({
                    "type": "lvs_mismatch",
                    "severity": "error",
                    "plain_text": line.strip()[:200],
                })
                if len(violations) >= 20:
                    break

    return summary, passed, violations


def run_lvs_from_report(report_path: Path, tool_version: str | None = None) -> dict:
    """Build a check object from an already-produced Netgen report file."""
    if not report_path.exists():
        return check_obj(
            "LVS", report_path, "netgen", tool_version,
            status="ERROR", duration_ms=0,
            violations=[{"type": "missing_report", "severity": "error",
                         "plain_text": f"LVS report not found: {report_path.name}"}],
        )
    text = report_path.read_text(errors="replace")
    summary, passed, violations = parse_lvs_report(text)
    status = "PASS" if passed else ("FAIL" if summary["result"] == "mismatch" or not passed else "ERROR")
    return check_obj(
        "LVS", report_path, "netgen", tool_version,
        status=status,
        error_count=len(violations),
        duration_ms=0,
        violations=violations if violations else None,
        summary=summary,
    )


def run_lvs(netlist_file: Path, gds_file: Path, top_cell: str | None = None) -> dict:
    """Run Netgen LVS standalone: netlist + GDS → compare.

    top_cell: if None, Netgen will attempt to auto-detect.
    """
    version = _netgen_version()

    setup = _SKY130_SETUP if Path(_SKY130_SETUP).exists() else ""

    cell = top_cell or ""
    with tempfile.TemporaryDirectory() as tmpdir:
        result_file = Path(tmpdir) / "lvs_result.txt"

        # Netgen batch LVS command:
        # netgen -batch lvs "<gds cell>" "<netlist cell>" <setup.tcl> <results_file>
        gds_arg = f"{gds_file.resolve()} {cell}".strip()
        net_arg = f"{netlist_file.resolve()} {cell}".strip()

        cmd = ["netgen", "-batch", "lvs",
               gds_arg, net_arg]
        if setup:
            cmd.append(setup)
        cmd.append(str(result_file))

        rc, out, err, ms = run_tool(cmd, timeout=300)
        combined = out + err

        # Read the result file (Netgen writes a summary there)
        report_text = result_file.read_text(errors="replace") if result_file.exists() else combined

        summary, passed, violations = parse_lvs_report(report_text + "\n" + combined)
        status = "PASS" if passed else ("FAIL" if violations else "ERROR")

        return check_obj(
            "LVS", netlist_file, "netgen", version,
            status=status,
            error_count=len(violations),
            duration_ms=ms,
            violations=violations if violations else None,
            summary=summary,
        )
