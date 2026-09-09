<template>
  <div class="container">
    <HeaderToolbar
      :username="username"
      @open-settings="openSettings"
      @logout="onLogout"
      @open-version-manager="openVersionManager"
    />

    <main>
      <ActivityBar @open-settings="openSettings" />

      <div class="workspace-area">
        <transition name="spark-view" mode="out-in">
          <keep-alive>
            <component :is="activeComponent" :key="viewStore.currentView" :projectId="projectStore.currentProject" />
          </keep-alive>
        </transition>

        <div v-show="viewStore.currentView === 'production'" class="production-shell">
          <div class="production-layout">
            <!-- 左侧作品管理器面板 -->
            <Transition name="sidebar-slide">
              <div v-show="sidebarVisible" class="panel sidebar-panel" :style="{ width: sidebarWidth + 'px' }">
                <div class="sidebar-section file-section">
                  <div class="file-section-header">
                    <span class="file-section-title">{{ t('views.scriptWriter.desktop.workspaceManager') }}</span>
                    <n-dropdown
                      v-if="isNovelWorkspace"
                      trigger="click"
                      :options="submissionExportOptions"
                      :disabled="!projectStore.currentProject || exportingSubmission"
                      @select="handleSubmissionExport"
                    >
                      <n-button
                        class="submission-platform-button"
                        secondary
                        size="small"
                        :loading="exportingSubmission"
                        :disabled="!projectStore.currentProject"
                      >
                        <template #icon>
                          <n-icon>
                            <Send />
                          </n-icon>
                        </template>
                        {{ t('components.novelEditor.submissionExport.button') }}
                      </n-button>
                    </n-dropdown>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <n-button quaternary circle size="tiny" class="sidebar-collapse-btn" @click="toggleSidebar">
                          <template #icon>
                            <n-icon :size="14"><PanelLeftClose /></n-icon>
                          </template>
                        </n-button>
                      </template>
                      {{ t('views.scriptWriter.desktop.collapseSidebar') }}
                    </n-tooltip>
                  </div>
                  <FileTree :key="workspaceMode" />
                </div>
              </div>
            </Transition>

            <div v-show="sidebarVisible" class="resizer" data-resize="sidebar" @mousedown="handleMouseDown"></div>

            <div class="panel center-panel" style="position: relative;">
              <div class="center-panel-header">
                <n-tooltip v-if="!sidebarVisible" trigger="hover">
                  <template #trigger>
                    <n-button quaternary circle size="small" class="sidebar-expand-btn" @click="toggleSidebar">
                      <template #icon>
                        <n-icon :size="16"><PanelLeftOpen /></n-icon>
                      </template>
                    </n-button>
                  </template>
                  {{ t('views.scriptWriter.desktop.expandSidebar') }}
                </n-tooltip>
                <h2 v-if="settingsVisible">{{ t('views.scriptWriter.desktop.settingEditor') }}</h2>
                <h2 v-else-if="!isNovelWorkspace">{{ t('views.scriptWriter.desktop.dialogueTree') }}</h2>
                <h2 v-else>{{ t('views.scriptWriter.desktop.modeNovel') }}</h2>
                <OnboardingHelpButton v-if="!settingsVisible" scene-id="page-production" />
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <button
                      class="memory-toggle-button"
                      :class="{ active: memoryPanelVisible }"
                      @click="toggleMemoryPanel"
                    >
                      <ScrollText :size="14" />
                      <span class="memory-toggle-label">{{ t('components.storyMemoryPanel.title') }}</span>
                      <span v-if="memoryAttentionCount > 0" class="memory-toggle-badge">
                        {{ memoryAttentionCount > 99 ? '99+' : memoryAttentionCount }}
                      </span>
                    </button>
                  </template>
                  {{ t('components.storyMemoryPanel.toggleHint') }}
                </n-tooltip>
              </div>

              <Transition name="workspace-mode" mode="out-in">
                <LorebookEditor v-if="settingsVisible" key="settings" :visible="true" @close="settingsVisible = false" />
                <NovelReader v-else-if="isNovelWorkspace" key="novel-editor" :content="typeof sceneStore.scriptData === 'string' ? sceneStore.scriptData : ''" />
                <DialogueTree v-else key="dialogue-tree" />
              </Transition>

              <GlobalLoading scope="production" />
            </div>

            <!-- 故事记忆停靠面板：编辑器右侧、节点面板左侧；剧本/小说模式共用同一位置 -->
            <template v-if="memoryPanelVisible">
              <div class="resizer" data-resize="memory" @mousedown="handleMouseDown"></div>
              <div class="panel memory-panel" :style="{ width: memoryWidth + 'px' }">
                <StoryMemoryPanel @close="closeMemoryPanel" />
              </div>
            </template>

            <Transition name="inspector-slide">
              <div v-if="showInspectorPanel" class="resizer" data-resize="center" @mousedown="handleMouseDown"></div>
            </Transition>

            <Transition name="inspector-slide">
              <div v-show="showInspectorPanel" class="panel inspector-panel" :style="{ width: inspectorWidth + 'px' }">
                <template v-if="!settingsVisible">
                  <NodeEditor key="node-editor" />
                </template>
                <div v-else class="settings-right-panel">
                  <AiSettingsPanel :visible="true" />
                  <CharacterGeneratorPanel :visible="true" />
                </div>
              </div>
            </Transition>

            <!-- 右侧 Docked 停靠边栏（编剧与工具箱统一收口，单抽屉多标签系统） -->
            <div
              class="chat-dock-container"
              :class="{ 'is-collapsed': !rightDockVisible }"
              :style="{ width: rightDockVisible ? chatSidebarWidth + 'px' : '58px' }"
            >
              <div v-show="rightDockVisible" class="resizer" data-resize="chat-sidebar" @mousedown="handleMouseDown"></div>

              <div class="chat-dock-inner">
                <!-- 展开态：统一多窗口停靠面板（固定物理宽度，由 CSS 纯平滑滑入滑出，绝不挤压抽动） -->
                <div
                  class="panel chat-docked-sidebar"
                  :style="{ width: chatSidebarWidth + 'px' }"
                >
                  <!-- 停靠内容区：直顶到头，双层丝滑平移动画切换 -->
                  <div class="dock-content-body">
                    <!-- 编剧面板 -->
                    <div
                      class="dock-panel-pane"
                      :class="{ 'is-active': activeDockTab === 'scriptwriter', 'is-hidden': activeDockTab !== 'scriptwriter' }"
                    >
                      <ChatPanel
                        ref="dockedChatPanelRef"
                        :agent-id="chat.currentAgentId"
                        :agent-options="agentOptions"
                        :allow-agent-switch-while-sending="true"
                        :history="chat.history"
                        :loading="chat.loading"
                        :last-error="chat.lastError"
                        :sending="chat.sending"
                        :thinking-seconds="thinkingSeconds"
                        :tool-calling="chat.toolCalling"
                        :tool-name="chat.toolName"
                        :tool-progress-text="chat.toolProgressText"
                        :retry-attempt="chat.retryAttempt"
                        :retry-mode="chat.retryMode"
                        :retry-max-retries="chat.retryMaxRetries"
                        :retry-error-summary="chat.retryErrorSummary"
                        :context-token-count="chat.contextTokenCount"
                        :context-token-usage="chat.contextTokenUsage"
                        :context-window-stats="chat.contextWindowStats"
                        loading-target="chat-primary"
                        :editing-message-id="editingMessageId"
                        :editing-content="editingContent"
                        :draft="draft"
                        @update:agent-id="onAgentChanged"
                        @update:draft="draft = $event"
                        @update:editing-content="editingContent = $event"
                        @clear="clear"
                        @compact-context="compactContext"
                        @send="send"
                        @stop="stop"
                        @draft-keydown="onDraftKeydown"
                        @start-edit="startEdit"
                        @cancel-edit="cancelEdit"
                        @save-edit="saveEdit"
                        @edit-keydown="onEditKeydown"
                        @delete-msg="deleteMsg"
                        @retry="retryMsg"
                      >
                        <template #input-prefix>
                          <ChatFileImportButton :session-id="primarySessionId" :agent-id="chat.currentAgentId" />
                        </template>
                        <template #input-model>
                          <AiSettingsPanel :visible="true" :compact="true" :agent-name="chat.currentAgentId" placement="top-end" trigger="pill" />
                        </template>

                        <!-- 标题栏右侧：编剧与工具箱切换胶囊 + 工作区打开 + 收起按钮 -->
                        <template #header-right>
                          <div class="dock-header-tab-switch">
                            <button
                              type="button"
                              class="dock-header-tab-btn"
                              :class="{ 'is-active': activeDockTab === 'scriptwriter' }"
                              @click="switchDockTab('scriptwriter')"
                              :title="t('views.scriptWriter.desktop.aiAssistant')"
                            >
                              <n-icon :component="Feather" :size="13" />
                              <span>{{ t('views.scriptWriter.desktop.aiAssistant') }}</span>
                              <span
                                v-if="chat.sending || chat.toolCalling"
                                class="dock-tab-pulse-dot"
                              ></span>
                            </button>
                            <button
                              type="button"
                              class="dock-header-tab-btn"
                              :class="{ 'is-active': activeDockTab === 'toolbox' }"
                              @click="switchDockTab('toolbox')"
                              :title="t('nodeEditor.presentation.toolboxTitle')"
                            >
                              <n-icon :component="Wrench" :size="13" />
                              <span>{{ t('nodeEditor.presentation.toolboxTitle') }}</span>
                            </button>
                          </div>

                          <!-- 在工作区打开按钮（仅编剧） -->
                          <n-tooltip trigger="hover">
                            <template #trigger>
                              <n-button size="tiny" @click="openInWorkspace" class="btn-action-clear" circle quaternary>
                                <template #icon>
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
                                  </svg>
                                </template>
                              </n-button>
                            </template>
                            {{ t('components.chatPanel.openInWorkspace') }}
                          </n-tooltip>

                          <!-- 统一收起右侧 Dock 按钮 -->
                          <n-tooltip trigger="hover">
                            <template #trigger>
                              <n-button quaternary circle size="small" @click="toggleRightDock">
                                <template #icon>
                                  <n-icon :size="16"><PanelRightClose /></n-icon>
                                </template>
                              </n-button>
                            </template>
                            {{ t('views.scriptWriter.desktop.collapseChatSidebar') }}
                          </n-tooltip>
                        </template>
                      </ChatPanel>
                    </div>

                    <!-- 工具箱面板 -->
                    <div
                      class="dock-panel-pane"
                      :class="{ 'is-active': activeDockTab === 'toolbox', 'is-hidden': activeDockTab !== 'toolbox' }"
                    >
                      <AiPanel @close="toggleRightDock">
                        <template #header-extra>
                          <div class="dock-header-tab-switch">
                            <button
                              type="button"
                              class="dock-header-tab-btn"
                              :class="{ 'is-active': activeDockTab === 'scriptwriter' }"
                              @click="switchDockTab('scriptwriter')"
                              :title="t('views.scriptWriter.desktop.aiAssistant')"
                            >
                              <n-icon :component="Feather" :size="13" />
                              <span>{{ t('views.scriptWriter.desktop.aiAssistant') }}</span>
                              <span
                                v-if="chat.sending || chat.toolCalling"
                                class="dock-tab-pulse-dot"
                              ></span>
                            </button>
                            <button
                              type="button"
                              class="dock-header-tab-btn"
                              :class="{ 'is-active': activeDockTab === 'toolbox' }"
                              @click="switchDockTab('toolbox')"
                              :title="t('nodeEditor.presentation.toolboxTitle')"
                            >
                              <n-icon :component="Wrench" :size="13" />
                              <span>{{ t('nodeEditor.presentation.toolboxTitle') }}</span>
                            </button>
                          </div>
                        </template>
                      </AiPanel>
                    </div>
                  </div>
                </div>

                <!-- 折叠态：右侧 58px IDE 风格扁平图标 Dock（扩大为 1.3 倍，精简纯粹） -->
                <div class="chat-docked-collapsed-strip">
                    <!-- 正方形应用图标栈（体积放大 1.3 倍） -->
                    <div class="dock-square-buttons">
                      <!-- 【编剧】正方形按钮 -->
                      <n-tooltip trigger="hover" placement="left">
                        <template #trigger>
                          <button
                            type="button"
                            class="dock-square-btn"
                            :class="{
                              'is-busy': chat.sending || chat.toolCalling,
                              'is-active': activeDockTab === 'scriptwriter'
                            }"
                            @click="selectDockTab('scriptwriter')"
                          >
                            <n-icon :component="Feather" :size="28" />
                            <!-- 状态脉冲指示灯 -->
                            <span class="square-badge-indicator" :class="{ 'is-busy': chat.sending || chat.toolCalling }"></span>
                          </button>
                        </template>
                        {{ t('views.scriptWriter.desktop.aiAssistant') }}
                      </n-tooltip>

                      <!-- 【工具箱】正方形按钮 -->
                      <n-tooltip trigger="hover" placement="left">
                        <template #trigger>
                          <button
                            type="button"
                            class="dock-square-btn"
                            :class="{
                              'is-active': activeDockTab === 'toolbox'
                            }"
                            @click="selectDockTab('toolbox')"
                          >
                            <n-icon :component="Wrench" :size="28" />
                          </button>
                        </template>
                        {{ t('nodeEditor.presentation.toolboxTitle') }}
                      </n-tooltip>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

      <n-modal v-model:show="versionManagerVisible" preset="card" :title="t('views.scriptWriter.desktop.versionManager')" style="width: 800px; max-height: 90vh;">
        <VersionManager :projectId="projectStore.currentProject || undefined" :content-format="workspaceMode" hide-title />
      </n-modal>

      <GlobalChatFloat />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { NButton, NDropdown, NIcon, NModal, NTooltip, type DropdownOption } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { useOnboarding } from '../../onboarding';
