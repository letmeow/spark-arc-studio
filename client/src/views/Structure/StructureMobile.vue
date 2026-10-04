<template>
  <div class="structure-mobile-flow" style="position: relative;">
    <!-- 策划输入区 -->
    <div class="flow-section">
      <!-- 通用全局加载遮罩 -->
      <GlobalLoading scope="outline" variant="card" />
      <div class="section-header">
        <n-icon :component="List" size="18" />
        <span>{{ t('views.structure.mobile.outlinePlanning') }}</span>
      </div>
      
      <MobileTextArea 
        v-model:value="context" 
        :autosize="{ minRows: 4, maxRows: 15 }"
        customClass="context-input"
        :title="t('views.structure.mobile.storyBackground')"
        :placeholder="t('views.structure.mobile.storyBackgroundPlaceholder')" 
      />
      
      <MobileTextArea 
        v-model:value="guidance" 
        :autosize="{ minRows: 2, maxRows: 5 }"
        customClass="guidance-input"
        :title="t('views.structure.mobile.developmentGuidance')"
        :placeholder="t('views.structure.mobile.guidancePlaceholder')" 
      />
    </div>
    
    <!-- 故事分组数量 + 生成；chapterCount/sceneCount 是历史兼容字段，界面按模式显示正式术语。 -->
    <div class="flow-section control-section">
      <div class="length-preset-group">
        <div class="setting-label-row">
          <span class="setting-label">{{ t('views.structure.mobile.lengthPreset') }}</span>
          <n-tooltip trigger="click" placement="top">
            <template #trigger>
              <button
                type="button"
                class="length-help-button"
                :aria-label="t('views.structure.lengthPresetTooltipLabel')"
              >
                <n-icon :component="Info" size="14" />
              </button>
            </template>
            <div class="length-help-text">{{ t('views.structure.lengthPresetTooltip') }}</div>
          </n-tooltip>
        </div>
        <div class="preset-grid">
          <button
            v-for="option in mobileLengthOptions"
            :key="option.value"
            type="button"
            class="preset-chip"
            :class="{ 'is-active': lengthType === option.value }"
            @click="lengthType = option.value"
          >
            <span class="preset-chip-label">{{ option.label }}</span>
          </button>
        </div>
        <p class="preset-description">{{ activeMobileLengthOption.description }}</p>
      </div>

      <div class="custom-setting-grid" v-if="lengthType === 'custom'">
        <label class="custom-setting-card">
          <span class="custom-setting-label">{{ t(`views.structure.mobile.${workspaceMode}.plannedGroupCount`) }}</span>
          <n-input-number v-model:value="chapterCount" :min="1" :max="50" size="small" class="compact-number-input" />
        </label>
        <label class="custom-setting-card">
          <span class="custom-setting-label">{{ t(`views.structure.mobile.${workspaceMode}.unitsPerGroup`) }}</span>
          <n-input-number v-model:value="sceneCount" :min="1" :max="30" size="small" class="compact-number-input" />
        </label>
      </div>
      
      <div class="m-action-bar">
        <n-button type="primary" :loading="isLoading" :disabled="!context?.trim()" @click="handleGenerateOutlineClick">
          <template #icon><n-icon :component="Sparkles" /></template>
          {{ t('views.structure.mobile.generateOutline') }}
        </n-button>
      </div>
    </div>
    
    <!-- 大纲列表 -->
    <div class="flow-section" v-if="outlineChapters.length > 0">
      <div class="section-header">
        <n-icon :component="Files" size="18" />
        <span>{{ t(`views.structure.mobile.${workspaceMode}.groupOutline`) }}</span>
        <SparkTag type="info" size="small">{{ t(`views.structure.mobile.${workspaceMode}.groupCountLabel`, { count: outlineChapters.length }) }}</SparkTag>
        <!-- 自动写作入口见列表下方操作栏 -->
      </div>
      
      <div class="chapter-list">
        <div 
          v-for="(chapter, idx) in outlineChapters.slice(0, 5)" 
          :key="idx"
          class="chapter-card"
          @click="editChapter(chapter, idx)"
        >
          <div class="chapter-header">
            <SparkTag type="primary" size="small">{{ t(`views.structure.mobile.${workspaceMode}.groupIndexLabel`) }} {{ chapter.chapter || (Number(idx) + 1) }}</SparkTag>
            <span class="chapter-title">{{ chapter.title || t('views.structure.mobile.untitled') }}</span>
          </div>
          <div class="chapter-summary">{{ chapter.description || '' }}</div>
        </div>
        
        <div v-if="outlineChapters.length > 5" class="more-hint" @click="showFullList = true">
          {{ t(`views.structure.mobile.${workspaceMode}.viewAllGroups`, { count: outlineChapters.length }) }}
        </div>
      </div>
      <div class="m-action-bar">
        <n-button type="primary" secondary @click="openAutoWrite">
          <template #icon><n-icon :component="ArrowRight" /></template>
          {{ t(structureKey('startAutoWrite')) }}
        </n-button>
      </div>
      
    </div>
    

    <MobileEmptyState
      v-else
      :icon="Files"
      :title="t('views.structure.mobile.noOutline')"
      :hint="t('views.structure.mobile.emptyHint')"
    />
    
    <!-- 历史入口 -->
    <div class="history-hint" @click="showHistory = true">
      <n-icon :component="Clock" size="16" />
      <span>{{ t('views.structure.mobile.history') }}</span>
      <n-icon :component="ChevronRight" size="16" />
    </div>
    
    <!-- 完整列表抽屉 -->
    <n-drawer v-model:show="showFullList" placement="bottom" height="85%">
      <n-drawer-content :title="t(`views.structure.mobile.${workspaceMode}.allGroups`)" closable>
        <div class="full-chapter-list">
          <div 
            v-for="(chapter, idx) in outlineChapters" 
            :key="idx"
            class="chapter-card"
          >
            <div class="chapter-header">
              <SparkTag type="primary" size="small">{{ t(`views.structure.mobile.${workspaceMode}.groupIndexLabel`) }} {{ chapter.chapter || (Number(idx) + 1) }}</SparkTag>
              <span class="chapter-title">{{ chapter.title || t('views.structure.mobile.untitled') }}</span>
            </div>
            <MobileTextArea 
              v-model:value="chapter.description" 
              customClass="chapter-input"
              :title="t(`views.structure.mobile.${workspaceMode}.groupOutline`)"
              :autosize="{ minRows: 6, maxRows: 25 }"
            />
          </div>
        </div>
      </n-drawer-content>
    </n-drawer>
    
    <!-- 历史抽屉 -->
    <n-drawer v-model:show="showHistory" placement="bottom" height="70%">
      <n-drawer-content :title="t('views.structure.mobile.history')" closable>
        <HistoryPanel 
          ref="outlineHistoryRef"
          type="outline" 
          :show-header="false"
          @select="handleOutlineHistorySelect"
          @restore="handleOutlineRestore"
        />
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { NButton, NIcon, NInputNumber, NDrawer, NDrawerContent, NTooltip } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import SparkTag from '../../components/share/SparkTag.vue';
import GlobalLoading from '../../components/share/GlobalLoading.vue';
import MobileTextArea from '../../components/editors/mobile/MobileTextArea.vue';
import MobileEmptyState from '../../components/layouts/mobile/MobileEmptyState.vue';
import { ArrowRight, ChevronRight, Clock, Files, Info, List, Sparkles } from '@lucide/vue';
import HistoryPanel from '../../components/dlg-editor/HistoryPanel.vue';
import { useStructureLogic } from '../../composables/useStructureLogic';
import bus from '../../eventBus';

