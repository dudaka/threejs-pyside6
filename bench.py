"""Benchmark runner: opens web/bench.html in QWebEngineView, polls results, prints a table."""

from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

WEB_DIR = Path(__file__).parent / "web"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args: object, **kwargs: object) -> None:
        pass


def start_server(directory: Path) -> int:
    handler = partial(_Quiet, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def print_table(results: list[dict]) -> None:
    print()
    print(f"{'fps':>7}  {'render ms/f':>11}  {'tris':>10}  {'shadow':>6}  test")
    print("-" * 76)
    for r in results:
        print(
            f"{r['fps']:>7.2f}  {r['renderMsPerFrame']:>11.3f}  "
            f"{r['tris']:>10,}  {('yes' if r['shadow'] else 'no'):>6}  {r['name']}"
        )
    print()


def main() -> int:
    port = start_server(WEB_DIR)
    app = QApplication(sys.argv)

    win = QMainWindow()
    win.setWindowTitle("Three.js bench")
    win.resize(1024, 720)
    view = QWebEngineView(win)
    win.setCentralWidget(view)
    view.load(QUrl(f"http://127.0.0.1:{port}/bench.html"))
    win.show()

    last_done = [-1]

    def on_progress(value: object) -> None:
        if isinstance(value, dict) and value.get("done") != last_done[0]:
            last_done[0] = value.get("done")
            print(
                f"  [{value.get('done')}/{value.get('total')}] {value.get('current')}",
                flush=True,
            )

    def on_results(payload: object) -> None:
        if not payload:
            QTimer.singleShot(500, poll)
            return
        print_table(json.loads(payload))
        QTimer.singleShot(300, app.quit)

    def poll() -> None:
        view.page().runJavaScript("window.__benchProgress || null", on_progress)
        view.page().runJavaScript(
            "window.__benchResults ? JSON.stringify(window.__benchResults) : null",
            on_results,
        )

    QTimer.singleShot(800, poll)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
