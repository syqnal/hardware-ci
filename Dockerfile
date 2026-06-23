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

# OSS CAD Suite for latest Yosys (0.38+) — Ubuntu apt ships 0.17 which lacks JSON stat output
ARG OSS_CAD_DATE=2024-11-01
RUN oss_date="${OSS_CAD_DATE//-/}" && \
    case "$(uname -m)" in \
      x86_64|amd64) oss_arch="x64" ;; \
      aarch64|arm64) oss_arch="arm64" ;; \
      *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac && \
    curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 --max-time 1800 --progress-bar \
      "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_DATE}/oss-cad-suite-linux-${oss_arch}-${oss_date}.tgz" \
      -o /tmp/oss-cad.tgz && \
    tar -xzf /tmp/oss-cad.tgz -C /opt && \
    rm /tmp/oss-cad.tgz

ENV PATH="/opt/oss-cad-suite/bin:${PATH}"

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
RUN mkdir -p /opt/pdks && \
    if [[ "${INSTALL_SKY130}" == "true" ]]; then \
      python3 -m volare fetch --pdk sky130 --pdk-root /opt/pdks --include-libraries sky130_fd_sc_hd; \
    fi && \
    if [[ "${INSTALL_GF180}" == "true" ]]; then \
      python3 -m volare fetch --pdk gf180mcu --pdk-root /opt/pdks; \
    fi

ENV PDK_ROOT=/opt/pdks
ENV PDK=sky130A

COPY action/ /action/

RUN python3 /action/doctor.py

ENTRYPOINT ["python3", "/action/run.py"]
