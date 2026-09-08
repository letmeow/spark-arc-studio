<template>
  <div class="chat-panel">
    <!-- Header -->
    <div class="chat-panel-header" @mousedown="$emit('header-mousedown', $event)" @touchstart.passive="$emit('header-touchstart', $event)">
      <div class="chat-panel-header-left">
        <span v-if="!hideHeaderIcon" class="chat-header-icon">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 2L14.4 9.6L22 12L14.4 14.4L12 22L9.6 14.4L2 12L9.6 9.6L12 2Z" fill="currentColor"/>
          </svg>
        </span>
        <AgentRadialPicker
          :value="agentId"
          :options="agentOptions"
          :disabled="sending && !allowAgentSwitchWhileSending"
          @update:value="onAgentSelected"
          @closed="onAgentPickerClosed"
          @rerun="$emit('rerun')"
        />
        <ChatProgressBoardPopover :history="history" :agent-id="agentId" />
        <template v-if="isMobile && contextTokenLabel">
          <button
            ref="mobileTokenButtonRef"
            type="button"
            class="chat-token-chip"
            :title="contextTokenHint"
            :aria-label="contextTokenHint"
            @mousedown.stop
            @touchstart.stop
            @touchend.stop.prevent="onMobileTokenTouchEnd"
            @click.stop="onMobileTokenClick"
          >
            {{ contextTokenLabel }}
          </button>
          <Teleport to="body">
            <Transition name="chat-token-layer">
              <div
                v-if="mobileTokenUsageVisible"
                class="chat-token-usage-mobile-layer"
                role="presentation"
                :style="mobileTokenLayerStyle"
                @click.self="closeMobileTokenUsage"
                @touchend.self.prevent="closeMobileTokenUsage"
              >
                <section
                  class="chat-token-usage-mobile-panel"
                  role="dialog"
                  :aria-label="t('components.chatPanel.tokenUsageTitle')"
                  @click.stop
                  @touchend.stop
                >
                  <ChatTokenUsagePanel
                    :usage="contextTokenUsage"
                    :agent-id="agentId"
                    :live="sending"
                  />
                </section>
              </div>
            </Transition>
          </Teleport>
        </template>
        <n-popover
          v-else-if="contextTokenLabel"
          trigger="click"
          placement="bottom-start"
          :show-arrow="false"
          :overlap="false"
        >
          <template #trigger>
            <button
              type="button"
              class="chat-token-chip"
              :title="contextTokenHint"
              :aria-label="contextTokenHint"
              @mousedown.stop
            >
              {{ contextTokenLabel }}
            </button>
          </template>
          <ChatTokenUsagePanel
            :usage="contextTokenUsage"
            :agent-id="agentId"
            :live="sending"
          />
        </n-popover>
        <n-popconfirm
          :positive-text="t('common.confirm')"
          :negative-text="t('common.cancel')"
          @positive-click="$emit('compact-context')"
        >
          <template #trigger>
            <n-button
              size="small"
              class="btn-action-clear"
              circle
              quaternary
              :disabled="sending || loading"
              style="margin-left: 4px;"
            >
              <template #icon>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M16 22h2a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v18" />
                  <path d="M14 2v4a2 2 0 0 0 2 2h4" />
                  <path d="M8 6h2v2H8z" />
                  <path d="M10 8h2v2h-2z" />
                  <path d="M8 10h2v2H8z" />
                  <path d="M10 12h2v2h-2z" />
                  <path d="M8 14h2v2H8z" />
                  <path d="M10 16h2v2h-2z" />
                  <rect x="6" y="18" width="6" height="4" rx="1" />
                </svg>
              </template>
            </n-button>
          </template>
          {{ t('components.chatPanel.compactContextConfirm') }}
        </n-popconfirm>
        <n-popconfirm
          :positive-text="t('common.confirm')"
          :negative-text="t('common.cancel')"
          @positive-click="$emit('clear')"
        >
          <template #trigger>
            <n-button size="small" class="btn-action-clear" circle quaternary style="margin-left: 4px;">
              <template #icon>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
              </template>
            </n-button>
          </template>
          {{ t('components.chatPanel.clearHistoryConfirm') }}
        </n-popconfirm>
        <!-- 额外按钮插槽（新建窗口等） -->
        <slot name="header-actions"></slot>
      </div>
      <div class="chat-panel-header-right">
        <slot name="header-right"></slot>
      </div>
    </div>

    <div class="chat-panel-body">
      <GlobalLoading scope="chat" :target="loadingTarget" variant="card" />
      <div
        v-if="loading || agentContentPending"
        class="chat-panel-loading-state"
        role="status"
        aria-live="polite"
        :aria-label="t('components.chatMessageList.loading')"
      >
        <SparkLoaderAnimation class="chat-panel-loader-animation" aria-hidden="true" />
      </div>
      <!-- 消息列表 -->
      <ChatMessageList
        ref="chatListRef"
        :history="visibleHistory"
        :loading="loading || agentContentPending"
        :last-error="lastError"
        :sending="sending"
        :thinking-seconds="thinkingSeconds"
        :tool-calling="toolCalling"
        :tool-name="toolName"
        :tool-progress-text="toolProgressText"
        :retry-attempt="retryAttempt"
        :retry-mode="retryMode"
        :retry-max-retries="retryMaxRetries"
        :retry-error-summary="retryErrorSummary"
        :editing-message-id="editingMessageId"
        v-model:editing-content="editingContentLocal"
        :extra-class="listExtraClass"
        @start-edit="$emit('start-edit', $event)"
        @cancel-edit="$emit('cancel-edit')"
        @save-edit="$emit('save-edit', $event)"
        @edit-keydown="(e, id) => $emit('edit-keydown', e, id)"
        @delete-msg="$emit('delete-msg', $event)"
        @retry="(id, content) => $emit('retry', id, content)"
        @reach-top="loadOlderHistory"
      >
        <template #empty-state>
          <slot name="empty-state"></slot>
        </template>
      </ChatMessageList>
      <div class="chat-input-wrapper" :class="inputWrapperClass">
        <div v-if="slots['input-prefix']" class="chat-input-prefix">
          <slot name="input-prefix"></slot>
        </div>
        <n-input
          :value="draft"
          type="textarea"
          :size="inputWrapperClass.includes('is-compact') ? 'small' : undefined"
          :autosize="inputWrapperClass.includes('is-compact') ? { minRows: 1, maxRows: 5 } : { minRows: 2, maxRows: 6 }"
          :placeholder="placeholder || t('components.chatPanel.inputPlaceholder')"
          @update:value="$emit('update:draft', $event)"
          @keydown="$emit('draft-keydown', $event)"
          class="chat-textarea"
        />
        <n-button
          circle
          size="small"
          @click="sending ? $emit('stop') : $emit('send')"
          class="send-btn spark-send-btn"
          :class="{ 'is-working': sending }"
          :aria-label="sending ? t('components.chatPanel.stop') : t('components.chatPanel.send')"
        >
          <template #icon>
            <span class="send-icon-stage" aria-hidden="true">
              <svg class="send-glyph send-glyph--ready" viewBox="0 0 24 24" fill="none">
                <!-- 上升能量拖尾：灵感被激发、向右上方送出的轨迹 -->
                <circle class="send-trail send-trail-1" cx="4.5" cy="19.5" r="1" />
                <circle class="send-trail send-trail-2" cx="8.2" cy="15.8" r="1.25" />
                <!-- 主火花：四角星，灵感核心 -->
                <path class="send-spark-core" d="M14.8 3.6l1.75 5.45 5.45 1.75-5.45 1.75L14.8 18.5l-1.75-5.45L7.6 11.3l5.45-1.75L14.8 3.6Z" />
                <!-- 点缀小火花 -->
                <path class="send-spark-mini" d="M19.6 14.4l.55 1.7 1.7.55-1.7.55-.55 1.7-.55-1.7-1.7-.55 1.7-.55.55-1.7Z" />
              </svg>
              <svg class="send-glyph send-glyph--working" viewBox="0 0 24 24" fill="none">
                <circle class="work-orbit" cx="12" cy="12" r="8.5" />
                <rect class="work-core" x="8.5" y="8.5" width="7" height="7" rx="2" />
                <path class="work-spark" d="M18.5 4.5 19.2 7l2.3.8-2.3.8-.7 2.4-.8-2.4-2.2-.8 2.2-.8.8-2.5Z" />
              </svg>
            </span>
          </template>
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ChatPanel.vue - 聊天面板核心 UI 原子
 * 
 * 职责：
 * 1. 核心渲染层：封装了消息历史展示（ChatMessageList）和底部输入区（Input Bar）。
 * 2. 状态驱动：通过 props 接收对话数据，通过 events 发出交互指令，本身不持有业务 Store。
 * 3. 高复用性：同时服务于 GlobalChatFloat（单例主入口）和 ExtraChatWindow（多实例窗口）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useSlots, watch, type CSSProperties, type PropType } from 'vue';
