<template>
  <div class="mobile-flow-shell">
    <!-- 顶部固定导航栏 -->
    <header class="flow-header">
      <div class="header-left">
        <a :href="SPARKARC_GITHUB_URL" target="_blank" rel="noopener" class="app-logo-link"><AppBrand class="app-logo" :size="28" :show-text="false" /></a>
      </div>
      
      <div class="header-center">
        <span class="current-step-label">{{ currentStepLabel }}</span>
        <OnboardingHelpButton :scene-id="currentTutorialSceneId" />
      </div>
      
      <div class="header-right">
        <n-dropdown trigger="click" :options="projectSwitchOptions" @select="handleProjectSwitch">
          <span class="tooltip-dropdown-trigger">
            <n-button
              quaternary
              circle
              size="small"
              :aria-label="t('mobileFlow.header.switchProject')"
              :title="t('mobileFlow.header.switchProject')"
            >
              <template #icon><n-icon :component="FolderOpen" /></template>
            </n-button>
          </span>
        </n-dropdown>
        <StoryTagsPanel />
        <n-button
          quaternary
          circle
          size="small"
          :aria-label="t('components.headerToolbar.publishTitle')"
          :title="t('components.headerToolbar.publishTitle')"
          @click="openPublishDrawer"
        >
          <template #icon><n-icon :component="Share2" /></template>
        </n-button>
        <n-button
          quaternary
          circle
          size="small"
          :aria-label="t('components.headerToolbar.quickPreviewTitle')"
          :title="t('components.headerToolbar.quickPreviewTitle')"
          @click="quickPreview"
          :loading="previewing"
        >
          <template #icon><n-icon :component="Play" /></template>
        </n-button>
        <n-button
          class="mobile-settings-trigger"
          quaternary
          circle
          size="small"
          :aria-label="t('mobileFlow.drawer.tabs.settings')"
          :title="t('mobileFlow.drawer.tabs.settings')"
          @click="openSettings"
        >
          <template #icon><n-icon :component="Settings" /></template>
        </n-button>
      </div>
    </header>
    
    <!-- 滚动容器 -->
    <main class="flow-container" ref="containerRef">
      <!-- Step 0: 首页（问候 + 中央输入 + 最近项目/灵感） -->
      <FlowCard
        :step="0"
        :title="t('views.home.tagline')"
        :subtitle="''"
        :is-active="currentStep === 0"
      >
        <HomeMobile />
      </FlowCard>

      <!-- Step 1: 灵感 -->
      <FlowCard
        :step="1"
        :title="t('mobileFlow.cards.inspireTitle')"
        :subtitle="t('mobileFlow.cards.inspireSubtitle')"
        :is-active="currentStep === 1"
      >
        <WorldMobile />
      </FlowCard>
      
      <!-- Step 2: 世界观 -->
      <FlowCard
        :step="2"
        :title="t('mobileFlow.cards.worldTitle')"
        :subtitle="t('mobileFlow.cards.worldSubtitle')"
        :is-active="currentStep === 2"
      >
        <LorebookMobile :world-only="true" />
      </FlowCard>

      <!-- Step 3: 角色设定 -->
      <FlowCard
        :step="3"
        :title="t('mobileFlow.cards.charactersTitle')"
        :subtitle="t('mobileFlow.cards.charactersSubtitle')"
        :is-active="currentStep === 3"
      >
        <CharactersMobile />
      </FlowCard>

      <!-- Step 4: 故事梗概 -->
      <FlowCard
        :step="4"
        :title="t('mobileFlow.cards.synopsisTitle')"
        :subtitle="t('mobileFlow.cards.synopsisSubtitle')"
        :is-active="currentStep === 4"
      >
        <SynopsisMobile />
      </FlowCard>

      <!-- Step 5: 大纲编排 -->
      <FlowCard
        :step="5"
        :title="t('mobileFlow.cards.structureTitle')"
        :subtitle="t('mobileFlow.cards.structureSubtitle')"
        :is-active="currentStep === 5"
      >
        <StructureMobile />
      </FlowCard>

      <!-- Step 6: 剧本创作 -->
      <FlowCard
        :step="6"
        :title="t('mobileFlow.cards.productionTitle')"
        :subtitle="t('mobileFlow.cards.productionSubtitle')"
        :is-active="currentStep === 6"
        :show-next-button="false"
      >
        <ProductionMobile />
      </FlowCard>

      <!-- Step 7: 故事蓝图 -->
      <FlowCard
        :step="7"
        :title="t('mobileFlow.cards.blueprintTitle')"
        :subtitle="t('mobileFlow.cards.blueprintSubtitleNew')"
        :is-active="currentStep === 7"
        :show-next-button="false"
      >
        <BlueprintIndex />
        <template #footer>
          <div class="completion-message">
            <n-icon :component="CircleCheckBig" size="24" color="var(--spark-success)" />
            <span>{{ t('mobileFlow.cards.completion') }}</span>
          </div>
        </template>
      </FlowCard>
    </main>
    
    <!-- 步骤指示器 -->
    <StepIndicator v-show="!immersiveMode" :steps="flowSteps" :container-ref="containerRef" />
    
    <!-- 移动端所有创作步骤都保留 AI 悬浮聊天入口 -->
    <GlobalChatFloat />
    
    <!-- 发布管理抽屉 -->
    <n-drawer v-model:show="publishDrawerVisible" placement="bottom" height="90%">
      <n-drawer-content closable>
        <template #header>
          <div class="drawer-header">
            <span>{{ t('components.versionManager.title') }}</span>
          </div>
        </template>
        <VersionManager :projectId="projectStore.currentProject || undefined" :content-format="workspaceMode" hide-title />
      </n-drawer-content>
    </n-drawer>

    <!-- 项目导入文件选择 -->
    <input type="file" ref="importSparkInput" @change="onSparkFileChange" accept=".spark" style="display:none;">

    <!-- 设置抽屉 (包含 AI配置、风格、引擎等辅助功能) -->
    <n-drawer v-model:show="settingsDrawerVisible" placement="bottom" height="90%" class="mobile-settings-drawer">
      <n-drawer-content closable :native-scrollbar="false">
        <template #header>
          <div class="drawer-header">
            <span>{{ t('mobileFlow.drawer.title') }}</span>
          </div>
        </template>
        
        <n-tabs type="segment" :animated="false" class="mobile-settings-tabs spark-segment-tabs">
          <n-tab-pane name="settings" :tab="t('mobileFlow.drawer.tabs.settings')">
            <SettingsMobile />
          </n-tab-pane>
          <n-tab-pane name="style" :tab="t('mobileFlow.drawer.tabs.style')">
            <StyleMobile />
          </n-tab-pane>
          <n-tab-pane name="engine" :tab="t('mobileFlow.drawer.tabs.engine')">
            <EngineMobile />
          </n-tab-pane>
          <n-tab-pane name="dashboard" :tab="t('mobileFlow.drawer.tabs.dashboard')">
            <DashboardMobile />
          </n-tab-pane>
        </n-tabs>
      </n-drawer-content>
    </n-drawer>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, provide, watch, h, nextTick } from 'vue';
