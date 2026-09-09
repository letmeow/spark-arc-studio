<template>
  <header class="app-header no-select" data-tauri-drag-region @mousedown="onHeaderMousedown">
    <div class="header-left">
      <n-tooltip trigger="hover">
        <template #trigger>
          <a class="logo" :href="SPARKARC_GITHUB_URL" target="_blank" rel="noopener">
            <AppBrand :size="28" :alt="t('components.headerToolbar.projectHome')" />
          </a>
        </template>
        {{ t('components.headerToolbar.projectHome') }}
      </n-tooltip>
      <ProjectSelector />
    </div>
    <div class="header-center header-buttons">
      <div class="dock-bar" ref="dockBarRef"
        @mouseenter="onDockEnter" @mousemove="onDockMove" @mouseleave="onDockLeave">
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button
              class="header-action-btn"
              type="primary"
              strong
              @click="projectStore.createProject"
            >
              <template #icon>
                <n-icon :component="FilePlus2" />
              </template>
              {{ t('components.projectSelector.newProject') }}
            </n-button>
          </template>
          {{ t('components.projectSelector.newProject') }}
        </n-tooltip>

        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button class="header-action-btn" @click="$emit('open-version-manager')" type="primary" strong>
              <template #icon>
                <n-icon :component="Share2" />
              </template>
              {{ t('components.headerToolbar.publish') }}
            </n-button>
          </template>
          {{ t('components.headerToolbar.publishTitle') }}
        </n-tooltip>

        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button class="header-action-btn" @click="quickPreview" :loading="previewing" type="primary" strong>
              <template #icon>
                <n-icon :component="Play" />
              </template>
              {{ t('components.headerToolbar.quickPreview') }}
            </n-button>
          </template>
          {{ t('components.headerToolbar.quickPreviewTitle') }}
        </n-tooltip>

        <n-dropdown trigger="click" :options="fileOptions" @select="handleFileAction">
          <span class="tooltip-dropdown-trigger">
            <n-tooltip trigger="hover">
              <template #trigger>
                <n-button class="header-action-btn" type="primary" strong>
                  <template #icon>
                    <n-icon :component="FolderOpen" />
                  </template>
                  {{ t('components.headerToolbar.file') }}
                </n-button>
              </template>
              {{ t('components.headerToolbar.fileActionTitle') }}
            </n-tooltip>
          </span>
        </n-dropdown>

        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button class="header-action-btn" quaternary :disabled="!sceneStore.canUndo" @click="sceneStore.undoStoryEdit()">
              <template #icon><n-icon :component="Undo2" /></template>
            </n-button>
          </template>
          {{ t('components.headerToolbar.undo') }}
        </n-tooltip>

        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button class="header-action-btn" quaternary :disabled="!sceneStore.canRedo" @click="sceneStore.redoStoryEdit()">
              <template #icon><n-icon :component="Redo2" /></template>
            </n-button>
          </template>
          {{ t('components.headerToolbar.redo') }}
        </n-tooltip>
      </div>
      <input type="file" ref="importFileInput" @change="onFileChange" accept=".arc" style="display:none;">
      <input type="file" ref="importSparkInput" @change="onSparkFileChange" accept=".spark" style="display:none;">
    </div>
    <div class="header-right">
      <n-tooltip trigger="hover">
        <template #trigger>
          <n-button text style="font-size: var(--spark-fs-h2); margin-left: 8px;" @click="handleToggleFullscreen">
            <n-icon :component="isFullscreen ? Minimize2 : Maximize2" />
          </n-button>
        </template>
        {{ isFullscreen ? t('components.headerToolbar.exitFullscreen') : t('components.headerToolbar.fullscreen') }}
      </n-tooltip>
      <n-dropdown trigger="click" :options="themeOptions" @select="handleThemeChange">
        <span class="tooltip-dropdown-trigger">
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button text style="font-size: var(--spark-fs-h1); margin-left: 8px;">
                <n-icon :component="currentThemeIcon" />
              </n-button>
            </template>
            {{ t('components.headerToolbar.themeSwitch') }}
          </n-tooltip>
        </span>
      </n-dropdown>
      <n-dropdown trigger="click" :options="localeOptions" @select="handleLocaleChange">
        <span class="tooltip-dropdown-trigger">
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button text style="font-size: var(--spark-fs-h1); margin-left: 8px;">
                <n-icon :component="Languages" />
              </n-button>
            </template>
            {{ t('settings.language.label') }}
          </n-tooltip>
        </span>
      </n-dropdown>
      <div class="user-info">
        <n-text>{{ username || t('components.headerToolbar.loading') }}</n-text>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button @click="handleLogout" text>
              <template #icon>
                <n-icon :component="LogOut" />
              </template>
              {{ t('components.headerToolbar.logout') }}
            </n-button>
          </template>
          {{ t('components.headerToolbar.logoutTitle') }}
        </n-tooltip>
      </div>
      <!-- Tauri 桌面端窗口控制按钮 -->
      <WindowControls variant="header" />
    </div>
  </header>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, computed, h } from 'vue';
