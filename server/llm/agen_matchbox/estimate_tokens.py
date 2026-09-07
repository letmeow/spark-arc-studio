import math
import os
import re
import threading
import warnings
import logging
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional, Tuple, Union

import tiktoken
import tiktoken.load  # 必须显式导入，才能访问 tiktoken.load 模块

from .hf_mirror import get_hf_candidates

# 注：全局 warnings 过滤和 logging 抑制已统一在 app.py 顶部设置，
# 确保在所有第三方库导入之前生效。此文件仅保留局部 catch_warnings。

# Windows 兼容：tiktoken 的 load_tiktoken_bpe 使用 blobfile.BlobFile 读取本地文件，
# 但 blobfile 不识别 Windows 绝对路径（如 C:\Users\...），导致 kimi 等使用
# 自定义 tiktoken tokenizer 的模型加载失败。此处对 tiktoken.load.read_file
# 打猴子补丁，在 blobfile 失败时回退到标准 open()。
if os.name == "nt":
    _orig_read_file = tiktoken.load.read_file

    def _patched_read_file(blobpath: str):
        try:
            return _orig_read_file(blobpath)
        except Exception:
            if os.path.exists(blobpath):
                with open(blobpath, "rb") as f:
                    return f.read()
            raise

    tiktoken.load.read_file = _patched_read_file

# -----------------------------------------------------------------------------
# 全局缓存 / 线程同步
# -----------------------------------------------------------------------------
_lock = threading.RLock()

_cl100k = None
_o200k = None

_tokenizer_cache: Dict[str, object] = {}
_counter_cache: Dict[str, Callable[[str], int]] = {}
_warmup_status: Dict[str, dict] = {}

_warmup_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tokenizer-warmup")

# -----------------------------------------------------------------------------
# estimate_tokens 结果缓存（按 (hash(text), model) LRU，进程内有效）
# 主要收益：当同一 prompt 连续提问时，tracked_model.on_llm_start 的重复估算会命中缓存。
# hash(str) 在单进程内稳定；cap 很小，内存开销可忽略，不怕冲突（撞到就再算一次，结果覆盖）。
# -----------------------------------------------------------------------------
_ESTIMATE_CACHE_CAP = 64
_estimate_cache: "OrderedDict[Tuple[int, str], int]" = OrderedDict()
_estimate_cache_lock = threading.RLock()

# -----------------------------------------------------------------------------
# 运行模式
# -----------------------------------------------------------------------------
# 只使用本地缓存，不联网
# export TOKEN_ESTIMATOR_LOCAL_ONLY=1
_LOCAL_ONLY = os.getenv("TOKEN_ESTIMATOR_LOCAL_ONLY", "0").lower() in {"1", "true", "yes", "on"}

# 是否允许 estimate_tokens() 在主线程里触发首次下载
# 默认关闭：这样首个用户请求不会因为拉 tokenizer 而被阻塞
# 建议在服务启动时调用 warmup_tokenizers() 进行后台预热
# export TOKEN_ESTIMATOR_ALLOW_RUNTIME_DOWNLOAD=1
_ALLOW_RUNTIME_DOWNLOAD = os.getenv("TOKEN_ESTIMATOR_ALLOW_RUNTIME_DOWNLOAD", "0").lower() in {"1", "true", "yes", "on"}

# -----------------------------------------------------------------------------
# tiktoken 基础编码器
# -----------------------------------------------------------------------------
def _get_cl100k():
    global _cl100k
    if _cl100k is None:
        _cl100k = tiktoken.get_encoding("cl100k_base")
    return _cl100k


def _get_o200k():
    global _o200k
    if _o200k is None:
        _o200k = tiktoken.get_encoding("o200k_base")
    return _o200k


# -----------------------------------------------------------------------------
# 文本分析正则
# -----------------------------------------------------------------------------
CJK = re.compile(r'[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]')
CODE_FENCE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
ALNUM_CJK = re.compile(r'[\u3000-\u9fff\uac00-\ud7af\uff00-\uffefA-Za-z0-9_]')

