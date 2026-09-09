<template>
  <!-- 空态欢迎：即创作首页。火花 + 问候 + 项目胶囊 + 大输入 + 建议片 + 灵感匣 + 最近两列 + 引导。
    欢迎页输入框直接驱动聊天页 draft，回车/发送即经统一收口发送，无视图跳转、无残影动画。 -->
  <div class="chat-welcome">
    <div class="welcome-min">
      <SparkLoaderAnimation class="welcome-spark-anim" aria-hidden="true" />
      <h2 class="welcome-title">{{ greetWord }}</h2>
      <p class="welcome-tagline">{{ t('views.home.tagline') }}</p>

      <!-- 当前项目胶囊 + 新建 -->
      <div class="welcome-proj-row">
        <div class="welcome-proj-pill" :class="{ 'is-warn': !projectStore.currentProject }">
          <span class="welcome-proj-dot"></span>
          <span v-if="projectStore.currentProject">{{ projectStore.currentProject }} · {{ workspaceModeLabel }}</span>
          <span v-else>{{ t('views.home.noProjectHint') }}</span>
        </div>
        <button class="welcome-link-btn" @click="projectStore.createProject()">{{ t('views.home.newProject') }}</button>
      </div>

      <!-- 中央大输入：直接驱动聊天页 draft，回车即发送 -->
      <div class="welcome-composer">
        <n-input
          :value="draft"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 6 }"
          :placeholder="placeholder"
          class="welcome-textarea"
          @update:value="draft = $event"
          @keydown.enter.exact.prevent="handleSend"
        />
        <div class="welcome-composer-foot">
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button quaternary circle size="small" :disabled="!projectStore.currentProject" @click="toastAttachmentHint">
                <template #icon><n-icon :component="Plus" /></template>
              </n-button>
            </template>
            {{ t('components.chatPanel.attachFile') }}
          </n-tooltip>
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button quaternary circle size="small" class="welcome-save-muse" :loading="savingMuse" @click="saveAsMuse">
                <template #icon><n-icon :component="Sparkles" /></template>
              </n-button>
            </template>
            {{ t('views.home.saveMuseTitle') }}
          </n-tooltip>
          <span class="welcome-foot-hint">{{ t('views.home.sendHint') }}</span>
          <n-button circle type="primary" class="welcome-send-btn" :loading="sending" @click="handleSend">
            <template #icon><n-icon :component="ArrowUp" /></template>
          </n-button>
        </div>
      </div>

      <!-- 最近两列：项目 + 灵感草稿 -->
      <div class="welcome-recents">
        <section class="welcome-recent-card">
          <header>
            <span>{{ t('views.home.recentProjects') }}</span>
            <button @click="toastAllProjects">{{ t('views.home.viewAll') }}</button>
          </header>
          <div
            v-for="name in recentProjects"
            :key="name"
            class="welcome-rrow"
            :class="{ 'is-active': name === projectStore.currentProject }"
            @click="openProject(name)"
          >
            <span class="welcome-rrow-cover" :class="name === projectStore.currentProject ? 'is-active' : ''">
              <n-icon :component="projectModeIcon(name)" />
            </span>
            <span class="welcome-rrow-text">
              <span class="welcome-rrow-title">{{ name }}</span>
              <span class="welcome-rrow-meta">{{ projectModeLabel(name) }}</span>
            </span>
            <span class="welcome-rrow-go">›</span>
          </div>
          <n-empty v-if="recentProjects.length === 0" size="small" :description="t('views.home.noProjectHint')" />
        </section>

        <section class="welcome-recent-card">
          <header>
            <span>{{ t('views.home.recentMuses') }}</span>
            <button @click="goMuseWorkshop">{{ t('views.home.viewAll') }}</button>
          </header>
          <div v-for="item in recentMuses" :key="item.id" class="welcome-mrow" @click="adoptMuse(item)">
            <div class="welcome-mrow-title">✦ {{ museTitle(item) }}<span class="welcome-mrow-tag">{{ t('views.world.history.drafts') }}</span></div>
            <div class="welcome-mrow-desc">{{ museDesc(item) }}</div>
          </div>
          <n-empty v-if="recentMuses.length === 0" size="small" :description="t('views.world.history.emptyDrafts')" />
        </section>
      </div>

      <!-- 引导：快速开始 + 字字斟酌（原欢迎页文案，扁平无浮雕） -->
      <div class="welcome-tips">
        <div class="tip-section">
          <div class="tip-section-header">
            <span class="tip-section-title">{{ t('components.chatWelcome.quickStart') }}</span>
          </div>
          <ul class="tip-list">
            <li v-for="(tip, i) in quickStartTips" :key="i" class="tip-item">
              <span class="tip-num">{{ i + 1 }}</span>
              <span class="tip-text">{{ tip }}</span>
            </li>
          </ul>
        </div>

        <div class="tip-section">
          <div class="tip-section-header">
            <span class="tip-section-title">{{ t('components.chatWelcome.proWorkflow') }}</span>
          </div>
          <p class="tip-section-hint">{{ t('components.chatWelcome.proWorkflowHint') }}</p>
          <ul class="tip-list">
            <li v-for="(tip, i) in proTips" :key="i" class="tip-item">
              <span class="tip-num">{{ i + 1 }}</span>
              <span class="tip-text">{{ tip.text }}</span>
              <button class="tip-goto" :title="t('components.chatWelcome.goToPage')" @click.stop="goToView(tip.view)">→</button>
            </li>
          </ul>
        </div>
      </div>

      <div class="welcome-footer">
        <span class="footer-hint">{{ t('components.chatWelcome.footerHint') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { NButton, NEmpty, NIcon, NInput, NTooltip } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { ArrowUp, BookOpen, Clapperboard, Plus, Sparkles } from '@lucide/vue';
import SparkLoaderAnimation from '@/components/share/SparkLoaderAnimation.vue';
import { useHomeLogic } from '@/composables/useHomeLogic';
import { useProjectStore } from '@/components/stores/projectStore';
import { useViewStore, type AppViewKey } from '@/components/stores/viewStore';
import { bindInspiration } from '@/services/storyService';
import type { InspirationEntry } from '@/services/aiContracts';
import bus from '@/eventBus';

const { t } = useI18n();
const projectStore = useProjectStore();
const viewStore = useViewStore();

const emit = defineEmits<{
  /** 请求聊天页发送：携带欢迎页输入文本（父级写入其 draft 后经统一收口发送） */
  (e: 'send', text: string): void;
}>();

const {
  draft,
  savingMuse,
  sending,
  greetWord,
  placeholder,
  recentProjects,
  recentMuses,
  openProject,
  saveAsMuse,
  beginSendToDirector,
} = useHomeLogic();

const workspaceModeLabel = computed(() =>
  projectStore.currentWorkspaceMode === 'novel'
    ? t('components.fileExplorer.novel')
    : t('components.fileExplorer.script'),
);

function projectModeLabel(name: string): string {
  return projectStore.projectMode(name) === 'novel'
    ? t('components.fileExplorer.novel')
    : t('components.fileExplorer.script');
}

function projectModeIcon(name: string) {
  return projectStore.projectMode(name) === 'novel' ? BookOpen : Clapperboard;
}

// 欢迎页发送：无项目时 toast 拦截停留原地；有项目时把文本交父级发送。
// 注意：父级 draft 与欢迎页 draft 是两份隔离的 ref，必须显式传文本过去，
// 否则父级 send() 读到空 draft 会静默返回（无报错、无反应）。
function handleSend() {
  const text = beginSendToDirector();
  if (!text) return;
  if (!projectStore.currentProject) {
    bus.emit('toast', { type: 'info', message: t('common.selectProjectFirst') });
    return;
  }
  draft.value = '';
  emit('send', text);
}

function toastAttachmentHint() {
  bus.emit('toast', { type: 'info', message: t('components.chatPanel.fileImportRequiresProject') });
}

function toastAllProjects() {
  bus.emit('toast', { type: 'info', message: t('components.projectSelector.project') });
}

// ── 引导文案（原欢迎页两组 tips，扁平化回归；跳转走 viewStore，不自造管线） ──
const quickStartTips = computed(() => [
  t('components.chatWelcome.quickTip1'),
  t('components.chatWelcome.quickTip2'),
  t('components.chatWelcome.quickTip3'),
  t('components.chatWelcome.quickTip4'),
]);

const proTips = computed(() => [
  { text: t('components.chatWelcome.proTip1'), view: 'world' as AppViewKey },
  { text: t('components.chatWelcome.proTip2'), view: 'synopsis' as AppViewKey },
  { text: t('components.chatWelcome.proTip3'), view: 'structure' as AppViewKey },
  { text: t('components.chatWelcome.proTip4'), view: 'blueprint' as AppViewKey },
  { text: t('components.chatWelcome.proTip5'), view: 'engine' as AppViewKey },
]);

function goToView(view: AppViewKey) {
  viewStore.setView(view);
}

function goMuseWorkshop() {
  viewStore.setView('world');
}

function museTitle(item: InspirationEntry): string {
  const source = String(item.source || '').trim().split('\n')[0];
  if (source) return source.length > 18 ? `${source.slice(0, 18)}…` : source;
  const content = String(item.content || '').trim().split('\n')[0];
  if (content) return content.length > 18 ? `${content.slice(0, 18)}…` : content;
  return t('views.world.history.inspirationDrafts');
}

function museDesc(item: InspirationEntry): string {
  const content = String(item.content || '').trim().split('\n')[0] || String(item.source || '').trim();
  return content.length > 42 ? `${content.slice(0, 42)}…` : content;
}

// 点灵感卡 = 入项目：直接走现有 bindInspiration 绑定到当前项目（多对多软关联），
// 再跳灵感页由 HistoryPanel/useWorldLogic 展示绑定态。不自造第二套绑定管线。
async function adoptMuse(item: InspirationEntry) {
  const projectName = projectStore.currentProject;
  if (!projectName) {
    bus.emit('toast', { type: 'info', message: t('common.selectProjectFirst') });
    viewStore.setView('world');
    return;
  }
  try {
    const result = await bindInspiration(item.id, projectName);
    const unboundIds = (result as { unbound_ids?: string[] } | null)?.unbound_ids || [];
    bus.emit('inspiration-bind-changed', {
      boundId: item.id,
      unboundIds,
      projectName,
      entry: item,
    });
    await projectStore.refreshCurrentProjectInspiration(projectName);
    bus.emit('toast', { type: 'success', message: t('views.home.adoptMuseHint') });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e || '');
    bus.emit('toast', { type: 'error', message: msg });
  }
}
</script>

<style scoped>
.chat-welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100%;
  overflow-y: auto;
  user-select: none;
  -webkit-user-select: none;
}

