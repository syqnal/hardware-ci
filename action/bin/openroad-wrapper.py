#!/usr/bin/env python3
"""`openroad` on PATH in the hardware-ci image.

OpenLane 2 runs its ODB scripts as `openroad -python script.py`. This OpenROAD
build embeds Python 3.10, which can't see the packages pip installed for the
image's Python 3.12; the scripts need click, rich, PyYAML and ioplace_parser.
/opt/openroad-python links those pure-Python packages (PyYAML falls back to
its pure-Python loader) and goes on PYTHONPATH for -python runs.
"""

import os
import sys

REAL_OPENROAD = "@REAL_OPENROAD@"
OPENROAD_PYTHONPATH = "/opt/openroad-python"

args = sys.argv[1:]
env = dict(os.environ)
if "-python" in args:
    env["PYTHONPATH"] = os.pathsep.join(p for p in (OPENROAD_PYTHONPATH, env.get("PYTHONPATH")) if p)
os.execve(REAL_OPENROAD, ["openroad", *args], env)
