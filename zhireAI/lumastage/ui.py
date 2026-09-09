"""Native Cinema 4D user interface for 挚热AI渲染器."""

from __future__ import annotations

import os
import math
import platform
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Union

import c4d

from .api import ImageApiClient
from .constants import (
    ASPECT_PRESETS,
    NETWORK_EVENT_ID,
    PLUGIN_ID,
    PROMPT_EVENT_ID,
    PRODUCT_WINDOW_TITLE,
    QUALITY_PRESETS,
    SEEDANCE_REFERENCE_MAX_BYTES,
    output_dimensions,
    output_dimensions_for_ratio,
    seedance_reference_dimensions,
    seedance_reference_issue,
)
from .prompt import (
    canonical_editor_display,
    editor_prompt_after_change,
    equivalent_editor_display,
    image_token,
    inserted_mention_position,
    plain_editor_prompt,
    referenced_indices,
    tracked_display_offset_from_plain,
    tracked_plain_offset_from_display,
    validate_tokens,
    wrapped_editor_layout,
)
from .prompt_ai import PromptGenerator, skill_options
from .providers import (
    ATLAS_IMAGE_MODELS,
    JUAIHUB_IMAGE_MODELS,
    PROVIDER_LABELS,
    is_compatible_image_model,
    is_compatible_seedance_model,
    is_seedance_25_model,
    seedance_capability,
    seedance_unavailable_message,
)
from .settings_dialog import ApiSettingsDialog
from .state import StudioState
from .storage import HistoryStore, JsonStore, merge_settings
from .worker import GenerationThread, PromptGenerationThread, RenderLoadTask


COLORS = {
    "bg": c4d.Vector(0.055, 0.064, 0.086),
    "preview_empty": c4d.Vector(0.012, 0.014, 0.019),
    "surface": c4d.Vector(0.083, 0.097, 0.128),
    "surface_2": c4d.Vector(0.112, 0.127, 0.165),
    "input_bg": c4d.Vector(0.205, 0.212, 0.225),
    "input_focus_bg": c4d.Vector(0.235, 0.245, 0.265),
    "line": c4d.Vector(0.205, 0.224, 0.285),
    "text": c4d.Vector(0.90, 0.92, 0.97),
    "muted": c4d.Vector(0.55, 0.59, 0.68),
    "primary": c4d.Vector(0.28, 0.55, 0.98),
    "cyan": c4d.Vector(0.08, 0.84, 0.90),
    "mint": c4d.Vector(0.35, 0.82, 0.65),
    "danger": c4d.Vector(0.90, 0.34, 0.36),
    "scene": c4d.Vector(0.95, 0.58, 0.20),
    "mode_track": c4d.Vector(0.075, 0.082, 0.096),
    "mode_selected": c4d.Vector(0.285, 0.295, 0.315),
    "mode_pressed": c4d.Vector(0.345, 0.355, 0.375),
    "mode_disabled": c4d.Vector(0.175, 0.182, 0.195),
}
ACTION_BUTTON_HEIGHT = 36
ACTION_BUTTON_RADIUS = 10.0
PROMPT_EDITOR_STYLE = getattr(c4d, "DR_MULTILINE_WORDWRAP", 256)

PROMPT_PLACEHOLDER = "先写图片职责与画面提示，例如：图1负责材质，图2负责场景；再写想要的画面"
ANIMATION_DURATIONS = (5, 8, 10, 15)
ANIMATION_DURATIONS_25 = (5, 8, 10, 15, 20, 30)
ANIMATION_RESOLUTIONS = ("480P", "720P", "1080P", "4K")
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".exr", ".hdr"
}
PORTABLE_API_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
# Show a larger history window so the outer dialog scroll container handles
# most navigation instead of competing with a short inner list.
HISTORY_VISIBLE_ROWS = 15


class Id:
    GROUP_NAV = 1090
    SCROLL_CONTROLS = 1091
    HEADER_REFRESH = 1092
    API_BADGE = 1093
    GROUP_HISTORY = 1100
    GROUP_CONTROLS = 1101
    HEADER_AREA = 1102
    STATUS = 1103
    TOGGLE_HISTORY = 1104
    TOGGLE_CONTROLS = 1105
    SETTINGS = 1106
    GROUP_PREVIEW = 1107
    GROUP_COMPACT_PREVIEW = 1108
    TOGGLE_PREVIEW = 1109
    HISTORY_AREA = 1110
    HISTORY_DELETE = 1111
    HISTORY_CLEAR = 1112
    HISTORY_OUTPUT_DIRECTORY = 1113
    PREVIEW_AREA = 1120
    COMPARE = 1121
    COMPARE_SLIDER = 1122
    LARGE_PREVIEW = 1123
    OPEN_FOLDER = 1124
    COMPACT_PREVIEW_AREA = 1125
    COMPACT_LARGE_PREVIEW = 1126
    COMPACT_COMPARE = 1127
    CLOSE_COMPARE = 1128
    IMPORT_SCENE = 1130
    CAPTURE_SCENE = 1131
    SCENE_PATH = 1132
    REFERENCE_AREA = 1140
    REFERENCE_ADD = 1141
    REFERENCE_DELETE = 1142
    REFERENCE_TOKEN = 1143
    REFERENCE_INSERT = 1144
    REFERENCE_PASTE = 1145
    REFERENCE_CLEAR = 1146
    RENDER_VIEW_REFERENCE = 1147
    PROMPT = 1150
    NEGATIVE_PROMPT = 1151
    PROMPT_GROUP = 1152
    PROMPT_ROWS = 1153
    PROMPT_MODEL = 1156
    PROMPT_AI_GENERATE = 1157
    PROMPT_SKILL = 1158
    PREVIEW_GROUP = 1154
    PREVIEW_RESIZE_AREA = 1155
    ASPECT = 1160
    QUALITY = 1161
    COUNT = 1162
    PRESERVE = 1164
    MAIN_MODEL = 1165
    PROGRESS = 1170
    GENERATE = 1171
    WORKFLOW = 1172
    CANCEL_GENERATION = 1173
    FOLD_SCENE = 1180
    FOLD_REFERENCES = 1181
    FOLD_PROMPT = 1182
    FOLD_OUTPUT = 1183
    CONTENT_SCENE = 1184
    CONTENT_REFERENCES = 1185
    CONTENT_PROMPT = 1186
    CONTENT_OUTPUT = 1187
    FOLD_RESULT = 1188
    CONTENT_RESULT = 1189
    FOLD_HISTORY = 1190
    CONTENT_HISTORY = 1191
    INLINE_HISTORY_AREA = 1192
    SCENE_CAPTURE_AREA = 1193
    MODE_IMAGE = 1194
    MODE_ANIMATION = 1195
    PARAM_MODEL_LABEL = 1196
    PARAM_ASPECT_LABEL = 1197
    PARAM_QUALITY_LABEL = 1198
    PARAM_COUNT_LABEL = 1199
    MODE_SWITCH = 1200


def _load_bitmap(path: str) -> Optional[c4d.bitmaps.BaseBitmap]:
    if not path or not Path(path).is_file():
        return None
    bitmap = c4d.bitmaps.BaseBitmap()
    result = bitmap.InitWith(path)
    code = result[0] if isinstance(result, tuple) else result
    return bitmap if code == c4d.IMAGERESULT_OK else None


def _is_video_path(path: str) -> bool:
    return Path(str(path or "")).suffix.lower() in VIDEO_EXTENSIONS


def _is_image_path(path: str) -> bool:
    return Path(str(path or "")).suffix.lower() in IMAGE_EXTENSIONS


def _load_preview_bitmap(path: str) -> Optional[c4d.bitmaps.BaseBitmap]:
    """Load an image or decode a video's first frame for the preview canvas."""

    if not path or not Path(path).is_file():
        return None
    if not _is_video_path(path):
        return _load_bitmap(path)
    loader = c4d.bitmaps.MovieLoader()
    try:
        # MovieLoader.Open() returns None in Cinema 4D 2023, while some newer
        # SDK builds return an IMAGERESULT value.  None means that the open
        # call completed; the subsequent GetInfo/Read calls are authoritative.
        open_result = loader.Open(str(path))
        if open_result not in (None, c4d.IMAGERESULT_OK):
            return None
        value = loader.Read(0)
        if not isinstance(value, tuple) or len(value) < 2:
            return None
        result, bitmap = value[0], value[1]
        if result != c4d.IMAGERESULT_OK or bitmap is None:
            return None
        # MovieLoader owns its decoded frame. Clone it before closing the
        # loader so the compact preview keeps a live bitmap.
        return bitmap.GetClone()
    except (AttributeError, RuntimeError, TypeError):
        return None
    finally:
        try:
            loader.Close()
        except (AttributeError, RuntimeError):
            pass


def _movie_reference_info(path: str) -> tuple[int, int, float]:
    """Read a movie's first-frame dimensions and FPS through C4D 2023."""

    if not _is_video_path(path) or not Path(path).is_file():
        return 0, 0, 0.0
    loader = c4d.bitmaps.MovieLoader()
    try:
        open_result = loader.Open(str(path))
        if open_result not in (None, c4d.IMAGERESULT_OK):
            return 0, 0, 0.0
        info = loader.GetInfo()
        fps = float(info[1]) if isinstance(info, tuple) and len(info) > 1 else 0.0
        value = loader.Read(0)
        if not isinstance(value, tuple) or len(value) < 2:
            return 0, 0, fps
        code, bitmap = value[0], value[1]
        if code != c4d.IMAGERESULT_OK or bitmap is None:
            return 0, 0, fps
        return int(bitmap.GetBw()), int(bitmap.GetBh()), fps
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return 0, 0, 0.0
    finally:
        try:
            loader.Close()
        except (AttributeError, RuntimeError):
            pass


def _reference_frames_are_seedance_safe(paths: list[str]) -> bool:
    """Verify cached/snapshot frames before reusing them for a new request."""

    if not paths:
        return False
    for path in paths:
        bitmap = _load_bitmap(path)
        if bitmap is None or seedance_reference_issue(bitmap.GetBw(), bitmap.GetBh()):
            return False
    return True


