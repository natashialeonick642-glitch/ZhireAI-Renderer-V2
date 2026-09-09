"""Prompt composition helpers for interleaved text and reference images."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, Optional


TOKEN_PATTERN = re.compile(r"\[\[图(\d+)\]\]")
PROMPT_SOFT_BREAK = "\u200b"
PROMPT_SOFT_WRAP = PROMPT_SOFT_BREAK + "\n"


def canonical_editor_display(value: str) -> str:
    """Normalize host line endings while retaining visual wrap markers."""

    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def plain_editor_prompt(value: str) -> str:
    """Remove visual-only wrapping before saving or submitting a prompt."""

    return (
        canonical_editor_display(value)
        .replace(PROMPT_SOFT_WRAP, "")
        .replace(PROMPT_SOFT_BREAK, "")
    )


def _editor_character_units(character: str) -> int:
    """Approximate a proportional UI font with two units for wide glyphs."""

    codepoint = ord(character)
    if (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3000 <= codepoint <= 0x303F
        or 0xFF00 <= codepoint <= 0xFFEF
    ):
        return 2
    if character == "\t":
        return 4
    return 1


def breakable_editor_prompt(value: str) -> str:
    """Add native soft-wrap opportunities without changing prompt whitespace.

    Cinema 4D's multiline editor treats an uninterrupted Chinese paragraph as
    one word, so its WORDWRAP flag alone can expand the containing layout.
    Zero-width spaces give the native control legal break positions which it
    can reflow whenever its width changes. ``plain_editor_prompt`` removes
    every marker before saving or submitting, while real user newlines remain.
    """

    text = plain_editor_prompt(value)
    if not text:
        return ""
    output = []
    ascii_run = 0
    for index, character in enumerate(text):
        output.append(character)
        if character == "\n" or character.isspace():
            ascii_run = 0
            continue
        next_character = text[index + 1] if index + 1 < len(text) else ""
        if not next_character or next_character == "\n" or next_character.isspace():
            ascii_run = 0
            continue
        if _editor_character_units(character) == 2:
            output.append(PROMPT_SOFT_BREAK)
            ascii_run = 0
            continue
        ascii_run += 1
        # Ordinary Latin text already wraps at spaces. This only protects the
        # layout from unusually long URLs, identifiers and unspaced tokens.
        if ascii_run >= 24:
            output.append(PROMPT_SOFT_BREAK)
            ascii_run = 0
    return "".join(output)


def wrapped_editor_layout(
    value: str, max_columns: int = 42
) -> tuple[str, tuple[int, ...]]:
    """Return display text and the logical offsets of visual-only wraps."""

    text = plain_editor_prompt(value)
    if not text:
        return "", ()
    limit = max(12, int(max_columns))
    output = []
    automatic_breaks = []
    line_units = 0
    for offset, character in enumerate(text):
        if character == "\n":
            output.append(character)
            line_units = 0
            continue
        units = _editor_character_units(character)
        if line_units and line_units + units > limit:
            output.append(PROMPT_SOFT_WRAP)
            automatic_breaks.append(offset)
            line_units = 0
        output.append(character)
        line_units += units
    return "".join(output), tuple(automatic_breaks)


def wrapped_editor_prompt(value: str, max_columns: int = 42) -> str:
    """Hard-wrap long CJK/Latin text without changing its logical value."""

    return wrapped_editor_layout(value, max_columns)[0]


def equivalent_editor_display(left: str, right: str) -> bool:
    """Compare editor text while tolerating C4D removing zero-width markers."""

    def markerless(value: str) -> str:
        return canonical_editor_display(value).replace(PROMPT_SOFT_BREAK, "")

    return markerless(left) == markerless(right)


def tracked_plain_offset_from_display(
    displayed: str,
    display_offset: int,
    logical_text: str,
    automatic_breaks: Iterable[int],
) -> int:
    """Map a host cursor offset while ignoring known visual-only newlines."""

    raw = str(displayed or "")
    raw_limit = max(0, min(len(raw), int(display_offset)))
    source = canonical_editor_display(raw).replace(PROMPT_SOFT_BREAK, "")
    limit = len(
        canonical_editor_display(raw[:raw_limit]).replace(PROMPT_SOFT_BREAK, "")
    )
    logical_source = canonical_editor_display(logical_text).replace(
        PROMPT_SOFT_BREAK, ""
    )
    break_offsets = {max(0, int(offset)) for offset in automatic_breaks}
    logical_offset = 0
    for index, character in enumerate(source):
        if index >= limit:
            break
        is_automatic_break = (
            character == "\n"
            and logical_offset in break_offsets
            and (
                logical_offset >= len(logical_source)
                or logical_source[logical_offset] != "\n"
            )
        )
        if is_automatic_break:
            continue
        if logical_offset < len(logical_source):
            logical_offset += 1
    return min(logical_offset, len(logical_source))


def tracked_display_offset_from_plain(
    displayed: str,
    plain_offset: int,
    logical_text: str,
    automatic_breaks: Iterable[int],
) -> int:
    """Map a logical offset to the host display, including a wrap at that offset."""

    source = str(displayed or "")
    logical_source = canonical_editor_display(logical_text).replace(
        PROMPT_SOFT_BREAK, ""
    )
    target = max(0, min(len(logical_source), int(plain_offset)))
    break_offsets = {max(0, int(offset)) for offset in automatic_breaks}
    logical_offset = 0
    index = 0
    while index < len(source):
        if source[index] == PROMPT_SOFT_BREAK:
            index += 1
            continue
        newline_size = 0
        if source.startswith("\r\n", index):
            newline_size = 2
        elif source[index] in "\r\n":
            newline_size = 1
        is_automatic_break = (
            newline_size > 0
            and logical_offset in break_offsets
            and (
                logical_offset >= len(logical_source)
                or logical_source[logical_offset] != "\n"
            )
        )
        if is_automatic_break:
            # A caret between two logical characters belongs at the beginning
            # of the wrapped line, not at the end of the preceding line.
            index += newline_size
            continue
        if logical_offset >= target:
            break
        if newline_size:
            index += newline_size
        else:
            index += 1
        logical_offset += 1
    return index


def editor_prompt_after_change(
    previous_logical: str,
    previous_displayed: str,
    current_displayed: str,
    automatic_breaks: Iterable[int],
) -> tuple[str, int]:
    """Apply one editor change without adopting old visual wraps as newlines."""

    logical = canonical_editor_display(previous_logical).replace(
        PROMPT_SOFT_BREAK, ""
    )
    before = canonical_editor_display(previous_displayed).replace(
        PROMPT_SOFT_BREAK, ""
    )
    after = canonical_editor_display(current_displayed).replace(
        PROMPT_SOFT_BREAK, ""
    )
    if before == after:
        return logical, len(logical)

    prefix = 0
    shared_limit = min(len(before), len(after))
    while prefix < shared_limit and before[prefix] == after[prefix]:
        prefix += 1

    suffix = 0
    while (
        suffix < len(before) - prefix
        and suffix < len(after) - prefix
        and before[len(before) - suffix - 1] == after[len(after) - suffix - 1]
    ):
        suffix += 1

    old_end_display = len(before) - suffix
    new_end_display = len(after) - suffix
    old_start = tracked_plain_offset_from_display(
        before, prefix, logical, automatic_breaks
    )
    old_end = tracked_plain_offset_from_display(
        before, old_end_display, logical, automatic_breaks
    )
    inserted = after[prefix:new_end_display]
    updated = logical[:old_start] + inserted + logical[old_end:]
    return updated, old_start + len(inserted)


def edited_caret_offset(previous: str, current: str) -> int:
    """Infer the caret after one text edit from the previous/current values."""

    before = plain_editor_prompt(previous)
    after = plain_editor_prompt(current)
    prefix = 0
    shared_limit = min(len(before), len(after))
    while prefix < shared_limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(before) - prefix
        and suffix < len(after) - prefix
        and before[len(before) - suffix - 1] == after[len(after) - suffix - 1]
    ):
        suffix += 1
    return len(after) - suffix


def plain_offset_from_display(displayed: str, display_offset: int) -> int:
    """Map C4D's absolute editor cursor offset to the logical prompt text."""

    source = str(displayed or "")
    limit = max(0, min(len(source), int(display_offset)))
    prefix = source[:limit]
    return len(plain_editor_prompt(prefix))


