# syntax=docker/dockerfile:1.7
FROM --platform=linux/amd64 hdlc/yosys:latest AS yosys_tools

FROM ubuntu:22.04

SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# KiCad 8 headless CLI + baseline EDA/runtime tools.
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
    add-apt-repository ppa:kicad/kicad-8.0-releases && \
    apt-get update && \
    apt-get -o Dpkg::Options::="--force-overwrite" install -y --no-install-recommends \
      kicad \
      verilator \
      iverilog \
      ngspice \
      python3 \
      python3-pip \
      python3-venv \
      klayout \
      magic \
      netgen-lvs \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/lib/netgen/bin/netgen /usr/local/bin/netgen

# OpenROAD is not packaged in Ubuntu 22.04. Use the OpenROAD Flow Scripts
# documented prebuilt package for Ubuntu 22.04 so OpenLane signoff can run.
ARG OPENROAD_DEB_URL=https://github.com/Precision-Innovations/OpenROAD/releases/download/2024-12-14/openroad_2.0-17598-ga008522d8_amd64-ubuntu-22.04.deb
RUN if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then \
      echo "OpenROAD prebuilt package is only available for linux/amd64 in this runner image" >&2; \
      exit 1; \
    fi && \
    apt-get update && \
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
ARG SBY_REF=main
RUN git clone --depth 1 --branch "${SBY_REF}" https://github.com/YosysHQ/sby.git /tmp/sby && \
    make -C /tmp/sby install PREFIX=/usr/local && \
    rm -rf /tmp/sby

# OpenLane2 orchestrates OpenROAD/OpenSTA and volare manages open PDKs.
ARG OPENLANE_VERSION=
RUN if [[ -n "${OPENLANE_VERSION}" ]]; then \
      pip3 install --no-cache-dir "openlane==${OPENLANE_VERSION}" volare lxml; \
    else \
      pip3 install --no-cache-dir openlane volare lxml; \
    fi

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