import { NButton, NIcon, NDrawer, NDrawerContent, NTabs, NTabPane, NDropdown, type DropdownOption, useDialog } from 'naive-ui';
import { Archive, BookOpen, CircleCheckBig, CirclePlus, Clapperboard, FolderOpen, PaintBucket, Play, Settings, Share2, SquarePen, Trash } from '@lucide/vue';
import { useI18n } from 'vue-i18n';

import FlowCard from './FlowCard.vue';
import OnboardingHelpButton from '../../../onboarding/components/OnboardingHelpButton.vue';
import StepIndicator from './StepIndicator.vue';
import GlobalChatFloat from '../../chat/GlobalChatFloat.vue';

// 核心工作流视图
import HomeMobile from '../../../views/Home/HomeMobile.vue';
import WorldMobile from '../../../views/World/WorldIndex.vue';
import LorebookMobile from '../../../views/Lorebook/LorebookMobile.vue';
import CharactersMobile from '../../../views/Characters/CharactersMobile.vue';
import SynopsisMobile from '../../../views/Synopsis/SynopsisIndex.vue';
import StructureMobile from '../../../views/Structure/StructureIndex.vue';
import BlueprintIndex from '../../../views/Blueprint/BlueprintIndex.vue';
import ProductionMobile from '../../../views/Production/ProductionIndex.vue';

// 辅助功能（放入设置抽屉）
import SettingsMobile from '../../../views/Settings/SettingsIndex.vue';
import StyleMobile from '../../../views/Style/StyleIndex.vue';
import EngineMobile from '../../../views/Engine/EngineIndex.vue';
import DashboardMobile from '../../../views/Dashboard/DashboardIndex.vue';

