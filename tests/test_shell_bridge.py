"""Bridge: the exact contract surface, delegation to the engine, error sentences, settings refusals, dialogs,
window calls, the drop listener and the self-update wiring. Runs against tests/shell_fake_engine.py; no window."""

from __future__ import annotations

import inspect
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell import bridge as bridge_mod  # noqa: E402
from media_forge.shell.bridge import (  # noqa: E402
    CONTRACT_METHODS,
    FILE_TYPES,
    MSG_OUTPUT_DIR,
    MSG_SUFFIX,
    Bridge,
    BridgeError,
    EventPump,
    friendly_message,
    settings_refusal_message,
)
from tests.shell_fake_engine import FakeEngine  # noqa: E402


class FakeWindow:
    def __init__(self, dialog_result=None):
        self.dialog_result = dialog_result
        self.dialog_calls: list[tuple] = []
        self.minimized = 0
        self.destroyed = threading.Event()
        self.scripts: list[str] = []

    def run_js(self, script):
        self.scripts.append(script)

    def create_file_dialog(self, dialog_type, directory="", allow_multiple=False, save_filename="", file_types=()):
        self.dialog_calls.append((dialog_type, directory, allow_multiple, tuple(file_types)))
        return self.dialog_result

    def minimize(self):
        self.minimized += 1

    def destroy(self):
        self.destroyed.set()


class FakeUpdater:
    def __init__(self, stage="idle", apply_result=True):
        self._status = {"stage": stage, "current": "0.1.0", "latest": None, "can_apply": False}
        self.apply_result = apply_result
        self.calls: list[str] = []

    def status(self):
        return dict(self._status)

    def startup(self):
        self.calls.append("startup")

    def check_background(self):
        self.calls.append("check")

    def download_background(self):
        self.calls.append("download")
        if self._status["stage"] == "available":
            self._status["stage"] = "downloading"

    def apply(self):
        self.calls.append("apply")
        return self.apply_result


class FakeScheduler:
    def __init__(self):
        self.interval_key = "30m"
        self.started = self.stopped = False
        self.intervals: list[str] = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def set_interval(self, key):
        self.intervals.append(key)
        self.interval_key = key


class BridgeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mf_shell_bridge_"))
        self.pump = EventPump(threaded=False)
        self.engine = FakeEngine(self.pump.emit, "0.1.0", probe_delay=None)
        self.revealed: list[str] = []
        self.opened: list[str] = []
        self.urls: list[str] = []
        self.bridge = Bridge(self.engine, self.pump, "0.1.0",
                             reveal=lambda p: self.revealed.append(p) or os.path.exists(p),
                             opener=lambda p: self.opened.append(p) or os.path.exists(p),
                             url_opener=lambda u: self.urls.append(u) or u.startswith("https://"))
        self.window = FakeWindow()
        self.bridge._attach_window(self.window)

    def tearDown(self):
        self.pump.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def events(self, name=None):
        out = self.pump.pending()
        return [e for e in out if name is None or e[0] == name]

    def touch(self, name: str) -> str:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return str(path)


class ContractSurfaceTest(unittest.TestCase):
    def test_public_names_are_exactly_the_contract(self):
        public = sorted(n for n in dir(Bridge) if not n.startswith("_"))
        self.assertEqual(public, sorted(CONTRACT_METHODS))
        self.assertEqual(len(CONTRACT_METHODS), 25)

    def test_parameter_names_visible_to_pywebview(self):
        expected = {
            "ready": [], "add_files": ["paths"], "pick_files": [], "remove_item": ["id"], "clear": ["what"],
            "set_item_settings": ["id", "patch"], "apply_to_kind": ["id"], "start": ["ids"], "start_now": ["id"],
            "cancel": ["id"], "cancel_all": [], "get_settings": [], "set_settings": ["patch"], "pick_output_dir": [],
            "reveal": ["path"], "open_path": ["path"], "open_url": ["url"], "capabilities": [], "window_minimize": [],
            "window_close": [], "update_status": [], "check_updates": [], "download_update": [],
            "apply_update": [], "log": ["level", "message"],
        }
        for name, params in expected.items():
            spec = inspect.getfullargspec(getattr(Bridge, name))
            self.assertEqual(spec.args[1:], params, name)

    def test_instance_has_no_public_data(self):
        b = Bridge(FakeEngine(lambda *_: None, "0.1.0"), EventPump(threaded=False), "0.1.0")
        public = [n for n in vars(b) if not n.startswith("_")]
        self.assertEqual(public, [])

    def test_file_dialog_filters(self):
        from webview.util import parse_file_type

        names = [parse_file_type(f)[0] for f in FILE_TYPES]
        self.assertEqual(names, ["Media files", "Video", "Images", "Audio", "All files"])
        self.assertIn("*.m2ts", FILE_TYPES[1])
        self.assertIn("*.jxl", FILE_TYPES[2])
        self.assertIn("*.aif", FILE_TYPES[3])
        self.assertEqual(FILE_TYPES[0].count("*.gif"), 1)


