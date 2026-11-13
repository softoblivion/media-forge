"""Single-instance hand-over: the message format, the pipe receiver and the sender, on a pipe of the test's own."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
import uuid
from multiprocessing.connection import Client
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell import instance  # noqa: E402


def unique_pipe() -> str:
    return f"{instance.PIPE_PREFIX}test-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class MessageTest(unittest.TestCase):
    def test_round_trip_keeps_non_ascii_paths(self):
        paths = ["C:\\Видео\\clip 1.mp4", "D:\\photos\\é.jpg"]
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


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.name = unique_pipe()
        self.receiver = instance.FileReceiver(self.name)
        self.assertTrue(self.receiver.start())
        self.addCleanup(self.receiver.stop)
        self.got: list[list[str]] = []
        self.arrived = threading.Event()

    def handler(self, paths):
        self.got.append(paths)
        self.arrived.set()

    def test_files_reach_the_handler(self):
        self.receiver.set_handler(self.handler)
        self.assertTrue(instance.send_files(["C:\\a.mp4", "C:\\b.jpg"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\a.mp4", "C:\\b.jpg"]])

    def test_files_sent_before_the_handler_are_kept_until_it_is_set(self):
        # A second start may come while the first is still building its window.
        self.assertTrue(instance.send_files(["C:\\early.mp4"], name=self.name, timeout=2))
        self.assertTrue(instance.send_files(["C:\\second.mp4"], name=self.name, timeout=2))
        time.sleep(0.2)
        self.assertEqual(self.got, [])
        self.receiver.set_handler(self.handler)
        self.assertEqual(self.got, [["C:\\early.mp4"], ["C:\\second.mp4"]])

    def test_a_bad_message_is_ignored_and_the_pipe_keeps_serving(self):
        with self.assertLogs("media_forge.shell.instance", "WARNING") as logs:
            with Client(self.name, family="AF_PIPE") as conn:
                conn.send_bytes(b'{"cmd": "delete"}')
                with self.assertRaises((EOFError, OSError)):
                    conn.recv_bytes(64)  # no acknowledgement for a refused message
        self.assertIn("unreadable message", logs.output[0])
        self.receiver.set_handler(self.handler)
        self.assertTrue(instance.send_files(["C:\\after.mp4"], name=self.name, timeout=2))
        self.assertTrue(self.arrived.wait(2))
        self.assertEqual(self.got, [["C:\\after.mp4"]])

    def test_a_failing_handler_does_not_stop_the_receiver(self):
        calls = []

        def broken(paths):
            calls.append(paths)
            if len(calls) == 1:
                raise RuntimeError("add failed")
            self.arrived.set()

        self.receiver.set_handler(broken)
        with self.assertLogs("media_forge.shell.instance", "ERROR"):
            self.assertTrue(instance.send_files(["C:\\one.mp4"], name=self.name, timeout=2))
            self.assertTrue(instance.send_files(["C:\\two.mp4"], name=self.name, timeout=2))
            self.assertTrue(self.arrived.wait(2))
        self.assertEqual(calls, [["C:\\one.mp4"], ["C:\\two.mp4"]])

    def test_stop_ends_the_thread_and_frees_the_pipe(self):
        self.receiver.stop()
        self.assertFalse(self.receiver._thread.is_alive())
        self.assertFalse(instance.send_files(["C:\\late.mp4"], name=self.name, timeout=0.3))


class SenderTest(unittest.TestCase):
    def test_no_receiver_returns_false_after_the_timeout(self):
        started = time.monotonic()
        self.assertFalse(instance.send_files(["C:\\a.mp4"], name=unique_pipe(), timeout=0.3))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_waits_for_a_receiver_that_opens_late(self):
        name = unique_pipe()
        receiver = instance.FileReceiver(name)
        self.addCleanup(receiver.stop)
        timer = threading.Timer(0.4, receiver.start)
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertTrue(instance.send_files(["C:\\a.mp4"], name=name, timeout=3))


if __name__ == "__main__":
    unittest.main()