# -----------------------------------------------------------------------------
# 懒加载依赖 + 运行时警告抑制
# -----------------------------------------------------------------------------
# app.py 的全局抑制无法覆盖以下两种场景：
#   a) transformers 的 logger 有自己的 StreamHandler(stderr) 且 propagate=False，
#      root Filter 拦截不到，必须在导入后直接 setLevel + 移除 handler
#   b) google.genai 的 ExperimentalWarning 在 count_tokens() 运行时触发，
#      需 catch_warnings 局部包裹
# 因此这些逻辑集中在此区域管理
def _suppress_noisy_library_loggers():
    """导入第三方 tokenizer 相关依赖后，强制压低其自带 logger 噪音。"""
    for logger_name in ("transformers", "transformers_modules", "huggingface_hub"):
        library_logger = logging.getLogger(logger_name)
        library_logger.setLevel(logging.ERROR)
        for handler in list(library_logger.handlers):
            library_logger.removeHandler(handler)


def _lazy_auto_tokenizer():
    from transformers import AutoTokenizer
    _suppress_noisy_library_loggers()
    return AutoTokenizer


def _lazy_google_local_tokenizer():
    from google.genai.local_tokenizer import LocalTokenizer
    return LocalTokenizer


# -----------------------------------------------------------------------------
# 环境变量临时覆盖
# -----------------------------------------------------------------------------
@contextmanager
def _temporary_env(**kwargs):
    old = {}
    try:
        for k, v in kwargs.items():
            old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# -----------------------------------------------------------------------------
# 包装为“计数器”
# -----------------------------------------------------------------------------
def _wrap_tiktoken_counter(enc) -> Callable[[str], int]:
    def _count(text: str) -> int:
        return len(enc.encode(text, disallowed_special=()))
    return _count


def _wrap_hf_tokenizer_counter(tok) -> Callable[[str], int]:
    def _count(text: str) -> int:
        encoded = tok(
            text,
            add_special_tokens=False,
            return_attention_mask=False,
            return_token_type_ids=False,
        )
        input_ids = encoded["input_ids"]
        return len(input_ids)
    return _count


def _wrap_gemini_local_counter(tok) -> Callable[[str], int]:
    def _count(text: str) -> int:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*experimental.*")
            result = tok.count_tokens(text)
        if hasattr(result, "total_tokens"):
            return int(result.total_tokens)
        if isinstance(result, dict):
            if "total_tokens" in result:
                return int(result["total_tokens"])
            if "totalTokens" in result:
                return int(result["totalTokens"])
        if isinstance(result, (int, float)):
            return int(result)
        raise TypeError(f"Unsupported Gemini count_tokens result: {type(result)!r}")
    return _count


# -----------------------------------------------------------------------------
# 下载策略：由 Matchbox 自有镜像探测模块排序候选地址。
# -----------------------------------------------------------------------------
def _build_hf_attempts() -> list[dict[str, str | None]]:
    """构造 tokenizer 下载尝试序列。

    hf_mirror 已按配置、网络归属地和可达性排序，这里直接复用其候选列表。
    注意：huggingface_hub 的 ENDPOINT 常量在 import 时快照 os.environ，
    运行时改 os.environ['HF_ENDPOINT'] 不会影响已创建的 HfApi/transformers
    调用。因此每个 attempt 必须同时设置 HF_ENDPOINT（给新进程/新 import
    看）和直接改写 huggingface_hub.constants.ENDPOINT（给当前进程内已
    import 的 hub 看），否则大陆镜像顺序排第一也切不过去——hf_debug 实测：
    候选 [mirror, official] 全走 official 超时，而显式 HF_ENDPOINT=mirror
    秒通。_apply_attempt_endpoint 统一做这两件事，调用方禁止只设一半。
    """
    if _LOCAL_ONLY:
        return [{"HF_ENDPOINT": None}]

    base_timeout = {
        "HF_HUB_ETAG_TIMEOUT": "8",
        "HF_HUB_DOWNLOAD_TIMEOUT": "30",
    }
    candidates = get_hf_candidates(probe=True)
    attempts: list[dict[str, str | None]] = []
    for endpoint in candidates:
        env = dict(base_timeout)
        env["HF_ENDPOINT"] = endpoint if endpoint != "https://huggingface.co" else None
        attempts.append(env)
    if not attempts:
        attempts.append({"HF_ENDPOINT": None, **base_timeout})
    return attempts

# -----------------------------------------------------------------------------
# tokenizer / counter 加载器
# -----------------------------------------------------------------------------
def _cache_get_tokenizer(key: str):
    with _lock:
        return _tokenizer_cache.get(key)


