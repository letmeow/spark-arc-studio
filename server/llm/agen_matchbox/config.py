"""
配置管理模块
负责加载 YAML 配置文件和管理常量
"""

from copy import deepcopy
from pathlib import Path
import os
import re
import shutil
import yaml
from typing import Dict, Any, Optional

from .env_utils import load_env, get_env_var, get_env_path
from .paths import get_config_file_path, get_key_file_path, get_packaged_config_template_path, ensure_mgr_home_exists
from .security import SecurityManager
from .platform_identity import legacy_config_platform_key, normalize_platform_key
from .utils import normalize_base_url


_API_KEY_PLACEHOLDER_RE = re.compile(r"^\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}$")
_STARTUP_KEY_NOTICE_PRINTED = False


# ---------------- 配置常量 ----------------

# 当 user_id = '-1' 时，代表系统运行于无用户/全局单用户模式，也称$系统模式$
# 这是一个虚拟的系统用户，从环境变量获取apikey，不需要用户自己设置apikey
SYSTEM_USER_ID = "-1"

# 如果为True 则当用户无apikey时 将尝试自动获取服务器apikey密钥
LLM_AUTO_KEY = True 
# 如果为True 则所有用户均使用系统平台配置 不能创建自己的平台和模型
USE_SYS_LLM_CONFIG = False

DEFAULT_USAGE_KEY = "main"
BUILTIN_USAGE_SLOTS = [
    {"key": DEFAULT_USAGE_KEY, "label": "主模型"},
    {"key": "fast", "label": "快速模型"},
    {"key": "reason", "label": "推理模型"},
]


# ---------------- 配置加载 ----------------

def _safe_decrypt(sec_mgr: SecurityManager, value: str, stats: Optional[Dict[str, int]] = None) -> Any:
    if not value:
        return None
    if value.startswith("ENC:"):
        # 注意：仓库同步下发的 YAML 中可能携带其他环境生成的 ENC 密文。
        # 这类值在新站点首次拉取后无法直接解开属于正常现象；
        # 配置加载层统一将其视为“当前不可用”，等待管理员设置本机 LLM_KEY 并重新配置托管密钥。
        result = sec_mgr.decrypt(value)
        if stats is not None:
            stats[result.status] = stats.get(result.status, 0) + 1
        return result.to_optional_plaintext()
    return value


def _emit_startup_key_notice(stats: Dict[str, int]) -> None:
    """启动期聚合提示密钥状态，避免每个平台逐条刷屏。"""
    global _STARTUP_KEY_NOTICE_PRINTED
    if _STARTUP_KEY_NOTICE_PRINTED:
        return

    missing_key = int(stats.get("missing_key", 0) or 0)
    failed = int(stats.get("failed", 0) or 0)
    if missing_key <= 0 and failed <= 0:
        return

    _STARTUP_KEY_NOTICE_PRINTED = True
    parts = []
    if missing_key:
        parts.append(f"{missing_key} 个托管密钥等待设置 LLM_KEY")
    if failed:
        parts.append(f"{failed} 个托管密钥需要重新配置")

    print(
        "🔑 Matchbox key notice: "
        + "，".join(parts)
        + "。这在首次克隆或迁移环境时通常是正常现象；平台/模型结构会继续同步，"
        + "请在管理后台设置主密钥并重新录入需要使用的平台 API Key。",
        flush=True,
    )


def is_api_key_placeholder(value: Any) -> bool:
    """判断 YAML api_key 是否为 {ENV_VAR} 占位符。"""
    return isinstance(value, str) and bool(_API_KEY_PLACEHOLDER_RE.match(value.strip()))


def resolve_api_key_reference(value: Any) -> Optional[str]:
    """解析 YAML api_key 原始值；若为占位符则读取对应环境变量。"""
    if not isinstance(value, str):
        return None

    raw_value = value.strip()
    if not raw_value:
        return None

    match = _API_KEY_PLACEHOLDER_RE.match(raw_value)
    if not match:
        return raw_value

    env_name = match.group(1)
    env_val = get_env_var(env_name)
    if not isinstance(env_val, str):
        return None

    env_val = env_val.strip()
    return env_val or None


def load_default_platform_configs_raw() -> Dict[str, Any]:
    """从 matchbox_cfg.yaml 加载原始平台配置（不合并 matchbox_key.yaml）。"""
    ensure_mgr_home_exists()
    config_path: Path = get_config_file_path()

    if not config_path.exists():
        template_path = get_packaged_config_template_path()
        if template_path.exists() and template_path != config_path:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template_path, config_path)
        else:
            raise FileNotFoundError(f"LLM_MGR:预设平台配置文件 '{config_path}' 不存在，请手动创建 matchbox_cfg.yaml")

    with config_path.open("r", encoding="utf-8") as f:
        configs = yaml.safe_load(f) or {}

    if not isinstance(configs, dict):
        raise ValueError("matchbox_cfg.yaml 顶层结构必须是字典")

    return configs