import { useProjectStore } from '../../stores/projectStore';
import { useViewStore, type AppViewKey } from '../../stores/viewStore';
import { useSceneStore } from '../../stores/sceneStore';
import { useFileStore } from '../../stores/fileStore';
import { useFullscreen } from '../../../composables/useFullscreen';
import { useOnboarding } from '../../../onboarding';
import { mobilePageSceneIds } from '../../../onboarding/engine/stepDefinitions';
import AppBrand from '../../share/AppBrand.vue';
import { SPARKARC_GITHUB_URL } from '@/config';
import VersionManager from '../../dlg-editor/VersionManager.vue';
import bus from '../../../eventBus';
import { saveStory, fetchWithAuth } from '../../../services/api';
import { exportProjectAsSpark, importProjectFromSpark } from '../../../services/projectService';
import StoryTagsPanel from '../../share/StoryTagsPanel.vue';
import { closeDeferredBrowserTab, navigateDeferredBrowserTab, openDeferredBrowserTab } from '../../../utils/deferredBrowserTab';

const projectStore = useProjectStore();
const viewStore = useViewStore();
const sceneStore = useSceneStore();
const fileStore = useFileStore();
const { preferred, requestFullscreen, setPreferred } = useFullscreen();
const { t } = useI18n();
const dialog = useDialog();
const containerRef = ref(null);
const currentStep = ref(0);
const settingsDrawerVisible = ref(false);
const publishDrawerVisible = ref(false);
const previewing = ref(false);
// 沉浸模式：创作页进入场景详情时隐藏右侧步骤导航，避免阅读下滑误触
const immersiveMode = ref(false);

const workspaceMode = computed(() => sceneStore.workspaceMode || 'script');


// 提供 projectId 给子组件
provide('projectId', computed(() => projectStore.currentProject));

const flowSteps = computed(() => [
  { id: 'home', label: t('activityBar.home') },
  { id: 'muse', label: t('mobileFlow.steps.muse') },
  { id: 'lorebook', label: t('mobileFlow.steps.world') },
  { id: 'characters', label: t('mobileFlow.steps.characters') },
  { id: 'synopsis', label: t('mobileFlow.steps.synopsis') },
  { id: 'structure', label: t('mobileFlow.steps.structure') },
  { id: 'production', label: t('mobileFlow.steps.production') },
  { id: 'blueprint', label: t('mobileFlow.steps.blueprint') }
]);

const currentStepLabel = computed(() => {
  return flowSteps.value[currentStep.value]?.label || t('mobileFlow.sparkArc');
});

const currentTutorialSceneId = computed(() => (
  ['page-mobile-muse', 'page-mobile-muse', 'page-mobile-world', 'page-mobile-world', 'page-mobile-synopsis', 'page-mobile-structure', 'page-mobile-production', 'page-mobile-blueprint'][currentStep.value] || mobilePageSceneIds[0]
));

const stepViewMap: AppViewKey[] = ['home', 'world', 'lorebook', 'characters', 'synopsis', 'structure', 'production', 'blueprint'];
watch(currentStep, (idx) => {
  const view = stepViewMap[idx] || 'home';
  if (viewStore.currentView !== view) {
    viewStore.setView(view);
  }
}, { immediate: true });

function openSettings() {
  settingsDrawerVisible.value = true;
}

function openPublishDrawer() {
  publishDrawerVisible.value = true;
}

// ── 项目菜单（含切换、新建、重命名、删除、导入导出） ──
const projectSwitchOptions = computed<DropdownOption[]>(() => {
  const items: DropdownOption[] = projectStore.projects.map(p => ({
    label: p === projectStore.currentProject ? `✓ ${p}` : p,
    key: `switch:${p}`,
    icon: () => renderProjectModeIcon(projectStore.projectMode(p)),
  }));
  items.push({ type: 'divider', key: 'd1' });
  items.push({ label: t('components.projectSelector.newProject'), key: 'create', icon: () => h(NIcon, null, { default: () => h(CirclePlus) }) });
  if (projectStore.currentProject) {
    items.push({ label: t('components.projectSelector.renameCurrentProject'), key: 'rename', icon: () => h(NIcon, null, { default: () => h(SquarePen) }) });
    items.push({ label: t('components.projectSelector.deleteCurrentProject'), key: 'delete', icon: () => h(NIcon, null, { default: () => h(Trash) }) });
  }
  items.push({ type: 'divider', key: 'd2' });
  items.push({ label: t('components.headerToolbar.exportProject'), key: 'export_project', icon: () => h(NIcon, null, { default: () => h(Archive) }) });
  items.push({ label: t('components.headerToolbar.importProject'), key: 'import_project', icon: () => h(NIcon, null, { default: () => h(PaintBucket) }) });
  return items;
});

