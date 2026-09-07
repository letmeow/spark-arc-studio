import { i18n } from '@/i18n';
import { normalizeToolName } from '../chatDomain';

export type ToolUiBinding = {
  scope: string;
  target: string;
  refreshEvents: string[];
};

type AnyRecord = Record<string, any>;

export const TOOL_PRESENTATION_KEY_MAP: Record<string, string> = {
  rewrite_inspiration: 'rewriteInspiration',
  rewrite_worldview: 'rewriteWorldview',
  rewrite_all_characters: 'rewriteAllCharacters',
  update_character: 'updateCharacter',
  patch_worldview: 'patchWorldview',
  rewrite_synopsis: 'rewriteSynopsis',
  patch_synopsis: 'patchSynopsis',
  rewrite_beat_sheet: 'rewriteBeatSheet',
  patch_beat_sheet: 'patchBeatSheet',
  rewrite_outline: 'rewriteOutline',
  patch_outline: 'patchOutline',
  create_chapter: 'createChapter',
  prepare_script_creation: 'prepareScriptCreation',
  create_or_rewrite_script: 'createOrRewriteScript',
  rename_chapter: 'renameChapter',
  rename_scene: 'renameScene',
  batch_rename_chapters: 'batchRenameChapters',
  batch_rename_scenes: 'batchRenameScenes',
  batch_update_story_metadata: 'batchUpdateStoryMetadata',
  reorder_chapters: 'reorderChapters',
  reorder_scenes: 'reorderScenes',
  organize_scenes_to_chapter: 'organizeScenesToChapter',
  patch_script: 'patchScript',
  list_chapters: 'listChapters',
  read_chapter_scene: 'readChapterScene',
  read_chapter_outline_raw: 'readChapterOutlineRaw',
  read_attachment_chunk: 'readAttachmentChunk',
  read_longread_window: 'readLongreadWindow',
  describe_longread_source: 'describeLongreadSource',
  note_window_clues: 'noteWindowClues',
  read_worldview_window: 'readWorldviewWindow',
  read_worldview: 'readWorldview',
  read_character: 'readCharacter',
  read_synopsis: 'readSynopsis',
  read_beat_sheet: 'readBeatSheet',
  work_tracker: 'workTracker',
  update_project_story_tags: 'updateProjectStoryTags',
  read_project_story_tags: 'readProjectStoryTags',
  story_memory_tool: 'storyMemoryTool',
  graph_rag_tool: 'graphRagTool',
  delegate_task: 'delegateTask',
  capture_inspiration: 'captureInspiration',
  list_inspirations: 'listInspirations',
  read_inspiration: 'readInspiration',
  bind_inspiration_to_current_project: 'bindInspirationToProject',
  trigger_auto_write: 'triggerAutoWrite',
  check_scriptwriter_status: 'checkScriptwriterStatus',
  search_project: 'searchProject',
  semantic_search: 'semanticSearch',
  replace_from_search: 'replaceFromSearch',
  web_search: 'webSearch',
  search_skills: 'searchSkills',
  read_skill: 'readSkill',
  read_skill_reference: 'readSkillReference',
  search_chat_history: 'searchChatHistory',
};

function getSearchProviderSuffix(toolName: unknown, metadata: AnyRecord = {}) {
  if (normalizeToolName(toolName) !== 'web_search') return '';
  const provider = String(metadata.tool_provider || metadata.toolProvider || '').trim().toLowerCase();
  if (provider === 'exa') return 'webSearchExa';
  if (provider === 'tavily') return 'webSearchTavily';
  return '';
}

export function getToolNameLabelKey(toolName: unknown, metadata: AnyRecord = {}) {
  const providerSuffix = getSearchProviderSuffix(toolName, metadata);
  if (providerSuffix) return `components.chatMessageList.tools.${providerSuffix}`;
  const suffix = TOOL_PRESENTATION_KEY_MAP[normalizeToolName(toolName)];
  return suffix ? `components.chatMessageList.tools.${suffix}` : '';
}

export function getToolProgressText(toolName: unknown, fallbackText = '', metadata: AnyRecord = {}) {
  const normalizedToolName = normalizeToolName(toolName);
  const suffix = getSearchProviderSuffix(normalizedToolName, metadata) || TOOL_PRESENTATION_KEY_MAP[normalizedToolName];
  if (suffix) return i18n.global.t(`chatStore.toolProgress.${suffix}`);
  if (fallbackText && fallbackText.trim()) return fallbackText.trim();
  return i18n.global.t('chatStore.toolProgress.executingTool', { tool: normalizedToolName || 'unknown' });
}

