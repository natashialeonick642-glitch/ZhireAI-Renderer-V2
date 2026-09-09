"""Cinema 4D scene inspection, document rendering, and viewport capture."""

from __future__ import annotations

import binascii
import math
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Callable, Optional, Union

import c4d

from .constants import (
    SEEDANCE_REFERENCE_MAX_BYTES,
    SEEDANCE_REFERENCE_TARGET_EDGE,
    seedance_reference_dimensions,
)


# Cinema 4D's built-in MP4 bitmap saver plug-in ID. Maxon's SDK examples use
# this value for RDATA_FORMAT when rendering an animation movie.
MP4_FILTER_ID = getattr(c4d, "FILTER_MOVIE", 1125)
ANIMATION_PREVIEW_MAX_EDGE = SEEDANCE_REFERENCE_TARGET_EDGE
ANIMATION_PREVIEW_MAX_FPS = 30
ANIMATION_PREVIEW_MIN_REFERENCE_FPS = 24
ANIMATION_PREVIEW_MAX_SECONDS = 15
ANIMATION_PREVIEW_MAX_BYTES = SEEDANCE_REFERENCE_MAX_BYTES


class CaptureError(RuntimeError):
    pass


def _vector_data(value: c4d.Vector) -> list[float]:
    return [float(value.x), float(value.y), float(value.z)]


def _frame_data(frame: dict) -> dict[str, int]:
    return {
        "left": int(frame.get("cl", 0)),
        "top": int(frame.get("ct", 0)),
        "right": int(frame.get("cr", 0)),
        "bottom": int(frame.get("cb", 0)),
    }


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", binascii.crc32(payload) & 0xFFFFFFFF)
    )


def _save_bgra_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    """Write top-down BGRA pixels as a compact RGBA PNG."""

    stride = width * 4
    # Convert whole color channels with bytearray extended slices. This avoids
    # a Python loop for every pixel (millions of iterations on a large C4D
    # viewport) while preserving the exact visible viewport capture.
    rgba = bytearray(pixels[: stride * height])
    blue = rgba[0::4]
    red = rgba[2::4]
    rgba[0::4] = red
    rgba[2::4] = blue
    rgba[3::4] = b"\xff" * (width * height)
    raw = bytearray((stride + 1) * height)
    target = 0
    for y in range(height):
        raw[target] = 0
        target += 1
        source = y * stride
        raw[target : target + stride] = rgba[source : source + stride]
        target += stride
    content = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(content)


def _record_capture_dimensions(context: dict, path: Union[str, Path]) -> None:
    """Store the actual saved PNG dimensions used as the AI composition guide."""

    try:
        header = Path(path).read_bytes()[:24]
        if header[:8] != b"\x89PNG\r\n\x1a\n" or len(header) < 24:
            return
        width, height = struct.unpack(">II", header[16:24])
        if width < 1 or height < 1:
            return
        context["capture_size"] = [int(width), int(height)]
        context["capture_aspect"] = round(width / float(height), 6)
    except (OSError, ValueError, struct.error):
        return


def _active_view_screen_rect(active_draw: c4d.BaseDraw) -> tuple[int, int, int, int]:
    """Resolve the visible active viewport rectangle in physical screen pixels."""

    frame = active_draw.GetFrame()
    width = int(frame.get("cr", 0) - frame.get("cl", 0))
    height = int(frame.get("cb", 0) - frame.get("ct", 0))
    if width < 64 or height < 64:
        raise CaptureError("当前活动视图尺寸无效。")
    try:
        origin = active_draw.GetEditorWindow().Local2Screen()
        if isinstance(origin, dict):
            origin_x, origin_y = int(origin.get("x", 0)), int(origin.get("y", 0))
        else:
            origin_x, origin_y = int(origin[0]), int(origin[1])
        left = origin_x + int(frame.get("cl", 0))
        top = origin_y + int(frame.get("ct", 0))
        return left, top, width, height
    except (AttributeError, RuntimeError, TypeError, ValueError, IndexError):
        screen_frame = active_draw.GetFrameScreen()
        if not screen_frame:
            raise CaptureError("无法定位当前活动视图的屏幕区域。")
        left = int(screen_frame.get("cl", 0))
        top = int(screen_frame.get("ct", 0))
        width = int(screen_frame.get("cr", 0) - left)
        height = int(screen_frame.get("cb", 0) - top)
        if width < 64 or height < 64:
            raise CaptureError("当前活动视图的屏幕区域无效。")
        return left, top, width, height