def _cache_set_tokenizer(key: str, value):
    with _lock:
        _tokenizer_cache[key] = value


def _cache_get_counter(key: str):
    with _lock:
        return _counter_cache.get(key)


def _cache_set_counter(key: str, value):
    with _lock:
        _counter_cache[key] = value


@contextmanager
def _attempt_endpoint(endpoint: str | None):
    """单个 HF 下载 attempt 的 endpoint 上下文：env + 进程内 hub 常量一起切。

    只设 os.environ 不够：huggingface_hub.constants.ENDPOINT 在 import 时快照，
    且 transformers 内部自建的 HfApi() 在构造时再次快照 constants.ENDPOINT——
    运行时只改 env，已构造的 Api 照样走旧地址。只改常量也不够（子进程/新
    import 看 env）。退出时两者都恢复，避免污染后续非 HF 请求。
    """
    old_env = os.environ.get("HF_ENDPOINT")
    old_constants: list[tuple[object, str, object]] = []
    try:
        if endpoint is None:
            os.environ.pop("HF_ENDPOINT", None)
        else:
            os.environ["HF_ENDPOINT"] = endpoint
        try:
            import huggingface_hub.constants as _hub_constants

            for attr in ("ENDPOINT", "HUGGINGFACE_CO_URL_TEMPLATE"):
                if hasattr(_hub_constants, attr):
                    old_constants.append((_hub_constants, attr, getattr(_hub_constants, attr)))
            _hub_constants.ENDPOINT = (endpoint or "https://huggingface.co").rstrip("/")
            if hasattr(_hub_constants, "HUGGINGFACE_CO_URL_TEMPLATE"):
                _hub_constants.HUGGINGFACE_CO_URL_TEMPLATE = (
                    _hub_constants.ENDPOINT + "/{repo_id}/resolve/{revision}/{filename}"
                )
        except Exception:
            pass
        try:
            # transformers.utils.hub 在 import 时就构造了一个全局 HfApi()
            # 单例（transformers.utils.hub.list_repo_tree 是它的绑定方法），
            # 其 endpoint 在 import 时快照，后续改 env/改常量都影响不到它——
            # 这就是“候选顺序正确但流量仍走 official 超时”的根因。
            # attempt 内把它也指向当前 endpoint，退出时恢复。
            from huggingface_hub import HfApi as _HfApi

            _orig_init = _HfApi.__init__

            def _patched_init(self, endpoint=None, *args, **kwargs):
                if endpoint is None:
                    endpoint = os.environ.get("HF_ENDPOINT")
                return _orig_init(self, endpoint=endpoint, *args, **kwargs)

            _HfApi.__init__ = _patched_init  # type: ignore[method-assign]
            _hfapi_patched = True
            _hub_singleton_old_endpoint: str | None = None
            try:
                import transformers.utils.hub as _tf_hub

                _hub_singleton = getattr(_tf_hub.list_repo_tree, "__self__", None)
                if _hub_singleton is not None and hasattr(_hub_singleton, "endpoint"):
                    _hub_singleton_old_endpoint = str(_hub_singleton.endpoint)
                    _hub_singleton.endpoint = os.environ.get("HF_ENDPOINT") or _hub_singleton.endpoint
            except Exception:
                _hub_singleton = None
        except Exception:
            _hfapi_patched = False
            _hub_singleton = None
            _hub_singleton_old_endpoint = None
        try:
            yield
        finally:
            if _hfapi_patched:
                try:
                    _HfApi.__init__ = _orig_init  # type: ignore[method-assign]
                except Exception:
                    pass
            try:
                if _hub_singleton is not None and _hub_singleton_old_endpoint is not None:
                    _hub_singleton.endpoint = _hub_singleton_old_endpoint
            except Exception:
                pass
    finally:
        try:
            if old_env is None:
                os.environ.pop("HF_ENDPOINT", None)
            else:
                os.environ["HF_ENDPOINT"] = old_env
        except Exception:
            pass
        for module, attr, value in old_constants:
            try:
                setattr(module, attr, value)
            except Exception:
                pass


