"""平台身份与重复 Base URL 的回归测试（从主项目迁移，网关自包含版）。"""

from __future__ import annotations

import pytest

from .. import config as matchbox_config
from ..manager import AIManager
from ..models import LLMPlatform
from ..platform_identity import (
    legacy_config_platform_key,
    legacy_database_platform_key,
)
from ..security import SecurityManager


def test_custom_and_system_platforms_allow_duplicate_base_url(manager: AIManager) -> None:
    """同一用户自定义平台与系统平台都可以共享 URL，但身份不同。"""
    url = "https://same.example/v1"
    custom_a = manager.add_platform("自定义甲", url, user_id="42")
    custom_b = manager.add_platform("自定义乙", url, user_id="42")
    system_a = manager.admin_add_sys_platform("系统甲", url)
    system_b = manager.admin_add_sys_platform("系统乙", url)

    assert len({custom_a.platform_key, custom_b.platform_key, system_a.platform_key, system_b.platform_key}) == 4
    with manager.Session() as session:
        rows = session.query(LLMPlatform).filter(LLMPlatform.base_url == url).all()
        assert len(rows) == 4


def test_disabled_platform_revival_requires_matching_name_and_url(manager: AIManager) -> None:
    """同 URL 的不同名称创建新平台，同名同 URL 创建复活原平台。"""
    url = "https://revive.example/v1"
    custom_a = manager.add_platform("禁用自定义", url, user_id="42")
    manager.disable_platform(custom_a.id, user_id="42")
    custom_b = manager.add_platform("新自定义", url, user_id="42")
    assert custom_b.id != custom_a.id

    custom_revived = manager.add_platform("禁用自定义", url, user_id="42")
    assert custom_revived.id == custom_a.id

    # 显式 platform_key 仍可在名称变化时复活自定义平台的稳定身份。
    manager.disable_platform(custom_revived.id, user_id="42")
    custom_key_revived = manager.add_platform(
        "复活自定义",
        url,
        user_id="42",
        platform_key=custom_a.platform_key,
    )
    assert custom_key_revived.id == custom_a.id

    system_a = manager.admin_add_sys_platform("禁用系统", url)
    manager.disable_platform(system_a.id, admin_mode=True)
    system_b = manager.admin_add_sys_platform("新系统", url)
    assert system_b.id != system_a.id

    system_revived = manager.admin_add_sys_platform("禁用系统", url)
    assert system_revived.id == system_a.id

    # 显式 platform_key 仍可在名称变化时复活稳定身份。
    manager.disable_platform(system_revived.id, admin_mode=True)
    revived = manager.admin_add_sys_platform("复活系统", url, platform_key=system_a.platform_key)
    assert revived.id == system_a.id
    with manager.Session() as session:
        assert session.query(LLMPlatform).filter_by(id=system_a.id).one().disable == 0
        assert session.query(LLMPlatform).filter_by(id=custom_a.id).one().disable == 0


def test_disabled_system_name_does_not_block_system_rename(manager: AIManager) -> None:
    """旧的禁用系统平台名称不应阻塞管理员重命名当前系统平台。"""
    url = "https://rename-disabled.example/v1"
    legacy = manager.admin_add_sys_platform("Alibaba Dashscope", url)
    manager.disable_platform(legacy.id, admin_mode=True)
    current = manager.admin_add_sys_platform("阿里云百炼", url)

    manager.admin_update_sys_platform(
        current.id,
        new_name="Alibaba Dashscope",
        new_base_url=url,
    )

    with manager.Session() as session:
        assert session.query(LLMPlatform).filter_by(id=legacy.id).one().disable == 1
        renamed = session.query(LLMPlatform).filter_by(id=current.id).one()
        assert renamed.name == "Alibaba Dashscope"
        assert renamed.disable == 0


def test_disabled_system_name_can_be_reused_by_custom_platform_without_revival(
    manager: AIManager,
) -> None:
    """普通平台创建不能跨类型复活禁用系统平台，但应允许重新使用该名称。"""
    url = "https://reuse-disabled-system.example/v1"
    name = "禁用系统平台名称"
    system = manager.admin_add_sys_platform(name, url)
    manager.disable_platform(system.id, admin_mode=True)

    custom = manager.add_platform(name, url, user_id="1")

    assert custom.id != system.id
    assert custom.is_sys == 0
    with manager.Session() as session:
        assert session.query(LLMPlatform).filter_by(id=system.id).one().disable == 1

    with pytest.raises(ValueError, match="平台名称.*已存在"):
        manager.admin_add_sys_platform(name, url)


