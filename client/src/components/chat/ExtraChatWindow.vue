<template>
  <Teleport to="body">
    <transition name="chat-float-panel">
      <n-card
        v-if="session.expanded"
        size="small"
        :bordered="true"
        class="extra-chat-window"
        :style="windowStyle"
      >
        <!-- 桌面级八向调整尺寸手柄 -->
        <div class="resize-handle resize-handle--n" @mousedown="startResize($event, 'n')" />
        <div class="resize-handle resize-handle--s" @mousedown="startResize($event, 's')" />
        <div class="resize-handle resize-handle--w" @mousedown="startResize($event, 'w')" />
        <div class="resize-handle resize-handle--e" @mousedown="startResize($event, 'e')" />
        <div class="resize-handle resize-handle--nw" @mousedown="startResize($event, 'nw')" />
        <div class="resize-handle resize-handle--ne" @mousedown="startResize($event, 'ne')" />
        <div class="resize-handle resize-handle--sw" @mousedown="startResize($event, 'sw')" />
        <div class="resize-handle resize-handle--se" @mousedown="startResize($event, 'se')" />

        <ChatPanel
          ref="panelRef"
          :agent-id="session.agentId"
          :agent-options="agentOptions"
          :history="session.history"
          :loading="session.loading"
          :last-error="session.lastError"
          :sending="session.sending"
          :thinking-seconds="actions.thinkingSeconds.value"
          :tool-calling="session.toolCalling"
          :tool-name="session.toolName"
          :tool-progress-text="session.toolProgressText"
          :retry-attempt="session.retryAttempt"
          :retry-mode="session.retryMode"
          :retry-max-retries="session.retryMaxRetries"
          :retry-error-summary="session.retryErrorSummary"
          :context-token-count="session.contextTokenCount"
          :context-token-usage="session.contextTokenUsage"
          :context-window-stats="session.contextWindowStats"
          :loading-target="`chat-session-${session.id}`"
          :editing-message-id="actions.editingMessageId.value"
          :editing-content="actions.editingContent.value"
          :draft="actions.draft.value"
          :placeholder="t('components.chatPanel.inputPlaceholder')"
          @update:agent-id="onAgentChanged"
          @update:draft="actions.draft.value = $event"
          @update:editing-content="actions.editingContent.value = $event"
          @clear="actions.clear"
          @compact-context="compactContext"
          @send="actions.send"
          @stop="actions.stop"
          @draft-keydown="actions.onDraftKeydown"
          @start-edit="actions.startEdit"
          @cancel-edit="actions.cancelEdit"
          @save-edit="actions.saveEdit"
          @edit-keydown="actions.onEditKeydown"
          @delete-msg="actions.deleteMsg"
          @header-mousedown="onHeaderDrag"
          @header-touchstart="onHeaderDrag"
        >
          <template #input-prefix>
            <ChatFileImportButton :session-id="session.id" :agent-id="session.agentId" />
          </template>
          <template #input-model>
            <AiSettingsPanel :visible="true" :compact="true" :agent-name="session.agentId" placement="top-end" trigger="pill" />
          </template>
          <!-- 关闭按钮 -->
          <template #header-right>
            <n-tooltip trigger="hover">
              <template #trigger>
                <n-button quaternary circle size="small" @click="$emit('close')">
                  <template #icon>
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                  </template>
                </n-button>
              </template>
              {{ t('components.chatPanel.closeWindow') }}
            </n-tooltip>
          </template>
        </ChatPanel>
      </n-card>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * ExtraChatWindow.vue - 独立浮动窗口外壳
 * 
 * 职责：
 * 1. 实例容器：作为一个轻量级的“窗口外壳”，为 ChatPanel 提供自由拖拽、缩放和独立的浮动展示。
 * 2. 状态上下文：绑定 chatStore 中的特定 session 数据，维持一个独立的、不随主焦点变化的对话上下文。
 * 3. 独立性：位置不持久化到 localStorage，位置管理相对主窗口更自由。
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch, type PropType } from 'vue';
import { useI18n } from 'vue-i18n';
import { NButton, NCard, NTooltip } from 'naive-ui';
import ChatPanel from '@/components/chat/ChatPanel.vue';
import ChatFileImportButton from '@/components/chat/ChatFileImportButton.vue';
import AiSettingsPanel from '@/components/lorebook/AiSettingsPanel.vue';
import { useChatStore } from '@/components/stores/chatStore';
import { useChatActions } from '@/composables/useChatActions';
import { useResizable } from '@/composables/useResizable';

type AgentOption = {
  label: string;
  value: string;
  [key: string]: unknown;
};

const props = defineProps({
  /** 会话对象（来自 chatStore 的 session） */
  session: { type: Object, required: true },
  /** 可选的 agent 列表（已过滤互斥） */
  agentOptions: { type: Array as PropType<AgentOption[]>, default: () => [] },
  /** 主窗口的 right 定位值 */
  primaryRight: { type: Number, default: 16 },
  /** 主窗口的宽度 */
  primaryWidth: { type: Number, default: 640 },
});