def _capture_windows_viewport(
    active_draw: c4d.BaseDraw, output_dir: Union[str, Path], max_edge: int
) -> str:
    """Capture the exact visible active viewport pixels on Windows."""

    if sys.platform != "win32":
        raise CaptureError("精确视图像素捕获当前仅用于 Windows。")
    import ctypes
    from ctypes import wintypes

    left, top, source_width, source_height = _active_view_screen_rect(active_draw)
    width, height = _scaled_size(source_width, source_height, max(256, int(max_edge)))

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 1)]

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.StretchBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BitmapInfo),
        wintypes.UINT,
    ]

    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap_handle = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    old_object = gdi32.SelectObject(memory_dc, bitmap_handle)
    try:
        gdi32.SetStretchBltMode(memory_dc, 4)  # HALFTONE
        copied = gdi32.StretchBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            left,
            top,
            source_width,
            source_height,
            0x00CC0020 | 0x40000000,  # SRCCOPY | CAPTUREBLT
        )
        if not copied:
            raise CaptureError("Windows 无法读取当前活动视图像素。")
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            memory_dc, bitmap_handle, 0, height, buffer, ctypes.byref(info), 0
        )
        if rows != height:
            raise CaptureError("Windows 读取当前活动视图像素不完整。")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "scene_{0}.png".format(int(time.time() * 1000))
        _save_bgra_png(path, width, height, buffer.raw)
        return str(path)
    finally:
        if old_object:
            gdi32.SelectObject(memory_dc, old_object)
        if bitmap_handle:
            gdi32.DeleteObject(bitmap_handle)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)


def _scaled_size(width: int, height: int, max_edge: int) -> tuple[int, int]:
    width = max(64, int(width))
    height = max(64, int(height))
    maximum = max(width, height)
    if maximum <= max_edge:
        return width, height
    scale = float(max_edge) / float(maximum)
    return max(64, int(width * scale)), max(64, int(height * scale))


def _animation_preview_size(width: int, height: int) -> tuple[int, int]:
    """Fit an AI motion guide to a legal Seedance reference-video canvas."""

    try:
        return seedance_reference_dimensions(
            width,
            height,
            ANIMATION_PREVIEW_MAX_EDGE,
        )
    except ValueError as exc:
        raise CaptureError(str(exc)) from exc


def _animation_preview_timing(
    start: int, end: int, source_fps: int
) -> tuple[int, float, int]:
    """Return a duration-safe render end, output FPS, and source frame step.

    The movie encoder must use ``source_fps / frame_step``. Raising a 12 FPS
    project to 24 FPS, or sampling a 48 FPS project every second frame while
    encoding at 30 FPS, changes playback speed. Keeping these values paired
    preserves C4D timeline speed. When no integer sampling step can stay at or
    below the 30 FPS performance target, retain the source FPS up to the
    Seedance 60 FPS ceiling instead of accidentally producing an invalid
    15.5–23.5 FPS reference movie.
    """

    fps = max(1, int(source_fps))
    frame_step = max(
        1,
        int(math.floor(fps / float(ANIMATION_PREVIEW_MIN_REFERENCE_FPS))),
    )
    preview_fps = fps / float(frame_step)
    max_output_frames = max(
        1, int(math.floor(preview_fps * ANIMATION_PREVIEW_MAX_SECONDS))
    )
    capped_end = min(int(end), int(start) + (max_output_frames - 1) * frame_step)
    return capped_end, preview_fps, frame_step