import { useI18n } from 'vue-i18n';
import { NButton, NInput, NPopconfirm, NPopover, NTooltip } from 'naive-ui';
import ChatMessageList from '@/components/chat/ChatMessageList.vue';
import AgentRadialPicker from '@/components/chat/AgentRadialPicker.vue';
import ChatProgressBoardPopover from '@/components/chat/ChatProgressBoardPopover.vue';
import ChatTokenUsagePanel from '@/components/chat/ChatTokenUsagePanel.vue';
import GlobalLoading from '@/components/share/GlobalLoading.vue';
import SparkLoaderAnimation from '@/components/share/SparkLoaderAnimation.vue';
import { useMobile } from '@/composables/useMobile';
import type { ChatMessage } from '@/services/chatService';
import {
  selectChatTailWindowStart,
  selectOlderChatWindowStart,
} from '@/components/chat/message/render';
import type { TokenUsageStats } from '@/components/stores/chat/tokenStats';

type AgentOption = {
  label: string;
  value: string;
  [key: string]: unknown;
};

type ChatPanelMessage = ChatMessage & {
  id?: string | number | null;
  clientId?: string | number | null;
  [key: string]: unknown;
};

type ContextWindowStats = {
  inputTokens?: number;
  outputTokens?: number;
  cachedPromptTokens?: number;
  cacheMissPromptTokens?: number;
  cacheHitRate?: number | null;
  maxContextTokens?: number;
  usageRatio?: number | null;
  originalTokens?: number;
  model?: string;
  agentId?: string;
  compacted?: boolean;
};

