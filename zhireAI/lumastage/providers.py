"""Provider presets and safe URL normalization for 挚热AI渲染器."""

from __future__ import annotations

from copy import deepcopy
from typing import Optional
from urllib.parse import urlparse

from .constants import is_seedance_model


ATLAS_IMAGE_MODELS = [
    "google/nano-banana-2/edit",
    "bytedance/seedream-v4/edit",
]

# Curated JuAIHub image IDs used when an older saved /v1/models response does
# not yet contain the latest GPT Image 2.5 variants.
JUAIHUB_IMAGE_MODELS = [
    # The live JuAIHub catalogue currently exposes Gemini's official image
    # IDs for the Banana family; the old nano-banana aliases are retired.
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
    "gpt-image-2",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
]

JUAIHUB_MODEL_ALIASES = {
    "nano-banana-2": "gemini-3.1-flash-image-preview",
    "nano-banana-2-cl": "gemini-3.1-flash-image-preview",
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-pro-cl": "gemini-3-pro-image-preview",
}


# Only multimodal reference-to-video variants are exposed in the animation
# panel. This keeps the C4D white-model movie as Seedance's primary guide.
ATLAS_SEEDANCE_MODELS = [
    "bytedance/seedance-2.0/reference-to-video",
    "bytedance/seedance-2.0-fast/reference-to-video",
    "bytedance/seedance-2.0-mini/reference-to-video",
    "bytedance/seedance-2.5/reference-to-video",
]

# APIMART publishes these Nano Banana image IDs on the same asynchronous
# /v1/images/generations contract. Keep a curated bootstrap list so connecting
# a valid key exposes the intended image workflow even when /v1/models also
# contains a large number of unrelated text models.
APIMART_IMAGE_MODELS = [
    "gemini-3.1-flash-image-preview",
    "gemini-3.1-flash-image-preview-official",
    "gemini-3-pro-image-preview",
    "gemini-3-pro-image-preview-official",
    "gemini-2.5-flash-image-preview",
    "gemini-2.5-flash-image-preview-official",
]

# APIMART exposes Seedance 2.5 directly and uses the documented Doubao aliases
# for its 2.0 generation contract. Face variants require a separate identity-
# review workflow and are intentionally not exposed in this general renderer.
APIMART_SEEDANCE_MODELS = [
    "doubao-seedance-2.0",
    "doubao-seedance-2.0-fast",
    "seedance-2.5",
]

# The built-in Volcengine profile is intentionally image-only. Keeping this
# public constant avoids breaking imports from previous V2 builds while an
# empty list prevents old saved animation settings from being revived.
VOLCENGINE_SEEDANCE_MODELS: list[str] = []