def _sample_movie_reference_frames(
    path: str, output_dir: Path, sample_count: int = 5
) -> list[str]:
    """Prepare a local MP4 for providers whose API accepts image references."""

    if not _is_video_path(path) or not Path(path).is_file():
        return []
    loader = c4d.bitmaps.MovieLoader()
    results: list[str] = []
    try:
        # C4D 2023 declares MovieLoader.Open() as a void call.  Treating its
        # None return value as an error made every valid MP4 look unsupported.
        open_result = loader.Open(str(path))
        if open_result not in (None, c4d.IMAGERESULT_OK):
            return []
        info = loader.GetInfo()
        if not isinstance(info, tuple) or not info:
            return []
        frame_count = max(0, int(info[0]))
        if frame_count <= 0:
            return []
        # Seedance 2.5 can use a denser ordered reference sequence than the
        # earlier 2.0 adapter. Keep a practical 30-frame ceiling so a long MP4
        # is represented without flooding C4D memory or the upload queue.
        count = max(1, min(30, int(sample_count), frame_count))
        if count == 1:
            indices = [0]
        else:
            indices = sorted(
                {
                    int(round(index * (frame_count - 1) / float(count - 1)))
                    for index in range(count)
                }
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        for sequence, frame_index in enumerate(indices):
            value = loader.Read(frame_index)
            if not isinstance(value, tuple) or len(value) < 2:
                continue
            code, bitmap = value[0], value[1]
            if code != c4d.IMAGERESULT_OK or bitmap is None:
                continue
            target = output_dir / "animation_reference_{}_{:02d}.png".format(
                stamp, sequence
            )
            try:
                target_width, target_height = seedance_reference_dimensions(
                    bitmap.GetBw(), bitmap.GetBh()
                )
            except ValueError:
                return []
            save_bitmap = bitmap
            if (
                int(bitmap.GetBw()) != target_width
                or int(bitmap.GetBh()) != target_height
            ):
                # A fresh bitmap is both writable in C4D 2023 and suitable for
                # scaling. MovieLoader's cloned frame can be displayed but its
                # Save call is unreliable in that host version.
                prepared = c4d.bitmaps.BaseBitmap()
                if prepared.Init(target_width, target_height, 24) != c4d.IMAGERESULT_OK:
                    continue
                bitmap.ScaleIt(prepared, 256, True, True)
                save_bitmap = prepared
            saved = save_bitmap.Save(
                str(target), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
            )
            if saved == c4d.IMAGERESULT_OK and target.is_file():
                results.append(str(target))
        return results
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return []
    finally:
        try:
            loader.Close()
        except (AttributeError, RuntimeError):
            pass


def _portable_api_image(path: str, output_dir: Path) -> str:
    """Convert C4D-only bitmap formats to a provider-safe PNG.

    APIMART, Volcengine and several OpenAI-compatible relays accept a much
    smaller input-format set than Cinema 4D. Do this conversion on C4D's main
    thread before the network worker starts, retaining the exact pixel canvas.
    """

    source_path = Path(str(path or ""))
    if not source_path.is_file():
        raise ValueError("输入图像不存在：{}".format(path))
    if source_path.suffix.lower() in PORTABLE_API_IMAGE_EXTENSIONS:
        return str(source_path)
    bitmap = _load_bitmap(str(source_path))
    if bitmap is None or bitmap.GetBw() <= 0 or bitmap.GetBh() <= 0:
        raise ValueError("无法读取输入图像：{}".format(source_path.name))
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "{}_api_{}.png".format(
        source_path.stem,
        int(time.time() * 1000),
    )
    saved = bitmap.Save(
        str(target), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
    )
    if saved != c4d.IMAGERESULT_OK or not target.is_file():
        raise ValueError("无法将 {} 转换为 API 通用 PNG。".format(source_path.name))
    verified = _load_bitmap(str(target))
    if (
        verified is None
        or verified.GetBw() != bitmap.GetBw()
        or verified.GetBh() != bitmap.GetBh()
    ):
        raise ValueError("输入图像转换后的尺寸校验失败。")
    return str(target)


def _seedance_safe_reference_image(path: str, output_dir: Path) -> str:
    """Create a compact, aspect-preserving Seedance reference image."""

    source_path = Path(str(path or ""))
    bitmap = _load_bitmap(str(source_path))
    if bitmap is None or bitmap.GetBw() <= 0 or bitmap.GetBh() <= 0:
        raise ValueError("无法读取动画参考图：{}".format(source_path.name))
    try:
        target_width, target_height = seedance_reference_dimensions(
            bitmap.GetBw(), bitmap.GetBh()
        )
    except ValueError as exc:
        raise ValueError(
            "动画参考图 {} 不符合 Seedance 的画幅要求：{}".format(
                source_path.name, exc
            )
        ) from exc
    if (
        source_path.suffix.lower() in PORTABLE_API_IMAGE_EXTENSIONS
        and int(bitmap.GetBw()) == target_width
        and int(bitmap.GetBh()) == target_height
    ):
        return str(source_path)
    prepared = c4d.bitmaps.BaseBitmap()
    if prepared.Init(target_width, target_height, 24) != c4d.IMAGERESULT_OK:
        raise ValueError("无法创建 Seedance 动画参考画布。")
    bitmap.ScaleIt(prepared, 256, True, True)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "{}_seedance_{}x{}_{}.png".format(
        source_path.stem,
        target_width,
        target_height,
        int(time.time() * 1000),
    )
    saved = prepared.Save(
        str(target), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
    )
    if saved != c4d.IMAGERESULT_OK or not target.is_file():
        raise ValueError("无法保存尺寸校正后的 Seedance 动画参考图。")
    verified = _load_bitmap(str(target))
    if (
        verified is None
        or int(verified.GetBw()) != target_width
        or int(verified.GetBh()) != target_height
    ):
        raise ValueError("Seedance 动画参考图尺寸校验失败。")
    return str(target)


def _fit_rect(source_w: int, source_h: int, target_w: int, target_h: int) -> tuple[int, int, int, int]:
    if source_w <= 0 or source_h <= 0 or target_w <= 0 or target_h <= 0:
        return 0, 0, 0, 0
    scale = min(float(target_w) / source_w, float(target_h) / source_h)
    width = max(1, int(source_w * scale))
    height = max(1, int(source_h * scale))
    return (target_w - width) // 2, (target_h - height) // 2, width, height


def _cover_source_rect(
    source_w: int, source_h: int, target_w: int, target_h: int
) -> tuple[int, int, int, int]:
    """Return a centered source crop that fills a thumbnail without distortion."""

    if source_w <= 0 or source_h <= 0 or target_w <= 0 or target_h <= 0:
        return 0, 0, 0, 0
    source_ratio = float(source_w) / float(source_h)
    target_ratio = float(target_w) / float(target_h)
    if source_ratio > target_ratio:
        crop_h = source_h
        crop_w = max(1, int(round(crop_h * target_ratio)))
        return max(0, (source_w - crop_w) // 2), 0, crop_w, crop_h
    crop_w = source_w
    crop_h = max(1, int(round(crop_w / target_ratio)))
    return 0, max(0, (source_h - crop_h) // 2), crop_w, crop_h


def _blend_color(background: c4d.Vector, foreground: c4d.Vector, amount: float) -> c4d.Vector:
    """Blend one edge pixel so hand-drawn rounded controls do not staircase."""

    value = max(0.0, min(1.0, float(amount)))
    return c4d.Vector(
        background.x + (foreground.x - background.x) * value,
        background.y + (foreground.y - background.y) * value,
        background.z + (foreground.z - background.z) * value,
    )


def _draw_soft_round_rect(
    area: c4d.gui.GeUserArea,
    left: int,
    top: int,
    right: int,
    bottom: int,
    radius: float,
    fill: c4d.Vector,
    background: c4d.Vector,
) -> None:
    """Draw a compact rounded rectangle with blended one-pixel corner edges."""

    if right < left or bottom < top:
        return
    radius = max(1.0, min(float(radius), (right - left + 1) / 2.0, (bottom - top + 1) / 2.0))
    top_center = top + radius
    bottom_center = bottom + 1.0 - radius
    for row in range(top, bottom + 1):
        sample_y = row + 0.5
        if sample_y < top_center:
            distance = top_center - sample_y
        elif sample_y > bottom_center:
            distance = sample_y - bottom_center
        else:
            distance = 0.0
        inset = radius - math.sqrt(max(0.0, radius * radius - distance * distance))
        edge_left = left + inset
        edge_right = right + 1.0 - inset
        first = int(math.floor(edge_left))
        last = int(math.floor(edge_right))
        left_coverage = max(0.0, min(1.0, first + 1.0 - edge_left))
        right_coverage = max(0.0, min(1.0, edge_right - last))
        interior_left = first + 1
        interior_right = last - 1
        if interior_right >= interior_left:
            area.DrawSetPen(fill)
            area.DrawLine(interior_left, row, interior_right, row)
        if left_coverage > 0.0 and left <= first <= right:
            area.DrawSetPen(_blend_color(background, fill, left_coverage))
            area.DrawLine(first, row, first, row)
        if right_coverage > 0.0 and left <= last <= right and last != first:
            area.DrawSetPen(_blend_color(background, fill, right_coverage))
            area.DrawLine(last, row, last, row)


def _local_input_position(area: c4d.gui.GeUserArea, message: c4d.BaseContainer) -> tuple[int, int]:
    """Convert Cinema 4D's window-relative input coordinates to this user area's space."""

    x = int(message.GetInt32(c4d.BFM_INPUT_X))
    y = int(message.GetInt32(c4d.BFM_INPUT_Y))
    offset = area.Global2Local()
    if offset:
        x += int(offset.get("x", 0))
        y += int(offset.get("y", 0))
    return x, y


def _area_dialog(area):
    """Resolve a user area's owner across old and current C4D Python APIs."""

    # The explicit owner is assigned by StudioDialog and is stable across C4D
    # releases. C4D 2023 can return a generic/proxy GeDialog from GetDialog(),
    # which draws correctly but does not expose our command handlers.
    owner = getattr(area, "_zhire_dialog", None)
    if owner is not None:
        return owner
    getter = getattr(area, "GetDialog", None)
    if callable(getter):
        try:
            dialog = getter()
            if dialog is not None:
                return dialog
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    return None


def _copy_image_to_clipboard(path: str) -> tuple[bool, str]:
    """Copy image pixels to the persistent system clipboard."""

    source = Path(str(path or ""))
    if not source.is_file() or not _is_image_path(str(source)):
        return False, "原图不存在或不是支持的图片格式。"
    system = platform.system()
    clipboard_source = source
    temporary_source: Optional[Path] = None
    try:
        if source.suffix.lower() not in PORTABLE_API_IMAGE_EXTENSIONS:
            clipboard_source = Path(
                _portable_api_image(
                    str(source),
                    Path(tempfile.gettempdir()) / "zhireAI-clipboard",
                )
            )
            if clipboard_source != source:
                temporary_source = clipboard_source
        if system == "Windows":
            environment = os.environ.copy()
            environment["ZHIREAI_CLIPBOARD_SOURCE"] = str(clipboard_source)
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                "$source=[System.Drawing.Image]::FromFile($env:ZHIREAI_CLIPBOARD_SOURCE); "
                "try { $bitmap=[System.Drawing.Bitmap]::new($source); "
                "[System.Windows.Forms.Clipboard]::SetDataObject($bitmap,$true); "
                "$bitmap.Dispose() } finally { $source.Dispose() }"
            )
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-Command",
                    script,
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        elif system == "Darwin":
            script = (
                'use framework "AppKit"\n'
                "on run argv\n"
                "set sourcePath to item 1 of argv\n"
                "set imageObject to current application's NSImage's alloc()'s "
                "initWithContentsOfFile:sourcePath\n"
                'if imageObject is missing value then error "Unable to load image"\n'
                "set pasteboard to current application's NSPasteboard's generalPasteboard()\n"
                "pasteboard's clearContents()\n"
                "pasteboard's writeObjects:{imageObject}\n"
                "end run"
            )
            completed = subprocess.run(
                ["osascript", "-e", script, str(clipboard_source)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        else:
            return False, "当前系统不支持直接复制图片，请打开原图后复制。"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, "复制原图失败：{}".format(exc)
    finally:
        if temporary_source is not None:
            try:
                temporary_source.unlink()
            except OSError:
                pass
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()
        return False, "复制原图失败{}".format("：" + detail if detail else "。")
    return True, ""


def _show_image_copy_menu(dialog, path: str) -> bool:
    """Show the thumbnail context menu when a real image is available."""

    source = str(path or "")
    if (
        dialog is None
        or not hasattr(dialog, "copy_image_requested")
        or not Path(source).is_file()
        or not _is_image_path(source)
    ):
        return False
    menu = c4d.BaseContainer()
    menu_id = getattr(c4d, "FIRST_POPUP_ID", 1000)
    menu.InsData(menu_id, "复制原图")
    mouse_position = getattr(c4d, "MOUSEPOS", -1)
    try:
        command = c4d.gui.ShowPopupDialog(
            cd=dialog,
            bc=menu,
            x=mouse_position,
            y=mouse_position,
        )
    except TypeError:
        command = c4d.gui.ShowPopupDialog(
            dialog, menu, mouse_position, mouse_position
        )
    if command == menu_id:
        dialog.copy_image_requested(source)
    return True


class HeaderArea(c4d.gui.GeUserArea):
    def __init__(self):
        super().__init__()
        self.status = "就绪"
        self.busy = False

    def GetMinSize(self) -> tuple[int, int]:
        return 104, 34

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(c4d.Vector(0.02, 0.02, 0.025))
        self.DrawRectangle(0, 0, width, height)
        self.DrawSetFont(c4d.FONT_BOLD)
        self.DrawSetPen(COLORS["text"])
        self.DrawText(PRODUCT_WINDOW_TITLE, 9, 9)

    def set_status(self, text: str, busy: bool) -> None:
        self.status = text
        self.busy = busy
        self.Redraw()


class PillActionButtonArea(c4d.gui.GeUserArea):
    """Draw one reliable colored pill without tinting the surrounding C4D group."""

    def __init__(
        self,
        command_id: int,
        label: str,
        variant: str,
        min_width: int,
        min_height: int = 26,
    ):
        super().__init__()
        self.command_id = command_id
        self.label = label
        self.variant = variant
        self.min_width = min_width
        self.min_height = min_height
        self.enabled = True
        self.pressed = False
        self.progress_value: Optional[float] = None

    def GetMinSize(self) -> tuple[int, int]:
        return self.min_width, self.min_height

    def _system_background(self) -> c4d.Vector:
        try:
            value = self.GetColorRGB(c4d.COLOR_BG)
            if isinstance(value, dict):
                return c4d.Vector(
                    float(value.get("r", 0)) / 255.0,
                    float(value.get("g", 0)) / 255.0,
                    float(value.get("b", 0)) / 255.0,
                )
        except (AttributeError, TypeError, ValueError):
            pass
        return COLORS["bg"]

    def _fill_color(self) -> c4d.Vector:
        if self.variant == "secondary":
            if not self.enabled:
                return c4d.Vector(0.15, 0.15, 0.15)
            if self.pressed:
                return c4d.Vector(0.16, 0.16, 0.16)
            return c4d.Vector(0.22, 0.22, 0.22)
        if self.variant == "danger":
            if not self.enabled:
                return c4d.Vector(0.48, 0.18, 0.20)
            if self.pressed:
                return c4d.Vector(0.70, 0.18, 0.22)
            return c4d.Vector(0.88, 0.25, 0.29)
        if not self.enabled:
            return c4d.Vector(0.12, 0.29, 0.50)
        if self.pressed:
            return c4d.Vector(0.08, 0.34, 0.68)
        return c4d.Vector(0.08, 0.43, 0.86)

    def _draw_rounded_button(
        self,
        color: c4d.Vector,
        width: int,
        height: int,
        fraction: float = 1.0,
    ) -> None:
        left, top = 1, 1
        right, bottom = max(left, width - 2), max(top, height - 2)
        radius = max(
            1.0,
            min(ACTION_BUTTON_RADIUS, float(bottom - top + 1) / 2.0),
        )
        top_center = top + radius
        bottom_center = bottom - radius
        self.DrawSetPen(color)
        for row in range(top, bottom + 1):
            if row < top_center:
                distance = top_center - float(row)
            elif row > bottom_center:
                distance = float(row) - bottom_center
            else:
                distance = 0.0
            inset = radius - math.sqrt(
                max(0.0, radius * radius - distance * distance)
            )
            offset = int(math.ceil(inset))
            row_left = left + offset
            row_right = max(row_left, right - offset)
            fill_right = min(
                row_right,
                left + int(round((right - left) * max(0.0, min(1.0, fraction)))),
            )
            if fill_right >= row_left:
                self.DrawLine(row_left, row, fill_right, row)

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        background = self._system_background()
        fill = self._fill_color()
        self.OffScreenOn()
        self.DrawSetPen(background)
        self.DrawRectangle(0, 0, max(0, width - 1), max(0, height - 1))
        self._draw_rounded_button(fill, width, height)
        if self.progress_value is not None:
            progress_color = c4d.Vector(0.08, 0.43, 0.86)
            self._draw_rounded_button(
                progress_color,
                width,
                height,
                self.progress_value,
            )
            text_background = progress_color
        else:
            text_background = fill
        self.DrawSetFont(c4d.FONT_DEFAULT)
        self.DrawSetTextCol(c4d.Vector(1.0, 1.0, 1.0), text_background)
        label = self.label
        available = max(8, width - 12)
        if self.DrawGetTextWidth(label) > available:
            suffix = "…"
            while label and self.DrawGetTextWidth(label + suffix) > available:
                label = label[:-1]
            label = (label + suffix) if label else suffix
        text_width = self.DrawGetTextWidth(label)
        text_height = self.DrawGetFontHeight()
        self.DrawText(
            label,
            max(5, (width - text_width) // 2),
            max(0, (height - text_height) // 2 - 1 + (1 if self.pressed else 0)),
        )

    def Message(self, message: c4d.BaseContainer, result: c4d.BaseContainer) -> bool:
        if message.GetId() == getattr(c4d, "BFM_GETCURSORINFO", -1) and self.enabled:
            result.SetId(c4d.BFM_GETCURSORINFO)
            result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_POINT_HAND)
            result.SetString(c4d.RESULT_BUBBLEHELP, self.label)
            return True
        return super().Message(message, result)

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        dialog = _area_dialog(self)
        if dialog is not None:
            recover = getattr(dialog, "_recover_interaction_state", None)
            if callable(recover):
                recover()
        if (
            not self.enabled
            or message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE
            or message.GetInt32(c4d.BFM_INPUT_CHANNEL) != c4d.BFM_INPUT_MOUSELEFT
        ):
            return False
        x, y = _local_input_position(self, message)
        if not (0 <= x < self.GetWidth() and 0 <= y < self.GetHeight()):
            return False
        if dialog is None:
            return False
        self.pressed = True
        self.Redraw()
        self.SetTimer(90)
        dialog.Command(self.command_id, c4d.BaseContainer())
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        self.pressed = False
        self.SetTimer(0)
        self.Redraw()

    def set_label(self, label: str) -> None:
        self.label = str(label)
        self.Redraw()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.pressed = False
        self.Redraw()

    def set_variant(self, variant: str) -> None:
        self.variant = str(variant)
        self.Redraw()

    def set_progress(self, value: Optional[float]) -> None:
        self.progress_value = (
            None if value is None else max(0.0, min(1.0, float(value)))
        )
        self.Redraw()


class WorkflowModeArea(c4d.gui.GeUserArea):
    """Compact two-segment switch with consistent cross-version rendering."""

    def __init__(self):
        super().__init__()
        self.workflow = "image"
        self.enabled = True
        self.animation_available = True
        self.animation_reason = ""
        self.pressed = ""

    def GetMinSize(self) -> tuple[int, int]:
        return 164, 30

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = max(2, self.GetWidth()), max(2, self.GetHeight())
        self.OffScreenOn()
        self.DrawSetPen(COLORS["bg"])
        self.DrawRectangle(0, 0, width - 1, height - 1)
        _draw_soft_round_rect(
            self,
            1,
            1,
            width - 2,
            height - 2,
            6.0,
            COLORS["mode_track"],
            COLORS["bg"],
        )
        half = width // 2
        for workflow, left, right, label in (
            ("image", 2, max(3, half - 1), "渲染图像"),
            ("animation", half, width - 3, "渲染动画"),
        ):
            if workflow == "animation" and not self.animation_available:
                label = "渲染动画 ×"
            selected = workflow == self.workflow
            if selected:
                fill = COLORS["mode_selected"]
                if self.pressed == workflow:
                    fill = COLORS["mode_pressed"]
                if not self.enabled:
                    fill = COLORS["mode_disabled"]
                _draw_soft_round_rect(
                    self,
                    left,
                    3,
                    right,
                    height - 4,
                    5.0,
                    fill,
                    COLORS["mode_track"],
                )
            available = workflow != "animation" or self.animation_available
            text_color = (
                COLORS["text"]
                if (selected or (self.enabled and available))
                else COLORS["muted"]
            )
            background = fill if selected else COLORS["mode_track"]
            self.DrawSetFont(c4d.FONT_DEFAULT)
            self.DrawSetTextCol(text_color, background)
            text_width = self.DrawGetTextWidth(label)
            self.DrawText(
                label,
                left + max(4, (right - left - text_width) // 2),
                max(0, (height - self.DrawGetFontHeight()) // 2 - 1),
            )

    def Message(self, message: c4d.BaseContainer, result: c4d.BaseContainer) -> bool:
        if message.GetId() == getattr(c4d, "BFM_GETCURSORINFO", -1) and self.enabled:
            mouse_x, _mouse_y = _local_input_position(self, message)
            animation_hovered = mouse_x >= self.GetWidth() // 2
            result.SetId(c4d.BFM_GETCURSORINFO)
            result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_POINT_HAND)
            result.SetString(
                c4d.RESULT_BUBBLEHELP,
                self.animation_reason
                if animation_hovered and not self.animation_available
                else "切换图像生成或 Seedance 动画生成",
            )
            return True
        return super().Message(message, result)

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        dialog = _area_dialog(self)
        if dialog is not None:
            recover = getattr(dialog, "_recover_interaction_state", None)
            if callable(recover):
                recover()
        if (
            not self.enabled
            or message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE
            or message.GetInt32(c4d.BFM_INPUT_CHANNEL) != c4d.BFM_INPUT_MOUSELEFT
        ):
            return False
        mouse_x, _mouse_y = _local_input_position(self, message)
        workflow = "image" if mouse_x < self.GetWidth() // 2 else "animation"
        self.pressed = workflow
        self.Redraw()
        self.SetTimer(90)
        if dialog is not None:
            dialog._switch_workflow(workflow)
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        self.pressed = ""
        self.SetTimer(0)
        self.Redraw()

    def set_workflow(self, workflow: str) -> None:
        self.workflow = "animation" if workflow == "animation" else "image"
        self.Redraw()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.pressed = ""
        self.Redraw()

    def set_animation_available(self, available: bool, reason: str = "") -> None:
        self.animation_available = bool(available)
        self.animation_reason = str(reason or "")
        self.Redraw()


class PreviewArea(c4d.gui.GeUserArea):
    def __init__(self, min_width: int = 300, min_height: int = 240):
        super().__init__()
        self.min_width = min_width
        self.min_height = min_height
        self.scene_path = ""
        self.result_path = ""
        self.scene_bitmap = None
        self.result_bitmap = None
        self.compare = False
        self.position = 0.5
        self.zoom = 1.0
        self.horizontal_position = 0.5
        self.scroll_position = 0.5
        self.scroll_visible = False
        self.scroll_visible_ratio = 1.0
        self.close_visible = False
        self.content_rect = (4, 4, max(1, min_width - 24), max(1, min_height - 8))

    def GetMinSize(self) -> tuple[int, int]:
        return self.min_width, self.min_height

    def set_images(self, scene_path: str, result_path: str) -> None:
        changed = False
        if scene_path != self.scene_path:
            self.scene_path = scene_path
            self.scene_bitmap = _load_preview_bitmap(scene_path)
            changed = True
        if result_path != self.result_path:
            self.result_path = result_path
            self.result_bitmap = _load_preview_bitmap(result_path)
            changed = True
        if changed:
            self.horizontal_position = 0.5
            self.scroll_position = 0.5
        self.Redraw()

    def set_compare(self, enabled: bool, position: Optional[float] = None) -> None:
        self.compare = bool(enabled)
        if position is not None:
            self.position = max(0.0, min(1.0, float(position)))
        self.Redraw()

    def set_zoom(self, value: float) -> None:
        self.zoom = max(0.5, min(3.0, round(float(value) * 4.0) / 4.0))
        if self.zoom <= 1.0:
            self.horizontal_position = 0.5
            self.scroll_position = 0.5
        self.Redraw()

    def zoom_by(self, delta: float) -> None:
        self.set_zoom(self.zoom + float(delta))

    def zoom_at(self, delta: float, mouse_x: float, mouse_y: float) -> None:
        """Zoom with the hovered image area as the crop focus."""

        x, y, width, height = self.content_rect
        if delta > 0.0:
            self.horizontal_position = max(
                0.0, min(1.0, (float(mouse_x) - x) / float(max(1, width)))
            )
            self.scroll_position = max(
                0.0, min(1.0, (float(mouse_y) - y) / float(max(1, height)))
            )
        self.zoom_by(delta)

    def reset_zoom(self) -> None:
        self.zoom = 1.0
        self.horizontal_position = 0.5
        self.scroll_position = 0.5
        self.Redraw()

    def _image_geometry(self, bitmap, rect) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], bool, float]:
        """Fit at 100%, then crop the synchronized image only when zoomed."""

        x, y, width, height = rect
        source_w, source_h = bitmap.GetBw(), bitmap.GetBh()
        if source_w <= 0 or source_h <= 0 or width <= 0 or height <= 0:
            safe_source = (0, 0, max(1, source_w), max(1, source_h))
            return (x, y, max(1, width), max(1, height)), safe_source, False, 1.0
        fit_scale = min(width / float(source_w), height / float(source_h))
        draw_w = max(1.0, source_w * fit_scale * self.zoom)
        draw_h = max(1.0, source_h * fit_scale * self.zoom)
        source_x, source_y = 0.0, 0.0
        visible_w, visible_h = float(source_w), float(source_h)
        if draw_w > width:
            visible_w = max(1.0, source_w * width / draw_w)
            source_x = (source_w - visible_w) * self.horizontal_position
            dest_x, dest_w = x, width
        else:
            dest_w = max(1, int(round(draw_w)))
            dest_x = x + (width - dest_w) // 2
        vertical_overflow = draw_h > height + 0.5
        if vertical_overflow:
            visible_h = max(1.0, source_h * height / draw_h)
            source_y = (source_h - visible_h) * self.scroll_position
            dest_y, dest_h = y, height
        else:
            dest_h = max(1, int(round(draw_h)))
            dest_y = y + (height - dest_h) // 2
        destination = (dest_x, dest_y, dest_w, dest_h)
        source = (
            int(round(source_x)),
            int(round(source_y)),
            max(1, int(round(visible_w))),
            max(1, int(round(visible_h))),
        )
        return destination, source, vertical_overflow, min(1.0, visible_h / source_h)

    def _draw_image(self, bitmap, rect, clip_right: Optional[int] = None):
        destination, source, scroll_visible, visible_ratio = self._image_geometry(bitmap, rect)
        dest_x, dest_y, dest_w, dest_h = destination
        source_x, source_y, source_w, source_h = source
        if clip_right is not None:
            clipped_width = max(0, min(dest_w, int(clip_right) - dest_x))
            if clipped_width <= 0:
                return destination, source, scroll_visible, visible_ratio
            source_w = max(1, int(round(source_w * clipped_width / float(max(1, dest_w)))))
            dest_w = clipped_width
        self.DrawBitmap(
            bitmap,
            dest_x,
            dest_y,
            dest_w,
            dest_h,
            source_x,
            source_y,
            source_w,
            source_h,
            c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
        )
        return destination, source, scroll_visible, visible_ratio

    def _draw_scrollbar(self, width: int, height: int) -> None:
        if not self.scroll_visible:
            return
        track_x1 = max(0, width - 16)
        track_x2 = max(track_x1, width - 4)
        track_y1 = 7
        track_y2 = max(track_y1 + 1, height - 7)
        self.DrawSetPen(COLORS["surface"])
        self.DrawRectangle(track_x1, track_y1, track_x2, track_y2)
        track_height = max(1, track_y2 - track_y1)
        handle_height = max(28, int(track_height * self.scroll_visible_ratio))
        travel = max(1, track_height - handle_height)
        handle_y = track_y1 + int(round(travel * self.scroll_position))
        self.DrawSetPen(COLORS["primary"])
        self.DrawRectangle(track_x1 + 2, handle_y, track_x2 - 2, min(track_y2, handle_y + handle_height))

    @staticmethod
    def _close_rect(width: int) -> tuple[int, int, int, int]:
        """Keep the compact close action inside the image, clear of its scrollbar."""

        right = max(28, width - 25)
        return max(4, right - 22), 8, right, 30

    def _draw_close_button(self, width: int) -> None:
        left, top, right, bottom = self._close_rect(width)
        self.DrawSetPen(c4d.Vector(0.07, 0.08, 0.10))
        self.DrawRectangle(left, top, right, bottom)
        self.DrawSetPen(COLORS["line"])
        self.DrawRectangle(left, top, right, top + 1)
        self.DrawRectangle(left, bottom - 1, right, bottom)
        self.DrawRectangle(left, top, left + 1, bottom)
        self.DrawRectangle(right - 1, top, right, bottom)
        self.DrawSetPen(COLORS["text"])
        self.DrawText("×", left + 7, top + 1)

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["bg"])
        self.DrawRectangle(0, 0, width, height)
        result_video = bool(self.result_path and _is_video_path(self.result_path))
        scene_video = bool(self.scene_path and _is_video_path(self.scene_path))
        has_video = result_video or scene_video
        display = self.result_bitmap or self.scene_bitmap
        if display is None:
            self.close_visible = has_video
            self.scroll_visible = False
            self.DrawSetPen(COLORS["preview_empty"])
            self.DrawRectangle(4, 4, max(4, width - 20), max(4, height - 4))
            self.DrawSetFont(c4d.FONT_BOLD)
            self.DrawSetTextCol(COLORS["text"], COLORS["preview_empty"])
            title = (
                "AI 动画已生成"
                if result_video
                else ("场景动画已加载" if scene_video else "等待生成效果图")
            )
            self.DrawText(title, max(30, (width - self.DrawGetTextWidth(title)) // 2), max(60, height // 2 - 24))
            self.DrawSetFont(c4d.FONT_DEFAULT)
            self.DrawSetTextCol(COLORS["muted"], COLORS["preview_empty"])
            hint = (
                "单击此预览区即可播放白模动画"
                if scene_video and not result_video
                else (
                    "单击此预览区即可播放 AI 动画"
                    if result_video
                    else "生成完成后可开启白模 / AI 滑动对比"
                )
            )
            self.DrawText(hint, max(30, (width - self.DrawGetTextWidth(hint)) // 2), max(84, height // 2 + 6))
            if has_video:
                self._draw_close_button(width)
            return

        self.close_visible = True
        rect = (4, 4, max(1, width - 24), max(1, height - 8))
        self.content_rect = rect
        display_geometry = self._draw_image(display, rect)
        self.scroll_visible = bool(display_geometry[2])
        self.scroll_visible_ratio = float(display_geometry[3])
        if self.compare and self.scene_bitmap is not None and self.result_bitmap is not None:
            x, y, draw_w, draw_h = rect
            line_x = x + max(1, min(draw_w, int(draw_w * self.position)))
            self._draw_image(self.scene_bitmap, rect, line_x)
            self.DrawSetPen(COLORS["cyan"])
            self.DrawRectangle(line_x - 1, y, line_x + 1, y + draw_h)
            self.DrawSetPen(COLORS["primary"])
            self.DrawRectangle(line_x - 11, y + draw_h // 2 - 18, line_x + 11, y + draw_h // 2 + 18)
            self.DrawSetPen(COLORS["text"])
            self.DrawText("↔", line_x - 7, y + draw_h // 2 - 8)
            self.DrawSetPen(COLORS["text"])
            self.DrawText("白模动画" if has_video else "原始场景", x + 12, y + 12)
            label = "AI 动画" if has_video else "AI 效果"
            self.DrawText(label, x + draw_w - self.DrawGetTextWidth(label) - 12, y + 12)
        if has_video:
            # The canvas shows the first decoded frame; playback stays in a
            # native C4D dialog so MP4 decoding and timing remain reliable.
            center_x, center_y = width // 2, height // 2
            self.DrawSetPen(c4d.Vector(0.06, 0.07, 0.09))
            self.DrawRectangle(center_x - 31, center_y - 18, center_x + 31, center_y + 18)
            self.DrawSetPen(COLORS["text"])
            self.DrawText("▶  播放", center_x - 23, center_y - 8)
        self._draw_scrollbar(width, height)
        self._draw_close_button(width)

    def Message(self, message: c4d.BaseContainer, result: c4d.BaseContainer):
        if message.GetId() == getattr(c4d, "BFM_GETCURSORINFO", -1) and self.close_visible:
            mouse_x, mouse_y = _local_input_position(self, message)
            left, top, right, bottom = self._close_rect(max(1, self.GetWidth()))
            if left <= mouse_x <= right and top <= mouse_y <= bottom:
                result.SetId(c4d.BFM_GETCURSORINFO)
                result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_POINT_HAND)
                result.SetString(c4d.RESULT_BUBBLEHELP, "关闭当前效果图预览")
                return True
            if _is_video_path(self.result_path) or _is_video_path(self.scene_path):
                result.SetId(c4d.BFM_GETCURSORINFO)
                result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_POINT_HAND)
                result.SetString(
                    c4d.RESULT_BUBBLEHELP,
                    "播放白模与 AI 对比动画" if self.compare else "播放当前动画",
                )
                return True
        return super().Message(message, result)

    def _set_compare_from_x(self, mouse_x: float) -> None:
        x, _y, draw_w, _draw_h = self.content_rect
        self.position = max(0.0, min(1.0, (mouse_x - x) / float(max(1, draw_w))))
        self.Redraw()
        dialog = _area_dialog(self)
        if dialog and hasattr(dialog, "preview_position_changed"):
            dialog.preview_position_changed(self.position)

    def _set_scroll_from_y(self, mouse_y: float) -> None:
        height = max(1, self.GetHeight())
        track_y1, track_y2 = 7.0, float(max(8, height - 7))
        track_height = max(1.0, track_y2 - track_y1)
        handle_height = max(28.0, track_height * self.scroll_visible_ratio)
        travel = max(1.0, track_height - handle_height)
        self.scroll_position = max(0.0, min(1.0, (mouse_y - track_y1 - handle_height * 0.5) / travel))
        self.Redraw()

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        if message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE:
            return False
        channel = message.GetInt32(c4d.BFM_INPUT_CHANNEL)
        has_video = bool(
            _is_video_path(self.result_path) or _is_video_path(self.scene_path)
        )
        wheel_channels = {
            getattr(c4d, "BFM_INPUT_MOUSEWHEEL", -997),
            getattr(c4d, "BFM_INPUT_WHEELSCROLL", -998),
        }
        if channel in wheel_channels:
            if has_video:
                return False
            delta = message.GetInt32(c4d.BFM_INPUT_VALUE)
            if delta == 0:
                return False
            mouse_x, mouse_y = _local_input_position(self, message)
            self.zoom_at(0.25 if delta > 0 else -0.25, mouse_x, mouse_y)
            return True
        if channel == c4d.BFM_INPUT_MOUSELEFT:
            local_x, local_y = _local_input_position(self, message)
            start_x = float(local_x)
            start_y = float(local_y)
            left, top, right, bottom = self._close_rect(max(1, self.GetWidth()))
            if self.close_visible and left <= start_x <= right and top <= start_y <= bottom:
                dialog = _area_dialog(self)
                if dialog and hasattr(dialog, "preview_close_requested"):
                    dialog.preview_close_requested()
                return True
            if has_video:
                dialog = _area_dialog(self)
                if dialog and hasattr(dialog, "preview_open_requested"):
                    dialog.preview_open_requested()
                return True
            scroll_drag = bool(self.scroll_visible and start_x >= self.GetWidth() - 22)
            if not scroll_drag and not self.compare:
                return False
            if scroll_drag:
                self._set_scroll_from_y(start_y)
            else:
                self._set_compare_from_x(start_x)
            current_x, current_y = start_x, start_y
            drag_flags = getattr(c4d, "MOUSEDRAGFLAGS_DONTHIDEMOUSE", 0) | getattr(
                c4d, "MOUSEDRAGFLAGS_EVERYPACKET", 0
            )
            self.MouseDragStart(c4d.BFM_INPUT_MOUSELEFT, start_x, start_y, drag_flags)
            while True:
                result, delta_x, delta_y, _channels = self.MouseDrag()
                if result != c4d.MOUSEDRAGRESULT_CONTINUE:
                    break
                # MouseDrag() reports movement deltas in Cinema 4D's drag
                # coordinate direction, so subtract them to follow the cursor.
                current_x -= float(delta_x)
                current_y -= float(delta_y)
                if scroll_drag:
                    self._set_scroll_from_y(current_y)
                else:
                    self._set_compare_from_x(current_x)
            self.MouseDragEnd()
            return True
        return False


class SceneCaptureArea(c4d.gui.GeUserArea):
    """Small live tile for the captured C4D viewport beside the prompt."""

    def __init__(self):
        super().__init__()
        self.path = ""
        self.bitmap = None

    def GetMinSize(self) -> tuple[int, int]:
        return 84, 52

    def set_scene(self, path: str) -> None:
        if path != self.path:
            self.path = path
            self.bitmap = _load_bitmap(path)
        self.Redraw()

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["bg"])
        self.DrawRectangle(0, 0, width, height)
        if self.bitmap is not None:
            rect = _fit_rect(self.bitmap.GetBw(), self.bitmap.GetBh(), width - 6, height - 6)
            self.DrawBitmap(
                self.bitmap,
                3 + rect[0],
                3 + rect[1],
                rect[2],
                rect[3],
                0,
                0,
                self.bitmap.GetBw(),
                self.bitmap.GetBh(),
                c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
            )
            return
        self.DrawSetPen(COLORS["surface_2"])
        self.DrawRectangle(3, 3, max(4, width - 3), max(4, height - 3))
        center_x, center_y = width // 2, height // 2 - 4
        self.DrawSetPen(COLORS["muted"])
        self.DrawRectangle(center_x - 9, center_y - 9, center_x + 9, center_y + 9)
        self.DrawSetPen(COLORS["text"])
        hint = "捕获视图"
        self.DrawText(hint, max(4, (width - self.DrawGetTextWidth(hint)) // 2), height - 17)


class ReferenceArea(c4d.gui.GeUserArea):
    """Dedicated C4D scene slot followed by token-controlled references."""

    def __init__(self, allow_delete: bool = True, show_scene: bool = True):
        super().__init__()
        self.allow_delete = bool(allow_delete)
        self.show_scene = bool(show_scene)
        self.scene_path = ""
        self.workflow = "image"
        self.paths: list[str] = []
        self.selected = -1
        self.bitmaps: dict[str, Optional[c4d.bitmaps.BaseBitmap]] = {}

    def GetMinSize(self) -> tuple[int, int]:
        return 292, 58 if self.show_scene else 48

    def _geometry(self, width: int) -> tuple[int, list[int], Optional[int]]:
        normal_gap = 4
        scene_gap = 12 if self.show_scene else normal_gap
        normal_slots = 5 if self.show_scene else 6
        total_slots = normal_slots + (1 if self.show_scene else 0)
        gaps = normal_gap * max(0, normal_slots - 1)
        if self.show_scene:
            gaps += scene_gap
        tile = max(36, min(64, (max(240, width) - 8 - gaps) // total_slots))
        left = 4
        scene_left = left if self.show_scene else None
        if self.show_scene:
            left += tile + scene_gap
        positions = []
        for _index in range(normal_slots):
            positions.append(left)
            left += tile + normal_gap
        return tile, positions, scene_left

    def set_references(
        self,
        scene_path: str,
        paths: list[str],
        selected: int,
        workflow: str = "image",
    ) -> None:
        self.scene_path = str(scene_path or "") if self.show_scene else ""
        self.workflow = str(workflow or "image")
        self.paths = list(paths)
        self.selected = selected
        all_paths = ([self.scene_path] if self.scene_path else []) + self.paths
        self.bitmaps = {
            path: self.bitmaps.get(path) or _load_bitmap(path) for path in all_paths
        }
        self.Redraw()

    def _draw_tile(
        self,
        left: int,
        tile: int,
        height: int,
        path: str,
        label: str,
        border: c4d.Vector,
        can_delete: bool,
    ) -> None:
        right = min(self.GetWidth() - 2, left + tile)
        bottom = max(44, height - 5)
        self.DrawSetPen(border)
        self.DrawRectangle(left, 4, right, bottom)
        self.DrawSetPen(COLORS["surface_2"] if path else COLORS["surface"])
        self.DrawRectangle(left + 2, 6, max(left + 2, right - 2), bottom - 2)
        bitmap = self.bitmaps.get(path)
        if bitmap is not None:
            image_left = left + 2
            image_top = 6
            image_width = max(1, right - left - 4)
            image_height = max(1, bottom - image_top - 2)
            source = _cover_source_rect(
                bitmap.GetBw(), bitmap.GetBh(), image_width, image_height
            )
            self.DrawBitmap(
                bitmap,
                image_left,
                image_top,
                image_width,
                image_height,
                source[0],
                source[1],
                source[2],
                source[3],
                c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
            )
            if can_delete:
                self.DrawSetPen(COLORS["danger"])
                self.DrawRectangle(max(left + 3, right - 15), 6, right - 3, 18)
                self.DrawSetPen(COLORS["text"])
                self.DrawText("×", max(left + 4, right - 13), 4)
        elif path and _is_video_path(path):
            self.DrawSetFont(c4d.FONT_BOLD)
            self.DrawSetPen(COLORS["primary"])
            play = "▶"
            self.DrawText(
                play,
                left + max(5, (tile - self.DrawGetTextWidth(play)) // 2),
                12,
            )
            self.DrawSetFont(c4d.FONT_DEFAULT)
            if can_delete:
                self.DrawSetPen(COLORS["danger"])
                self.DrawRectangle(max(left + 3, right - 15), 6, right - 3, 18)
                self.DrawSetPen(COLORS["text"])
                self.DrawText("×", max(left + 4, right - 13), 4)
        elif not path:
            self.DrawSetFont(c4d.FONT_BOLD)
            self.DrawSetTextCol(COLORS["text"], COLORS["surface"])
            plus = "+"
            self.DrawText(
                plus,
                left + max(3, (tile - self.DrawGetTextWidth(plus)) // 2),
                10,
            )
            self.DrawSetFont(c4d.FONT_DEFAULT)
        label_text = label
        if path:
            # Overlay a compact caption on top of the full-bleed thumbnail;
            # do not reserve a separate lower strip that visually splits it.
            caption_right = min(right - 2, left + 8 + self.DrawGetTextWidth(label_text))
            self.DrawSetPen(COLORS["preview_empty"])
            self.DrawRectangle(left + 2, bottom - 17, caption_right, bottom - 2)
            self.DrawSetTextCol(COLORS["text"], COLORS["preview_empty"])
        else:
            self.DrawSetTextCol(COLORS["muted"], COLORS["surface"])
        self.DrawText(label_text, left + 4, bottom - 14)

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["bg"])
        self.DrawRectangle(0, 0, width, height)
        tile, positions, scene_left = self._geometry(width)
        if scene_left is not None:
            scene_label = "动画" if self.workflow == "animation" else "场景"
            self._draw_tile(
                scene_left,
                tile,
                height,
                self.scene_path,
                scene_label,
                COLORS["scene"],
                bool(self.allow_delete and self.scene_path),
            )
            separator_x = scene_left + tile + 6
            self.DrawSetPen(COLORS["line"])
            self.DrawRectangle(separator_x, 8, separator_x + 1, max(9, height - 9))
        for index, left in enumerate(positions):
            if left >= width:
                break
            path = self.paths[index] if index < len(self.paths) else ""
            border = COLORS["cyan"] if index == self.selected else COLORS["line"]
            self._draw_tile(
                left,
                tile,
                height,
                path,
                "图{}".format(index + 1),
                border,
                bool(self.allow_delete and path),
            )
        if len(self.paths) > len(positions):
            self.DrawSetPen(COLORS["muted"])
            self.DrawText("共 {} 张".format(len(self.paths)), max(6, width - 58), height - 16)
        self.DrawSetPen(COLORS["line"])
        self.DrawRectangle(0, 0, max(0, width - 1), 1)
        self.DrawRectangle(0, max(0, height - 2), max(0, width - 1), max(0, height - 1))
        self.DrawRectangle(0, 0, 1, max(0, height - 1))
        self.DrawRectangle(max(0, width - 2), 0, max(0, width - 1), max(0, height - 1))

    def Message(self, message: c4d.BaseContainer, result: c4d.BaseContainer):
        if message.GetId() == getattr(c4d, "BFM_GETCURSORINFO", -1):
            result.SetId(c4d.BFM_GETCURSORINFO)
            result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_POINT_HAND)
            result.SetString(
                c4d.RESULT_BUBBLEHELP,
                "主场景/动画自动生效；左键查看，右键复制原图；普通参考图需输入 @",
            )
            return True
        return super().Message(message, result)

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        if message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE:
            return False
        channel = message.GetInt32(c4d.BFM_INPUT_CHANNEL)
        tile, positions, scene_left = self._geometry(max(1, self.GetWidth()))
        mouse_x, mouse_y = _local_input_position(self, message)
        dialog = _area_dialog(self)
        if channel == getattr(c4d, "BFM_INPUT_MOUSERIGHT", -997):
            path = ""
            if scene_left is not None and scene_left <= mouse_x <= scene_left + tile:
                path = self.scene_path
            else:
                index = next(
                    (
                        current
                        for current, left in enumerate(positions)
                        if left <= mouse_x <= left + tile
                    ),
                    -1,
                )
                if 0 <= index < len(self.paths):
                    path = self.paths[index]
            return _show_image_copy_menu(dialog, path)
        if channel == c4d.BFM_INPUT_MOUSELEFT:
            if scene_left is not None and scene_left <= mouse_x <= scene_left + tile:
                local_x = mouse_x - scene_left
                if (
                    self.allow_delete
                    and self.scene_path
                    and local_x >= tile - 16
                    and mouse_y <= 20
                    and dialog
                    and hasattr(dialog, "scene_delete_requested")
                ):
                    dialog.scene_delete_requested()
                    return True
                if self.scene_path and dialog and hasattr(dialog, "scene_open_requested"):
                    dialog.scene_open_requested()
                    return True
                if dialog and hasattr(dialog, "scene_slot_requested"):
                    dialog.scene_slot_requested()
                    return True
            index = next(
                (
                    current
                    for current, left in enumerate(positions)
                    if left <= mouse_x <= left + tile
                ),
                -1,
            )
            if 0 <= index < len(self.paths):
                local_x = mouse_x - positions[index]
                if self.allow_delete and local_x >= tile - 16 and mouse_y <= 19:
                    if dialog and hasattr(dialog, "reference_delete_requested"):
                        dialog.reference_delete_requested(index)
                        return True
                self.selected = index
                self.Redraw()
                if self.allow_delete and dialog and hasattr(dialog, "reference_open_requested"):
                    dialog.reference_open_requested(index)
                elif dialog and hasattr(dialog, "reference_selected"):
                    dialog.reference_selected(index)
                return True
            if 0 <= index < 8:
                if dialog and hasattr(dialog, "reference_slot_requested"):
                    dialog.reference_slot_requested(index)
                    return True
        return False


class HistoryArea(c4d.gui.GeUserArea):
    ROW_HEIGHT = 74

    def __init__(self, min_width: int = 172, min_height: int = ROW_HEIGHT * HISTORY_VISIBLE_ROWS):
        super().__init__()
        self.min_width = min_width
        self.min_height = min_height
        self.items: list[dict] = []
        self.selected_id = ""
        self.offset = 0
        self.bitmaps: dict[str, Optional[c4d.bitmaps.BaseBitmap]] = {}
        # Delay a single-click action just long enough to distinguish it from
        # a double click. This prevents the first half of a double click from
        # rebuilding the whole work area and changing the dialog scroll offset.
        self.pending_click_id = ""
        self.pending_click_time = 0.0

    def GetMinSize(self) -> tuple[int, int]:
        return self.min_width, self.min_height

    def set_history(self, items: list[dict], selected_id: str) -> None:
        self.items = list(items)
        self.selected_id = selected_id
        for item in self.items[:30]:
            path = str(item.get("result") or "")
            if path and path not in self.bitmaps:
                self.bitmaps[path] = _load_bitmap(path)
        visible = max(1, self.min_height // self.ROW_HEIGHT)
        self.offset = min(self.offset, max(0, len(self.items) - visible))
        self.Redraw()

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["bg"])
        self.DrawRectangle(0, 0, width, height)
        if not self.items:
            self.DrawSetTextCol(COLORS["muted"], COLORS["bg"])
            self.DrawText("生成记录会出现在这里", 14, 24)
            self.DrawText("双击整条记录可打开大图或动画", 14, 47)
            return
        visible = max(1, height // self.ROW_HEIGHT)
        for row, item in enumerate(self.items[self.offset : self.offset + visible]):
            index = self.offset + row
            top = row * self.ROW_HEIGHT
            selected = item.get("id") == self.selected_id
            row_fill = COLORS["surface_2"] if selected else COLORS["surface"]
            self.DrawSetPen(row_fill)
            self.DrawRectangle(2, top + 2, width - 2, top + self.ROW_HEIGHT - 3)
            if selected:
                self.DrawSetPen(COLORS["line"])
                self.DrawRectangle(2, top + 2, 5, top + self.ROW_HEIGHT - 3)
            bitmap = self.bitmaps.get(str(item.get("result") or ""))
            if bitmap is not None:
                rect = _fit_rect(bitmap.GetBw(), bitmap.GetBh(), 56, 56)
                self.DrawBitmap(
                    bitmap,
                    10 + rect[0],
                    top + 9 + rect[1],
                    rect[2],
                    rect[3],
                    0,
                    0,
                    bitmap.GetBw(),
                    bitmap.GetBh(),
                    c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
                )
                self.DrawSetTextCol(COLORS["primary"], row_fill)
                self.DrawText("↗", 55, top + 9)
            elif _is_video_path(str(item.get("result") or "")):
                self.DrawSetPen(COLORS["surface_2"])
                self.DrawRectangle(10, top + 9, 66, top + 65)
                self.DrawSetTextCol(COLORS["primary"], row_fill)
                self.DrawSetFont(c4d.FONT_BOLD)
                self.DrawText("▶", 31, top + 28)
                self.DrawSetFont(c4d.FONT_DEFAULT)
            prompt = str(item.get("prompt") or "未命名生成").replace("\n", " ")
            prompt = prompt[:18] + ("…" if len(prompt) > 18 else "")
            self.DrawSetTextCol(COLORS["text"], row_fill)
            self.DrawText(prompt, 76, top + 13)
            created = time.strftime("%m-%d %H:%M", time.localtime(float(item.get("created_at") or 0)))
            media_label = (
                "{}秒".format(item.get("duration", 5))
                if item.get("workflow") == "animation"
                else item.get("quality", "1K")
            )
            meta = "{} · {} · {}".format(
                created, media_label, item.get("aspect", "1:1")
            )
            self.DrawSetTextCol(COLORS["muted"], row_fill)
            self.DrawText(meta, 76, top + 39)

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        if message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE:
            return False
        channel = message.GetInt32(c4d.BFM_INPUT_CHANNEL)
        if channel == c4d.BFM_INPUT_MOUSELEFT:
            _mouse_x, mouse_y = _local_input_position(self, message)
            row = mouse_y // self.ROW_HEIGHT
            index = self.offset + row
            if 0 <= index < len(self.items):
                dialog = _area_dialog(self)
                item_id = str(self.items[index].get("id") or "")
                now = time.monotonic()
                is_double = bool(
                    item_id == self.pending_click_id
                    and 0.0 < now - self.pending_click_time <= 0.48
                )
                if is_double and dialog and hasattr(dialog, "history_open_requested"):
                    self.pending_click_id = ""
                    self.pending_click_time = 0.0
                    self.SetTimer(0)
                    dialog.history_open_requested(item_id)
                else:
                    self.pending_click_id = item_id
                    self.pending_click_time = now
                    self.SetTimer(60)
                return True
        if channel == getattr(c4d, "BFM_INPUT_MOUSERIGHT", -997):
            _mouse_x, mouse_y = _local_input_position(self, message)
            row = mouse_y // self.ROW_HEIGHT
            index = self.offset + row
            if 0 <= index < len(self.items):
                dialog = _area_dialog(self)
                if dialog and hasattr(dialog, "history_reveal_requested"):
                    menu = c4d.BaseContainer()
                    menu_id = getattr(c4d, "FIRST_POPUP_ID", 1000)
                    menu.InsData(menu_id, "打开文件位置")
                    mouse_position = getattr(c4d, "MOUSEPOS", -1)
                    try:
                        command = c4d.gui.ShowPopupDialog(
                            cd=dialog,
                            bc=menu,
                            x=mouse_position,
                            y=mouse_position,
                        )
                    except TypeError:
                        command = c4d.gui.ShowPopupDialog(
                            dialog, menu, mouse_position, mouse_position
                        )
                    if command == menu_id:
                        dialog.history_reveal_requested(
                            str(self.items[index].get("id") or "")
                        )
                    return True
        wheel_channels = {
            getattr(c4d, "BFM_INPUT_MOUSEWHEEL", -999),
            getattr(c4d, "BFM_INPUT_WHEELSCROLL", -998),
        }
        if channel in wheel_channels:
            delta = message.GetInt32(c4d.BFM_INPUT_VALUE)
            visible = max(1, self.GetHeight() // self.ROW_HEIGHT)
            maximum = max(0, len(self.items) - visible)
            next_offset = max(
                0,
                min(maximum, self.offset - (1 if delta > 0 else -1)),
            )
            if next_offset == self.offset:
                # At either end of the history list, let the parent
                # ScrollGroup consume the wheel event so the entire plugin
                # panel continues scrolling while the pointer is over this
                # area.
                return False
            self.offset = next_offset
            self.Redraw()
            return True
        return False

    def Timer(self, message: c4d.BaseContainer) -> None:
        if not self.pending_click_id:
            self.SetTimer(0)
            return
        if time.monotonic() - self.pending_click_time < 0.48:
            return
        item_id = self.pending_click_id
        self.pending_click_id = ""
        self.pending_click_time = 0.0
        self.SetTimer(0)
        # Clicking the already active row should be a layout-free no-op.
        if item_id == self.selected_id:
            return
        self.selected_id = item_id
        self.Redraw()
        dialog = _area_dialog(self)
        if dialog and hasattr(dialog, "history_selected"):
            dialog.history_selected(item_id)


class GenerationProgressArea(c4d.gui.GeUserArea):
    """Thin status strip above the native generate button."""

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.status = "就绪"
        self.busy = False
        self.rate = 0.0
        self._last_value = 0.0
        self._last_update = time.monotonic()

    def GetMinSize(self) -> tuple[int, int]:
        return 270, 12

    def set_state(self, value: float, status: str, busy: bool) -> None:
        next_value = max(0.0, min(1.0, float(value)))
        next_busy = bool(busy)
        now = time.monotonic()
        if next_busy and next_value > self._last_value:
            elapsed = max(0.001, now - self._last_update)
            instant_rate = (next_value - self._last_value) * 100.0 / elapsed
            self.rate = instant_rate if self.rate <= 0.0 else self.rate * 0.68 + instant_rate * 0.32
            self._last_update = now
        elif not next_busy or next_value < self._last_value:
            self.rate = 0.0
            self._last_update = now
        self._last_value = next_value
        self.value = next_value
        self.status = str(status or "就绪")
        self.busy = next_busy
        self.Redraw()

    def _stage(self) -> str:
        text = self.status
        if "失败" in text or "错误" in text:
            return "生成失败"
        if self.value >= 1.0 or "完成" in text or "已生成" in text:
            return "生成完成"
        if "准备" in text or "编码" in text or "上传" in text:
            return "1/3 准备素材"
        if "下载" in text or "保存" in text:
            return "3/3 保存结果"
        if self.busy:
            return "2/3 AI 生成"
        return ""

    def _label(self) -> str:
        percent = int(round(self.value * 100.0))
        if self.busy:
            speed = " · 速度 {:.1f}%/s".format(self.rate) if self.rate >= 0.1 else ""
            return "{} · {}%{}".format(self._stage(), percent, speed)
        if "失败" in self.status or "错误" in self.status:
            return "生成失败"
        if self.value >= 1.0 or "已生成" in self.status or "完成" in self.status:
            return "生成完成 · 100%"
        return ""

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["surface_2"])
        self.DrawRectangle(0, 0, width, height)
        self.DrawSetPen(COLORS["surface"])
        self.DrawRectangle(1, 1, max(1, width - 2), max(1, height - 2))

        if self.busy:
            fill_right = max(3, min(width - 2, int((width - 4) * self.value) + 2))
            if "失败" in self.status or "错误" in self.status:
                fill_color = c4d.Vector(0.72, 0.20, 0.22)
            else:
                fill_color = COLORS["primary"]
            self.DrawSetPen(fill_color)
            self.DrawRectangle(2, 2, fill_right, max(2, height - 3))
        elif "失败" in self.status or "错误" in self.status:
            self.DrawSetPen(COLORS["danger"])
            self.DrawRectangle(2, 2, max(2, width - 3), max(2, height - 3))
        elif self.value >= 1.0 or "已生成" in self.status:
            self.DrawSetPen(COLORS["mint"])
            self.DrawRectangle(2, 2, max(2, width - 3), max(2, height - 3))
        label = self._label()
        if label:
            self.DrawSetPen(COLORS["text"])
            self.DrawText(label, max(6, (width - self.DrawGetTextWidth(label)) // 2), 0)


class PreviewResizeArea(c4d.gui.GeUserArea):
    """Compact drag handle below the result image for resizing its height."""

    def __init__(self, height: int = 230):
        super().__init__()
        self.preview_height = height
        self.dragging = False
        self.drag_start_y = 0
        self.drag_start_height = height

    def GetMinSize(self) -> tuple[int, int]:
        return 270, 14

    def set_height(self, height: int) -> None:
        self.preview_height = int(height)
        self.Redraw()

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["surface_2"])
        self.DrawRectangle(0, 0, width, height)
        self.DrawSetPen(COLORS["primary"])
        center = max(24, width // 2)
        for offset in (-10, 0, 10):
            self.DrawRectangle(center + offset - 4, 4, center + offset + 4, 5)
        self.DrawSetPen(COLORS["cyan"])
        self.DrawText("↕", center - 5, 0)

    def Message(self, message: c4d.BaseContainer, result: c4d.BaseContainer):
        if message.GetId() == getattr(c4d, "BFM_GETCURSORINFO", -1):
            result.SetId(c4d.BFM_GETCURSORINFO)
            result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_ARROW_V)
            result.SetString(c4d.RESULT_BUBBLEHELP, "上下拖动调整效果图高度")
            return True
        return super().Message(message, result)

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        if (
            message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE
            or message.GetInt32(c4d.BFM_INPUT_CHANNEL) != c4d.BFM_INPUT_MOUSELEFT
        ):
            return False
        self.dragging = True
        self.drag_start_y = int(message.GetInt32(c4d.BFM_INPUT_Y))
        self.drag_start_height = int(self.preview_height)
        self.SetTimer(16)
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        if not self.dragging:
            return
        state = c4d.BaseContainer()
        active = c4d.gui.GetInputState(c4d.BFM_INPUT_MOUSE, c4d.BFM_INPUT_MOUSELEFT, state)
        if not active or state.GetInt32(c4d.BFM_INPUT_VALUE) == 0:
            self.dragging = False
            self.SetTimer(0)
            return
        current_y = int(state.GetInt32(c4d.BFM_INPUT_Y))
        target_height = max(140, min(440, self.drag_start_height + current_y - self.drag_start_y))
        if target_height == self.preview_height:
            return
        self.preview_height = target_height
        self.Redraw()
        dialog = _area_dialog(self)
        if dialog and hasattr(dialog, "request_preview_height"):
            dialog.request_preview_height(target_height)


class ReferenceMentionDialog(c4d.gui.GeDialog):
    """Compact picker shown when the user types @ in the prompt editor."""

    FIRST_ITEM = 7200
    CANCEL = 7299

    def __init__(self, references: list[str]):
        super().__init__()
        self.references = list(references)
        self.selected_index = -1
        self.area = ReferenceArea(allow_delete=False, show_scene=False)
        self.area._zhire_dialog = self

    def CreateLayout(self) -> bool:
        self.SetTitle("@ 选择参考图")
        self.GroupBegin(7199, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderSpace(8, 8, 8, 8)
        self.GroupSpace(4, 4)
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "点击缩略图，将对应参考图插入提示词")
        self.AddUserArea(7198, c4d.BFH_SCALEFIT, 320, 52)
        self.AttachUserArea(self.area, 7198)
        names = "  ·  ".join(
            "图{} {}".format(index + 1, Path(path).name[:14])
            for index, path in enumerate(self.references[:4])
        )
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, names)
        self.AddButton(self.CANCEL, c4d.BFH_SCALEFIT, 0, 22, "取消")
        self.GroupEnd()
        return True

    def InitValues(self) -> bool:
        self.area.set_references("", self.references, -1)
        return True

    def reference_selected(self, index: int) -> None:
        if 0 <= index < len(self.references):
            self.selected_index = index
            self.Close()

    def Command(self, item_id: int, message: c4d.BaseContainer) -> bool:
        if self.FIRST_ITEM <= item_id < self.FIRST_ITEM + len(self.references):
            self.selected_index = item_id - self.FIRST_ITEM
            self.Close()
        elif item_id == self.CANCEL:
            self.Close()
        return True


class LargePreviewDialog(c4d.gui.GeDialog):
    AREA = 9101

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.area = PreviewArea()

    def CreateLayout(self) -> bool:
        self.SetTitle("{} · 大图预览".format(PRODUCT_WINDOW_TITLE))
        self.AddUserArea(self.AREA, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 960, 640)
        self.AttachUserArea(self.area, self.AREA)
        return True

    def InitValues(self) -> bool:
        self.area.set_images("", self.path)
        return True


class AnimationSequenceArea(c4d.gui.GeUserArea):
    """Draw one frame from a render-loaded C4D animation sequence."""

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = list(paths)
        self.bitmaps = [_load_bitmap(path) for path in self.paths]
        self.index = 0

    def GetMinSize(self) -> tuple[int, int]:
        return 640, 420

    def set_index(self, index: int) -> None:
        if self.bitmaps:
            self.index = int(index) % len(self.bitmaps)
        self.Redraw()

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["preview_empty"])
        self.DrawRectangle(0, 0, max(0, width - 1), max(0, height - 1))
        bitmap = self.bitmaps[self.index] if self.bitmaps else None
        if bitmap is not None:
            rect = _fit_rect(bitmap.GetBw(), bitmap.GetBh(), width - 16, height - 34)
            self.DrawBitmap(
                bitmap,
                8 + rect[0],
                8 + rect[1],
                rect[2],
                rect[3],
                0,
                0,
                bitmap.GetBw(),
                bitmap.GetBh(),
                c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
            )
        self.DrawSetFont(c4d.FONT_DEFAULT)
        self.DrawSetTextCol(COLORS["text"], COLORS["preview_empty"])
        status = "白模动画预览 · {}/{}".format(
            min(self.index + 1, len(self.paths)), len(self.paths)
        )
        self.DrawText(status, 10, max(8, height - 24))


class AnimationMovieArea(c4d.gui.GeUserArea):
    """Draw frames decoded by C4D's native MovieLoader."""

    def __init__(self):
        super().__init__()
        self.bitmap: Optional[c4d.bitmaps.BaseBitmap] = None
        self.index = 0
        self.frame_count = 0
        self.fps = 0.0
        self.error = ""

    def GetMinSize(self) -> tuple[int, int]:
        return 640, 420

    def set_frame(
        self,
        bitmap: Optional[c4d.bitmaps.BaseBitmap],
        index: int,
        frame_count: int,
        fps: float,
    ) -> None:
        self.bitmap = bitmap
        self.index = max(0, int(index))
        self.frame_count = max(0, int(frame_count))
        self.fps = max(0.0, float(fps))
        self.error = ""
        self.Redraw()

    def set_error(self, message: str) -> None:
        self.bitmap = None
        self.error = str(message or "无法读取动画")
        self.Redraw()

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        self.OffScreenOn()
        self.DrawSetPen(COLORS["preview_empty"])
        self.DrawRectangle(0, 0, max(0, width - 1), max(0, height - 1))
        if self.bitmap is not None:
            rect = _fit_rect(
                self.bitmap.GetBw(), self.bitmap.GetBh(), width - 16, height - 38
            )
            self.DrawBitmap(
                self.bitmap,
                8 + rect[0],
                8 + rect[1],
                rect[2],
                rect[3],
                0,
                0,
                self.bitmap.GetBw(),
                self.bitmap.GetBh(),
                c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
            )
        self.DrawSetFont(c4d.FONT_DEFAULT)
        self.DrawSetTextCol(COLORS["text"], COLORS["preview_empty"])
        if self.error:
            label = self.error
        else:
            label = "C4D 动画 · {}/{} · {:.0f} FPS".format(
                min(self.index + 1, self.frame_count), self.frame_count, self.fps
            )
        self.DrawText(label, 10, max(8, height - 25))


class AnimationMovieDialog(c4d.gui.GeDialog):
    """Play MP4/MOV media inside C4D using its native movie decoder."""

    AREA = 9251
    PREVIOUS = 9252
    PLAY = 9253
    NEXT = 9254
    CLOSE = 9255

    def __init__(self, path: str):
        super().__init__()
        self.path = str(path)
        self.area = AnimationMovieArea()
        self.loader = c4d.bitmaps.MovieLoader()
        self.frame_count = 0
        self.fps = 0.0
        self.index = 0
        self.playing = True
        self.ready = False

    def CreateLayout(self) -> bool:
        self.SetTitle("{} · C4D 动画查看器".format(PRODUCT_WINDOW_TITLE))
        self.GroupBegin(9250, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.AddUserArea(
            self.AREA, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 640, 420
        )
        self.AttachUserArea(self.area, self.AREA)
        self.GroupBegin(9256, c4d.BFH_RIGHT, 4, 1)
        self.AddButton(self.PREVIOUS, c4d.BFH_RIGHT, 64, 28, "上一帧")
        self.AddButton(self.PLAY, c4d.BFH_RIGHT, 80, 28, "暂停")
        self.AddButton(self.NEXT, c4d.BFH_RIGHT, 64, 28, "下一帧")
        self.AddButton(self.CLOSE, c4d.BFH_RIGHT, 64, 28, "关闭")
        self.GroupEnd()
        self.GroupEnd()
        return True

    def _read_frame(self, index: int) -> bool:
        if not self.ready or self.frame_count <= 0:
            return False
        frame_index = int(index) % self.frame_count
        value = self.loader.Read(frame_index)
        if not isinstance(value, tuple) or len(value) < 2:
            return False
        result, bitmap = value[0], value[1]
        if result != c4d.IMAGERESULT_OK or bitmap is None:
            return False
        self.index = frame_index
        self.area.set_frame(bitmap, self.index, self.frame_count, self.fps)
        return True

    def InitValues(self) -> bool:
        result = self.loader.Open(self.path)
        if result not in (None, c4d.IMAGERESULT_OK):
            self.area.set_error("C4D 无法解码这个动画文件")
            self.Enable(self.PREVIOUS, False)
            self.Enable(self.PLAY, False)
            self.Enable(self.NEXT, False)
            return True
        info = self.loader.GetInfo()
        if not isinstance(info, tuple) or len(info) < 2:
            self.area.set_error("C4D 未能读取动画帧信息")
            return True
        self.frame_count = max(0, int(info[0]))
        self.fps = max(1.0, float(info[1]))
        self.ready = self.frame_count > 0
        if not self.ready or not self._read_frame(0):
            self.area.set_error("C4D 未能读取动画画面")
            return True
        self.SetTimer(max(16, int(round(1000.0 / self.fps))))
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        if self.playing and self.ready:
            self._read_frame(self.index + 1)

    def Command(self, item_id: int, message: c4d.BaseContainer) -> bool:
        if item_id == self.PLAY:
            self.playing = not self.playing
            self.SetString(self.PLAY, "暂停" if self.playing else "播放")
        elif item_id == self.PREVIOUS:
            self.playing = False
            self.SetString(self.PLAY, "播放")
            self._read_frame(self.index - 1)
        elif item_id == self.NEXT:
            self.playing = False
            self.SetString(self.PLAY, "播放")
            self._read_frame(self.index + 1)
        elif item_id == self.CLOSE:
            self.Close()
        return True

    def DestroyWindow(self) -> None:
        self.SetTimer(0)
        try:
            self.loader.Close()
        except (AttributeError, RuntimeError):
            pass


class AnimationCompareArea(c4d.gui.GeUserArea):
    """Draw synchronized white-model and AI frames side by side."""

    def __init__(self):
        super().__init__()
        self.scene_bitmap: Optional[c4d.bitmaps.BaseBitmap] = None
        self.result_bitmap: Optional[c4d.bitmaps.BaseBitmap] = None
        self.scene_index = 0
        self.result_index = 0
        self.scene_count = 0
        self.result_count = 0
        self.error = ""

    def GetMinSize(self) -> tuple[int, int]:
        return 920, 440

    def set_frames(
        self,
        scene_bitmap,
        result_bitmap,
        scene_index: int,
        result_index: int,
        scene_count: int,
        result_count: int,
    ) -> None:
        self.scene_bitmap = scene_bitmap
        self.result_bitmap = result_bitmap
        self.scene_index = max(0, int(scene_index))
        self.result_index = max(0, int(result_index))
        self.scene_count = max(0, int(scene_count))
        self.result_count = max(0, int(result_count))
        self.error = ""
        self.Redraw()

    def set_error(self, message: str) -> None:
        self.error = str(message or "无法读取对比动画")
        self.Redraw()

    def _draw_frame(self, bitmap, left: int, width: int, height: int) -> None:
        if bitmap is None:
            return
        rect = _fit_rect(bitmap.GetBw(), bitmap.GetBh(), max(1, width - 16), max(1, height - 44))
        self.DrawBitmap(
            bitmap,
            left + 8 + rect[0],
            30 + rect[1],
            rect[2],
            rect[3],
            0,
            0,
            bitmap.GetBw(),
            bitmap.GetBh(),
            c4d.BMP_NORMAL | getattr(c4d, "BMP_ALLOWALPHA", 0),
        )

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        half = max(1, width // 2)
        self.OffScreenOn()
        self.DrawSetPen(COLORS["preview_empty"])
        self.DrawRectangle(0, 0, max(0, width - 1), max(0, height - 1))
        if self.error:
            self.DrawSetTextCol(COLORS["text"], COLORS["preview_empty"])
            self.DrawText(self.error, 16, max(20, height // 2))
            return
        self._draw_frame(self.scene_bitmap, 0, half, height)
        self._draw_frame(self.result_bitmap, half, width - half, height)
        self.DrawSetPen(COLORS["line"])
        self.DrawRectangle(half - 1, 0, half + 1, max(0, height - 1))
        self.DrawSetFont(c4d.FONT_BOLD)
        self.DrawSetTextCol(COLORS["text"], COLORS["preview_empty"])
        self.DrawText("白模动画", 12, 8)
        self.DrawText("AI 渲染动画", half + 12, 8)
        self.DrawSetFont(c4d.FONT_DEFAULT)
        self.DrawSetTextCol(COLORS["muted"], COLORS["preview_empty"])
        left_status = "{}/{}".format(min(self.scene_index + 1, self.scene_count), self.scene_count)
        right_status = "{}/{}".format(min(self.result_index + 1, self.result_count), self.result_count)
        self.DrawText(left_status, 12, max(8, height - 24))
        self.DrawText(right_status, half + 12, max(8, height - 24))


class AnimationCompareDialog(c4d.gui.GeDialog):
    """Synchronize two MP4/MOV files in one native C4D comparison player."""

    AREA = 9271
    PREVIOUS = 9272
    PLAY = 9273
    NEXT = 9274
    CLOSE = 9275

    def __init__(self, scene_path: str, result_path: str):
        super().__init__()
        self.scene_path = str(scene_path)
        self.result_path = str(result_path)
        self.area = AnimationCompareArea()
        self.scene_loader = c4d.bitmaps.MovieLoader()
        self.result_loader = c4d.bitmaps.MovieLoader()
        self.scene_count = 0
        self.result_count = 0
        self.scene_fps = 24.0
        self.result_fps = 24.0
        self.clock_fps = 24.0
        self.playhead = 0.0
        self.playing = True
        self.ready = False

    def CreateLayout(self) -> bool:
        self.SetTitle("{} · 白模 / AI 动画对比".format(PRODUCT_WINDOW_TITLE))
        self.GroupBegin(9270, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(6, 6)
        self.AddUserArea(self.AREA, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 920, 440)
        self.AttachUserArea(self.area, self.AREA)
        self.GroupBegin(9276, c4d.BFH_RIGHT, 4, 1)
        self.GroupSpace(8, 0)
        self.AddButton(self.PREVIOUS, c4d.BFH_RIGHT, 72, 28, "后退")
        self.AddButton(self.PLAY, c4d.BFH_RIGHT, 80, 28, "暂停")
        self.AddButton(self.NEXT, c4d.BFH_RIGHT, 72, 28, "前进")
        self.AddButton(self.CLOSE, c4d.BFH_RIGHT, 64, 28, "关闭")
        self.GroupEnd()
        self.GroupEnd()
        return True

    @staticmethod
    def _open_loader(loader, path: str) -> tuple[int, float]:
        result = loader.Open(path)
        if result not in (None, c4d.IMAGERESULT_OK):
            return 0, 0.0
        info = loader.GetInfo()
        if not isinstance(info, tuple) or len(info) < 2:
            return 0, 0.0
        return max(0, int(info[0])), max(1.0, float(info[1]))

    @staticmethod
    def _read_loader(loader, index: int, count: int):
        if count <= 0:
            return None
        value = loader.Read(int(index) % count)
        if not isinstance(value, tuple) or len(value) < 2:
            return None
        return value[1] if value[0] == c4d.IMAGERESULT_OK else None

    def _refresh_frames(self) -> None:
        if not self.ready:
            return
        scene_index = int(self.playhead * self.scene_fps) % self.scene_count
        result_index = int(self.playhead * self.result_fps) % self.result_count
        scene_bitmap = self._read_loader(self.scene_loader, scene_index, self.scene_count)
        result_bitmap = self._read_loader(self.result_loader, result_index, self.result_count)
        if scene_bitmap is None or result_bitmap is None:
            self.area.set_error("C4D 未能同步读取白模与 AI 动画画面")
            self.ready = False
            return
        self.area.set_frames(
            scene_bitmap,
            result_bitmap,
            scene_index,
            result_index,
            self.scene_count,
            self.result_count,
        )

    def InitValues(self) -> bool:
        self.scene_count, self.scene_fps = self._open_loader(
            self.scene_loader, self.scene_path
        )
        self.result_count, self.result_fps = self._open_loader(
            self.result_loader, self.result_path
        )
        self.ready = self.scene_count > 0 and self.result_count > 0
        if not self.ready:
            self.area.set_error("C4D 无法解码白模动画或 AI 动画")
            for item_id in (self.PREVIOUS, self.PLAY, self.NEXT):
                self.Enable(item_id, False)
            return True
        self.clock_fps = max(self.scene_fps, self.result_fps, 24.0)
        self._refresh_frames()
        self.SetTimer(max(16, int(round(1000.0 / self.clock_fps))))
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        if self.playing and self.ready:
            self.playhead += 1.0 / self.clock_fps
            self._refresh_frames()

    def Command(self, item_id: int, message: c4d.BaseContainer) -> bool:
        if item_id == self.PLAY:
            self.playing = not self.playing
            self.SetString(self.PLAY, "暂停" if self.playing else "播放")
        elif item_id == self.PREVIOUS:
            self.playing = False
            self.SetString(self.PLAY, "播放")
            self.playhead = max(0.0, self.playhead - 1.0 / self.clock_fps)
            self._refresh_frames()
        elif item_id == self.NEXT:
            self.playing = False
            self.SetString(self.PLAY, "播放")
            self.playhead += 1.0 / self.clock_fps
            self._refresh_frames()
        elif item_id == self.CLOSE:
            self.Close()
        return True

    def DestroyWindow(self) -> None:
        self.SetTimer(0)
        for loader in (self.scene_loader, self.result_loader):
            try:
                loader.Close()
            except (AttributeError, RuntimeError):
                pass


class AnimationPreviewDialog(c4d.gui.GeDialog):
    """Cross-platform player for C4D render-loaded keyframe sequences."""

    AREA = 9201
    PLAY = 9202
    CLOSE = 9203

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = list(paths)
        self.area = AnimationSequenceArea(self.paths)
        self.playing = True
        self.index = 0

    def CreateLayout(self) -> bool:
        self.SetTitle("{} · 白模动画预览".format(PRODUCT_WINDOW_TITLE))
        self.GroupBegin(9200, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.AddUserArea(
            self.AREA, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 640, 420
        )
        self.AttachUserArea(self.area, self.AREA)
        self.GroupBegin(9204, c4d.BFH_RIGHT, 2, 1)
        self.AddButton(self.PLAY, c4d.BFH_RIGHT, 92, 28, "暂停")
        self.AddButton(self.CLOSE, c4d.BFH_RIGHT, 72, 28, "关闭")
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self) -> bool:
        self.area.set_index(0)
        self.SetTimer(160)
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        if self.playing and self.paths:
            self.index = (self.index + 1) % len(self.paths)
            self.area.set_index(self.index)

    def Command(self, item_id: int, message: c4d.BaseContainer) -> bool:
        if item_id == self.PLAY:
            self.playing = not self.playing
            self.SetString(self.PLAY, "暂停" if self.playing else "播放")
        elif item_id == self.CLOSE:
            self.Close()
        return True

    def DestroyWindow(self) -> None:
        self.SetTimer(0)


class StudioDialog(c4d.gui.GeDialog):
    def __init__(self, data_dir: Union[str, Path]):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.capture_dir = self.data_dir / "captures"
        self.default_render_dir = self.data_dir / "renders"
        self.output_store = JsonStore(self.data_dir / "output.json", {})
        output_preferences = self.output_store.load()
        configured_output = (
            str(output_preferences.get("directory") or "").strip()
            if isinstance(output_preferences, dict)
            else ""
        )
        self.render_dir = (
            Path(configured_output).expanduser()
            if configured_output
            else self.default_render_dir
        )
        self.settings_store = JsonStore(self.data_dir / "settings.json", {})
        self.settings = merge_settings(self.settings_store.load())
        self.history = HistoryStore(
            self.data_dir / "history.json", int(self.settings.get("history_limit") or 200)
        )
        self.state = StudioState()
        self.header_area = HeaderArea()
        self.preview_area = PreviewArea(300, 240)
        self.compact_preview_area = PreviewArea(270, 230)
        self.scene_capture_area = SceneCaptureArea()
        self.reference_area = ReferenceArea()
        self.history_area = HistoryArea(
            270, HistoryArea.ROW_HEIGHT * HISTORY_VISIBLE_ROWS
        )
        self.preview_resize_area = PreviewResizeArea(230)
        self.render_button_area = PillActionButtonArea(
            Id.RENDER_VIEW_REFERENCE,
            "▣  渲染加载",
            "secondary",
            88,
            ACTION_BUTTON_HEIGHT,
        )
        self.upload_button_area = PillActionButtonArea(
            Id.REFERENCE_ADD,
            "＋  上传参考",
            "secondary",
            88,
            ACTION_BUTTON_HEIGHT,
        )
        self.paste_button_area = PillActionButtonArea(
            Id.REFERENCE_PASTE,
            "粘贴",
            "secondary",
            68,
            ACTION_BUTTON_HEIGHT,
        )
        self.workflow_mode_area = WorkflowModeArea()
        self.generate_button_area = PillActionButtonArea(
            Id.GENERATE,
            "✦  立即生成",
            "primary",
            150,
            ACTION_BUTTON_HEIGHT,
        )
        self.cancel_button_area = PillActionButtonArea(
            Id.CANCEL_GENERATION,
            "取消",
            "danger",
            48,
            ACTION_BUTTON_HEIGHT,
        )
        for area in (
            self.header_area,
            self.preview_area,
            self.compact_preview_area,
            self.scene_capture_area,
            self.reference_area,
            self.history_area,
            self.preview_resize_area,
            self.render_button_area,
            self.upload_button_area,
            self.paste_button_area,
            self.workflow_mode_area,
            self.generate_button_area,
            self.cancel_button_area,
        ):
            area._zhire_dialog = self
        self.main_model_values: list[str] = []
        self.aspect_control_values: list[str] = ["自动"] + list(ASPECT_PRESETS.keys())
        self.animation_duration_values: list[int] = list(ANIMATION_DURATIONS)
        self.animation_resolution_values: list[str] = list(ANIMATION_RESOLUTIONS)
        self.compare_position = 0.5
        self.preview_height = 230
        self.pending_preview_height: Optional[int] = None
        # Keep a stable native multiline editor.  Dynamic row-count rebuilds
        # were resetting C4D's caret to column zero after hard/soft wraps.
        self.prompt_rows = 10
        self.prompt_height = self._prompt_height_for_rows(10)
        self.prompt_layout_width = 0
        self.prompt_wrap_width = 340
        self.prompt_placeholder_active = False
        self.prompt_has_focus = False
        self.prompt_rewrap_pending = False
        self.prompt_rewrap_deadline = 0.0
        self.pending_prompt_cursor: Optional[tuple] = None
        self.prompt_rewrite_active = False
        self.preview_dismissed = False
        self.last_prompt_text = ""
        self.prompt_logical_text = ""
        self.prompt_auto_breaks: tuple[int, ...] = ()
        self.mention_dialog_open = False
        self.pending_generation = False
        self.cancelled_workers: list[GenerationThread] = []
        self.render_worker: Optional[RenderLoadTask] = None
        self.prompt_worker: Optional[PromptGenerationThread] = None
        self.prompt_busy = False
        self.prompt_model_values = ["gpt-5.6-sol"]
        self.prompt_skill_values = [key for key, _label in skill_options()]
        self.animation_preview_dialog: Optional[c4d.gui.GeDialog] = None
        self.render_started_at = 0.0
        self.generation_started_at = 0.0
        self.history_visible = True
        self.preview_visible = True
        self.controls_visible = True
        self.layout_mode = ""
        self.compact_view = "controls"
        self.folded = {
            Id.FOLD_HISTORY: False,
        }
        self.fold_content = {
            Id.FOLD_HISTORY: Id.CONTENT_HISTORY,
        }
        self.fold_titles = {
            Id.FOLD_HISTORY: "历史管理器 · 双击缩略图看大图",
        }
        if self.history.items:
            latest = self.history.items[0]
            self.state.restore_history_snapshot(latest)
            if self.state.current_result and not Path(self.state.current_result).is_file():
                self.state.current_result = ""
            animation_movie = str(
                (self.state.scene_context or {}).get("animation_movie") or ""
            )
            if (
                self.state.workflow == "animation"
                and _is_video_path(animation_movie)
                and Path(animation_movie).is_file()
            ):
                self.state.scene_image = animation_movie
            elif self.state.scene_image and not Path(self.state.scene_image).is_file():
                self.state.scene_image = ""
            self.state.animation_frames = [
                str(path)
                for path in self.state.animation_frames
                if Path(str(path)).is_file()
            ]
            self.state.references = [
                str(path)
                for path in self.state.references
                if Path(str(path)).is_file()
            ]

    def _CreateLegacyLayout(self) -> bool:
        self.SetTitle(PRODUCT_WINDOW_TITLE)
        self.GroupBegin(1000, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupSpace(6, 6)
        self.GroupBorderSpace(6, 6, 6, 6)

        self.GroupBegin(1001, c4d.BFH_SCALEFIT, 1, 1)
        self.AddUserArea(Id.HEADER_AREA, c4d.BFH_SCALEFIT, 156, 48)
        self.AttachUserArea(self.header_area, Id.HEADER_AREA)
        self.GroupEnd()

        self.GroupBegin(1002, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 4, 1)
        self.GroupSpace(6, 6)

        self.GroupBegin(Id.GROUP_NAV, c4d.BFH_LEFT | c4d.BFV_SCALEFIT, 1, 0, "", 0, 44, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(3, 3, 3, 3)
        self.GroupSpace(3, 5)
        self.AddStaticText(0, c4d.BFH_CENTER, 38, 22, "AI")
        self.AddButton(Id.TOGGLE_CONTROLS, c4d.BFH_CENTER, 38, 32, "创作")
        self.AddButton(Id.TOGGLE_PREVIEW, c4d.BFH_CENTER, 38, 32, "预览")
        self.AddButton(Id.TOGGLE_HISTORY, c4d.BFH_CENTER, 38, 32, "历史")
        self.AddButton(Id.SETTINGS, c4d.BFH_CENTER | c4d.BFV_BOTTOM, 38, 32, "设置")
        self.GroupEnd()

        self.GroupBegin(Id.GROUP_HISTORY, c4d.BFH_LEFT | c4d.BFV_SCALEFIT, 1, 0, "", 0, 184, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "生成历史")
        self.AddUserArea(Id.HISTORY_AREA, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 172, 320)
        self.AttachUserArea(self.history_area, Id.HISTORY_AREA)
        self.GroupBegin(1199, c4d.BFH_SCALEFIT, 3, 1)
        self.AddButton(Id.HISTORY_DELETE, c4d.BFH_SCALEFIT, 0, 24, "删除")
        self.AddButton(Id.HISTORY_CLEAR, c4d.BFH_SCALEFIT, 0, 24, "清空")
        self.AddButton(Id.HISTORY_OUTPUT_DIRECTORY, c4d.BFH_SCALEFIT, 0, 24, "保存路径")
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(Id.GROUP_PREVIEW, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.GroupBegin(1201, c4d.BFH_SCALEFIT, 3, 1)
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "效果预览")
        self.AddCheckbox(Id.COMPARE, c4d.BFH_RIGHT, 0, 0, "白模 / AI 对比")
        self.AddButton(Id.LARGE_PREVIEW, c4d.BFH_RIGHT, 72, 24, "大图")
        self.GroupEnd()
        self.AddUserArea(Id.PREVIEW_AREA, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 300, 240)
        self.AttachUserArea(self.preview_area, Id.PREVIEW_AREA)
        self.GroupBegin(1202, c4d.BFH_SCALEFIT, 3, 1)
        self.AddStaticText(0, c4d.BFH_LEFT | c4d.BFV_CENTER, 50, 0, "对比")
        self.AddEditSlider(Id.COMPARE_SLIDER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(Id.OPEN_FOLDER, c4d.BFH_RIGHT, 72, 24, "目录")
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(
            Id.GROUP_CONTROLS,
            c4d.BFH_RIGHT | c4d.BFV_SCALEFIT,
            1,
            0,
            "",
            0,
            306,
            0,
        )
        self.ScrollGroupBegin(
            Id.SCROLL_CONTROLS,
            c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
            c4d.SCROLLGROUP_VERT | c4d.SCROLLGROUP_BORDERIN,
            306,
            0,
        )
        self.GroupBegin(1300, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0)
        self.GroupBorderSpace(7, 7, 7, 7)
        self.GroupSpace(6, 7)

        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "AI 功能")
        self.AddComboBox(Id.WORKFLOW, c4d.BFH_SCALEFIT, 0, 0)

        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "选择修图目标、场景与参考图后开始生成")

        self._section_button(Id.FOLD_SCENE)
        self.GroupBegin(Id.CONTENT_SCENE, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.AddStaticText(Id.SCENE_PATH, c4d.BFH_SCALEFIT, 0, 0, "尚未捕获视图", c4d.BORDER_ROUND)
        self.GroupBegin(1301, c4d.BFH_SCALEFIT, 2, 1)
        self.AddButton(Id.CAPTURE_SCENE, c4d.BFH_SCALEFIT, 0, 26, "捕获视图")
        self.AddButton(Id.IMPORT_SCENE, c4d.BFH_SCALEFIT, 0, 26, "本地上传")
        self.GroupEnd()
        self.GroupEnd()

        self._section_button(Id.FOLD_REFERENCES)
        self.GroupBegin(Id.CONTENT_REFERENCES, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.AddUserArea(Id.REFERENCE_AREA, c4d.BFH_SCALEFIT, 270, 96)
        self.AttachUserArea(self.reference_area, Id.REFERENCE_AREA)
        self.GroupBegin(1302, c4d.BFH_SCALEFIT, 2, 1)
        self.AddButton(Id.REFERENCE_ADD, c4d.BFH_SCALEFIT, 0, 24, "＋ 添加参考")
        self.AddButton(Id.REFERENCE_DELETE, c4d.BFH_SCALEFIT, 0, 24, "删除选中")
        self.GroupEnd()
        self.GroupBegin(1303, c4d.BFH_SCALEFIT, 2, 1)
        self.AddComboBox(Id.REFERENCE_TOKEN, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(Id.REFERENCE_INSERT, c4d.BFH_RIGHT, 84, 24, "插入词中")
        self.GroupEnd()
        self.GroupEnd()

        self._section_button(Id.FOLD_PROMPT)
        self.GroupBegin(Id.CONTENT_PROMPT, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.AddMultiLineEditText(
            Id.PROMPT,
            c4d.BFH_SCALEFIT,
            270,
            120,
            getattr(c4d, "DR_MULTILINE_WORDWRAP", 0),
        )
        self.AddStaticText(
            0, c4d.BFH_SCALEFIT, 0, 0, "插入 [[图1]]，可指定参考图在文字中的语义位置。"
        )
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "反向提示词")
        self.AddMultiLineEditText(
            Id.NEGATIVE_PROMPT,
            c4d.BFH_SCALEFIT,
            270,
            54,
            getattr(c4d, "DR_MULTILINE_WORDWRAP", 0),
        )
        self.GroupEnd()

        self._section_button(Id.FOLD_OUTPUT)
        self.GroupBegin(Id.CONTENT_OUTPUT, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.GroupBegin(1304, c4d.BFH_SCALEFIT, 2, 0)
        self.AddStaticText(0, c4d.BFH_LEFT | c4d.BFV_CENTER, 82, 0, "画幅比例")
        self.AddComboBox(Id.ASPECT, c4d.BFH_SCALEFIT, 0, 0)
        self.AddStaticText(0, c4d.BFH_LEFT | c4d.BFV_CENTER, 82, 0, "精度")
        self.AddComboBox(Id.QUALITY, c4d.BFH_SCALEFIT, 0, 0)
        self.AddStaticText(0, c4d.BFH_LEFT | c4d.BFV_CENTER, 82, 0, "生成数量")
        self.AddComboBox(Id.COUNT, c4d.BFH_SCALEFIT, 0, 0)
        self.GroupEnd()
        self.AddCheckbox(Id.PRESERVE, c4d.BFH_LEFT, 0, 0, "锁定视图构图与模型形体")
        self.GroupEnd()

        self.AddSeparatorH(1, c4d.BFH_SCALEFIT)
        self.GroupBegin(Id.GROUP_COMPACT_PREVIEW, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupSpace(5, 5)
        self.GroupBegin(1305, c4d.BFH_SCALEFIT, 3, 1)
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "生成结果")
        self.AddCheckbox(Id.COMPACT_COMPARE, c4d.BFH_RIGHT, 0, 0, "对比")
        self.AddButton(Id.LARGE_PREVIEW, c4d.BFH_RIGHT, 52, 24, "大图")
        self.GroupEnd()
        self.AddUserArea(Id.COMPACT_PREVIEW_AREA, c4d.BFH_SCALEFIT, 270, 170)
        self.AttachUserArea(self.compact_preview_area, Id.COMPACT_PREVIEW_AREA)
        self.GroupEnd()

        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(1306, c4d.BFH_SCALEFIT | c4d.BFV_BOTTOM, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(7, 7, 7, 7)
        self.GroupSpace(4, 4)
        self.AddButton(Id.GENERATE, c4d.BFH_SCALEFIT, 0, 26, "✦  立即生成")
        self.AddButton(Id.CANCEL_GENERATION, c4d.BFH_SCALEFIT, 0, 22, "取消生成")
        self.GroupEnd()

        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()
        return True

    def CreateLayout(self) -> bool:
        self.SetTitle(PRODUCT_WINDOW_TITLE)
        self.GroupBegin(1000, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupSpace(3, 3)
        self.GroupBorderSpace(3, 3, 3, 3)

        self.GroupBegin(1002, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBegin(Id.GROUP_CONTROLS, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.ScrollGroupBegin(
            Id.SCROLL_CONTROLS,
            c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
            c4d.SCROLLGROUP_VERT | c4d.SCROLLGROUP_BORDERIN,
            306,
            0,
        )
        self.GroupBegin(1300, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0)
        self.GroupBorderSpace(3, 3, 3, 3)
        self.GroupSpace(3, 3)

        self.GroupBegin(1312, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(5, 5, 5, 5)
        self.GroupSpace(5, 4)
        # Keep the preview header on one compact row. The brand replaces the
        # redundant “效果图预览” label; connection details remain in Settings.
        self.GroupBegin(1314, c4d.BFH_SCALEFIT, 2, 1)
        self.AddStaticText(
            0,
            c4d.BFH_SCALEFIT | c4d.BFV_CENTER,
            0,
            20,
            PRODUCT_WINDOW_TITLE,
        )
        # Keep the three utilities on one row, while giving every target its
        # own 8 px rhythm so the checkbox and icon buttons no longer collide.
        self.GroupBegin(1315, c4d.BFH_RIGHT | c4d.BFV_CENTER, 3, 1)
        self.GroupSpace(8, 0)
        self.AddCheckbox(Id.COMPACT_COMPARE, c4d.BFH_RIGHT | c4d.BFV_CENTER, 58, 24, "对比")
        self.AddButton(Id.HEADER_REFRESH, c4d.BFH_RIGHT | c4d.BFV_CENTER, 34, 24, "↻")
        self.AddButton(Id.SETTINGS, c4d.BFH_RIGHT | c4d.BFV_CENTER, 34, 24, "⚙")
        self.GroupEnd()
        self.GroupEnd()
        self.AddSeparatorH(1, c4d.BFH_SCALEFIT)
        self.GroupBegin(Id.PREVIEW_GROUP, c4d.BFH_SCALEFIT, 1, 0)
        self._build_preview_editor()
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(1333, c4d.BFH_SCALEFIT, 1, 0)
        self.AddStaticText(
            0,
            c4d.BFH_SCALEFIT | c4d.BFV_CENTER,
            0,
            18,
            "提示词 · @ 引用参考图 · 手动确认构图",
            c4d.BORDER_ROUND,
        )
        self.GroupEnd()
        self.GroupBegin(Id.PROMPT_GROUP, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(4, 4, 4, 4)
        self._build_prompt_editor()
        self.AddStaticText(
            0,
            c4d.BFH_SCALEFIT,
            0,
            18,
            "先写图片职责与画面提示，AI 会按职责扩写；技能只补全，不覆盖你的明确要求。",
        )
        self.GroupEnd()
        self.GroupBegin(1335, c4d.BFH_SCALEFIT, 3, 1)
        self.GroupSpace(5, 0)
        self.AddComboBox(Id.PROMPT_MODEL, c4d.BFH_SCALEFIT, 0, 22)
        self.AddComboBox(Id.PROMPT_SKILL, c4d.BFH_SCALEFIT, 0, 22)
        self.AddButton(
            Id.PROMPT_AI_GENERATE,
            c4d.BFH_RIGHT | c4d.BFV_CENTER,
            126,
            22,
            "AI 生成提示词",
        )
        self.GroupEnd()

        self.AddSeparatorH(1, c4d.BFH_SCALEFIT)
        self.GroupBegin(1323, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(5, 5, 5, 5)
        self.GroupSpace(5, 5)
        self.GroupBegin(1340, c4d.BFH_SCALEFIT, 2, 1)
        self.AddStaticText(
            0,
            c4d.BFH_SCALEFIT | c4d.BFV_CENTER,
            0,
            22,
            "场景与参考图",
        )
        self.AddUserArea(Id.MODE_SWITCH, c4d.BFH_RIGHT, 164, 30)
        self.AttachUserArea(self.workflow_mode_area, Id.MODE_SWITCH)
        self.GroupEnd()
        self.AddUserArea(Id.REFERENCE_AREA, c4d.BFH_SCALEFIT, 292, 58)
        self.AttachUserArea(self.reference_area, Id.REFERENCE_AREA)
        self.GroupBegin(1325, c4d.BFH_SCALEFIT, 3, 1)
        self.AddUserArea(
            Id.RENDER_VIEW_REFERENCE,
            c4d.BFH_SCALEFIT,
            88,
            ACTION_BUTTON_HEIGHT,
        )
        self.AttachUserArea(self.render_button_area, Id.RENDER_VIEW_REFERENCE)
        self.AddUserArea(
            Id.REFERENCE_ADD,
            c4d.BFH_SCALEFIT,
            88,
            ACTION_BUTTON_HEIGHT,
        )
        self.AttachUserArea(self.upload_button_area, Id.REFERENCE_ADD)
        self.AddUserArea(
            Id.REFERENCE_PASTE,
            c4d.BFH_SCALEFIT,
            68,
            ACTION_BUTTON_HEIGHT,
        )
        self.AttachUserArea(self.paste_button_area, Id.REFERENCE_PASTE)
        self.GroupEnd()
        self.AddSeparatorH(1, c4d.BFH_SCALEFIT)
        self.GroupBegin(1329, c4d.BFH_SCALEFIT, 4, 1)
        self.AddStaticText(Id.PARAM_MODEL_LABEL, c4d.BFH_SCALEFIT, 0, 0, "模型")
        self.AddStaticText(Id.PARAM_ASPECT_LABEL, c4d.BFH_SCALEFIT, 0, 0, "画幅")
        self.AddStaticText(Id.PARAM_QUALITY_LABEL, c4d.BFH_SCALEFIT, 0, 0, "精度")
        self.AddStaticText(Id.PARAM_COUNT_LABEL, c4d.BFH_SCALEFIT, 0, 0, "数量")
        self.GroupEnd()
        self.GroupBegin(1330, c4d.BFH_SCALEFIT, 4, 1)
        self.AddComboBox(Id.MAIN_MODEL, c4d.BFH_SCALEFIT, 0, 0)
        self.AddComboBox(Id.ASPECT, c4d.BFH_SCALEFIT, 0, 0)
        self.AddComboBox(Id.QUALITY, c4d.BFH_SCALEFIT, 0, 0)
        self.AddComboBox(Id.COUNT, c4d.BFH_SCALEFIT, 0, 0)
        self.GroupEnd()
        self.GroupBegin(1331, c4d.BFH_SCALEFIT, 1, 1)
        self.AddCheckbox(Id.PRESERVE, c4d.BFH_SCALEFIT, 0, 0, "锁定视图构图与模型形体")
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(1332, c4d.BFH_SCALEFIT, 2, 1)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(2, 2, 2, 2)
        self.GroupSpace(2, 0)
        self.AddUserArea(
            Id.GENERATE,
            c4d.BFH_SCALEFIT | c4d.BFV_CENTER,
            150,
            ACTION_BUTTON_HEIGHT,
        )
        self.AttachUserArea(self.generate_button_area, Id.GENERATE)
        self.AddUserArea(
            Id.CANCEL_GENERATION,
            c4d.BFH_RIGHT | c4d.BFV_CENTER,
            48,
            ACTION_BUTTON_HEIGHT,
        )
        self.AttachUserArea(self.cancel_button_area, Id.CANCEL_GENERATION)
        self.GroupEnd()

        self._section_button(Id.FOLD_HISTORY)
        self.GroupBegin(Id.CONTENT_HISTORY, c4d.BFH_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(3, 3, 3, 3)
        self.GroupSpace(2, 2)
        self.AddUserArea(
            Id.INLINE_HISTORY_AREA,
            c4d.BFH_SCALEFIT,
            292,
            HistoryArea.ROW_HEIGHT * HISTORY_VISIBLE_ROWS,
        )
        self.AttachUserArea(self.history_area, Id.INLINE_HISTORY_AREA)
        self.GroupBegin(1316, c4d.BFH_SCALEFIT, 3, 1)
        self.AddButton(Id.HISTORY_DELETE, c4d.BFH_SCALEFIT, 0, 18, "删除记录")
        self.AddButton(Id.HISTORY_CLEAR, c4d.BFH_SCALEFIT, 0, 18, "清空历史")
        self.AddButton(
            Id.HISTORY_OUTPUT_DIRECTORY,
            c4d.BFH_SCALEFIT,
            0,
            18,
            "设置保存路径",
        )
        self.GroupEnd()
        self.GroupEnd()

        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()
        return True

    def _build_preview_editor(self) -> None:
        self.compact_preview_area.min_height = self.preview_height
        self.AddUserArea(
            Id.COMPACT_PREVIEW_AREA,
            c4d.BFH_SCALEFIT,
            292,
            self.preview_height,
        )
        self.AttachUserArea(self.compact_preview_area, Id.COMPACT_PREVIEW_AREA)
        self.AddUserArea(Id.PREVIEW_RESIZE_AREA, c4d.BFH_SCALEFIT, 292, 14)
        self.AttachUserArea(self.preview_resize_area, Id.PREVIEW_RESIZE_AREA)

    @staticmethod
    def _prompt_height_for_rows(rows: int) -> int:
        # Fixed visual height; the native editor still accepts unlimited text
        # and real newline characters beyond the ten visible rows.
        return 10 * 18 + 10

    def _build_prompt_editor(self) -> None:
        self.AddMultiLineEditText(
            Id.PROMPT,
            c4d.BFH_SCALEFIT,
            0,
            self.prompt_height,
            style=PROMPT_EDITOR_STYLE,
        )
        # C4D 2023 resolves a multiline gadget's background map when it is
        # created. Apply the colors here, not only later in InitValues, so the
        # input surface is visibly lighter on the first frame and after a row
        # count rebuild.
        self._apply_prompt_text_color()

    def _prompt_wrap_columns(self) -> int:
        """Convert the last stable panel width into conservative text units."""

        # C4D 2023.1 treats an unspaced Chinese run as one word even with
        # DR_MULTILINE_WORDWRAP. Wide glyphs count as two units in
        # wrapped_editor_prompt; eight pixels per unit leaves enough room for
        # the group border, scrollbar and proportional UI font differences.
        usable_width = max(128, int(self.prompt_wrap_width) - 70)
        return max(16, usable_width // 8)

    def _apply_prompt_text_color(self) -> None:
        color = COLORS["muted"] if self.prompt_placeholder_active else COLORS["text"]
        for color_name in ("COLOR_TEXT_EDIT", "COLOR_TEXTFOCUS", "COLOR_TEXT"):
            color_id = getattr(c4d, color_name, None)
            if color_id is None:
                continue
            try:
                self.SetDefaultColor(Id.PROMPT, color_id, color)
            except (AttributeError, TypeError):
                pass
        for color_name, background in (
            ("COLOR_BG", COLORS["input_bg"]),
            ("COLOR_BGEDIT", COLORS["input_bg"]),
            ("COLOR_BGFOCUS", COLORS["input_focus_bg"]),
        ):
            color_id = getattr(c4d, color_name, None)
            if color_id is None:
                continue
            try:
                self.SetDefaultColor(Id.PROMPT, color_id, background)
            except (AttributeError, TypeError):
                pass

    def _prompt_cursor_offset(self) -> Optional[int]:
        """Read the native absolute cursor position without guessing edits."""

        get_cursor = getattr(c4d, "BFM_EDITFIELD_GETCURSORPOS", None)
        requires_result = getattr(c4d, "BFM_REQUIRESRESULT", None)
        if get_cursor is None:
            return None
        try:
            message = c4d.BaseContainer(get_cursor)
            if requires_result is not None:
                message.SetBool(requires_result, True)
            result = self.SendMessage(Id.PROMPT, message)
            position = int(result)
            if position < 0:
                return None
            return max(0, min(len(self.GetString(Id.PROMPT)), position))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return None

    def _restore_prompt_cursor(self, displayed: str, plain_offset: int) -> None:
        """Restore the caret after a programmatic prompt or row-count update."""

        absolute = tracked_display_offset_from_plain(
            displayed,
            plain_offset,
            self.prompt_logical_text,
            self.prompt_auto_breaks,
        )
        prefix = canonical_editor_display(str(displayed or "")[:absolute])
        line = prefix.count("\n")
        column = len(prefix.rsplit("\n", 1)[-1])
        try:
            # SetMultiLinePos is the documented C4D API. Its line index is
            # zero-based; the old private edit-field message silently failed
            # on C4D 2025 and left the caret at the beginning of a wrapped line.
            self.Activate(Id.PROMPT)
            self.SetMultiLinePos(Id.PROMPT, line, column)
        except (AttributeError, TypeError, RuntimeError):
            pass

    def _set_prompt_text(
        self,
        text: str,
        caret_plain_offset: Optional[int] = None,
        activate: bool = False,
    ) -> None:
        self.pending_prompt_cursor = None
        value = plain_editor_prompt(text)
        self.prompt_placeholder_active = not bool(value.strip()) and not self.prompt_has_focus
        if self.prompt_placeholder_active:
            displayed = PROMPT_PLACEHOLDER
            automatic_breaks: tuple[int, ...] = ()
        else:
            displayed, automatic_breaks = wrapped_editor_layout(
                value, self._prompt_wrap_columns()
            )
        self.prompt_logical_text = value
        self.prompt_auto_breaks = automatic_breaks
        self.prompt_rewrap_deadline = 0.0
        self.prompt_rewrite_active = True
        try:
            self.SetString(Id.PROMPT, displayed)
        finally:
            self.prompt_rewrite_active = False
        displayed = self.GetString(Id.PROMPT)
        self.last_prompt_text = displayed
        self._apply_prompt_text_color()
        if caret_plain_offset is not None:
            self._restore_prompt_cursor(displayed, caret_plain_offset)
        elif activate:
            self.Activate(Id.PROMPT)

    def _prompt_value(self) -> str:
        text = self.GetString(Id.PROMPT)
        if self.prompt_placeholder_active or text == PROMPT_PLACEHOLDER:
            return ""
        if text != self.last_prompt_text:
            return editor_prompt_after_change(
                self.prompt_logical_text,
                self.last_prompt_text,
                text,
                self.prompt_auto_breaks,
            )[0]
        return self.prompt_logical_text

    def _sync_prompt_focus_state(self) -> None:
        """Keep the prompt hint visual-only while preserving native text editing."""

        try:
            active = bool(self.IsActive(Id.PROMPT))
        except (AttributeError, TypeError):
            active = False
        if active == self.prompt_has_focus:
            return
        self.prompt_has_focus = active
        if active and self.prompt_placeholder_active:
            self.prompt_placeholder_active = False
            self.SetString(Id.PROMPT, "")
            self.last_prompt_text = ""
            self.prompt_logical_text = ""
            self.prompt_auto_breaks = ()
            self._apply_prompt_text_color()
        elif not active:
            if not self.GetString(Id.PROMPT).strip():
                self._set_prompt_text("")
            if self.prompt_rewrap_pending:
                self.prompt_rewrap_pending = False
                self._update_responsive_layout(force=True)

    def request_preview_height(self, height: int) -> None:
        self.pending_preview_height = max(140, min(440, int(height)))

    def _apply_preview_height(self, height: int) -> None:
        height = max(140, min(440, int(height)))
        if height == self.preview_height:
            self.preview_resize_area.set_height(height)
            return
        self.preview_height = height
        self.compact_preview_area.min_height = height
        self.preview_resize_area.set_height(height)
        self.compact_preview_area.LayoutChanged()
        self.LayoutChanged(Id.PREVIEW_GROUP)

    def _handle_prompt_change(self, previous_text: Optional[str] = None) -> None:
        if self.prompt_rewrite_active:
            return
        previous_displayed = (
            self.last_prompt_text if previous_text is None else str(previous_text)
        )
        previous = self.prompt_logical_text
        displayed_text = self.GetString(Id.PROMPT)
        if (
            self.pending_prompt_cursor is not None
            and displayed_text != self.pending_prompt_cursor[0]
        ):
            # A newer user edit always wins over a queued cursor confirmation.
            self.pending_prompt_cursor = None
        if displayed_text == previous_displayed:
            return
        if self.prompt_placeholder_active:
            if displayed_text == PROMPT_PLACEHOLDER:
                return
            text = plain_editor_prompt(displayed_text).replace(
                PROMPT_PLACEHOLDER, "", 1
            )
            caret_offset = len(text)
            self.prompt_placeholder_active = False
            self._apply_prompt_text_color()
        else:
            text, _caret_offset = editor_prompt_after_change(
                previous,
                previous_displayed,
                displayed_text,
                self.prompt_auto_breaks,
            )
        if not text:
            self.last_prompt_text = ""
            self.prompt_logical_text = ""
            self.prompt_auto_breaks = ()
            self.prompt_placeholder_active = False
            self._apply_prompt_text_color()
            return
        self.prompt_logical_text = text
        self.last_prompt_text = displayed_text
        # Rewriting SetString inside every keyboard event makes C4D 2025 reset
        # the caret during IME and Backspace handling. Reflow after a short idle
        # interval instead; paste still wraps almost immediately.
        self.prompt_rewrap_deadline = time.monotonic() + 0.20
        at_position = inserted_mention_position(previous, text)
        if at_position < 0 or self.mention_dialog_open:
            return
        if not self.state.references:
            self._set_status("请先添加参考图，再输入 @ 选择")
            return
        self.mention_dialog_open = True
        try:
            picker = ReferenceMentionDialog(self.state.references)
            picker.Open(
                c4d.DLG_TYPE_MODAL_RESIZEABLE,
                PLUGIN_ID,
                defaultw=360,
                defaulth=142,
            )
            if picker.selected_index >= 0:
                updated = (
                    text[:at_position]
                    + image_token(picker.selected_index + 1)
                    + " "
                    + text[at_position + 1 :].lstrip()
                )
                self._set_prompt_text(updated)
                self._restore_prompt_cursor(
                    self.last_prompt_text,
                    at_position + len(image_token(picker.selected_index + 1)) + 1,
                )
        finally:
            self.mention_dialog_open = False

    def _rewrap_prompt_editor(self) -> None:
        """Reflow display-only lines while preserving the logical prompt."""

        self.prompt_rewrap_deadline = 0.0
        if self.prompt_placeholder_active:
            return
        displayed = self.GetString(Id.PROMPT)
        native_cursor = self._prompt_cursor_offset()
        logical_prompt = self._prompt_value()
        caret_offset = (
            tracked_plain_offset_from_display(
                displayed,
                native_cursor,
                self.prompt_logical_text,
                self.prompt_auto_breaks,
            )
            if native_cursor is not None
            else len(logical_prompt)
        )
        wrapped, automatic_breaks = wrapped_editor_layout(
            logical_prompt, self._prompt_wrap_columns()
        )
        self.prompt_logical_text = logical_prompt
        self.prompt_auto_breaks = automatic_breaks
        if equivalent_editor_display(wrapped, displayed):
            self.last_prompt_text = displayed
            return
        self.prompt_rewrite_active = True
        try:
            self.SetString(Id.PROMPT, wrapped)
            self.LayoutChanged(Id.PROMPT_GROUP)
        finally:
            self.prompt_rewrite_active = False
        displayed = self.GetString(Id.PROMPT)
        self.last_prompt_text = displayed
        if self.prompt_has_focus:
            self._restore_prompt_cursor(displayed, caret_offset)
            self.pending_prompt_cursor = (displayed, caret_offset)

    def _section_button(self, button_id: int) -> None:
        self.AddSeparatorH(1, c4d.BFH_SCALEFIT)
        self.AddButton(button_id, c4d.BFH_SCALEFIT, 0, 18, "")

    def InitValues(self) -> bool:
        self._populate_parameter_controls()
        self._populate_prompt_models()
        self._populate_prompt_skills()
        self.SetBool(Id.PRESERVE, True)
        self.SetBool(Id.COMPACT_COMPARE, False)
        self.Enable(Id.COMPACT_COMPARE, False)
        self._set_prompt_text(self.state.prompt)
        self.preview_resize_area.set_height(self.preview_height)
        self._apply_accent_colors()
        self._refresh_api_badge()
        self._refresh_reference_controls()
        self._refresh_history()
        self._refresh_preview()
        self._apply_fold_state()
        self._update_responsive_layout(force=True)
        self._set_status("就绪")
        self.SetTimer(24)
        return True

    def _populate_prompt_models(self) -> None:
        current = str(self.settings.get("prompt_model") or "gpt-5.6-sol").strip()
        if current not in self.prompt_model_values:
            current = self.prompt_model_values[0]
        self.FreeChildren(Id.PROMPT_MODEL)
        labels = {"gpt-5.6-sol": "GPT-5.6 Sol"}
        for child_id, model in enumerate(self.prompt_model_values, start=1):
            self.AddChild(Id.PROMPT_MODEL, child_id, labels[model])
        self.SetInt32(Id.PROMPT_MODEL, self.prompt_model_values.index(current) + 1)
        self.settings["prompt_model"] = current

    def _populate_prompt_skills(self) -> None:
        current = str(self.settings.get("prompt_skill") or "").strip()
        if current not in self.prompt_skill_values:
            current = self.prompt_skill_values[0]
        self.FreeChildren(Id.PROMPT_SKILL)
        labels = dict(skill_options())
        for child_id, skill_id in enumerate(self.prompt_skill_values, start=1):
            self.AddChild(Id.PROMPT_SKILL, child_id, labels[skill_id])
        self.SetInt32(Id.PROMPT_SKILL, self.prompt_skill_values.index(current) + 1)
        self.settings["prompt_skill"] = current

    def _populate_main_models(self) -> None:
        is_animation = self.state.workflow == "animation"
        current = str(
            self.settings.get("video_model" if is_animation else "model") or ""
        ).strip()
        provider = str(self.settings.get("provider") or "")

        def animation_model(value: str) -> bool:
            return is_compatible_seedance_model(provider, value)

        if is_animation:
            stored_seedance = self.settings.get("seedance_models")
            discovered = self.settings.get("available_models")
            source_models = (
                stored_seedance
                if isinstance(stored_seedance, list) and stored_seedance
                else (discovered if isinstance(discovered, list) else [])
            )
            values = []
            for model in source_models:
                value = str(model or "").strip()
                if animation_model(value) and value not in values:
                    values.append(value)
            if animation_model(current) and current not in values:
                values.insert(0, current)
        else:
            discovered = self.settings.get("available_models")
            values = [
                str(model or "").strip()
                for model in discovered or []
                if is_compatible_image_model(provider, str(model or ""))
            ] if isinstance(discovered, list) else []
            if str(self.settings.get("provider") or "") == "atlascloud":
                values = list(ATLAS_IMAGE_MODELS)
            # JuAIHub keeps its image catalog on a custom OpenAI-compatible
            # profile and may not expose the newest IDs in a stale /v1/models
            # cache.  Add only the curated image IDs for that host.
            custom_url = str(self.settings.get("base_url") or "").lower()
            if (
                str(self.settings.get("provider") or "") == "custom"
                and "api.juaihub.cn" in custom_url
            ):
                for model in JUAIHUB_IMAGE_MODELS:
                    if model not in values and is_compatible_image_model(provider, model):
                        values.append(model)
            if (
                current
                and is_compatible_image_model(provider, current)
                and current not in values
            ):
                values.insert(0, current)
        if current and current not in values and (
            (is_animation and animation_model(current))
            or (not is_animation and is_compatible_image_model(provider, current))
        ):
            values.insert(0, current)
        self.main_model_values = values
        self.FreeChildren(Id.MAIN_MODEL)
        if not values:
            self.AddChild(
                Id.MAIN_MODEL,
                1,
                "连接平台读取 Seedance" if is_animation else "使用 API 设置中的模型",
            )
            self.SetInt32(Id.MAIN_MODEL, 1)
            return
        for child_id, label in enumerate(values, start=1):
            display_names = {
                "google/nano-banana-2/edit": "Banana 2",
                "gemini-3.1-flash-image-preview": "Banana 2（Gemini 3.1 Flash）",
                "gemini-3-pro-image-preview": "Banana Pro（Gemini 3 Pro）",
                "bytedance/seedream-v4/edit": "Seedream 4 Edit",
                "gpt-image-2.5-flare": "Image 2.5 Flare",
                "gpt-image-2.5-sunburst": "Image 2.5 Sunburst",
            }
            if is_animation:
                normalized = label.lower().replace("_", "-")
                if "2-5" in normalized or "2.5" in normalized:
                    compact_label = "Seedance 2.5"
                elif "2-0" in normalized or "2.0" in normalized:
                    if "fast" in normalized:
                        compact_label = "Seedance 2.0 Fast"
                    elif "lite" in normalized or "mini" in normalized:
                        # Atlas names the lightweight 2.0 reference model
                        # "mini"; present it in designer-friendly terms while
                        # retaining the official model ID for API requests.
                        compact_label = "Seedance 2.0 Lite (Mini)"
                    elif "pro" in normalized:
                        compact_label = "Seedance 2.0 Pro"
                    else:
                        compact_label = "Seedance 2.0"
                elif "1-5" in normalized or "1.5" in normalized:
                    compact_label = "Seedance 1.5"
                else:
                    compact_label = label.rsplit("/", 1)[-1][:22]
            else:
                compact_label = display_names.get(label, label.rsplit("/", 1)[-1][:16])
            self.AddChild(Id.MAIN_MODEL, child_id, compact_label)
        self.SetInt32(Id.MAIN_MODEL, values.index(current) + 1 if current in values else 1)

    def _populate_parameter_controls(self) -> None:
        """Swap image/video parameter semantics without rebuilding the panel."""

        animation_ready, animation_reason = self._animation_availability()
        self._populate_main_models()
        self.FreeChildren(Id.ASPECT)
        aspect_values = (
            ["自动", "1:1", "4:3", "3:4", "16:9", "9:16"]
            if self.state.workflow == "animation"
            else ["自动"] + list(ASPECT_PRESETS.keys())
        )
        self.aspect_control_values = list(aspect_values)
        for child_id, label in enumerate(aspect_values, start=1):
            self.AddChild(Id.ASPECT, child_id, label)
        self.SetInt32(
            Id.ASPECT,
            aspect_values.index(self.state.options.aspect) + 1
            if self.state.options.aspect in aspect_values
            else 1,
        )
        self.FreeChildren(Id.QUALITY)
        self.FreeChildren(Id.COUNT)
        if self.state.workflow == "animation":
            provider = str(self.settings.get("provider") or "")
            video_model = str(self.settings.get("video_model") or "").lower()
            seedance_25 = is_seedance_25_model(video_model)
            duration_values = (
                ANIMATION_DURATIONS_25 if seedance_25 else ANIMATION_DURATIONS
            )
            self.animation_duration_values = list(duration_values)
            if seedance_25 or (
                provider == "atlascloud"
                and not any(marker in video_model for marker in ("fast", "mini", "lite"))
            ):
                resolution_values = ("480P", "720P", "1080P", "4K")
            elif provider == "volcengine" and "fast" not in video_model:
                resolution_values = ("480P", "720P", "1080P")
            else:
                resolution_values = ("480P", "720P")
            self.animation_resolution_values = list(resolution_values)
            for child_id, duration in enumerate(duration_values, start=1):
                self.AddChild(Id.QUALITY, child_id, "{} 秒".format(duration))
            for child_id, resolution in enumerate(resolution_values, start=1):
                self.AddChild(Id.COUNT, child_id, resolution)
            self.SetInt32(
                Id.QUALITY,
                duration_values.index(self.state.options.duration_seconds) + 1
                if self.state.options.duration_seconds in duration_values
                else 1,
            )
            self.SetInt32(
                Id.COUNT,
                resolution_values.index(self.state.options.video_resolution) + 1
                if self.state.options.video_resolution in resolution_values
                else min(2, len(resolution_values)),
            )
            self.SetString(Id.PARAM_QUALITY_LABEL, "时长")
            self.SetString(Id.PARAM_COUNT_LABEL, "分辨率")
            self.SetString(Id.PRESERVE, "锁定动画主体、镜头与运动")
        else:
            quality_values = list(QUALITY_PRESETS.keys())
            for child_id, label in enumerate(quality_values, start=1):
                self.AddChild(Id.QUALITY, child_id, label)
            for count in range(1, 5):
                self.AddChild(Id.COUNT, count, "{} 张".format(count))
            self.SetInt32(
                Id.QUALITY,
                quality_values.index(self.state.options.quality) + 1
                if self.state.options.quality in quality_values
                else 1,
            )
            self.SetInt32(Id.COUNT, max(1, min(4, self.state.options.count)))
            self.SetString(Id.PARAM_QUALITY_LABEL, "精度")
            self.SetString(Id.PARAM_COUNT_LABEL, "数量")
            self.SetString(Id.PRESERVE, "锁定视图构图与模型形体")
        self.SetBool(Id.PRESERVE, self.state.options.preserve_composition)
        self.SetString(
            Id.PARAM_MODEL_LABEL,
            "Seedance·全能参考" if self.state.workflow == "animation" else "模型",
        )
        self.SetString(Id.PARAM_ASPECT_LABEL, "画幅")
        self.workflow_mode_area.set_workflow(self.state.workflow)
        self.workflow_mode_area.set_animation_available(
            animation_ready, animation_reason
        )

    def _animation_availability(self) -> tuple[bool, str]:
        """Resolve the selected platform's animation capability independently."""

        tracks_active_provider = any(
            key in self.settings
            for key in (
                "last_connected_provider",
                "connected_providers",
                "provider_connection_states",
            )
        )
        active_provider = str(self.settings.get("last_connected_provider") or "").strip()
        if tracks_active_provider and not active_provider:
            return False, "请先在 API 设置中连接一个平台，再使用 Seedance 动画。"
        available, reason = seedance_capability(self.settings)
        if available:
            return True, ""
        return False, reason or seedance_unavailable_message(self.settings)

    def _switch_workflow(self, workflow: str) -> None:
        self._recover_interaction_state()
        if self.state.busy or self.state.render_busy or self.prompt_busy:
            return
        target = "animation" if workflow == "animation" else "image"
        if target == "animation":
            animation_ready, reason = self._animation_availability()
            self.workflow_mode_area.set_animation_available(animation_ready, reason)
            if not animation_ready:
                c4d.gui.MessageDialog(reason)
                self._set_status("Seedance 动画当前不可用；图像渲染不受影响", 0.0)
                return
        if target == self.state.workflow:
            return
        self._sync_form()
        self.state.workflow = target
        self.state.scene_image = ""
        self.state.scene_context = {}
        self.state.animation_frames = []
        self.state.current_result = ""
        self.state.compare_enabled = False
        self._populate_parameter_controls()
        self._refresh_reference_controls()
        self._refresh_preview()
        self._set_status(
            "已切换到{}，请重新点击渲染加载".format(
                "渲染动画" if target == "animation" else "渲染图像"
            ),
            0.0,
        )

    def _refresh_api_badge(self) -> None:
        tracks_active_provider = any(
            key in self.settings
            for key in (
                "last_connected_provider",
                "connected_providers",
                "provider_connection_states",
            )
        )
        active_provider = str(self.settings.get("last_connected_provider") or "").strip()
        configured = bool(
            str(self.settings.get("base_url") or "").strip()
            and (str(self.settings.get("api_key") or "").strip() or self.settings.get("api_key_env"))
            and (not tracks_active_provider or bool(active_provider))
        )
        self.SetString(Id.API_BADGE, "API ✓" if configured else "API !")

    def _apply_accent_colors(self) -> None:
        """Add restrained native highlights to the primary interactive controls."""

        def apply(control_id: int, names: tuple[str, ...], color: c4d.Vector) -> None:
            for name in names:
                color_id = getattr(c4d, name, None)
                if color_id is None:
                    continue
                try:
                    self.SetDefaultColor(control_id, color_id, color)
                except (AttributeError, TypeError):
                    pass

        apply(Id.API_BADGE, ("COLOR_TEXT",), COLORS["mint"])

    def _apply_fold_state(self) -> None:
        for button_id, content_id in self.fold_content.items():
            folded = self.folded.get(button_id, False)
            prefix = "▸" if folded else "▾"
            self.SetString(button_id, "{}  {}".format(prefix, self.fold_titles[button_id]))
            self.HideElement(content_id, folded)

    def _toggle_fold(self, button_id: int) -> None:
        self.folded[button_id] = not self.folded.get(button_id, False)
        self._apply_fold_state()
        self.LayoutChanged(Id.GROUP_CONTROLS)

    def _current_width(self) -> int:
        try:
            dimensions = self.GetItemDim(1000)
            if isinstance(dimensions, dict):
                width = int(dimensions.get("w", 0))
            elif isinstance(dimensions, (tuple, list)) and len(dimensions) >= 3:
                width = int(dimensions[2])
            else:
                width = int(getattr(dimensions, "w", 0))
        except Exception:
            width = 0
        return width if width >= 100 else 340

    def _update_responsive_layout(self, force: bool = False) -> None:
        width = self._current_width()
        mode = "narrow" if width < 620 else "expanded"
        width_changed = (
            self.prompt_layout_width > 0
            and abs(width - self.prompt_layout_width) >= 4
        )
        if self.prompt_has_focus and not force:
            # Never adopt a width which may have been temporarily enlarged by
            # an unwrapped keystroke. Keep using the last stable width until
            # focus leaves the editor.
            if width_changed:
                self.prompt_rewrap_pending = True
            return
        if not force and mode == self.layout_mode and not width_changed:
            return
        previous_wrap_width = self.prompt_wrap_width
        self.prompt_wrap_width = width
        if force or width_changed:
            self.LayoutChanged(Id.PROMPT_GROUP)
        self.prompt_layout_width = width
        if (
            abs(width - previous_wrap_width) >= 4
            and not self.prompt_placeholder_active
            and self._prompt_value()
        ):
            self._rewrap_prompt_editor()
        self.layout_mode = mode
        self._apply_panel_visibility()

    def _apply_panel_visibility(self) -> None:
        # The sketch-led interface uses one vertical flow at every width. Controls
        # expand with the dock but never force a second or third column into C4D.
        self.LayoutChanged(1002)

    def _selected_label(self, control_id: int, values) -> str:
        selected = max(1, self.GetInt32(control_id))
        sequence = list(values)
        return sequence[min(len(sequence), selected) - 1]

    def _sync_form(self) -> None:
        self.state.prompt = self._prompt_value()
        # The compact dock keeps the optional negative prompt out of the main
        # flow so the complete generation setup fits on one screen.
        self.state.options.negative_prompt = ""
        selected_model = max(1, self.GetInt32(Id.MAIN_MODEL))
        if self.main_model_values:
            model_key = "video_model" if self.state.workflow == "animation" else "model"
            self.settings[model_key] = self.main_model_values[
                min(len(self.main_model_values), selected_model) - 1
            ]
        self.state.options.aspect = self._selected_label(
            Id.ASPECT, self.aspect_control_values
        )
        if self.state.workflow == "animation":
            self.state.options.duration_seconds = int(
                self._selected_label(Id.QUALITY, self.animation_duration_values)
            )
            self.state.options.video_resolution = str(
                self._selected_label(Id.COUNT, self.animation_resolution_values)
            )
            self.state.options.count = 1
            self.state.options.quality = "视频"
        else:
            self.state.options.quality = self._selected_label(
                Id.QUALITY, QUALITY_PRESETS.keys()
            )
            self.state.options.count = max(1, self.GetInt32(Id.COUNT))
        self.state.options.preserve_composition = self.GetBool(Id.PRESERVE)

    def _selected_prompt_model(self) -> str:
        selected = max(1, self.GetInt32(Id.PROMPT_MODEL))
        return self.prompt_model_values[
            min(len(self.prompt_model_values), selected) - 1
        ]

    def _selected_prompt_skill(self) -> str:
        selected = max(1, self.GetInt32(Id.PROMPT_SKILL))
        return self.prompt_skill_values[
            min(len(self.prompt_skill_values), selected) - 1
        ]

    def _prompt_input_images(self) -> tuple[str, str, list[str]]:
        scene = str(self.state.scene_image or "").strip()
        if _is_video_path(scene):
            scene = next(
                (
                    str(path)
                    for path in self.state.animation_frames
                    if _is_image_path(str(path)) and Path(str(path)).is_file()
                ),
                "",
            )
        depth = str((self.state.scene_context or {}).get("depth_image") or "").strip()
        if depth and not Path(depth).is_file():
            depth = ""

        prepared_scene = _portable_api_image(scene, self.capture_dir) if scene else ""
        prepared_depth = (
            _portable_api_image(depth, self.capture_dir)
            if prepared_scene and depth
            else ""
        )
        prepared_references = []
        for path in self.state.references:
            value = str(path or "").strip()
            if not value:
                continue
            if _is_video_path(value):
                raise ValueError("提示词模型暂不读取视频参考，请上传静态参考图。")
            prepared_references.append(_portable_api_image(value, self.capture_dir))
        return prepared_scene, prepared_depth, prepared_references

    def _refresh_prompt_ai_controls(self) -> None:
        interaction_busy = self.state.busy or self.state.render_busy
        has_input = bool(self.state.scene_image or self.state.references)
        self.Enable(Id.PROMPT_MODEL, not interaction_busy and not self.prompt_busy)
        self.Enable(Id.PROMPT_SKILL, not interaction_busy and not self.prompt_busy)
        self.Enable(
            Id.PROMPT_AI_GENERATE,
            not interaction_busy and (self.prompt_busy or has_input),
        )
        self.SetString(
            Id.PROMPT_AI_GENERATE,
            "停止生成" if self.prompt_busy else "AI 生成提示词",
        )

    def _start_prompt_generation(self) -> None:
        if self.prompt_busy:
            self._cancel_prompt_generation()
            return
        if self.state.busy or self.state.render_busy or self.prompt_busy:
            return
        try:
            scene, depth, references = self._prompt_input_images()
        except ValueError as exc:
            c4d.gui.MessageDialog(str(exc))
            return
        if not scene and not references:
            c4d.gui.MessageDialog("请先点击“渲染加载”，或上传至少一张参考图。")
            return

        model = self._selected_prompt_model()
        self.settings["prompt_model"] = model
        self.settings_store.save(self.settings)
        worker = PromptGenerationThread(
            PromptGenerator(self.settings),
            model,
            scene,
            depth,
            references,
            self._prompt_value(),
            self._selected_prompt_skill(),
        )
        self.prompt_worker = worker
        self.prompt_busy = True
        self._set_status("AI 正在分析白膜、深度与参考图…", 0.05)
        self._refresh_prompt_ai_controls()
        worker.Start()

    def _cancel_prompt_generation(self) -> None:
        worker = self.prompt_worker
        if worker is not None:
            worker.cancel()
        self._set_status("正在停止提示词生成…")

    def _finish_prompt_generation(self) -> None:
        worker = self.prompt_worker
        if worker is None:
            return
        _progress, _message, done = worker.snapshot()
        if not done:
            return
        self.prompt_worker = None
        self.prompt_busy = False
        self._refresh_prompt_ai_controls()
        if worker.cancelled:
            self._set_status("已停止提示词生成", 0.0)
            return
        if worker.error:
            self._set_status("提示词生成失败", 0.0)
            c4d.gui.MessageDialog("提示词生成失败：\n{}".format(worker.error))
            return
        self._set_prompt_text(worker.result)
        self.state.prompt = worker.result
        self._set_status("提示词已生成，请确认后再点击立即生成", 1.0)

    def _resolved_aspect(self) -> str:
        label = self.state.options.aspect
        if label != "自动":
            return label
        bitmap = _load_bitmap(self.state.scene_image)
        if bitmap is None and self.state.workflow == "animation":
            for frame in self.state.animation_frames:
                bitmap = _load_bitmap(frame)
                if bitmap is not None:
                    break
            if bitmap is None:
                bitmap = _load_preview_bitmap(self._animation_source_movie())
        if bitmap is None:
            context = (
                self.state.scene_context
                if isinstance(self.state.scene_context, dict)
                else {}
            )
            capture_size = context.get("capture_size")
            if isinstance(capture_size, (tuple, list)) and len(capture_size) >= 2:
                try:
                    ratio = float(capture_size[0]) / float(capture_size[1])
                except (TypeError, ValueError, ZeroDivisionError):
                    ratio = 0.0
                if ratio > 0:
                    return min(
                        ASPECT_PRESETS,
                        key=lambda name: abs(
                            math.log(
                                ratio
                                / (
                                    ASPECT_PRESETS[name][0]
                                    / float(ASPECT_PRESETS[name][1])
                                )
                            )
                        ),
                    )
        if bitmap is None or bitmap.GetBh() <= 0:
            return "1:1"
        ratio = bitmap.GetBw() / float(bitmap.GetBh())
        return min(
            ASPECT_PRESETS,
            key=lambda name: abs(
                math.log(ratio / (ASPECT_PRESETS[name][0] / float(ASPECT_PRESETS[name][1])))
            ),
        )

    def _refresh_reference_controls(self) -> None:
        scene_path = self.state.scene_image
        if self.state.workflow == "animation":
            scene_path = self._animation_source_movie() or scene_path
        self.reference_area.set_references(
            scene_path,
            self.state.references,
            self.state.selected_reference,
            self.state.workflow,
        )

    def _refresh_history(self) -> None:
        self.history_area.set_history(self.history.items, self.state.selected_history_id)
        can_delete = bool(self.state.selected_history_id)
        self.Enable(Id.HISTORY_DELETE, can_delete)
        self.fold_titles[Id.FOLD_HISTORY] = "历史 · {} 条 · 双击缩略图看大图".format(
            len(self.history.items)
        )
        folded = self.folded.get(Id.FOLD_HISTORY, False)
        self.SetString(
            Id.FOLD_HISTORY,
            "{}  {}".format("▸" if folded else "▾", self.fold_titles[Id.FOLD_HISTORY]),
        )

    def _animation_source_movie(self) -> str:
        """Return the real white-model MP4 even when an older snapshot kept a PNG."""

        context = self.state.scene_context if isinstance(self.state.scene_context, dict) else {}
        candidates = (
            self.state.scene_image,
            str(context.get("animation_movie") or ""),
        )
        for candidate in candidates:
            if candidate and _is_video_path(candidate) and Path(candidate).is_file():
                return str(candidate)
        return ""

    def _animation_reference_sample_count(self) -> int:
        """Choose an ordered-frame density supported by the active model."""

        model = str(self.settings.get("video_model") or "")
        provider = str(self.settings.get("provider") or "").strip().lower()
        duration = max(5, int(self.state.options.duration_seconds))
        mentioned_references = len(referenced_indices(self._prompt_value()))
        if provider == "apimart":
            # APIMART currently accepts our local MP4 through ordered stills.
            # A two-frames-per-second sequence made short Seedance 2.5 jobs
            # unnecessarily heavy and often left them at the provider's 50%
            # preprocessing stage. Five to twelve evenly sampled guide frames
            # preserve start/middle/end motion while cutting upload and model
            # preprocessing work substantially.
            if is_seedance_25_model(model):
                desired = min(12, max(5, int(math.ceil(duration / 2.0)) + 3))
                return max(1, min(desired, 50 - mentioned_references))
            if "fast" in model.lower():
                desired = 5 if duration <= 8 else 7
                return max(1, min(desired, 9 - mentioned_references))
        if is_seedance_25_model(model):
            desired = min(
                30,
                max(11, duration * 2 + 1),
            )
            return max(1, min(desired, 50 - mentioned_references))
        desired = 9 if duration >= 10 else 7
        return max(1, min(desired, 9 - mentioned_references))

    def _refresh_preview(self) -> None:
        self.scene_capture_area.set_scene(self.state.scene_image)
        source_movie = (
            self._animation_source_movie()
            if self.state.workflow == "animation"
            else ""
        )
        preview_scene = "" if self.preview_dismissed else (
            source_movie or self.state.scene_image
        )
        preview_result = "" if self.preview_dismissed else self.state.current_result
        self.compact_preview_area.set_images(preview_scene, preview_result)
        can_preview = bool(preview_scene or preview_result)
        both_images = bool(
            preview_scene
            and preview_result
            and not _is_video_path(preview_scene)
            and not _is_video_path(preview_result)
        )
        both_videos = bool(
            preview_scene
            and preview_result
            and _is_video_path(preview_scene)
            and _is_video_path(preview_result)
        )
        can_compare = both_images or both_videos
        if not can_compare:
            self.state.compare_enabled = False
        self.Enable(Id.COMPACT_COMPARE, can_compare)
        self.SetBool(Id.COMPACT_COMPARE, self.state.compare_enabled)
        self.compact_preview_area.set_compare(
            self.state.compare_enabled, self.compare_position
        )

    def _set_status(self, text: str, progress: Optional[float] = None) -> None:
        self.state.status = text
        self.header_area.set_status(
            text, self.state.busy or self.state.render_busy or self.prompt_busy
        )
        if progress is not None:
            self.state.progress = max(0.0, min(1.0, float(progress)))
        percent = int(round(self.state.progress * 100.0))
        if self.state.busy:
            if "准备" in text:
                stage = "准备中"
            elif "编码" in text or "上传" in text or "提交" in text:
                stage = "上传中"
            elif "下载" in text or "保存" in text:
                stage = "保存中"
            elif "任务" in text or "AI" in text or "第 " in text:
                stage = "AI生成中"
            else:
                stage = "生成中"
            elapsed = max(0, int(time.monotonic() - self.generation_started_at)) if self.generation_started_at else 0
            apimart_progress = re.search(r"APIMART 平台进度\s*(\d+)%", text)
            apimart_query = re.search(r"第\s*(\d+)\s*次", text)
            if apimart_progress:
                if "同步视频地址" in text:
                    stage = "APIMART结果同步中"
                elif "持续查询" in text or "暂未变化" in text:
                    stage = "APIMART持续处理中"
                else:
                    stage = "APIMART生成中"
                label = "{} · {}% · {}秒".format(
                    stage, apimart_progress.group(1), elapsed
                )
                if apimart_query:
                    label += " · 查询{}次".format(apimart_query.group(1))
            elif "APIMART 正在处理" in text and apimart_query:
                label = "APIMART持续处理中 · {}秒 · 查询{}次".format(
                    elapsed, apimart_query.group(1)
                )
            else:
                label = "{} · {}% · {}秒".format(stage, percent, elapsed)
        elif "失败" in text or "错误" in text:
            label = "↻  重试生成"
        elif self.state.progress >= 1.0 or "完成" in text or "已生成" in text:
            label = "✓  完成 · 再次生成"
        else:
            label = "✦  立即生成"
        self.generate_button_area.set_label(label)
        interaction_ready = (
            not self.state.busy and not self.state.render_busy and not self.prompt_busy
        )
        self.generate_button_area.set_enabled(interaction_ready)
        self.render_button_area.set_enabled(interaction_ready)
        self.upload_button_area.set_enabled(interaction_ready)
        self.paste_button_area.set_enabled(interaction_ready)
        self.workflow_mode_area.set_enabled(interaction_ready)
        self.cancel_button_area.set_enabled(bool(self.state.busy))
        self._refresh_prompt_ai_controls()

    def _recover_interaction_state(self) -> bool:
        """Release controls when a C4D completion event was delayed or lost."""

        recovered = False
        render_worker = self.render_worker
        if render_worker is not None:
            try:
                render_done = bool(render_worker.snapshot()[2])
            except (AttributeError, RuntimeError, TypeError):
                render_done = True
            if render_done:
                self._finish_render_load()
                recovered = True
        elif self.state.render_busy:
            # No render task exists, so keeping the panel disabled can never
            # make progress. This also repairs state left by a host exception.
            self.state.render_busy = False
            self.render_started_at = 0.0
            self.render_button_area.set_progress(None)
            self.render_button_area.set_label("▣  渲染加载")
            recovered = True

        worker = self.state.worker
        if worker is not None:
            try:
                worker_done = bool(worker.snapshot()[2])
            except (AttributeError, RuntimeError, TypeError):
                worker_done = False
            if worker_done:
                self._finish_generation()
                recovered = True
        elif self.state.busy and not self.pending_generation:
            self.state.busy = False
            self.generation_started_at = 0.0
            recovered = True

        prompt_worker = self.prompt_worker
        if prompt_worker is not None:
            try:
                prompt_done = bool(prompt_worker.snapshot()[2])
            except (AttributeError, RuntimeError, TypeError):
                prompt_done = False
            if prompt_done:
                self._finish_prompt_generation()
                recovered = True
        elif self.prompt_busy:
            self.prompt_busy = False
            recovered = True

        if (
            recovered
            and not self.state.busy
            and not self.state.render_busy
            and not self.prompt_busy
        ):
            if self.state.status not in {
                "生成失败",
                "C4D 渲染失败",
                "画布尺寸校验失败",
            }:
                self._set_status(self.state.status or "界面已恢复，可继续操作")
            else:
                self._set_status(self.state.status, self.state.progress)
        return recovered

    def _choose_image(self, title: str) -> str:
        return c4d.storage.LoadDialog(type=c4d.FILESELECTTYPE_IMAGES, title=title) or ""

    def _choose_scene_media(self) -> None:
        """Load an image or movie directly into the automatic scene slot."""

        path = c4d.storage.LoadDialog(
            type=c4d.FILESELECTTYPE_ANYTHING,
            title="选择场景图片或动画视频",
        ) or ""
        if not path:
            return
        media_path = Path(path)
        if not media_path.is_file() or not (
            _is_image_path(path) or _is_video_path(path)
        ):
            c4d.gui.MessageDialog(
                "请选择图片或视频文件。\n"
                "支持 PNG、JPG、WebP、TIFF、EXR、MP4、MOV、WebM、AVI、MKV。"
            )
            return
        if _is_video_path(path) and self.state.workflow != "animation":
            self._switch_workflow("animation")
            if self.state.workflow != "animation":
                return
        self.state.scene_image = str(media_path)
        if _is_video_path(path):
            sample_count = (
                self._animation_reference_sample_count()
                if str(self.settings.get("animation_reference_mode") or "") == "frames"
                else 5
            )
            self.state.animation_frames = _sample_movie_reference_frames(
                str(media_path), self.capture_dir, sample_count
            )
        else:
            self.state.animation_frames = []
        self.state.scene_context = {
            "source": "imported_video" if _is_video_path(path) else "imported_image",
            "workflow": self.state.workflow,
        }
        if _is_video_path(path):
            self.state.scene_context["animation_movie"] = str(media_path)
            self.state.scene_context["animation_source_kind"] = "uploaded_video"
            width, height, fps = _movie_reference_info(str(media_path))
            if width > 0 and height > 0:
                self.state.scene_context["capture_size"] = [width, height]
                self.state.scene_context["capture_aspect"] = round(
                    width / float(height), 6
                )
            if fps > 0:
                self.state.scene_context["animation_source_fps"] = fps
        self.state.current_result = ""
        self.state.compare_enabled = False
        self.preview_dismissed = False
        self._refresh_reference_controls()
        self._refresh_preview()
        self._set_status(
            "MP4 动画已加载为自动参考，提示词会直接作用于它；普通参考图仍需 @ 引用"
            if self.state.workflow == "animation"
            else "场景图已设为第1张自动参考，并锁定原始比例；普通参考图仍需 @ 引用",
            0.0,
        )

    def _import_scene(self) -> None:
        self._choose_scene_media()

    def _show_bitmap_in_viewer(
        self,
        path: str,
        description: str = "图片",
        bitmap: Optional[c4d.bitmaps.BaseBitmap] = None,
    ) -> bool:
        """Open an image in Cinema 4D's native picture viewer."""

        bitmap = bitmap or _load_bitmap(path)
        if bitmap is None:
            c4d.gui.MessageDialog("无法读取{}：{}".format(description, path))
            return False
        try:
            c4d.bitmaps.ShowBitmap(bitmap)
        except (AttributeError, RuntimeError, TypeError) as exc:
            c4d.gui.MessageDialog("无法在 C4D 图片查看器中打开{}：{}".format(description, exc))
            return False
        return True

    def _render_view_reference(self) -> None:
        """Start a real C4D image/animation render without opening Picture Viewer."""

        self._recover_interaction_state()
        if self.state.busy or self.state.render_busy or self.prompt_busy:
            return
        self._sync_form()
        sample_count = 5
        if self.state.workflow == "animation":
            sample_count = 9 if self.state.options.duration_seconds >= 10 else 7
        self.state.render_busy = True
        self.render_started_at = time.monotonic()
        self.render_worker = RenderLoadTask(
            self.capture_dir,
            self.state.workflow,
            sample_count=sample_count,
            model=str(self.settings.get("model") or ""),
        )
        self._set_render_progress(
            0.01,
            "准备创建动画预览" if self.state.workflow == "animation" else "准备渲染",
        )
        self._set_status(
            "正在加载 C4D {}…".format(
                "动画预览 MP4" if self.state.workflow == "animation" else "当前场景"
            ),
            0.0,
        )
        # Active-document rendering must stay on C4D's main thread. The native
        # renderer still reports its own progress through our callback; only
        # external API/network work is delegated to a background thread.
        try:
            c4d.StopAllThreads()
            self.render_worker.run()
        except Exception as exc:
            # StopAllThreads itself can raise before RenderLoadTask.run() gets
            # a chance to set ``done``. Convert that host failure into the same
            # recoverable result path so the controls are never left disabled.
            self.render_worker.error = str(exc)
            self.render_worker.done = True
        finally:
            self._finish_render_load()

    def _set_render_progress(self, value: float, message: str) -> None:
        progress = max(0.0, min(1.0, float(value)))
        percent = int(round(progress * 100.0))
        elapsed = max(0.0, time.monotonic() - self.render_started_at)
        remaining = ""
        if 0.03 <= progress < 1.0 and elapsed >= 0.5:
            seconds = int(round(elapsed * (1.0 - progress) / progress))
            if seconds > 0:
                remaining = " {}s".format(min(3599, seconds))
        label = "{}%{}".format(percent, remaining)
        if self.state.workflow == "animation" and "动画帧" in message:
            frame_label = message.split(" · ", 1)[0]
            label = "{} {}".format(frame_label, label)
        self.render_button_area.set_label(label)
        self.render_button_area.set_progress(progress)
        self.render_button_area.set_enabled(False)
        try:
            c4d.StatusSetBar(percent)
            c4d.StatusSetText("{} · {}".format(message, label))
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _finish_render_load(self) -> None:
        worker = self.render_worker
        if worker is None:
            return
        _progress, _message, done = worker.snapshot()
        if not done:
            return
        self.render_worker = None
        self.state.render_busy = False
        self.render_started_at = 0.0
        try:
            c4d.StatusClear()
        except (AttributeError, RuntimeError):
            pass
        if worker.error:
            self.render_button_area.set_progress(None)
            self.render_button_area.set_label("↻  重新渲染")
            self._set_status("C4D 渲染失败", 0.0)
            c4d.gui.MessageDialog(
                "无法完成当前 C4D 渲染。\n"
                "请检查当前相机、动画范围、渲染器和渲染设置。\n\n{}".format(
                    worker.error
                )
            )
            return
        if not worker.paths:
            self.render_button_area.set_progress(None)
            self.render_button_area.set_label("↻  重新渲染")
            self._set_status("C4D 没有返回渲染结果", 0.0)
            return
        self.state.scene_image = worker.paths[0]
        if self.state.workflow == "animation" and _is_video_path(worker.paths[0]):
            prepared_frames = [
                path
                for path in worker.paths[1:]
                if _is_image_path(path) and Path(path).is_file()
            ]
            self.state.animation_frames = prepared_frames or _sample_movie_reference_frames(
                worker.paths[0], self.capture_dir, 5
            )
        else:
            self.state.animation_frames = (
                list(worker.paths) if self.state.workflow == "animation" else []
            )
        # A newly rendered scene becomes the next generation's source. Clear a
        # previous AI result so the preview never compares unrelated takes.
        self.state.current_result = ""
        self.state.compare_enabled = False
        self.preview_dismissed = False
        self.state.scene_context = dict(worker.context)
        self.state.scene_context["workflow"] = self.state.workflow
        if self.state.workflow == "animation":
            if _is_video_path(worker.paths[0]):
                self.state.scene_context["animation_movie"] = worker.paths[0]
                self.state.scene_context["animation_source_kind"] = "c4d_full_render"
            preview_fps = worker.context.get("animation_preview_fps")
            try:
                self.state.options.fps = max(1.0, float(preview_fps))
            except (TypeError, ValueError):
                pass
        self._refresh_reference_controls()
        self._refresh_preview()
        self.render_button_area.set_progress(1.0)
        self.render_button_area.set_label("✓  渲染完成")
        self._set_status(
            (
                (
                    "MP4 动画已渲染并自动放入动画槽；提示词会直接作用于它"
                    if _is_video_path(worker.paths[0])
                    else "动画加载完成：{} 个白模动画帧会自动应用提示词；普通参考图仍需 @ 引用".format(
                        len(worker.paths)
                    )
                )
                if self.state.workflow == "animation"
                else "场景加载完成：已作为第1张自动参考并锁定原始比例；普通参考图仍需 @ 引用"
            ),
            0.0,
        )

    def _add_reference(self) -> None:
        if len(self.state.references) >= 8:
            c4d.gui.MessageDialog("最多可添加 8 张参考图。")
            return
        path = self._choose_image("添加参考图")
        if path:
            self.state.add_reference(path)
            self._refresh_reference_controls()

    def _paste_reference(self) -> None:
        if len(self.state.references) >= 8:
            c4d.gui.MessageDialog("最多可添加 8 张参考图。")
            return
        system = platform.system()
        if system not in {"Windows", "Darwin"}:
            c4d.gui.MessageDialog("当前系统无法直接读取剪贴板图片，请使用“＋上传参考”。")
            return
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".png" if system == "Windows" else ".tiff"
        target = self.capture_dir / "clipboard_{}{}".format(
            int(time.time() * 1000), suffix
        )
        try:
            if system == "Windows":
                environment = os.environ.copy()
                environment["ZHIREAI_CLIPBOARD_IMAGE"] = str(target)
                script = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$img=[System.Windows.Forms.Clipboard]::GetImage(); "
                    "if ($null -eq $img) { exit 2 }; "
                    "$img.Save($env:ZHIREAI_CLIPBOARD_IMAGE, "
                    "[System.Drawing.Imaging.ImageFormat]::Png); $img.Dispose()"
                )
                command = [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-Command",
                    script,
                ]
                completed = subprocess.run(
                    command,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
            else:
                # macOS exposes copied bitmap pixels to AppleScript as TIFF.
                # Passing the destination as argv avoids shell quoting issues
                # for user names and folders containing Chinese characters.
                script = (
                    "on run argv\n"
                    "set targetPath to item 1 of argv\n"
                    "set imageData to the clipboard as TIFF picture\n"
                    "set targetFile to open for access POSIX file targetPath with write permission\n"
                    "set eof targetFile to 0\n"
                    "write imageData to targetFile\n"
                    "close access targetFile\n"
                    "end run"
                )
                completed = subprocess.run(
                    ["osascript", "-e", script, str(target)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            c4d.gui.MessageDialog("读取剪贴板失败：{}".format(exc))
            return
        if completed.returncode != 0 or not target.is_file():
            c4d.gui.MessageDialog("剪贴板中没有可用图片，请先复制一张图片。")
            return
        self.state.add_reference(str(target))
        self._refresh_reference_controls()
        self._set_status("已粘贴参考图")

    def _clear_references(self) -> None:
        if not self.state.references:
            return
        if not c4d.gui.QuestionDialog("清空全部参考图？"):
            return
        prompt = self._prompt_value()
        for number in range(1, len(self.state.references) + 1):
            prompt = prompt.replace(image_token(number), "")
        self.state.references = []
        self.state.selected_reference = -1
        self._set_prompt_text(prompt)
        self._refresh_reference_controls()

    def _delete_reference(self) -> None:
        selected = self.state.selected_reference
        if not (0 <= selected < len(self.state.references)):
            return
        removed_path = self.state.delete_selected_reference()
        # Remove the deleted token and reindex later image references.
        prompt = self._prompt_value()
        removed_number = selected + 1
        prompt = prompt.replace(image_token(removed_number), "")
        for number in range(removed_number + 1, len(self.state.references) + 2):
            prompt = prompt.replace(image_token(number), "__LUMA_TOKEN_{}__".format(number - 1))
        for number in range(removed_number, len(self.state.references) + 1):
            prompt = prompt.replace("__LUMA_TOKEN_{}__".format(number), image_token(number))
        self._set_prompt_text(prompt)
        self._refresh_reference_controls()
        self._refresh_preview()

    def _insert_reference_token(self) -> None:
        if not self.state.references:
            return
        index = max(1, min(len(self.state.references), self.GetInt32(Id.REFERENCE_TOKEN)))
        text = self._prompt_value()
        spacer = "" if not text or text.endswith((" ", "\n")) else " "
        text = text + spacer + image_token(index) + " "
        self._set_prompt_text(text)
        self.Activate(Id.PROMPT)

    def _open_settings(self) -> None:
        dialog = ApiSettingsDialog(self.settings)
        dialog.Open(c4d.DLG_TYPE_MODAL_RESIZEABLE, PLUGIN_ID, defaultw=680, defaulth=560)
        if dialog.saved:
            self.settings = merge_settings(dialog.settings)
            self.settings_store.save(self.settings)
            self._populate_parameter_controls()
            self._refresh_api_badge()
            self._set_status("API 设置已保存")

    def _refresh_all(self) -> None:
        self.settings = merge_settings(self.settings_store.load())
        self.history = HistoryStore(
            self.data_dir / "history.json", int(self.settings.get("history_limit") or 200)
        )
        self._populate_parameter_controls()
        self._refresh_api_badge()
        self._refresh_reference_controls()
        self._refresh_history()
        self._refresh_preview()
        self._set_status("界面与配置已刷新", 0.0)

    def _job(self) -> dict:
        self._sync_form()
        errors = validate_tokens(self.state.prompt, len(self.state.references))
        if errors:
            raise ValueError("\n".join(errors))
        if not self.state.prompt.strip():
            raise ValueError("请先填写提示词。")
        if not str(self.settings.get("base_url") or "").strip():
            raise ValueError("请先完成 API 设置。")
        force_animation_frames = False
        animation_delivery_reason = ""
        if self.state.workflow == "animation":
            # The full-render MP4 is authoritative.  Older live state/history
            # may still carry the preceding PNG in scene_image while the real
            # movie is retained in scene_context.animation_movie.
            source_movie = self._animation_source_movie()
            if source_movie:
                self.state.scene_image = source_movie
        # Keep all providers on the same portable input contract. Cinema 4D
        # can display TIFF/EXR/HDR/WebP, while several image APIs cannot ingest
        # those formats directly. Conversion preserves width, height and the
        # scene/reference ordering used by the composition lock.
        if self.state.scene_image and _is_image_path(self.state.scene_image):
            self.state.scene_image = _portable_api_image(
                self.state.scene_image, self.capture_dir
            )
        if self.state.references:
            self.state.references = [
                _portable_api_image(path, self.capture_dir)
                for path in self.state.references
            ]
        if self.state.workflow == "animation":
            animation_ready, animation_reason = self._animation_availability()
            if not animation_ready:
                raise ValueError(animation_reason)
            if not self.state.animation_frames and not self.state.scene_image:
                raise ValueError("请先点击动画缩略图上传视频/图片，或点击“渲染加载”。")
            # Keep MP4 as the user's primary animation input.  APIMART exposes
            # an image-upload endpoint but no local-video upload endpoint, so
            # its Seedance adapter prepares references from the MP4 internally.
            # Retry here as well: immediately rendered movies can still be
            # finishing their file close when the thumbnail is first loaded.
            reference_mode = str(
                self.settings.get("animation_reference_mode") or ""
            )
            provider = str(self.settings.get("provider") or "").strip().lower()
            movie_width = movie_height = 0
            movie_fps = 0.0
            if _is_video_path(self.state.scene_image):
                movie_width, movie_height, movie_fps = _movie_reference_info(
                    self.state.scene_image
                )
                if movie_width <= 0 or movie_height <= 0:
                    raise ValueError(
                        "C4D 无法读取这个 MP4 的画面尺寸。\n"
                        "请确认它能在 C4D 图片查看器中播放，建议转换为 H.264 MP4 后重试。"
                    )
                try:
                    seedance_reference_dimensions(movie_width, movie_height)
                except ValueError as exc:
                    raise ValueError(
                        "这个动画画幅无法在不改变构图的情况下用于 Seedance：\n{}"
                        .format(exc)
                    ) from exc
            if (
                _is_video_path(self.state.scene_image)
                and reference_mode == "frames"
            ):
                self.state.animation_frames = [
                    path
                    for path in self.state.animation_frames
                    if _is_image_path(path) and Path(path).is_file()
                ]
                sample_count = self._animation_reference_sample_count()
                if (
                    len(self.state.animation_frames) != sample_count
                    or not _reference_frames_are_seedance_safe(
                        self.state.animation_frames
                    )
                ):
                    prepared = _sample_movie_reference_frames(
                        self.state.scene_image, self.capture_dir, sample_count
                    )
                    if prepared:
                        self.state.animation_frames = prepared
                if not self.state.animation_frames:
                    raise ValueError(
                        "已收到 MP4 动画，但 C4D 暂时无法读取其中的画面。\n"
                        "请确认该 MP4 能在 C4D 图片查看器中正常播放；"
                        "建议使用 H.264 编码后重新上传。"
                    )
            elif (
                _is_video_path(self.state.scene_image)
                and provider == "atlascloud"
                and reference_mode == "video"
            ):
                # Atlas can accept the MP4 directly, but Seedance rejects a
                # movie below/above its pixel or FPS limits.  Old V2.0.5 C4D
                # previews were 720x405 and therefore affected.  Preserve the
                # same source animation by automatically delivering evenly
                # sampled, resized frames when the direct movie is not legal.
                animation_delivery_reason = seedance_reference_issue(
                    movie_width,
                    movie_height,
                    movie_fps if movie_fps > 0 else None,
                )
                try:
                    movie_too_large = (
                        Path(self.state.scene_image).stat().st_size
                        > SEEDANCE_REFERENCE_MAX_BYTES
                    )
                except OSError:
                    movie_too_large = False
                if movie_too_large:
                    animation_delivery_reason = (
                        "动画参考视频超过 50MB，已自动改用合规参考帧。"
                    )
                if animation_delivery_reason:
                    sample_count = min(9, self._animation_reference_sample_count())
                    prepared = _sample_movie_reference_frames(
                        self.state.scene_image, self.capture_dir, sample_count
                    )
                    if not prepared or not _reference_frames_are_seedance_safe(prepared):
                        raise ValueError(
                            "动画参考视频不符合 Seedance 的输入要求，并且自动校正失败：\n{}"
                            .format(animation_delivery_reason)
                        )
                    self.state.animation_frames = prepared
                    force_animation_frames = True
            # Apply the same geometry contract to non-movie fallbacks, direct
            # still animation inputs and @-mentioned visual references. This
            # closes every provider path, including discovered custom
            # Seedance gateways, instead of fixing only Atlas' MP4 branch.
            if self.state.scene_image and _is_image_path(self.state.scene_image):
                self.state.scene_image = _seedance_safe_reference_image(
                    self.state.scene_image, self.capture_dir
                )
            if self.state.animation_frames:
                self.state.animation_frames = [
                    _seedance_safe_reference_image(path, self.capture_dir)
                    for path in self.state.animation_frames
                    if _is_image_path(path) and Path(path).is_file()
                ]
            if self.state.references:
                self.state.references = [
                    _seedance_safe_reference_image(path, self.capture_dir)
                    for path in self.state.references
                ]
        tracks_active_provider = any(
            key in self.settings
            for key in (
                "last_connected_provider",
                "connected_providers",
                "provider_connection_states",
            )
        )
        if tracks_active_provider and not str(
            self.settings.get("last_connected_provider") or ""
        ).strip():
            raise ValueError("请先在 API 设置中点击“连接并使用”。")
        if (
            str(self.settings.get("provider") or "") == "atlascloud"
            and not self.state.scene_image
            and not self.state.references
        ):
            raise ValueError("请先点击“渲染加载”，或手动添加一张参考图。")
        aspect_auto = self.state.options.aspect == "自动"
        aspect = self._resolved_aspect()
        dimension_quality = (
            "1K" if self.state.workflow == "animation" else self.state.options.quality
        )
        width, height = output_dimensions(aspect, dimension_quality)
        scene_context = dict(self.state.scene_context)
        scene_context["preserve_composition"] = self.state.options.preserve_composition
        if force_animation_frames:
            scene_context["animation_reference_delivery"] = "normalized_frames"
            scene_context["animation_reference_fallback_reason"] = (
                animation_delivery_reason
            )
        if aspect_auto:
            bitmap = _load_bitmap(self.state.scene_image)
            if bitmap is not None and bitmap.GetBw() > 0 and bitmap.GetBh() > 0:
                source_width = int(bitmap.GetBw())
                source_height = int(bitmap.GetBh())
                source_ratio = source_width / float(source_height)
                if (
                    self.state.workflow == "image"
                    and self.state.options.preserve_composition
                    and source_width <= 16000
                    and source_height <= 16000
                ):
                    # Locked image editing uses the C4D render itself as the
                    # authoritative canvas instead of rounding it to the
                    # nearest 1K/2K/4K preset.
                    width, height = source_width, source_height
                else:
                    width, height = output_dimensions_for_ratio(
                        source_ratio, dimension_quality
                    )
                scene_context["capture_size"] = [source_width, source_height]
                scene_context["capture_aspect"] = round(source_ratio, 6)
        return {
            "workflow": self.state.workflow,
            "model": self.settings.get(
                "video_model" if self.state.workflow == "animation" else "model", ""
            ),
            "prompt": self.state.prompt,
            "negative_prompt": self.state.options.negative_prompt,
            "width": width,
            "height": height,
            "count": self.state.options.count,
            "scene_image": (
                "" if force_animation_frames else self.state.scene_image
            ),
            "references": list(self.state.references),
            "animation_frames": list(self.state.animation_frames),
            "scene_context": scene_context,
            "aspect": aspect,
            "aspect_auto": aspect_auto,
            "quality": self.state.options.quality,
            "duration": self.state.options.duration_seconds,
            "fps": (
                scene_context.get("animation_preview_fps")
                or self.state.options.fps
            ),
            "video_resolution": self.state.options.video_resolution,
        }

    def _start_generation(self) -> None:
        if self.state.busy or self.state.render_busy or self.prompt_busy:
            return
        self._sync_form()
        if not self.state.prompt.strip():
            c4d.gui.MessageDialog("请先填写提示词。")
            return
        if not str(self.settings.get("base_url") or "").strip():
            c4d.gui.MessageDialog("请先完成 API 设置。")
            return
        if not self.state.scene_image and not self.state.references:
            c4d.gui.MessageDialog(
                "请先点击“渲染加载”，\n"
                "或手动上传一张参考图后再生成。"
            )
            return
        if (
            self.state.workflow == "animation"
            and not self.state.animation_frames
            and not self.state.scene_image
        ):
            c4d.gui.MessageDialog(
                "当前还没有动画素材。\n请点击动画缩略图上传视频，或点击“渲染加载”。"
            )
            return
        # Defer worker startup by one timer tick so Cinema 4D can paint the
        # first progress state. 生成阶段没有视图截取，不覆盖用户已确认的构图。
        self.state.busy = True
        self.state.last_error = ""
        self.pending_generation = True
        self.generation_started_at = time.monotonic()
        self._set_status("1/3 · 正在准备已确认的构图与参考图…", 0.01)

    def _cancel_generation(self) -> None:
        """Cancel the current local job and immediately restore the generate action."""

        if not self.state.busy and not self.pending_generation:
            return
        self.pending_generation = False
        worker = self.state.worker
        if worker is not None:
            worker.cancel()
            self.cancelled_workers.append(worker)
            self.state.worker = None
        self.state.busy = False
        self.state.last_error = ""
        self.generation_started_at = 0.0
        self._set_status("已取消生成，可重新开始", 0.0)

    def _continue_generation_from_references(self) -> None:
        try:
            job = self._job()
        except ValueError as exc:
            self.state.busy = False
            self.generation_started_at = 0.0
            self._set_status("生成准备失败", 0.0)
            c4d.gui.MessageDialog(str(exc))
            return
        client = ImageApiClient(self.settings, self.render_dir)
        worker = GenerationThread(client, job)
        self.state.worker = worker
        self._set_status("1/3 · 正在编码并上传已确认的构图与参考图…", 0.05)
        worker.Start()

    def _finish_generation(self) -> None:
        worker = self.state.worker
        if worker is None:
            return
        _progress, _status, done = worker.snapshot()
        if not done:
            return
        self.state.busy = False
        self.state.worker = None
        self.generation_started_at = 0.0
        if worker.error:
            self.state.last_error = worker.error
            self._set_status("生成失败", 0.0)
            c4d.gui.MessageDialog("生成失败：\n{}".format(worker.error))
            return
        self._sync_form()
        job = worker.job
        if str(job.get("workflow") or "image") == "animation":
            source_movie = self._animation_source_movie()
            if source_movie:
                self.state.scene_image = source_movie
        aspect = str(job.get("aspect") or "1:1")
        quality = str(job.get("quality") or self.state.options.quality)
        width = int(job.get("width") or 1024)
        height = int(job.get("height") or 1024)
        final_results = list(worker.results)
        # Preserve the provider's original file exactly.  Do not create a
        # second resized/cropped canvas for history or preview; this avoids the
        # visible aliasing introduced by the old ``*_canvas_WxH`` path.
        first_id = ""
        for result in final_results:
            item = self.history.add(
                prompt=self.state.prompt,
                scene_image=self.state.scene_image,
                scene_context=dict(job.get("scene_context") or self.state.scene_context),
                references=list(self.state.references),
                animation_frames=list(self.state.animation_frames),
                result=result,
                model=str(job.get("model") or self.settings.get("model", "")),
                provider=str(self.settings.get("provider") or "custom"),
                workflow=str(job.get("workflow") or "image"),
                aspect=aspect,
                aspect_setting=self.state.options.aspect,
                quality=quality,
                count=self.state.options.count,
                preserve_composition=self.state.options.preserve_composition,
                width=width,
                height=height,
                duration=int(job.get("duration") or 5),
                fps=float(job.get("fps") or 24.0),
                video_resolution=str(job.get("video_resolution") or "720P"),
            )
            if not first_id:
                first_id = item["id"]
        if final_results:
            self.state.current_result = final_results[0]
            self.preview_dismissed = False
            self.state.selected_history_id = first_id
            self.folded[Id.FOLD_HISTORY] = False
            self._apply_fold_state()
        self._refresh_history()
        self._refresh_preview()
        self._set_status(
            (
                "AI 动画已生成"
                if str(job.get("workflow") or "image") == "animation"
                else "已生成 {} 张效果图，已保留生图原尺寸".format(len(final_results))
            ),
            1.0,
        )

    def history_selected(self, item_id: str) -> None:
        item = self.history.find(item_id)
        if not item:
            return
        self.state.restore_history_snapshot(item)
        if self.state.workflow == "animation":
            source_movie = self._animation_source_movie()
            if source_movie:
                self.state.scene_image = source_movie
        self.preview_dismissed = False
        history_model = str(item.get("model") or "").strip()
        history_provider = str(item.get("provider") or "").strip()
        active_provider = str(self.settings.get("provider") or "").strip()
        same_provider = not history_provider or history_provider == active_provider
        if same_provider and history_model and (
            self.state.workflow == "image"
            or is_compatible_seedance_model(active_provider, history_model)
        ):
            model_key = "video_model" if self.state.workflow == "animation" else "model"
            self.settings[model_key] = history_model
        self._populate_parameter_controls()
        self._set_prompt_text(self.state.prompt)
        self._refresh_reference_controls()
        self._refresh_history()
        self._refresh_preview()
        media_paths = [
            self.state.current_result,
            self.state.scene_image,
            *self.state.references,
            *self.state.animation_frames,
        ]
        missing = sum(
            1 for path in media_paths if path and not Path(path).is_file()
        )
        if missing:
            self._set_status(
                "历史工作现场已恢复；{} 个本地素材已移动或删除".format(missing),
                0.0,
            )
        else:
            provider_note = ""
            if history_provider and not same_provider:
                provider_note = "；原记录由 {} 生成，当前 API 平台未改动".format(
                    PROVIDER_LABELS.get(history_provider, history_provider)
                )
            self._set_status(
                "历史工作现场已恢复：提示词、场景与参考图已同步{}".format(
                    provider_note
                ),
                0.0,
            )

    def history_open_requested(self, item_id: str) -> None:
        """Open history media without rebuilding or scrolling the main dialog."""

        item = next(
            (entry for entry in self.history.items if entry.get("id") == item_id),
            None,
        )
        path = str((item or {}).get("result") or "")
        if not path or not Path(path).is_file():
            c4d.gui.MessageDialog("该历史文件已移动或删除。")
            return
        if _is_video_path(path):
            self._open_animation_viewer(path)
        else:
            self._show_bitmap_in_viewer(path, "历史效果图")

    def history_reveal_requested(self, item_id: str) -> None:
        """Reveal one history result in Explorer/Finder without changing UI state."""

        item = next(
            (entry for entry in self.history.items if entry.get("id") == item_id),
            None,
        )
        path = str((item or {}).get("result") or "")
        self._reveal_file_in_folder(path)

    def reference_selected(self, index: int) -> None:
        self.state.selected_reference = index
        self._refresh_reference_controls()

    def copy_image_requested(self, path: str) -> None:
        copied, error = _copy_image_to_clipboard(path)
        if not copied:
            c4d.gui.MessageDialog(error)
            return
        self._set_status("原图已复制到剪贴板", 0.0)

    def scene_open_requested(self) -> None:
        source_movie = (
            self._animation_source_movie()
            if self.state.workflow == "animation"
            else ""
        )
        if not self.state.scene_image and not source_movie:
            return
        if source_movie:
            self._open_animation_viewer(source_movie)
            return
        if _is_video_path(self.state.scene_image):
            self._open_animation_viewer(self.state.scene_image)
            return
        if self.state.workflow == "animation" and self.state.animation_frames:
            if self.animation_preview_dialog is not None:
                try:
                    self.animation_preview_dialog.Close()
                except (AttributeError, RuntimeError):
                    pass
            self.animation_preview_dialog = AnimationPreviewDialog(
                self.state.animation_frames
            )
            self.animation_preview_dialog.Open(
                c4d.DLG_TYPE_ASYNC,
                PLUGIN_ID + 20,
                defaultw=760,
                defaulth=520,
            )
            return
        self._show_bitmap_in_viewer(
            self.state.scene_image,
            "动画参考图" if self.state.workflow == "animation" else "C4D 场景渲染图",
        )

    def scene_delete_requested(self) -> None:
        self.state.scene_image = ""
        self.state.scene_context = {}
        self.state.animation_frames = []
        self._refresh_reference_controls()
        self._refresh_preview()
        self._set_status("已清除 C4D 场景槽；普通参考图不受影响", 0.0)

    def scene_slot_requested(self) -> None:
        self._choose_scene_media()

    def reference_open_requested(self, index: int) -> None:
        """Open a clicked reference thumbnail in Cinema 4D's picture viewer."""

        if not (0 <= index < len(self.state.references)):
            return
        self.reference_selected(index)
        self._show_bitmap_in_viewer(
            self.state.references[index], "参考图{}".format(index + 1)
        )

    def reference_delete_requested(self, index: int) -> None:
        if 0 <= index < len(self.state.references):
            self.state.selected_reference = index
            self._delete_reference()

    def reference_slot_requested(self, index: int) -> None:
        """Treat every empty thumbnail slot as an upload target."""

        self._add_reference()

    def preview_position_changed(self, value: float) -> None:
        self.compare_position = max(0.0, min(1.0, float(value)))

    def preview_close_requested(self) -> None:
        """Hide the current preview without deleting its history or source files."""

        self.preview_dismissed = True
        self.state.compare_enabled = False
        self.SetBool(Id.COMPACT_COMPARE, False)
        self._refresh_preview()
        self._set_status("已关闭效果图预览，可从历史管理器重新查看")

    def preview_open_requested(self) -> None:
        """Play the video represented directly by the top preview canvas."""

        scene_path = self._animation_source_movie() or self.state.scene_image
        result_path = self.state.current_result
        if (
            self.state.compare_enabled
            and _is_video_path(scene_path)
            and _is_video_path(result_path)
            and Path(scene_path).is_file()
            and Path(result_path).is_file()
        ):
            self._open_animation_compare_viewer(scene_path, result_path)
            return
        path = result_path if _is_video_path(result_path) else scene_path
        if path and _is_video_path(path) and Path(path).is_file():
            self._open_animation_viewer(path)

    def _open_large_preview(self) -> None:
        path = self.state.current_result or self.state.scene_image
        if not path or not Path(path).is_file():
            c4d.gui.MessageDialog("当前没有可预览的图片或动画。")
            return
        if _is_video_path(path):
            self._open_animation_viewer(path)
            return
        self._show_bitmap_in_viewer(path, "预览图")

    def _open_animation_viewer(self, path: str) -> None:
        """Open an MP4/MOV in a C4D-native playback dialog."""

        if self.animation_preview_dialog is not None:
            try:
                self.animation_preview_dialog.Close()
            except (AttributeError, RuntimeError):
                pass
        self.animation_preview_dialog = AnimationMovieDialog(path)
        opened = self.animation_preview_dialog.Open(
            c4d.DLG_TYPE_ASYNC,
            PLUGIN_ID + 21,
            defaultw=760,
            defaulth=520,
        )
        if not opened:
            self.animation_preview_dialog = None
            c4d.gui.MessageDialog("无法打开 C4D 动画查看器。")

    def _open_animation_compare_viewer(self, scene_path: str, result_path: str) -> None:
        """Open synchronized white-model and AI videos in one C4D player."""

        if self.animation_preview_dialog is not None:
            try:
                self.animation_preview_dialog.Close()
            except (AttributeError, RuntimeError):
                pass
        self.animation_preview_dialog = AnimationCompareDialog(scene_path, result_path)
        opened = self.animation_preview_dialog.Open(
            c4d.DLG_TYPE_ASYNC,
            PLUGIN_ID + 22,
            defaultw=1040,
            defaulth=600,
        )
        if not opened:
            self.animation_preview_dialog = None
            c4d.gui.MessageDialog("无法打开白模 / AI 动画对比播放器。")

    def _open_media_file(self, path: str) -> None:
        """Open generated animation with the system player on Windows/macOS."""

        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except (OSError, RuntimeError) as exc:
            c4d.gui.MessageDialog("无法打开动画：{}".format(exc))

    def _open_output_folder(self) -> None:
        folder = self.render_dir
        folder.mkdir(parents=True, exist_ok=True)
        system = platform.system()
        if system == "Windows":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def _reveal_file_in_folder(self, path: str) -> None:
        """Open the native file manager and select a generated media file."""

        source = Path(str(path or ""))
        if not source.is_file():
            c4d.gui.MessageDialog("该历史文件已移动或删除。")
            return
        try:
            system = platform.system()
            if system == "Windows":
                subprocess.Popen(
                    ["explorer.exe", "/select,", str(source)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            elif system == "Darwin":
                subprocess.Popen(["open", "-R", str(source)])
            else:
                subprocess.Popen(["xdg-open", str(source.parent)])
        except (OSError, RuntimeError) as exc:
            c4d.gui.MessageDialog("无法打开文件位置：{}".format(exc))

    def _choose_output_directory(self) -> None:
        """Choose and persist the folder used for all future AI result images."""

        selected = c4d.storage.LoadDialog(
            type=c4d.FILESELECTTYPE_ANYTHING,
            title="设置 AI 效果图自动保存路径",
            flags=c4d.FILESELECT_DIRECTORY,
            def_path=str(self.render_dir),
        ) or ""
        if not selected:
            return
        folder = Path(selected).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=".zhireai_write_test_",
                suffix=".tmp",
                dir=str(folder),
            ):
                pass
            self.output_store.save({"directory": str(folder)})
        except (OSError, ValueError) as exc:
            c4d.gui.MessageDialog("无法使用该保存路径：\n{}".format(exc))
            return
        self.render_dir = folder
        self._set_status("效果图将自动保存到：{}".format(folder))
        c4d.gui.MessageDialog("保存路径已设置。\n今后生成的效果图会自动保存到：\n{}".format(folder))

    def Timer(self, message: c4d.BaseContainer) -> None:
        # SpecialEventAdd can be delayed when Cinema 4D is rendering or a
        # modal viewer has just closed. Polling completion here guarantees the
        # mode switch and render button are re-enabled on every supported C4D.
        self._recover_interaction_state()
        self._sync_prompt_focus_state()
        # Detect both typing and paste even when a host build omits an edit
        # Command event, then apply the same width-bound visual wrapping.
        current_prompt = self.GetString(Id.PROMPT)
        if current_prompt != self.last_prompt_text:
            self._handle_prompt_change(self.last_prompt_text)
        self._update_responsive_layout()
        if (
            self.prompt_rewrap_deadline > 0.0
            and time.monotonic() >= self.prompt_rewrap_deadline
        ):
            self._rewrap_prompt_editor()
        pending_cursor = self.pending_prompt_cursor
        self.pending_prompt_cursor = None
        if (
            pending_cursor is not None
            and self.prompt_has_focus
            and self.GetString(Id.PROMPT) == pending_cursor[0]
        ):
            current_native = self._prompt_cursor_offset()
            if pending_cursor[1] > 0 and current_native == 0:
                self._restore_prompt_cursor(pending_cursor[0], pending_cursor[1])
        if self.cancelled_workers:
            self.cancelled_workers = [
                worker for worker in self.cancelled_workers if not worker.snapshot()[2]
            ]
        if self.pending_preview_height is not None:
            height = self.pending_preview_height
            self.pending_preview_height = None
            self._apply_preview_height(height)
        if self.pending_generation:
            self.pending_generation = False
            self._continue_generation_from_references()
            return
        render_worker = self.render_worker
        if render_worker is not None:
            progress, status, done = render_worker.snapshot()
            if done:
                self._finish_render_load()
            else:
                self._set_render_progress(progress, status)
        worker = self.state.worker
        if worker is not None:
            progress, status, done = worker.snapshot()
            if done:
                self._finish_generation()
            else:
                self._set_status(status, progress)
        prompt_worker = self.prompt_worker
        if prompt_worker is not None:
            progress, status, done = prompt_worker.snapshot()
            if done:
                self._finish_prompt_generation()
            else:
                self._set_status(status, progress)

    def CoreMessage(self, message_id: int, message: c4d.BaseContainer) -> bool:
        if message_id == NETWORK_EVENT_ID:
            self._finish_generation()
            return True
        if message_id == PROMPT_EVENT_ID:
            self._finish_prompt_generation()
            return True
        return super().CoreMessage(message_id, message)

    def Command(self, item_id: int, message: c4d.BaseContainer) -> bool:
        if item_id == Id.CAPTURE_SCENE:
            self._render_view_reference()
        elif item_id == Id.MODE_IMAGE:
            self._switch_workflow("image")
        elif item_id == Id.MODE_ANIMATION:
            self._switch_workflow("animation")
        elif item_id == Id.IMPORT_SCENE:
            self._import_scene()
        elif item_id == Id.RENDER_VIEW_REFERENCE:
            self._render_view_reference()
        elif item_id == Id.REFERENCE_ADD:
            self._add_reference()
        elif item_id == Id.REFERENCE_PASTE:
            self._paste_reference()
        elif item_id == Id.REFERENCE_DELETE:
            self._delete_reference()
        elif item_id == Id.REFERENCE_CLEAR:
            self._clear_references()
        elif item_id == Id.REFERENCE_INSERT:
            self._insert_reference_token()
        elif item_id == Id.REFERENCE_TOKEN:
            self.reference_selected(max(0, self.GetInt32(Id.REFERENCE_TOKEN) - 1))
        elif item_id == Id.PROMPT:
            if self.GetString(Id.PROMPT) == self.last_prompt_text:
                # Cursor-only navigation must not be overwritten by a delayed
                # correction from the preceding wrap operation.
                self.pending_prompt_cursor = None
            else:
                self._handle_prompt_change()
        elif item_id == Id.PROMPT_MODEL:
            self.settings["prompt_model"] = self._selected_prompt_model()
            self.settings_store.save(self.settings)
            self._set_status("已切换提示词模型")
        elif item_id == Id.PROMPT_SKILL:
            self.settings["prompt_skill"] = self._selected_prompt_skill()
            self.settings_store.save(self.settings)
            self._set_status("已切换提示词技能")
        elif item_id == Id.PROMPT_AI_GENERATE:
            self._start_prompt_generation()
        elif item_id == Id.MAIN_MODEL:
            # Store the new model before rebuilding its dependent duration and
            # resolution choices. This makes 2.0 ↔ 2.5 switching immediate.
            self._sync_form()
            self._populate_parameter_controls()
            self._set_status("已切换动画模型" if self.state.workflow == "animation" else "已切换图像模型")
        elif item_id == Id.SETTINGS:
            self._open_settings()
        elif item_id == Id.HEADER_REFRESH:
            self._refresh_all()
        elif item_id == Id.GENERATE:
            self._start_generation()
        elif item_id == Id.CANCEL_GENERATION:
            self._cancel_generation()
        elif item_id == Id.COMPACT_COMPARE:
            self.state.compare_enabled = self.GetBool(item_id)
            self._refresh_preview()
            scene_path = self._animation_source_movie() or self.state.scene_image
            if (
                self.state.compare_enabled
                and _is_video_path(scene_path)
                and _is_video_path(self.state.current_result)
            ):
                self._open_animation_compare_viewer(
                    scene_path, self.state.current_result
                )
        elif item_id in (Id.LARGE_PREVIEW, Id.COMPACT_LARGE_PREVIEW):
            self._open_large_preview()
        elif item_id == Id.OPEN_FOLDER:
            self._open_output_folder()
        elif item_id == Id.HISTORY_DELETE:
            if self.state.selected_history_id:
                self.history.delete(self.state.selected_history_id)
                self.state.selected_history_id = ""
                self._refresh_history()
        elif item_id == Id.HISTORY_CLEAR:
            if c4d.gui.QuestionDialog("清空历史记录？生成图片文件仍会保留在磁盘中。"):
                self.history.clear()
                self.state.selected_history_id = ""
                self._refresh_history()
        elif item_id == Id.HISTORY_OUTPUT_DIRECTORY:
            self._choose_output_directory()
        elif item_id == Id.TOGGLE_HISTORY:
            self._toggle_fold(Id.FOLD_HISTORY)
        elif item_id in self.fold_content:
            self._toggle_fold(item_id)
        return True

    def DestroyWindow(self) -> None:
        self.pending_generation = False
        self.render_worker = None
        if self.prompt_worker is not None:
            self.prompt_worker.cancel()
        self.prompt_worker = None
        self.prompt_busy = False
        if self.state.worker is not None:
            self.state.worker.cancel()
            self.state.worker.End()
        for worker in self.cancelled_workers:
            worker.cancel()
        if self.animation_preview_dialog is not None:
            try:
                self.animation_preview_dialog.Close()
            except (AttributeError, RuntimeError):
                pass
            self.animation_preview_dialog = None
        self.settings_store.save(self.settings)
