"""EventPump: buffering until ready(), coalescing of `item` events per id, ordering, JS escaping."""

from __future__ import annotations

import json
import re
import sys
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell.bridge import EventPump, build_event_script, js_literal  # noqa: E402


def decode(script: str) -> list[tuple[str, object]]:
    """The (name, payload) pairs of a script built by build_event_script."""
    calls = re.findall(r"try\{d\((.*?)\)\}catch\(e\)\{console\.error\(e\)\};", script)
    out = []
    decoder = json.JSONDecoder()
    for call in calls:
        name, end = decoder.raw_decode(call)
        payload, _ = decoder.raw_decode(call[end + 1:])
        out.append((name, payload))
    return out


class JsLiteralTest(unittest.TestCase):
    def test_separators_and_unicode(self):
        self.assertEqual(js_literal({"a": [1, 2]}), '{"a":[1,2]}')
        self.assertEqual(js_literal("Größe – 日本"), '"Größe – 日本"')

    def test_line_separators_escaped(self):
        text = js_literal("a\u2028b\u2029c")
        self.assertNotIn("\u2028", text)
        self.assertNotIn("\u2029", text)
        self.assertIn("\\u2028", text)
        self.assertEqual(json.loads(text), "a\u2028b\u2029c")

    def test_quotes_and_script_tags(self):
        value = {"path": "D:\\clips\\it's \"new\".mp4", "x": "</script>"}
        self.assertEqual(json.loads(js_literal(value)), value)

    def test_script_targets_media_forge_dispatch(self):
        script = build_event_script([("toast", {"kind": "info", "message": "Hi"})])
        self.assertIn("window.mediaForge", script)
        self.assertIn("h.dispatch(n,p)", script)
        self.assertEqual(decode(script), [("toast", {"kind": "info", "message": "Hi"})])


class EventPumpTest(unittest.TestCase):
    def setUp(self):
        self.scripts: list[str] = []
        self.pump = EventPump(deliver=self.scripts.append, threaded=False)

    def delivered(self) -> list[tuple[str, object]]:
        out = []
        for script in self.scripts:
            out.extend(decode(script))
        return out

    def test_nothing_before_ready(self):
        self.pump.emit("toast", {"message": "a"})
        self.assertEqual(self.pump.flush(), 0)
        self.assertEqual(self.scripts, [])
        self.pump.set_ready()
        self.assertEqual(self.pump.flush(), 1)
        self.assertEqual(self.delivered(), [("toast", {"message": "a"})])

    def test_nothing_without_deliver(self):
        pump = EventPump(threaded=False)
        pump.emit("toast", {"message": "a"})
        pump.set_ready()
        self.assertEqual(pump.flush(), 0)
        self.assertEqual(len(pump.pending()), 1)

    def test_item_coalesced_per_id_keeps_position(self):
        self.pump.emit("item", {"id": "i1", "progress": 0.1})
        self.pump.emit("toast", {"message": "between"})
        self.pump.emit("item", {"id": "i2", "progress": 0.5})
        self.pump.emit("item", {"id": "i1", "progress": 0.2})
        self.pump.emit("item", {"id": "i1", "progress": 0.3})
        self.pump.set_ready()
        self.pump.flush()
        self.assertEqual(self.delivered(), [
            ("item", {"id": "i1", "progress": 0.3}),
            ("toast", {"message": "between"}),
            ("item", {"id": "i2", "progress": 0.5}),
        ])

    def test_other_events_in_order_not_coalesced(self):
        for n in range(3):
            self.pump.emit("capabilities", {"n": n})
            self.pump.emit("settings", {"n": n})
        self.pump.set_ready()
        self.pump.flush()
        self.assertEqual([p["n"] for _, p in self.delivered()], [0, 0, 1, 1, 2, 2])

    def test_item_without_id_not_coalesced(self):
        self.pump.emit("item", {"x": 1})
        self.pump.emit("item", {"x": 2})
        self.pump.set_ready()
        self.pump.flush()
        self.assertEqual(len(self.delivered()), 2)

    def test_coalescing_after_a_flush_starts_fresh(self):
        self.pump.set_ready()
        self.pump.emit("item", {"id": "i1", "progress": 0.1})
        self.pump.flush()
        self.pump.emit("item", {"id": "i1", "progress": 0.2})
        self.pump.emit("item", {"id": "i1", "progress": 0.4})
        self.pump.flush()
        self.assertEqual([p["progress"] for _, p in self.delivered()], [0.1, 0.4])

    def test_item_removed_drops_pending_item(self):
        self.pump.emit("item", {"id": "i1", "progress": 0.5})
        self.pump.emit("item", {"id": "i2", "progress": 0.5})
        self.pump.emit("item_removed", {"id": "i1"})
        self.pump.emit("item", {"id": "i2", "progress": 0.6})
        self.pump.set_ready()
        self.pump.flush()
        self.assertEqual(self.delivered(), [("item", {"id": "i2", "progress": 0.6}), ("item_removed", {"id": "i1"})])

    def test_items_added_goes_before_pending_item_updates(self):
        self.pump.emit("item", {"id": "i1", "status": "ready"})
        self.pump.emit("toast", {"message": "t"})
        self.pump.emit("items_added", {"items": [{"id": "i1", "status": "probing"}]})
        self.pump.set_ready()
        self.pump.flush()
        self.assertEqual([n for n, _ in self.delivered()], ["toast", "items_added", "item"])
        self.assertEqual(self.delivered()[2][1]["status"], "ready")

    def test_stop_discards_new_events(self):
        self.pump.set_ready()
        self.pump.stop()
        self.pump.emit("toast", {"message": "late"})
        self.assertEqual(self.pump.flush(), 0)

    def test_delivery_failure_is_swallowed(self):
        def boom(_script):
            raise RuntimeError("window closed")

        pump = EventPump(deliver=boom, threaded=False)
        pump.set_ready()
        pump.emit("toast", {"message": "x"})
        self.assertEqual(pump.flush(), 1)

    def test_threaded_delivery(self):
        pump = EventPump(deliver=self.scripts.append, start_delay=0.0, min_interval=0.01)
        try:
            pump.emit("toast", {"message": "buffered"})
            pump.set_ready()
            deadline = time.monotonic() + 2
            while not self.scripts and time.monotonic() < deadline:
                time.sleep(0.01)
            pump.emit("toast", {"message": "later"})
            while len(self.delivered()) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            pump.stop()
        self.assertEqual([p["message"] for _, p in self.delivered()], ["buffered", "later"])


if __name__ == "__main__":
    unittest.main()
