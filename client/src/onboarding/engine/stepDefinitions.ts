/**
 * 步骤定义 - 纯数据配置
 *
 * 所有引导步骤的配置集中在此文件，与动画逻辑和组件分离。
 * 修改引导顺序/文案/目标只需编辑此文件。
 */
import type { OnboardingStep, OnboardingScene } from './OnboardingEngine';

// ==================== 辅助函数 ====================

/**
 * 创建切换视图的 beforeEnter 钩子
 * 引导到某个视图时自动切换过去，确保目标元素可见
 */
function switchViewBeforeEnter(view: string): () => Promise<void> {
  return async () => {
    const { useViewStore } = await import('../../components/stores/viewStore');
    const viewStore = useViewStore();
    viewStore.setView(view as typeof viewStore.currentView);
    // 等待视图切换渲染
    await new Promise(r => setTimeout(r, 350));
  };
}

/** 打开移动端设置抽屉，供引导定位 AI 配置区。 */
async function openMobileSettingsBeforeEnter(): Promise<void> {
  const trigger = document.querySelector<HTMLButtonElement>('.mobile-settings-trigger');
  const drawer = document.querySelector('.mobile-settings-drawer');
  const isDrawerVisible = Boolean(drawer && drawer.getClientRects().length > 0);
  if (!isDrawerVisible) trigger?.click();
  await new Promise(r => setTimeout(r, 350));
}

/** 离开 AI 配置引导后关闭移动端设置抽屉。 */
async function closeMobileSettingsBeforeEnter(): Promise<void> {
  const drawer = document.querySelector('.mobile-settings-drawer');
  if (!drawer || drawer.getClientRects().length === 0) return;
  const closeButton = document.querySelector<HTMLButtonElement>('.mobile-settings-drawer .n-base-close');
  if (closeButton) {
    closeButton.click();
  } else {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  }
  await new Promise(r => setTimeout(r, 300));
}

// ==================== 桌面端步骤定义 ====================

