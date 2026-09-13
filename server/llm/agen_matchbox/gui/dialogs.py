"""
对话框 Mixin — 添加/编辑模型、系统用途管理、用户配额管理、用户调用明细（适配 Flet 0.28.3）。
"""
from __future__ import annotations

import os
import sys
import json as json_lib
import flet as ft

if __package__ in (None, "", "gui"):
    _GUI_DIR = os.path.dirname(os.path.abspath(__file__))
    _PKG_DIR = os.path.dirname(_GUI_DIR)
    _PARENT_DIR = os.path.dirname(_PKG_DIR)
    if _PARENT_DIR not in sys.path:
        sys.path.insert(0, _PARENT_DIR)
    __package__ = f"{os.path.basename(_PKG_DIR)}.{os.path.basename(_GUI_DIR)}"

from ..models import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MODALITY_EMBEDDING,
    MODALITY_IMAGE,
    MODALITY_TEXT,
    normalize_model_modalities,
)
from ..image_adapters import (
    IMAGE_ADAPTER_GEMINI_GENERATE_CONTENT,
    IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    IMAGE_ADAPTER_OPENAI_IMAGES,
    IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    IMAGE_ADAPTER_XAI_IMAGES,
    normalize_image_generation_adapter,
    strip_internal_image_generation_fields,
)


