"""A named pipe that only the current user's processes can open, with deadlines on every wait (Win32 through ctypes).

Server (`PipeServer`):
- the pipe's DACL grants access to the current user's SID alone, and `PIPE_REJECT_REMOTE_CLIENTS` refuses clients of
  other machines;
- `FILE_FLAG_FIRST_PIPE_INSTANCE` with one instance: the creator owns the name; a second creator, or a process that
  took the name first, makes `open()` fail instead of joining it;
- one client at a time, each with a deadline for its message, so a client that connects and stays silent is dropped
  and the next one served.

Client (`request`):
- opens the pipe with `SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION`, so the server cannot act as the user;
- sends nothing unless the server process runs as the same user at an integrity level not below its own;
- one deadline covers the connect, the write and the reply.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Callable, Optional

log = logging.getLogger("media_forge.shell.winpipe")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PIPE_ACCESS_DUPLEX = 0x00000003
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IDENTIFICATION = 0x00010000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TOKEN_INTEGRITY_LEVEL = 25
SDDL_REVISION_1 = 1
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_BROKEN_PIPE = 109
ERROR_PIPE_BUSY = 231
ERROR_NO_DATA = 232
ERROR_MORE_DATA = 234
ERROR_PIPE_CONNECTED = 535
ERROR_IO_PENDING = 997
TIMED_OUT = -1   # `_io` results that are no Windows error
STOPPED = -2

READ_CHUNK = 64 * 1024


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


def _fn(dll, name, restype, *argtypes):
    func = getattr(dll, name)
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


_LPOV = ctypes.POINTER(_OVERLAPPED)
_CreateNamedPipeW = _fn(_kernel32, "CreateNamedPipeW", wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                        ctypes.POINTER(_SECURITY_ATTRIBUTES))
_ConnectNamedPipe = _fn(_kernel32, "ConnectNamedPipe", wintypes.BOOL, wintypes.HANDLE, _LPOV)
_DisconnectNamedPipe = _fn(_kernel32, "DisconnectNamedPipe", wintypes.BOOL, wintypes.HANDLE)
_CreateFileW = _fn(_kernel32, "CreateFileW", wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
_ReadFile = _fn(_kernel32, "ReadFile", wintypes.BOOL, wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), _LPOV)
_WriteFile = _fn(_kernel32, "WriteFile", wintypes.BOOL, wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                 ctypes.POINTER(wintypes.DWORD), _LPOV)
_GetOverlappedResult = _fn(_kernel32, "GetOverlappedResult", wintypes.BOOL, wintypes.HANDLE, _LPOV,
                           ctypes.POINTER(wintypes.DWORD), wintypes.BOOL)
_CancelIoEx = _fn(_kernel32, "CancelIoEx", wintypes.BOOL, wintypes.HANDLE, _LPOV)
_CreateEventW = _fn(_kernel32, "CreateEventW", wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                    wintypes.LPCWSTR)
_SetEvent = _fn(_kernel32, "SetEvent", wintypes.BOOL, wintypes.HANDLE)
_WaitForMultipleObjects = _fn(_kernel32, "WaitForMultipleObjects", wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD)
_CloseHandle = _fn(_kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_SetNamedPipeHandleState = _fn(_kernel32, "SetNamedPipeHandleState", wintypes.BOOL, wintypes.HANDLE,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p)
_GetNamedPipeServerProcessId = _fn(_kernel32, "GetNamedPipeServerProcessId", wintypes.BOOL, wintypes.HANDLE,
                                   ctypes.POINTER(wintypes.ULONG))
_OpenProcess = _fn(_kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_GetCurrentProcess = _fn(_kernel32, "GetCurrentProcess", wintypes.HANDLE)
_LocalFree = _fn(_kernel32, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)
_OpenProcessToken = _fn(_advapi32, "OpenProcessToken", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
                        ctypes.POINTER(wintypes.HANDLE))
_GetTokenInformation = _fn(_advapi32, "GetTokenInformation", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int,
                           ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
_ConvertSidToStringSidW = _fn(_advapi32, "ConvertSidToStringSidW", wintypes.BOOL, ctypes.c_void_p,
                              ctypes.POINTER(wintypes.LPWSTR))
_ConvertSddl = _fn(_advapi32, "ConvertStringSecurityDescriptorToSecurityDescriptorW", wintypes.BOOL,
                   wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                   ctypes.POINTER(wintypes.ULONG))


def _error(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(0, f"{what}: {ctypes.FormatError(code).strip()}", None, code)


# --- token facts ---------------------------------------------------------------------------------------------------

def _token_sid(token, info_class: int) -> str:
    """The SID of a TOKEN_USER or TOKEN_MANDATORY_LABEL (both start with SID_AND_ATTRIBUTES) as a string."""
    size = wintypes.DWORD()
    _GetTokenInformation(token, info_class, None, 0, ctypes.byref(size))
    if not size.value:
        raise _error("GetTokenInformation")
    buf = ctypes.create_string_buffer(size.value)
    if not _GetTokenInformation(token, info_class, buf, size, ctypes.byref(size)):
        raise _error("GetTokenInformation")
    psid = ctypes.c_void_p.from_buffer(buf).value
    text = wintypes.LPWSTR()
    if not _ConvertSidToStringSidW(psid, ctypes.byref(text)):
        raise _error("ConvertSidToStringSidW")
    try:
        return text.value or ""
    finally:
        _LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _identity_of(process) -> tuple[str, int]:
    """(user SID, integrity RID) of the process handle's primary token."""
    token = wintypes.HANDLE()
    if not _OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        raise _error("OpenProcessToken")
    try:
        user = _token_sid(token, TOKEN_USER)
        label = _token_sid(token, TOKEN_INTEGRITY_LEVEL)  # S-1-16-<rid>
        return user, int(label.rsplit("-", 1)[1])
    finally:
        _CloseHandle(token)