import VersionManager from '../../components/dlg-editor/VersionManager.vue';
import HeaderToolbar from '../../components/layouts/desktop/HeaderToolbar.vue';
import FileTree from '../../components/file-explorer/FileTree.vue';
import BlueprintView from '../Blueprint/BlueprintIndex.vue';
import DialogueTree from '../../components/dlg-editor/DialogueTree.vue';
import NovelReader from '../../components/dlg-editor/NovelReader.vue';
import NodeEditor from '../../components/dlg-editor/NodeEditor.vue';
import AiPanel from '../../components/dlg-editor/AiPanel.vue';
import StoryMemoryPanel from '../../components/dlg-editor/StoryMemoryPanel.vue';
import LorebookEditor from '../../components/lorebook/LorebookEditor.vue';
import AiSettingsPanel from '../../components/lorebook/AiSettingsPanel.vue';
import CharacterGeneratorPanel from '../../components/lorebook/CharacterGeneratorPanel.vue';
import GlobalLoading from '../../components/share/GlobalLoading.vue';
import GlobalChatFloat from '../../components/chat/GlobalChatFloat.vue';
import ChatPanel from '../../components/chat/ChatPanel.vue';
import ChatFileImportButton from '../../components/chat/ChatFileImportButton.vue';
import ActivityBar from '../../components/layouts/desktop/ActivityBar.vue';
import OnboardingHelpButton from '../../onboarding/components/OnboardingHelpButton.vue';

