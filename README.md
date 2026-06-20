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
| `run_rtl_sim` | Icarus Verilog + vvp | Testbench simulation |
| `run_spice` | ngspice | SPICE convergence + error check |
| `run_bom` | built-in CSV parser | BOM column validation |
| `run_step` | FreeCAD CLI / header check | STEP geometry validation |
| `run_gerber` | built-in layer check | Gerber fabrication layer completeness |
| `run_synthesis` | Yosys | RTL synthesis — cell/wire/flop counts |

## Output schema

`syqnal-verification.json` — see Syqnal's `src/lib/verificationImport.ts` for
the full type definition consumed on the platform side.

## Adding a new tool

1. Add a check module in `action/checks/<name>.py` following the pattern of
   existing modules — return a `check_obj(...)` dict.
2. Add the `INPUT_RUN_<NAME>` branch in `action/run.py`.
3. Add the input to `action.yml`.
4. Add the `DetectedCheck` type and `checkToInput` mapping in Syqnal's
   `src/lib/workflowGenerator.ts`.

## Docker image

Built on Ubuntu 22.04. KiCad CLI from the official KiCad 8 PPA.
Yosys from OSS CAD Suite nightly (0.38+) for JSON stat output.