def display_offset_from_plain(displayed: str, plain_offset: int) -> int:
    """Map a logical prompt offset to C4D's absolute editor cursor offset."""

    target = max(0, int(plain_offset))
    logical = 0
    index = 0
    source = str(displayed or "")
    while index < len(source) and logical < target:
        if source[index] == PROMPT_SOFT_BREAK:
            index += 1
            if source.startswith("\r\n", index):
                index += 2
            elif index < len(source) and source[index] in "\r\n":
                index += 1
            continue
        if source.startswith("\r\n", index):
            logical += 1
            index += 2
            continue
        logical += 1
        index += 1
    # When the logical caret sits exactly on a visual wrap, position it at the
    # beginning of the next line. Otherwise continued typing happens before
    # the wrap and creates the short-line pattern seen in C4D 2023.
    if index < len(source) and source[index] == PROMPT_SOFT_BREAK:
        index += 1
        if source.startswith("\r\n", index):
            index += 2
        elif index < len(source) and source[index] in "\r\n":
            index += 1
    return index


def editor_cursor_position(displayed: str, plain_offset: int) -> tuple[int, int]:
    """Map a logical prompt offset to C4D's line/column cursor coordinates."""

    source = str(displayed or "")
    display_offset = display_offset_from_plain(source, plain_offset)
    prefix = source[:display_offset]
    # GeDialog.SetMultiLinePos uses one-based line numbers. This path is kept
    # only as a compatibility fallback when the absolute cursor messages are
    # unavailable in a host build.
    line = prefix.count("\n") + 1
    column = len(prefix.rsplit("\n", 1)[-1])
    return line, column