class DelegationTest(BridgeTestBase):
    def test_ready_returns_state_and_starts_delivery(self):
        self.engine._emit("toast", {"kind": "info", "message": "early"})
        state = self.bridge.ready()
        self.assertEqual(state["version"], "0.1.0")
        self.assertEqual(state["window"], {"focused": True})
        self.assertEqual(state["update"]["stage"], "idle")
        for key in ("settings", "items", "capabilities", "options"):
            self.assertIn(key, state)
        self.assertTrue(self.pump.ready)
        self.pump.flush()
        self.assertIn("early", "".join(self.window.scripts))

    def test_add_files_and_item_lifecycle(self):
        a, b = self.touch("a.mp4"), self.touch("b.png")
        items = self.bridge.add_files([a, b])
        self.assertEqual([i["name"] for i in items], ["a.mp4", "b.png"])
        self.assertEqual(self.engine.calls[-1], ("add_files", ([a, b],)))
        ids = [i["id"] for i in items]
        self.assertTrue(self.bridge.set_item_settings(ids[0], {"quality": 50})["settings"]["quality"] == 50)
        self.assertEqual(self.bridge.start(None), ids)
        self.assertEqual(self.bridge.cancel(ids[0]), True)
        self.assertEqual(self.bridge.cancel_all(), 1)
        self.assertEqual(self.bridge.remove_item(ids[0]), True)
        self.assertEqual(self.bridge.clear("all"), [ids[1]])
        self.assertEqual(self.events("item_removed")[-1][1], {"id": ids[1]})

    def test_start_with_ids_calls_start_items(self):
        a = self.touch("a.mp3")
        item = self.bridge.add_files([a])[0]
        self.assertEqual(self.bridge.start([item["id"]]), [item["id"]])
        self.assertEqual(self.engine.calls[-1], ("start_items", ([item["id"]],)))

    def test_start_now_delegates_and_refuses_running(self):
        item = self.bridge.add_files([self.touch("a.mp4")])[0]
        self.assertTrue(self.bridge.start_now(item["id"]))
        self.assertEqual(self.engine.calls[-1], ("start_now", (item["id"],)))
        self.assertEqual(self.engine.state()["items"][0]["status"], "running")
        self.assertFalse(self.bridge.start_now(item["id"]))
        with self.assertRaises(BridgeError) as ctx:
            self.bridge.start_now(None)
        self.assertEqual(str(ctx.exception), "The file is no longer in the list.")

    def test_apply_to_kind(self):
        paths = [self.touch(f"{n}.mp4") for n in "abc"]
        items = self.bridge.add_files(paths)
        self.bridge.set_item_settings(items[0]["id"], {"quality": 33})
        changed = self.bridge.apply_to_kind(items[0]["id"])
        self.assertEqual(len(changed), 2)
        self.assertTrue(all(c["settings"]["quality"] == 33 for c in changed))

    def test_add_files_expands_folders(self):
        folder = self.tmp / "clips"
        self.touch("clips/b.mov")
        self.touch("clips/a.flac")
        self.touch("clips/notes.txt")
        items = self.bridge.add_files([str(folder)])
        self.assertEqual([i["name"] for i in items], ["a.flac", "b.mov"])

    def test_capabilities_and_settings(self):
        self.assertEqual(self.bridge.capabilities()["ffmpeg"], "ready")
        self.assertEqual(self.bridge.get_settings()["suffix"], "_forge")
        self.assertEqual(self.bridge.set_settings({"parallel_jobs": 2})["parallel_jobs"], 2)

    def test_open_url(self):
        self.assertTrue(self.bridge.open_url("https://github.com/softoblivion/media-forge/releases"))
        self.assertFalse(self.bridge.open_url("http://example.com"))
        self.assertFalse(self.bridge.open_url(""))
        self.assertFalse(self.bridge.open_url(None))
        self.assertEqual(len(self.urls), 2)

    def test_open_when_done_reveals_the_last_output(self):
        output = self.touch("out\\clip_forge.mp4")
        self.engine.state = lambda: {"items": [
            {"id": "a", "status": "done", "output": {"path": output}},
            {"id": "b", "status": "error", "output": None},
        ]}
        self.bridge._emit("batch_done", {"done": 1, "failed": 1, "cancelled": 0})
        self.assertEqual(self.revealed, [])  # the setting is off by default
        self.engine.set_settings({"open_when_done": True})
        self.bridge._emit("batch_done", {"done": 0, "failed": 1, "cancelled": 0})
        self.assertEqual(self.revealed, [])  # nothing finished
        self.bridge._emit("batch_done", {"done": 1, "failed": 0, "cancelled": 0})
        self.assertEqual(self.revealed, [output])
        self.assertEqual([e[0] for e in self.events("batch_done")], ["batch_done"] * 3)

    def test_reveal_and_open(self):
        a = self.touch("a.mp4")
        self.assertTrue(self.bridge.reveal(a))
        self.assertTrue(self.bridge.open_path(str(self.tmp)))
        self.assertFalse(self.bridge.reveal(str(self.tmp / "missing.mp4")))
        self.assertFalse(self.bridge.reveal(""))
        self.assertFalse(self.bridge.open_path(None))
        self.assertEqual(self.revealed, [a, str(self.tmp / "missing.mp4")])

    def test_log(self):
        with self.assertLogs("media_forge.ui", level="WARNING") as captured:
            self.assertIsNone(self.bridge.log("warning", "x" * 5000))
        self.assertEqual(len(captured.records), 1)
        self.assertLessEqual(len(captured.records[0].getMessage()), 2010)


