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
        :title="step.label"
        @click="scrollToStep(index)"
      >
        <n-icon class="nav-icon" size="15">
          <component :is="getIconComponent(step.id)" />
        </n-icon>
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
  top: 50%;
  right: 8px;
  transform: translateY(-50%);
  z-index: 100;

  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 3px;

  background: color-mix(in srgb, var(--spark-panel-bg) 66%, transparent);
  -webkit-backdrop-filter: blur(18px) saturate(135%);
  backdrop-filter: blur(18px) saturate(135%);
  border: 1px solid color-mix(in srgb, var(--spark-border) 28%, transparent);
  border-radius: 12px;
  box-shadow: 0 8px 20px color-mix(in srgb, #000 10%, transparent);
}

.flow-nav-track {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  scrollbar-width: none;
}

.flow-nav-track::-webkit-scrollbar { display: none; }

.flow-nav .nav-item {
  all: unset;
  box-sizing: border-box;
  position: relative;
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: var(--spark-text-muted);
  background: transparent;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: color 160ms ease, opacity 160ms ease, transform 160ms ease;
}

.flow-nav .nav-item:hover { background: transparent; box-shadow: none; transform: none; }
.flow-nav .nav-item:active { transform: scale(0.9); }
.flow-nav .nav-item:focus-visible { outline: 1px solid var(--spark-primary); outline-offset: -1px; }

.flow-nav .nav-item.is-active {
  color: var(--spark-primary);
  opacity: 1;
}

.flow-nav .nav-item.is-active::after {
  content: '';
  position: absolute;
  left: -3px;
  top: 6px;
  bottom: 6px;
  width: 1px;
  border-radius: 999px;
  background: currentColor;
}

.nav-icon {
  width: 15px;
  height: 15px;
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

@media (max-width: 380px) {
  .flow-nav {
    right: 5px;
    padding: 3px 2px;
  }

  .flow-nav-track { gap: 1px; }

  .flow-nav .nav-item { width: 22px; height: 22px; }
  .nav-icon { width: 14px; height: 14px; font-size: 14px !important; }
}
</style>