// 这里的 View 引用改为新的分发器路径
import WorldView from '../World/WorldIndex.vue';
import CharactersView from '../Characters/CharactersIndex.vue';
import SynopsisView from '../Synopsis/SynopsisIndex.vue';
import StructureView from '../Structure/StructureIndex.vue';
import StyleView from '../Style/StyleIndex.vue';
import EngineView from '../Engine/EngineIndex.vue';
import SettingsView from '../Settings/SettingsIndex.vue';
import DashboardView from '../Dashboard/DashboardIndex.vue';
import ChatDesktopView from '../ChatDesktop/ChatDesktopIndex.vue';

import bus from '../../eventBus';
import { useResizer } from '../../hooks/useResizer';
import { useScriptWriterLogic } from '../../composables/useScriptWriterLogic';
import { useSceneStore } from '../../components/stores/sceneStore';
import { useChatStore } from '../../components/stores/chatStore';
import { useStoryMemoryStore } from '../../components/stores/storyMemoryStore';
import { useChatActions } from '../../composables/useChatActions';
import { useAgentRegistry } from '../../composables/useAgentRegistry';
import { shouldShowProductionInspector } from './productionInspector';
import { Send, PanelRightClose, PanelLeftClose, PanelLeftOpen, ChevronLeft, ScrollText, Wrench, Feather } from '@lucide/vue';
import {
  NOVEL_SUBMISSION_PLATFORMS,
  downloadNovelSubmissionExport,
  type NovelSubmissionPlatform,
} from '../../services/storyService';