const props = defineProps({
  /** 当前 agent ID */
  agentId: { type: String, default: 'agent_director' },
  /** agent 选项列表 */
  agentOptions: { type: Array as PropType<AgentOption[]>, default: () => [] },
  /** 消息历史 */
  history: { type: Array as PropType<ChatPanelMessage[]>, default: () => [] },
  /** 是否正在加载 */
  loading: { type: Boolean, default: false },
  /** 最后一个错误 */
  lastError: { type: String, default: '' },
  /** 是否正在发送 */
  sending: { type: Boolean, default: false },
  allowAgentSwitchWhileSending: { type: Boolean, default: false },
  /** 思考计时秒数 */
  thinkingSeconds: { type: Number, default: 0 },
  /** 是否正在执行工具调用 */
  toolCalling: { type: Boolean, default: false },
  /** 当前工具名 */
  toolName: { type: String, default: '' },
  /** 工具进度文本 */
  toolProgressText: { type: String, default: '' },
  /** 正在编辑的消息 ID */
  editingMessageId: { type: [String, Number, null], default: null },
  /** 编辑内容（双向绑定） */
  editingContent: { type: String, default: '' },
  /** 草稿（双向绑定） */
  draft: { type: String, default: '' },
  /** 输入框占位符 */
  placeholder: { type: String, default: '' },
  /** 消息列表的额外 CSS class */
  listExtraClass: { type: String, default: '' },
  /** 输入区包裹层的额外 CSS class */
  inputWrapperClass: { type: String, default: '' },
  /** 是否隐藏 header 闪电星标图标 */
  hideHeaderIcon: { type: Boolean, default: false },
  /** 当前重试次数 */
  retryAttempt: { type: [Number, null], default: null },
  /** 重试来源 */
  retryMode: { type: [String, null], default: null },
  /** 最大重试次数 */
  retryMaxRetries: { type: Number, default: 3 },
  /** 最近一次重试的错误摘要 */
  retryErrorSummary: { type: String, default: '' },
  /** 当前任务总 token（所有 Agent/请求聚合） */
  contextTokenCount: { type: [Number, null], default: null },
  /** 当前任务输入/输出 token（所有 Agent/请求聚合） */
  contextTokenUsage: { type: [Object, null] as PropType<TokenUsageStats | null>, default: null },
  /** 当前聊天面板最近一次实际塞入 LLM 窗口的 token */
  contextWindowStats: { type: [Object, null] as PropType<ContextWindowStats | null>, default: null },
  /** 当前面板专属全局加载 target，用于只覆盖本聊天框主体 */
  loadingTarget: { type: String, default: 'chat-primary' },
});

// 编辑内容的双向绑定代理
const emit = defineEmits([
  'update:agentId',
  'update:draft',
  'update:editingContent',
  'clear',
  'compact-context',
  'send',
  'stop',
  'rerun',
  'draft-keydown',
  'start-edit',
  'cancel-edit',
  'save-edit',
  'edit-keydown',
  'delete-msg',
  'retry',
  'header-mousedown',
  'header-touchstart',
  'history-rendered',
]);

