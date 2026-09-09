"""Geometry-only viewport capture and compact prompt editor for ZhireAI."""

from __future__ import annotations

import math
import struct
import time
from pathlib import Path
from typing import Iterator, Optional, Union

import c4d
from c4d import utils

from lumastage import ui as _ui
from lumastage import worker as _worker
from lumastage.c4d_capture import (
    CaptureError,
    _record_capture_dimensions,
    _scaled_size,
    collect_scene_context,
)


MAX_PROMPT_ROWS = 6
# C4D scales native gadget heights again on this high-DPI layout. A logical
# height of 64 renders as roughly six visible text rows on the user's monitor.
MAX_PROMPT_HEIGHT = 64
DEPTH_TRACE_MAX_EDGE = 240
# AI reference canvas profiles. The selected image model's conventional 2K
# canvas is used for both the white-film geometry and the Z-depth image; the
# current camera aspect is preserved when converting that canvas to pixels.
MODEL_2K_REFERENCE_EDGE = {
    "gemini-3.1-flash-image-preview": 2048,
    "gemini-3-pro-image-preview": 2048,
    "gpt-image-2": 2048,
    "gpt-image-2.5-flare": 2048,
    "gpt-image-2.5-sunburst": 2048,
}
DEFAULT_2K_REFERENCE_EDGE = 2048
PROMPT_EDITOR_STYLE = (
    getattr(c4d, "DR_MULTILINE_WORDWRAP", 256)
    | getattr(c4d, "DR_MULTILINE_NO_SCROLLBARS", 8192)
)
GEOMETRY_COLORS = (
    c4d.Vector(0.60),
    c4d.Vector(0.64),
    c4d.Vector(0.68),
    c4d.Vector(0.62),
    c4d.Vector(0.66),
    c4d.Vector(0.70),
)
NON_GEOMETRY_TYPES = {
    c4d.Ocamera,
    c4d.Olight,
    c4d.Osky,
    c4d.Obackground,
    c4d.Oenvironment,
    c4d.Oforeground,
}


def _iter_objects(root) -> Iterator[c4d.BaseObject]:
    current = root
    while current is not None:
        yield current
        child = current.GetDown()
        if child is not None:
            yield from _iter_objects(child)
        current = current.GetNext()


def _remove_tags(obj: c4d.BaseObject) -> None:
    tag = obj.GetFirstTag()
    while tag is not None:
        following = tag.GetNext()
        if tag.GetType() in {c4d.Ttexture, c4d.Tcompositing}:
            tag.Remove()
        tag = following