_own: Optional[tuple[str, int]] = None


def own_identity() -> tuple[str, int]:
    """(user SID, integrity RID) of this process."""
    global _own
    if _own is None:
        _own = _identity_of(_GetCurrentProcess())
    return _own


def server_identity(handle) -> tuple[str, int]:
    """(user SID, integrity RID) of the process that serves the pipe `handle` was opened on."""
    pid = wintypes.ULONG()
    if not _GetNamedPipeServerProcessId(handle, ctypes.byref(pid)):
        raise _error("GetNamedPipeServerProcessId")
    process = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        raise _error("OpenProcess")
    try:
        return _identity_of(process)
    finally:
        _CloseHandle(process)


def trusted_server(handle) -> bool:
    """True when the pipe's server runs as this user at an integrity level not below this process's."""
    try:
        user, level = server_identity(handle)
        own_user, own_level = own_identity()
    except OSError:
        log.warning("the owner of the instance pipe could not be checked", exc_info=True)
        return False
    return user == own_user and level >= own_level


# --- overlapped I/O with a deadline --------------------------------------------------------------------------------

def _io(handle, start: Callable[[ctypes.c_void_p], bool], timeout: Optional[float],
        stop_event=None) -> tuple[int, int]:
    """Run one overlapped operation; (Windows error or 0, bytes transferred). TIMED_OUT when `timeout` (s) passed
    first, STOPPED when `stop_event` (a Win32 event) was set first; the operation is cancelled then."""
    event = _CreateEventW(None, True, False, None)
    if not event:
        raise _error("CreateEventW")
    ov = _OVERLAPPED(hEvent=event)
    count = wintypes.DWORD()
    try:
        ok = start(ctypes.byref(ov))
        err = 0 if ok else ctypes.get_last_error()
        if err == ERROR_PIPE_CONNECTED:
            return 0, 0
        if err not in (0, ERROR_IO_PENDING, ERROR_MORE_DATA):
            return err, 0
        if err == ERROR_IO_PENDING:
            handles = [event] + ([stop_event] if stop_event else [])
            array = (wintypes.HANDLE * len(handles))(*handles)
            ms = INFINITE if timeout is None else max(0, int(timeout * 1000))
            result = _WaitForMultipleObjects(len(handles), array, False, ms)
            if result != WAIT_OBJECT_0:
                _CancelIoEx(handle, ctypes.byref(ov))
                _GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(count), True)  # the buffer is free
                return (STOPPED if result == WAIT_OBJECT_0 + 1 else TIMED_OUT), 0
        ok = _GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(count), True)
        return (0 if ok else ctypes.get_last_error()), count.value
    finally:
        _CloseHandle(event)


def _read_message(handle, limit: int, deadline: float, stop_event=None) -> tuple[int, bytes]:
    """One whole message of at most `limit` bytes; (0, data) or (error, partial data). ERROR_MORE_DATA when the
    message is longer than `limit`."""
    data = bytearray()
    while True:
        buf = ctypes.create_string_buffer(READ_CHUNK)
        err, n = _io(handle, lambda ov: _ReadFile(handle, buf, READ_CHUNK, None, ov),
                     max(0.0, deadline - time.monotonic()), stop_event)
        data += buf.raw[:n]
        if len(data) > limit:
            return ERROR_MORE_DATA, bytes(data)
        if err != ERROR_MORE_DATA:
            return err, bytes(data)


def _write(handle, data: bytes, deadline: float, stop_event=None) -> int:
    buf = ctypes.create_string_buffer(data, len(data))
    err, n = _io(handle, lambda ov: _WriteFile(handle, buf, len(data), None, ov),
                 max(0.0, deadline - time.monotonic()), stop_event)
    return err if err or n == len(data) else ERROR_NO_DATA