const desktopWorkspaceBaseSteps: OnboardingStep[] = [
  // ── 顶部区域 ──
  // 1. 全局操作栏（屏幕最顶部）
  {
    id: 'dw-header-toolbar',
    target: '.app-header',
    placement: 'bottom',
    titleKey: 'onboarding.desktop.workspace.headerToolbarTitle',
    descKey: 'onboarding.desktop.workspace.headerToolbarDesc',
    spotlight: true,
    spotlightPadding: 4,
  },
  // 2. 导航栏总览
  {
    id: 'dw-activity-bar',
    target: '.activity-bar',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.activityBarTitle',
    descKey: 'onboarding.desktop.workspace.activityBarDesc',
    spotlight: true,
    spotlightPadding: 4,
    scrollIntoView: false,
  },
  // ── 侧边栏：从上到下，严格按默认顺序 ──
  // 3. AI 沉浸聊天
  {
    id: 'dw-chat',
    target: '.activity-list .activity-item[data-view="chat"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.chatTitle',
    descKey: 'onboarding.desktop.workspace.chatDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('chat'),
  },
  // 4. 灵感与世界观
  {
    id: 'dw-world',
    target: '.activity-list .activity-item[data-view="world"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.worldTitle',
    descKey: 'onboarding.desktop.workspace.worldDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('world'),
  },
  // 5. 故事梗概
  {
    id: 'dw-synopsis',
    target: '.activity-list .activity-item[data-view="synopsis"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.synopsisTitle',
    descKey: 'onboarding.desktop.workspace.synopsisDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('synopsis'),
  },
  // 6. 大纲编排
  {
    id: 'dw-structure',
    target: '.activity-list .activity-item[data-view="structure"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.structureTitle',
    descKey: 'onboarding.desktop.workspace.structureDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('structure'),
  },
  // 7. 剧本创作
  {
    id: 'dw-production',
    target: '.activity-list .activity-item[data-view="production"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.productionTitle',
    descKey: 'onboarding.desktop.workspace.productionDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('production'),
  },
  // 8. 故事蓝图
  {
    id: 'dw-blueprint',
    target: '.activity-list .activity-item[data-view="blueprint"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.blueprintTitle',
    descKey: 'onboarding.desktop.workspace.blueprintDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('blueprint'),
  },
  // 9. 风格管理
  {
    id: 'dw-style',
    target: '.activity-list .activity-item[data-view="style"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.styleTitle',
    descKey: 'onboarding.desktop.workspace.styleDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('style'),
  },
  // 10. 引擎绑定
  {
    id: 'dw-engine',
    target: '.activity-list .activity-item[data-view="engine"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.engineTitle',
    descKey: 'onboarding.desktop.workspace.engineDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('engine'),
  },
  // ── 侧边栏底部：分隔线以下 ──
  // 11. 管理中心
  {
    id: 'dw-admin',
    target: '.activity-bar .activity-item[data-view="dashboard"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.adminTitle',
    descKey: 'onboarding.desktop.workspace.adminDesc',
    spotlight: true,
    spotlightPadding: 12,
    scrollIntoView: true,
    beforeEnter: switchViewBeforeEnter('dashboard'),
  },
  // 12. 设置
  {
    id: 'dw-settings',
    target: '.activity-bar .activity-item[data-view="settings"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.settingsTitle',
    descKey: 'onboarding.desktop.workspace.settingsDesc',
    spotlight: true,
    spotlightPadding: 12,
    scrollIntoView: true,
    beforeEnter: switchViewBeforeEnter('settings'),
  },
  // ── 浮动元素 ──
  // 13. AI 导演浮窗
  {
    id: 'dw-chat-float',
    target: '.chat-float-launch',
    placement: 'left',
    titleKey: 'onboarding.desktop.workspace.chatFloatTitle',
    descKey: 'onboarding.desktop.workspace.chatFloatDesc',
    spotlight: true,
    spotlightPadding: 12,
    allowInteraction: true,
    beforeEnter: async () => {
      const { useChatStore } = await import('../../components/stores/chatStore');
      const chat = useChatStore();
      chat.setExpanded(false); // 先收起，确保 launch 按钮可见可定位
      await new Promise(r => setTimeout(r, 300));
    },
  },
  // 14. 完成
  {
    id: 'dw-complete',
    target: 'body',
    placement: 'center',
    titleKey: 'onboarding.desktop.workspace.completeTitle',
    descKey: 'onboarding.desktop.workspace.completeDesc',
    spotlight: false,
  },
];

function pageStep(
  id: string,
  target: OnboardingStep['target'],
  placement: OnboardingStep['placement'],
  page: string,
  section: string,
  beforeEnter?: () => Promise<void>,
): OnboardingStep {
  return {
    id,
    target,
    placement,
    titleKey: `onboarding.pages.${page}.${section}Title`,
    descKey: `onboarding.pages.${page}.${section}Desc`,
    spotlight: true,
    spotlightPadding: 8,
    beforeEnter,
  };
}

const desktopHomeSteps = [
  pageStep('home-composer', '.home-view .home-composer', 'bottom', 'home', 'composer', switchViewBeforeEnter('home')),
  pageStep('home-recents', '.home-view .home-recents', 'top', 'home', 'recents'),
  pageStep('home-muse', '.home-view .home-muse-line', 'top', 'home', 'muse'),
];

const desktopChatSteps = [
  pageStep('chat-team', '.chat-desktop-view .chat-panel-header', 'bottom', 'chat', 'team', switchViewBeforeEnter('chat')),
  pageStep('chat-history', '.chat-desktop-view .chat-panel-body', 'left', 'chat', 'history'),
  pageStep('chat-input', '.chat-desktop-view .chat-input-wrapper', 'top', 'chat', 'input'),
];