const { t } = useI18n();
const sceneStore = useSceneStore();
const { sidebarWidth, inspectorWidth, aiSidebarWidth, chatSidebarWidth, memoryWidth, handleMouseDown } = useResizer();
const {
  viewStore,
  projectStore,
  username,
  settingsVisible,
  versionManagerVisible,
  aiSidebarVisible,
  openSettings,
  onLogout
} = useScriptWriterLogic();

// ==================== 右侧智能编剧对话边栏 ====================
const chat = useChatStore();
const { registry: agentRegistry, load: loadAgentRegistry } = useAgentRegistry();
const dockedChatPanelRef = ref<any>(null);
const dockedListRef = computed(() => dockedChatPanelRef.value?.listRef);

const chatActions = useChatActions({
  getSending: () => chat.sending,
  getHistory: () => chat.history,
  send: (msg) => chat.send(msg),
  stop: () => chat.cancel(),
  clear: () => chat.clear(),
  editMessage: (id, content) => chat.editMessage(id, content),
  deleteMessage: (id) => chat.deleteMessage(id),
}, {
  listRef: dockedListRef,
  getEditScopeKey: () => `${chat.currentAgentId || ''}::${chat.contextKey || ''}`,
});

const {
  draft, editingMessageId, editingContent, thinkingSeconds,
  onDraftKeydown, send, stop, startEdit, cancelEdit,
  onEditKeydown, saveEdit, deleteMsg, retryMsg, scrollToBottom
} = chatActions;