function isLorebookRewriteTool(toolName: unknown) {
  const normalizedToolName = normalizeToolName(toolName);
  return normalizedToolName === 'rewrite_worldview' || normalizedToolName === 'rewrite_all_characters' || normalizedToolName === 'update_character';
}

function isMuseRewriteTool(toolName: unknown) {
  return normalizeToolName(toolName) === 'rewrite_inspiration';
}

function isOutlineRewriteTool(toolName: unknown) {
  const normalizedToolName = normalizeToolName(toolName);
  return normalizedToolName === 'rewrite_outline' || normalizedToolName === 'patch_outline' || normalizedToolName === 'read_chapter_outline_raw';
}

function isSynopsisTool(toolName: unknown) {
  const normalizedToolName = normalizeToolName(toolName);
  return normalizedToolName === 'rewrite_synopsis' || normalizedToolName === 'patch_synopsis';
}

function isBeatSheetTool(toolName: unknown) {
  const normalizedToolName = normalizeToolName(toolName);
  return normalizedToolName === 'rewrite_beat_sheet' || normalizedToolName === 'patch_beat_sheet';
}

function isStoryTagsTool(toolName: unknown) {
  return normalizeToolName(toolName) === 'update_project_story_tags';
}

function getLorebookRefreshTarget(toolName: unknown) {
  const normalizedToolName = normalizeToolName(toolName);
  if (normalizedToolName === 'rewrite_worldview') return 'worldview';
  if (normalizedToolName === 'rewrite_all_characters' || normalizedToolName === 'update_character') return 'characters';
  return '';
}

function getToolUiBinding(toolName: unknown): ToolUiBinding {
  if (isMuseRewriteTool(toolName)) {
    return {
      scope: 'muse',
      target: '',
      refreshEvents: ['muse-refresh'],
    };
  }

  if (isLorebookRewriteTool(toolName)) {
    const target = getLorebookRefreshTarget(toolName);
    const refreshEvents = ['lorebook-refresh'];
    if (target === 'worldview') refreshEvents.unshift('lorebook-refresh-worldview');
    if (target === 'characters') refreshEvents.unshift('lorebook-refresh-characters');
    return {
      scope: 'world',
      target,
      refreshEvents,
    };
  }

  if (isOutlineRewriteTool(toolName)) {
    return {
      scope: 'outline',
      target: '',
      refreshEvents: ['outline-refresh'],
    };
  }

  if (isSynopsisTool(toolName)) {
    return {
      scope: 'synopsis',
      target: 'content',
      refreshEvents: ['synopsis-refresh'],
    };
  }

  if (isBeatSheetTool(toolName)) {
    return {
      scope: 'synopsis',
      target: 'beats',
      refreshEvents: ['synopsis-refresh'],
    };
  }

  if (isStoryTagsTool(toolName)) {
    return {
      scope: 'story-tags',
      target: '',
      refreshEvents: ['story-tags-refresh'],
    };
  }

  return {
    scope: '',
    target: '',
    refreshEvents: [],
  };
}

export function resolveToolUiBinding(toolName: unknown, evt: AnyRecord = {}): ToolUiBinding {
  const base = getToolUiBinding(toolName);
  const uiScope = String(evt?.ui_scope || evt?.uiScope || '').trim();
  const uiTarget = String(evt?.ui_target || evt?.uiTarget || '').trim();
  const uiRefreshEvents = Array.isArray(evt?.ui_refresh_events)
    ? evt.ui_refresh_events
    : Array.isArray(evt?.uiRefreshEvents)
      ? evt.uiRefreshEvents
      : null;

  return {
    scope: uiScope || base.scope || '',
    target: uiTarget || base.target || '',
    refreshEvents: uiRefreshEvents?.filter(Boolean) || base.refreshEvents || [],
  };
}

export function getToolUiTaskKey(binding: AnyRecord = {}) {
  const scope = String(binding?.scope || '').trim();
  const target = String(binding?.target || '').trim();
  return scope ? `${scope}::${target}` : '';
}
