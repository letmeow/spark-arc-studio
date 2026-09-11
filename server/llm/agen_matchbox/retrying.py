"""上游探测/测试请求的重试策略（基于 tenacity）。

只用于**运维侧**的探测与连通性测试（``probe_platform_models``、
``test_platform_chat``、``stream_speed_test``），不包裹业务模型调用。
业务调用的重试/降级属于宿主编排层决策，网关不擅自重试计费请求。

- 默认：最多 3 次、指数退避（0.5s 起，最高 4s），只重试网络层异常；
- 401 鉴权失败、400 参数错误等业务错误永不重试；
- 宿主可通过环境变量关闭或调整次数。
"""

from __future__ import annotations

import os
from typing import Callable, TypeVar

T = TypeVar("T")


def _retry_enabled() -> bool:
    raw = (os.environ.get("AGENT_MATCHBOX_PROBE_RETRY_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("AGENT_MATCHBOX_PROBE_RETRY_ATTEMPTS", "3")))
    except (TypeError, ValueError):
        return 3


def run_with_probe_retry(func: Callable[[], T], *, operation: str = "probe") -> T:
    """执行一次探测操作，网络异常时按策略重试。"""
    if not _retry_enabled():
        return func()

    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )
    import requests

    retried = retry(
        reraise=True,
        stop=stop_after_attempt(_max_attempts()),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )(func)
    try:
        return retried()
    except Exception as exc:
        # 统一加上操作名前缀，方便 GUI/日志定位是探测还是聊天测试失败。
        raise type(exc)(f"[{operation}] {exc}") from exc
