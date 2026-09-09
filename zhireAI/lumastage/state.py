"""In-memory state shared by the Cinema 4D dialogs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _history_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _history_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


@dataclass
class GenerationOptions:
    aspect: str = "自动"
    quality: str = "1K"
    count: int = 1
    negative_prompt: str = ""
    preserve_composition: bool = True
    duration_seconds: int = 5
    fps: float = 24.0
    video_resolution: str = "720P"


@dataclass
class StudioState:
    workflow: str = "image"
    scene_image: str = ""
    scene_context: dict = field(default_factory=dict)
    animation_frames: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    selected_reference: int = -1
    prompt: str = ""
    options: GenerationOptions = field(default_factory=GenerationOptions)
    current_result: str = ""
    selected_history_id: str = ""
    compare_enabled: bool = False
    busy: bool = False
    render_busy: bool = False
    status: str = "就绪"
    progress: float = 0.0
    last_error: str = ""
    worker: Optional[object] = None

    def restore_history_snapshot(self, item: dict) -> None:
        """Replace the current workspace with one complete history snapshot."""

        workflow = "animation" if item.get("workflow") == "animation" else "image"
        references = [
            str(path)
            for path in (item.get("references") or [])
            if str(path or "").strip()
        ][:8]
        frames = [
            str(path)
            for path in (item.get("animation_frames") or [])
            if str(path or "").strip()
        ]
        scene_context = item.get("scene_context")

        self.selected_history_id = str(item.get("id") or "")
        self.workflow = workflow
        self.current_result = str(item.get("result") or "")
        self.scene_image = str(item.get("scene_image") or "")
        self.scene_context = dict(scene_context) if isinstance(scene_context, dict) else {}
        self.animation_frames = frames
        self.references = references
        self.selected_reference = 0 if references else -1
        self.prompt = str(item.get("prompt") or "")

        self.options.aspect = str(
            item.get("aspect_setting") or item.get("aspect") or "自动"
        )
        self.options.quality = str(item.get("quality") or "1K")
        self.options.count = max(1, min(4, _history_int(item.get("count"), 1)))
        self.options.preserve_composition = bool(
            item.get("preserve_composition", True)
        )
        self.options.duration_seconds = _history_int(item.get("duration"), 5)
        self.options.fps = max(1.0, _history_float(item.get("fps"), 24.0))
        self.options.video_resolution = str(
            item.get("video_resolution") or "720P"
        )

    def add_reference(self, path: str) -> bool:
        normalized = str(path)
        if normalized in self.references:
            self.selected_reference = self.references.index(normalized)
            return False
        self.references.append(normalized)
        self.selected_reference = len(self.references) - 1
        return True

    def delete_selected_reference(self) -> str:
        if not (0 <= self.selected_reference < len(self.references)):
            return ""
        removed = self.references.pop(self.selected_reference)
        self.selected_reference = min(self.selected_reference, len(self.references) - 1)
        return removed
