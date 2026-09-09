"""Vision prompt generation through the JuAIHub Responses endpoint."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional


PROMPT_MODELS = ("gpt-5.6-sol",)
PROMPT_SKILLS = {
    "": {
        "label": "不使用技能",
        "rules": "不额外套用技能规则，仅遵循用户填写的图片职责与画面提示。",
    },
}

# User-authored Markdown skills live beside the plug-in and are loaded at
# startup.  Keeping the files external makes them editable without changing
# the Python code or embedding long prompt rules in the UI module.
_EXTERNAL_SKILL_FILES = (
    ("user-custom-stainless-steel", "不锈钢质感优化.md"),
    ("user-custom-wood-product-v20", "木质产品真实摄影优化V20.md"),
)


def _load_external_skills() -> None:
    skill_dir = Path(__file__).resolve().parent.parent / "prompt_skills"
    for skill_id, filename in _EXTERNAL_SKILL_FILES:
        source = skill_dir / filename
        try:
            raw = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        body = raw.strip()
        label = source.stem
        if body.startswith("---"):
            parts = body.split("---", 2)
            if len(parts) == 3:
                metadata, body = parts[1], parts[2].strip()
                match = re.search(r"(?m)^name:\s*(.+?)\s*$", metadata)
                if match:
                    label = match.group(1).strip()
                if label.startswith("user-custom-"):
                    label = label[len("user-custom-") :]
        if body:
            PROMPT_SKILLS[skill_id] = {"label": label, "rules": body}


_load_external_skills()
DEFAULT_PROMPT_BASE_URL = "https://api.juaihub.cn"
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 160 * 1024 * 1024

PROMPT_INSTRUCTIONS = """你是商业产品视觉提示词导演和可组合技能执行器。请根据按顺序提供的 C4D 白膜、Z 深度图和参考图，生成一段可直接提交给图生图模型的中文提示词。

