# syqnal/hardware-ci

GitHub Actions Docker action that runs EDA verification checks and emits
`syqnal-verification.json` — consumed by Syqnal's webhook handler to surface
proof-of-work results on project pages.

## Supported checks

| Input flag | Tool | Check type |
|---|---|---|
| `run_drc` | kicad-cli | PCB design rule check |
| `run_erc` | kicad-cli | Schematic electrical rule check |
| `run_rtl_lint` | Verilator | Verilog/SV lint |
| `run_rtl_sim` | Icarus Verilog + vvp, GHDL | HDL testbench simulation |
| `run_spice` | ngspice | SPICE convergence + error check |
| `run_bom` | built-in CSV parser | BOM column validation |
| `run_step` | FreeCAD CLI / header check | STEP geometry validation |
| `run_gerber` | built-in layer check | Gerber fabrication layer completeness |
| `run_synthesis` | Yosys | RTL synthesis — cell/wire/flop counts |
| `run_formal` | SymbiYosys (yices, z3, boolector) | Formal proof / cover runs from `.sby` files; `[files]` paths may be relative to the `.sby` file's folder or the repo root |
| `run_gdsii` | KLayout | Standalone GDS/OAS validation, metadata extraction, and SVG preview generation |
| `run_lvs` | Netgen | Standalone layout-vs-schematic for paired SPICE/CDL + GDS |
| `run_openlane` | OpenLane/OpenROAD | RTL→GDSII flow from `config.json` or `config.tcl`, emitting synthesis, P&R, STA, DRC, power, LVS, GDSII checks |

## Output schema

`syqnal-verification.json` — see Syqnal's `src/lib/verificationImport.ts` for
the full type definition consumed on the platform side.

GDSII/OAS checks include `summary.layout_preview_svg_b64` and
`summary.layoutPreviewSvgB64` when KLayout can render the actual geometry into a
compact SVG preview. Syqnal displays that preview in the public CI verification
panel alongside top cell, layer count, bounding box, and file size metadata.

## Runner image

The GitHub Action uses the prebuilt container image:

```text
ghcr.io/syqnal/hardware-ci:2.5.8
```

The image is intentionally heavy: it contains the hardware and IC toolchain plus
the open PDK data needed for repeatable CI runs. Project workflows should not
build the image on every run.

Included toolchain:

| Domain | Tools |
|---|---|
| PCB | KiCad CLI |
| RTL/HDL | Icarus Verilog, GHDL, Verilator, Yosys, SymbiYosys |
| SPICE | ngspice |
| IC physical design | OpenLane, OpenROAD, OpenSTA |
| IC signoff/viewing | KLayout, Magic, Netgen |
| PDK | sky130A by default; GF180 can be enabled at image build time |

### Pinned tool versions

Rebuilding the image must not change anyone's results, so every tool is pinned
in the `Dockerfile`. Change a pin only in a release, and rerun a known project
(e.g. `skillsbuffcom/tensorrail`) to compare results before moving the worker.

| Tool | Version | Pinned by |
|---|---|---|
| Yosys | 0.66 (git 86f2ddebc) | `hdlc/yosys` image digest |
| Yosys for OpenLane | 0.46, built with Python | `YOSYS_OPENLANE_REF` (the version OpenLane 2.3.10 ships); only OpenLane's `yosys -y` scripts use it |
| SymbiYosys | v0.66 | `SBY_REF` git tag |
| Yices 2 | 2.7.0 | `YICES_VERSION` + `YICES_SHA256` (SRI static release) |
| OpenLane | 2.3.10 | `OPENLANE_VERSION` (pip) |
| volare / lxml / ioplace-parser | 0.20.6 / 6.1.3 / 0.3.0 | `VOLARE_VERSION`, `LXML_VERSION`, `IOPLACE_PARSER_VERSION` (pip; 0.4.0 pins the wrong ANTLR runtime) |
| OpenROAD | v2.0-17598-ga008522d8 | `OPENROAD_DEB_URL` |
| sky130 PDK | fa87f8f4 | `SKY130_PDK_VERSION` |
| Magic / Netgen | 8.3.489 / 1.5.278 | `MAGIC_REF`, `NETGEN_REF` git tags (built from source; OpenLane 2.3.10's versions) |
| Verilator, Icarus, GHDL, ngspice, KLayout, z3, Boolector | 5.020, 12.0, 4.1.0, noble, 0.28.16, 4.8.12, 1.5.118 | `ubuntu:24.04` image digest (noble archive) |
| KiCad CLI | nightly (10.99 at 2.5.6) | **not pinned**: the nightly PPA keeps only the newest build |

`doctor.py` prints every tool's version during the image build.

Build locally:

```bash
docker build -t ghcr.io/syqnal/hardware-ci:2.5.8 .
```

Build with GF180 included:

```bash
docker build \
  --build-arg INSTALL_GF180=true \
  -t ghcr.io/syqnal/hardware-ci:2.5.8-gf180 .
```

Publish from GitHub:

1. Push changes to `main`.
2. Run **Publish hardware-ci image** from the Actions tab, or push a version tag.
3. Confirm `ghcr.io/syqnal/hardware-ci:2.5.8` exists before moving/updating the `v2` action tag.

Run against a local hardware project:

```bash
scripts/run-local-docker.sh /path/to/project
```

To force a local image:

```bash
SYQNAL_HARDWARE_CI_IMAGE=ghcr.io/syqnal/hardware-ci:2.5.8 \
  scripts/run-local-docker.sh /path/to/project
```

The image build runs `python3 /action/doctor.py`. If any required tool or the
default PDK is missing, the build fails loudly.

## Adding a new tool

1. Add a check module in `action/checks/<name>.py` following the pattern of
   existing modules — return a `check_obj(...)` dict.
2. Add the `INPUT_RUN_<NAME>` branch in `action/run.py`.
3. Add the input to `action.yml`.
4. Add the `DetectedCheck` type and `checkToInput` mapping in Syqnal's
   `src/lib/workflowGenerator.ts`.

## Docker image

Built on Ubuntu 24.04. KiCad CLI from the official KiCad dev nightly PPA.
Yosys from OSS CAD Suite nightly (0.38+) for JSON stat output.
