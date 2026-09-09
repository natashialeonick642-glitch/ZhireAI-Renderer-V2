"""JSON persistence and history management."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional, Union

from .constants import new_default_settings
from .providers import apply_provider_preset


def merge_settings(value: Optional[dict]) -> dict:
    """Merge saved values onto defaults without sharing nested objects."""

    result = new_default_settings()
    if not isinstance(value, dict):
        return result
    configured = apply_provider_preset(value)
    for key, item in configured.items():
        if key in ("headers", "request_template") and isinstance(item, dict):
            result[key] = deepcopy(item)
        else:
            result[key] = deepcopy(item)
    return result


class JsonStore:
    """Small atomic JSON store suitable for settings and history."""

    def __init__(self, path: Union[str, Path], default: Any):
        self.path = Path(path)
        self.default = default

    def load(self) -> Any:
        if not self.path.exists():
            return deepcopy(self.default)
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError, TypeError):
            return deepcopy(self.default)

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


class HistoryStore:
    def __init__(self, path: Union[str, Path], limit: int = 200):
        self.store = JsonStore(path, [])
        self.limit = max(1, int(limit))
        self.items = self._valid_items(self.store.load())

    @staticmethod
    def _valid_items(value: Any) -> list[dict]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            # Older builds stored a resized/cropped copy as ``*_canvas_WxH``.
            # Keep those files intact, but make history point back to the
            # original API result whenever it is still present.
            raw_result = str(item.get("result") or "")
            source = Path(raw_result)
            if source.is_file() and "_canvas_" in source.stem:
                original_stem = source.stem.split("_canvas_", 1)[0]
                original = source.with_name(original_stem + source.suffix)
                if original.is_file():
                    item = dict(item)
                    item["result"] = str(original)
            result.append(item)
        return result

    def add(self, **values: Any) -> dict:
        item = {
            "id": uuid.uuid4().hex,
            "created_at": time.time(),
            "prompt": "",
            "scene_image": "",
            "scene_context": {},
            "references": [],
            "animation_frames": [],
            "result": "",
            "model": "",
            "provider": "",
            "workflow": "image",
            "aspect": "1:1",
            "aspect_setting": "自动",
            "quality": "1K",
            "count": 1,
            "preserve_composition": True,
            "width": 1024,
            "height": 1024,
            "duration": 5,
            "fps": 24,
            "video_resolution": "720P",
        }
        item.update(values)
        self.items.insert(0, item)
        self.items = self.items[: self.limit]
        self.store.save(self.items)
        return item

    def delete(self, item_id: str) -> bool:
        before = len(self.items)
        self.items = [item for item in self.items if item.get("id") != item_id]
        changed = len(self.items) != before
        if changed:
            self.store.save(self.items)
        return changed

    def clear(self) -> None:
        self.items = []
        self.store.save(self.items)

    def find(self, item_id: str) -> Optional[dict]:
        for item in self.items:
            if item.get("id") == item_id:
                return item
        return None