ATLAS_CLOUD_PRESET = {
    "contract_revision": 3,
    "provider": "atlascloud",
    "profile_name": "Atlas Cloud 图像 + Seedance API",
    "base_url": "https://api.atlascloud.ai",
    "endpoint": "/api/v1/model/generateImage",
    "method": "POST",
    "model": "google/nano-banana-2/edit",
    # Atlas media models are not reliably exposed by /v1/models.  Validate the
    # selected key against the official read-only billing endpoint instead;
    # the image model list below remains curated for the C4D workflow.
    "connection_test_endpoint": "/public/v1/balance",
    "models_endpoint": "/v1/models",
    "models_response_path": "data.*.id",
    "media_upload_endpoint": "/api/v1/model/uploadMedia",
    "image_upload_supported": True,
    "video_upload_supported": True,
    "animation_reference_mode": "video",
    "auth_type": "bearer",
    "api_key_header": "Authorization",
    "headers": {"Content-Type": "application/json"},
    "request_template": {
        "model": "$MODEL",
        "images": "$ALL_INPUT_IMAGE_URLS",
        "prompt": "$PROMPT",
        "aspect_ratio": "$ASPECT",
        "resolution": "$QUALITY_LOWER",
        "thinking_level": "default",
        "media_resolution": "high",
        "enable_web_search": False,
        "enable_image_search": False,
        "enable_sync_mode": False,
        "enable_base64_output": False,
    },
    "response_url_path": "",
    "response_base64_path": "",
    "async_enabled": True,
    "task_id_path": "data.id",
    "poll_endpoint": "/api/v1/model/prediction/$TASK_ID",
    "status_path": "data.status",
    "poll_response_url_path": "data.outputs.*",
    "poll_response_base64_path": "",
    # Image and Seedance generation share one Atlas host, Bearer key, upload
    # endpoint and polling contract. Selecting Atlas therefore completes both
    # workflows without making designers edit the professional parameters.
    "video_endpoint": "/api/v1/model/generateVideo",
    "video_model": ATLAS_SEEDANCE_MODELS[0],
    "seedance_models": list(ATLAS_SEEDANCE_MODELS),
    "supports_seedance_animation": True,
    "seedance_unavailable_reason": "",
    "video_request_template": {
        "model": "$VIDEO_MODEL",
        "prompt": "$PROMPT",
        "reference_images": "$ANIMATION_REFERENCE_IMAGE_URLS",
        "reference_videos": "$ANIMATION_REFERENCE_VIDEO_URLS",
        "duration": "$DURATION",
        "resolution": "$VIDEO_RESOLUTION",
        "ratio": "$VIDEO_RATIO",
        "bitrate_mode": "standard",
        "generate_audio": False,
        "watermark": False,
    },
    "video_response_url_path": "",
    "video_response_base64_path": "",
    "video_async_enabled": True,
    "video_task_id_path": "data.id",
    "video_poll_endpoint": "/api/v1/model/prediction/$TASK_ID",
    "video_status_path": "data.status",
    "video_progress_path": "",
    "video_poll_response_url_path": "data.outputs.*",
    "video_poll_response_base64_path": "",
    # Atlas' official reference implementation queries every two seconds.
    # This does not change paid model compute time, but removes avoidable wait
    # between remote completion and the result appearing in C4D.
    "video_poll_initial_delay_seconds": 2.0,
    "video_poll_interval_seconds": 2.0,
    "video_poll_request_timeout_seconds": 20,
    "video_poll_timeout_seconds": 1800,
    "video_poll_stall_notice_seconds": 60,
    "video_poll_result_grace_seconds": 90,
    "video_poll_max_transient_failures": 20,
    "video_output_extension": ".mp4",
}


VOLCENGINE_PRESET = {
    "contract_revision": 2,
    "provider": "volcengine",
    "profile_name": "火山引擎方舟（仅图像）",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "endpoint": "/images/generations",
    "method": "POST",
    "model": "doubao-seedream-4-0-250828",
    "models_endpoint": "/models",
    "models_response_path": "data.*.id",
    "image_upload_supported": False,
    "video_upload_supported": False,
    "animation_reference_mode": "none",
    "auth_type": "bearer",
    "api_key_header": "Authorization",
    "headers": {"Content-Type": "application/json"},
    "request_template": {
        "model": "$MODEL",
        "prompt": "$PROMPT",
        "image": "$ALL_INPUT_IMAGE_URLS",
        "size": "$QUALITY",
        "sequential_image_generation": "disabled",
        "stream": False,
        "response_format": "url",
        "watermark": False,
    },
    "response_url_path": "data.*.url",
    "response_base64_path": "data.*.b64_json",
    "async_enabled": False,
    "repeat_per_count": True,
    "video_endpoint": "",
    "video_model": "",
    "seedance_models": [],
    "supports_seedance_animation": False,
    "seedance_unavailable_reason": "目前火山引擎和阿里百炼暂不支持动画功能。",
}


ALIYUN_BAILIAN_PRESET = {
    "contract_revision": 2,
    "provider": "aliyun_bailian",
    "profile_name": "阿里云百炼（仅图像）",
    "base_url": "https://dashscope.aliyuncs.com",
    "endpoint": "/api/v1/services/aigc/multimodal-generation/generation",
    "method": "POST",
    "model": "qwen-image-2.0-pro",
    "models_endpoint": "/api/v1/models",
    "models_response_path": "output.models.*.model",
    "image_upload_supported": False,
    "video_upload_supported": False,
    "animation_reference_mode": "none",
    "auth_type": "bearer",
    "api_key_header": "Authorization",
    "headers": {"Content-Type": "application/json"},
    "request_template": {
        "model": "$MODEL",
        "input": {
            "messages": [
                {"role": "user", "content": "$DASHSCOPE_CONTENT"}
            ]
        },
        "parameters": {
            "n": "$COUNT",
            "size": "$SIZE_STAR",
            "prompt_extend": True,
            "watermark": False,
        },
    },
    "response_url_path": "output.choices.*.message.content.*.image",
    "response_base64_path": "",
    "async_enabled": False,
    "video_endpoint": "",
    "video_model": "",
    "seedance_models": [],
    "supports_seedance_animation": False,
}


