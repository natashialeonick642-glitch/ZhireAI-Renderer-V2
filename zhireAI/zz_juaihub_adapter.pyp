"""JuAIHub image adapter for ZhireAI Renderer V2.

This companion leaves the vendor plug-in untouched. It only replaces the
image request path when the active custom profile points to api.juaihub.cn.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import mimetypes
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from lumastage import api as _api


BANANA_MODELS = {
    # Retain these IDs only as an in-memory compatibility bridge for a C4D
    # session opened before the settings migration. They are never listed in
    # the UI and are converted to the live Gemini IDs before requesting.
    "nano-banana-2",
    "nano-banana-2-cl",
    "nano-banana-pro",
    "nano-banana-pro-cl",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
}
IMAGE2_MODEL = "gpt-image-2"
# GPT Image 2.5 is exposed by JuAIHub through the OpenAI-compatible Images
# API.  Keep the two official variants explicit so a generic model listing
# cannot accidentally route an unrelated text model into this adapter.
IMAGE25_MODELS = {
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
}
SUPPORTED_MODELS = BANANA_MODELS | {IMAGE2_MODEL} | IMAGE25_MODELS
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 100 * 1024 * 1024


def _write_diagnostic(
    client: _api.ImageApiClient,
    stage: str,
    model: str = "",
    endpoint: str = "",
    detail: str = "",
) -> None:
    """Append request stages without prompts, image paths, or credentials."""

    output_dir = client.output_dir
    if output_dir is None:
        return
    try:
        path = Path(output_dir) / "juaihub_adapter_diagnostics.log"
        host = (
            urllib.parse.urlsplit(str(client.settings.get("base_url") or "")).hostname
            or ""
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                "{} stage={} host={} model={} endpoint={}{}\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    stage,
                    host,
                    model,
                    endpoint,
                    " " + detail if detail else "",
                )
            )
    except OSError:
        pass


def _is_juaihub(client: _api.ImageApiClient, job: dict) -> bool:
    base_url = str(client.settings.get("base_url") or "").strip()
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower().rstrip(".")
    model = str(job.get("model") or client.settings.get("model") or "").strip()
    return (
        host == "api.juaihub.cn"
        and str(job.get("workflow") or "image") == "image"
        and model in SUPPORTED_MODELS
    )


def _input_bundle(job: dict) -> tuple[list[str], dict[int, int], int]:
    paths: list[str] = []
    reference_ordinals: dict[int, int] = {}
    scene = str(job.get("scene_image") or "").strip()
    if scene:
        paths.append(scene)

    references = [str(path) for path in job.get("references") or []]
    indices = sorted(set(_api.referenced_indices(str(job.get("prompt") or ""))))
    for index in indices:
        if 1 <= index <= len(references):
            path = references[index - 1]
            if not path:
                continue
            if path not in paths:
                paths.append(path)
            reference_ordinals[index] = paths.index(path) + 1

    scene_context = job.get("scene_context") or {}
    depth = str(scene_context.get("depth_image") or "").strip()
    depth_ordinal = 0
    if scene and depth:
        if depth not in paths:
            paths.append(depth)
        depth_ordinal = paths.index(depth) + 1

    # Image API generations are valid without input images.  Reference-based
    # C4D jobs still populate this list with the scene/depth/reference files
    # and are sent to the edits endpoint below.
    for path in paths:
        source = Path(path)
        if not source.is_file():
            raise _api.ApiError("参考图不存在：{}".format(path))
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise _api.ApiError("单张参考图超过 20MB，请压缩后重试。")
    return paths, reference_ordinals, depth_ordinal


def _mapped_prompt(
    raw_prompt: str,
    has_scene: bool,
    reference_ordinals: dict[int, int],
    depth_ordinal: int,
) -> str:
    prompt = raw_prompt
    for index in reference_ordinals:
        prompt = prompt.replace(
            "[[图{}]]".format(index),
            "【参考图{}】".format(index),
        )

    manifest: list[str] = ["【参考图编号与优先级】"]
    if has_scene:
        manifest.append(
            "C4D视窗场景 = 场景母图（独立名称，不属于图1至图5），"
            "是最高优先级的摄像机、构图、透视、空间位置和产品形体依据；"
            "必须优先保持它的画布、镜头、主体位置、比例、遮挡和留白。"
        )
    for index, ordinal in sorted(reference_ordinals.items()):
        manifest.append(
            "插件参考图{} = 提示词中的【参考图{}】（本次请求图像序列中的第{}张），"
            "只按用户为图{}指定的职责使用；参考图编号不因场景母图或深度图而顺延。".format(
                index, index, ordinal, index
            )
        )
    if depth_ordinal:
        manifest.append(
            "插件内部Z深度约束图（本次请求序列中的第{}张）与场景母图逐像素对齐；"
            "白色更近、黑色更远或无几何，仅约束空间、遮挡和轮廓。".format(
                depth_ordinal
            )
        )
        manifest.append(
            "深度图是隐藏结构约束，不属于图1至图5，不得将它理解为图1、图2，"
            "也不得把其灰度用作材质、颜色、灯光或背景参考。"
        )
    manifest.append(
        "图1至图5只代表插件参考槽位；若用户写【参考图1】或【参考图2】，"
        "必须分别使用对应槽位的图片，不得把场景母图或深度图代替任何参考图。"
    )
    return "{}\n{}".format("\n".join(manifest), prompt).strip()


def _closest_ratio(width: int, height: int) -> str:
    value = width / float(max(1, height))
    ratios = {
        "1:1": 1.0,
        "16:9": 16.0 / 9.0,
        "9:16": 9.0 / 16.0,
        "4:3": 4.0 / 3.0,
        "3:4": 3.0 / 4.0,
    }
    return min(ratios, key=lambda name: abs(ratios[name] - value))


def _image2_size(width: int, height: int, quality: str = "2K") -> str:
    if width <= 0 or height <= 0:
        raise _api.ApiError("Image 2 输出尺寸无效。")
    ratio = width / float(height)
    if ratio < 1.0 / 3.0 or ratio > 3.0:
        raise _api.ApiError("Image 2 仅支持 1:3 到 3:1 的输出比例。")
    edge_by_quality = {"1K": 1024, "2K": 2048, "4K": 3840}
    edge = edge_by_quality.get(str(quality or "2K").upper(), 2048)
    scale = float(edge) / max(width, height)

    def aligned(value: float) -> int:
        return max(16, int(round(value / 16.0) * 16))

    target_width = aligned(width * scale)
    target_height = aligned(height * scale)

    # GPT Image 2.5 requires 655,360–8,294,400 pixels.  Adjust the
    # request-only canvas while preserving the captured aspect ratio.
    pixels = target_width * target_height
    if pixels < 655_360:
        factor = (655_360 / float(max(1, pixels))) ** 0.5
        target_width = aligned(target_width * factor)
        target_height = aligned(target_height * factor)
    elif pixels > 8_294_400:
        factor = (8_294_400 / float(pixels)) ** 0.5
        target_width = aligned(target_width * factor)
        target_height = aligned(target_height * factor)
    target_width = min(3840, target_width)
    target_height = min(3840, target_height)
    return "{}x{}".format(target_width, target_height)


def _generation_body(
    model: str,
    prompt: str,
    count: int,
    width: int,
    height: int,
    quality: str,
) -> bytes:
    """Build the JSON contract for text-to-image Image API generations."""

    payload = {
        "model": model,
        "prompt": prompt,
        "n": max(1, int(count or 1)),
        "size": _image2_size(width, height, quality),
        "quality": "high" if model in IMAGE25_MODELS else "low",
        "output_format": "png",
        "response_format": "b64_json",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _resolved_api_key(client: _api.ImageApiClient) -> str:
    """Prefer the saved active profile over a stale inherited environment."""

    settings = client.settings
    direct = str(settings.get("api_key") or "").strip()
    if direct:
        return direct

    profiles = settings.get("provider_profiles") or {}
    if isinstance(profiles, dict):
        current = profiles.get(str(settings.get("provider") or "custom"))
        if not isinstance(current, dict):
            current = profiles.get("custom")
        if isinstance(current, dict):
            saved = str(current.get("api_key") or "").strip()
            if saved:
                return saved

    env_name = str(settings.get("api_key_env") or "").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


def _headers(client: _api.ImageApiClient, content_type: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + _resolved_api_key(client),
        "Accept": "application/json",
        "Content-Type": content_type,
        "User-Agent": "ZhireAI-C4D/JuAIHub-Adapter",
    }


def _error_message(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or fallback)
        if error:
            return str(error)
        return str(data.get("message") or data.get("msg") or fallback)
    return fallback


def _request_json(
    client: _api.ImageApiClient,
    endpoint: str,
    body: bytes,
    content_type: str,
    model: str = "",
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Any:
    base_url = str(client.settings.get("base_url") or "").rstrip("/")
    # The Gemini-compatible route is rooted at /v1beta, while Image2 uses
    # the OpenAI-compatible /v1 routes. Accept either saved base URL form.
    if endpoint.startswith(("/v1/", "/v1beta/")) and base_url.endswith("/v1"):
        base_url = base_url[:-3]
    url = base_url + endpoint
    request = urllib.request.Request(
        url,
        data=body,
        headers=_headers(client, content_type),
        method="POST",
    )
    timeout = max(60.0, float(client.settings.get("timeout_seconds") or 900))
    started = time.monotonic()
    _api._check_cancel(should_cancel)
    _write_diagnostic(
        client,
        "request_started",
        model,
        endpoint,
        "request_bytes={} timeout_seconds={}".format(len(body), int(timeout)),
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            status = int(getattr(response, "status", 200))
            _write_diagnostic(
                client,
                "response_headers",
                model,
                endpoint,
                "status={} elapsed_ms={}".format(
                    status, int((time.monotonic() - started) * 1000)
                ),
            )
            chunks: list[bytes] = []
            total = 0
            while True:
                _api._check_cancel(should_cancel)
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise _api.ApiError("JuAIHub 返回的数据超过 100MB，已停止读取。")
                chunks.append(chunk)
            raw = b"".join(chunks)
            _write_diagnostic(
                client,
                "response_complete",
                model,
                endpoint,
                "status={} response_bytes={} elapsed_ms={}".format(
                    status, len(raw), int((time.monotonic() - started) * 1000)
                ),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read(64 * 1024)
        _write_diagnostic(
            client,
            "http_error",
            model,
            endpoint,
            "status={} response_bytes={} elapsed_ms={}".format(
                exc.code, len(raw), int((time.monotonic() - started) * 1000)
            ),
        )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        message = _error_message(data, raw.decode("utf-8", errors="replace")[:500])
        if exc.code == 401:
            message = "API Key 无效或已过期。"
        elif exc.code == 402:
            message = "JuAIHub 当前 API Key 的模型积分不足。"
        elif exc.code in (502, 503, 504):
            message = (
                "JuAIHub 网关未在规定时间内返回结果。请求可能已经到达上游，"
                "请先在平台后台确认任务和扣费记录，不要立即重复提交。"
            )
        raise _api.ApiError("JuAIHub 请求失败（HTTP {}）：{}".format(exc.code, message)) from exc
    except (
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        http.client.IncompleteRead,
        http.client.RemoteDisconnected,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
    ) as exc:
        _write_diagnostic(
            client,
            "transport_error",
            model,
            endpoint,
            "error_type={} elapsed_ms={}".format(
                type(exc).__name__, int((time.monotonic() - started) * 1000)
            ),
        )
        raise _api.ApiError("连接 JuAIHub 失败或等待生成超时：{}".format(exc)) from exc
    _api._check_cancel(should_cancel)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _api.ApiError("JuAIHub 返回了无法解析的数据。") from exc
    if isinstance(data, dict) and data.get("error"):
        raise _api.ApiError("JuAIHub 请求失败：{}".format(_error_message(data, "未知错误")))
    return data


def _canonical_banana_model(model: str) -> str:
    # Old sessions can still hold a retired alias in memory. Convert it at
    # the last possible point so no request is sent to a dead model ID.
    aliases = {
        "nano-banana-2": "gemini-3.1-flash-image-preview",
        "nano-banana-2-cl": "gemini-3.1-flash-image-preview",
        "nano-banana-pro": "gemini-3-pro-image-preview",
        "nano-banana-pro-cl": "gemini-3-pro-image-preview",
    }
    return aliases.get(str(model or "").strip().lower(), model)


def _gemini_inline_data(path: str) -> dict[str, Any]:
    source = Path(path)
    mime = mimetypes.guess_type(source.name)[0] or "image/png"
    return {
        "inlineData": {
            "mimeType": mime,
            "data": base64.b64encode(source.read_bytes()).decode("ascii"),
        }
    }


def _gemini_request(
    client: _api.ImageApiClient,
    model: str,
    prompt: str,
    paths: list[str],
    width: int,
    height: int,
    quality: str,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Any:
    """Send Banana models with the DUMIK v0.1.6 Gemini-native contract."""
    quality = str(quality or "2K").upper()
    if quality not in {"1K", "2K", "4K"}:
        quality = "2K"
    encode_started = time.monotonic()
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for path in paths:
        _api._check_cancel(should_cancel)
        parts.append(_gemini_inline_data(path))
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": _gemini_aspect_ratio(width, height),
                "imageSize": quality,
            },
        },
    }
    endpoint = "/v1beta/models/{}:generateContent".format(
        urllib.parse.quote(_canonical_banana_model(model), safe="")
    )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _write_diagnostic(
        client,
        "payload_ready",
        model,
        endpoint,
        "input_count={} request_bytes={} encode_ms={} aspect={} image_size={}".format(
            len(paths),
            len(body),
            int((time.monotonic() - encode_started) * 1000),
            _gemini_aspect_ratio(width, height),
            quality,
        ),
    )
    return _request_json(
        client,
        endpoint,
        body,
        "application/json",
        model,
        should_cancel,
    )


def _gemini_aspect_ratio(width: int, height: int) -> str:
    value = float(width) / float(max(1, height))
    ratios = {
        "1:1": 1.0,
        "1:4": 1.0 / 4.0,
        "4:1": 4.0,
        "1:8": 1.0 / 8.0,
        "8:1": 8.0,
        "2:3": 2.0 / 3.0,
        "3:2": 3.0 / 2.0,
        "3:4": 3.0 / 4.0,
        "4:3": 4.0 / 3.0,
        "4:5": 4.0 / 5.0,
        "5:4": 5.0 / 4.0,
        "9:16": 9.0 / 16.0,
        "16:9": 16.0 / 9.0,
        "21:9": 21.0 / 9.0,
        "9:21": 9.0 / 21.0,
    }
    return min(ratios, key=lambda name: abs(ratios[name] - value))


def _multipart_body(
    model: str,
    prompt: str,
    paths: list[str],
    width: int,
    height: int,
    quality: str = "2K",
) -> tuple[bytes, str]:
    boundary = "----ZhireAIJuAIHub" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def add_text(name: str, value: str) -> None:
        chunks.extend(
            [
                ("--{}\r\n".format(boundary)).encode("ascii"),
                ('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name)).encode("ascii"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    add_text("model", model)
    add_text("prompt", prompt)
    add_text("n", "1")
    add_text("size", _image2_size(width, height, quality))
    add_text("quality", "high" if model in IMAGE25_MODELS else "low")
    add_text("response_format", "b64_json")
    add_text("output_format", "png")
    for index, path in enumerate(paths, start=1):
        source = Path(path)
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        filename = "reference_{:02d}{}".format(index, source.suffix.lower() or ".png")
        chunks.extend(
            [
                ("--{}\r\n".format(boundary)).encode("ascii"),
                (
                    'Content-Disposition: form-data; name="image"; filename="{}"\r\n'.format(
                        filename
                    )
                ).encode("ascii"),
                ("Content-Type: {}\r\n\r\n".format(mime)).encode("ascii"),
                source.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(("--{}--\r\n".format(boundary)).encode("ascii"))
    return b"".join(chunks), boundary


def _collect_outputs(value: Any) -> list[tuple[str, str]]:
    outputs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, item: Any) -> None:
        if not isinstance(item, str) or not item:
            return
        actual_kind = "base64" if item.startswith("data:image/") else kind
        pair = (actual_kind, item)
        if pair not in seen:
            seen.add(pair)
            outputs.append(pair)

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("b64_json", "base64", "image_base64"):
                add("base64", item.get(key))
            for key in ("url", "image_url"):
                add("url", item.get(key))
            for key in ("inlineData", "inline_data"):
                inline = item.get(key)
                if isinstance(inline, dict):
                    add("base64", inline.get("data"))
            for nested in item.values():
                if isinstance(nested, (dict, list)):
                    walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
    return outputs


def _image_extension(data: bytes) -> str:
    """Return the real container extension without decoding or recompressing."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _generate_juaihub(
    client: _api.ImageApiClient,
    job: dict,
    progress: Optional[Callable[[float, str], None]],
    should_cancel: Optional[Callable[[], bool]],
) -> list[str]:
    _api._check_cancel(should_cancel)
    model = str(job.get("model") or client.settings.get("model") or "").strip()
    paths, reference_ordinals, depth_ordinal = _input_bundle(job)
    scene_context = job.get("scene_context") or {}
    raw_prompt = _api.composition_locked_prompt(
        str(job.get("prompt") or ""),
        bool(
            str(job.get("scene_image") or "").strip()
            and scene_context.get("preserve_composition")
        ),
        scene_context,
    )
    prompt = _mapped_prompt(
        raw_prompt.strip(),
        bool(str(job.get("scene_image") or "").strip()),
        reference_ordinals,
        depth_ordinal,
    )
    width = int(job.get("width") or 1024)
    height = int(job.get("height") or 1024)
    count = max(1, int(job.get("count") or 1))
    endpoint = (
        "/v1beta/models/{}:generateContent".format(
            urllib.parse.quote(_canonical_banana_model(model), safe="")
        )
        if model in BANANA_MODELS
        else "/v1/images/edits-or-generations"
    )
    _write_diagnostic(
        client,
        "inputs_ready",
        model,
        endpoint,
        "input_count={} input_bytes={} prompt_chars={} output={}x{} count={}".format(
            len(paths),
            sum(Path(path).stat().st_size for path in paths),
            len(prompt),
            width,
            height,
            count,
        ),
    )
    results: list[str] = []
    for index in range(count):
        _api._check_cancel(should_cancel)
        if progress:
            progress(0.08 + 0.72 * (index / count), "正在调用 JuAIHub（{}/{}）…".format(index + 1, count))
        if model == IMAGE2_MODEL or model in IMAGE25_MODELS:
            if paths:
                body, boundary = _multipart_body(
                    model,
                    prompt,
                    paths,
                    width,
                    height,
                    str(job.get("quality") or "2K"),
                )
                data = _request_json(
                    client,
                    "/v1/images/edits",
                    body,
                    "multipart/form-data; boundary=" + boundary,
                    model,
                    should_cancel,
                )
            else:
                data = _request_json(
                    client,
                    "/v1/images/generations",
                    _generation_body(
                        model,
                        prompt,
                        count,
                        width,
                        height,
                        str(job.get("quality") or "2K"),
                    ),
                    "application/json",
                    model,
                    should_cancel,
                )
        else:
            data = _gemini_request(
                client,
                model,
                prompt,
                paths,
                width,
                height,
                str(job.get("quality") or "2K"),
                should_cancel,
            )
        outputs = _collect_outputs(data)
        if not outputs:
            raise _api.ApiError("JuAIHub 已返回结果，但没有找到图片数据。")
        kind, value = outputs[0]
        if kind == "base64":
            try:
                normalized = "".join(value.partition(",")[2].split()) if value.startswith("data:") else "".join(value.split())
                image_data = base64.b64decode(normalized, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise _api.ApiError("JuAIHub 返回了无效的 Base64 图片。") from exc
            output_job = dict(job)
            output_job["output_extension"] = _image_extension(image_data)
            results.append(client._save_b64(value, len(results), output_job))
        else:
            results.append(client._download(value, len(results), should_cancel, job))
        _write_diagnostic(
            client,
            "result_saved",
            model,
            endpoint,
            "result_index={}".format(index + 1),
        )
    if progress:
        progress(1.0, "生成完成")
    return results


if not getattr(_api.ImageApiClient.generate, "_zhire_juaihub_adapter", False):
    _original_generate = _api.ImageApiClient.generate

    def _patched_generate(
        self: _api.ImageApiClient,
        job: dict,
        progress: Optional[Callable[[float, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> list[str]:
        if _is_juaihub(self, job):
            model = str(job.get("model") or self.settings.get("model") or "").strip()
            endpoint = (
                "/v1beta/models/{}:generateContent".format(
                    _canonical_banana_model(model)
                )
                if model in BANANA_MODELS
                else "/v1/images/edits-or-generations"
            )
            _write_diagnostic(self, "route_selected", model, endpoint)
            return _generate_juaihub(self, job, progress, should_cancel)
        return _original_generate(self, job, progress, should_cancel)

    _patched_generate._zhire_juaihub_adapter = True
    _api.ImageApiClient.generate = _patched_generate
