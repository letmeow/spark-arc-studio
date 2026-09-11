"""兑换码次数控制与管理员额度发放回归测试（从主项目迁移，网关自包含版）。

主项目仅保留路由鉴权用例（依赖 core.auth / core.routes_redeem），
此处覆盖网关核心的兑换与发放逻辑。
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..credit_grant_services import CreditGrantServicesMixin
from ..credit_services import CreditServicesMixin
from ..models import (
    Base,
    CreditGrantRecipient,
    RedeemCode,
    RedeemCodeUsage,
    UserCreditAccount,
    UserCreditLedger,
)
from ..redeem_code_services import (
    RedeemCodeAlreadyRedeemedByUserError,
    RedeemCodeAlreadyUsedError,
    RedeemCodeServicesMixin,
)


class _Services(CreditServicesMixin, CreditGrantServicesMixin, RedeemCodeServicesMixin):
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)


@pytest.fixture()
def services(tmp_path):
    return _Services(f"sqlite:///{(tmp_path / 'credits.db').as_posix()}")


def test_limited_redeem_code_enforces_total_and_per_user_limits(services):
    created = services.create_redeem_code(
        credit_amount=25,
        code_type="limited",
        code="LIMITED-2",
        max_redemptions=2,
    )[0]
    assert created["max_redemptions"] == 2

    first = services.redeem_code("user-1", "LIMITED-2")
    assert first["new_balance"] == 25

    with pytest.raises(RedeemCodeAlreadyRedeemedByUserError):
        services.redeem_code("user-1", "LIMITED-2")

    second = services.redeem_code("user-2", "LIMITED-2")
    assert second["new_balance"] == 25

    with pytest.raises(RedeemCodeAlreadyUsedError):
        services.redeem_code("user-3", "LIMITED-2")

    with services.Session() as session:
        code = session.query(RedeemCode).filter_by(id=created["id"]).one()
        assert code.redemption_count == 2
        assert code.status == "exhausted"
        ledgers = session.query(UserCreditLedger).filter_by(reason_type="redeem_code").all()
        assert len(ledgers) == 2


def test_concurrent_same_user_redemption_only_grants_once(services):
    services.create_redeem_code(
        credit_amount=10,
        code_type="per_user",
        code="CONCURRENT",
    )
    barrier = Barrier(2)

    def redeem_once():
        barrier.wait()
        try:
            services.redeem_code("same-user", "CONCURRENT")
            return "granted"
        except RedeemCodeAlreadyRedeemedByUserError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: redeem_once(), range(2)))

    assert sorted(results) == ["duplicate", "granted"]
    with services.Session() as session:
        account = session.query(UserCreditAccount).filter_by(user_id="same-user").one()
        assert account.credit_balance == 10
        assert session.query(UserCreditLedger).filter_by(user_id="same-user").count() == 1


def test_legacy_single_code_without_new_limit_remains_one_time(services):
    with services.Session() as session:
        session.add(RedeemCode(
            code="LEGACY-SINGLE",
            credit_amount=15,
            code_type="single",
            status="active",
            max_redemptions=None,
            redemption_count=0,
        ))
        session.commit()

    services.redeem_code("legacy-user", "LEGACY-SINGLE")
    with pytest.raises(RedeemCodeAlreadyUsedError):
        services.redeem_code("another-user", "LEGACY-SINGLE")

    detail = services.list_redeem_codes()["items"][0]
    assert detail["max_redemptions"] == 1
    assert detail["redemption_count"] == 1


def test_revoke_deletes_code_and_usages_but_keeps_credit_ledger(services):
    created = services.create_redeem_code(
        credit_amount=20,
        code_type="per_user",
        code="REVOKE-ME",
    )[0]
    services.redeem_code("user-1", "REVOKE-ME")

    result = services.revoke_redeem_code(created["id"], operator_user_id="admin-1")
    assert result["deleted"] is True

    with services.Session() as session:
        assert session.query(RedeemCode).filter_by(id=created["id"]).first() is None
        assert session.query(RedeemCodeUsage).filter_by(redeem_code_id=created["id"]).count() == 0
        assert session.query(UserCreditLedger).filter_by(user_id="user-1", reason_type="redeem_code").count() == 1


def test_current_user_grant_updates_every_account_and_ledger(services):
    campaign = services.create_credit_grant_campaign(
        credit_amount=80,
        grant_scope="current_users",
        created_by="admin-1",
        remark="全服福利",
        user_ids=["1", "2", "2"],
    )

    assert campaign["status"] == "completed"
    assert campaign["granted_count"] == 2
    with services.Session() as session:
        accounts = session.query(UserCreditAccount).order_by(UserCreditAccount.user_id).all()
        assert [(account.user_id, account.credit_balance) for account in accounts] == [("1", 80), ("2", 80)]
        ledgers = session.query(UserCreditLedger).filter_by(reason_type="credit_grant").all()
        assert len(ledgers) == 2
        assert all(ledger.operator_user_id == "admin-1" for ledger in ledgers)


def test_future_user_grant_is_idempotent_and_can_be_stopped(services):
    campaign = services.create_credit_grant_campaign(
        credit_amount=60,
        grant_scope="future_users",
        created_by="admin-1",
        remark="注册赠送",
    )

    assert services.grant_future_signup_credits("new-user") == [
        {"campaign_id": campaign["id"], "credit_amount": 60.0}
    ]
    assert services.grant_future_signup_credits("new-user") == []

    services.revoke_credit_grant_campaign(campaign["id"])
    assert services.grant_future_signup_credits("later-user") == []

    with services.Session() as session:
        account = session.query(UserCreditAccount).filter_by(user_id="new-user").one()
        assert account.credit_balance == 60
        assert session.query(CreditGrantRecipient).count() == 1
        assert session.query(UserCreditLedger).filter_by(user_id="new-user", reason_type="credit_grant").count() == 1


def test_concurrent_settlement_never_loses_credit_deduction(manager) -> None:
    """后台写入 + 行级锁兜底后，并发结算不应丢扣。"""
    from ..models import LLModels
    from ..usage_writer import BackgroundUsageWriter
    from ..tracked_model import UsageTrackingCallback

    manager.billing_enabled = True
    platform = manager.admin_add_sys_platform("并发结算平台", "https://settle.example")
    model = manager.add_model(platform.id, "settle-model", "结算模型", admin_mode=True)
    with manager.Session() as session:
        row = session.query(LLModels).filter_by(id=model.id).one()
        row.sys_credit_input_price_per_million = 1000.0
        row.sys_credit_output_price_per_million = 2000.0
        session.commit()
    manager.adjust_user_credit("settle-user", 100000, operator_user_id="admin")

    writer = BackgroundUsageWriter()
    try:
        callback = UsageTrackingCallback(
            user_id="settle-user",
            model_id=model.id,
            platform_id=platform.id,
            model_name="settle-model",
            platform_name="并发结算平台",
            session_maker=manager.Session,
            quota_scope="sys_paid",
            billing_enabled=True,
            usage_writer=writer,
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(
                lambda _: callback._record_usage(prompt_tokens=1000, completion_tokens=1000),
                range(20),
            ))
        writer._tasks.join()
    finally:
        writer.shutdown()

    account = manager.get_user_credit_account("settle-user")
    # 每次 (1000*1000 + 1000*2000)/1e6 = 3 点，共 20 次 = 60 点。
    assert account["credit_balance"] == pytest.approx(100000 - 60)
