"""剧本与小说正文的统一文本统计。"""

from __future__ import annotations

import re
from typing import Any, Iterable


_MARKUP_TAG_RE = re.compile(
    r"<\s*(?P<closing>/?)\s*(?P<tag>[A-Za-z][A-Za-z0-9:_-]*)\b[^>]*>"
)
_ALLOWED_MARKUP = {
    "arc": {"conception", "choice", "opt"},
    "novel": {"conception"},
}


def _count_letters_and_numbers(text: Any) -> int:
    """与编辑器字数口径一致：只统计 Unicode 字母与数字。"""
    return sum(1 for char in str(text or "") if char.isalnum())


def _iter_dialogue_text(nodes: Iterable[dict[str, Any]]) -> Iterable[str]:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = node.get("txt")
        if text:
            yield str(text)
        for option in node.get("opt") or []:
            if not isinstance(option, dict):
                continue
            option_text = option.get("optn")
            if option_text:
                yield str(option_text)
            yield from _iter_dialogue_text(option.get("dia") or [])


def find_story_markup_violations(content: str, export_format: str) -> list[str]:
    """检查正文中的标签白名单、闭合关系和嵌套顺序。"""
    normalized_format = "novel" if str(export_format or "").strip().lower() == "novel" else "arc"
    allowed = _ALLOWED_MARKUP[normalized_format]
    stack: list[str] = []
    violations: list[str] = []

    def add_once(reason: str) -> None:
        if reason not in violations:
            violations.append(reason)

    for match in _MARKUP_TAG_RE.finditer(str(content or "")):
        tag = match.group("tag").lower()
        closing = bool(match.group("closing"))
        if tag not in allowed:
            add_once(f"markup_tag_forbidden:{tag}")
            continue
        if closing:
            if not stack or stack[-1] != tag:
                add_once(f"markup_tag_unbalanced:{tag}")
            else:
                stack.pop()
            continue
        stack.append(tag)

    for tag in stack:
        add_once(f"markup_tag_unbalanced:{tag}")
    return violations


def validate_story_document(
    content: str,
    export_format: str,
    *,
    require_conception: bool = False,
    require_parseable_arc: bool = True,
) -> list[str]:
    """验证待落盘正文，返回确定性拒收原因；不修改输入内容。"""
    normalized_format = "novel" if str(export_format or "").strip().lower() == "novel" else "arc"
    reasons = find_story_markup_violations(content, normalized_format)
    conception_matches = re.findall(
        r"<conception(?:\s[^>]*)?>([\s\S]*?)</conception\s*>",
        str(content or ""),
        flags=re.IGNORECASE,
    )
    if require_conception and len(conception_matches) != 1:
        reasons.append(f"conception_count_{len(conception_matches)}")

    if normalized_format == "arc":
        scenes = []
        try:
            from story.arc_parser import parse_arc

            scenes = parse_arc(str(content or ""))
        except Exception as exc:
            if require_parseable_arc:
                reasons.append(f"arc_parse_error:{type(exc).__name__}")
        if require_parseable_arc and not scenes:
            reasons.append("arc_parse_empty")
        if count_story_body_chars(str(content or ""), "arc") <= 0:
            reasons.append("body_empty")
    else:
        from story.novel_parser import parse_novel_document

        document = parse_novel_document(str(content or ""))
        if count_story_body_chars(str(content or ""), "novel") <= 0 or not str(document.get("body") or "").strip():
            reasons.append("body_empty")

    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def count_story_body_chars(content: str, export_format: str) -> int:
    """统计最终可见正文字符，排除 ARC/Markdown 标记和构思块。"""
    text = str(content or "")
    if str(export_format or "").strip().lower() == "novel":
        from story.novel_parser import clean_novel_visible_text

        text = clean_novel_visible_text(text)
        return _count_letters_and_numbers(text)

    try:
        from story.arc_parser import parse_arc

        parts: list[str] = []
        parsed_scenes = parse_arc(text)
        for scene in parsed_scenes:
            intro = scene.get("intro")
            if intro:
                parts.append(str(intro))
            parts.extend(_iter_dialogue_text(scene.get("dia") or []))
        if parsed_scenes:
            return _count_letters_and_numbers("\n".join(parts))
    except Exception:
        pass

    fallback = re.sub(r"<conception>[\s\S]*?</conception>", "", text)
    fallback = re.sub(r"^\s*(?:#|@\w+|\[[^\]]+\]|</?\w+[^>]*>)\s*", "", fallback, flags=re.MULTILINE)
    return _count_letters_and_numbers(fallback)