class DialogsMixin:
    """对话框功能 Mixin，需与 LLMConfigGUI 混入使用。"""

    IMAGE_ADAPTER_OPTIONS = {
        "OpenAI Images / 兼容协议": IMAGE_ADAPTER_OPENAI_IMAGES,
        "OpenAI Responses 图片工具": IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
        "Gemini generateContent / Nano Banana": IMAGE_ADAPTER_GEMINI_GENERATE_CONTENT,
        "Gemini Interactions": IMAGE_ADAPTER_GEMINI_INTERACTIONS,
        "Grok Image": IMAGE_ADAPTER_XAI_IMAGES,
    }
    DEFAULT_IMAGE_ADAPTER = IMAGE_ADAPTER_OPENAI_IMAGES

    def _normalize_image_adapter(self, value) -> str:
        return normalize_image_generation_adapter(value) or self.DEFAULT_IMAGE_ADAPTER

    def _image_adapter_label(self, value) -> str:
        normalized = self._normalize_image_adapter(value)
        for label, adapter in self.IMAGE_ADAPTER_OPTIONS.items():
            if adapter == normalized:
                return label
        return next(iter(self.IMAGE_ADAPTER_OPTIONS))

    def _image_adapter_value(self, label_or_value) -> str:
        return self.IMAGE_ADAPTER_OPTIONS.get(
            str(label_or_value or "").strip(),
            self._normalize_image_adapter(label_or_value),
        )

    def _extract_image_adapter(self, adapter_value=None) -> str:
        return self._normalize_image_adapter(adapter_value)

    def _image_adapter_for_modalities(self, output_modalities, adapter):
        _, normalized_output = normalize_model_modalities(None, output_modalities)
        if MODALITY_IMAGE not in normalized_output:
            return None
        return self._image_adapter_value(adapter)

    @staticmethod
    def _parse_optional_non_negative_int(raw_value: str, *, field_label: str):
        text = str(raw_value or "").strip()
        if not text:
            return None
        try:
            value = int(text)
        except (TypeError, ValueError):
            raise ValueError(f"{field_label} 必须是整数")
        if value < 0:
            raise ValueError(f"{field_label} 不能小于 0")
        return value

    @staticmethod
    def _parse_optional_non_negative_float(raw_value: str, *, field_label: str):
        text = str(raw_value or "").strip()
        if not text:
            return None
        try:
            value = float(text)
        except (TypeError, ValueError):
            raise ValueError(f"{field_label} 必须是数字")
        if value < 0:
            raise ValueError(f"{field_label} 不能小于 0")
        return value

    # ------------------------------------------------------------------ #
    #  添加模型对话框                                                       #
    # ------------------------------------------------------------------ #

    def open_add_model_dialog(self, custom_model_id=None):
        """打开添加模型对话框。"""
        platform_name = self._resolve_platform_name()
        if not platform_name:
            self.show_warning("警告", "请先选择一个平台")
            return

        selected_model_id = ""
        auto_max_context = None
        auto_max_output = None

        if custom_model_id:
            selected_model_id = custom_model_id
        else:
            selected_model_id = self._get_selected_probe_model_id()

        if selected_model_id:
            cache_key = self._get_probe_cache_key(
                platform_name,
                (self.base_url_entry.value or "").strip(),
                (self.api_key_entry.value or "").strip(),
            )
            cached_models = self.probe_models_cache.get(cache_key, [])
            for m in cached_models:
                if isinstance(m, dict) and m.get("id") == selected_model_id:
                    auto_max_context = m.get("max_context_tokens")
                    auto_max_output = m.get("max_output_tokens")
                    break

        display_name_entry = ft.TextField(
            label="显示名称",
            value=selected_model_id,
            autofocus=True,
            expand=True,
        )
        model_id_entry = ft.TextField(
            label="模型 ID",
            value=selected_model_id,
            expand=True,
        )

        vision_cb = ft.Checkbox(label="视觉 (V)", value=False)
        image_cb = ft.Checkbox(label="生图 (I)", value=False)
        embedding_cb = ft.Checkbox(label="向量 (E)", value=False)

        image_adapter_dd = ft.Dropdown(
            label="生图协议",
            options=[ft.dropdown.Option(lbl, lbl) for lbl in self.IMAGE_ADAPTER_OPTIONS.keys()],
            value=self._image_adapter_label(self.DEFAULT_IMAGE_ADAPTER),
            disabled=True,
            expand=True,
        )

        def on_embedding_toggle(e):
            if embedding_cb.value:
                vision_cb.value = False
                image_cb.value = False
                image_adapter_dd.disabled = True
            self.page.update()

        def on_regular_toggle(e):
            if vision_cb.value or image_cb.value:
                embedding_cb.value = False
            image_adapter_dd.disabled = not image_cb.value
            self.page.update()

        embedding_cb.on_change = on_embedding_toggle
        vision_cb.on_change = on_regular_toggle
        image_cb.on_change = on_regular_toggle

        temp_switch = ft.Switch(label="启用 Temperature", value=False)
        temp_entry = ft.TextField(
            label="Temperature (0.3 ~ 1.5)",
            value="0.7",
            disabled=True,
            width=160,
        )

        def on_temp_toggle(e):
            temp_entry.disabled = not temp_switch.value
            if temp_switch.value:
                self.show_warning(
                    "Temperature 参数提示",
                    "务必了解该模型的 Temperature 基准值。部分模型在温度设置错误时会直接报错。如果你不清楚该参数的作用，建议保持禁用。",
                )
            self.page.update()

        temp_switch.on_change = on_temp_toggle

        max_ctx_entry = ft.TextField(
            label="最大上下文 Token",
            value=str(auto_max_context if auto_max_context is not None else DEFAULT_MAX_CONTEXT_TOKENS),
            expand=True,
        )
        max_out_entry = ft.TextField(
            label="最大单次输出 Token",
            value=str(auto_max_output if auto_max_output is not None else DEFAULT_MAX_OUTPUT_TOKENS),
            expand=True,
        )

        input_price_entry = ft.TextField(label="输入单价 (元/1M Token)", hint_text="留空或0免费", expand=True)
        cached_price_entry = ft.TextField(label="缓存输入单价 (元/1M Token)", hint_text="留空或0免费", expand=True)
        output_price_entry = ft.TextField(label="输出单价 (元/1M Token)", hint_text="留空或0免费", expand=True)

        extra_body_entry = ft.TextField(
            label="Extra Body (JSON)",
            hint_text='示例: {"thinkingBudget": 0} 或 {"top_k": 40}',
            multiline=True,
            min_lines=3,
            max_lines=5,
            expand=True,
        )
        error_text = ft.Text("", color=ft.Colors.RED_600, size=12, visible=False)

        def do_add(e):
            d_name = (display_name_entry.value or "").strip()
            m_id = (model_id_entry.value or "").strip()

            if not d_name or not m_id:
                error_text.value = "请填写显示名称和模型 ID"
                error_text.visible = True
                self.page.update()
                return

            if d_name in self.current_config[platform_name].get("models", {}):
                error_text.value = f"显示名称 '{d_name}' 已存在"
                error_text.visible = True
                self.page.update()
                return

            extra_str = (extra_body_entry.value or "").strip()
            try:
                extra_body = self._parse_extra_body(extra_str) if extra_str else None
            except ValueError as err:
                error_text.value = str(err)
                error_text.visible = True
                self.page.update()
                return

            temp_val = None
            if temp_switch.value:
                try:
                    tv = float(temp_entry.value or 0.7)
                except (TypeError, ValueError):
                    error_text.value = "Temperature 必须是合法浮点数"
                    error_text.visible = True
                    self.page.update()
                    return
                if tv < 0.3 or tv > 1.5:
                    error_text.value = "Temperature 必须在 0.3 到 1.5 之间"
                    error_text.visible = True
                    self.page.update()
                    return
                temp_val = tv

            if embedding_cb.value:
                in_mods, out_mods = normalize_model_modalities([MODALITY_TEXT], [MODALITY_EMBEDDING])
            else:
                in_mods = [MODALITY_TEXT]
                out_mods = [MODALITY_TEXT]
                if vision_cb.value:
                    in_mods.append(MODALITY_IMAGE)
                if image_cb.value:
                    out_mods.append(MODALITY_IMAGE)
                in_mods, out_mods = normalize_model_modalities(in_mods, out_mods)

            img_adapter = self._image_adapter_for_modalities(out_mods, image_adapter_dd.value)

            try:
                max_ctx = self._parse_optional_non_negative_int(max_ctx_entry.value, field_label="最大上下文")
                max_out = self._parse_optional_non_negative_int(max_out_entry.value, field_label="最大单次输出")
                in_price = self._parse_optional_non_negative_float(input_price_entry.value, field_label="输入单价")
                cached_price = self._parse_optional_non_negative_float(cached_price_entry.value, field_label="缓存输入单价")
                out_price = self._parse_optional_non_negative_float(output_price_entry.value, field_label="输出单价")
            except ValueError as err:
                error_text.value = str(err)
                error_text.visible = True
                self.page.update()
                return

            max_ctx = DEFAULT_MAX_CONTEXT_TOKENS if max_ctx is None else max_ctx
            max_out = DEFAULT_MAX_OUTPUT_TOKENS if max_out is None else max_out

            try:
                db_id = self.current_config[platform_name].get("_db_id")
                if not db_id:
                    raise ValueError("无法获取平台数据库 ID")

                payload = {
                    "display_name": d_name,
                    "model_name": m_id,
                    "input_modalities": in_mods,
                    "output_modalities": out_mods,
                    "extra_body": extra_body,
                    "image_generation_adapter": img_adapter,
                    "temperature": temp_val,
                    "max_context_tokens": max_ctx,
                    "max_output_tokens": max_out,
                    "sys_credit_input_price_per_million": in_price,
                    "sys_credit_cached_input_price_per_million": cached_price,
                    "sys_credit_output_price_per_million": out_price,
                }
                existing_models = self.current_config[platform_name].get("models", {})
                all_payloads = []
                for ex_name, ex_cfg in existing_models.items():
                    if isinstance(ex_cfg, dict) and ex_name != d_name:
                        all_payloads.append({
                            "display_name": ex_name,
                            "model_name": ex_cfg.get("model_name") or ex_name,
                            "input_modalities": ex_cfg.get("input_modalities", ["text"]),
                            "output_modalities": ex_cfg.get("output_modalities", ["text"]),
                            "extra_body": ex_cfg.get("extra_body"),
                            "image_generation_adapter": ex_cfg.get("image_generation_adapter"),
                            "temperature": ex_cfg.get("temperature"),
                            "max_context_tokens": ex_cfg.get("max_context_tokens"),
                            "max_output_tokens": ex_cfg.get("max_output_tokens"),
                            "sys_credit_input_price_per_million": ex_cfg.get("sys_credit_input_price_per_million"),
                            "sys_credit_cached_input_price_per_million": ex_cfg.get("sys_credit_cached_input_price_per_million"),
                            "sys_credit_output_price_per_million": ex_cfg.get("sys_credit_output_price_per_million"),
                        })
                all_payloads.append(payload)
                self.ai_manager.admin_sync_platform_models(db_id, all_payloads)
                self.page.close(dlg)
                self.load_config_from_db()
                self.log(f"✓ 模型 '{d_name}' 已成功添加", tag="success")
                self.show_snack(f"模型 '{d_name}' 已成功添加！")
            except Exception as e:
                self.log(f"✗ 添加模型失败: {e}", tag="error")
                error_text.value = f"添加模型失败: {e}"
                error_text.visible = True
                self.page.update()

        form_column = ft.Column(
            [
                ft.Row([display_name_entry, model_id_entry], spacing=10),
                ft.Row([vision_cb, image_cb, embedding_cb], spacing=16),
                ft.Row([image_adapter_dd], spacing=10),
                ft.Row([temp_switch, temp_entry], alignment=ft.MainAxisAlignment.START, spacing=16),
                ft.Row([max_ctx_entry, max_out_entry], spacing=10),
                ft.Row([input_price_entry, cached_price_entry, output_price_entry], spacing=10),
                extra_body_entry,
                error_text,
            ],
            tight=True,
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"添加模型到平台: {platform_name}"),
            content=ft.Container(content=form_column, width=640, height=480),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dlg)),
                ft.ElevatedButton("添加模型", on_click=do_add),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)

    # ------------------------------------------------------------------ #
    #  编辑模型对话框                                                       #
    # ------------------------------------------------------------------ #

    def edit_model(self):
        """编辑选中的模型。"""
        platform_name = self._resolve_platform_name()
        if not platform_name:
            return

        display_name = self._get_selected_model_display_name()
        if not display_name:
            self.show_warning("警告", "请先在已配置列表中选择要编辑的模型")
            return

        models = self.current_config[platform_name].get("models", {})
        model_config = models.get(display_name)
        if not model_config:
            return

        if isinstance(model_config, str):
            model_id = model_config
            extra_body_dict = None
            model_image_adapter = None
            in_mods, out_mods = normalize_model_modalities()
            model_temp = None
            in_price = None
            cached_price = None
            out_price = None
            max_ctx = DEFAULT_MAX_CONTEXT_TOKENS
            max_out = DEFAULT_MAX_OUTPUT_TOKENS
        else:
            model_id = model_config.get("model_name", "")
            extra_body_dict = model_config.get("extra_body")
            model_image_adapter = model_config.get("image_generation_adapter")
            in_mods, out_mods = normalize_model_modalities(
                model_config.get("input_modalities"),
                model_config.get("output_modalities"),
            )
            model_temp = model_config.get("temperature")
            in_price = model_config.get("sys_credit_input_price_per_million")
            cached_price = model_config.get("sys_credit_cached_input_price_per_million")
            out_price = model_config.get("sys_credit_output_price_per_million")
            max_ctx = model_config.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)
            max_out = model_config.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
            if isinstance(extra_body_dict, dict):
                extra_body_dict = strip_internal_image_generation_fields(extra_body_dict)

        if model_temp is None and isinstance(extra_body_dict, dict) and "temperature" in extra_body_dict:
            try:
                model_temp = float(extra_body_dict.get("temperature"))
            except (TypeError, ValueError):
                model_temp = None
            extra_body_dict = dict(extra_body_dict)
            extra_body_dict.pop("temperature", None)

        display_name_entry = ft.TextField(
            label="显示名称",
            value=display_name,
            autofocus=True,
            expand=True,
        )
        model_id_entry = ft.TextField(
            label="模型 ID (只读)",
            value=model_id,
            read_only=True,
            expand=True,
        )

        has_embedding = MODALITY_EMBEDDING in out_mods
        vision_cb = ft.Checkbox(label="视觉 (V)", value=MODALITY_IMAGE in in_mods and not has_embedding)
        image_cb = ft.Checkbox(label="生图 (I)", value=MODALITY_IMAGE in out_mods and not has_embedding)
        embedding_cb = ft.Checkbox(label="向量 (E)", value=has_embedding)

        image_adapter_dd = ft.Dropdown(
            label="生图协议",
            options=[ft.dropdown.Option(lbl, lbl) for lbl in self.IMAGE_ADAPTER_OPTIONS.keys()],
            value=self._image_adapter_label(self._extract_image_adapter(model_image_adapter)),
            disabled=not image_cb.value,
            expand=True,
        )

        def on_embedding_toggle(e):
            if embedding_cb.value:
                vision_cb.value = False
                image_cb.value = False
                image_adapter_dd.disabled = True
            self.page.update()

        def on_regular_toggle(e):
            if vision_cb.value or image_cb.value:
                embedding_cb.value = False
            image_adapter_dd.disabled = not image_cb.value
            self.page.update()

        embedding_cb.on_change = on_embedding_toggle
        vision_cb.on_change = on_regular_toggle
        image_cb.on_change = on_regular_toggle

        temp_switch = ft.Switch(label="启用 Temperature", value=model_temp is not None)
        temp_entry = ft.TextField(
            label="Temperature (0.3 ~ 1.5)",
            value=str(model_temp if model_temp is not None else 0.7),
            disabled=model_temp is None,
            width=160,
        )

        def on_temp_toggle(e):
            temp_entry.disabled = not temp_switch.value
            if temp_switch.value:
                self.show_warning(
                    "Temperature 参数提示",
                    "务必了解该模型的 Temperature 基准值。部分模型在温度设置错误时会直接报错。",
                )
            self.page.update()

        temp_switch.on_change = on_temp_toggle

        max_ctx_entry = ft.TextField(label="最大上下文 Token", value=str(max_ctx), expand=True)
        max_out_entry = ft.TextField(label="最大单次输出 Token", value=str(max_out), expand=True)

        input_price_entry = ft.TextField(
            label="输入单价 (元/1M Token)",
            value=str(in_price) if in_price is not None else "",
            hint_text="留空或0免费",
            expand=True,
        )
        cached_price_entry = ft.TextField(
            label="缓存输入单价 (元/1M Token)",
            value=str(cached_price) if cached_price is not None else "",
            hint_text="留空或0免费",
            expand=True,
        )
        output_price_entry = ft.TextField(
            label="输出单价 (元/1M Token)",
            value=str(out_price) if out_price is not None else "",
            hint_text="留空或0免费",
            expand=True,
        )

        initial_extra_json = json_lib.dumps(extra_body_dict, indent=2, ensure_ascii=False) if extra_body_dict else ""
        extra_body_entry = ft.TextField(
            label="Extra Body (JSON)",
            value=initial_extra_json,
            hint_text='示例: {"thinkingBudget": 0} 或 {"top_k": 40}',
            multiline=True,
            min_lines=3,
            max_lines=5,
            expand=True,
        )
        error_text = ft.Text("", color=ft.Colors.RED_600, size=12, visible=False)

        def do_update(e):
            new_d_name = (display_name_entry.value or "").strip()
            if not new_d_name:
                error_text.value = "显示名称不能为空"
                error_text.visible = True
                self.page.update()
                return

            if new_d_name != display_name and new_d_name in self.current_config[platform_name].get("models", {}):
                error_text.value = f"显示名称 '{new_d_name}' 已被其他模型使用"
                error_text.visible = True
                self.page.update()
                return

            extra_str = (extra_body_entry.value or "").strip()
            try:
                extra_body = self._parse_extra_body(extra_str) if extra_str else None
            except ValueError as err:
                error_text.value = str(err)
                error_text.visible = True
                self.page.update()
                return

            temp_val = None
            if temp_switch.value:
                try:
                    tv = float(temp_entry.value or 0.7)
                except (TypeError, ValueError):
                    error_text.value = "Temperature 必须是合法浮点数"
                    error_text.visible = True
                    self.page.update()
                    return
                if tv < 0.3 or tv > 1.5:
                    error_text.value = "Temperature 必须在 0.3 到 1.5 之间"
                    error_text.visible = True
                    self.page.update()
                    return
                temp_val = tv

            if embedding_cb.value:
                updated_in_mods, updated_out_mods = normalize_model_modalities([MODALITY_TEXT], [MODALITY_EMBEDDING])
            else:
                updated_in_mods = [MODALITY_TEXT]
                updated_out_mods = [MODALITY_TEXT]
                if vision_cb.value:
                    updated_in_mods.append(MODALITY_IMAGE)
                if image_cb.value:
                    updated_out_mods.append(MODALITY_IMAGE)
                updated_in_mods, updated_out_mods = normalize_model_modalities(updated_in_mods, updated_out_mods)

            img_adapter = self._image_adapter_for_modalities(updated_out_mods, image_adapter_dd.value)

            try:
                new_max_ctx = self._parse_optional_non_negative_int(max_ctx_entry.value, field_label="最大上下文")
                new_max_out = self._parse_optional_non_negative_int(max_out_entry.value, field_label="最大单次输出")
                raw_in_price = self._parse_optional_non_negative_float(input_price_entry.value, field_label="输入单价")
                raw_cached_price = self._parse_optional_non_negative_float(cached_price_entry.value, field_label="缓存输入单价")
                raw_out_price = self._parse_optional_non_negative_float(output_price_entry.value, field_label="输出单价")
            except ValueError as err:
                error_text.value = str(err)
                error_text.visible = True
                self.page.update()
                return

            new_max_ctx = DEFAULT_MAX_CONTEXT_TOKENS if new_max_ctx is None else new_max_ctx
            new_max_out = DEFAULT_MAX_OUTPUT_TOKENS if new_max_out is None else new_max_out
            update_credit_price = (
                input_price_entry.value != ""
                or cached_price_entry.value != ""
                or output_price_entry.value != ""
                or in_price is not None
                or cached_price is not None
                or out_price is not None
            )

            try:
                db_id = self.current_config[platform_name].get("_db_id")
                if not db_id:
                    raise ValueError("无法获取平台数据库 ID")

                model_db_id = model_config.get("_db_id") if isinstance(model_config, dict) else None
                if not model_db_id:
                    raise ValueError("无法获取模型数据库 ID")

                self.ai_manager.admin_update_sys_model(
                    model_id=model_db_id,
                    display_name=new_d_name,
                    extra_body=extra_body,
                    image_generation_adapter=img_adapter,
                    update_image_generation_adapter=True,
                    temperature=temp_val,
                    input_modalities=updated_in_mods,
                    output_modalities=updated_out_mods,
                    update_modalities=True,
                    max_context_tokens=new_max_ctx,
                    max_output_tokens=new_max_out,
                    sys_credit_input_price_per_million=raw_in_price,
                    sys_credit_cached_input_price_per_million=raw_cached_price,
                    sys_credit_output_price_per_million=raw_out_price,
                    update_credit_price=update_credit_price,
                    update_max_context_tokens=True,
                    update_max_output_tokens=True,
                )

                self.page.close(dlg)
                self.selected_model_display_name = new_d_name
                self.load_config_from_db()
                self.log(f"✓ 模型 '{new_d_name}' 配置已成功更新", tag="success")
                self.show_snack(f"模型 '{new_d_name}' 已成功更新！")
            except Exception as e:
                self.log(f"✗ 更新模型失败: {e}", tag="error")
                error_text.value = f"更新模型失败: {e}"
                error_text.visible = True
                self.page.update()

        form_column = ft.Column(
            [
                ft.Row([display_name_entry, model_id_entry], spacing=10),
                ft.Row([vision_cb, image_cb, embedding_cb], spacing=16),
                ft.Row([image_adapter_dd], spacing=10),
                ft.Row([temp_switch, temp_entry], alignment=ft.MainAxisAlignment.START, spacing=16),
                ft.Row([max_ctx_entry, max_out_entry], spacing=10),
                ft.Row([input_price_entry, cached_price_entry, output_price_entry], spacing=10),
                extra_body_entry,
                error_text,
            ],
            tight=True,
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"编辑模型: {display_name}"),
            content=ft.Container(content=form_column, width=640, height=480),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.page.close(dlg)),
                ft.ElevatedButton("保存修改", on_click=do_update),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)

    # ------------------------------------------------------------------ #
    #  系统用途管理对话框                                                    #
    # ------------------------------------------------------------------ #

    def edit_system_model(self):
        """编辑系统用户 (-1) 的模型选择及用途管理。"""
        system_user_id = "-1"

        def load_system_data():
            try:
                self.ai_manager.admin_sync_from_yaml()
                all_models = self.ai_manager.get_platform_models(user_id=system_user_id)
                usage_list = self.ai_manager.list_user_usage_selections(user_id=system_user_id)
                return all_models, usage_list
            except Exception as e:
                self.show_error("错误", f"加载系统用途数据失败: {e}")
                return [], []

        all_models, usage_list = load_system_data()
        platforms = sorted(list(set(m["platform_name"] for m in all_models)))
        models_by_platform = {p_name: [] for p_name in platforms}
        for model_info in all_models:
            models_by_platform[model_info["platform_name"]].append((model_info["display_name"], model_info))

        current_selected_idx = [0 if usage_list else -1]
        current_usage_data = [usage_list[0] if usage_list else {}]

        key_label_text = ft.Text("-", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        label_label_text = ft.Text("-")
        platform_dd = ft.Dropdown(label="绑定平台", options=[ft.dropdown.Option(p, p) for p in platforms], expand=True)
        model_dd = ft.Dropdown(label="绑定模型", expand=True)

        usage_tiles_column = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=4)

        def sync_right_panel():
            usage = current_usage_data[0]
            if not usage:
                key_label_text.value = "-"
                label_label_text.value = "-"
                platform_dd.value = None
                model_dd.value = None
                model_dd.options = []
            else:
                key_label_text.value = usage.get("usage_key", "-")
                label_label_text.value = usage.get("usage_label", "-")
                plat_name = usage.get("platform")
                model_name = usage.get("model_display_name")
                if plat_name in platforms:
                    platform_dd.value = plat_name
                    m_list = [m[0] for m in models_by_platform.get(plat_name, [])]
                    model_dd.options = [ft.dropdown.Option(m, m) for m in m_list]
                    model_dd.value = model_name if model_name in m_list else (m_list[0] if m_list else None)
                else:
                    platform_dd.value = None
                    model_dd.value = None
                    model_dd.options = []

        def on_platform_dd_change(e):
            sel_plat = platform_dd.value
            m_list = [m[0] for m in models_by_platform.get(sel_plat, [])]
            model_dd.options = [ft.dropdown.Option(m, m) for m in m_list]
            model_dd.value = m_list[0] if m_list else None
            self.page.update()

        platform_dd.on_change = on_platform_dd_change

        def rebuild_usage_tiles():
            usage_tiles_column.controls.clear()
            for idx, u in enumerate(usage_list):
                is_selected = idx == current_selected_idx[0]

                def select_item(e, item_idx=idx):
                    current_selected_idx[0] = item_idx
                    current_usage_data[0] = usage_list[item_idx]
                    sync_right_panel()
                    rebuild_usage_tiles()
                    self.page.update()

                usage_tiles_column.controls.append(
                    ft.Container(
                        content=ft.ListTile(
                            leading=ft.Icon(ft.Icons.LABEL_OUTLINE, size=20),
                            title=ft.Text(u["usage_label"], weight=ft.FontWeight.BOLD if is_selected else None),
                            subtitle=ft.Text(f"Key: {u['usage_key']}"),
                            dense=True,
                            on_click=select_item,
                        ),
                        bgcolor=ft.Colors.BLUE_50 if is_selected else None,
                        border_radius=6,
                    )
                )

        def add_usage(e):
            k_input = ft.TextField(label="用途标识 (Key, 英文)", autofocus=True)
            l_input = ft.TextField(label="显示名称 (Label)")

            def do_add_slot(ev):
                k = (k_input.value or "").strip()
                l = (l_input.value or "").strip() or k
                if not k:
                    return
                try:
                    self.ai_manager.create_user_usage_slot(user_id=system_user_id, usage_key=k, usage_label=l)
                    nonlocal all_models, usage_list
                    all_models, usage_list = load_system_data()
                    current_selected_idx[0] = len(usage_list) - 1
                    current_usage_data[0] = usage_list[-1] if usage_list else {}
                    self.page.close(add_dlg)
                    sync_right_panel()
                    rebuild_usage_tiles()
                    self.page.update()
                    self.log(f"✓ 已创建系统用途: {l} ({k})", tag="success")
                except Exception as ex:
                    self.show_error("添加失败", str(ex))

            add_dlg = ft.AlertDialog(
                title=ft.Text("新建系统用途"),
                content=ft.Column([k_input, l_input], tight=True, spacing=10),
                actions=[
                    ft.TextButton("取消", on_click=lambda ev: self.page.close(add_dlg)),
                    ft.ElevatedButton("创建", on_click=do_add_slot),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.open(add_dlg)

        def delete_usage(e):
            if current_selected_idx[0] < 0 or not current_usage_data[0]:
                self.show_warning("提示", "请先选择要删除的用途")
                return
            u = current_usage_data[0]
            k = u["usage_key"]

            def do_delete():
                try:
                    self.ai_manager.delete_user_usage_slot(user_id=system_user_id, usage_key=k)
                    nonlocal all_models, usage_list
                    all_models, usage_list = load_system_data()
                    current_selected_idx[0] = 0 if usage_list else -1
                    current_usage_data[0] = usage_list[0] if usage_list else {}
                    sync_right_panel()
                    rebuild_usage_tiles()
                    self.page.update()
                    self.log(f"✓ 已删除系统用途: {k}", tag="success")
                except Exception as ex:
                    self.show_error("删除失败", str(ex))

            self.ask_yes_no("确认删除", f"确定要删除系统用途 '{u['usage_label']}' ({k}) 吗？", on_yes=do_delete)

        def save_binding(e):
            if not current_usage_data[0]:
                self.show_warning("提示", "请先选择一个用途")
                return
            sel_plat = platform_dd.value
            sel_model = model_dd.value
            if not sel_plat or not sel_model:
                self.show_error("错误", "请先选择绑定的平台与模型")
                return
            model_info = next((m[1] for m in models_by_platform[sel_plat] if m[0] == sel_model), None)
            if not model_info:
                self.show_error("错误", "模型信息无效")
                return
            try:
                self.ai_manager.save_user_selection(
                    user_id=system_user_id,
                    platform_id=model_info["platform_id"],
                    model_id=model_info["model_id"],
                    usage_key=current_usage_data[0]["usage_key"],
                )
                self.log(f"✓ 用途 '{current_usage_data[0]['usage_key']}' 的绑定已更新", tag="success")
                self.show_snack(f"用途 '{current_usage_data[0]['usage_key']}' 绑定已成功更新！")
                nonlocal all_models, usage_list
                all_models, usage_list = load_system_data()
            except Exception as ex:
                self.show_error("保存失败", str(ex))

        sync_right_panel()
        rebuild_usage_tiles()

        layout = ft.Row(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("系统用途列表 (Usage Slots)", weight=ft.FontWeight.BOLD),
                            ft.Container(content=usage_tiles_column, expand=True, border=ft.border.all(1, ft.Colors.GREY_300), border_radius=6),
                            ft.Row([
                                ft.ElevatedButton("+ 新建用途", on_click=add_usage, expand=True),
                                ft.OutlinedButton("- 删除用途", on_click=delete_usage, expand=True),
                            ]),
                        ],
                        expand=True,
                    ),
                    width=300,
                ),
                ft.VerticalDivider(width=1),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("绑定模型配置", weight=ft.FontWeight.BOLD),
                            ft.Row([ft.Text("用途标识 (Key):", width=120), key_label_text]),
                            ft.Row([ft.Text("显示名称 (Label):", width=120), label_label_text]),
                            ft.Divider(height=1),
                            platform_dd,
                            model_dd,
                            ft.Row([ft.ElevatedButton("保存绑定配置", on_click=save_binding)], alignment=ft.MainAxisAlignment.END),
                        ],
                        expand=True,
                        spacing=12,
                    ),
                    expand=True,
                ),
            ],
            expand=True,
            spacing=16,
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("系统模型与用途管理"),
            content=ft.Container(content=layout, width=780, height=480),
            actions=[ft.TextButton("关闭", on_click=lambda e: self.page.close(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)

    # ------------------------------------------------------------------ #
    #  用户配额管理对话框                                                    #
    # ------------------------------------------------------------------ #

    def open_quota_manager_dialog(self, default_user_id=None):
        """打开用户配额管理对话框。"""
        user_id_entry = ft.TextField(
            label="用户 ID",
            value=str(default_user_id) if default_user_id is not None else "",
            expand=True,
        )

        quota_fields = [
            ("sys_paid_window_hours", "sys_paid 窗口小时数"),
            ("sys_paid_window_token_limit", "sys_paid 窗口 token 上限"),
            ("sys_paid_window_request_limit", "sys_paid 窗口请求上限"),
            ("sys_paid_total_token_limit", "sys_paid 总 token 上限"),
            ("sys_paid_total_request_limit", "sys_paid 总请求上限"),
            ("self_paid_window_hours", "self_paid 窗口小时数"),
            ("self_paid_window_token_limit", "self_paid 窗口 token 上限"),
            ("self_paid_window_request_limit", "self_paid 窗口请求上限"),
            ("self_paid_total_token_limit", "self_paid 总 token 上限"),
            ("self_paid_total_request_limit", "self_paid 总请求上限"),
        ]

        entries = {}
        for field_name, label_text in quota_fields:
            entries[field_name] = ft.TextField(label=label_text, dense=True, expand=True)

        status_box = ft.TextField(
            label="当前配额状态 (JSON)",
            multiline=True,
            read_only=True,
            min_lines=6,
            max_lines=9,
            expand=True,
        )

        def render_status(payload):
            status_box.value = json_lib.dumps(payload, ensure_ascii=False, indent=2)
            self.page.update()

        def fill_policy(payload):
            for field_name, _ in quota_fields:
                val = payload.get(field_name)
                entries[field_name].value = str(val) if val is not None else ""
            self.page.update()

        def load_quota(e=None):
            uid = (user_id_entry.value or "").strip()
            if not uid:
                self.show_warning("警告", "请先输入用户 ID")
                return
            try:
                policy = self.ai_manager.admin_get_user_quota_policy(uid)
                status = self.ai_manager.admin_get_user_quota_status(uid)
                fill_policy(policy)
                render_status(status)
                self.log(f"✓ 已加载用户 '{uid}' 的配额策略", tag="success")
            except Exception as ex:
                self.log(f"✗ 加载配额失败: {ex}", tag="error")
                self.show_error("错误", f"加载配额失败: {ex}")

        def save_quota(e):
            uid = (user_id_entry.value or "").strip()
            if not uid:
                self.show_warning("警告", "请先输入用户 ID")
                return
            payload = {}
            try:
                for field_name, _ in quota_fields:
                    raw_text = (entries[field_name].value or "").strip()
                    if not raw_text:
                        payload[field_name] = None
                    else:
                        parsed = int(raw_text)
                        min_v = 1 if field_name.endswith("_window_hours") else 0
                        if parsed < min_v:
                            raise ValueError(f"{field_name} 不能小于 {min_v}")
                        payload[field_name] = parsed
            except Exception as ex:
                self.show_error("错误", f"输入格式错误: {ex}")
                return

            try:
                self.ai_manager.admin_save_user_quota_policy(uid, **payload)
                status = self.ai_manager.admin_get_user_quota_status(uid)
                render_status(status)
                self.log(f"✓ 已保存用户 '{uid}' 的配额策略", tag="success")
                self.show_snack("用户配额策略已成功保存！")
            except Exception as ex:
                self.log(f"✗ 保存配额失败: {ex}", tag="error")
                self.show_error("错误", f"保存配额失败: {ex}")

        def clear_fields(e):
            for entry in entries.values():
                entry.value = ""
            status_box.value = ""
            self.page.update()

        left_col_controls = [entries[quota_fields[i][0]] for i in range(5)]
        right_col_controls = [entries[quota_fields[i][0]] for i in range(5, 10)]

        form_layout = ft.Column(
            [
                ft.Row([user_id_entry, ft.ElevatedButton("加载配额", on_click=load_quota)], spacing=10),
                ft.Text("配额策略（留空表示不限制；小时数字段 >= 1，其它字段 >= 0）：", size=12, color=ft.Colors.GREY_700),
                ft.Row(
                    [
                        ft.Column(left_col_controls, expand=True, spacing=6),
                        ft.Column(right_col_controls, expand=True, spacing=6),
                    ],
                    spacing=16,
                ),
                status_box,
            ],
            tight=True,
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("用户配额管理"),
            content=ft.Container(content=form_layout, width=760, height=520),
            actions=[
                ft.TextButton("清空", on_click=clear_fields),
                ft.ElevatedButton("保存配额", on_click=save_quota),
                ft.TextButton("关闭", on_click=lambda e: self.page.close(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)
        if default_user_id is not None:
            load_quota()

    # ------------------------------------------------------------------ #
    #  用户调用详情对话框                                                    #
    # ------------------------------------------------------------------ #

    def open_user_usage_detail_dialog(self, user_id: str):
        """打开单个用户的调用详情对话框。"""
        uid = str(user_id or "").strip()
        if not uid:
            return

        total_payload = self.ai_manager.get_user_usage_total(uid)
        stats_rows = self.ai_manager.get_user_usage_stats(uid)
        quota_payload = self.ai_manager.admin_get_user_quota_status(uid)

        def make_metric_card(title: str, val: str):
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Text(title, size=11, color=ft.Colors.GREY_600),
                        ft.Text(val, size=16, weight=ft.FontWeight.BOLD),
                    ],
                    spacing=2,
                    tight=True,
                ),
                padding=10,
                bgcolor=ft.Colors.GREY_100,
                border_radius=6,
                expand=True,
            )

        summary_row = ft.Row(
            [
                make_metric_card("总请求", f"{int(total_payload.get('requests', 0))} 次"),
                make_metric_card("总 Token", self._fmt_tokens(total_payload.get("tokens", 0))),
                make_metric_card("站长付费", f"{int(quota_payload.get('sys_paid', {}).get('total', {}).get('usage', {}).get('requests', 0))} 次"),
                make_metric_card("用户自费", f"{int(quota_payload.get('self_paid', {}).get('total', {}).get('usage', {}).get('requests', 0))} 次"),
            ],
            spacing=8,
        )

        detail_data = list(stats_rows)
        sort_state = {"column": "calls", "desc": True}

        data_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("平台")),
                ft.DataColumn(ft.Text("模型")),
                ft.DataColumn(ft.Text("调用"), numeric=True),
                ft.DataColumn(ft.Text("总 Token"), numeric=True),
                ft.DataColumn(ft.Text("Prompt"), numeric=True),
                ft.DataColumn(ft.Text("Completion"), numeric=True),
                ft.DataColumn(ft.Text("成功"), numeric=True),
                ft.DataColumn(ft.Text("错误"), numeric=True),
            ],
            rows=[],
            heading_row_height=36,
            data_row_min_height=32,
            data_row_max_height=36,
        )

        def render_detail_rows():
            data_table.rows.clear()
            for r in detail_data:
                data_table.rows.append(
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(str(r.get("platform_name", "-")))),
                            ft.DataCell(ft.Text(str(r.get("display_name", "-")))),
                            ft.DataCell(ft.Text(str(int(r.get("call_count", 0))))),
                            ft.DataCell(ft.Text(self._fmt_tokens(r.get("total_tokens", 0)))),
                            ft.DataCell(ft.Text(self._fmt_tokens(r.get("prompt_tokens", 0)))),
                            ft.DataCell(ft.Text(self._fmt_tokens(r.get("completion_tokens", 0)))),
                            ft.DataCell(ft.Text(str(int(r.get("success_count", 0))))),
                            ft.DataCell(ft.Text(str(int(r.get("error_count", 0))))),
                        ]
                    )
                )

        detail_data.sort(key=lambda r: int(r.get("call_count", 0)), reverse=True)
        render_detail_rows()

        content_layout = ft.Column(
            [
                summary_row,
                ft.Text("按模型聚合明细：", size=12, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=ft.Column([data_table], scroll=ft.ScrollMode.AUTO),
                    expand=True,
                    border=ft.border.all(1, ft.Colors.GREY_300),
                    border_radius=6,
                ),
            ],
            expand=True,
            spacing=10,
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"用户详情 · {uid}"),
            content=ft.Container(content=content_layout, width=820, height=480),
            actions=[
                ft.ElevatedButton(
                    "编辑配额",
                    on_click=lambda e: [self.page.close(dlg), self.open_quota_manager_dialog(default_user_id=uid)],
                ),
                ft.TextButton("关闭", on_click=lambda e: self.page.close(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)
