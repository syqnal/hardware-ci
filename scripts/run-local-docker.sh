#!/usr/bin/env bash
set -euo pipefail

repo="${1:-$PWD}"
image="${SYQNAL_HARDWARE_CI_IMAGE:-ghcr.io/syqnal/hardware-ci:2.3.0}"

if [[ ! -d "$repo" ]]; then
  echo "Usage: $0 /path/to/hardware/project" >&2
  exit 2
fi

docker run --rm \
  -e GITHUB_SHA="${GITHUB_SHA:-local}" \
  -e GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-local/local}" \
  -e GITHUB_REF_NAME="${GITHUB_REF_NAME:-local}" \
  -e INPUT_SYQNAL_PROJECT_SLUG="${INPUT_SYQNAL_PROJECT_SLUG:-local-project}" \
  -e INPUT_RUN_DRC="${INPUT_RUN_DRC:-false}" \
  -e INPUT_RUN_ERC="${INPUT_RUN_ERC:-false}" \
  -e INPUT_RUN_RTL_LINT="${INPUT_RUN_RTL_LINT:-true}" \
  -e INPUT_RUN_RTL_SIM="${INPUT_RUN_RTL_SIM:-true}" \
  -e INPUT_RUN_SPICE="${INPUT_RUN_SPICE:-true}" \
  -e INPUT_RUN_BOM="${INPUT_RUN_BOM:-true}" \
  -e INPUT_RUN_STEP="${INPUT_RUN_STEP:-true}" \
  -e INPUT_RUN_GERBER="${INPUT_RUN_GERBER:-true}" \
  -e INPUT_RUN_SYNTHESIS="${INPUT_RUN_SYNTHESIS:-true}" \
  -e INPUT_RUN_FORMAL="${INPUT_RUN_FORMAL:-true}" \
  -e INPUT_RUN_GDSII="${INPUT_RUN_GDSII:-true}" \
  -e INPUT_RUN_LVS="${INPUT_RUN_LVS:-true}" \
  -e INPUT_RUN_OPENLANE="${INPUT_RUN_OPENLANE:-true}" \
  -v "$repo:/workspace" \
  -w /workspace \
  "$image"
