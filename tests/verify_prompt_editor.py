"""Headless checks for the Cinema 4D prompt editor contract."""

from __future__ import annotations

import ast
import importlib.machinery
import os
import sys
from pathlib import Path

import c4d


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "zhireAI"
sys.path.insert(0, str(PLUGIN_ROOT))
os.chdir(PLUGIN_ROOT)

plugin_scripts = [
    path
    for path in PLUGIN_ROOT.rglob("*")
    if path.suffix.lower() in {".py", ".pyp"}
]
for plugin_script in plugin_scripts:
    ast.parse(
        plugin_script.read_text(encoding="utf-8-sig"),
        filename=str(plugin_script),
    )

patch = importlib.machinery.SourceFileLoader(
    "_zhire_prompt_editor_check",
    str(PLUGIN_ROOT / "zzz_zhire_geometry_capture.pyp"),
).load_module()


class FakeEditor:
    prompt_height = 999

    def __init__(self) -> None:
        self.call = None

    def AddMultiLineEditText(self, *args, **kwargs) -> None:
        self.call = (args, kwargs)

    def _apply_prompt_text_color(self) -> None:
        pass


editor = FakeEditor()
patch._fixed_prompt_editor(editor)
args, kwargs = editor.call
assert patch.MAX_PROMPT_ROWS == 10
assert editor.prompt_height == patch.MAX_PROMPT_HEIGHT == 110
assert args[1] & c4d.BFV_FIXED
assert kwargs["style"] & c4d.DR_MULTILINE_WORDWRAP
assert not kwargs["style"] & c4d.DR_MULTILINE_NO_SCROLLBARS


class FakeTypingState:
    def __init__(self, before: str, after: str) -> None:
        self.before = before
        self.after = after
        self.prompt_rewrite_active = False
        self.last_prompt_text = before
        self.prompt_logical_text = before
        self.pending_prompt_cursor = None
        self.prompt_placeholder_active = False
        self.prompt_auto_breaks = ()
        self.prompt_rewrap_deadline = 99.0
        self.mention_dialog_open = False
        self.state = type("State", (), {"references": []})()

    def GetString(self, _item_id: int) -> str:
        return self.after

    def _apply_prompt_text_color(self) -> None:
        pass

    def _set_status(self, _value: str) -> None:
        pass


for before, after in (
    ("第一行\n第二行", "第新一行\n第二行"),
    ("第一行\n第二行", "第一行\n\n第二行"),
    ("第一行\n第二行", "第一\n第二行"),
):
    state = FakeTypingState(before, after)
    patch._ui.StudioDialog._handle_prompt_change(state, before)
    assert state.prompt_logical_text == after
    assert state.last_prompt_text == after
    assert state.prompt_rewrap_deadline == 0.0


class FakeHistoryState:
    workflow = "image"
    prompt = ""
    current_result = ""
    scene_image = ""
    references = []
    animation_frames = []

    def restore_history_snapshot(self, _item: dict) -> None:
        pass


class FakeHistoryDialog:
    def __init__(self) -> None:
        item = {"id": "old-pro", "model": "nano-banana-pro", "provider": "custom"}
        self.history = type("History", (), {"find": lambda _self, _item_id: item})()
        self.state = FakeHistoryState()
        self.settings = {"provider": "custom", "model": "gemini-3.1-flash-image-preview"}
        self.preview_dismissed = False

    def _populate_parameter_controls(self) -> None:
        pass

    def _set_prompt_text(self, _prompt: str) -> None:
        pass

    def _refresh_reference_controls(self) -> None:
        pass

    def _refresh_history(self) -> None:
        pass

    def _refresh_preview(self) -> None:
        pass

    def _set_status(self, _message: str, _progress: float) -> None:
        pass


history_dialog = FakeHistoryDialog()
patch._ui.StudioDialog.history_selected(history_dialog, "old-pro")
assert history_dialog.settings["model"] == "gemini-3-pro-image-preview"

print("PROMPT_EDITOR_CHECK_OK", len(plugin_scripts))
