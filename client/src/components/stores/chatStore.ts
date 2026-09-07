/**
 * chatStore.js — 对话 Agent 的 Pinia 会话状态管理器
 *
 * 本文件承担双重职责：
 *
 * 1. 【对话状态管理】
 *    维护所有 Agent 会话（主会话 + 额外窗口会话）的消息历史、发送状态、
 *    工具调用状态（toolCalling / toolName / toolProgressText）等响应式数据。
 *
 * 2. 【SSE 流代理 → createStreamingTask 的桥接层】
 *    通过内部的 `_consumeStream` 方法，统一解析来自后端 chat.py 的 NDJSON 事件流。
 *    当检测到 `tool_exec_started` / `tool_exec_finished` 等工具调用事件时，
 *    本 Store 会自动充当"代理客户"，代表大模型向 createStreamingTask 申请加载遮罩，
 *    使工具执行过程对用户呈现与"一键生成按钮"完全一致的加载视觉体验。
 *
 * 【与 streamingRuntime.js 的关系】
 *    streamingRuntime.js 中的 createStreamingTask 是系统标准加载管线的唯一入口。
 *    本文件在 `import { createStreamingTask }` 后，在工具调用事件触发时动态实例化任务（见 _consumeStream 内部的 startPanelToolTask）。
 *    ⚠️ 不要在本文件之外另行实现类似的 SSE→遮罩桥接逻辑，以免产生双重遮罩或状态不同步。
 *
 * 【与 production.py（业务 SSE 流）的边界】
 *    本 Store 只消费 chat.py 的对话流（NDJSON 格式，含 event 字段）。
 *    production.py 发出的业务语义流（如剧本生成、场景桥接）由各自的前端 composable 直接调用
 *    createStreamingTask 消费，不经过本 Store。
 */
import { defineStore } from 'pinia';
import { i18n } from '@/i18n';

import { getChatHistory, sendChatMessageStream, clearChatHistory, compactChatContext, deleteChatMessage, removeChatMessageAttachment, editChatMessageStream, getChatTaskStatus, getChatRecentTasks, cancelChatTask, reconnectChatTaskStream } from '@/services/chatService';
import { useProjectStore } from './projectStore';
import { useDirectorAutoWriteStore } from './directorAutoWriteStore';
import bus from '@/eventBus';
import { createStreamingTask } from '@/utils/streamingRuntime';
import {
  consumeThinkStreamChunk,
  flushThinkStreamState,
  getLatestHistoryTerminalError,
  mergeToolTrace,
  normalizeToolName,
  reconcileSessionHistory,
} from './chatDomain';
import {
  getToolProgressText,
  getToolUiTaskKey,
  resolveToolUiBinding,
} from './chat/toolUi';
import {
  applyPersistedTokenStats,
  extractContextWindowStats,
  latestHistoryContextWindowStats,
  restoreHistoryTokenStats,
  type ContextWindowStats,
  type TokenUsageStats,
} from './chat/tokenStats';
import {
  buildContextCompactionSegmentPayload,
  coerceStreamEventText,
  extractContextWindowEventPatch,
  extractTaskDoneMetadataPatch,
  pickStreamEventText,
} from './chat/streamEvents';
import {
  buildUserMessageMetadata,
  extractImportedFilesMeta,
  findLatestImportedContexts,
  markSessionImportedFileDeleted,
  resolveActiveContext,
  resolveMessageContextForEdit,
  sameImportedFile,
  type ChatImportedContext,
  type ResolvedMessageContext,
} from './chat/attachments';

type AnyRecord = Record<string, any>;

type ChatSessionKind = 'primary' | 'extra';
type RetryMode = 'model' | 'transport';
type ChatTaskAuthorityState = 'running' | 'terminal' | 'missing' | 'unknown';

type ChatSession = {
  id: number;
  kind: ChatSessionKind;
  /** 会话创建时绑定的项目。运行中切换项目不能改变这个值。 */
  projectName: string;
  agentId: string;
  contextKey: string;
  expanded: boolean;
  history: AnyRecord[];
  loading: boolean;
  sending: boolean;
  toolCalling: boolean;
  toolName: string;
  toolProgressText: string;
  lastError: string;
  abortController: AbortController | null;
  abortRequested: boolean;
  historyRequestSeq: number;
  streamEpoch: number;
  localMessageSeq: number;
  toolStateStartedAt: number;
  toolClearTimer: ReturnType<typeof setTimeout> | null;
  /** 后台任务状态：null 表示无任务 */
  backgroundTaskStatus: 'running' | 'completed' | 'cancelled' | 'error' | null;
  /** 当前重试次数（null 表示未在重试） */
  retryAttempt: number | null;
  /** 重试来源：模型上游重试或前端传输重连 */
  retryMode: RetryMode | null;
  /** 最大重试次数 */
  retryMaxRetries: number;
  /** 最近一次重试的错误摘要 */
  retryErrorSummary: string;
  /** 多附件场景的真相源；对所有附件维持一个滑动窗口。 */
  attachments: ChatImportedContext[];
  /**
   * 兼容字段：永远 = ``attachments[0] ?? null``。
   * 老代码 / 老测试仍可读 ``session.importedContext``，但写入应走
   * ``setSessionAttachments`` / ``addSessionAttachment`` 等多附件 actions。
   */
  importedContext: ChatImportedContext | null;
  /** 当前聊天上下文累计的真实 LLM token 总数（按 task_id 去重） */
  contextTokenCount: number | null;
  /** 当前聊天上下文的真实输入/输出 token（跨轮汇总所有 Agent/请求） */
  contextTokenUsage: TokenUsageStats | null;
  /** 每个后端聊天任务的最新累计快照，用于实时覆盖与历史回放去重。 */
  tokenUsageByTask: Record<string, TokenUsageStats>;
  /** 最近一次实际塞入当前 Agent LLM 窗口的请求 token 统计 */
  contextWindowStats: ContextWindowStats | null;
};

type ChatStoreState = {
  sessions: Record<number, ChatSession>;
  _nextId: number;
  _contextProvider: (() => string | { text?: unknown; meta?: unknown } | null | undefined) | null;
  /** 当前界面展示的项目，用于选择对应的主会话。 */
  activeProjectName: string;
  primaryAgentId: string;
  primaryContextKey: string;
  primaryExpanded: boolean;
  primarySessionBindings: Record<string, number>;
  /** 按项目隔离后台任务检查，避免快速切换项目时互相跳过。 */
  _bgChecksInProgress: Record<string, boolean>;
};

type PanelToolTaskEntry = {
  count: number;
  task: ReturnType<typeof createStreamingTask>;
  refreshEvents: string[];
};

type ToolLoadingStatsTask = {
  push?: (text: string, title?: string, options?: Record<string, unknown>) => void;
  dispose?: () => void;
};

/**
 * 主会话 ID，永远存在，对应悬浮窗口 / 桌面全屏聊天页面。
 * 额外窗口（ExtraChatWindow）使用 ID >= 1 的会话。
 */
const PRIMARY_SESSION_ID = 0;

function _getPrimaryScopeKey(projectName = '', agentId = 'agent_director', contextKey = 'global') {
  return `${projectName || ''}::${agentId || 'agent_director'}::${(contextKey || 'global').toString()}`;
}

/**
 * 创建一个空会话对象
 */
function _createSession(id: number, agentId = 'agent_director', kind: ChatSessionKind = id === PRIMARY_SESSION_ID ? 'primary' : 'extra', projectName = ''): ChatSession {
  return {
    id,
    kind,
    projectName,
    agentId,
    contextKey: 'global',
    expanded: kind === 'extra',
    history: [],
    loading: false,
    sending: false,
    toolCalling: false,
    toolName: '',
    toolProgressText: '',
    lastError: '',
    abortController: null,
    abortRequested: false,
    historyRequestSeq: 0,
    streamEpoch: 0,
    localMessageSeq: 0,
    toolStateStartedAt: 0,
    toolClearTimer: null,
    backgroundTaskStatus: null,
    retryAttempt: null,
    retryMode: null,
    retryMaxRetries: 3,
    retryErrorSummary: '',
    attachments: [],
    importedContext: null,
    contextTokenCount: null,
    contextTokenUsage: null,
    tokenUsageByTask: {},
    contextWindowStats: null,
  };
}

function _nextLocalMessageId(session: ChatSession, role = 'msg') {
  session.localMessageSeq = (session.localMessageSeq || 0) + 1;
  return `local:${session.id}:${role}:${session.localMessageSeq}`;
}

function _replaceHistoryMessageByClientId(history: AnyRecord[] = [], clientId: string | number | null | undefined, nextMessage: AnyRecord): AnyRecord[] {
  if (!clientId) return Array.isArray(history) ? [...history] : [];
  const list = Array.isArray(history) ? [...history] : [];
  const index = list.findIndex(item => item?.clientId === clientId);
  if (index >= 0) {
    list[index] = nextMessage;
    return list;
  }
  list.push(nextMessage);
  return list;
}

function _upsertHistoryMessage(history: AnyRecord[] = [], nextMessage: AnyRecord): AnyRecord[] {
  const list = Array.isArray(history) ? [...history] : [];
  const id = nextMessage?.id;
  const clientId = nextMessage?.clientId;
  let index = -1;
  if (id != null && String(id).trim() !== '') {
    index = list.findIndex(item => item?.id != null && String(item.id) === String(id));
  }
  if (index < 0 && clientId != null && String(clientId).trim() !== '') {
    index = list.findIndex(item => item?.clientId != null && String(item.clientId) === String(clientId));
  }
  if (index >= 0) {
    list[index] = nextMessage;
  } else {
    list.push(nextMessage);
  }
  return list;
}

function _isAbortError(error: unknown) {
  if (!error) return false;
  if (error instanceof Error && error.name === 'AbortError') return true;
  const errorRecord = error && typeof error === 'object' ? error as { name?: unknown; message?: unknown } : null;
  if (String(errorRecord?.name || '') === 'AbortError') return true;
  return /aborted|aborterror|canceled|cancelled/i.test(String(errorRecord?.message || error));
}

function _defaultBackgroundTaskError() {
  return i18n.global.t('components.chatMessageList.backgroundTaskError');
}

function _getErrorMessage(error: unknown, fallback = '') {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error.trim()) return error;
  return fallback;
}

function _isLikelyStreamTransportError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || '');
  const name = error instanceof Error ? error.name : '';
  return /network|failed to fetch|fetch failed|load failed|terminated|connection|stream|body|socket/i.test(`${name} ${message}`);
}

function _getAssistantStreamSeq(message: AnyRecord | null | undefined) {
  const raw = message?.streamSeq ?? message?.stream_seq ?? message?.metadata?.stream_seq ?? message?.metadata?.streamSeq;
  const seq = Number(raw ?? 0);
  return Number.isFinite(seq) && seq > 0 ? seq : 0;
}