const { t } = useI18n();

const showFullList = ref(false);
const showHistory = ref(false);

const {
  context,
  guidance,
  isLoading,
  currentOutline,
  outlineHistoryRef,
  chapterCount,
  sceneCount,
  lengthType,
  handleGenerateOutline,
  handleOutlineHistorySelect,
  handleOutlineRestore,
  workspaceMode,
} = useStructureLogic();

const outlineChapters = computed(() => {
  return currentOutline?.value?.nodes || [];
});

// outlineChapters 是大纲数据中的历史变量名；用户界面按模式显示为剧幕或分卷。
const structureKey = (variant: string) => `views.structure.mobile.${workspaceMode.value}.${variant}`;

const mobileLengthOptions = computed(() => [
  {
    value: 'short',
    label: t('views.structure.mobile.lengthPresetOptions.short'),
    description: t(structureKey('lengthPresetDescriptions.short')),
  },
  {
    value: 'medium',
    label: t('views.structure.mobile.lengthPresetOptions.medium'),
    description: t(structureKey('lengthPresetDescriptions.medium')),
  },
  {
    value: 'long',
    label: t('views.structure.mobile.lengthPresetOptions.long'),
    description: t(structureKey('lengthPresetDescriptions.long')),
  },
  {
    value: 'unlimited',
    label: t('views.structure.mobile.lengthPresetOptions.unlimited'),
    description: t(structureKey('lengthPresetDescriptions.unlimited')),
  },
  {
    value: 'custom',
    label: t('views.structure.mobile.lengthPresetOptions.custom'),
    description: t(structureKey('lengthPresetDescriptions.custom')),
  },
]);

