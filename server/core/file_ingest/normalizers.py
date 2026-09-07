import re

from charset_normalizer import from_bytes

# 统一口径：本模块的 estimate_text_tokens 转调 chunking 的唯一入口，
# 禁止各模块自行 import 不同的估算函数（国家意志 e2e：跨口径比较误杀）。
from .chunking import estimate_text_tokens  # noqa: F401  (re-export，旧调用方兼容)


def normalize_text(text: str) -> str:
    normalized = (text or "").replace("\ufeff", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\xa0", " ").replace("\u3000", " ")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def decode_text_bytes(raw_bytes: bytes) -> tuple[str, dict[str, str]]:
    matches = from_bytes(
        raw_bytes,
        cp_isolation=["utf_8", "utf_8_sig", "gb18030", "big5", "utf_16", "utf_16_le", "utf_16_be"],
        cp_exclusion=["ascii"],
        enable_fallback=True,
    )
    best = matches.best()
    if best is None:
        text = raw_bytes.decode("utf-8", errors="ignore")
        return normalize_text(text), {"encoding": "utf-8", "encoding_confidence": "fallback"}
    return normalize_text(str(best)), {
        "encoding": str(best.encoding or "utf-8"),
        "encoding_confidence": f"{1 - float(best.chaos or 0):.4f}",
    }


def remove_repeated_page_edges(page_texts: list[str]) -> tuple[list[str], list[str]]:
    if len(page_texts) < 2:
        return page_texts, []

    first_lines: dict[str, int] = {}
    last_lines: dict[str, int] = {}
    for page in page_texts:
        lines = [line.strip() for line in page.split("\n") if line.strip()]
        if not lines:
            continue
        if 1 < len(lines[0]) <= 80:
            first_lines[lines[0]] = first_lines.get(lines[0], 0) + 1
        if 1 < len(lines[-1]) <= 80:
            last_lines[lines[-1]] = last_lines.get(lines[-1], 0) + 1

    repeated_headers = {line for line, count in first_lines.items() if count >= 2}
    repeated_footers = {line for line, count in last_lines.items() if count >= 2}
    cleaned_pages: list[str] = []
    removed: list[str] = []

    for page in page_texts:
        lines = [line.rstrip() for line in page.split("\n")]
        while lines and lines[0].strip() in repeated_headers:
            removed.append(lines.pop(0).strip())
        while lines and lines[-1].strip() in repeated_footers:
            removed.append(lines.pop().strip())
        cleaned_pages.append(normalize_text("\n".join(lines)))

    return cleaned_pages, sorted(set(item for item in removed if item))