def _get_hf_counter(cache_key: str, repo: str, trust_remote_code: bool = False) -> Optional[Callable[[str], int]]:
    cached = _cache_get_counter(cache_key)
    if cached is not None:
        return cached

    try:
        AutoTokenizer = _lazy_auto_tokenizer()
    except Exception:
        return None

    attempts = _build_hf_attempts()

    last_error: Exception | None = None
    for envs in attempts:
        try:
            with warnings.catch_warnings():
                # 局部屏蔽 HF Hub unauthenticated / transformers FutureWarning+UserWarning
                warnings.filterwarnings("ignore", message=".*unauthenticated.*")
                warnings.filterwarnings("ignore", category=FutureWarning)
                warnings.filterwarnings("ignore", category=UserWarning, module="transformers")
                with _temporary_env(**{k: v for k, v in envs.items() if k != "HF_ENDPOINT"}):
                    with _attempt_endpoint(envs.get("HF_ENDPOINT")):
                        tok = AutoTokenizer.from_pretrained(
                            repo,
                            trust_remote_code=trust_remote_code,
                            local_files_only=_LOCAL_ONLY,
                        )
            _cache_set_tokenizer(f"tok::{cache_key}", tok)
            counter = _wrap_hf_tokenizer_counter(tok)
            _cache_set_counter(cache_key, counter)
            return counter
        except Exception as exc:
            # 保留最后一次失败原因：调用方（warmup 状态/estimate 诊断）需要它
            # 判断是网络直连失败还是仓库本身不可用，而不是吞掉后只报 None。
            last_error = exc
            continue

    if last_error is not None:
        try:
            family = str(cache_key).replace("exact::", "")
        except Exception:
            family = ""
        _set_warmup_status(
            family or cache_key,
            {
                "matched": family or None,
                "exact": True,
                "ok": False,
                "detail": f"loader failed: {type(last_error).__name__}: {str(last_error)[:300]}",
            },
        )
    return None


def _get_gemini_local_counter(cache_key: str, model_name: str) -> Optional[Callable[[str], int]]:
    cached = _cache_get_counter(cache_key)
    if cached is not None:
        return cached

    try:
        LocalTokenizer = _lazy_google_local_tokenizer()
        with warnings.catch_warnings():
            # 局部屏蔽 google-genai SDK ExperimentalWarning
            warnings.filterwarnings("ignore", message=".*experimental.*")
            tok = LocalTokenizer(model_name=model_name)
        _cache_set_tokenizer(f"tok::{cache_key}", tok)
        counter = _wrap_gemini_local_counter(tok)
        _cache_set_counter(cache_key, counter)
        return counter
    except Exception:
        return None


_GEMINI_LOCAL_TOKENIZER_CANDIDATES = (
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
    "gemini-2.5-flash",
)


def _get_gemini_local_counter_with_fallback(
    cache_key: str,
    model_name: Optional[str],
) -> Optional[Callable[[str], int]]:
    """优先尝试 Gemini 3 Flash，本地 SDK 不支持时回退到同代/次代可用模型。"""
    candidates: list[str] = []
    normalized = str(model_name or "").strip()
    if normalized and "gemini" in normalized.lower() and "-" in normalized:
        candidates.append(normalized)
    for candidate in _GEMINI_LOCAL_TOKENIZER_CANDIDATES:
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        counter = _get_gemini_local_counter(cache_key, candidate)
        if counter is not None:
            return counter
    return None


