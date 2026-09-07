import bus from '@/eventBus';
import { defineStore } from 'pinia';
import { fetchProjects, createProject, deleteProject, renameProject, refreshSemanticSearchProject, getInspirations, getProjectWorkspaceMode as fetchProjectWorkspaceMode } from '@/services/api';
import { getUserId } from '@/services/apiClient';
import { i18n } from '@/i18n';
import type { InspirationEntry, InspirationScope } from '@/services/aiContracts';
import { useFileStore } from './fileStore';
import { useCharacterStore } from './characterStore';
import { useChatStore } from './chatStore';
import { useSceneStore } from './sceneStore';
import { useBlueprintStore } from './blueprintStore';
import { useStoryMemoryStore } from './storyMemoryStore';

/**
 * 当前会话已经触发过语义索引刷新检查的项目集合。
 *
 * 进入工作台/切换项目时，每个项目只主动触发一次 /api/semantic-search/refresh，
 * 让后端按 freshness 检测决定是否真的启动后台增量构建。
 * 这样既不会在每次写入时打断流式生成，也避免了反复轮询触发。
 */
const semanticRefreshTriggeredProjects = new Set<string>();

function triggerSemanticRefreshOnce(projectName: string): void {
  if (!projectName) return;
  if (semanticRefreshTriggeredProjects.has(projectName)) return;
  semanticRefreshTriggeredProjects.add(projectName);
  // 异步发起，不 await，不阻塞 UI；失败也不影响项目切换
  void refreshSemanticSearchProject(projectName).catch((err) => {
    console.warn('[semantic] 自动刷新触发失败：', err);
    // 失败时允许下次重试
    semanticRefreshTriggeredProjects.delete(projectName);
  });
}

const LAST_PROJECT_KEY_PREFIX = 'sparkarc_last_project';

/** 按当前用户 ID 生成隔离的 localStorage 键 */
function getLastProjectKey(): string {
  const uid = getUserId();
  return uid ? `${LAST_PROJECT_KEY_PREFIX}:${uid}` : LAST_PROJECT_KEY_PREFIX;
}

type PendingSynopsisAdoption = {
  projectName?: string;
  logline?: string;
  inspiration?: string;
  lengthHint?: unknown;
  pov?: string;
  autoGenerateSynopsis?: boolean;
  autoGenerateBeats?: boolean;
  [key: string]: unknown;
};

type PendingStructureAdoption = {
  projectName?: string;
  context?: string;
  guidance?: string;
  lengthHint?: unknown;
  autoGenerateOutline?: boolean;
  [key: string]: unknown;
};

type BoundInspirationEntry = Pick<InspirationEntry, 'id' | 'source' | 'content'>;
type ProjectWorkspaceMode = 'script' | 'novel';
type ProjectCreateResult = {
  projectName: string;
  workspaceMode: ProjectWorkspaceMode;
};

type ProjectStoreState = {
  projects: string[];
  projectWorkspaceModes: Record<string, ProjectWorkspaceMode>;
  _currentProject: string | null;
  currentInspiration: string;
  currentInspirationId: string | null;
  boundInspiration: string;
  boundInspirationSource: string;
  boundInspirationId: string | null;
  inspirationHistoryScope: InspirationScope;
  pendingSynopsisAdoption: PendingSynopsisAdoption | null;
  pendingStructureAdoption: PendingStructureAdoption | null;
};