def load_key_yaml_raw() -> Dict[str, Any]:
    """从 matchbox_key.yaml 加载原始密钥配置。

    新结构使用 platform_key 作为唯一键：
        platform-0123456789abcdef:
          api_key: sk-xxx

    旧结构仍可读取（使用 base_url 作为键）：
        https://other.example.com/v1:
          api_key: ENC:...

    也兼容简写形式：
        https://api.example.com/v1: sk-xxx

    文件不存在时返回空字典，表示没有外部密钥文件。
    """
    key_path: Path = get_key_file_path()
    if not key_path.exists():
        return {}

    with key_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError("matchbox_key.yaml 顶层结构必须是字典")

    return data


def _extract_api_key_from_key_entry(key_entry: Any) -> Optional[str]:
    """从 matchbox_key.yaml 中单个平台的条目提取 api_key 原始字符串。"""
    if isinstance(key_entry, str):
        raw = key_entry.strip()
        return raw or None
    if isinstance(key_entry, dict):
        raw = key_entry.get("api_key")
        if isinstance(raw, str):
            raw = raw.strip()
            return raw or None
    return None


def merge_key_yaml_into_configs(configs: Dict[str, Any]) -> Dict[str, Any]:
    """将 matchbox_key.yaml 中的 api_key 合并到平台配置字典中（原地修改）。

    匹配逻辑优先使用 platform_key；旧版 URL 键只在同一 URL 只对应一个
    配置时回退，避免两个相同 URL 的平台互相覆盖密钥。
    """
    key_data = load_key_yaml_raw()
    normalized_url_configs: Dict[str, list[tuple[str, Dict[str, Any]]]] = {}
    seen_platform_keys: set[str] = set()

    for name, cfg in configs.items():
        if not isinstance(cfg, dict):
            continue
        base_url = cfg.get("base_url")
        if not base_url:
            continue
        normalized_url = normalize_base_url(str(base_url))
        normalized_url_configs.setdefault(normalized_url, []).append((name, cfg))
        platform_key = normalize_platform_key(cfg.get("platform_key"))
        if platform_key is None:
            platform_key = legacy_config_platform_key(name, normalized_url)
            cfg["platform_key"] = platform_key
        if platform_key in seen_platform_keys:
            raise ValueError(f"配置中存在重复 platform_key: {platform_key}")
        seen_platform_keys.add(platform_key)

    for name, cfg in configs.items():
        if not isinstance(cfg, dict):
            continue
        # 平台结构配置文件中不应再包含 api_key；若存在则忽略。
        cfg.pop("api_key", None)
        base_url = cfg.get("base_url")
        if not base_url:
            continue
        platform_key = cfg.get("platform_key")
        key_entry = key_data.get(platform_key)
        if key_entry is None:
            normalized_url = normalize_base_url(str(base_url))
            candidates = normalized_url_configs.get(normalized_url, [])
            if len(candidates) == 1:
                # 兼容旧版以 URL 为键的密钥文件；重复 URL 时不做猜测。
                for legacy_key, legacy_entry in key_data.items():
                    if not isinstance(legacy_key, str):
                        continue
                    try:
                        if normalize_base_url(legacy_key) == normalized_url:
                            key_entry = legacy_entry
                            break
                    except (AttributeError, TypeError):
                        continue
        key_val = _extract_api_key_from_key_entry(key_entry)
        if key_val is not None:
            cfg["api_key"] = key_val
    return configs


