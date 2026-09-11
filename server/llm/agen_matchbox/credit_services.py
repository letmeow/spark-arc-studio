"""
点数服务 Mixin

用于统一管理：
1. 系统模型定价
2. 用户系统点数账户
3. 调用前余额检查
4. 调用后实际扣点与流水

注意：
- 仅对 sys_paid 生效
- self_paid 只统计，不做额度控制
"""

from __future__ import annotations

from typing import Optional, Dict, Any, List

from sqlalchemy import func

from .models import UserCreditAccount, UserCreditLedger, UsageLogEntry, LLModels, LLMPlatform, is_chat_model


class CreditBalanceExceededError(ValueError):
    """用户系统点数余额不足。"""


def _normalize_billing_scope(billing_scope: Optional[str]) -> Optional[str]:
    if billing_scope is None:
        return None
    normalized = str(billing_scope).strip().lower()
    if not normalized:
        return None
    if normalized not in {"sys_paid", "self_paid"}:
        raise ValueError("billing_scope 仅支持 'sys_paid' 或 'self_paid'")
    return normalized


def calculate_credit_cost(
    input_price_per_million: Optional[float],
    output_price_per_million: Optional[float],
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_prompt_tokens: int = 0,
    cached_input_price_per_million: Optional[float] = None,
) -> float:
    """按未命中输入、缓存命中输入、输出三段价格精确计算点数消耗。"""
    input_price = max(float(input_price_per_million or 0), 0)
    cached_input_price = input_price if cached_input_price_per_million is None else max(float(cached_input_price_per_million or 0), 0)
    output_price = max(float(output_price_per_million or 0), 0)
    p_tokens = max(int(prompt_tokens), 0)
    cached_tokens = min(max(int(cached_prompt_tokens or 0), 0), p_tokens)
    uncached_tokens = max(p_tokens - cached_tokens, 0)
    c_tokens = max(int(completion_tokens), 0)
    input_cost = uncached_tokens * input_price / 1_000_000
    cached_input_cost = cached_tokens * cached_input_price / 1_000_000
    output_cost = c_tokens * output_price / 1_000_000
    return input_cost + cached_input_cost + output_cost


def resolve_input_price_per_million(model: Optional[LLModels]) -> float:
    """获取模型输入价格，None 视为 0（免费）。"""
    val = getattr(model, "sys_credit_input_price_per_million", None)
    return max(float(val), 0) if val is not None else 0.0


def resolve_output_price_per_million(model: Optional[LLModels]) -> float:
    """获取模型输出价格，None 视为 0（免费）。"""
    val = getattr(model, "sys_credit_output_price_per_million", None)
    return max(float(val), 0) if val is not None else 0.0


def resolve_cached_input_price_per_million(model: Optional[LLModels], fallback_input_price: Optional[float] = None) -> float:
    """获取模型缓存命中输入价格，未配置时回退到普通输入价格。"""
    val = getattr(model, "sys_credit_cached_input_price_per_million", None)
    if val is None:
        return max(float(fallback_input_price or 0), 0)
    return max(float(val), 0)


