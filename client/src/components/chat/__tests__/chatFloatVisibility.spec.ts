import { describe, expect, it } from 'vitest';
import {
  clampFloatingChatPosition,
  resolveChatFloatSurface,
} from '../chatFloatVisibility';

describe('悬浮聊天可见性策略', () => {
  it('桌面端在首页概念删除后：仅全屏聊天页与编剧面板隐藏悬浮聊天（编剧面板已内嵌右侧边栏）', () => {
    expect(resolveChatFloatSurface({
      expanded: false,
      isMobile: false,
      currentView: 'chat',
    })).toEqual({
      rootVisible: false,
      launchVisible: false,
      desktopPanelVisible: false,
      mobileDrawerVisible: false,
    });

    expect(resolveChatFloatSurface({
      expanded: false,
      isMobile: false,
      currentView: 'production',
    })).toEqual({
      rootVisible: false,
      launchVisible: false,
      desktopPanelVisible: false,
      mobileDrawerVisible: false,
    });

    expect(resolveChatFloatSurface({
      expanded: true,
      isMobile: false,
      currentView: 'world',
    })).toEqual({
      rootVisible: true,
      launchVisible: false,
      desktopPanelVisible: true,
      mobileDrawerVisible: false,
    });
  });

  it('移动端不因内部页面是聊天状态而隐藏入口', () => {
    const collapsed = resolveChatFloatSurface({
      expanded: false,
      isMobile: true,
      currentView: 'chat',
    });
    expect(collapsed.rootVisible).toBe(true);
    expect(collapsed.launchVisible).toBe(true);

    const expanded = resolveChatFloatSurface({
      expanded: true,
      isMobile: true,
      currentView: 'production',
    });
    expect(expanded.rootVisible).toBe(true);
    expect(expanded.mobileDrawerVisible).toBe(true);
    expect(expanded.desktopPanelVisible).toBe(false);
  });

  it('视口缩小或持久化坐标异常时把悬浮入口归位到可见区域', () => {
    expect(clampFloatingChatPosition({
      right: -120,
      top: 1600,
      width: 64,
      height: 64,
      viewportWidth: 390,
      viewportHeight: 720,
    })).toEqual({
      right: 8,
      top: 648,
    });

    expect(clampFloatingChatPosition({
      right: 900,
      top: -50,
      width: 640,
      height: 500,
      viewportWidth: 1280,
      viewportHeight: 800,
    })).toEqual({
      right: 632,
      top: 8,
    });
  });
});
