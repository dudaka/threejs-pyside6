"""PySide6 desktop window that hosts a Three.js scene via QWebEngineView.

The scene is served over a loopback HTTP server because Chromium blocks
ES module loading from file:// URLs (CORS, null origin).
"""

from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

WEB_DIR = Path(__file__).parent / "web"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def start_static_server(directory: Path) -> int:
    """Serve `directory` on loopback with an OS-assigned port; return the port."""
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


class MainWindow(QMainWindow):
    def __init__(self, port: int) -> None:
        super().__init__()
        self.setWindowTitle("Three.js in PySide6")
        self.resize(1024, 720)

        self.view = QWebEngineView(self)
        self.view.load(QUrl(f"http://127.0.0.1:{port}/index.html"))
        self.setCentralWidget(self.view)


def main() -> int:
    port = start_static_server(WEB_DIR)
    app = QApplication(sys.argv)
    window = MainWindow(port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
