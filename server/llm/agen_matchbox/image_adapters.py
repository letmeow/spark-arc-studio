"""生图协议适配器配置辅助。"""

from __future__ import annotations

from typing import Any, Optional


IMAGE_ADAPTER_OPENAI_IMAGES = "openai_images"
IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE = "openai_responses_image"
IMAGE_ADAPTER_XAI_IMAGES = "xai_images"
IMAGE_ADAPTER_GEMINI_INTERACTIONS = "gemini_interactions"
IMAGE_ADAPTER_GEMINI_GENERATE_CONTENT = "gemini_generate_content"

DEFAULT_IMAGE_GENERATION_ADAPTER = IMAGE_ADAPTER_OPENAI_IMAGES

IMAGE_GENERATION_ADAPTERS = {
    IMAGE_ADAPTER_OPENAI_IMAGES,
    IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    IMAGE_ADAPTER_XAI_IMAGES,
    IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    IMAGE_ADAPTER_GEMINI_GENERATE_CONTENT,
}

_ADAPTER_ALIASES = {
    "openai": IMAGE_ADAPTER_OPENAI_IMAGES,
    "openai_images": IMAGE_ADAPTER_OPENAI_IMAGES,
    "openai_compatible": IMAGE_ADAPTER_OPENAI_IMAGES,
    "images": IMAGE_ADAPTER_OPENAI_IMAGES,
    "images_generations": IMAGE_ADAPTER_OPENAI_IMAGES,
    "gpt_image": IMAGE_ADAPTER_OPENAI_IMAGES,
    "gpt-image": IMAGE_ADAPTER_OPENAI_IMAGES,
    "openai_responses": IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    "openai_responses_image": IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    "responses": IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    "responses_image": IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    "responses_image_generation": IMAGE_ADAPTER_OPENAI_RESPONSES_IMAGE,
    "xai": IMAGE_ADAPTER_XAI_IMAGES,
    "xai_images": IMAGE_ADAPTER_XAI_IMAGES,
    "grok": IMAGE_ADAPTER_XAI_IMAGES,
    "grok_image": IMAGE_ADAPTER_XAI_IMAGES,
    "grok_images": IMAGE_ADAPTER_XAI_IMAGES,
    "grok_imagine": IMAGE_ADAPTER_XAI_IMAGES,
    "gemini": IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    "google": IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    "google_gemini": IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    "gemini_interactions": IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    "google_interactions": IMAGE_ADAPTER_GEMINI_INTERACTIONS,
    "gemini_generate_content": IMAGE_ADAPTER_GEMINI_GENERATE_CONTENT,
    "google_generate_content": IMAGE_ADAPTER_GEMINI_GENERATE_CONTENT,
}


def normalize_image_generation_adapter(value: Any) -> Optional[str]:
    """规范化生图协议适配器；无法识别时返回 None，由调用方回落默认。"""
    text = str(value or "").strip().lower()
    if not text:
        return None
    return _ADAPTER_ALIASES.get(text)


def strip_internal_image_generation_fields(extra_body: Any) -> Optional[dict[str, Any]]:
    """移除不应上传给云端的内部生图控制字段。"""
    if not isinstance(extra_body, dict):
        return None

    cleaned = dict(extra_body)
    image_config = cleaned.get("image_generation")
    if isinstance(image_config, dict):
        image_config = dict(image_config)
        image_config.pop("adapter", None)
        if image_config:
            cleaned["image_generation"] = image_config
        else:
            cleaned.pop("image_generation", None)

    return cleaned or None
