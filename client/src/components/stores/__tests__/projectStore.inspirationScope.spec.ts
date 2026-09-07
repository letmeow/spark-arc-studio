import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

const mocks = vi.hoisted(() => ({
  chatStore: {
    switchProject: vi.fn(),
    resetAllSessions: vi.fn(),
    purgeProjectSessions: vi.fn(),
  },
  sceneStore: {
    workspaceMode: 'script',
    scriptData: [],
    currentFilePath: null,
    currentScene: null,
    currentNode: null,
    nodeParent: null,
    selectionType: '',
    lastScriptwriterThought: '',
    loadWorkspaceMode: vi.fn(async () => undefined),
  },
  fileStore: {
    fileTree: [],
    selectedFile: null,
    loadFileTree: vi.fn(),
  },
  characterStore: {
    list: [],
    map: {},
    loadedForProject: null,
    load: vi.fn(),
  },
  blueprintStore: {
    nodePositions: {},
    connections: [],
    loadBlueprint: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  fetchProjects: vi.fn(async () => []),
  createProject: vi.fn(),
  deleteProject: vi.fn(),
  renameProject: vi.fn(),
  refreshSemanticSearchProject: vi.fn(async () => undefined),
  getInspirations: vi.fn(async () => ({ inspirations: [], unreadCount: 0 })),
  getProjectWorkspaceMode: vi.fn(async () => 'script'),
}));

vi.mock('@/services/apiClient', () => ({
  getUserId: vi.fn(() => 'test-user'),
}));

vi.mock('../chatStore', () => ({ useChatStore: () => mocks.chatStore }));
vi.mock('../sceneStore', () => ({ useSceneStore: () => mocks.sceneStore }));
vi.mock('../fileStore', () => ({ useFileStore: () => mocks.fileStore }));
vi.mock('../characterStore', () => ({ useCharacterStore: () => mocks.characterStore }));
vi.mock('../blueprintStore', () => ({ useBlueprintStore: () => mocks.blueprintStore }));

import { useProjectStore } from '../projectStore';

describe('projectStore 灵感历史范围', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('按项目语境初始化和重置范围，并在组件重挂载时保留用户选择', () => {
    const store = useProjectStore();
    expect(store.inspirationHistoryScope).toBe('drafts');

    store.setCurrentProject('项目 A');
    expect(store.inspirationHistoryScope).toBe('project');

    store.inspirationHistoryScope = 'all';
    expect(useProjectStore().inspirationHistoryScope).toBe('all');

    store.setCurrentProject('项目 A');
    expect(store.inspirationHistoryScope).toBe('all');

    store.setCurrentProject('项目 B');
    expect(store.inspirationHistoryScope).toBe('project');

    store.setCurrentProject(null);
    expect(store.inspirationHistoryScope).toBe('drafts');
  });

  it('删除项目后释放该项目的本地聊天会话，避免同名重建复用旧历史', async () => {
    const { deleteProject } = await import('@/services/api');
    vi.mocked(deleteProject).mockResolvedValueOnce({ success: true });
    const store = useProjectStore();
    store.projects = ['旧项目', '其他项目'];
    store._currentProject = '旧项目';

    await store.deleteCurrentProject();

    expect(mocks.chatStore.purgeProjectSessions).toHaveBeenCalledWith('旧项目');
  });
});
