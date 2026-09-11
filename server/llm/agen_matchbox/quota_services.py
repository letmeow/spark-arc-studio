"""
配额服务 Mixin

集中处理：
1. 用户配额策略读写
2. sys_paid / self_paid 用量汇总
3. 调用前配额拦截
"""

from datetime import datetime, timedelta, UTC
from typing import Optional, Dict, Any

from sqlalchemy import func

from .models import UsageLogEntry, UserQuotaPolicy


class QuotaExceededError(ValueError):
    """用户配额超限。"""


class QuotaServicesMixin:
    """配额配置、统计与拦截功能。"""

    _QUOTA_SCOPES = ("sys_paid", "self_paid")
    _QUOTA_POLICY_FIELDS = (
        "sys_paid_window_hours",
        "sys_paid_window_token_limit",
        "sys_paid_window_request_limit",
        "sys_paid_total_token_limit",
        "sys_paid_total_request_limit",
        "self_paid_window_hours",
        "self_paid_window_token_limit",
        "self_paid_window_request_limit",
        "self_paid_total_token_limit",
        "self_paid_total_request_limit",
    )

    @classmethod
    def _normalize_quota_scope(cls, quota_scope: Optional[str]) -> Optional[str]:
        if quota_scope is None:
            return None
        normalized = str(quota_scope).strip().lower()
        if not normalized or normalized == "total":
            return None
        if normalized not in cls._QUOTA_SCOPES:
            raise ValueError("quota_scope 仅支持 'sys_paid'、'self_paid' 或 'total'")
        return normalized

    @staticmethod
    def _sanitize_quota_int(value: Any, *, field_name: str, allow_zero: bool = True) -> Optional[int]:
        if value is None or value == "":
            return None
        parsed = int(value)
        minimum = 0 if allow_zero else 1
        if parsed < minimum:
            raise ValueError(f"{field_name} 不能小于 {minimum}")
        return parsed

    def _serialize_quota_policy(self, policy: Optional[UserQuotaPolicy], user_id: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"user_id": str(user_id)}
        for field_name in self._QUOTA_POLICY_FIELDS:
            payload[field_name] = getattr(policy, field_name, None) if policy else None
        return payload

    def _get_or_create_quota_policy(self, session, user_id: str) -> UserQuotaPolicy:
        user_id = str(user_id)
        policy = session.query(UserQuotaPolicy).filter_by(user_id=user_id).first()
        if not policy:
            policy = UserQuotaPolicy(user_id=user_id)
            session.add(policy)
            session.flush()
        return policy

    def _query_quota_usage_summary(
        self,
        session,
        user_id: str,
        quota_scope: Optional[str] = None,
        since: Optional[timedelta] = None,
    ) -> Dict[str, Any]:
        normalized_scope = self._normalize_quota_scope(quota_scope)
        query = session.query(
            func.coalesce(func.sum(UsageLogEntry.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(UsageLogEntry.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageLogEntry.completion_tokens), 0).label("completion_tokens"),
            func.count(UsageLogEntry.id).label("requests"),
            func.coalesce(func.sum(1 - UsageLogEntry.success), 0).label("errors"),
        ).filter(UsageLogEntry.user_id == str(user_id))

        if normalized_scope is not None:
            query = query.filter(UsageLogEntry.quota_scope == normalized_scope)

        if since is not None:
            cutoff = datetime.now(UTC) - since
            query = query.filter(UsageLogEntry.created_at >= cutoff)

        result = query.first()
        return {
            "tokens": int(result.tokens or 0),
            "prompt_tokens": int(result.prompt_tokens or 0),
            "completion_tokens": int(result.completion_tokens or 0),
            "requests": int(result.requests or 0),
            "errors": int(result.errors or 0),
        }

    @staticmethod
    def _calc_remaining(limit_value: Optional[int], used_value: int) -> Optional[int]:
        if limit_value is None:
            return None
        return max(int(limit_value) - int(used_value), 0)

    def _build_quota_scope_status(self, session, user_id: str, policy: Optional[UserQuotaPolicy], quota_scope: str) -> Dict[str, Any]:
        prefix = quota_scope
        window_hours = getattr(policy, f"{prefix}_window_hours", None) if policy else None
        window_token_limit = getattr(policy, f"{prefix}_window_token_limit", None) if policy else None
        window_request_limit = getattr(policy, f"{prefix}_window_request_limit", None) if policy else None
        total_token_limit = getattr(policy, f"{prefix}_total_token_limit", None) if policy else None
        total_request_limit = getattr(policy, f"{prefix}_total_request_limit", None) if policy else None

        total_usage = self._query_quota_usage_summary(session, user_id, quota_scope=quota_scope, since=None)
        window_usage = None
        if window_hours is not None:
            window_usage = self._query_quota_usage_summary(
                session,
                user_id,
                quota_scope=quota_scope,
                since=timedelta(hours=int(window_hours)),
            )

        return {
            "quota_scope": quota_scope,
            "window_hours": window_hours,
            "window": {
                "token_limit": window_token_limit,
                "request_limit": window_request_limit,
                "usage": window_usage,
                "token_remaining": self._calc_remaining(window_token_limit, window_usage["tokens"]) if window_usage else None,
                "request_remaining": self._calc_remaining(window_request_limit, window_usage["requests"]) if window_usage else None,
                "token_exceeded": bool(window_usage is not None and window_token_limit is not None and window_usage["tokens"] >= int(window_token_limit)),
                "request_exceeded": bool(window_usage is not None and window_request_limit is not None and window_usage["requests"] >= int(window_request_limit)),
            },
            "total": {
                "token_limit": total_token_limit,
                "request_limit": total_request_limit,
                "usage": total_usage,
                "token_remaining": self._calc_remaining(total_token_limit, total_usage["tokens"]),
                "request_remaining": self._calc_remaining(total_request_limit, total_usage["requests"]),
                "token_exceeded": bool(total_token_limit is not None and total_usage["tokens"] >= int(total_token_limit)),
                "request_exceeded": bool(total_request_limit is not None and total_usage["requests"] >= int(total_request_limit)),
            },
        }

    def get_user_quota_policy(self, user_id: str) -> Dict[str, Any]:
        user_id = str(user_id)
        with self.Session() as session:
            policy = session.query(UserQuotaPolicy).filter_by(user_id=user_id).first()
            return self._serialize_quota_policy(policy, user_id)

    def save_user_quota_policy(self, user_id: str, **kwargs: Any) -> Dict[str, Any]:
        user_id = str(user_id)
        with self.Session() as session:
            policy = self._get_or_create_quota_policy(session, user_id)
            for field_name in self._QUOTA_POLICY_FIELDS:
                if field_name not in kwargs:
                    continue
                allow_zero = field_name != "sys_paid_window_hours" and field_name != "self_paid_window_hours"
                sanitized = self._sanitize_quota_int(kwargs.get(field_name), field_name=field_name, allow_zero=allow_zero)
                setattr(policy, field_name, sanitized)
            session.commit()
            return self._serialize_quota_policy(policy, user_id)

    def get_user_quota_status(self, user_id: str) -> Dict[str, Any]:
        user_id = str(user_id)
        with self.Session() as session:
            policy = session.query(UserQuotaPolicy).filter_by(user_id=user_id).first()
            return {
                "policy": self._serialize_quota_policy(policy, user_id),
                "sys_paid": self._build_quota_scope_status(session, user_id, policy, "sys_paid"),
                "self_paid": self._build_quota_scope_status(session, user_id, policy, "self_paid"),
                "total": self._query_quota_usage_summary(session, user_id, quota_scope=None, since=None),
            }

    def enforce_user_quota(self, session, user_id: str, quota_scope: Optional[str]) -> None:
        """调用前配额拦截。

        执行顺序（与点数拦截的关系）：
        1. 配额（quota）先行：窗口/总量 token 与请求次数上限；
        2. 点数（credit）随后：见 ``CreditServicesMixin.enforce_user_credit``，
           由 ``builder.get_user_llm`` 在同一事务内按配额→点数顺序调用。
        """
        normalized_scope = self._normalize_quota_scope(quota_scope)
        if normalized_scope is None:
            return

        if normalized_scope == "self_paid":
            # 不该拦截用户自费的请求，无权限制用户自己的消费。
            return

        user_id = str(user_id)
        policy = session.query(UserQuotaPolicy).filter_by(user_id=user_id).first()
        if not policy:
            return

        prefix = normalized_scope
        window_hours = getattr(policy, f"{prefix}_window_hours", None)
        window_token_limit = getattr(policy, f"{prefix}_window_token_limit", None)
        window_request_limit = getattr(policy, f"{prefix}_window_request_limit", None)
        total_token_limit = getattr(policy, f"{prefix}_total_token_limit", None)
        total_request_limit = getattr(policy, f"{prefix}_total_request_limit", None)

        if window_hours is not None and (window_token_limit is not None or window_request_limit is not None):
            window_usage = self._query_quota_usage_summary(
                session,
                user_id,
                quota_scope=normalized_scope,
                since=timedelta(hours=int(window_hours)),
            )
            if window_token_limit is not None and window_usage["tokens"] >= int(window_token_limit):
                raise QuotaExceededError(f"用户 '{user_id}' 已达到 {normalized_scope} 的 {window_hours} 小时 token 配额上限")
            if window_request_limit is not None and window_usage["requests"] >= int(window_request_limit):
                raise QuotaExceededError(f"用户 '{user_id}' 已达到 {normalized_scope} 的 {window_hours} 小时请求次数上限")

        if total_token_limit is not None or total_request_limit is not None:
            total_usage = self._query_quota_usage_summary(session, user_id, quota_scope=normalized_scope, since=None)
            if total_token_limit is not None and total_usage["tokens"] >= int(total_token_limit):
                raise QuotaExceededError(f"用户 '{user_id}' 已达到 {normalized_scope} 的总 token 配额上限")
            if total_request_limit is not None and total_usage["requests"] >= int(total_request_limit):
                raise QuotaExceededError(f"用户 '{user_id}' 已达到 {normalized_scope} 的总请求次数上限")
