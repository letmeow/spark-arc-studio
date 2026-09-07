from dataclasses import dataclass
import re

try:
    from llm.agen_matchbox.estimate_tokens import estimate_tokens as _estimate_tokens
except ImportError:
    try:
        from server.llm.agen_matchbox.estimate_tokens import estimate_tokens as _estimate_tokens
    except ImportError:
        def _estimate_tokens(text, model=None):
            return len(text)


def estimate_text_tokens(text: str, model: str | None = None) -> int:
    """全仓库统一的 token 估算唯一入口。

    铁律：任何“切多大 / 读不读 / 预算够不够”的判断都走这里，禁止各模块
    自行 import 不同的估算函数或手写字符换算。口径是否真实（tokenizer
    已预热）还是回退，由 estimate_tokens 内部统一决定；调用方只传 model，
    不关心 exact/fallback，从而消灭“切 64K、读 100K”这类跨口径比较
    （国家意志 e2e 根因）。口径探针见 is_exact_counter_ready。
    """
    return _estimate_tokens(text or "", model=model)


def is_exact_counter_ready(model: str | None = None) -> bool:
    """统一口径探针的唯一出口：给定 model 的真实 tokenizer 是否就绪。"""
    try:
        from llm.agen_matchbox.estimate_tokens import is_exact_counter_ready as _ready
    except ImportError:
        try:
            from server.llm.agen_matchbox.estimate_tokens import is_exact_counter_ready as _ready
        except ImportError:
            return False
    return bool(_ready(model))


# 历史兼容：旧调用方直接 import 本模块的 estimate_tokens，现收敛到统一入口。
def estimate_tokens(text, model=None):
    return estimate_text_tokens(text, model=model)


@dataclass(slots=True)
class TokenChunk:
    text: str
    index: int
    total: int
    char_count: int
    estimated_tokens: int
    previous_tail: str = ""