# -----------------------------------------------------------------------------
# 模型规则
# keys 只保留“模型族名 / 公司名”
# exact_loader 内部指向当前较新的公开 tokenizer / 本地 tokenizer 能力
# -----------------------------------------------------------------------------
MODEL_RULES = [
    {
        "name": "openai",
        "keys": ("gpt", "openai"),
        "vocab_size": 200000,
        "exact_loader": lambda model: _wrap_tiktoken_counter(
            tiktoken.encoding_for_model(model if model and "-" in model else "gpt-4o")
        ),
        "fallback_encoder": _get_o200k,
        "fallback_factors": (1.00, 1.00, 1.00),
    },
    {
        "name": "claude",
        "keys": ("claude", "anthropic"),
        "vocab_size": 200000,
        "exact_loader": None,
        "fallback_encoder": _get_o200k,
        "fallback_factors": (1.15, 1.25, 1.08),
    },
    {
        "name": "grok",
        "keys": ("grok", "xai"),
        "vocab_size": 200000,
        "exact_loader": None,
        "fallback_encoder": _get_o200k,
        "fallback_factors": (1.00, 1.00, 1.00),
    },
    {
        "name": "qwen",
        "keys": ("qwen", "tongyi", "aliyun", "alibaba"),
        # 说明：Qwen 最新公开家族正在快速演化；未预热前这里作为家族级参考值。
        # 预热成功后 get_vocab_size() 会优先返回真实 tokenizer.vocab_size
        "vocab_size": 248320,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::qwen", "Qwen/Qwen3.6-35B-A3B", trust_remote_code=False
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.00, 0.60, 0.92),
    },
    {
        "name": "kimi",
        "keys": ("kimi", "moonshot"),
        "vocab_size": 163840,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::kimi", "moonshotai/Kimi-K2.5", trust_remote_code=True
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.02, 0.60, 0.98),
    },
    {
        "name": "glm",
        "keys": ("chatglm", "glm", "zai"),
        "vocab_size": 154880,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::glm", "zai-org/GLM-5.1", trust_remote_code=True
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.00, 0.62, 0.95),
    },
    {
        "name": "deepseek",
        "keys": ("deepseek",),
        "vocab_size": 129280,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::deepseek", "deepseek-ai/DeepSeek-V3", trust_remote_code=False
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.08, 0.62, 0.95),
    },
    {
        "name": "gemini",
        "keys": ("gemini",),
        "vocab_size": 256000,
        # Python 官方 SDK 已提供 LocalTokenizer；优先尝试 Gemini 3 Flash，
        # 当前环境若尚未支持该具体型号，再回退到同代/次代可用模型。
        "exact_loader": lambda model: _get_gemini_local_counter_with_fallback(
            "exact::gemini",
            model,
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.00, 0.72, 0.90),
    },
    {
        "name": "gemma",
        "keys": ("gemma",),
        "vocab_size": 262144,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::gemma", "google/gemma-4-E4B-it", trust_remote_code=False
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.00, 0.74, 0.88),
    },
    {
        "name": "minimax",
        "keys": ("minimax",),
        "vocab_size": 200064,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::minimax", "MiniMaxAI/MiniMax-M2.7", trust_remote_code=True
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (0.98, 0.75, 0.92),
    },
    {
        "name": "mimo",
        "keys": ("mimov2", "mimo", "xiaomi"),
        "vocab_size": 152576,
        "exact_loader": lambda model: _get_hf_counter(
            "exact::mimo", "XiaomiMiMo/MiMo-V2-Flash", trust_remote_code=True
        ),
        "fallback_encoder": _get_cl100k,
        "fallback_factors": (1.00, 0.60, 0.92),
    },
]

UNKNOWN_RULE = {
    "name": "unknown",
    "vocab_size": 100000,
    "fallback_encoder": _get_cl100k,
    "fallback_factors": (1.00, 0.85, 0.96),
}

# -----------------------------------------------------------------------------
# 工具函数
# -----------------------------------------------------------------------------
def _match_rule(model: Optional[str]) -> Optional[dict]:
    if not model:
        return None

    m = model.lower().strip()

    indexed = []
    for rule in MODEL_RULES:
        for key in rule["keys"]:
            indexed.append((len(key), key, rule))
    indexed.sort(reverse=True)

    for _, key, rule in indexed:
        if key in m:
            return rule
    return None


def _safe_exact_count(text: str, rule: Optional[dict], model: Optional[str]) -> Optional[int]:
    if not text or not rule:
        return None

    cache_key = f"exact::{rule['name']}"
    cached = _cache_get_counter(cache_key)
    if cached is not None:
        try:
            return int(cached(text))
        except Exception:
            return None

    loader = rule.get("exact_loader")
    if loader is None:
        return None

    # 默认不允许 estimate_tokens() 在用户请求链路里触发首次下载
    # 这样不会因为 tokenizer 初次拉取而阻塞主线程
    if not _ALLOW_RUNTIME_DOWNLOAD and not _LOCAL_ONLY:
        return None

    try:
        counter = loader(model)
        if counter is None:
            return None
        _cache_set_counter(cache_key, counter)
        return int(counter(text))
    except Exception:
        return None


def _text_chunks(text: str) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    last = 0
    for m in CODE_FENCE.finditer(text):
        if m.start() > last:
            out.append((text[last:m.start()], False))
        out.append((m.group(0), True))
        last = m.end()
    if last < len(text):
        out.append((text[last:], False))
    return out