# --- server --------------------------------------------------------------------------------------------------------

class PipeServer:
    """The pipe end of the first instance. `on_message(data)` returns the reply to send, or None for none; it runs
    on the serving thread and must be short."""

    def __init__(self, name: str, on_message: Callable[[bytes], Optional[bytes]], max_message: int,
                 read_timeout: float = 2.0, close_timeout: float = 1.0) -> None:
        self.name = name
        self.on_message = on_message
        self.max_message = max_message
        self.read_timeout = read_timeout
        self.close_timeout = close_timeout
        self.handle = None
        self._stop_event = None

    def open(self) -> None:
        """Create the pipe; OSError when it cannot be created or its name is taken (ERROR_ACCESS_DENIED)."""
        user, _level = own_identity()
        descriptor = ctypes.c_void_p()
        if not _ConvertSddl(f"D:P(A;;GA;;;{user})", SDDL_REVISION_1, ctypes.byref(descriptor), None):
            raise _error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            attrs = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
            handle = _CreateNamedPipeW(
                self.name, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS, 1, READ_CHUNK, READ_CHUNK, 0,
                ctypes.byref(attrs))
        finally:
            _LocalFree(descriptor)
        if handle == INVALID_HANDLE_VALUE or not handle:
            raise _error("CreateNamedPipeW")
        stop_event = _CreateEventW(None, True, False, None)
        if not stop_event:
            _CloseHandle(handle)
            raise _error("CreateEventW")
        self.handle, self._stop_event = handle, stop_event

    def stop(self) -> None:
        """Wake `serve` (it returns and closes the pipe); `close` frees the wake-up event once `serve` returned."""
        if self._stop_event:
            _SetEvent(self._stop_event)

    def close(self) -> None:
        if self.handle is None and self._stop_event:
            _CloseHandle(self._stop_event)
            self._stop_event = None

    def serve(self) -> None:
        """Serve clients one at a time until `stop`; closes the pipe at the end."""
        try:
            while True:
                err, _n = _io(self.handle, lambda ov: _ConnectNamedPipe(self.handle, ov), None, self._stop_event)
                if err == STOPPED:
                    return
                if err == 0:
                    self._serve_client()
                elif err != ERROR_NO_DATA:  # ERROR_NO_DATA: the client left before it was seen
                    log.warning("instance pipe connect failed: %s", ctypes.FormatError(err).strip())
                    time.sleep(0.2)
                _DisconnectNamedPipe(self.handle)
        finally:
            _CloseHandle(self.handle)
            self.handle = None

    def _serve_client(self) -> None:
        deadline = time.monotonic() + self.read_timeout
        err, data = _read_message(self.handle, self.max_message, deadline, self._stop_event)
        if err == TIMED_OUT:
            log.warning("a client of the instance pipe sent nothing in %.1f s and was dropped", self.read_timeout)
            return
        if err == ERROR_MORE_DATA:
            log.warning("a message above %d bytes on the instance pipe was dropped", self.max_message)
            return
        if err:
            return
        reply = self.on_message(data)
        if reply is None:
            return
        if _write(self.handle, reply, time.monotonic() + self.close_timeout, self._stop_event):
            return
        # the client closes once it has read the reply; a disconnect before that would discard the reply
        _read_message(self.handle, 0, time.monotonic() + self.close_timeout, self._stop_event)


# --- client --------------------------------------------------------------------------------------------------------

def request(name: str, data: bytes, timeout: float, reply_limit: int = 64) -> Optional[bytes]:
    """Send `data` as one message and return the reply; None when no trusted server answered within `timeout`."""
    deadline = time.monotonic() + timeout
    while True:
        handle = _CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING,
                              FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None)
        if handle == INVALID_HANDLE_VALUE or not handle:
            err = ctypes.get_last_error()
            if err in (ERROR_FILE_NOT_FOUND, ERROR_PIPE_BUSY) and time.monotonic() < deadline:
                time.sleep(0.05)  # not open yet, or serving another client
                continue
            if err not in (ERROR_FILE_NOT_FOUND, ERROR_PIPE_BUSY):
                log.warning("the instance pipe could not be opened: %s", ctypes.FormatError(err).strip())
            return None
        try:
            mode = wintypes.DWORD(PIPE_READMODE_MESSAGE)
            if not _SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
                return None
            if not trusted_server(handle):
                log.warning("the instance pipe is served by another user or a lower integrity level; "
                            "nothing was sent")
                return None
            if _write(handle, data, deadline):
                return None
            err, reply = _read_message(handle, reply_limit, deadline)
            return None if err else reply
        finally:
            _CloseHandle(handle)
