# syntax=docker/dockerfile:1.7
#
# Tool versions are pinned so a rebuild never changes verification results:
# a builder's PASS must not flip, and cell counts must not move, because the
# image was rebuilt. Bump a pin deliberately, in a release, with a smoke test.
# See "Pinned tool versions" in README.md.

# Yosys 0.66 (git 86f2ddebc). hdlc/yosys only publishes :latest, so pin the digest.
FROM --platform=linux/amd64 hdlc/yosys:latest@sha256:4e4bce92f7555bbebd0d0b7cf196ed0ae0866652004c2cd8e1b5f6d5adb4823b AS yosys_tools

# OpenLane 2.3.10 runs synthesis as Python scripts (`yosys -y`) written for
# Yosys 0.46's Boost.Python bindings (the version its Nix build ships). Build
# exactly that, with Python, for OpenLane alone; every other check keeps 0.66.
FROM --platform=linux/amd64 ubuntu:24.04@sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c AS yosys_openlane
ARG YOSYS_OPENLANE_REF=0.46
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential clang bison flex gawk git pkg-config ca-certificates \
      libreadline-dev tcl-dev libffi-dev zlib1g-dev \
      python3 python3-dev libboost-python-dev libboost-system-dev libboost-filesystem-dev && \
    git clone --depth 1 --branch "${YOSYS_OPENLANE_REF}" --recurse-submodules --shallow-submodules \
      https://github.com/YosysHQ/yosys.git /src/yosys && \
    make -C /src/yosys -j"$(nproc)" ENABLE_PYOSYS=1 PREFIX=/opt/yosys-openlane PYTHON_DESTDIR=/opt/yosys-openlane/python && \
    make -C /src/yosys install ENABLE_PYOSYS=1 PREFIX=/opt/yosys-openlane PYTHON_DESTDIR=/opt/yosys-openlane/python && \
    /opt/yosys-openlane/bin/yosys -V

# Ubuntu 24.04 (noble): Verilator 5.020, Icarus Verilog 12.0, GHDL 4.1.0,
# ngspice, KLayout 0.28.16, Magic 8.3.105 and Netgen come from noble's archive,
# whose versions stay fixed for the release (security fixes only).
FROM --platform=linux/amd64 ubuntu:24.04@sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c

SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# KiCad nightly CLI + baseline EDA/runtime tools.
# TensorRail and other current projects may be saved by KiCad 10 dev builds
# using file format 20260206+, which KiCad 8/9 cannot open.
# kicad-nightly is the one deliberately unpinned tool: the nightly PPA keeps
# only the newest build, so there is no version to pin to. DRC/ERC results can
# change with it; doctor.py logs the kicad-cli version of every image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      gnupg \
      jq \
      make \
      software-properties-common \
      tar \
      unzip \
      xz-utils && \
    add-apt-repository ppa:kicad/kicad-dev-nightly && \
    apt-get update && \
    apt-get -o Dpkg::Options::="--force-overwrite" install -y --no-install-recommends \
      kicad-nightly \
      verilator \
      iverilog \
      ghdl \
      ngspice \
      python3 \
      python3-pip \
      python3-venv \
      klayout \
      magic \
      netgen-lvs \
      z3 \
      boolector \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/lib/netgen/bin/netgen /usr/local/bin/netgen && \
    if command -v kicad-cli-nightly >/dev/null 2>&1; then ln -sf "$(command -v kicad-cli-nightly)" /usr/local/bin/kicad-cli; fi && \
    if command -v kicad-nightly >/dev/null 2>&1; then ln -sf "$(command -v kicad-nightly)" /usr/local/bin/kicad; fi && \
    kicad-cli version

