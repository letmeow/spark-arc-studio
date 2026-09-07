import { normalizeToolName } from '@/components/stores/chatDomain';

export type ToolDetailSectionKey = 'input' | 'result' | 'error';

export type ToolDetailEntry = {
  key: string;
  labelKey: string;
  text: string;
};

export type ToolDetailSection = {
  key: ToolDetailSectionKey;
  labelKey: string;
  entries: ToolDetailEntry[];
};

export type ToolDisplayDetails = {
  toolName: string;
  expandable: boolean;
  sections: ToolDetailSection[];
};

const PATCH_FIELDS = ['search_text', 'replace_text'] as const;
const WORK_TRACKER_FAILURE_PREFIXES = ['任务板更新失败', '读取任务板失败'] as const;

/** 只列出适合展示给用户的参数；工具请求本身不会使用这份映射。 */
export const TOOL_DETAIL_FIELD_POLICIES: Readonly<Record<string, readonly string[]>> = {
  delegate_task: [
    'target_agent', 'task_description', 'completion_mode', 'chapter_name',
    'scene_name', 'scene_file_path', 'scene_guidance', 'scene_characters',
  ],
  // 滑窗读窗：只展示指针，不展示正文（正文走内存 ToolMessage，不进落盘 segments）。
  describe_longread_source: ['source_id'],
  read_longread_window: ['source_id', 'chunk_index'],
  read_worldview_window: ['chunk_index'],
  read_attachment_chunk: ['attachment_id', 'chunk_index'],
  note_window_clues: ['source_id', 'chunk_index', 'clue_type', 'importance'],
  // 检索：展示“搜了什么”（pattern/query + scope），不展示命中正文。
  search_project: ['pattern', 'scope', 'max_results'],
  semantic_search: ['query', 'scope', 'k'],
  replace_from_search: ['indices', 'replacement'],
  patch_script: PATCH_FIELDS,
  patch_worldview: PATCH_FIELDS,
  patch_synopsis: PATCH_FIELDS,
  patch_beat_sheet: PATCH_FIELDS,
  patch_outline: PATCH_FIELDS,
  web_search: ['provider', 'query', 'num_results', 'exa_options', 'tavily_options'],
  work_tracker: ['overwrite', 'items', 'operations', 'summary', 'contract'],
  rewrite_inspiration: ['content', 'title', 'tags'],
  rewrite_worldview: ['content'],
  rewrite_all_characters: ['characters'],
  rewrite_outline: ['outline'],
  rewrite_synopsis: ['synopsis'],
  rewrite_beat_sheet: ['beat_sheet'],
  update_character: ['character_id', 'name', 'content'],
  create_character_relation: ['source_character', 'target_character', 'relation', 'description'],
  update_project_story_tags: [
    'workspace_mode', 'style', 'genres', 'tones', 'worldviews', 'pov',
    'length_hint', 'scene_length_hint', 'scene_target_chars', 'active_inspiration_id',
  ],
  create_or_rewrite_script: ['chapter_name', 'work_name', 'scene_name', 'content', 'file_path'],
  create_chapter: ['chapter_name'],
  organize_scenes_to_chapter: ['scene_paths', 'new_chapter_name', 'chapter_num', 'preserve_originals'],

  // 后端继续使用 chapter/scene 兼容字段；这里仅控制适合用户查看的参数白名单。
  rename_chapter: ['chapter_path', 'new_chapter_name', 'old_name', 'new_name', 'chapter_name', 'path', 'file_path'],
  rename_scene: ['scene_path', 'new_scene_name', 'old_name', 'new_name', 'scene_name', 'path', 'file_path'],
  batch_rename_chapters: ['renames'],
  batch_rename_scenes: ['renames'],
  batch_update_story_metadata: ['updates'],
  rename_chapter_folder: ['chapter_path', 'new_chapter_name', 'old_name', 'new_name', 'chapter_name', 'path', 'file_path'],
  rename_scene_file: ['scene_path', 'new_scene_name', 'old_name', 'new_name', 'scene_name', 'path', 'file_path'],
  rename_chapter_metadata: ['old_name', 'new_name', 'chapter_name', 'path', 'file_path'],
  rename_scene_metadata: ['old_name', 'new_name', 'scene_name', 'path', 'file_path'],
  reorder_chapters: ['chapter_paths', 'chapter_index', 'from_index', 'to_index', 'order', 'items'],
  reorder_scenes: ['chapter_path', 'scene_paths', 'scene_index', 'from_index', 'to_index', 'order', 'items'],
  reorder_chapter: ['chapter_index', 'from_index', 'to_index', 'order', 'items'],
  reorder_scene: ['scene_index', 'from_index', 'to_index', 'order', 'items'],
  organize_chapters: ['chapter_index', 'from_index', 'to_index', 'order', 'items'],
  organize_scenes: ['scene_index', 'from_index', 'to_index', 'order', 'items'],
  update_chapter_metadata: ['chapter_name', 'chapter_index', 'path', 'file_path', 'metadata'],
  update_scene_metadata: ['scene_name', 'scene_index', 'path', 'file_path', 'metadata'],
  batch_update_chapters: ['items', 'order', 'metadata'],
  batch_update_scenes: ['items', 'order', 'metadata'],
};

