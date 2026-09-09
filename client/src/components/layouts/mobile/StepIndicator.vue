<template>
  <div class="flow-nav">
    <div
      v-for="(step, index) in steps"
      :key="step.id"
      class="nav-item"
      :class="{
        'is-active': currentStep === index
      }"
      @click="scrollToStep(index)"
    >
      <n-icon class="nav-icon" size="18">
        <component :is="getIconComponent(step.id)" />
      </n-icon>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, markRaw, type PropType } from 'vue';
import { NIcon } from 'naive-ui';
import { Activity, Globe2, Lightbulb, List, Map as MapIcon, SquarePen } from '@lucide/vue';

// 图标映射
const iconMap = {
  'muse': Lightbulb,          // 灵感 - 灯泡
  'lorebook': Globe2,    // 世界观 - 星球 (移动端独立页面)
  'synopsis': Activity,     // 梗概 - 脉冲
  'structure': List,     // 大纲 - 列表
  'production': SquarePen,  // 创作 - 铅笔
  'blueprint': MapIcon       // 蓝图 - 地图
};

function getIconComponent(stepId) {
  return markRaw(iconMap[stepId] || Lightbulb);
}

type StepItem = {
  id: string;
};

const props = defineProps({
  steps: {
    type: Array as PropType<StepItem[]>,
    required: true
  },
  containerRef: {
    type: Object,
    default: null
  }
});

const currentStep = ref(0);

function scrollToStep(index) {
  const stepId = `step-${index + 1}`;
  const element = document.getElementById(stepId);
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// IntersectionObserver 检测当前可见卡片
let observer: IntersectionObserver | null = null;

function setupObserver() {
  const options = {
    root: props.containerRef?.value || null,
    rootMargin: '-40% 0px -40% 0px',
    threshold: 0
  };
  
  observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id;
        const match = id.match(/step-(\d+)/);
        if (match) {
          currentStep.value = parseInt(match[1]) - 1;
        }
      }
    });
  }, options);
  
  props.steps.forEach((_, index) => {
    const element = document.getElementById(`step-${index + 1}`);
    if (element) {
      observer?.observe(element);
    }
  });
}

onMounted(() => {
  setTimeout(setupObserver, 100);
});

onUnmounted(() => {
  if (observer) {
    observer.disconnect();
  }
});
</script>

<style scoped>
.flow-nav {
  position: fixed;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 100;
  
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px;
  
  background: color-mix(in srgb, var(--spark-panel-bg) 80%, transparent);
  backdrop-filter: blur(12px);
  border-radius: 14px;
  border: 1px solid color-mix(in srgb, var(--spark-border) 30%, transparent);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.nav-item {
  position: relative;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
  
  /* 背景 */
  background: transparent;
}

/* 增大触控区域 */
.nav-item::before {
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 14px;
}

.nav-item:active {
  transform: scale(0.92);
}

.nav-icon {
  width: 18px;
  height: 18px;
  color: var(--spark-text-muted);
  opacity: 0.5;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

/* 当前激活状态 - 主色高亮 + 背景 */
.nav-item.is-active {
  background: rgba(var(--spark-primary-rgb), 0.12);
}

.nav-item.is-active .nav-icon {
  color: var(--spark-primary);
  opacity: 1;
  transform: scale(1.1);
}

/* 响应式 - 极小屏幕 */
@media (max-width: 380px) {
  .flow-nav {
    right: 4px;
    padding: 5px;
    gap: 1px;
  }
  
  .nav-item {
    width: 28px;
    height: 28px;
  }
  
  .nav-icon {
    width: 16px;
    height: 16px;
  }
}
</style>
