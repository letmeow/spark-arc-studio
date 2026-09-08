<template>
  <div class="home-view">
    <div class="home-min">
      <!-- 火花 + 问候：复用 SparkLoaderAnimation（主题自适应），与 ChatWelcomeScreen 同源 -->
      <SparkLoaderAnimation class="home-spark-anim" aria-hidden="true" />
      <h1 class="home-title">{{ greetWord }}<span v-if="username">，{{ username }}</span></h1>
      <p class="home-tagline">{{ t('views.home.tagline') }}</p>

      <!-- 当前项目胶囊 + 新建 -->
      <div class="home-proj-row">
        <div class="home-proj-pill" :class="{ 'is-warn': !projectStore.currentProject }">
          <span class="home-proj-dot"></span>
          <span v-if="projectStore.currentProject">{{ projectStore.currentProject }} · {{ workspaceModeLabel }}</span>
          <span v-else>{{ t('views.home.noProjectHint') }}</span>
        </div>
        <button class="home-link-btn" @click="projectStore.createProject()">{{ t('views.home.newProject') }}</button>
      </div>

      <!-- 回访：仅 C 态 -->
      <div v-if="homeState === 'C'" class="home-resume">
        <span>{{ t('views.home.resumePrefix') }} <b>{{ lastSceneLabel }}</b></span>
        <n-button type="primary" size="small" round @click="resumeChat">{{ t('views.home.resumeAction') }}</n-button>
      </div>

      <!-- 中央大输入：唯一主角 -->
      <div ref="composerRef" class="home-composer">
        <n-input
          :value="draft"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 6 }"
          :placeholder="placeholder"
          class="home-textarea"
          @update:value="draft = $event"
          @keydown.enter.exact.prevent="handleSend"
        />
        <div class="home-composer-foot">
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
              <n-button quaternary circle size="small" class="home-save-muse" :loading="savingMuse" @click="saveAsMuse">
                <template #icon><n-icon :component="Sparkles" /></template>
              </n-button>
            </template>
            {{ t('views.home.saveMuseTitle') }}
          </n-tooltip>
          <span class="home-foot-hint">{{ t('views.home.sendHint') }}</span>
          <n-button circle type="primary" class="home-send-btn" :loading="flying" @click="handleSend">
            <template #icon><n-icon :component="ArrowUp" /></template>
          </n-button>
        </div>
      </div>

      <!-- 建议片：3 个，单击填入 -->
      <div class="home-chips">
        <button v-for="chip in chips" :key="chip" class="home-chip" @click="fillChip(chip)">{{ chip }}</button>
      </div>

      <!-- 灵感匣入口：锚点滚到下方两列 -->
      <button class="home-muse-line" @click="scrollToRecents">
        <n-icon :component="Sparkles" class="home-muse-star" />
        <span>{{ t('views.home.museLine', { count: museTotal }) }}</span>
        <span>↓</span>
      </button>

      <!-- 最近两列：项目 + 灵感草稿（首页唯一下半区） -->
      <div ref="recentsRef" class="home-recents">
        <section class="home-recent-card">
          <header>
            <span>{{ t('views.home.recentProjects') }}</span>
            <button @click="toastAllProjects">{{ t('views.home.viewAll') }}</button>
          </header>
          <div
            v-for="name in recentProjects"
            :key="name"
            class="home-rrow"
            :class="{ 'is-active': name === projectStore.currentProject }"
            @click="openProject(name)"
          >
            <span class="home-rrow-cover" :class="name === projectStore.currentProject ? 'is-active' : ''">
              <n-icon :component="projectModeIcon(name)" />
            </span>
            <span class="home-rrow-text">
              <span class="home-rrow-title">{{ name }}</span>
              <span class="home-rrow-meta">{{ projectModeLabel(name) }}</span>
            </span>
            <span class="home-rrow-go">›</span>
          </div>
          <n-empty v-if="recentProjects.length === 0" size="small" :description="t('views.home.noProjectHint')" />
        </section>

        <section class="home-recent-card">
          <header>
            <span>{{ t('views.home.recentMuses') }}</span>
            <button @click="goMuseWorkshop">{{ t('views.home.viewAll') }}</button>
          </header>
          <div v-for="item in recentMuses" :key="item.id" class="home-mrow" @click="adoptMuse(item)">
            <div class="home-mrow-title">✦ {{ museTitle(item) }}<span class="home-mrow-tag">{{ t('views.world.history.drafts') }}</span></div>
            <div class="home-mrow-desc">{{ museDesc(item) }}</div>
          </div>
          <n-empty v-if="recentMuses.length === 0" size="small" :description="t('views.world.history.emptyDrafts')" />
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { NButton, NEmpty, NIcon, NInput, NTooltip } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { ArrowUp, BookOpen, Clapperboard, Plus, Sparkles } from '@lucide/vue';
import SparkLoaderAnimation from '../../components/share/SparkLoaderAnimation.vue';
import { useHomeLogic } from '../../composables/useHomeLogic';
import { runHomeSendTransition } from '../../components/home/HomeSendTransition';
import { useSceneStore } from '../../components/stores/sceneStore';
import { bindInspiration } from '../../services/storyService';
import type { InspirationEntry } from '../../services/aiContracts';
import bus from '../../eventBus';

