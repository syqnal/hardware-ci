#!/usr/bin/env python3
"""`yosys` on PATH in the hardware-ci image.

OpenLane 2.3.10 runs its synthesis scripts as `yosys -y script.py ... -- args`,
written for Yosys 0.46's Python bindings. Those go to the Yosys 0.46 + Python
build in /opt/yosys-openlane; everything else goes to the pinned Yosys 0.66.
"""

import os
import sys

REAL_YOSYS = "/usr/local/bin/yosys-real"
OPENLANE_YOSYS = "/opt/yosys-openlane/bin/yosys"
OPENLANE_PYTHONPATH = "/opt/yosys-openlane/python"

args = sys.argv[1:]
if args and args[0] == "-y":
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (OPENLANE_PYTHONPATH, env.get("PYTHONPATH")) if p)
    os.execve(OPENLANE_YOSYS, ["yosys", *args], env)
os.execv(REAL_YOSYS, ["yosys", *args])
