<template>
  <nav class="flow-nav" :aria-label="t('mobileFlow.steps.home')">
    <div class="flow-nav-track">
      <button
        v-for="(step, index) in steps"
        :key="step.id"
        type="button"
        class="nav-item"
        :class="{ 'is-active': currentStep === index }"
        :aria-current="currentStep === index ? 'step' : undefined"
        :aria-label="step.label"
        @click="scrollToStep(index)"
      >
        <n-icon class="nav-icon" size="17">
          <component :is="getIconComponent(step.id)" />
        </n-icon>
        <span class="nav-label">{{ step.label }}</span>
      </button>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { markRaw, type PropType } from 'vue';
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
});

function scrollToStep(index: number) {
  const element = document.getElementById(`step-${index}`);
  if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

</script>

<style scoped>
.flow-nav {
  position: fixed;
  left: 12px;
  right: 12px;
  bottom: calc(var(--sab, 0px) + 12px);
  z-index: 100;
  padding: 6px;
  border: 1px solid color-mix(in srgb, var(--spark-border) 78%, transparent);
  border-radius: 16px;
  background: color-mix(in srgb, var(--spark-panel-bg) 92%, transparent);
  box-shadow: 0 10px 30px color-mix(in srgb, #000 28%, transparent);
  backdrop-filter: blur(18px) saturate(125%);
}

.flow-nav-track {
  display: flex;
  gap: 1px;
  width: 100%;
  overflow: hidden;
  scrollbar-width: none;
}

.flow-nav-track::-webkit-scrollbar { display: none; }

.nav-item {
  min-width: 0;
  min-height: 42px;
  flex: 1 1 0;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 4px 2px;
  border: 0;
  border-radius: 11px;
  color: var(--spark-text-muted);
  background: transparent;
  font: inherit;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: color 160ms ease, background 160ms ease, transform 160ms ease;
}

.nav-item:active { transform: scale(0.96); }

.nav-item.is-active {
  color: var(--spark-primary);
  background: color-mix(in srgb, var(--spark-primary) 13%, transparent);
}

.nav-icon { flex: 0 0 auto; }

.nav-label {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  line-height: 1.2;
}

@media (max-width: 360px) {
  .flow-nav { left: 8px; right: 8px; }
  .nav-item { min-height: 40px; }
  .nav-label { font-size: 9px; }
}
</style>
