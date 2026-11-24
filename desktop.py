"""Inicializador desktop nativo (Windows .exe / Linux) usando pywebview + FastAPI."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request

import uvicorn
import webview

from app import app


def _find_port(preferred: int = 4000) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/brands", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)


def main() -> None:
    port = _find_port(4000)
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    _wait_ready(url)
    webview.create_window(
        "Social Studio",
        url,
        width=1360,
        height=860,
        min_size=(960, 640),
    )
    try:
        webview.start()
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
