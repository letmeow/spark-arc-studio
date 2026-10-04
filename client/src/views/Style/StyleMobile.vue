
<template>
  <div class="mobile-style-view">
    <GlobalLoading scope="style" />
    <input
      ref="styleProfileImportInput"
      class="hidden-file-input"
      type="file"
      accept=".md,text/markdown"
      @change="handleStyleProfileImportFile"
    />
    <div class="mobile-header">
       <n-button circle quaternary size="small" @click="loadStyles">
         <template #icon><n-icon><RefreshCw /></n-icon></template>
       </n-button>
    </div>

    <!-- Content -->
    <div class="style-list-mobile">
        <n-spin v-if="isLoadingList" />
        <MobileEmptyState
          v-else-if="styles.length === 0"
          fill
          :icon="Palette"
          :title="t('views.style.mobile.noLocalStyle')"
        />
        <div 
          v-else 
          v-for="style in styles" 
          :key="style.style_id"
          class="mobile-style-item"
          @click="openStyleDetails(style)"
        >
           <div class="style-indicator" :style="{ background: getGradient(style.style_id) }"></div>
           <div class="style-info">
              <span class="style-name">{{ style.style_name }}</span>
            </div>
            <div class="mobile-style-actions">
             <n-button
               v-if="projectStore.currentProject"
               size="tiny"
               secondary
               :type="isStyleAppliedToCurrentProject(style) ? 'success' : 'primary'"
               :disabled="isApplying"
               :loading="isApplying && applyingStyleId === style.style_id"
               @click.stop="handleApplyToProject(style)"
             >
               {{ isStyleAppliedToCurrentProject(style) ? t('views.style.common.applied') : t('views.style.common.apply') }}
             </n-button>
             <n-button
               size="tiny"
               quaternary
               circle
               :title="t('views.style.common.exportProfile')"
               @click.stop="handleExportStyle(style)"
             >
               <template #icon><n-icon><Download /></n-icon></template>
             </n-button>
            </div>
            <n-icon class="chevron"><ChevronRight /></n-icon>
         </div>
     </div>

     <div class="mobile-footer-actions m-action-bar">
       <n-button
         secondary
                  :loading="isImportingStyleProfile"
         @click="triggerStyleProfileImport"
       >
         <template #icon><n-icon><Upload /></n-icon></template>
         {{ t('views.style.common.importProfile') }}
       </n-button>
       <n-button
         type="primary"
                  :disabled="hasRunningAnalysis"
         @click="openCreateModal"
       >
         <template #icon><n-icon><Plus /></n-icon></template>
         {{ hasRunningAnalysis ? t('views.common.analyzing') : t('views.common.create') }}
       </n-button>
    </div>

    <!-- Details Sidebar (using full screen for mobile or adjusted drawer) -->
    <n-drawer v-model:show="showDetailsDrawer" width="100%" placement="right">
       <n-drawer-content :title="selectedStyleName" closable>
         <!-- @vue-ignore -->
         <template #header-extra>
             <div class="mobile-drawer-actions">
               <n-button
                 secondary
                 size="small"
                 @click="handleExportStyle(selectedStyle)"
               >
                 <template #icon><n-icon><Download /></n-icon></template>
                 {{ t('views.style.common.exportProfile') }}
               </n-button>
             </div>
          </template>

         <div v-if="isLoadingProfile" class="loading-state">
            <n-spin size="medium" />
         </div>

         <div v-else-if="currentProfile" class="mobile-profile-content">
            <div class="mobile-profile-markdown" v-html="profileMarkdown"></div>
         </div>
       </n-drawer-content>
    </n-drawer>

    <!-- Simple Create Modal for Mobile -->
     <n-modal v-model:show="showCreateModal" preset="card" :title="t('views.style.mobile.createStyle')" style="width: 90vw">
       <div class="mobile-form">
         <n-form-item :label="t('views.style.desktop.styleNameLabel')">
           <n-input v-model:value="newStyleName" :placeholder="t('views.style.mobile.styleName')" />
          </n-form-item>
          
          <div
            class="mobile-upload-zone"
          >
            <DocumentImportPicker
              usage="style_analysis"
              variant="mobile"
              :icon-size="32"
              :title="t('views.style.mobile.selectFile')"
              :subtitle="t('views.style.mobile.uploadHint')"
              @select="handleImportedFile"
              @invalid="handleInvalidImportedFile"
            />
          </div>

       </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { NIcon, NSpin, NButton, NInput, NDrawer, NDrawerContent, NModal, NFormItem } from 'naive-ui';