# OpenROAD is not packaged in Ubuntu. Use the OpenROAD Flow Scripts
# documented prebuilt package so OpenLane signoff can run.
ARG OPENROAD_DEB_URL=https://github.com/Precision-Innovations/OpenROAD/releases/download/2024-12-14/openroad_2.0-17598-ga008522d8_amd64-ubuntu-22.04.deb
RUN if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then \
      echo "OpenROAD prebuilt package is only available for linux/amd64 in this runner image" >&2; \
      exit 1; \
    fi && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends libpython3.10 python3.10-minimal && \
    curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 --max-time 1800 \
      "${OPENROAD_DEB_URL}" -o /tmp/openroad.deb && \
    apt-get -o Dpkg::Options::="--force-overwrite" install -y --no-install-recommends /tmp/openroad.deb && \
    rm -f /tmp/openroad.deb && \
    rm -rf /var/lib/apt/lists/*

# Modern Yosys from the public HDL containers. This avoids downloading the
# very large OSS CAD Suite tarball during every Dockerfile-action build.
COPY --from=yosys_tools /usr/local/bin/yosys* /usr/local/bin/
COPY --from=yosys_tools /usr/local/share/yosys /usr/local/share/yosys
COPY --from=yosys_tools /usr/lib/x86_64-linux-gnu/libffi.so.7* /usr/lib/x86_64-linux-gnu/
RUN ldconfig && yosys -V && yosys-abc -c quit

# SymbiYosys is a small Python/Tcl wrapper; install it separately from source.
# v0.66 is the SymbiYosys release matching Yosys 0.66.
ARG SBY_REF=v0.66
RUN git clone --depth 1 --branch "${SBY_REF}" https://github.com/YosysHQ/sby.git /tmp/sby && \
    make -C /tmp/sby install PREFIX=/usr/local && \
    rm -rf /tmp/sby

# Yices 2, the solver most .sby files name ("smtbmc yices"). Not packaged in
# noble; SRI's static release binary, pinned by checksum.
ARG YICES_VERSION=2.7.0
ARG YICES_SHA256=49566b6f817692820538df78fe406878400d79810631c9372b2495bc81d3e00a
RUN curl -fL --retry 5 --retry-delay 5 \
      "https://github.com/SRI-CSL/yices2/releases/download/yices-${YICES_VERSION}/yices-${YICES_VERSION}-x86_64-pc-linux-gnu-static-gmp.tar.gz" \
      -o /tmp/yices.tgz && \
    echo "${YICES_SHA256}  /tmp/yices.tgz" | sha256sum -c - && \
    tar xzf /tmp/yices.tgz -C /tmp && \
    install -m 0755 /tmp/yices-${YICES_VERSION}/bin/yices* /usr/local/bin/ && \
    rm -rf /tmp/yices.tgz /tmp/yices-${YICES_VERSION} && \
    yices-smt2 --version

# OpenLane2 orchestrates OpenROAD/OpenSTA and volare manages open PDKs.
ARG OPENLANE_VERSION=2.3.10
ARG VOLARE_VERSION=0.20.6
ARG LXML_VERSION=6.1.3
RUN pip3 install --break-system-packages --no-cache-dir \
      "openlane==${OPENLANE_VERSION}" "volare==${VOLARE_VERSION}" "lxml==${LXML_VERSION}"

# OpenLane's `yosys -y` scripts go to the Yosys 0.46 + Python build; the
# wrapper on PATH sends everything else to the pinned 0.66.
COPY --from=yosys_openlane /opt/yosys-openlane /opt/yosys-openlane
COPY action/bin/yosys-wrapper.py /usr/local/bin/yosys-wrapper.py
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libboost-python1.83.0 libboost-filesystem1.83.0 libboost-system1.83.0 libtcl8.6 libreadline8t64 libffi8 && \
    rm -rf /var/lib/apt/lists/* && \
    mv /usr/local/bin/yosys /usr/local/bin/yosys-real && \
    install -m 0755 /usr/local/bin/yosys-wrapper.py /usr/local/bin/yosys && \
    yosys -V && /opt/yosys-openlane/bin/yosys -V && \
    PYTHONPATH=/opt/yosys-openlane/python python3 -c "from pyosys import libyosys as ys; ys.Design()"

# Build the image with the PDK available so CI runs do not fetch process data.
# The build must fail if the PDK cannot be installed.
ARG INSTALL_SKY130=true
ARG INSTALL_GF180=false
ARG SKY130_PDK_VERSION=fa87f8f4bbcc7255b6f0c0fb506960f531ae2392
ARG GF180_PDK_VERSION=
RUN --mount=type=secret,id=github_token,required=false \
    mkdir -p /opt/pdks && \
    token_args=() && \
    if [[ -s /run/secrets/github_token ]]; then \
      token_args=(--token "$(cat /run/secrets/github_token)"); \
    fi && \
    if [[ "${INSTALL_SKY130}" == "true" ]]; then \
      python3 -m volare fetch "${token_args[@]}" --pdk sky130 --pdk-root /opt/pdks --include-libraries sky130_fd_sc_hd "${SKY130_PDK_VERSION}" && \
      python3 -m volare enable "${token_args[@]}" --pdk sky130 --pdk-root /opt/pdks --include-libraries sky130_fd_sc_hd "${SKY130_PDK_VERSION}"; \
    fi && \
    if [[ "${INSTALL_GF180}" == "true" ]]; then \
      if [[ -z "${GF180_PDK_VERSION}" ]]; then echo "GF180_PDK_VERSION is required when INSTALL_GF180=true" >&2; exit 1; fi && \
      python3 -m volare fetch "${token_args[@]}" --pdk gf180mcu --pdk-root /opt/pdks "${GF180_PDK_VERSION}" && \
      python3 -m volare enable "${token_args[@]}" --pdk gf180mcu --pdk-root /opt/pdks "${GF180_PDK_VERSION}"; \
    fi

ENV PDK_ROOT=/opt/pdks
ENV PDK=sky130A

COPY action/ /action/

RUN python3 /action/doctor.py

ENTRYPOINT ["python3", "/action/run.py"]