const desktopWorldSteps = [
  pageStep('world-seed', '.world-view .world-panel-left', 'right', 'world', 'seed', switchViewBeforeEnter('world')),
  pageStep('world-workshop', '.world-view .world-panel-result', 'right', 'world', 'workshop'),
  pageStep('world-lorebook', '.world-view .world-panel-center', 'left', 'world', 'lorebook'),
  pageStep('world-tools', '.world-view .world-panel-right', 'left', 'world', 'tools'),
];

const desktopSynopsisSteps = [
  pageStep('synopsis-context', '.synopsis-grid .context-panel', 'right', 'synopsis', 'context', switchViewBeforeEnter('synopsis')),
  pageStep('synopsis-beats', '.synopsis-grid .beats-panel', 'right', 'synopsis', 'beats'),
  pageStep('synopsis-editor', '.synopsis-grid .editor-panel', 'left', 'synopsis', 'editor'),
];

const desktopStructureSteps = [
  pageStep('structure-outline', '.view-container .outline-panel', 'right', 'structure', 'outline', switchViewBeforeEnter('structure')),
  pageStep('structure-planning', '.view-container .planning-panel', 'left', 'structure', 'planning'),
];

const desktopProductionSteps = [
  pageStep('production-files', '.production-shell .sidebar-panel', 'right', 'production', 'files', switchViewBeforeEnter('production')),
  pageStep('production-editor', '.production-shell .center-panel', 'left', 'production', 'editor'),
  pageStep(
    'production-inspector',
    () => {
      const inspector = document.querySelector('.production-shell .inspector-panel');
      if (inspector && inspector.getClientRects().length > 0) return inspector;
      return document.querySelector('.production-shell .center-panel');
    },
    'left',
    'production',
    'inspector',
  ),
];

const desktopBlueprintSteps = [
  pageStep('blueprint-toolbar', '.blueprint-desktop .blueprint-toolbar', 'bottom', 'blueprint', 'toolbar', switchViewBeforeEnter('blueprint')),
  pageStep('blueprint-canvas', '.blueprint-desktop .blueprint-canvas', 'left', 'blueprint', 'canvas'),
];

const desktopStyleSteps = [
  pageStep('style-actions', '.view-container .spark-desktop-header__actions', 'bottom', 'style', 'actions', switchViewBeforeEnter('style')),
  pageStep('style-library', '.view-container .style-content', 'left', 'style', 'library'),
];

const desktopEngineSteps = [
  pageStep('engine-flow', '.agent-flow-blueprint .blueprint-canvas', 'left', 'engine', 'flow', switchViewBeforeEnter('engine')),
  pageStep('engine-skills', '.view-container .skills-trigger-btn', 'right', 'engine', 'skills'),
];

const desktopDashboardSteps = [
  pageStep('dashboard-usage', '.admin-container .admin-column:first-child', 'right', 'dashboard', 'usage', switchViewBeforeEnter('dashboard')),
  pageStep('dashboard-projects', '.admin-container .admin-column:nth-child(2)', 'left', 'dashboard', 'projects'),
];

const desktopSettingsSteps = [
  pageStep('settings-models', '.settings-columns .settings-col--left', 'right', 'settings', 'models', switchViewBeforeEnter('settings')),
  {
    ...pageStep('settings-platforms', '.settings-columns .settings-col--middle', 'right', 'settings', 'platforms'),
    detailKeys: [
      'onboarding.desktop.aiSetup.systemPlatform',
      'onboarding.desktop.aiSetup.personalOverride',
      'onboarding.desktop.aiSetup.customPlatform',
    ],
  },
  pageStep('settings-preferences', '.settings-columns .settings-col--right', 'left', 'settings', 'preferences'),
];

