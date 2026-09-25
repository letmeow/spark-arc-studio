<template>
  <div class="synopsis-mobile-flow">
    <!-- Logline 区 -->
    <div class="flow-section">
      <div class="section-header">
        <n-icon :component="FileText" size="18" />
        <span>{{ t('views.synopsis.common.logline') }}</span>
      </div>
      <MobileTextArea
        v-model:value="synopsisData.logline"
        :autosize="{ minRows: 2, maxRows: 8 }"
        customClass="logline-input"
        :title="t('views.synopsis.mobile.editLogline')"
        :placeholder="t('views.synopsis.common.loglinePlaceholder')"
      />
    </div>
    
    <!-- 生成引导 -->
    <div class="flow-section">
      <div class="section-header">
        <n-icon :component="MessagesSquare" size="18" />
        <span>{{ t('views.synopsis.common.guidance') }}</span>
        <div class="header-actions">
          <n-button
            size="tiny"
            type="primary"
            :loading="isGenerating"
            :disabled="!synopsisData.logline?.trim()"
            @click="handleGenerateSynopsisClick"
          >
            <template #icon><n-icon :component="Sparkles" /></template>
            {{ t('views.synopsis.mobile.generateFullSynopsis') }}
          </n-button>
        </div>
      </div>
      <MobileTextArea
        v-model:value="synopsisData.guidance"
        :autosize="{ minRows: 3, maxRows: 10 }"
        customClass="guidance-input"
        :title="t('views.synopsis.mobile.editGuidance')"
        :placeholder="t('views.synopsis.common.guidancePlaceholder')"
      />
    </div>
    
    <!-- 梗概内容 -->
    <div class="flow-section content-section" v-if="synopsisData.synopsis_text || isGenerating">
      <GlobalLoading scope="synopsis" target="content" variant="card" />
        <div class="section-header">
        <n-icon :component="BookOpen" size="18" />
        <span>{{ t('views.synopsis.mobile.storySynopsis') }}</span>
        <div class="header-actions">
          <n-button size="tiny" type="primary" @click="goToStructureStep">
            <template #icon><n-icon :component="ArrowRight" /></template>
            {{ t('views.structure.mobile.generateOutline') }}
          </n-button>
          <n-button size="tiny" quaternary @click="synopsisData.synopsis_text = ''">{{ t('views.world.mobile.clear') }}</n-button>
        </div>
      </div>
      <MobileTextArea
        v-model:value="synopsisData.synopsis_text"
        customClass="synopsis-input"
        :title="t('views.synopsis.mobile.editSynopsis')"
        :disabled="isGenerating"
        :autosize="{ minRows: 4, maxRows: 25 }"
      />
    </div>
    
    <!-- 节拍表快速预览 -->
    <div class="flow-section beat-section">
      <GlobalLoading scope="synopsis" target="beats" variant="card" />
      <div class="section-header">
        <n-icon :component="Activity" size="18" />
        <span>{{ t('views.synopsis.common.beatSheet') }}</span>
        <n-button 
          size="tiny" 
          type="primary" 
          ghost
          :loading="isGeneratingBeats"
          :disabled="!synopsisData.synopsis_text?.trim()"
          @click="handleGenerateBeatsClick"
        >
          {{ t('views.synopsis.mobile.generateBeat') }}
        </n-button>
      </div>
      
      <!-- Mini 情绪曲线 -->
      <div v-if="beatSheet.beats?.length > 0" class="beat-visualizer">
        <div class="beat-chart">
          <div 
            v-for="(beat, index) in beatSheet.beats" 
            :key="beat.beat_id || index"
            class="beat-bar"
            :style="{ 
              height: getTensionHeight(beat.tension_level),
              backgroundColor: getBeatColor(beat.emotional_goal)
            }"
          />
        </div>
        <div class="beat-count">{{ t('views.synopsis.mobile.beatCount', { count: beatSheet.beats.length }) }}</div>
      </div>
      
      <n-empty v-else :description="t('views.synopsis.mobile.noBeatData')" style="padding: 20px 0;">
        <template #extra>
          <span class="empty-hint">{{ t('views.synopsis.mobile.generateSynopsisFirst') }}</span>
        </template>
      </n-empty>
      
      <!-- 展开详情按钮 -->
      <n-button 
        v-if="beatSheet.beats?.length > 0"
        block 
        dashed 
        @click="showBeatDetail = true"
      >
        {{ t('views.synopsis.mobile.viewDetailedBeat') }}
      </n-button>
    </div>
    
    
    <!-- 节拍详情抽屉 -->
    <n-drawer v-model:show="showBeatDetail" placement="bottom" height="85%">
      <n-drawer-content :title="t('views.synopsis.mobile.editBeatSheet')" closable>
        <div class="beat-detail-list">
          <div 
            v-for="(beat, index) in beatSheet.beats" 
            :key="beat.beat_id || index"
            class="beat-card"
          >
            <div class="beat-header">
              <SparkTag type="info" size="small">#{{ Number(index) + 1 }}</SparkTag>
              <n-input v-model:value="beat.beat_type" :placeholder="t('views.synopsis.desktop.beatType')" size="small" style="flex: 1" />
              <n-select 
                v-model:value="beat.tension_level" 
                :options="tensionOptions" 
                size="small"
                style="width: 70px"
              />
              <n-button quaternary circle size="small" @click="removeBeat(index)">
                <template #icon><n-icon :component="X" /></template>
              </n-button>
            </div>
            <MobileTextArea 
              v-model:value="beat.narrative_action" 
              customClass="beat-input"
              :title="t('views.synopsis.mobile.editNarrativeAction')"
              :placeholder="t('views.synopsis.desktop.narrativeAction')"
              :autosize="{ minRows: 2, maxRows: 5 }"
            />
          </div>
          <n-button block dashed @click="addBeat">{{ t('views.synopsis.desktop.addBeat') }}</n-button>
        </div>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { NInput, NSelect, NButton, NIcon, NEmpty, NDrawer, NDrawerContent } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import SparkTag from '../../components/share/SparkTag.vue';