import { useI18n } from 'vue-i18n';
import { NButton, NIcon, NText, NDropdown, NTooltip } from 'naive-ui';
import { Archive, BrainCircuit, CloudDownload, CloudUpload, FilePlus2, FolderOpen, Laptop, LogOut, Maximize2, Minimize2, Moon, PaintBucket, Play, Redo2, Share2, Sun, Languages, Undo2 } from '@lucide/vue';
import { useLocaleStore } from '@/components/stores/localeStore';
import type { AppLocale } from '@/i18n/types';
import bus from '@/eventBus';
import ProjectSelector from '../../user/ProjectSelector.vue';
import { useSceneStore } from '@/components/stores/sceneStore';
import { useProjectStore } from '@/components/stores/projectStore';
import { useFileStore } from '@/components/stores/fileStore';
import { useThemeStore } from '@/components/stores/themeStore';
import { absorbStoryMemory, uploadStory, logout as apiLogout, fetchWithAuth } from '@/services/api';
import { exportProjectToSQLite, exportProjectAsSpark, importProjectFromSpark } from '@/services/projectService';
import { useFullscreen } from '@/composables/useFullscreen';
import { useWindowControls } from '@/composables/useWindowControls';
import { useDockMagnify } from '@/composables/useDockMagnify';
import AppBrand from '@/components/share/AppBrand.vue';
import { SPARKARC_GITHUB_URL } from '@/config';
import { closeDeferredBrowserTab, navigateDeferredBrowserTab, openDeferredBrowserTab } from '@/utils/deferredBrowserTab';
import WindowControls from './WindowControls.vue';

const { startDragging, isTauriDesktop: showWinControls } = useWindowControls();

/** 仅在空白区域触发窗口拖拽，按钮/输入等交互元素不触发 */
function onHeaderMousedown(e: MouseEvent) {
  if (!showWinControls.value || e.button !== 0) return;
  const target = e.target as HTMLElement;
  const tag = target.tagName.toLowerCase();
  const interactiveTags = ['button', 'input', 'select', 'textarea', 'a', 'svg', 'path'];
  if (
    interactiveTags.includes(tag) ||
    target.closest(
      'button, a, input, select, textarea, ' +
      '.n-button, .n-switch, .n-dropdown, .n-popover, .n-tooltip, ' +
      '.win-controls, .dock-bar, .user-info, .logo'
    )
  ) {
    return;
  }
  void startDragging();
}

/** ── macOS Dock 放大推开效果（composable） ── */
const { dockRef: dockBarRef, onDockEnter, onDockMove, onDockLeave } = useDockMagnify();

const props = defineProps({
  username: { type: String, default: '' },
});

const { t } = useI18n();

const emit = defineEmits(['open-settings', 'logout', 'open-version-manager']);
const previewing = ref(false);

const importFileInput = ref(null);
function triggerFileImport() { importFileInput.value?.click(); }
function onFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  handleFileSelected(file);
  e.target.value = '';
}

const importSparkInput = ref(null);
function triggerSparkImport() { importSparkInput.value?.click(); }
function onSparkFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  handleSparkImport(file);
  e.target.value = '';
}

// stores
const sceneStore = useSceneStore();
const projectStore = useProjectStore();
const fileStore = useFileStore();
const themeStore = useThemeStore();
const { isFullscreen, preferred, requestFullscreen, toggleFullscreen } = useFullscreen();