const { t } = useI18n();
const { isMobile } = useMobile();
const slots = useSlots();
type ChatListExpose = { listRef?: HTMLElement | null };
const chatListRef = ref<ChatListExpose | null>(null);
const agentContentPending = ref(false);
const INITIAL_WINDOW = Object.freeze({ maxMessages: 4, maxContentChars: 6000 });
const OLDER_WINDOW = Object.freeze({ maxMessages: 6, maxContentChars: 12000 });
const visibleStartIndex = ref(selectChatTailWindowStart(props.history, INITIAL_WINDOW));
const hasLoadedOlder = ref(false);
const visibleHistory = computed(() => (
  agentContentPending.value ? [] : props.history.slice(visibleStartIndex.value)
));
let pendingPickerAgentId = '';
let listResizeObserver: ResizeObserver | null = null;
let viewportFillPending = false;

function getChatListElement(): HTMLElement | null {
  return chatListRef.value?.listRef || null;
}

/**
 * 尾部窗口过短时不会产生滚动条，用户也就无法通过触顶加载更早历史。
 * 自动补到列表可滚动为止，同时保留长历史的有界挂载策略。
 */
function fillHistoryViewport(): void {
  if (viewportFillPending || agentContentPending.value || visibleStartIndex.value <= 0) return;
  viewportFillPending = true;
  nextTick(() => {
    viewportFillPending = false;
    const list = getChatListElement();
    if (!list || list.clientHeight <= 0) return;
    if (list.scrollHeight <= list.clientHeight + 1) loadOlderHistory();
  });
}

function notifyHistoryRendered(): void {
  nextTick(() => {
    emit('history-rendered', visibleStartIndex.value > 0);
    fillHistoryViewport();
  });
}

function resetHistoryWindow(): void {
  visibleStartIndex.value = selectChatTailWindowStart(props.history, INITIAL_WINDOW);
  hasLoadedOlder.value = false;
}

function getHistoryMessageKey(message: ChatPanelMessage | null | undefined, index: number): string {
  for (const [kind, value] of [['id', message?.id], ['client', message?.clientId]] as const) {
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      return `${kind}:${String(value)}`;
    }
  }
  return `position:${index}:${String(message?.role || '')}:${String(message?.timestamp || '')}`;
}

function getHistoryKeys(history: ChatPanelMessage[] = []): string[] {
  return history.map((message, index) => getHistoryMessageKey(message, index));
}

function haveSameHistoryKeys(nextKeys: string[], previousKeys: string[]): boolean {
  return nextKeys.length === previousKeys.length
    && nextKeys.every((key, index) => key === previousKeys[index]);
}

function isHistoryAppend(nextKeys: string[], previousKeys: string[]): boolean {
  return nextKeys.length > previousKeys.length
    && previousKeys.every((key, index) => key === nextKeys[index]);
}

function scrollToLatest(): void {
  nextTick(() => {
    const list = getChatListElement();
    if (list) list.scrollTop = list.scrollHeight;
  });
}

watch(() => props.agentId, (nextAgentId, previousAgentId) => {
  if (nextAgentId === previousAgentId) return;
  resetHistoryWindow();
  if (nextAgentId !== pendingPickerAgentId) {
    pendingPickerAgentId = '';
    agentContentPending.value = false;
    notifyHistoryRendered();
    scrollToLatest();
  }
}, { flush: 'sync' });

watch(() => getHistoryKeys(props.history), (nextKeys, previousKeys = []) => {
  if (haveSameHistoryKeys(nextKeys, previousKeys)) return;

  if (!isHistoryAppend(nextKeys, previousKeys)) {
    // 历史被刷新或会话切换后，即使消息总数不变，也必须重新取尾部窗口。
    resetHistoryWindow();
    scrollToLatest();
  } else if (!hasLoadedOlder.value) {
    visibleStartIndex.value = selectChatTailWindowStart(props.history, INITIAL_WINDOW);
  }
  notifyHistoryRendered();
}, { flush: 'sync' });

const chatLoading = computed(() => props.loading || agentContentPending.value);

watch(chatLoading, (isLoading, wasLoading) => {
  if (isLoading || !wasLoading) return;
  // 加载完成时重新计算尾部，覆盖同身份消息内容被替换的情况。
  resetHistoryWindow();
  notifyHistoryRendered();
  scrollToLatest();
}, { flush: 'post' });

const editingContentLocal = computed({
  get: () => props.editingContent,
  set: (val) => emit('update:editingContent', val),
});

