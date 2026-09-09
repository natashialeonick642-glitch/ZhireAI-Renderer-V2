"""Product constants and default settings."""

from __future__ import annotations

import copy
import math
import re
from typing import Optional


PRODUCT_NAME = "挚热AI渲染器"
PRODUCT_WINDOW_TITLE = "挚热AI渲染器 V2"
PRODUCT_VERSION = "2.0.7"

# Seedance reference-video input guardrails.  Atlas surfaces these upstream
# validation limits verbatim, and APIMART receives equivalent motion content
# as sampled still frames.  Keep one shared contract so C4D rendering, manual
# MP4 loading and every provider adapter prepare the same safe canvas.
SEEDANCE_REFERENCE_MIN_PIXELS = 407_696
SEEDANCE_REFERENCE_SAFE_MIN_PIXELS = 420_000
SEEDANCE_REFERENCE_MAX_PIXELS = 8_295_044
SEEDANCE_REFERENCE_MIN_SIDE = 300
SEEDANCE_REFERENCE_MAX_SIDE = 6000
SEEDANCE_REFERENCE_MIN_RATIO = 0.4
SEEDANCE_REFERENCE_MAX_RATIO = 2.5
SEEDANCE_REFERENCE_MIN_FPS = 24.0
SEEDANCE_REFERENCE_MAX_FPS = 60.0
SEEDANCE_REFERENCE_TARGET_EDGE = 1024
SEEDANCE_REFERENCE_MAX_BYTES = 50 * 1024 * 1024

# Development-only ID. A public build must replace this with an ID issued by
# Maxon's Plugin Café: https://developers.maxon.net/
PLUGIN_ID = 1069587
NETWORK_EVENT_ID = PLUGIN_ID + 1
DISCOVERY_EVENT_ID = PLUGIN_ID + 2
PROMPT_EVENT_ID = PLUGIN_ID + 3


DEFAULT_SETTINGS = {
    "provider": "auto",
    "profile_name": "挚热 API 图像模型",
    "base_url": "https://api.juaihub.cn",
    "endpoint": "/v1/images/generations",
    "method": "POST",
    # Live JuAIHub Banana 2 model (the retired nano-banana aliases are not
    # used by the renderer).
    "model": "gemini-3.1-flash-image-preview",
    "models_endpoint": "/v1/models",
    "models_response_path": "data.*.id",
    "media_upload_endpoint": "/api/v1/model/uploadMedia",
    "auth_type": "bearer",
    "api_key": "",
    "api_key_env": "",
    "api_key_header": "Authorization",
    "headers": {"Content-Type": "application/json"},
    "timeout_seconds": 180,
    "request_template": {
        "model": "$MODEL",
        "prompt": "$PROMPT",
        "n": "$COUNT",
        "size": "$SIZE",
        "scene_image": "$SCENE_IMAGE_DATA_URL",
        "reference_images": "$REFERENCE_IMAGES_DATA_URLS",
        "content": "$MULTIMODAL_CONTENT",
    },
    "response_url_path": "data.*.url",
    "response_base64_path": "data.*.b64_json",
    "async_enabled": False,
    "task_id_path": "id",
    "poll_endpoint": "/v1/tasks/$TASK_ID",
    "status_path": "status",
    "progress_path": "progress",
    "success_values": ["succeeded", "completed", "success"],
    "failure_values": ["failed", "cancelled", "canceled", "error"],
    "poll_interval_seconds": 2.0,
    "poll_timeout_seconds": 600,
    "poll_response_url_path": "data.*.url",
    "poll_response_base64_path": "data.*.b64_json",
    "output_extension": ".png",
    # Seedance animation generation uses the same host, authentication and
    # headers as the selected platform, but keeps its model/endpoint contract
    # separate so switching modes never overwrites the image configuration.
    "video_endpoint": "",
    "video_model": "",
    "available_models": [],
    "seedance_models": [],
    # Image and animation capability are tracked independently per provider.
    # A successful image connection must remain usable when that platform does
    # not expose a compatible Seedance reference-to-video contract.
    "supports_seedance_animation": False,
    "seedance_unavailable_reason": "当前平台没有可调用的 Seedance 动画模型，仅可进行 AI 图像渲染生成。",
    "video_request_template": {
        "model": "$VIDEO_MODEL",
        "prompt": "$PROMPT",
        "images": "$ALL_INPUT_MEDIA_URLS",
        "duration": "$DURATION",
        "resolution": "$VIDEO_RESOLUTION",
        "aspect_ratio": "$ASPECT",
    },
    "video_response_url_path": "data.*.url",
    "video_response_base64_path": "data.*.b64_json",
    "video_async_enabled": True,
    "video_task_id_path": "id",
    "video_poll_endpoint": "/v1/tasks/$TASK_ID",
    "video_status_path": "status",
    "video_progress_path": "progress",
    "video_poll_response_url_path": "data.*.url",
    "video_poll_response_base64_path": "data.*.b64_json",
    "video_output_extension": ".mp4",
    "capture_max_edge": 1600,
    "history_limit": 200,
    "prompt_model": "gpt-5.6-sol",
    "prompt_base_url": "https://api.juaihub.cn",
    "prompt_skill": "",
}


