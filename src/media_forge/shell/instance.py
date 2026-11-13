"""Single-instance hand-over: a second start passes the files it was given to the running window.

The first instance listens on a named pipe of the user's session (the single-instance mutex is per session too);
a second start sends its file list there as one JSON message and exits. Paths only: nothing is unpickled and no
command is taken from the pipe, so a message can at most add files to the list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Callable, Iterable, Optional

log = logging.getLogger("media_forge.shell.instance")

PIPE_PREFIX = "\\\\.\\pipe\\MediaForge-"
MAX_MESSAGE = 1 << 20  # a command line holds at most 32767 characters; this leaves room for UTF-8 and JSON escapes
SEND_TIMEOUT = 5.0  # covers the first instance's start-up between taking the mutex and opening the pipe
ACK = b"ok"

Handler = Callable[[list], object]


def pipe_name() -> str:
    """The pipe of this user and logon session."""
    user = (os.environ.get("USERNAME") or "").lower()
    digest = hashlib.sha256(f"{user}|{_session_id()}".encode("utf-8")).hexdigest()[:16]
    return PIPE_PREFIX + digest


def _session_id() -> int:
    try:
        import ctypes

        session = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            return int(session.value)
    except (AttributeError, OSError):
        pass
    return 0


def encode(paths: Iterable[str]) -> bytes:
    return json.dumps({"files": [str(p) for p in paths]}, ensure_ascii=False).encode("utf-8")


def decode(data: bytes) -> list[str]:
    """The file list of one message; ValueError for anything else."""
    obj = json.loads(data.decode("utf-8"))
    files = obj.get("files") if isinstance(obj, dict) else None
    if not isinstance(files, list) or not all(isinstance(p, str) for p in files):
        raise ValueError("not a file list")
    return [p for p in files if p.strip()]


class FileReceiver:
    """Listens for file lists from later starts. Lists that arrive before `set_handler` are kept and handed over
    then, so files sent while the window is still being built are not lost."""

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name or pipe_name()
        self._lock = threading.Lock()
        self._handler: Optional[Handler] = None
        self._pending: list[list[str]] = []
        self._listener: Optional[Listener] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> bool:
        try:
            self._listener = Listener(self.name, family="AF_PIPE")
        except OSError:
            log.warning("the instance pipe could not be opened; later starts cannot pass files", exc_info=True)
            return False
        self._thread = threading.Thread(target=self._serve, name="instance-pipe", daemon=True)
        self._thread.start()
        return True

    def set_handler(self, handler: Handler) -> None:
        with self._lock:
            self._handler = handler
            pending, self._pending = self._pending, []
        for paths in pending:
            self._deliver(handler, paths)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            try:  # wakes the blocking accept()
                Client(self.name, family="AF_PIPE").close()
            except OSError:
                pass
            self._thread.join(2.0)
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn = self._listener.accept()
            except OSError:
                if self._stop.is_set():
                    break
                log.warning("instance pipe accept failed", exc_info=True)
                time.sleep(0.2)
                continue
            with conn:
                if self._stop.is_set():
                    break
                try:
                    paths = decode(conn.recv_bytes(MAX_MESSAGE))
                    conn.send_bytes(ACK)
                except (OSError, EOFError, ValueError):
                    log.warning("an unreadable message on the instance pipe was ignored", exc_info=True)
                    continue
            log.info("received %d file(s) from a second start", len(paths))
            if not paths:
                continue
            with self._lock:
                handler = self._handler
                if handler is None:
                    self._pending.append(paths)
                    continue
            self._deliver(handler, paths)

    @staticmethod
    def _deliver(handler: Handler, paths: list[str]) -> None:
        try:
            handler(paths)
        except Exception:  # noqa: BLE001 - the listener must survive a failing add
            log.exception("files from a second start could not be added")


def send_files(paths: Iterable[str], name: Optional[str] = None, timeout: float = SEND_TIMEOUT) -> bool:
    """Hand `paths` to the running instance; False when none answered within `timeout` seconds."""
    data = encode(paths)
    target = name or pipe_name()
    deadline = time.monotonic() + timeout
    while True:
        try:
            with Client(target, family="AF_PIPE") as conn:
                conn.send_bytes(data)
                return conn.recv_bytes(64) == ACK
        except (OSError, EOFError):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