export const desktopPageScenes: OnboardingScene[] = [
  { id: 'page-home', steps: desktopHomeSteps },
  { id: 'page-chat', steps: desktopChatSteps },
  { id: 'page-world', steps: desktopWorldSteps },
  { id: 'page-synopsis', steps: desktopSynopsisSteps },
  { id: 'page-structure', steps: desktopStructureSteps },
  { id: 'page-production', steps: desktopProductionSteps },
  { id: 'page-blueprint', steps: desktopBlueprintSteps },
  { id: 'page-style', steps: desktopStyleSteps },
  { id: 'page-engine', steps: desktopEngineSteps },
  { id: 'page-dashboard', steps: desktopDashboardSteps },
  { id: 'page-settings', steps: desktopSettingsSteps },
];

const workspaceChromeSteps = desktopWorkspaceBaseSteps.filter(step =>
  ['dw-header-toolbar', 'dw-activity-bar'].includes(step.id),
);
const workspaceClosingSteps = desktopWorkspaceBaseSteps.filter(step =>
  ['dw-chat-float', 'dw-complete'].includes(step.id),
);

export const desktopWorkspaceSteps: OnboardingStep[] = [
  {
    id: 'dw-ai-setup-overview',
    target: 'body',
    placement: 'center',
    titleKey: 'onboarding.desktop.workspace.aiSetupTitle',
    descKey: 'onboarding.desktop.workspace.aiSetupDesc',
    detailKeys: [
      'onboarding.desktop.aiSetup.systemPlatform',
      'onboarding.desktop.aiSetup.adminBoundary',
      'onboarding.desktop.aiSetup.personalOverride',
      'onboarding.desktop.aiSetup.customPlatform',
      'onboarding.desktop.aiSetup.modelUsage',
    ],
    spotlight: false,
  },
  {
    ...desktopSettingsSteps[1],
    id: 'dw-ai-platforms',
    beforeEnter: switchViewBeforeEnter('settings'),
  },
  {
    ...desktopSettingsSteps[0],
    id: 'dw-ai-model-usage',
  },
  {
    id: 'dw-workflow-overview',
    target: 'body',
    placement: 'center',
    titleKey: 'onboarding.desktop.workspace.workflowTitle',
    descKey: 'onboarding.desktop.workspace.workflowDesc',
    detailKeys: [
      'onboarding.desktop.workflow.inspiration',
      'onboarding.desktop.workflow.world',
      'onboarding.desktop.workflow.synopsis',
      'onboarding.desktop.workflow.structure',
      'onboarding.desktop.workflow.production',
      'onboarding.desktop.workflow.finish',
    ],
    spotlight: false,
  },
  ...workspaceChromeSteps,
  {
    id: 'dw-home',
    target: '.activity-list .activity-item[data-view="home"]',
    placement: 'right',
    titleKey: 'onboarding.desktop.workspace.homeTitle',
    descKey: 'onboarding.desktop.workspace.homeDesc',
    spotlight: true,
    spotlightPadding: 12,
    beforeEnter: switchViewBeforeEnter('home'),
  },
  ...desktopHomeSteps,
  ...desktopChatSteps,
  ...desktopWorldSteps,
  ...desktopSynopsisSteps,
  ...desktopStructureSteps,
  ...desktopProductionSteps,
  ...desktopBlueprintSteps,
  ...desktopStyleSteps,
  ...desktopEngineSteps,
  ...workspaceClosingSteps,
];

// ==================== 移动端步骤定义 ====================

/**
 * 滚动移动端 FlowCard 到指定步骤
 * 使用容器 scrollTop 而非 scrollIntoView，兼容 scroll-snap
 */
function scrollMobileStep(stepNum: number): () => Promise<void> {
  return async () => {
    const container = document.querySelector('.flow-container') as HTMLElement;
    const target = document.getElementById(`step-${stepNum}`);
    if (container && target) {
      container.scrollTo({ top: target.offsetTop, behavior: 'smooth' });
      await new Promise(r => setTimeout(r, 500));
    }
  };
}

