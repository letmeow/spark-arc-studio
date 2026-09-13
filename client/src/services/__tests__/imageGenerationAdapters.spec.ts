import { describe, expect, it } from 'vitest';

import {
  IMAGE_GENERATION_ADAPTER_KEYS,
  normalizeImageGenerationAdapter,
} from '../imageGenerationAdapters';

describe('生图协议镜像', () => {
  it('包含后端公开的全部显式协议', () => {
    expect(IMAGE_GENERATION_ADAPTER_KEYS).toEqual([
      'openai_images',
      'openai_responses_image',
      'gemini_generate_content',
      'gemini_interactions',
      'xai_images',
    ]);
  });

  it('规范化 Responses 与 Gemini generateContent 别名', () => {
    expect(normalizeImageGenerationAdapter('responses_image')).toBe('openai_responses_image');
    expect(normalizeImageGenerationAdapter('google_generate_content')).toBe('gemini_generate_content');
  });

  it('未知协议回落到 OpenAI Images', () => {
    expect(normalizeImageGenerationAdapter('unknown')).toBe('openai_images');
  });

  it('已彻底删除的 chat 协议按未知值回落', () => {
    expect(normalizeImageGenerationAdapter('openai_chat_image')).toBe('openai_images');
    expect(normalizeImageGenerationAdapter('chat_completions')).toBe('openai_images');
  });
});