def collect_scene_context(doc: c4d.documents.BaseDocument) -> dict:
    """Return compact, privacy-conscious scene metadata for an API template."""

    if doc is None:
        return {}
    top_level = []
    object_count = 0
    node = doc.GetFirstObject()
    stack = []
    while node is not None or stack:
        if node is None:
            node = stack.pop()
            continue
        object_count += 1
        if node.GetUp() is None and len(top_level) < 24:
            top_level.append({"name": node.GetName(), "type": node.GetTypeName()})
        child = node.GetDown()
        sibling = node.GetNext()
        if sibling is not None:
            stack.append(sibling)
        node = child

    selected = [item.GetName() for item in doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_NONE)]
    active_draw = doc.GetActiveBaseDraw()
    camera = None
    if active_draw is not None:
        camera = (
            active_draw.GetSceneCamera(doc)
            if active_draw.HasCameraLink()
            else active_draw.GetEditorCamera()
        )
    camera_data = {}
    if camera is not None:
        matrix = active_draw.GetMg()
        camera_data = {
            "name": camera.GetName(),
            "position": _vector_data(matrix.off),
            "axis_x": _vector_data(matrix.v1),
            "axis_y": _vector_data(matrix.v2),
            "axis_z": _vector_data(matrix.v3),
            "projection": int(active_draw.GetProjection()),
        }
        try:
            offset, scale, scale_z = active_draw.GetViewParameter()
            camera_data["view_offset"] = _vector_data(offset)
            camera_data["view_scale"] = _vector_data(scale)
            camera_data["view_scale_z"] = _vector_data(scale_z)
        except (AttributeError, TypeError, ValueError):
            pass
        for constant_name, key in (
            ("CAMERA_FOCUS", "focal_length"),
            ("CAMERAOBJECT_APERTURE", "sensor_aperture"),
            ("CAMERA_ZOOM", "zoom"),
        ):
            parameter_id = getattr(c4d, constant_name, None)
            if parameter_id is None:
                continue
            try:
                camera_data[key] = float(camera[parameter_id])
            except (TypeError, ValueError):
                pass

    frame = active_draw.GetFrame()
    safe_frame = active_draw.GetSafeFrame()

    return {
        "document": doc.GetDocumentName() or "Untitled",
        "frame": int(doc.GetTime().GetFrame(doc.GetFps())),
        "object_count": object_count,
        "material_count": len(doc.GetMaterials()),
        "selected_objects": selected[:24],
        "top_level_objects": top_level,
        "viewport_frame": _frame_data(frame),
        "safe_frame": _frame_data(safe_frame),
        "viewport_aspect": round(
            max(1, frame.get("cr", 0) - frame.get("cl", 0))
            / float(max(1, frame.get("cb", 0) - frame.get("ct", 0))),
            6,
        ),
        "camera": camera_data,
    }


