"""Provider-neutral image generation client based on request templates."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .constants import PRODUCT_VERSION
from .prompt import (
    animation_locked_prompt,
    build_multimodal_content,
    composition_locked_prompt,
    plain_prompt,
    referenced_indices,
)
from .providers import (
    APIMART_IMAGE_MODELS,
    APIMART_SEEDANCE_MODELS,
    ATLAS_IMAGE_MODELS,
    ATLAS_SEEDANCE_MODELS,
    is_compatible_image_model,
    is_compatible_seedance_model,
    is_seedance_25_model,
    seedance_capability,
    seedance_unavailable_message,
)


class ApiError(RuntimeError):
    pass


class GenerationCancelled(ApiError):
    """Raised when the user stops a generation job from the C4D panel."""


# Media URLs returned by Atlas and APIMART stay reusable for a while. Keep a
# small process-wide cache so regenerating the same C4D scene does not upload
# its movie or every sampled frame again. The key includes the file identity,
# provider endpoint and a one-way account fingerprint, so switching accounts
# or replacing a file at the same path cannot reuse the wrong URL.
_SHARED_UPLOAD_CACHE: dict[str, tuple[float, str]] = {}
_SHARED_UPLOAD_CACHE_LOCK = threading.Lock()
_DEFAULT_UPLOAD_CACHE_SECONDS = 6 * 60 * 60
_RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def _is_timeout_error(exc: BaseException) -> bool:
    """Recognize the timeout shapes emitted by urllib across C4D Python builds."""

    current: Optional[BaseException] = exc
    visited: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if "timed out" in str(current).lower() or "timeout" in str(current).lower():
            return True
        reason = getattr(current, "reason", None)
        current = reason if isinstance(reason, BaseException) else None
    return False


def _is_retryable_transport_error(exc: BaseException) -> bool:
    if _is_timeout_error(exc):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return int(exc.code) in _RETRYABLE_HTTP_CODES
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
        ),
    )


def _check_cancel(should_cancel: Optional[Callable[[], bool]]) -> None:
    if should_cancel is not None and should_cancel():
        raise GenerationCancelled("生成已取消")


TOKEN_PATTERN = re.compile(r"\$[A-Z][A-Z0-9_]*")


def file_to_data_url(path: str) -> str:
    source = Path(path)
    if not source.is_file():
        raise ApiError("图片不存在：{}".format(path))
    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    return "data:{};base64,{}".format(mime, encoded)


def expand_template(value: Any, context: dict[str, Any]) -> Any:
    """Recursively substitute ``$TOKENS`` in a JSON-compatible template."""

    if isinstance(value, dict):
        return {key: expand_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_template(item, context) for item in value]
    if not isinstance(value, str):
        return value
    if value in context:
        return context[value]

    def replace(match: re.Match[str]) -> str:
        replacement = context.get(match.group(0), "")
        if isinstance(replacement, (dict, list)):
            return json.dumps(replacement, ensure_ascii=False)
        return str(replacement)

    return TOKEN_PATTERN.sub(replace, value)


def drop_none_fields(value: Any) -> Any:
    """Remove optional template fields whose resolved value is ``None``."""

    if isinstance(value, dict):
        return {
            key: drop_none_fields(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [drop_none_fields(item) for item in value if item is not None]
    return value


def extract_path(data: Any, path: str) -> list[Any]:
    """Extract all values from a dotted path with ``*`` list wildcards."""

    if not path:
        return []
    nodes = [data]
    for segment in path.split("."):
        next_nodes: list[Any] = []
        for node in nodes:
            if segment == "*":
                if isinstance(node, list):
                    next_nodes.extend(node)
                elif isinstance(node, dict):
                    next_nodes.extend(node.values())
                continue
            if isinstance(node, dict) and segment in node:
                next_nodes.append(node[segment])
            elif isinstance(node, list):
                try:
                    next_nodes.append(node[int(segment)])
                except (ValueError, IndexError):
                    continue
        nodes = next_nodes
    return nodes


def extract_uploaded_url(data: Any) -> str:
    """Read upload URLs from both legacy and current Atlas response shapes."""

    paths = (
        "url",
        "download_url",
        "file_url",
        "media_url",
        "data.url",
        "data.download_url",
        "data.file_url",
        "data.media_url",
        "data.*.url",
        "data.*.download_url",
    )
    for path in paths:
        for candidate in extract_path(data, path):
            value = str(candidate or "").strip()
            if value.startswith(("https://", "http://")):
                return value

    def walk(value: Any) -> str:
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
        if isinstance(value, dict):
            for key in ("download_url", "url", "file_url", "media_url", "signed_url"):
                if key in value:
                    found = walk(value[key])
                    if found:
                        return found
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return ""

    return walk(data)


def extract_media_urls(data: Any, configured_path: str, workflow: str) -> list[str]:
    """Extract result URLs across documented provider response variants.

    Gateways sometimes return ``url`` as a string and sometimes as an array.
    A configured wildcard path alone cannot cover both, so the built-in
    adapters use this bounded set of known result locations as a fallback.
    """

    paths = [str(configured_path or "")]
    if str(workflow or "image") == "animation":
        paths.extend(
            [
                "data.result.videos.*.url.*",
                "data.result.videos.*.url",
                "data.result.videos.*.video_url",
                "data.result.video_url",
                "data.outputs.*",
                "content.video_url",
                "data.content.video_url",
                "video_url",
            ]
        )
    else:
        paths.extend(
            [
                "data.result.images.*.url.*",
                "data.result.images.*.url",
                "data.outputs.*",
                "data.*.url",
                "results.*.url",
                "output.choices.*.message.content.*.image",
            ]
        )
    results: list[str] = []
    for path in paths:
        if not path:
            continue
        for candidate in extract_path(data, path):
            values = candidate if isinstance(candidate, list) else [candidate]
            for value in values:
                text = str(value or "").strip()
                if text.startswith(("http://", "https://", "data:")) and text not in results:
                    results.append(text)
    return results


def _size_with_pixel_limit(
    width: int, height: int, max_pixels: int, min_pixels: int = 1
) -> str:
    """Keep the source ratio while fitting a provider's total-pixel range."""

    source_width = max(64, int(width or 1024))
    source_height = max(64, int(height or 1024))
    pixels = source_width * source_height
    minimum = max(1, int(min_pixels))
    maximum = max(minimum, int(max_pixels))
    if pixels < minimum:
        scale = (float(minimum) / float(pixels)) ** 0.5
        source_width = max(64, int(math.ceil(source_width * scale / 16.0)) * 16)
        source_height = max(64, int(math.ceil(source_height * scale / 16.0)) * 16)
    elif pixels > maximum:
        scale = (float(maximum) / float(pixels)) ** 0.5
        source_width = max(64, int(source_width * scale) // 16 * 16)
        source_height = max(64, int(source_height * scale) // 16 * 16)
    return "{}*{}".format(source_width, source_height)


def _size_with_side_bounds(
    width: int,
    height: int,
    min_side: int,
    max_side: int,
    separator: str,
    grid: int = 1,
) -> str:
    """Scale a locked canvas into a provider's legal range without stretching.

    The original scene dimensions remain in the job and are restored after
    download. This request-only size prevents small C4D viewports such as
    960x540 from being rejected before the exact-canvas normalization runs.
    """

    source_width = max(1, int(width or 1024))
    source_height = max(1, int(height or 1024))
    minimum = max(1, int(min_side))
    maximum = max(minimum, int(max_side))
    step = max(1, int(grid))
    lower_scale = max(
        minimum / float(source_width), minimum / float(source_height)
    )
    upper_scale = min(
        maximum / float(source_width), maximum / float(source_height)
    )
    if lower_scale > upper_scale + 1e-9:
        raise ApiError(
            "当前场景画幅过于狭长，无法在平台允许的尺寸范围内保持原比例；"
            "请稍微调整 C4D 画幅后重试。"
        )
    scale = min(max(1.0, lower_scale), upper_scale)

    def aligned(value: float) -> int:
        rounded = int(round(value / float(step))) * step
        return max(minimum, min(maximum, rounded))

    target_width = aligned(source_width * scale)
    target_height = aligned(source_height * scale)
    return "{}{}{}".format(target_width, separator, target_height)


def _friendly_media_parameter_error(detail: object) -> str:
    """Translate provider media validation codes into actionable Chinese."""

    value = str(detail or "")
    lower = value.lower()
    if "pixelcounttoosmall" in lower or (
        "pixelcount" in lower and "too small" in lower
    ):
        return (
            "动画参考素材分辨率过低。Seedance 每帧至少需要 407,696 像素"
            "（16:9 建议 1024×576）。请重新点击“渲染加载”；"
            "若为手动上传 MP4，插件会自动改用合规参考帧。"
        )
    if "pixelcounttoolarge" in lower or (
        "pixelcount" in lower and "too large" in lower
    ):
        return (
            "动画参考素材分辨率过高。Seedance 每帧不能超过 8,295,044 像素；"
            "请重新点击“渲染加载”，插件会生成合规尺寸。"
        )
    if (
        "aspectratio" in lower
        or "aspect ratio" in lower
        or "ratiooutofrange" in lower
    ) and any(token in lower for token in ("invalid", "between", "range", "unsupported")):
        return "动画参考素材画幅不受支持；宽高比需要在 0.4 到 2.5 之间。"
    if (
        "framerate" in lower
        or "frame rate" in lower
        or "invalidparameter.fps" in lower
    ) and any(token in lower for token in ("invalid", "between", "range", "unsupported")):
        return "动画参考视频帧率不受支持；请使用 24–60 FPS 的 MP4。"
    return ""


def _endpoint_query(endpoint: str, **updates: object) -> str:
    """Merge query parameters into a relative or absolute API endpoint."""

    parts = urllib.parse.urlsplit(str(endpoint or ""))
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    for key, value in updates.items():
        query[str(key)] = str(value)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def build_url(base_url: str, endpoint: str) -> str:
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    if not base_url:
        raise ApiError("请先在 API 设置中填写 Base URL。")
    return base_url.rstrip("/") + "/" + endpoint.lstrip("/")


class ImageApiClient:
    def __init__(self, settings: dict, output_dir: Optional[Union[str, Path]] = None):
        self.settings = settings
        self.output_dir = Path(output_dir) if output_dir is not None else None
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        self._upload_cache: dict[str, str] = {}
        # Poll requests should fail fast enough to reconnect without changing
        # the longer timeout used by uploads and generation submissions.
        self._request_timeout_override: Optional[float] = None

    def _job_setting(self, job: Optional[dict], key: str, default=None):
        """Read the animation-specific contract without changing image settings."""

        if job and str(job.get("workflow") or "image") == "animation":
            video_key = "video_" + key
            if video_key in self.settings:
                return self.settings.get(video_key)
        return self.settings.get(key, default)

    def _api_key(self) -> str:
        env_name = str(self.settings.get("api_key_env") or "").strip()
        if env_name:
            return os.environ.get(env_name, "")
        return str(self.settings.get("api_key") or "")

    def _headers(self) -> dict[str, str]:
        headers = {
            str(key): str(value)
            for key, value in (self.settings.get("headers") or {}).items()
        }
        key = self._api_key()
        auth_type = str(self.settings.get("auth_type") or "none").lower()
        if key and auth_type == "bearer":
            headers["Authorization"] = "Bearer " + key
        elif key and auth_type == "header":
            name = str(self.settings.get("api_key_header") or "X-API-Key")
            headers[name] = key
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        # Atlas Cloud's Cloudflare rules reject urllib's default Python signature.
        # A stable product UA also gives other API gateways a useful client identity.
        headers.setdefault("User-Agent", "ZhireAI-C4D/{}".format(PRODUCT_VERSION))
        return headers

    def _transport_error_message(self, exc: BaseException, has_payload: bool) -> str:
        timed_out = _is_timeout_error(exc)
        if self._request_timeout_override is not None:
            problem = "读取超时" if timed_out else "连接暂时中断"
            return "无法连接 API：本次任务查询{}，插件将继续查询同一任务。".format(
                problem
            )
        if has_payload:
            problem = "读取超时" if timed_out else "连接中断"
            return (
                "生成请求已发出，但平台响应{}。为避免同一任务被重复提交或重复扣费，"
                "插件没有自动重新提交。请先到当前平台的任务记录中确认是否已创建任务；"
                "确认没有任务后再重新生成。"
            ).format(problem)
        problem = "读取超时" if timed_out else "连接失败"
        return "无法连接 API：网络{}，请检查网络、API 地址或代理设置后重试。".format(
            problem
        )

    def _request(
        self,
        endpoint: str,
        payload: Optional[Any] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Any:
        _check_cancel(should_cancel)
        url = build_url(str(self.settings.get("base_url") or ""), endpoint)
        method = str(self.settings.get("method") or "POST").upper()
        if payload is None:
            method = "GET"
            body = None
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=url, data=body, headers=self._headers(), method=method
        )
        request_timeout = self._request_timeout_override
        if request_timeout is None:
            request_timeout = float(self.settings.get("timeout_seconds") or 180)
        try:
            with urllib.request.urlopen(
                request, timeout=max(1.0, float(request_timeout))
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            media_friendly = _friendly_media_parameter_error(detail)
            if media_friendly:
                raise ApiError(media_friendly) from exc
            friendly = {
                401: "API Key 无效或已过期，请重新连接当前平台。",
                402: "当前 API 平台余额不足，请充值后重试。",
                403: "当前 API Key 没有该模型或接口权限。",
                429: "当前平台请求过于频繁，请稍后重试。",
            }.get(int(exc.code))
            if friendly:
                raise ApiError(friendly) from exc
            raise ApiError("API 返回 HTTP {}：{}".format(exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise ApiError(self._transport_error_message(exc, payload is not None)) from exc
        except (
            TimeoutError,
            socket.timeout,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
        ) as exc:
            raise ApiError(self._transport_error_message(exc, payload is not None)) from exc
        _check_cancel(should_cancel)
        try:
            decoded = raw.decode("utf-8")
            if decoded.lstrip().startswith(("<html", "<!doctype", "<HTML", "<!DOCTYPE")):
                raise ApiError(
                    "接口返回了网页而不是 API 数据。请填写服务商的 API 地址，不要填写官网首页。"
                )
            data = json.loads(decoded)
            provider = str(self.settings.get("provider") or "")
            if isinstance(data, dict):
                code = data.get("code")
                if code not in (None, 0, 200, "0", "200"):
                    message = data.get("msg") or data.get("message") or "未知错误"
                    media_friendly = _friendly_media_parameter_error(
                        "{} {}".format(code, message)
                    )
                    if media_friendly:
                        raise ApiError(media_friendly)
                    labels = {
                        "atlascloud": "Atlas Cloud",
                        "apimart": "APIMART",
                        "volcengine": "火山引擎",
                        "aliyun_bailian": "阿里百炼",
                    }
                    label = labels.get(provider, "API")
                    raise ApiError("{} 返回错误 {}：{}".format(label, code, message))
                error = data.get("error")
                if isinstance(error, dict) and error:
                    message = error.get("message") or error.get("msg") or error.get("code")
                    if message:
                        media_friendly = _friendly_media_parameter_error(message)
                        if media_friendly:
                            raise ApiError(media_friendly)
                        raise ApiError("API 返回错误：{}".format(message))
            return data
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError("API 没有返回有效 JSON。") from exc

    def _upload_media(
        self, path: str, should_cancel: Optional[Callable[[], bool]] = None
    ) -> str:
        """Upload a local image to providers which require temporary public URLs."""

        _check_cancel(should_cancel)
        source = Path(path)
        if not source.is_file():
            raise ApiError("待上传图片不存在：{}".format(path))
        source_stat = source.stat()
        account_fingerprint = hashlib.sha256(
            self._api_key().encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        cache_key = "|".join(
            (
                str(self.settings.get("provider") or "custom").strip().lower(),
                str(self.settings.get("base_url") or "").strip().lower(),
                str(self.settings.get("media_upload_endpoint") or "").strip(),
                account_fingerprint,
                str(source.resolve()).casefold(),
                str(int(source_stat.st_size)),
                str(
                    int(
                        getattr(
                            source_stat,
                            "st_mtime_ns",
                            source_stat.st_mtime * 1e9,
                        )
                    )
                ),
            )
        )
        if cache_key in self._upload_cache:
            return self._upload_cache[cache_key]
        now = time.monotonic()
        with _SHARED_UPLOAD_CACHE_LOCK:
            shared = _SHARED_UPLOAD_CACHE.get(cache_key)
            if shared and shared[0] > now:
                self._upload_cache[cache_key] = shared[1]
                return shared[1]
            if shared:
                _SHARED_UPLOAD_CACHE.pop(cache_key, None)

        endpoint = str(
            self.settings.get("media_upload_endpoint") or "/api/v1/model/uploadMedia"
        )
        url = build_url(str(self.settings.get("base_url") or ""), endpoint)
        boundary = "----ZhireAI{}".format(int(time.time() * 1000))
        suffix = source.suffix.lower() or ".png"
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        prefix = (
            "--{0}\r\n"
            "Content-Disposition: form-data; name=\"file\"; filename=\"upload{1}\"\r\n"
            "Content-Type: {2}\r\n\r\n"
        ).format(boundary, suffix, mime).encode("utf-8")
        body = prefix + source.read_bytes() + ("\r\n--{}--\r\n".format(boundary)).encode("ascii")
        headers = self._headers()
        headers.pop("Content-Type", None)
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        upload_timeout = max(
            30.0,
            float(
                self.settings.get("media_upload_timeout_seconds")
                or self.settings.get("timeout_seconds")
                or 180
            ),
        )
        max_attempts = max(1, int(self.settings.get("media_upload_attempts") or 3))
        data = None
        for attempt in range(1, max_attempts + 1):
            _check_cancel(should_cancel)
            request = urllib.request.Request(
                url=url, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=upload_timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1200]
                media_friendly = _friendly_media_parameter_error(detail)
                if media_friendly:
                    raise ApiError(media_friendly) from exc
                if int(exc.code) not in _RETRYABLE_HTTP_CODES or attempt >= max_attempts:
                    raise ApiError(
                        "上传参考素材失败（HTTP {}）：{}".format(exc.code, detail)
                    ) from exc
            except (UnicodeDecodeError, ValueError) as exc:
                raise ApiError("上传参考素材失败：平台未返回有效数据。") from exc
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
                if not _is_retryable_transport_error(exc) or attempt >= max_attempts:
                    suffix = "网络读取超时" if _is_timeout_error(exc) else "网络连接中断"
                    raise ApiError(
                        "上传参考素材失败：{}，已自动重试 {} 次。".format(
                            suffix, attempt
                        )
                    ) from exc
            self._wait_poll_delay(min(3.0, float(attempt)), should_cancel)
        _check_cancel(should_cancel)

        if isinstance(data, dict) and data.get("code") not in (None, 0, 200, "0", "200"):
            raise ApiError(
                "上传参考图失败：{}".format(data.get("msg") or data.get("message") or data)
            )
        uploaded = extract_uploaded_url(data)
        if not uploaded:
            raise ApiError(
                "图片已上传，但接口没有返回可用于生成的图片地址。"
                "请在 API 设置中重新测试连接，并确认服务商支持当前图片上传接口。"
            )
        self._upload_cache[cache_key] = uploaded
        cache_seconds = max(
            0.0,
            float(
                self.settings.get("media_upload_cache_seconds")
                or _DEFAULT_UPLOAD_CACHE_SECONDS
            ),
        )
        if cache_seconds:
            with _SHARED_UPLOAD_CACHE_LOCK:
                _SHARED_UPLOAD_CACHE[cache_key] = (now + cache_seconds, uploaded)
        return uploaded

    def _wait_poll_delay(
        self,
        seconds: float,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Wait between task queries while keeping the cancel button responsive."""

        sleep_until = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < sleep_until:
            _check_cancel(should_cancel)
            time.sleep(min(0.1, max(0.0, sleep_until - time.monotonic())))

    def _build_context(
        self, job: dict, should_cancel: Optional[Callable[[], bool]] = None
    ) -> dict[str, Any]:
        _check_cancel(should_cancel)
        scene = str(job.get("scene_image") or "")
        all_references = [str(path) for path in job.get("references") or []]
        prompt = str(job.get("prompt") or "")
        references = [
            all_references[index - 1]
            for index in referenced_indices(prompt)
            if 1 <= index <= len(all_references)
        ]
        animation_frames = [str(path) for path in job.get("animation_frames") or []]
        encoded_by_path: dict[str, str] = {}

        def cached_data_url(path: str) -> str:
            if not path:
                return ""
            if path not in encoded_by_path:
                encoded_by_path[path] = file_to_data_url(path)
            return encoded_by_path[path]

        provider = str(self.settings.get("provider") or "")
        workflow = str(job.get("workflow") or "image")
        image_upload_supported = bool(
            self.settings.get("image_upload_supported")
            or provider in {"atlascloud", "apimart"}
        )
        video_upload_supported = bool(
            self.settings.get("video_upload_supported")
            or provider == "atlascloud"
        )
        scene_is_video = Path(scene).suffix.lower() in {
            ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"
        }
        uploaded_by_path: dict[str, str] = {}

        def media_url(path: str, is_video: bool = False) -> str:
            if not path:
                return ""
            can_upload = video_upload_supported if is_video else image_upload_supported
            if can_upload:
                if path not in uploaded_by_path:
                    uploaded_by_path[path] = (
                        self._upload_media(path)
                        if should_cancel is None
                        else self._upload_media(path, should_cancel)
                    )
                return uploaded_by_path[path]
            # Providers without a video-upload contract must use the sampled
            # PNG frames prepared by the C4D UI; never send an MP4 to an image
            # upload endpoint or embed a huge video as a data URL.
            if is_video:
                return ""
            return cached_data_url(path)

        scene_data_url = "" if scene_is_video else (
            "" if image_upload_supported else cached_data_url(scene)
        )
        reference_urls = (
            []
            if image_upload_supported
            else [cached_data_url(path) for path in references]
        )
        frame_images = [
            path
            for path in animation_frames
            if Path(path).suffix.lower()
            not in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
        ]
        if workflow == "animation" and scene_is_video and video_upload_supported:
            frame_images = []
        animation_urls = (
            []
            if image_upload_supported
            else [cached_data_url(path) for path in frame_images]
        )
        scene_remote = media_url(scene, scene_is_video)
        reference_remote = [media_url(path, False) for path in references]
        animation_remote = [media_url(path, False) for path in frame_images]
        _check_cancel(should_cancel)
        animation_reference_videos = (
            [scene_remote] if workflow == "animation" and scene_is_video and scene_remote else []
        )
        if workflow == "animation":
            animation_primary_images = (
                animation_remote
                if animation_remote and not animation_reference_videos
                else ([scene_remote] if scene_remote and not scene_is_video else [])
            )
            animation_reference_images = animation_primary_images + reference_remote
            if provider in {"apimart", "volcengine"} and not (
                animation_reference_images or animation_reference_videos
            ):
                raise ApiError(
                    "已收到 MP4 动画，但插件没有准备好当前平台所需的动画参考内容。"
                    "请重新选择该 MP4 后再试。"
                )
            video_model = str(
                job.get("model") or self.settings.get("video_model") or ""
            )
            reference_limit = (
                50
                if provider == "apimart" and is_seedance_25_model(video_model)
                else 9
            )
            if (
                provider in {"apimart", "volcengine"}
                and len(animation_reference_images) > reference_limit
            ):
                raise ApiError(
                    "当前 Seedance 模型最多接收 {} 张动画参考图，"
                    "请减少 @ 引用的参考图后重试。".format(reference_limit)
                )
            primary_inputs = animation_reference_videos + animation_reference_images
        else:
            animation_reference_images = []
            primary_inputs = [scene_remote] if scene_remote else []
            primary_inputs += reference_remote
            if provider == "aliyun_bailian" and len(primary_inputs) > 3:
                raise ApiError(
                    "阿里百炼 Qwen 图像编辑每次最多使用 3 张输入图（含场景图），"
                    "请减少 @ 引用的参考图。"
                )
        all_input_urls: list[str] = []
        for value in primary_inputs:
            if value and value not in all_input_urls:
                all_input_urls.append(value)
        scene_context = job.get("scene_context") or {}
        if workflow == "animation":
            effective_prompt = animation_locked_prompt(
                prompt,
                bool(
                    (animation_frames or scene)
                    and scene_context.get("preserve_composition")
                ),
                scene_context,
            )
        else:
            effective_prompt = composition_locked_prompt(
                prompt,
                bool(scene and scene_context.get("preserve_composition")),
                scene_context,
            )
        # In locked automatic mode, edit-capable providers must inherit the
        # first input canvas. Sending a nearest preset (for example 16:9 for a
        # 1280x718 viewport) causes an avoidable crop/recomposition.
        aspect_value: Optional[str] = (
            None
            if bool(job.get("aspect_auto"))
            else str(job.get("aspect") or "1:1")
        )
        dashscope_content = [
            {"image": value} for value in all_input_urls
        ] + [{"text": plain_prompt(effective_prompt)}]
        volcengine_video_content = [
            {"type": "text", "text": plain_prompt(effective_prompt)}
        ]
        volcengine_video_content.extend(
            {
                "type": "image_url",
                "image_url": {"url": value},
                "role": "reference_image",
            }
            for value in animation_reference_images
        )
        volcengine_video_content.extend(
            {
                "type": "video_url",
                "video_url": {"url": value},
                "role": "reference_video",
            }
            for value in animation_reference_videos
        )
        return {
            "$MODEL": str(job.get("model") or self.settings.get("model") or ""),
            "$VIDEO_MODEL": str(
                job.get("model") or self.settings.get("video_model") or ""
            ),
            "$WORKFLOW_MODE": str(job.get("workflow") or "image"),
            "$PROMPT": plain_prompt(effective_prompt),
            "$RAW_PROMPT": effective_prompt,
            "$NEGATIVE_PROMPT": str(job.get("negative_prompt") or ""),
            "$WIDTH": int(job.get("width") or 1024),
            "$HEIGHT": int(job.get("height") or 1024),
            "$SIZE": "{}x{}".format(job.get("width") or 1024, job.get("height") or 1024),
            "$SIZE_STAR": "{}*{}".format(job.get("width") or 1024, job.get("height") or 1024),
            "$COUNT": int(job.get("count") or 1),
            "$SCENE_IMAGE_DATA_URL": scene_data_url,
            "$SCENE_IMAGE_BASE64": scene_data_url.partition(",")[2],
            "$SCENE_MEDIA_DATA_URL": scene_data_url,
            "$REFERENCE_IMAGES_DATA_URLS": reference_urls,
            "$REFERENCE_IMAGES_BASE64": [url.partition(",")[2] for url in reference_urls],
            "$ANIMATION_FRAMES_DATA_URLS": animation_urls,
            "$ANIMATION_FRAMES_BASE64": [
                url.partition(",")[2] for url in animation_urls
            ],
            "$SCENE_IMAGE_URL": scene_remote,
            "$SCENE_MEDIA_URL": scene_remote,
            "$REFERENCE_IMAGE_URLS": reference_remote,
            "$ANIMATION_FRAME_URLS": animation_remote,
            "$ANIMATION_REFERENCE_IMAGE_URLS": animation_reference_images,
            "$ANIMATION_REFERENCE_VIDEO_URLS": animation_reference_videos,
            "$ALL_INPUT_IMAGE_URLS": all_input_urls,
            "$ALL_INPUT_MEDIA_URLS": all_input_urls,
            "$MULTIMODAL_CONTENT": (
                []
                if image_upload_supported
                else build_multimodal_content(
                    effective_prompt,
                    all_references,
                    cached_data_url,
                    scene,
                )
            ),
            "$DASHSCOPE_CONTENT": dashscope_content,
            "$VOLCENGINE_VIDEO_CONTENT": volcengine_video_content,
            "$SCENE_CONTEXT": scene_context,
            "$ASPECT": aspect_value,
            "$QUALITY": str(job.get("quality") or "1K"),
            "$QUALITY_LOWER": str(job.get("quality") or "1K").lower(),
            "$DURATION": int(job.get("duration") or 5),
            "$FPS": float(job.get("fps") or 24.0),
            "$VIDEO_RESOLUTION": str(
                job.get("video_resolution") or "720P"
            ).lower(),
            "$VIDEO_RATIO": (
                "adaptive"
                if bool(job.get("aspect_auto"))
                else str(aspect_value or "16:9")
            ),
        }

    def _save_b64(self, value: str, index: int, job: Optional[dict] = None) -> str:
        if self.output_dir is None:
            raise ApiError("没有设置图片输出目录。")
        raw_value = value.partition(",")[2] if value.startswith("data:") else value
        try:
            data = base64.b64decode(raw_value)
        except (ValueError, TypeError) as exc:
            raise ApiError("API 返回了无效的 Base64 结果。") from exc
        extension = str(self._job_setting(job, "output_extension", ".png") or ".png")
        if not extension.startswith("."):
            extension = "." + extension
        path = self.output_dir / "lumastage_{:03d}_{}{}".format(
            index + 1, int(time.time() * 1000), extension
        )
        path.write_bytes(data)
        return str(path)

    def _download(
        self,
        url: str,
        index: int,
        should_cancel: Optional[Callable[[], bool]] = None,
        job: Optional[dict] = None,
    ) -> str:
        _check_cancel(should_cancel)
        if self.output_dir is None:
            raise ApiError("没有设置图片输出目录。")
        if url.startswith("data:"):
            return self._save_b64(url, index, job)
        request = urllib.request.Request(
            url=url,
            headers={
                "Accept": "video/*,image/*,*/*",
                "User-Agent": "ZhireAI-C4D/{}".format(PRODUCT_VERSION),
            },
        )
        download_timeout = max(
            60.0,
            float(
                self.settings.get("result_download_timeout_seconds")
                or self.settings.get("timeout_seconds")
                or 180
            ),
        )
        max_attempts = max(1, int(self.settings.get("result_download_attempts") or 3))
        data = b""
        content_type = "application/octet-stream"
        for attempt in range(1, max_attempts + 1):
            _check_cancel(should_cancel)
            try:
                with urllib.request.urlopen(request, timeout=download_timeout) as response:
                    data = response.read()
                    content_type = response.headers.get_content_type()
                break
            except urllib.error.HTTPError as exc:
                if int(exc.code) not in _RETRYABLE_HTTP_CODES or attempt >= max_attempts:
                    raise ApiError(
                        "下载生成结果失败（HTTP {}）。生成任务不会重复提交。".format(
                            exc.code
                        )
                    ) from exc
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
                if not _is_retryable_transport_error(exc) or attempt >= max_attempts:
                    suffix = "网络读取超时" if _is_timeout_error(exc) else "网络连接中断"
                    raise ApiError(
                        "下载生成结果失败：{}，已自动重试 {} 次。"
                        "生成任务不会重复提交。".format(suffix, attempt)
                    ) from exc
            self._wait_poll_delay(min(3.0, float(attempt)), should_cancel)
        _check_cancel(should_cancel)
        configured_extension = str(
            self._job_setting(job, "output_extension", ".png") or ".png"
        )
        if job and str(job.get("workflow") or "image") == "animation":
            url_extension = Path(urllib.parse.urlparse(url).path).suffix.lower()
            extension = (
                url_extension
                if url_extension in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
                else configured_extension
            )
        else:
            extension = mimetypes.guess_extension(content_type) or configured_extension
        if not extension.startswith("."):
            extension = "." + extension
        path = self.output_dir / "lumastage_{:03d}_{}{}".format(
            index + 1, int(time.time() * 1000), extension
        )
        path.write_bytes(data)
        return str(path)

    def list_models(self) -> list[str]:
        """Read model IDs from an OpenAI-compatible model-list endpoint."""

        provider = str(self.settings.get("provider") or "")
        if provider == "atlascloud":
            if not self._api_key().strip():
                raise ApiError("请先填写当前 Atlas Cloud API Key。")
            # Atlas' /v1/models endpoint is focused on chat models and can
            # reject or omit media models even when the key is valid.  The
            # official balance endpoint is read-only, does not start a paid
            # generation, and validates the same Bearer credential used by
            # upload/generate/prediction requests.
            endpoint = str(
                self.settings.get("connection_test_endpoint")
                or "/public/v1/balance"
            )
            self._request(endpoint, None)
            return list(ATLAS_IMAGE_MODELS) + list(ATLAS_SEEDANCE_MODELS)
        if provider == "apimart":
            if not self._api_key().strip():
                raise ApiError("请先填写当前 APIMART API Key。")
            # Validate the credential with APIMART's read-only balance route,
            # then inspect the model catalogue so Seedance 2.5 is only shown
            # when the platform currently exposes it. A catalogue outage must
            # not turn a valid image/Seedance 2.0 connection into a red error.
            endpoint = str(
                self.settings.get("connection_test_endpoint") or "/v1/balance"
            )
            self._request(endpoint, None)
            catalog_models: list[str] = []
            try:
                catalog = self._request(
                    str(self.settings.get("models_endpoint") or "/v1/models"),
                    None,
                )
                values = extract_path(
                    catalog,
                    str(self.settings.get("models_response_path") or "data.*.id"),
                )
                seen: set[str] = set()
                for item in values:
                    model = str(item or "").strip()
                    if model and model not in seen:
                        seen.add(model)
                        catalog_models.append(model)
            except ApiError:
                catalog_models = []

            if not catalog_models:
                return list(APIMART_IMAGE_MODELS) + [
                    model
                    for model in APIMART_SEEDANCE_MODELS
                    if not is_seedance_25_model(model)
                ]

            image_models = [
                model
                for model in APIMART_IMAGE_MODELS
                if model in catalog_models
            ]
            image_models.extend(
                model
                for model in catalog_models
                if is_compatible_image_model("apimart", model)
                and model not in image_models
            )
            if not image_models:
                image_models = list(APIMART_IMAGE_MODELS)

            normalized_catalog = {
                str(model).strip().lower().replace("_", "-")
                for model in catalog_models
            }
            seedance_models: list[str] = []
            alias_pairs = (
                ("seedance-2.0", "doubao-seedance-2.0"),
                ("seedance-2.0-fast", "doubao-seedance-2.0-fast"),
            )
            for discovered, request_model in alias_pairs:
                if (
                    discovered in normalized_catalog
                    or request_model in normalized_catalog
                ):
                    seedance_models.append(request_model)
            if "seedance-2.5" in normalized_catalog:
                seedance_models.append("seedance-2.5")
            if not seedance_models:
                seedance_models = [
                    model
                    for model in APIMART_SEEDANCE_MODELS
                    if not is_seedance_25_model(model)
                ]
            return image_models + seedance_models
        if provider == "aliyun_bailian":
            # Model Studio defaults to only 20 unfiltered models, which can
            # omit every image model. Ask for image-generation capability and
            # follow the documented pagination so a valid Key is not reported
            # as image-incompatible merely because its model appeared later.
            endpoint = str(self.settings.get("models_endpoint") or "/api/v1/models")
            path = str(
                self.settings.get("models_response_path")
                or "output.models.*.model"
            )
            models: list[str] = []
            seen: set[str] = set()
            page_size = 100
            for page_no in range(1, 21):
                page_endpoint = _endpoint_query(
                    endpoint,
                    capabilities="IG",
                    page_no=page_no,
                    page_size=page_size,
                )
                response = self._request(page_endpoint, None)
                values = extract_path(response, path)
                for value in values:
                    model = str(value or "").strip()
                    if model and model not in seen:
                        seen.add(model)
                        models.append(model)
                output = response.get("output") if isinstance(response, dict) else None
                page_items = output.get("models") if isinstance(output, dict) else None
                returned_count = (
                    len(page_items) if isinstance(page_items, list) else len(values)
                )
                try:
                    total = int(output.get("total") or 0) if isinstance(output, dict) else 0
                except (TypeError, ValueError):
                    total = 0
                if (total and len(models) >= total) or returned_count < page_size:
                    break
            if not models:
                raise ApiError(
                    "连接成功，但当前 Key 没有读取到可用的图像生成模型。"
                )
            return sorted(models, key=str.lower)
        endpoint = str(self.settings.get("models_endpoint") or "/v1/models")
        response = self._request(endpoint, None)
        path = str(self.settings.get("models_response_path") or "data.*.id")
        values = extract_path(response, path)
        models: list[str] = []
        seen: set[str] = set()
        for value in values:
            model = str(value or "").strip()
            if model and model not in seen:
                seen.add(model)
                models.append(model)
        if not models:
            raise ApiError(
                "连接成功，但没有读取到模型。可在专业参数中修改模型列表路径，或直接填写模型名称。"
            )
        return sorted(models, key=str.lower)

    def _enforce_provider_contract(
        self, payload: Any, context: dict[str, Any], job: dict
    ) -> Any:
        """Apply non-optional media fields for every built-in provider.

        Professional templates remain editable, but a stale or accidentally
        edited template must never be allowed to omit the C4D scene. The scene
        is restored as the first visual input immediately before submit.
        """

        if not isinstance(payload, dict):
            return payload
        provider = str(self.settings.get("provider") or "")
        if provider not in {
            "atlascloud",
            "volcengine",
            "aliyun_bailian",
            "apimart",
        }:
            return payload
        result = dict(payload)
        workflow = str(job.get("workflow") or "image")
        result["model"] = (
            context["$VIDEO_MODEL"] if workflow == "animation" else context["$MODEL"]
        )
        result["prompt"] = context["$PROMPT"]
        if workflow != "animation":
            # The dedicated C4D render is always visual input zero, even when
            # the user's prompt contains no @ reference token. Rebuild every
            # built-in provider's required field so an old saved template
            # cannot silently fall back to text-to-image generation.
            images = list(context["$ALL_INPUT_IMAGE_URLS"])
            locked = bool(
                job.get("scene_image")
                and (job.get("scene_context") or {}).get("preserve_composition")
            )
            if provider == "atlascloud":
                result["images"] = images
                model = str(context["$MODEL"] or "").lower()
                if "seedream" in model:
                    # Seedream edit uses an exact pixel size and does not share
                    # Nano Banana's thinking/media-resolution parameters. Its
                    # endpoint rejects either side below 1024, so use a
                    # request-only upscaled canvas and normalize the result
                    # back to the user's exact C4D dimensions after download.
                    for key in (
                        "aspect_ratio",
                        "resolution",
                        "thinking_level",
                        "media_resolution",
                        "enable_web_search",
                        "enable_image_search",
                    ):
                        result.pop(key, None)
                    result["size"] = _size_with_side_bounds(
                        context["$WIDTH"],
                        context["$HEIGHT"],
                        1024,
                        4096,
                        "*",
                    )
                else:
                    result.pop("size", None)
                    if context["$ASPECT"] is None:
                        result.pop("aspect_ratio", None)
                    else:
                        result["aspect_ratio"] = context["$ASPECT"]
                    result["resolution"] = context["$QUALITY_LOWER"]
            elif provider == "volcengine":
                result["image"] = images
                result["size"] = (
                    _size_with_side_bounds(
                        context["$WIDTH"],
                        context["$HEIGHT"],
                        1024,
                        4096,
                        "x",
                        32,
                    )
                    if locked and bool(job.get("aspect_auto"))
                    else context["$QUALITY"]
                )
                result["sequential_image_generation"] = "disabled"
                result["stream"] = False
                result["response_format"] = "url"
            elif provider == "aliyun_bailian":
                result.pop("prompt", None)
                result["input"] = {
                    "messages": [
                        {"role": "user", "content": context["$DASHSCOPE_CONTENT"]}
                    ]
                }
                parameters = dict(result.get("parameters") or {})
                parameters["n"] = context["$COUNT"]
                parameters["size"] = _size_with_pixel_limit(
                    context["$WIDTH"],
                    context["$HEIGHT"],
                    2048 * 2048,
                    512 * 512,
                )
                parameters["watermark"] = False
                if locked:
                    parameters["prompt_extend"] = False
                    parameters["negative_prompt"] = (
                        "改变构图，改变镜头，裁切，扩图，主体放大，主体缩小，"
                        "主体移动，主体变形，改变主体包围框"
                    )
                result["parameters"] = parameters
            elif provider == "apimart":
                result["image_urls"] = images
                # GPT Image 2 currently accepts one result per asynchronous
                # task. ``repeat_per_count`` submits multiple independent
                # tasks when the user requests more than one image.
                result["n"] = 1
                model = str(context["$MODEL"] or "").strip().lower()
                model_key = model.rsplit("/", 1)[-1].replace("_", "-")
                if model_key.startswith("flux-kontext-") and len(images) > 1:
                    raise ApiError(
                        "APIMART 的 Flux Kontext 每次只能接收 1 张场景图。"
                        "请移除提示词中的 @ 参考图后重试，或改用支持多参考图的模型。"
                    )
                if locked and bool(job.get("aspect_auto")):
                    if model_key in {"gpt-image-2", "gpt-image-2-ext"}:
                        # GPT Image 2 inherits the first input image's exact
                        # dimensions when ``size`` is omitted.
                        result.pop("size", None)
                    elif model_key == "gpt-image-2-official":
                        # The official channel's ``auto`` may still choose
                        # 1:1, while arbitrary pixel strings are not part of
                        # its guaranteed size table. Use the source-derived
                        # nearest documented ratio and normalize back to the
                        # exact C4D canvas after download.
                        result["size"] = str(job.get("aspect") or "1:1")
                    elif (
                        "seedream" in model_key
                        or "nano-banana" in model_key
                        or ("gemini" in model_key and "image" in model_key)
                        or model_key.startswith("flux-kontext-")
                    ):
                        # These edit families document ``auto`` as matching
                        # the reference image's aspect ratio.
                        result["size"] = "auto"
                    else:
                        # Qwen/Flux/Imagen-style APIMART adapters do not all
                        # expose ``auto``. Use the source-derived nearest ratio
                        # instead of allowing their default 1:1 canvas.
                        result["size"] = str(job.get("aspect") or "1:1")
                    result.pop("resolution", None)
                else:
                    result["size"] = context["$ASPECT"]
                    requested_quality = str(context["$QUALITY_LOWER"] or "1k").lower()
                    if "seedream-5" in model_key and "lite" in model_key:
                        # Seedream 5 Lite supports 2K/3K only.
                        result["resolution"] = (
                            "3K" if requested_quality == "4k" else "2K"
                        )
                    elif "seedream-5" in model_key:
                        # Other Seedream 5 image adapters start at 2K. Use
                        # their common high tier for the UI's 4K choice.
                        result["resolution"] = (
                            "4K" if requested_quality == "4k" else "2K"
                        )
                    elif "seedream-4.5" in model_key or "seedream-4-5" in model_key:
                        # Seedream 4.5 supports 2K/4K only.
                        result["resolution"] = (
                            "4K" if requested_quality == "4k" else "2K"
                        )
                    elif model_key.startswith(
                        ("qwen-image-2.0", "qwen-image-2-0", "qwen-image-3.0", "qwen-image-3-0")
                    ):
                        # APIMART's Qwen image adapter is capped at 2K.
                        result["resolution"] = (
                            "1K" if requested_quality == "1k" else "2K"
                        )
                    elif model_key.startswith("flux-kontext-"):
                        # Flux Kontext controls output through ``size`` and
                        # rejects/ignores the shared resolution field.
                        result.pop("resolution", None)
                    elif (
                        model_key == "gpt-image-2-official"
                        and requested_quality == "4k"
                        and str(context["$ASPECT"] or "")
                        not in {"16:9", "9:16", "2:1", "1:2", "21:9", "9:21"}
                    ):
                        result["resolution"] = "2k"
                    else:
                        result["resolution"] = requested_quality
            return result

        if provider == "apimart":
            result.pop("images", None)
            result.pop("aspect_ratio", None)
            image_urls = list(context["$ANIMATION_REFERENCE_IMAGE_URLS"])
            video_urls = list(context["$ANIMATION_REFERENCE_VIDEO_URLS"])
            if image_urls:
                result["image_urls"] = image_urls
            else:
                result.pop("image_urls", None)
            if video_urls:
                result["video_urls"] = video_urls
            else:
                result.pop("video_urls", None)
            video_ratio = str(context["$VIDEO_RATIO"] or "adaptive")
            if video_ratio not in {"adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}:
                video_ratio = "adaptive"
            result["size"] = video_ratio
            model = str(context["$VIDEO_MODEL"] or "")
            seedance_25 = is_seedance_25_model(model)
            result["duration"] = max(
                5,
                min(30 if seedance_25 else 15, int(context["$DURATION"])),
            )
            resolution = str(context["$VIDEO_RESOLUTION"] or "720p").lower()
            supported_resolutions = (
                {"480p", "720p", "1080p", "4k"}
                if seedance_25
                else {"480p", "720p"}
            )
            # Keep old 2.0 jobs on their documented 480p/720p range while
            # exposing Seedance 2.5's full 480p–4K range.
            if resolution not in supported_resolutions:
                resolution = "720p"
            result["resolution"] = resolution
            # C4D white-model previews do not carry a useful soundtrack.
            # Disabling AI audio removes an unnecessary generation pass and
            # materially reduces queue time for the common design workflow.
            result["generate_audio"] = False
            return result

        if provider == "volcengine":
            result.pop("images", None)
            result.pop("image_urls", None)
            result.pop("video_urls", None)
            result.pop("aspect_ratio", None)
            result["content"] = list(context["$VOLCENGINE_VIDEO_CONTENT"])
            video_ratio = str(context["$VIDEO_RATIO"] or "adaptive")
            if video_ratio not in {"adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}:
                video_ratio = "adaptive"
            result["ratio"] = video_ratio
            result["duration"] = max(5, min(15, int(context["$DURATION"])))
            resolution = str(context["$VIDEO_RESOLUTION"] or "720p").lower()
            if (
                "fast" in str(context["$VIDEO_MODEL"] or "").lower()
                and resolution == "1080p"
            ):
                resolution = "720p"
            if resolution not in {"480p", "720p", "1080p"}:
                resolution = "720p"
            result["resolution"] = resolution
            result["generate_audio"] = True
            result["watermark"] = False
            return result

        if provider != "atlascloud":
            raise ApiError(seedance_unavailable_message(self.settings))

        # Seedance reference-to-video distinguishes movie and still inputs.
        # Omit empty arrays because Atlas declares a minimum of one item when
        # either optional field is present.
        result.pop("images", None)
        result.pop("aspect_ratio", None)
        video_urls = list(context["$ANIMATION_REFERENCE_VIDEO_URLS"])
        image_urls = list(context["$ANIMATION_REFERENCE_IMAGE_URLS"])
        if video_urls:
            result["reference_videos"] = video_urls
        else:
            result.pop("reference_videos", None)
        if image_urls:
            result["reference_images"] = image_urls
        else:
            result.pop("reference_images", None)
        result["duration"] = context["$DURATION"]
        resolution = str(context["$VIDEO_RESOLUTION"] or "720p").lower()
        model = str(context["$VIDEO_MODEL"] or "").lower()
        if resolution == "4k" and ("fast" in model or "mini" in model):
            # Atlas documents native 4K only for the full Seedance model.
            resolution = "1080p"
        result["resolution"] = resolution
        video_ratio = str(context["$VIDEO_RATIO"] or "adaptive")
        if video_ratio not in {"adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}:
            video_ratio = "adaptive"
        result["ratio"] = video_ratio
        # Keep motion rendering focused on the uploaded C4D guide. Atlas
        # documents audio as optional; no-audio is the fastest predictable
        # default for architectural and product animation previews.
        result["generate_audio"] = False
        return result

    def _wait_for_task(
        self,
        initial: Any,
        progress: Optional[Callable[[float, str], None]],
        should_cancel: Optional[Callable[[], bool]] = None,
        job: Optional[dict] = None,
    ) -> Any:
        _check_cancel(should_cancel)
        result_url_path = str(
            self._job_setting(job, "poll_response_url_path", "results.*.url")
            or "results.*.url"
        )
        result_b64_path = str(
            self._job_setting(job, "poll_response_base64_path", "results.*.b64_json")
            or "results.*.b64_json"
        )
        workflow = str((job or {}).get("workflow") or "image")
        # Some gateways complete synchronously even when the selected preset is
        # asynchronous. Accept that response immediately instead of waiting for
        # a task ID and an unnecessary first polling delay.
        if extract_media_urls(initial, result_url_path, workflow) or extract_path(
            initial, result_b64_path
        ):
            if progress:
                progress(0.98, "服务端已直接返回结果")
            return initial
        task_values = extract_path(
            initial, str(self._job_setting(job, "task_id_path", "id") or "id")
        )
        if not task_values:
            raise ApiError("异步响应中找不到任务 ID。")
        task_id = str(task_values[0])
        started_at = time.monotonic()
        deadline = started_at + max(
            30.0,
            float(self._job_setting(job, "poll_timeout_seconds", 600) or 600),
        )
        base_interval = max(
            0.5, float(self._job_setting(job, "poll_interval_seconds", 2) or 2)
        )
        interval = base_interval
        initial_delay = max(
            0.0,
            float(self._job_setting(job, "poll_initial_delay_seconds", 0) or 0),
        )
        poll_request_timeout = max(
            5.0,
            float(self._job_setting(job, "poll_request_timeout_seconds", 30) or 30),
        )
        stall_notice_seconds = max(
            10.0,
            float(self._job_setting(job, "poll_stall_notice_seconds", 45) or 45),
        )
        result_grace_seconds = max(
            15.0,
            float(self._job_setting(job, "poll_result_grace_seconds", 90) or 90),
        )
        max_transient_failures = max(
            4,
            int(self._job_setting(job, "poll_max_transient_failures", 12) or 12),
        )
        success = {
            str(item).lower()
            for item in self._job_setting(job, "success_values", []) or []
        }
        failure = {
            str(item).lower()
            for item in self._job_setting(job, "failure_values", []) or []
        }
        poll_template = str(
            self._job_setting(job, "poll_endpoint", "/v1/tasks/$TASK_ID")
            or "/v1/tasks/$TASK_ID"
        )
        endpoint = poll_template.replace("$TASK_ID", urllib.parse.quote(task_id, safe=""))
        attempt = 0
        transient_failures = 0
        last_status = ""
        last_provider_progress: Optional[float] = None
        highest_provider_progress = 0.0
        last_change_at = started_at
        completed_without_result_at: Optional[float] = None
        provider = str(self.settings.get("provider") or "").strip().lower()
        while time.monotonic() < deadline:
            _check_cancel(should_cancel)
            # APIMART recommends a brief delay before the first video query and
            # a steady interval afterwards. Every wait remains cancellable.
            delay = initial_delay if attempt == 0 else interval
            if delay:
                self._wait_poll_delay(delay, should_cancel)
            attempt += 1
            poll_method = str(
                self._job_setting(job, "poll_method", "GET") or "GET"
            ).upper()
            poll_payload = {"id": task_id} if poll_method == "POST" else None
            try:
                self._request_timeout_override = poll_request_timeout
                response = self._request(endpoint, poll_payload, should_cancel)
                transient_failures = 0
                interval = base_interval
            except ApiError as exc:
                message = str(exc)
                transient = any(
                    marker in message
                    for marker in (
                        "无法连接 API",
                        "请求过于频繁",
                        "HTTP 408",
                        "HTTP 500",
                        "HTTP 502",
                        "HTTP 503",
                        "HTTP 504",
                    )
                )
                transient_failures += 1
                if not transient or transient_failures > max_transient_failures:
                    raise
                interval = min(30.0, max(base_interval, interval * 1.6))
                if progress:
                    progress(
                        min(0.92, max(highest_provider_progress, 0.15)),
                        "平台网络暂时波动，正在自动恢复查询（第 {} 次）…".format(
                            attempt
                        ),
                    )
                continue
            finally:
                self._request_timeout_override = None
            _check_cancel(should_cancel)
            statuses = extract_path(
                response,
                str(self._job_setting(job, "status_path", "status") or "status"),
            )
            status = str(statuses[0] if statuses else "").lower()
            progress_values = extract_path(
                response,
                str(self._job_setting(job, "progress_path", "progress") or "progress"),
            )
            provider_progress: Optional[float] = None
            if progress_values:
                try:
                    provider_progress = min(
                        100.0, max(0.0, float(progress_values[0]))
                    )
                except (TypeError, ValueError):
                    provider_progress = None
            now = time.monotonic()
            if status != last_status or provider_progress != last_provider_progress:
                last_change_at = now
                last_status = status
                last_provider_progress = provider_progress
            if provider_progress is not None:
                highest_provider_progress = max(
                    highest_provider_progress, provider_progress / 100.0
                )
                value = min(0.99, highest_provider_progress)
            else:
                value = min(
                    0.92,
                    max(highest_provider_progress, 0.15 + attempt * 0.025),
                )

            if progress:
                if provider == "apimart":
                    if provider_progress is None:
                        status_message = (
                            "APIMART 正在处理，插件持续查询（第 {} 次）".format(
                                attempt
                            )
                        )
                    else:
                        shown = int(round(provider_progress))
                        if now - last_change_at >= stall_notice_seconds:
                            status_message = (
                                "APIMART 平台进度 {}%，平台仍在生成（不是插件卡死）；插件持续查询同一任务（第 {} 次）".format(
                                    shown, attempt
                                )
                            )
                        else:
                            status_message = (
                                "APIMART 平台进度 {}% · 正在查询（第 {} 次）".format(
                                    shown, attempt
                                )
                            )
                    estimated_values = extract_path(response, "data.estimated_time")
                    if not estimated_values:
                        estimated_values = extract_path(response, "estimated_time")
                    if estimated_values:
                        try:
                            estimated = max(0, int(float(estimated_values[0])))
                        except (TypeError, ValueError):
                            estimated = 0
                        if estimated:
                            status_message += " · 平台预计约 {} 秒".format(estimated)
                else:
                    status_message = "任务状态：{} · 第 {} 次查询".format(
                        status or "等待", attempt
                    )
                progress(value, status_message)
            if extract_media_urls(response, result_url_path, workflow) or extract_path(
                response, result_b64_path
            ):
                return response
            if status in success:
                # A completed task can become visible slightly before its CDN
                # result URL. Keep querying the same paid task instead of
                # failing and tempting the user to submit (and pay for) it again.
                if completed_without_result_at is None:
                    completed_without_result_at = now
                    deadline = max(deadline, now + result_grace_seconds)
                if now - completed_without_result_at < result_grace_seconds:
                    if progress:
                        progress(
                            0.99,
                            "APIMART 平台进度 100% · 任务已完成，正在同步视频地址（第 {} 次）".format(
                                attempt
                            )
                            if provider == "apimart"
                            else "任务已完成，正在等待结果地址（第 {} 次）".format(
                                attempt
                            ),
                        )
                    continue
                raise ApiError(
                    "任务已完成，但平台尚未返回结果地址。任务 ID：{}。"
                    "请先到平台任务记录中查看，避免重复提交。".format(task_id)
                )
            if status in failure:
                details = []
                for detail_path in (
                    "data.error.message",
                    "error.message",
                    "data.failure_reason",
                    "failure_reason",
                    "message",
                ):
                    details = extract_path(response, detail_path)
                    if details:
                        break
                suffix = "：{}".format(str(details[0])) if details else ""
                raise ApiError("生成任务失败（{}）{}".format(status, suffix))
        if provider == "apimart":
            raise ApiError(
                "APIMART 动画任务长时间仍在处理中。任务 ID：{}。"
                "插件没有重复提交或取消该任务，请先到 APIMART 任务记录中查看结果。".format(
                    task_id
                )
            )
        raise ApiError("生成任务轮询超时（任务 ID：{}）。".format(task_id))

    def generate(
        self,
        job: dict,
        progress: Optional[Callable[[float, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> list[str]:
        _check_cancel(should_cancel)
        if progress:
            progress(0.05, "正在编码场景与参考图…")
        workflow = str(job.get("workflow") or "image")
        if workflow == "animation":
            animation_ready, animation_reason = seedance_capability(self.settings)
            if not animation_ready:
                raise ApiError(animation_reason or seedance_unavailable_message(self.settings))
        context = self._build_context(job, should_cancel)
        _check_cancel(should_cancel)
        payload = drop_none_fields(
            expand_template(self._job_setting(job, "request_template", {}) or {}, context)
        )
        payload = self._enforce_provider_contract(payload, context, job)
        results: list[str] = []
        repeat_per_count = workflow != "animation" and bool(
            self.settings.get("repeat_per_count")
            or str(self.settings.get("provider") or "") == "atlascloud"
        )
        repeats = max(1, int(job.get("count") or 1)) if repeat_per_count else 1
        for run_index in range(repeats):
            _check_cancel(should_cancel)
            if progress:
                progress(
                    0.10 + 0.72 * (run_index / repeats),
                    "正在提交生成任务（{}/{}）…".format(run_index + 1, repeats),
                )
            endpoint = str(self._job_setting(job, "endpoint", "") or "")
            response = (
                self._request(endpoint, payload)
                if should_cancel is None
                else self._request(endpoint, payload, should_cancel)
            )
            _check_cancel(should_cancel)
            if bool(self._job_setting(job, "async_enabled", False)):
                if progress:
                    def mapped_progress(value, status, current=run_index):
                        unit = "段" if workflow == "animation" else "张"
                        progress(
                            0.10 + 0.72 * ((current + min(1.0, value)) / repeats),
                            "第 {}/{} {} · {}".format(
                                current + 1, repeats, unit, status
                            ),
                        )
                else:
                    mapped_progress = None
                response = self._wait_for_task(
                    response, mapped_progress, should_cancel, job
                )
                url_path = str(
                    self._job_setting(job, "poll_response_url_path", "") or ""
                )
                b64_path = str(
                    self._job_setting(job, "poll_response_base64_path", "") or ""
                )
            else:
                url_path = str(self._job_setting(job, "response_url_path", "") or "")
                b64_path = str(
                    self._job_setting(job, "response_base64_path", "") or ""
                )

            urls = extract_media_urls(response, url_path, workflow)
            encoded = [str(value) for value in extract_path(response, b64_path) if value]
            if not urls and not encoded:
                raise ApiError(
                    "响应中没有找到生成结果。请检查结果 URL/Base64 的 JSON 路径设置。"
                )
            for url in urls:
                _check_cancel(should_cancel)
                if progress:
                    progress(
                        0.84 + 0.14 * ((run_index + 0.5) / repeats),
                        "正在下载第 {}/{} 张结果…".format(run_index + 1, repeats),
                    )
                results.append(self._download(url, len(results), should_cancel, job))
            for value in encoded:
                _check_cancel(should_cancel)
                results.append(self._save_b64(value, len(results), job))
        _check_cancel(should_cancel)
        if progress:
            progress(1.0, "生成完成")
        return results