class TokenTextSplitter:
    SENTENCE_ENDINGS = re.compile(r'[。！？.!?]["\'」』）\)]*')
    PARAGRAPH_BOUNDARY = re.compile(r'\n\s*\n+')
    HEADING_BOUNDARY = re.compile(
        r'(?m)^(?:\s*(?:第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节卷部篇回集]|chapter\s+\d+|prologue|epilogue|序章|终章|番外|楔子|后记)[^\n]*|\s{0,3}#{1,6}\s+[^\n]+)$',
        re.IGNORECASE,
    )
    TAIL_CHARS = 100

    def __init__(
        self,
        chunk_tokens: int = 30000,
        min_tokens: int = 1000,
        max_tokens: int = 120000,
        tail_merge_threshold_ratio: float = 0.2,
        tail_merge_cap_ratio: float = 1.15,
        estimate_model: str | None = None,
    ):
        """Token 驱动的文本分块器。

        尾部合并策略：当切出的最后一片 < ``chunk_tokens * tail_merge_threshold_ratio``
        且合并后实测仍 <= ``chunk_tokens`` 时，合并二者，避免产生“小尾巴”分片。
        合并的承诺是“合完仍是合法单窗”，不存在“允许超窗合并”的语义：
        ``tail_merge_cap_ratio`` 已废弃，仅为兼容旧调用签名保留，不再参与判断。

        默认 0.2 保持保守，适用于风格分析以及 low-context 模型。
        聊天附件场景可传 0.5，避免“64.1K 切成 64K + 0.1K”这类尴尬——
        但 64K + 0.1K 合完若实测超窗，仍会走最终钳制切开，不静默放行。
        """
        self.chunk_tokens = max(min_tokens, min(chunk_tokens, max_tokens))
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.tail_merge_threshold_ratio = max(0.0, min(float(tail_merge_threshold_ratio), 0.95))
        self.tail_merge_cap_ratio = max(1.0, float(tail_merge_cap_ratio))
        normalized_model = str(estimate_model).strip() if estimate_model is not None else ""
        self.estimate_model = normalized_model or None

    def estimate(self, text: str) -> int:
        return estimate_text_tokens(text, model=self.estimate_model)

    def split(self, text: str) -> list[TokenChunk]:
        normalized = self._normalize_text(text)
        if not normalized:
            return []

        # 估算口径必须全程一致：total 走 pack 路径（逐 unit 累加），不走整体
        # 一次性估算。否则整体估算与 pack 累加出现模型相关的系统性偏差时，
        # 会出现“total 说只有 1 片、pack 却装了 2 片 81K”的超窗单片
        # （见 longread 七堇年 e2e：qwen 口径整体 77K、pack 累加 136K）。
        units = self._build_units(normalized)
        if not units:
            return []
        raw_chunks = self._pack_units(units)
        return self._build_chunks(raw_chunks or [normalized])

    def split_with_info(self, text: str) -> tuple[list[TokenChunk], dict]:
        chunks = self.split(text)
        # total 取各片累加（pack 口径），不取整体一次性估算：两者在部分
        # tokenizer 下存在系统性偏差，累加口径才是各窗口真实成本之和。
        return chunks, {
            "total_chars": len(text or ""),
            "total_tokens_estimated": sum(
                chunk.estimated_tokens for chunk in chunks
            ) if chunks else 0,
            "chunk_count": len(chunks),
            "chunk_tokens_target": self.chunk_tokens,
            "chunks_info": [
                {
                    "index": chunk.index,
                    "chars": chunk.char_count,
                    "tokens_est": chunk.estimated_tokens,
                }
                for chunk in chunks
            ],
        }

    def _normalize_text(self, text: str) -> str:
        return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    def _build_units(self, text: str) -> list[str]:
        heading_parts = self._split_keep_markers(text, self.HEADING_BOUNDARY)
        units: list[str] = []
        for heading_part in heading_parts:
            paragraph_parts = self._split_keep_markers(heading_part, self.PARAGRAPH_BOUNDARY)
            for paragraph_part in paragraph_parts:
                paragraph_part = paragraph_part.strip()
                if not paragraph_part:
                    continue
                units.extend(self._split_sentence_units(paragraph_part))
        return [unit for unit in units if unit.strip()]

    # "\n\n" 连接符在常见 tokenizer 下估算为 1 个 token，用固定常数避免重复编码
    _JOIN_TOKENS = 1

    def _verify_window(self, text: str) -> int:
        """返回窗口正文的实测 token 数（真相源口径）。

        pack 累加、合并判断、最终钳制全部以它为准；逐 unit 累加只用于
        “装箱时何时换下一片”的启发式，不能作为窗口大小的承诺。
        """
        return self.estimate(text)

    def _pack_units(self, units: list[str]) -> list[str]:
        if not units:
            return []

        chunks: list[tuple[str, int]] = []
        current_parts: list[str] = []
        current_tokens = 0

        for unit in units:
            unit = unit.strip()
            if not unit:
                continue
            unit_tokens = self.estimate(unit)

            if current_parts:
                projected = current_tokens + self._JOIN_TOKENS + unit_tokens
                if projected > self.chunk_tokens:
                    chunks.append(("\n\n".join(current_parts).strip(), current_tokens))
                    if unit_tokens > self.chunk_tokens:
                        for sub in self._force_split_large_unit(unit):
                            sub_tokens = self.estimate(sub)
                            chunks.append((sub.strip(), sub_tokens))
                        current_parts = []
                        current_tokens = 0
                    else:
                        current_parts = [unit]
                        current_tokens = unit_tokens
                    continue
                current_parts.append(unit)
                current_tokens = projected
            else:
                if unit_tokens > self.chunk_tokens:
                    for sub in self._force_split_large_unit(unit):
                        sub_tokens = self.estimate(sub)
                        chunks.append((sub.strip(), sub_tokens))
                    continue
                current_parts = [unit]
                current_tokens = unit_tokens

        if current_parts:
            chunks.append(("\n\n".join(current_parts).strip(), current_tokens))

        # 尾部合并：若最后一块 < threshold_ratio 目标 tokens，且合并后
        # “实测”（_verify_window）仍 <= chunk_tokens，才合回倒数第二块。
        # 合并的承诺是“合完仍是合法单窗”：64K 窗口 + 小尾巴合完必须仍 ≤64K，
        # 否则读窗工具会返回超窗正文（七堇年 e2e：累加口径 63982+15588 相加
        # 通过 cap 1.5x 放行，实测合并后 84K > 64K，不可用）。
        # 实测必须用 estimate(合并后全文)，不能用累加口径相加：逐 unit 累加
        # 在部分 tokenizer 下系统性偏小。
        if len(chunks) > 1:
            tail_text, tail_tokens = chunks[-1]
            threshold = max(1, int(self.chunk_tokens * self.tail_merge_threshold_ratio))
            if tail_tokens < threshold:
                prev_text, _prev_tokens = chunks[-2]
                merged_text = f"{prev_text}\n\n{tail_text}".strip()
                if self._verify_window(merged_text) <= self.chunk_tokens:
                    chunks[-2] = (merged_text, self._verify_window(merged_text))
                    chunks.pop()

        # 最终钳制：任何窗口实测超过 chunk_tokens 的，必须按行/字符强制重切。
        # 这是读窗可用的最后一道门：pack 累加口径偏小、连接符低估都可能漏
        # 过去，实测是唯一真相源。钳制后仍超窗的（整段不可再分），如实保留
        # 并交由工具侧 LONGREAD_MAX_WINDOW_TOKENS 拒绝——不静默放行超窗正文。
        clamped: list[str] = []
        for text, _tokens in chunks:
            if self._verify_window(text) <= self.chunk_tokens:
                clamped.append(text)
                continue
            for sub in self._force_split_large_unit(text):
                piece = sub.strip()
                if not piece:
                    continue
                if self._verify_window(piece) <= self.chunk_tokens:
                    clamped.append(piece)
                    continue
                for sub_piece in self._force_split_by_chars(piece):
                    sub_piece = sub_piece.strip()
                    if sub_piece:
                        clamped.append(sub_piece)

        return clamped if clamped else [text for text, _tokens in chunks]

    def _build_chunks(self, raw_chunks: list[str]) -> list[TokenChunk]:
        total = len(raw_chunks)
        chunks: list[TokenChunk] = []
        for index, chunk_text in enumerate(raw_chunks):
            previous_tail = ""
            if index > 0:
                prev_text = raw_chunks[index - 1]
                previous_tail = prev_text[-self.TAIL_CHARS:] if len(prev_text) > self.TAIL_CHARS else prev_text
            chunks.append(
                TokenChunk(
                    text=chunk_text,
                    index=index,
                    total=total,
                    char_count=len(chunk_text),
                    estimated_tokens=self.estimate(chunk_text),
                    previous_tail=previous_tail,
                )
            )
        return chunks

    def _split_sentence_units(self, text: str) -> list[str]:
        boundaries = [match.end() for match in self.SENTENCE_ENDINGS.finditer(text)]
        if not boundaries:
            return [text]
        units: list[str] = []
        start = 0
        for boundary in boundaries:
            piece = text[start:boundary].strip()
            if piece:
                units.append(piece)
            start = boundary
        tail = text[start:].strip()
        if tail:
            units.append(tail)
        return units or [text]

    def _split_keep_markers(self, text: str, pattern: re.Pattern[str]) -> list[str]:
        matches = list(pattern.finditer(text))
        if not matches:
            return [text]
        parts: list[str] = []
        cursor = 0
        for match in matches:
            start = match.start()
            if start > cursor:
                segment = text[cursor:start].strip()
                if segment:
                    parts.append(segment)
            cursor = start
        tail = text[cursor:].strip()
        if tail:
            parts.append(tail)
        return parts or [text]

    def _force_split_large_unit(self, text: str) -> list[str]:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) <= 1:
            return self._force_split_by_chars(text)

        # 与 _pack_units 同思路：按 line 做增量 token 累加，避免对越滚越长的 candidate 反复 estimate
        _JOIN = 1  # "\n" 的近似 token 开销
        chunks: list[tuple[str, int]] = []
        current_parts: list[str] = []
        current_tokens = 0

        for line in lines:
            line_tokens = self.estimate(line)

            if current_parts:
                projected = current_tokens + _JOIN + line_tokens
                if projected > self.chunk_tokens:
                    chunks.append(("\n".join(current_parts).strip(), current_tokens))
                    current_parts = [line]
                    current_tokens = line_tokens
                    continue
                current_parts.append(line)
                current_tokens = projected
            else:
                current_parts = [line]
                current_tokens = line_tokens

        if current_parts:
            chunks.append(("\n".join(current_parts).strip(), current_tokens))

        flattened: list[str] = []
        for chunk_text, chunk_tokens in chunks:
            if chunk_tokens > self.chunk_tokens:
                flattened.extend(self._force_split_by_chars(chunk_text))
            else:
                flattened.append(chunk_text)
        return flattened

    def _force_split_by_chars(self, text: str) -> list[str]:
        approx_size = max(2000, int(len(text) * (self.chunk_tokens / max(self.estimate(text), 1))))
        parts: list[str] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(length, start + approx_size)
            piece = text[start:end].strip()
            if piece:
                parts.append(piece)
            start = end
        return parts


def split_text_by_tokens(
    text: str,
    chunk_tokens: int = 30000,
    tail_merge_threshold_ratio: float = 0.2,
    tail_merge_cap_ratio: float = 1.15,
    estimate_model: str | None = None,
) -> list[TokenChunk]:
    return TokenTextSplitter(
        chunk_tokens=chunk_tokens,
        tail_merge_threshold_ratio=tail_merge_threshold_ratio,
        tail_merge_cap_ratio=tail_merge_cap_ratio,
        estimate_model=estimate_model,
    ).split(text)
