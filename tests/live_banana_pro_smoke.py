"""Run an opt-in Banana Pro request through the plug-in adapter.

This script contains no credentials or machine-specific paths. Pass a local
settings file, history file, prepared references and output directory when a
live provider check is needed.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path


BANANA_PRO_MODELS = {
    "gemini-3-pro-image-preview",
    "nano-banana-pro",
    "nano-banana-pro-cl",
}


def _latest_banana_pro(history_path: Path) -> dict:
    entries = json.loads(history_path.read_text(encoding="utf-8-sig"))
    for entry in reversed(entries if isinstance(entries, list) else []):
        if str(entry.get("model") or "").strip() in BANANA_PRO_MODELS:
            return dict(entry)
    raise RuntimeError("History does not contain a Banana Pro image job.")


def _load_adapter(plugin_root: Path):
    sys.path.insert(0, str(plugin_root))
    from lumastage import api

    adapter_path = plugin_root / "zz_juaihub_adapter.pyp"
    loader = importlib.machinery.SourceFileLoader("zhire_live_juaihub_adapter", str(adapter_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("Unable to create the JuAIHub adapter module spec.")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return api, module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--reference", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = json.loads(args.settings.read_text(encoding="utf-8-sig"))
    if not str(settings.get("api_key") or "").strip():
        raise RuntimeError("The selected settings file does not contain an API key.")
    job = _latest_banana_pro(args.history)
    scene_path = Path(str(job.get("scene_image") or ""))
    if not scene_path.is_file():
        raise RuntimeError("The scene image from history no longer exists.")
    if scene_path.stem.startswith("geometry_"):
        depth_name = "geometry_depth_" + scene_path.stem[len("geometry_") :] + scene_path.suffix
    else:
        depth_name = scene_path.stem + "_depth" + scene_path.suffix
    depth_path = scene_path.with_name(depth_name)
    if not depth_path.is_file():
        raise RuntimeError("The matching depth image no longer exists.")
    if not all(path.is_file() for path in args.reference):
        raise RuntimeError("At least one prepared reference image does not exist.")

    job.update(
        {
            "workflow": "image",
            "model": "gemini-3-pro-image-preview",
            "references": [str(path) for path in args.reference],
            "scene_context": {
                "preserve_composition": True,
                "depth_image": str(depth_path),
            },
            "quality": "2K",
            "count": 1,
        }
    )
    args.output.mkdir(parents=True, exist_ok=True)
    api, adapter = _load_adapter(args.plugin_root)
    client = api.ImageApiClient(settings, args.output)
    results = adapter._generate_juaihub(
        client,
        job,
        lambda progress, message: print("{:.0%} {}".format(progress, message), flush=True),
        None,
    )
    for result in results:
        result_path = Path(result)
        print("RESULT {} {} bytes".format(result_path.name, result_path.stat().st_size))


if __name__ == "__main__":
    main()
