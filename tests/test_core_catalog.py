"""catalog: option tables, normalisation rules (BRIDGE_API.md 3.4) and output naming (CORE.md 5.1)."""

from __future__ import annotations

import unittest
from pathlib import Path

from media_forge.core import catalog

from tests.core_support import ScratchCase

CAPS_GPU = {"nvenc": {"h264": True, "h265": True, "av1": False}, "heif": True}
CAPS_NONE = {"nvenc": {"h264": False, "h265": False, "av1": False}, "heif": False}


class NormalizeTest(unittest.TestCase):
    def test_defaults_are_complete_and_stable(self):
        for kind in catalog.KINDS:
            self.assertEqual(catalog.normalize(kind, {}), catalog.DEFAULTS[kind])
            self.assertEqual(catalog.normalize(kind, catalog.DEFAULTS[kind]), catalog.DEFAULTS[kind])

    def test_unknown_keys_dropped(self):
        result = catalog.normalize("video", {"format": "mkv", "bogus": 1, "__proto__": {}})
        self.assertNotIn("bogus", result)
        self.assertEqual(set(result), set(catalog.DEFAULTS["video"]))

    def test_numbers_clamped_and_snapped(self):
        v = catalog.normalize("video", {"quality": 250, "audio_bitrate_k": 200})
        self.assertEqual(v["quality"], 100)
        self.assertEqual(v["audio_bitrate_k"], 192)
        self.assertEqual(catalog.normalize("video", {"quality": -5})["quality"], 0)
        self.assertEqual(catalog.normalize("video", {"quality": "55"})["quality"], 55)
        self.assertEqual(catalog.normalize("audio", {"bitrate_k": 1000})["bitrate_k"], 320)
        self.assertEqual(catalog.normalize("audio", {"bitrate_k": 70})["bitrate_k"], 64)

    def test_enums_fall_back_to_default(self):
        v = catalog.normalize("video", {"format": "avi", "scale": "3x", "fps": 30, "encoder": True})
        self.assertEqual(v["format"], "mp4")
        self.assertEqual(v["scale"], "none")
        self.assertEqual(v["fps"], "30")  # numbers are accepted for numeric ids
        self.assertEqual(v["encoder"], "auto")
        a = catalog.normalize("audio", {"sample_rate": 48000, "channels": 1})
        self.assertEqual((a["sample_rate"], a["channels"]), ("48000", "1"))

    def test_size_limit(self):
        self.assertIsNone(catalog.normalize("image", {"size_limit_mb": 0})["size_limit_mb"])
        self.assertIsNone(catalog.normalize("image", {"size_limit_mb": -2})["size_limit_mb"])
        self.assertIsNone(catalog.normalize("image", {"size_limit_mb": "abc"})["size_limit_mb"])
        self.assertEqual(catalog.normalize("image", {"size_limit_mb": 0.5})["size_limit_mb"], 0.5)
        self.assertEqual(catalog.normalize("image", {"size_limit_mb": "2"})["size_limit_mb"], 2.0)

    def test_booleans(self):
        self.assertTrue(catalog.normalize("video", {"sharpen": "no"})["sharpen"])
        self.assertFalse(catalog.normalize("video", {"sharpen": False})["sharpen"])

    def test_codec_not_allowed_for_format(self):
        self.assertEqual(catalog.normalize("video", {"format": "webm", "codec": "h264"})["codec"], "vp9")
        self.assertEqual(catalog.normalize("video", {"format": "mp4", "codec": "vp9"})["codec"], "h264")
        self.assertEqual(catalog.normalize("video", {"format": "mov", "codec": "av1"})["codec"], "h264")
        self.assertEqual(catalog.normalize("video", {"format": "mp4", "codec": "copy"})["codec"], "h264")
        # codec-less formats keep the value; it does not apply
        self.assertEqual(catalog.normalize("video", {"format": "gif", "codec": "h265"})["codec"], "h265")

    def test_copy_rules(self):
        base = {"format": "mkv", "codec": "copy"}
        self.assertEqual(catalog.normalize("video", base)["codec"], "copy")
        self.assertEqual(catalog.normalize("video", {**base, "scale": "2x"})["codec"], "h264")
        self.assertEqual(catalog.normalize("video", {**base, "size_limit_mb": 5})["codec"], "h264")
        self.assertEqual(catalog.normalize("video", {**base, "fps": "30"})["codec"], "h264")

    def test_gpu_rules(self):
        self.assertEqual(catalog.normalize("video", {"encoder": "gpu"}, CAPS_GPU)["encoder"], "gpu")
        self.assertEqual(catalog.normalize("video", {"encoder": "gpu", "codec": "av1"}, CAPS_GPU)["encoder"], "auto")
        self.assertEqual(catalog.normalize("video", {"encoder": "gpu"}, CAPS_NONE)["encoder"], "auto")
        self.assertEqual(catalog.normalize("video", {"format": "webm", "encoder": "gpu"}, CAPS_GPU)["encoder"],
                         "auto")
        # before the probe the static table decides
        self.assertEqual(catalog.normalize("video", {"encoder": "gpu"}, None)["encoder"], "gpu")
        self.assertEqual(catalog.normalize("video", {"codec": "prores", "format": "mov", "encoder": "gpu"},
                                           None)["encoder"], "auto")

    def test_upscale_method_kept_without_upscale(self):
        v = catalog.normalize("video", {"scale": "720p", "upscale_method": "fsrcnn"})
        self.assertEqual(v["upscale_method"], "fsrcnn")

    def test_heic_only_with_heif(self):
        self.assertEqual(catalog.normalize("image", {"format": "heic"}, CAPS_GPU)["format"], "heic")
        self.assertEqual(catalog.normalize("image", {"format": "heic"}, CAPS_NONE)["format"], "jpg")
        ids = [f["id"] for f in catalog.options(CAPS_NONE)["image"]["formats"]]
        self.assertNotIn("heic", ids)
        ids = [f["id"] for f in catalog.options(CAPS_GPU)["image"]["formats"]]
        self.assertIn("heic", ids)

    def test_opus_bitrate_clamped(self):
        self.assertEqual(catalog.normalize("audio", {"format": "opus", "bitrate_k": 320})["bitrate_k"], 256)
        self.assertEqual(catalog.normalize("audio", {"format": "mp3", "bitrate_k": 320})["bitrate_k"], 320)

    def test_merge(self):
        current = catalog.normalize("video", {"format": "mkv", "codec": "vp9", "quality": 40})
        merged = catalog.merge("video", current, {"format": "mp4"})
        self.assertEqual((merged["format"], merged["codec"], merged["quality"]), ("mp4", "h264", 40))

    def test_edsr_is_photos_only(self):
        video = catalog.normalize("video", {"scale": "2x", "upscale_method": "edsr"}, CAPS_GPU)
        self.assertEqual(video["upscale_method"], "fsrcnn")
        image = catalog.normalize("image", {"scale": "2x", "upscale_method": "edsr"}, CAPS_GPU)
        self.assertEqual(image["upscale_method"], "edsr")
        self.assertEqual([m["id"] for m in catalog.VIDEO_UPSCALE_METHODS], ["lanczos", "fsrcnn"])

    def test_options_shape(self):
        opts = catalog.options(CAPS_GPU)
        self.assertEqual([f["id"] for f in opts["video"]["formats"]],
                         ["mp4", "mkv", "webm", "mov", "gif", "webp", "apng", "mp3", "m4a", "opus", "flac", "wav"])
        self.assertEqual(opts["video"]["audio_bitrates"], [96, 128, 192, 256, 320])
        self.assertEqual(opts["audio"]["bitrates"], [64, 96, 128, 160, 192, 256, 320])
        self.assertEqual([m["id"] for m in opts["upscale_methods"]], ["lanczos", "fsrcnn", "edsr"])
        self.assertNotIn("update_intervals", opts)
        opts["video"]["formats"].clear()  # a copy, not the module table
        self.assertTrue(catalog.options(CAPS_GPU)["video"]["formats"])

    def test_short_labels_carry_their_meaning_as_titles(self):
        opts = catalog.options(CAPS_GPU)
        video = {f["id"]: f for f in opts["video"]["formats"]}
        audio = {f["id"]: f for f in opts["audio"]["formats"]}
        self.assertEqual((video["webp"]["label"], video["webp"]["title"]), ("WebP", "Animated WebP"))
        self.assertEqual((video["apng"]["label"], video["apng"]["title"]), ("APNG", "Animated PNG"))
        self.assertEqual((audio["m4a"]["label"], audio["m4a"]["title"]), ("M4A", "AAC in M4A"))
        self.assertEqual((audio["ogg"]["label"], audio["ogg"]["title"]), ("OGG", "Vorbis in OGG"))
        for kind in ("video", "image", "audio"):
            for f in opts[kind]["formats"]:
                with self.subTest(kind=kind, id=f["id"]):
                    self.assertTrue(isinstance(f.get("title", ""), str))
                    self.assertNotEqual(f.get("title"), "")

    def test_audio_only_outputs_are_named_by_their_format_alone(self):
        # The UI shows them in a row labelled "Audio only", with the same names as the audio kind's formats.
        opts = catalog.options(CAPS_GPU)
        video = {f["id"]: f["label"] for f in opts["video"]["formats"]}
        audio = {f["id"]: f["label"] for f in opts["audio"]["formats"]}
        self.assertEqual({i: video[i] for i in catalog.AUDIO_ONLY_VIDEO_FORMATS},
                         {i: audio[i] for i in catalog.AUDIO_ONLY_VIDEO_FORMATS})
        for fid, label in video.items():
            self.assertNotIn("audio", label.lower(), fid)

    def test_the_ui_splits_the_video_formats_as_the_core_does(self):
        panels = (Path(catalog.__file__).parents[1] / "ui" / "js" / "panels.js").read_text(encoding="utf-8")
        line = next(t for t in panels.splitlines() if t.startswith("const AUDIO_ONLY = new Set("))
        ids = tuple(part.strip(" '") for part in line.split("[", 1)[1].split("]", 1)[0].split(","))
        self.assertEqual(ids, catalog.AUDIO_ONLY_VIDEO_FORMATS)

    def test_kind_for_extension(self):
        self.assertEqual(catalog.kind_for_extension(r"C:\a\b.MP4"), "video")
        self.assertEqual(catalog.kind_for_extension("x.heic"), "image")
        self.assertEqual(catalog.kind_for_extension("x.gif"), "image")
        self.assertEqual(catalog.kind_for_extension("x.opus"), "audio")
        self.assertIsNone(catalog.kind_for_extension("x.txt"))


