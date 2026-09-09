import { describe, expect, it } from 'vitest';
import {
  desktopPageScenes,
  desktopWorkspaceSteps,
  mobilePageSceneIds,
  mobilePageScenes,
  mobileWorkspaceSteps,
} from '../engine/stepDefinitions';

describe('onboarding 场景契约', () => {
  it('为每个桌面主页面注册可重放教程', () => {
    expect(desktopPageScenes.map(scene => scene.id)).toEqual([
      'page-chat',
      'page-world',
      'page-synopsis',
      'page-structure',
      'page-production',
      'page-blueprint',
      'page-style',
      'page-engine',
      'page-dashboard',
      'page-settings',
    ]);
    expect(desktopPageScenes.every(scene => scene.steps.length >= 2)).toBe(true);
    expect(desktopPageScenes.flatMap(scene => scene.steps).every(step => step.spotlight !== false)).toBe(true);
  });

  it('完整引导先解释 AI 平台与模型，再进入创作流程', () => {
    expect(desktopWorkspaceSteps.slice(0, 4).map(step => step.id)).toEqual([
      'dw-ai-setup-overview',
      'dw-ai-platforms',
      'dw-ai-model-usage',
      'dw-workflow-overview',
    ]);
    expect(desktopWorkspaceSteps[0].detailKeys).toHaveLength(5);
    expect(desktopWorkspaceSteps[3].detailKeys).toHaveLength(6);
    expect(desktopWorkspaceSteps.some(step => step.id === 'world-seed')).toBe(true);
    expect(desktopWorkspaceSteps.some(step => step.id === 'production-editor')).toBe(true);
  });

  it('移动端也先完成 AI 平台与模型引导', () => {
    expect(mobileWorkspaceSteps.slice(0, 4).map(step => step.id)).toEqual([
      'mw-ai-setup-overview',
      'mw-ai-platforms',
      'mw-ai-model-usage',
      'mw-workflow-overview',
    ]);
    expect(mobileWorkspaceSteps[0].detailKeys).toHaveLength(5);
    expect(mobileWorkspaceSteps[3].detailKeys).toHaveLength(6);
  });

  it('设置页平台教程解释个人密钥覆盖路径', () => {
    const settingsScene = desktopPageScenes.find(scene => scene.id === 'page-settings');
    const platformStep = settingsScene?.steps.find(step => step.id === 'settings-platforms');
    expect(platformStep?.detailKeys).toContain('onboarding.desktop.aiSetup.personalOverride');
  });

  it('移动端标题按钮的每个场景只包含当前页面教程', () => {
    expect(mobilePageScenes.map(scene => scene.id)).toEqual([...mobilePageSceneIds]);
    expect(mobilePageScenes.map(scene => scene.steps.map(step => step.id))).toEqual([
      ['mw-muse'],
      ['mw-world'],
      ['mw-synopsis'],
      ['mw-structure'],
      ['mw-production'],
      ['mw-blueprint'],
    ]);
  });
});