import DocumentImportPicker from '../../components/import/DocumentImportPicker.vue';
import MobileEmptyState from '../../components/layouts/mobile/MobileEmptyState.vue';
import { ChevronRight, Download, Palette, Plus, RefreshCw, Upload } from '@lucide/vue';
import GlobalLoading from '../../components/share/GlobalLoading.vue';
import { useStyleLogic } from '../../composables/useStyleLogic';
import { renderStyleMarkdown } from '../../utils/styleMarkdown';

const { t } = useI18n();


const {
  styles,
  isLoadingList,
  isImportingStyleProfile,
  styleProfileImportInput,
  showCreateModal,
  showDetailsDrawer,
  selectedStyle,
  selectedStyleName,
  currentProfile,
  isLoadingProfile,
  newStyleName,
  isApplying,
  applyingStyleId,
  hasRunningAnalysis,
  isStyleAppliedToCurrentProject,
  handleExportStyle,
  triggerStyleProfileImport,
  handleStyleProfileImportFile,
  loadStyles,
  openCreateModal,
  openStyleDetails,
  handleApplyToProject,
  handleImportedFile,
  handleInvalidImportedFile,
  getGradient,
  projectStore
} = useStyleLogic();

const profileMarkdown = computed(() => {
  if (!currentProfile.value) return '';
  return renderStyleMarkdown(currentProfile.value);
});
</script>

<style scoped>
.mobile-style-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 4px;
  gap: 10px;
  background: transparent;
  min-height: 0;
}

/* ============ Markdown 风格档案渲染样式(移动端) ============ */
.mobile-profile-markdown {
  font-size: 14px;
  color: var(--text-color);
  line-height: 1.65;
  padding: 4px 6px;
}

.mobile-profile-markdown :deep(.style-md-h2),
.mobile-profile-markdown :deep(.style-md-h3) {
  margin: 18px 0 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-color);
  font-weight: 600;
  color: var(--primary-color);
}

.mobile-profile-markdown :deep(.style-md-h2) {
  font-size: 1.05rem;
}

.mobile-profile-markdown :deep(.style-md-h3) {
  font-size: 0.98rem;
  color: var(--text-color);
  border-bottom-style: dashed;
  opacity: 0.95;
}

.mobile-profile-markdown :deep(.style-md-p) {
  margin: 6px 0;
}

.mobile-profile-markdown :deep(.style-md-ul) {
  margin: 6px 0;
  padding-left: 20px;
  list-style: disc;
}

.mobile-profile-markdown :deep(.style-md-li) {
  margin-bottom: 4px;
}

.mobile-profile-markdown :deep(.style-md-hr) {
  margin: 14px 0;
  border: none;
  border-top: 1px dashed var(--border-color);
}

.mobile-profile-markdown :deep(strong) {
  color: var(--primary-color);
  font-weight: 600;
}

.hidden-file-input {
  display: none;
}

.mobile-header {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
  min-height: 34px;
}

.style-list-mobile {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mobile-style-item {
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 12px;
  padding: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.style-indicator {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  flex-shrink: 0;
}

.style-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.style-name {
  font-weight: 600;
  color: var(--spark-text-bright);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.style-sub {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
}

.chevron {
  color: var(--spark-text-muted);
  flex-shrink: 0;
}

.mobile-style-actions,
.mobile-drawer-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.mobile-footer-actions {
  padding-bottom: 24px;
  display: flex;
  gap: 8px;
}

.mobile-footer-actions :deep(.n-button) {
  flex: 1;
  min-width: 0;
}

.mobile-profile-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mobile-upload-hint {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
  text-align: center;
  padding: 12px 0;
}

.mobile-upload-zone {
  border: 2px dashed var(--spark-border);
  border-radius: 12px;
  padding: 24px;
  text-align: center;
  background: var(--spark-panel-bg);
  color: var(--spark-text-muted);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.mobile-upload-zone p {
  margin: 0;
  font-size: var(--spark-fs-sm);
}

/* 任务浮层（屏幕居中） */
</style>