APIMART_PRESET = {
    "contract_revision": 3,
    "provider": "apimart",
    "profile_name": "APIMART Nano Banana + Seedance API",
    "base_url": "https://api.apimart.ai",
    "endpoint": "/v1/images/generations",
    "method": "POST",
    "model": APIMART_IMAGE_MODELS[0],
    "models_endpoint": "/v1/models",
    "models_response_path": "data.*.id",
    "connection_test_endpoint": "/v1/balance",
    "media_upload_endpoint": "/v1/uploads/images",
    "image_upload_supported": True,
    "video_upload_supported": False,
    "animation_reference_mode": "frames",
    "auth_type": "bearer",
    "api_key_header": "Authorization",
    "headers": {"Content-Type": "application/json"},
    "request_template": {
        "model": "$MODEL",
        "prompt": "$PROMPT",
        "image_urls": "$ALL_INPUT_IMAGE_URLS",
        "size": "$ASPECT",
        "resolution": "$QUALITY_LOWER",
        "n": "$COUNT",
    },
    "response_url_path": "",
    "response_base64_path": "",
    "async_enabled": True,
    "task_id_path": "data.*.task_id",
    "poll_endpoint": "/v1/tasks/$TASK_ID",
    "status_path": "data.status",
    "progress_path": "data.progress",
    "poll_response_url_path": "data.result.images.*.url.*",
    "poll_response_base64_path": "",
    "poll_interval_seconds": 4.0,
    "poll_timeout_seconds": 600,
    "repeat_per_count": True,
    "video_endpoint": "/v1/videos/generations",
    "video_model": APIMART_SEEDANCE_MODELS[0],
    "seedance_models": list(APIMART_SEEDANCE_MODELS),
    "supports_seedance_animation": True,
    "seedance_unavailable_reason": "",
    "video_request_template": {
        "model": "$VIDEO_MODEL",
        "prompt": "$PROMPT",
        "image_urls": "$ANIMATION_REFERENCE_IMAGE_URLS",
        "video_urls": "$ANIMATION_REFERENCE_VIDEO_URLS",
        "size": "$VIDEO_RATIO",
        "resolution": "$VIDEO_RESOLUTION",
        "duration": "$DURATION",
        "generate_audio": False,
    },
    "video_response_url_path": "",
    "video_response_base64_path": "",
    "video_async_enabled": True,
    "video_task_id_path": "data.*.task_id",
    "video_poll_endpoint": "/v1/tasks/$TASK_ID?language=zh",
    "video_status_path": "data.status",
    "video_progress_path": "data.progress",
    "video_poll_response_url_path": "data.result.videos.*.url.*",
    "video_poll_response_base64_path": "",
    # Video tasks commonly keep one provider percentage for several minutes.
    # Use APIMART's recommended polling cadence, tolerate queue time, and make
    # a slow status request reconnect independently from the upload timeout.
    "video_poll_initial_delay_seconds": 6.0,
    "video_poll_interval_seconds": 6.0,
    "video_poll_request_timeout_seconds": 30,
    "video_poll_timeout_seconds": 1800,
    "video_poll_stall_notice_seconds": 45,
    "video_poll_result_grace_seconds": 90,
    "video_poll_max_transient_failures": 20,
    "video_output_extension": ".mp4",
    "video_success_values": ["completed", "succeeded"],
    "video_failure_values": ["failed", "cancelled", "canceled"],
}


PROVIDER_PRESETS = {
    "atlascloud": ATLAS_CLOUD_PRESET,
    "apimart": APIMART_PRESET,
    "volcengine": VOLCENGINE_PRESET,
    "aliyun_bailian": ALIYUN_BAILIAN_PRESET,
}


PROVIDER_LABELS = {
    "custom": "挚热 API",
}