def _settle_usage_entry_credit(session, usage_entry: UsageLogEntry, *, billing_enabled: bool) -> float:
    """对单条 usage 记录进行系统点数结算。"""
    if not billing_enabled:
        usage_entry.credit_cost = None
        return 0

    billing_scope = _normalize_billing_scope(getattr(usage_entry, "quota_scope", None))
    if billing_scope != "sys_paid":
        usage_entry.credit_cost = None
        return 0

    model = session.query(LLModels).filter_by(id=usage_entry.model_id).first()
    if not model:
        usage_entry.credit_cost = 0
        return 0

    if (
        model.sys_credit_input_price_per_million is None
        or model.sys_credit_output_price_per_million is None
    ):
        usage_entry.credit_cost = None
        return 0

    input_price = resolve_input_price_per_million(model)
    cached_input_price = resolve_cached_input_price_per_million(model, input_price)
    output_price = resolve_output_price_per_million(model)
    if input_price == 0 and cached_input_price == 0 and output_price == 0:
        usage_entry.credit_cost = 0
        return 0

    cost = calculate_credit_cost(
        input_price,
        output_price,
        prompt_tokens=int(usage_entry.prompt_tokens or 0),
        completion_tokens=int(usage_entry.completion_tokens or 0),
        cached_prompt_tokens=int(usage_entry.cached_prompt_tokens or 0),
        cached_input_price_per_million=cached_input_price,
    )
    usage_entry.credit_cost = cost

    # 并发安全：用量写入已收敛到后台单线程（见 usage_writer），此处再叠加
    # 行级锁，保证同一账户/平台在多进程部署下也不会丢扣。
    # SQLite 不支持 SELECT ... FOR UPDATE，PG 下才加锁，行为按方言降级。
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", "")
    account_query = session.query(UserCreditAccount).filter_by(
        user_id=str(usage_entry.user_id),
        billing_scope="sys_paid",
    )
    platform_query = session.query(LLMPlatform).filter_by(id=model.platform_id)
    if dialect == "postgresql":
        account_query = account_query.with_for_update()
        platform_query = platform_query.with_for_update()
    account = account_query.first()
    if not account:
        account = UserCreditAccount(user_id=str(usage_entry.user_id), billing_scope="sys_paid")
        session.add(account)
        session.flush()

    account.credit_balance = float(account.credit_balance or 0) - cost
    account.credit_total_used = float(account.credit_total_used or 0) + cost

    platform = platform_query.first()
    if platform is not None and platform.sys_credit_balance is not None:
        platform.sys_credit_balance = float(platform.sys_credit_balance or 0) - cost

    ledger = UserCreditLedger(
        user_id=str(usage_entry.user_id),
        billing_scope="sys_paid",
        delta_credit=-cost,
        balance_after=float(account.credit_balance or 0),
        reason_type="consume",
        platform_id=model.platform_id,
        model_id=model.id,
        usage_log_id=usage_entry.id,
        remark=f"usage_log:{usage_entry.id}",
    )
    session.add(ledger)
    return cost


def settle_usage_entry_credit(session, usage_entry: UsageLogEntry, *, billing_enabled: bool = True) -> float:
    """对单条 usage 记录进行系统点数结算。"""
    return _settle_usage_entry_credit(session, usage_entry, billing_enabled=billing_enabled)