const { t } = useI18n();
const sceneStore = useSceneStore();
const composerRef = ref<HTMLElement | null>(null);
const recentsRef = ref<HTMLElement | null>(null);
const flying = ref(false);

const {
  draft,
  savingMuse,
  greetWord,
  homeState,
  placeholder,
  chips,
  recentProjects,
  recentMuses,
  museTotal,
  fillChip,
  openProject,
  saveAsMuse,
  beginSendToDirector,
  commitSendToDirector,
  viewStore,
  projectStore,
} = useHomeLogic();

const username = computed(() => '');

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

// 回访标签：优先用当前场景名，否则回退到“继续上次对话”。
const lastSceneLabel = computed(() => {
  const scene = sceneStore.currentScene as { scene?: string; title?: string } | null;
  const name = scene?.title || scene?.scene;
  return name || t('components.chatPanel.openInWorkspace');
});

function resumeChat() {
  viewStore.openChatView('agent_director');
}

async function handleSend() {
  const text = beginSendToDirector();
  if (!text) return;
  if (flying.value) return;
  flying.value = true;
  try {
    await runHomeSendTransition({
      sourceEl: composerRef.value,
      text,
      switchToChat: () => commitSendToDirector(text),
    });
  } finally {
    flying.value = false;
  }
}

function toastAttachmentHint() {
  bus.emit('toast', { type: 'info', message: t('components.chatPanel.fileImportRequiresProject') });
}

function toastAllProjects() {
  bus.emit('toast', { type: 'info', message: t('components.projectSelector.project') });
}

function scrollToRecents() {
  recentsRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
.home-view {
  height: 100%;
  width: 100%;
  overflow-y: auto;
  background: var(--spark-bg);
}

.home-min {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 720px;
  margin: 0 auto;
  padding: 8vh 20px 40px;
  text-align: center;
}

.home-spark-anim {
  width: 96px;
  height: 96px;
  flex-shrink: 0;
}

.home-spark-anim :deep(.spark-loader-stage) {
  width: 100%;
  height: 100%;
  margin-bottom: 0;
}

.home-title {
  font-size: var(--spark-fs-h1);
  font-weight: 800;
  color: var(--spark-text);
  margin: 16px 0 0;
  letter-spacing: -0.3px;
}

.home-tagline {
  margin: 6px 0 0;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-base);
}

.home-proj-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 16px;
  flex-wrap: wrap;
  justify-content: center;
}

.home-proj-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-text);
}

.home-proj-pill.is-warn {
  border-color: color-mix(in srgb, var(--spark-warning), var(--spark-border) 40%);
  background: color-mix(in srgb, var(--spark-warning), transparent 88%);
}

.home-proj-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--spark-success);
}

.home-proj-pill.is-warn .home-proj-dot {
  background: var(--spark-warning);
}