async function clear() {
  await chatActions.clear();
}

async function compactContext() {
  if (chat.sending) return;
  await chat.compactContext();
}

const primarySessionId = computed(() => chat.primarySession?.id ?? null);

const agentOptions = computed(() =>
  agentRegistry.value
    .filter(a => a.visibleInChat !== false)
    .map(a => ({
      label: a.name || a.key,
      value: a.key,
    }))
);

function onAgentChanged(agentId: string) {
  chat.setAgent(agentId);
}

function openInWorkspace() {
  viewStore.openChatView(chat.currentAgentId);
}

// ==================== 右侧 Dock（编剧 / 工具箱 多窗口） ====================
type RightDockTab = 'scriptwriter' | 'toolbox';

const RIGHT_DOCK_VISIBLE_KEY = 'spark_right_dock_visible_v2';
const RIGHT_DOCK_TAB_KEY = 'spark_right_dock_tab_v2';

const rightDockVisible = ref(true);
const activeDockTab = ref<RightDockTab>('scriptwriter');

function loadRightDockState() {
  try {
    const savedVisible = localStorage.getItem(RIGHT_DOCK_VISIBLE_KEY);
    if (savedVisible !== null) {
      rightDockVisible.value = savedVisible === 'true';
    }
    const savedTab = localStorage.getItem(RIGHT_DOCK_TAB_KEY) as RightDockTab | null;
    if (savedTab === 'scriptwriter' || savedTab === 'toolbox') {
      activeDockTab.value = savedTab;
    }
  } catch {}
}

function selectDockTab(tab: RightDockTab) {
  if (!rightDockVisible.value) {
    rightDockVisible.value = true;
    activeDockTab.value = tab;
  } else if (activeDockTab.value === tab) {
    rightDockVisible.value = false;
  } else {
    activeDockTab.value = tab;
  }
  persistRightDockState();
  if (rightDockVisible.value && activeDockTab.value === 'scriptwriter') {
    nextTick(() => scrollToBottom(true));
  }
}

function switchDockTab(tab: RightDockTab) {
  activeDockTab.value = tab;
  rightDockVisible.value = true;
  persistRightDockState();
  if (tab === 'scriptwriter') {
    nextTick(() => scrollToBottom(true));
  }
}

function toggleRightDock() {
  rightDockVisible.value = !rightDockVisible.value;
  persistRightDockState();
  if (rightDockVisible.value && activeDockTab.value === 'scriptwriter') {
    nextTick(() => scrollToBottom(true));
  }
}

function persistRightDockState() {
  try {
    localStorage.setItem(RIGHT_DOCK_VISIBLE_KEY, String(rightDockVisible.value));
    localStorage.setItem(RIGHT_DOCK_TAB_KEY, activeDockTab.value);
  } catch {}
}

// ==================== 左侧作品管理边栏收展 ====================
const SIDEBAR_STORAGE_KEY = 'spark_sidebar_visible_v1';
const sidebarVisible = ref(true);

function loadSidebarVisible() {
  try {
    const saved = localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (saved !== null) {
      sidebarVisible.value = saved === 'true';
    }
  } catch {}
}

function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value;
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, String(sidebarVisible.value));
  } catch {}
}

// ==================== 故事记忆停靠面板 ====================
const MEMORY_PANEL_STORAGE_KEY = 'spark_memory_panel_visible_v1';
const storyMemoryStore = useStoryMemoryStore();
const memoryPanelVisible = ref(false);

const memoryAttentionCount = computed(() => storyMemoryStore.attentionCount);

function loadMemoryPanelVisible() {
  try {
    const saved = localStorage.getItem(MEMORY_PANEL_STORAGE_KEY);
    if (saved !== null) {
      memoryPanelVisible.value = saved === 'true';
    }
  } catch {}
}

function persistMemoryPanelVisible() {
  try {
    localStorage.setItem(MEMORY_PANEL_STORAGE_KEY, String(memoryPanelVisible.value));
  } catch {}
}

function toggleMemoryPanel() {
  memoryPanelVisible.value = !memoryPanelVisible.value;
  persistMemoryPanelVisible();
  if (memoryPanelVisible.value && projectStore.currentProject) {
    void storyMemoryStore.fetch(projectStore.currentProject);
  }
}