def new_default_settings() -> dict:
    """Return an isolated settings dictionary."""

    return copy.deepcopy(DEFAULT_SETTINGS)


def is_seedance_model(model: str) -> bool:
    """Return whether a model ID has a real Seedance model-name segment.

    A substring check incorrectly accepted values such as
    ``not-seedance-disabled``. Provider capability checks rely on this helper,
    so only IDs whose path contains a segment beginning with the documented
    Seedance naming form are accepted.
    """

    value = str(model or "").strip().lower().replace("_", "-")
    if not value:
        return False
    return any(
        re.match(r"^(?:doubao-)?seedance(?:-|\.)\d", segment) is not None
        for segment in value.split("/")
    )


ASPECT_PRESETS = {
    "1:1": (1, 1),
    "5:4": (5, 4),
    "4:5": (4, 5),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "16:10": (16, 10),
    "10:16": (10, 16),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "2:1": (2, 1),
    "1:2": (1, 2),
    "21:9": (21, 9),
    "9:21": (9, 21),
}

QUALITY_PRESETS = {
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}


def output_dimensions(aspect: str, quality: str) -> tuple[int, int]:
    """Calculate dimensions while making the longest edge match the preset."""

    ratio = ASPECT_PRESETS.get(aspect, ASPECT_PRESETS["1:1"])
    edge = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["1K"])
    rw, rh = ratio
    if rw >= rh:
        return edge, max(64, int(round(edge * rh / rw / 8.0) * 8))
    return max(64, int(round(edge * rw / rh / 8.0) * 8)), edge


def output_dimensions_for_ratio(ratio: float, quality: str) -> tuple[int, int]:
    """Preserve an arbitrary captured viewport ratio for automatic output."""

    edge = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["1K"])
    value = max(0.05, min(20.0, float(ratio or 1.0)))
    if value >= 1.0:
        width, height = edge, int(round(edge / value))
    else:
        width, height = int(round(edge * value)), edge
    width = max(64, int(round(width / 8.0)) * 8)
    height = max(64, int(round(height / 8.0)) * 8)
    return width, height


def seedance_reference_issue(
    width: int, height: int, fps: Optional[float] = None
) -> str:
    """Return a concise Chinese explanation when motion input is not legal."""

    source_width = int(width or 0)
    source_height = int(height or 0)
    if source_width <= 0 or source_height <= 0:
        return "无法读取动画参考素材的画面尺寸。"
    ratio = source_width / float(source_height)
    if ratio < SEEDANCE_REFERENCE_MIN_RATIO or ratio > SEEDANCE_REFERENCE_MAX_RATIO:
        return (
            "动画参考素材的画幅过于狭长；宽高比需要在 0.4 到 2.5 之间。"
        )
    if (
        source_width < SEEDANCE_REFERENCE_MIN_SIDE
        or source_height < SEEDANCE_REFERENCE_MIN_SIDE
    ):
        return "动画参考素材的宽和高都需要至少 300 像素。"
    if (
        source_width > SEEDANCE_REFERENCE_MAX_SIDE
        or source_height > SEEDANCE_REFERENCE_MAX_SIDE
    ):
        return "动画参考素材的宽和高不能超过 6000 像素。"
    pixels = source_width * source_height
    if pixels < SEEDANCE_REFERENCE_MIN_PIXELS:
        return (
            "动画参考素材分辨率过低；每帧至少需要 407,696 像素"
            "（16:9 建议使用 1024×576）。"
        )
    if pixels > SEEDANCE_REFERENCE_MAX_PIXELS:
        return "动画参考素材分辨率过高；每帧不能超过 8,295,044 像素。"
    if fps is not None:
        source_fps = float(fps or 0.0)
        if source_fps < SEEDANCE_REFERENCE_MIN_FPS or source_fps > SEEDANCE_REFERENCE_MAX_FPS:
            return "动画参考视频帧率需要在 24–60 FPS 之间。"
    return ""


