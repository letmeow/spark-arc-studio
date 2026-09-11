"""启动期契约：轻初始化 + 重依赖延迟加载 + 独立性。

从主项目 ``test/architecture/test_matchbox_startup_contracts.py`` 迁移而来，
去除 SparkArc 宿主依赖，改为网关自包含断言。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LLM_DIR = PACKAGE_ROOT.parent


def _run_probe(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LLM_DIR)
    env["AGENT_MATCHBOX_HOME"] = str(tmp_path / "matchbox-home")
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def test_manager_import_keeps_llm_runtime_lazy(tmp_path: Path) -> None:
    code = """
import importlib
import sys

importlib.import_module("agen_matchbox.manager")

for name in (
    "agen_matchbox.gateway",
    "agen_matchbox.tracked_model",
    "langchain_openai",
):
    print(f"{name}={name in sys.modules}")
"""
    output = _run_probe(code, tmp_path)
    assert "agen_matchbox.gateway=False" in output
    assert "agen_matchbox.tracked_model=False" in output
    assert "langchain_openai=False" in output


def test_config_import_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    """import config 不读 YAML、不解密、不打印密钥提示。"""
    code = """
import sys

import agen_matchbox.config as config

print(f"loaded_lazy={not config.DEFAULT_PLATFORM_CONFIGS._loaded}")
print(f"core_loaded={'core' in sys.modules}")
"""
    output = _run_probe(code, tmp_path)
    assert "loaded_lazy=True" in output
    assert "core_loaded=False" in output


def test_initialize_without_host_core(tmp_path: Path) -> None:
    code = """
import sys

from agen_matchbox import initialize_matchbox

manager = initialize_matchbox(ensure_defaults=True)
connection = manager.engine.connect()
try:
    tables = set(manager.engine.dialect.get_table_names(connection))
finally:
    connection.close()
    manager.engine.dispose()

print(f"manager={type(manager).__name__}")
print(f"has_llm_platforms={'llm_platforms' in tables}")
print(f"core_loaded={'core' in sys.modules}")
"""
    output = _run_probe(code, tmp_path)
    assert "manager=AIManager" in output
    assert "has_llm_platforms=True" in output
    assert "core_loaded=False" in output


def test_hf_mirror_does_not_import_host_core(tmp_path: Path) -> None:
    code = """
import sys

from agen_matchbox import hf_mirror

print(f"candidates={len(hf_mirror.get_hf_candidates(probe=False))}")
print(f"core_loaded={'core' in sys.modules}")
"""
    output = _run_probe(code, tmp_path)
    assert "candidates=2" in output
    assert "core_loaded=False" in output