def inserted_mention_position(previous: str, current: str) -> int:
    """Return the position of an @ newly inserted anywhere in the prompt."""

    before = str(previous or "")
    after = str(current or "")
    if after.count("@") <= before.count("@"):
        return -1

    prefix = 0
    shared_limit = min(len(before), len(after))
    while prefix < shared_limit and before[prefix] == after[prefix]:
        prefix += 1

    suffix = 0
    before_remaining = len(before) - prefix
    after_remaining = len(after) - prefix
    while (
        suffix < before_remaining
        and suffix < after_remaining
        and before[len(before) - suffix - 1] == after[len(after) - suffix - 1]
    ):
        suffix += 1

    inserted_end = len(after) - suffix if suffix else len(after)
    inserted = after[prefix:inserted_end]
    relative = inserted.rfind("@")
    return prefix + relative if relative >= 0 else after.rfind("@")


def image_token(index: int) -> str:
    """Return the visible one-based token used in the prompt editor."""

    if index < 1:
        raise ValueError("Image token indices start at one.")
    return "[[图{}]]".format(index)


def referenced_indices(prompt: str) -> list[int]:
    """Return unique image indices in first-appearance order."""

    found: list[int] = []
    for match in TOKEN_PATTERN.finditer(prompt or ""):
        value = int(match.group(1))
        if value not in found:
            found.append(value)
    return found


def validate_tokens(prompt: str, reference_count: int) -> list[str]:
    """Return human-readable validation errors for invalid image tokens."""

    errors = []
    for index in referenced_indices(prompt):
        if index < 1 or index > reference_count:
            errors.append("提示词中的[[图{}]]没有对应的参考图。".format(index))
    return errors


def plain_prompt(prompt: str) -> str:
    """Remove image tokens while retaining readable spacing."""

    text = TOKEN_PATTERN.sub(" ", prompt or "")
    return re.sub(r"[ \t]+", " ", text).strip()