class CreditServicesMixin:
    """点数账户、定价与结算功能。"""

    def _get_or_create_credit_account(self, session, user_id: str, billing_scope: str = "sys_paid") -> UserCreditAccount:
        scope = _normalize_billing_scope(billing_scope)
        account = session.query(UserCreditAccount).filter_by(user_id=str(user_id), billing_scope=scope).first()
        if not account:
            account = UserCreditAccount(user_id=str(user_id), billing_scope=scope)
            session.add(account)
            session.flush()
        return account

    def _serialize_credit_account(self, account: Optional[UserCreditAccount], user_id: str, billing_scope: str = "sys_paid") -> Dict[str, Any]:
        return {
            "user_id": str(user_id),
            "billing_scope": billing_scope,
            "credit_balance": float(getattr(account, "credit_balance", 0) or 0),
            "credit_total_granted": float(getattr(account, "credit_total_granted", 0) or 0),
            "credit_total_used": float(getattr(account, "credit_total_used", 0) or 0),
            "status": getattr(account, "status", "active") if account else "active",
            "updated_at": getattr(account, "updated_at", None).isoformat() if getattr(account, "updated_at", None) else None,
        }

    def list_model_credit_pricing(self, billing_scope: str = "sys_paid") -> List[Dict[str, Any]]:
        scope = _normalize_billing_scope(billing_scope)
        with self.Session() as session:
            rows = (
                session.query(LLModels, LLMPlatform)
                .join(LLMPlatform, LLMPlatform.id == LLModels.platform_id)
                .filter(LLMPlatform.is_sys == 1)
                .all()
            )
            result: List[Dict[str, Any]] = []
            for model, platform in rows:
                if not is_chat_model(model):
                    continue
                result.append({
                    "platform_id": platform.id,
                    "model_id": model.id,
                    "billing_scope": scope,
                    "model_input_price_per_million": model.sys_credit_input_price_per_million,
                    "model_cached_input_price_per_million": model.sys_credit_cached_input_price_per_million,
                    "model_output_price_per_million": model.sys_credit_output_price_per_million,
                    "display_name": model.display_name,
                    "model_name": model.model_name,
                    "platform_name": platform.name,
                })
            return result

    def save_model_credit_pricing(
        self,
        platform_id: int,
        model_id: int,
        *,
        billing_scope: str = "sys_paid",
        model_input_price_per_million: Optional[int] = None,
        model_cached_input_price_per_million: Optional[int] = None,
        model_output_price_per_million: Optional[int] = None,
        remark: Optional[str] = None,
    ) -> Dict[str, Any]:
        scope = _normalize_billing_scope(billing_scope)
        if scope != "sys_paid":
            raise ValueError("当前仅支持为 sys_paid 配置模型点数定价")
        if not getattr(self, "billing_enabled", False):
            raise ValueError("请先开启计费系统，再设置模型火柴价格")

        with self.Session() as session:
            platform = session.query(LLMPlatform).filter_by(id=platform_id, is_sys=1).first()
            model = session.query(LLModels).filter_by(id=model_id, platform_id=platform_id).first()
            if not platform or not model:
                raise ValueError("系统平台或模型不存在")

            if model_input_price_per_million is not None:
                model.sys_credit_input_price_per_million = max(float(model_input_price_per_million), 0)
            if model_cached_input_price_per_million is not None:
                model.sys_credit_cached_input_price_per_million = max(float(model_cached_input_price_per_million), 0)
            if model_output_price_per_million is not None:
                model.sys_credit_output_price_per_million = max(float(model_output_price_per_million), 0)
            session.commit()

            return {
                "platform_id": platform.id,
                "model_id": model.id,
                "billing_scope": scope,
                "model_input_price_per_million": model.sys_credit_input_price_per_million,
                "model_cached_input_price_per_million": model.sys_credit_cached_input_price_per_million,
                "model_output_price_per_million": model.sys_credit_output_price_per_million,
                "remark": remark,
            }

    def get_user_credit_account(self, user_id: str, billing_scope: str = "sys_paid") -> Dict[str, Any]:
        scope = _normalize_billing_scope(billing_scope)
        with self.Session() as session:
            account = self._get_or_create_credit_account(session, str(user_id), scope)
            session.commit()
            return self._serialize_credit_account(account, str(user_id), scope)

    def adjust_user_credit(
        self,
        user_id: str,
        delta_credit: int,
        *,
        billing_scope: str = "sys_paid",
        operator_user_id: Optional[str] = None,
        remark: Optional[str] = None,
        reason_type: str = "manual_adjust",
    ) -> Dict[str, Any]:
        scope = _normalize_billing_scope(billing_scope)
        if scope != "sys_paid":
            raise ValueError("当前仅支持调整 sys_paid 的点数账户")

        with self.Session() as session:
            account = self._get_or_create_credit_account(session, str(user_id), scope)
            delta = float(delta_credit)
            new_balance = float(account.credit_balance or 0) + delta
            if new_balance < 0:
                raise CreditBalanceExceededError(f"用户 '{user_id}' 的系统点数余额不足，无法扣减 {abs(delta)} 点")

            account.credit_balance = new_balance
            if delta > 0:
                account.credit_total_granted = float(account.credit_total_granted or 0) + delta
            else:
                account.credit_total_used = float(account.credit_total_used or 0) + abs(delta)

            session.add(UserCreditLedger(
                user_id=str(user_id),
                billing_scope=scope,
                delta_credit=delta,
                balance_after=new_balance,
                reason_type=reason_type,
                operator_user_id=str(operator_user_id) if operator_user_id is not None else None,
                remark=remark,
            ))
            session.commit()
            return self._serialize_credit_account(account, str(user_id), scope)

    def get_user_credit_ledger(self, user_id: str, billing_scope: str = "sys_paid", limit: int = 50) -> List[Dict[str, Any]]:
        scope = _normalize_billing_scope(billing_scope)
        with self.Session() as session:
            rows = (
                session.query(UserCreditLedger)
                .filter_by(user_id=str(user_id), billing_scope=scope)
                .order_by(UserCreditLedger.created_at.desc(), UserCreditLedger.id.desc())
                .limit(max(int(limit), 1))
                .all()
            )
            return [
                {
                    "id": row.id,
                    "delta_credit": float(row.delta_credit or 0),
                    "balance_after": float(row.balance_after or 0),
                    "reason_type": row.reason_type,
                    "platform_id": row.platform_id,
                    "model_id": row.model_id,
                    "usage_log_id": row.usage_log_id,
                    "operator_user_id": row.operator_user_id,
                    "remark": row.remark,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

    def get_user_credit_usage_summary(self, user_id: str, billing_scope: str = "sys_paid") -> Dict[str, Any]:
        scope = _normalize_billing_scope(billing_scope)
        with self.Session() as session:
            account = self._get_or_create_credit_account(session, str(user_id), scope)
            usage = session.query(
                func.coalesce(func.sum(UsageLogEntry.credit_cost), 0).label("credit_used"),
                func.count(UsageLogEntry.id).label("requests"),
            ).filter(
                UsageLogEntry.user_id == str(user_id),
                UsageLogEntry.quota_scope == scope,
            ).first()
            session.commit()
            return {
                **self._serialize_credit_account(account, str(user_id), scope),
                "credit_used_from_usage": float(usage.credit_used or 0),
                "requests": int(usage.requests or 0),
            }

    def enforce_user_credit(
        self,
        session,
        user_id: str,
        platform_id: int,
        model_id: int,
        billing_scope: Optional[str],
    ) -> None:
        """调用前点数拦截（在配额拦截之后执行，见 enforce_user_quota）。"""
        if not getattr(self, "billing_enabled", False):
            return

        scope = _normalize_billing_scope(billing_scope)
        if scope != "sys_paid":
            return

        model = session.query(LLModels).filter_by(id=int(model_id), platform_id=int(platform_id)).first()
        if not model:
            return

        if (
            model.sys_credit_input_price_per_million is None
            or model.sys_credit_output_price_per_million is None
        ):
            raise CreditBalanceExceededError("管理员尚未设置此模型价格")

        platform = session.query(LLMPlatform).filter_by(id=int(platform_id)).first()
        if platform and platform.sys_credit_balance is not None and float(platform.sys_credit_balance or 0) <= 0:
            raise CreditBalanceExceededError("该平台的额度已被耗尽，请稍等片刻或更换模型")

        input_price = resolve_input_price_per_million(model)
        output_price = resolve_output_price_per_million(model)

        account = self._get_or_create_credit_account(session, str(user_id), "sys_paid")
        # 预估最低消耗：按 1 token 计算实际消耗（价格是每百万 token 的）
        estimated_cost = calculate_credit_cost(input_price, output_price, prompt_tokens=1, completion_tokens=1)
        if str(account.status or "active") != "active":
            raise CreditBalanceExceededError(f"用户 '{user_id}' 的系统点数账户当前不可用")
        if estimated_cost == 0:
            return
        if float(account.credit_balance or 0) < estimated_cost:
            raise CreditBalanceExceededError(
                f"用户 '{user_id}' 的系统点数余额不足，当前余额 {float(account.credit_balance or 0):.2f}，至少需要 {estimated_cost:.2f} 点"
            )
        if platform and platform.sys_credit_balance is not None and float(platform.sys_credit_balance or 0) < estimated_cost:
            raise CreditBalanceExceededError("该平台的额度已被耗尽，请稍等片刻或更换模型")
