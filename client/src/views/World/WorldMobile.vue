<template>
  <div class="world-mobile-host">
    <GlobalLoading scope="world" />
    <GlobalLoading scope="muse" variant="card" />
    <div class="world-mobile-flow">
      <!-- 灵感输入区 -->
      <div class="flow-section">
      <div class="section-header">
        <n-icon :component="Zap" size="18" />
        <span>{{ t('views.world.common.seed') }}</span>
        <div class="header-actions">
          <n-button
            size="tiny"
            type="primary"
            :loading="museLoading"
            :disabled="isGenerating"
            @click="handleIgnite"
          >
            <template #icon><n-icon :component="Zap" /></template>
            {{ t('views.world.desktop.ignite') }}
          </n-button>
          <n-button
            size="tiny"
            type="primary"
            secondary
            :disabled="!museResult || isGenerating || isCurrentInspirationBound"
            @click="handlePinInspiration"
          >
            <template #icon><n-icon :component="Pin" /></template>
            {{ t('views.world.desktop.pinInspiration') }}
          </n-button>
          <n-button
            size="tiny"
            type="primary"
            secondary
            :disabled="!museResult || isGenerating"
            @click="handleGenerateSettingsAndScroll"
          >
            <template #icon><n-icon :component="Sparkles" /></template>
            {{ t('views.world.desktop.generateSettings') }}
          </n-button>
        </div>
      </div>
      <MobileTextArea
        v-model:value="museInput"
        :title="t('views.world.common.seed')"
        :placeholder="t('views.world.common.seedPlaceholder')"
        :autosize="{ minRows: 3, maxRows: 15 }"
        :disabled="isGenerating"
      />
      </div>
    
    <!-- 历史记录快捷入口 -->
      <button type="button" class="history-hint" @click="showHistory = true">
        <n-icon :component="Clock" size="16" />
        <span class="history-hint-copy">
          <strong>{{ historyEntryTitle }}</strong>
          <span>{{ historyEntrySummary }}</span>
        </span>
        <n-badge v-if="unreadCount > 0" :value="unreadCount" :max="99" />
        <n-icon :component="ChevronRight" size="16" />
      </button>
    
    <!-- 灵感主题参数 -->
      <div class="flow-section tags-section">
      <InspireTagSelector
        :title="t('components.inspireTagsPanel.title')"
        v-model:genres="selectedGenres"
        v-model:tones="selectedTones"
        v-model:worldviews="selectedWorldviews"
        v-model:pov="selectedPov"
        v-model:lengthHint="selectedLength"
        :show-style="false"
        :show-length="true"
      />
      </div>
    
    
    <!-- 生成结果 -->
      <div v-if="museResult" class="flow-section result-section">
      <div class="section-header">
        <n-icon :component="Sparkles" size="18" />
        <span>{{ t('views.world.mobile.result') }}</span>
        <div class="header-actions">
          <n-button
            size="tiny"
            type="primary"
            :disabled="!museResult || isGenerating"
            @click="handleGenerateSettingsAndScroll"
          >
            <template #icon><n-icon :component="ArrowRight" /></template>
            {{ t('views.world.desktop.generateSettings') }}
          </n-button>
          <n-button size="tiny" quaternary @click="museResult = ''">{{ t('views.world.mobile.clear') }}</n-button>
        </div>
      </div>
      <MobileTextArea
        v-model:value="museResult"
        :title="t('views.world.mobile.editResult')"
        :disabled="isGenerating"
        :autosize="{ minRows: 6, maxRows: 25 }"
      />
      </div>
    
    <!-- 历史记录抽屉 -->
      <n-drawer v-model:show="showHistory" placement="bottom" height="70%">
      <n-drawer-content :title="t('views.world.common.history')" closable>
        <HistoryPanel 
          ref="museHistoryRef" 
          type="muse" 
          :show-header="false" 
          @select="handleMobileHistorySelect"
          @unread-change="handleUnreadChange"
        />
      </n-drawer-content>
      </n-drawer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { NBadge, NButton, NIcon, NDrawer, NDrawerContent } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { ArrowRight, ChevronRight, Clock, Pin, Sparkles, Zap } from '@lucide/vue';
import HistoryPanel from '../../components/dlg-editor/HistoryPanel.vue';
import GlobalLoading from '../../components/share/GlobalLoading.vue';
import InspireTagSelector from '../../components/lorebook/InspireTagSelector.vue';
import MobileTextArea from '../../components/editors/mobile/MobileTextArea.vue';
import { useWorldLogic } from '../../composables/useWorldLogic';
import { MOBILE_FLOW_STEP, scrollToFlowStep } from '../../utils/mobileFlow';
import { useProjectStore } from '../../components/stores/projectStore';

const { t } = useI18n();
const projectStore = useProjectStore();
const showHistory = ref(false);

const {
  museInput,
  museLoading,
  museResult,
  museHistoryRef,
  isGenerating,
  isCurrentInspirationBound,
  unreadCount,
  handleUnreadChange,
  selectedGenres,
  selectedTones,
  selectedWorldviews,
  selectedPov,
  selectedLength,
  handleIgnite,
  handleMuseHistorySelect,
  handleGenerateFromMuse,
  handlePinInspiration,
} = useWorldLogic();

const historyEntryTitle = computed(() => projectStore.currentProject
  ? t('views.world.history.projectInspiration')
  : t('views.world.history.inspirationDrafts'));

const historyEntrySummary = computed(() => {
  if (!projectStore.currentProject) return t('views.world.history.draftsSummary');
  return projectStore.boundInspirationSource || t('views.world.history.noActiveInspiration');
});

function handleMobileHistorySelect(item: Parameters<typeof handleMuseHistorySelect>[0]) {
  handleMuseHistorySelect(item);
  showHistory.value = false;
}

function handleGenerateSettingsAndScroll() {
  void handleGenerateFromMuse({
      beforeGenerate: () => scrollToFlowStep(MOBILE_FLOW_STEP.world),
  });
}
</script>

<style scoped>
.world-mobile-flow {
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: relative;
}

.world-mobile-host {
  position: relative;
  width: calc(100% + 20px);
  margin: 0 -10px;
  padding: 10px;
  box-sizing: border-box;
}

.flow-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--spark-fs-base);
  font-weight: 600;
  color: var(--spark-primary);
}

.header-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  margin-left: auto;
}

.section-header > .n-button {
  margin-left: auto;
}

.result-section {
  padding: 10px 12px;
  background: rgba(var(--spark-primary-rgb), 0.05);
  border: 1px solid rgba(var(--spark-primary-rgb), 0.15);
  border-radius: 12px;
}

.result-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.history-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 10px;
  font-size: var(--spark-fs-base);
  color: var(--spark-text-muted);
  cursor: pointer;
  transition: all 0.2s;
  width: 100%;
  font-family: inherit;
  text-align: left;
}

.history-hint:active {
  background: rgba(var(--spark-primary-rgb), 0.05);
}

.history-hint-copy {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.history-hint-copy strong,
.history-hint-copy span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-hint-copy strong {
  color: var(--spark-text);
  font-size: var(--spark-fs-sm);
  font-weight: 600;
}

.history-hint-copy span {
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
}

:deep(.n-input-wrapper),
:deep(.n-input__state-border),
:deep(.n-input__border) {
  height: 100% !important;
}

:deep(.n-input__textarea-el) {
  height: 100% !important;
  overflow-y: auto !important;
}
</style>