def test_yaml_sync_uses_platform_key_for_duplicate_urls(manager: AIManager) -> None:
    """YAML 中相同 URL 的两个系统平台必须分别落库。"""
    url = "https://yaml-duplicate.example/v1"
    configs = {
        "配置甲": {"platform_key": "seed-a", "base_url": url, "models": {}},
        "配置乙": {"platform_key": "seed-b", "base_url": url, "models": {}},
    }
    manager._sync_default_platforms(force_reset=True, raw_platform_configs=configs)

    with manager.Session() as session:
        rows = session.query(LLMPlatform).filter_by(is_sys=1).order_by(LLMPlatform.id).all()
        assert [(row.name, row.platform_key, row.base_url) for row in rows] == [
            ("配置甲", "seed-a", url),
            ("配置乙", "seed-b", url),
        ]


def test_legacy_yaml_without_platform_key_keeps_duplicate_urls_separate(manager: AIManager) -> None:
    """没有 platform_key 的旧 YAML 按名称和 URL 生成不同的兼容身份。"""
    url = "https://legacy-yaml-duplicate.example/v1"
    configs = {
        "旧配置甲": {"base_url": url, "models": {}},
        "旧配置乙": {"base_url": url, "models": {}},
    }
    manager._sync_default_platforms(force_reset=True, raw_platform_configs=configs)

    with manager.Session() as session:
        rows = session.query(LLMPlatform).filter_by(is_sys=1).order_by(LLMPlatform.id).all()
        assert len(rows) == 2
        assert {row.platform_key for row in rows} == {
            legacy_config_platform_key("旧配置甲", url),
            legacy_config_platform_key("旧配置乙", url),
        }


def test_explicit_changed_platform_key_does_not_reuse_name_and_url(manager: AIManager) -> None:
    """显式平台 key 变化代表新身份，不得按同名同 URL 静默复用旧行。"""
    url = "https://identity-change.example/v1"
    manager._sync_default_platforms(
        force_reset=True,
        raw_platform_configs={
            "稳定名称": {"platform_key": "identity-old", "base_url": url, "models": {}},
        },
    )
    manager._sync_default_platforms(
        force_reset=True,
        raw_platform_configs={
            "稳定名称": {"platform_key": "identity-new", "base_url": url, "models": {}},
        },
    )

    with manager.Session() as session:
        old_row = session.query(LLMPlatform).filter_by(platform_key="identity-old").one()
        new_row = session.query(LLMPlatform).filter_by(platform_key="identity-new").one()
        assert old_row.id != new_row.id
        assert old_row.disable == 1
        assert new_row.disable == 0


def test_export_keys_are_indexed_by_platform_key(manager: AIManager) -> None:
    """两个同 URL 平台的结构和密钥导出不能互相覆盖。"""
    manager.set_llm_key("platform-test-master", persist=False)
    url = "https://export-duplicate.example/v1"
    first = manager.admin_add_sys_platform("导出甲", url)
    second = manager.admin_add_sys_platform("导出乙", url)
    manager.admin_update_sys_platform_api_key(first.id, "key-a")
    manager.admin_update_sys_platform_api_key(second.id, "key-b")

    config_data = manager.admin_build_export_data()
    key_data = manager.admin_build_key_export_data()
    assert {config_data["导出甲"]["platform_key"], config_data["导出乙"]["platform_key"]} == {
        first.platform_key,
        second.platform_key,
    }
    assert set(key_data) == {first.platform_key, second.platform_key}
    assert key_data[first.platform_key] != key_data[second.platform_key]


