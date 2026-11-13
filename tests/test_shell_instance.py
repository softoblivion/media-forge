"""Single-instance hand-over: the message format, the pipe receiver and the sender, on a pipe of the test's own."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import unittest
import uuid
from ctypes import wintypes
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell import instance, winpipe  # noqa: E402

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetCurrentThread.restype = wintypes.HANDLE
_advapi32.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
_advapi32.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
_advapi32.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_advapi32.GetSecurityInfo.restype = wintypes.DWORD
_advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]

SE_KERNEL_OBJECT = 6
DACL_SECURITY_INFORMATION = 4
TOKEN_QUERY = 8
TOKEN_IMPERSONATION_LEVEL = 9
SECURITY_IDENTIFICATION_LEVEL = 1  # SECURITY_IMPERSONATION_LEVEL.SecurityIdentification


def unique_pipe() -> str:
    return f"{instance.PIPE_PREFIX}test-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def sent(paths, **kwargs) -> bool:
    """True when the running instance acknowledged `paths`."""
    return instance.hand_over(paths, **kwargs) == instance.SENT


def open_silently(name: str, timeout: float = 2.0):
    """A client that connects and sends nothing (a hung or hostile peer); the caller closes the handle."""
    deadline = time.monotonic() + timeout
    while True:
        handle = _kernel32.CreateFileW(name, winpipe.GENERIC_READ | winpipe.GENERIC_WRITE, 0, None,
                                       winpipe.OPEN_EXISTING, 0, None)
        if handle and handle != winpipe.INVALID_HANDLE_VALUE:
            return handle
        if time.monotonic() >= deadline:
            raise OSError(ctypes.get_last_error(), "pipe not available")
        time.sleep(0.05)


def pipe_dacl(handle) -> str:
    dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    err = _advapi32.GetSecurityInfo(handle, SE_KERNEL_OBJECT, DACL_SECURITY_INFORMATION, None, None,
                                    ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if err:
        raise OSError(err, "GetSecurityInfo")
    text = wintypes.LPWSTR()
    try:
        if not _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, DACL_SECURITY_INFORMATION, ctypes.byref(text), None):
            raise OSError(ctypes.get_last_error(), "ConvertSecurityDescriptorToStringSecurityDescriptorW")
        return text.value or ""
    finally:
        _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        _kernel32.LocalFree(descriptor)


class MessageTest(unittest.TestCase):
    def test_round_trip_keeps_non_ascii_paths(self):
        paths = ["C:\\Βίντεο\\clip 1.mp4", "D:\\photos\\é.jpg"]
        self.assertEqual(instance.decode(instance.encode(paths)), paths)

    def test_blank_entries_are_dropped(self):
        self.assertEqual(instance.decode(instance.encode(["C:\\a.mp4", "  "])), ["C:\\a.mp4"])

    def test_anything_but_a_file_list_is_refused(self):
        for data in (b"[]", b'{"files": "C:\\\\a.mp4"}', b'{"files": [1]}', b'{"cmd": "x"}', b"\xff"):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    instance.decode(data)

    def test_pipe_name_is_stable_and_per_session(self):
        name = instance.pipe_name()
        self.assertTrue(name.startswith("\\\\.\\pipe\\MediaForge-"))
        self.assertEqual(name, instance.pipe_name())
        with mock.patch.object(instance, "_session_id", return_value=1):
            first = instance.pipe_name()
        with mock.patch.object(instance, "_session_id", return_value=2):
            second = instance.pipe_name()
        self.assertNotEqual(first, second)
        with mock.patch.object(instance, "_session_id", return_value=1):
            self.assertEqual(instance.pipe_name(), first)


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.name = unique_pipe()
        self.receiver = instance.FileReceiver(self.name, read_timeout=0.5)
        self.assertTrue(self.receiver.start())
        self.addCleanup(self.receiver.stop)
        self.got: list[list[str]] = []
        self.arrived = threading.Event()

    def handler(self, paths):
        self.got.append(paths)
        self.arrived.set()

    def wait_for(self, count: int, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while len(self.got) < count and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_files_reach_the_handler(self):
        self.receiver.set_handler(self.handler)
        self.assertTrue(sent(["C:\\a.mp4", "C:\\b.jpg"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\a.mp4", "C:\\b.jpg"]])

    def test_files_sent_before_the_handler_are_kept_until_it_is_set(self):
        # A second start may come while the first is still building its window.
        self.assertTrue(sent(["C:\\early.mp4"], name=self.name, timeout=2))
        self.assertTrue(sent(["C:\\second.mp4"], name=self.name, timeout=2))
        time.sleep(0.2)
        self.assertEqual(self.got, [])
        self.receiver.set_handler(self.handler)
        self.wait_for(2)
        self.assertEqual(self.got, [["C:\\early.mp4"], ["C:\\second.mp4"]])

    def test_a_bad_message_is_ignored_and_the_pipe_keeps_serving(self):
        with self.assertLogs("media_forge.shell.instance", "WARNING") as logs:
            self.assertIsNone(winpipe.request(self.name, b'{"cmd": "delete"}', 2))  # no acknowledgement
        self.assertIn("unreadable message", logs.output[0])
        self.receiver.set_handler(self.handler)
        self.assertTrue(sent(["C:\\after.mp4"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\after.mp4"]])

    def test_a_deeply_nested_message_is_refused_and_the_pipe_keeps_serving(self):
        deep = b"[" * 200000 + b"]" * 200000  # 400,000 bytes, below MAX_MESSAGE
        with self.assertLogs("media_forge.shell.instance", "WARNING"):
            self.assertIsNone(winpipe.request(self.name, deep, 2))
        self.assertTrue(self.receiver._thread.is_alive())
        self.receiver.set_handler(self.handler)
        self.assertTrue(sent(["C:\\after.mp4"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\after.mp4"]])

    def test_any_error_while_reading_a_message_is_refused_and_the_pipe_keeps_serving(self):
        with mock.patch.object(instance, "decode", side_effect=[RecursionError("deep"), ["C:\\after.mp4"]]):
            with self.assertLogs("media_forge.shell.instance", "WARNING"):
                self.assertIsNone(winpipe.request(self.name, b'{"files": []}', 2))
            self.receiver.set_handler(self.handler)
            self.assertTrue(sent(["C:\\after.mp4"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\after.mp4"]])

    def test_the_listener_outlives_a_failure_while_serving_one_client(self):
        server = self.receiver._server
        real = server._serve_client
        errors = [OSError(6, "broken")]

        def fails_once():
            if errors:
                raise errors.pop()
            return real()

        with mock.patch.object(server, "_serve_client", side_effect=fails_once):
            with self.assertLogs("media_forge.shell.winpipe", "ERROR"):
                self.assertIsNone(winpipe.request(self.name, b'{"files": ["C:\\\\lost.mp4"]}', 2))
            self.receiver.set_handler(self.handler)
            self.assertTrue(sent(["C:\\after.mp4"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\after.mp4"]])

    def test_stop_on_the_ui_thread_does_not_wait_for_a_slow_handler(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def slow(paths):
            calls.append(paths)
            entered.set()
            release.wait(1.5)

        self.receiver.set_handler(slow)
        self.assertTrue(sent(["C:\\walk"], name=self.name, timeout=2))
        self.assertTrue(entered.wait(2))
        self.assertTrue(sent(["C:\\pending.mp4"], name=self.name, timeout=2))
        started = time.monotonic()
        self.receiver.stop()
        self.assertLess(time.monotonic() - started, 0.5)
        time.sleep(2.0)  # the slow call ends; the list that waited behind it is not handed over
        self.assertEqual(calls, [["C:\\walk"]])

    def test_a_failing_handler_does_not_stop_the_receiver(self):
        calls = []

        def broken(paths):
            calls.append(paths)
            if len(calls) == 1:
                raise RuntimeError("add failed")
            self.arrived.set()

        self.receiver.set_handler(broken)
        with self.assertLogs("media_forge.shell.instance", "ERROR"):
            self.assertTrue(sent(["C:\\one.mp4"], name=self.name, timeout=2))
            self.assertTrue(sent(["C:\\two.mp4"], name=self.name, timeout=2))
            self.assertTrue(self.arrived.wait(2))
        self.assertEqual(calls, [["C:\\one.mp4"], ["C:\\two.mp4"]])

    def test_a_slow_handler_does_not_hold_the_pipe(self):
        # A long folder walk in the handler must not keep the next second start waiting.
        release = threading.Event()
        self.addCleanup(release.set)
        self.receiver.set_handler(lambda paths: release.wait(5))
        self.assertTrue(sent(["C:\\walk"], name=self.name, timeout=2))
        started = time.monotonic()
        self.assertTrue(sent(["C:\\next.mp4"], name=self.name, timeout=2))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_a_silent_client_is_dropped_and_a_sender_behind_it_gives_up_in_time(self):
        silent = open_silently(self.name)
        try:
            started = time.monotonic()
            with self.assertLogs("media_forge.shell.winpipe", "WARNING") as logs:
                self.assertFalse(sent(["C:\\a.mp4"], name=self.name, timeout=0.3))
                self.assertLess(time.monotonic() - started, 0.3 + 0.5)
                # the receiver drops the silent client after its read deadline and serves the next one
                self.receiver.set_handler(self.handler)
                self.assertTrue(sent(["C:\\b.mp4"], name=self.name, timeout=2))
            self.assertTrue(any("sent nothing" in line for line in logs.output))
        finally:
            _kernel32.CloseHandle(silent)
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\b.mp4"]])

    def test_a_sender_does_not_wait_for_ever_on_a_server_that_never_answers(self):
        hung = threading.Event()
        self.addCleanup(hung.set)
        self.receiver._server.on_message = lambda data: (hung.wait(5), None)[1]  # a hung first instance
        started = time.monotonic()
        self.assertFalse(sent(["C:\\a.mp4"], name=self.name, timeout=0.5))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_second_creator_of_the_name_fails_instead_of_joining(self):
        second = instance.FileReceiver(self.name)
        with self.assertLogs("media_forge.shell.instance", "WARNING"):
            self.assertFalse(second.start())
        second.stop()
        self.receiver.set_handler(self.handler)
        self.assertTrue(sent(["C:\\a.mp4"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))

    def test_the_pipe_admits_the_current_user_alone(self):
        user, _level = winpipe.own_identity()
        dacl = pipe_dacl(self.receiver._server.handle)
        aces = dacl.split("(")[1:]
        self.assertTrue(dacl.startswith("D:P"), dacl)  # protected: nothing inherited
        self.assertEqual(len(aces), 1, dacl)
        self.assertTrue(aces[0].rstrip(")").endswith(";" + user), dacl)
        self.assertTrue(aces[0].startswith("A;"), dacl)

    def test_stop_ends_the_thread_and_frees_the_pipe(self):
        self.receiver.stop()
        self.assertFalse(self.receiver._thread.is_alive())
        self.assertFalse(sent(["C:\\late.mp4"], name=self.name, timeout=0.3))

    def test_a_list_sent_while_stopping_is_not_acknowledged(self):
        self.receiver._stopped = True  # stop() has begun; the pipe may still take one message
        self.assertIsNone(self.receiver._on_message(instance.encode(["C:\\late.mp4"])))


class ClientIdentityTest(unittest.TestCase):
    def test_the_server_can_identify_but_not_impersonate_the_sender(self):
        levels = []

        def on_message(data):
            levels.append(impersonation_level(server.handle))
            return instance.ACK

        name = unique_pipe()
        server = winpipe.PipeServer(name, on_message, 1024)
        server.open()
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        try:
            self.assertEqual(winpipe.request(name, b"x", 2), instance.ACK)
        finally:
            server.stop()
            thread.join(2)
            server.close()
        self.assertEqual(levels, [SECURITY_IDENTIFICATION_LEVEL])

    def test_nothing_is_sent_to_a_server_of_another_user_or_a_lower_level(self):
        user, level = winpipe.own_identity()
        received = []
        name = unique_pipe()
        server = winpipe.PipeServer(name, lambda data: received.append(data) or instance.ACK, 1024, read_timeout=0.3)
        server.open()
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        try:
            for identity in (("S-1-5-21-1-2-3-1001", level), (user, level - 0x1000)):
                with self.subTest(identity=identity), \
                        mock.patch.object(winpipe, "server_identity", return_value=identity), \
                        self.assertLogs("media_forge.shell.winpipe", "WARNING"):
                    self.assertFalse(sent(["C:\\secret.mp4"], name=name, timeout=1))
            self.assertTrue(sent(["C:\\ok.mp4"], name=name, timeout=2))
        finally:
            server.stop()
            thread.join(2)
            server.close()
        self.assertEqual(received, [instance.encode(["C:\\ok.mp4"])])

    def test_own_identity_is_a_user_at_a_real_level(self):
        user, level = winpipe.own_identity()
        self.assertTrue(user.startswith("S-1-5-"), user)
        self.assertGreaterEqual(level, 0x1000)


def impersonation_level(pipe) -> int:
    """The impersonation level the client granted, read while impersonating it on this thread."""
    if not _advapi32.ImpersonateNamedPipeClient(pipe):
        raise OSError(ctypes.get_last_error(), "ImpersonateNamedPipeClient")
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenThreadToken(_kernel32.GetCurrentThread(), TOKEN_QUERY, True, ctypes.byref(token)):
            raise OSError(ctypes.get_last_error(), "OpenThreadToken")
        try:
            value, size = ctypes.c_int(), wintypes.DWORD()
            if not _advapi32.GetTokenInformation(token, TOKEN_IMPERSONATION_LEVEL, ctypes.byref(value), 4,
                                                 ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), "GetTokenInformation")
            return value.value
        finally:
            _kernel32.CloseHandle(token)
    finally:
        _advapi32.RevertToSelf()


class SenderTest(unittest.TestCase):
    def test_no_receiver_returns_false_after_the_timeout(self):
        started = time.monotonic()
        self.assertFalse(sent(["C:\\a.mp4"], name=unique_pipe(), timeout=0.3))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_waits_for_a_receiver_that_opens_late(self):
        name = unique_pipe()
        receiver = instance.FileReceiver(name)
        self.addCleanup(receiver.stop)
        timer = threading.Timer(0.4, receiver.start)
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertTrue(sent(["C:\\a.mp4"], name=name, timeout=3))


if __name__ == "__main__":
    unittest.main()
