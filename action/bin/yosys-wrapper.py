#!/usr/bin/env python3
"""`yosys` on PATH in the hardware-ci image.

OpenLane 2 runs its synthesis scripts as `yosys -y script.py [-Q] [-q|-qq] -- args`,
which needs a Yosys built with Python. The pinned Yosys (hdlc/yosys) isn't, so
`-y` runs the script under python3 with the matching pyosys wheel (OpenLane's
scripts import `pyosys.libyosys` when `libyosys` isn't built in). Everything
else goes to the real binary unchanged.
"""

import os
import sys

REAL_YOSYS = "/usr/local/bin/yosys-real"

args = sys.argv[1:]
if len(args) >= 2 and args[0] == "-y":
    script, rest = args[1], args[2:]
    # Yosys options before "--" (log level) don't apply to a plain python run.
    rest = rest[rest.index("--") + 1:] if "--" in rest else [a for a in rest if a not in ("-Q", "-q", "-qq")]
    os.execv(sys.executable, [sys.executable, script, *rest])
os.execv(REAL_YOSYS, ["yosys", *args])