const fileOptions = computed(() => [
  { label: t('components.headerToolbar.importArc'), key: 'import', icon: () => h(NIcon, null, { default: () => h(CloudDownload) }) },
  { label: t('components.headerToolbar.exportArc'), key: 'export_arc', icon: () => h(NIcon, null, { default: () => h(CloudUpload) }) },
  {
    label: t('components.headerToolbar.absorbStoryMemory'),
    key: 'absorb_story_memory',
    disabled: !currentFilePath.value,
    icon: () => h(NIcon, null, { default: () => h(BrainCircuit) }),
  },
  { type: 'divider', key: 'project-actions-divider' },
  { label: t('components.headerToolbar.exportProject'), key: 'export_project', icon: () => h(NIcon, null, { default: () => h(Archive) }) },
  { label: t('components.headerToolbar.importProject'), key: 'import_project', icon: () => h(NIcon, null, { default: () => h(PaintBucket) }) },
]);

function handleFileAction(key) {
  if (key === 'import') triggerFileImport();
  else if (key === 'export_arc') exportArc();
  else if (key === 'absorb_story_memory') absorbCurrentStoryMemory();
  else if (key === 'export_project') exportProjectSpark();
  else if (key === 'import_project') triggerSparkImport();
}

const exportingSpark = ref(false);

