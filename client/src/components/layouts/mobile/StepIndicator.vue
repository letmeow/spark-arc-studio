<template>
  <nav
    class="flow-nav"
    :class="{ 'is-dimmed': dimmed }"
    :aria-label="t('mobileFlow.steps.home')"
  >
    <div class="flow-nav-track">
      <button
        v-for="(step, index) in steps"
        :key="step.id"
        type="button"
        class="nav-item"
        :class="{ 'is-active': currentStep === index }"
        :aria-current="currentStep === index ? 'step' : undefined"
        :aria-label="step.label"
        :title="step.label"
        @click="scrollToStep(index)"
      >
        <n-icon class="nav-icon" size="18">
          <component :is="getIconComponent(step.id)" />
        </n-icon>
      </button>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { markRaw, onBeforeUnmount, onMounted, ref, type PropType } from 'vue';
import { NIcon } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { Activity, Globe2, House, Lightbulb, List, Map as MapIcon, SquarePen, UsersRound } from '@lucide/vue';

const { t } = useI18n();

const iconMap = {
  home: House,
  muse: Lightbulb,
  lorebook: Globe2,
  characters: UsersRound,
  synopsis: Activity,
  structure: List,
  production: SquarePen,
  blueprint: MapIcon,
};

function getIconComponent(stepId: string) {
  return markRaw(iconMap[stepId as keyof typeof iconMap] || Lightbulb);
}

type StepItem = { id: string; label: string };

const props = defineProps({
  steps: {
    type: Array as PropType<StepItem[]>,
    required: true,
  },
  currentStep: {
    type: Number,
    required: true,
  },
  /** 需要监听滚动以便导航自动淡出的滚动容器选择器。 */
  scrollSelector: {
    type: String,
    default: '.flow-container',
  },
});

/** 内容滚动停止后恢复导航可交互的延迟。 */
const SCROLL_IDLE_MS = 600;

/**
 * 内容滚动（含惯性滚动）期间导航淡出并暂停响应，
 * 避免手指在边缘滑动或惯性滚动结束时误触切换步骤。
 */
const dimmed = ref(false);
let scrollIdleTimer: ReturnType<typeof setTimeout> | null = null;

function scrollToStep(index: number) {
  if (index === props.currentStep) return;
  tapFeedback();
  const element = document.getElementById(`step-${index}`);
  if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/** 轻触觉反馈；不支持的设备（如 iOS Safari）静默忽略。 */
function tapFeedback() {
  try {
    navigator.vibrate?.(6);
  } catch {
    // 触觉反馈属于增强体验，失败不影响导航。
  }
}

// 滚动是 non-bubbling 事件，只能捕获阶段监听；仅响应主滚动容器，忽略输入框等内部滚动。
function onContentScroll(event: Event) {
  const target = event.target as HTMLElement | null;
  if (!target || !target.matches?.(props.scrollSelector)) return;
  dimmed.value = true;
  if (scrollIdleTimer) clearTimeout(scrollIdleTimer);
  scrollIdleTimer = setTimeout(() => { dimmed.value = false; }, SCROLL_IDLE_MS);
}

onMounted(() => {
  document.addEventListener('scroll', onContentScroll, { capture: true, passive: true });
});

onBeforeUnmount(() => {
  document.removeEventListener('scroll', onContentScroll, { capture: true });
  if (scrollIdleTimer) clearTimeout(scrollIdleTimer);
});
</script>

<style scoped>
.flow-nav {
  position: fixed;
  top: 50%;
  right: 4px;
  transform: translateY(-50%);
  z-index: 100;

  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 2px;

  background: color-mix(in srgb, var(--spark-panel-bg) 66%, transparent);
  -webkit-backdrop-filter: blur(18px) saturate(135%);
  backdrop-filter: blur(18px) saturate(135%);
  border: 1px solid color-mix(in srgb, var(--spark-border) 28%, transparent);
  border-radius: 16px;
  box-shadow: 0 8px 20px color-mix(in srgb, #000 10%, transparent);
  transition: opacity 200ms ease;
}

/* 滚动期间淡出并让触摸穿透给内容，杜绝惯性滚动时的误触 */
.flow-nav.is-dimmed {
  opacity: 0.4;
  pointer-events: none;
}

.flow-nav-track {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

/* 触控目标 36×36（原 24×24），图标 18px */
.flow-nav .nav-item {
  all: unset;
  box-sizing: border-box;
  position: relative;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  color: var(--spark-text-muted);
  background: transparent;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: color 160ms ease, background 160ms ease, transform 160ms ease;
}

.flow-nav .nav-item:active { transform: scale(0.9); }
.flow-nav .nav-item:focus-visible { outline: 2px solid var(--spark-primary); outline-offset: -2px; }

.flow-nav .nav-item.is-active {
  color: var(--spark-primary);
  background: color-mix(in srgb, var(--spark-primary) 12%, transparent);
}

.nav-icon {
  color: var(--spark-text-muted);
  opacity: 0.58;
  transition: color 160ms ease, opacity 160ms ease, transform 160ms ease;
  flex-shrink: 0;
}

.flow-nav .nav-item.is-active .nav-icon {
  color: var(--spark-primary);
  opacity: 1;
  transform: scale(1.06);
}

/* 矮屏（含横屏）压缩，保证 8 个步骤完整可见 */
@media (max-height: 560px) {
  .flow-nav .nav-item { width: 32px; height: 32px; }
  .flow-nav-track { gap: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .flow-nav,
  .flow-nav .nav-item,
  .nav-icon { transition: none; }
}
</style>
