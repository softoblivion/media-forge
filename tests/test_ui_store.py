"""ui/js/store.js: the derived values of the shared page state, run through Node when it is installed."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from media_forge.core import procs

STORE_JS = Path(__file__).resolve().parents[1] / "src" / "media_forge" / "ui" / "js" / "store.js"
NODE = shutil.which("node")


def shown_scale(saved, scales, scale) -> object:
    """`shownScale(saved)` of store.js with `store.scales` and `store.scale` set as given."""
    script = (f"const m = await import({json.dumps(STORE_JS.as_uri())});"
              f"m.store.scales = {json.dumps(scales)}; m.store.scale = {json.dumps(scale)};"
              f"process.stdout.write(JSON.stringify(m.shownScale({json.dumps(saved)})));")
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, timeout=30,
                         creationflags=procs.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        raise AssertionError(res.stderr.decode("utf-8", "replace"))
    return json.loads(res.stdout)


@unittest.skipUnless(NODE, "node is not on PATH")
class ShownScaleTest(unittest.TestCase):
    def test_a_saved_step_that_does_not_fit_shows_the_applied_step(self):
        self.assertEqual(shown_scale(200, [100, 125, 150], 150), 150)

    def test_a_saved_step_that_fits_is_shown(self):
        self.assertEqual(shown_scale(125, [100, 125, 150], 125), 125)
        self.assertEqual(shown_scale(200, [100, 125, 150, 175, 200], 200), 200)

    def test_the_saved_step_is_shown_while_the_shell_has_not_said(self):
        self.assertEqual(shown_scale(200, None, None), 200)
        self.assertEqual(shown_scale(200, [100, 125], None), 200)


if __name__ == "__main__":
    unittest.main()