/**
 * 移动端统一引导步骤
 *
 * 移动端布局与桌面端完全不同：
 * - 顶部 flow-header（Logo + 操作按钮）
 * - 垂直滚动 FlowCard（6步创作流程）
 * - 右侧 StepIndicator（导航图标）
 * - 底部 GlobalChatFloat（抽屉式聊天）
 * - 设置/风格/引擎/管理 在抽屉中
 */
export const mobileWorkspaceSteps: OnboardingStep[] = [
  {
    id: 'mw-ai-setup-overview',
    target: 'body',
    placement: 'center',
    titleKey: 'onboarding.desktop.workspace.aiSetupTitle',
    descKey: 'onboarding.desktop.workspace.aiSetupDesc',
    detailKeys: [
      'onboarding.desktop.aiSetup.systemPlatform',
      'onboarding.desktop.aiSetup.adminBoundary',
      'onboarding.desktop.aiSetup.personalOverride',
      'onboarding.desktop.aiSetup.customPlatform',
      'onboarding.desktop.aiSetup.modelUsage',
    ],
    spotlight: false,
  },
  {
    id: 'mw-ai-platforms',
    target: '.mobile-settings-drawer .ai-manager',
    placement: 'bottom',
    titleKey: 'onboarding.pages.settings.platformsTitle',
    descKey: 'onboarding.pages.settings.platformsDesc',
    detailKeys: [
      'onboarding.desktop.aiSetup.personalOverride',
      'onboarding.desktop.aiSetup.customPlatform',
    ],
    spotlight: true,
    spotlightPadding: 6,
    scrollIntoView: true,
    beforeEnter: openMobileSettingsBeforeEnter,
  },
  {
    id: 'mw-ai-model-usage',
    target: '.mobile-settings-drawer .model-usage-manager',
    placement: 'top',
    titleKey: 'onboarding.pages.settings.modelsTitle',
    descKey: 'onboarding.pages.settings.modelsDesc',
    detailKeys: ['onboarding.desktop.aiSetup.modelUsage'],
    spotlight: true,
    spotlightPadding: 6,
    scrollIntoView: true,
    beforeEnter: openMobileSettingsBeforeEnter,
  },
  {
    id: 'mw-workflow-overview',
    target: 'body',
    placement: 'center',
    titleKey: 'onboarding.desktop.workspace.workflowTitle',
    descKey: 'onboarding.desktop.workspace.workflowDesc',
    detailKeys: [
      'onboarding.desktop.workflow.inspiration',
      'onboarding.desktop.workflow.world',
      'onboarding.desktop.workflow.synopsis',
      'onboarding.desktop.workflow.structure',
      'onboarding.desktop.workflow.production',
      'onboarding.desktop.workflow.finish',
    ],
    spotlight: false,
    beforeEnter: closeMobileSettingsBeforeEnter,
  },
  // ── 顶部导航栏 ──
  // 1. 顶部操作栏
  {
    id: 'mw-header',
    target: '.flow-header',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.workspace.headerTitle',
    descKey: 'onboarding.mobile.workspace.headerDesc',
    spotlight: true,
    spotlightPadding: 4,
  },
  // ── 右侧导航指示器 ──
  // 2. 步骤导航
  {
    id: 'mw-step-indicator',
    target: '.flow-nav',
    placement: 'left',
    titleKey: 'onboarding.mobile.workspace.stepIndicatorTitle',
    descKey: 'onboarding.mobile.workspace.stepIndicatorDesc',
    spotlight: true,
    spotlightPadding: 4,
  },
  // ── FlowCard 创作流程（6步） ──
  // 3. 灵感
  {
    id: 'mw-muse',
    target: '#step-1 .world-mobile-flow .flow-section:first-child',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.flow.museTitle',
    descKey: 'onboarding.mobile.flow.museDesc',
    spotlight: true,
    beforeEnter: scrollMobileStep(1),
  },
  // 4. 世界观
  {
    id: 'mw-world',
    target: '#step-2 .lorebook-mobile-flow .flow-section:first-child',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.flow.worldTitle',
    descKey: 'onboarding.mobile.flow.worldDesc',
    spotlight: true,
    beforeEnter: scrollMobileStep(2),
  },
  // 5. 故事梗概
  {
    id: 'mw-synopsis',
    target: '#step-3 .synopsis-mobile-flow .flow-section:first-child',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.flow.synopsisTitle',
    descKey: 'onboarding.mobile.flow.synopsisDesc',
    spotlight: true,
    beforeEnter: scrollMobileStep(3),
  },
  // 6. 大纲编排
  {
    id: 'mw-structure',
    target: '#step-4 .structure-mobile-flow .control-section',
    placement: 'top',
    titleKey: 'onboarding.mobile.flow.structureTitle',
    descKey: 'onboarding.mobile.flow.structureDesc',
    spotlight: true,
    beforeEnter: scrollMobileStep(4),
  },
  // 7. 剧本创作
  {
    id: 'mw-production',
    target: '#step-5 .production-mobile .workbench-context-bar',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.flow.productionTitle',
    descKey: 'onboarding.mobile.flow.productionDesc',
    spotlight: true,
    beforeEnter: scrollMobileStep(5),
  },
  // 8. 故事蓝图
  {
    id: 'mw-blueprint',
    target: '#step-6 .relation-checker-mobile .relation-control-bar',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.flow.blueprintTitle',
    descKey: 'onboarding.mobile.flow.blueprintDesc',
    spotlight: true,
    beforeEnter: scrollMobileStep(6),
  },
  // ── AI 聊天浮窗 ──
  // 9. AI 导演浮窗按钮
  {
    id: 'mw-chat-float',
    target: '.chat-float-launch',
    placement: 'top',
    titleKey: 'onboarding.mobile.workspace.chatFloatTitle',
    descKey: 'onboarding.mobile.workspace.chatFloatDesc',
    spotlight: true,
    spotlightPadding: 12,
    allowInteraction: true,
  },
  // ── 设置抽屉入口 ──
  // 10. 设置按钮（顶部导航栏内）
  {
    id: 'mw-settings-btn',
    target: '.flow-header .header-right button:last-child',
    placement: 'bottom',
    titleKey: 'onboarding.mobile.workspace.settingsBtnTitle',
    descKey: 'onboarding.mobile.workspace.settingsBtnDesc',
    spotlight: true,
    spotlightPadding: 12,
    allowInteraction: true,
  },
  // 11. 完成
  {
    id: 'mw-complete',
    target: 'body',
    placement: 'center',
    titleKey: 'onboarding.mobile.workspace.completeTitle',
    descKey: 'onboarding.mobile.workspace.completeDesc',
    hintKey: 'onboarding.mobile.workspace.completeHint',
    spotlight: false,
  },
];

/**
 * 移动端标题会随当前创作卡片变化，因此为每张卡片注册独立的页面教程。
 * 完整工作台教程仍复用 mobileWorkspaceSteps，不与页面重看入口混用。
 */
export const mobilePageSceneIds = [
  'page-mobile-muse',
  'page-mobile-world',
  'page-mobile-synopsis',
  'page-mobile-structure',
  'page-mobile-production',
  'page-mobile-blueprint',
] as const;

const mobilePageStepIds = [
  'mw-muse',
  'mw-world',
  'mw-synopsis',
  'mw-structure',
  'mw-production',
  'mw-blueprint',
] as const;

export const mobilePageScenes: OnboardingScene[] = mobilePageSceneIds.map((sceneId, index) => ({
  id: sceneId,
  steps: mobileWorkspaceSteps.filter(step => step.id === mobilePageStepIds[index]),
}));

// ==================== 场景组装 ====================

export const desktopWorkspaceScene: OnboardingScene = {
  id: 'desktop-workspace',
  steps: desktopWorkspaceSteps,
};

export const mobileWorkspaceScene: OnboardingScene = {
  id: 'mobile-workspace',
  steps: mobileWorkspaceSteps,
};