def is_image_generation_model(model: str) -> bool:
    """Conservatively classify model IDs shown in the image-model controls."""

    value = str(model or "").strip().lower().replace("_", "-")
    if not value or is_seedance_model(value):
        return False
    markers = (
        "image",
        "seedream",
        "flux-kontext",
        "gpt-image",
        "z-image",
    )
    return any(marker in value for marker in markers)


def is_aliyun_image_edit_model(model: str) -> bool:
    """Return whether a Bailian model supports this edit-and-size contract.

    Model Studio's general model listing also contains text-to-image-only
    models such as ``qwen-image-max`` and the legacy ``qwen-image-edit`` model,
    which does not accept the size field used by this adapter. Keep only the
    documented 2.0/3.0 and edit Max/Plus families in the editing controls.
    """

    value = str(model or "").strip().lower().replace("_", "-")
    compatible_families = (
        "qwen-image-2.0",
        "qwen-image-2-0",
        "qwen-image-3.0",
        "qwen-image-3-0",
        "qwen-image-edit-max",
        "qwen-image-edit-plus",
    )
    return any(
        value == family or value.startswith(family + "-")
        for family in compatible_families
    )


def is_apimart_image_edit_model(model: str) -> bool:
    """Keep APIMART discovery on models that accept reference images.

    APIMART's general model endpoint also lists text-to-image-only models.
    The C4D workflow is intentionally image-to-image and always submits the
    scene through ``image_urls``, so presenting a model which cannot consume
    that field would silently lose the user's composition.
    """

    value = str(model or "").strip().lower().replace("_", "-")
    if not value or "/" in value or is_seedance_model(value):
        return False
    exact_models = {
        "gpt-image-2",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst",
        "gpt-image-2-ext",
        "gpt-image-2-official",
        "gemini-3.1-flash-image-preview",
        "gemini-3.1-flash-image-preview-official",
        "gemini-3-pro-image-preview",
        "gemini-3-pro-image-preview-official",
        "gemini-2.5-flash-image-preview",
        "gemini-2.5-flash-image-preview-official",
        "flux-kontext-pro",
        "flux-kontext-max",
    }
    if value in exact_models:
        return True
    # APIMART publishes these versioned families under a shared
    # /v1/images/generations + image_urls contract. Requiring their canonical
    # un-namespaced prefix prevents an Atlas/other-provider ID from leaking
    # into the APIMART profile merely because its display name looks similar.
    return value.startswith("doubao-seedream-") or value.startswith(
        ("qwen-image-2.0", "qwen-image-2-0", "qwen-image-3.0", "qwen-image-3-0")
    )


def is_volcengine_image_edit_model(model: str) -> bool:
    """Accept only Ark Seedream image models handled by this adapter.

    Ark ``ep-*`` identifiers can point to any deployed model. Their prefix
    does not prove image-edit capability, so they must not be presented as a
    working image model without endpoint metadata which the generic model-list
    response does not provide.
    """

    value = str(model or "").strip().lower().replace("_", "-")
    return value.startswith("doubao-seedream-")


def is_compatible_image_model(provider: str, model: str) -> bool:
    """Filter discovered image models against the active adapter contract."""

    provider_id = str(provider or "custom").strip().lower()
    if provider_id == "atlascloud":
        return str(model or "").strip() in ATLAS_IMAGE_MODELS
    if provider_id == "volcengine":
        return is_volcengine_image_edit_model(model)
    if provider_id == "aliyun_bailian":
        return is_aliyun_image_edit_model(model)
    if provider_id == "apimart":
        return is_apimart_image_edit_model(model)
    return is_image_generation_model(model)


def is_compatible_seedance_model(provider: str, model: str) -> bool:
    """Return whether this V2 adapter can safely submit the discovered model.

    A provider model list can contain older/newer Seedance generations whose
    request fields differ from the 2.0 all-reference workflow implemented by
    this plug-in. Discovery must therefore prove both account access and an
    adapter-compatible model, instead of enabling every ID containing the
    word ``seedance``.
    """

    provider_id = str(provider or "custom").strip().lower()
    value = str(model or "").strip()
    if provider_id == "atlascloud":
        return value in ATLAS_SEEDANCE_MODELS
    if provider_id == "apimart":
        return value in APIMART_SEEDANCE_MODELS
    if provider_id == "volcengine":
        return False
    if provider_id == "custom":
        return is_seedance_model(value)
    return False