class ErrorTest(BridgeTestBase):
    def test_user_errors_pass_through(self):
        item = self.bridge.add_files([self.touch("a.mp4")])[0]
        self.bridge.start([item["id"]])
        with self.assertRaises(BridgeError) as ctx:
            self.bridge.set_item_settings(item["id"], {"quality": 1})
        self.assertEqual(str(ctx.exception), "The file is being processed.")
        with self.assertRaises(BridgeError) as ctx:
            self.bridge.remove_item("i999")
        self.assertEqual(str(ctx.exception), "The file is no longer in the list.")

    def test_argument_validation(self):
        cases = [
            (lambda: self.bridge.add_files("a.mp4"), "Invalid file list."),
            (lambda: self.bridge.add_files([1]), "Invalid file list."),
            (lambda: self.bridge.clear("everything"), "Unknown list filter."),
            (lambda: self.bridge.set_item_settings("i1", "x"), "Invalid settings."),
            (lambda: self.bridge.set_settings([]), "Invalid settings."),
            (lambda: self.bridge.start("i1"), "Invalid file list."),
            (lambda: self.bridge.cancel(None), "The file is no longer in the list."),
        ]
        for call, message in cases:
            with self.assertRaises(BridgeError) as ctx:
                call()
            self.assertEqual(str(ctx.exception), message)

    def test_unexpected_exception_becomes_one_sentence_and_is_logged(self):
        def broken(*_):
            raise KeyError("internal detail")

        self.engine.capabilities = broken
        with self.assertLogs("media_forge.shell.bridge", level="ERROR"):
            with self.assertRaises(BridgeError) as ctx:
                self.bridge.capabilities()
        self.assertEqual(str(ctx.exception), "The operation failed; details are in the log.")

    def test_friendly_message(self):
        self.assertEqual(friendly_message(PermissionError(13, "denied")), "No access to the file or folder.")
        self.assertEqual(friendly_message(FileNotFoundError(2, "x")), "The file was not found.")
        self.assertEqual(friendly_message(OSError(28, "No space")), "File system error: No space.")
        self.assertEqual(friendly_message(ValueError("A  sentence\nhere.")), "A sentence here.")
        self.assertEqual(friendly_message(RuntimeError("x")), "The operation failed; details are in the log.")

    def test_core_user_error_passes_through(self):
        from media_forge.core.errors import UserError

        self.assertEqual(friendly_message(UserError("ffmpeg is not available.")), "ffmpeg is not available.")


