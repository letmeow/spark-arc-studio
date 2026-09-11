"""
AIManager 核心实现
集成所有管理功能模块

⚠️ 重要说明：系统平台配置的两种数据源策略
------------------------------------------
1. YAML 文件 (matchbox_cfg.yaml)
   - 作用：初始化模板、配置分发分享、跨环境/设备快速迁移、提供基础模型参考，也作为项目作者及时向站长们同步最新模型的方式。只需要拉取最新仓库即可增量同步。
   - 特点：仅在首次建库时同步到数据库；也供管理员手动更新和分享配置清单（非热修改）
   - 当目前由于系统平台及模型可以在界面可视化管理，因此我们鼓励尽量仅将 YAML 当作 "Init Seed"
   - 提供功能支持（如 admin_reload_from_yaml 接口）使平台运维人员能下发新的模型配置

2. 数据库 (llm_config.db)
   - 作用：运行时的唯一权威数据源，所有业务操作强制从此读取
   - 特点：动态支持前端 CRUD 操作、细粒度权限管控、安全加密存储用户的 API keys 和 Admin 配置
   - 任何改动即时生效，无需重启服务
------------------------------------------
"""

import os
import json
import threading
import time
from collections import Counter
from copy import deepcopy
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import sessionmaker, selectinload

from .models import (
    Base,
    LLMPlatform, LLModels, LLMSysPlatformKey,
    UserModelUsage, AgentModelBinding, ModelUsageStats, UserEmbeddingSelection,
    DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL_INPUT_MODALITIES, DEFAULT_MODEL_OUTPUT_MODALITIES,
    MODALITY_IMAGE,
    get_model_modalities,
    is_chat_model,
    model_sort_key,
)
from .config import (
    DEFAULT_PLATFORM_CONFIGS, SYSTEM_USER_ID, DEFAULT_USAGE_KEY,
    BUILTIN_USAGE_SLOTS, USE_SYS_LLM_CONFIG, LLM_AUTO_KEY,
    get_decrypted_api_key,  # Still kept for backwards compatibility / internal CLI scripts if needed
    load_default_platform_configs_raw,
    load_key_yaml_raw,
    merge_key_yaml_into_configs,
    reload_default_platform_configs,
    resolve_api_key_reference,
    is_api_key_placeholder,
)
from .env_utils import get_env_var
from .image_adapters import (
    DEFAULT_IMAGE_GENERATION_ADAPTER,
    normalize_image_generation_adapter,
    strip_internal_image_generation_fields,
)
from .paths import get_db_file_path, get_state_file_path, get_config_file_path, get_key_file_path, get_mgr_home
from .security import SecurityManager
from .utils import normalize_base_url, normalize_recharge_url
from .database import create_configured_engine, normalize_database_url
from .integrations import MatchboxIntegrations
from .platform_identity import (
    generate_platform_key,
    is_legacy_database_platform_key,
    legacy_config_platform_key,
    legacy_database_platform_key,
    normalize_platform_key,
)

from .admin import AdminMixin
from .user_services import UserServicesMixin
from .builder import LLMBuilderMixin
from .credit_services import CreditServicesMixin
from .credit_grant_services import CreditGrantServicesMixin
from .quota_services import QuotaServicesMixin
from .usage_services import UsageServicesMixin
from .redeem_code_services import RedeemCodeServicesMixin


class MasterKeyMigrationRequiredError(RuntimeError):
    """存在历史密钥需要旧主密钥迁移或显式清除。"""

    def __init__(self, unresolved_count: int, sample_labels: Optional[List[str]] = None):
        self.unresolved_count = int(unresolved_count)
        self.sample_labels = list(sample_labels or [])
        sample_text = ""
        if self.sample_labels:
            sample_text = f" 示例: {', '.join(self.sample_labels)}"
        guidance_text = (
            "\n\n1.如果这些密钥来自仓库同步下发的 YAML、他人分享，当前无法解密通常属于正常现象。\n"
            "你可以直接点击确认清除这些无效的占位密钥。\n"
            "2.如果这些密钥来自你的配置文件，请提供之前使用的主密钥，以验证身份。\n"
            "清理仅会移除不可用的托管 API Key，不会删除平台和模型结构。"
        )
        super().__init__(
            f"存在 {self.unresolved_count} 项历史密钥无法用当前新主密钥解密，"
            f"请提供旧主密钥进行迁移，或明确确认清除这些历史密钥。{sample_text}{guidance_text}"
        )


