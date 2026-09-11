"""运行目录优先级测试（从主项目迁移，网关自包含版）。"""

from __future__ import annotations

from pathlib import Path

from ..env_utils import get_env_path
from ..paths import (
    get_config_file_path,
    get_mgr_home,
    get_package_dir,
    set_default_mgr_home,
)


def test_component_root_is_default_home(monkeypatch, isolated_home: Path) -> None:
    monkeypatch.delenv("AGENT_MATCHBOX_HOME", raising=False)
    set_default_mgr_home(None)

    assert get_mgr_home() == get_package_dir()
    assert get_config_file_path() == get_package_dir() / "matchbox_cfg.yaml"
    assert get_env_path() == get_package_dir() / ".env"


def test_host_can_set_default_home_in_code(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_MATCHBOX_HOME", raising=False)
    host_home = tmp_path / "host-default"
    try:
        set_default_mgr_home(host_home)
        assert get_mgr_home() == host_home.resolve()
        assert get_config_file_path() == host_home.resolve() / "matchbox_cfg.yaml"
        assert get_env_path() == host_home.resolve() / ".env"
    finally:
        set_default_mgr_home(None)


def test_environment_home_overrides_code_default(monkeypatch, tmp_path: Path) -> None:
    code_default = tmp_path / "code-default"
    environment_home = tmp_path / "environment-home"
    try:
        set_default_mgr_home(code_default)
        monkeypatch.setenv("AGENT_MATCHBOX_HOME", str(environment_home))
        assert get_mgr_home() == environment_home.resolve()
    finally:
        set_default_mgr_home(None)