class SettingsRefusalTest(BridgeTestBase):
    def test_output_dir_refused_and_settings_resent(self):
        with self.assertRaises(BridgeError) as ctx:
            self.bridge.set_settings({"output_mode": "folder", "output_dir": str(self.tmp / "missing")})
        self.assertEqual(str(ctx.exception), MSG_OUTPUT_DIR)
        resent = self.events("settings")[-1][1]
        self.assertEqual(resent["output_mode"], "beside")

    def test_suffix_refused(self):
        with self.assertRaises(BridgeError) as ctx:
            self.bridge.set_settings({"suffix": ""})
        self.assertEqual(str(ctx.exception), MSG_SUFFIX)

    def test_folder_mode_accepts_existing_dir_and_empty_suffix(self):
        result = self.bridge.set_settings({"output_mode": "folder", "output_dir": str(self.tmp), "suffix": ""})
        self.assertEqual(result["output_mode"], "folder")

    def test_refusal_message_mapping(self):
        self.assertEqual(settings_refusal_message(ValueError("suffix_required")), MSG_SUFFIX)
        self.assertEqual(settings_refusal_message(ValueError("output_dir: missing")), MSG_OUTPUT_DIR)
        self.assertEqual(settings_refusal_message(ValueError("Invalid value.")), "Invalid value.")
        self.assertEqual(settings_refusal_message(RuntimeError("boom")), "The setting could not be changed.")
        from media_forge.core.errors import UserError

        sentence = "The suffix has characters that are not allowed in a file name."
        self.assertEqual(settings_refusal_message(UserError(sentence)), sentence)
        self.assertEqual(settings_refusal_message(UserError(MSG_OUTPUT_DIR)), MSG_OUTPUT_DIR)


class DialogTest(BridgeTestBase):
    def test_pick_files_adds_selection(self):
        a, b = self.touch("x/a.mp4"), self.touch("x/b.wav")
        self.window.dialog_result = (a, b)
        items = self.bridge.pick_files()
        self.assertEqual([i["path"] for i in items], [a, b])
        kind, directory, multiple, types = self.window.dialog_calls[0]
        self.assertEqual(int(kind), 10)
        self.assertTrue(multiple)
        self.assertEqual(types, FILE_TYPES)
        self.window.dialog_result = None
        self.assertEqual(self.bridge.pick_files(), [])
        self.assertEqual(self.window.dialog_calls[1][1], str(self.tmp / "x"))  # last folder remembered

    def test_pick_output_dir(self):
        self.window.dialog_result = (str(self.tmp),)
        self.assertEqual(self.bridge.pick_output_dir(), str(self.tmp))
        self.assertEqual(int(self.window.dialog_calls[0][0]), 20)
        self.window.dialog_result = None
        self.assertIsNone(self.bridge.pick_output_dir())

    def test_dialog_without_window(self):
        b = Bridge(self.engine, self.pump, "0.1.0")
        with self.assertRaises(BridgeError) as ctx:
            b.pick_files()
        self.assertEqual(str(ctx.exception), "The window is not ready.")


class WindowTest(BridgeTestBase):
    def test_minimize_and_close(self):
        self.assertIsNone(self.bridge.window_minimize())
        self.assertEqual(self.window.minimized, 1)
        self.assertIsNone(self.bridge.window_close())
        self.assertTrue(self.window.destroyed.wait(2))

    def test_focus_events_deduplicated(self):
        self.bridge._on_focus(True)
        self.bridge._on_focus(False)
        self.bridge._on_focus(False)
        self.bridge._on_focus(True)
        self.assertEqual([p for _, p in self.events("window")], [{"focused": False}, {"focused": True}])
        self.assertEqual(self.bridge.ready()["window"], {"focused": True})

    def test_shutdown_stops_everything_once(self):
        scheduler = FakeScheduler()
        self.bridge._attach_updater(FakeUpdater(), scheduler)
        self.bridge._shutdown()
        self.bridge._shutdown()
        self.assertTrue(scheduler.stopped)
        self.assertTrue(self.engine.stopped)
        self.assertEqual([c for c, _ in self.engine.calls].count("stop"), 1)