function formatTokenCount(value: number): string {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return '0';
  if (n >= 1000000) return `${(n / 1000000).toFixed(n >= 10000000 ? 0 : 1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(Math.round(n));
}

const contextTokenLabel = computed(() => {
  const usage = props.contextTokenUsage;
  if (usage) {
    const input = Number(usage.promptTokens ?? 0) || 0;
    const output = Number(usage.completionTokens ?? 0) || 0;
    if (input > 0 || output > 0) {
      const baseLabel = t('components.chatPanel.taskTokenIoLabel', {
        input: formatTokenCount(input),
        output: formatTokenCount(output),
      });
      return baseLabel;
    }
  }
  const total = Number(props.contextTokenCount ?? 0);
  if (!Number.isFinite(total) || total <= 0) return '';
  return t('components.chatPanel.taskTokenLabel', { tokens: formatTokenCount(total) });
});

const contextTokenHint = computed(() => t('components.chatPanel.taskTokenHint'));
const mobileTokenUsageVisible = ref(false);
const mobileTokenButtonRef = ref<HTMLButtonElement | null>(null);
const mobileTokenLayerStyle = ref<CSSProperties>({});
let suppressMobileTokenClickUntil = 0;

function updateMobileTokenPosition(): void {
  if (!mobileTokenUsageVisible.value && !mobileTokenButtonRef.value) return;

  const buttonRect = mobileTokenButtonRef.value?.getBoundingClientRect();
  if (!buttonRect) return;

  const visualViewport = window.visualViewport;
  const viewportLeft = visualViewport?.offsetLeft ?? 0;
  const viewportTop = visualViewport?.offsetTop ?? 0;
  const viewportWidth = visualViewport?.width ?? window.innerWidth;
  const viewportHeight = visualViewport?.height ?? window.innerHeight;
  const edgeGap = 12;
  const anchorGap = 8;
  const panelWidth = Math.min(440, Math.max(0, viewportWidth - edgeGap * 2));
  const desiredCenter = buttonRect.left + buttonRect.width / 2;
  const panelCenter = Math.min(
    viewportLeft + viewportWidth - edgeGap - panelWidth / 2,
    Math.max(viewportLeft + edgeGap + panelWidth / 2, desiredCenter),
  );
  const panelTop = buttonRect.bottom + anchorGap;
  const availableHeight = Math.max(80, viewportTop + viewportHeight - panelTop - edgeGap);

  mobileTokenLayerStyle.value = {
    '--chat-token-panel-left': `${Math.round(panelCenter)}px`,
    '--chat-token-panel-top': `${Math.round(panelTop)}px`,
    '--chat-token-panel-width': `${Math.round(panelWidth)}px`,
    '--chat-token-panel-max-height': `${Math.round(availableHeight)}px`,
  } as CSSProperties;
}

function toggleMobileTokenUsage(): void {
  if (mobileTokenUsageVisible.value) {
    closeMobileTokenUsage();
    return;
  }
  updateMobileTokenPosition();
  mobileTokenUsageVisible.value = true;
  nextTick(updateMobileTokenPosition);
}

function onMobileTokenTouchEnd(): void {
  // 部分移动浏览器/WebView 会吞掉触摸后的合成 click，因此直接在触摸结束时响应。
  // 同时短暂忽略紧随其后的合成 click，避免弹层被连续切换两次。
  suppressMobileTokenClickUntil = Date.now() + 700;
  toggleMobileTokenUsage();
}

function onMobileTokenClick(): void {
  if (Date.now() < suppressMobileTokenClickUntil) return;
  toggleMobileTokenUsage();
}

function closeMobileTokenUsage(): void {
  mobileTokenUsageVisible.value = false;
}

function bindMobileTokenPositionListeners(): void {
  window.addEventListener('resize', updateMobileTokenPosition, { passive: true });
  window.addEventListener('scroll', updateMobileTokenPosition, { passive: true, capture: true });
  window.visualViewport?.addEventListener('resize', updateMobileTokenPosition, { passive: true });
  window.visualViewport?.addEventListener('scroll', updateMobileTokenPosition, { passive: true });
}

function unbindMobileTokenPositionListeners(): void {
  window.removeEventListener('resize', updateMobileTokenPosition);
  window.removeEventListener('scroll', updateMobileTokenPosition, { capture: true });
  window.visualViewport?.removeEventListener('resize', updateMobileTokenPosition);
  window.visualViewport?.removeEventListener('scroll', updateMobileTokenPosition);
}

watch(mobileTokenUsageVisible, (visible) => {
  if (visible) bindMobileTokenPositionListeners();
  else unbindMobileTokenPositionListeners();
}, { flush: 'sync' });

watch([isMobile, contextTokenLabel], ([mobile, label]) => {
  if (!mobile || !label) closeMobileTokenUsage();
}, { flush: 'post' });

/** AgentRadialPicker 选中 Agent 时透传给上层（轮盘自身会自动关闭） */
function onAgentSelected(val: string): void {
  if (!val || val === props.agentId) return;
  pendingPickerAgentId = val;
  agentContentPending.value = true;
  emit('update:agentId', val);
}

/** 轮盘真实离场后才挂载新会话的尾部窗口，避免与收起动画争抢主线程。 */
function onAgentPickerClosed(): void {
  if (!pendingPickerAgentId) return;
  pendingPickerAgentId = '';
  agentContentPending.value = false;
  notifyHistoryRendered();
  scrollToLatest();
}

/** 用户触顶时补一批更早历史，并保持当前内容在视口中的像素位置。 */
function loadOlderHistory(): void {
  const currentStart = visibleStartIndex.value;
  if (currentStart <= 0 || agentContentPending.value) return;
  const nextStart = selectOlderChatWindowStart(props.history, currentStart, OLDER_WINDOW);
  if (nextStart === currentStart) return;

  const list = getChatListElement();
  const previousScrollTop = list?.scrollTop ?? 0;
  const previousScrollHeight = list?.scrollHeight ?? 0;
  hasLoadedOlder.value = true;
  visibleStartIndex.value = nextStart;
  nextTick(() => {
    if (list) {
      list.scrollTop = previousScrollTop + Math.max(0, list.scrollHeight - previousScrollHeight);
    }
    emit('history-rendered', visibleStartIndex.value > 0);
    fillHistoryViewport();
  });
}

onMounted(() => {
  notifyHistoryRendered();
  scrollToLatest();
  const list = getChatListElement();
  if (list && typeof ResizeObserver !== 'undefined') {
    listResizeObserver = new ResizeObserver(fillHistoryViewport);
    listResizeObserver.observe(list);
  }
});

onBeforeUnmount(() => {
  listResizeObserver?.disconnect();
  listResizeObserver = null;
  unbindMobileTokenPositionListeners();
});

// 暴露 ChatMessageList 内部的 DOM listRef，保持与 useChatActions scrollToBottom 兼容
// useChatActions 做 listRef.value?.listRef -> 应得到 DOM element
// chatListRef.value 是 ChatMessageList 组件 ref，chatListRef.value.listRef 是 DOM el
defineExpose({ listRef: chatListRef });
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Header */
.chat-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px 4px;
  border-bottom: 1px solid var(--spark-border);
  cursor: grab;
  user-select: none;
}

.chat-panel-header:active {
  cursor: grabbing;
}

.chat-panel-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.chat-token-chip {
  display: inline-flex;
  align-items: center;
  max-width: 120px;
  height: 22px;
  padding: 0 7px;
  margin-left: 2px;
  border: 1px solid var(--spark-border);
  border-radius: 6px;
  background: transparent;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-2xs);
  font-weight: 650;
  font-family: inherit;
  line-height: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  box-shadow: none;
  touch-action: manipulation;
  -webkit-tap-highlight-color: transparent;
  transition: border-color 0.16s ease, background-color 0.16s ease, color 0.16s ease;
}

.chat-token-chip:hover,
.chat-token-chip:focus-visible {
  border-color: color-mix(in srgb, var(--spark-primary) 55%, var(--spark-border));
  background: color-mix(in srgb, var(--spark-primary) 7%, transparent);
  color: var(--spark-primary);
  outline: none;
}

.chat-token-chip.is-window {
  color: var(--spark-primary);
  border-color: color-mix(in srgb, var(--spark-primary) 38%, var(--spark-border));
}

.chat-token-usage-mobile-layer {
  position: fixed;
  inset: 0;
  z-index: 9000; /* 与聊天全屏浮层统一，必须高于 Naive UI 抽屉的动态层级。 */
  pointer-events: auto;
  background: color-mix(in srgb, #000 7%, transparent);
}

.chat-token-usage-mobile-panel {
  position: fixed;
  top: var(--chat-token-panel-top);
  left: var(--chat-token-panel-left);
  width: var(--chat-token-panel-width);
  max-width: calc(100vw - 24px);
  max-height: var(--chat-token-panel-max-height);
  padding: 12px;
  box-sizing: border-box;
  overflow: auto;
  border: 1px solid var(--spark-border-subtle);
  border-radius: var(--spark-radius-lg);
  background: var(--spark-panel-bg);
  box-shadow: var(--spark-shadow-2xl);
  transform: translateX(-50%);
  transform-origin: top center;
  will-change: transform, opacity;
}

.chat-token-layer-enter-active,
.chat-token-layer-leave-active {
  transition: background-color 0.2s ease;
}

.chat-token-layer-enter-active .chat-token-usage-mobile-panel,
.chat-token-layer-leave-active .chat-token-usage-mobile-panel {
  transition:
    opacity 0.18s ease,
    transform 0.24s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.chat-token-layer-enter-from,
.chat-token-layer-leave-to {
  background: transparent;
}

.chat-token-layer-enter-from .chat-token-usage-mobile-panel,
.chat-token-layer-leave-to .chat-token-usage-mobile-panel {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px) scale(0.97);
}

.chat-token-usage-mobile-panel :deep(.token-usage-panel) {
  width: 100%;
  max-width: 100%;
}

@media (prefers-reduced-motion: reduce) {
  .chat-token-layer-enter-active,
  .chat-token-layer-leave-active,
  .chat-token-layer-enter-active .chat-token-usage-mobile-panel,
  .chat-token-layer-leave-active .chat-token-usage-mobile-panel {
    transition-duration: 0.01ms;
  }
}

.chat-panel-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat-panel-body {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.chat-panel-loading-state {
  position: absolute;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: color-mix(in srgb, var(--spark-bg) 94%, transparent);
  pointer-events: auto;
  user-select: none;
}

.chat-panel-loader-animation {
  width: 140px;
  height: 140px;
}

.chat-panel-loader-animation :deep(.spark-loader-stage) {
  margin-bottom: 0;
}

/* Header Icon */
.chat-header-icon {
  width: 20px;
  height: 20px;
  display: inline-flex;
  color: var(--spark-primary);
  filter: drop-shadow(0 0 4px var(--spark-primary-glow));
  flex-shrink: 0;
}

.chat-header-icon svg {
  width: 100%;
  height: 100%;
}

/* 清空/操作按钮 */
.btn-action-clear {
  min-width: 28px;
  height: var(--n-height-small, 28px) !important;
  color: var(--spark-text-3, rgba(128, 128, 128, 0.7)) !important;
  transition: color 0.2s ease, transform 0.15s ease, box-shadow 0.2s ease;
}

.btn-action-clear:hover {
  color: var(--spark-primary) !important;
}

.btn-action-clear:focus {
  color: var(--spark-primary) !important;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--spark-primary) 30%, transparent);
}

.btn-action-clear:active {
  transform: scale(0.88);
}

/* 输入区：默认 comfort 胶囊（圆角卡 + 呼吸感）；.compact 变体保留旧扁条以兼容抽屉紧凑场景 */
.chat-input-wrapper {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 12px 12px;
  contain: layout style;
  flex: 0 0 auto;
}

.chat-input-wrapper.is-comfort {
  margin: 0 12px 12px;
  padding: 10px 10px 8px 14px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius-lg);
  box-shadow: var(--spark-shadow-sm);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.chat-input-wrapper.is-comfort:focus-within {
  border-color: var(--spark-primary);
  box-shadow: 0 0 0 3px var(--spark-primary-glow), var(--spark-shadow-sm);
}

.chat-input-wrapper.is-compact {
  padding: 8px 12px;
  border-top: 1px solid var(--spark-border);
}

.chat-input-prefix {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.chat-input-wrapper .chat-textarea {
  flex: 1;
  min-width: 0;
}

.chat-input-wrapper .send-btn {
  flex-shrink: 0;
  align-self: flex-end;
}

.spark-send-btn {
  --send-size: 34px;
  width: var(--send-size) !important;
  height: var(--send-size) !important;
  min-width: var(--send-size) !important;
  color: var(--spark-text-inverse) !important;
  border: 1px solid rgba(var(--spark-primary-rgb), 0.3) !important;
  background:
    radial-gradient(circle at 68% 28%, rgba(255, 255, 255, 0.48), transparent 18px),
    linear-gradient(135deg, var(--spark-primary-light), var(--spark-primary)) !important;
  box-shadow:
    0 8px 18px rgba(var(--spark-primary-rgb), 0.22),
    inset 0 0 0 1px rgba(255, 255, 255, 0.2);
  transition:
    transform 0.18s ease,
    box-shadow 0.22s ease,
    background 0.22s ease,
    border-color 0.22s ease;
}

.spark-send-btn:hover {
  transform: translateY(-1px);
  box-shadow:
    0 10px 22px rgba(var(--spark-primary-rgb), 0.28),
    inset 0 0 0 1px rgba(255, 255, 255, 0.22);
}

.spark-send-btn:active {
  transform: translateY(0) scale(0.94);
}

.spark-send-btn.is-working {
  border-color: color-mix(in srgb, var(--spark-primary), #ff4d4f 38%) !important;
  background:
    radial-gradient(circle at 35% 30%, rgba(255, 255, 255, 0.38), transparent 18px),
    linear-gradient(135deg, color-mix(in srgb, var(--spark-primary), #ff4d4f 28%), var(--spark-primary)) !important;
  box-shadow:
    0 0 0 4px rgba(var(--spark-primary-rgb), 0.1),
    0 8px 20px rgba(var(--spark-primary-rgb), 0.28);
}

.send-icon-stage {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
}

.send-glyph {
  grid-area: 1 / 1;
  width: 20px;
  height: 20px;
  overflow: visible;
  color: currentColor;
  transform-origin: center;
  transition: opacity 0.18s ease, transform 0.22s cubic-bezier(0.2, 0, 0.2, 1);
}

.send-glyph--ready {
  opacity: 1;
  transform: rotate(0deg) scale(1);
}

.send-glyph--working {
  opacity: 0;
  transform: rotate(-80deg) scale(0.65);
}

.spark-send-btn.is-working .send-glyph--ready {
  opacity: 0;
  transform: rotate(70deg) scale(0.58);
}

.spark-send-btn.is-working .send-glyph--working {
  opacity: 1;
  transform: rotate(0deg) scale(1);
}

/* ready 态：火花核心 + 上升拖尾，灵感被激发送出 */
.send-spark-core {
  fill: currentColor;
  opacity: 0.96;
  transform-origin: 14.8px 11px;
  animation: sendSparkBreathe 2.6s ease-in-out infinite;
}

.send-spark-mini {
  fill: currentColor;
  opacity: 0.55;
  transform-origin: 19.6px 16.7px;
  animation: sendSparkTwinkle 2.2s ease-in-out infinite;
}

.send-trail {
  fill: currentColor;
  opacity: 0;
  transform-origin: center;
  animation: sendTrailRise 2.4s ease-in-out infinite;
}

.send-trail-2 {
  animation-delay: 0.28s;
}

/* hover 时火花更亮、拖尾节奏加快，呼应"准备点燃" */
.spark-send-btn:hover .send-spark-core {
  animation-duration: 1.6s;
}

.spark-send-btn:hover .send-trail {
  animation-duration: 1.5s;
}

.work-orbit {
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-dasharray: 13 42;
  opacity: 0.72;
  transform-origin: center;
  animation: sendWorkOrbit 1.15s linear infinite;
}

.work-core {
  fill: currentColor;
  rx: 1.5;
  transform-origin: center;
  animation: sendWorkCore 0.9s ease-in-out infinite;
}

.work-spark {
  fill: currentColor;
  opacity: 0.62;
  transform-origin: center;
  animation: sendWorkSpark 1.5s ease-in-out infinite;
}

/* 火花核心：轻微呼吸缩放，像蓄势待发的灵感 */
@keyframes sendSparkBreathe {
  0%, 100% { transform: scale(0.94) rotate(0deg); opacity: 0.9; }
  50% { transform: scale(1.06) rotate(8deg); opacity: 1; }
}

@keyframes sendSparkTwinkle {
  0%, 100% { transform: scale(0.7); opacity: 0.3; }
  50% { transform: scale(1.05); opacity: 0.75; }
}

/* 拖尾光点：从左下向核心方向上升、淡入淡出，暗示"送出" */
@keyframes sendTrailRise {
  0% { transform: translate(0, 0) scale(0.6); opacity: 0; }
  35% { opacity: 0.7; }
  70% { transform: translate(2.4px, -2.4px) scale(1); opacity: 0.35; }
  100% { transform: translate(4px, -4px) scale(0.5); opacity: 0; }
}

@keyframes sendWorkOrbit {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@keyframes sendWorkCore {
  0%, 100% { transform: scale(0.86); opacity: 0.82; }
  50% { transform: scale(1.04); opacity: 1; }
}

@keyframes sendWorkSpark {
  0%, 100% { transform: translate(-1px, 1px) scale(0.9); opacity: 0.38; }
  50% { transform: translate(0, 0) scale(1.12); opacity: 0.75; }
}

/* 移动端输入样式 */
.mobile-input-wrapper {
  width: 100%;
}

.mobile-input-wrapper .chat-textarea {
  flex: 1;
}
</style>