def render_active_document(
    output_dir: Union[str, Path],
    progress: Optional[Callable[[float, str], None]] = None,
    filename_prefix: str = "render",
    document=None,
) -> tuple[str, dict, c4d.bitmaps.MultipassBitmap]:
    """Render the active view with the document's configured render engine.

    Unlike :func:`capture_active_view`, this calls Cinema 4D's real document
    renderer. The active scene camera is used directly; when the user is in an
    editor perspective, its editor camera is cloned temporarily so the render
    keeps the camera angle the user confirmed in the viewport.
    """

    doc = document or c4d.documents.GetActiveDocument()
    if doc is None:
        raise CaptureError("当前没有活动的 C4D 文档。")
    active_draw = doc.GetActiveBaseDraw()
    render_draw = doc.GetRenderBaseDraw()
    render_data = doc.GetActiveRenderData()
    if active_draw is None or render_draw is None:
        raise CaptureError("无法取得当前 C4D 视图。")
    if render_data is None:
        raise CaptureError("文档没有有效的渲染设置。")

    settings = render_data.GetData()
    width = max(1, int(float(settings[c4d.RDATA_XRES])))
    height = max(1, int(float(settings[c4d.RDATA_YRES])))
    if width < 16 or height < 16:
        raise CaptureError("渲染设置的输出尺寸无效：{}×{}".format(width, height))

    source_camera = (
        active_draw.GetSceneCamera(doc)
        if active_draw.HasCameraLink()
        else active_draw.GetEditorCamera()
    )
    if source_camera is None:
        raise CaptureError("无法取得当前视图相机。")

    context = collect_scene_context(doc)
    context["capture_source"] = "document_renderer"
    context["render_size"] = [width, height]
    try:
        context["render_engine"] = int(settings[c4d.RDATA_RENDERENGINE])
    except (TypeError, ValueError):
        pass

    old_camera = render_draw.GetSceneCamera(doc) if render_draw.HasCameraLink() else None
    temporary_camera = None
    try:
        if active_draw.HasCameraLink():
            render_draw.SetSceneCamera(source_camera)
        else:
            c4d.StopAllThreads()
            temporary_camera = source_camera.GetClone()
            if temporary_camera is None:
                raise CaptureError("无法复制当前编辑器相机。")
            temporary_camera.SetName("__ZhireAIRenderCamera__")
            doc.InsertObject(temporary_camera)
            render_draw.SetSceneCamera(temporary_camera)

        # Keep the user's renderer, quality, effects, and output resolution,
        # while preventing the temporary reference render from writing to the
        # document's configured production output path.
        settings[c4d.RDATA_SAVEIMAGE] = False
        if hasattr(c4d, "RDATA_GLOBALSAVE"):
            settings[c4d.RDATA_GLOBALSAVE] = False

        bitmap = c4d.bitmaps.MultipassBitmap(width, height, c4d.COLORMODE_RGB)
        if bitmap is None:
            raise CaptureError("无法创建渲染位图。")
        bitmap.AddChannel(True, True)
        render_flags = getattr(c4d, "RENDERFLAGS_EXTERNAL", 0)
        def render_progress(value, progress_type):
            if progress is None:
                return
            names = {
                getattr(c4d, "RENDERPROGRESSTYPE_BEFORERENDERING", -1): "准备渲染",
                getattr(c4d, "RENDERPROGRESSTYPE_DURINGRENDERING", -2): "正在渲染",
                getattr(c4d, "RENDERPROGRESSTYPE_AFTERRENDERING", -3): "整理结果",
                getattr(c4d, "RENDERPROGRESSTYPE_GLOBALILLUMINATION", -4): "计算全局光照",
                getattr(c4d, "RENDERPROGRESSTYPE_QUICK_PREVIEW", -5): "快速预览",
                getattr(c4d, "RENDERPROGRESSTYPE_AMBIENTOCCLUSION", -6): "计算环境吸收",
            }
            progress(
                0.03 + max(0.0, min(1.0, float(value))) * 0.91,
                names.get(progress_type, "正在渲染"),
            )

        if progress is not None:
            progress(0.01, "准备当前 C4D 场景")
        result = c4d.documents.RenderDocument(
            doc,
            settings,
            bitmap,
            render_flags,
            # C4D 2023 accepts only a native BaseThread here. A Python
            # C4DThread subclass is not a valid value for this parameter.
            th=None,
            prog=render_progress,
        )
        if result != c4d.RENDERRESULT_OK:
            raise CaptureError("C4D 渲染失败，错误码：{}".format(result))

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_prefix = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in str(filename_prefix or "render")
        )
        path = target_dir / "{0}_{1}.png".format(
            safe_prefix or "render", int(time.time() * 1000)
        )
        if progress is not None:
            progress(0.96, "正在保存渲染图")
        saved = bitmap.Save(
            str(path), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
        )
        if saved != c4d.IMAGERESULT_OK:
            raise CaptureError("无法保存 C4D 渲染图，错误码：{}".format(saved))
        _record_capture_dimensions(context, path)
        if progress is not None:
            progress(1.0, "渲染加载完成")
        return str(path), context, bitmap
    finally:
        try:
            render_draw.SetSceneCamera(old_camera)
        except ReferenceError:
            pass
        if temporary_camera is not None:
            try:
                temporary_camera.Remove()
            except ReferenceError:
                pass
        c4d.EventAdd(getattr(c4d, "EVENT_ENQUEUE_REDRAW", 0))


