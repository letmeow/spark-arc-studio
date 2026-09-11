"""
平台与模型管理 Mixin (Admin)
处理平台和模型的增删改查
"""

import json
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import selectinload

from .models import (
    MODALITY_EMBEDDING,
    MODALITY_IMAGE,
    MODALITY_TEXT,
    LLMPlatform,
    LLModels,
    LLMSysPlatformKey,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    get_model_modalities,
    is_chat_model,
    is_embedding_model,
    is_image_generation_model,
    model_sort_key,
    normalize_model_modalities,
    set_model_modalities,
)
from .config import DEFAULT_PLATFORM_CONFIGS, SYSTEM_USER_ID
from .image_adapters import (
    DEFAULT_IMAGE_GENERATION_ADAPTER,
    normalize_image_generation_adapter,
    strip_internal_image_generation_fields,
)
from .security import SecurityManager
from .utils import normalize_base_url, normalize_recharge_url
from .platform_identity import generate_platform_key, normalize_platform_key


def _parse_extra_body_raw(extra_body_str: Optional[str]) -> Optional[Dict[str, Any]]:
    """解析数据库中的 extra_body 原始对象。"""
    if not extra_body_str:
        return None
    try:
        parsed = json.loads(extra_body_str)
        if parsed is None or parsed == {} or not isinstance(parsed, dict):
            return None
        return parsed
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_extra_body_for_response(extra_body_str: Optional[str]) -> Optional[Dict]:
    """将数据库中的 extra_body 字符串解析为 Python 对象，用于 API 响应。
    
    返回:
        - None: 如果输入为 None、空字符串、"null" 或 "{}"
        - dict: 解析后的 JSON 对象
    """
    return strip_internal_image_generation_fields(_parse_extra_body_raw(extra_body_str))


def _normalize_model_image_generation_adapter(
    *,
    output_modalities: Optional[List[str]],
    image_generation_adapter: Optional[str],
    existing_value: Optional[str] = None,
) -> Optional[str]:
    """按模型输出模态决定是否保存生图协议字段。"""
    _, normalized_output = normalize_model_modalities(None, output_modalities)
    if MODALITY_IMAGE not in normalized_output:
        return None
    return (
        normalize_image_generation_adapter(image_generation_adapter)
        or normalize_image_generation_adapter(existing_value)
        or DEFAULT_IMAGE_GENERATION_ADAPTER
    )


def _normalize_non_negative_limit(raw_value: Optional[int], *, field_label: str, default_value: int) -> int:
    """将模型上限值规范化为非负整数；None 时回退默认值。"""
    if raw_value is None:
        return int(default_value)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_label} 必须是整数")
    if parsed < 0:
        raise ValueError(f"{field_label} 不能小于 0")
    return parsed


def _normalize_nullable_credit_balance(raw_value: Optional[float], *, field_label: str = "平台火柴余额") -> Optional[float]:
    """将平台预算规范化；None 表示无限，数值必须非负。"""
    if raw_value is None:
        return None
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_label} 必须是数字")
    if parsed < 0:
        raise ValueError(f"{field_label} 不能小于 0，留空表示无限")
    return parsed


def _model_payload_for_response(model: LLModels) -> Dict[str, Any]:
    """构建前端与 GUI 共用的模型响应字段。"""
    raw_extra_body = _parse_extra_body_raw(model.extra_body)
    response_extra_body = strip_internal_image_generation_fields(raw_extra_body)
    return {
        "model_id": model.id,
        "model_name": model.model_name,
        "display_name": model.display_name,
        **get_model_modalities(model),
        "extra_body": response_extra_body,
        "image_generation_adapter": _normalize_model_image_generation_adapter(
            output_modalities=get_model_modalities(model)["output_modalities"],
            image_generation_adapter=getattr(model, "image_generation_adapter", None),
        ),
        "temperature": model.temperature,
        "max_context_tokens": int(model.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS),
        "max_output_tokens": int(model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS),
        "sys_credit_input_price_per_million": model.sys_credit_input_price_per_million,
        "sys_credit_cached_input_price_per_million": model.sys_credit_cached_input_price_per_million,
        "sys_credit_output_price_per_million": model.sys_credit_output_price_per_million,
        "sort_order": model.sort_order,
    }


def _models_in_sort_order(models: List[LLModels]) -> List[LLModels]:
    """按持久化排序返回模型，并用数据库 ID 解决历史并列值。"""
    return sorted(models or [], key=model_sort_key)