def is_seedance_25_model(model: str) -> bool:
    """Return whether a provider model ID belongs to the Seedance 2.5 family."""

    value = str(model or "").strip().lower().replace("_", "-")
    return "seedance-2.5" in value or "seedance-2-5" in value


def seedance_unavailable_message(settings: dict) -> str:
    """Return one consistent, non-technical animation availability message."""

    values = settings if isinstance(settings, dict) else {}
    provider = str(
        values.get("last_connected_provider")
        or values.get("provider")
        or "custom"
    ).strip().lower()
    if provider in {"volcengine", "aliyun_bailian"}:
        return "目前火山引擎和阿里百炼暂不支持动画功能。"
    if provider == "custom":
        return "该平台暂不支持动画功能。"
    label = PROVIDER_LABELS.get(provider, "当前平台")
    return "{} 当前暂不支持动画功能。".format(label)


def seedance_capability(settings: dict) -> tuple[bool, str]:
    """Return whether the active provider has a usable Seedance contract.

    Connection and generation are intentionally split into image and animation
    capabilities.  This lets an image-only provider stay active while the UI
    gives a precise explanation when the user clicks the animation segment.
    """

    values = settings if isinstance(settings, dict) else {}
    provider = str(values.get("provider") or "custom").strip().lower()
    endpoint = str(values.get("video_endpoint") or "").strip()
    model = str(values.get("video_model") or "").strip()
    seedance_models = values.get("seedance_models")
    known_models = [
        str(item or "").strip()
        for item in seedance_models or []
        if is_compatible_seedance_model(provider, str(item or ""))
    ] if isinstance(seedance_models, list) else []
    contract_ready = bool(
        endpoint
        and is_compatible_seedance_model(provider, model)
    )
    explicit = values.get("supports_seedance_animation")

    # An explicit disabled state always wins. This prevents an old or manually
    # typed model containing the word "seedance" from creating a false-positive
    # animation tab.
    if explicit is False:
        return False, seedance_unavailable_message(values)

    recognized_provider = provider in {"atlascloud", "apimart"}
    model_verified = model in known_models
    custom_verified = provider == "custom" and explicit is True and model_verified
    if contract_ready and explicit is True and (
        (recognized_provider and model_verified) or custom_verified
    ):
        return True, ""

    # Do not surface provider contracts, endpoint names or stale technical
    # reasons saved by older builds. Designers only need to know which part of
    # the product remains available and what cannot be used on this platform.
    return False, seedance_unavailable_message(values)


def provider_from_url(value: str) -> str:
    """Identify known providers without attempting to infer them from a key."""

    raw = str(value or "").strip()
    if not raw:
        return "auto"
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    host = (parsed.hostname or "").lower()
    if host == "atlascloud.ai" or host.endswith(".atlascloud.ai"):
        return "atlascloud"
    if host == "volcengine.com" or host.endswith(".volcengine.com") or host.endswith(".volces.com"):
        return "volcengine"
    if (
        host in {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
        }
        or host.endswith(".dashscope.aliyuncs.com")
        or host.endswith(".maas.aliyuncs.com")
    ):
        return "aliyun_bailian"
    if host in {"apib.ai", "apimart.ai"} or host.endswith((".apib.ai", ".apimart.ai")):
        return "apimart"
    return "custom"


