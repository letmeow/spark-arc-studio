export type ChatFloatSurfaceOptions = {
  expanded: boolean;
  isMobile: boolean;
  currentView: string;
};

export type FloatingChatPositionOptions = {
  right: number;
  top: number;
  width: number;
  height: number;
  viewportWidth: number;
  viewportHeight: number;
  margin?: number;
};

/** 悬浮聊天的可见性规则：移动端始终显示；桌面端在首页('home')、聊天全屏页('chat')及编剧面板('production')停靠隐藏，避免与中央输入框/全屏聊天重复。 */
export function resolveChatFloatSurface(options: ChatFloatSurfaceOptions) {
  const rootVisible = options.isMobile || (options.currentView !== 'home' && options.currentView !== 'chat' && options.currentView !== 'production');
  return {
    rootVisible,
    launchVisible: rootVisible && !options.expanded,
    desktopPanelVisible: rootVisible && options.expanded && !options.isMobile,
    mobileDrawerVisible: options.expanded && options.isMobile,
  };
}

/** 将持久化或拖动产生的悬浮坐标限制在当前视口内。 */
export function clampFloatingChatPosition(options: FloatingChatPositionOptions) {
  const margin = Math.max(0, Number(options.margin ?? 8));
  const width = Math.max(0, Number(options.width) || 0);
  const height = Math.max(0, Number(options.height) || 0);
  const maxRight = Math.max(margin, options.viewportWidth - width - margin);
  const maxTop = Math.max(margin, options.viewportHeight - height - margin);
  return {
    right: Math.min(Math.max(margin, options.right), maxRight),
    top: Math.min(Math.max(margin, options.top), maxTop),
  };
}