.welcome-min {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
  padding: 8vh 20px 40px;
  text-align: center;
}

.welcome-spark-anim {
  width: 96px;
  height: 96px;
  flex-shrink: 0;
}

.welcome-spark-anim :deep(.spark-loader-stage) {
  width: 100%;
  height: 100%;
  margin-bottom: 0;
}

.welcome-title {
  font-size: var(--spark-fs-h1);
  font-weight: 800;
  color: var(--spark-text);
  margin: 16px 0 0;
  letter-spacing: -0.3px;
}

.welcome-tagline {
  margin: 6px 0 0;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-base);
}

.welcome-proj-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 16px;
  flex-wrap: wrap;
  justify-content: center;
}

.welcome-proj-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  background: transparent;
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-text);
}

.welcome-proj-pill.is-warn {
  border-color: var(--spark-warning);
  background: transparent;
}

.welcome-proj-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--spark-success);
}

.welcome-proj-pill.is-warn .welcome-proj-dot {
  background: var(--spark-warning);
}

.welcome-link-btn {
  border: none;
  background: transparent;
  color: var(--spark-primary);
  font-size: var(--spark-fs-sm);
  cursor: pointer;
  font-family: inherit;
  font-weight: 600;
}

/* 中央大输入：与聊天输入框同视觉语言，欢迎页是它的放大版；扁平无浮雕 */
.welcome-composer {
  width: 100%;
  margin-top: 18px;
  background: transparent;
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius-lg);
  padding: 16px 18px 12px;
  text-align: left;
  transition: border-color 0.2s ease;
}

