"""独立测试的公共 fixture：内存数据库 + 隔离运行目录。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..database import create_configured_engine
from ..manager import AIManager


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AIManager:
    """创建使用临时运行目录和内存数据库的独立管理器。"""
    monkeypatch.setenv("AGENT_MATCHBOX_HOME", str(tmp_path / "matchbox-home"))
    instance = AIManager(engine=create_configured_engine("sqlite:///:memory:"))
    instance.ensure_schema()
    return instance


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """仅隔离运行目录（用于 paths/env 等无 DB 测试）。"""
    home = tmp_path / "matchbox-home"
    monkeypatch.setenv("AGENT_MATCHBOX_HOME", str(home))
    return home
