"""统一构建发往上游模型服务的请求头。

默认只透传调用方已有的请求头，不注入任何厂商标识。宿主如需握手标识
（例如 SparkArc 的 ``X-SparkArc-Client``），请通过以下任一方式显式配置：

- 环境变量 ``AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_HEADER`` /
  ``AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_VALUE``；
- 调用 :func:`set_upstream_handshake` 在进程内覆盖。

这样独立使用网关时不会把宿主标识带到上游，宿主行为也保持不变。
"""

from __future__ import annotations

from typing import Mapping, Optional

from .env_utils import get_env_var


# 通用默认值：不注入任何宿主标识。
DEFAULT_HANDSHAKE_HEADER = "X-Matchbox-Client"
DEFAULT_HANDSHAKE_VALUE = ""


def _configured_handshake() -> tuple[Optional[str], Optional[str]]:
    """读取宿主配置的握手标识；未配置时返回 ``(None, None)``。"""
    from . import request_headers as _self  # 延迟导入，避免循环引用

    header = getattr(_self, "_handshake_header_override", None)
    value = getattr(_self, "_handshake_value_override", None)
    if header is None:
        header = (get_env_var("AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_HEADER") or "").strip() or None
    if value is None:
        value = (get_env_var("AGENT_MATCHBOX_UPSTREAM_HANDSHAKE_VALUE") or "").strip() or None
    return header, value


def set_upstream_handshake(header: Optional[str], value: Optional[str]) -> None:
    """在进程内覆盖上游握手标识；传入空值表示关闭注入。"""
    from . import request_headers as _self

    _self._handshake_header_override = (str(header or "").strip() or None)
    _self._handshake_value_override = (str(value or "").strip() or None)


def build_upstream_request_headers(
    existing_headers: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """在已有请求头上补充宿主配置的固定客户端标识。

    标识不携带用户、项目、会话或请求序号等动态信息，因此不会改变请求
    消息内容，也不会把动态数据引入上游提示词缓存前缀。
    """
    headers = dict(existing_headers or {})
    header, value = _configured_handshake()
    if not header or not value:
        return headers
    header_name = header.casefold()
    for key in list(headers):
        if str(key).casefold() == header_name:
            del headers[key]
    headers[header] = value
    return headers


# 向后兼容：历史常量名保留，但不再作为默认注入值。
# 新代码请使用环境变量或 set_upstream_handshake() 显式配置。
SPARKARC_HANDSHAKE_HEADER = "X-SparkArc-Client"
SPARKARC_HANDSHAKE_VALUE = "sparkarc"