function closeMemoryPanel() {
  memoryPanelVisible.value = false;
  persistMemoryPanelVisible();
}

// 进入写作视图时拉取一次总览，驱动标题栏角标显示待处理风险/工单数
watch(
  () => viewStore.currentView,
  (view) => {
    if (view === 'production' && projectStore.currentProject) {
      void storyMemoryStore.fetch(projectStore.currentProject);
    }
  },
  { immediate: true },
);

// 首次进入桌面工作台时触发引导（等待登录后检查完成）
const { triggerIfFirst } = useOnboarding();
const onPostLoginReady = () => {
  nextTick(() => triggerIfFirst('desktop-workspace'));
};
const onToggleAiToolboxEvent = () => selectDockTab('toolbox');

onMounted(() => {
  loadSidebarVisible();
  loadRightDockState();
  loadMemoryPanelVisible();
  loadAgentRegistry();
  bus.on('toggle-ai-toolbox', onToggleAiToolboxEvent);
  bus.on('post-login-ready', onPostLoginReady);
  // 如果 App.vue 已经发过 post-login-ready（竞态：子组件晚于 App mount），直接触发
  if ((bus as any).postLoginReadySent) onPostLoginReady();
});
onUnmounted(() => {
  bus.off('toggle-ai-toolbox', onToggleAiToolboxEvent);
  bus.off('post-login-ready', onPostLoginReady);
});

function openVersionManager() {
  versionManagerVisible.value = true;
}

const workspaceMode = computed(() => sceneStore.workspaceMode || 'script');

const isNovelWorkspace = computed(() => workspaceMode.value === 'novel');
const showInspectorPanel = computed(() => shouldShowProductionInspector({
  isNovelWorkspace: isNovelWorkspace.value,
  settingsVisible: settingsVisible.value,
  hasOpenScriptFile: !!sceneStore.currentFilePath,
  hasCurrentScene: !!sceneStore.currentScene,
  selectionType: sceneStore.selectionType,
}));
const exportingSubmission = ref(false);
const submissionExportOptions = computed<DropdownOption[]>(() => (
  NOVEL_SUBMISSION_PLATFORMS.map(platform => ({
    key: platform,
    label: t(`components.novelEditor.submissionExport.platforms.${platform}`),
    icon: () => h(NIcon, null, { default: () => h(Send) }),
  }))
));

async function handleSubmissionExport(key: string | number) {
  if (exportingSubmission.value) return;
  if (!projectStore.currentProject) {
    bus.emit('toast', { type: 'warning', message: t('components.novelEditor.submissionExport.noProject') });
    return;
  }

  exportingSubmission.value = true;
  try {
    if (sceneStore.workspaceMode === 'novel') {
      await sceneStore._saveStory();
    }
    await downloadNovelSubmissionExport(projectStore.currentProject, key as NovelSubmissionPlatform);
    bus.emit('toast', { type: 'success', message: t('components.novelEditor.submissionExport.success') });
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    bus.emit('toast', {
      type: 'error',
      message: `${t('components.novelEditor.submissionExport.failed')}: ${errorMessage}`,
    });
  } finally {
    exportingSubmission.value = false;
  }
}

const activeComponent = computed(() => {
  switch (viewStore.currentView) {
    case 'world': return WorldView;
    case 'characters': return CharactersView;
    case 'synopsis': return SynopsisView;
    case 'structure': return StructureView;
    case 'style': return StyleView;
    case 'engine': return EngineView;
    case 'blueprint': return BlueprintView;
    case 'settings': return SettingsView;
    case 'dashboard': return DashboardView;
    case 'chat': return ChatDesktopView;
    default: return null;
  }
});
</script>