export const useProjectStore = defineStore('project', {
  state: (): ProjectStoreState => ({
    projects: [],
    projectWorkspaceModes: {},
    _currentProject: null,
    currentInspiration: '', // 当前灵感，供大纲页面使用
    currentInspirationId: null,
    boundInspiration: '',
    boundInspirationSource: '',
    boundInspirationId: null,
    inspirationHistoryScope: 'drafts',
    pendingSynopsisAdoption: null,
    pendingStructureAdoption: null,
  }),
  getters: {
    currentProject: (state): string => state._currentProject || '',
    currentWorkspaceMode: (state): ProjectWorkspaceMode => {
      const projectName = state._currentProject || '';
      return state.projectWorkspaceModes[projectName] === 'novel' ? 'novel' : 'script';
    },
    projectMode: (state) => (projectName: string | null | undefined): ProjectWorkspaceMode => {
      const key = typeof projectName === 'string' ? projectName.trim() : '';
      return state.projectWorkspaceModes[key] === 'novel' ? 'novel' : 'script';
    },
  },
  actions: {
    async loadProjects() {
      try {
        let projects = await fetchProjects();
        // 过滤掉无效的项目名称，以防意外创建
        projects = projects.filter((p) => {
          if (typeof p !== 'string') return false;
          const normalized = p.trim();
          return normalized && normalized !== 'undefined' && normalized !== 'null';
        });
        this.projects = projects;
        void this.refreshProjectWorkspaceModes(projects);
        if (Array.isArray(projects) && projects.length > 0) {
          // 优先恢复上次缓存的项目（直接访问不带 URL 锁定时）
          const lastProject = localStorage.getItem(getLastProjectKey());
          if (lastProject && projects.includes(lastProject)) {
            if (this._currentProject !== lastProject) {
              this.setCurrentProject(lastProject);
            }
          } else if (!this._currentProject || !projects.includes(this._currentProject)) {
            // 当前未选择或选择的项目不再存在时，选择第一个项目
            this.setCurrentProject(projects[0]);
          }
        } else {
          // 无项目时清空当前项目
          this.setCurrentProject(null);
        }
      } catch (error: unknown) {
        console.error('加载项目失败:', error);
      }
    },
    async refreshProjectWorkspaceModes(projects?: string[]) {
      const targetProjects = Array.isArray(projects) ? projects : this.projects;
      const validProjects = targetProjects
        .map((projectName) => (typeof projectName === 'string' ? projectName.trim() : ''))
        .filter((projectName) => projectName && projectName !== 'undefined' && projectName !== 'null');
      const entries = await Promise.all(validProjects.map(async (projectName): Promise<[string, ProjectWorkspaceMode]> => {
        try {
          const mode = await fetchProjectWorkspaceMode(projectName);
          return [projectName, mode === 'novel' ? 'novel' : 'script'];
        } catch (error) {
          console.warn('加载项目创作模式失败:', projectName, error);
          return [projectName, 'script'];
        }
      }));
      const nextModes: Record<string, ProjectWorkspaceMode> = {};
      for (const [projectName, mode] of entries) {
        nextModes[projectName] = mode;
      }
      this.projectWorkspaceModes = nextModes;
    },
    setCurrentProject(projectName: string | string[] | null | undefined) {
      // 纠正误传数组的情况，取第一个作为当前项目
      if (Array.isArray(projectName)) {
        projectName = projectName[0] ?? null;
      }

      // 如果传入既不是字符串也不是 null/undefined，直接忽略
      if (projectName !== null && projectName !== undefined && typeof projectName !== 'string') {
        console.warn('非法的项目名称，已忽略:', projectName);
        return;
      }

      const normalizedProjectName = typeof projectName === 'string' ? projectName.trim() : projectName;
      const safeProjectName = !normalizedProjectName || normalizedProjectName === 'undefined' || normalizedProjectName === 'null'
        ? null
        : normalizedProjectName;

      // 避免重复设置触发不必要的加载
      if (this._currentProject === safeProjectName) return;

      this._currentProject = safeProjectName;
      this.inspirationHistoryScope = safeProjectName ? 'project' : 'drafts';

      // 缓存最后切换的项目，下次访问时自动恢复
      if (safeProjectName) {
        localStorage.setItem(getLastProjectKey(), safeProjectName);
      } else {
        localStorage.removeItem(getLastProjectKey());
      }

      // 聊天会话按项目隔离；切换时只改变可见会话，其他项目可继续后台生成。
      const chatStore = useChatStore();
      chatStore.switchProject(safeProjectName);

      // 清空剧本编辑器，避免残留旧项目的场景数据
      const sceneStore = useSceneStore();
      sceneStore.scriptData = sceneStore.workspaceMode === 'novel' ? '' : [];
      sceneStore.currentFilePath = null;
      sceneStore.currentScene = null;
      sceneStore.currentNode = null;
      sceneStore.nodeParent = null;
      sceneStore.selectionType = sceneStore.workspaceMode === 'novel' ? 'novel' : '';
      sceneStore.lastScriptwriterThought = '';

      // 清空灵感数据
      this.currentInspiration = '';

      // StoryMemory 总览按项目隔离，切换时清空旧项目数据
      const storyMemoryStore = useStoryMemoryStore();
      storyMemoryStore.reset();
      this.currentInspirationId = null;
      this.boundInspiration = '';
      this.boundInspirationSource = '';
      this.boundInspirationId = null;
      this.pendingSynopsisAdoption = null;
      this.pendingStructureAdoption = null;

      const fileStore = useFileStore();
      const chrStore = useCharacterStore();
      const blueprintStore = useBlueprintStore();
      if (this._currentProject) {
        const activeProject = this._currentProject;
        // 先异步加载项目创作模式，完成后用正确格式加载文件树
        sceneStore.loadWorkspaceMode(activeProject).then(() => {
          if (this._currentProject !== activeProject) return;
          this.projectWorkspaceModes = {
            ...this.projectWorkspaceModes,
            [activeProject]: sceneStore.workspaceMode === 'novel' ? 'novel' : 'script',
          };
          fileStore.loadFileTree(activeProject, sceneStore.workspaceMode);
        });
        chrStore.load(this._currentProject);
        blueprintStore.loadBlueprint(this._currentProject);
        // 进入工作台/切换项目时统一触发一次语义索引差异检查（仅一次/每会话/每项目）
        triggerSemanticRefreshOnce(this._currentProject);
        void this.refreshCurrentProjectInspiration(this._currentProject);
      } else {
        // 没有项目时清空文件树和蓝图
        fileStore.fileTree = [];
        fileStore.selectedFile = null;
        chrStore.load(null);
        blueprintStore.loadBlueprint(null);
      }
    },
    applyBoundInspiration(entry: BoundInspirationEntry | null | undefined) {
      const safeEntry = entry || null;
      this.boundInspiration = safeEntry?.content || '';
      this.boundInspirationSource = safeEntry?.source || '';
      this.boundInspirationId = safeEntry?.id || null;
    },
    async refreshCurrentProjectInspiration(projectName?: string | null) {
      const targetProject = typeof projectName === 'string'
        ? projectName.trim()
        : (this._currentProject || '').trim();
      if (!targetProject) {
        this.applyBoundInspiration(null);
        return null;
      }
      try {
        const result = await getInspirations({ scope: 'project', project: targetProject });
        const entry = Array.isArray(result?.inspirations) ? result.inspirations[0] || null : null;
        if (this._currentProject === targetProject) {
          this.applyBoundInspiration(entry);
        }
        return entry;
      } catch (error: unknown) {
        console.warn('刷新当前项目绑定灵感失败:', error);
        if (this._currentProject === targetProject) {
          this.applyBoundInspiration(null);
        }
        return null;
      }
    },
    async createProject() {
      const result = await new Promise<ProjectCreateResult | null>((resolve) => bus.emit('project-create', { resolve }));
      const finalName = typeof result?.projectName === 'string' ? result.projectName.trim() : '';
      if (finalName && finalName !== 'undefined' && finalName !== 'null') {
        try {
          const workspaceMode = result?.workspaceMode === 'novel' ? 'novel' : 'script';
          await createProject(finalName, workspaceMode);
          const chatStore = useChatStore();
          chatStore.purgeProjectSessions(finalName);
          this.projectWorkspaceModes = { ...this.projectWorkspaceModes, [finalName]: workspaceMode };
          await this.loadProjects();
          // 创建成功后切换到新项目
          this.setCurrentProject(finalName);
          return finalName;
        } catch (error: unknown) {
          const errorMessage = error instanceof Error ? error.message : String(error || '未知错误');
          bus.emit('toast', { type: 'error', message: i18n.global.t('components.projectCreateModal.createFailed', { error: errorMessage }) });
          return null;
        }
      } else if (result !== null) { // 如果不是用户取消，而是输入了无效名称
        bus.emit('toast', { type: 'error', message: i18n.global.t('components.projectCreateModal.invalidName') });
      }
      return null;
    },
    async deleteCurrentProject() {
      if (!this.currentProject) {
        bus.emit('toast', { type: 'error', message: '没有选中的项目' });
        return;
      }
      const deletedProject = this.currentProject;
      try {
        await deleteProject(this.currentProject);
        const chatStore = useChatStore();
        chatStore.purgeProjectSessions(deletedProject);
        const nextModes = { ...this.projectWorkspaceModes };
        delete nextModes[deletedProject];
        this.projectWorkspaceModes = nextModes;
        await this.loadProjects();
        this.setCurrentProject(this.projects[0] ?? null);
        bus.emit('toast', { type: 'success', message: '项目已删除' });
      } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : String(error || '未知错误');
        bus.emit('toast', { type: 'error', message: `删除项目失败: ${errorMessage}` });
      }
    },
    async renameCurrentProject(newName: string) {
      if (!this.currentProject) {
        bus.emit('toast', { type: 'error', message: '没有选中的项目' });
        return;
      }
      const trimmed = newName.trim();
      if (!trimmed || trimmed === 'undefined' || trimmed === 'null') {
        bus.emit('toast', { type: 'error', message: '无效的项目名称' });
        return;
      }
      if (trimmed === this.currentProject) return;
      const oldName = this.currentProject;
      try {
        const result = await renameProject(this.currentProject, trimmed);
        const finalName = result.newName || trimmed;
        const oldMode = this.projectWorkspaceModes[oldName];
        const nextModes = { ...this.projectWorkspaceModes };
        delete nextModes[oldName];
        if (oldMode) nextModes[finalName] = oldMode;
        this.projectWorkspaceModes = nextModes;
        await this.loadProjects();
        this.setCurrentProject(finalName);
        bus.emit('toast', { type: 'success', message: '项目已重命名' });
      } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : String(error || '未知错误');
        bus.emit('toast', { type: 'error', message: `重命名项目失败: ${errorMessage}` });
      }
    },
    setPendingSynopsisAdoption(payload: PendingSynopsisAdoption | null | undefined) {
      this.pendingSynopsisAdoption = payload || null;
    },
    clearPendingSynopsisAdoption() {
      this.pendingSynopsisAdoption = null;
    },
    setPendingStructureAdoption(payload: PendingStructureAdoption | null | undefined) {
      this.pendingStructureAdoption = payload || null;
    },
    clearPendingStructureAdoption() {
      this.pendingStructureAdoption = null;
    },
    /**
     * 登出时重置项目状态，防止切换用户后残留旧项目名
     * 触发后端 ensure_project_* 副作用而意外创建幽灵项目目录。
     */
    resetForLogout() {
      this._currentProject = null;
      this.projects = [];
      this.projectWorkspaceModes = {};
      this.currentInspiration = '';
      this.currentInspirationId = null;
      this.boundInspiration = '';
      this.boundInspirationSource = '';
      this.boundInspirationId = null;
      this.inspirationHistoryScope = 'drafts';
      this.pendingSynopsisAdoption = null;
      this.pendingStructureAdoption = null;
      localStorage.removeItem(getLastProjectKey());
      // 登出后清空“已触发刷新”的项目集合，下个用户重新进入再触发
      semanticRefreshTriggeredProjects.clear();

      // 同步清空关联 store，避免残留项目名被 watch immediate 捕获
      const chatStore = useChatStore();
      chatStore.resetAllSessions();

      const sceneStore = useSceneStore();
      sceneStore.scriptData = sceneStore.workspaceMode === 'novel' ? '' : [];
      sceneStore.currentFilePath = null;
      sceneStore.currentScene = null;
      sceneStore.currentNode = null;
      sceneStore.nodeParent = null;
      sceneStore.selectionType = sceneStore.workspaceMode === 'novel' ? 'novel' : '';
      sceneStore.lastScriptwriterThought = '';

      const fileStore = useFileStore();
      fileStore.fileTree = [];
      fileStore.selectedFile = null;

      const chrStore = useCharacterStore();
      chrStore.list = [];
      chrStore.map = {};
      chrStore.loadedForProject = null;

      const blueprintStore = useBlueprintStore();
      blueprintStore.nodePositions = {};
      blueprintStore.connections = [];

      const storyMemoryStore = useStoryMemoryStore();
      storyMemoryStore.reset();
    },
  },
});
