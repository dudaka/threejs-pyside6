"""Parameter sweep: runs bridge.py once per (n, protocol) combo, parses stats."""

from __future__ import annotations

import argparse
import re
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# Each invocation runs bridge.py with --seconds and we sample the LAST K
# (py: / js stats:) lines so transient startup behavior is discarded.

PY_RE = re.compile(
    r"py:\s+([\d.]+)\s+Hz emitted\s+([\d.]+)\s+MB/s"
)
JS_RE = re.compile(
    r"js stats:\s+([\d.]+)\s+Hz recv\s+([\d.]+)\s+fps\s+"
    r"([\d.]+)\s+(B/s|KB/s|MB/s)\s+decode=([\d.]+)ms\s+apply=([\d.]+)ms"
)


def parse_bps(value: float, unit: str) -> float:
    return value * {"B/s": 1, "KB/s": 1e3, "MB/s": 1e6}[unit]


def run_one(n: int, hz: int, protocol: str, seconds: int, sample_last: int) -> dict:
    print(f"\n=== n={n:,}  hz={hz}  protocol={protocol}  ({seconds}s) ===", flush=True)
    cmd = [
        "uv", "run", "python", "bridge.py",
        "--n", str(n), "--hz", str(hz), "--protocol", protocol,
        "--seconds", str(seconds),
    ]
    proc = subprocess.run(
        cmd, cwd=HERE, capture_output=True, text=True, timeout=seconds + 30
    )
    py_rows, js_rows = [], []
    for line in proc.stdout.splitlines():
        m = PY_RE.search(line)
        if m:
            py_rows.append((float(m.group(1)), float(m.group(2))))
            continue
        m = JS_RE.search(line)
        if m:
            js_rows.append((
                float(m.group(1)),  # recv Hz
                float(m.group(2)),  # fps
                parse_bps(float(m.group(3)), m.group(4)),  # bytes/sec
                float(m.group(5)),  # decode ms
                float(m.group(6)),  # apply ms
            ))

    # take the last `sample_last` entries (steady-state)
    py_rows = py_rows[-sample_last:]
    js_rows = js_rows[-sample_last:]
    if not py_rows or not js_rows:
        print("  (no rows parsed — bridge may have failed)", flush=True)
        print(proc.stdout[-2000:])
        return {}

    return {
        "n": n, "hz": hz, "protocol": protocol,
        "py_hz": statistics.mean(r[0] for r in py_rows),
        "py_mbps": statistics.mean(r[1] for r in py_rows),
        "js_hz": statistics.mean(r[0] for r in js_rows),
        "js_fps": statistics.mean(r[1] for r in js_rows),
        "js_bps": statistics.mean(r[2] for r in js_rows),
        "js_decode_ms": statistics.mean(r[3] for r in js_rows),
        "js_apply_ms": statistics.mean(r[4] for r in js_rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=6)
    ap.add_argument("--sample-last", type=int, default=3)
    ap.add_argument("--hz", type=int, default=60)
    args = ap.parse_args()

    configs = [
        (n, args.hz, p)
        for n in (1_000, 5_000, 10_000, 25_000, 50_000, 100_000)
        for p in ("bytes", "json")
    ]

    results = []
    for n, hz, p in configs:
        r = run_one(n, hz, p, args.seconds, args.sample_last)
        if r:
            results.append(r)

    print("\n\n=== summary (steady-state averages, last "
          f"{args.sample_last}s of each run) ===")
    print(
        f"{'n':>8}  {'proto':>5}  "
        f"{'py Hz':>6}  {'js Hz':>6}  {'fps':>5}  "
        f"{'wire MB/s':>10}  {'decode ms':>9}  {'apply ms':>8}"
    )
    print("-" * 84)
    for r in results:
        print(
            f"{r['n']:>8,}  {r['protocol']:>5}  "
            f"{r['py_hz']:>6.1f}  {r['js_hz']:>6.1f}  {r['js_fps']:>5.1f}  "
            f"{r['js_bps']/1e6:>10.2f}  "
            f"{r['js_decode_ms']:>9.3f}  {r['js_apply_ms']:>8.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