function getProjectModeIcon(mode: string | undefined) {
  return mode === 'novel' ? BookOpen : Clapperboard;
}

function renderProjectModeIcon(mode: string | undefined) {
  const normalized = mode === 'novel' ? 'novel' : 'script';
  return h(
    NIcon,
    {
      class: ['mobile-project-mode-icon', `is-${normalized}`],
      size: 16,
      style: {
        color: 'var(--spark-primary)',
        transform: 'translateY(1.5px)',
      },
    },
    { default: () => h(getProjectModeIcon(normalized)) },
  );
}

async function handleProjectSwitch(key: string) {
  if (key === 'create') {
    await projectStore.createProject();
  } else if (key === 'rename') {
    handleRenameProject();
  } else if (key === 'delete') {
    handleDeleteProject();
  } else if (key === 'export_project') {
    exportProjectSpark();
  } else if (key === 'import_project') {
    importSparkInput.value?.click();
  } else if (key.startsWith('switch:')) {
    const name = key.slice(7);
    if (name !== projectStore.currentProject) {
      await projectStore.setCurrentProject(name);
    }
  }
}

async function handleRenameProject() {
  if (!projectStore.currentProject) return;
  const newName = await new Promise<unknown>((resolve) => bus.emit('prompt', {
    title: t('components.projectSelector.renameProject'),
    message: t('components.projectSelector.renamePrompt', { project: projectStore.currentProject }),
    defaultValue: projectStore.currentProject,
    resolve,
  }));
  if (typeof newName === 'string' && newName.trim()) {
    await projectStore.renameCurrentProject(newName);
  }
}

function handleDeleteProject() {
  if (!projectStore.currentProject) return;
  // 第一次确认
  dialog.warning({
    title: t('components.projectSelector.confirmDeleteTitle'),
    content: t('components.projectSelector.confirmDelete', { project: projectStore.currentProject }),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      // 第二次确认
      dialog.error({
        title: t('components.projectSelector.confirmDeleteTitle'),
        content: t('components.projectSelector.confirmDeleteFinal', { project: projectStore.currentProject }),
        positiveText: t('common.delete'),
        negativeText: t('common.cancel'),
        onPositiveClick: () => {
          projectStore.deleteCurrentProject();
        },
      });
    },
  });
}

const importSparkInput = ref<HTMLInputElement | null>(null);

function onSparkFileChange(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  handleSparkImport(file);
  (e.target as HTMLInputElement).value = '';
}

async function exportProjectSpark() {
  if (!projectStore.currentProject) {
    bus.emit('toast', { type: 'error', message: t('components.headerToolbar.selectProjectFirst') });
    return;
  }
  try {
    await exportProjectAsSpark(projectStore.currentProject);
    bus.emit('toast', { type: 'success', message: t('components.headerToolbar.exportProjectSuccess') });
  } catch (e: unknown) {
    const errorMessage = e instanceof Error ? e.message : String(e || 'Unknown error');
    bus.emit('toast', { type: 'error', message: `${t('components.headerToolbar.exportProjectFailed')}: ${errorMessage}` });
  }
}

async function handleSparkImport(file: File) {
  try {
    const result = await importProjectFromSpark(file);
    const newProjectName = result.projectName || '';
    if (newProjectName) {
      await projectStore.loadProjects();
      projectStore.setCurrentProject(newProjectName);
    }
    bus.emit('toast', { type: 'success', message: t('components.headerToolbar.importProjectSuccess', { name: newProjectName }) });
    const importedStyle = result.importedStyle as { styleName?: string } | undefined;
    if (importedStyle?.styleName) {
      bus.emit('toast', {
        type: 'success',
        message: t('components.headerToolbar.importProjectStyleRestored', { name: importedStyle.styleName }),
      });
    }
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    for (const warning of warnings) {
      bus.emit('toast', {
        type: 'warning',
        message: t('components.headerToolbar.importProjectWarning', { reason: String(warning) }),
      });
    }
  } catch (e: unknown) {
    const errorMessage = e instanceof Error ? e.message : String(e || 'Unknown error');
    bus.emit('toast', { type: 'error', message: `${t('components.headerToolbar.importProjectFailed')}: ${errorMessage}` });
  }
}