def _document_time_range(doc) -> tuple[int, int, int]:
    """Return the visible animation range as start/end frame plus FPS."""

    fps = max(1, int(doc.GetFps()))
    get_start = getattr(doc, "GetLoopMinTime", None) or getattr(doc, "GetMinTime", None)
    get_end = getattr(doc, "GetLoopMaxTime", None) or getattr(doc, "GetMaxTime", None)
    if not callable(get_start) or not callable(get_end):
        current = int(doc.GetTime().GetFrame(fps))
        return current, current, fps
    start = int(get_start().GetFrame(fps))
    end = int(get_end().GetFrame(fps))
    if end < start:
        start, end = end, start
    return start, end, fps


def _sample_frames(start: int, end: int, count: int) -> list[int]:
    if end <= start:
        return [start]
    count = max(2, min(max(2, end - start + 1), int(count)))
    frames = []
    for index in range(count):
        value = int(round(start + (end - start) * index / float(count - 1)))
        if value not in frames:
            frames.append(value)
    return frames


def _render_active_animation_mp4(
    output_dir: Union[str, Path],
    progress: Optional[Callable[[float, str], None]] = None,
) -> tuple[list[str], dict]:
    """Create a compact MP4 with C4D's current full document renderer.

    Cinema 4D does not expose the Make Preview dialog parameters to Python.
    This is the supported equivalent of its ``完全渲染`` mode: the active
    production renderer, current preview range, compact aspect-preserving
    output, and MP4 encoding.
    """

    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        raise CaptureError("当前没有活动的 C4D 文档。")
    render_data = doc.GetActiveRenderData()
    active_draw = doc.GetActiveBaseDraw()
    render_draw = doc.GetRenderBaseDraw()
    if render_data is None or active_draw is None or render_draw is None:
        raise CaptureError("无法取得当前 C4D 动画渲染设置。")

    render_clone = render_data.GetClone()
    if render_clone is None:
        raise CaptureError("无法复制当前 C4D 渲染设置。")
    settings = render_clone.GetData()

    start, end, fps = _document_time_range(doc)
    if end <= start:
        raise CaptureError("当前动画时间范围只有一帧，请先设置动画起止帧。")
    source_end = end
    end, preview_fps, frame_step = _animation_preview_timing(start, end, fps)

    source_width = max(16, int(float(settings[c4d.RDATA_XRES])))
    source_height = max(16, int(float(settings[c4d.RDATA_YRES])))
    width, height = _animation_preview_size(source_width, source_height)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_base = target_dir / "c4d_animation_preview_{}".format(
        int(time.time() * 1000)
    )
    target = target_base.with_suffix(".mp4")

    try:
        render_engine = int(settings[c4d.RDATA_RENDERENGINE])
    except (TypeError, ValueError):
        render_engine = -1
    settings[c4d.RDATA_XRES] = float(width)
    settings[c4d.RDATA_YRES] = float(height)
    if hasattr(c4d, "RDATA_XRES_VIRTUAL"):
        settings[c4d.RDATA_XRES_VIRTUAL] = float(width)
    if hasattr(c4d, "RDATA_YRES_VIRTUAL"):
        settings[c4d.RDATA_YRES_VIRTUAL] = float(height)
    settings[c4d.RDATA_SAVEIMAGE] = True
    if hasattr(c4d, "RDATA_GLOBALSAVE"):
        settings[c4d.RDATA_GLOBALSAVE] = True
    if hasattr(c4d, "RDATA_MULTIPASS_SAVEIMAGE"):
        settings[c4d.RDATA_MULTIPASS_SAVEIMAGE] = False
    settings[c4d.RDATA_FORMAT] = MP4_FILTER_ID
    settings[c4d.RDATA_FRAMESEQUENCE] = c4d.RDATA_FRAMESEQUENCE_MANUAL
    settings[c4d.RDATA_FRAMEFROM] = c4d.BaseTime(start, fps)
    settings[c4d.RDATA_FRAMETO] = c4d.BaseTime(end, fps)
    settings[c4d.RDATA_FRAMESTEP] = frame_step
    if hasattr(c4d, "RDATA_FRAMERATE"):
        settings[c4d.RDATA_FRAMERATE] = float(preview_fps)
    if hasattr(c4d, "RDATA_FORMATDEPTH"):
        settings[c4d.RDATA_FORMATDEPTH] = getattr(c4d, "RDATA_FORMATDEPTH_8", 0)
    if hasattr(c4d, "RDATA_ALPHACHANNEL"):
        settings[c4d.RDATA_ALPHACHANNEL] = False
    set_filename = getattr(settings, "SetFilename", None)
    if callable(set_filename):
        set_filename(c4d.RDATA_PATH, str(target_base))
    else:
        settings[c4d.RDATA_PATH] = str(target_base)

    source_camera = (
        active_draw.GetSceneCamera(doc)
        if active_draw.HasCameraLink()
        else active_draw.GetEditorCamera()
    )
    if source_camera is None:
        raise CaptureError("无法取得当前视图相机。")
    old_camera = render_draw.GetSceneCamera(doc) if render_draw.HasCameraLink() else None
    temporary_camera = None
    original_time = doc.GetTime()
    started_at = time.time()
    try:
        if active_draw.HasCameraLink():
            render_draw.SetSceneCamera(source_camera)
        else:
            c4d.StopAllThreads()
            temporary_camera = source_camera.GetClone()
            if temporary_camera is None:
                raise CaptureError("无法复制当前编辑器相机。")
            temporary_camera.SetName("__ZhireAIAnimationCamera__")
            doc.InsertObject(temporary_camera)
            render_draw.SetSceneCamera(temporary_camera)

        bitmap = c4d.bitmaps.MultipassBitmap(width, height, c4d.COLORMODE_RGB)
        if bitmap is None:
            raise CaptureError("无法创建动画渲染位图。")
        bitmap.AddChannel(True, True)

        def movie_progress(value, progress_type):
            if progress is not None:
                progress(
                    0.02 + max(0.0, min(1.0, float(value))) * 0.95,
                    "正在以完全渲染模式创建 MP4",
                )

        if progress is not None:
            progress(0.01, "准备 C4D 完全渲染动画")
        result = c4d.documents.RenderDocument(
            doc,
            settings,
            bitmap,
            getattr(c4d, "RENDERFLAGS_EXTERNAL", 0),
            th=None,
            prog=movie_progress,
        )
        if result != c4d.RENDERRESULT_OK:
            raise CaptureError("C4D MP4 动画渲染失败，错误码：{}".format(result))

        candidates = [target, target_base, Path(str(target_base) + ".mp4")]
        candidates.extend(
            sorted(
                target_dir.glob(target_base.name + "*.mp4"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        )
        movie_path = next(
            (
                item
                for item in candidates
                if item.is_file() and item.stat().st_size > 0 and item.stat().st_mtime >= started_at - 2.0
            ),
            None,
        )
        if movie_path is None:
            raise CaptureError("C4D 已完成渲染，但没有找到输出的 MP4 文件。")
        if movie_path.stat().st_size > ANIMATION_PREVIEW_MAX_BYTES:
            raise CaptureError("完全渲染 MP4 超过 Seedance 的 50MB 参考视频限制。")
        if movie_path.suffix.lower() != ".mp4":
            try:
                movie_path.rename(target)
                movie_path = target
            except OSError as exc:
                raise CaptureError("无法整理 C4D 动画预览文件：{}".format(exc))
        context = collect_scene_context(doc)
        context.update(
            {
                "capture_source": "document_animation_preview_mp4",
                "workflow": "animation",
                "animation_frame_range": [start, end],
                "animation_source_frame_range": [start, source_end],
                "animation_preview_trimmed": bool(end < source_end),
                "animation_source_fps": fps,
                "animation_preview_fps": preview_fps,
                "animation_frame_step": frame_step,
                "animation_preview_mode": "full_render",
                "render_engine": render_engine,
                "capture_size": [width, height],
                "capture_aspect": round(width / float(height), 6),
                "render_size": [width, height],
                "animation_movie": str(movie_path),
            }
        )
        if progress is not None:
            progress(1.0, "完全渲染 MP4 已自动加载")
        return [str(movie_path)], context
    finally:
        try:
            render_draw.SetSceneCamera(old_camera)
        except ReferenceError:
            pass
        if temporary_camera is not None:
            try:
                temporary_camera.Remove()
            except ReferenceError:
                pass
        try:
            doc.SetTime(original_time)
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        c4d.EventAdd(getattr(c4d, "EVENT_ENQUEUE_REDRAW", 0))


def render_active_animation(
    output_dir: Union[str, Path],
    sample_count: int = 5,
    progress: Optional[Callable[[float, str], None]] = None,
) -> tuple[list[str], dict]:
    """Create a compact preview MP4, with ordered white-model frames as fallback."""

    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        raise CaptureError("当前没有活动的 C4D 文档。")
    movie_fallback_reason = ""
    try:
        return _render_active_animation_mp4(output_dir, progress)
    except Exception as exc:
        movie_fallback_reason = str(exc)
        if progress is not None:
            progress(0.01, "动画预览 MP4 不可用，改用白模动画帧")
    start, end, fps = _document_time_range(doc)
    if end <= start:
        raise CaptureError("当前动画时间范围只有一帧，请先设置动画起止帧。")
    frames = _sample_frames(start, end, sample_count)
    original_time = doc.GetTime()
    paths: list[str] = []
    animation_context = collect_scene_context(doc)
    animation_context.update(
        {
            "capture_source": "document_animation_keyframes",
            "workflow": "animation",
            "animation_frame_range": [start, end],
            "animation_sample_frames": frames,
            "animation_source_fps": fps,
        }
    )
    if movie_fallback_reason:
        animation_context["movie_fallback_reason"] = movie_fallback_reason
    try:
        for index, frame in enumerate(frames):
            doc.SetTime(c4d.BaseTime(frame, fps))
            execute_passes = getattr(doc, "ExecutePasses", None)
            if callable(execute_passes):
                try:
                    execute_passes(
                        None,
                        True,
                        True,
                        True,
                        getattr(c4d, "BUILDFLAGS_NONE", 0),
                    )
                except (AttributeError, RuntimeError, TypeError):
                    pass

            def frame_progress(value, message, current=index):
                if progress is not None:
                    overall = (current + max(0.0, min(1.0, value))) / float(len(frames))
                    progress(
                        overall,
                        "动画帧 {}/{} · {}".format(current + 1, len(frames), message),
                    )

            path, context, _bitmap = render_active_document(
                output_dir,
                progress=frame_progress,
                filename_prefix="animation_f{:04d}".format(frame),
                document=doc,
            )
            paths.append(path)
            if index == 0:
                for key in ("render_size", "render_engine", "capture_size", "capture_aspect"):
                    if key in context:
                        animation_context[key] = context[key]
        if progress is not None:
            progress(1.0, "白模动画加载完成")
        return paths, animation_context
    finally:
        doc.SetTime(original_time)
        c4d.EventAdd(getattr(c4d, "EVENT_ENQUEUE_REDRAW", 0))


def capture_active_view(
    output_dir: Union[str, Path], max_edge: int = 1600
) -> tuple[str, dict]:
    """Mimic the active viewport with C4D's hardware preview renderer.

    The Python SDK does not expose ``BaseDraw.GetViewportImage``. We therefore
    clone the active editor camera temporarily and render through the supported
    hardware preview path. The document is restored immediately afterwards.
    """

    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        raise CaptureError("当前没有活动的 C4D 文档。")
    active_draw = doc.GetActiveBaseDraw()
    render_draw = doc.GetRenderBaseDraw()
    if active_draw is None or render_draw is None:
        raise CaptureError("无法取得当前 C4D 视图。")

    context = collect_scene_context(doc)
    try:
        path = _capture_windows_viewport(active_draw, output_dir, max_edge)
        context["capture_source"] = "active_viewport_pixels"
        _record_capture_dimensions(context, path)
        return path, context
    except CaptureError as direct_error:
        context["capture_source"] = "hardware_preview_fallback"
        context["capture_fallback_reason"] = str(direct_error)

    frame = active_draw.GetFrame()
    width = int(frame.get("cr", 0) - frame.get("cl", 0))
    height = int(frame.get("cb", 0) - frame.get("ct", 0))
    width, height = _scaled_size(width, height, max(256, int(max_edge)))

    source_camera = (
        active_draw.GetSceneCamera(doc)
        if active_draw.HasCameraLink()
        else active_draw.GetEditorCamera()
    )
    if source_camera is None:
        raise CaptureError("无法取得当前视图相机。")

    old_camera = render_draw.GetSceneCamera(doc) if render_draw.HasCameraLink() else None
    temporary_camera = None
    try:
        # A linked scene camera can be used directly. The editor camera is not a
        # document object, so clone it into the scene for the duration of render.
        if active_draw.HasCameraLink():
            render_draw.SetSceneCamera(source_camera)
        else:
            c4d.StopAllThreads()
            temporary_camera = source_camera.GetClone()
            if temporary_camera is None:
                raise CaptureError("无法复制当前编辑器相机。")
            temporary_camera.SetName("__LumaStageCaptureCamera__")
            doc.InsertObject(temporary_camera)
            render_draw.SetSceneCamera(temporary_camera)

        render_data = doc.GetActiveRenderData()
        if render_data is None:
            raise CaptureError("文档没有有效的渲染设置。")
        settings = render_data.GetData()
        settings[c4d.RDATA_RENDERENGINE] = c4d.RDATA_RENDERENGINE_PREVIEWHARDWARE
        settings[c4d.RDATA_XRES] = float(width)
        settings[c4d.RDATA_YRES] = float(height)
        settings[c4d.RDATA_XRES_VIRTUAL] = float(width)
        settings[c4d.RDATA_YRES_VIRTUAL] = float(height)
        settings[c4d.RDATA_SAVEIMAGE] = False

        bitmap = c4d.bitmaps.MultipassBitmap(width, height, c4d.COLORMODE_RGB)
        if bitmap is None:
            raise CaptureError("无法创建视图位图。")
        bitmap.AddChannel(True, True)
        render_flags = getattr(c4d, "RENDERFLAGS_EXTERNAL", 0) | getattr(
            c4d, "RENDERFLAGS_PREVIEWRENDER", 0
        )
        result = c4d.documents.RenderDocument(
            doc,
            settings,
            bitmap,
            render_flags,
        )
        if result != c4d.RENDERRESULT_OK:
            raise CaptureError("硬件预览捕获失败，错误码：{}".format(result))

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "scene_{0}.png".format(int(time.time() * 1000))
        saved = bitmap.Save(
            str(path), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_NONE
        )
        if saved != c4d.IMAGERESULT_OK:
            raise CaptureError("无法保存捕获的视图，错误码：{}".format(saved))
        _record_capture_dimensions(context, path)
        return str(path), context
    finally:
        try:
            render_draw.SetSceneCamera(old_camera)
        except ReferenceError:
            pass
        if temporary_camera is not None:
            try:
                temporary_camera.Remove()
            except ReferenceError:
                pass
        c4d.EventAdd(getattr(c4d, "EVENT_ENQUEUE_REDRAW", 0))
