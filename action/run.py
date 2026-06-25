"""
Syqnal Hardware CI — action orchestrator.

Reads INPUT_RUN_* env vars, discovers target files, runs EDA tools,
and writes syqnal-verification.json in the schema expected by
Syqnal's verificationImport.ts.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from checks.drc import run_drc
from checks.erc import run_erc
from checks.rtl_lint import run_rtl_lint
from checks.rtl_sim import run_rtl_sim, is_testbench_path
from checks.vhdl_sim import run_vhdl_sim, is_vhdl_testbench_path
from checks.spice import run_spice
from checks.bom import run_bom
from checks.step import run_step
from checks.gerber import run_gerber
from checks.synthesis import run_synthesis
from checks.formal import run_formal
from checks.gdsii import run_gdsii
from checks.lvs import run_lvs
from checks.openlane_flow import run_openlane_flow

ACTION_VERSION = "2.5.3"


def env(key: str) -> bool:
    return os.getenv(key, "false").lower() == "true"


def find(pattern: str) -> list[Path]:
    return sorted(Path(".").rglob(pattern))


def main() -> None:
    checks: list[dict] = []

    if env("INPUT_RUN_DRC"):
        for f in find("*.kicad_pcb"):
            checks.append(run_drc(f))

    if env("INPUT_RUN_ERC"):
        for f in find("*.kicad_sch"):
            # Skip sub-sheets — only run ERC on top-level schematic (no parent ref)
            checks.append(run_erc(f))

    if env("INPUT_RUN_RTL_LINT"):
        for f in find("*.v") + find("*.sv"):
            # Skip testbench files — RTL_SIM handles those
            if _is_testbench(f):
                continue
            checks.append(run_rtl_lint(f))

    if env("INPUT_RUN_RTL_SIM"):
        for f in find("*.v") + find("*.sv"):
            if _is_testbench(f):
                checks.append(run_rtl_sim(f))
        for f in find("*.vhd") + find("*.vhdl"):
            if is_vhdl_testbench_path(f):
                checks.append(run_vhdl_sim(f))

    if env("INPUT_RUN_SPICE"):
        for f in find("*.cir") + find("*.sp") + find("*.asc"):
            checks.append(run_spice(f))

    if env("INPUT_RUN_BOM"):
        for f in find("bom.csv") + find("*_bom.csv") + find("*-bom.csv"):
            checks.append(run_bom(f))

    if env("INPUT_RUN_STEP"):
        for f in find("*.step") + find("*.stp"):
            checks.append(run_step(f))

    if env("INPUT_RUN_GERBER"):
        gerber_dirs = _find_gerber_dirs()
        for d in gerber_dirs:
            checks.append(run_gerber(d))

    if env("INPUT_RUN_SYNTHESIS"):
        for f in find("*.v") + find("*.sv"):
            if not _is_testbench(f):
                checks.append(run_synthesis(f))

    if env("INPUT_RUN_FORMAL"):
        for f in find("*.sby"):
            checks.append(run_formal(f))

    if env("INPUT_RUN_GDSII"):
        for f in find("*.gds") + find("*.oas") + find("*.gds.gz"):
            checks.append(run_gdsii(f))

    if env("INPUT_RUN_LVS"):
        # Standalone LVS: pair each .spice/.cdl with a .gds of the same stem
        for net in find("*.spice") + find("*.cdl"):
            gds = net.with_suffix(".gds")
            if not gds.exists():
                gds_matches = list(Path(".").rglob(net.stem + ".gds"))
                gds = gds_matches[0] if gds_matches else None
            if gds:
                checks.append(run_lvs(net, gds))

    if env("INPUT_RUN_OPENLANE"):
        for f in find("config.json") + find("config.tcl"):
            checks.extend(run_openlane_flow(f))  # emits one check per pipeline stage

    artifact = {
        "schema_version": "1.0",
        "syqnal_action_version": ACTION_VERSION,
        "commit_sha": os.environ["GITHUB_SHA"],
        "repo": os.environ["GITHUB_REPOSITORY"],
        "branch": os.environ.get("GITHUB_REF_NAME"),
        "triggered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runner_os": "Linux",
        "checks": checks,
    }

    out_path = Path("syqnal-verification.json")
    out_path.write_text(json.dumps(artifact, indent=2))
    print(f"[syqnal] wrote {out_path} with {len(checks)} check(s)")

    # Exit 0 always — individual check failures are communicated via the artifact,
    # not the process exit code. The upload-artifact step must always run.


def _is_testbench(path: Path) -> bool:
    return is_testbench_path(path)


def _find_gerber_dirs() -> list[Path]:
    """Return directories that look like Gerber output folders."""
    gerber_exts = {".gbr", ".gtl", ".gbl", ".gts", ".gbs", ".gko", ".drl"}
    seen: set[Path] = set()
    results: list[Path] = []
    for ext in gerber_exts:
        for f in Path(".").rglob(f"*{ext}"):
            d = f.parent
            if d not in seen:
                seen.add(d)
                results.append(d)
    return results


if __name__ == "__main__":
    main()