.home-link-btn {
  border: none;
  background: transparent;
  color: var(--spark-primary);
  font-size: var(--spark-fs-sm);
  cursor: pointer;
  font-weight: 600;
}

.home-resume {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-text-muted);
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 7px 8px 7px 14px;
}

.home-resume b {
  color: var(--spark-text);
}

/* 中央大输入：胶囊卡，主题自适应 */
.home-composer {
  width: 100%;
  margin-top: 18px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius-lg);
  box-shadow: var(--spark-shadow);
  padding: 16px 18px 12px;
  text-align: left;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.home-composer:focus-within {
  border-color: var(--spark-primary);
  box-shadow: 0 0 0 3px var(--spark-primary-glow), var(--spark-shadow);
}

.home-textarea :deep(.n-input__textarea-el) {
  font-size: var(--spark-fs-lg);
  line-height: 1.6;
}

.home-composer-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.home-save-muse {
  color: var(--spark-warning);
}

.home-foot-hint {
  font-size: var(--spark-fs-2xs);
  color: var(--spark-text-muted);
  opacity: 0.7;
  margin-left: auto;
}

.home-send-btn {
  box-shadow: 0 4px 12px var(--spark-primary-glow);
}

.home-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
  justify-content: center;
}

.home-chip {
  font-size: var(--spark-fs-sm);
  font-family: inherit;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 8px 16px;
  cursor: pointer;
  color: var(--spark-text);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.home-chip:hover {
  border-color: var(--spark-primary);
  box-shadow: 0 4px 14px var(--spark-primary-glow);
}

.home-muse-line {
  margin-top: 18px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: var(--spark-fs-xs);
  font-family: inherit;
  color: var(--spark-text-muted);
  opacity: 0.7;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.home-muse-line:hover {
  opacity: 1;
  color: var(--spark-primary);
}

.home-muse-star {
  color: var(--spark-warning);
}

.home-recents {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 26px;
  text-align: left;
}

.home-recent-card {
  background: transparent;
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius);
  padding: 12px 14px;
  min-width: 0;
}

.home-recent-card header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: var(--spark-fs-xs);
  font-weight: 600;
  color: var(--spark-text-muted);
}

.home-recent-card header button {
  margin-left: auto;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
  font-family: inherit;
}

.home-recent-card header button:hover {
  color: var(--spark-primary);
}

.home-rrow {
  display: flex;
  align-items: center;
  gap: 10px;
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  border: 1px solid transparent;
}

.home-rrow:hover {
  background: var(--spark-panel-bg);
  border-color: var(--spark-border);
}

.home-rrow-cover {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--spark-primary);
  background: var(--spark-primary-container);
}

.home-rrow-cover.is-active {
  color: var(--spark-text-inverse);
  background: var(--spark-primary);
}

.home-rrow-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.home-rrow-title {
  font-size: var(--spark-fs-sm);
  font-weight: 600;
  color: var(--spark-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.home-rrow-meta {
  font-size: var(--spark-fs-2xs);
  color: var(--spark-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.home-rrow-go {
  margin-left: auto;
  color: var(--spark-text-muted);
  opacity: 0.5;
  flex-shrink: 0;
}

.home-rrow:hover .home-rrow-go {
  color: var(--spark-primary);
  opacity: 1;
}

.home-mrow {
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  border: 1px solid transparent;
}

.home-mrow:hover {
  background: var(--spark-panel-bg);
  border-color: var(--spark-border);
}

.home-mrow-title {
  font-size: var(--spark-fs-sm);
  font-weight: 600;
  color: var(--spark-text);
}

.home-mrow-tag {
  font-weight: 400;
  font-size: var(--spark-fs-3xs);
  color: var(--spark-warning);
  background: color-mix(in srgb, var(--spark-warning), transparent 85%);
  border-radius: 999px;
  padding: 1px 7px;
  margin-left: 6px;
}

.home-mrow-desc {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 640px) {
  .home-recents {
    grid-template-columns: 1fr;
  }
  .home-min {
    padding-top: 5vh;
  }
}
</style>