import { Activity, ArrowRight, BookOpen, FileText, MessagesSquare, Sparkles, X } from '@lucide/vue';
import { useSynopsisLogic } from '../../composables/useSynopsisLogic';
import GlobalLoading from '../../components/share/GlobalLoading.vue';
import MobileTextArea from '../../components/editors/mobile/MobileTextArea.vue';
import { MOBILE_FLOW_STEP, scrollToFlowStep } from '../../utils/mobileFlow';

const { t } = useI18n();
const showBeatDetail = ref(false);

const {
  synopsisData,
  isGenerating,
  beatSheet,
  isGeneratingBeats,
  tensionOptions,
  getTensionHeight,
  getBeatColor,
  handleGenerateSynopsis,
  handleGenerateBeats,
  addBeat,
  removeBeat,
  goToStructure
} = useSynopsisLogic();

function handleGenerateSynopsisClick() {
  void handleGenerateSynopsis();
}

function handleGenerateBeatsClick() {
  void handleGenerateBeats();
}

function goToStructureStep() {
  void goToStructure({
    autoGenerateOutline: true,
    beforeNavigate: () => scrollToFlowStep(MOBILE_FLOW_STEP.structure),
  });
}
</script>

<style scoped>
.synopsis-mobile-flow {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.flow-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.content-section {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
}

.beat-section {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
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

.beat-visualizer {
  padding: 12px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 10px;
}

.beat-chart {
  display: flex;
  align-items: flex-end;
  height: 50px;
  gap: 4px;
  margin-bottom: 8px;
}

.beat-bar {
  flex: 1;
  min-width: 8px;
  border-radius: 3px 3px 0 0;
  transition: height 0.3s ease;
}

.beat-count {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
  text-align: center;
}

.empty-hint {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
}

.beat-detail-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 100px;
}

.beat-card {
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--spark-border);
  border-radius: 8px;
  padding: 10px;
}

.beat-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

/* Custom Textarea Heights */

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
