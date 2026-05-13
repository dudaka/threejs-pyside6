"""Python->JS bridge throughput experiment.

Simulates N agents with a vectorized random walk (numpy), pushes their (x,z)
positions to the JS scene each Qt tick via QWebChannel. JS places them on
the procedurally-generated terrain as an InstancedMesh and reports stats.

Usage:
    uv run python bridge.py --n 10000 --hz 60 --protocol bytes
    uv run python bridge.py --n 50000 --hz 30 --protocol json
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow


class LoggingPage(QWebEnginePage):
    """Forwards console.log/warn/error to stdout so the bench is debuggable."""

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: D401
        tag = str(level).rsplit(".", 1)[-1].replace("Level", "")
        print(f"  js[{tag}] {source.rsplit('/', 1)[-1]}:{line} {message}", flush=True)

WEB_DIR = Path(__file__).parent / "web"
AREA = 180.0


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args: object, **kwargs: object) -> None:
        pass


def start_server(directory: Path) -> int:
    handler = partial(_Quiet, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


class Bridge(QObject):
    """Owns the agent state and the signals JS subscribes to."""

    tickJson = Signal(list)
    tickBytes = Signal(str)  # base64-encoded Float32 buffer (ascii-safe over QWebChannel)

    def __init__(self, n: int, hz: int, protocol: str) -> None:
        super().__init__()
        self.n = n
        self.hz = hz
        self.protocol = protocol

        rng = np.random.default_rng(0xC0FFEE)
        half = AREA / 2
        self.x = rng.uniform(-half, half, n).astype(np.float32)
        self.z = rng.uniform(-half, half, n).astype(np.float32)
        self.hd = rng.uniform(0, 2 * np.pi, n).astype(np.float32)
        self.speed = rng.uniform(1.5, 4.0, n).astype(np.float32)
        self._packed = np.empty(n * 2, dtype=np.float32)

    def step(self, dt: float) -> None:
        rng = np.random.default_rng()
        self.hd += rng.normal(0.0, 0.8, self.n).astype(np.float32) * dt
        self.x += np.cos(self.hd) * self.speed * dt
        self.z += np.sin(self.hd) * self.speed * dt
        half = AREA / 2
        ob = (self.x < -half) | (self.x > half) | (self.z < -half) | (self.z > half)
        self.hd[ob] += np.pi
        np.clip(self.x, -half, half, out=self.x)
        np.clip(self.z, -half, half, out=self.z)

    def emit_tick(self) -> int:
        """Emit one tick over the configured protocol; return wire bytes pushed."""
        self._packed[0::2] = self.x
        self._packed[1::2] = self.z
        if self.protocol == "json":
            data = self._packed.tolist()
            self.tickJson.emit(data)
            # Wire size is JSON-string length; approximate as ~12 chars per number
            # (sign, integer/fraction, ", ").
            return len(data) * 12
        b64 = base64.b64encode(self._packed.tobytes()).decode("ascii")
        self.tickBytes.emit(b64)
        return len(b64)

    @Slot(result=str)
    def config(self) -> str:
        return json.dumps(
            {"n": self.n, "hz": self.hz, "protocol": self.protocol, "area": AREA}
        )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000, help="number of agents")
    ap.add_argument("--hz", type=int, default=60, help="target tick rate (Hz)")
    ap.add_argument("--protocol", choices=["json", "bytes"], default="bytes")
    ap.add_argument("--seconds", type=float, default=0,
                    help="auto-quit after this many seconds (0 = run forever)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    port = start_server(WEB_DIR)

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle(
        f"Bridge bench - {args.n:,} entities - {args.protocol} @ {args.hz} Hz"
    )
    win.resize(1100, 760)

    view = QWebEngineView(win)
    view.setPage(LoggingPage(view))
    win.setCentralWidget(view)

    bridge = Bridge(args.n, args.hz, args.protocol)
    channel = QWebChannel()
    channel.registerObject("bridge", bridge)
    view.page().setWebChannel(channel)

    sent_bytes = 0
    sent_ticks = 0
    last_report = time.perf_counter()
    last_step = time.perf_counter()

    def tick() -> None:
        nonlocal sent_bytes, sent_ticks, last_report, last_step
        now = time.perf_counter()
        dt = now - last_step
        last_step = now

        bridge.step(dt)
        sent_bytes += bridge.emit_tick()
        sent_ticks += 1

        if now - last_report >= 1.0:
            elapsed = now - last_report
            mbps = sent_bytes / elapsed / 1e6
            print(
                f"  py: {sent_ticks/elapsed:6.1f} Hz emitted  "
                f"{mbps:6.2f} MB/s  (n={args.n}, proto={args.protocol})",
                flush=True,
            )
            sent_bytes = 0
            sent_ticks = 0
            last_report = now

    interval_ms = max(1, int(round(1000 / args.hz)))
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(interval_ms)

    if args.seconds > 0:
        QTimer.singleShot(int(args.seconds * 1000), app.quit)

    view.load(QUrl(f"http://127.0.0.1:{port}/bridge.html"))
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
