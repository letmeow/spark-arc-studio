"""
工具函数模块
"""

import re
import json
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from .request_headers import build_upstream_request_headers


# ─────────────────────────────────────────────
# URL 工具
# ─────────────────────────────────────────────

def normalize_base_url(url: str) -> str:
    """规范化 Base URL。

    处理逻辑：
    - 去除首尾空白及末尾斜杠
    - 剥离 /chat/completions 等路径后缀，保留到 /v1 级别
    - 若末尾不是版本号（/v\\d+），自动追加 /v1
    """
    url = url.strip().rstrip('/')
    if not url:
        return url

    # 剥离 /chat/completions、/completions、/models 等常见末尾路径
    for suffix in ('/chat/completions', '/completions', '/models'):
        if url.endswith(suffix):
            url = url[:-len(suffix)].rstrip('/')
            break

    # 若末尾不是 /v<数字>，自动追加 /v1
    if not re.search(r'/v\d+$', url):
        url = f"{url}/v1"

    return url


def normalize_recharge_url(url: Optional[str]) -> Optional[str]:
    """规范化平台充值地址；空值返回 None，非 http(s) 链接会被拒绝。"""
    if url is None:
        return None
    normalized = str(url).strip()
    if not normalized:
        return None
    if len(normalized) > 512:
        raise ValueError("充值地址不能超过 512 个字符")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("充值地址必须是 http(s) 链接")
    return normalized


def _build_endpoint(base_url: str, path: str) -> str:
    """基于已规范化的 base_url 拼接端点路径。

    示例：
        base_url = "https://api.openai.com/v1"
        path     = "/models"
        →  "https://api.openai.com/v1/models"
    """
    normalized = normalize_base_url(base_url)
    return normalized + path


# ─────────────────────────────────────────────
# extra_body JSON 解析
# ─────────────────────────────────────────────

_PYTHON_COMMENT_RE = re.compile(r'(?m)#[^\n]*')
_ASSIGNMENT_RE = re.compile(r'^\s*\w+\s*=\s*')  # 匹配 "extra_body = " 这类赋值前缀