const FIELD_LABEL_KEYS: Readonly<Record<string, string>> = {
  value: 'components.chatMessageList.toolDetails.value',
  source_id: 'components.chatMessageList.toolDetails.fields.sourceId',
  attachment_id: 'components.chatMessageList.toolDetails.fields.attachmentId',
  chunk_index: 'components.chatMessageList.toolDetails.fields.chunkIndex',
  clue_type: 'components.chatMessageList.toolDetails.fields.clueType',
  importance: 'components.chatMessageList.toolDetails.fields.importance',
  pattern: 'components.chatMessageList.toolDetails.fields.pattern',
  scope: 'components.chatMessageList.toolDetails.fields.scope',
  max_results: 'components.chatMessageList.toolDetails.fields.maxResults',
  query: 'components.chatMessageList.toolDetails.fields.query',
  k: 'components.chatMessageList.toolDetails.fields.resultCount',
  target_agent: 'components.chatMessageList.toolDetails.fields.targetAgent',
  task_description: 'components.chatMessageList.toolDetails.fields.taskDescription',
  completion_mode: 'components.chatMessageList.toolDetails.fields.completionMode',
  chapter_name: 'components.chatMessageList.toolDetails.fields.chapterName',
  scene_name: 'components.chatMessageList.toolDetails.fields.sceneName',
  work_name: 'components.chatMessageList.toolDetails.fields.workName',
  scene_file_path: 'components.chatMessageList.toolDetails.fields.sceneFilePath',
  scene_guidance: 'components.chatMessageList.toolDetails.fields.sceneGuidance',
  scene_characters: 'components.chatMessageList.toolDetails.fields.sceneCharacters',
  indices: 'components.chatMessageList.toolDetails.fields.indices',
  replacement: 'components.chatMessageList.toolDetails.fields.replacement',
  search_text: 'components.chatMessageList.toolDetails.fields.searchText',
  replace_text: 'components.chatMessageList.toolDetails.fields.replaceText',
  provider: 'components.chatMessageList.toolDetails.fields.provider',
  query: 'components.chatMessageList.toolDetails.fields.query',
  num_results: 'components.chatMessageList.toolDetails.fields.numResults',
  exa_options: 'components.chatMessageList.toolDetails.fields.exaOptions',
  tavily_options: 'components.chatMessageList.toolDetails.fields.tavilyOptions',
  overwrite: 'components.chatMessageList.toolDetails.fields.overwrite',
  items: 'components.chatMessageList.toolDetails.fields.items',
  operations: 'components.chatMessageList.toolDetails.fields.operations',
  summary: 'components.chatMessageList.toolDetails.fields.summary',
  contract: 'components.chatMessageList.toolDetails.fields.contract',
  content: 'components.chatMessageList.toolDetails.fields.content',
  title: 'components.chatMessageList.toolDetails.fields.title',
  tags: 'components.chatMessageList.toolDetails.fields.tags',
  characters: 'components.chatMessageList.toolDetails.fields.characters',
  outline: 'components.chatMessageList.toolDetails.fields.outline',
  synopsis: 'components.chatMessageList.toolDetails.fields.synopsis',
  beat_sheet: 'components.chatMessageList.toolDetails.fields.beatSheet',
  character_id: 'components.chatMessageList.toolDetails.fields.characterId',
  name: 'components.chatMessageList.toolDetails.fields.name',
  source_character: 'components.chatMessageList.toolDetails.fields.sourceCharacter',
  target_character: 'components.chatMessageList.toolDetails.fields.targetCharacter',
  relation: 'components.chatMessageList.toolDetails.fields.relation',
  description: 'components.chatMessageList.toolDetails.fields.description',
  workspace_mode: 'components.chatMessageList.toolDetails.fields.workspaceMode',
  style: 'components.chatMessageList.toolDetails.fields.style',
  genres: 'components.chatMessageList.toolDetails.fields.genres',
  tones: 'components.chatMessageList.toolDetails.fields.tones',
  worldviews: 'components.chatMessageList.toolDetails.fields.worldviews',
  pov: 'components.chatMessageList.toolDetails.fields.pov',
  length_hint: 'components.chatMessageList.toolDetails.fields.lengthHint',
  scene_length_hint: 'components.chatMessageList.toolDetails.fields.sceneLengthHint',
  scene_target_chars: 'components.chatMessageList.toolDetails.fields.sceneTargetChars',
  active_inspiration_id: 'components.chatMessageList.toolDetails.fields.activeInspirationId',
  file_path: 'components.chatMessageList.toolDetails.fields.filePath',
  scene_names: 'components.chatMessageList.toolDetails.fields.sceneNames',
  scene_files: 'components.chatMessageList.toolDetails.fields.sceneFiles',
  old_name: 'components.chatMessageList.toolDetails.fields.oldName',
  new_name: 'components.chatMessageList.toolDetails.fields.newName',
  path: 'components.chatMessageList.toolDetails.fields.path',
  chapter_path: 'components.chatMessageList.toolDetails.fields.chapterPath',
  new_chapter_name: 'components.chatMessageList.toolDetails.fields.newChapterName',
  scene_path: 'components.chatMessageList.toolDetails.fields.scenePath',
  new_scene_name: 'components.chatMessageList.toolDetails.fields.newSceneName',
  chapter_paths: 'components.chatMessageList.toolDetails.fields.chapterPaths',
  scene_paths: 'components.chatMessageList.toolDetails.fields.scenePaths',
  chapter_index: 'components.chatMessageList.toolDetails.fields.chapterIndex',
  scene_index: 'components.chatMessageList.toolDetails.fields.sceneIndex',
  from_index: 'components.chatMessageList.toolDetails.fields.fromIndex',
  to_index: 'components.chatMessageList.toolDetails.fields.toIndex',
  order: 'components.chatMessageList.toolDetails.fields.order',
  metadata: 'components.chatMessageList.toolDetails.fields.metadata',
  renames: 'components.chatMessageList.toolDetails.fields.renames',
  updates: 'components.chatMessageList.toolDetails.fields.updates',
};