class DropTest(BridgeTestBase):
    def drop_event(self, *paths):
        return {"type": "drop", "dataTransfer": {"files": [
            {"name": Path(p).name, "pywebviewFullPath": p} for p in paths] + [{"name": "no-path"}]}}

    def test_drop_adds_files_and_emits_items_added(self):
        a = self.touch("a.mp4")
        folder = self.tmp / "folder"
        self.touch("folder/c.jpg")
        self.touch("folder/readme.md")
        self.touch("folder/sub/deep.mp4")
        self.bridge._on_drop(self.drop_event(a, str(folder)))
        added = self.events("items_added")
        self.assertEqual(len(added), 1)
        self.assertEqual([i["name"] for i in added[0][1]["items"]], ["a.mp4", "c.jpg"])

    def test_drop_of_non_media_file_goes_to_the_engine(self):
        doc = self.touch("doc.txt")
        self.bridge._on_drop(self.drop_event(doc))
        items = self.events("items_added")[0][1]["items"]
        self.assertEqual(items[0]["name"], "doc.txt")
        self.assertEqual(self.events("item")[-1][1]["status"], "error")

    def test_drop_of_empty_folder_toasts(self):
        (self.tmp / "empty").mkdir()
        self.bridge._on_drop(self.drop_event(str(self.tmp / "empty")))
        self.assertEqual(self.events("items_added"), [])
        self.assertEqual(self.events("toast")[-1][1]["message"], "No media files in the dropped folder.")

    def test_drop_without_paths_is_ignored(self):
        self.bridge._on_drop({"dataTransfer": {"files": [{"name": "x.mp4"}]}})
        self.bridge._on_drop(None)
        self.assertEqual(self.engine.calls, [])

    def test_engine_failure_on_drop_becomes_toast(self):
        def broken(_paths):
            raise ValueError("ffmpeg is not available.")

        self.engine.add_files = broken
        self.bridge._on_drop(self.drop_event(self.touch("a.mp4")))
        self.assertEqual(self.events("toast")[-1][1], {"kind": "error", "message": "ffmpeg is not available."})


class UpdateWiringTest(BridgeTestBase):
    def test_without_updater(self):
        self.assertEqual(self.bridge.update_status()["stage"], "idle")
        with self.assertRaises(BridgeError) as ctx:
            self.bridge.check_updates()
        self.assertEqual(str(ctx.exception), "Updates are not available.")

    def test_status_check_download_apply(self):
        updater = FakeUpdater()
        self.bridge._attach_updater(updater, FakeScheduler())
        self.assertEqual(self.bridge.check_updates()["stage"], "idle")
        self.bridge.download_update()
        self.assertEqual(updater.calls, ["check", "download"])
        self.assertTrue(self.bridge.apply_update())
        self.assertTrue(self.window.destroyed.wait(2))

    def test_failed_apply_keeps_window(self):
        self.bridge._attach_updater(FakeUpdater(apply_result=False))
        self.assertFalse(self.bridge.apply_update())
        self.assertFalse(self.window.destroyed.wait(0.5))

    def test_start_updates(self):
        updater, scheduler = FakeUpdater(), FakeScheduler()
        self.bridge._attach_updater(updater, scheduler)
        self.bridge._start_updates()
        self.assertEqual(updater.calls, ["startup", "check"])
        self.assertTrue(scheduler.started)

    def test_status_events_and_toast(self):
        self.bridge._attach_updater(FakeUpdater())
        self.bridge._on_update_status({"stage": "checking"})
        self.bridge._on_update_toast("success", "Media Forge updated to v0.2.0")
        self.assertEqual(self.events("update")[-1][1], {"stage": "checking"})
        self.assertEqual(self.events("toast")[-1][1], {"kind": "success", "message": "Media Forge updated to v0.2.0"})

    def test_auto_download_only_when_enabled(self):
        updater = FakeUpdater(stage="available")
        self.bridge._attach_updater(updater)
        self.bridge._on_update_status({"stage": "available", "latest": "0.2.0"})
        time.sleep(0.2)
        self.assertNotIn("download", updater.calls)
        self.bridge.set_settings({"auto_download_updates": True})  # turning it on downloads the found release
        deadline = time.monotonic() + 2
        while "download" not in updater.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIn("download", updater.calls)

    def test_interval_change_reaches_scheduler(self):
        scheduler = FakeScheduler()
        self.bridge._attach_updater(FakeUpdater(), scheduler)
        self.bridge.set_settings({"update_check_interval": "6h"})
        self.bridge.set_settings({"parallel_jobs": 2})
        self.assertEqual(scheduler.intervals, ["6h"])

    def test_interval_keys_match_the_updater(self):
        from media_forge.core.selfupdate import INTERVAL_KEYS
        from tests.shell_fake_engine import INTERVALS

        self.assertEqual(tuple(INTERVALS), INTERVAL_KEYS)


class ModuleTest(unittest.TestCase):
    def test_bridge_module_imports_no_pywebview_at_import_time(self):
        self.assertFalse(hasattr(bridge_mod, "webview"))


if __name__ == "__main__":
    unittest.main()