def test_legacy_database_key_is_omitted_from_config_export_and_migrated_for_keys(
    manager: AIManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """历史数据库 key 不写入结构文件，密钥导出改用可迁移的兼容 key。"""
    manager.set_llm_key("legacy-export-master", persist=False)
    url = "https://legacy-export.example/v1"
    platform = manager.admin_add_sys_platform("历史平台", url)
    manager.admin_update_sys_platform_api_key(platform.id, "legacy-api-key")

    with manager.Session() as session:
        row = session.query(LLMPlatform).filter_by(id=platform.id).one()
        row.platform_key = legacy_database_platform_key(row.id)
        session.commit()

    config_data = manager.admin_build_export_data()
    key_data = manager.admin_build_key_export_data()
    portable_key = legacy_config_platform_key("历史平台", url)
    assert "platform_key" not in config_data["历史平台"]
    assert set(key_data) == {portable_key}

    monkeypatch.setattr(matchbox_config, "load_key_yaml_raw", lambda: key_data)
    configs = {"历史平台": {"base_url": url, "models": {}}}
    matchbox_config.merge_key_yaml_into_configs(configs)
    assert configs["历史平台"]["platform_key"] == portable_key
    assert configs["历史平台"]["api_key"] == key_data[portable_key]["api_key"]


def test_exported_config_without_legacy_key_keeps_existing_database_row(
    manager: AIManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置合并自动补 key 后，仍按名称和 URL 找回历史数据库平台。"""
    url = "https://legacy-sync.example/v1"
    platform = manager.admin_add_sys_platform("历史同步平台", url)
    with manager.Session() as session:
        row = session.query(LLMPlatform).filter_by(id=platform.id).one()
        row.platform_key = legacy_database_platform_key(row.id)
        session.commit()

    configs = {"历史同步平台": {"base_url": url, "models": {}}}
    monkeypatch.setattr(
        "agen_matchbox.manager.load_default_platform_configs_raw",
        lambda: configs,
    )

    def add_compatibility_key(raw_configs):
        raw_configs["历史同步平台"]["platform_key"] = legacy_config_platform_key(
            "历史同步平台", url
        )
        return raw_configs

    monkeypatch.setattr(
        "agen_matchbox.manager.merge_key_yaml_into_configs",
        add_compatibility_key,
    )
    manager._sync_default_platforms(force_reset=False)

    with manager.Session() as session:
        rows = session.query(LLMPlatform).filter_by(is_sys=1).all()
        assert [(row.id, row.name, row.platform_key) for row in rows] == [
            (platform.id, "历史同步平台", legacy_database_platform_key(platform.id)),
        ]


def test_import_keys_are_indexed_by_platform_key(manager: AIManager) -> None:
    """上传的新格式密钥按平台 key 写入，两个同 URL 平台互不覆盖。"""
    manager.set_llm_key("platform-import-master", persist=False)
    url = "https://import-duplicate.example/v1"
    models = {"模型": {"model_name": "model"}}
    configs = {
        "导入甲": {"platform_key": "import-a", "base_url": url, "models": models},
        "导入乙": {"platform_key": "import-b", "base_url": url, "models": models},
    }
    manager.admin_import_from_yaml(
        configs,
        uploaded_key_data={
            "import-a": {"api_key": "key-a"},
            "import-b": {"api_key": "key-b"},
        },
    )

    with manager.Session() as session:
        rows = session.query(LLMPlatform).filter(LLMPlatform.base_url == url).all()
        assert len(rows) == 2
        values = {
            row.platform_key: manager._get_effective_api_key(session, "-1", row)
            for row in rows
        }
        assert values == {"import-a": "key-a", "import-b": "key-b"}


def test_legacy_url_key_fallback_is_only_used_when_unambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧 URL 密钥仅在配置中只有一个同 URL 平台时回退。"""
    url = "https://legacy.example/v1"
    monkeypatch.setattr(
        matchbox_config,
        "load_key_yaml_raw",
        lambda: {url: {"api_key": "legacy-key"}},
    )

    single = {"唯一平台": {"base_url": url, "models": {}}}
    matchbox_config.merge_key_yaml_into_configs(single)
    assert single["唯一平台"]["api_key"] == "legacy-key"
    assert single["唯一平台"]["platform_key"] == legacy_config_platform_key("唯一平台", url)

    duplicate = {
        "平台甲": {"base_url": url, "models": {}},
        "平台乙": {"base_url": url, "models": {}},
    }
    matchbox_config.merge_key_yaml_into_configs(duplicate)
    assert "api_key" not in duplicate["平台甲"]
    assert "api_key" not in duplicate["平台乙"]


def test_master_key_rotation_keeps_duplicate_url_keys_separate(
    manager: AIManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 URL 平台的密钥轮换按 platform_key 回写，不能互相覆盖。"""
    manager.set_llm_key("old-master", persist=False)
    url = "https://rotate-duplicate.example/v1"
    first = manager.admin_add_sys_platform("轮换甲", url)
    second = manager.admin_add_sys_platform("轮换乙", url)

    key_data = {
        first.platform_key: {"api_key": SecurityManager.encrypt_with_key("key-a", "old-master")},
        second.platform_key: {"api_key": SecurityManager.encrypt_with_key("key-b", "old-master")},
    }
    monkeypatch.setattr("agen_matchbox.manager.load_key_yaml_raw", lambda: key_data)
    manager.rotate_master_key("new-master", old_key="old-master", persist=False)

    assert set(key_data) == {first.platform_key, second.platform_key}
    assert SecurityManager.decrypt_with_key(
        key_data[first.platform_key]["api_key"], "new-master"
    ).value == "key-a"
    assert SecurityManager.decrypt_with_key(
        key_data[second.platform_key]["api_key"], "new-master"
    ).value == "key-b"