const SECTION_LABEL_KEYS: Readonly<Record<ToolDetailSectionKey, string>> = {
  input: 'components.chatMessageList.toolDetails.input',
  result: 'components.chatMessageList.toolDetails.result',
  error: 'components.chatMessageList.toolDetails.error',
};

function hasValue(value: unknown) {
  return value !== undefined && value !== null && value !== '';
}

function hasOwn(value: unknown, key: string): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, key);
}

function formatDetailValue(value: unknown) {
  if (typeof value === 'string') return value;
  try {
    const encoded = JSON.stringify(value, null, 2);
    return encoded === undefined ? String(value) : encoded;
  } catch {
    return String(value);
  }
}

function getFieldLabelKey(field: string) {
  return FIELD_LABEL_KEYS[field] || 'components.chatMessageList.toolDetails.value';
}

function makeEntries(value: unknown, allowedFields?: readonly string[]): ToolDetailEntry[] {
  if (!hasValue(value)) return [];
  if (!allowedFields || !allowedFields.length || !value || typeof value !== 'object' || Array.isArray(value)) {
    return [{ key: 'value', labelKey: getFieldLabelKey('value'), text: formatDetailValue(value) }];
  }

  return allowedFields
    .filter(field => hasOwn(value, field) && hasValue(value[field]))
    .map(field => ({
      key: field,
      labelKey: getFieldLabelKey(field),
      text: formatDetailValue(value[field]),
    }));
}

function getPolicy(toolName: string) {
  const explicit = TOOL_DETAIL_FIELD_POLICIES[toolName];
  if (explicit) return explicit;
  // 所有局部替换工具共享同一组可读参数，但不放开其他字段。
  if (toolName.startsWith('patch_')) return PATCH_FIELDS;
  return undefined;
}

function makeSection(key: ToolDetailSectionKey, value: unknown, allowedFields?: readonly string[]) {
  const entries = makeEntries(value, allowedFields);
  if (!entries.length) return null;
  return {
    key,
    labelKey: SECTION_LABEL_KEYS[key],
    entries,
  } satisfies ToolDetailSection;
}

function getEffectiveToolError(toolName: string, segment: Record<string, unknown>) {
  if (hasOwn(segment, 'tool_error') && hasValue(segment.tool_error)) {
    return segment.tool_error;
  }

  // 兼容旧事件：部分链路曾把任务板校验失败文本放在 tool_result 中。
  if (
    toolName === 'work_tracker'
    && typeof segment.tool_result === 'string'
  ) {
    const toolResult = segment.tool_result;
    if (WORK_TRACKER_FAILURE_PREFIXES.some(prefix => toolResult.trimStart().startsWith(prefix))) {
      return toolResult;
    }
  }

  return undefined;
}

export function adaptToolDetails(toolName: unknown, segment: Record<string, unknown> = {}): ToolDisplayDetails {
  const normalizedToolName = normalizeToolName(toolName);
  const policy = getPolicy(normalizedToolName);
  const sections: ToolDetailSection[] = [];
  const effectiveError = getEffectiveToolError(normalizedToolName, segment);
  const hasError = hasValue(effectiveError);

  // 输入只从已知工具的字段白名单取值，避免未知工具把完整参数回显给用户。
  if (policy && hasOwn(segment, 'tool_input')) {
    const section = makeSection('input', segment.tool_input, policy);
    if (section) sections.push(section);
  }
  if (policy && hasOwn(segment, 'tool_result') && !hasError) {
    const section = makeSection('result', segment.tool_result);
    if (section) sections.push(section);
  }
  // 失败详情是诊断信息，即使工具不在成功展示白名单中也允许展示。
  if (hasError) {
    const section = makeSection('error', effectiveError);
    if (section) sections.push(section);
  }

  return {
    toolName: normalizedToolName,
    expandable: sections.length > 0,
    sections,
  };
}
