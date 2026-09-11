"""YAML 种子同步算法单测（新增：覆盖从 manager.py 抽离的 seed_sync 模块）。"""

from types import SimpleNamespace

from ..seed_sync import (
    build_seed_model_specs,
    match_seed_models_to_db,
)


def _db_model(model_id, display_name, model_name, extra_body=None):
    return SimpleNamespace(
        id=model_id,
        display_name=display_name,
        model_name=model_name,
        extra_body=extra_body,
        input_modalities='["text"]',
        output_modalities='["text"]',
    )


def _spec(display_name, model_name, extra_body_json=None):
    return {
        "display_name": display_name,
        "model_name": model_name,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "extra_body_json": extra_body_json,
    }


def test_build_seed_specs_supports_string_and_dict_forms() -> None:
    specs = build_seed_model_specs(
        {"models": {"甲": "m-a", "乙": {"model_name": "m-b", "extra_body": {"k": 1}}}},
        default_max_context=100,
        default_max_output=10,
    )
    assert [(s["display_name"], s["model_name"]) for s in specs] == [("甲", "m-a"), ("乙", "m-b")]
    assert specs[0]["extra_body_json"] is None
    assert '"k": 1' in (specs[1]["extra_body_json"] or "")


def test_match_prefers_exact_then_display_name() -> None:
    seeds = [_spec("甲", "m-a"), _spec("乙", "m-new")]
    pool = [_db_model(1, "甲", "m-a"), _db_model(2, "乙", "m-old")]
    matched, used = match_seed_models_to_db(seeds, pool)
    assert matched[0].id == 1
    assert matched[1].id == 2
    assert used == {1, 2}


def test_match_uses_extra_body_for_ambiguous_model_names() -> None:
    # 第四阶段只在“同名”失效时按 extra_body 区分；显示名不同时
    # 第二阶段已按显示名配对，不会被 extra_body 反转。
    seeds = [_spec("甲", "m", '{"a": 1}'), _spec("乙", "m", '{"b": 2}')]
    pool = [_db_model(1, "甲", "m", '{"a": 1}'), _db_model(2, "乙", "m", '{"b": 2}')]
    matched, _ = match_seed_models_to_db(seeds, pool)
    assert matched[0].id == 1
    assert matched[1].id == 2


def test_match_second_stage_matches_display_name_despite_model_rename() -> None:
    seeds = [_spec("甲", "m-new")]
    pool = [_db_model(1, "甲", "m-old")]
    matched, _ = match_seed_models_to_db(seeds, pool)
    assert matched[0].id == 1
