"""探测重试策略单测（新增：覆盖 retrying 模块）。"""

import pytest
import requests

from ..retrying import run_with_probe_retry


def test_network_errors_are_retried_then_succeed(monkeypatch) -> None:
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.ConnectionError("boom")
        return "ok"

    assert run_with_probe_retry(flaky, operation="probe") == "ok"
    assert calls["count"] == 3


def test_business_errors_are_not_retried(monkeypatch) -> None:
    calls = {"count": 0}

    def bad_request():
        calls["count"] += 1
        raise RuntimeError("HTTP 400")

    with pytest.raises(RuntimeError):
        run_with_probe_retry(bad_request, operation="probe")
    assert calls["count"] == 1


def test_retry_can_be_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MATCHBOX_PROBE_RETRY_ENABLED", "0")
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        raise requests.Timeout("slow")

    with pytest.raises(requests.Timeout):
        run_with_probe_retry(flaky, operation="probe")
    assert calls["count"] == 1