def save_key_yaml_raw(key_data: Dict[str, Any]) -> str:
    """将密钥配置写回 matchbox_key.yaml 文件。"""
    ensure_mgr_home_exists()
    key_path = get_key_file_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with key_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(key_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return str(key_path)


def load_default_platform_configs() -> Dict[str, Any]:
    """从配置文件加载并解析平台配置（缺少 LLM_KEY 也不中断）。

    密钥唯一来源：matchbox_key.yaml 中对应平台 platform_key 的 api_key。
    matchbox_cfg.yaml 中内嵌的 api_key 已被废弃，不再读取。
    """
    configs = deepcopy(load_default_platform_configs_raw())
    merge_key_yaml_into_configs(configs)

    sec_mgr = SecurityManager.get_instance()
    decrypt_stats: Dict[str, int] = {}

    for name, cfg in configs.items():
        api_val = cfg.get("api_key")
        if not isinstance(api_val, str) or api_val.strip() == "":
            cfg["api_key"] = None
            continue

        api_val = api_val.strip()
        # 情况1: 已加密值
        if api_val.startswith("ENC:"):
            cfg["api_key"] = _safe_decrypt(sec_mgr, api_val, decrypt_stats)
            continue

        # 情况2: 占位符 {ENV_VAR}
        if is_api_key_placeholder(api_val):
            env_val = resolve_api_key_reference(api_val)
            if env_val:
                cfg["api_key"] = _safe_decrypt(sec_mgr, env_val, decrypt_stats)
            else:
                cfg["api_key"] = None
            continue

        # 情况3: 纯明文
        cfg["api_key"] = api_val

    _emit_startup_key_notice(decrypt_stats)
    return configs


def reload_default_platform_configs() -> Dict[str, Any]:
    """重新加载平台配置，并原地更新默认配置字典"""
    global DEFAULT_PLATFORM_CONFIGS
    new_configs = load_default_platform_configs()
    if isinstance(DEFAULT_PLATFORM_CONFIGS, dict):
        # _LazyPlatformConfigs.clear() 会标记已加载，避免 clear 触发一次多余读盘。
        DEFAULT_PLATFORM_CONFIGS.clear()
        # dict.update 会触发 _ensure_loaded，但此时已是已加载状态，不会重复读盘。
        dict.update(DEFAULT_PLATFORM_CONFIGS, new_configs)
    else:
        DEFAULT_PLATFORM_CONFIGS = new_configs
    return DEFAULT_PLATFORM_CONFIGS


def _ensure_env_setup():
    """在加载配置前检查环境"""
    # 首先加载 .env 文件
    load_env()

    key = get_env_var("LLM_KEY")
            
    if not key:
        print(
            f"🔑 Matchbox LLM_KEY is not configured yet. First startup can continue; set it later via admin page or {get_env_path()}.",
            flush=True,
        )
        return


def get_decrypted_api_key(
    platform_name: str = None,
    base_url: str = None,
    platform_key: str = None,
):
    """
    获取系统平台配置中的 API Key（已解密）。
    支持通过 platform_key、平台名称或 Base URL 查找。
    供外部工具或 Agent 脚本直接获取特定平台的 Key，也供 AIManager 内部使用。
    """
    if platform_key:
        normalized_key = normalize_platform_key(platform_key)
        for cfg in DEFAULT_PLATFORM_CONFIGS.values():
            if cfg.get("platform_key") == normalized_key:
                return cfg.get("api_key")

    # Base URL 仅在匹配不歧义时使用，避免重复 URL 平台取错密钥。
    if base_url:
        normalized_url = normalize_base_url(base_url)
        matches = [
            cfg for cfg in DEFAULT_PLATFORM_CONFIGS.values()
            if isinstance(cfg, dict) and cfg.get("base_url")
            and normalize_base_url(str(cfg.get("base_url"))) == normalized_url
        ]
        if len(matches) == 1:
            return matches[0].get("api_key")
        return None
    
    # 其次匹配名称
    if platform_name:
        cfg = DEFAULT_PLATFORM_CONFIGS.get(platform_name)
        if cfg:
            return cfg.get("api_key")
            
    return None


class _LazyPlatformConfigs(dict):
    """延迟加载的系统后备平台配置。

    ``import config`` 不再触碰文件系统；首次读取/写入时才解析
    ``matchbox_cfg.yaml`` + ``matchbox_key.yaml`` + 环境变量。
    """

    def __init__(self) -> None:
        super().__init__()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        super().clear()
        super().update(load_default_platform_configs())

    def __getitem__(self, key):
        self._ensure_loaded()
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        self._ensure_loaded()
        return super().__setitem__(key, value)

    def __delitem__(self, key):
        self._ensure_loaded()
        return super().__delitem__(key)

    def __iter__(self):
        self._ensure_loaded()
        return super().__iter__()

    def __len__(self):
        self._ensure_loaded()
        return super().__len__()

    def __contains__(self, key):
        self._ensure_loaded()
        return super().__contains__(key)

    def __repr__(self):
        if not self._loaded:
            return "DEFAULT_PLATFORM_CONFIGS(<lazy, not loaded yet>)"
        return super().__repr__()

    def get(self, key, default=None):
        self._ensure_loaded()
        return super().get(key, default)

    def keys(self):
        self._ensure_loaded()
        return super().keys()

    def values(self):
        self._ensure_loaded()
        return super().values()

    def items(self):
        self._ensure_loaded()
        return super().items()

    def copy(self):
        self._ensure_loaded()
        return dict(super().copy())

    def clear(self):
        # reload 场景会 clear 后 update，此时视为已加载，避免重复读盘。
        self._loaded = True
        return super().clear()

    def update(self, *args, **kwargs):
        self._ensure_loaded()
        return super().update(*args, **kwargs)

    def setdefault(self, key, default=None):
        self._ensure_loaded()
        return super().setdefault(key, default)

    def pop(self, key, *args):
        self._ensure_loaded()
        return super().pop(key, *args)

    def popitem(self):
        self._ensure_loaded()
        return super().popitem()


# 模块加载时只做环境检查，不读 YAML、不解密、不打印密钥提示。
# 首次访问 DEFAULT_PLATFORM_CONFIGS 时才真正加载（见 _LazyPlatformConfigs）。
_ensure_env_setup()
DEFAULT_PLATFORM_CONFIGS = _LazyPlatformConfigs()
