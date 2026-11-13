"""ui/js/format.js: pure text helpers of the UI, run through Node when it is installed."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from media_forge.core import procs

FORMAT_JS = Path(__file__).resolve().parents[1] / "src" / "media_forge" / "ui" / "js" / "format.js"
NODE = shutil.which("node")


def call(function: str, *args) -> object:
    """Result of `format.js` `function(*args)`, through an ES module import in Node."""
    script = (f"const m = await import({json.dumps(FORMAT_JS.as_uri())});"
              f"process.stdout.write(JSON.stringify(m.{function}(...{json.dumps(list(args))})));")
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, timeout=30,
                         creationflags=procs.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        raise AssertionError(res.stderr.decode("utf-8", "replace"))
    return json.loads(res.stdout)


@unittest.skipUnless(NODE, "node is not on PATH")
class AudioNoteTest(unittest.TestCase):
    def test_webm_states_the_written_audio(self):
        self.assertEqual(call("audioNote", "webm", "aac"), "Written as Opus: WebM cannot hold AAC")
        self.assertEqual(call("audioNote", "webm", "keep"),
                         "Opus and Vorbis are copied; other audio is written as Opus")
        self.assertEqual(call("audioNote", "webm", "none"), "")

    def test_other_containers_need_no_note(self):
        for fmt in ("mp4", "mkv", "mov"):
            for audio in ("keep", "aac", "none"):
                self.assertEqual(call("audioNote", fmt, audio), "", (fmt, audio))


if __name__ == "__main__":
    unittest.main()
