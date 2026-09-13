export const IMAGE_GENERATION_ADAPTER_KEYS = [
  'openai_images',
  'openai_responses_image',
  'gemini_generate_content',
  'gemini_interactions',
  'xai_images',
] as const;

export type ImageGenerationAdapterKey = typeof IMAGE_GENERATION_ADAPTER_KEYS[number];

export const DEFAULT_IMAGE_GENERATION_ADAPTER: ImageGenerationAdapterKey = 'openai_images';

const ADAPTER_ALIASES: Record<string, ImageGenerationAdapterKey> = {
  openai: 'openai_images',
  openai_images: 'openai_images',
  openai_compatible: 'openai_images',
  gpt_image: 'openai_images',
  'gpt-image': 'openai_images',
  openai_responses: 'openai_responses_image',
  openai_responses_image: 'openai_responses_image',
  responses: 'openai_responses_image',
  responses_image: 'openai_responses_image',
  responses_image_generation: 'openai_responses_image',
  gemini: 'gemini_interactions',
  google: 'gemini_interactions',
  google_gemini: 'gemini_interactions',
  gemini_interactions: 'gemini_interactions',
  google_interactions: 'gemini_interactions',
  gemini_generate_content: 'gemini_generate_content',
  google_generate_content: 'gemini_generate_content',
  xai: 'xai_images',
  xai_images: 'xai_images',
  grok: 'xai_images',
  grok_image: 'xai_images',
  grok_images: 'xai_images',
  grok_imagine: 'xai_images',
};

export function normalizeImageGenerationAdapter(value: unknown): ImageGenerationAdapterKey {
  const text = String(value || '').trim().toLowerCase();
  return ADAPTER_ALIASES[text] || DEFAULT_IMAGE_GENERATION_ADAPTER;
}