class AdminMixin:
    """平台与模型管理功能 (Admin)"""

    # ==================== 平台管理 ====================

    @staticmethod
    def _find_active_platform_with_name(
        session,
        *,
        name: str,
        exclude_platform_id: Optional[int] = None,
    ) -> Optional[LLMPlatform]:
        """查找仍在使用中的同名平台；已禁用记录不再占用名称。"""
        filters = [
            LLMPlatform.name == name,
            LLMPlatform.disable == 0,
        ]
        if exclude_platform_id is not None:
            filters.append(LLMPlatform.id != exclude_platform_id)
        return (
            session.query(LLMPlatform)
            .filter(*filters)
            .order_by(LLMPlatform.id)
            .first()
        )

    @staticmethod
    def _find_disabled_platform_for_revival(
        session,
        *,
        name: str,
        base_url: str,
        is_sys: bool,
        user_id: Optional[str] = None,
    ) -> Optional[LLMPlatform]:
        """按平台类型、归属、名称和规范化 URL 精确查找可复活的平台。"""
        filters = [
            LLMPlatform.name == name,
            LLMPlatform.base_url == base_url,
            LLMPlatform.disable == 1,
            LLMPlatform.is_sys == int(is_sys),
        ]
        if not is_sys:
            if user_id is None:
                return None
            filters.append(LLMPlatform.user_id == str(user_id))
        return (
            session.query(LLMPlatform)
            .filter(*filters)
            .order_by(LLMPlatform.id)
            .first()
        )

    def _describe_secret_state(self, raw_value: Optional[str], *, audience: str = "generic") -> Dict[str, Any]:
        text = raw_value.strip() if isinstance(raw_value, str) else ""
        if not text:
            return {
                "status": "missing",
                "configured": False,
                "available": False,
                "message": "未配置 API Key。",
            }

        result = SecurityManager.get_instance().decrypt(text)
        if result.has_plaintext:
            return {
                "status": "ok",
                "configured": True,
                "available": True,
                "message": "API Key 已配置并可用。",
            }

        if result.is_missing_key:
            if audience == "system_managed":
                return {
                    "status": "missing_key",
                    "configured": True,
                    "available": False,
                    "message": "检测到仓库同步或历史导入的托管密钥，但当前站点尚未设置主密钥 LLM_KEY。首次启动时这是正常现象，请站长先设置主密钥，再按需重新配置托管 API Key。",
                }
            return {
                "status": "missing_key",
                "configured": True,
                "available": False,
                "message": "检测到已保存的加密 API Key，但当前站点尚未设置主密钥 LLM_KEY。",
            }

        if audience == "system_managed":
            return {
                "status": "needs_reconfigure",
                "configured": True,
                "available": False,
                "message": "该平台托管密钥需要配置。首次拉取项目后这是常见现象，请站长为该平台填写自己的 API Key。",
            }

        if audience == "user_override":
            return {
                "status": "needs_reconfigure",
                "configured": True,
                "available": False,
                "message": "您保存的 API Key 需要重新配置，请为该平台重新填写 API Key。",
            }

        return {
            "status": "needs_reconfigure",
            "configured": True,
            "available": False,
            "message": "该平台 API Key 需要配置，请重新填写 API Key。",
        }

    def _build_effective_key_view(
        self,
        *,
        user_id: str,
        user_key_saved: bool,
        user_key_info: Optional[Dict[str, Any]],
        sys_key_info: Optional[Dict[str, Any]],
        api_key_available: bool,
    ) -> Dict[str, str]:
        can_use_sys_key = self._can_use_system_hosted_key(user_id)
        is_owner_call = self._is_system_hosted_key_owner_call(user_id)

        if user_key_saved and user_key_info and user_key_info.get("available"):
            return {
                "status": "user_override",
                "message": "当前使用您自己的 API Key。",
            }

        if user_key_saved and user_key_info and not user_key_info.get("available"):
            if sys_key_info and sys_key_info.get("available") and can_use_sys_key and api_key_available:
                return {
                    "status": "managed_owner_fallback" if is_owner_call else "managed_fallback",
                    "message": (
                        f"{user_key_info.get('message')} 当前已回退到您作为站长配置的托管 API Key。"
                        if is_owner_call
                        else f"{user_key_info.get('message')} 当前已自动回退到站长托管 API Key。"
                    ),
                }
            return {
                "status": "user_override_missing_key" if user_key_info.get("status") == "missing_key" else "user_override_needs_reconfigure",
                "message": user_key_info.get("message") or "您保存的 API Key 需要重新配置。",
            }

        if sys_key_info and sys_key_info.get("available") and can_use_sys_key and api_key_available:
            if is_owner_call:
                return {
                    "status": "managed_owner_ok",
                    "message": "当前使用您作为站长配置的托管 API Key。关闭全体用户共享不会影响站长本人使用。",
                }
            return {
                "status": "managed_ok",
                "message": "当前使用站长托管 API Key。",
            }

        if sys_key_info and sys_key_info.get("available") and not can_use_sys_key:
            return {
                "status": "managed_available_but_locked",
                "message": "站长已配置托管 API Key，但当前未开启对全体用户共享。请填写您自己的 API Key。",
            }

        if sys_key_info and sys_key_info.get("status") == "missing_key":
            return {
                "status": "managed_missing_key",
                "message": sys_key_info.get("message") or "检测到托管密钥，但当前尚未设置主密钥。",
            }

        if sys_key_info and sys_key_info.get("status") == "needs_reconfigure":
            return {
                "status": "managed_needs_reconfigure",
                "message": sys_key_info.get("message") or "托管密钥需要配置，请为该平台填写 API Key。",
            }

        return {
            "status": "missing",
            "message": "未配置任何可用 API Key。请设置您自己的 API Key，或联系站长配置托管密钥。",
        }

    def add_platform(
        self,
        name: str,
        base_url: str,
        api_key: Optional[str] = None,
        user_id: str = None,
        recharge_url: Optional[str] = None,
        platform_key: Optional[str] = None,
    ):
        self._ensure_mutable()
        if not (name and base_url):
            raise ValueError("name / base_url 必填")
        if user_id is None or user_id == SYSTEM_USER_ID:
            raise ValueError("用户自定义平台必须绑定真实 user_id")
        
        user_id = str(user_id)
        base_url = normalize_base_url(base_url)
        recharge_url = normalize_recharge_url(recharge_url)
        supplied_platform_key = normalize_platform_key(platform_key)
        platform_key = supplied_platform_key or generate_platform_key()
        
        if api_key:
            api_key = SecurityManager.get_instance().encrypt(api_key)
        
        with self.Session() as session:
            existing_key = session.query(LLMPlatform).filter_by(platform_key=platform_key).first()
            if existing_key:
                if not supplied_platform_key or existing_key.is_sys or existing_key.user_id != user_id or not existing_key.disable:
                    raise ValueError(f"platform_key '{platform_key}' 已存在")
                existing_name = self._find_active_platform_with_name(
                    session,
                    name=name,
                    exclude_platform_id=existing_key.id,
                )
                if existing_name:
                    raise ValueError(f"平台名称 '{name}' 已存在")
                existing_key.name = name
                existing_key.base_url = base_url
                existing_key.api_key = api_key
                existing_key.recharge_url = recharge_url
                existing_key.disable = 0
                session.commit()
                return existing_key

            # 活动平台名称全局唯一；活动记录优先于历史禁用记录。
            if self._find_active_platform_with_name(session, name=name):
                raise ValueError(f"平台名称 '{name}' 已存在（系统预设或已被其他用户使用）")

            # 未提供稳定身份时，仅复活同名、同 URL 的禁用自定义平台。
            if not supplied_platform_key:
                revived = self._find_disabled_platform_for_revival(
                    session,
                    name=name,
                    base_url=base_url,
                    is_sys=False,
                    user_id=user_id,
                )
                if revived:
                    revived.api_key = api_key
                    revived.recharge_url = recharge_url
                    revived.disable = 0
                    session.commit()
                    return revived

            # 平台名称全局唯一；平台身份由新生成的 key 区分。
            if name in DEFAULT_PLATFORM_CONFIGS:
                raise ValueError(f"平台名称 '{name}' 已存在（系统预设或已被其他用户使用）")

            p = LLMPlatform(
                name=name,
                platform_key=platform_key,
                base_url=base_url,
                recharge_url=recharge_url,
                api_key=api_key,
                user_id=user_id,
                is_sys=0,
            )
            session.add(p)
            session.commit()
            return p

    def disable_platform(self, platform_id: int, user_id: str = None, admin_mode: bool = False):
        """
        统一的平台删除方法（软禁用）。
        - admin_mode=True: 管理员禁用系统平台
        - admin_mode=False: 用户禁用自定义平台，需要 user_id

        注意：软禁用平台不属于"创建/修改"范畴，不受 USE_SYS_LLM_CONFIG 锁定限制。
        """
        # 不调用 _ensure_mutable()：删除 ≠ 创建，锁定模式下也应允许删除
        with self.Session() as session:
            if admin_mode:
                plat = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
                if not plat:
                    raise ValueError("系统平台不存在")
            else:
                user_id = str(user_id) if user_id else None
                plat = session.query(LLMPlatform).filter_by(id=platform_id, user_id=user_id, is_sys=0).first()
                if not plat:
                    raise ValueError("平台不存在或无权删除")

            plat.disable = 1
            session.commit()

            # 刷新缓存
            if plat.is_sys:
                self._invalidate_sys_platforms_cache()

            return True

    def update_platform_details(
        self,
        user_id: str,
        platform_id: int,
        new_name: str,
        new_base_url: str,
        recharge_url: Optional[str] = None,
        update_recharge_url: bool = False,
    ):
        self._ensure_mutable()
        user_id = str(user_id)
        if not (new_name and new_base_url):
            raise ValueError("name 和 base_url 都不能为空")
        
        new_base_url = normalize_base_url(new_base_url)
        
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id, user_id=user_id, is_sys=0).first()
            if not plat:
                raise ValueError("平台不存在或无权修改")
            
            # 名称全局唯一性检查（排除自己）
            if new_name in DEFAULT_PLATFORM_CONFIGS:
                raise ValueError("平台名称与系统平台冲突")
            existing_name = self._find_active_platform_with_name(
                session,
                name=new_name,
                exclude_platform_id=platform_id,
            )
            if existing_name:
                raise ValueError(f"平台名称 '{new_name}' 已被使用")
                
            plat.name = new_name
            plat.base_url = new_base_url
            if update_recharge_url:
                plat.recharge_url = normalize_recharge_url(recharge_url)
            session.commit()
            return True

    def update_platform_config(
        self, user_id: str, platform_id: int, api_key: Optional[str]
    ):
        """更新平台的 API Key；系统平台传空值时清除用户个人覆盖。"""
        user_id = str(user_id)
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id).first()
            if not plat:
                raise ValueError("平台不存在")
            
            sec_mgr = SecurityManager.get_instance()
            encrypted_key = sec_mgr.encrypt(api_key) if api_key else None
            
            if plat.is_sys:
                # 系统平台：更新用户的密钥配置
                cred = session.query(LLMSysPlatformKey).filter_by(
                    user_id=user_id, platform_id=platform_id
                ).first()
                if not cred:
                    cred = LLMSysPlatformKey(user_id=user_id, platform_id=platform_id)
                    session.add(cred)
                cred.api_key = encrypted_key
            else:
                # 用户平台：直接更新
                if plat.user_id != user_id:
                    raise ValueError("无权修改此平台")
                plat.api_key = encrypted_key
            
            session.commit()
            return True


    def _collect_platform_views(self, session, user_id: str) -> List[Dict[str, Any]]:
        """收集用户可见的所有平台视图"""
        user_id = str(user_id)
        self._get_sys_config(session)
        
        # 将缓存的系统平台对象合并到当前会话
        sys_platforms = [session.merge(p, load=False) for p in self._sys_platforms_cache]
        
        sys_platform_ids = [p.id for p in sys_platforms]
        live_sys_credit_balances: Dict[int, Optional[float]] = {}
        if sys_platform_ids:
            live_sys_credit_balances = {
                row.id: row.sys_credit_balance
                for row in (
                    session.query(LLMPlatform.id, LLMPlatform.sys_credit_balance)
                    .filter(LLMPlatform.id.in_(sys_platform_ids))
                    .all()
                )
            }
        
        user_sys_keys: Dict[int, LLMSysPlatformKey] = {}
        if sys_platform_ids:
            creds = (
                session.query(LLMSysPlatformKey)
                .filter(
                    LLMSysPlatformKey.user_id == user_id,
                    LLMSysPlatformKey.platform_id.in_(sys_platform_ids),
                )
                .all()
            )
            user_sys_keys = {c.platform_id: c for c in creds}

        views: List[Dict[str, Any]] = []

        for plat in sys_platforms:
            cred = user_sys_keys.get(plat.id)
            api_key = self._get_effective_api_key(session, user_id, plat)
            user_disable = cred.disable if cred else 0
            user_key_saved = bool(cred and cred.api_key)
            user_key_info = self._describe_secret_state(cred.api_key, audience="user_override") if user_key_saved else {
                "status": "missing",
                "configured": False,
                "available": False,
                "message": "您尚未为该系统平台配置个人 API Key。",
            }
            sys_key_info = self._describe_secret_state(plat.api_key, audience="system_managed")
            effective_key_view = self._build_effective_key_view(
                user_id=user_id,
                user_key_saved=user_key_saved,
                user_key_info=user_key_info,
                sys_key_info=sys_key_info,
                api_key_available=bool(api_key),
            )

            views.append(
                {
                    "platform_id": plat.id,
                    "platform_key": plat.platform_key,
                    "name": plat.name,
                    "base_url": plat.base_url,
                    "recharge_url": plat.recharge_url,
                    "api_key_set": bool(api_key),
                    "api_key_status": effective_key_view["status"],
                    "api_key_message": effective_key_view["message"],
                    "sys_key_set": bool(sys_key_info["available"]),
                    "sys_key_status": sys_key_info["status"],
                    "sys_key_message": sys_key_info["message"],
                    "sys_credit_balance": live_sys_credit_balances.get(plat.id, plat.sys_credit_balance),
                    "user_id": plat.user_id,
                    "is_sys": True,
                    "user_key_override": bool(user_key_info["available"]),
                    "user_key_saved": user_key_saved,
                    "user_key_status": user_key_info["status"],
                    "user_key_message": user_key_info["message"],
                    "disabled": int(bool(plat.disable) or bool(user_disable)),
                    "models": _models_in_sort_order(
                        [m for m in plat.models if not self._is_model_disabled(m)]
                    ),
                }
            )

        # 查询用户自定义平台（统一使用字符串类型 user_id）
        user_platforms = (
            session.query(LLMPlatform)
            .options(selectinload(LLMPlatform.models))
            .filter_by(user_id=user_id, is_sys=0)
            .all()
        )

        for plat in user_platforms:
            api_key = self._get_effective_api_key(session, user_id, plat)
            key_info = self._describe_secret_state(plat.api_key, audience="custom")
            views.append(
                {
                    "platform_id": plat.id,
                    "platform_key": plat.platform_key,
                    "name": plat.name,
                    "base_url": plat.base_url,
                    "recharge_url": plat.recharge_url,
                    "api_key_set": bool(api_key),
                    "api_key_status": "ok" if bool(api_key) else key_info["status"],
                    "api_key_message": "当前平台 API Key 已配置并可用。" if bool(api_key) else key_info["message"],
                    "sys_credit_balance": plat.sys_credit_balance,
                    "user_id": plat.user_id,
                    "is_sys": False,
                    "user_key_override": False,
                    "user_key_saved": False,
                    "disabled": plat.disable,
                    "models": _models_in_sort_order(
                        [m for m in plat.models if not self._is_model_disabled(m)]
                    ),
                }
            )

        return views

    def get_platforms(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户可见的所有平台（不含模型详情，用于平台管理界面）"""
        user_id = str(user_id)
        with self.Session() as session:
            views = self._collect_platform_views(session, user_id)
            return [
                {
                    "platform_id": view["platform_id"],
                    "platform_key": view.get("platform_key"),
                    "name": view["name"],
                    "base_url": view["base_url"],
                    "recharge_url": view.get("recharge_url"),
                    "api_key_set": view["api_key_set"],
                    "api_key_status": view.get("api_key_status", "missing"),
                    "api_key_message": view.get("api_key_message", ""),
                    "sys_key_set": view.get("sys_key_set", False),
                    "sys_key_status": view.get("sys_key_status", "missing"),
                    "sys_key_message": view.get("sys_key_message", ""),
                    "sys_credit_balance": view.get("sys_credit_balance"),
                    "is_sys": view["is_sys"],
                    "user_key_override": view.get("user_key_override", False),
                    "user_key_saved": view.get("user_key_saved", False),
                    "user_key_status": view.get("user_key_status", "missing"),
                    "user_key_message": view.get("user_key_message", ""),
                    "disabled": view["disabled"],
                    "model_count": len(view["models"]),
                }
                for view in views
                if not view["disabled"]
            ]

    def get_platforms_with_models(self, user_id: str, only_custom: bool = False) -> List[Dict[str, Any]]:
        """获取平台列表，包含嵌套的模型数组（用于模型管理界面）"""
        user_id = str(user_id)
        with self.Session() as session:
            views = self._collect_platform_views(session, user_id)
            results = []
            for view in views:
                if view["disabled"]:
                    continue
                if only_custom and view["is_sys"]:
                    continue
                results.append({
                    "platform_id": view["platform_id"],
                    "platform_key": view.get("platform_key"),
                    "name": view["name"],
                    "base_url": view["base_url"],
                    "recharge_url": view.get("recharge_url"),
                    "api_key_set": view["api_key_set"],
                    "api_key_status": view.get("api_key_status", "missing"),
                    "api_key_message": view.get("api_key_message", ""),
                    "sys_key_set": view.get("sys_key_set", False),
                    "sys_key_status": view.get("sys_key_status", "missing"),
                    "sys_key_message": view.get("sys_key_message", ""),
                    "sys_credit_balance": view.get("sys_credit_balance"),
                    "is_sys": view["is_sys"],
                    "user_key_override": view.get("user_key_override", False),
                    "user_key_saved": view.get("user_key_saved", False),
                    "user_key_status": view.get("user_key_status", "missing"),
                    "user_key_message": view.get("user_key_message", ""),
                    "disabled": view["disabled"],
                    "models": [
                        _model_payload_for_response(m)
                        for m in view["models"]
                    ]
                })
            return results

    def get_platform_models(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户可见的所有平台和模型（打平结构，用于模型选择）"""
        with self.Session() as session:
            views = self._collect_platform_views(session, user_id)
            return [
                {
                    "platform_id": view["platform_id"],
                    "platform_key": view.get("platform_key"),
                    "platform_name": view["name"],
                    "platform_is_sys": view["is_sys"],
                    "platform_disabled": view["disabled"],
                    "base_url": view["base_url"],
                    "recharge_url": view.get("recharge_url"),
                    "api_key_set": view["api_key_set"],
                    "api_key_status": view.get("api_key_status", "missing"),
                    "api_key_message": view.get("api_key_message", ""),
                    "sys_key_set": view.get("sys_key_set", False),
                    "sys_key_status": view.get("sys_key_status", "missing"),
                    "sys_key_message": view.get("sys_key_message", ""),
                    "sys_credit_balance": view.get("sys_credit_balance"),
                    "user_key_override": view.get("user_key_override", False),
                    "user_key_saved": view.get("user_key_saved", False),
                    "user_key_status": view.get("user_key_status", "missing"),
                    "user_key_message": view.get("user_key_message", ""),
                    "model_id": model.id,
                    "model_name": model.model_name,
                    "display_name": model.display_name,
                    **get_model_modalities(model),
                    "extra_body": _parse_extra_body_for_response(model.extra_body),
                    "temperature": model.temperature,
                    "max_context_tokens": int(model.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS),
                    "max_output_tokens": int(model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS),
                    "sys_credit_input_price_per_million": model.sys_credit_input_price_per_million,
                    "sys_credit_cached_input_price_per_million": model.sys_credit_cached_input_price_per_million,
                    "sys_credit_output_price_per_million": model.sys_credit_output_price_per_million,
                }
                for view in views
                if not view["disabled"]
                for model in view["models"]
                if is_chat_model(model)
            ]

    def get_platforms_with_embeddings(self, user_id: str, only_custom: bool = False) -> List[Dict[str, Any]]:
        """获取平台列表，包含嵌套的 Embedding 模型数组"""
        user_id = str(user_id)
        with self.Session() as session:
            views = self._collect_platform_views(session, user_id)
            results = []
            for view in views:
                if view["disabled"]:
                    continue
                if only_custom and view["is_sys"]:
                    continue
                results.append({
                    "platform_id": view["platform_id"],
                    "platform_key": view.get("platform_key"),
                    "name": view["name"],
                    "base_url": view["base_url"],
                    "recharge_url": view.get("recharge_url"),
                    "api_key_set": view["api_key_set"],
                    "api_key_status": view.get("api_key_status", "missing"),
                    "api_key_message": view.get("api_key_message", ""),
                    "is_sys": view["is_sys"],
                    "user_key_override": view.get("user_key_override", False),
                    "user_key_saved": view.get("user_key_saved", False),
                    "user_key_status": view.get("user_key_status", "missing"),
                    "user_key_message": view.get("user_key_message", ""),
                    "sys_key_set": view.get("sys_key_set", False),
                    "sys_key_status": view.get("sys_key_status", "missing"),
                    "sys_key_message": view.get("sys_key_message", ""),
                    "disabled": view["disabled"],
                    "embeddings": [
                        _model_payload_for_response(m)
                        for m in view["models"]
                        if is_embedding_model(m)
                    ]
                })
            return results

    # ==================== 模型管理 ====================

    def add_model(
        self,
        platform_id: int,
        model_name: str,
        display_name: str,
        user_id: str = None,
        extra_body: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        sys_credit_input_price_per_million: Optional[float] = None,
        sys_credit_cached_input_price_per_million: Optional[float] = None,
        sys_credit_output_price_per_million: Optional[float] = None,
        admin_mode: bool = False,
        max_context_tokens: Optional[int] = DEFAULT_MAX_CONTEXT_TOKENS,
        max_output_tokens: Optional[int] = DEFAULT_MAX_OUTPUT_TOKENS,
        input_modalities: Optional[List[str]] = None,
        output_modalities: Optional[List[str]] = None,
        image_generation_adapter: Optional[str] = None,
    ):
        """
        添加模型（统一入口）
        - admin_mode=False: 普通用户为自定义平台添加模型，需要 user_id
        - admin_mode=True: 管理员为系统平台添加模型，不受 USE_SYS_LLM_CONFIG 锁定限制
        """
        self._ensure_mutable(admin_mode=admin_mode)
        if not (platform_id and model_name and display_name):
            raise ValueError("platform_id / model_name / display_name 必填")

        normalized_max_context_tokens = _normalize_non_negative_limit(
            max_context_tokens,
            field_label="最大上下文",
            default_value=DEFAULT_MAX_CONTEXT_TOKENS,
        )
        normalized_max_output_tokens = _normalize_non_negative_limit(
            max_output_tokens,
            field_label="最大单次输出",
            default_value=DEFAULT_MAX_OUTPUT_TOKENS,
        )
        normalized_input_modalities, normalized_output_modalities = normalize_model_modalities(
            input_modalities,
            output_modalities,
        )
        cleaned_extra_body = strip_internal_image_generation_fields(extra_body)
        normalized_image_adapter = _normalize_model_image_generation_adapter(
            output_modalities=normalized_output_modalities,
            image_generation_adapter=image_generation_adapter,
        )
        is_text_capable = MODALITY_TEXT in normalized_output_modalities
        if not is_text_capable:
            temperature = None
            sys_credit_input_price_per_million = None
            sys_credit_cached_input_price_per_million = None
            sys_credit_output_price_per_million = None

        with self.Session() as session:
            if admin_mode:
                if not self.billing_enabled and (
                    sys_credit_input_price_per_million is not None
                    or sys_credit_cached_input_price_per_million is not None
                    or sys_credit_output_price_per_million is not None
                ):
                    raise ValueError("请先开启计费系统，再设置模型火柴价格")
                # 管理员模式：操作系统平台
                plat = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
                if not plat:
                    raise ValueError("系统平台不存在")
            else:
                # 用户模式：操作自定义平台
                if user_id is None or user_id == SYSTEM_USER_ID:
                    raise ValueError("为模型绑定真实 user_id")
                user_id = str(user_id)
                plat = session.query(LLMPlatform).filter_by(id=platform_id, user_id=user_id, is_sys=0).first()
                if not plat:
                    raise ValueError("平台不存在、无权限或为不可修改的系统平台")
                if self._is_platform_disabled(session, user_id, plat):
                    raise ValueError("平台已禁用")

            # 检查显示名称在当前平台下唯一（跨平台允许重复）
            existing_display = session.query(LLModels).filter(
                LLModels.platform_id == platform_id,
                LLModels.display_name == display_name
            ).first()
            if existing_display:
                if self._is_model_disabled(existing_display):
                    existing_display.platform_id = plat.id
                    existing_display.model_name = model_name
                    existing_display.display_name = display_name
                    set_model_modalities(
                        existing_display,
                        normalized_input_modalities,
                        normalized_output_modalities,
                    )
                    existing_display.extra_body = json.dumps(cleaned_extra_body) if cleaned_extra_body else None
                    existing_display.image_generation_adapter = normalized_image_adapter
                    existing_display.temperature = temperature
                    existing_display.max_context_tokens = normalized_max_context_tokens
                    existing_display.max_output_tokens = normalized_max_output_tokens
                    if admin_mode:
                        existing_display.sys_credit_input_price_per_million = (
                            None if sys_credit_input_price_per_million is None else max(float(sys_credit_input_price_per_million), 0)
                        )
                        existing_display.sys_credit_cached_input_price_per_million = (
                            None if sys_credit_cached_input_price_per_million is None else max(float(sys_credit_cached_input_price_per_million), 0)
                        )
                        existing_display.sys_credit_output_price_per_million = (
                            None if sys_credit_output_price_per_million is None else max(float(sys_credit_output_price_per_million), 0)
                        )
                    self._set_model_disabled(existing_display, False)
                    session.commit()
                    if admin_mode:
                        self._invalidate_sys_platforms_cache()
                    return existing_display
                existing_plat = session.query(LLMPlatform).filter_by(id=existing_display.platform_id).first()
                raise ValueError(f"模型显示名称 '{display_name}' 已存在于平台 '{existing_plat.name}'")

            extra_body_json = json.dumps(cleaned_extra_body) if cleaned_extra_body else None

            m = LLModels(
                platform_id=plat.id,
                model_name=model_name,
                display_name=display_name,
                extra_body=extra_body_json,
                image_generation_adapter=normalized_image_adapter,
                temperature=temperature,
                max_context_tokens=normalized_max_context_tokens,
                max_output_tokens=normalized_max_output_tokens,
                sys_credit_input_price_per_million=(
                    None if sys_credit_input_price_per_million is None else max(float(sys_credit_input_price_per_million), 0)
                ),
                sys_credit_cached_input_price_per_million=(
                    None if sys_credit_cached_input_price_per_million is None else max(float(sys_credit_cached_input_price_per_million), 0)
                ),
                sys_credit_output_price_per_million=(
                    None if sys_credit_output_price_per_million is None else max(float(sys_credit_output_price_per_million), 0)
                ),
            )
            set_model_modalities(m, normalized_input_modalities, normalized_output_modalities)
            session.add(m)
            session.commit()
            
            # 如果是系统平台模型，刷新缓存
            if admin_mode:
                self._invalidate_sys_platforms_cache()
            
            return m

    def add_embedding(
        self,
        platform_id: int,
        model_name: str,
        display_name: str,
        user_id: str = None,
        extra_body: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        admin_mode: bool = False,
        max_context_tokens: Optional[int] = DEFAULT_MAX_CONTEXT_TOKENS,
        max_output_tokens: Optional[int] = DEFAULT_MAX_OUTPUT_TOKENS,
    ):
        """
        添加 Embedding（统一入口）
        - admin_mode=False: 普通用户为自定义平台添加
        - admin_mode=True: 管理员为系统平台添加，不受 USE_SYS_LLM_CONFIG 锁定限制
        """
        return self.add_model(
            platform_id=platform_id,
            model_name=model_name,
            display_name=display_name,
            user_id=user_id,
            extra_body=extra_body,
            temperature=temperature,
            admin_mode=admin_mode,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            input_modalities=[MODALITY_TEXT],
            output_modalities=[MODALITY_EMBEDDING],
        )

    def update_model(
        self,
        model_id: int,
        new_display_name: Optional[str] = None,
        new_extra_body: Optional[Dict[str, Any]] = None,
        new_temperature: Optional[float] = None,
        sys_credit_input_price_per_million: Optional[float] = None,
        sys_credit_cached_input_price_per_million: Optional[float] = None,
        sys_credit_output_price_per_million: Optional[float] = None,
        update_credit_price: bool = False,
        update_temperature: bool = False,
        user_id: str = None,
        admin_mode: bool = False,
        max_context_tokens: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        update_max_context_tokens: bool = False,
        update_max_output_tokens: bool = False,
        input_modalities: Optional[List[str]] = None,
        output_modalities: Optional[List[str]] = None,
        update_modalities: bool = False,
        image_generation_adapter: Optional[str] = None,
        update_image_generation_adapter: bool = False,
    ):
        """
        更新模型（统一入口）
        - admin_mode=False: 普通用户更新自定义平台模型，需要 user_id
        - admin_mode=True: 管理员更新系统平台模型，不受 USE_SYS_LLM_CONFIG 锁定限制
        """
        self._ensure_mutable(admin_mode=admin_mode)
        with self.Session() as session:
            model = session.query(LLModels).filter_by(id=model_id).first()
            if not model:
                raise ValueError("模型不存在")

            plat = session.query(LLMPlatform).filter_by(id=model.platform_id).first()
            
            if admin_mode:
                if not plat or not plat.is_sys:
                    raise ValueError("此模型不属于系统平台")
            else:
                user_id = str(user_id) if user_id else None
                if not plat or plat.is_sys or plat.user_id != user_id:
                    raise ValueError("无权修改此模型（系统模型或他人模型）")
                if self._is_platform_disabled(session, user_id, plat):
                    raise ValueError("平台已禁用")

            if new_display_name is not None:
                # 检查显示名称在当前平台下唯一（跨平台允许重复）
                existing = session.query(LLModels).filter(
                    LLModels.platform_id == model.platform_id,
                    LLModels.display_name == new_display_name,
                    LLModels.id != model_id
                ).first()
                if existing:
                    raise ValueError(f"显示名称 '{new_display_name}' 已被使用")
                model.display_name = new_display_name

            if new_extra_body is not None:
                cleaned_extra_body = strip_internal_image_generation_fields(new_extra_body)
                model.extra_body = json.dumps(cleaned_extra_body) if cleaned_extra_body else None

            if update_temperature:
                model.temperature = new_temperature

            if update_modalities:
                current_modalities = get_model_modalities(model)
                set_model_modalities(
                    model,
                    input_modalities if input_modalities is not None else current_modalities["input_modalities"],
                    output_modalities if output_modalities is not None else current_modalities["output_modalities"],
                )

            if is_image_generation_model(model):
                if update_image_generation_adapter or not getattr(model, "image_generation_adapter", None):
                    model.image_generation_adapter = _normalize_model_image_generation_adapter(
                        output_modalities=get_model_modalities(model)["output_modalities"],
                        image_generation_adapter=image_generation_adapter,
                        existing_value=getattr(model, "image_generation_adapter", None),
                    )
            else:
                model.image_generation_adapter = None

            if not is_chat_model(model):
                model.temperature = None
                model.sys_credit_input_price_per_million = None
                model.sys_credit_cached_input_price_per_million = None
                model.sys_credit_output_price_per_million = None

            if update_max_context_tokens:
                model.max_context_tokens = _normalize_non_negative_limit(
                    max_context_tokens,
                    field_label="最大上下文",
                    default_value=DEFAULT_MAX_CONTEXT_TOKENS,
                )

            if update_max_output_tokens:
                model.max_output_tokens = _normalize_non_negative_limit(
                    max_output_tokens,
                    field_label="最大单次输出",
                    default_value=DEFAULT_MAX_OUTPUT_TOKENS,
                )

            if admin_mode and update_credit_price and is_chat_model(model):
                if not self.billing_enabled:
                    raise ValueError("请先开启计费系统，再设置模型火柴价格")
                model.sys_credit_input_price_per_million = (
                    None if sys_credit_input_price_per_million is None else max(float(sys_credit_input_price_per_million), 0)
                )
                model.sys_credit_cached_input_price_per_million = (
                    None if sys_credit_cached_input_price_per_million is None else max(float(sys_credit_cached_input_price_per_million), 0)
                )
                model.sys_credit_output_price_per_million = (
                    None if sys_credit_output_price_per_million is None else max(float(sys_credit_output_price_per_million), 0)
                )

            session.commit()
            
            # 如果是系统平台模型，刷新缓存
            if admin_mode:
                self._invalidate_sys_platforms_cache()
            
            return True

    def update_embedding(
        self,
        model_id: int,
        new_display_name: Optional[str] = None,
        new_extra_body: Optional[Dict[str, Any]] = None,
        new_temperature: Optional[float] = None,
        update_temperature: bool = False,
        user_id: str = None,
        admin_mode: bool = False,
        max_context_tokens: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        update_max_context_tokens: bool = False,
        update_max_output_tokens: bool = False,
    ):
        """
        更新 Embedding（统一入口）
        - admin_mode=False: 普通用户更新
        - admin_mode=True: 管理员更新系统平台，不受 USE_SYS_LLM_CONFIG 锁定限制
        """
        self._ensure_mutable(admin_mode=admin_mode)
        with self.Session() as session:
            model = session.query(LLModels).filter_by(id=model_id).first()
            if not model:
                raise ValueError("模型不存在")

            plat = session.query(LLMPlatform).filter_by(id=model.platform_id).first()
            
            if admin_mode:
                if not plat or not plat.is_sys:
                    raise ValueError("此模型不属于系统平台")
            else:
                user_id = str(user_id) if user_id else None
                if not plat or plat.is_sys or plat.user_id != user_id:
                    raise ValueError("无权修改此模型（系统模型或他人模型）")
                if self._is_platform_disabled(session, user_id, plat):
                    raise ValueError("平台已禁用")

            if not is_embedding_model(model):
                raise ValueError("目标模型不是 Embedding")

            if new_display_name is not None:
                # 检查显示名称在当前平台下唯一（跨平台允许重复）
                existing = session.query(LLModels).filter(
                    LLModels.platform_id == model.platform_id,
                    LLModels.display_name == new_display_name,
                    LLModels.id != model_id
                ).first()
                if existing:
                    raise ValueError(f"显示名称 '{new_display_name}' 已被使用")
                model.display_name = new_display_name

            if new_extra_body is not None:
                model.extra_body = json.dumps(new_extra_body) if new_extra_body else None

            if update_temperature:
                model.temperature = new_temperature

            if update_max_context_tokens:
                model.max_context_tokens = _normalize_non_negative_limit(
                    max_context_tokens,
                    field_label="最大上下文",
                    default_value=DEFAULT_MAX_CONTEXT_TOKENS,
                )

            if update_max_output_tokens:
                model.max_output_tokens = _normalize_non_negative_limit(
                    max_output_tokens,
                    field_label="最大单次输出",
                    default_value=DEFAULT_MAX_OUTPUT_TOKENS,
                )

            session.commit()
            
            # 如果是系统平台 Embedding，刷新缓存
            if admin_mode:
                self._invalidate_sys_platforms_cache()
            
            return True

    def disable_model(self, model_id: int, user_id: str = None, admin_mode: bool = False):
        """
        统一的模型/Embedding 删除方法（全部软禁用，保护外键引用）。
        - admin_mode=True: 管理员禁用系统平台下的模型
        - admin_mode=False: 用户禁用自定义平台下的模型，需要 user_id
        不区分 model / embedding，统一处理。

        注意：删除模型不属于"创建/修改"范畴，不受 USE_SYS_LLM_CONFIG 锁定限制。
        """
        # 不调用 _ensure_mutable()：删除 ≠ 创建，锁定模式下也应允许删除
        with self.Session() as session:
            model = session.query(LLModels).filter_by(id=model_id).first()
            if not model:
                raise ValueError("模型不存在")

            plat = session.query(LLMPlatform).filter_by(id=model.platform_id).first()

            if admin_mode:
                if not plat or not plat.is_sys:
                    raise ValueError("此模型不属于系统平台")
            else:
                user_id = str(user_id) if user_id else None
                if not plat or plat.is_sys or plat.user_id != user_id:
                    raise ValueError("无权删除此模型（系统模型或他人模型）")

            model.disable = 1
            session.commit()

            # 系统平台模型需刷新缓存
            if plat and plat.is_sys:
                self._invalidate_sys_platforms_cache()

            return True

    # ==================== 管理员：系统平台管理 ====================
    #
    # ⚠️ 重要说明：系统平台的两种数据源
    #
    # 1. YAML 文件
    #    - matchbox_cfg.yaml：平台结构配置（base_url、models），可进入版本控制
    #    - matchbox_key.yaml：平台 API Key，应被 git 忽略；与结构文件同目录
    #    - 作用：初始化模板、配置分享、备份迁移
    #    - 特点：修改后需重启服务才生效（或通过 admin_import_from_yaml 即时导入）
    #    - 适用场景：无前端环境、快速部署、配置模板分发
    #
    # 2. 数据库 (llm_config.db)
    #    - 作用：运行时的唯一数据源，所有 API 和 GUI 操作都写入数据库
    #    - 特点：修改即时生效，无需重启；支持前端和 API 管理
    #    - 适用场景：生产环境、需要动态修改配置
    #
    # 同步策略：
    #    - 首次启动时，YAML 配置（含 matchbox_key.yaml 中的密钥）初始化到数据库
    #    - 后续启动时，仅添加 YAML 中新增的平台，不覆盖已有配置
    #    - 提供 admin_reload_from_yaml() 方法手动重置为本地 YAML 配置
    #    - 提供 admin_import_from_yaml() 方法从上传文件增量同步系统配置
    #

    def admin_get_sys_platforms(
        self,
        include_disabled: bool = False,
        include_models: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        获取系统平台列表（管理员专用）。
        默认过滤已禁用平台，按 sort_order 排序。

        参数:
            include_disabled: 是否包含已禁用的平台
            include_models: 是否在结果中包含完整的模型列表
                            （含 _db_id、disabled、extra_body、temperature）
                            GUI 使用此参数，避免直接操作 DB Session
        """
        import json as _json
        with self.Session() as session:
            query = session.query(LLMPlatform).filter_by(is_sys=1)
            if not include_disabled:
                query = query.filter(LLMPlatform.disable == 0)
            platforms = query.order_by(LLMPlatform.sort_order, LLMPlatform.id).all()

            sec_mgr = SecurityManager.get_instance()
            results = []

            for plat in platforms:
                # 检查是否有 API Key
                key_info = self._describe_secret_state(plat.api_key, audience="system_managed")
                api_key_set = bool(key_info["available"])
                api_key_raw = ""
                if plat.api_key:
                    decrypted = sec_mgr.decrypt(plat.api_key)
                    if decrypted.has_plaintext:
                        api_key_raw = decrypted.value

                # 统计模型数量（仅启用的）
                model_count = len([m for m in plat.models if is_chat_model(m) and not self._is_model_disabled(m)])
                embedding_count = len([m for m in plat.models if is_embedding_model(m) and not self._is_model_disabled(m)])

                entry: Dict[str, Any] = {
                    "platform_id": plat.id,
                    "platform_key": plat.platform_key,
                    "name": plat.name,
                    "base_url": plat.base_url,
                    "recharge_url": plat.recharge_url,
                    "api_key_set": api_key_set,
                    "api_key_status": key_info["status"],
                    "api_key_message": key_info["message"],
                    "sys_credit_balance": plat.sys_credit_balance,
                    "model_count": model_count,
                    "embedding_count": embedding_count,
                    "disabled": int(bool(plat.disable)),
                    "sort_order": plat.sort_order,
                }

                if include_models:
                    # 返回完整模型列表，GUI 用此替代直接 session.query
                    entry["api_key"] = api_key_raw
                    models_list = []
                    for m in _models_in_sort_order(plat.models):
                        raw_extra_body = None
                        if m.extra_body:
                            try:
                                parsed_extra_body = _json.loads(m.extra_body)
                                raw_extra_body = parsed_extra_body if isinstance(parsed_extra_body, dict) else None
                            except Exception:
                                pass
                        extra_body = strip_internal_image_generation_fields(raw_extra_body)
                        models_list.append({
                            "_db_id": m.id,
                            "display_name": m.display_name,
                            "model_name": m.model_name,
                            **get_model_modalities(m),
                            "image_generation_adapter": _normalize_model_image_generation_adapter(
                                output_modalities=get_model_modalities(m)["output_modalities"],
                                image_generation_adapter=getattr(m, "image_generation_adapter", None),
                            ),
                            "disabled": bool(m.disable),
                            "temperature": m.temperature,
                            "max_context_tokens": int(m.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS),
                            "max_output_tokens": int(m.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS),
                            "extra_body": extra_body,
                            "sys_credit_input_price_per_million": m.sys_credit_input_price_per_million,
                            "sys_credit_cached_input_price_per_million": m.sys_credit_cached_input_price_per_million,
                            "sys_credit_output_price_per_million": m.sys_credit_output_price_per_million,
                            "sort_order": m.sort_order,
                        })
                    entry["models"] = models_list

                results.append(entry)

            return results

    def admin_add_sys_platform(
        self,
        name: str,
        base_url: str,
        api_key: Optional[str] = None,
        sys_credit_balance: Optional[float] = None,
        recharge_url: Optional[str] = None,
        platform_key: Optional[str] = None,
    ) -> LLMPlatform:
        """
        添加系统平台（管理员专用）
        直接写入数据库，即时生效，无需重启服务
        """
        if not (name and base_url):
            raise ValueError("name / base_url 必填")
        
        base_url = normalize_base_url(base_url)
        recharge_url = normalize_recharge_url(recharge_url)
        supplied_platform_key = normalize_platform_key(platform_key)
        platform_key = supplied_platform_key or generate_platform_key()
        
        with self.Session() as session:
            # 只有调用方明确提供原 platform_key 时才允许复活已禁用平台。
            existing_key = session.query(LLMPlatform).filter_by(platform_key=platform_key).first()
            if existing_key:
                if not supplied_platform_key or not (existing_key.is_sys and existing_key.disable):
                    raise ValueError(f"platform_key '{platform_key}' 已存在")
                existing_name = self._find_active_platform_with_name(
                    session,
                    name=name,
                    exclude_platform_id=existing_key.id,
                )
                if existing_name:
                    raise ValueError(f"平台名称 '{name}' 已存在")
                existing_key.name = name
                existing_key.base_url = base_url
                existing_key.disable = 0
                existing_key.recharge_url = recharge_url
                existing_key.sys_credit_balance = _normalize_nullable_credit_balance(sys_credit_balance)
                if api_key:
                    existing_key.api_key = SecurityManager.get_instance().encrypt(api_key)
                session.commit()
                self._invalidate_sys_platforms_cache()
                return existing_key

            # 活动平台名称全局唯一；活动记录优先于历史禁用记录。
            if self._find_active_platform_with_name(session, name=name):
                raise ValueError(f"平台名称 '{name}' 已存在")

            # 未提供稳定身份时，仅复活同名、同 URL 的禁用系统平台。
            if not supplied_platform_key:
                revived = self._find_disabled_platform_for_revival(
                    session,
                    name=name,
                    base_url=base_url,
                    is_sys=True,
                )
                if revived:
                    revived.disable = 0
                    revived.recharge_url = recharge_url
                    revived.sys_credit_balance = _normalize_nullable_credit_balance(sys_credit_balance)
                    if api_key:
                        revived.api_key = SecurityManager.get_instance().encrypt(api_key)
                    session.commit()
                    self._invalidate_sys_platforms_cache()
                    return revived

            # 相同 URL 允许对应多个平台。
            # 加密 API Key
            encrypted_key = None
            if api_key:
                encrypted_key = SecurityManager.get_instance().encrypt(api_key)
            
            plat = LLMPlatform(
                name=name,
                platform_key=platform_key,
                base_url=base_url,
                recharge_url=recharge_url,
                api_key=encrypted_key,
                user_id=SYSTEM_USER_ID,
                is_sys=1,
                sys_credit_balance=_normalize_nullable_credit_balance(sys_credit_balance),
            )
            session.add(plat)
            session.commit()
            
            # 刷新缓存
            self._invalidate_sys_platforms_cache()
            
            return plat

    def admin_update_sys_platform(
        self,
        platform_id: int,
        new_name: Optional[str] = None,
        new_base_url: Optional[str] = None,
        sys_credit_balance: Optional[float] = None,
        update_sys_credit_balance: bool = False,
        recharge_url: Optional[str] = None,
        update_recharge_url: bool = False,
    ) -> bool:
        """
        更新系统平台信息（管理员专用）
        """
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
            if not plat:
                raise ValueError("系统平台不存在")
            
            if new_name is not None:
                # 检查名称全局唯一性
                existing = self._find_active_platform_with_name(
                    session,
                    name=new_name,
                    exclude_platform_id=platform_id,
                )
                if existing:
                    raise ValueError(f"平台名称 '{new_name}' 已被使用")
                plat.name = new_name
            
            if new_base_url is not None:
                new_base_url = normalize_base_url(new_base_url)
                plat.base_url = new_base_url

            if update_sys_credit_balance:
                plat.sys_credit_balance = _normalize_nullable_credit_balance(sys_credit_balance)

            if update_recharge_url:
                plat.recharge_url = normalize_recharge_url(recharge_url)

            session.commit()
            
            # 刷新缓存
            self._invalidate_sys_platforms_cache()
            
            return True

    def admin_update_sys_platform_api_key(
        self,
        platform_id: int,
        api_key: Optional[str],
    ) -> bool:
        """
        更新系统平台的默认 API Key（管理员专用）
        此 Key 作为系统默认 Key，当用户未设置自己的 Key 且 LLM_AUTO_KEY=True 时使用
        """
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
            if not plat:
                raise ValueError("系统平台不存在")
            
            if api_key:
                plat.api_key = SecurityManager.get_instance().encrypt(api_key)
            else:
                plat.api_key = None
            
            session.commit()
            
            # 刷新缓存
            self._invalidate_sys_platforms_cache()
            
            return True

    def admin_set_sys_platform_default(self, platform_id: int) -> bool:
        """
        将指定系统平台设为默认（sort_order=0），其余平台 sort_order 递增。
        持久化到数据库，重启后保持顺序。
        """
        with self.Session() as session:
            target = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
            if not target:
                raise ValueError("系统平台不存在")

            # 获取所有未禁用的系统平台，按当前 sort_order 排序
            all_plats = (
                session.query(LLMPlatform)
                .filter_by(is_sys=1)
                .filter(LLMPlatform.disable == 0)
                .order_by(LLMPlatform.sort_order, LLMPlatform.id)
                .all()
            )

            # 目标平台排第一，其余保持原相对顺序
            target.sort_order = 0
            order = 1
            for p in all_plats:
                if p.id == platform_id:
                    continue
                p.sort_order = order
                order += 1

            session.commit()

            self._refresh_runtime_default_ids(session)

            # 刷新缓存
            self._invalidate_sys_platforms_cache()

            return True

    def admin_reorder_sys_platforms(self, ordered_ids: List[int]) -> bool:
        """
        按给定 ID 列表重新排序系统平台。
        ordered_ids 中的第一个 ID sort_order=0，以此类推。
        """
        with self.Session() as session:
            for idx, pid in enumerate(ordered_ids):
                plat = session.query(LLMPlatform).filter_by(id=pid, is_sys=1).first()
                if plat:
                    plat.sort_order = idx
            session.commit()

            self._refresh_runtime_default_ids(session)

            self._invalidate_sys_platforms_cache()

            return True

    def admin_reorder_sys_models(self, platform_id: int, ordered_ids: List[int]) -> bool:
        """
        按给定 ID 列表重新排序指定平台下的模型。
        """
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
            if not plat:
                raise ValueError("系统平台不存在")
            for idx, mid in enumerate(ordered_ids):
                model = session.query(LLModels).filter_by(id=mid, platform_id=platform_id).first()
                if model:
                    model.sort_order = idx
            session.commit()

            self._refresh_runtime_default_ids(session)

            self._invalidate_sys_platforms_cache()

            return True

    def admin_sync_platform_models(
        self,
        platform_id: int,
        models_config: List[Dict[str, Any]],
    ) -> bool:
        """
        增量同步平台的模型配置（替代全删重建，保护外键引用）。

        models_config 格式：
        [
            {
                "model_name": "gpt-4o",
                "display_name": "GPT-4o",
                "extra_body": {...} or None,
                "image_generation_adapter": "openai_images" or None,
                "temperature": 0.7 or None,
                "max_context_tokens": 256000,
                "max_output_tokens": 64000,
                "sys_credit_input_price_per_million": 100000 or None,
                "sys_credit_cached_input_price_per_million": 25000 or None,
                "sys_credit_output_price_per_million": 400000 or None,
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "sort_order": 0,
            },
            ...
        ]

        同步策略：
        - 按 model_name + 输入/输出模态匹配已有模型
        - 匹配到 → 更新属性（保留 model.id，外键不断裂）
        - 未匹配到 → 新增
        - 旧列表中有但新列表中没有 → 设 disable=1
        """
        with self.Session() as session:
            plat = session.query(LLMPlatform).filter_by(id=platform_id).first()
            if not plat:
                raise ValueError(f"平台 ID={platform_id} 不存在")

            existing_models = session.query(LLModels).filter_by(platform_id=platform_id).all()

            # 索引：(model_name, input_modalities, output_modalities) → LLModels
            existing_map = {}
            for m in existing_models:
                modalities = get_model_modalities(m)
                key = (
                    m.model_name,
                    tuple(modalities["input_modalities"]),
                    tuple(modalities["output_modalities"]),
                )
                existing_map[key] = m

            seen_keys = set()

            for idx, cfg in enumerate(models_config):
                model_name = cfg.get("model_name", "")
                display_name = cfg.get("display_name", model_name)
                extra_body = cfg.get("extra_body")
                temperature = cfg.get("temperature")
                input_modalities, output_modalities = normalize_model_modalities(
                    cfg.get("input_modalities"),
                    cfg.get("output_modalities"),
                )
                sort_order = cfg.get("sort_order", idx)
                # 兼容旧字段名 sys_credit_price_per_million_tokens（自动拆分为输入/输出同值）
                has_input_price = "sys_credit_input_price_per_million" in cfg
                has_cached_input_price = "sys_credit_cached_input_price_per_million" in cfg
                has_output_price = "sys_credit_output_price_per_million" in cfg
                has_legacy_price = "sys_credit_price_per_million_tokens" in cfg
                model_input_price = cfg.get("sys_credit_input_price_per_million") if has_input_price else None
                model_cached_input_price = cfg.get("sys_credit_cached_input_price_per_million") if has_cached_input_price else None
                model_output_price = cfg.get("sys_credit_output_price_per_million") if has_output_price else None
                if not has_input_price and not has_output_price and has_legacy_price:
                    legacy_val = cfg.get("sys_credit_price_per_million_tokens")
                    model_input_price = legacy_val
                    model_output_price = legacy_val
                has_max_context_field = "max_context_tokens" in cfg
                has_max_output_field = "max_output_tokens" in cfg
                model_max_context = cfg.get("max_context_tokens") if has_max_context_field else None
                model_max_output = cfg.get("max_output_tokens") if has_max_output_field else None

                cleaned_extra_body = strip_internal_image_generation_fields(extra_body)
                key = (model_name, tuple(input_modalities), tuple(output_modalities))
                image_generation_adapter = _normalize_model_image_generation_adapter(
                    output_modalities=output_modalities,
                    image_generation_adapter=cfg.get("image_generation_adapter"),
                    existing_value=getattr(existing_map.get(key), "image_generation_adapter", None),
                )
                extra_body_json = json.dumps(cleaned_extra_body) if cleaned_extra_body else None
                seen_keys.add(key)

                existing = existing_map.get(key)
                if existing:
                    # 更新已有模型（保留 ID）
                    existing.display_name = display_name
                    set_model_modalities(existing, input_modalities, output_modalities)
                    existing.extra_body = extra_body_json
                    existing.image_generation_adapter = image_generation_adapter
                    existing.temperature = temperature
                    if has_input_price or has_cached_input_price or has_output_price or has_legacy_price:
                        existing.sys_credit_input_price_per_million = (
                            None if model_input_price is None else max(float(model_input_price), 0)
                        )
                        existing.sys_credit_cached_input_price_per_million = (
                            None if model_cached_input_price is None else max(float(model_cached_input_price), 0)
                        )
                        existing.sys_credit_output_price_per_million = (
                            None if model_output_price is None else max(float(model_output_price), 0)
                        )
                    if has_max_context_field:
                        existing.max_context_tokens = _normalize_non_negative_limit(
                            model_max_context,
                            field_label="最大上下文",
                            default_value=DEFAULT_MAX_CONTEXT_TOKENS,
                        )
                    if has_max_output_field:
                        existing.max_output_tokens = _normalize_non_negative_limit(
                            model_max_output,
                            field_label="最大单次输出",
                            default_value=DEFAULT_MAX_OUTPUT_TOKENS,
                        )
                    existing.sort_order = sort_order
                    existing.disable = 0  # 如果之前被禁用，同步时复活
                else:
                    # 新增模型
                    new_model = LLModels(
                        platform_id=platform_id,
                        model_name=model_name,
                        display_name=display_name,
                        extra_body=extra_body_json,
                        image_generation_adapter=image_generation_adapter,
                        temperature=temperature,
                        sys_credit_input_price_per_million=(
                            None if model_input_price is None else max(float(model_input_price), 0)
                        ),
                        sys_credit_cached_input_price_per_million=(
                            None if model_cached_input_price is None else max(float(model_cached_input_price), 0)
                        ),
                        sys_credit_output_price_per_million=(
                            None if model_output_price is None else max(float(model_output_price), 0)
                        ),
                        max_context_tokens=_normalize_non_negative_limit(
                            model_max_context,
                            field_label="最大上下文",
                            default_value=DEFAULT_MAX_CONTEXT_TOKENS,
                        ),
                        max_output_tokens=_normalize_non_negative_limit(
                            model_max_output,
                            field_label="最大单次输出",
                            default_value=DEFAULT_MAX_OUTPUT_TOKENS,
                        ),
                        sort_order=sort_order,
                    )
                    set_model_modalities(new_model, input_modalities, output_modalities)
                    session.add(new_model)

            # 旧列表中有但新列表中没有 → 禁用
            for key, model in existing_map.items():
                if key not in seen_keys:
                    model.disable = 1

            session.commit()

            # 如果是系统平台，刷新缓存
            if plat.is_sys:
                self._invalidate_sys_platforms_cache()

            return True

    def admin_update_sys_model(
        self,
        model_id: int,
        display_name=None,
        extra_body=None,
        temperature=None,
        sys_credit_input_price_per_million: Optional[float] = None,
        sys_credit_cached_input_price_per_million: Optional[float] = None,
        sys_credit_output_price_per_million: Optional[float] = None,
        update_credit_price: bool = False,
        input_modalities: Optional[List[str]] = None,
        output_modalities: Optional[List[str]] = None,
        update_modalities: bool = False,
        image_generation_adapter: Optional[str] = None,
        update_image_generation_adapter: bool = False,
        max_context_tokens: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        update_max_context_tokens: bool = False,
        update_max_output_tokens: bool = False,
    ) -> bool:
        """
        更新系统平台下的模型属性（管理员专用便捷方法）。

        参数:
            model_id: 模型数据库 ID
            display_name: 新的显示名称（可选）
            extra_body: 新的 extra_body 字典（可选，None 表示清除）
            temperature: 新的 temperature（可选，None 表示清除）
            input_modalities: 模型输入模态。
            output_modalities: 模型输出模态。
        """
        update_temperature = temperature is not None

        return self.update_model(
            model_id=model_id,
            new_display_name=display_name,
            new_extra_body=extra_body,
            new_temperature=temperature,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            sys_credit_input_price_per_million=sys_credit_input_price_per_million,
            sys_credit_cached_input_price_per_million=sys_credit_cached_input_price_per_million,
            sys_credit_output_price_per_million=sys_credit_output_price_per_million,
            update_credit_price=update_credit_price,
            update_temperature=update_temperature,
            update_max_context_tokens=update_max_context_tokens,
            update_max_output_tokens=update_max_output_tokens,
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            update_modalities=update_modalities,
            image_generation_adapter=image_generation_adapter,
            update_image_generation_adapter=update_image_generation_adapter,
            admin_mode=True,
        )

    def admin_get_user_quota_policy(self, user_id: str) -> Dict[str, Any]:
        """管理员读取指定用户配额策略。"""
        return self.get_user_quota_policy(user_id)

    def admin_save_user_quota_policy(self, user_id: str, **kwargs: Any) -> Dict[str, Any]:
        """管理员保存指定用户配额策略。"""
        return self.save_user_quota_policy(user_id, **kwargs)

    def admin_get_user_quota_status(self, user_id: str) -> Dict[str, Any]:
        """管理员查看指定用户配额状态（策略 + 已用量 + 剩余额度）。"""
        return self.get_user_quota_status(user_id)
