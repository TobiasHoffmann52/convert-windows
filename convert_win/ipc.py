"""Merge the one-process-per-file launches Explorer makes into a single window.

Select 10 files and click "Convert…" and Explorer starts 10 copies of the app, each with one path.
The first copy listens on a local port; the others hand their path over and exit.
"""
from __future__ import annotations

import json
import queue
import socket
import sys
import threading
import time

PORT = 47831
HOST = "127.0.0.1"
GREETING = b"CONVERT-WIN-1\n"


def send(files: list[str], port: int = PORT, timeout: float = 1.0) -> bool:
    """Give paths to an already-running instance. True if one accepted them."""
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as s:
            s.settimeout(timeout)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(64)
                if not chunk:
                    return False
                buf += chunk
            if buf != GREETING:
                return False                       # something else owns the port
            s.sendall(json.dumps({"files": files}).encode("utf-8") + b"\n")
            return True
    except OSError:
        return False


class Listener:
    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self.inbox: "queue.Queue[list[str]]" = queue.Queue()
        self._sock: socket.socket | None = None

    def start(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((HOST, self.port))
            s.listen(32)
        except OSError:
            s.close()
            return False
        self._sock = s
        self.port = s.getsockname()[1]            # the real port when 0 (any free port) was requested
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(2)
                conn.sendall(GREETING)
                buf = b""
                while not buf.endswith(b"\n") and len(buf) < 1_000_000:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                files = json.loads(buf.decode("utf-8")).get("files", [])
                self.inbox.put([f for f in files if isinstance(f, str)])
            except Exception:
                pass

    def collect(self, quiet: float = 0.6, longest: float = 4.0) -> list[str]:
        """Wait until the stream of arriving paths goes quiet, return them all."""
        got: list[str] = []
        start = last = time.monotonic()
        while time.monotonic() - last < quiet and time.monotonic() - start < longest:
            try:
                got += self.inbox.get(timeout=0.05)
                last = time.monotonic()
            except queue.Empty:
                pass
        return got

    def close(self) -> None:
        if self._sock:
            self._sock.close()