def seedance_reference_dimensions(
    width: int,
    height: int,
    target_edge: int = SEEDANCE_REFERENCE_TARGET_EDGE,
) -> tuple[int, int]:
    """Return an even, aspect-preserving, Seedance-safe reference size.

    C4D/H.264 prefers even dimensions.  The small safety margin above the
    exact pixel floor prevents a final even-pixel rounding from falling back
    below the server limit on very wide or tall legal aspect ratios.
    """

    source_width = max(1, int(width or 0))
    source_height = max(1, int(height or 0))
    ratio = source_width / float(source_height)
    if ratio < SEEDANCE_REFERENCE_MIN_RATIO or ratio > SEEDANCE_REFERENCE_MAX_RATIO:
        raise ValueError(
            "动画参考素材的宽高比需要在 0.4 到 2.5 之间，"
            "当前为 {:.3f}。".format(ratio)
        )

    pixels = source_width * source_height
    minimum_scale = max(
        SEEDANCE_REFERENCE_MIN_SIDE / float(source_width),
        SEEDANCE_REFERENCE_MIN_SIDE / float(source_height),
        math.sqrt(SEEDANCE_REFERENCE_SAFE_MIN_PIXELS / float(pixels)),
    )
    maximum_scale = min(
        SEEDANCE_REFERENCE_MAX_SIDE / float(source_width),
        SEEDANCE_REFERENCE_MAX_SIDE / float(source_height),
        math.sqrt(SEEDANCE_REFERENCE_MAX_PIXELS / float(pixels)),
    )
    if minimum_scale > maximum_scale + 1e-9:
        raise ValueError("当前动画画幅无法在 Seedance 的尺寸范围内保持原比例。")

    requested_scale = max(2, int(target_edge or SEEDANCE_REFERENCE_TARGET_EDGE)) / float(
        max(source_width, source_height)
    )
    scale = min(max(requested_scale, minimum_scale), maximum_scale)

    def even_up(value: float) -> int:
        return max(2, int(math.ceil(value / 2.0)) * 2)

    target_width = even_up(source_width * scale)
    target_height = even_up(source_height * scale)
    if target_width * target_height < SEEDANCE_REFERENCE_SAFE_MIN_PIXELS:
        safety_scale = math.sqrt(
            SEEDANCE_REFERENCE_SAFE_MIN_PIXELS
            / float(target_width * target_height)
        )
        target_width = even_up(target_width * safety_scale)
        target_height = even_up(target_height * safety_scale)

    # At the exact 0.4/2.5 boundaries, independently rounding both H.264
    # dimensions upward can move the result a fraction outside the legal
    # ratio. Expand only the shorter side by at most a few pixels.
    if target_width / float(target_height) > SEEDANCE_REFERENCE_MAX_RATIO:
        target_height = even_up(
            target_width / SEEDANCE_REFERENCE_MAX_RATIO
        )
    elif target_width / float(target_height) < SEEDANCE_REFERENCE_MIN_RATIO:
        target_width = even_up(
            target_height * SEEDANCE_REFERENCE_MIN_RATIO
        )

    issue = seedance_reference_issue(target_width, target_height)
    if issue:
        raise ValueError(issue)
    return target_width, target_height
