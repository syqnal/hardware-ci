FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# KiCad 8 headless CLI + EDA tools
RUN apt-get update && \
    apt-get install -y software-properties-common curl && \
    add-apt-repository ppa:kicad/kicad-8.0-releases && \
    apt-get update && \
    apt-get install -y \
      kicad-cli \
      verilator \
      iverilog \
      ngspice \
      python3 \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

# OSS CAD Suite for latest Yosys (0.38+) — Ubuntu apt ships 0.17 which lacks JSON stat output
ARG OSS_CAD_DATE=2024-11-01
RUN curl -L "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_DATE}/oss-cad-suite-linux-x64-${OSS_CAD_DATE//-/}.tgz" \
      -o /tmp/oss-cad.tgz && \
    tar -xzf /tmp/oss-cad.tgz -C /opt && \
    rm /tmp/oss-cad.tgz

ENV PATH="/opt/oss-cad-suite/bin:${PATH}"

# Python deps for output parsing
RUN pip3 install --no-cache-dir lxml

COPY action/ /action/

ENTRYPOINT ["python3", "/action/run.py"]
