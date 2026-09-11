"""YAML 种子配置与数据库系统平台的同步逻辑。

从 ``manager.py`` 抽离，供 ``AIManagerBase`` 复用：

- ``build_seed_model_specs``：把 YAML 平台模型配置展开为内部规格；
- ``match_seed_models_to_db``：四阶段匹配 YAML 规格与既有数据库模型；
- ``apply_seed_model_update / create_seed_model``：写回/新建数据库模型；
- ``sync_seed_models_for_platform``：同步单个平台的全部模型。

抽离后 ``manager.py`` 只保留编排（平台增删、事务提交、缓存失效），
匹配算法的单测可以直接针对本模块编写，不依赖数据库。
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from .image_adapters import (
    DEFAULT_IMAGE_GENERATION_ADAPTER,
    normalize_image_generation_adapter,
    strip_internal_image_generation_fields,
)
from .models import (
    LLModels,
    MODALITY_IMAGE,
    get_model_modalities,
    normalize_model_modalities,
    set_model_modalities,
)


def resolve_seed_model_limits(
    model_config: Any,
    *,
    default_max_context: int,
    default_max_output: int,
) -> Tuple[int, int]:
    """解析 YAML 模型配置中的上下文与输出上限。"""
    max_context = default_max_context
    max_output = default_max_output
    if isinstance(model_config, dict):
        raw_context = model_config.get("max_context_tokens")
        raw_output = model_config.get("max_output_tokens")
        if raw_context is not None:
            try:
                max_context = max(int(raw_context), 0)
            except (TypeError, ValueError):
                max_context = default_max_context
        if raw_output is not None:
            try:
                max_output = max(int(raw_output), 0)
            except (TypeError, ValueError):
                max_output = default_max_output
    return max_context, max_output


def build_seed_model_specs(
    cfg: Dict[str, Any],
    *,
    default_max_context: int,
    default_max_output: int,
) -> List[Dict[str, Any]]:
    """将 YAML 平台模型配置统一展开为内部规格列表。"""
    specs: List[Dict[str, Any]] = []
    raw_models = cfg.get("models", {})
    if not isinstance(raw_models, dict):
        return specs

    for model_idx, (display_name, model_config) in enumerate(raw_models.items()):
        if isinstance(model_config, str):
            model_name = model_config
            extra_body = None
            temperature = None
            input_modalities, output_modalities = normalize_model_modalities()
            image_generation_adapter = None
        elif isinstance(model_config, dict):
            model_name = model_config.get("model_name")
            extra_body = model_config.get("extra_body")
            temperature = model_config.get("temperature")
            input_modalities, output_modalities = normalize_model_modalities(
                model_config.get("input_modalities"),
                model_config.get("output_modalities"),
            )
            image_generation_adapter = (
                normalize_image_generation_adapter(model_config.get("image_generation_adapter"))
            )
        else:
            continue

        if not model_name:
            continue

        max_context_tokens, max_output_tokens = resolve_seed_model_limits(
            model_config,
            default_max_context=default_max_context,
            default_max_output=default_max_output,
        )
        cleaned_extra_body = strip_internal_image_generation_fields(extra_body)
        if MODALITY_IMAGE in output_modalities and not image_generation_adapter:
            image_generation_adapter = DEFAULT_IMAGE_GENERATION_ADAPTER
        specs.append({
            "display_name": display_name,
            "model_name": model_name,
            "input_modalities": input_modalities,
            "output_modalities": output_modalities,
            "extra_body_json": json.dumps(cleaned_extra_body) if cleaned_extra_body else None,
            "image_generation_adapter": image_generation_adapter if MODALITY_IMAGE in output_modalities else None,
            "temperature": temperature,
            "max_context_tokens": max_context_tokens,
            "max_output_tokens": max_output_tokens,
            "has_max_context_tokens": (
                isinstance(model_config, dict) and "max_context_tokens" in model_config
            ),
            "has_max_output_tokens": (
                isinstance(model_config, dict) and "max_output_tokens" in model_config
            ),
            "sort_order": model_idx,
        })
    return specs


def _modalities_match(db_model: LLModels, seed: Dict[str, Any]) -> bool:
    modalities = get_model_modalities(db_model)
    return (
        modalities["input_modalities"] == seed["input_modalities"]
        and modalities["output_modalities"] == seed["output_modalities"]
    )


def match_seed_models_to_db(
    seed_models: List[Dict[str, Any]],
    db_models_pool: List[LLModels],
) -> Tuple[Dict[int, LLModels], set]:
    """用统一四阶段策略匹配 YAML 模型规格与数据库既有模型。"""
    matched_pairs: Dict[int, LLModels] = {}
    matched_db_ids: set = set()

    # 第一阶段：完美匹配（显示名、模型名、输入/输出模态）
    for idx, seed in enumerate(seed_models):
        for db_model in db_models_pool:
            if db_model.id in matched_db_ids:
                continue
            if (
                db_model.display_name == seed["display_name"]
                and db_model.model_name == seed["model_name"]
                and _modalities_match(db_model, seed)
            ):
                matched_pairs[idx] = db_model
                matched_db_ids.add(db_model.id)
                break

    # 第二阶段：同名同类别匹配，允许 model_name 改动。
    for idx, seed in enumerate(seed_models):
        if idx in matched_pairs:
            continue
        for db_model in db_models_pool:
            if db_model.id in matched_db_ids:
                continue
            if db_model.display_name == seed["display_name"] and _modalities_match(db_model, seed):
                matched_pairs[idx] = db_model
                matched_db_ids.add(db_model.id)
                break

    # 第三阶段：唯一 model_name + 模态组合改名匹配。
    seed_key_counter = Counter(
        (
            seed["model_name"],
            tuple(seed["input_modalities"]),
            tuple(seed["output_modalities"]),
        )
        for seed in seed_models
    )
    for idx, seed in enumerate(seed_models):
        if idx in matched_pairs:
            continue
        key = (
            seed["model_name"],
            tuple(seed["input_modalities"]),
            tuple(seed["output_modalities"]),
        )
        if seed_key_counter[key] != 1:
            continue
        candidates = [
            db_model for db_model in db_models_pool
            if db_model.id not in matched_db_ids
            and db_model.model_name == seed["model_name"]
            and _modalities_match(db_model, seed)
        ]
        if len(candidates) == 1:
            db_model = candidates[0]
            matched_pairs[idx] = db_model
            matched_db_ids.add(db_model.id)

    # 第四阶段：相同 model_name 多配置时，用 extra_body 匹配。
    for idx, seed in enumerate(seed_models):
        if idx in matched_pairs:
            continue
        candidates = [
            db_model for db_model in db_models_pool
            if db_model.id not in matched_db_ids
            and db_model.model_name == seed["model_name"]
            and _modalities_match(db_model, seed)
        ]
        best_match = next(
            (candidate for candidate in candidates if candidate.extra_body == seed["extra_body_json"]),
            None,
        )
        if best_match:
            matched_pairs[idx] = best_match
            matched_db_ids.add(best_match.id)

    return matched_pairs, matched_db_ids


def apply_seed_model_update(model: LLModels, spec: Dict[str, Any], *, reset_mode: bool) -> None:
    """把 YAML 模型规格写回已有数据库模型。"""
    model.display_name = spec["display_name"]
    model.extra_body = spec["extra_body_json"]
    model.image_generation_adapter = spec.get("image_generation_adapter")
    model.temperature = spec["temperature"]
    # 增量启动时，YAML 缺省值不能覆盖数据库中的管理员配置。
    if reset_mode or spec.get("has_max_context_tokens"):
        model.max_context_tokens = spec["max_context_tokens"]
    if reset_mode or spec.get("has_max_output_tokens"):
        model.max_output_tokens = spec["max_output_tokens"]
    set_model_modalities(model, spec["input_modalities"], spec["output_modalities"])
    if reset_mode:
        model.sort_order = spec["sort_order"]


def create_seed_model(platform_id: int, spec: Dict[str, Any], *, sort_order: int) -> LLModels:
    """根据 YAML 模型规格创建数据库模型对象。"""
    model = LLModels(
        platform_id=platform_id,
        model_name=spec["model_name"],
        display_name=spec["display_name"],
        extra_body=spec["extra_body_json"],
        image_generation_adapter=spec.get("image_generation_adapter"),
        temperature=spec["temperature"],
        max_context_tokens=spec["max_context_tokens"],
        max_output_tokens=spec["max_output_tokens"],
        sort_order=sort_order,
    )
    set_model_modalities(model, spec["input_modalities"], spec["output_modalities"])
    return model


def sync_seed_models_for_platform(
    session,
    plat: LLModels,
    platform_name: str,
    cfg: Dict[str, Any],
    *,
    reset_mode: bool,
    default_max_context: int,
    default_max_output: int,
) -> None:
    """同步单个平台的 YAML 模型配置，供初始化、增量同步和强制重置共用。"""
    seed_models = build_seed_model_specs(
        cfg,
        default_max_context=default_max_context,
        default_max_output=default_max_output,
    )
    db_models_pool = list(plat.models)
    matched_pairs, matched_db_ids = match_seed_models_to_db(seed_models, db_models_pool)

    max_sort = max((model.sort_order or 0 for model in db_models_pool), default=-1)
    log_prefix = "yaml-reset" if reset_mode else "incremental-sync"

    for idx, spec in enumerate(seed_models):
        matched_model = matched_pairs.get(idx)
        if matched_model is not None:
            if matched_model.display_name != spec["display_name"]:
                print(
                    f"[{log_prefix}] Platform {platform_name} model display name changed: "
                    f"{matched_model.display_name} -> {spec['display_name']}"
                )
            apply_seed_model_update(matched_model, spec, reset_mode=reset_mode)
            continue

        sort_order = spec["sort_order"] if reset_mode else max_sort + 1
        if not reset_mode:
            max_sort = sort_order
        session.add(create_seed_model(plat.id, spec, sort_order=sort_order))
        action = "added model" if reset_mode else "added new model"
        print(f"[{log_prefix}] Platform {platform_name} {action}: {spec['display_name']} ({spec['model_name']})")

    if reset_mode:
        for db_model in db_models_pool:
            if db_model.id not in matched_db_ids:
                session.delete(db_model)
                print(f"[yaml-reset] Platform {platform_name} removed deprecated model: {db_model.display_name}")