def _calc_lang_ratio(text: str) -> float:
    if not text:
        return 0.0

    meaningful = ALNUM_CJK.findall(text)
    if not meaningful:
        cjk_chars = len(CJK.findall(text))
        return 1.0 if cjk_chars > 0 else 0.0

    cjk_chars = sum(1 for ch in meaningful if CJK.match(ch))
    return cjk_chars / len(meaningful)


def _estimate_with_fallback(
    text: str,
    base_encoder_fn: Callable[[], object],
    en_factor: float,
    zh_factor: float,
    code_factor: float,
    force_code: bool = False,
) -> int:
    if not text:
        return 0

    enc = base_encoder_fn()

    if force_code:
        base = len(enc.encode(text, disallowed_special=()))
        return max(1, math.ceil(base * code_factor))

    total = 0
    for chunk, chunk_is_code in _text_chunks(text):
        if not chunk:
            continue

        base = len(enc.encode(chunk, disallowed_special=()))
        if base == 0:
            continue

        if chunk_is_code:
            factor = code_factor
        else:
            ratio = _calc_lang_ratio(chunk)
            factor = zh_factor * ratio + en_factor * (1.0 - ratio)

        total += max(1, math.ceil(base * factor))

    return max(1, total)


def _try_get_real_vocab_size(rule: dict) -> Optional[int]:
    tok = _cache_get_tokenizer(f"tok::exact::{rule['name']}")
    if tok is None:
        return None

    value = getattr(tok, "vocab_size", None)
    if isinstance(value, int) and value > 0:
        return value

    if hasattr(tok, "get_vocab"):
        try:
            vocab = tok.get_vocab()
            if isinstance(vocab, dict) and vocab:
                return len(vocab)
        except Exception:
            pass

    return None


# -----------------------------------------------------------------------------
# 对外导出方法
# -----------------------------------------------------------------------------
def estimate_tokens(text: str, model: str = None, is_code: bool = False) -> int:
    """
    估算文本 Token 数量

    优先级：
    1) 已缓存的本地真实 tokenizer
    2) （可选）允许主线程首次下载时，尝试真实 tokenizer
    3) 家族级 fallback tokenizer + 分段/语言修正

    结果会按 (hash(text), model) 走进程内 LRU 缓存（is_code 路径不缓存）。
    """
    if not text:
        return 0

    cache_key: Optional[Tuple[int, str]] = None
    if not is_code:
        cache_key = (hash(text), model or "")
        with _estimate_cache_lock:
            cached = _estimate_cache.get(cache_key)
            if cached is not None:
                _estimate_cache.move_to_end(cache_key)
                return cached

    rule = _match_rule(model)

    exact = _safe_exact_count(text, rule, model)
    if exact is not None:
        result = max(1, exact)
    else:
        active_rule = rule or UNKNOWN_RULE
        en_factor, zh_factor, code_factor = active_rule["fallback_factors"]
        result = _estimate_with_fallback(
            text=text,
            base_encoder_fn=active_rule["fallback_encoder"],
            en_factor=en_factor,
            zh_factor=zh_factor,
            code_factor=code_factor,
            force_code=is_code,
        )

    if cache_key is not None:
        with _estimate_cache_lock:
            _estimate_cache[cache_key] = result
            _estimate_cache.move_to_end(cache_key)
            while len(_estimate_cache) > _ESTIMATE_CACHE_CAP:
                _estimate_cache.popitem(last=False)

    return result


def get_vocab_size(model: str) -> int:
    """
    获取模型词表大小（用于参考）

    优先级：
    1) 已缓存真实 tokenizer 的实际 vocab_size
    2) 家族级静态参考值
    """
    rule = _match_rule(model)
    if rule is None:
        return UNKNOWN_RULE["vocab_size"]

    real = _try_get_real_vocab_size(rule)
    if real is not None:
        return real

    return int(rule["vocab_size"])


# -----------------------------------------------------------------------------
# 后台预热
# -----------------------------------------------------------------------------
def _set_warmup_status(name: str, data: dict):
    with _lock:
        _warmup_status[name] = data


def get_warmup_status() -> Dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _warmup_status.items()}


