"""SPICE simulation check — ngspice -b → parse convergence, errors, and waveforms."""

import re
import tempfile
from pathlib import Path

from ._base import check_obj, run_tool


_VERSION_RE = re.compile(r"ngspice-(\S+)", re.IGNORECASE)
_ERROR_RE = re.compile(r"(?i)^\s*(error|fatal)[\s:]")
_WARNING_RE = re.compile(r"(?i)^\s*warning[\s:]")
_CONVERGENCE_FAIL_RE = re.compile(r"(?i)(no convergence|convergence problem|did not converge)")
_TIME_STEPS_RE = re.compile(r"(\d+)\s+time[-_\s]?steps?", re.IGNORECASE)
_PROBE_RE = re.compile(r"^\s*\.probe\b[^\n]*", re.IGNORECASE | re.MULTILINE)
_CONTROL_RE = re.compile(r"^\s*\.control\b.*?^\s*\.endc\b", re.IGNORECASE | re.MULTILINE | re.DOTALL)

# Matches a data row in ngspice .print tran output:
#   <index>  <time>  <val1>  <val2> ...
# All fields are floating-point numbers (possibly in sci notation).
_DATA_ROW_RE = re.compile(
    r"^\s*(\d+)\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"   # index + time
    r"((?:\s+[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)+)\s*$"    # one or more values
)

_WAVEFORM_MAX_POINTS = 500


def _ngspice_version() -> str | None:
    rc, out, err, _ = run_tool(["ngspice", "--version"])
    combined = out + err
    m = _VERSION_RE.search(combined)
    return m.group(1) if m else None


def _probe_signals(netlist: str) -> list[str]:
    """Extract signal names from .probe tran lines."""
    signals: list[str] = []
    for line in netlist.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(".probe"):
            # .probe tran V(x) I(y) ...  OR  .probe V(x) I(y) ...
            parts = line.strip().split()
            for tok in parts[1:]:
                if tok.lower() == "tran":
                    continue
                signals.append(tok.strip())
    return signals


def _signal_type_unit(name: str) -> tuple[str, str]:
    """Guess trace type and unit from ngspice signal name."""
    low = name.lower()
    if low.startswith("v(") or low.startswith("vdb(") or low.startswith("vm("):
        return "voltage", "V"
    if low.startswith("i(") or low.startswith("ix("):
        return "current", "A"
    if low.startswith("@"):  # instance param
        return "current", "A"
    return "unknown", ""


def _downsample(xs: list[float], cols: list[list[float]], max_pts: int) -> tuple[list[float], list[list[float]]]:
    """Evenly subsample all columns to at most max_pts points."""
    n = len(xs)
    if n <= max_pts:
        return xs, cols
    step = n / max_pts
    indices = [int(i * step) for i in range(max_pts)]
    xs_out = [xs[i] for i in indices]
    cols_out = [[col[i] for i in indices] for col in cols]
    return xs_out, cols_out


def _parse_waveform(combined_output: str, signal_names: list[str]) -> dict | None:
    """
    Parse ngspice .print tran tabular output.

    ngspice emits a header line followed by data rows:
      Index   time      v(vcore)   i(l2)  ...
        0    0.00e+00   1.20e+00   2.5e-02 ...
        1    2.00e-08   1.19e+00   2.5e-02 ...

    The number of value columns depends on how many signals were printed.
    We use the count of signals we injected to know how many columns to expect.
    """
    n_signals = len(signal_names)
    if n_signals == 0:
        return None

    xs: list[float] = []
    cols: list[list[float]] = [[] for _ in range(n_signals)]

    for line in combined_output.splitlines():
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        vals_str = m.group(3).split()
        if len(vals_str) < n_signals:
            continue
        try:
            t = float(m.group(2))
            vals = [float(v) for v in vals_str[:n_signals]]
        except ValueError:
            continue
        xs.append(t)
        for k, v in enumerate(vals):
            cols[k].append(v)

    if not xs:
        return None

    xs_ds, cols_ds = _downsample(xs, cols, _WAVEFORM_MAX_POINTS)

    traces = []
    for k, name in enumerate(signal_names):
        vtype, unit = _signal_type_unit(name)
        traces.append({
            "name": name,
            "type": vtype,
            "unit": unit,
            "y": [round(v, 9) for v in cols_ds[k]],
        })

    return {
        "sim_type": "TRAN",
        "point_count": len(xs_ds),
        "x_unit": "s",
        "x": [round(v, 12) for v in xs_ds],
        "traces": traces,
    }