<style scoped>
.container {
  height: 100vh;
  width: 100%;
  max-width: none;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.workspace-area {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
  position: relative;
}

.production-shell {
  display: flex;
  flex-direction: column;
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  background: var(--spark-bg);
}

.production-layout {
  display: flex;
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.sidebar-panel {
  width: 250px;
  min-width: 0;
  max-width: 400px;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  background-color: var(--n-color-modal);
  border-right: 1px solid var(--n-border-color);
}

.sidebar-section {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.file-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 8px 8px 16px;
  background: var(--spark-panel-header-bg);
  border-bottom: 1px solid var(--spark-border);
  flex-shrink: 0;
}

.file-section-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--spark-fs-base);
  font-weight: 600;
  color: var(--spark-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.submission-platform-button {
  flex: 0 1 auto;
  max-width: 100%;
}

.submission-platform-button :deep(.n-button__content) {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.sidebar-divider {
  height: 1px;
  background-color: var(--n-border-color);
  margin: 4px 0;
}

.center-panel {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background-color: var(--n-color);
}

.center-panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--spark-panel-header-bg);
  border-bottom: 1px solid var(--spark-border);
  flex-shrink: 0;
  position: relative;
}

.inspector-panel {
  width: 300px;
  min-width: 0;
  max-width: 600px;
  flex: 0 0 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background-color: var(--n-color-modal);
  border-left: 1px solid var(--n-border-color);
}

.memory-toggle-button {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  background: transparent;
  /* 前景可见性：先兜底用已验证可用的主题令牌，再用 color-mix 渐进增强（任一失效自动落到上一行） */
  color: var(--spark-text-muted);
  color: color-mix(in srgb, var(--spark-text), var(--spark-bg) 18%);
  font-size: var(--spark-fs-xs, 12px);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.memory-toggle-button:hover,
.memory-toggle-button.active {
  border-color: var(--spark-primary-muted, var(--spark-primary));
  color: var(--spark-primary);
}

.memory-toggle-button.active {
  background: color-mix(in srgb, var(--spark-primary) 12%, transparent);
}

.memory-toggle-label {
  white-space: nowrap;
}

.memory-toggle-badge {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: var(--spark-warning);
  color: var(--spark-text-inverse, #fff);
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.memory-panel {
  width: 320px;
  min-width: 0;
  max-width: 460px;
  flex: 0 0 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: var(--n-color-modal);
  border-left: 1px solid var(--n-border-color);
}

.memory-panel :deep(.story-memory-panel) {
  flex: 1 1 auto;
  min-height: 0;
}

.ai-sidebar {
  width: 350px;
  min-width: 0;
  max-width: 800px;
  flex: 0 0 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: var(--n-color-modal);
  border-left: 1px solid var(--n-border-color);
}

/* 编剧与工具箱右侧 Dock 停靠边栏 */
.chat-dock-container {
  height: 100%;
  display: flex;
  flex-direction: row;
  flex-shrink: 0;
  position: relative;
  transition: width 0.28s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: width;
  overflow: hidden;
}

.chat-dock-inner {
  flex: 1;
  min-width: 0;
  height: 100%;
  position: relative;
  display: flex;
  overflow: hidden;
}

.chat-docked-sidebar {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: var(--n-color-modal);
  border-left: 1px solid var(--n-border-color);
  z-index: 2;
  transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1),
              opacity 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: transform, opacity;
}

.chat-dock-container.is-collapsed .chat-docked-sidebar {
  transform: translateX(100%);
  opacity: 0;
  pointer-events: none;
}

/* 标题栏右侧 Tab 切换胶囊：极度紧凑，节省垂直纵向空间，让面板直顶到头 */
.dock-header-tab-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: var(--spark-input-bg, rgba(0, 0, 0, 0.04));
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--spark-border);
  margin-right: 4px;
}

.dock-header-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  font-size: var(--spark-fs-2xs, 11px);
  font-weight: 500;
  color: var(--spark-text-muted);
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  line-height: 1;
  position: relative;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.dock-header-tab-btn:hover {
  color: var(--spark-text);
}

.dock-header-tab-btn.is-active {
  color: var(--spark-primary);
  background: var(--n-color-modal, #fff);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.dock-tab-pulse-dot {
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: var(--spark-primary);
  box-shadow: 0 0 6px var(--spark-primary);
  animation: writer-pulse 1.8s cubic-bezier(0.24, 0, 0.38, 1) infinite;
}

.dock-content-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}

/* 编剧与工具箱双面板平滑层叠切换动画（状态不销毁，带微位移与模糊对焦） */
.dock-panel-pane {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: opacity 0.22s cubic-bezier(0.16, 1, 0.3, 1),
              transform 0.22s cubic-bezier(0.16, 1, 0.3, 1),
              filter 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: opacity, transform, filter;
}

.dock-panel-pane.is-hidden {
  opacity: 0;
  transform: translateY(8px) scale(0.985);
  filter: blur(2px);
  pointer-events: none;
  visibility: hidden;
}

.dock-panel-pane.is-active {
  opacity: 1;
  transform: translateY(0) scale(1);
  filter: blur(0);
  pointer-events: auto;
  visibility: visible;
}

.dock-panel-pane :deep(.n-card) {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.dock-panel-pane :deep(.n-card__content) {
  flex: 1;
  overflow-y: auto;
}

/* 58px IDE 风格扁平图标 Dock（折叠态：按钮扩大1.3倍至 50px，移除了多余文字和箭头） */
.chat-docked-collapsed-strip {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  width: 58px;
  height: 100%;
  background: var(--spark-panel-header-bg, var(--n-color-modal));
  border-left: 1px solid var(--spark-border);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 10px 0 16px;
  overflow: hidden;
  user-select: none;
  box-shadow: none;
  z-index: 1;
  transition: opacity 0.22s ease, transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: opacity, transform;
}

.chat-dock-container:not(.is-collapsed) .chat-docked-collapsed-strip {
  opacity: 0;
  pointer-events: none;
  transform: scale(0.94);
}

.chat-dock-container.is-collapsed .chat-docked-collapsed-strip {
  opacity: 1;
  pointer-events: auto;
  transform: scale(1);
}

.dock-square-buttons {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 100%;
}

/* IDE 风格扁平正方形按钮：50x50px（体积扩大1.3倍），高空间利用率，无浮雕悬浮卡片感 */
.dock-square-btn {
  width: 50px;
  height: 50px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--spark-text-muted);
  cursor: pointer;
  position: relative;
  box-shadow: none;
  outline: none;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.dock-square-btn:hover {
  color: var(--spark-text);
  background: color-mix(in srgb, var(--spark-text) 8%, transparent);
  box-shadow: none;
}

.dock-square-btn.is-active {
  color: var(--spark-primary);
  background: color-mix(in srgb, var(--spark-primary) 12%, transparent);
}

.square-badge-indicator {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--spark-text-muted);
  opacity: 0.5;
  transition: all 0.25s ease;
}

.square-badge-indicator.is-busy {
  background: var(--spark-primary);
  opacity: 1;
  box-shadow: 0 0 6px var(--spark-primary);
  animation: writer-pulse 1.8s cubic-bezier(0.24, 0, 0.38, 1) infinite;
}

@keyframes writer-pulse {
  0% { transform: scale(0.6); opacity: 0.8; }
  100% { transform: scale(1.8); opacity: 0; }
}

/* 左侧边栏平滑折叠 */
.sidebar-slide-enter-active,
.sidebar-slide-leave-active {
  transition: opacity 0.25s cubic-bezier(0.16, 1, 0.3, 1), transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
}
.sidebar-slide-enter-from,
.sidebar-slide-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}