def normalized_base_url(value: str) -> str:
    """Convert known website URLs into API roots and trim generic URLs."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    provider = provider_from_url(raw)
    if provider == "atlascloud":
        return "https://api.atlascloud.ai"
    if provider == "volcengine":
        parsed = urlparse(raw if "://" in raw else "https://" + raw)
        host = (parsed.hostname or "").lower()
        if host.endswith(".volces.com"):
            prefix = "{}://{}".format(parsed.scheme or "https", parsed.netloc)
            path = parsed.path.rstrip("/")
            if path.endswith("/api/v3"):
                return prefix + path
            return prefix + "/api/v3"
        return "https://ark.cn-beijing.volces.com/api/v3"
    if provider == "aliyun_bailian":
        parsed = urlparse(raw if "://" in raw else "https://" + raw)
        return "{}://{}".format(parsed.scheme or "https", parsed.netloc)
    if provider == "apimart":
        return "https://api.apimart.ai"
    return raw.rstrip("/")


def configure_custom_seedance_contract(settings: dict, models: list[str]) -> dict:
    """Configure a common Seedance contract after authenticated discovery.

    A custom platform is animation-capable only when its model list actually
    exposes Seedance. This keeps image-only gateways usable while allowing a
    compatible OpenAI-style gateway to work from just its key and API address.
    """

    result = deepcopy(settings or {})
    discovered = []
    for item in models or []:
        model = str(item or "").strip()
        if is_compatible_seedance_model("custom", model) and model not in discovered:
            discovered.append(model)
    result["seedance_models"] = discovered
    if not discovered:
        result["video_model"] = ""
        result["supports_seedance_animation"] = False
        result["seedance_unavailable_reason"] = seedance_unavailable_message(
            {"provider": "custom"}
        )
        return result

    selected = str(result.get("video_model") or "").strip()
    result["video_model"] = selected if selected in discovered else discovered[0]
    result["video_endpoint"] = str(
        result.get("video_endpoint") or "/v1/videos/generations"
    ).strip()
    result["animation_reference_mode"] = str(
        result.get("animation_reference_mode") or "frames"
    ).strip()
    result["video_request_template"] = {
        "model": "$VIDEO_MODEL",
        "prompt": "$PROMPT",
        "image_urls": "$ANIMATION_REFERENCE_IMAGE_URLS",
        "video_urls": "$ANIMATION_REFERENCE_VIDEO_URLS",
        "size": "$VIDEO_RATIO",
        "resolution": "$VIDEO_RESOLUTION",
        "duration": "$DURATION",
        "generate_audio": True,
    }
    result["video_async_enabled"] = True
    task_path = str(result.get("video_task_id_path") or "").strip()
    result["video_task_id_path"] = (
        "data.*.task_id" if task_path in {"", "id"} else task_path
    )
    result["video_poll_endpoint"] = str(
        result.get("video_poll_endpoint") or "/v1/tasks/$TASK_ID"
    ).strip()
    status_path = str(result.get("video_status_path") or "").strip()
    result["video_status_path"] = (
        "data.status" if status_path in {"", "status"} else status_path
    )
    progress_path = str(result.get("video_progress_path") or "").strip()
    result["video_progress_path"] = (
        "data.progress" if progress_path in {"", "progress"} else progress_path
    )
    result_path = str(result.get("video_poll_response_url_path") or "").strip()
    result["video_poll_response_url_path"] = (
        "data.result.videos.*.url.*"
        if result_path in {"", "data.*.url"}
        else result_path
    )
    result["video_output_extension"] = ".mp4"
    result["video_success_values"] = ["completed", "succeeded", "success"]
    result["video_failure_values"] = [
        "failed",
        "cancelled",
        "canceled",
        "error",
    ]
    result["supports_seedance_animation"] = True
    result["seedance_unavailable_reason"] = ""
    return result


def apply_provider_preset(settings: dict, provider: Optional[str] = None) -> dict:
    """Apply a known provider preset while preserving credentials and user model choice."""

    original = deepcopy(settings or {})
    selected = str(provider or original.get("provider") or "auto").strip().lower()
    # v0.3.2 mislabeled APIMART as "API易" and stored an unrelated API root.
    # Keep the user's credential while replacing that obsolete contract with
    # APIMART's official upload + asynchronous image-generation API.
    legacy_apiyi = selected == "apiyi"
    if legacy_apiyi:
        selected = "apimart"
    # GRSAI is retired from the product surface in V2.0.2. Preserve any old
    # endpoint/key as a custom profile instead of silently continuing to run a
    # hidden built-in provider or deleting the user's saved values.
    if selected == "grsai":
        selected = "custom"
    if selected == "auto":
        selected = provider_from_url(str(original.get("base_url") or ""))
    if selected not in PROVIDER_PRESETS:
        result = deepcopy(original)
        result["provider"] = selected or "custom"
        result["base_url"] = normalized_base_url(str(result.get("base_url") or ""))
        # Migrate retired JuAIHub Banana aliases to the live official model
        # IDs so stale settings never reappear in the model selector.
        if "api.juaihub.cn" in str(result.get("base_url") or "").lower():
            model = str(result.get("model") or "").strip().lower()
            if model in JUAIHUB_MODEL_ALIASES:
                result["model"] = JUAIHUB_MODEL_ALIASES[model]
            available = result.get("available_models")
            if isinstance(available, list):
                migrated = []
                for item in available:
                    value = str(item or "").strip()
                    value = JUAIHUB_MODEL_ALIASES.get(value.lower(), value)
                    if value and value not in migrated:
                        migrated.append(value)
                result["available_models"] = migrated
        return result

    result = deepcopy(original)
    result.update(deepcopy(PROVIDER_PRESETS[selected]))
    for key in (
        "api_key",
        "api_key_env",
        "timeout_seconds",
        "capture_max_edge",
        "history_limit",
    ):
        if key in original:
            result[key] = deepcopy(original[key])
    same_provider = (
        not legacy_apiyi
        and str(original.get("provider") or "").strip().lower() == selected
    )
    if same_provider:
        # Built-in providers are adapters, not arbitrary JSON templates. Keep
        # credentials, selected models and an official regional host, while
        # always upgrading request/poll paths and payloads to this build's
        # tested contract. This is what repairs old profiles automatically.
        original_base = str(original.get("base_url") or "").strip()
        if original_base and provider_from_url(original_base) == selected:
            result["base_url"] = normalized_base_url(original_base)
        image_model = str(original.get("model") or "").strip()
        if (
            image_model
            and not is_seedance_model(image_model)
            and is_compatible_image_model(selected, image_model)
        ):
            if selected == "atlascloud" and image_model == "bytedance/seedream-v4":
                image_model = "bytedance/seedream-v4/edit"
            result["model"] = image_model
        if isinstance(original.get("available_models"), list):
            result["available_models"] = deepcopy(original["available_models"])

        if selected in {"volcengine", "aliyun_bailian"}:
            # These built-in entries are deliberately image-only. Never let an
            # older saved V2 animation contract revive them after an upgrade.
            result["video_endpoint"] = ""
            result["video_model"] = ""
            result["seedance_models"] = []
            result["supports_seedance_animation"] = False
            result["seedance_unavailable_reason"] = seedance_unavailable_message(result)
            return result

        stored_seedance = original.get("seedance_models")
        discovered_seedance = [
            str(item or "").strip()
            for item in stored_seedance or []
            if is_compatible_seedance_model(selected, str(item or ""))
        ] if isinstance(stored_seedance, list) else []
        # Once a connection check has stored an account-level result, it must
        # win over the platform's general adapter capability. In particular,
        # an image-only key must not regain an enabled animation tab merely
        # because settings were loaded again.
        has_saved_capability = "supports_seedance_animation" in original
        if has_saved_capability:
            result["supports_seedance_animation"] = bool(
                original.get("supports_seedance_animation")
            )
            # A successful profile from an older adapter revision should gain
            # newly supported models automatically after an upgrade. Preserve
            # the user's selected 2.0 model, while making 2.5 available without
            # asking a designer to delete and reconnect the same API Key.
            try:
                previous_revision = int(original.get("contract_revision") or 0)
                current_revision = int(result.get("contract_revision") or 0)
            except (TypeError, ValueError):
                previous_revision, current_revision = 0, 0
            if (
                bool(original.get("supports_seedance_animation"))
                and previous_revision < current_revision
            ):
                for model in result.get("seedance_models") or []:
                    value = str(model or "").strip()
                    if value and value not in discovered_seedance:
                        discovered_seedance.append(value)
            result["seedance_models"] = discovered_seedance
        known_seedance = list(result.get("seedance_models") or [])
        if bool(result.get("supports_seedance_animation")) and known_seedance:
            old_video_model = str(original.get("video_model") or "").strip()
            if old_video_model in known_seedance:
                result["video_model"] = old_video_model
            elif result.get("video_model") not in known_seedance:
                result["video_model"] = known_seedance[0]
        elif has_saved_capability or not bool(result.get("supports_seedance_animation")):
            result["video_model"] = ""
            result["seedance_models"] = []
            result["supports_seedance_animation"] = False
            result["seedance_unavailable_reason"] = str(
                original.get("seedance_unavailable_reason")
                or seedance_unavailable_message(result)
            )
    return result