def _prepare_netlist_for_waveform(netlist: str, signals: list[str]) -> str:
    """
    Strip .probe and .control blocks; inject .print tran for each signal.
    Returns a modified netlist suitable for batch waveform capture.
    """
    # Remove .control/.endc blocks (they may call 'write' etc.)
    cleaned = _CONTROL_RE.sub("", netlist)
    # Remove .probe lines (we add .print tran instead)
    cleaned = _PROBE_RE.sub("", cleaned)

    # Inject .print tran line before .end
    print_line = ".print tran " + " ".join(signals)
    # Insert before the final .end line
    if re.search(r"^\s*\.end\s*$", cleaned, re.IGNORECASE | re.MULTILINE):
        cleaned = re.sub(
            r"(^\s*\.end\s*$)",
            f"{print_line}\n\\1",
            cleaned,
            count=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    else:
        cleaned = cleaned.rstrip() + f"\n{print_line}\n.end\n"

    return cleaned


def run_spice(cir_file: Path) -> dict:
    version = _ngspice_version()

    # ── First pass: normal simulation for status/convergence ─────────────────
    rc, stdout, stderr, duration_ms = run_tool(
        ["ngspice", "-b", "-o", "/dev/stdout", str(cir_file)],
        timeout=120,
    )

    combined = stdout + stderr
    error_count = 0
    warning_count = 0
    violations = []
    converged = True

    for line in combined.splitlines():
        if _CONVERGENCE_FAIL_RE.search(line):
            converged = False
            error_count += 1
            violations.append({
                "type": "convergence_failure",
                "severity": "error",
                "plain_text": line.strip(),
            })
        elif _ERROR_RE.match(line):
            error_count += 1
            violations.append({
                "type": "spice_error",
                "severity": "error",
                "plain_text": line.strip()[:200],
            })
        elif _WARNING_RE.match(line):
            warning_count += 1

    if rc != 0 and error_count == 0:
        error_count += 1
        violations.append({
            "type": "tool_error",
            "severity": "error",
            "plain_text": f"ngspice exited with code {rc}",
        })
        converged = False

    time_steps: int | None = None
    m = _TIME_STEPS_RE.search(combined)
    if m:
        time_steps = int(m.group(1))

    # ── Second pass: waveform capture (only if simulation converged) ──────────
    waveform: dict | None = None
    if converged:
        try:
            original = cir_file.read_text(encoding="utf-8", errors="replace")
            signals = _probe_signals(original)

            if signals:
                modified = _prepare_netlist_for_waveform(original, signals)

                with tempfile.NamedTemporaryFile(
                    suffix=".cir", mode="w", encoding="utf-8", delete=False
                ) as tmp:
                    tmp.write(modified)
                    tmp_path = Path(tmp.name)

                try:
                    _, wv_stdout, wv_stderr, _ = run_tool(
                        ["ngspice", "-b", "-o", "/dev/stdout", str(tmp_path)],
                        timeout=120,
                    )
                    waveform = _parse_waveform(wv_stdout + wv_stderr, signals)
                finally:
                    tmp_path.unlink(missing_ok=True)
        except Exception:
            # Waveform capture is best-effort; don't fail the check for this
            waveform = None

    status = "PASS" if error_count == 0 else "FAIL"
    summary: dict = {
        "convergence": converged,
        "time_steps": time_steps,
    }
    if waveform is not None:
        summary["waveform"] = waveform

    return check_obj(
        "SPICE_SIM", cir_file, "ngspice", version,
        status=status,
        error_count=error_count,
        warning_count=warning_count,
        duration_ms=duration_ms,
        violations=violations if violations else None,
        summary=summary,
    )