async function quickPreview() {
  if (!projectStore.currentProject || previewing.value) {
    if (!projectStore.currentProject) {
      bus.emit('toast', { type: 'error', message: t('components.headerToolbar.selectProjectFirst') });
    }
    return;
  }

  const previewTab = openDeferredBrowserTab();
  // 先保存当前文件
  const currentFilePath = fileStore.selectedFile?.type === 'story' ? fileStore.selectedFile.path : null;
  if (currentFilePath) {
    try {
      await saveStory(projectStore.currentProject, currentFilePath, sceneStore.scriptData);
    } catch {
      closeDeferredBrowserTab(previewTab);
      return;
    }
  }

  previewing.value = true;
  try {
    const contentFormat = sceneStore.fileFormat === 'novel' || sceneStore.workspaceMode === 'novel' ? 'novel' : 'script';
    const res = await fetchWithAuth(`/api/versions/${projectStore.currentProject}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contentFormat }),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({} as Record<string, unknown>));
      const errorMessage = typeof body.error === 'string'
        ? body.error
        : typeof body.message === 'string'
          ? body.message
          : t('components.headerToolbar.quickPreviewFailed');
      throw new Error(errorMessage);
    }

    const data = await res.json() as { version_id?: string };
    if (!data.version_id) {
      throw new Error(t('components.headerToolbar.quickPreviewFailed'));
    }

    navigateDeferredBrowserTab(previewTab, `#/play/v/${data.version_id}`);
    bus.emit('toast', { type: 'success', message: t('components.headerToolbar.quickPreviewStarted') });
  } catch (e: unknown) {
    closeDeferredBrowserTab(previewTab);
    const errorMessage = e instanceof Error ? e.message : String(e || t('components.headerToolbar.quickPreviewFailed'));
    bus.emit('toast', { type: 'error', message: `${t('components.headerToolbar.quickPreviewFailed')}: ${errorMessage}` });
  } finally {
    previewing.value = false;
  }
}

// IntersectionObserver 检测当前可见卡片
let observer: IntersectionObserver | null = null;

function setupObserver() {
  const options = {
    root: containerRef.value,
    rootMargin: '-40% 0px -40% 0px',
    threshold: 0
  };
  
  observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id;
        const match = id.match(/step-(\d+)/);
        if (match) {
          currentStep.value = parseInt(match[1]) - 1;
        }
      }
    });
  }, options);
  
  // 观察所有步骤卡片
  flowSteps.value.forEach((_, index) => {
    const element = document.getElementById(`step-${index + 1}`);
    if (element) {
      observer?.observe(element);
    }
  });
}

// 首次进入移动端时触发统一引导（等待登录后检查完成）
const { triggerIfFirst } = useOnboarding();
const onPostLoginReady = () => {
  nextTick(() => triggerIfFirst('mobile-workspace'));
};

// 监听子页面发出的沉浸模式信号（创作页进入/退出场景详情）
const onImmersiveChange = (active: unknown) => {
  immersiveMode.value = !!active;
};

onMounted(() => {
  setTimeout(setupObserver, 200);

  // 确保移动端独立初始化项目列表并恢复上次选中的项目
  if (projectStore.projects.length === 0) {
    projectStore.loadProjects();
  }

  bus.on('post-login-ready', onPostLoginReady);
  bus.on('mobile-flow-immersive', onImmersiveChange);
  // 如果 App.vue 已经发过 post-login-ready（竞态：子组件晚于 App mount），直接触发
  if ((bus as any).postLoginReadySent) onPostLoginReady();

  try {
    const stored = localStorage.getItem('spark_fullscreen');
    if (stored === null) {
      setPreferred(true);
    }
  } catch {}

  if (preferred.value && !document.fullscreenElement) {
    const tryOnce = () => {
      requestFullscreen();
    };
    window.addEventListener('touchstart', tryOnce, { once: true, passive: true });
    window.addEventListener('click', tryOnce, { once: true });
  }
});