const { t } = useI18n();

const emit = defineEmits(['close', 'agent-changed']);

const chatSession = useChatStore();
const panelRef = ref(null);

// ==================== 使用 useChatActions composable ====================
const listRef = computed(() => panelRef.value?.listRef);
const actions = useChatActions({
  getSending: () => props.session.sending,
  getHistory: () => props.session.history,
  send: (msg) => chatSession.sendSessionMessage(props.session.id, msg),
  stop: () => chatSession.cancelSessionRequest(props.session.id),
  clear: () => chatSession.clearSession(props.session.id),
  editMessage: (id, content) => chatSession.editSessionMessage(props.session.id, id, content),
  deleteMessage: (id) => chatSession.deleteSessionMessage(props.session.id, id),
}, {
  listRef,
  getEditScopeKey: () => `${props.session.agentId || ''}::${props.session.contextKey || ''}`,
});

async function compactContext() {
  if (props.session.sending) return;
  await chatSession.compactSessionContext(props.session.id);
}

// ==================== 使用 useResizable composable ====================
const windowPos = reactive({ right: 0, top: 80 });
const resizable = useResizable({
  pos: windowPos,
  isMobile: ref(false),
  onResizeEnd: null,
});

const { panelSize, startResize } = resizable;

// 初始化位置：在主窗口左侧
onMounted(() => {
  windowPos.right = props.primaryRight + props.primaryWidth + 16;
  windowPos.top = 80;
  panelSize.width = 520;
  panelSize.height = 460;
});

const windowStyle = computed(() => ({
  position: 'fixed',
  right: `${windowPos.right}px`,
  top: `${windowPos.top}px`,
  width: `${panelSize.width}px`,
  height: `${panelSize.height}px`,
  minHeight: '280px',
  zIndex: 1009,
  marginTop: `${resizable.fitOffset.value}px`,
}));

// ==================== 拖拽功能 ====================
const drag = reactive({
  isDragging: false,
  startX: 0,
  startY: 0,
  startRight: 0,
  startTop: 0,
});

function onHeaderDrag(e) {
  if (e.target.closest('button, input, select, textarea, .n-base-select')) return;
  const isTouch = e.type.startsWith('touch');
  if (!isTouch && e.button !== 0) return;
  e.preventDefault();
  
  drag.isDragging = true;
  const clientX = isTouch ? e.touches[0].clientX : e.clientX;
  const clientY = isTouch ? e.touches[0].clientY : e.clientY;
  
  drag.startX = clientX;
  drag.startY = clientY;
  drag.startRight = windowPos.right;
  drag.startTop = windowPos.top;
  
  if (isTouch) {
    document.addEventListener('touchmove', onDragMove, { passive: false });
    document.addEventListener('touchend', stopDrag, { once: true });
  } else {
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', stopDrag, { once: true });
  }
}

function onDragMove(e) {
  if (!drag.isDragging) return;
  const isTouch = e.type.startsWith('touch');
  const clientX = isTouch ? e.touches[0].clientX : e.clientX;
  const clientY = isTouch ? e.touches[0].clientY : e.clientY;
  
  const dx = clientX - drag.startX;
  const dy = clientY - drag.startY;
  
  windowPos.right = drag.startRight - dx;
  windowPos.top = drag.startTop + dy;
}

function stopDrag(e) {
  drag.isDragging = false;
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('touchmove', onDragMove);
}

function onAgentChanged(agentId: string) {
  emit('agent-changed', agentId);
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('touchmove', onDragMove);
});
</script>

<style scoped>
.extra-chat-window {
  border-radius: var(--spark-radius-lg);
  box-shadow: var(--spark-shadow-2xl);
  backdrop-filter: blur(16px);
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border-subtle);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.extra-chat-window :deep(.n-card__content) {
  padding: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 八向调整尺寸手柄样式 */
.resize-handle {
  position: absolute;
  z-index: 15;
  background: transparent;
}

.resize-handle--n {
  top: -4px;
  left: 8px;
  right: 8px;
  height: 8px;
  cursor: ns-resize;
}

.resize-handle--s {
  bottom: -4px;
  left: 8px;
  right: 8px;
  height: 8px;
  cursor: ns-resize;
}

.resize-handle--w {
  left: -4px;
  top: 8px;
  bottom: 8px;
  width: 8px;
  cursor: ew-resize;
}

.resize-handle--e {
  right: -4px;
  top: 8px;
  bottom: 8px;
  width: 8px;
  cursor: ew-resize;
}

.resize-handle--nw {
  top: -4px;
  left: -4px;
  width: 12px;
  height: 12px;
  cursor: nwse-resize;
}

.resize-handle--ne {
  top: -4px;
  right: -4px;
  width: 12px;
  height: 12px;
  cursor: nesw-resize;
}

.resize-handle--sw {
  bottom: -4px;
  left: -4px;
  width: 12px;
  height: 12px;
  cursor: nesw-resize;
}

.resize-handle--se {
  bottom: -4px;
  right: -4px;
  width: 12px;
  height: 12px;
  cursor: nwse-resize;
}
</style>