function _delay(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function _localizedStreamError(evt: AnyRecord) {
  const code = String(evt?.code || '').trim();
  if (code === 'context_window_incompatible') {
    return i18n.global.t('components.chatPanel.contextWindowIncompatible');
  }
  if (code === 'context_compaction_failed') {
    return i18n.global.t('components.chatPanel.contextCompactionRuntimeFailed');
  }
  return pickStreamEventText(evt, ['message', 'data', 'text']);
}

const CHAT_TRANSPORT_RECONNECT_BASE_DELAY_MS = 800;
const CHAT_TRANSPORT_RECONNECT_MAX_DELAY_MS = 5000;
const CHAT_CANCEL_STATUS_POLL_DELAYS_MS = [250, 500, 750, 1000, 1500, 2000, 3000, 5000];

function _clearRetryState(session: ChatSession) {
  session.retryAttempt = null;
  session.retryMode = null;
  session.retryMaxRetries = 3;
  session.retryErrorSummary = '';
}

function _setTransportReconnectState(session: ChatSession, attempt: number) {
  session.retryAttempt = Math.max(1, Number(attempt || 1));
  session.retryMode = 'transport';
  session.retryMaxRetries = 0;
  session.retryErrorSummary = i18n.global.t('components.chatMessageList.reconnectWaiting');
}

function _getTransportReconnectDelay(attempt: number) {
  const nextAttempt = Math.max(1, Number(attempt || 1));
  return Math.min(
    CHAT_TRANSPORT_RECONNECT_MAX_DELAY_MS,
    CHAT_TRANSPORT_RECONNECT_BASE_DELAY_MS * Math.max(1, nextAttempt),
  );
}

function _getRecoverySeq(message: AnyRecord | null | undefined, streamState: AnyRecord | null | undefined = null, fallback = 0) {
  const stateSeq = Number(streamState?.lastSeq ?? 0) || 0;
  return Math.max(Number(fallback || 0), _getAssistantStreamSeq(message), stateSeq);
}

// ==================== Store 定义 ====================

export const useChatStore = defineStore('chat', {
  state: (): ChatStoreState => ({
    /** @type {Object<number, ChatSession>} 所有活跃会话（ID 0 = 主会话） */
    sessions: { [PRIMARY_SESSION_ID]: _createSession(PRIMARY_SESSION_ID, 'agent_director', 'primary') },
    /** 自增 ID（从 1 开始，0 已被主会话占用） */
    _nextId: 1,
    /** 全局上下文提供器 */
    _contextProvider: null,
    activeProjectName: '',
    primaryAgentId: 'agent_director',
    primaryContextKey: 'global',
    primaryExpanded: false,
    primarySessionBindings: {
      [_getPrimaryScopeKey('', 'agent_director', 'global')]: PRIMARY_SESSION_ID,
    },
    _bgChecksInProgress: {},
  }),

  getters: {
    /** 主会话（悬浮窗口 / 桌面全屏使用） */
    primarySession: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId] || state.sessions[PRIMARY_SESSION_ID];
    },

    // ---------- 向后兼容 getter（代理到主会话，消费者无需改动） ----------
    currentAgentId: (state: ChatStoreState) => state.primaryAgentId || 'agent_director',
    contextKey: (state: ChatStoreState) => state.primaryContextKey || 'global',
    expanded: (state: ChatStoreState) => state.primaryExpanded || false,
    history: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.history || [];
    },
    loading: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.loading || false;
    },
    sending: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.sending || false;
    },
    toolCalling: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.toolCalling || false;
    },
    toolName: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.toolName || '';
    },
    toolProgressText: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.toolProgressText || '';
    },
    lastError: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.lastError || '';
    },
    retryAttempt: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.retryAttempt ?? null;
    },
    retryMode: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.retryMode ?? null;
    },
    retryMaxRetries: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.retryMaxRetries || 3;
    },
    retryErrorSummary: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.retryErrorSummary || '';
    },
    contextTokenCount: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.contextTokenCount ?? null;
    },
    contextTokenUsage: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.contextTokenUsage ?? null;
    },
    contextWindowStats: (state: ChatStoreState) => {
      const sessionId = state.primarySessionBindings[_getPrimaryScopeKey(state.activeProjectName, state.primaryAgentId, state.primaryContextKey)];
      return state.sessions[sessionId]?.contextWindowStats ?? null;
    },

    // ---------- 多窗口 getter ----------
    /** 所有额外会话（不含主会话） */
    sessionList: (state: ChatStoreState) => Object.values(state.sessions).filter(
      (s) => s.kind === 'extra' && s.projectName === state.activeProjectName,
    ),
    /** 已被占用的 agent ID 集合 */
    occupiedAgentIds: (state: ChatStoreState) => new Set([
      state.primaryAgentId || 'agent_director',
      ...Object.values(state.sessions)
        .filter((s) => s.kind === 'extra' && s.projectName === state.activeProjectName)
        .map((s) => s.agentId),
    ]),
    /** 所有仍在生成或等待后台任务终态的 Agent，用于聊天入口展示跨会话运行状态。 */
    runningAgentIds: (state: ChatStoreState) => new Set(
      Object.values(state.sessions)
        .filter((session) => session.projectName === state.activeProjectName)
        .filter((session) => session.sending || session.backgroundTaskStatus === 'running')
        .map((session) => session.agentId)
        .filter(Boolean),
    ),
  },

  actions: {
    // ==================== 通用会话管理 ====================

    _invalidateSessionStream(sessionId) {
      const session = this.sessions[sessionId];
      if (!session) return;
      // 关闭窗口、切换 Agent 或上下文只代表前端停止观察，不能隐式取消后端任务。
      // 后台任务的生命周期只由显式“停止”操作或后端终态控制。
      session.streamEpoch = (session.streamEpoch || 0) + 1;
      if (session.toolClearTimer) {
        clearTimeout(session.toolClearTimer);
        session.toolClearTimer = null;
      }
      if (session.abortController) {
        session.abortRequested = true;
        try {
          session.abortController.abort('session_invalidated');
        } catch {}
      }
      session.abortController = null;
      session.sending = false;
      session.backgroundTaskStatus = null;
      session.toolCalling = false;
      session.toolName = '';
      session.toolProgressText = '';
      session.toolStateStartedAt = 0;
      _clearRetryState(session);
    },

    /** 注册全局上下文提供器 */
    registerContextProvider(fn: (() => string | { text?: unknown; meta?: unknown } | null | undefined) | null) {
      this._contextProvider = fn;
    },

    /**
     * 多附件真相源写入：替换 session.attachments 的全部内容并同步老 importedContext 镜像。
     *
     * 所有写入路径都最终落到这里，避免 attachments / importedContext 不一致。
     */
    setSessionAttachments(sessionId: number, payloads: ChatImportedContext[] | null) {
      const session = this.sessions[sessionId];
      if (!session) return;
      const list = Array.isArray(payloads) ? payloads : [];
      const next = list
        .filter((item) => item && String(item.attachmentId || '').trim() && String(item.filename || '').trim())
        .map((payload) => ({
          ...payload,
          warnings: Array.isArray(payload.warnings) ? payload.warnings.map((item) => ({ ...item })) : [],
        }));
      session.attachments = next;
      session.importedContext = next[0] || null;
    },

    /** 追加单个附件到 session.attachments；按 attachmentId 去重，重复的覆盖 meta。 */
    addSessionAttachment(sessionId: number, payload: ChatImportedContext) {
      const session = this.sessions[sessionId];
      if (!session || !payload) return;
      const attachmentId = String(payload.attachmentId || '').trim();
      const filename = String(payload.filename || '').trim();
      if (!attachmentId || !filename) return;
      const cloned: ChatImportedContext = {
        ...payload,
        warnings: Array.isArray(payload.warnings) ? payload.warnings.map((item) => ({ ...item })) : [],
      };
      const existing = session.attachments || [];
      const idx = existing.findIndex((item) => item.attachmentId === attachmentId);
      const next = idx >= 0
        ? existing.map((item, i) => (i === idx ? cloned : item))
        : [...existing, cloned];
      session.attachments = next;
      session.importedContext = next[0] || null;
    },

    /** 按 attachmentId 精确移除一个附件（仅本地 session 状态；不调后端）。 */
    removeSessionAttachmentById(sessionId: number, attachmentId: string) {
      const session = this.sessions[sessionId];
      if (!session) return;
      const trimmed = String(attachmentId || '').trim();
      if (!trimmed) return;
      const existing = session.attachments || [];
      const next = existing.filter((item) => item.attachmentId !== trimmed);
      session.attachments = next;
      session.importedContext = next[0] || null;
    },

    // ──────────────────────────────────────────────────────
    // 兼容糖：老调用方仍可单数操作；内部委托到多附件 actions。
    // ──────────────────────────────────────────────────────
    setSessionImportedContext(sessionId: number, payload: ChatImportedContext | null) {
      this.setSessionAttachments(sessionId, payload ? [payload] : []);
    },

    clearSessionImportedContext(sessionId: number) {
      this.setSessionAttachments(sessionId, []);
    },

    async removeSessionImportedContext(sessionId: number, attachmentId?: string | null) {
      const session = this.sessions[sessionId];
      if (!session) return;

      // 1. 找到要删的附件：优先用传入的 attachmentId，否则取第一个。
      const targetId = String(attachmentId || '').trim() || (session.attachments?.[0]?.attachmentId || '');
      if (!targetId) return;
      const reference = (session.attachments || []).find((item) => item.attachmentId === targetId)
        || (session.importedContext && session.importedContext.attachmentId === targetId
          ? session.importedContext
          : null);

      // 2. 本地立刻把它从 attachments 列表里去掉。
      this.removeSessionAttachmentById(sessionId, targetId);

      if (!reference?.filename) return;

      // 3. 找后端持久化的锚点消息以便落库标记 deleted。
      const persistedMessage = (session.history || []).find((message) => {
        if (message?.id == null || String(message.id).trim() === '') return false;
        const list = extractImportedFilesMeta(message?.metadata || null);
        if (list.length > 0) {
          return list.some((entry) => entry.attachmentId === targetId);
        }
        return false;
      });

      if (persistedMessage?.id != null) {
        await this.removeSessionAttachment(sessionId, persistedMessage.id, targetId);
        return;
      }

      // 没有持久化的锚点消息（用户上传后立刻删除）→ 仅本地标记历史消息 deleted。
      markSessionImportedFileDeleted(session, reference);
    },

    /** 获取指定会话（不存在时返回 null） */
    getSession(sessionId: number): ChatSession | null {
      return this.sessions[sessionId] || null;
    },

    _resolveSessionProjectName(session: ChatSession): string {
      const boundProjectName = String(session.projectName || '').trim();
      if (boundProjectName) return boundProjectName;
      const projectName = String(this.activeProjectName || useProjectStore().currentProject || '').trim();
      if (projectName) {
        session.projectName = projectName;
        this.activeProjectName = projectName;
      }
      return projectName;
    },

    _getPrimarySessionId(agentId?: string | null, contextKey?: string | null, projectName?: string | null) {
      const normalizedAgentId = (agentId ?? this.primaryAgentId) || 'agent_director';
      const normalizedContextKey = ((contextKey ?? this.primaryContextKey) || 'global').toString();
      const normalizedProjectName = String(projectName ?? this.activeProjectName ?? '').trim();
      const scopeKey = _getPrimaryScopeKey(normalizedProjectName, normalizedAgentId, normalizedContextKey);
      const existingId = this.primarySessionBindings?.[scopeKey];
      if (existingId != null && this.sessions[existingId]) {
        return existingId;
      }
      const sessionId = this._nextId++;
      const session = _createSession(sessionId, normalizedAgentId, 'primary', normalizedProjectName);
      session.contextKey = normalizedContextKey;
      this.sessions[sessionId] = session;
      this.primarySessionBindings = {
        ...(this.primarySessionBindings || {}),
        [scopeKey]: sessionId,
      };
      return sessionId;
    },

    _getPrimarySession(agentId?: string | null, contextKey?: string | null, projectName?: string | null): ChatSession | null {
      const sessionId = this._getPrimarySessionId(agentId, contextKey, projectName);
      return this.sessions[sessionId] || null;
    },

    // ==================== 主会话便捷方法（向后兼容） ====================

    setExpanded(v: boolean) {
      this.primaryExpanded = !!v;
    },

    toggleExpanded() {
      this.primaryExpanded = !this.primaryExpanded;
    },

    setAgent(agentId: string | null | undefined) {
      const nextAgentId = agentId || 'agent_director';
      this._getPrimarySession(nextAgentId, this.primaryContextKey);
      this.primaryAgentId = nextAgentId;
    },

    setContextKey(key: string | null | undefined) {
      const nextContextKey = (key || 'global').toString();
      this._getPrimarySession(this.primaryAgentId, nextContextKey);
      this.primaryContextKey = nextContextKey;
    },

    async refreshHistory(limit = 50) {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.refreshSessionHistory(session.id, limit);
    },

    async send(message, targets = undefined) {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.sendSessionMessage(session.id, message, targets);
    },

    async cancel() {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.cancelSessionRequest(session.id);
    },

    async clear() {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.clearSession(session.id);
    },

    async compactContext(targetTokens?: number) {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.compactSessionContext(session.id, targetTokens);
    },

    async deleteMessage(messageId) {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.deleteSessionMessage(session.id, messageId);
    },

    async editMessage(messageId, newContent) {
      const session = this._getPrimarySession();
      if (!session) return;
      await this.editSessionMessage(session.id, messageId, newContent);
    },

    // ==================== 多窗口管理 ====================

    /** 检查 agent 是否已被占用 */
    isAgentOccupied(agentId) {
      return this.currentAgentId === agentId
        || Object.values(this.sessions as Record<number, AnyRecord>).some(
          s => s.kind === 'extra' && s.projectName === this.activeProjectName && s.agentId === agentId,
        );
    },

    /** 获取未被占用的 agent 列表 */
    getAvailableAgents(allAgents, excludeSessionId = null) {
      const occupied = new Set([
        this.currentAgentId,
        ...Object.values(this.sessions as Record<number, AnyRecord>)
          .filter(s => s.kind === 'extra' && s.projectName === this.activeProjectName && s.id !== excludeSessionId)
          .map(s => s.agentId),
      ]);
      return allAgents.filter(a => !occupied.has(a.value || a.key));
    },

    /**
     * 创建新的额外会话
     * @param {string} agentId - 初始 agent ID
     * @returns {number} 新会话 ID
     */
    createSession(agentId = 'agent_director') {
      if (this.isAgentOccupied(agentId)) {
        throw new Error(`Agent "${agentId}" 已在另一个窗口中使用`);
      }
      const id = this._nextId++;
      this.sessions[id] = _createSession(id, agentId, 'extra', this.activeProjectName);
      return id;
    },

    /** 关闭并移除额外会话（不允许移除主会话） */
    removeSession(sessionId) {
      if (sessionId === PRIMARY_SESSION_ID) return;
      this._invalidateSessionStream(sessionId);
      this.clearSessionImportedContext(sessionId);
      delete this.sessions[sessionId];
    },

    /** 切换可见项目；其他项目的会话和运行中流继续在各自会话内维护。 */
    switchProject(projectName: string | null | undefined) {
      this.activeProjectName = String(projectName || '').trim();
      this._getPrimarySession(this.primaryAgentId, this.primaryContextKey, this.activeProjectName);
    },

    /**
     * 项目删除后释放该项目的本地会话，避免同名重建时复用旧 history。
     * 后端 DB 与内存任务由项目删除/创建接口清理，这里只清理前端内存；
     * 不触碰其他项目的会话与运行中流。
     */
    purgeProjectSessions(projectName: string | null | undefined) {
      const target = String(projectName || '').trim();
      if (!target) return;
      for (const id of Object.keys(this.sessions as Record<number, AnyRecord>).map(Number)) {
        const session = (this.sessions as Record<number, AnyRecord>)[id];
        if (!session || String(session.projectName || '').trim() !== target) continue;
        this._invalidateSessionStream(id);
        if (id === PRIMARY_SESSION_ID) {
          const fresh = _createSession(PRIMARY_SESSION_ID, 'agent_director', 'primary');
          this.sessions[id] = fresh;
          continue;
        }
        this.clearSessionImportedContext(id);
        delete (this.sessions as Record<number, AnyRecord>)[id];
      }
      const nextBindings: Record<string, number> = {};
      for (const [scopeKey, sessionId] of Object.entries(this.primarySessionBindings || {})) {
        if (scopeKey.split('::')[0] === target) continue;
        if (!(this.sessions as Record<number, AnyRecord>)[sessionId]) continue;
        nextBindings[scopeKey] = sessionId;
      }
      this.primarySessionBindings = nextBindings;
      const nextChecks = { ...(this._bgChecksInProgress || {}) };
      delete nextChecks[target];
      this._bgChecksInProgress = nextChecks;
    },

    /** 登出时才真正释放全部聊天会话。 */
    resetAllSessions() {
      for (const id of Object.keys(this.sessions as Record<number, AnyRecord>).map(Number)) {
        this._invalidateSessionStream(id);
      }
      this.sessions = {
        [PRIMARY_SESSION_ID]: _createSession(PRIMARY_SESSION_ID, 'agent_director', 'primary'),
      };
      this._nextId = 1;
      this.activeProjectName = '';
      this.primaryAgentId = 'agent_director';
      this.primaryContextKey = 'global';
      this.primarySessionBindings = {
        [_getPrimaryScopeKey('', 'agent_director', 'global')]: PRIMARY_SESSION_ID,
      };
      this._bgChecksInProgress = {};
    },

    /** 切换会话的 agent（强制互斥） */
    setSessionAgent(sessionId, agentId) {
      const session = this.sessions[sessionId];
      if (!session) return false;

      const occupiedBy = Object.values(this.sessions as Record<number, AnyRecord>).find(
        s => s.kind === 'extra' && s.id !== sessionId && s.projectName === session.projectName && s.agentId === agentId
      );
      if (occupiedBy || this.currentAgentId === agentId) {
        bus.emit('toast', { type: 'warning', message: '该 Agent 已在另一个窗口中使用' });
        return false;
      }

      this._invalidateSessionStream(sessionId);
      session.agentId = agentId || 'agent_director';
      session.history = [];
      session.lastError = '';
      session.loading = false;
      session.importedContext = null;
      session.contextTokenCount = null;
      session.contextTokenUsage = null;
      session.tokenUsageByTask = {};
      session.contextWindowStats = null;
      session.historyRequestSeq += 1;
      return true;
    },

    /** 设置会话的 contextKey */
    setSessionContextKey(sessionId, key) {
      const session = this.sessions[sessionId];
      if (session) {
        this._invalidateSessionStream(sessionId);
        session.contextKey = (key || 'global').toString();
        session.history = [];
        session.lastError = '';
        session.loading = false;
        session.importedContext = null;
        session.contextTokenCount = null;
        session.contextTokenUsage = null;
        session.tokenUsageByTask = {};
        session.contextWindowStats = null;
        session.historyRequestSeq += 1;
      }
    },

    /** 展开/收起会话面板 */
    setSessionExpanded(sessionId, v) {
      const session = this.sessions[sessionId];
      if (session) {
        session.expanded = !!v;
      }
    },

    _setSessionAbortController(sessionId: number, controller: AbortController | null = null) {
      const session = this.sessions[sessionId];
      if (!session) return;
      session.abortController = controller;
      session.abortRequested = false;
    },

    _finalizeSessionAbort(sessionId: number, controller: AbortController | null = null) {
      const session = this.sessions[sessionId];
      if (!session) return;
      if (!controller || session.abortController === controller) {
        session.abortController = null;
      }
      session.abortRequested = false;
    },

    async cancelSessionRequest(sessionId) {
      const session = this.sessions[sessionId];
      if (!session) return;

      if (!session.sending && session.backgroundTaskStatus !== 'running') return;
      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;

      const cancelStreamEpoch = session.streamEpoch;
      const cancelAgentId = session.agentId;
      const cancelContextKey = session.contextKey;
      // 先中止当前浏览器读取并解除思考锁；网络请求只负责通知后端，不应阻塞按钮反馈。
      session.abortRequested = true;
      if (session.abortController && !session.abortController.signal.aborted) {
        try {
          session.abortController.abort('user_cancelled');
        } catch {}
      }
      session.sending = false;
      session.backgroundTaskStatus = null;
      session.toolCalling = false;
      session.toolName = '';
      session.toolProgressText = '';
      _clearRetryState(session);

      let result;
      try {
        result = await cancelChatTask(projectName, cancelAgentId, cancelContextKey);
      } catch (error) {
        const state = await this._getChatTaskAuthorityState(cancelAgentId, cancelContextKey, projectName);
        this._applyChatTaskAuthorityState(session, state);
        bus.emit('toast', {
          type: 'error',
          message: _getErrorMessage(error, i18n.global.t('components.chatMessageList.cancelFailed')),
        });
        return;
      }
      if (!result.success) {
        const state = await this._getChatTaskAuthorityState(cancelAgentId, cancelContextKey, projectName);
        this._applyChatTaskAuthorityState(session, state);
        if (state === 'terminal' || state === 'missing') {
          await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
        }
        return;
      }

      // 前端立即退出“思考中”；后台只负责在服务端终态落盘后刷新权威历史。
      void this._waitForChatTaskCancellation(
        session,
        cancelAgentId,
        cancelContextKey,
        projectName,
        cancelStreamEpoch,
      ).catch(error => {
        console.warn('等待聊天任务取消终态失败', error);
      });
    },

    // ==================== 统一的会话操作 ====================

    /** 刷新会话历史 */
    async refreshSessionHistory(sessionId, limit = 80, options: AnyRecord = {}) {
      const session = this.sessions[sessionId];
      if (!session) return;

      const { silent = false, preserveLocalTail = null, authoritative = false } = options || {};
      const agentIdAtStart = session.agentId;
      const contextKeyAtStart = session.contextKey;
      const requestSeq = (session.historyRequestSeq || 0) + 1;
      session.historyRequestSeq = requestSeq;

      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;

      if (!silent) {
        session.loading = true;
      }
      session.lastError = '';
      try {
        const rawHistory = await getChatHistory(projectName, agentIdAtStart, contextKeyAtStart, limit);
        if (session.projectName !== projectName || session.agentId !== agentIdAtStart || session.contextKey !== contextKeyAtStart || session.historyRequestSeq !== requestSeq) {
          return;
        }
        // 历史刷新、后台恢复与本地 optimistic 消息统一走同一套 reconciliation 规则。
        const fallbackAssistant = authoritative ? null : preserveLocalTail;
        const localHistory = authoritative ? [] : session.history;
        session.history = reconcileSessionHistory(rawHistory || [], fallbackAssistant, localHistory);
        session.lastError = getLatestHistoryTerminalError(
          session.history,
          _defaultBackgroundTaskError(),
        );
        restoreHistoryTokenStats(session, session.history, {
          preserveLive: session.sending || session.backgroundTaskStatus === 'running',
        });
        session.contextWindowStats = latestHistoryContextWindowStats(session.history);
        // 历史中存在附件且 session.attachments 为空时，恢复完整列表（多附件场景下也能正确还原）。
        if ((session.attachments?.length || 0) === 0) {
          const restored = findLatestImportedContexts(session.history);
          if (restored.length > 0) {
            session.attachments = restored;
            session.importedContext = restored[0] || null;
          }
        }
      } catch (e: unknown) {
        if (session.projectName !== projectName || session.agentId !== agentIdAtStart || session.contextKey !== contextKeyAtStart || session.historyRequestSeq !== requestSeq) {
          return;
        }
        session.lastError = _getErrorMessage(e, '加载失败');
      } finally {
        if (!silent && session.historyRequestSeq === requestSeq) {
          session.loading = false;
        }
      }
    },

    /** 发送消息（统一入口，所有窗口共用） */
    async sendSessionMessage(sessionId, message, targets = undefined, skipOptimisticAdd = false, contextOverride: ResolvedMessageContext | null = null) {
      const session = this.sessions[sessionId];
      if (!session) return;
      if (session.sending) return;

      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) throw new Error('未选择项目');
      const text = (message || '').trim();
      if (!text) return;

      // 本地状态可能因刷新、窗口切换或旧版客户端取消竞态而落后。
      // 提交新任务前先向后端确认房间所有权，避免乐观消息写入后才收到 409。
      // 查询本身也是提交临界区，必须先占本地锁，防止双击并发通过预检。
      session.sending = true;
      let authoritativeStatus: AnyRecord;
      try {
        authoritativeStatus = await getChatTaskStatus(projectName, session.agentId, session.contextKey) as AnyRecord;
      } catch (error) {
        session.sending = false;
        throw error;
      }
      if (authoritativeStatus?.hasTask && authoritativeStatus.status === 'running') {
        session.backgroundTaskStatus = 'running';
        session.lastError = '';
        applyPersistedTokenStats(session, authoritativeStatus);
        if (!session.abortController || session.abortController.signal.aborted) {
          void this._reconnectTaskStream(session, session.agentId, session.contextKey).catch(error => {
            console.warn('恢复已有聊天任务观察流失败', error);
          });
        }
        bus.emit('toast', {
          type: 'info',
          message: i18n.global.t('components.chatMessageList.reconnectWaiting'),
        });
        return;
      }

      const agentIdAtStart = session.agentId;
      const contextKeyAtStart = session.contextKey;
      const streamEpoch = (session.streamEpoch || 0) + 1;
      session.streamEpoch = streamEpoch;

      const abortController = new AbortController();
      this._setSessionAbortController(sessionId, abortController);

      if (session.toolClearTimer) {
        clearTimeout(session.toolClearTimer);
        session.toolClearTimer = null;
      }
      session.sending = true;
      session.toolCalling = false;
      session.toolName = '';
      session.toolProgressText = '';
      session.lastError = '';
      session.backgroundTaskStatus = 'running';
      _clearRetryState(session);
      let streamState: AnyRecord | null = null;
      try {
        const resolvedContext = contextOverride || (() => {
          const { activeContext, activeMeta } = resolveActiveContext(this._contextProvider, session.attachments);
          return {
            activeContext,
            activeMeta,
            messageMetadata: buildUserMessageMetadata(activeContext, activeMeta),
          };
        })();
        const { activeContext, activeMeta, messageMetadata } = resolvedContext;

        // 乐观添加用户消息（编辑重发时由调用方自行写入，跳过此步避免重复）
        const userClientId = _nextLocalMessageId(session, 'user');
        if (!skipOptimisticAdd) {
          session.history = (session.history || []).concat([
            {
              clientId: userClientId,
              role: 'user',
              content: text,
              timestamp: Math.floor(Date.now() / 1000),
              ...(messageMetadata ? { metadata: messageMetadata } : {}),
            }
          ]);
        }

        // AI 回复占位
        const assistantMsg = {
          clientId: _nextLocalMessageId(session, 'assistant'),
          role: 'assistant',
          content: '',
          reasoning: '',
          tool_traces: [],
          segments: [],
          timestamp: Math.floor(Date.now() / 1000),
        };
        let assistantMsgAdded = false;

        const reader = await sendChatMessageStream(projectName, agentIdAtStart, contextKeyAtStart, text, targets, activeContext, activeMeta, abortController.signal);

        // 统一流式处理
        streamState = {
          signal: abortController.signal,
          projectName,
          agentId: agentIdAtStart,
          contextKey: contextKeyAtStart,
          streamEpoch,
          userMessageClientId: userClientId,
          userMessageContent: text,
        };
        await this._consumeStream(session, assistantMsg, assistantMsgAdded, reader, sessionId, streamState);

        if (abortController.signal.aborted || session.abortRequested || session.streamEpoch !== streamEpoch) {
          return;
        }
        if (!streamState.receivedTaskDone) {
          const recovered = await this._recoverChatStreamObserver(session, agentIdAtStart, contextKeyAtStart, _getRecoverySeq(assistantMsg, streamState), streamEpoch);
          if (recovered) return;
        }

        if (!assistantMsg.content && agentIdAtStart === 'agent_lorebook') {
          if (!session.history.some(m => m?.clientId === assistantMsg.clientId)) {
            session.history = session.history.concat([assistantMsg]);
          }
          assistantMsg.content = '设定已更新。';
          session.history = _replaceHistoryMessageByClientId(session.history, assistantMsg.clientId, { ...assistantMsg });
        }

        // 从服务器同步持久化历史
        // 不在发送完成后立即用服务端历史覆盖本地会话。
        // 历史持久化存在短暂延迟，强制 refresh 会把当前轮或上一轮本地消息顶乱。
        // 统一改为：发送流期间以本地会话为准，切换 agent/context 或手动刷新时再从服务端重载。
      } catch (e: unknown) {
        if (_isAbortError(e) || abortController.signal.aborted || session.abortRequested) {
          return;
        }
        if (_isLikelyStreamTransportError(e)) {
          const lastAssistant = (session.history || []).slice().reverse().find(m => m?.role === 'assistant');
          const recovered = await this._resumeChatTaskAfterTransportLoss(
            session,
            agentIdAtStart,
            contextKeyAtStart,
            _getRecoverySeq(lastAssistant || null, streamState),
            streamEpoch,
            { initialError: e },
          );
          if (recovered) return;
        }
        const errorMsg = _getErrorMessage(e, '发送失败');
        session.lastError = errorMsg;
        bus.emit('toast', { type: 'error', message: errorMsg });
        throw e;
      } finally {
        if (session.streamEpoch === streamEpoch) {
          if (!session.toolClearTimer) {
            session.toolCalling = false;
            session.toolName = '';
            session.toolProgressText = '';
          }
          const canKeepRunning = !session.abortRequested && !abortController.signal.aborted;
          const taskState: ChatTaskAuthorityState = streamState?.receivedTaskDone
            ? 'terminal'
            : canKeepRunning
              ? await this._getChatTaskAuthorityState(agentIdAtStart, contextKeyAtStart, projectName)
              : 'terminal';
          this._applyChatTaskAuthorityState(session, taskState);
          this._finalizeSessionAbort(sessionId, abortController);
          if (taskState === 'unknown' && session.streamEpoch === streamEpoch) {
            void this._resumeChatTaskAfterTransportLoss(
              session,
              agentIdAtStart,
              contextKeyAtStart,
              _getRecoverySeq(null, streamState),
              streamEpoch,
            ).catch(error => console.warn('恢复聊天任务状态失败', error));
          }
        }
      }
    },

    /**
     * 检查当前项目是否有后台聊天任务，并恢复状态。
     * - running → 先刷新历史，再重连 SSE 流消费后续事件
     * - completed/cancelled/error → 刷新历史获取结果，清除标记
     * 返回 true 表示有 running 任务（供前端自动展开聊天窗口）。
     */
    _getOrCreateSessionForTask(agentId: string, contextKey = 'global', projectName?: string): ChatSession | null {
      const normalizedAgentId = agentId || 'agent_director';
      const normalizedContextKey = (contextKey || 'global').toString();
      const normalizedProjectName = String(projectName ?? this.activeProjectName ?? '').trim();
      const existing = (Object.values(this.sessions) as ChatSession[]).find(
        session => session.projectName === normalizedProjectName && session.agentId === normalizedAgentId && session.contextKey === normalizedContextKey,
      );
      if (existing) return existing;
      const sessionId = this._getPrimarySessionId(normalizedAgentId, normalizedContextKey, normalizedProjectName);
      return this.sessions[sessionId] || null;
    },

    async _recoverChatStreamObserver(session: ChatSession, agentId: string, contextKey: string, afterSeq = 0, previousEpoch: number | null = null): Promise<boolean> {
      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return false;
      if (previousEpoch != null && session.streamEpoch !== previousEpoch) return true;

      let status: AnyRecord | null = null;
      try {
        status = await getChatTaskStatus(projectName, agentId, contextKey) as AnyRecord;
      } catch (e: unknown) {
        if (_isLikelyStreamTransportError(e)) {
          return this._resumeChatTaskAfterTransportLoss(session, agentId, contextKey, afterSeq, previousEpoch, {
            initialError: e,
          });
        }
        console.warn('查询聊天任务状态失败，无法自动重连', e);
        return false;
      }

      if (!status?.hasTask) return false;

      if (status.status === 'running') {
        session.backgroundTaskStatus = 'running';
        session.sending = true;
        session.lastError = '';
        applyPersistedTokenStats(session, status);
        await this._reconnectTaskStream(session, agentId, contextKey, Number(afterSeq || 0));
        return true;
      }

      if (status.status === 'completed' || status.status === 'cancelled' || status.status === 'error') {
        session.backgroundTaskStatus = null;
        session.sending = false;
        applyPersistedTokenStats(session, status);
        if (status.status === 'error') {
          session.lastError = String(status.error || _defaultBackgroundTaskError());
        }
        await this.refreshSessionHistory(session.id, 80, { silent: true, authoritative: true });
        return true;
      }

      return false;
    },

    async _resumeChatTaskAfterTransportLoss(
      session: ChatSession,
      agentId: string,
      contextKey: string,
      afterSeq = 0,
      previousEpoch: number | null = null,
      options: AnyRecord = {},
    ): Promise<boolean> {
      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return false;

      const sessionId = session.id;
      const normalizedContextKey = contextKey || 'global';
      let attempt = Math.max(0, Number(options.startAttempt || 0));
      const maxTransportRetries = Number(options.maxTransportRetries ?? 3);
      const shouldStop = () => (
        session.abortRequested
        || (previousEpoch != null && session.streamEpoch !== previousEpoch)
        || session.projectName !== projectName
        || session.agentId !== agentId
        || session.contextKey !== normalizedContextKey
      );

      while (true) {
        if (shouldStop()) return true;

        attempt += 1;
        session.backgroundTaskStatus = 'running';
        session.sending = true;
        session.lastError = '';
        _setTransportReconnectState(session, attempt);

        let status: AnyRecord | null = null;
        try {
          status = await getChatTaskStatus(projectName, agentId, normalizedContextKey) as AnyRecord;
        } catch (e: unknown) {
          if (_isAbortError(e) || shouldStop()) return true;
          if (!_isLikelyStreamTransportError(e)) {
            return false;
          }
          await _delay(_getTransportReconnectDelay(attempt));
          continue;
        }

        if (shouldStop()) return true;

        if (!status?.hasTask) {
          session.backgroundTaskStatus = null;
          session.sending = false;
          _clearRetryState(session);
          await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
          return true;
        }

        applyPersistedTokenStats(session, status);

        if (status.status === 'running') {
          await this._reconnectTaskStream(session, agentId, normalizedContextKey, Number(afterSeq || 0), {
            retryIndex: 0,
            maxTransportRetries,
          });
          return true;
        }

        if (status.status === 'completed' || status.status === 'cancelled' || status.status === 'error') {
          session.backgroundTaskStatus = null;
          session.sending = false;
          if (status.status === 'error') {
            session.lastError = String(status.error || _defaultBackgroundTaskError());
          }
          _clearRetryState(session);
          await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
          return true;
        }

        await _delay(_getTransportReconnectDelay(attempt));
      }
    },

    async _getChatTaskAuthorityState(agentId: string, contextKey: string, projectName?: string): Promise<ChatTaskAuthorityState> {
      const targetProjectName = String(projectName || this.activeProjectName || useProjectStore().currentProject || '').trim();
      if (!targetProjectName) return 'missing';
      try {
        const status = await getChatTaskStatus(targetProjectName, agentId, contextKey) as AnyRecord;
        if (!status?.hasTask) return 'missing';
        if (status.status === 'running') return 'running';
        if (status.status === 'completed' || status.status === 'cancelled' || status.status === 'error') {
          return 'terminal';
        }
        return 'unknown';
      } catch {
        return 'unknown';
      }
    },

    async _cancelAndWaitForChatTaskReplacement(session: ChatSession, agentId: string, contextKey: string, projectName: string) {
      // 先使旧观察器失效，再通知服务端停止后台线程。
      if (session.abortController) {
        session.abortRequested = true;
        try {
          session.abortController.abort('message_replaced');
        } catch {}
      }

      let status = await getChatTaskStatus(projectName, agentId, contextKey) as AnyRecord;
      if (!status?.hasTask || status.status !== 'running') return status;
      await cancelChatTask(projectName, agentId, contextKey);

      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        await _delay(100);
        status = await getChatTaskStatus(projectName, agentId, contextKey) as AnyRecord;
        if (!status?.hasTask || status.status !== 'running') return status;
      }
      throw new Error('上一条聊天任务仍在退出，请稍后重试');
    },

    _applyChatTaskAuthorityState(session: ChatSession, state: ChatTaskAuthorityState): boolean {
      const keepLocked = state === 'running' || state === 'unknown';
      session.sending = keepLocked;
      session.backgroundTaskStatus = keepLocked ? 'running' : null;
      return keepLocked;
    },

    async _waitForChatTaskCancellation(
      session: ChatSession,
      agentId: string,
      contextKey: string,
      projectName: string,
      streamEpoch: number,
    ): Promise<void> {
      for (const delayMs of CHAT_CANCEL_STATUS_POLL_DELAYS_MS) {
        await _delay(delayMs);

        // 会话已切换或已经收到终态时，旧取消请求不得触碰当前状态。
        if (
          session.projectName !== projectName
          || session.agentId !== agentId
          || session.contextKey !== contextKey
          || session.streamEpoch !== streamEpoch
        ) {
          return;
        }

        let status: AnyRecord | null = null;
        try {
          status = await getChatTaskStatus(projectName, agentId, contextKey) as AnyRecord;
        } catch {
          // 查询失败不代表任务已结束，保留锁定并等待观察流或下一轮查询。
          continue;
        }

        if (
          session.projectName !== projectName
          || session.agentId !== agentId
          || session.contextKey !== contextKey
          || session.streamEpoch !== streamEpoch
        ) {
          return;
        }

        if (!status || typeof status !== 'object') continue;

        const terminal = status.hasTask === false
          || status.status === 'completed'
          || status.status === 'cancelled'
          || status.status === 'error';
        if (!terminal) continue;

        session.backgroundTaskStatus = null;
        session.sending = false;
        applyPersistedTokenStats(session, status);
        if (status.status === 'error') {
          session.lastError = String(status.error || _defaultBackgroundTaskError());
        }
        _clearRetryState(session);
        await this.refreshSessionHistory(session.id, 80, { silent: true, authoritative: true });
        return;
      }
    },

    async _isChatTaskStillRunning(agentId: string, contextKey: string, projectName?: string): Promise<boolean> {
      return (await this._getChatTaskAuthorityState(agentId, contextKey, projectName)) === 'running';
    },

    async checkBackgroundTasks(): Promise<boolean> {
      const projectStore = useProjectStore();
      const projectName = String(projectStore.currentProject || this.activeProjectName || '').trim();
      if (!projectName) return false;

      // 同一项目去重，不同项目允许并行检查与恢复。
      if (this._bgChecksInProgress[projectName]) return false;
      this._bgChecksInProgress = { ...this._bgChecksInProgress, [projectName]: true };

      try {
        const { tasks, count } = await getChatRecentTasks(projectName);
        if (count === 0) {
          // 没有未清理任务，清理残留状态
          for (const session of Object.values(this.sessions) as ChatSession[]) {
            if (session.projectName === projectName && session.backgroundTaskStatus === 'running') {
              session.backgroundTaskStatus = null;
              await this.refreshSessionHistory(session.id, 80, { silent: true, authoritative: true });
            }
          }
          return false;
        }

        let hasRunning = false;
        for (const task of tasks) {
          const agentId = task.agentId || '';
          const contextKey = task.contextKey || 'global';
          const session = this._getOrCreateSessionForTask(agentId, contextKey, projectName);
          if (!session) continue;

          const status = task.status || null;
          if (status === 'running') {
            session.backgroundTaskStatus = 'running';
            session.sending = true;
            _clearRetryState(session);
            applyPersistedTokenStats(session, task as AnyRecord);
            hasRunning = true;

            if (this.activeProjectName === projectName && (!this.primaryExpanded || !this.primaryAgentId || this.primaryAgentId === 'agent_director')) {
              this.primaryAgentId = agentId;
              this.primaryContextKey = contextKey;
            }

            await this.refreshSessionHistory(session.id, 80, { silent: true });

            if (!session.abortController || session.abortController.signal.aborted) {
              this._reconnectTaskStream(session, agentId, contextKey);
            }
          } else if (status === 'completed' || status === 'cancelled' || status === 'error') {
            session.backgroundTaskStatus = null;
            session.sending = false;
            applyPersistedTokenStats(session, task as AnyRecord);
            if (status === 'error') {
              session.lastError = String(task.error || _defaultBackgroundTaskError());
            }
            await this.refreshSessionHistory(session.id, 80, { silent: true, authoritative: true });
          }
        }
        return hasRunning;
      } catch (e: unknown) {
        console.warn('检查后台聊天任务失败', e);
        return false;
      } finally {
        const nextChecks = { ...this._bgChecksInProgress };
        delete nextChecks[projectName];
        this._bgChecksInProgress = nextChecks;
      }
    },

    /**
     * 重连到后台聊天任务的 SSE 流，消费后续 delta 事件。
     * 内部调用 reconnectChatTaskStream → 如果返回 NDJSON 流则走 _consumeStream；
     * 如果返回 JSON（任务已结束）则刷新历史获取结果。
     */
    async _reconnectTaskStream(session: ChatSession, agentId: string, contextKey: string, afterSeq = 0, options: AnyRecord = {}) {
      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;

      const sessionId = session.id;
      const streamEpoch = (session.streamEpoch || 0) + 1;
      session.streamEpoch = streamEpoch;

      const abortController = new AbortController();
      this._setSessionAbortController(sessionId, abortController);

      let assistantMsg: AnyRecord | null = null;
      let streamState: AnyRecord | null = null;
      const retryIndex = Number(options.retryIndex || 0);
      const maxTransportRetries = Number(options.maxTransportRetries ?? 3);

      try {
        const result = await reconnectChatTaskStream(projectName, agentId, contextKey, afterSeq, abortController.signal);

        // 返回的是 JSON 状态对象（任务已结束或不存在）
        if (result && typeof result === 'object' && 'hasTask' in result) {
          const status = (result as AnyRecord).status as string;
          if (status === 'completed' || status === 'cancelled' || status === 'error') {
            session.backgroundTaskStatus = null;
            session.sending = false;
            applyPersistedTokenStats(session, result as AnyRecord);
            if (status === 'error') {
              session.lastError = String((result as AnyRecord).error || _defaultBackgroundTaskError());
            }
            await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
          }
          return;
        }

        // 返回的是 ReadableStream reader（任务仍在运行）
        const reader = result as ReadableStreamDefaultReader<Uint8Array>;
        if (session.retryMode === 'transport') {
          _clearRetryState(session);
        }

        // 复用历史中已有的最后一条 assistant 消息，避免重复追加
        // ⚠️ 仅复用本地创建的消息（有 clientId）：DB 消息无 clientId，
        // 复用后 syncAssistantSnapshot 因 clientId=undefined 静默失败，
        // 导致流式内容无法写回 history（"思考中"不消退 + 正文不更新）
        const lastMsg = (session.history || []).slice().reverse().find(m => m.role === 'assistant' && m.clientId);
        let assistantMsgAdded: boolean;
        if (lastMsg) {
          // 复用本地已有消息，继续在其上追加 delta
          assistantMsg = { ...lastMsg };
          assistantMsgAdded = true;
        } else {
          // 历史中无本地 assistant 消息（刷新后 DB 消息无 clientId 不复用），新建占位
          assistantMsg = {
            clientId: _nextLocalMessageId(session, 'assistant'),
            role: 'assistant',
            content: '',
            reasoning: '',
            tool_traces: [],
            segments: [],
            timestamp: Math.floor(Date.now() / 1000),
          };
          assistantMsgAdded = false;
        }

        streamState = {
          signal: abortController.signal,
          projectName,
          agentId,
          contextKey,
          streamEpoch,
        };
        await this._consumeStream(session, assistantMsg, assistantMsgAdded, reader, sessionId, streamState);

        if (
          !abortController.signal.aborted
          && session.streamEpoch === streamEpoch
          && !streamState.receivedTaskDone
        ) {
          const recovered = await this._recoverChatStreamObserver(
            session,
            agentId,
            contextKey,
            _getRecoverySeq(assistantMsg, streamState, afterSeq),
            streamEpoch,
          );
          if (recovered) return;
        }
      } catch (e: unknown) {
        if (_isAbortError(e) || abortController.signal.aborted) return;
        if (_isLikelyStreamTransportError(e)) {
          session.backgroundTaskStatus = 'running';
          session.sending = true;
          session.lastError = '';
          _setTransportReconnectState(session, retryIndex + 1);

          let taskStatus: AnyRecord | null = null;
          try {
            taskStatus = await getChatTaskStatus(projectName, agentId, contextKey) as AnyRecord;
          } catch {}

          if (taskStatus?.status === 'running' && retryIndex < maxTransportRetries) {
            session.backgroundTaskStatus = 'running';
            session.sending = true;
            applyPersistedTokenStats(session, taskStatus);
            const nextSeq = _getRecoverySeq(assistantMsg, streamState, afterSeq);
            await _delay(Math.min(2500, 500 * (retryIndex + 1)));
            await this._reconnectTaskStream(session, agentId, contextKey, nextSeq, {
              retryIndex: retryIndex + 1,
              maxTransportRetries,
            });
            return;
          }

          if (taskStatus?.status === 'completed' || taskStatus?.status === 'cancelled' || taskStatus?.status === 'error') {
            session.backgroundTaskStatus = null;
            session.sending = false;
            applyPersistedTokenStats(session, taskStatus);
            if (taskStatus.status === 'error') {
              session.lastError = String(taskStatus.error || _defaultBackgroundTaskError());
            }
            _clearRetryState(session);
            await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
            return;
          }

          const recovered = await this._resumeChatTaskAfterTransportLoss(
            session,
            agentId,
            contextKey,
            _getRecoverySeq(assistantMsg, streamState, afterSeq),
            streamEpoch,
            {
              initialError: e,
              startAttempt: retryIndex + 1,
              maxTransportRetries,
            },
          );
          if (recovered) return;
        }
        const errorMsg = _getErrorMessage(e, '重连聊天任务流失败');
        console.warn(errorMsg, e);
        session.lastError = errorMsg;
        session.backgroundTaskStatus = null;
        session.sending = false;
        bus.emit('toast', { type: 'error', message: errorMsg });
        await this.refreshSessionHistory(sessionId, 80, { silent: true });
      } finally {
        if (session.streamEpoch === streamEpoch) {
          const canKeepRunning = !session.abortRequested && !abortController.signal.aborted;
          const taskState: ChatTaskAuthorityState = streamState?.receivedTaskDone
            ? 'terminal'
            : canKeepRunning
              ? await this._getChatTaskAuthorityState(agentId, contextKey, projectName)
              : 'terminal';
          const keepLocked = this._applyChatTaskAuthorityState(session, taskState);
          this._finalizeSessionAbort(sessionId, abortController);

          // 重连流只包含断开后的 delta，本地 assistant 内容不完整。
          // 流结束时后端已将完整消息落盘，移除本地部分消息后刷新历史。
          if (!keepLocked && !abortController.signal.aborted && assistantMsg) {
            const partialClientId = assistantMsg.clientId;
            if (partialClientId) {
              session.history = (session.history || []).filter(
                m => !(m.role === 'assistant' && m.clientId === partialClientId),
              );
            }
            await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
          }
          if (taskState === 'unknown' && session.streamEpoch === streamEpoch) {
            void this._resumeChatTaskAfterTransportLoss(
              session,
              agentId,
              contextKey,
              _getRecoverySeq(assistantMsg, streamState, afterSeq),
              streamEpoch,
            ).catch(error => console.warn('恢复后台聊天任务状态失败', error));
          }
        }
      }
    },

    /** 清空会话历史 */
    async clearSession(sessionId) {
      const session = this.sessions[sessionId];
      if (!session) return;

      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;
      await clearChatHistory(projectName, session.agentId, session.contextKey);
      session.history = [];
      session.importedContext = null;
      session.attachments = [];
      session.contextTokenCount = null;
      session.contextTokenUsage = null;
      session.tokenUsageByTask = {};
      session.contextWindowStats = null;
      session.lastError = '';
    },

    /** 删除会话中的单条消息 */
    async deleteSessionMessage(sessionId, messageId) {
      const session = this.sessions[sessionId];
      if (!session || messageId == null || String(messageId).trim() === '') return;

      const targetIndex = (session.history || []).findIndex(
        (m) => String(m?.id ?? '') === String(messageId) || String(m?.clientId ?? '') === String(messageId)
      );
      const targetMessage = (session.history || []).find(
        (m) => String(m?.id ?? '') === String(messageId) || String(m?.clientId ?? '') === String(messageId)
      );
      if (!targetMessage || targetIndex === -1) return;

      const hasPersistedId = targetMessage.id != null && String(targetMessage.id).trim() !== '';
      if (!hasPersistedId) {
        session.history = (session.history || []).filter(m => (m?.clientId || '') !== targetMessage.clientId);
        return;
      }

      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;

      try {
        await deleteChatMessage(projectName, targetMessage.id);
        session.history = (session.history || []).filter(m => m.id !== targetMessage.id);
      } catch (e: unknown) {
        bus.emit('toast', { type: 'error', message: _getErrorMessage(e, '删除失败') });
      }
    },

    async compactSessionContext(sessionId, targetTokens?: number) {
      const session = this.sessions[sessionId];
      if (!session || session.sending) return;
      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;
      const loadingTarget = session.kind === 'primary' ? 'chat-primary' : `chat-session-${sessionId}`;
      const task = createStreamingTask('chat', {
        target: loadingTarget,
        text: i18n.global.t('components.chatPanel.compactingContext'),
        progress: i18n.global.t('components.chatPanel.compactingContextProgress'),
        canCancel: false,
        showStats: true,
        statsMode: 'elapsed',
      });
      try {
        const result = await compactChatContext(projectName, session.agentId, session.contextKey, targetTokens) as AnyRecord;
        if (result.compacted === false) {
          bus.emit('toast', { type: 'info', message: i18n.global.t('components.chatPanel.compactContextSkipped') });
          return result;
        }
        const nextStats = result.contextWindowStats || result.context_window_stats;
        if (nextStats && typeof nextStats === 'object') {
          session.contextWindowStats = extractContextWindowStats({
            event: 'context_window_stats',
            agent_id: session.agentId,
            ...nextStats,
          });
        } else {
          session.contextWindowStats = null;
        }
        await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
        bus.emit('toast', { type: 'success', message: i18n.global.t('components.chatPanel.compactContextSuccess') });
        return result;
      } catch (e: unknown) {
        const message = _getErrorMessage(e, i18n.global.t('components.chatPanel.compactContextFailed'));
        bus.emit('toast', { type: 'error', message });
        throw e;
      } finally {
        task.hide();
        task.dispose();
      }
    },

    /** 移除消息的附件上下文（不删除消息本身）。
     *
     * 多附件场景下可选传入 ``attachmentId`` 精确指定要删除的附件；不传时
     * 沿用旧语义——按消息 metadata 中首个未删除附件做匹配。
     */
    async removeSessionAttachment(sessionId, messageId, attachmentId?: string | null) {
      const session = this.sessions[sessionId];
      if (!session || messageId == null || String(messageId).trim() === '') return;

      const targetMessage = (session.history || []).find(
        (m) => String(m?.id ?? '') === String(messageId) || String(m?.clientId ?? '') === String(messageId)
      );
      if (!targetMessage) return;

      const trimmedAttachmentId = String(attachmentId || '').trim();
      // 解析 reference：传 attachmentId 时按 id 查找，否则取列表首项。
      let reference: AnyRecord | null = null;
      const importedFiles = extractImportedFilesMeta(targetMessage?.metadata || null);
      if (trimmedAttachmentId) {
        reference = importedFiles.find((entry) => entry.attachmentId === trimmedAttachmentId) || null;
      } else {
        reference = importedFiles[0] || null;
      }

      // 本地：从 session.attachments 中移除对应项（按 id 优先），并同步 importedContext。
      if (reference) {
        const refId = String(reference.attachmentId || '').trim();
        if (refId) {
          this.removeSessionAttachmentById(sessionId, refId);
        } else if (sameImportedFile(session.importedContext as AnyRecord | null, reference)) {
          session.importedContext = null;
          session.attachments = (session.attachments || []).filter(
            (item) => !sameImportedFile(item as AnyRecord, reference as AnyRecord),
          );
        }
      }

      const hasPersistedId = targetMessage.id != null && String(targetMessage.id).trim() !== '';
      if (!hasPersistedId) {
        // 未持久化消息：直接本地标记同一会话里的同一附件。
        markSessionImportedFileDeleted(session, reference);
        return;
      }

      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;

      try {
        await removeChatMessageAttachment(projectName, targetMessage.id, trimmedAttachmentId || undefined);
        // 后端会按同一会话/同一文件批量失效；前端立即同步本地状态。
        markSessionImportedFileDeleted(session, reference);
      } catch (e: unknown) {
        bus.emit('toast', { type: 'error', message: _getErrorMessage(e, '移除附件失败') });
      }
    },

    /** 编辑会话中的消息 */
    async editSessionMessage(sessionId, messageId, newContent) {
      const session = this.sessions[sessionId];
      if (!session || messageId == null || String(messageId).trim() === '') return;

      const targetIndex = (session.history || []).findIndex(
        (m) => String(m?.id ?? '') === String(messageId) || String(m?.clientId ?? '') === String(messageId)
      );
      if (targetIndex === -1) return;

      const targetMessage = session.history[targetIndex];
      let hasPersistedId = targetMessage?.id != null && String(targetMessage.id).trim() !== '';
      if (!hasPersistedId) {
        const projectName = this._resolveSessionProjectName(session);
        if (!projectName) return;
        const status = await getChatTaskStatus(projectName, session.agentId, session.contextKey) as AnyRecord;
        const persistedId = status?.userMessageId ?? status?.user_message_id;
        if (persistedId != null && String(persistedId).trim() !== '') {
          targetMessage.id = persistedId;
          hasPersistedId = true;
        } else {
          await this.refreshSessionHistory(sessionId, 80, { silent: true, authoritative: true });
          const refreshed = (session.history || []).filter(item => item?.role === 'user');
          const candidate = refreshed.slice().reverse().find(item => String(item?.content || '').trim() === String(targetMessage.content || '').trim());
          if (!candidate?.id) throw new Error('消息尚未完成保存，请稍后再编辑');
          return this.editSessionMessage(sessionId, candidate.id, newContent);
        }
      }

      const projectName = this._resolveSessionProjectName(session);
      if (!projectName) return;

      const agentIdAtStart = session.agentId;
      const contextKeyAtStart = session.contextKey;
      const streamEpoch = (session.streamEpoch || 0) + 1;
      session.streamEpoch = streamEpoch;

      try {
        await this._cancelAndWaitForChatTaskReplacement(session, agentIdAtStart, contextKeyAtStart, projectName);
      } catch (error) {
        if (session.streamEpoch === streamEpoch) {
          session.sending = false;
          session.backgroundTaskStatus = null;
          session.abortController = null;
          session.abortRequested = false;
        }
        throw error;
      }

      const abortController = new AbortController();
      this._setSessionAbortController(sessionId, abortController);

      if (session.toolClearTimer) {
        clearTimeout(session.toolClearTimer);
        session.toolClearTimer = null;
      }
      session.sending = true;
      session.toolCalling = false;
      session.toolName = '';
      session.toolProgressText = '';
      session.lastError = '';
      session.backgroundTaskStatus = 'running';
      _clearRetryState(session);
      let streamState: AnyRecord | null = null;
      try {
        // 立即在本地截断该消息之后的回复
        const index = session.history.findIndex(m => m.id === targetMessage.id);
        if (index !== -1) {
          const nextHistory = session.history.slice(0, index + 1);
          nextHistory[index] = { ...nextHistory[index], content: newContent };
          session.history = nextHistory;
        }

        const { activeContext, activeMeta, messageMetadata } = resolveMessageContextForEdit(this._contextProvider, targetMessage);
        if (index !== -1 && messageMetadata) {
          const nextHistory = session.history.slice();
          nextHistory[index] = { ...nextHistory[index], metadata: messageMetadata };
          session.history = nextHistory;
        }

        const assistantMsg = {
          clientId: _nextLocalMessageId(session, 'assistant'),
          role: 'assistant',
          content: '',
          reasoning: '',
          tool_traces: [],
          segments: [],
          timestamp: Math.floor(Date.now() / 1000),
        };
        let assistantMsgAdded = false;
        const reader = await editChatMessageStream(projectName, agentIdAtStart, contextKeyAtStart, targetMessage.id, newContent, activeContext, activeMeta, abortController.signal);

        // 统一流式处理
        streamState = {
          signal: abortController.signal,
          projectName,
          agentId: agentIdAtStart,
          contextKey: contextKeyAtStart,
          streamEpoch,
          userMessageClientId: targetMessage.clientId,
          userMessageContent: String(newContent || '').trim(),
        };
        await this._consumeStream(session, assistantMsg, assistantMsgAdded, reader, sessionId, streamState);

        if (abortController.signal.aborted || session.abortRequested || session.streamEpoch !== streamEpoch) {
          return;
        }
        if (!streamState.receivedTaskDone) {
          const recovered = await this._recoverChatStreamObserver(session, agentIdAtStart, contextKeyAtStart, _getRecoverySeq(assistantMsg, streamState), streamEpoch);
          if (recovered) return;
        }

        // 从服务器同步
        // 同 sendSessionMessage：编辑重生成结束后不立即 refresh，避免本地新回复被不完整历史覆盖。
      } catch (e: unknown) {
        if (_isAbortError(e) || abortController.signal.aborted || session.abortRequested) {
          return;
        }
        if (_isLikelyStreamTransportError(e)) {
          const lastAssistant = (session.history || []).slice().reverse().find(m => m?.role === 'assistant');
          const recovered = await this._resumeChatTaskAfterTransportLoss(
            session,
            agentIdAtStart,
            contextKeyAtStart,
            _getRecoverySeq(lastAssistant || null, streamState),
            streamEpoch,
            { initialError: e },
          );
          if (recovered) return;
        }
        const errorMsg = _getErrorMessage(e, '编辑失败');
        session.lastError = errorMsg;
        bus.emit('toast', { type: 'error', message: errorMsg });
        throw e;
      } finally {
        if (session.streamEpoch === streamEpoch) {
          if (!session.toolClearTimer) {
            session.toolCalling = false;
            session.toolName = '';
            session.toolProgressText = '';
          }
          const canKeepRunning = !session.abortRequested && !abortController.signal.aborted;
          const taskState: ChatTaskAuthorityState = streamState?.receivedTaskDone
            ? 'terminal'
            : canKeepRunning
              ? await this._getChatTaskAuthorityState(agentIdAtStart, contextKeyAtStart, projectName)
              : 'terminal';
          this._applyChatTaskAuthorityState(session, taskState);
          this._finalizeSessionAbort(sessionId, abortController);
          if (taskState === 'unknown' && session.streamEpoch === streamEpoch) {
            void this._resumeChatTaskAfterTransportLoss(
              session,
              agentIdAtStart,
              contextKeyAtStart,
              _getRecoverySeq(null, streamState),
              streamEpoch,
            ).catch(error => console.warn('恢复编辑任务状态失败', error));
          }
        }
      }
    },

    // ==================== 内部：统一流式消费逻辑（只维护这一份） ====================

    /**
     * 消费 ReadableStream reader，解析 NDJSON 事件并更新会话状态。
     * 所有流式入口（send / edit × 主会话 / 额外会话）都走这一个方法。
     */
    async _consumeStream(session: ChatSession, assistantMsg: AnyRecord, assistantMsgAdded: boolean, reader: ReadableStreamDefaultReader<Uint8Array>, sessionId: number, streamState: AnyRecord = {}) {
      const decoder = new TextDecoder('utf-8');
      let currentToolName = '';
      let currentToolTarget = '';
      let currentToolSegId = '';
      let lineBuffer = '';
      let toolLoadingStats: unknown = null;
      const panelToolTasks = new Map<string, PanelToolTaskEntry>();
      const panelToolEventKeyMap = new Map();
      const hasEventField = (event: AnyRecord, field: string) => Object.prototype.hasOwnProperty.call(event, field);
      const {
        signal = null,
        projectName = session.projectName,
        agentId = session.agentId,
        contextKey = session.contextKey,
        streamEpoch = session.streamEpoch,
      } = streamState;
      streamState.receivedTaskDone = false;
      streamState.lastSeq = Number(streamState.lastSeq || 0) || 0;
      const isStreamCurrent = () => (
        session.projectName === projectName
        && session.agentId === agentId
        && session.contextKey === contextKey
        && session.streamEpoch === streamEpoch
      );
      const wasAborted = () => Boolean(signal?.aborted || session.abortRequested || !isStreamCurrent());

      // ---------- 局部闭包 ----------

      const ensureAssistantAdded = () => {
        if (!isStreamCurrent()) return;
        if (!assistantMsgAdded) {
          session.history = _upsertHistoryMessage(session.history, assistantMsg);
          assistantMsgAdded = true;
        }
      };

      const syncAssistantSnapshot = () => {
        if (!assistantMsgAdded || !isStreamCurrent()) return;
        // 深拷贝 segments 和 tool_traces，避免 history 中的 snapshot 与 assistantMsg 共享可变引用
        // 否则后续 push/修改操作会静默修改已存入 history 的旧 snapshot，导致 Vue diff 失效
        session.history = _upsertHistoryMessage(session.history, {
          ...assistantMsg,
          segments: assistantMsg.segments.map(s => ({ ...s })),
          tool_traces: assistantMsg.tool_traces.map(t => ({ ...t })),
        });
      };

      const upsertAssistantToolTrace = (toolName, patch = {}) => {
        const normalizedToolName = normalizeToolName(toolName);
        if (!normalizedToolName) return;
        assistantMsg.tool_traces = mergeToolTrace(assistantMsg.tool_traces, {
          tool_name: normalizedToolName,
          ...patch,
        });
        syncAssistantSnapshot();
      };

      const setSessionToolState = (toolName = '', progressText = '', startedAt = Date.now()) => {
        if (session.toolClearTimer) {
          clearTimeout(session.toolClearTimer);
          session.toolClearTimer = null;
        }
        session.toolCalling = !!toolName;
        session.toolName = toolName || '';
        session.toolProgressText = progressText || '';
        session.toolStateStartedAt = toolName ? startedAt : 0;
      };

      const scheduleSessionToolClear = () => {
        const finalizeToolUi = () => {
          if (!isStreamCurrent()) return;
          session.toolCalling = false;
          session.toolName = '';
          session.toolProgressText = '';
          session.toolStateStartedAt = 0;
          session.toolClearTimer = null;
        };

        const elapsed = session.toolStateStartedAt > 0 ? Date.now() - session.toolStateStartedAt : 0;
        const minVisibleMs = 900;
        if (elapsed < minVisibleMs) {
          session.toolClearTimer = setTimeout(finalizeToolUi, minVisibleMs - elapsed);
        } else {
          finalizeToolUi();
        }
      };

      const startPanelToolTask = (toolName, progressText, evt: AnyRecord = {}) => {
        const binding = resolveToolUiBinding(toolName, evt);
        const eventKey = String(evt?.tool_call_key || evt?.toolCallKey || '').trim();
        let taskKey = getToolUiTaskKey(binding);
        if (eventKey) {
          const mappedTaskKey = panelToolEventKeyMap.get(eventKey);
          if (mappedTaskKey) {
            const existingEntry = panelToolTasks.get(mappedTaskKey);
            if (existingEntry) {
              existingEntry.task.setProgress(progressText);
              return { binding, task: existingEntry.task, taskKey: mappedTaskKey, reused: true };
            }
            panelToolEventKeyMap.delete(eventKey);
          }
        }
        if (!taskKey) {
          return { binding, task: null, taskKey: '' };
        }

        let entry = panelToolTasks.get(taskKey);
        if (!entry) {
          const task = createStreamingTask(binding.scope, {
            target: binding.target,
            text: progressText,
            canCancel: true,
            autoStart: false,
            // 工具调用遮罩的首要职责是阻断误操作并告知当前阶段；
            // 由于工具执行期通常没有稳定、连续的正文输出流，展示实时字速会产生误导，
            // 因此这里改为工具专用时长模式：显示“正在工作中 xx秒”，但不显示实时速度。
            showStats: true,
            statsMode: 'tool_elapsed',
            onCancel: () => {
              session.abortRequested = true;
              try {
                session.abortController?.abort?.('user_cancelled');
              } catch {}
            },
          });
          entry = {
            count: 0,
            task,
            refreshEvents: binding.refreshEvents,
          };
          panelToolTasks.set(taskKey, entry);
        }

        entry.count += 1;
        entry.refreshEvents = binding.refreshEvents;
        entry.task.start(progressText, binding.target ? { target: binding.target } : {});
        entry.task.setProgress(progressText);
        if (eventKey) {
          panelToolEventKeyMap.set(eventKey, taskKey);
        }
        return { binding, task: entry.task, taskKey, reused: false };
      };

      const finishPanelToolTask = (toolName, status = 'finished', evt: AnyRecord = {}) => {
        const eventKey = String(evt?.tool_call_key || evt?.toolCallKey || '').trim();
        const binding = resolveToolUiBinding(toolName, evt);
        let taskKey = eventKey ? String(panelToolEventKeyMap.get(eventKey) || '') : '';
        if (!taskKey) {
          taskKey = getToolUiTaskKey(binding);
        }
        if (eventKey) {
          panelToolEventKeyMap.delete(eventKey);
        }
        if (!taskKey) {
          return { binding, taskKey: '', removed: true };
        }

        const entry = panelToolTasks.get(taskKey);
        if (!entry) {
          return { binding, taskKey, removed: true };
        }

        entry.count = Math.max(0, Number(entry.count || 0) - 1);
        if (entry.count > 0) {
          return { binding, taskKey, removed: false };
        }

        entry.task.dispose?.();
        panelToolTasks.delete(taskKey);
        if (status === 'finished') {
          for (const eventName of entry.refreshEvents || []) {
            bus.emit(eventName);
          }
        }
        return { binding, taskKey, removed: true };
      };

      const disposeAllPanelToolTasks = () => {
        for (const entry of panelToolTasks.values()) {
          entry.task?.dispose?.();
        }
        panelToolTasks.clear();
        panelToolEventKeyMap.clear();
        toolLoadingStats = null;
        currentToolName = '';
        currentToolTarget = '';
        currentToolSegId = '';
        if (session.toolClearTimer) {
          clearTimeout(session.toolClearTimer);
          session.toolClearTimer = null;
        }
        session.toolCalling = false;
        session.toolName = '';
        session.toolProgressText = '';
        session.toolStateStartedAt = 0;
      };

      let currentTextSourceAgent = '';

      const appendAssistantDelta = (textDelta: unknown, sourceAgent = '') => {
        const normalized = coerceStreamEventText(textDelta);
        if (!normalized) return;
        ensureAssistantAdded();
        assistantMsg.content += normalized;
        // 追加到 segments：source_agent 切换时强制新建段（用于输出被不同角色切分）
        const segs = assistantMsg.segments;
        const last = segs.length > 0 ? segs[segs.length - 1] : null;
        const agentChanged = sourceAgent !== currentTextSourceAgent;
        if (agentChanged) {
          currentTextSourceAgent = sourceAgent;
        }
        if (last && last.type === 'text' && !agentChanged) {
          last.text += normalized;
        } else {
          segs.push({ type: 'text', text: normalized, source_agent: sourceAgent || '' });
        }
        syncAssistantSnapshot();
        const toolStatsTask = toolLoadingStats as ToolLoadingStatsTask | null;
        if (toolStatsTask?.push && session.toolCalling && normalized) {
          toolStatsTask.push(
            normalized,
            session.toolProgressText || i18n.global.t('components.chatMessageList.executingTool'),
            currentToolTarget ? { target: currentToolTarget } : {},
          );
        }
      };

      const appendReasoningDelta = (textDelta: unknown, sourceAgent = '') => {
        const normalized = coerceStreamEventText(textDelta);
        if (!normalized) return;
        ensureAssistantAdded();
        assistantMsg.reasoning += normalized;
        // 插入 reasoning segment，让后续 text delta 知道需要新建 segment 而非追加
        const segs = assistantMsg.segments;
        const last = segs.length > 0 ? segs[segs.length - 1] : null;
        const sameAgent = (last?.source_agent || '') === (sourceAgent || '');
        if (last && last.type === 'reasoning' && sameAgent) {
          last.text += normalized;
        } else {
          segs.push({ type: 'reasoning', text: normalized, source_agent: sourceAgent || '' });
        }
        syncAssistantSnapshot();
      };

      let toolSegInvocationIndex = 0;
      const thinkStreamState: { mode: 'text' | 'reasoning'; pending: string } = { mode: 'text', pending: '' };

      const appendToolTraceSegment = (traceData: AnyRecord) => {
        const segs = assistantMsg.segments;
        // 使用 _seg_id 精确匹配同一次调用的 segment（start → finish 更新）
        // 不同次调用同名工具会得到不同的 _seg_id，避免覆盖
        const segId = String(traceData._seg_id || traceData.tool_call_key || traceData.toolCallKey || '').trim();
        const traceTool = normalizeToolName(traceData.tool_name || traceData.toolName || '');
        const traceSource = String(traceData.source_agent || '').trim();
        const traceNested = !!traceData.nested;
        let matchIdx = segId
          ? segs.findIndex(s => (
            s.type === 'tool_trace'
            && (
              String(s._seg_id || '').trim() === segId
              || String(s.tool_call_key || s.toolCallKey || '').trim() === segId
            )
          ))
          : -1;
        if (matchIdx < 0 && traceTool) {
          for (let i = segs.length - 1; i >= 0; i -= 1) {
            const seg = segs[i];
            if (
              seg.type === 'tool_trace'
              && normalizeToolName(seg.tool_name || seg.toolName || '') === traceTool
              && String(seg.source_agent || '').trim() === traceSource
              && !!seg.nested === traceNested
              && !['finished', 'failed', 'cancelled'].includes(String(seg.status || ''))
            ) {
              matchIdx = i;
              break;
            }
          }
        }
        if (matchIdx >= 0) {
          segs[matchIdx] = { ...segs[matchIdx], ...traceData };
          if (segId) {
            segs[matchIdx]._seg_id = segs[matchIdx]._seg_id || segId;
            segs[matchIdx].tool_call_key = segs[matchIdx].tool_call_key || traceData.tool_call_key || traceData.toolCallKey || segId;
          }
        } else {
          toolSegInvocationIndex += 1;
          const newSegId = traceData.tool_call_key || `${traceData.tool_name}:${traceData.source_agent || ''}:${toolSegInvocationIndex}`;
          segs.push({ type: 'tool_trace', ...traceData, _seg_id: newSegId });
        }
        syncAssistantSnapshot();
      };

      const appendContextCompactionSegment = (evt: AnyRecord) => {
        ensureAssistantAdded();
        const segs = assistantMsg.segments;
        const payload = buildContextCompactionSegmentPayload(evt);
        const existingIdx = segs.findIndex(seg => seg.type === 'context_compaction' && seg.status === 'running');
        if (existingIdx >= 0) {
          segs[existingIdx] = { ...segs[existingIdx], ...payload };
        } else {
          segs.push(payload);
        }
        syncAssistantSnapshot();
      };

      const onToolCallStart = (toolName: string, progressText: string, status = 'started', evt: AnyRecord = {}) => {
        if (!isStreamCurrent()) return;
        if (!toolName) return;
        const normalizedToolName = normalizeToolName(toolName);
        ensureAssistantAdded();
        currentToolName = normalizedToolName;
        const panelTaskState = startPanelToolTask(normalizedToolName, progressText, evt);
        const { scope, target } = panelTaskState.binding;
        currentToolTarget = target;
        const startedAt = Number((Date.now() / 1000).toFixed(3));
        upsertAssistantToolTrace(normalizedToolName, {
          status,
          started_at: startedAt,
          ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
          ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
          ...(evt.target_agent ? { target_agent: evt.target_agent } : {}),
          ...(evt.tool_action ? { tool_action: evt.tool_action } : {}),
          ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
        });
        appendToolTraceSegment({
          tool_name: normalizedToolName,
          status,
          started_at: startedAt,
          ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
          ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
          ...(evt.target_agent ? { target_agent: evt.target_agent } : {}),
          ...(evt.tool_action ? { tool_action: evt.tool_action } : {}),
          ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
        });
        // 记录当前工具调用的 segment ID，供 onToolCallEnd 更新时使用
        const segs = assistantMsg.segments;
        const lastSeg = segs[segs.length - 1];
        currentToolSegId = lastSeg?._seg_id || '';
        setSessionToolState(normalizedToolName, progressText, Date.now());
        bus.emit('tool-call-start', { toolName: normalizedToolName, text: progressText, target, sessionId });
        toolLoadingStats = scope ? panelTaskState.task : null;
      };

      const onToolCallEnd = (endedToolName: string, status = 'finished', extraData: Record<string, unknown> = {}) => {
        if (!isStreamCurrent()) return;
        const toolName = normalizeToolName(endedToolName || currentToolName);
        ensureAssistantAdded();
        const { target } = resolveToolUiBinding(toolName);
        const finishedAt = Number((Date.now() / 1000).toFixed(3));
        upsertAssistantToolTrace(toolName, {
          status,
          finished_at: finishedAt,
          ...extraData,
        });
        appendToolTraceSegment({ tool_name: toolName, status, finished_at: finishedAt, _seg_id: currentToolSegId, ...extraData });
        bus.emit('tool-call-end', { toolName, target, sessionId });
        bus.emit('refresh-file-tree');

        finishPanelToolTask(toolName, status, extraData);
        toolLoadingStats = null;
        scheduleSessionToolClear();
        currentToolName = '';
        currentToolTarget = '';
        currentToolSegId = '';
      };

      const applyTaskSnapshot = (evt: AnyRecord) => {
        if (!isStreamCurrent()) return;
        const userMessageId = evt.user_message_id ?? evt.userMessageId;
        if (userMessageId != null && String(userMessageId).trim() !== '') {
          const targetClientId = streamState.userMessageClientId;
          let userIndex = targetClientId
            ? (session.history || []).findIndex(item => String(item?.clientId || '') === String(targetClientId))
            : -1;
          if (userIndex < 0 && streamState.userMessageContent) {
            userIndex = (session.history || []).map((item, index) => ({ item, index })).reverse().find(({ item }) => (
              item?.role === 'user'
              && !item?.id
              && String(item?.content || '').trim() === String(streamState.userMessageContent).trim()
            ))?.index ?? -1;
          }
          if (userIndex >= 0) {
            session.history[userIndex] = {
              ...session.history[userIndex],
              id: userMessageId,
            };
          }
        }
        const assistantId = evt.assistant_message_id ?? evt.assistantMessageId ?? evt.result_message_id ?? evt.resultMessageId;
        if (assistantId != null && String(assistantId).trim() !== '') {
          assistantMsg.id = assistantId;
        }
        assistantMsg.task_id = evt.task_id || evt.taskId || assistantMsg.task_id || '';
        assistantMsg.streamSeq = Number(evt.seq ?? evt.lastSeq ?? assistantMsg.streamSeq ?? 0) || 0;
        assistantMsg.content = coerceStreamEventText(evt.content || '');
        assistantMsg.reasoning = coerceStreamEventText(evt.reasoning || '');
        assistantMsg.reasoning_duration = Number(evt.reasoning_duration ?? evt.reasoningDuration ?? assistantMsg.reasoning_duration ?? 0) || 0;
        assistantMsg.tool_traces = Array.isArray(evt.tool_traces) ? evt.tool_traces.map(item => ({ ...item })) : [];
        assistantMsg.segments = Array.isArray(evt.segments) ? evt.segments.map(item => ({ ...item })) : [];
        if (evt.metadata && typeof evt.metadata === 'object') {
          assistantMsg.metadata = { ...(assistantMsg.metadata || {}), ...evt.metadata };
        }
        applyPersistedTokenStats(session, evt);
        ensureAssistantAdded();
        syncAssistantSnapshot();

        const snapshotStatus = String(evt.status || '').trim();
        session.backgroundTaskStatus = snapshotStatus === 'running' ? 'running' : null;
        session.sending = snapshotStatus === 'running';
        if (snapshotStatus === 'error') {
          session.lastError = String(evt.error || session.lastError || _defaultBackgroundTaskError());
        }

        const activeToolSeg = assistantMsg.segments.slice().reverse().find(seg =>
          seg?.type === 'tool_trace' && (seg.status === 'started' || seg.status === 'running')
        );
        if (activeToolSeg) {
          const activeToolName = normalizeToolName(activeToolSeg.tool_name || '');
          const activeProgress = getToolProgressText(activeToolName, activeToolSeg.message || activeToolSeg.text || '', activeToolSeg);
          currentToolName = activeToolName;
          currentToolSegId = activeToolSeg._seg_id || activeToolSeg.tool_call_key || '';
          const panelTaskState = startPanelToolTask(activeToolName, activeProgress, activeToolSeg);
          currentToolTarget = panelTaskState.binding.target || '';
          setSessionToolState(activeToolName, activeProgress, Date.now());
          if (panelTaskState.binding.scope) {
            toolLoadingStats = panelTaskState.task;
          }
        } else if (session.toolCalling) {
          scheduleSessionToolClear();
        }
      };

      const handleStreamEvent = (evt: AnyRecord) => {
        if (!isStreamCurrent()) return;
        if (!evt || typeof evt !== 'object') return;
        const eventType = evt.event;
        const eventSeq = Number(evt.seq ?? evt.lastSeq ?? 0) || 0;
        if (eventSeq > 0) {
          streamState.lastSeq = Math.max(Number(streamState.lastSeq || 0), eventSeq);
        }
        const toolName = normalizeToolName(evt.tool_name || evt.toolName || '');
        const progressText = getToolProgressText(toolName, evt.message || evt.text || '', evt);
        const isNested = !!evt.nested;

        if (eventType === 'task_snapshot') {
          applyTaskSnapshot(evt);
          return;
        }
        if (eventType === 'context_window_stats') {
          const { stats: nextWindowStats, metadata } = extractContextWindowEventPatch(evt);
          session.contextWindowStats = nextWindowStats;
          ensureAssistantAdded();
          assistantMsg.metadata = {
            ...(assistantMsg.metadata || {}),
            context_window_stats: metadata,
          };
          syncAssistantSnapshot();
          return;
        }
        if (eventType === 'llm_usage') {
          applyPersistedTokenStats(session, evt);
          ensureAssistantAdded();
          assistantMsg.metadata = {
            ...(assistantMsg.metadata || {}),
            llm_usage: evt.llm_usage || evt.llmUsage || {},
          };
          syncAssistantSnapshot();
          return;
        }
        if (eventType === 'task_done') {
          streamState.receivedTaskDone = true;
          session.backgroundTaskStatus = null;
          session.sending = false;
          // 任务终态是工具遮罩的权威收口点，不依赖底层连接随后是否立即关闭。
          disposeAllPanelToolTasks();
          const metadataPatch = extractTaskDoneMetadataPatch(evt);
          applyPersistedTokenStats(session, evt);
          if (metadataPatch.changed) {
            assistantMsg.metadata = {
              ...(assistantMsg.metadata || {}),
              ...metadataPatch.metadata,
            };
            syncAssistantSnapshot();
          }
          if (evt.status === 'error') {
            session.lastError = String(session.lastError || evt.error || _defaultBackgroundTaskError());
          }
          return;
        }
        if (eventType === 'heartbeat' || eventType === 'task_cancel_requested') {
          return;
        }
        if (eventType === 'retry_attempt') {
          session.retryAttempt = Number(evt.attempt || 0) || null;
          session.retryMode = 'model';
          session.retryMaxRetries = Number(evt.max_retries ?? evt.maxRetries ?? session.retryMaxRetries ?? 3) || 3;
          session.retryErrorSummary = String(evt.error_summary || evt.errorSummary || '');
          return;
        }
        if (eventType === 'context_compaction_started' || eventType === 'context_compaction_finished' || eventType === 'context_compaction_failed') {
          assistantMsg.streamSeq = Number(evt.seq ?? assistantMsg.streamSeq ?? 0) || assistantMsg.streamSeq;
          appendContextCompactionSegment(evt);
          return;
        }
        if (eventType === 'reasoning_delta') {
          assistantMsg.streamSeq = Number(evt.seq ?? assistantMsg.streamSeq ?? 0) || assistantMsg.streamSeq;
          appendReasoningDelta(pickStreamEventText(evt, ['text', 'reasoning', 'delta', 'content', 'message', 'data']), evt.source_agent || '');
          return;
        }
        if (eventType === 'assistant_delta') {
          assistantMsg.streamSeq = Number(evt.seq ?? assistantMsg.streamSeq ?? 0) || assistantMsg.streamSeq;
          // 重试成功后收到正常 delta，清除重试状态
          if (session.retryAttempt != null) {
            _clearRetryState(session);
          }
          const parsed = consumeThinkStreamChunk(
            pickStreamEventText(evt, ['text', 'delta', 'content', 'message', 'data']),
            thinkStreamState,
          );
          if (parsed.reasoning) {
            appendReasoningDelta(parsed.reasoning, evt.source_agent || '');
          }
          if (parsed.display) {
            appendAssistantDelta(parsed.display, evt.source_agent || '');
          }
          return;
        }
        if (eventType === 'tool_intent_started') {
          if (isNested) {
            const sourceAgent = evt.source_agent || '';
            const nestedProgress = getToolProgressText(toolName, evt.message || evt.text || '', evt);
            const panelTaskState = startPanelToolTask(toolName, nestedProgress, evt);
            setSessionToolState(toolName, nestedProgress, Date.now());
            if (panelTaskState.binding.scope) {
              toolLoadingStats = panelTaskState.task;
            }
            const startedAt = Number((Date.now() / 1000).toFixed(3));
            const traceData = {
              status: 'started',
              started_at: startedAt,
              source_agent: sourceAgent,
              nested: true,
              parent_tool: evt.parent_tool || '',
              ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
              ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
              ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
            };
            upsertAssistantToolTrace(toolName, traceData);
            appendToolTraceSegment({ tool_name: toolName, ...traceData });
            // 记录嵌套工具的 segment ID 供后续 exec_started 查找
            const nestedSegs = assistantMsg.segments;
            const nestedLastSeg = nestedSegs[nestedSegs.length - 1];
            if (nestedLastSeg) nestedLastSeg._nested_seg_id = nestedLastSeg._seg_id;
          } else {
            onToolCallStart(toolName, progressText, 'started', evt);
          }
          return;
        }
        if (eventType === 'tool_exec_started') {
          if (isNested) {
            const sourceAgent = evt.source_agent || '';
            const nestedProgress = getToolProgressText(toolName, evt.message || evt.text || '', evt);
            const panelTaskState = startPanelToolTask(toolName, nestedProgress, evt);
            setSessionToolState(toolName, nestedProgress, Date.now());
            if (panelTaskState.binding.scope) {
              toolLoadingStats = panelTaskState.task;
            }
            
            // 查找是否已经有 intent 给它建好的 segment
            const existingNestedSeg = assistantMsg.segments.find(s =>
              s.type === 'tool_trace'
              && s.tool_name === toolName
              && s.nested
              && s.status === 'started'
              && (
                !(evt.tool_call_key || evt.toolCallKey)
                || s.tool_call_key === (evt.tool_call_key || evt.toolCallKey)
                || s._seg_id === (evt.tool_call_key || evt.toolCallKey)
              )
            );
            
            if (existingNestedSeg) {
              existingNestedSeg.status = 'running';
              if (hasEventField(evt, 'tool_input')) existingNestedSeg.tool_input = evt.tool_input;
              upsertAssistantToolTrace(toolName, { status: 'running' });
              if (hasEventField(evt, 'tool_input')) {
                upsertAssistantToolTrace(toolName, { tool_input: evt.tool_input });
              }
              syncAssistantSnapshot();
            } else {
              // 备用：万一只有 exec 没有 intent
              const nestedProgress = getToolProgressText(toolName, evt.message || evt.text || '', evt);
              setSessionToolState(toolName, nestedProgress, session.toolStateStartedAt || Date.now());
              const startedAt = Number((Date.now() / 1000).toFixed(3));
              const traceData = {
                status: 'running',
                started_at: startedAt,
                source_agent: sourceAgent,
                nested: true,
                parent_tool: evt.parent_tool || '',
                ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
                ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
                ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
              };
              upsertAssistantToolTrace(toolName, traceData);
              appendToolTraceSegment({ tool_name: toolName, ...traceData });
              const nestedSegs = assistantMsg.segments;
              const nestedLastSeg = nestedSegs[nestedSegs.length - 1];
              if (nestedLastSeg) nestedLastSeg._nested_seg_id = nestedLastSeg._seg_id;
            }
          } else {
            // 如果该工具已由 tool_intent_started 建立了 started 状态，
            // 直接升级为 running，复用已有 segment，避免新建导致 currentToolSegId 漂移
            const normalizedTool = normalizeToolName(toolName);
            if (normalizedTool && normalizedTool === currentToolName) {
              // 找到对应的已有 segment，原地升级
              const existingSeg = assistantMsg.segments.find(
                s => s.type === 'tool_trace' && s.tool_name === normalizedTool && s._seg_id === currentToolSegId
              );
              if (existingSeg) {
                existingSeg.status = 'running';
                // 将 target_agent / tool_action 等额外字段从事件透传到 segment
                if (hasEventField(evt, 'tool_input')) existingSeg.tool_input = evt.tool_input;
                if (evt.target_agent) existingSeg.target_agent = evt.target_agent;
                if (evt.tool_action) existingSeg.tool_action = evt.tool_action;
                if (evt.tool_provider || evt.toolProvider) existingSeg.tool_provider = evt.tool_provider || evt.toolProvider;
                upsertAssistantToolTrace(normalizedTool, {
                  status: 'running',
                  ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
                  ...(evt.target_agent ? { target_agent: evt.target_agent } : {}),
                  ...(evt.tool_action ? { tool_action: evt.tool_action } : {}),
                  ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
                });
                syncAssistantSnapshot();
                session.toolProgressText = progressText || session.toolProgressText;
              } else {
                onToolCallStart(toolName, progressText, 'running', evt);
                // 补丁 extra 字段到刚创建的 segment，并强制更新快照
                if (hasEventField(evt, 'tool_input') || evt.target_agent || evt.tool_action || evt.tool_provider || evt.toolProvider) {
                  const patchSeg = assistantMsg.segments[assistantMsg.segments.length - 1];
                  if (patchSeg && patchSeg.type === 'tool_trace') {
                    if (hasEventField(evt, 'tool_input')) patchSeg.tool_input = evt.tool_input;
                    if (evt.target_agent) patchSeg.target_agent = evt.target_agent;
                    if (evt.tool_action) patchSeg.tool_action = evt.tool_action;
                    if (evt.tool_provider || evt.toolProvider) patchSeg.tool_provider = evt.tool_provider || evt.toolProvider;
                    syncAssistantSnapshot();
                  }
                }
              }
            } else {
              onToolCallStart(toolName, progressText, 'running', evt);
              // 将额外字段补丁到刚创建的 segment（新建路径），并强制更新快照
              if (hasEventField(evt, 'tool_input') || evt.target_agent || evt.tool_action) {
                const lastSeg = assistantMsg.segments[assistantMsg.segments.length - 1];
                if (lastSeg && lastSeg.type === 'tool_trace') {
                  if (hasEventField(evt, 'tool_input')) lastSeg.tool_input = evt.tool_input;
                  if (evt.target_agent) lastSeg.target_agent = evt.target_agent;
                  if (evt.tool_action) lastSeg.tool_action = evt.tool_action;
                  syncAssistantSnapshot();
                }
              }
            }
          }
          return;
        }
        if (eventType === 'tool_exec_finished') {
          if (isNested) {
            const finishedAt = Number((Date.now() / 1000).toFixed(3));
            const sourceAgent = evt.source_agent || '';
            // 找到对应的嵌套 segment ID
            const nestedMatchSeg = assistantMsg.segments.find(s =>
              s.type === 'tool_trace' && s.tool_name === toolName
              && (s.source_agent || '') === sourceAgent && s.nested && s.status !== 'finished'
            );
            const traceData = {
              status: 'finished',
              finished_at: finishedAt,
              source_agent: sourceAgent,
              nested: true,
              parent_tool: evt.parent_tool || '',
              ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
              ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
              _seg_id: nestedMatchSeg?._seg_id || '',
              ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
              ...(hasEventField(evt, 'tool_result') ? { tool_result: evt.tool_result } : {}),
            };
            upsertAssistantToolTrace(toolName, traceData);
            appendToolTraceSegment({ tool_name: toolName, ...traceData });
            const parentTool = evt.parent_tool || currentToolName;
            finishPanelToolTask(toolName, 'finished', evt);
            toolLoadingStats = null;
            if (parentTool && parentTool !== toolName) {
              setSessionToolState(parentTool, getToolProgressText(parentTool, ''), session.toolStateStartedAt || Date.now());
            } else {
              scheduleSessionToolClear();
            }
          } else {
            onToolCallEnd(toolName || currentToolName, 'finished', {
              ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
              ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
              ...(hasEventField(evt, 'tool_result') ? { tool_result: evt.tool_result } : {}),
              ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
            });
          }
          return;
        }
        if (eventType === 'tool_exec_failed') {
          if (isNested) {
            const failedAt = Number((Date.now() / 1000).toFixed(3));
            const sourceAgent = evt.source_agent || '';
            const nestedMatchSeg = assistantMsg.segments.find(s =>
              s.type === 'tool_trace' && s.tool_name === toolName
              && (s.source_agent || '') === sourceAgent && s.nested && s.status !== 'finished'
            );
            const failureText = evt.tool_error ?? evt.message ?? evt.text;
            const traceData = {
              status: 'failed',
              finished_at: failedAt,
              source_agent: sourceAgent,
              nested: true,
              parent_tool: evt.parent_tool || '',
              ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
              ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
              _seg_id: nestedMatchSeg?._seg_id || '',
              ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
              ...(failureText !== undefined && failureText !== null ? { tool_error: failureText } : {}),
            };
            upsertAssistantToolTrace(toolName, traceData);
            appendToolTraceSegment({ tool_name: toolName, ...traceData });
            finishPanelToolTask(toolName, 'failed', evt);
            toolLoadingStats = null;
            scheduleSessionToolClear();
          } else {
            const failureText = evt.tool_error ?? evt.message ?? evt.text;
            onToolCallEnd(toolName || currentToolName, 'failed', {
              ...(hasEventField(evt, 'tool_input') ? { tool_input: evt.tool_input } : {}),
              ...(evt.tool_call_key || evt.toolCallKey ? { tool_call_key: evt.tool_call_key || evt.toolCallKey } : {}),
              ...(failureText !== undefined && failureText !== null ? { tool_error: failureText } : {}),
              // 保留旧客户端依赖的失败摘要字段，详情面板优先使用 tool_error。
              ...(evt.message || evt.text ? { tool_result: evt.message || evt.text } : {}),
              ...(evt.tool_provider || evt.toolProvider ? { tool_provider: evt.tool_provider || evt.toolProvider } : {}),
            });
          }
          return;
        }
        if (eventType === 'error') {
          // 状态唯一性原则：
          //   - sending / backgroundTaskStatus 的清零统一交给 sendSessionMessage / _reconnectTaskStream
          //     的 finally 块（它们会在流自然结束或主动 abort 时收口），避免在事件层"提前清零"
          //     与后端 _active_chat_tasks 中残留的 running 任务出现分歧。
          //   - 但 error 事件仍标记 receivedTaskDone，让外层不再触发不必要的 observer 重连
          //     —— 后端中间错误已统一被 _run_chat_stream_with_retry 静默拦截，前端能拿到的
          //     error 事件已经是"全部重试均失败"的最终结论。
          streamState.receivedTaskDone = true;
          disposeAllPanelToolTasks();
          const errMsg = _localizedStreamError(evt);
          session.lastError = errMsg;
          _clearRetryState(session);
          if (errMsg) {
            bus.emit('toast', { type: 'error', message: errMsg });
          }
        }
        if (eventType === 'director_auto_write_started') {
          const targetProjectName = String(evt.project_name || projectName || session.projectName || '').trim();
          if (!targetProjectName) return;

          useDirectorAutoWriteStore().onDirectorStarted({
            project_name:        targetProjectName,
            start_chapter_index: Number(evt.start_chapter_index ?? 0),
            start_scene_index:   Number(evt.start_scene_index ?? 0),
            mode:                String(evt.mode || 'chapter_by_chapter'),
            export_format:       String(evt.export_format || 'arc'),
            auto_review:         evt.auto_review === true,
            total_chapters:      Number(evt.total_chapters ?? 0),
            total_scenes:        Number(evt.total_scenes ?? 0),
          });
          // Store 已同步进入 running 后再通知 App 装载覆盖层，避免后台标签页中的异步竞态。
          bus.emit('director-auto-write-started', { ...evt, project_name: targetProjectName });
        }
      };


      const consumeLine = (line) => {
        const raw = String(line || '');
        const trimmed = raw.trim();
        if (!trimmed) return;
        try {
          const evt = JSON.parse(trimmed);
          handleStreamEvent(evt);
        } catch {
          appendAssistantDelta(raw);
        }
      };

      // ---------- 主循环 ----------

      while (true) {
        if (wasAborted()) break;
        let readResult;
        try {
          readResult = await reader.read();
        } catch (error: unknown) {
          if (_isAbortError(error) || wasAborted()) {
            break;
          }
          throw error;
        }
        const { value, done } = readResult;
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        if (!chunk) continue;

        // 所有 agent（包括导演）统一使用 JSON 事件格式解析
        lineBuffer += chunk;
        let nlIndex = lineBuffer.indexOf('\n');
        while (nlIndex >= 0) {
          const line = lineBuffer.slice(0, nlIndex);
          lineBuffer = lineBuffer.slice(nlIndex + 1);
          consumeLine(line);
          nlIndex = lineBuffer.indexOf('\n');
        }
      }

      // 处理末尾残余数据
      const tail = wasAborted() ? '' : decoder.decode();
      if (tail) {
        lineBuffer += tail;
      }
      if (!wasAborted() && lineBuffer.trim()) {
        consumeLine(lineBuffer);
      }

      if (!wasAborted()) {
        const flushedThinkTail = flushThinkStreamState(thinkStreamState);
        if (flushedThinkTail.reasoning) {
          appendReasoningDelta(flushedThinkTail.reasoning);
        }
        if (flushedThinkTail.display) {
          appendAssistantDelta(flushedThinkTail.display);
        }
        thinkStreamState.pending = '';
      }

      // 清理未关闭的工具调用
      if (currentToolName) {
        onToolCallEnd(currentToolName, wasAborted() ? 'cancelled' : 'finished');
      } else if (session.toolCalling) {
        scheduleSessionToolClear();
      }
      // 收尾：将所有遗留的 started/running segment 标记为 finished
      // 解决同一轮多工具意图导致 currentToolSegId 漂移遗留旧 segment 仍显示旋转动画的问题
      if (!wasAborted()) {
        const nowTs = Number((Date.now() / 1000).toFixed(3));
        let orphanFixed = false;
        for (const seg of assistantMsg.segments) {
          if (seg.type === 'tool_trace' && (seg.status === 'started' || seg.status === 'running')) {
            seg.status = 'finished';
            if (!seg.finished_at) seg.finished_at = nowTs;
            orphanFixed = true;
          }
        }
        if (orphanFixed) syncAssistantSnapshot();
      }
      disposeAllPanelToolTasks();
      if (wasAborted()) {
        try {
          await reader.cancel();
        } catch {}
      }

      // Token 显示由后端 llm_usage 实时事件及 task_done/task_snapshot 终态快照共同驱动。
    },
  },
});
