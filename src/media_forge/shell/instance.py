"""Single-instance hand-over: a second start passes the files it was given to the running window.

The first instance listens on a named pipe of the user's session (the single-instance mutex is per session too);
a second start sends its file list there as one JSON message and exits. Paths only: nothing is unpickled and no
command is taken from the pipe, so a message can at most add files to the list. The pipe admits this user's
processes alone and the sender checks who serves it (shell/winpipe.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from typing import Callable, Iterable, Optional

from . import winpipe

log = logging.getLogger("media_forge.shell.instance")

PIPE_PREFIX = "\\\\.\\pipe\\MediaForge-"
MAX_MESSAGE = 1 << 20  # a command line holds at most 32767 characters; this leaves room for UTF-8 and JSON escapes
SEND_TIMEOUT = 5.0  # covers the first instance's start-up and one silent client ahead (READ_TIMEOUT)
READ_TIMEOUT = 2.0  # a client that connected sends its message within this time or is dropped
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


# A JSON string, or the rest of the message after an unclosed quote; possessive and never failing, so one pass.
_JSON_STRING = re.compile(rb'"(?:[^"\\]|\\(?:.|\Z))*+(?:"|\Z)', re.DOTALL)


def decode(data: bytes) -> list[str]:
    """The file list of one message; ValueError for anything else. A file list holds one object and one array, so
    a message with more brackets outside its strings is refused before it is parsed (deep nesting would exhaust
    the parser's stack)."""
    outside = _JSON_STRING.sub(b"", data)
    if outside.count(b"{") + outside.count(b"[") > 2:
        raise ValueError("not a file list")
    obj = json.loads(data.decode("utf-8"))
    files = obj.get("files") if isinstance(obj, dict) else None
    if not isinstance(files, list) or not all(isinstance(p, str) for p in files):
        raise ValueError("not a file list")
    return [p for p in files if p.strip()]


class FileReceiver:
    """Listens for file lists from later starts (`winpipe.PipeServer`: this user only, one client at a time, each
    with a read deadline). A list is acknowledged on the pipe thread and handed to the handler on a delivery thread
    of its own, in arrival order, so a slow add never holds the pipe; lists that arrive before `set_handler` wait
    for it, so files sent while the window is still being built are not lost. After `stop` nothing more is taken."""

    def __init__(self, name: Optional[str] = None, read_timeout: float = READ_TIMEOUT) -> None:
        self.name = name or pipe_name()
        self._server = winpipe.PipeServer(self.name, self._on_message, MAX_MESSAGE, read_timeout)
        self._cond = threading.Condition()
        self._handler: Optional[Handler] = None
        self._pending: list[list[str]] = []
        self._stopped = False
        self._thread: Optional[threading.Thread] = None
        self._delivery: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Open the pipe; False when it cannot be created, its name taken by another process included."""
        try:
            self._server.open()
        except OSError:
            log.warning("the instance pipe could not be opened; later starts cannot pass files", exc_info=True)
            return False
        self._thread = threading.Thread(target=self._server.serve, name="instance-pipe", daemon=True)
        self._delivery = threading.Thread(target=self._deliver_loop, name="instance-delivery", daemon=True)
        self._thread.start()
        self._delivery.start()
        return True

    def set_handler(self, handler: Handler) -> None:
        with self._cond:
            self._handler = handler
            self._cond.notify_all()

    def stop(self) -> None:
        """Close the pipe (later starts see no window) and end the pipe thread; lists not yet handed over are
        dropped. It returns at once on the UI thread: the delivery thread is not waited for, since a handler call
        in progress (a long folder walk) may take long; it ends after that call and starts no other."""
        with self._cond:
            self._stopped = True
            self._pending.clear()
            self._cond.notify_all()
        self._server.stop()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(2.0)  # the stop event cancels its pending I/O, so it ends within milliseconds
        self._server.close()

    def _on_message(self, data: bytes) -> Optional[bytes]:
        try:
            paths = decode(data)
        except Exception:  # noqa: BLE001 - any message, however malformed, is refused and the pipe goes on
            log.warning("an unreadable message on the instance pipe was ignored", exc_info=True)
            return None
        with self._cond:
            if self._stopped:
                return None  # no acknowledgement: the second start reports that the window did not answer
            if paths:
                self._pending.append(paths)
                self._cond.notify_all()
        log.info("received %d file(s) from a second start", len(paths))
        return ACK

    def _deliver_loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopped and (self._handler is None or not self._pending):
                    self._cond.wait()
                if self._stopped:
                    return
                handler, paths = self._handler, self._pending.pop(0)
            if self._stopped:  # stop() came after the list was taken: it is dropped like the others
                return
            try:
                handler(paths)
            except Exception:  # noqa: BLE001 - the receiver must survive a failing add
                log.exception("files from a second start could not be added")


SENT = "sent"
REFUSED = "refused"  # the running instance is another user's or at a lower integrity level
NO_ANSWER = "no answer"


def hand_over(paths: Iterable[str], name: Optional[str] = None, timeout: float = SEND_TIMEOUT,
              give_up: Optional[Callable[[], bool]] = None) -> str:
    """Hand `paths` to the running instance: SENT, REFUSED (nothing was sent to it), or NO_ANSWER when none answered
    within `timeout` seconds or `give_up()` turned true while its pipe did not exist."""
    try:
        reply = winpipe.request(name or pipe_name(), encode(paths), timeout, give_up=give_up)
    except winpipe.UntrustedServer:
        return REFUSED
    return SENT if reply == ACK else NO_ANSWER
