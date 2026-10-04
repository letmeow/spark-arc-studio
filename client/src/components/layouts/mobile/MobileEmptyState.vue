<template>
  <div
    class="m-empty-state"
    :class="{ 'is-fill': fill, 'is-compact': compact }"
    role="status"
  >
    <!-- 柔光图标：外圈呼吸光晕 + 毛玻璃底，与全局步骤条的玻璃质感保持一致 -->
    <div class="m-empty-icon" aria-hidden="true">
      <n-icon :component="icon" :size="compact ? 22 : 26" />
    </div>
    <strong class="m-empty-title">{{ title }}</strong>
    <p v-if="hint" class="m-empty-hint">{{ hint }}</p>
    <div v-if="$slots.default" class="m-empty-extra">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue';
import { NIcon } from 'naive-ui';

/**
 * 移动端统一空状态。
 * 各页面在“暂无内容”时统一使用，避免页面大片留白、风格各异（n-empty / 手写 empty-state 并存）。
 * - fill：撑满父级剩余高度并垂直居中，用于整页空状态；
 * - compact：用于列表/卡片内的小块空状态；
 * - 默认插槽：放引导操作（按钮、示例芯片等）。
 */
withDefaults(defineProps<{
  icon: Component;
  title: string;
  hint?: string;
  fill?: boolean;
  compact?: boolean;
}>(), {
  hint: '',
  fill: false,
  compact: false,
});
</script>

<style scoped>
.m-empty-state {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 28px 20px;
  text-align: center;
  border: 1.5px dashed rgba(var(--spark-primary-rgb), 0.28);
  border-radius: 18px;
  background:
    radial-gradient(120% 90% at 50% 0%, rgba(var(--spark-primary-rgb), 0.09), transparent 70%),
    rgba(var(--spark-primary-rgb), 0.025);
  box-sizing: border-box;
}

.m-empty-state.is-fill {
  flex: 1 1 auto;
  min-height: 220px;
  max-height: 380px;
  /* 父级为纵向 flex 时，超出 max-height 的剩余空间由 auto 外边距均分，使卡片落在视觉重心 */
  margin-block: auto;
}

.m-empty-state.is-compact {
  padding: 18px 16px;
  gap: 6px;
  border-radius: 14px;
}

.m-empty-icon {
  position: relative;
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  margin-bottom: 4px;
  border-radius: 50%;
  color: var(--spark-primary);
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 6px 18px rgba(var(--spark-primary-rgb), 0.18);
}

.is-compact .m-empty-icon {
  width: 44px;
  height: 44px;
}

.m-empty-icon::after {
  content: '';
  position: absolute;
  inset: -6px;
  border-radius: 50%;
  border: 1px solid rgba(var(--spark-primary-rgb), 0.22);
  animation: m-empty-breathe 3.2s ease-in-out infinite;
}

.m-empty-title {
  font-size: var(--spark-fs-base);
  font-weight: 600;
  color: var(--spark-text-secondary, var(--spark-text));
}

.m-empty-hint {
  margin: 0;
  max-width: 280px;
  font-size: var(--spark-fs-sm);
  line-height: 1.55;
  color: var(--spark-text-muted);
}

.m-empty-extra {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 8px;
}

@keyframes m-empty-breathe {
  0%, 100% { transform: scale(0.94); opacity: 0.4; }
  50% { transform: scale(1.08); opacity: 0.95; }
}

@media (prefers-reduced-motion: reduce) {
  .m-empty-icon::after { animation: none; }
}
</style>