onUnmounted(() => {
  if (observer) {
    observer.disconnect();
  }
  bus.off('post-login-ready', onPostLoginReady);
  bus.off('mobile-flow-immersive', onImmersiveChange);
});
</script>

<style scoped>
.mobile-flow-shell {
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background-color: var(--spark-bg);
  overflow: hidden;
}

/* 顶部导航栏 */
.flow-header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: calc(var(--mobile-header-height, 48px) + var(--sat, 0px));
  z-index: 200;
  
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  padding-top: var(--sat, 0px);
  
  background: color-mix(in srgb, var(--spark-panel-bg) 85%, transparent);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid color-mix(in srgb, var(--spark-border) 50%, transparent);
  overflow-x: hidden;
}

.header-left, .header-right {
  width: 48px;
  display: flex;
  justify-content: center;
  flex-shrink: 0;
}

.header-left {
  justify-content: flex-start;
}

.header-center {
  flex: 1;
  min-width: 0;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 2px;
}

.header-right {
  justify-content: flex-end;
  gap: 4px;
  width: auto;
  min-width: 0;
  max-width: calc(100% - 96px);
  overflow: hidden;
}

.app-logo-link {
  display: flex;
  align-items: center;
  text-decoration: none;
  color: inherit;
  line-height: 0;
}
.app-logo {
  display: flex;
  align-items: center;
  line-height: 0;
}

.tooltip-dropdown-trigger {
  display: inline-flex;
}

.mobile-project-mode-icon {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
}

.mobile-project-mode-icon.is-script {
  color: var(--spark-primary);
  background: color-mix(in srgb, var(--spark-primary), transparent 86%);
}

.mobile-project-mode-icon.is-novel {
  color: var(--spark-primary);
  background: color-mix(in srgb, var(--spark-primary), transparent 86%);
}


.current-step-label {
  display: block;
  font-weight: 600;
  font-size: var(--spark-fs-md);
  color: var(--spark-text);
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 滚动容器 */
.flow-container {
  flex: 1;
  /* 强制垂直布局 */
  display: flex;
  flex-direction: column;
  
  /* 确保宽度正确 */
  width: 100%;
  max-width: 100%;
  
  overflow-y: auto;
  overflow-x: hidden;
  
  /* 垂直滚动吸附 */
  scroll-snap-type: y mandatory;
  -webkit-overflow-scrolling: touch;
  scroll-behavior: smooth;
  
  /* 防止橡皮筋效果影响整体 */
  overscroll-behavior-y: contain;
}

/* 完成消息 */
.completion-message {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px;
  background: rgba(var(--spark-success-rgb), 0.1);
  border: 1px solid rgba(var(--spark-success-rgb), 0.3);
  border-radius: 12px;
  color: var(--spark-success);
  font-weight: 500;
}

/* 抽屉样式 */
.drawer-header {
  font-size: var(--spark-fs-lg);
  font-weight: 600;
}

:deep(.n-drawer) {
  border-radius: 16px 16px 0 0;
}

:deep(.n-drawer-header) {
  padding: 14px 12px !important;
  border-bottom: 1px solid var(--spark-border);
}

/* 抽屉内容区：收窄横向留白，保留少量边距维持美观 */
:deep(.n-drawer-body-content-wrapper) {
  padding: 0 8px !important;
}

.mobile-settings-drawer :deep(.n-drawer-body) {
  min-height: 0;
}

.mobile-settings-drawer :deep(.n-drawer-body-content-wrapper) {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

:deep(.n-tabs-nav) {
  padding: 0 4px;
}

/* 移动端移除选项卡滑动指示条阴影特效 */
:deep(.n-tabs-bar) {
  box-shadow: none !important;
}

/* 标签页之间保持紧凑，避免标签左右过度留白 */
:deep(.n-tabs .n-tabs-tab) {
  padding-left: 10px;
  padding-right: 10px;
}

:deep(.n-tab-pane) {
  padding: 12px 0;
  padding-bottom: 100px;
}

.mobile-settings-tabs {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.mobile-settings-tabs :deep(.n-tabs-nav) {
  flex: 0 0 auto;
  padding: 8px 0 6px;
}

.mobile-settings-tabs :deep(.n-tabs-pane-wrapper) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.mobile-settings-tabs :deep(.n-tab-pane) {
  padding: 8px 0 calc(84px + var(--sab, 0px));
}
</style>