class AIManagerBase:
    """AIManager 基础类：数据库连接和初始化"""
    
    def __init__(
        self,
        db_name: str = "llm_config.db",
        *,
        engine=None,
        database_url: Optional[str] = None,
        engine_factory=None,
        integrations: Optional[MatchboxIntegrations] = None,
    ):
        db_path = get_db_file_path(db_name)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._integrations = integrations or MatchboxIntegrations()
        if engine is not None:
            self.engine = engine
        else:
            db_url = database_url or normalize_database_url(
                env_key="AGENT_MATCHBOX_DATABASE_URL",
                default_sqlite_path=db_path,
            )
            factory = engine_factory or create_configured_engine
            self.engine = factory(db_url, future=True)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._sys_platforms_cache = None 
        self._cache_lock = threading.Lock()
        self._sys_platforms_cache_at = 0.0
        self._sys_platforms_cache_ttl = float(os.getenv("LLM_SYS_PLATFORM_CACHE_TTL", "5"))
        self.use_sys_llm_config = USE_SYS_LLM_CONFIG
        self.llm_auto_key = LLM_AUTO_KEY
        self.billing_enabled = False
        self._default_platform_id = None
        self._default_model_id = None
        self._builtin_usage_map = {slot["key"]: slot for slot in BUILTIN_USAGE_SLOTS}
        self._default_usage_key = DEFAULT_USAGE_KEY
        self._default_usage_key_resolver = self._integrations.default_usage_key_resolver
        self._caller_context_provider = self._integrations.caller_context_provider
        self._usage_context_provider = self._integrations.usage_context_provider
        self._usage_recorded_handler = self._integrations.usage_recorded_handler
        self._secret_rotation_handler = self._integrations.secret_rotation_handler
        if getattr(self._integrations, "prompt_cache_context_reader", None) is not None:
            from .integrations import set_prompt_cache_context_reader

            set_prompt_cache_context_reader(self._integrations.prompt_cache_context_reader)
        
        state_file_path = get_state_file_path()
        state_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_file = str(state_file_path)
        self._load_state()

    def ensure_schema(self) -> None:
        """在没有独立迁移系统的环境中创建 Matchbox 自有表。"""
        Base.metadata.create_all(self.engine)
        self.ensure_platform_identity()

    def ensure_platform_identity(self) -> None:
        """为历史平台补齐稳定身份，供迁移后及轻量初始化链路复用。"""
        with self.Session() as session:
            self._ensure_platform_keys(session)
            session.commit()

    @staticmethod
    def _ensure_platform_keys(session) -> None:
        """为历史平台补齐身份标识，并修复意外的重复值。"""
        platforms = session.query(LLMPlatform).order_by(LLMPlatform.id).all()
        seen: set[str] = set()
        changed = False
        for platform in platforms:
            try:
                platform_key = normalize_platform_key(platform.platform_key)
            except ValueError:
                platform_key = None
            if platform_key is None or platform_key in seen:
                platform_key = legacy_database_platform_key(platform.id)
                suffix = 1
                while platform_key in seen:
                    platform_key = f"{legacy_database_platform_key(platform.id)}-{suffix}"
                    suffix += 1
            if platform.platform_key != platform_key:
                platform.platform_key = platform_key
                changed = True
            seen.add(platform_key)
        if changed:
            session.flush()

    def _load_state(self):
        """加载运行时状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    # 仅覆盖允许动态修改的配置
                    if "use_sys_llm_config" in state:
                        self.use_sys_llm_config = state["use_sys_llm_config"]
                    if "llm_auto_key" in state:
                        self.llm_auto_key = state["llm_auto_key"]
                    if "billing_enabled" in state:
                        self.billing_enabled = bool(state["billing_enabled"])
            except Exception as e:
                print(f"Failed to load state: {e}")

    def _save_state(self):
        """保存运行时状态"""
        try:
            state = {
                "use_sys_llm_config": self.use_sys_llm_config,
                "llm_auto_key": self.llm_auto_key,
                "billing_enabled": self.billing_enabled,
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Failed to save state: {e}")

    def _resolve_default_ids_from_db(self, session) -> None:
        """从数据库 sort_order 确定默认平台 ID 和默认模型 ID。

        优先级：
        1. 数据库中 sort_order 最小且存在可用文本模型的未禁用系统平台
        2. 该平台内 sort_order 最小的未禁用文本生成模型
        """
        default_plats = (
            session.query(LLMPlatform)
            .options(selectinload(LLMPlatform.models))
            .filter_by(is_sys=1, disable=0)
            .order_by(LLMPlatform.sort_order, LLMPlatform.id)
            .all()
        )
        if not default_plats:
            raise RuntimeError("数据库中没有可用的系统平台")

        for default_plat in default_plats:
            sorted_models = sorted(default_plat.models, key=model_sort_key)
            default_model = next(
                (model for model in sorted_models if is_chat_model(model) and not self._is_model_disabled(model)),
                None,
            )
            if default_model:
                self._default_platform_id = default_plat.id
                self._default_model_id = default_model.id
                return

        raise RuntimeError("所有可用的系统平台都没有可用的 LLM 模型")

    def _refresh_runtime_default_ids(self, session) -> None:
        """排序或失效状态变化后，立即刷新当前进程的默认平台和模型。"""
        try:
            self._resolve_default_ids_from_db(session)
        except RuntimeError:
            # 允许管理员先调整空平台或暂时禁用全部模型，后续请求仍可按排序尝试回退。
            self._default_platform_id = None
            self._default_model_id = None

    def initialize_defaults(self):
        """同步默认平台并初始化默认ID"""
        with self.Session() as session:
            self._ensure_platform_keys(session)
            session.commit()
        self._sync_default_platforms()

        with self.Session() as session:
            self._resolve_default_ids_from_db(session)

        with self.Session() as session:
            self.ensure_user_has_config(session, SYSTEM_USER_ID)

    # -- YAML 种子同步算法已抽离到 seed_sync.py；此处保留薄兼容封装 ----
    # 新代码请直接使用 seed_sync 模块，便于单测与复用。
    @staticmethod
    def _resolve_seed_model_limits(model_config: Any) -> tuple[int, int]:
        """解析 YAML 模型配置中的上下文与输出上限（兼容封装）。"""
        from .seed_sync import resolve_seed_model_limits

        return resolve_seed_model_limits(
            model_config,
            default_max_context=DEFAULT_MAX_CONTEXT_TOKENS,
            default_max_output=DEFAULT_MAX_OUTPUT_TOKENS,
        )

    def _build_seed_model_specs(self, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """将 YAML 平台模型配置统一展开为内部规格列表（兼容封装）。"""
        from .seed_sync import build_seed_model_specs

        return build_seed_model_specs(
            cfg,
            default_max_context=DEFAULT_MAX_CONTEXT_TOKENS,
            default_max_output=DEFAULT_MAX_OUTPUT_TOKENS,
        )

    @staticmethod
    def _match_seed_models_to_db(
        seed_models: List[Dict[str, Any]],
        db_models_pool: List[LLModels],
    ) -> tuple[Dict[int, LLModels], set[int]]:
        """用统一四阶段策略匹配 YAML 模型规格与数据库既有模型（兼容封装）。"""
        from .seed_sync import match_seed_models_to_db

        return match_seed_models_to_db(seed_models, db_models_pool)

    @staticmethod
    def _apply_seed_model_update(model: LLModels, spec: Dict[str, Any], *, reset_mode: bool) -> None:
        """把 YAML 模型规格写回已有数据库模型（兼容封装）。"""
        from .seed_sync import apply_seed_model_update

        return apply_seed_model_update(model, spec, reset_mode=reset_mode)

    @staticmethod
    def _create_seed_model(platform_id: int, spec: Dict[str, Any], *, sort_order: int) -> LLModels:
        """根据 YAML 模型规格创建数据库模型对象（兼容封装）。"""
        from .seed_sync import create_seed_model

        return create_seed_model(platform_id, spec, sort_order=sort_order)

    def _sync_seed_models_for_platform(
        self,
        session,
        plat: LLMPlatform,
        platform_name: str,
        cfg: Dict[str, Any],
        *,
        reset_mode: bool,
    ) -> None:
        """同步单个平台的 YAML 模型配置（兼容封装）。"""
        from .seed_sync import sync_seed_models_for_platform

        return sync_seed_models_for_platform(
            session,
            plat,
            platform_name,
            cfg,
            reset_mode=reset_mode,
            default_max_context=DEFAULT_MAX_CONTEXT_TOKENS,
            default_max_output=DEFAULT_MAX_OUTPUT_TOKENS,
        )

    @staticmethod
    def _resolve_seed_platform_key(platform_name: str, cfg: Dict[str, Any]) -> str:
        """解析 YAML 平台身份；旧配置按名称和规范化 URL 确定性兼容。"""
        platform_key = normalize_platform_key(cfg.get("platform_key"))
        if platform_key is None:
            platform_key = legacy_config_platform_key(
                platform_name,
                normalize_base_url(str(cfg.get("base_url") or "")),
            )
            cfg["platform_key"] = platform_key
        return platform_key

    @staticmethod
    def _find_seed_platform(
        session,
        *,
        platform_key: str,
        platform_name: str,
        base_url: str,
        allow_legacy_fallback: bool,
    ) -> Optional[LLMPlatform]:
        """按平台 key 查找平台，旧 YAML 仅回退到唯一名称与 URL。"""
        platform = (
            session.query(LLMPlatform)
            .filter_by(platform_key=platform_key, is_sys=1)
            .first()
        )
        if platform is not None:
            return platform
        if not allow_legacy_fallback:
            return None
        candidates = (
            session.query(LLMPlatform)
            .filter_by(name=platform_name, base_url=normalize_base_url(base_url), is_sys=1)
            .order_by(LLMPlatform.id)
            .limit(2)
            .all()
        )
        if len(candidates) > 1:
            raise ValueError(
                f"旧 YAML 平台身份不唯一，请为 {platform_name} 配置明确的 platform_key"
            )
        return candidates[0] if candidates else None

    def _sync_default_platforms(
        self,
        force_reset: bool = False,
        raw_platform_configs: Optional[Dict[str, Any]] = None,
    ):
        """
        同步系统平台配置（仅初始化模式）

        ⚠️ 数据源说明：
        - YAML 文件 (matchbox_cfg.yaml): 初始化模板，便于配置分享和版本控制
        - 数据库 (llm_config.db): 运行时权威数据源 (Authority)，修改即时生效。

        同步策略 (三种触发时机):
        1. 首次启动 (First Initialization):
           - 触发：数据库为空。
           - 行为：YAML 配置完整初始化到数据库。

        2. 增量同步 (Incremental Sync):
           - 触发：后续启动 (默认)。
           - 行为：仅添加 YAML 中新增的平台和模型，**不覆盖、不删除**数据库中已有的配置。
           - 目的：保护管理员在数据库模式下所做的自定义修改。

        3. 强制重置 (Force Reset):
           - 触发：GUI "从配置文件重置" 或 API 调用。
           - 行为：以 YAML 为准重置数据库，软禁用 YAML 中不存在的平台（保留用户 API Key）。

        参数:
            force_reset: 是否强制从配置文件重置系统平台配置
            raw_platform_configs: 可选的外部传入配置；未提供时读取 matchbox_cfg.yaml + matchbox_key.yaml。
        """
        sec_mgr = SecurityManager.get_instance()
        seed_key_stats: Dict[str, int] = {}

        def _prepare_seed_api_key(value: Optional[str]) -> Optional[str]:
            if not isinstance(value, str):
                return None

            raw_value = value.strip()
            if not raw_value:
                return None

            if is_api_key_placeholder(raw_value):
                raw_value = resolve_api_key_reference(raw_value)
                if not raw_value:
                    return None

            if SecurityManager.is_encrypted_value(raw_value):
                if sec_mgr.has_active_key():
                    plain_result = sec_mgr.decrypt(raw_value)
                    seed_key_stats[plain_result.status] = seed_key_stats.get(plain_result.status, 0) + 1
                    if plain_result.has_plaintext:
                        return sec_mgr.encrypt(plain_result.value)
                else:
                    seed_key_stats["missing_key"] = seed_key_stats.get("missing_key", 0) + 1
                return None

            if not sec_mgr.has_active_key():
                raise ValueError("检测到 YAML 中存在明文 API Key，但当前未设置 LLM_KEY，拒绝将明文密钥写入数据库")

            return sec_mgr.encrypt(raw_value)

        if raw_platform_configs is None:
            raw_platform_configs = load_default_platform_configs_raw()
            # merge_key_yaml_into_configs 会在内存中补充兼容 key，显式性必须在合并前判断。
            explicit_platform_keys = {
                name: (
                    isinstance(cfg, dict)
                    and normalize_platform_key(cfg.get("platform_key")) is not None
                )
                for name, cfg in raw_platform_configs.items()
            }
            # 从 matchbox_key.yaml 合并各平台 api_key；上传/结构配置文件中的内嵌 api_key 会被忽略。
            merge_key_yaml_into_configs(raw_platform_configs)
        else:
            explicit_platform_keys = {
                name: (
                    isinstance(cfg, dict)
                    and normalize_platform_key(cfg.get("platform_key")) is not None
                )
                for name, cfg in raw_platform_configs.items()
            }

        with self.Session() as session:
            self._ensure_platform_keys(session)
            config_platform_keys = set()
            for name, cfg in raw_platform_configs.items():
                if isinstance(cfg, dict) and cfg.get("base_url"):
                    platform_key = self._resolve_seed_platform_key(name, cfg)
                    if platform_key in config_platform_keys:
                        raise ValueError(f"配置中存在重复 platform_key: {platform_key}")
                    config_platform_keys.add(platform_key)
            all_sys_platforms = session.query(LLMPlatform).filter_by(is_sys=1).all()
            # 已被管理员禁用的平台 key 集合；相同 URL 的其他平台不受影响。
            # 检查是否为首次初始化（数据库中没有任何系统平台）
            is_first_init = len(all_sys_platforms) == 0

            if force_reset:
                # 强制重置模式：禁用所有不在 YAML 中的平台（软禁用，不硬删除）
                for plat in all_sys_platforms:
                    if plat.platform_key not in config_platform_keys:
                        print(f"[yaml-reset] Disabling removed system platform: {plat.name} ({plat.platform_key})")
                        plat.disable = 1
                session.flush()
            
            for plat_idx, (name, cfg) in enumerate(raw_platform_configs.items()):
                if not isinstance(cfg, dict) or "base_url" not in cfg:
                    continue
                base_url = normalize_base_url(str(cfg["base_url"]))
                has_explicit_platform_key = explicit_platform_keys.get(name, False)
                platform_key = self._resolve_seed_platform_key(name, cfg)
                recharge_url = normalize_recharge_url(cfg.get("recharge_url"))
                plat = self._find_seed_platform(
                    session,
                    platform_key=platform_key,
                    platform_name=name,
                    base_url=base_url,
                    allow_legacy_fallback=not has_explicit_platform_key,
                )

                if not plat:
                    # 新平台：添加到数据库；同 URL 的其他平台不会被复用。
                    encrypted_key = _prepare_seed_api_key(cfg.get("api_key"))
                    plat = LLMPlatform(
                        name=name,
                        platform_key=platform_key,
                        base_url=base_url,
                        recharge_url=recharge_url,
                        api_key=encrypted_key,  # matchbox_key.yaml 中若有密钥则加密写入
                        user_id=SYSTEM_USER_ID,
                        is_sys=1,
                        sort_order=plat_idx,
                    )
                    session.add(plat)
                    session.flush()
                    print(f"[init] Adding new system platform: {name}")
                    self._sync_seed_models_for_platform(session, plat, name, cfg, reset_mode=True)

                elif force_reset or is_first_init:
                    # 强制重置或首次初始化：更新平台名称和同步模型
                    if plat.name != name:
                        print(f"[yaml-reset] Restoring system platform name: {plat.name} -> {name}")
                        plat.name = name

                    # YAML 明确保留该身份时允许复活该平台；不会按 URL 复活其他平台。
                    plat.disable = 0

                    # 按 YAML 顺序同步 sort_order
                    plat.sort_order = plat_idx
                    plat.recharge_url = recharge_url

                    # 若 matchbox_key.yaml 提供 API Key，则更新平台默认 Key（加密写入）
                    encrypted_key = _prepare_seed_api_key(cfg.get("api_key"))
                    if encrypted_key:
                        plat.api_key = encrypted_key

                    self._sync_seed_models_for_platform(session, plat, name, cfg, reset_mode=True)

                else:
                    # 正常启动增量更新模式：自动同步平台名称（若有变动）。
                    if plat.name != name:
                        print(f"[incremental-sync] Updating system platform name: {plat.name} -> {name}")
                        plat.name = name

                    if "recharge_url" in cfg:
                        plat.recharge_url = recharge_url

                    self._sync_seed_models_for_platform(session, plat, name, cfg, reset_mode=False)

            session.commit()
            self._invalidate_sys_platforms_cache()

        missing_key = int(seed_key_stats.get("missing_key", 0) or 0)
        failed = int(seed_key_stats.get("failed", 0) or 0)
        if missing_key or failed:
            parts = []
            if missing_key:
                parts.append(f"{missing_key} 个密钥等待 LLM_KEY")
            if failed:
                parts.append(f"{failed} 个密钥需要重新配置")
            print(
                "[init] YAML 托管密钥未全部导入："
                + "，".join(parts)
                + "；平台/模型结构已保留。",
                flush=True,
            )

    def _plan_secret_rewrite(
        self,
        raw_value: Optional[str],
        new_key: str,
        old_key: Optional[str] = None,
        allow_clear_unrecoverable: bool = False,
    ) -> Dict[str, Any]:
        """为单个密钥值生成迁移计划，不直接落库。"""
        if not isinstance(raw_value, str):
            return {"action": "skip", "value": None, "changed": False, "summary": None}

        text = raw_value.strip()
        if not text:
            return {"action": "skip", "value": None, "changed": False, "summary": None}

        if is_api_key_placeholder(text):
            return {"action": "skip", "value": text, "changed": False, "summary": None}

        if not SecurityManager.is_encrypted_value(text):
            return {
                "action": "write",
                "value": SecurityManager.encrypt_with_key(text, new_key),
                "changed": True,
                "summary": "encrypted_plaintext",
            }

        decrypted_with_new = SecurityManager.decrypt_with_key(text, new_key)
        if decrypted_with_new.has_plaintext:
            # Fernet 每次加密都会引入随机因子，
            # 同一明文在同一主密钥下也会生成不同密文。
            # 若当前密文已可被新主密钥解密，则视为有效，不做重写，
            # 避免 GUI 启动时产生无意义的 YAML/DB 脏变更。
            return {"action": "skip", "value": text, "changed": False, "summary": None}
        
        if old_key:
            decrypted_with_old = SecurityManager.decrypt_with_key(text, old_key)
            if decrypted_with_old.has_plaintext:
                return {
                    "action": "write",
                    "value": SecurityManager.encrypt_with_key(decrypted_with_old.value, new_key),
                    "changed": True,
                    "summary": "rotated_with_old_key",
                }

        if allow_clear_unrecoverable:
            return {
                "action": "write",
                "value": None,
                "changed": True,
                "summary": "cleared_unrecoverable",
            }

        return {"action": "unresolved", "value": text, "changed": False, "summary": None}

    def set_llm_key(self, key: str, persist: bool = True) -> None:
        """设置主密钥 LLM_KEY 的原子操作。

        更新进程内加密组件、环境变量、.env 文件（可选），并刷新平台配置缓存。
        若 .env 文件不存在会自动创建。

        调用场景：
        - 首次部署，尚无 .env 和历史密文 → 直接调用本方法即可完成初始化。
        - 外部服务层只需"写入 key 并立刻生效"，不涉及历史密钥迁移。

        不适用场景：
        - 已有 ENC: 密文存储在数据库或 YAML 中，需要更换主密钥 →
          必须使用 rotate_master_key()，它会在全量迁移密文后内部调用本方法。

        Args:
            key: 新的主密钥，不能为空。
            persist: 是否持久化到 .env 文件（默认 True）。

        Raises:
            ValueError: key 为空时抛出。
        """
        key = str(key or "").strip()
        if not key:
            raise ValueError("主密钥不能为空")

        SecurityManager.get_instance().set_key(key, persist=persist)
        self._invalidate_sys_platforms_cache()

    def rotate_master_key(
        self,
        new_key: str,
        old_key: Optional[str] = None,
        persist: bool = True,
        allow_clear_unrecoverable: bool = False,
    ) -> Dict[str, int]:
        """统一的主密钥设置/换密入口，负责 YAML 与数据库中的全部密钥迁移。

        调用场景：
        - 已有 ENC: 密文存储在数据库或 YAML 中，需要更换主密钥 → 必须使用本方法。
        - 不确定是否存在历史密文时，也应使用本方法（安全兜底）。
        - 首次部署无历史密文时同样可用（全量扫描后直接落地，等价于 set_llm_key）。

        与 set_llm_key() 的关系：
        - 本方法是上层编排，完成全量密文迁移后内部调用 set_llm_key() 落地。
        - set_llm_key() 是底层原子操作，不扫描不迁移，仅写入 key 并生效。

        Args:
            new_key: 新的主密钥，不能为空。
            old_key: 旧主密钥，用于解密历史密文。省略时自动从 .env 读取当前值。
            persist: 是否持久化到 .env 文件（默认 True）。
            allow_clear_unrecoverable: 无法解密的历史密文是否允许直接清除（默认 False）。

        Returns:
            迁移统计摘要，各类型变更数量。

        Raises:
            ValueError: new_key 为空时抛出。
            MasterKeyMigrationRequiredError: 存在无法解密的历史密文且未允许清除时抛出，
                调用方可提示用户提供旧密钥或确认清除后重试。
        """
        new_key = str(new_key or "").strip()
        old_key = str(old_key or "").strip() or None
        if not new_key:
            raise ValueError("新主密钥不能为空")

        if old_key is None:
            current_key = str(get_env_var("LLM_KEY") or "").strip()
            if current_key and current_key != new_key:
                old_key = current_key

        # 平台结构来自 matchbox_cfg.yaml，密钥独立存放在 matchbox_key.yaml。
        key_yaml_data = load_key_yaml_raw()
        rewrite_jobs = []
        unresolved_labels: List[str] = []
        summary: Dict[str, int] = {
            "encrypted_plaintext": 0,
            "rotated_with_old_key": 0,
            "cleared_unrecoverable": 0,
        }

        with self.Session() as session:
            for plat in session.query(LLMPlatform).all():
                plan = self._plan_secret_rewrite(
                    raw_value=plat.api_key,
                    new_key=new_key,
                    old_key=old_key,
                    allow_clear_unrecoverable=allow_clear_unrecoverable,
                )
                if plan["action"] == "unresolved":
                    unresolved_labels.append(f"DB平台:{plat.name}")
                    continue
                if plan["action"] == "write":
                    rewrite_jobs.append(("db_platform", plat, plan))

            for cred in session.query(LLMSysPlatformKey).all():
                plan = self._plan_secret_rewrite(
                    raw_value=cred.api_key,
                    new_key=new_key,
                    old_key=old_key,
                    allow_clear_unrecoverable=allow_clear_unrecoverable,
                )
                if plan["action"] == "unresolved":
                    unresolved_labels.append(f"DB系统平台用户Key:{cred.user_id}:{cred.platform_id}")
                    continue
                if plan["action"] == "write":
                    rewrite_jobs.append(("db_sys_key", cred, plan))

            if self._secret_rotation_handler is not None:
                self._secret_rotation_handler(
                    session=session,
                    plan_secret_rewrite=self._plan_secret_rewrite,
                    new_key=new_key,
                    old_key=old_key,
                    allow_clear_unrecoverable=allow_clear_unrecoverable,
                    add_unresolved=unresolved_labels.append,
                    add_rewrite=lambda apply_change, plan: rewrite_jobs.append(
                        ("external", apply_change, plan)
                    ),
                )

            for platform_key, key_entry in key_yaml_data.items():
                if isinstance(key_entry, dict):
                    key_val = key_entry.get("api_key")
                elif isinstance(key_entry, str):
                    key_val = key_entry
                else:
                    continue
                plan = self._plan_secret_rewrite(
                    raw_value=key_val,
                    new_key=new_key,
                    old_key=old_key,
                    allow_clear_unrecoverable=allow_clear_unrecoverable,
                )
                if plan["action"] == "unresolved":
                    unresolved_labels.append(f"KEY平台:{platform_key}")
                    continue
                if plan["action"] == "write":
                    rewrite_jobs.append(("key_yaml", (platform_key, key_yaml_data), plan))

            if unresolved_labels:
                raise MasterKeyMigrationRequiredError(len(unresolved_labels), unresolved_labels[:3])

            key_yaml_changed = False
            for job_type, target, plan in rewrite_jobs:
                if not plan.get("changed"):
                    continue

                summary_key = plan.get("summary")
                if summary_key:
                    summary[summary_key] += 1

                if job_type == "db_platform":
                    target.api_key = plan["value"]
                elif job_type == "db_sys_key":
                    target.api_key = plan["value"]
                elif job_type == "external":
                    target()
                elif job_type == "key_yaml":
                    platform_key, key_data = target
                    key_entry = key_data.get(platform_key)
                    if plan["value"]:
                        if isinstance(key_entry, dict):
                            key_entry["api_key"] = plan["value"]
                        else:
                            key_data[platform_key] = {"api_key": plan["value"]}
                    else:
                        if isinstance(key_entry, dict):
                            key_entry.pop("api_key", None)
                        elif platform_key in key_data:
                            del key_data[platform_key]
                    key_yaml_changed = True

            if key_yaml_changed:
                from .config import save_key_yaml_raw
                save_key_yaml_raw(key_yaml_data)

            session.commit()

        self.set_llm_key(new_key, persist=persist)
        return summary

    def _invalidate_sys_platforms_cache(self):
        with self._cache_lock:
            self._sys_platforms_cache = None
            self._sys_platforms_cache_at = 0.0

    def _refresh_sys_platforms_cache(self, session) -> None:
        """在调用方已持有会话时刷新系统平台缓存（供 AdminMixin 复用）。"""
        with self._cache_lock:
            self._sys_platforms_cache = None
            self._sys_platforms_cache_at = 0.0
            self._get_sys_config(session)

    def _is_sys_platforms_cache_expired(self) -> bool:
        if self._sys_platforms_cache is None:
            return True
        if self._sys_platforms_cache_ttl <= 0:
            return False
        return (time.time() - self._sys_platforms_cache_at) > self._sys_platforms_cache_ttl

    def admin_reload_from_yaml(self) -> bool:
        """
        管理员：从配置文件强制重新加载系统平台配置
        
        ⚠️ 警告：此操作会重置数据库中的系统平台配置
        - 软禁用 YAML 中不存在的平台
        - 更新已存在平台的名称和模型
        - API Key 不受影响（YAML 中的 api_key 字段被忽略）
        """
        reload_default_platform_configs()
        self._sync_default_platforms(force_reset=True)
        return True

    def admin_sync_from_yaml(self) -> bool:
        """
        管理员：从配置文件增量同步系统平台配置。

        行为与启动期同步一致：只添加/更新 YAML 中的新平台、新模型和模型元数据，
        不删除或禁用数据库中已有的平台与模型。
        """
        reload_default_platform_configs()
        self._sync_default_platforms(force_reset=False)
        self._invalidate_sys_platforms_cache()
        return True

    def admin_build_export_data(self) -> Dict[str, Any]:
        """
        管理员：从数据库提取当前系统平台配置，返回可序列化的字典。

        纯数据层，不涉及任何文件 I/O，供以下场景复用：
        - 写入文件（admin_save_to_yaml）
        - 直接返回给前端作 JSON/YAML 响应
        - 下载接口（内存直出，无需落盘）
        """
        from .models import LLMPlatform

        export_data: Dict[str, Any] = {}

        with self.Session() as session:
            platforms = (
                session.query(LLMPlatform)
                .options(selectinload(LLMPlatform.models))
                .filter_by(is_sys=1)
                .order_by(LLMPlatform.sort_order, LLMPlatform.id)
                .all()
            )

            for plat in platforms:
                if bool(plat.disable):
                    continue

                platform_key = normalize_platform_key(plat.platform_key)
                plat_config: Dict[str, Any] = {
                    "base_url": plat.base_url,
                    "models": {}
                }
                # legacy-db-* 依赖本地数据库主键，不具备跨环境配置身份，不写入结构文件。
                if platform_key and not is_legacy_database_platform_key(platform_key):
                    plat_config["platform_key"] = platform_key
                if plat.recharge_url:
                    plat_config["recharge_url"] = plat.recharge_url

                # matchbox_cfg.yaml 不再存放 api_key；密钥统一由 matchbox_key.yaml 管理。
                # 导出时仅保留平台结构，便于安全分享与版本控制。

                for model in sorted(plat.models, key=model_sort_key):
                    if self._is_model_disabled(model):
                        continue

                    modalities = get_model_modalities(model)
                    has_default_modalities = (
                        modalities["input_modalities"] == list(DEFAULT_MODEL_INPUT_MODALITIES)
                        and modalities["output_modalities"] == list(DEFAULT_MODEL_OUTPUT_MODALITIES)
                    )
                    entry: Dict[str, Any] = {
                        "model_name": model.model_name,
                        "max_context_tokens": int(
                            DEFAULT_MAX_CONTEXT_TOKENS
                            if model.max_context_tokens is None
                            else model.max_context_tokens
                        ),
                        "max_output_tokens": int(
                            DEFAULT_MAX_OUTPUT_TOKENS
                            if model.max_output_tokens is None
                            else model.max_output_tokens
                        ),
                    }
                    if not has_default_modalities:
                        entry.update(modalities)
                    image_generation_adapter = normalize_image_generation_adapter(
                        getattr(model, "image_generation_adapter", None)
                    )
                    if MODALITY_IMAGE in modalities["output_modalities"] and image_generation_adapter:
                        entry["image_generation_adapter"] = image_generation_adapter
                    if model.extra_body:
                        try:
                            cleaned_extra_body = strip_internal_image_generation_fields(json.loads(model.extra_body))
                            if cleaned_extra_body:
                                entry["extra_body"] = cleaned_extra_body
                        except Exception:
                            pass
                    if model.temperature is not None:
                        entry["temperature"] = model.temperature
                    if model.sys_credit_input_price_per_million is not None:
                        entry["sys_credit_input_price_per_million"] = model.sys_credit_input_price_per_million
                    if model.sys_credit_cached_input_price_per_million is not None:
                        entry["sys_credit_cached_input_price_per_million"] = model.sys_credit_cached_input_price_per_million
                    if model.sys_credit_output_price_per_million is not None:
                        entry["sys_credit_output_price_per_million"] = model.sys_credit_output_price_per_million
                    plat_config["models"][model.display_name] = entry

                export_data[plat.name] = plat_config

        return export_data

    def admin_build_key_export_data(self) -> Dict[str, Any]:
        """
        管理员：从数据库提取当前系统平台的 API Key，返回可序列化的字典。

        结构（使用 platform_key 作为唯一键）：
            platform-0123456789abcdef:
              api_key: ENC:...

        只导出存在且为加密字符串的系统平台默认 Key；空 Key 不导出。
        """
        export_data: Dict[str, Any] = {}

        with self.Session() as session:
            platforms = (
                session.query(LLMPlatform)
                .filter_by(is_sys=1)
                .order_by(LLMPlatform.sort_order, LLMPlatform.id)
                .all()
            )

            for plat in platforms:
                if bool(plat.disable):
                    continue
                if plat.api_key and isinstance(plat.api_key, str) and plat.api_key.startswith("ENC:"):
                    platform_key = normalize_platform_key(plat.platform_key)
                    if not platform_key or is_legacy_database_platform_key(platform_key):
                        platform_key = legacy_config_platform_key(
                            plat.name,
                            normalize_base_url(plat.base_url),
                        )
                    export_data[platform_key] = {"api_key": plat.api_key}

        return export_data

    def admin_save_to_yaml(self) -> Dict[str, str]:
        """
        管理员：将当前系统平台配置写入（覆盖） matchbox_cfg.yaml，
        同时将系统平台默认 API Key 写入 matchbox_key.yaml。

        ⚠️ 破坏性操作：会完整覆盖两个配置文件。

        返回包含两个文件路径的字典。
        """
        import yaml

        mgr_home = get_mgr_home()
        mgr_home.mkdir(parents=True, exist_ok=True)

        config_path = get_config_file_path()
        key_path = get_key_file_path()

        export_data = self.admin_build_export_data()
        key_data = self.admin_build_key_export_data()

        # allow_unicode=True 确保中文正常显示，不转义为 \uXXXX
        with config_path.open("w", encoding="utf-8") as f:
            yaml.dump(export_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

        with key_path.open("w", encoding="utf-8") as f:
            yaml.dump(key_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

        return {"config_path": str(config_path), "key_path": str(key_path)}

    def admin_export_to_yaml(self) -> Dict[str, str]:
        """
        向后兼容别名：等同于 admin_save_to_yaml()。

        已有调用方（GUI、内部脚本）无需变更。
        新代码请直接调用语义更明确的 admin_save_to_yaml()。
        """
        return self.admin_save_to_yaml()

    def admin_import_from_yaml(
        self,
        configs: Dict[str, Any],
        uploaded_key_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        管理员：从上传的配置文件增量同步系统平台配置。

        行为：
        - 增量模式：只添加新平台/新模型，不删除已有配置。
        - 已有平台的密钥和用户自定义配置不受影响。
        - 若 uploaded_key_data 非空（.matchbox 文件），仅对新平台尝试解密并写入密钥；
          解密失败则静默跳过，在控制台打印警告。
        - 若 uploaded_key_data 为空（.yaml 文件），不处理密钥，新平台需后续手动配置。
        - 操作完成后刷新默认平台配置缓存，确保即时生效。

        返回变更摘要，包含平台/模型变更统计。
        """
        if not isinstance(configs, dict):
            raise ValueError("传入配置必须是字典")

        # 过滤有效平台配置
        raw_platform_configs: Dict[str, Any] = {}
        for name, cfg in configs.items():
            if isinstance(cfg, dict) and "base_url" in cfg:
                raw_platform_configs[name] = deepcopy(cfg)

        # 处理密钥：仅在上传了密钥数据时，为新平台设置密钥
        # 已有平台的密钥由 _sync_default_platforms 增量模式保留，不会被覆盖
        if uploaded_key_data and isinstance(uploaded_key_data, dict):
            sec_mgr = SecurityManager.get_instance()
            merged_count = 0
            skipped_count = 0
            url_counts: Counter[str] = Counter(
                normalize_base_url(str(cfg.get("base_url")))
                for cfg in raw_platform_configs.values()
                if isinstance(cfg, dict) and cfg.get("base_url")
            )
            for name, cfg in raw_platform_configs.items():
                if not isinstance(cfg, dict):
                    continue
                cfg.pop("api_key", None)
                base_url = cfg.get("base_url")
                if not base_url:
                    continue
                platform_key = self._resolve_seed_platform_key(name, cfg)
                key_entry = uploaded_key_data.get(platform_key)
                if key_entry is None and url_counts[normalize_base_url(str(base_url))] == 1:
                    normalized_url = normalize_base_url(str(base_url))
                    for legacy_key, legacy_entry in uploaded_key_data.items():
                        if not isinstance(legacy_key, str):
                            continue
                        try:
                            if normalize_base_url(legacy_key) == normalized_url:
                                key_entry = legacy_entry
                                break
                        except (AttributeError, TypeError):
                            continue
                if not key_entry:
                    continue
                raw_key = None
                if isinstance(key_entry, dict):
                    raw_key = key_entry.get("api_key")
                elif isinstance(key_entry, str):
                    raw_key = key_entry
                if not raw_key or not isinstance(raw_key, str):
                    continue
                raw_key = raw_key.strip()
                if not raw_key:
                    continue
                # 尝试解密 ENC: 值
                if raw_key.startswith("ENC:"):
                    result = sec_mgr.decrypt(raw_key)
                    if result.has_plaintext:
                        cfg["api_key"] = sec_mgr.encrypt(result.value)
                        merged_count += 1
                    else:
                        print(f"[import] 密钥解密失败，已跳过: {platform_key}")
                        skipped_count += 1
                else:
                    # 明文或 ENV 占位符
                    cfg["api_key"] = raw_key
                    merged_count += 1
            if merged_count or skipped_count:
                print(f"[import] 从上传文件合并密钥: {merged_count} 成功, {skipped_count} 跳过")
        else:
            # .yaml 文件：清除内嵌 api_key，不从本地 matchbox_key.yaml 获取
            # 已有平台的密钥由增量同步保留，新平台需后续手动配置
            for cfg in raw_platform_configs.values():
                if isinstance(cfg, dict):
                    cfg.pop("api_key", None)

        # 增量同步：只添加，不删除
        self._sync_default_platforms(force_reset=False, raw_platform_configs=raw_platform_configs)

        # 刷新内存中的默认平台配置
        reload_default_platform_configs()

        # 使系统平台缓存失效
        self._invalidate_sys_platforms_cache()

        # 重新解析默认平台/模型 ID
        with self.Session() as session:
            self._resolve_default_ids_from_db(session)

        return {
            "success": True,
            "message": "系统平台配置已增量同步",
            "platform_count": len(raw_platform_configs),
        }

    def _get_sys_config(self, session):
        if self._is_sys_platforms_cache_expired():
            with self._cache_lock:
                if self._is_sys_platforms_cache_expired():
                    self._sys_platforms_cache = (
                        session.query(LLMPlatform)
                        .options(selectinload(LLMPlatform.models))
                        .filter_by(is_sys=1)
                        .filter(LLMPlatform.disable == 0)
                        .order_by(LLMPlatform.sort_order, LLMPlatform.id)
                        .all()
                    )
                    self._sys_platforms_cache_at = time.time()

    def _ensure_mutable(self, admin_mode: bool = False):
        if self.use_sys_llm_config and not admin_mode:
            raise ValueError("当前处于 USE_SYS_LLM_CONFIG 模式，请直接修改 DEFAULT_PLATFORM_CONFIGS 或环境变量。")

    @staticmethod
    def _bool_to_int(value: bool) -> int:
        return 1 if value else 0
    
    @staticmethod
    def _int_to_bool(value: int) -> bool:
        return bool(value)

    @staticmethod
    def _apply_model_params(model_obj: 'LLModels', kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if model_obj is not None and getattr(model_obj, "temperature", None) is not None and "temperature" not in kwargs:
            kwargs["temperature"] = float(model_obj.temperature)

        if model_obj and model_obj.extra_body:
            try:
                model_extra_params = json.loads(model_obj.extra_body)
                if model_extra_params:
                    model_kwargs = kwargs.get("model_kwargs", {})
                    existing_extra_body = kwargs.get("extra_body", model_kwargs.get("extra_body", {}))
                    merged_extra_body = {**existing_extra_body, **model_extra_params}
                    # ⚠️ 如果 extra_body 配置中错误包含了 streaming 字段，此处将其删除。
                    # 流式/非流式由调用方式（invoke/stream）自动决定，不应通过 extra_body 控制。
                    merged_extra_body.pop("streaming", None)
                    if merged_extra_body:
                        kwargs["extra_body"] = merged_extra_body
            except json.JSONDecodeError:
                pass
        return kwargs

    @staticmethod
    def _normalize_usage_key(usage_key: Optional[str]) -> str:
        if usage_key is None:
            return DEFAULT_USAGE_KEY
        normalized = str(usage_key).strip().lower()
        return normalized or DEFAULT_USAGE_KEY

    def _get_usage_slot(self, session, user_id: str, usage_key: str) -> Optional[UserModelUsage]:
        return (
            session.query(UserModelUsage)
            .filter_by(user_id=user_id, usage_key=usage_key)
            .first()
        )

    def _get_request_caller_context(self) -> tuple[Optional[str], bool]:
        """读取宿主注入的调用者身份；未注入时按匿名调用处理。"""
        provider = self._caller_context_provider
        if provider is None:
            return None, False
        try:
            caller_user_id, caller_is_admin = provider()
        except Exception:
            return None, False
        return (
            str(caller_user_id) if caller_user_id is not None else None,
            bool(caller_is_admin),
        )

    def _is_system_hosted_key_owner_call(self, user_id: str) -> bool:
        """判断本次调用是否是站长本人使用自己的系统托管 Key。"""
        caller_user_id, caller_is_admin = self._get_request_caller_context()
        if not caller_is_admin or caller_user_id is None:
            return False
        return str(caller_user_id) == str(user_id)

    def _can_use_system_hosted_key(self, user_id: str) -> bool:
        """系统托管 Key 的唯一访问策略。

        这里刻意区分三种主体：
        1. SYSTEM_USER_ID(-1)：内部系统/单用户模式，天然可用托管 Key。
        2. 站长真人账号：即使关闭“向全体用户共享”，也应能使用自己配置的托管 Key。
        3. 普通用户：只有 llm_auto_key 开启时，才可回退到托管 Key。

        不要把管理员 user_id 改写成 -1，也不要复制一份 Key 到个人密钥表。
        那会污染用途配置、Agent 绑定、用量统计和计费归属，形成难以维护的补丁扩散。
        """
        if str(user_id) == SYSTEM_USER_ID:
            return True
        if self._is_system_hosted_key_owner_call(str(user_id)):
            return True
        return bool(self.llm_auto_key)

    def _ensure_usage_slot(
        self,
        session,
        user_id: str,
        usage_key: str,
        usage_label: Optional[str] = None,
        platform_id: Optional[int] = None,
        model_id: Optional[int] = None,
    ) -> tuple:
        slot = self._get_usage_slot(session, user_id, usage_key)
        if slot:
            return slot, False

        if platform_id is None:
            platform_id = self._default_platform_id
        if model_id is None:
            model_id = self._default_model_id
        if platform_id is None or model_id is None:
            raise RuntimeError("默认平台或模型尚未初始化")

        label = usage_label or self._builtin_usage_map.get(usage_key, {}).get("label") or usage_key

        slot = UserModelUsage(
            user_id=user_id,
            usage_key=usage_key,
            usage_label=label,
            selected_platform_id=platform_id,
            selected_model_id=model_id,
        )
        session.add(slot)
        session.flush()
        return slot, True

    def _ensure_default_usage_slots(self, session, user_id: str) -> bool:
        created = False
        for slot_cfg in BUILTIN_USAGE_SLOTS:
            _, added = self._ensure_usage_slot(
                session,
                user_id,
                slot_cfg["key"],
                slot_cfg.get("label"),
            )
            created = created or added
        return created

    def _get_effective_api_access(self, session, user_id: str, platform: LLMPlatform) -> Dict[str, Optional[str]]:
        """解析用户当前实际命中的 API Key、来源及其计费范围。"""
        api_key = None
        quota_scope = None
        key_source = None
        sec_mgr = SecurityManager.get_instance()

        if platform.is_sys:
            cred = session.query(LLMSysPlatformKey).filter_by(
                user_id=user_id, platform_id=platform.id
            ).first()

            if cred and cred.api_key:
                api_key = sec_mgr.decrypt(cred.api_key).to_optional_plaintext()
                if api_key:
                    quota_scope = "self_paid"
                    key_source = "user_override"

            if not api_key and self._can_use_system_hosted_key(str(user_id)):
                if platform.api_key:
                    api_key = sec_mgr.decrypt(platform.api_key).to_optional_plaintext()
                    if api_key:
                        quota_scope = "sys_paid"
                        key_source = "system_hosted"
        else:
            api_key = sec_mgr.decrypt(platform.api_key).to_optional_plaintext()
            if api_key:
                quota_scope = "self_paid"
                key_source = "custom_platform"

        return {
            "api_key": api_key,
            "quota_scope": quota_scope,
            "key_source": key_source,
        }

    def _get_effective_api_key(self, session, user_id: str, platform: LLMPlatform) -> Optional[str]:
        return self._get_effective_api_access(session, user_id, platform).get("api_key")

    def _is_platform_disabled(self, session, user_id: str, platform: LLMPlatform) -> bool:
        if platform.is_sys:
            cred = session.query(LLMSysPlatformKey).filter_by(
                user_id=user_id, platform_id=platform.id
            ).first()
            return bool(platform.disable) or bool(cred and cred.disable)
        return bool(platform.disable)

    def _is_model_disabled(self, model: Optional[LLModels]) -> bool:
        if not model:
            return True
        return bool(getattr(model, "disable", 0))

    def _set_model_disabled(self, model: LLModels, disabled: bool) -> None:
        model.disable = 1 if disabled else 0

    def ensure_user_has_config(self, session, user_id: str) -> UserModelUsage:
        """确保用户至少拥有内置用途槽位，并返回默认用途(main)槽位。"""
        user_id = str(user_id)

        if self._default_platform_id is None or self._default_model_id is None:
            raise RuntimeError("AIManager 未正确初始化，默认平台或模型 ID 缺失")

        created = self._ensure_default_usage_slots(session, user_id)
        main_slot = self._get_usage_slot(session, user_id, self._default_usage_key)
        if not main_slot:
            main_slot, added = self._ensure_usage_slot(session, user_id, self._default_usage_key)
            created = created or added

            session.commit()

        return main_slot

    def proxy_list_models(self, user_id: str, platform_id: int) -> List[Dict[str, Any]]:
        """代理调用远程平台获取模型列表（含 token 上限信息）"""
        user_id = str(user_id)
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id).first()
            if not plat:
                raise ValueError("平台不存在")

            if self._is_platform_disabled(session, user_id, plat):
                raise ValueError("平台已禁用")
            
            # 权限检查：系统平台或者用户自己的平台
            if not plat.is_sys and plat.user_id != user_id:
                raise ValueError("无权访问此平台")
            
            api_key = self._get_effective_api_key(session, user_id, plat)
            base_url = plat.base_url
            
            if not api_key:
                raise ValueError(f"平台 {plat.name} 未配置 API Key")
        
        # 调用 utils 中的通用探测逻辑
        try:
            from .utils import probe_platform_models

            models_data = probe_platform_models(base_url, api_key, raise_on_error=True)
            # 返回含 token 上限的富数据，供前端自动填充
            return [
                {
                    "id": m["id"],
                    "max_context_tokens": m.get("max_context_tokens"),
                    "max_output_tokens": m.get("max_output_tokens"),
                }
                for m in models_data
            ]
        except Exception as e:
            raise ValueError(f"获取模型列表失败: {e}")

    def proxy_test_chat(self, user_id: str, platform_id: int, model_name: str, extra_body_override: Dict[str, Any] = None) -> str:
        """测试模型连接（发送固定连通性探测语句）"""
        user_id = str(user_id)
        extra_body = extra_body_override
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id).first()
            if not plat:
                raise ValueError("平台不存在")

            if self._is_platform_disabled(session, user_id, plat):
                raise ValueError("平台已禁用")
            
            if not plat.is_sys and plat.user_id != user_id:
                raise ValueError("无权访问此平台")
            
            # 如果没有覆盖，则尝试从数据库查找模型配置以获取 extra_body
            if extra_body is None:
                model_obj = next(
                    (
                        model
                        for model in session.query(LLModels).filter_by(platform_id=platform_id, model_name=model_name).all()
                        if is_chat_model(model) and not self._is_model_disabled(model)
                    ),
                    None,
                )
                if model_obj and model_obj.extra_body:
                    try:
                        extra_body = json.loads(model_obj.extra_body)
                    except:
                        pass

            api_key = self._get_effective_api_key(session, user_id, plat)
            base_url = plat.base_url
            
            if not api_key:
                raise ValueError(f"平台 {plat.name} 未配置 API Key")
        
        # 调用 utils 中的通用测试逻辑
        try:
            from .utils import test_platform_chat

            return test_platform_chat(base_url, api_key, model_name, extra_body=extra_body)
        except Exception as e:
            raise ValueError(f"测试失败: {e}")

    def proxy_speed_test(self, user_id: str, platform_id: int, model_name: str):
        """流式测速代理"""
        user_id = str(user_id)
        extra_body = None
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id).first()
            if not plat:
                raise ValueError("平台不存在")

            if self._is_platform_disabled(session, user_id, plat):
                raise ValueError("平台已禁用")
            
            if not plat.is_sys and plat.user_id != user_id:
                raise ValueError("无权访问此平台")

            # 尝试查找模型配置以获取 extra_body
            model_obj = next(
                (
                    model
                    for model in session.query(LLModels).filter_by(platform_id=platform_id, model_name=model_name).all()
                    if is_chat_model(model) and not self._is_model_disabled(model)
                ),
                None,
            )
            if model_obj and model_obj.extra_body:
                try:
                    extra_body = json.loads(model_obj.extra_body)
                except:
                    pass
            
            api_key = self._get_effective_api_key(session, user_id, plat)
            base_url = plat.base_url
            
            if not api_key:
                raise ValueError(f"平台 {plat.name} 未配置 API Key")

        from .utils import stream_speed_test

        return stream_speed_test(base_url, api_key, model_name, extra_body=extra_body)

    def proxy_test_embedding(self, user_id: str, platform_id: int, model_name: str) -> Dict[str, Any]:
        """测试 Embedding 连接"""
        user_id = str(user_id)
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id).first()
            if not plat:
                raise ValueError("平台不存在")

            if self._is_platform_disabled(session, user_id, plat):
                raise ValueError("平台已禁用")

            if not plat.is_sys and plat.user_id != user_id:
                raise ValueError("无权访问此平台")

            api_key = self._get_effective_api_key(session, user_id, plat)
            base_url = plat.base_url

            if not api_key:
                raise ValueError(f"平台 {plat.name} 未配置 API Key")

        try:
            from .utils import test_platform_embedding

            return test_platform_embedding(base_url, api_key, model_name)
        except Exception as e:
            raise ValueError(f"测试失败: {e}")

    def get_system_config(self) -> Dict[str, bool]:
        """获取系统级配置 (LLM_AUTO_KEY, USE_SYS_LLM_CONFIG)"""
        return {
            "llm_auto_key": self.llm_auto_key,
            "use_sys_llm_config": self.use_sys_llm_config,
            "billing_enabled": self.billing_enabled,
        }

    def set_system_config(
        self,
        use_sys_llm_config: bool = None,
        llm_auto_key: bool = None,
        billing_enabled: bool = None,
    ) -> bool:
        """设置系统级配置"""
        changed = False
        if use_sys_llm_config is not None:
            if self.use_sys_llm_config != use_sys_llm_config:
                self.use_sys_llm_config = use_sys_llm_config
                changed = True
        
        if llm_auto_key is not None:
            if self.llm_auto_key != llm_auto_key:
                self.llm_auto_key = llm_auto_key
                changed = True

        if billing_enabled is not None:
            next_billing_enabled = bool(billing_enabled)
            if self.billing_enabled != next_billing_enabled:
                self.billing_enabled = next_billing_enabled
                changed = True
        
        if changed:
            self._save_state()
            # Cache invalidation might be needed if behaviour depends on this flag heavily
            # accessing self.use_sys_llm_config is direct, so it should be fine.
        
        return True


class AIManager(
    AIManagerBase,
    AdminMixin,
    UserServicesMixin,
    LLMBuilderMixin,
    CreditServicesMixin,
    CreditGrantServicesMixin,
    QuotaServicesMixin,
    UsageServicesMixin,
    RedeemCodeServicesMixin,
):
    """
    AI 模型管理器
    
    集成 AdminMixin, UserServicesMixin, UsageServicesMixin, LLMBuilderMixin
    """
    
    def __init__(self, db_name: str = "llm_config.db", **kwargs):
        super().__init__(db_name, **kwargs)
        # ⚠️ 不要在这里调用 initialize_defaults()
        # 这会导致 Import 时建立 DB 连接，从而在启动迁移时造成 SQLite 死锁。
        # 请务必在 app.py 的 lifespan 中显式调用 initialize_matchbox(ensure_defaults=True)
        # self.initialize_defaults()