const activeMobileLengthOption = computed(() => {
  return mobileLengthOptions.value.find((option) => option.value === lengthType.value) || mobileLengthOptions.value[0];
});

function handleGenerateOutlineClick() {
  void handleGenerateOutline();
}

function editChapter(chapter, idx) {
  showFullList.value = true;
}

function openAutoWrite() {
  bus.emit('open-auto-write-setup');
}
</script>

<style scoped>
.structure-mobile-flow {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.flow-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--spark-fs-base);
  font-weight: 600;
  color: var(--spark-primary);
}

.section-header .spark-tag {
  margin-left: auto;
}

.control-section {
  gap: 10px;
  padding: 10px 12px 12px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 10px;
}

.length-preset-group {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
}

.preset-chip {
  min-width: 0;
  min-height: 40px;
  padding: 5px 6px;
  border: 1px solid color-mix(in srgb, var(--spark-border), transparent 18%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--spark-panel-bg), var(--spark-bg) 45%);
  color: var(--spark-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}

.preset-chip.is-active {
  border-color: color-mix(in srgb, var(--spark-primary), transparent 20%);
  background: color-mix(in srgb, var(--spark-primary), white 88%);
  color: var(--spark-primary);
  box-shadow: none;
}

.preset-chip-label {
  font-size: var(--spark-fs-xs);
  font-weight: 600;
  line-height: 1.15;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.setting-label {
  font-size: var(--spark-fs-sm);
  font-weight: 600;
  color: var(--spark-text);
}

.setting-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

/* 扩展点击热区到 44px，视觉尺寸不变 */
.length-help-button::after {
  content: '';
  position: absolute;
  inset: -11px;
}

.length-help-button {
  width: 22px;
  height: 22px;
  position: relative;
  flex: 0 0 22px;
  padding: 0;
  border: 1px solid var(--spark-border);
  border-radius: 50%;
  background: var(--spark-panel-bg);
  color: var(--spark-text-muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.length-help-button:active {
  color: var(--spark-primary);
  border-color: color-mix(in srgb, var(--spark-primary), transparent 35%);
  background: color-mix(in srgb, var(--spark-panel-bg), var(--spark-primary) 8%);
}

.length-help-text {
  max-width: min(280px, 72vw);
  line-height: 1.5;
}

.preset-description {
  margin: 0;
  font-size: var(--spark-fs-xs);
  line-height: 1.35;
  color: var(--spark-text-muted);
}

.custom-setting-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.custom-setting-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--spark-border), transparent 10%);
  border-radius: 10px;
  background: color-mix(in srgb, var(--spark-panel-bg), var(--spark-primary) 2%);
}

.custom-setting-label {
  font-size: var(--spark-fs-xs);
  line-height: 1.4;
  color: var(--spark-text-muted);
}

.compact-number-input {
  width: 100%;
}

.chapter-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chapter-card {
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.chapter-card:active {
  transform: scale(0.98);
  background: rgba(var(--spark-primary-rgb), 0.03);
}

.chapter-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.chapter-title {
  font-weight: 600;
  color: var(--spark-text);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chapter-summary {
  font-size: var(--spark-fs-sm);
  color: var(--spark-text-muted);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.more-hint {
  text-align: center;
  padding: 12px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-primary);
  cursor: pointer;
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
}

.history-hint:active {
  background: rgba(var(--spark-primary-rgb), 0.05);
}

.history-hint span {
  flex: 1;
}

.full-chapter-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 100px;
}

.full-chapter-list .chapter-card {
  cursor: default;
}

.full-chapter-list .chapter-card:active {
  transform: none;
  background: var(--spark-panel-bg);
}

/* Custom Textarea Heights */
/* Custom Textarea Heights */

.chapter-input {
  height: 15vh;
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

@media (max-width: 380px) {
  .preset-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .custom-setting-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