async function exportProjectSpark() {
  if (!projectStore.currentProject) {
    bus.emit('toast', { type: 'error', message: t('components.headerToolbar.selectProjectFirst') });
    return;
  }
  exportingSpark.value = true;
  try {
    await exportProjectAsSpark(projectStore.currentProject);
    bus.emit('toast', { type: 'success', message: t('components.headerToolbar.exportProjectSuccess') });
  } catch (e: unknown) {
    const errorMessage = e instanceof Error ? e.message : String(e || 'Unknown error');
    bus.emit('toast', { type: 'error', message: `${t('components.headerToolbar.exportProjectFailed')}: ${errorMessage}` });
  } finally {
    exportingSpark.value = false;
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

const themeOptions = computed(() => [
  { label: t('components.headerToolbar.themeLight'), key: 'light', icon: () => h(NIcon, null, { default: () => h(Sun) }) },
  { label: t('components.headerToolbar.themeDark'), key: 'dark', icon: () => h(NIcon, null, { default: () => h(Moon) }) },
  { label: t('components.headerToolbar.themeSystem'), key: 'system', icon: () => h(NIcon, null, { default: () => h(Laptop) }) },
]);

const handleThemeChange = (key) => {
  themeStore.setThemeMode(key);
};

const localeStore = useLocaleStore();

const localeOptions = computed(() => [
  { label: t('locale.zh-CN'), key: 'zh-CN' },
  { label: t('locale.en-US'), key: 'en-US' },
  { label: t('locale.ja-JP'), key: 'ja-JP' },
  { label: t('locale.ko-KR'), key: 'ko-KR' },
]);

const handleLocaleChange = (key: AppLocale) => {
  localeStore.setLocale(key);
};

const currentThemeIcon = computed(() => {
  switch (themeStore.themeMode) {
    case 'light': return Sun;
    case 'dark': return Moon;
    default: return Laptop;
  }
});

const currentFilePath = computed(() => fileStore.selectedFile?.type === 'story' ? fileStore.selectedFile.path : null);

async function handleFileSelected(file) {
  try {
    const res = await uploadStory(projectStore.currentProject, file);
    // 刷新文件树并尝试加载上传的文件
    await fileStore.loadFileTree(projectStore.currentProject);
    const uploaded = res.filename;
    const path = typeof uploaded === 'string' ? uploaded : uploaded?.[0] || null;
    if (path) {
      const match = findFileByPath(fileStore.fileTree, path);
      if (match) {
        fileStore.selectedFile = match;
        await sceneStore.loadStory(match.path);
      }
    }
  bus.emit('toast', { type: 'success', message: t('components.headerToolbar.uploadSuccess') });
  } catch (e: unknown) {
    const errorMessage = e instanceof Error ? e.message : String(e || 'Unknown error');
    bus.emit('toast', { type: 'error', message: `${t('components.headerToolbar.uploadFailed')}: ${errorMessage}` });
  }
}

function findFileByPath(tree, path) {
  for (const item of tree) {
    if (item.type === 'story' && item.path === path) return item;
    if (item.children) {
      const r = findFileByPath(item.children, path);
      if (r) return r;
    }
  }
  return null;
}

function exportArc() {
  // Import the serializer
  import('@/services/arcParser').then(({ serializeToArc }) => {
    const data = sceneStore.scriptData;
    const arcText = Array.isArray(data) ? serializeToArc(data) : String(data || '');
    const blob = new Blob([arcText], { type: 'text/plain; charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    // 优先使用选中文件的 name，退回到 path 的最后一段，再退回默认名
    let base = fileStore.selectedFile?.name
      || (currentFilePath.value ? String(currentFilePath.value).split(/[\\/]/).pop() : '')
      || 'dialogue_script';
    // 清洗 Windows 非法字符
    base = base.replace(/[\\/:*?"<>|]/g, '_').replace(/^\s+|\s+$/g, '').replace(/^\.+|\.+$/g, '');
    // 移除原有扩展名并添加 .arc
    base = base.replace(/\.arc$/i, '');
    base += '.arc';
    a.download = base;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });
}

function handleToggleFullscreen() {
  toggleFullscreen();
}

async function absorbCurrentStoryMemory() {
  if (!projectStore.currentProject || !currentFilePath.value) {
    bus.emit('toast', { type: 'info', message: t('components.headerToolbar.selectStoryFileFirst') });
    return;
  }
  try {
    await absorbStoryMemory(projectStore.currentProject, currentFilePath.value);
    bus.emit('toast', { type: 'success', message: t('components.headerToolbar.absorbStoryMemoryQueued') });
  } catch (e: unknown) {
    const errorMessage = e instanceof Error ? e.message : String(e || 'Unknown error');
    bus.emit('toast', { type: 'error', message: `${t('components.headerToolbar.absorbStoryMemoryFailed')}: ${errorMessage}` });
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
  const saved = await sceneStore.flushStorySave();
  if (!saved) {
    closeDeferredBrowserTab(previewTab);
    return;
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

async function handleLogout() {
  // 先重置 Pinia 状态，防止旧项目名残留导致新用户登录后被 watch immediate 捕获
  projectStore.resetForLogout();
  try { await apiLogout(); } catch {}
  // 切换到应用内登录视图（SPA），由父组件处理
  emit('logout');
}

onMounted(() => {
  if (preferred.value && !document.fullscreenElement) {
    requestFullscreen();
  }
});

function openBlueprint() {
  bus.emit('open-blueprint');
}

const exporting = ref(false);

async function exportToSQLite() {
  if (!projectStore.currentProject) {
    bus.emit('toast', { type: 'error', message: t('components.headerToolbar.selectProjectFirst') });
    return;
  }
  
  exporting.value = true;
  try {
    const result = await exportProjectToSQLite(projectStore.currentProject, true);
    bus.emit('toast', { 
      type: 'success', 
      message: `${t('components.headerToolbar.exportSuccess')}: ${result.chapters}/${result.scenes}`,
      duration: 5000
    });
    console.log('SQLite 数据库路径:', result.db_path);
  } catch (e: unknown) {
    const errorMessage = e instanceof Error ? e.message : String(e || 'Unknown error');
    bus.emit('toast', { 
      type: 'error', 
      message: `${t('components.headerToolbar.exportFailed')}: ${errorMessage}`
    });
  } finally {
    exporting.value = false;
  }
}
</script>

<style scoped>
.no-select, .no-select * {
  -webkit-user-select: none;
  -ms-user-select: none;
  user-select: none;
}

/* ── Dock 栏容器：flex 布局，子元素居中 ── */
.dock-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* ── Dock 子元素：默认无 transition（即时跟手），离开时 JS 添加 .dock-leaving 触发回弹 ── */
.dock-bar > :deep(*) {
  transform-origin: center center;
  will-change: transform;
}
.dock-bar > :deep(.dock-leaving) {
  transition: transform 0.22s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}

/* ── 按钮基础 ── */
.header-action-btn {
  height: 32px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tooltip-dropdown-trigger {
  display: inline-flex;
}

/* ── 点击动画：按下缩放 + 弹性回弹 ── */
.header-action-btn:active {
  transform: scale(0.9) !important;
  transition-duration: 0.08s;
}

/* ── 点击涟漪光晕 ── */
@keyframes dock-click-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(107, 144, 128, 0.45); }
  100% { box-shadow: 0 0 0 10px rgba(107, 144, 128, 0); }
}
.header-action-btn:active {
  animation: dock-click-pulse 0.4s ease-out;
}

.save-icon-stack {
  position: relative;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #9aa0a6;
}

.save-icon-stack .n-icon {
  position: absolute;
  top: 0;
  left: 0;
}

.save-icon-stack .n-icon:last-child {
  transform: translate(3px, 3px);
  opacity: 0.6;
}

.save-icon-stack.is-active {
  color: var(--n-primary-color);
}

.logo {
  display: flex;
  align-items: center;
  text-decoration: none;
  color: inherit;
  cursor: pointer;
}
</style>
