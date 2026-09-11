"""模型输出模态与文本 Agent 能力边界测试（从主项目迁移）。"""

from types import SimpleNamespace

from ..models import (
    is_chat_model,
    is_image_generation_model,
    is_text_generation_model,
)


def test_multimodal_image_model_is_not_a_text_agent_model() -> None:
    model = SimpleNamespace(
        input_modalities='["text", "image"]',
        output_modalities='["text", "image"]',
    )

    assert is_image_generation_model(model) is True
    assert is_text_generation_model(model) is False
    assert is_chat_model(model) is False


def test_vision_text_model_remains_available_to_text_agents() -> None:
    model = SimpleNamespace(
        input_modalities='["text", "image"]',
        output_modalities='["text"]',
    )

    assert is_image_generation_model(model) is False
    assert is_text_generation_model(model) is True
    assert is_chat_model(model) is True