.welcome-composer:focus-within {
  border-color: var(--spark-primary);
}

.welcome-textarea :deep(.n-input__textarea-el) {
  font-size: var(--spark-fs-lg);
  line-height: 1.6;
}

.welcome-composer-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.welcome-save-muse {
  color: var(--spark-warning);
}

.welcome-foot-hint {
  font-size: var(--spark-fs-2xs);
  color: var(--spark-text-muted);
  opacity: 0.7;
  margin-left: auto;
}

.welcome-send-btn {
  background: var(--spark-primary);
}

/* 最近两列与引导区：扁平无浮雕，与主题同色系 */

.welcome-recents {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 26px;
  text-align: left;
}

.welcome-recent-card {
  background: transparent;
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius);
  padding: 12px 14px;
  min-width: 0;
}

.welcome-recent-card header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: var(--spark-fs-xs);
  font-weight: 600;
  color: var(--spark-text-muted);
}

.welcome-recent-card header button {
  margin-left: auto;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
  font-family: inherit;
}

.welcome-recent-card header button:hover {
  color: var(--spark-primary);
}

.welcome-rrow {
  display: flex;
  align-items: center;
  gap: 10px;
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
}

.welcome-rrow:hover {
  background: var(--spark-primary-container);
}

.welcome-rrow-cover {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--spark-primary);
  border: 1px solid var(--spark-border);
}