class OutputPathTest(ScratchCase):
    def test_beside_and_numbering(self):
        src = self.out / "clip.mp4"
        src.write_bytes(b"x")
        gs = {"output_mode": "beside", "suffix": "_forge"}
        s = catalog.normalize("video", {"format": "webm"})
        first = catalog.output_path(src, "video", s, gs)
        self.assertEqual(first, self.out / "clip_forge.webm")
        reserved = catalog.output_path(src, "video", s, gs, reserve=True)
        self.assertEqual(reserved, first)
        self.assertTrue(reserved.exists())
        second = catalog.output_path(src, "video", s, gs, reserve=True)
        self.assertEqual(second, self.out / "clip_forge (2).webm")
        third = catalog.output_path(src, "video", s, gs)
        self.assertEqual(third, self.out / "clip_forge (3).webm")

    def test_folder_mode_and_never_the_source(self):
        folder = self.out / "target"
        folder.mkdir(exist_ok=True)
        src = folder / "pic.jpg"
        src.write_bytes(b"x")
        gs = {"output_mode": "folder", "output_dir": str(folder), "suffix": ""}
        s = catalog.normalize("image", {"format": "jpg"})
        result = catalog.output_path(src, "image", s, gs)
        self.assertEqual(result, folder / "pic (2).jpg")
        s_png = catalog.normalize("image", {"format": "png"})
        self.assertEqual(catalog.output_path(src, "image", s_png, gs), folder / "pic.png")

    def test_subfolder_mirrored_in_folder_mode_only(self):
        import os
        import tempfile

        src = self.out / "card" / "2024" / "05" / "pic.jpg"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x")
        target = Path(tempfile.mkdtemp(prefix="mirror_", dir=self.out))
        rel = os.path.join("2024", "05")
        gs = {"output_mode": "folder", "output_dir": str(target), "suffix": "_forge"}
        s = catalog.normalize("image", {"format": "jpg"})
        planned = catalog.output_path(src, "image", s, gs, rel_dir=rel)
        self.assertEqual(planned, target / "2024" / "05" / "pic_forge.jpg")
        self.assertFalse(planned.parent.exists())  # a preview creates nothing
        reserved = catalog.output_path(src, "image", s, gs, reserve=True, rel_dir=rel)
        self.assertEqual(reserved, planned)
        self.assertTrue(reserved.is_file())
        beside = {"output_mode": "beside", "suffix": "_forge"}
        self.assertEqual(catalog.output_path(src, "image", s, beside, rel_dir=rel), src.parent / "pic_forge.jpg")
        for bad in ("..", os.path.join("..", "up"), str(self.out), "C:/x", "C:x"):
            with self.subTest(rel_dir=bad):  # a sub-folder that would leave the output folder is ignored
                self.assertEqual(catalog.output_path(src, "image", s, gs, rel_dir=bad).parent, target)
        self.assertEqual(catalog.mirrored_folders(gs, rel), [target / "2024", target / "2024" / "05"])
        self.assertEqual(catalog.mirrored_folders(beside, rel), [])
        self.assertEqual(catalog.mirrored_folders(gs, ".."), [])

    def test_a_mirrored_folder_removed_meanwhile_is_made_again(self):
        """A failed job of the same sub-folder removes the empty folder between the mkdir and the reservation."""
        import os
        import tempfile
        from unittest import mock

        src = self.out / "card2" / "pic.jpg"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x")
        target = Path(tempfile.mkdtemp(prefix="mirror_gone_", dir=self.out))
        gs = {"output_mode": "folder", "output_dir": str(target), "suffix": "_forge"}
        s = catalog.normalize("image", {"format": "jpg"})
        real_open, calls = os.open, []

        def vanishing_open(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                os.rmdir(os.path.dirname(path))
            return real_open(path, *args, **kwargs)

        with mock.patch.object(catalog.os, "open", vanishing_open):
            reserved = catalog.output_path(src, "image", s, gs, reserve=True, rel_dir="day")
        self.assertEqual(reserved, target / "day" / "pic_forge.jpg")
        self.assertTrue(reserved.is_file())
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
