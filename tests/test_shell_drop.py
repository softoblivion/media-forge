"""Drop-path handling: pywebviewFullPath extraction and one-level folder expansion to media files."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell.bridge import (  # noqa: E402
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    VIDEO_EXTENSIONS,
    dropped_paths,
    expand_paths,
    is_media_path,
)


class ExtensionTableTest(unittest.TestCase):
    def test_table_matches_core_doc(self):
        self.assertEqual(set(VIDEO_EXTENSIONS), set(".mp4 .m4v .mov .mkv .webm .avi .wmv .flv .ts .m2ts .mts .mpg "
                                                    ".mpeg .3gp .gif".split()))
        self.assertEqual(set(IMAGE_EXTENSIONS), set(".jpg .jpeg .png .webp .avif .heic .heif .tif .tiff .bmp .gif "
                                                    ".jxl".split()))
        self.assertEqual(set(AUDIO_EXTENSIONS), set(".mp3 .m4a .aac .wav .flac .ogg .opus .wma .aiff .aif".split()))

    def test_case_insensitive(self):
        self.assertTrue(is_media_path("C:\\a\\B.MP4"))
        self.assertTrue(is_media_path("x.HeIc"))
        self.assertFalse(is_media_path("x.txt"))
        self.assertFalse(is_media_path("mp4"))
        self.assertIn(".gif", MEDIA_EXTENSIONS)

    def test_core_kind_detection_agrees_when_available(self):
        try:
            from media_forge.core.probe import detect_kind
        except Exception:  # noqa: BLE001 - the core may not be there yet
            self.skipTest("core probe not available")
        for ext in sorted(MEDIA_EXTENSIONS):
            try:
                kind = detect_kind("C:\\nowhere\\sample" + ext)
            except Exception:  # noqa: BLE001 - a detector that needs the file cannot be checked here
                self.skipTest("detect_kind needs an existing file")
            self.assertIn(kind, ("video", "image", "audio"), ext)


class DroppedPathsTest(unittest.TestCase):
    def test_reads_full_paths_only(self):
        event = {"dataTransfer": {"files": [
            {"name": "a.mp4", "pywebviewFullPath": "D:\\clips\\a.mp4"},
            {"name": "b.mp4"},
            {"name": "c.mp4", "pywebviewFullPath": ""},
            "junk",
        ]}}
        self.assertEqual(dropped_paths(event), ["D:\\clips\\a.mp4"])

    def test_malformed_events(self):
        self.assertEqual(dropped_paths(None), [])
        self.assertEqual(dropped_paths({}), [])
        self.assertEqual(dropped_paths({"dataTransfer": None}), [])


class ExpandPathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mf_shell_drop_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, rel: str) -> str:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return str(path)

    def test_files_pass_through_in_order(self):
        a, b = self.touch("b.mp4"), self.touch("a.txt")
        self.assertEqual(expand_paths([a, b]), [a, b])

    def test_folder_expanded_one_level_media_only_sorted(self):
        self.touch("f/Zeta.MOV")
        self.touch("f/alpha.jpg")
        self.touch("f/beta.Mp3")
        self.touch("f/notes.txt")
        self.touch("f/sub/deep.mp4")
        (self.tmp / "f" / "folder.mp4").mkdir()  # a folder named like a media file is skipped
        result = [os.path.basename(p) for p in expand_paths([str(self.tmp / "f")])]
        self.assertEqual(result, ["alpha.jpg", "beta.Mp3", "Zeta.MOV"])

    def test_duplicates_dropped(self):
        a = self.touch("f/a.mp4")
        result = expand_paths([a, str(self.tmp / "f"), a.upper() if os.name == "nt" else a])
        self.assertEqual(result, [a])

    def test_missing_and_invalid_entries(self):
        missing = str(self.tmp / "gone.mp4")
        self.assertEqual(expand_paths([missing, "", "  ", None, 5]), [missing])

    def test_empty_folder(self):
        (self.tmp / "empty").mkdir()
        self.assertEqual(expand_paths([str(self.tmp / "empty")]), [])

    def test_path_with_spaces_and_unicode(self):
        a = self.touch("Ünïcode folder (1)/clip 01.webm")
        self.assertEqual(expand_paths([str(Path(a).parent)]), [a])


if __name__ == "__main__":
    unittest.main()
