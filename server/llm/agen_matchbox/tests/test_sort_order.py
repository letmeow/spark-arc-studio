"""平台和模型排序持久化、默认选择与失效回退测试（从主项目迁移）。"""

from __future__ import annotations

from ..manager import AIManager
from ..models import LLModels


def test_model_order_is_restored_from_persisted_sort_order(manager: AIManager) -> None:
    """模型重排后，数据库和管理页读取结果都保持新顺序。"""
    platform = manager.admin_add_sys_platform("模型排序平台", "https://models.example")
    models = [
        manager.add_model(platform.id, f"model-{index}", f"模型 {index}", admin_mode=True)
        for index in range(3)
    ]

    # 先建立一次系统平台缓存，覆盖页面刷新前已经读取过列表的场景。
    manager.get_platforms_with_models("user-1")
    manager.admin_reorder_sys_models(platform.id, [models[2].id, models[0].id, models[1].id])

    with manager.Session() as session:
        persisted = session.query(LLModels).filter_by(platform_id=platform.id).all()
        assert {
            model.id: model.sort_order
            for model in persisted
        } == {
            models[2].id: 0,
            models[0].id: 1,
            models[1].id: 2,
        }

    views = manager.get_platforms_with_models("user-1")
    model_view = next(view for view in views if view["platform_id"] == platform.id)
    assert [model["model_id"] for model in model_view["models"]] == [
        models[2].id,
        models[0].id,
        models[1].id,
    ]
    assert [model["sort_order"] for model in model_view["models"]] == [0, 1, 2]
    assert manager._default_platform_id == platform.id
    assert manager._default_model_id == models[2].id


def test_platform_and_model_order_drive_default_and_fallback(manager: AIManager) -> None:
    """平台或模型失效时，回退顺序继续遵循持久化排序。"""
    first_platform = manager.admin_add_sys_platform("平台甲", "https://platform-a.example")
    second_platform = manager.admin_add_sys_platform("平台乙", "https://platform-b.example")
    first_model = manager.add_model(first_platform.id, "model-a", "模型甲", admin_mode=True)
    second_model = manager.add_model(second_platform.id, "model-b1", "模型乙一", admin_mode=True)
    third_model = manager.add_model(second_platform.id, "model-b2", "模型乙二", admin_mode=True)

    manager.admin_reorder_sys_platforms([second_platform.id, first_platform.id])
    manager.admin_reorder_sys_models(second_platform.id, [third_model.id, second_model.id])

    views = manager.get_platforms_with_models("user-1")
    assert [view["platform_id"] for view in views] == [second_platform.id, first_platform.id]
    second_view = views[0]
    assert [model["model_id"] for model in second_view["models"]] == [
        third_model.id,
        second_model.id,
    ]
    assert manager._default_platform_id == second_platform.id
    assert manager._default_model_id == third_model.id

    with manager.Session() as session:
        third_model_row = session.query(LLModels).filter_by(id=third_model.id).one()
        third_model_row.disable = 1
        session.commit()
        fallback_platform, fallback_model = manager._get_fallback_platform_model(session, "user-1")
        assert (fallback_platform.id, fallback_model.id) == (second_platform.id, second_model.id)

        second_platform_row = session.query(type(second_platform)).filter_by(id=second_platform.id).one()
        second_platform_row.disable = 1
        session.commit()
        fallback_platform, fallback_model = manager._get_fallback_platform_model(session, "user-1")
        assert (fallback_platform.id, fallback_model.id) == (first_platform.id, first_model.id)