.welcome-rrow-cover.is-active {
  color: var(--spark-text-inverse);
  background: var(--spark-primary);
}

.welcome-rrow-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.welcome-rrow-title {
  font-size: var(--spark-fs-sm);
  font-weight: 600;
  color: var(--spark-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.welcome-rrow-meta {
  font-size: var(--spark-fs-2xs);
  color: var(--spark-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.welcome-rrow-go {
  margin-left: auto;
  color: var(--spark-text-muted);
  opacity: 0.5;
  flex-shrink: 0;
}

.welcome-rrow:hover .welcome-rrow-go {
  color: var(--spark-primary);
  opacity: 1;
}

.welcome-mrow {
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
}

.welcome-mrow:hover {
  background: var(--spark-primary-container);
}

.welcome-mrow-title {
  font-size: var(--spark-fs-sm);
  font-weight: 600;
  color: var(--spark-text);
}

.welcome-mrow-tag {
  font-weight: 400;
  font-size: var(--spark-fs-3xs);
  color: var(--spark-warning);
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 1px 7px;
  margin-left: 6px;
}

.welcome-mrow-desc {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ===== 引导区（原欢迎页文案回归）：扁平卡，无浮雕/3D 特效，与主题同色系 ===== */
.welcome-tips {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  margin-top: 26px;
  text-align: left;
}

.tip-section {
  background: transparent;
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius);
  padding: 12px 16px;
}

.tip-section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.tip-section-title {
  font-size: var(--spark-fs-sm);
  font-weight: 700;
  color: var(--spark-text);
}

.tip-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tip-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-text-muted);
  line-height: 1.55;
}

.tip-section-hint {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
  margin: 0 0 8px 0;
  line-height: 1.5;
}

.tip-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border-radius: 50%;
  border: 1px solid var(--spark-border);
  color: var(--spark-primary);
  font-size: var(--spark-fs-3xs);
  font-weight: 700;
  line-height: 1;
}

.tip-text {
  flex: 1;
}

.tip-goto {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  align-self: center;
  min-width: 26px;
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  background: transparent;
  color: var(--spark-primary);
  font-size: var(--spark-fs-xs);
  cursor: pointer;
  font-family: inherit;
}

.tip-goto:hover {
  border-color: var(--spark-primary);
}

.welcome-footer {
  margin-top: 16px;
}

.footer-hint {
  font-size: var(--spark-fs-2xs);
  color: var(--spark-text-muted);
}

@media (max-width: 640px) {
  .welcome-recents {
    grid-template-columns: 1fr;
  }
  .welcome-min {
    padding-top: 5vh;
  }
}
</style>
