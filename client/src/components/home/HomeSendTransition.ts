/**
 * HomeSendTransition.ts - 首页 → 聊天页发送过渡（FLIP 残影飞）
 *
 * 三段式（与原型一致）：
 * P0 残影飞：只动 ghost（从首页输入框 rect 飞到聊天页输入框 rect），首页 DOM 不动 → 布局零抖动。
 * P1 聊天页淡入：走现有 .spark-view 过渡（setView('chat') 自带），ghost 此时已半透明，两者叠化。
 * P2 草稿预填：把文本写入 chat 页 draft（经 'home-send-to-director' 事件），由用户确认发送。
 *
 * 防崩坏：
 * - flying 锁防连点；空文本直接返回。
 * - 目标 rect 量不到（如聊天页未挂载）时降级为直接切换，无动画但不断链。
 * - prefers-reduced-motion 用户跳过动画直接切换。
 * - ghost 是纯视觉残影，移除后无残留；失败路径保证 view 一定切换。
 */

import { nextTick } from 'vue';
import bus from '@/eventBus';

let flying = false;

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

function measureRect(el: Element | null): DOMRect | null {
  if (!el || typeof el.getBoundingClientRect !== 'function') return null;
  try {
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return null;
    return r;
  } catch {
    return null;
  }
}

export type HomeSendTransitionOptions = {
  /** 首页输入框容器（.home-composer） */
  sourceEl: Element | null;
  /** 发送文本 */
  text: string;
  /** 切换到 chat 视图（含 pending agent 设置） */
  switchToChat: () => void;
  /** 动画总时长（默认 450ms，与 .spark-view 同系） */
  durationMs?: number;
};

/**
 * 执行发送过渡。始终 resolve；动画失败时自动降级为直接切换。
 * 草稿传递走 'home-send-to-director' 事件，由 ChatDesktopIndex 监听并预填。
 */
export async function runHomeSendTransition(options: HomeSendTransitionOptions): Promise<void> {
  const text = (options.text || '').trim();
  if (!text || flying) return;
  const duration = Math.max(0, options.durationMs ?? 450);

  if (prefersReducedMotion() || duration === 0) {
    options.switchToChat();
    bus.emit('home-send-to-director', { text });
    return;
  }

  flying = true;
  const sourceRect = measureRect(options.sourceEl);
  let ghost: HTMLElement | null = null;

  const cleanup = () => {
    try { ghost?.remove(); } catch { /* 忽略 */ }
    ghost = null;
    try { document.body.classList.remove('home-flying'); } catch { /* 忽略 */ }
    flying = false;
  };

  const commit = () => {
    try {
      options.switchToChat();
      bus.emit('home-send-to-director', { text });
    } finally {
      cleanup();
    }
  };

  try {
    if (!sourceRect) {
      commit();
      return;
    }

    // 先切换视图，让聊天页挂载，再量目标 rect。setView 是同步赋值，
    // 目标输入框需等下一帧挂载；期间首页淡出（body.home-flying），ghost 原地待命。
    options.switchToChat();
    document.body.classList.add('home-flying');
    await nextTick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    const targetEl = document.querySelector('.chat-desktop-view .chat-input-wrapper')
      || document.querySelector('.chat-desktop-view .chat-textarea');
    const targetRect = measureRect(targetEl);

    if (!targetRect) {
      // 量不到目标：直接发事件收尾，不做飞行动画。
      bus.emit('home-send-to-director', { text });
      cleanup();
      return;
    }

    ghost = document.createElement('div');
    ghost.className = 'home-send-ghost';
    ghost.textContent = text.length > 60 ? `${text.slice(0, 60)}…` : text;
    ghost.style.left = `${sourceRect.left}px`;
    ghost.style.top = `${sourceRect.top}px`;
    ghost.style.width = `${sourceRect.width}px`;
    ghost.style.height = `${sourceRect.height}px`;
    document.body.appendChild(ghost);
    // 强制 reflow，否则 transition 不触发。
    void ghost.offsetWidth;
    requestAnimationFrame(() => {
      if (!ghost) return;
      ghost.style.left = `${targetRect.left}px`;
      ghost.style.top = `${targetRect.top}px`;
      ghost.style.width = `${targetRect.width}px`;
      ghost.style.height = `${targetRect.height}px`;
      ghost.style.opacity = '0.25';
      ghost.style.transform = 'scale(0.98)';
    });

    await new Promise<void>((resolve) => setTimeout(resolve, duration));
    bus.emit('home-send-to-director', { text });
    cleanup();
  } catch {
    try { commit(); } catch { cleanup(); }
  }
}

export function isHomeSendFlying(): boolean {
  return flying;
}