输出规则：
1. 只输出最终提示词正文，不要标题、解释、分析过程、Markdown 或代码块。
2. 白膜和 Z 深度只用于你理解产品结构、镜头和空间，不要在最终提示词中提到“白膜”“灰模”“深度图”“近白远黑”等内部控制信息。插件提交生图时会另行注入这些约束，避免重复。
3. 聚焦最终画面要呈现的环境、材质与色彩、光线、氛围、商业摄影质感和用户的创意目标。不要凭空增删产品部件，不要杜撰看不清的品牌文字。
4. 每张普通参考图都必须在最终提示词中使用一次对应占位符 [[图1]]、[[图2]]……，并在占位符附近准确说明借鉴职责。参考图编号按提供顺序，不得把白膜或深度图计入编号。
5. 用户草稿会明确写出“图片职责”和“画面提示”。它们是最高优先级的创作指令：先逐条理解并保留职责，再把画面提示扩写成完整、连贯、可执行的生图提示词；技能规则只能补全，不得改写、削弱或替换用户明确要求。
6. 若用户草稿为空，则根据图像自行形成完整、自然、可执行的产品视觉方案。
7. 不要重复书写插件内置的摄像机锁定、构图锁定、产品形体锁定和 Z 深度定义。"""


class PromptApiError(RuntimeError):
    pass


class PromptGenerationCancelled(PromptApiError):
    pass


def _host(value: str) -> str:
    return (urllib.parse.urlsplit(str(value or "")).hostname or "").lower().rstrip(".")


def _matching_profile(settings: dict, host: str) -> Optional[dict]:
    profiles = settings.get("provider_profiles") or {}
    if not isinstance(profiles, dict):
        return None
    for profile in profiles.values():
        if isinstance(profile, dict) and _host(profile.get("base_url")) == host:
            return profile
    return None


def resolved_prompt_configuration(settings: dict) -> tuple[str, str]:
    """Resolve the JuAIHub endpoint and key without persisting another secret."""

    explicit = str(settings.get("prompt_base_url") or "").strip()
    candidates = [explicit, str(settings.get("base_url") or "").strip()]
    profiles = settings.get("provider_profiles") or {}
    if isinstance(profiles, dict):
        candidates.extend(
            str(item.get("base_url") or "").strip()
            for item in profiles.values()
            if isinstance(item, dict)
        )
    base_url = next(
        (value for value in candidates if _host(value) == "api.juaihub.cn"),
        explicit or DEFAULT_PROMPT_BASE_URL,
    ).rstrip("/")

    key = str(settings.get("prompt_api_key") or "").strip()
    active_url = str(settings.get("base_url") or "").strip()
    if not key and _host(active_url) == _host(base_url):
        key = str(settings.get("api_key") or "").strip()
    profile = _matching_profile(settings, _host(base_url))
    if not key and profile is not None:
        key = str(profile.get("api_key") or "").strip()

    env_names = [
        str(settings.get("prompt_api_key_env") or "").strip(),
        str(settings.get("api_key_env") or "").strip(),
    ]
    if profile is not None:
        env_names.append(str(profile.get("api_key_env") or "").strip())
    for env_name in env_names:
        if not key and env_name:
            key = os.environ.get(env_name, "").strip()

    if not key:
        raise PromptApiError("未找到可用于提示词模型的 JuAIHub API Key，请先完成 API 设置。")
    return base_url, key


def _responses_url(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    return value + "/responses" if value.endswith("/v1") else value + "/v1/responses"


def _image_data_url(path: str) -> tuple[str, int]:
    source = Path(str(path or ""))
    if not source.is_file():
        raise PromptApiError("提示词参考图不存在：{}".format(source.name or path))
    if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise PromptApiError("提示词模型不支持此图片格式：{}".format(source.name))
    size = source.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise PromptApiError("提示词参考图超过 50MB：{}".format(source.name))
    mime = mimetypes.guess_type(source.name)[0] or "image/png"
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    return "data:{};base64,{}".format(mime, encoded), size


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or fallback)
        if error:
            return str(error)
        return str(payload.get("message") or payload.get("msg") or fallback)
    return fallback


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    if parts:
        return "\n".join(parts).strip()
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
    return ""


def _clean_prompt(value: str, reference_count: int) -> str:
    prompt = str(value or "").strip()
    fence = re.fullmatch(r"```(?:\w+)?\s*(.*?)\s*```", prompt, re.DOTALL)
    if fence:
        prompt = fence.group(1).strip()
    prompt = re.sub(r"^\s*(?:最终)?提示词\s*[:：]\s*", "", prompt, count=1)
    prompt = re.sub(r"【(?:参考)?图\s*(\d+)】", r"[[图\1]]", prompt)
    if len(prompt) >= 2 and (prompt[0], prompt[-1]) in {
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
    }:
        prompt = prompt[1:-1].strip()
    for index in range(1, reference_count + 1):
        token = "[[图{}]]".format(index)
        if token not in prompt:
            prompt = "{}；参考 {} 的可迁移视觉特征".format(prompt.rstrip("。；"), token)
    return prompt.strip(" \r\n")


def skill_options() -> list[tuple[str, str]]:
    return [(key, value["label"]) for key, value in PROMPT_SKILLS.items()]


class PromptGenerator:
    def __init__(self, settings: dict):
        self.settings = dict(settings or {})

    def generate(
        self,
        model: str,
        scene_image: str,
        depth_image: str,
        references: list[str],
        draft_prompt: str = "",
        skill_id: str = "",
        progress: Optional[Callable[[float, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> str:
        model_id = str(model or "").strip()
        if model_id not in PROMPT_MODELS:
            raise PromptApiError("不支持的提示词模型：{}".format(model_id))
        if should_cancel is not None and should_cancel():
            raise PromptGenerationCancelled("提示词生成已取消")

        base_url, api_key = resolved_prompt_configuration(self.settings)
        skill = PROMPT_SKILLS.get(skill_id) or PROMPT_SKILLS[""]
        if progress is not None:
            progress(0.08, "正在整理白膜、深度与参考图…")

        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "请生成最终中文生图提示词。\n"
                    "【用户图片职责与画面提示（最高优先级）】\n{}\n"
                    "【当前技能】{}：{}\n"
                    "技能只用于补全用户没有写明的部分，不得覆盖用户职责。\n"
                    "普通参考图共 {} 张；最终提示词必须保留从 [[图1]] 开始的原编号。"
                ).format(
                    str(draft_prompt or "").strip() or "（用户未填写，按图像分析）",
                    skill["label"],
                    skill["rules"],
                    len(references),
                ),
            }
        ]
        total_bytes = 0

        def add_image(label: str, path: str, detail: str) -> None:
            nonlocal total_bytes
            data_url, size = _image_data_url(path)
            total_bytes += size
            if total_bytes > MAX_TOTAL_IMAGE_BYTES:
                raise PromptApiError("提示词模型的图片总大小超过 160MB，请减少或压缩参考图。")
            content.append({"type": "input_text", "text": label})
            content.append(
                {"type": "input_image", "image_url": data_url, "detail": detail}
            )

        if scene_image:
            add_image(
                "以下是 C4D 白膜/场景图：仅用于理解真实产品结构、摄像机角度、主体位置和画面占比。",
                scene_image,
                "original",
            )
        if depth_image:
            add_image(
                "以下是与白膜逐像素同画幅的 Z 深度图：仅用于理解轮廓、遮挡和前后空间层次，不作为材质或颜色参考。",
                depth_image,
                "original",
            )
        for index, path in enumerate(references, start=1):
            add_image(
                "以下是普通参考图{}；它在最终提示词中的固定占位符是 [[图{}]]。请判断并写明其可迁移职责。".format(
                    index, index
                ),
                path,
                "high",
            )
        if len(content) == 1:
            raise PromptApiError("请先渲染加载白膜，或上传至少一张参考图。")
        if should_cancel is not None and should_cancel():
            raise PromptGenerationCancelled("提示词生成已取消")

        body = json.dumps(
            {
                "model": model_id,
                "instructions": PROMPT_INSTRUCTIONS,
                "input": [{"role": "user", "content": content}],
                "max_output_tokens": 1800,
                "reasoning": {"effort": "low"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            _responses_url(base_url),
            data=body,
            headers={
                "Authorization": "Bearer " + api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "ZhireAI-C4D/Prompt-Generator",
            },
            method="POST",
        )
        timeout = max(60.0, float(self.settings.get("timeout_seconds") or 180))
        if progress is not None:
            progress(0.35, "AI 正在理解产品与参考图…")
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=ssl.create_default_context()
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                payload = None
            raise PromptApiError(
                "提示词接口 HTTP {}：{}".format(
                    exc.code, _error_message(payload, str(exc.reason or "请求失败"))
                )
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise PromptApiError("提示词接口连接失败：{}".format(exc)) from exc
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise PromptApiError("提示词接口返回了无法解析的数据。") from exc

        if should_cancel is not None and should_cancel():
            raise PromptGenerationCancelled("提示词生成已取消")
        prompt = _clean_prompt(_response_text(payload), len(references))
        if not prompt:
            raise PromptApiError("提示词模型没有返回可用文本。")
        if progress is not None:
            progress(1.0, "提示词生成完成")
        return prompt
