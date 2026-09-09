"""Native Cinema 4D dialog for simple and advanced API configuration."""

from __future__ import annotations

import json
import math
import threading
from copy import deepcopy
from typing import Optional

import c4d

from .api import ImageApiClient
from .constants import (
    DISCOVERY_EVENT_ID,
    PRODUCT_WINDOW_TITLE,
    is_seedance_model,
)
from .providers import (
    PROVIDER_LABELS,
    apply_provider_preset,
    configure_custom_seedance_contract,
    is_compatible_image_model,
    is_compatible_seedance_model,
    provider_from_url,
    seedance_capability,
    seedance_unavailable_message,
)
from .storage import merge_settings


class ProviderButtonArea(c4d.gui.GeUserArea):
    """Version-stable provider button whose full surface carries its state."""

    def __init__(self, owner, command_id: int, label: str):
        super().__init__()
        self.owner = owner
        self.command_id = command_id
        self.label = label
        self.state = ""
        self.selected = False
        self.enabled = True
        self.pressed = False

    def GetMinSize(self) -> tuple[int, int]:
        return 252, 36

    def _colors(self):
        if self.state == "success":
            return c4d.Vector(0.07, 0.32, 0.68), c4d.Vector(1.0, 1.0, 1.0)
        if self.state == "error":
            return c4d.Vector(0.58, 0.12, 0.15), c4d.Vector(1.0, 1.0, 1.0)
        if self.state == "testing":
            return c4d.Vector(0.38, 0.31, 0.12), c4d.Vector(1.0, 0.94, 0.75)
        if self.selected:
            return c4d.Vector(0.27, 0.27, 0.27), c4d.Vector(0.94, 0.94, 0.94)
        return c4d.Vector(0.22, 0.22, 0.22), c4d.Vector(0.84, 0.84, 0.84)

    def _draw_pill(self, color, width: int, height: int) -> None:
        left, top, right, bottom = 2, 2, max(2, width - 3), max(2, height - 3)
        radius = max(1.0, min(10.0, (bottom - top + 1) / 2.0))
        top_center, bottom_center = top + radius, bottom - radius
        self.DrawSetPen(color)
        for row in range(top, bottom + 1):
            if row < top_center:
                distance = top_center - row
            elif row > bottom_center:
                distance = row - bottom_center
            else:
                distance = 0.0
            inset = int(
                math.ceil(radius - math.sqrt(max(0.0, radius * radius - distance * distance)))
            )
            self.DrawLine(left + inset, row, max(left + inset, right - inset), row)

    def DrawMsg(self, x1, y1, x2, y2, message) -> None:
        width, height = self.GetWidth(), self.GetHeight()
        fill, text_color = self._colors()
        if not self.enabled:
            fill = c4d.Vector(fill.x * 0.62, fill.y * 0.62, fill.z * 0.62)
        if self.pressed:
            fill = c4d.Vector(fill.x * 0.82, fill.y * 0.82, fill.z * 0.82)
        self.OffScreenOn()
        try:
            raw = self.GetColorRGB(c4d.COLOR_BG)
            background = c4d.Vector(raw["r"] / 255.0, raw["g"] / 255.0, raw["b"] / 255.0)
        except (AttributeError, KeyError, TypeError, ValueError):
            background = c4d.Vector(0.12, 0.12, 0.12)
        self.DrawSetPen(background)
        self.DrawRectangle(0, 0, max(0, width - 1), max(0, height - 1))
        self._draw_pill(fill, width, height)
        self.DrawSetTextCol(text_color, fill)
        self.DrawSetFont(c4d.FONT_DEFAULT)
        label_width = self.DrawGetTextWidth(self.label)
        self.DrawText(
            self.label,
            max(5, (width - label_width) // 2),
            max(0, (height - self.DrawGetFontHeight()) // 2 - 1),
        )

    def Message(self, message: c4d.BaseContainer, result: c4d.BaseContainer) -> bool:
        if message.GetId() == getattr(c4d, "BFM_GETCURSORINFO", -1) and self.enabled:
            result.SetId(c4d.BFM_GETCURSORINFO)
            result.SetInt32(c4d.RESULT_CURSOR, c4d.MOUSE_POINT_HAND)
            result.SetString(c4d.RESULT_BUBBLEHELP, self.label)
            return True
        return super().Message(message, result)

    def InputEvent(self, message: c4d.BaseContainer) -> bool:
        if (
            not self.enabled
            or message.GetInt32(c4d.BFM_INPUT_DEVICE) != c4d.BFM_INPUT_MOUSE
            or message.GetInt32(c4d.BFM_INPUT_CHANNEL) != c4d.BFM_INPUT_MOUSELEFT
        ):
            return False
        self.pressed = True
        self.Redraw()
        self.SetTimer(90)
        self.owner.Command(self.command_id, c4d.BaseContainer())
        return True

    def Timer(self, message: c4d.BaseContainer) -> None:
        self.pressed = False
        self.SetTimer(0)
        self.Redraw()

    def set_state(self, label: str, state: str, selected: bool) -> None:
        self.label = str(label)
        self.state = str(state or "")
        self.selected = bool(selected)
        self.Redraw()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.Redraw()


class Id:
    PROFILE = 3101
    BASE_URL = 3102
    ENDPOINT = 3103
    MODEL = 3104
    AUTH = 3105
    API_KEY = 3106
    API_KEY_ENV = 3107
    API_KEY_HEADER = 3108
    TIMEOUT = 3109
    HEADERS = 3110
    TEMPLATE = 3111
    URL_PATH = 3112
    B64_PATH = 3113
    ASYNC = 3114
    TASK_PATH = 3115
    POLL_ENDPOINT = 3116
    STATUS_PATH = 3117
    POLL_URL_PATH = 3118
    POLL_B64_PATH = 3119
    DISCOVER = 3120
    SAVE = 3121
    CANCEL = 3122
    ADVANCED_TOGGLE = 3123
    ADVANCED_GROUP = 3124
    CONNECTION_STATUS = 3125
    MODEL_LIST = 3126
    MODELS_ENDPOINT = 3127
    MODELS_PATH = 3128
    PROVIDER = 3129
    PROVIDER_ATLAS = 3131
    PROVIDER_VOLCENGINE = 3132
    PROVIDER_ALIYUN = 3134
    PROVIDER_APIMART = 3136
    PROVIDER_CUSTOM = 3137
    PROVIDER_TITLE = 3138
    PROVIDER_HINT = 3139
    VIDEO_ENDPOINT = 3140
    VIDEO_MODEL = 3141
    VIDEO_TEMPLATE = 3142
    VIDEO_URL_PATH = 3143
    VIDEO_B64_PATH = 3144
    VIDEO_ASYNC = 3145
    VIDEO_TASK_PATH = 3146
    VIDEO_POLL_ENDPOINT = 3147
    VIDEO_STATUS_PATH = 3148
    VIDEO_POLL_URL_PATH = 3149
    VIDEO_POLL_B64_PATH = 3150


class ModelDiscoveryThread(c4d.threading.C4DThread):
    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings
        self.models: list[str] = []
        self.error = ""
        self.done = False
        self._lock = threading.Lock()

    def snapshot(self) -> tuple[list[str], str, bool]:
        with self._lock:
            return list(self.models), self.error, self.done

    def Main(self) -> None:
        try:
            self.models = ImageApiClient(self.settings).list_models()
        except Exception as exc:  # surfaced on C4D's main thread
            self.error = str(exc)
        finally:
            with self._lock:
                self.done = True
            c4d.SpecialEventAdd(DISCOVERY_EVENT_ID)


class ApiSettingsDialog(c4d.gui.GeDialog):
    FIELD_WIDTH = 330
    PROVIDER_RAIL_WIDTH = 188
    PROVIDER_BUTTON_WIDTH = 174

    AUTH_VALUES = {0: "none", 1: "bearer", 2: "header"}
    PROVIDER_BUTTONS = (
        (Id.PROVIDER_CUSTOM, "custom", "挚热 API（JuAIHub）"),
    )
    PROVIDER_HINTS = {
        "custom": "挚热 API：自动读取当前可用模型；本插件仅保留图像渲染接口。",
    }

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = merge_settings(settings)
        self.saved = False
        self.advanced_visible = False
        self.discovery: Optional[ModelDiscoveryThread] = None
        self.pending_settings: Optional[dict] = None
        self.discovery_provider = ""
        self.provider_states: dict[str, str] = {}
        self.model_values: list[str] = []
        self.provider_areas = {
            button_id: ProviderButtonArea(self, button_id, label)
            for button_id, _provider, label in self.PROVIDER_BUTTONS
        }
        valid_providers = {provider for _button_id, provider, _label in self.PROVIDER_BUTTONS}
        self.selected_provider = str(self.settings.get("provider") or "auto")
        if self.selected_provider == "auto":
            self.selected_provider = provider_from_url(str(self.settings.get("base_url") or ""))
        if self.selected_provider == "auto" or self.selected_provider not in valid_providers:
            self.selected_provider = "custom"
        self.provider_profiles: dict[str, dict] = {}
        saved_profiles = self.settings.get("provider_profiles")
        if isinstance(saved_profiles, dict):
            for provider, profile in saved_profiles.items():
                target_provider = "apimart" if provider == "apiyi" else provider
                if target_provider in {item[1] for item in self.PROVIDER_BUTTONS} and isinstance(profile, dict):
                    migrated_profile = merge_settings(profile) if provider == "apiyi" else profile
                    self.provider_profiles[target_provider] = self._profile_payload(
                        migrated_profile, target_provider
                    )
        # Migrate the old flat settings into the active platform once. New
        # profiles start without credentials, so switching platforms can never
        # copy one provider's API Key into another provider.
        if self.selected_provider not in self.provider_profiles:
            self.provider_profiles[self.selected_provider] = self._profile_payload(
                self.settings, self.selected_provider
            )
        # Older builds allowed several blue states. Normalize old data to one active
        # provider: the last explicitly connected platform wins, otherwise the
        # flattened provider currently used by generation wins.
        saved_states = self.settings.get("provider_connection_states")
        if isinstance(saved_states, dict):
            for provider, state in saved_states.items():
                target_provider = "apimart" if provider == "apiyi" else provider
                if target_provider in valid_providers and state == "error":
                    self.provider_states[target_provider] = "error"
        last_connected = str(self.settings.get("last_connected_provider") or "")
        if last_connected == "apiyi":
            last_connected = "apimart"
        self.active_provider = last_connected if last_connected in valid_providers else ""
        connected_providers = self.settings.get("connected_providers")
        normalized_connected = [
            "apimart" if provider == "apiyi" else provider
            for provider in connected_providers or []
        ] if isinstance(connected_providers, list) else []
        if not self.active_provider and self.selected_provider in normalized_connected:
            self.active_provider = self.selected_provider
        if not self.active_provider and self.selected_provider in valid_providers:
            has_legacy_connection = bool(
                isinstance(saved_states, dict)
                and saved_states.get(self.selected_provider) == "success"
            )
            has_configured_key = bool(
                str(self.settings.get("base_url") or "").strip()
                and (
                    str(self.settings.get("api_key") or "").strip()
                    or str(self.settings.get("api_key_env") or "").strip()
                )
            )
            if has_legacy_connection or has_configured_key:
                self.active_provider = self.selected_provider
        if self.active_provider:
            self.provider_states[self.active_provider] = "success"

    def _label(self, text: str, width: int = 116) -> None:
        self.AddStaticText(0, c4d.BFH_LEFT | c4d.BFV_CENTER, width, 0, text)

    def _edit(self, item_id: int, password: bool = False) -> None:
        flags = getattr(c4d, "EDITTEXT_PASSWORD", 0) if password else 0
        self.AddEditText(item_id, c4d.BFH_SCALEFIT, self.FIELD_WIDTH, 0, flags)

    def CreateLayout(self) -> bool:
        self.SetTitle("{} · API 连接".format(PRODUCT_WINDOW_TITLE))
        self.GroupBegin(3000, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 2, 1)
        self.GroupBorderSpace(10, 10, 10, 10)
        self.GroupSpace(10, 6)

        self.GroupBegin(
            2990,
            c4d.BFH_LEFT | c4d.BFV_SCALEFIT,
            1,
            0,
            "",
            0,
            self.PROVIDER_RAIL_WIDTH,
            0,
        )
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 8, 6, 8)
        self.GroupSpace(4, 4)
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 20, "API 平台")
        for button_id, _provider, label in self.PROVIDER_BUTTONS:
            self.AddUserArea(
                button_id,
                c4d.BFH_SCALEFIT,
                self.PROVIDER_BUTTON_WIDTH,
                36,
            )
            self.AttachUserArea(self.provider_areas[button_id], button_id)
        self.AddSeparatorH(1, c4d.BFH_SCALEFIT)
        self.AddStaticText(
            0,
            c4d.BFH_SCALEFIT,
            0,
            0,
            "蓝色表示当前使用平台。",
        )
        self.GroupEnd()

        self.GroupBegin(2991, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.GroupBorderNoTitle(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(10, 8, 10, 8)
        self.GroupSpace(6, 6)
        self.AddStaticText(Id.PROVIDER_TITLE, c4d.BFH_SCALEFIT, 0, 20, "平台连接")
        self.AddStaticText(Id.PROVIDER_HINT, c4d.BFH_SCALEFIT, 0, 0, "")

        self.GroupBegin(3001, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 2, 0)
        self.GroupSpace(8, 6)
        self._label("API Key", 104)
        self._edit(Id.API_KEY, password=True)
        self._label("API 地址", 104)
        self._edit(Id.BASE_URL)
        self._label("生成 Endpoint", 104)
        self._edit(Id.ENDPOINT)
        self._label("模型名称", 104)
        self._edit(Id.MODEL)
        self._label("已读取模型", 104)
        self.AddComboBox(Id.MODEL_LIST, c4d.BFH_SCALEFIT, self.FIELD_WIDTH, 0)
        self.GroupEnd()

        self.GroupBegin(3002, c4d.BFH_RIGHT, 1, 1)
        self.AddButton(Id.ADVANCED_TOGGLE, c4d.BFH_RIGHT, 132, 28, "展开专业参数  ▾")
        self.GroupEnd()
        self.AddStaticText(Id.CONNECTION_STATUS, c4d.BFH_SCALEFIT, 0, 0, "当前未连接平台")

        self.GroupBegin(Id.ADVANCED_GROUP, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0)
        self.ScrollGroupBegin(
            3003,
            c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
            c4d.SCROLLGROUP_VERT | c4d.SCROLLGROUP_BORDERIN,
            450,
            300,
        )
        self.GroupBegin(3004, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 2, 0)
        self.GroupBorderSpace(8, 8, 8, 8)
        self.GroupSpace(8, 6)

        for item_id, label in (
            (Id.PROFILE, "配置名称"),
            (Id.MODELS_ENDPOINT, "模型 Endpoint"),
            (Id.MODELS_PATH, "模型列表路径"),
        ):
            self._label(label)
            self._edit(item_id)

        self._label("鉴权方式")
        self.AddComboBox(Id.AUTH, c4d.BFH_SCALEFIT, 390, 0)
        self.AddChild(Id.AUTH, 0, "无")
        self.AddChild(Id.AUTH, 1, "Bearer Token")
        self.AddChild(Id.AUTH, 2, "自定义 Header")
        self._label("Key 环境变量")
        self._edit(Id.API_KEY_ENV)
        self._label("Key Header 名称")
        self._edit(Id.API_KEY_HEADER)
        self._label("请求超时（秒）")
        self.AddEditNumberArrows(Id.TIMEOUT, c4d.BFH_LEFT, 120, 0)

        self._label("Headers（JSON）")
        self.AddMultiLineEditText(
            Id.HEADERS,
            c4d.BFH_SCALEFIT,
            self.FIELD_WIDTH,
            78,
            getattr(c4d, "DR_MULTILINE_WORDWRAP", 0),
        )
        self._label("请求模板（JSON）")
        self.AddMultiLineEditText(
            Id.TEMPLATE,
            c4d.BFH_SCALEFIT,
            self.FIELD_WIDTH,
            170,
            getattr(c4d, "DR_MULTILINE_WORDWRAP", 0),
        )
        for item_id, label in (
            (Id.URL_PATH, "结果 URL 路径"),
            (Id.B64_PATH, "结果 Base64 路径"),
        ):
            self._label(label)
            self._edit(item_id)
        self._label("异步任务")
        self.AddCheckbox(Id.ASYNC, c4d.BFH_LEFT, 0, 0, "提交后轮询")
        for item_id, label in (
            (Id.TASK_PATH, "任务 ID 路径"),
            (Id.POLL_ENDPOINT, "轮询 Endpoint"),
            (Id.STATUS_PATH, "状态路径"),
            (Id.POLL_URL_PATH, "轮询 URL 路径"),
            (Id.POLL_B64_PATH, "轮询 Base64 路径"),
        ):
            self._label(label)
            self._edit(item_id)

        self._label("Seedance 动画 Endpoint")
        self._edit(Id.VIDEO_ENDPOINT)
        self._label("Seedance 模型（2.0 / 2.5 / 全能参考）")
        self._edit(Id.VIDEO_MODEL)
        self._label("Seedance 全能参考请求模板（JSON）")
        self.AddMultiLineEditText(
            Id.VIDEO_TEMPLATE,
            c4d.BFH_SCALEFIT,
            self.FIELD_WIDTH,
            150,
            getattr(c4d, "DR_MULTILINE_WORDWRAP", 0),
        )
        self._label("动画异步任务")
        self.AddCheckbox(Id.VIDEO_ASYNC, c4d.BFH_LEFT, 0, 0, "提交后轮询")
        for item_id, label in (
            (Id.VIDEO_URL_PATH, "动画结果 URL 路径"),
            (Id.VIDEO_B64_PATH, "动画 Base64 路径"),
            (Id.VIDEO_TASK_PATH, "动画任务 ID 路径"),
            (Id.VIDEO_POLL_ENDPOINT, "动画轮询 Endpoint"),
            (Id.VIDEO_STATUS_PATH, "动画状态路径"),
            (Id.VIDEO_POLL_URL_PATH, "动画轮询 URL 路径"),
            (Id.VIDEO_POLL_B64_PATH, "动画轮询 Base64 路径"),
        ):
            self._label(label)
            self._edit(item_id)
        self.GroupEnd()
        self.GroupEnd()
        # Static text does not word-wrap in C4D. Keep every line shorter than
        # the settings form so this help text cannot force a multi-monitor
        # minimum dialog width while the professional section is collapsed.
        for line in (
            "模板变量：$MODEL $PROMPT $WIDTH $HEIGHT $SIZE $COUNT",
            "$ASPECT $QUALITY $SCENE_IMAGE_DATA_URL",
            "$ALL_INPUT_IMAGE_URLS $MULTIMODAL_CONTENT $DASHSCOPE_CONTENT",
            "$SCENE_MEDIA_DATA_URL $SCENE_MEDIA_URL $ALL_INPUT_MEDIA_URLS",
            "$VIDEO_MODEL $ANIMATION_FRAMES_DATA_URLS $ANIMATION_FRAME_URLS",
            "$ANIMATION_REFERENCE_IMAGE_URLS $ANIMATION_REFERENCE_VIDEO_URLS",
            "$DURATION $VIDEO_RESOLUTION $VIDEO_RATIO",
        ):
            self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, line)
        self.GroupEnd()

        self.GroupBegin(3005, c4d.BFH_RIGHT, 3, 1)
        self.AddButton(Id.CANCEL, c4d.BFH_LEFT, 72, 28, "关闭")
        self.AddButton(Id.SAVE, c4d.BFH_LEFT, 92, 28, "保存设置")
        self.AddButton(Id.DISCOVER, c4d.BFH_LEFT, 112, 28, "连接并使用")
        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self) -> bool:
        values = self._profile_for_provider(self.selected_provider)
        self._write_values(values)
        available = values.get("available_models")
        self.model_values = (
            [
                str(model)
                for model in available
                if is_compatible_image_model(self.selected_provider, str(model))
            ]
            if isinstance(available, list)
            else []
        )
        current_model = str(values.get("model") or "").strip()
        if (
            current_model
            and is_compatible_image_model(self.selected_provider, current_model)
            and current_model not in self.model_values
        ):
            self.model_values.insert(0, current_model)
        if self.model_values:
            for child_id, model in enumerate(self.model_values, start=1):
                self.AddChild(Id.MODEL_LIST, child_id, model)
            current = str(values.get("model") or "")
            selected = self.model_values.index(current) + 1 if current in self.model_values else 1
            self.SetInt32(Id.MODEL_LIST, selected)
        else:
            self.AddChild(Id.MODEL_LIST, 0, "连接并使用后自动读取")
            self.SetInt32(Id.MODEL_LIST, 0)
        self.HideElement(Id.ADVANCED_GROUP, True)
        return True

    def _write_values(self, values: dict) -> None:
        mapping = {
            Id.PROFILE: values.get("profile_name", ""),
            Id.BASE_URL: values.get("base_url", ""),
            Id.ENDPOINT: values.get("endpoint", ""),
            Id.MODEL: values.get("model", ""),
            Id.API_KEY: values.get("api_key", ""),
            Id.API_KEY_ENV: values.get("api_key_env", ""),
            Id.API_KEY_HEADER: values.get("api_key_header", ""),
            Id.MODELS_ENDPOINT: values.get("models_endpoint", "/v1/models"),
            Id.MODELS_PATH: values.get("models_response_path", "data.*.id"),
            Id.URL_PATH: values.get("response_url_path", ""),
            Id.B64_PATH: values.get("response_base64_path", ""),
            Id.TASK_PATH: values.get("task_id_path", ""),
            Id.POLL_ENDPOINT: values.get("poll_endpoint", ""),
            Id.STATUS_PATH: values.get("status_path", ""),
            Id.POLL_URL_PATH: values.get("poll_response_url_path", ""),
            Id.POLL_B64_PATH: values.get("poll_response_base64_path", ""),
            Id.VIDEO_ENDPOINT: values.get("video_endpoint", ""),
            Id.VIDEO_MODEL: values.get("video_model", ""),
            Id.VIDEO_URL_PATH: values.get("video_response_url_path", ""),
            Id.VIDEO_B64_PATH: values.get("video_response_base64_path", ""),
            Id.VIDEO_TASK_PATH: values.get("video_task_id_path", ""),
            Id.VIDEO_POLL_ENDPOINT: values.get("video_poll_endpoint", ""),
            Id.VIDEO_STATUS_PATH: values.get("video_status_path", ""),
            Id.VIDEO_POLL_URL_PATH: values.get("video_poll_response_url_path", ""),
            Id.VIDEO_POLL_B64_PATH: values.get("video_poll_response_base64_path", ""),
        }
        for item_id, value in mapping.items():
            self.SetString(item_id, str(value))
        provider = str(values.get("provider") or "custom")
        if provider == "auto":
            provider = provider_from_url(str(values.get("base_url") or ""))
        if provider == "auto":
            provider = "custom"
        self.selected_provider = provider if provider in PROVIDER_LABELS else "custom"
        self._refresh_provider_navigation()
        auth_reverse = {value: key for key, value in self.AUTH_VALUES.items()}
        self.SetInt32(Id.AUTH, auth_reverse.get(values.get("auth_type"), 1))
        self.SetInt32(Id.TIMEOUT, int(values.get("timeout_seconds") or 180), 5, 3600, 1)
        self.SetBool(Id.ASYNC, bool(values.get("async_enabled")))
        self.SetBool(Id.VIDEO_ASYNC, bool(values.get("video_async_enabled")))
        self.SetString(Id.HEADERS, json.dumps(values.get("headers") or {}, ensure_ascii=False, indent=2))
        self.SetString(
            Id.TEMPLATE,
            json.dumps(values.get("request_template") or {}, ensure_ascii=False, indent=2),
        )
        self.SetString(
            Id.VIDEO_TEMPLATE,
            json.dumps(
                values.get("video_request_template") or {},
                ensure_ascii=False,
                indent=2,
            ),
        )

    @staticmethod
    def _profile_payload(values: dict, provider: str) -> dict:
        """Return one provider's isolated configuration without profile metadata."""

        result = deepcopy(values or {})
        result["provider"] = provider
        for metadata_key in (
            "provider_profiles",
            "provider_connection_states",
            "connected_providers",
            "last_connected_provider",
        ):
            result.pop(metadata_key, None)
        return result

    def _profile_for_provider(self, provider: str) -> dict:
        existing = self.provider_profiles.get(provider)
        if isinstance(existing, dict):
            return self._profile_payload(merge_settings(existing), provider)
        # Deliberately seed a blank credential. apply_provider_preset preserves
        # credentials by design, so a clean seed is essential here.
        fresh = merge_settings({"provider": provider, "api_key": "", "api_key_env": ""})
        return self._profile_payload(fresh, provider)

    def _read(self, validate: bool = True) -> dict:
        selected_provider = self.selected_provider or "custom"
        base_profile = self._profile_for_provider(selected_provider)
        try:
            headers = json.loads(self.GetString(Id.HEADERS) or "{}")
            template = json.loads(self.GetString(Id.TEMPLATE) or "{}")
            video_template = json.loads(self.GetString(Id.VIDEO_TEMPLATE) or "{}")
        except ValueError as exc:
            if validate:
                raise ValueError("Headers 或请求模板不是有效 JSON：{}".format(exc))
            headers = deepcopy(base_profile.get("headers") or {})
            template = deepcopy(base_profile.get("request_template") or {})
            video_template = deepcopy(base_profile.get("video_request_template") or {})
        if (
            not isinstance(headers, dict)
            or not isinstance(template, dict)
            or not isinstance(video_template, dict)
        ):
            if validate:
                raise ValueError("Headers、图片请求模板和动画请求模板的顶层必须是 JSON 对象。")
            headers = deepcopy(base_profile.get("headers") or {})
            template = deepcopy(base_profile.get("request_template") or {})
            video_template = deepcopy(base_profile.get("video_request_template") or {})
        if validate and not self.GetString(Id.BASE_URL).strip():
            raise ValueError("请填写官方或中转站提供的服务地址。")
        if validate and not self.GetString(Id.ENDPOINT).strip():
            raise ValueError("生成 Endpoint 不能为空。")
        video_model = self.GetString(Id.VIDEO_MODEL).strip()
        if (
            validate
            and video_model
            and not is_seedance_model(video_model)
            and not (
                selected_provider == "volcengine"
                and video_model.lower().startswith("ep-")
            )
        ):
            raise ValueError(
                "动画模块仅支持 Seedance 系列模型；模型 ID 中必须包含 seedance。"
            )

        result = deepcopy(base_profile)
        result.update(
            {
                "provider": selected_provider,
                "profile_name": self.GetString(Id.PROFILE).strip() or "自定义图像 API",
                "base_url": self.GetString(Id.BASE_URL).strip(),
                "endpoint": self.GetString(Id.ENDPOINT).strip(),
                "model": self.GetString(Id.MODEL).strip(),
                "models_endpoint": self.GetString(Id.MODELS_ENDPOINT).strip() or "/v1/models",
                "models_response_path": self.GetString(Id.MODELS_PATH).strip() or "data.*.id",
                "auth_type": self.AUTH_VALUES.get(self.GetInt32(Id.AUTH), "bearer"),
                "api_key": self.GetString(Id.API_KEY),
                "api_key_env": self.GetString(Id.API_KEY_ENV).strip(),
                "api_key_header": self.GetString(Id.API_KEY_HEADER).strip() or "X-API-Key",
                "timeout_seconds": self.GetInt32(Id.TIMEOUT),
                "headers": headers,
                "request_template": template,
                "response_url_path": self.GetString(Id.URL_PATH).strip(),
                "response_base64_path": self.GetString(Id.B64_PATH).strip(),
                "async_enabled": self.GetBool(Id.ASYNC),
                "task_id_path": self.GetString(Id.TASK_PATH).strip(),
                "poll_endpoint": self.GetString(Id.POLL_ENDPOINT).strip(),
                "status_path": self.GetString(Id.STATUS_PATH).strip(),
                "poll_response_url_path": self.GetString(Id.POLL_URL_PATH).strip(),
                "poll_response_base64_path": self.GetString(Id.POLL_B64_PATH).strip(),
                "video_endpoint": self.GetString(Id.VIDEO_ENDPOINT).strip(),
                "video_model": video_model,
                "video_request_template": video_template,
                "video_response_url_path": self.GetString(Id.VIDEO_URL_PATH).strip(),
                "video_response_base64_path": self.GetString(Id.VIDEO_B64_PATH).strip(),
                "video_async_enabled": self.GetBool(Id.VIDEO_ASYNC),
                "video_task_id_path": self.GetString(Id.VIDEO_TASK_PATH).strip(),
                "video_poll_endpoint": self.GetString(Id.VIDEO_POLL_ENDPOINT).strip(),
                "video_status_path": self.GetString(Id.VIDEO_STATUS_PATH).strip(),
                "video_poll_response_url_path": self.GetString(Id.VIDEO_POLL_URL_PATH).strip(),
                "video_poll_response_base64_path": self.GetString(Id.VIDEO_POLL_B64_PATH).strip(),
            }
        )
        # Built-in providers use versioned adapters. Reapplying the preset here
        # upgrades stale endpoints/templates before the connection check while
        # preserving the credential, selected model and official regional host.
        if selected_provider != "custom":
            return apply_provider_preset(result, selected_provider)
        return result

    def _store_current_profile(self) -> dict:
        values = self._read(validate=False)
        self.provider_profiles[self.selected_provider] = self._profile_payload(
            values, self.selected_provider
        )
        return values

    def _refresh_provider_navigation(self) -> None:
        for button_id, provider, label in self.PROVIDER_BUTTONS:
            state = self.provider_states.get(provider, "")
            if state == "success":
                prefix = "✓"
            elif state == "error":
                prefix = "×"
            elif state == "testing":
                prefix = "…"
            elif provider == self.selected_provider:
                prefix = "●"
            else:
                prefix = " "
            self._apply_provider_button_color(
                button_id,
                provider,
                state,
                "{}  {}".format(prefix, label),
            )
        label = PROVIDER_LABELS.get(self.selected_provider, "自定义接口")
        self.SetString(Id.PROVIDER_TITLE, "{} · 连接设置".format(label))
        self.SetString(
            Id.PROVIDER_HINT,
            self.PROVIDER_HINTS.get(self.selected_provider, self.PROVIDER_HINTS["custom"]),
        )

    def _apply_provider_button_color(
        self, button_id: int, provider: str, state: str, label: str
    ) -> None:
        """Blue means uniquely active; selection alone remains neutral gray."""
        area = self.provider_areas.get(button_id)
        if area is not None:
            area.set_state(
                label,
                state,
                provider == self.selected_provider,
            )

    def _set_provider_state(self, provider: str, state: str) -> None:
        if provider and state == "success":
            for other in list(self.provider_states):
                if self.provider_states.get(other) == "success":
                    self.provider_states.pop(other, None)
            self.active_provider = provider
            self.provider_states[provider] = "success"
        elif provider and state == "error":
            if self.active_provider == provider:
                self.active_provider = ""
            self.provider_states[provider] = "error"
        elif provider and state:
            self.provider_states[provider] = state
        elif provider:
            self.provider_states.pop(provider, None)
        self._refresh_provider_navigation()
        self.LayoutChanged(2990)

    def _enable_provider_navigation(self, enabled: bool) -> None:
        for button_id, _provider, _label in self.PROVIDER_BUTTONS:
            area = self.provider_areas.get(button_id)
            if area is not None:
                area.set_enabled(enabled)

    def _apply_selected_provider(self, selected: str) -> None:
        self._store_current_profile()
        self.selected_provider = selected if selected in {
            provider for _button_id, provider, _label in self.PROVIDER_BUTTONS
        } else "custom"
        values = self._profile_for_provider(self.selected_provider)
        self._write_values(values)
        label = PROVIDER_LABELS.get(self.selected_provider, "自定义接口")
        if self.active_provider == self.selected_provider:
            message = "{} 当前已连接并正在使用。".format(label)
        elif self.provider_states.get(self.selected_provider) == "error":
            message = "{} 上次连接失败；可修改后重新连接。".format(label)
        else:
            message = "已选择 {}；可只保存配置，或连接后设为当前平台。".format(label)
        self.SetString(Id.CONNECTION_STATUS, message)

    def _settings_with_state(self, values: dict) -> dict:
        result = deepcopy(values)
        visible_states = {
            provider: state
            for provider, state in self.provider_states.items()
            if state == "error"
        }
        if self.active_provider:
            visible_states[self.active_provider] = "success"
        result["provider_profiles"] = deepcopy(self.provider_profiles)
        result["provider_connection_states"] = visible_states
        result["connected_providers"] = (
            [self.active_provider] if self.active_provider else []
        )
        result["last_connected_provider"] = self.active_provider
        return result

    def _save_settings_only(self) -> None:
        try:
            values = self._read()
        except ValueError as exc:
            self.SetString(Id.CONNECTION_STATUS, "保存失败：{}".format(str(exc)[:160]))
            c4d.gui.MessageDialog(str(exc))
            return
        self.provider_profiles[self.selected_provider] = self._profile_payload(
            values, self.selected_provider
        )
        if self.active_provider:
            base_values = deepcopy(self.settings)
        else:
            base_values = merge_settings(
                {"provider": "custom", "base_url": "", "api_key": "", "api_key_env": ""}
            )
        self.settings = merge_settings(self._settings_with_state(base_values))
        self.saved = True
        selected_label = PROVIDER_LABELS.get(self.selected_provider, "当前平台")
        if self.active_provider:
            active_label = PROVIDER_LABELS.get(self.active_provider, "当前平台")
            message = "已保存 {} 配置；当前仍使用 {}，未发起连接。".format(
                selected_label, active_label
            )
        else:
            message = "已保存 {} 配置；尚未启用任何平台。".format(selected_label)
        self.SetString(Id.CONNECTION_STATUS, message)

    def _start_discovery(self) -> None:
        if self.discovery is not None:
            return
        try:
            values = self._read()
        except ValueError as exc:
            self._set_provider_state(self.selected_provider, "error")
            self.SetString(Id.CONNECTION_STATUS, "连接失败：{}".format(str(exc)[:160]))
            c4d.gui.MessageDialog(str(exc))
            return
        self.provider_profiles[self.selected_provider] = self._profile_payload(
            values, self.selected_provider
        )
        self._write_values(values)
        self.pending_settings = deepcopy(values)
        self.discovery_provider = self.selected_provider
        self._set_provider_state(self.discovery_provider, "testing")
        self.discovery = ModelDiscoveryThread(values)
        self._enable_provider_navigation(False)
        self.Enable(Id.DISCOVER, False)
        self.Enable(Id.SAVE, False)
        self.SetString(Id.DISCOVER, "连接中…")
        provider_label = PROVIDER_LABELS.get(self.selected_provider, "当前平台")
        self.SetString(
            Id.CONNECTION_STATUS,
            "正在验证 {} 的当前 API Key 与接口，请稍候…".format(provider_label),
        )
        self.discovery.Start()

    def _finish_discovery(self) -> None:
        worker = self.discovery
        if worker is None:
            return
        models, error, done = worker.snapshot()
        if not done:
            return
        self.discovery = None
        provider = self.discovery_provider or self.selected_provider
        self.discovery_provider = ""
        self._enable_provider_navigation(True)
        self.Enable(Id.DISCOVER, True)
        self.Enable(Id.SAVE, True)
        self.SetString(Id.DISCOVER, "连接并使用")
        if error:
            self._set_provider_state(provider, "error")
            self.SetString(Id.CONNECTION_STATUS, "连接失败：{}。请修改后重试，窗口不会关闭。".format(error[:160]))
            # Persist the red failure state while leaving any different active
            # provider untouched. Retrying the active provider and failing
            # intentionally clears it, so generation cannot silently continue
            # with credentials which just failed validation.
            self.settings = merge_settings(self._settings_with_state(self.settings))
            self.saved = True
            self.pending_settings = None
            return
        image_models = [
            model for model in models if is_compatible_image_model(provider, model)
        ]
        current_image_model = self.GetString(Id.MODEL).strip()
        if (
            provider == "custom"
            and current_image_model
            and is_compatible_image_model(provider, current_image_model)
            and current_image_model not in image_models
        ):
            image_models.insert(0, current_image_model)
        if not image_models:
            self.model_values = []
            self.FreeChildren(Id.MODEL_LIST)
            self._set_provider_state(provider, "error")
            label = PROVIDER_LABELS.get(provider, "当前平台")
            self.SetString(
                Id.CONNECTION_STATUS,
                "{} 已连接，但当前 API Key 没有读取到本版可用的图像编辑模型；"
                "请检查模型权限或更换平台后重试。".format(label),
            )
            self.settings = merge_settings(self._settings_with_state(self.settings))
            self.saved = True
            self.pending_settings = None
            return
        self._set_provider_state(provider, "success")
        self.model_values = image_models
        seedance_models = [
            model
            for model in models
            if is_compatible_seedance_model(provider, model)
        ]
        current_video_model = self.GetString(Id.VIDEO_MODEL).strip()
        self.FreeChildren(Id.MODEL_LIST)
        for child_id, model in enumerate(image_models, start=1):
            self.AddChild(Id.MODEL_LIST, child_id, model)
        current = self.GetString(Id.MODEL).strip()
        selected = image_models.index(current) + 1 if current in image_models else 1
        self.SetInt32(Id.MODEL_LIST, selected)
        if image_models and current not in image_models:
            self.SetString(Id.MODEL, image_models[0])
        if seedance_models and current_video_model not in seedance_models:
            self.SetString(Id.VIDEO_MODEL, seedance_models[0])
        label = PROVIDER_LABELS.get(provider, "当前平台")
        if self.pending_settings is not None:
            saved_values = deepcopy(self.pending_settings)
            saved_values["model"] = self.GetString(Id.MODEL).strip()
            saved_values["video_model"] = self.GetString(Id.VIDEO_MODEL).strip()
            saved_values["available_models"] = list(models)
            selected_video_model = self.GetString(Id.VIDEO_MODEL).strip()
            saved_values["seedance_models"] = list(seedance_models)
            if provider == "custom":
                saved_values = configure_custom_seedance_contract(saved_values, models)
                selected_video_model = str(saved_values.get("video_model") or "")
                seedance_models = list(saved_values.get("seedance_models") or [])
                self.SetString(Id.VIDEO_ENDPOINT, saved_values.get("video_endpoint") or "")
                self.SetString(Id.VIDEO_MODEL, selected_video_model)
                self.SetString(
                    Id.VIDEO_TEMPLATE,
                    json.dumps(
                        saved_values.get("video_request_template") or {},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                self.SetBool(
                    Id.VIDEO_ASYNC,
                    bool(saved_values.get("video_async_enabled")),
                )
                self.SetString(
                    Id.VIDEO_TASK_PATH,
                    saved_values.get("video_task_id_path") or "",
                )
                self.SetString(
                    Id.VIDEO_POLL_ENDPOINT,
                    saved_values.get("video_poll_endpoint") or "",
                )
                self.SetString(
                    Id.VIDEO_STATUS_PATH,
                    saved_values.get("video_status_path") or "",
                )
                self.SetString(
                    Id.VIDEO_POLL_URL_PATH,
                    saved_values.get("video_poll_response_url_path") or "",
                )
            has_seedance_contract = bool(
                str(saved_values.get("video_endpoint") or "").strip()
                and selected_video_model in seedance_models
            )
            saved_values["supports_seedance_animation"] = has_seedance_contract
            if has_seedance_contract:
                saved_values["seedance_unavailable_reason"] = ""
            else:
                saved_values["seedance_unavailable_reason"] = seedance_unavailable_message(
                    saved_values
                )
            self.provider_profiles[provider] = self._profile_payload(saved_values, provider)
            self.settings = merge_settings(self._settings_with_state(saved_values))
            self.saved = True
            self.pending_settings = None
            animation_ready, _reason = seedance_capability(saved_values)
            self.SetString(
                Id.CONNECTION_STATUS,
                (
                    "{} 已连接：生图与 Seedance 动画均可用；已读取 {} 个模型，其中 {} 个 Seedance。"
                    if animation_ready
                    else "{} 已连接：AI 图像渲染可用；Seedance 动画暂不可用。"
                ).format(label, len(models), len(seedance_models)),
            )

    def Command(self, item_id: int, message: c4d.BaseContainer) -> bool:
        if item_id == Id.CANCEL:
            self.Close()
        elif item_id == Id.ADVANCED_TOGGLE:
            self.advanced_visible = not self.advanced_visible
            self.HideElement(Id.ADVANCED_GROUP, not self.advanced_visible)
            self.SetString(
                Id.ADVANCED_TOGGLE,
                "收起专业参数  ▴" if self.advanced_visible else "展开专业参数  ▾",
            )
            self.LayoutChanged(3000)
        elif item_id in {button_id for button_id, _provider, _label in self.PROVIDER_BUTTONS}:
            selected = next(
                provider for button_id, provider, _label in self.PROVIDER_BUTTONS if button_id == item_id
            )
            self._apply_selected_provider(selected)
        elif item_id == Id.DISCOVER:
            self._start_discovery()
        elif item_id == Id.MODEL_LIST:
            index = self.GetInt32(Id.MODEL_LIST) - 1
            if 0 <= index < len(self.model_values):
                self.SetString(Id.MODEL, self.model_values[index])
        elif item_id == Id.SAVE:
            self._save_settings_only()
        return True

    def CoreMessage(self, message_id: int, message: c4d.BaseContainer) -> bool:
        if message_id == DISCOVERY_EVENT_ID:
            self._finish_discovery()
            return True
        return super().CoreMessage(message_id, message)

    def DestroyWindow(self) -> None:
        if self.discovery is not None:
            self.discovery.End()