def is_exact_counter_ready(model: str | None = None) -> bool:
    """统一口径探针：给定 model 的真实 tokenizer 是否就绪。

    全仓库“切多大 / 读不读 / 预算够不够”的调用方禁止自行判断
    exact/fallback——需要向用户或日志解释口径时，调这个函数即可。
    model 为空表示无模型回退口径，永远返回 False（调用方无需区分）。
    """
    if not (model or "").strip():
        return False
    rule = _match_rule(model)
    if rule is None:
        return False
    if rule.get("exact_loader") is None:
        return False
    return _cache_get_counter(f"exact::{rule['name']}") is not None


def _warmup_job(models: Optional[List[str]] = None) -> Dict[str, dict]:
    results: Dict[str, dict] = {}

    # 先预热基础 tiktoken
    try:
        _get_cl100k()
        _get_o200k()
        results["_base_tiktoken"] = {
            "matched": None,
            "exact": False,
            "ok": True,
            "detail": "cl100k_base + o200k_base ready",
        }
    except Exception as e:
        results["_base_tiktoken"] = {
            "matched": None,
            "exact": False,
            "ok": False,
            "detail": f"base tiktoken warmup failed: {e}",
        }

    if not models:
        models = [
            "gpt",
            "claude",
            "grok",
            "qwen",
            "kimi",
            "glm",
            "deepseek",
            "gemini",
            "gemma",
            "minimax",
            "mimov2",
        ]

    for model in models:
        rule = _match_rule(model)
        if rule is None:
            data = {
                "matched": None,
                "exact": False,
                "ok": False,
                "detail": "no matching rule",
            }
            results[model] = data
            _set_warmup_status(model, data)
            continue

        cache_key = f"exact::{rule['name']}"
        cached = _cache_get_counter(cache_key)
        if cached is not None:
            try:
                _ = cached("warmup")
                data = {
                    "matched": rule["name"],
                    "exact": True,
                    "ok": True,
                    "detail": "already cached",
                }
            except Exception as e:
                data = {
                    "matched": rule["name"],
                    "exact": True,
                    "ok": False,
                    "detail": f"cached counter unusable: {e}",
                }
                print(f"  ⚠️ [{rule['name']}] Cached tokenizer unusable: {e}", flush=True)
            results[model] = data
            _set_warmup_status(model, data)
            continue

        loader = rule.get("exact_loader")
        if loader is None:
            data = {
                "matched": rule["name"],
                "exact": False,
                "ok": True,
                "detail": "fallback-only family",
            }
            results[model] = data
            _set_warmup_status(model, data)
            continue

        _set_warmup_status(model, {
            "matched": rule["name"],
            "exact": True,
            "ok": False,
            "detail": "warming",
        })

        try:
            counter = loader(model)
            if counter is None:
                data = {
                    "matched": rule["name"],
                    "exact": True,
                    "ok": False,
                    "detail": "loader returned None, will fallback at runtime",
                }
                print(f"  ⚠️ [{rule['name']}] Tokenizer returned None, will use fallback at runtime", flush=True)
            else:
                _cache_set_counter(cache_key, counter)
                _ = counter("warmup")
                data = {
                    "matched": rule["name"],
                    "exact": True,
                    "ok": True,
                    "detail": "loaded and tested",
                }
        except Exception as e:
            data = {
                "matched": rule["name"],
                "exact": True,
                "ok": False,
                "detail": f"warmup failed: {e}",
            }
            print(f"  ❌ [{rule['name']}] Tokenizer load failed: {e}", flush=True)

        results[model] = data
        _set_warmup_status(model, data)

    # 汇总
    ok_count = sum(1 for v in results.values() if v.get("ok"))
    total_count = len(results)
    cache_hits = sum(1 for v in results.values() if v.get("detail") == "already cached")
    print(f"🔥 Tokenizer warm-up complete: {ok_count}/{total_count} available", flush=True)
    return results


def warmup_tokenizers(models: Optional[List[str]] = None, blocking: bool = False) -> Union[Future, Dict[str, dict]]:
    """
    预热 tokenizer

    - blocking=False（默认）: 后台线程预热，立即返回 Future，不阻塞主线程
    - blocking=True         : 当前线程等待结果并返回结果字典

    建议：
        服务启动时调用一次 warmup_tokenizers(blocking=False)
        这样 estimate_tokens() 的热路径就更不容易卡在首次下载上
    """
    if blocking:
        return _warmup_job(models)
    return _warmup_executor.submit(_warmup_job, models)


def is_warmup_done() -> bool:
    status = get_warmup_status()
    if not status:
        return False
    return all(v.get("detail") != "warming" for v in status.values())
