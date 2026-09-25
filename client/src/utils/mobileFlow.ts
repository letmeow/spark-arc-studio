/** 移动端工作流的唯一步骤编号，避免跨页面跳转时出现 0/1 基准不一致。 */
export const MOBILE_FLOW_STEP = {
  home: 0,
  muse: 1,
  world: 2,
  characters: 3,
  synopsis: 4,
  structure: 5,
  production: 6,
  blueprint: 7,
} as const;

export type MobileFlowStep = typeof MOBILE_FLOW_STEP[keyof typeof MOBILE_FLOW_STEP];

export function scrollToFlowStep(step: MobileFlowStep) {
  const target = document.getElementById(`step-${step}`);
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
