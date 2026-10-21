"""Headless harness: probe a file, print the plan, run the job with a progress line, print the result.

    python -m media_forge.core.cli <file> [<file> ...] [--set key=value ...] [--out DIR] [--suffix TEXT]
                                   [--plan-only] [--json]

`--set` values are read as JSON when they parse (numbers, true / false, null), else as text. Settings changes are
kept in memory only; the data folder is `%MEDIA_FORGE_HOME%` (or `media-forge_data` beside the project).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from typing import Any

from .. import __version__
from .engine import Engine
from .errors import UserError


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _parse_sets(pairs: list[str]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--set needs key=value, got {pair!r}")
        patch[key.strip()] = _parse_value(value.strip())
    return patch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m media_forge.core.cli",
                                     description="Convert, compress or upscale media files with the Media Forge "
                                                 "core, without the window.")
    parser.add_argument("files", nargs="+", help="input files")
    parser.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                        help="item setting of the file's kind (BRIDGE_API.md 3.3), e.g. format=webm, quality=60, "
                             "size_limit_mb=2, scale=2x, upscale_method=fsrcnn; repeatable")
    parser.add_argument("--out", metavar="DIR", help="write outputs into DIR instead of beside the source")
    parser.add_argument("--suffix", metavar="TEXT", help="output name suffix (default _forge)")
    parser.add_argument("--plan-only", action="store_true", help="print the plan without running the job")
    parser.add_argument("--json", action="store_true", help="print the final items as JSON")
    parser.add_argument("--timeout", type=float, default=None, help="seconds to wait for all jobs")
    parser.add_argument("-v", "--verbose", action="store_true", help="log to stderr")
    return parser


class _Printer:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_line = 0.0
        self.batch_done = threading.Event()

    def __call__(self, name: str, payload: Any) -> None:
        with self.lock:
            if name == "item" and payload.get("status") == "running":
                now = time.monotonic()
                if now - self.last_line < 0.5:
                    return
                self.last_line = now
                pct = int(round((payload.get("progress") or 0) * 100))
                parts = [f"{payload['name']}: {pct:3d}%", payload.get("stage") or ""]
                if payload.get("speed"):
                    parts.append(payload["speed"])
                if payload.get("eta") is not None:
                    parts.append(f"eta {payload['eta']:.0f}s")
                print("  " + "  ".join(p for p in parts if p), flush=True)
            elif name == "toast":
                print(f"[{payload.get('kind')}] {payload.get('message')}", flush=True)
            elif name == "batch_done":
                self.batch_done.set()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    printer = _Printer()
    engine = Engine(printer, __version__, persist_settings=False)
    engine.start()
    try:
        engine.wait_tools()
        global_patch: dict[str, Any] = {}
        if args.out:
            global_patch.update(output_mode="folder", output_dir=args.out)
        if args.suffix is not None:
            global_patch["suffix"] = args.suffix
        if global_patch:
            engine.set_settings(global_patch)
        items = engine.add_files(args.files)
        engine.wait_probed()
        patch = _parse_sets(args.sets)
        runnable: list[str] = []
        for added in items:
            item = engine.item(added["id"])
            if item is None:
                continue
            if item["status"] == "error":
                print(f"{item['name']}: {item['error']}")
                continue
            if patch:
                item = engine.set_item_settings(item["id"], patch)
            probe = item["probe"] or {}
            print(f"{item['name']}: {item['kind']}, {probe.get('format')}, "
                  f"{probe.get('width')}x{probe.get('height')}, duration {probe.get('duration')}, "
                  f"{item['size']} bytes")
            print("  settings: " + json.dumps(item["settings"]))
            print("  plan:\n    " + engine.plan(item["id"]).replace("\n", "\n    "))
            runnable.append(item["id"])
        if args.plan_only or not runnable:
            return 0 if runnable or not items else 1
        engine.start_items(runnable)
        engine.wait_idle(args.timeout)
        failed = 0
        final = [engine.item(i) for i in runnable]
        for item in final:
            if item is None:
                continue
            if item["status"] == "done":
                out = item["output"]
                dims = f", {out['width']}x{out['height']}" if out.get("width") else ""
                print(f"{item['name']}: done -> {out['path']} ({out['size']} bytes, ratio {out['ratio']}{dims})")
            else:
                failed += 1
                print(f"{item['name']}: {item['status']}: {item['error']}")
        if args.json:
            shown = [{**i, "thumb": "(data url)" if i.get("thumb") else None} for i in final if i]
            print(json.dumps(shown, indent=2, default=str))
        return 1 if failed else 0
    except UserError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    finally:
        engine.stop()


if __name__ == "__main__":
    sys.exit(main())
