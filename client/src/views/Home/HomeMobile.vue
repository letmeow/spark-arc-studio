<template>
  <div class="home-mobile">
    <SparkLoaderAnimation class="home-spark-anim" aria-hidden="true" />
    <h1 class="home-title">{{ greetWord }}</h1>
    <p class="home-tagline">{{ t('views.home.tagline') }}</p>

    <div class="home-proj-row">
      <div class="home-proj-pill" :class="{ 'is-warn': !projectStore.currentProject }">
        <span class="home-proj-dot"></span>
        <span>{{ projectStore.currentProject || t('views.home.noProjectHint') }}</span>
      </div>
    </div>

    <div class="home-composer">
      <n-input
        :value="draft"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 5 }"
        :placeholder="t('views.home.placeholderDefault')"
        class="home-textarea"
        @update:value="draft = $event"
      />
      <div class="home-composer-foot">
        <n-button quaternary circle size="small" @click="toastAttachmentHint">
          <template #icon><n-icon :component="Plus" /></template>
        </n-button>
        <n-button quaternary circle size="small" class="home-save-muse" :loading="savingMuse" @click="saveAsMuse">
          <template #icon><n-icon :component="Sparkles" /></template>
        </n-button>
        <n-button circle type="primary" size="small" class="home-send-btn" @click="handleSend">
          <template #icon><n-icon :component="ArrowUp" /></template>
        </n-button>
      </div>
    </div>

    <div class="home-chips">
      <button v-for="chip in chips" :key="chip" class="home-chip" @click="fillChip(chip)">{{ chip }}</button>
    </div>

    <button class="home-muse-line" @click="toastMuse">
      <n-icon :component="Sparkles" class="home-muse-star" />
      <span>{{ t('views.home.museLine', { count: museTotal }) }}</span>
      <span>→</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { NButton, NIcon, NInput } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { ArrowUp, Plus, Sparkles } from '@lucide/vue';
import SparkLoaderAnimation from '../../components/share/SparkLoaderAnimation.vue';
import { useHomeLogic } from '../../composables/useHomeLogic';
import bus from '../../eventBus';

const { t } = useI18n();

const {
  draft,
  savingMuse,
  greetWord,
  chips,
  museTotal,
  fillChip,
  saveAsMuse,
  beginSendToDirector,
  commitSendToDirector,
  viewStore,
  projectStore,
} = useHomeLogic();

function toastAttachmentHint() {
  bus.emit('toast', { type: 'info', message: t('components.chatPanel.fileImportRequiresProject') });
}

function toastMuse() {
  bus.emit('toast', { type: 'info', message: t('views.home.adoptMuseHint') });
  viewStore.setView('world');
}

function handleSend() {
  const text = beginSendToDirector();
  if (!text) return;
  // 移动端抽屉已有 320ms 高度过渡（GlobalChatFloat DRAWER_HEIGHT_ANIM_MS），
  // 首页不做 FLIP，直接切 chat，动画交给 chat 页与抽屉，避免过渡打架。
  commitSendToDirector(text);
}
</script>

<style scoped>
.home-mobile {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 12px 16px 24px;
}

.home-spark-anim {
  width: 72px;
  height: 72px;
  flex-shrink: 0;
}

.home-spark-anim :deep(.spark-loader-stage) {
  width: 100%;
  height: 100%;
  margin-bottom: 0;
}

.home-title {
  font-size: var(--spark-fs-h2);
  font-weight: 800;
  color: var(--spark-text);
  margin: 10px 0 0;
}

.home-tagline {
  margin: 4px 0 0;
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
}

.home-proj-row {
  display: flex;
  margin-top: 12px;
}

.home-proj-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-text);
}

.home-proj-pill.is-warn {
  border-color: color-mix(in srgb, var(--spark-warning), var(--spark-border) 40%);
  background: color-mix(in srgb, var(--spark-warning), transparent 88%);
}

.home-proj-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--spark-success);
}

.home-proj-pill.is-warn .home-proj-dot {
  background: var(--spark-warning);
}

.home-composer {
  width: 100%;
  margin-top: 14px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius-lg);
  box-shadow: var(--spark-shadow);
  padding: 12px 14px 10px;
  text-align: left;
}

.home-composer:focus-within {
  border-color: var(--spark-primary);
  box-shadow: 0 0 0 3px var(--spark-primary-glow), var(--spark-shadow);
}

.home-textarea :deep(.n-input__textarea-el) {
  font-size: var(--spark-fs-base);
  line-height: 1.6;
}

.home-composer-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.home-save-muse {
  color: var(--spark-warning);
}

.home-send-btn {
  margin-left: auto;
  box-shadow: 0 4px 12px var(--spark-primary-glow);
}

.home-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
  justify-content: center;
}

.home-chip {
  font-size: var(--spark-fs-sm);
  font-family: inherit;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 999px;
  padding: 8px 16px;
  cursor: pointer;
  color: var(--spark-text);
}

.home-chip:active {
  border-color: var(--spark-primary);
}

.home-muse-line {
  margin-top: 16px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: var(--spark-fs-xs);
  font-family: inherit;
  color: var(--spark-text-muted);
  opacity: 0.7;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.home-muse-star {
  color: var(--spark-warning);
}
</style>