.sidebar-collapse-btn {
  color: var(--spark-text-muted);
  transition: color 0.2s ease;
}
.sidebar-collapse-btn:hover {
  color: var(--spark-primary);
}

.sidebar-expand-btn {
  margin-right: 4px;
  color: var(--spark-text-muted);
  transition: color 0.2s ease;
}
.sidebar-expand-btn:hover {
  color: var(--spark-primary);
}

.settings-right-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  min-height: 0;
  overflow: auto;
  padding: 12px;
}

.resizer {
  width: 4px;
  flex-shrink: 0;
  background: transparent;
  cursor: col-resize;
  z-index: 10;
  transition: background 0.2s;
}

.resizer:hover {
  background: var(--spark-primary);
}

h2 {
  font-size: var(--spark-fs-base);
  padding: 0;
  margin: 0;
}

.workspace-mode-enter-from,
.workspace-mode-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.995);
}
.workspace-mode-enter-active,
.workspace-mode-leave-active {
  transition: opacity 0.22s ease, transform 0.22s cubic-bezier(.4,0,.2,1);
}
.workspace-mode-enter-to,
.workspace-mode-leave-from {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.inspector-slide-enter-from {
  opacity: 0;
  transform: translateX(18px);
}
.inspector-slide-leave-to {
  opacity: 0;
  transform: translateX(18px);
}
.inspector-slide-enter-active,
.inspector-slide-leave-active {
  transition: opacity 0.24s ease, transform 0.24s cubic-bezier(.4,0,.2,1);
  overflow: hidden;
}
.inspector-slide-enter-to,
.inspector-slide-leave-from {
  opacity: 1;
  transform: translateX(0);
}

@media (max-width: 1520px) {
  .resizer {
    width: 3px;
  }
}

@media (max-width: 1280px) {
  h2 {
    font-size: var(--spark-fs-sm);
    padding: 8px 12px;
  }

  .sidebar-panel {
    max-width: 260px;
  }

  .inspector-panel {
    max-width: 320px;
  }

  .ai-sidebar {
    max-width: 340px;
  }

  .chat-docked-sidebar {
    max-width: 400px;
  }

  .settings-right-panel {
    padding: 10px;
    gap: 12px;
  }
}
</style>
