"""DRC check — kicad-cli pcb drc → parse JSON report."""

import json
import re
import subprocess
import tempfile
from pathlib import Path

from ._base import check_obj, repo_rel, run_tool


def _kicad_version() -> str | None:
    rc, out, _, _ = run_tool(["kicad-cli", "version"])
    return out.strip().split()[0] if rc == 0 and out.strip() else None


def run_drc(pcb_file: Path) -> dict:
    version = _kicad_version()

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        report_path = Path(tmp.name)

    rc, stdout, stderr, duration_ms = run_tool([
        "kicad-cli", "pcb", "drc",
        "--output", str(report_path),
        "--format", "json",
        "--exit-code-violations",
        str(pcb_file),
    ])

    if not report_path.exists() or report_path.stat().st_size == 0:
        message = (stderr or stdout or "kicad-cli did not produce a DRC JSON report.").strip()
        return check_obj(
            "DRC", pcb_file, "kicad-cli", version,
            status="ERROR", error_count=1, duration_ms=duration_ms,
            violations=[{"type": "tool_error", "severity": "error",
                         "plain_text": message[:1000]}],
        )

    try:
        report = json.loads(report_path.read_text())
    except Exception as exc:
        message = (stderr or stdout or f"Could not parse DRC JSON report: {exc}").strip()
        return check_obj("DRC", pcb_file, "kicad-cli", version,
                         status="ERROR", error_count=1, duration_ms=duration_ms,
                         violations=[{"type": "parse_error", "severity": "error",
                                      "plain_text": message[:1000]}])
    finally:
        report_path.unlink(missing_ok=True)

    violations = []
    error_count = 0
    warning_count = 0

    for item in [*report.get("violations", []), *report.get("unconnected_items", [])]:
        sev = item.get("severity", "error").lower()
        if sev == "error":
            error_count += 1
        else:
            warning_count += 1

        v: dict = {
            "type": item.get("type", "drc_violation"),
            "severity": "error" if sev == "error" else "warning",
            "plain_text": item.get("description", ""),
        }
        items = []
        for d in item.get("items", []):
            entry: dict = {"description": d.get("description", "")}
            if "pos" in d:
                entry["pos"] = {"x": d["pos"].get("x", 0), "y": d["pos"].get("y", 0)}
            items.append(entry)
        if items:
            v["items"] = items
        violations.append(v)

    status = "PASS" if error_count == 0 else "FAIL"
    return check_obj(
        "DRC", pcb_file, "kicad-cli", version,
        status=status,
        error_count=error_count,
        warning_count=warning_count,
        duration_ms=duration_ms,
        violations=violations,
    )