def parse_extra_body(text: str) -> Optional[Dict[str, Any]]:
    """解析 extra_body 字符串，返回 dict 或 None（空输入时）。

    宽松策略（按顺序应用）：
    1. 剥离 ``extra_body =`` 等赋值前缀
    2. 移除 Python 风格行注释（``# ...``）
    3. 将 Python 布尔/空值字面量替换为 JSON 等价物
       (``True`` → ``true``，``False`` → ``false``，``None`` → ``null``)
    4. 若结果不以 ``{`` 开头，自动补全外层 ``{}``
    5. 用 json.loads 解析，结果必须是 dict

    抛出 ValueError（含友好提示）；空字符串返回 None。
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # 步骤 1：剥离赋值前缀（如 extra_body={...} 或 body = {...}）
    raw = _ASSIGNMENT_RE.sub('', raw, count=1).strip()

    # 步骤 2：移除 Python 注释（# 到行末）
    raw = _PYTHON_COMMENT_RE.sub('', raw)

    # 步骤 3：Python 字面量 → JSON 字面量
    # 用词边界避免误替换 "Trueness"、"NoneType" 之类
    raw = re.sub(r'\bTrue\b',  'true',  raw)
    raw = re.sub(r'\bFalse\b', 'false', raw)
    raw = re.sub(r'\bNone\b',  'null',  raw)

    # 清理步骤 2/3 留下的多余空白行
    raw = '\n'.join(line for line in raw.splitlines() if line.strip())
    raw = raw.strip()

    if not raw:
        return None

    # 步骤 4：若不以 { 开头，尝试自动补全外层 {}
    if not raw.startswith('{'):
        wrapped = '{' + raw + '}'
    else:
        wrapped = raw

    # 步骤 5：解析并验证
    try:
        parsed = json.loads(wrapped)
    except json.JSONDecodeError as exc:
        # 如果包裹版失败，再试原始版（可能本来就是合法 JSON）
        if wrapped != raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError(
                    f"Extra Body 不是有效的 JSON（已尝试自动补全外层 {{}}）:\n{exc}"
                ) from exc
        else:
            raise ValueError(f"Extra Body 不是有效的 JSON:\n{exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f'Extra Body 必须是一个 JSON 对象，例如 {{"enable_thinking": true}}，'
            f'当前类型: {type(parsed).__name__}'
        )

    return parsed


def format_extra_body(data: Optional[Dict[str, Any]], indent: int = 2) -> str:
    """将 extra_body dict 格式化为标准 JSON 字符串。空值返回空字符串。"""
    if not data:
        return ''
    return json.dumps(data, ensure_ascii=False, indent=indent)


# ─────────────────────────────────────────────
# Token 上限提取
# ─────────────────────────────────────────────

def extract_model_token_limits(raw_item: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """从 /models 端点返回的单条 raw 数据中提取 token 上限。

    各平台字段名不一，按优先级依次尝试：
    - 上下文上限: max_model_context > context_length > context_window
      (vLLM 返回 max_model_context；Ollama / 部分兼容端点返回 context_length)
    - 单次输出上限: max_tokens > max_completion_tokens > max_output_tokens
      (OpenAI 协议中 max_tokens 指最大生成/completion token 数，非上下文窗口)

    返回 {"max_context_tokens": int|None, "max_output_tokens": int|None}。
    """
    max_context: Optional[int] = None
    max_output: Optional[int] = None

    # 上下文上限候选字段（按优先级）
    for key in ("max_model_context", "context_length", "context_window"):
        val = raw_item.get(key)
        if val is not None:
            try:
                max_context = int(val)
                break
            except (TypeError, ValueError):
                pass

    # 单次输出上限候选字段（按优先级）
    # OpenAI 协议: max_tokens = 最大生成 token 数 (completion/output)
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        val = raw_item.get(key)
        if val is not None:
            try:
                max_output = int(val)
                break
            except (TypeError, ValueError):
                pass

    return {"max_context_tokens": max_context, "max_output_tokens": max_output}


# ─────────────────────────────────────────────
# 平台探测 / 测试
# ─────────────────────────────────────────────

def probe_platform_models(
    base_url: str,
    api_key: str,
    timeout: float = 8.0,
    raise_on_error: bool = False,
) -> List[Dict[str, Any]]:
    """探测 OpenAI 兼容平台的可用模型列表"""
    try:
        import requests
    except ImportError as e:
        msg = "缺少 requests 库，无法执行远程探测"
        if raise_on_error:
            raise ImportError(msg) from e
        print(f"[probe_platform_models] {msg}")
        return []

    if not base_url or not api_key:
        msg = "base_url 和 api_key 不能为空"
        if raise_on_error:
            raise ValueError(msg)
        print(f"[probe_platform_models] {msg}")
        return []

    target_url = _build_endpoint(base_url, '/models')
    headers = build_upstream_request_headers({"Authorization": f"Bearer {api_key}"})

    def _fetch_models():
        from .retrying import run_with_probe_retry

        def _do_get(url: str):
            import requests

            return requests.get(url, headers=headers, timeout=timeout)

        # 只对网络层异常重试；HTTP 状态码由下文按业务语义处理，不重试。
        resp = run_with_probe_retry(lambda: _do_get(target_url), operation="probe")

        # 404 时降级：去掉 /v1 再试（兼容部分无版本号端点）
        if resp.status_code == 404:
            fallback = normalize_base_url(base_url).rstrip('/v1').rstrip('/') + '/models'
            if fallback != target_url:
                resp = run_with_probe_retry(lambda: _do_get(fallback), operation="probe")
        return resp

    try:
        resp = _fetch_models()

        if resp.status_code == 401:
            if raise_on_error:
                raise PermissionError("鉴权失败 (401)")
            return []

        if not resp.ok:
            if raise_on_error:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:100]}")
            return []

        js = resp.json()
        items = js.get('data') if isinstance(js, dict) else None

        # 部分非标接口直接返回 list
        if isinstance(js, list):
            items = js

        if not isinstance(items, list):
            return []

        out: List[Dict[str, Any]] = []
        for it in items:
            if isinstance(it, dict) and 'id' in it:
                token_limits = extract_model_token_limits(it)
                out.append({
                    'id': it['id'],
                    'raw': it,
                    'max_context_tokens': token_limits.get('max_context_tokens'),
                    'max_output_tokens': token_limits.get('max_output_tokens'),
                })
            elif isinstance(it, str):
                out.append({'id': it, 'raw': {}})

        return out

    except Exception as e:
        msg = f"探测失败: {e}"
        print(f"[probe_platform_models] {msg}")
        if raise_on_error:
            raise
        return []


def test_platform_chat(
    base_url: str,
    api_key: str,
    model_name: str,
    timeout: float = 30.0,
    extra_body: Dict[str, Any] = None,
    return_json: bool = False,
) -> Any:
    """测试模型对话连接"""
    try:
        import requests
    except ImportError:
        raise ImportError("缺少 requests 库")

    target_url = _build_endpoint(base_url, '/chat/completions')
    headers = build_upstream_request_headers({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "本次请求无任何要求 仅供测试连通性 无需任何思考 无需任何组织和计划 以最快的速度回答:OK"}],
        "max_tokens": 10
    }
    if extra_body:
        payload.update(extra_body)

    try:
        from .retrying import run_with_probe_retry

        def _do_post():
            import requests

            return requests.post(target_url, headers=headers, json=payload, timeout=timeout)

        resp = run_with_probe_retry(_do_post, operation="chat-test")

        if not resp.ok:
            try:
                err_msg = resp.json().get('error', {}).get('message') or resp.text
            except Exception:
                err_msg = resp.text
            raise RuntimeError(f"HTTP {resp.status_code}: {err_msg[:200]}")

        data = resp.json()
        if return_json:
            return data

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"无法解析响应内容: {str(data)[:100]}")

    except Exception as e:
        raise RuntimeError(f"测试失败: {e}")


def test_platform_embedding(
    base_url: str,
    api_key: str,
    model_name: str,
    input_text: str = "你好，这是一段测试文本。",
    extra_body: Dict[str, Any] | None = None,
):
    """测试 Embedding 可用性

    增强：
    - 支持 extra_body 参数（部分模型需要额外请求体字段）
    - 验证返回向量非零（空/全零向量视为异常）
    - 通过 httpx 超时控制避免无限等待
    """
    try:
        from .gateway import create_quick_embedding
    except ImportError as exc:
        raise ImportError("缺少 langchain_openai 库") from exc

    kwargs: Dict[str, Any] = {}
    if extra_body and isinstance(extra_body, dict):
        kwargs["extra_body"] = extra_body

    embeddings = create_quick_embedding(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        **kwargs,
    )

    vector = embeddings.embed_query(input_text)

    if not vector:
        raise ValueError("嵌入模型返回了空向量，请检查模型名称和平台配置是否正确")

    if len(vector) == 0:
        raise ValueError("嵌入模型返回了零维度向量，请检查模型配置")

    # 验证向量非全零（某些错误配置可能返回全零向量）
    if all(v == 0.0 for v in vector):
        raise ValueError("嵌入模型返回了全零向量，模型可能配置错误或不可用")

    return {
        "dims": len(vector),
    }


def stream_speed_test(
    base_url: str,
    api_key: str,
    model_name: str,
    timeout: float = 30.0,
    extra_body: Dict[str, Any] = None,
):
    """
    流式测速逻辑
    1. 发送请求要求输出 1000 个 "hello"（每个 hello = 1 token）
    2. 区分 reasoning_content（推理）和 content（正文）
    3. 首字延迟 = 从请求发送到首个正文 content 出现的时间（含推理时间）
    4. 5秒计时从首个正文 content 出现后开始
    5. 速度按 token 计（统计 hello 个数），时间从正文开始算
    """
    try:
        import requests
        import time
    except ImportError:
        raise ImportError("缺少必要库")

    target_url = _build_endpoint(base_url, '/chat/completions')
    headers = build_upstream_request_headers({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "此信息仅用于测试最快输出速度，不要任何思考，以最快的方式回答1000个\"hello\"，以空格分割"}],
        "stream": True
    }
    if extra_body:
        payload.update(extra_body)

    request_start_time = time.time()
    first_content_time = None
    content_buffer = ""
    token_count = 0
    last_update_time = None

    try:
        resp = requests.post(target_url, headers=headers, json=payload, timeout=timeout, stream=True)

        if not resp.ok:
            yield {"error": f"HTTP {resp.status_code}: {resp.text[:100]}"}
            return

        for line in resp.iter_lines():
            current_time = time.time()

            # 如果正文已经开始，检查是否超过5秒
            if first_content_time is not None:
                content_elapsed = current_time - first_content_time
                if content_elapsed >= 5.5:
                    break

            if not line:
                continue

            line_str = line.decode('utf-8')
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})

                    reasoning_content = delta.get("reasoning_content", "")
                    content = delta.get("content", "")

                    if content:
                        if first_content_time is None:
                            first_content_time = current_time
                            last_update_time = current_time
                            ftl = (first_content_time - request_start_time) * 1000
                            yield {"type": "first_token", "ftl": ftl}

                        content_buffer += content
                        token_count = content_buffer.lower().count("hello")

                    # 推理内容不计入速度统计
                    if reasoning_content and first_content_time is None:
                        pass

                except Exception:
                    continue

            # 正文开始后每秒更新速度
            if first_content_time is not None and last_update_time is not None:
                if current_time - last_update_time >= 1.0:
                    content_elapsed = current_time - first_content_time
                    avg_speed = token_count / content_elapsed if content_elapsed > 0 else 0
                    yield {
                        "type": "update",
                        "speed": avg_speed,
                        "elapsed": int(content_elapsed),
                        "total_tokens": token_count
                    }
                    last_update_time = current_time

        # 最终结算
        end_time = time.time()
        if first_content_time is not None:
            content_elapsed = min(end_time - first_content_time, 5.0)
            final_speed = token_count / content_elapsed if content_elapsed > 0 else 0
            ftl = (first_content_time - request_start_time) * 1000
        else:
            content_elapsed = 0
            final_speed = 0
            ftl = None

        yield {
            "type": "final",
            "speed": final_speed,
            "ftl": ftl,
            "total_tokens": token_count,
            "elapsed": content_elapsed
        }

    except Exception as e:
        yield {"error": str(e)}