def _neutral_material(doc, color: c4d.Vector, index: int) -> c4d.BaseMaterial:
    material = c4d.BaseMaterial(c4d.Mmaterial)
    material.SetName("__ZhireAI_Geometry_{:02d}__".format(index + 1))
    material[c4d.MATERIAL_USE_COLOR] = True
    material[c4d.MATERIAL_COLOR_COLOR] = color
    material[c4d.MATERIAL_COLOR_BRIGHTNESS] = 0.86
    material[c4d.MATERIAL_USE_LUMINANCE] = True
    material[c4d.MATERIAL_LUMINANCE_COLOR] = color
    material[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = 0.12
    material[c4d.MATERIAL_USE_REFLECTION] = False
    material[c4d.MATERIAL_USE_SPECULAR] = True
    material[c4d.MATERIAL_SPECULAR_BRIGHTNESS] = 0.18
    material[c4d.MATERIAL_SPECULAR_WIDTH] = 0.62
    doc.InsertMaterial(material)
    return material


def _neutral_background(doc) -> None:
    material = c4d.BaseMaterial(c4d.Mmaterial)
    material.SetName("__ZhireAI_Background__")
    material[c4d.MATERIAL_USE_COLOR] = False
    material[c4d.MATERIAL_USE_LUMINANCE] = True
    material[c4d.MATERIAL_LUMINANCE_COLOR] = c4d.Vector(0.92)
    material[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = 1.0
    material[c4d.MATERIAL_USE_REFLECTION] = False
    material[c4d.MATERIAL_USE_SPECULAR] = False
    doc.InsertMaterial(material)
    background = c4d.BaseObject(c4d.Obackground)
    background.SetName("__ZhireAI_Background__")
    texture = c4d.TextureTag()
    texture.SetMaterial(material)
    background.InsertTag(texture)
    doc.InsertObject(background)


def _bake_visible_geometry(doc) -> None:
    roots = []
    current = doc.GetFirstObject()
    while current is not None:
        following = current.GetNext()
        if (
            current.GetType() not in NON_GEOMETRY_TYPES
            and current.GetEditorMode() != c4d.MODE_OFF
        ):
            roots.append(current)
        current = following

    baked_objects = []
    for root in roots:
        try:
            result = utils.SendModelingCommand(
                command=c4d.MCOMMAND_CURRENTSTATETOOBJECT,
                list=[root],
                mode=c4d.MODELINGCOMMANDMODE_ALL,
                bc=c4d.BaseContainer(),
                doc=doc,
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            result = None
        if not isinstance(result, list) or not result:
            continue
        for baked in result:
            if baked is None:
                continue
            try:
                baked.Remove()
            except (AttributeError, ReferenceError, RuntimeError):
                pass
            baked_objects.append(baked)
        root.Remove()

    for baked in baked_objects:
        doc.InsertObject(baked)


def _prepare_geometry_document(source_doc, source_camera, camera_matrix=None):
    clone = source_doc.GetClone(c4d.COPYFLAGS_DOCUMENT)
    if clone is None:
        raise CaptureError("无法创建当前场景的安全几何副本。")

    _bake_visible_geometry(clone)
    materials = [
        _neutral_material(clone, color, index)
        for index, color in enumerate(GEOMETRY_COLORS)
    ]
    geometry_index = 0
    for obj in _iter_objects(clone.GetFirstObject()):
        _remove_tags(obj)
        if obj.GetType() in NON_GEOMETRY_TYPES:
            obj.SetRenderMode(c4d.MODE_OFF)
            continue
        editor_mode = obj.GetEditorMode()
        obj.SetRenderMode(c4d.MODE_OFF if editor_mode == c4d.MODE_OFF else c4d.MODE_UNDEF)
        if obj.CheckType(c4d.Onull):
            continue
        texture = c4d.TextureTag()
        texture.SetMaterial(materials[geometry_index % len(materials)])
        obj.InsertTag(texture)
        geometry_index += 1

    _neutral_background(clone)

    camera = source_camera.GetClone(c4d.COPYFLAGS_NONE)
    if camera is None:
        raise CaptureError("无法复制当前透视视图相机。")
    camera.SetName("__ZhireAI_Geometry_Camera__")
    if camera_matrix is not None:
        camera.SetMg(camera_matrix)
    clone.InsertObject(camera)
    render_draw = clone.GetRenderBaseDraw()
    if render_draw is None:
        raise CaptureError("无法创建几何捕获视图。")
    render_draw.SetSceneCamera(camera)
    return clone


def _clear_render_effects(render_data) -> None:
    multipass = render_data.GetFirstMultipass()
    while multipass is not None:
        following = multipass.GetNext()
        multipass.Remove()
        multipass = following
    video_post = render_data.GetFirstVideoPost()
    while video_post is not None:
        following = video_post.GetNext()
        video_post.Remove()
        video_post = following


def _save_rgb_with_background(bitmap, path: Path, background: int = 236) -> None:
    del background
    saved = bitmap.Save(
        str(path), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
    )
    if saved != c4d.IMAGERESULT_OK:
        raise CaptureError("无法保存几何结构图，错误码：{}".format(saved))


def _depth_layer(bitmap) -> Optional[c4d.bitmaps.MultipassBitmap]:
    layers = list(
        bitmap.GetLayers(c4d.MPB_GETLAYERS_IMAGE | c4d.MPB_GETLAYERS_ALPHA)
    )
    try:
        hidden_count = int(bitmap.GetHiddenLayerCount())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        hidden_count = 0
    for index in range(max(0, hidden_count)):
        try:
            hidden = bitmap.GetHiddenLayerNum(index)
        except (AttributeError, RuntimeError, TypeError):
            hidden = None
        if hidden is not None:
            layers.append(hidden)
    for layer in layers:
        if layer is bitmap:
            continue
        try:
            user_id = layer.GetParameter(c4d.MPBTYPE_USERID)
        except (AttributeError, RuntimeError, TypeError):
            user_id = None
        try:
            name = str(layer.GetParameter(c4d.MPBTYPE_NAME) or "").lower()
        except (AttributeError, RuntimeError, TypeError):
            name = ""
        try:
            bitmap_type = int(layer.GetParameter(c4d.MPBTYPE_BITMAPTYPE) or 0)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            bitmap_type = 0
        if (
            user_id == c4d.VPBUFFER_DEPTH
            or bitmap_type & getattr(c4d, "MPBUFFER_FLAGS_DEPTH", 2)
            or "depth" in name
            or "深度" in name
        ):
            return layer
    # C4D 2025 can return a metadata-only proxy from FindUserID. Prefer the
    # concrete layer list above, whose entries own the rendered pixel buffer.
    try:
        return bitmap.FindUserID(c4d.VPBUFFER_DEPTH, 0)
    except (AttributeError, RuntimeError, TypeError):
        return None


def _read_float_row(bitmap, y: int, width: int) -> tuple[float, ...]:
    formats = {
        c4d.COLORMODE_GRAY: (1, "B", 1, 255.0),
        c4d.COLORMODE_RGB: (3, "B", 3, 255.0),
        c4d.COLORMODE_GRAYw: (2, "H", 1, 65535.0),
        c4d.COLORMODE_RGBw: (6, "H", 3, 65535.0),
        c4d.COLORMODE_GRAYf: (4, "f", 1, 1.0),
        c4d.COLORMODE_RGBf: (12, "f", 3, 1.0),
    }
    try:
        color_mode = int(bitmap.GetParameter(c4d.MPBTYPE_COLORMODE))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        color_mode = 0
    pixel_format = formats.get(color_mode)
    if pixel_format is not None:
        stride, format_code, channels, divisor = pixel_format
        buffer = bytearray(width * stride)
        try:
            bitmap.GetPixelCnt(
            0, y, width, buffer, stride, color_mode, c4d.PIXELCNT_NONE
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        values = struct.unpack(
            "<{}{}".format(width * channels, format_code), buffer
        )
        if channels == 1:
            return tuple(float(value) / divisor for value in values)
        return tuple(float(values[index * channels]) / divisor for index in range(width))
    try:
        direct = []
        for x in range(width):
            pixel = bitmap.GetPixelDirect(x, y)
            direct.append(float(pixel.x))
        if direct:
            return tuple(direct)
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        pass
    try:
        color_mode = bitmap.GetParameter(c4d.MPBTYPE_COLORMODE)
    except (AttributeError, RuntimeError, TypeError):
        color_mode = "未知"
    raise CaptureError(
        "无法读取C4D原生Z深度通道（通道格式：{}）。".format(color_mode)
    )


def _save_normalized_depth(bitmap, depth, path: Path) -> None:
    width = bitmap.GetBw()
    height = bitmap.GetBh()
    # The multipass bitmap can expose a secondary all-black internal alpha in
    # C4D 2025. Z itself uses zero for background, so it is the reliable mask.
    alpha = None
    alpha_row = bytearray(width)
    samples = []
    row_step = max(1, height // 180)
    column_step = max(1, width // 180)
    for y in range(0, height, row_step):
        values = _read_float_row(depth, y, width)
        if alpha is not None:
            alpha.GetPixelCnt(
                0, y, width, alpha_row, 1, c4d.COLORMODE_GRAY, c4d.PIXELCNT_NONE
            )
        else:
            alpha_row[:] = b"\xff" * width
        for x in range(0, width, column_step):
            value = abs(values[x])
            if alpha_row[x] > 8 and math.isfinite(value) and 1.0e-9 < value < 1.0e20:
                samples.append(value)
    if len(samples) < 8:
        raise CaptureError("当前场景没有足够的可见几何深度信息。")
    samples.sort()
    near = samples[int((len(samples) - 1) * 0.01)]
    far = samples[int((len(samples) - 1) * 0.99)]
    if far <= near + max(1.0e-9, abs(near) * 1.0e-7):
        near = samples[0]
        far = samples[-1]
    if far <= near:
        raise CaptureError("当前视图的Z深度范围无效。")

    output = c4d.bitmaps.BaseBitmap()
    if output.Init(width, height, 24) != c4d.IMAGERESULT_OK:
        raise CaptureError("无法创建Z深度输出图。")
    output_row = bytearray(width * 3)
    span = far - near
    for y in range(height):
        values = _read_float_row(depth, y, width)
        if alpha is not None:
            alpha.GetPixelCnt(
                0, y, width, alpha_row, 1, c4d.COLORMODE_GRAY, c4d.PIXELCNT_NONE
            )
        else:
            alpha_row[:] = b"\xff" * width
        for x, raw in enumerate(values):
            distance = abs(raw)
            if (
                alpha_row[x] <= 8
                or not math.isfinite(distance)
                or distance <= 1.0e-9
                or distance >= 1.0e20
            ):
                level = 0
            else:
                normalized = (min(far, max(near, distance)) - near) / span
                level = int(round(255.0 * (1.0 - normalized)))
            offset = x * 3
            output_row[offset : offset + 3] = bytes((level, level, level))
        output.SetPixelCnt(
            0, y, width, output_row, 3, c4d.COLORMODE_RGB, c4d.PIXELCNT_NONE
        )
    saved = output.Save(
        str(path), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
    )
    if saved != c4d.IMAGERESULT_OK:
        raise CaptureError("无法保存Z深度图，错误码：{}".format(saved))


def _bitmap_stats(path: Union[str, Path]) -> tuple[float, float, float]:
    """Return sampled luminance range, variance, and mean for blank-image checks."""

    bitmap = _ui._load_bitmap(str(path))
    if bitmap is None:
        raise CaptureError("无法读取刚刚生成的捕获图。")
    width = max(1, int(bitmap.GetBw()))
    height = max(1, int(bitmap.GetBh()))
    step_x = max(1, width // 64)
    step_y = max(1, height // 64)
    minimum = 255.0
    maximum = 0.0
    total = 0.0
    total_sq = 0.0
    count = 0
    for y in range(step_y // 2, height, step_y):
        for x in range(step_x // 2, width, step_x):
            pixel = bitmap.GetPixel(min(x, width - 1), min(y, height - 1))
            value = (
                float(pixel[0]) * 0.2126
                + float(pixel[1]) * 0.7152
                + float(pixel[2]) * 0.0722
            )
            minimum = min(minimum, value)
            maximum = max(maximum, value)
            total += value
            total_sq += value * value
            count += 1
    if count < 1:
        raise CaptureError("捕获图没有可读取的像素。")
    mean = total / count
    variance = max(0.0, total_sq / count - mean * mean)
    return maximum - minimum, variance, mean


def _validate_nonblank(path: Union[str, Path], description: str) -> None:
    luminance_range, variance, mean = _bitmap_stats(path)
    if luminance_range < 6.0 or variance < 4.0 or mean <= 1.0 or mean >= 254.0:
        raise CaptureError("{}为空白图，已停止加载以免提交无效参考。".format(description))


def _images_are_effectively_equal(
    first_path: Union[str, Path], second_path: Union[str, Path]
) -> bool:
    first = _ui._load_bitmap(str(first_path))
    second = _ui._load_bitmap(str(second_path))
    if first is None or second is None:
        return False
    width = min(int(first.GetBw()), int(second.GetBw()))
    height = min(int(first.GetBh()), int(second.GetBh()))
    if width < 1 or height < 1:
        return False
    step_x = max(1, width // 48)
    step_y = max(1, height // 48)
    total_delta = 0.0
    count = 0
    for y in range(step_y // 2, height, step_y):
        first_y = min(int(y * first.GetBh() / height), first.GetBh() - 1)
        second_y = min(int(y * second.GetBh() / height), second.GetBh() - 1)
        for x in range(step_x // 2, width, step_x):
            first_x = min(int(x * first.GetBw() / width), first.GetBw() - 1)
            second_x = min(int(x * second.GetBw() / width), second.GetBw() - 1)
            first_pixel = first.GetPixel(first_x, first_y)
            second_pixel = second.GetPixel(second_x, second_y)
            total_delta += sum(
                abs(float(first_pixel[index]) - float(second_pixel[index]))
                for index in range(3)
            ) / 3.0
            count += 1
    return count > 0 and total_delta / count < 1.0


def _find_visible_plugin_windows() -> list[int]:
    """Find this plug-in's floating windows without touching the C4D main window."""

    try:
        import ctypes
        import os
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        handles: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        def visit(hwnd, _lparam):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if int(process_id.value) != os.getpid() or not user32.IsWindowVisible(hwnd):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length < 1:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if "挚热AI渲染器" in buffer.value:
                handles.append(int(hwnd))
            return True

        callback = callback_type(visit)
        user32.EnumWindows(callback, 0)
        return handles
    except (
        AttributeError,
        OSError,
        OverflowError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return []


def _set_plugin_windows_visible(handles: list[int], visible: bool) -> None:
    if not handles:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        command = 8 if visible else 0  # SW_SHOWNA / SW_HIDE
        for handle in handles:
            user32.ShowWindow(wintypes.HWND(handle), command)
    except (
        AttributeError,
        OSError,
        OverflowError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        pass


def _viewport_overrides(active_draw) -> dict[int, object]:
    data = active_draw.GetDataInstance()
    overrides = {
        c4d.BASEDRAW_DATA_SDISPLAYACTIVE: c4d.BASEDRAW_SDISPLAY_GOURAUD,
        c4d.BASEDRAW_DATA_TEXTURES: False,
        c4d.BASEDRAW_DATA_WIREFRAMESELECTION: False,
        c4d.BASEDRAW_DATA_WIREFRAMESELECTION_CHILDREN: False,
    }
    for name in (
        "BASEDRAW_DISPLAYFILTER_BASEGRID",
        "BASEDRAW_DISPLAYFILTER_CAMERA",
        "BASEDRAW_DISPLAYFILTER_DEFORMER",
        "BASEDRAW_DISPLAYFILTER_FIELD",
        "BASEDRAW_DISPLAYFILTER_GRID",
        "BASEDRAW_DISPLAYFILTER_GUIDELINES",
        "BASEDRAW_DISPLAYFILTER_HANDLES",
        "BASEDRAW_DISPLAYFILTER_HIGHLIGHTING",
        "BASEDRAW_DISPLAYFILTER_HIGHLIGHTING_HANDLES",
        "BASEDRAW_DISPLAYFILTER_HORIZON",
        "BASEDRAW_DISPLAYFILTER_HUD",
        "BASEDRAW_DISPLAYFILTER_JOINT",
        "BASEDRAW_DISPLAYFILTER_LIGHT",
        "BASEDRAW_DISPLAYFILTER_MULTIAXIS",
        "BASEDRAW_DISPLAYFILTER_NULL",
        "BASEDRAW_DISPLAYFILTER_OBJECTHANDLES",
        "BASEDRAW_DISPLAYFILTER_OBJECTHIGHLIGHTING",
        "BASEDRAW_DISPLAYFILTER_OTHER",
        "BASEDRAW_DISPLAYFILTER_PARTICLE",
        "BASEDRAW_DISPLAYFILTER_POI",
        "BASEDRAW_DISPLAYFILTER_SPLINE",
        "BASEDRAW_DISPLAYFILTER_WORLDAXIS",
    ):
        parameter_id = getattr(c4d, name, None)
        if parameter_id is not None:
            overrides[int(parameter_id)] = False
    previous = {}
    for parameter_id, value in overrides.items():
        try:
            previous[parameter_id] = data[parameter_id]
            data[parameter_id] = value
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError):
            pass
    return previous


def _restore_viewport(active_draw, previous: dict[int, object]) -> None:
    data = active_draw.GetDataInstance()
    for parameter_id, value in previous.items():
        try:
            data[parameter_id] = value
        except (AttributeError, IndexError, KeyError, RuntimeError, TypeError):
            pass


def _redraw_active_view(active_draw) -> None:
    flags = (
        getattr(c4d, "DRAWFLAGS_ONLY_ACTIVE_VIEW", 0)
        | getattr(c4d, "DRAWFLAGS_ONLY_BASEDRAW", 0)
        | getattr(c4d, "DRAWFLAGS_FORCEFULLREDRAW", 0)
        | getattr(c4d, "DRAWFLAGS_NO_THREAD", 0)
    )
    try:
        c4d.DrawViews(flags, active_draw)
    except (AttributeError, RuntimeError, TypeError):
        c4d.EventAdd(getattr(c4d, "EVENT_ENQUEUE_REDRAW", 0))


def _exact_active_view_screen_rect(active_draw) -> tuple[int, int, int, int]:
    """Use C4D's absolute viewport rectangle to avoid DPI/local-origin drift."""

    try:
        screen_frame = active_draw.GetFrameScreen()
        if screen_frame:
            left = int(screen_frame.get("cl", 0))
            top = int(screen_frame.get("ct", 0))
            right = int(screen_frame.get("cr", 0))
            bottom = int(screen_frame.get("cb", 0))
            scale = _active_window_dpi_scale()
            left = int(round(left * scale))
            top = int(round(top * scale))
            right = int(round(right * scale))
            bottom = int(round(bottom * scale))
            width, height = right - left, bottom - top
            if width >= 64 and height >= 64:
                return left, top, width, height
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return _original_active_view_screen_rect(active_draw)


def _active_window_dpi_scale() -> float:
    """Translate C4D logical screen coordinates to GDI physical pixels."""

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        handle = user32.GetForegroundWindow()
        dpi = int(user32.GetDpiForWindow(handle)) if handle else 96
        return max(1.0, min(3.0, dpi / 96.0))
    except (
        AttributeError,
        OSError,
        OverflowError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return 1.0


def _append_capture_diagnostic(output_dir: Union[str, Path], message: str) -> None:
    try:
        target = Path(output_dir) / "capture_diagnostics.log"
        with target.open("a", encoding="utf-8") as stream:
            stream.write(
                "{} {}\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    str(message).replace("\r", " ").replace("\n", " "),
                )
            )
    except OSError:
        pass


def _render_current_view_depth(
    source_doc,
    active_draw,
    width: int,
    height: int,
    depth_path: Path,
    output_width: Optional[int] = None,
    output_height: Optional[int] = None,
) -> None:
    """Raycast evaluated geometry from the current camera into a Z-depth image."""

    started = time.monotonic()
    phase_started = started
    source_camera = (
        active_draw.GetSceneCamera(source_doc)
        if active_draw.HasCameraLink()
        else active_draw.GetEditorCamera()
    )
    if source_camera is None:
        raise CaptureError("无法取得当前视图相机的Z深度。")
    collision_doc = source_doc.GetClone(c4d.COPYFLAGS_DOCUMENT)
    if collision_doc is None:
        raise CaptureError("无法创建完整的Z深度几何副本。")
    try:
        _bake_visible_geometry(collision_doc)
        joined, object_count = _join_evaluated_geometry(collision_doc)
    finally:
        try:
            c4d.documents.KillDocument(collision_doc)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    join_seconds = time.monotonic() - phase_started
    polygon_count = int(joined.GetPolygonCount()) if joined is not None else 0
    if polygon_count < 1:
        raise CaptureError("当前视口缓存没有可用于Z深度的多边形。")
    point_count = int(joined.GetPointCount())
    phase_started = time.monotonic()
    collider = utils.GeRayCollider()
    if not collider.Init(joined, True):
        raise CaptureError("无法建立当前场景的Z深度碰撞缓存。")
    collider_seconds = time.monotonic() - phase_started

    camera_matrix = (
        source_camera.GetMg() if active_draw.HasCameraLink() else active_draw.GetMg()
    )
    render_data = source_doc.GetActiveRenderData()
    render_settings = render_data.GetDataInstance() if render_data is not None else None
    try:
        pixel_aspect = max(1.0e-6, float(render_settings[c4d.RDATA_PIXELASPECT]))
    except (IndexError, KeyError, TypeError, ValueError):
        pixel_aspect = 1.0
    try:
        projection = int(source_camera[c4d.CAMERA_PROJECTION])
    except (IndexError, KeyError, TypeError, ValueError):
        projection = c4d.Pperspective

    # GetViewParameter contains the active view's real projection offset. This
    # is essential for cameras using film offset; the old center-based math
    # silently projected every pixel as if the offset were zero.
    view_offset = None
    view_scale = None
    viewport_left = 0.0
    viewport_top = 0.0
    viewport_width = 0.0
    viewport_height = 0.0
    safe_frame_left = 0.0
    safe_frame_top = 0.0
    safe_frame_width = 0.0
    safe_frame_height = 0.0
    safe_frame_source = "GetSafeFrame"
    try:
        view_frame = active_draw.GetFrame()
        viewport_left = float(view_frame.get("cl", 0))
        viewport_top = float(view_frame.get("ct", 0))
        viewport_width = float(view_frame.get("cr", 0)) - viewport_left
        viewport_height = float(view_frame.get("cb", 0)) - viewport_top
    except (AttributeError, RuntimeError, TypeError, ValueError):
        viewport_left = 0.0
        viewport_top = 0.0
        viewport_width = 0.0
        viewport_height = 0.0
    try:
        safe_frame = active_draw.GetSafeFrame()
        safe_frame_left = float(safe_frame.get("cl", 0))
        safe_frame_top = float(safe_frame.get("ct", 0))
        safe_frame_width = float(safe_frame.get("cr", 0)) - safe_frame_left
        safe_frame_height = float(safe_frame.get("cb", 0)) - safe_frame_top
    except (AttributeError, RuntimeError, TypeError, ValueError):
        safe_frame_width = 0.0
        safe_frame_height = 0.0
    if safe_frame_width < 1.0 or safe_frame_height < 1.0:
        safe_frame_source = "centered_viewport_fallback"
        if viewport_width >= 1.0 and viewport_height >= 1.0:
            canvas_aspect = float(width) / float(max(1, height))
            viewport_aspect = viewport_width / viewport_height
            if viewport_aspect >= canvas_aspect:
                safe_frame_height = viewport_height
                safe_frame_width = safe_frame_height * canvas_aspect
                safe_frame_left = viewport_left + (
                    viewport_width - safe_frame_width
                ) * 0.5
                safe_frame_top = viewport_top
            else:
                safe_frame_width = viewport_width
                safe_frame_height = safe_frame_width / canvas_aspect
                safe_frame_left = viewport_left
                safe_frame_top = viewport_top + (
                    viewport_height - safe_frame_height
                ) * 0.5
    canvas_aspect = float(width) / float(max(1, height))
    safe_frame_aspect = safe_frame_width / max(1.0, safe_frame_height)
    safe_frame_aspect_error = abs(safe_frame_aspect - canvas_aspect)
    try:
        view_parameters = active_draw.GetViewParameter()
        if isinstance(view_parameters, dict):
            view_offset = view_parameters.get("offset")
            view_scale = view_parameters.get("scale")
        else:
            view_offset, view_scale, _scale_z = view_parameters
        if (
            view_offset is None
            or view_scale is None
            or abs(float(view_scale.x)) <= 1.0e-9
            or abs(float(view_scale.y)) <= 1.0e-9
            or safe_frame_width < 1.0
            or safe_frame_height < 1.0
        ):
            view_offset = None
            view_scale = None
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
        view_offset = None
        view_scale = None

    origin = camera_matrix.off
    right = camera_matrix.v1.GetNormalized()
    up = camera_matrix.v2.GetNormalized()
    forward = camera_matrix.v3.GetNormalized()
    distances = [0.0] * (width * height)
    valid = []
    if projection == c4d.Pperspective:
        focal = max(1.0e-6, float(source_camera[c4d.CAMERA_FOCUS]))
        aperture = max(1.0e-6, float(source_camera[c4d.CAMERAOBJECT_APERTURE]))
    else:
        zoom = max(1.0e-6, float(source_camera[c4d.CAMERA_ZOOM]))

    def camera_coordinates(
        pixel_x: float, pixel_y: float, canvas_width: int, canvas_height: int
    ):
        """Convert an output pixel into the active view's camera plane."""

        if view_offset is not None and view_scale is not None:
            screen_x = safe_frame_left + (
                (pixel_x + 0.5) * safe_frame_width / float(canvas_width)
            )
            screen_y = safe_frame_top + (
                (pixel_y + 0.5) * safe_frame_height / float(canvas_height)
            )
            return (
                (screen_x - float(view_offset.x)) / float(view_scale.x),
                (screen_y - float(view_offset.y)) / float(view_scale.y),
            )
        if projection == c4d.Pperspective:
            active_scale_x = focal / aperture * canvas_width
        else:
            active_scale_x = canvas_width / 1024.0 * zoom
        active_scale_y = -active_scale_x / pixel_aspect
        return (
            (pixel_x + 0.5 - canvas_width * 0.5) / active_scale_x,
            (pixel_y + 0.5 - canvas_height * 0.5) / active_scale_y,
        )

    def trace_camera_ray(camera_x: float, camera_y: float) -> float:
        if projection == c4d.Pperspective:
            ray_origin = origin
            direction = (forward + right * camera_x + up * camera_y).GetNormalized()
        else:
            ray_origin = origin + right * camera_x + up * camera_y
            direction = forward
        if not collider.Intersect(ray_origin, direction, 1.0e9, False):
            return 0.0
        nearest = collider.GetNearestIntersection()
        if not nearest:
            return 0.0
        ray_distance = float(nearest.get("distance", 0.0))
        if not math.isfinite(ray_distance) or ray_distance <= 1.0e-9:
            return 0.0
        z_depth = ray_distance * float(direction.Dot(forward))
        if not math.isfinite(z_depth) or z_depth <= 1.0e-9:
            return 0.0
        return z_depth

    phase_started = time.monotonic()
    for y in range(height):
        row_offset = y * width
        for x in range(width):
            camera_x, camera_y = camera_coordinates(x, y, width, height)
            distance = trace_camera_ray(camera_x, camera_y)
            if distance <= 0.0:
                continue
            distances[row_offset + x] = distance
            valid.append(distance)
    ray_seconds = time.monotonic() - phase_started

    if len(valid) < 8:
        raise CaptureError("当前场景没有足够的可见几何深度信息。")
    valid.sort()
    near = valid[int((len(valid) - 1) * 0.01)]
    far = valid[int((len(valid) - 1) * 0.99)]
    if far <= near:
        near, far = valid[0], valid[-1]
    if far <= near:
        raise CaptureError("当前视图的Z深度范围无效。")

    span = far - near
    final_width = max(1, int(output_width or width))
    final_height = max(1, int(output_height or height))

    # Locate only silhouette and depth-discontinuity cells. Those regions are
    # re-traced at output resolution; smooth surfaces use continuous depth
    # interpolation, avoiding both blocky upscaling and a full-resolution cast.
    phase_started = time.monotonic()
    edge_threshold = max(1.0e-6, span * 0.015)
    edge_rows = [bytearray(max(0, width - 1)) for _ in range(max(0, height - 1))]
    edge_cells = 0
    for y in range(height - 1):
        top = y * width
        bottom = top + width
        edge_row = edge_rows[y]
        for x in range(width - 1):
            samples = (
                distances[top + x],
                distances[top + x + 1],
                distances[bottom + x],
                distances[bottom + x + 1],
            )
            positive = [value for value in samples if value > 0.0]
            is_edge = 0 < len(positive) < 4
            if len(positive) == 4 and max(positive) - min(positive) > edge_threshold:
                is_edge = True
            if is_edge:
                edge_row[x] = 1
                edge_cells += 1
    edge_detect_seconds = time.monotonic() - phase_started

    phase_started = time.monotonic()
    output = c4d.bitmaps.BaseBitmap()
    if output.Init(final_width, final_height, 24) != c4d.IMAGERESULT_OK:
        raise CaptureError("无法创建Z深度输出图。")

    x_samples = []
    for x in range(final_width):
        source_x = max(0.0, min(width - 1.0, (x + 0.5) * width / final_width - 0.5))
        x0 = int(math.floor(source_x))
        x1 = min(width - 1, x0 + 1)
        x_samples.append((x0, x1, source_x - x0))

    refined_rays = 0
    for y in range(final_height):
        source_y = max(
            0.0, min(height - 1.0, (y + 0.5) * height / final_height - 0.5)
        )
        y0 = int(math.floor(source_y))
        y1 = min(height - 1, y0 + 1)
        ty = source_y - y0
        top = y0 * width
        bottom = y1 * width
        edge_y = min(max(0, y0), height - 2)
        edge_row = edge_rows[edge_y]
        _camera_x, camera_y = camera_coordinates(0, y, final_width, final_height)
        output_row = bytearray(final_width * 3)
        for x, (x0, x1, tx) in enumerate(x_samples):
            edge_x = min(max(0, x0), width - 2)
            if edge_row[edge_x]:
                camera_x, camera_y = camera_coordinates(
                    x, y, final_width, final_height
                )
                distance = trace_camera_ray(camera_x, camera_y)
                refined_rays += 1
            else:
                d00 = distances[top + x0]
                d10 = distances[top + x1]
                d01 = distances[bottom + x0]
                d11 = distances[bottom + x1]
                top_depth = d00 + (d10 - d00) * tx
                bottom_depth = d01 + (d11 - d01) * tx
                distance = top_depth + (bottom_depth - top_depth) * ty
            if distance <= 0.0:
                continue
            normalized = (min(far, max(near, distance)) - near) / span
            level = int(round(255.0 * (1.0 - normalized)))
            offset = x * 3
            output_row[offset] = level
            output_row[offset + 1] = level
            output_row[offset + 2] = level
        output.SetPixelCnt(
            0,
            y,
            final_width,
            output_row,
            3,
            c4d.COLORMODE_RGB,
            c4d.PIXELCNT_NONE,
        )
    output_seconds = time.monotonic() - phase_started

    phase_started = time.monotonic()
    saved = output.Save(
        str(depth_path), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
    )
    if saved != c4d.IMAGERESULT_OK:
        raise CaptureError("无法保存Z深度图，错误码：{}".format(saved))
    _validate_nonblank(depth_path, "Z深度图")
    save_seconds = time.monotonic() - phase_started
    _append_capture_diagnostic(
        depth_path.parent,
        (
            "depth trace={}x{} output={}x{} objects={} points={} polygons={} "
            "base_rays={} edge_cells={} refined_rays={} join={:.3f}s collider={:.3f}s "
            "rays={:.3f}s edge_detect={:.3f}s output={:.3f}s save={:.3f}s total={:.3f}s "
            "view_offset={} view_scale={} viewport={}x{} "
            "safe_frame=({},{} {}x{}) source={} render_aspect={:.6f} "
            "aspect_error={:.6f} "
            "depth_metric=camera_space_z"
        ).format(
            width,
            height,
            final_width,
            final_height,
            object_count,
            point_count,
            polygon_count,
            width * height,
            edge_cells,
            refined_rays,
            join_seconds,
            collider_seconds,
            ray_seconds,
            edge_detect_seconds,
            output_seconds,
            save_seconds,
            time.monotonic() - started,
            str(view_offset) if view_offset is not None else "fallback",
            str(view_scale) if view_scale is not None else "fallback",
            int(viewport_width),
            int(viewport_height),
            int(safe_frame_left),
            int(safe_frame_top),
            int(safe_frame_width),
            int(safe_frame_height),
            safe_frame_source,
            canvas_aspect,
            safe_frame_aspect_error,
        ),
    )


def _clone_evaluated_geometry(source_doc, target_doc) -> int:
    """Copy the polygonal result visible in the editor, including generator caches."""

    inserted = 0

    def insert_polygon(source) -> None:
        nonlocal inserted
        point_count = int(source.GetPointCount())
        polygon_count = int(source.GetPolygonCount())
        if point_count < 1 or polygon_count < 1:
            return
        clone = c4d.PolygonObject(point_count, polygon_count)
        clone.SetAllPoints(source.GetAllPoints())
        for index, polygon in enumerate(source.GetAllPolygons()):
            clone.SetPolygon(index, polygon)
        clone.SetName("__ZhireAI_Depth_{:04d}__".format(inserted + 1))
        clone.SetMg(source.GetMg())
        clone.SetEditorMode(c4d.MODE_UNDEF)
        clone.SetRenderMode(c4d.MODE_UNDEF)
        clone.Message(c4d.MSG_UPDATE)
        target_doc.InsertObject(clone)
        inserted += 1

    def walk_cache(root) -> None:
        current = root
        while current is not None:
            if current.CheckType(c4d.Opolygon):
                insert_polygon(current)
            child = current.GetDown()
            if child is not None:
                walk_cache(child)
            current = current.GetNext()

    def walk_source(root) -> None:
        current = root
        while current is not None:
            if (
                current.GetEditorMode() == c4d.MODE_OFF
            ):
                current = current.GetNext()
                continue
            evaluated = current.GetDeformCache() or current.GetCache()
            if evaluated is not None:
                walk_cache(evaluated)
            else:
                if current.CheckType(c4d.Opolygon):
                    insert_polygon(current)
                child = current.GetDown()
                if child is not None:
                    walk_source(child)
            current = current.GetNext()

    walk_source(source_doc.GetFirstObject())
    return inserted


def _join_evaluated_geometry(source_doc) -> tuple[c4d.PolygonObject, int]:
    """Build one world-space collision mesh without an intermediate document copy."""

    points = []
    polygons = []
    object_count = 0

    def append_polygon(source, owner_name: str = "") -> None:
        nonlocal object_count
        point_count = int(source.GetPointCount())
        polygon_count = int(source.GetPolygonCount())
        if point_count < 1 or polygon_count < 1:
            return
        point_offset = len(points)
        matrix = source.GetMg()
        points.extend(point * matrix for point in source.GetAllPoints())
        polygons.extend(
            c4d.CPolygon(
                polygon.a + point_offset,
                polygon.b + point_offset,
                polygon.c + point_offset,
                polygon.d + point_offset,
            )
            for polygon in source.GetAllPolygons()
        )
        object_count += 1

    def walk_cache(root, owner_name: str = "") -> None:
        current = root
        while current is not None:
            if current.CheckType(c4d.Opolygon):
                append_polygon(current, owner_name)
            child = current.GetDown()
            if child is not None:
                walk_cache(child, owner_name)
            current = current.GetNext()

    def append_current_state(source) -> bool:
        """Resolve visible instances/generators which expose no regular cache."""

        if source.GetType() in NON_GEOMETRY_TYPES or source.CheckType(c4d.Onull):
            return False
        try:
            result = utils.SendModelingCommand(
                command=c4d.MCOMMAND_CURRENTSTATETOOBJECT,
                list=[source],
                mode=c4d.MODELINGCOMMANDMODE_ALL,
                bc=c4d.BaseContainer(),
                doc=source_doc,
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            result = None
        if not isinstance(result, list) or not result:
            return False
        previous_count = object_count
        for converted in result:
            if converted is not None:
                walk_cache(converted, source.GetName() + "[CSTO]")
        return object_count > previous_count

    def walk_source(root) -> None:
        current = root
        while current is not None:
            if current.GetEditorMode() == c4d.MODE_OFF:
                current = current.GetNext()
                continue
            evaluated = current.GetDeformCache() or current.GetCache()
            if evaluated is not None:
                walk_cache(evaluated, current.GetName())
            else:
                if current.CheckType(c4d.Opolygon):
                    append_polygon(current, current.GetName())
                    converted = False
                else:
                    converted = append_current_state(current)
                if not converted:
                    child = current.GetDown()
                    if child is not None:
                        walk_source(child)
            current = current.GetNext()

    walk_source(source_doc.GetFirstObject())
    if not points or not polygons:
        raise CaptureError("当前视口没有可用于Z深度的有效网格。")
    joined = c4d.PolygonObject(len(points), len(polygons))
    joined.SetAllPoints(points)
    for index, polygon in enumerate(polygons):
        joined.SetPolygon(index, polygon)
    joined.Message(c4d.MSG_UPDATE)
    return joined, object_count


def _render_current_view_geometry(
    source_doc,
    active_draw,
    width: int,
    height: int,
    geometry_path: Path,
) -> None:
    """Render the safe-frame canvas with C4D's hardware preview renderer."""

    source_camera = (
        active_draw.GetSceneCamera(source_doc)
        if active_draw.HasCameraLink()
        else active_draw.GetEditorCamera()
    )
    if source_camera is None:
        raise CaptureError("无法取得当前视图相机。")
    camera_matrix = (
        None if active_draw.HasCameraLink() else active_draw.GetMg()
    )
    geometry_doc = _prepare_geometry_document(
        source_doc, source_camera, camera_matrix
    )
    render_draw = geometry_doc.GetRenderBaseDraw()
    if render_draw is None:
        c4d.documents.KillDocument(geometry_doc)
        raise CaptureError("无法创建仅几何结构捕获视图。")
    render_view_previous = {}
    try:
        render_view_previous = _viewport_overrides(render_draw)
        render_data = geometry_doc.GetActiveRenderData()
        if render_data is None:
            raise CaptureError("当前文档没有有效渲染设置。")
        settings = render_data.GetData()
        _clear_render_effects(render_data)
        settings[c4d.RDATA_RENDERENGINE] = getattr(
            c4d, "RDATA_RENDERENGINE_STANDARD", 0
        )
        settings[c4d.RDATA_XRES] = float(width)
        settings[c4d.RDATA_YRES] = float(height)
        if hasattr(c4d, "RDATA_XRES_VIRTUAL"):
            settings[c4d.RDATA_XRES_VIRTUAL] = float(width)
        if hasattr(c4d, "RDATA_YRES_VIRTUAL"):
            settings[c4d.RDATA_YRES_VIRTUAL] = float(height)
        settings[c4d.RDATA_SAVEIMAGE] = False
        settings[c4d.RDATA_MULTIPASS_ENABLE] = False
        bitmap = c4d.bitmaps.MultipassBitmap(width, height, c4d.COLORMODE_RGB)
        if bitmap is None:
            raise CaptureError("无法创建几何画布位图。")
        bitmap.AddChannel(True, True)
        flags = getattr(c4d, "RENDERFLAGS_EXTERNAL", 0)
        result = c4d.documents.RenderDocument(geometry_doc, settings, bitmap, flags)
        if result != c4d.RENDERRESULT_OK:
            raise CaptureError("硬件预览画布渲染失败，错误码：{}".format(result))
        saved = bitmap.Save(
            str(geometry_path),
            c4d.FILTER_PNG,
            c4d.BaseContainer(),
            c4d.SAVEBIT_NONE,
        )
        if saved != c4d.IMAGERESULT_OK:
            raise CaptureError("无法保存几何画布，错误码：{}".format(saved))
        _validate_nonblank(geometry_path, "当前相机安全框画布")
    finally:
        _restore_viewport(render_draw, render_view_previous)
        try:
            c4d.documents.KillDocument(geometry_doc)
        except (AttributeError, ReferenceError, RuntimeError):
            pass


def render_geometry_reference(
    output_dir: Union[str, Path],
    progress=None,
    model: str = "",
) -> tuple[str, str, dict]:
    source_doc = c4d.documents.GetActiveDocument()
    if source_doc is None:
        raise CaptureError("当前没有活动的C4D文档。")
    active_draw = source_doc.GetActiveBaseDraw()
    if active_draw is None:
        raise CaptureError("无法取得当前透视视图。")
    render_data = source_doc.GetActiveRenderData()
    if render_data is None:
        raise CaptureError("当前文档没有有效渲染设置。")
    settings = render_data.GetData()
    source_width = max(16, int(float(settings[c4d.RDATA_XRES])))
    source_height = max(16, int(float(settings[c4d.RDATA_YRES])))
    model_key = str(model or "").strip().lower().replace("_", "-")
    reference_edge = MODEL_2K_REFERENCE_EDGE.get(
        model_key, DEFAULT_2K_REFERENCE_EDGE
    )
    width, height = _scaled_size(source_width, source_height, reference_edge)
    context = collect_scene_context(source_doc)
    context["capture_source"] = "hardware_preview_render_canvas"
    context["render_size"] = [width, height]
    context["reference_model"] = str(model or "").strip()
    context["reference_quality"] = "2K"
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    geometry_path = target_dir / "geometry_{}.png".format(stamp)
    depth_path = target_dir / "geometry_depth_{}.png".format(stamp)
    depth_error = ""
    capture_started = time.monotonic()
    _append_capture_diagnostic(
        target_dir,
        "mode=render_canvas source={}x{} geometry={}x{}".format(
            source_width, source_height, width, height
        ),
    )
    if progress is not None:
        progress(0.08, "正在按当前相机安全框渲染几何画布")
    _render_current_view_geometry(
        source_doc, active_draw, width, height, geometry_path
    )
    if progress is not None:
        progress(0.62, "正在提取同一相机的真实Z深度")
    try:
        depth_width, depth_height = _scaled_size(
            source_width, source_height, reference_edge
        )
        trace_width, trace_height = _scaled_size(
            source_width, source_height, DEPTH_TRACE_MAX_EDGE
        )
        _render_current_view_depth(
            source_doc,
            active_draw,
            trace_width,
            trace_height,
            depth_path,
            depth_width,
            depth_height,
        )
        if _images_are_effectively_equal(geometry_path, depth_path):
            raise CaptureError("Z深度图与场景图相同，不是真实深度通道。")
    except Exception as exc:
        depth_error = str(exc)
        try:
            depth_path.unlink(missing_ok=True)
        except OSError:
            pass

    _record_capture_dimensions(context, geometry_path)
    if depth_path.is_file():
        context["depth_image"] = str(depth_path)
        context["depth_convention"] = "near_white_far_black"
        context["depth_metric"] = "camera_space_z"
        context["depth_trace_size"] = [trace_width, trace_height]
        context["depth_render_size"] = [depth_width, depth_height]
    elif depth_error:
        context["depth_unavailable_reason"] = depth_error
    _append_capture_diagnostic(
        target_dir,
        "done elapsed={:.3f}s geometry={} depth={} depth_error={}".format(
            time.monotonic() - capture_started,
            geometry_path.name,
            depth_path.name if depth_path.is_file() else "none",
            depth_error or "none",
        ),
    )
    context["geometry_only"] = True
    if progress is not None:
        progress(
            1.0,
            "几何结构与Z深度加载完成"
            if depth_path.is_file()
            else "几何结构加载完成；当前缓存未提供有效Z深度",
        )
    return str(geometry_path), str(depth_path) if depth_path.is_file() else "", context


_original_render_run = _worker.RenderLoadTask.run


def _geometry_render_run(self) -> None:
    if self.workflow != "image":
        return _original_render_run(self)
    try:
        is_main_thread = getattr(c4d.threading, "GeIsMainThread", None)
        if callable(is_main_thread) and not is_main_thread():
            raise RuntimeError("C4D几何捕获必须在主线程执行。")
        geometry, _depth, self.context = render_geometry_reference(
            self.output_dir,
            progress=self._set_progress,
            model=self.model,
        )
        self.paths = [geometry]
    except Exception as exc:
        self.error = str(exc)
    finally:
        with self._lock:
            self.done = True


_worker.RenderLoadTask.run = _geometry_render_run


_original_dialog_init = _ui.StudioDialog.__init__


def _geometry_dialog_init(self, *args, **kwargs) -> None:
    _original_dialog_init(self, *args, **kwargs)
    self.prompt_rows = MAX_PROMPT_ROWS
    self.prompt_height = MAX_PROMPT_HEIGHT


_ui.StudioDialog.__init__ = _geometry_dialog_init


def _fixed_prompt_editor(self) -> None:
    self.prompt_height = max(28, min(MAX_PROMPT_HEIGHT, int(self.prompt_height)))
    self.AddMultiLineEditText(
        _ui.Id.PROMPT,
        c4d.BFH_SCALEFIT,
        0,
        self.prompt_height,
        style=PROMPT_EDITOR_STYLE,
    )
    self._apply_prompt_text_color()


_ui.PROMPT_EDITOR_STYLE = PROMPT_EDITOR_STYLE
_ui.StudioDialog._build_prompt_editor = _fixed_prompt_editor


_original_reference_set = _ui.ReferenceArea.set_references


def _reference_set_with_depth(
    self,
    scene_path: str,
    paths: list[str],
    selected: int,
    workflow: str = "image",
) -> None:
    dialog = getattr(self, "_zhire_dialog", None)
    context = getattr(getattr(dialog, "state", None), "scene_context", {})
    depth_path = ""
    if str(workflow or "image") == "image" and isinstance(context, dict):
        candidate = str(context.get("depth_image") or "")
        if candidate and Path(candidate).is_file():
            depth_path = candidate
    self.depth_path = depth_path
    _original_reference_set(self, scene_path, paths, selected, workflow)
    if depth_path:
        self.bitmaps[depth_path] = self.bitmaps.get(depth_path) or _ui._load_bitmap(
            depth_path
        )
    self.Redraw()


def _reference_geometry_with_depth(
    self, width: int
) -> tuple[int, list[int], Optional[int]]:
    normal_gap = 4
    system_gap = 4
    references_gap = 12 if self.show_scene else normal_gap
    normal_slots = 5 if self.show_scene else 6
    has_depth = bool(
        self.show_scene
        and str(getattr(self, "workflow", "image")) == "image"
        and getattr(self, "depth_path", "")
    )
    system_slots = (1 if self.show_scene else 0) + (1 if has_depth else 0)
    total_slots = normal_slots + system_slots
    gaps = normal_gap * max(0, normal_slots - 1)
    if self.show_scene:
        gaps += references_gap
    if has_depth:
        gaps += system_gap
    tile = max(36, min(64, (max(240, width) - 8 - gaps) // total_slots))
    left = 4
    scene_left = left if self.show_scene else None
    if self.show_scene:
        left += tile
    self.depth_left = None
    if has_depth:
        left += system_gap
        self.depth_left = left
        left += tile
    if self.show_scene:
        left += references_gap
    positions = []
    for _index in range(normal_slots):
        positions.append(left)
        left += tile + normal_gap
    return tile, positions, scene_left


def _draw_reference_area_with_depth(self, x1, y1, x2, y2, message) -> None:
    width, height = self.GetWidth(), self.GetHeight()
    self.OffScreenOn()
    self.DrawSetPen(_ui.COLORS["bg"])
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
            _ui.COLORS["scene"],
            bool(self.allow_delete and self.scene_path),
        )
    depth_left = getattr(self, "depth_left", None)
    depth_path = str(getattr(self, "depth_path", "") or "")
    if depth_left is not None:
        self._draw_tile(
            depth_left,
            tile,
            height,
            depth_path,
            "深度",
            _ui.COLORS.get("mint", _ui.COLORS["cyan"]),
            False,
        )
    if scene_left is not None:
        system_right = (
            depth_left + tile if depth_left is not None else scene_left + tile
        )
        separator_x = system_right + 6
        self.DrawSetPen(_ui.COLORS["line"])
        self.DrawRectangle(separator_x, 8, separator_x + 1, max(9, height - 9))
    for index, left in enumerate(positions):
        if left >= width:
            break
        path = self.paths[index] if index < len(self.paths) else ""
        border = (
            _ui.COLORS["cyan"] if index == self.selected else _ui.COLORS["line"]
        )
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
        self.DrawSetPen(_ui.COLORS["muted"])
        self.DrawText("共 {} 张".format(len(self.paths)), max(6, width - 58), height - 16)
    self.DrawSetPen(_ui.COLORS["line"])
    self.DrawRectangle(0, 0, max(0, width - 1), 1)
    self.DrawRectangle(
        0, max(0, height - 2), max(0, width - 1), max(0, height - 1)
    )
    self.DrawRectangle(0, 0, 1, max(0, height - 1))
    self.DrawRectangle(
        max(0, width - 2), 0, max(0, width - 1), max(0, height - 1)
    )


def _reference_input_with_depth(self, message: c4d.BaseContainer) -> bool:
    if message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE:
        return False
    channel = message.GetInt32(c4d.BFM_INPUT_CHANNEL)
    tile, positions, scene_left = self._geometry(max(1, self.GetWidth()))
    mouse_x, mouse_y = _ui._local_input_position(self, message)
    dialog = _ui._area_dialog(self)
    if channel == getattr(c4d, "BFM_INPUT_MOUSERIGHT", -997):
        path = ""
        if scene_left is not None and scene_left <= mouse_x <= scene_left + tile:
            path = self.scene_path
        else:
            depth_left = getattr(self, "depth_left", None)
            if depth_left is not None and depth_left <= mouse_x <= depth_left + tile:
                path = str(getattr(self, "depth_path", "") or "")
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
        return _ui._show_image_copy_menu(dialog, path)
    if channel != c4d.BFM_INPUT_MOUSELEFT:
        return False
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
    depth_left = getattr(self, "depth_left", None)
    if depth_left is not None and depth_left <= mouse_x <= depth_left + tile:
        if dialog and hasattr(dialog, "depth_open_requested"):
            dialog.depth_open_requested()
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


def _depth_open_requested(self) -> None:
    context = self.state.scene_context if isinstance(self.state.scene_context, dict) else {}
    depth_path = str(context.get("depth_image") or "")
    if depth_path and Path(depth_path).is_file():
        self._show_bitmap_in_viewer(depth_path, "Z深度图（近白远黑）")


_ui.ReferenceArea.set_references = _reference_set_with_depth
_ui.ReferenceArea._geometry = _reference_geometry_with_depth
_ui.ReferenceArea.DrawMsg = _draw_reference_area_with_depth
_ui.ReferenceArea.InputEvent = _reference_input_with_depth
_ui.StudioDialog.depth_open_requested = _depth_open_requested


def _limited_prompt_height(rows: int) -> int:
    return max(28, min(MAX_PROMPT_HEIGHT, int(rows) * 18 + 10))


_ui.StudioDialog._prompt_height_for_rows = staticmethod(_limited_prompt_height)
_original_apply_prompt_rows = _ui.StudioDialog._apply_prompt_rows


def _limited_apply_prompt_rows(self, rows: int) -> None:
    rows = max(1, min(MAX_PROMPT_ROWS, int(rows)))
    if getattr(self, "_prompt_rows_update_active", False):
        return
    self._prompt_rows_update_active = True
    previous = int(getattr(self, "prompt_rows", 0))
    try:
        # Native C4D number gadgets can briefly expose an out-of-range value
        # before Command() reaches Python. Normalize it before rebuilding.
        self.SetInt32(_ui.Id.PROMPT_ROWS, rows, 1, MAX_PROMPT_ROWS, 1)
        _original_apply_prompt_rows(self, rows)
        # The prompt editor is rebuilt inside its own group. Refresh the
        # enclosing scroll layout as well, otherwise C4D can keep stale hit
        # bounds for the spinner and the lower controls.
        if rows != previous:
            try:
                self.LayoutChanged(_ui.Id.GROUP_CONTROLS)
            except (AttributeError, TypeError, RuntimeError):
                pass
    finally:
        # Always write the clamped value back. At the upper/lower bound the
        # original method legitimately does nothing, but the native gadget
        # may otherwise keep showing the rejected value.
        try:
            self.SetInt32(_ui.Id.PROMPT_ROWS, rows, 1, MAX_PROMPT_ROWS, 1)
        except (AttributeError, TypeError, RuntimeError):
            pass
        self._prompt_rows_update_active = False


_ui.StudioDialog._apply_prompt_rows = _limited_apply_prompt_rows


_original_studio_command = _ui.StudioDialog.Command


def _prompt_rows_command_guard(self, item_id: int, message: c4d.BaseContainer) -> bool:
    if item_id == _ui.Id.PROMPT_ROWS:
        # Read the native gadget after its arrow/text event, then route it
        # through the bounded handler instead of allowing the original 1-13
        # range to leak into the visible control.
        try:
            value = self.GetInt32(_ui.Id.PROMPT_ROWS)
        except (AttributeError, TypeError, RuntimeError, ValueError):
            value = getattr(self, "prompt_rows", 1)
        self._apply_prompt_rows(value)
        return True
    return _original_studio_command(self, item_id, message)


_ui.StudioDialog.Command = _prompt_rows_command_guard


_original_init_values = _ui.StudioDialog.InitValues


def _limited_init_values(self) -> bool:
    result = _original_init_values(self)
    self.SetInt32(
        _ui.Id.PROMPT_ROWS,
        max(1, min(MAX_PROMPT_ROWS, int(self.prompt_rows))),
        1,
        MAX_PROMPT_ROWS,
        1,
    )
    return result


_ui.StudioDialog.InitValues = _limited_init_values


# Keep the history list usable while allowing the parent ScrollGroup to take
# over once the list reaches either end. Returning False at the boundary is
# important on C4D builds where a child user area otherwise consumes the wheel.
_original_history_input = _ui.HistoryArea.InputEvent


def _history_input_with_parent_scroll(self, message: c4d.BaseContainer) -> bool:
    if message.GetInt32(c4d.BFM_INPUT_DEVICE) == c4d.BFM_INPUT_MOUSE:
        channel = message.GetInt32(c4d.BFM_INPUT_CHANNEL)
        wheel_channels = {
            getattr(c4d, "BFM_INPUT_MOUSEWHEEL", -999),
            getattr(c4d, "BFM_INPUT_WHEELSCROLL", -998),
        }
        if channel in wheel_channels:
            delta = message.GetInt32(c4d.BFM_INPUT_VALUE)
            visible = max(1, self.GetHeight() // self.ROW_HEIGHT)
            maximum = max(0, len(self.items) - visible)
            at_top = self.offset <= 0 and delta > 0
            at_bottom = self.offset >= maximum and delta < 0
            if at_top or at_bottom:
                return False
    return _original_history_input(self, message)


_ui.HistoryArea.InputEvent = _history_input_with_parent_scroll


_original_finish_render_load = _ui.StudioDialog._finish_render_load


def _geometry_finish_render_load(self) -> None:
    worker = self.render_worker
    context = worker.context if worker is not None else {}
    depth = str(context.get("depth_image") or "")
    is_geometry = context.get("capture_source") == "hardware_preview_render_canvas"
    _original_finish_render_load(self)
    if is_geometry and not self.state.render_busy:
        self.render_button_area.set_label("✓  几何已加载")
        self._set_status(
            "已加载当前透视视图的几何图，并自动附带Z深度约束"
            if depth and Path(depth).is_file()
            else "已加载当前透视视图的几何图；本场景未提交无效Z深度",
            0.0,
        )


_ui.StudioDialog._finish_render_load = _geometry_finish_render_load