def composition_locked_prompt(
    prompt: str, enabled: bool, scene_context: Optional[dict] = None
) -> str:
    """Add explicit camera/composition constraints for scene-guided generation."""

    if not enabled:
        return prompt or ""
    context = scene_context or {}
    capture_size = context.get("capture_size") or []
    size_instruction = ""
    if isinstance(capture_size, (list, tuple)) and len(capture_size) >= 2:
        try:
            source_width = max(1, int(capture_size[0]))
            source_height = max(1, int(capture_size[1]))
            size_instruction = (
                " 输入视口为{0}×{1}像素（宽高比{2:.4f}），输出画布必须继承这一宽高关系；"
                "禁止裁切、拉伸、补画扩边或改变主体在画面中的占比。"
            ).format(source_width, source_height, source_width / float(source_height))
        except (TypeError, ValueError):
            size_instruction = ""
    instruction = (
        "【最高优先级：锁定第一张 C4D 场景图】这是严格的、在原图同一画布上的图生图编辑任务，不是重新构图。"
        "EDIT THE FIRST IMAGE IN PLACE. DO NOT RECOMPOSE, CROP, ZOOM, PAN OR RESCALE. "
        "第一张输入图是不可改动的构图与三维几何母体；必须保持摄像机位置、朝向、透视、焦距、"
        "主体数量、外轮廓、内部结构、画面坐标、遮挡关系和四周留白。"
        + size_instruction
    )
    final_constraint = (
        "【最终硬约束】若文字要求与第一张图冲突，以第一张图为准。主体包围框的中心、宽度、高度"
        "和占画比例必须与原图一致，允许误差不超过画布宽高的2%；不得移动、旋转、缩放、增删或"
        "重塑主体，不得改变镜头。只可在原主体表面修改材质、颜色和细节，并在原主体之外补充灯光"
        "与环境。网格、坐标轴、选中轮廓、操作手柄和 HUD 不得出现在结果中。"
        "FINAL CHECK: SAME CAMERA, SAME CANVAS, SAME SUBJECT BOUNDING BOX AND SAME MARGINS."
    )
    return "{}\n【用户视觉要求】{}\n{}".format(
        instruction,
        prompt or "",
        final_constraint,
    )


def build_multimodal_content(
    prompt: str,
    references: Iterable[str],
    to_data_url: Callable[[str], str],
    scene_image: str = "",
) -> list[dict]:
    """Build OpenAI-style interleaved content from ``[[图N]]`` tokens.

    The dedicated C4D scene is always inserted first and therefore needs no
    visible ``@`` token. Ordinary references are included only when their
    ``[[图N]]`` token appears in the prompt.
    """

    paths = [str(Path(item)) for item in references]
    content: list[dict] = []
    if scene_image:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": to_data_url(str(Path(scene_image)))},
            }
        )
    cursor = 0
    used: list[int] = []

    for match in TOKEN_PATTERN.finditer(prompt or ""):
        before = (prompt or "")[cursor : match.start()]
        if before:
            content.append({"type": "text", "text": before})
        index = int(match.group(1)) - 1
        if 0 <= index < len(paths):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": to_data_url(paths[index])},
                }
            )
            used.append(index)
        cursor = match.end()

    tail = (prompt or "")[cursor:]
    if tail:
        content.append({"type": "text", "text": tail})

    if not content and prompt:
        content.append({"type": "text", "text": prompt})

    return content


def animation_locked_prompt(
    prompt: str, enabled: bool, scene_context: Optional[dict] = None
) -> str:
    """Describe a C4D movie or sampled frames as one animation guide."""

    if not enabled:
        return prompt or ""
    context = scene_context or {}
    frame_range = context.get("animation_frame_range") or []
    range_text = ""
    if isinstance(frame_range, (list, tuple)) and len(frame_range) >= 2:
        range_text = " 采样范围为第{}至{}帧。".format(frame_range[0], frame_range[1])
    instruction = (
        "【C4D 动画与镜头锁定，最高优先级】主媒体是同一段 Cinema 4D 白模动画，"
        "可能是完整视频，也可能是按时间顺序采样的动画帧。必须保持相机、主体身份、"
        "几何比例、动作方向和运动连续性；"
        "用户文字只用于添加材质、灯光、环境和创作风格，不得改变既有镜头或动作逻辑。"
        + range_text
        + " 普通参考图只提供被点名的风格或语义，不得替换 C4D 动画本体。只允许按下面要求创作："
    )
    return "{}\n{}".format(instruction, prompt or "")
