"""tools and procs: discovery, version parsing, verified ffmpeg install, capability probe, process control."""

from __future__ import annotations

import functools
import hashlib
import http.server
import shutil
import sys
import threading
import time
import unittest
import zipfile
from pathlib import Path

from media_forge.core import procs, tools
from media_forge.core.paths import AppPaths

from tests.core_support import FFMPEG, ScratchCase, needs_ffmpeg


class LocalServer:
    """A static HTTP server on 127.0.0.1 serving one folder (for download tests)."""

    def __init__(self, folder: Path) -> None:
        handler = functools.partial(_QuietHandler, directory=str(folder))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "LocalServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


class ParseTest(unittest.TestCase):
    def test_version(self):
        self.assertEqual(tools.parse_ffmpeg_version("ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright"),
                         "9.0.2")
        self.assertEqual(tools.parse_ffmpeg_version("ffmpeg version n7.1 Copyright"), "7.1")
        self.assertEqual(tools.parse_ffmpeg_version("ffmpeg version N-118000-gabc123-20260101 Copyright"),
                         "N-118000")
        self.assertIsNone(tools.parse_ffmpeg_version("nothing"))

    def test_sums(self):
        sha = "a" * 64
        sums = tools.parse_sums(f"{sha}  ffmpeg-master-latest-win64-gpl.zip\n{'b' * 64} *other.zip\njunk\n")
        self.assertEqual(sums["ffmpeg-master-latest-win64-gpl.zip"], sha)
        self.assertEqual(sums["other.zip"], "b" * 64)

    def test_progress_parser(self):
        blocks = []
        parser = procs.ProgressParser(blocks.append)
        for line in ["frame=10", "out_time_us=500000", "speed=2.5x", "progress=continue", "out_time_us=N/A",
                     "progress=end"]:
            parser.feed(line)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(procs.out_time_seconds(blocks[0]), 0.5)
        self.assertEqual(procs.speed_factor(blocks[0]), 2.5)
        self.assertIsNone(procs.out_time_seconds(blocks[1]))


class InstallTest(ScratchCase):
    def _fake_release(self, folder: Path) -> str:
        folder.mkdir(parents=True, exist_ok=True)
        zpath = folder / tools.FFMPEG_ZIP
        exe = Path(sys.executable).read_bytes()[:4096]
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe", exe)
            zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe", exe + b"probe")
            zf.writestr("ffmpeg-master-latest-win64-gpl/doc/readme.txt", "doc")
        sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
        (folder / tools.FFMPEG_SUMS).write_text(f"{sha}  {tools.FFMPEG_ZIP}\n", encoding="utf-8")
        return sha

    def test_find_prefers_bin(self):
        paths = AppPaths(home=self.out / "find")
        paths.ensure()
        found = tools.find_ffmpeg(paths, use_path=False)
        self.assertIsNone(found)
        paths.binary("ffmpeg.exe").write_bytes(b"x")
        paths.binary("ffprobe.exe").write_bytes(b"x")
        found = tools.find_ffmpeg(paths)
        self.assertEqual(found.source, "bin")
        self.assertEqual(Path(found.ffmpeg), paths.binary("ffmpeg.exe"))

    def test_verified_download_and_extract(self):
        release = self.out / "release"
        sha = self._fake_release(release)
        paths = AppPaths(home=self.out / "install")
        paths.ensure()
        seen = []
        with LocalServer(release) as server:
            tool = tools.download_ffmpeg(paths, on_progress=lambda d, t: seen.append(d), base_url=server.url)
        self.assertEqual(tool.source, "bin")
        self.assertTrue(paths.binary("ffmpeg.exe").is_file())
        self.assertTrue(paths.binary("ffprobe.exe").is_file())
        self.assertFalse(list(paths.bin_dir.glob("*.part")))
        self.assertFalse(list(paths.cache_dir.glob("*.part")))
        self.assertIn(sha, paths.versions_file.read_text(encoding="utf-8"))
        self.assertTrue(seen)

    def test_checksum_mismatch_installs_nothing(self):
        release = self.out / "release_bad"
        self._fake_release(release)
        (release / tools.FFMPEG_SUMS).write_text(f"{'0' * 64}  {tools.FFMPEG_ZIP}\n", encoding="utf-8")
        paths = AppPaths(home=self.out / "install_bad")
        paths.ensure()
        with LocalServer(release) as server, self.assertRaises(tools.DownloadError):
            tools.download_ffmpeg(paths, base_url=server.url)
        self.assertFalse(paths.binary("ffmpeg.exe").exists())
        self.assertFalse(list(paths.cache_dir.glob("*.part")))


@needs_ffmpeg
class CapabilitiesTest(unittest.TestCase):
    def test_probe(self):
        tool = tools.FFmpeg(FFMPEG, shutil.which("ffprobe"), "path")
        caps = tools.probe_capabilities(tool)
        self.assertEqual(caps["ffmpeg"], "ready")
        self.assertTrue(caps["ffmpeg_version"])
        self.assertIn("libx264", caps["encoders"])
        self.assertEqual(set(caps["nvenc"]), {"h264", "h265", "av1"})
        self.assertIsInstance(caps["avif"], bool)
        public = tools.public_capabilities({**caps, "models": {}})
        self.assertNotIn("encoders", public)
        missing = tools.probe_capabilities(None)
        self.assertEqual(missing["ffmpeg"], "missing")
        self.assertFalse(any(missing["nvenc"].values()))


class ProcsTest(ScratchCase):
    def test_run_and_capture(self):
        res = procs.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
                        capture=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), b"out")
        self.assertEqual(res.stderr, ["err"])

    def test_missing_executable(self):
        res = procs.run([str(self.out / "nope.exe")])
        self.assertIsNone(res.returncode)
        self.assertTrue(res.start_error)

    def test_cancel_kills_tree(self):
        marker = self.out / "child_alive.txt"
        child = self.out / "child.py"
        child.write_text("import pathlib, sys, time\ntime.sleep(3)\npathlib.Path(sys.argv[1]).write_text('alive')\n",
                         encoding="utf-8")
        parent = self.out / "parent.py"
        parent.write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
                          "time.sleep(30)\n", encoding="utf-8")
        cancel = threading.Event()
        threading.Timer(1.5, cancel.set).start()
        start = time.monotonic()
        res = procs.run([sys.executable, str(parent), str(child), str(marker)], cancel=cancel)
        self.assertTrue(res.cancelled)
        self.assertLess(time.monotonic() - start, 10)
        time.sleep(3.5)
        self.assertFalse(marker.exists(), "the grandchild survived the cancel")

    def test_timeout(self):
        res = procs.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertTrue(res.timed_out)


if __name__ == "__main__":
    unittest.main()
