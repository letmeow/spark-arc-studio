<template>
  <div class="lorebook-mobile-host">
    <GlobalLoading scope="world" />
    <div class="lorebook-mobile-flow">
      <!-- 世界观输入 -->
      <div class="flow-section">
      <div class="section-header">
        <n-icon :component="Globe" size="18" />
        <span>{{ t('views.lorebook.mobile.worldview') }}</span>
        <div class="header-actions">
          <n-button size="tiny" type="primary" @click="goToSynopsisStep">
            <template #icon><n-icon :component="ArrowRight" /></template>
            {{ t('views.lorebook.mobile.writeSynopsisAndRhythm') }}
          </n-button>
        </div>
      </div>
      <MobileTextArea
        v-model:value="worldview"
        customClass="worldview-input"
        :title="t('views.lorebook.mobile.worldview')"
        :placeholder="t('views.lorebook.mobile.worldviewPlaceholder')"
        :autosize="{ minRows: 3, maxRows: 20 }"
      />
      </div>
    
    <!-- 角色列表 -->
      <div v-if="!worldOnly" class="flow-section">
      <div class="section-header">
        <n-icon :component="Users" size="18" />
        <span>{{ t('views.lorebook.mobile.characterSettings') }}</span>
        <SparkTag type="info" size="small">{{ characters.length }}</SparkTag>
      </div>
      
      <n-spin :show="loading">
        <div v-if="characters.length > 0" class="character-list">
          <div 
            v-for="ch in characters.slice(0, 6)" 
            :key="ch.id"
            class="character-card"
            @click="editCharacter(ch)"
          >
            <div class="char-avatar">
              <n-icon :component="CircleUser" size="24" />
            </div>
            <div class="char-info">
              <div class="char-name">{{ ch.name || t('views.lorebook.mobile.characterDefaultName', { id: ch.id }) }}</div>
              <div class="char-desc">{{ ch.content?.substring(0, 50) || t('views.lorebook.mobile.noCharacterSetting') }}...</div>
            </div>
            <n-icon :component="ChevronRight" size="18" class="char-arrow" />
          </div>
          
          <div v-if="characters.length > 6" class="more-hint" @click="showEditor = true">
            {{ t('views.lorebook.mobile.viewAllCharacters', { count: characters.length }) }}
          </div>
        </div>
        
        <n-empty v-else :description="t('views.lorebook.mobile.noCharacterSettings')" style="padding: 20px 0;">
          <template #extra>
            <n-button size="small" type="primary" @click="showEditor = true">
              {{ t('views.lorebook.mobile.addCharacter') }}
            </n-button>
          </template>
        </n-empty>
      </n-spin>
      </div>
    
    <!-- 快捷工具 -->
      <div class="flow-section">
      <div class="section-header">
        <n-icon :component="Wrench" size="18" />
        <span>{{ t('views.lorebook.mobile.quickTools') }}</span>
      </div>
      
      <div class="action-buttons-row">
        <n-button v-if="!worldOnly" type="primary" secondary class="action-btn" @click="showCharGen = true">
          <template #icon><n-icon :component="UserPlus" /></template>
          {{ t('views.lorebook.mobile.aiCharacterGeneration') }}
        </n-button>
        <n-button type="primary" secondary class="action-btn" @click="showWorldGen = true">
          <template #icon><n-icon :component="Globe" /></template>
          {{ t('views.lorebook.mobile.adjustWorldview') }}
        </n-button>
      </div>
      </div>
    
    <!-- 完整编辑器抽屉（仅通过快捷工具访问） -->
      <n-drawer v-if="!worldOnly" v-model:show="showEditor" placement="bottom" height="90%">
      <n-drawer-content :title="t('views.lorebook.mobile.settingManagement')" closable>
        <LorebookEditor :visible="true" :embedded="true" @close="showEditor = false" />
      </n-drawer-content>
      </n-drawer>

    <!-- 单一角色编辑器抽屉（点击卡片访问） -->
      <n-drawer v-if="!worldOnly" v-model:show="showSingleCharDrawer" placement="bottom" height="85%" class="mobile-char-drawer">
      <n-drawer-content :title="editingChar.name || t('views.lorebook.mobile.newCharacter')" closable>
        <div class="char-editor-form" v-if="editingChar">
           <div class="form-item">
             <label>{{ t('views.lorebook.mobile.characterName') }}</label>
             <n-input v-model:value="editingChar.name" :placeholder="t('views.lorebook.mobile.characterNamePlaceholder')" size="large" />
           </div>
           
           <div class="form-item">
             <label>{{ t('views.lorebook.mobile.detailSetting') }}</label>
             <MobileTextArea 
               v-model:value="editingChar.content" 
               :title="t('views.lorebook.mobile.detailSetting')"
               :placeholder="t('views.lorebook.mobile.detailSettingPlaceholder')" 
               customClass="desc-input"
               :autosize="{ minRows: 6, maxRows: 20 }"
             />
           </div>

           <div class="action-bar">
              <n-button 
                type="error" 
                ghost 
                @click="handleDeleteChar" 
                v-if="editingChar.id !== -1 && editingChar.id"
              >
                {{ t('views.common.delete') }}
              </n-button>
           </div>
        </div>
      </n-drawer-content>
      </n-drawer>
    
    <!-- 角色生成器抽屉 -->
      <n-drawer v-if="!worldOnly" v-model:show="showCharGen" placement="bottom" height="80%">
      <n-drawer-content :title="t('views.lorebook.mobile.aiCharacterGeneration')" closable>
        <CharacterGeneratorPanel :visible="true" :embedded="true" />
      </n-drawer-content>
      </n-drawer>

    <!-- 调整世界观抽屉 -->
      <n-drawer v-model:show="showWorldGen" placement="bottom" height="85%">
      <n-drawer-content :title="t('views.lorebook.mobile.adjustWorldview')" closable>
        <WorldGeneratorPanel />
      </n-drawer-content>
      </n-drawer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, inject, watch, reactive, onBeforeUnmount } from 'vue';
import { useI18n } from 'vue-i18n';
import bus from '../../eventBus';
import { NButton, NIcon, NInput, NSpin, NEmpty, NDrawer, NDrawerContent, useMessage } from 'naive-ui';
import SparkTag from '../../components/share/SparkTag.vue';
import { ArrowRight, ChevronRight, CircleUser, Globe, UserPlus, Users, Wrench } from '@lucide/vue';
import LorebookEditor from '../../components/lorebook/LorebookEditor.vue';
import GlobalLoading from '../../components/share/GlobalLoading.vue';
import CharacterGeneratorPanel from '../../components/lorebook/CharacterGeneratorPanel.vue';
import WorldGeneratorPanel from '../../components/lorebook/WorldGeneratorPanel.vue';
import MobileTextArea from '../../components/editors/mobile/MobileTextArea.vue';
import { fetchWithAuth, fetchCharacters, saveCharacter, deleteCharacter, renameCharacter } from '../../services/api';
import { useProjectStore } from '../../components/stores/projectStore';
import { useCharacterStore } from '../../components/stores/characterStore';
import { useSceneStore } from '../../components/stores/sceneStore';
import { useViewStore } from '../../components/stores/viewStore';
import { MOBILE_FLOW_STEP, scrollToFlowStep } from '../../utils/mobileFlow';
import { extractLoglineFromInspiration } from '../../utils/inspiration';
import { buildCreativeCacheKey, isCreativeCacheEqual, loadCreativeCache, saveCreativeCache } from '@/utils/creativeLocalCache';
import { createAutoSaveScheduler } from '@/utils/autoSaveScheduler';

const { worldOnly = false } = defineProps<{ worldOnly?: boolean }>();

const { t } = useI18n();
const message = useMessage();
const projectId = inject('projectId', ref(null));
const projectStore = useProjectStore();
const characterStore = useCharacterStore();
const sceneStore = useSceneStore();
const viewStore = useViewStore();

const loading = ref(false);
const showEditor = ref(false);
const showCharGen = ref(false);
const showWorldGen = ref(false);
const showSingleCharDrawer = ref(false);
const worldview = ref('');
const characters = ref([]);
const SYSTEM_CHARACTER_IDS = new Set([-1, -2]);
const isSystemCharacter = (ch: any) => SYSTEM_CHARACTER_IDS.has(Number(ch?.id));
const userCharactersOnly = (items: any[]) => (Array.isArray(items) ? items.filter(ch => !isSystemCharacter(ch)) : []);
let suppressWorldviewAutoSave = false;
let worldviewSaveTimer: ReturnType<typeof setTimeout> | null = null;

// 编辑状态
const editingChar = reactive<{
  id: number | string | null;
  name: string;
  content: string;
}>({
  id: null,
  name: '',
  content: ''
});

type CharacterSavePayload = {
  projectName: string;
  id: number | string;
  name: string;
  content: string;
};

const characterSaveScheduler = createAutoSaveScheduler<CharacterSavePayload>(async payload => {
  const original = characters.value.find((character: any) => character.id === payload.id);
  const oldName = String(original?.name || '').trim();
  await saveCharacter(payload.projectName, payload.id, payload.content);
  if (payload.name && payload.name !== oldName) {
    await renameCharacter(payload.projectName, payload.id, payload.name);
    sceneStore.renameSpeaker(oldName, payload.name);
  }
  if (original) {
    original.name = payload.name || oldName;
    original.content = payload.content;
  }
  saveLorebookSnapshot();
  await characterStore.reload(payload.projectName);
}, {
  onError: error => {
    message.error(t('views.common.saveFailed'));
    console.error('自动保存角色失败:', error);
  },
});

type LorebookMobileCacheSnapshot = {
  worldview: string;
  characters: Array<{ id: number | string; name?: string; content?: string }>;
  editingCharDraft?: {
    id: number | string | null;
    name: string;
    content: string;
  } | null;
};

function buildLorebookCacheKey() {
  return buildCreativeCacheKey('lorebook-content', projectStore.currentProject);
}

function saveLorebookSnapshot() {
  if (!projectStore.currentProject) return;
  const payload: LorebookMobileCacheSnapshot = {
    worldview: worldview.value,
    characters: Array.isArray(characters.value)
      ? characters.value.map((ch: any) => ({
          id: ch.id,
          name: ch.name || '',
          content: ch.content || '',
        }))
      : [],
    editingCharDraft: showSingleCharDrawer.value
      ? {
          id: editingChar.id,
          name: editingChar.name,
          content: editingChar.content,
        }
      : null,
  };
  saveCreativeCache(buildLorebookCacheKey(), payload);
}

function hydrateLorebookFromCache() {
  const cached = loadCreativeCache<LorebookMobileCacheSnapshot>(buildLorebookCacheKey());
  if (!cached) return;
  worldview.value = cached.worldview || '';
  if (Array.isArray(cached.characters)) {
    characters.value = userCharactersOnly(cached.characters).map((ch) => ({ ...ch }));
  }
  if (cached.editingCharDraft) {
    editingChar.id = cached.editingCharDraft.id;
    editingChar.name = cached.editingCharDraft.name || '';
    editingChar.content = cached.editingCharDraft.content || '';
  }
}

// 加载世界观
async function loadWorldview() {
  const pid = projectId.value;
  const fileId = '世界观.txt';
  if (!pid) return;
  try {
    suppressWorldviewAutoSave = true;
    const res = await fetchWithAuth(`/api/lorebooks/${pid}/${fileId}`);
    if (res.ok) {
      const data = await res.json();
      const remoteWorldview = data?.content || '';
      if (!isCreativeCacheEqual(worldview.value, remoteWorldview)) {
        worldview.value = remoteWorldview;
      }
    } else if (res.status === 404) {
      worldview.value = '';
    }
  } catch {} finally {
    suppressWorldviewAutoSave = false;
    saveLorebookSnapshot();
  }
}

// 保存世界观
async function saveWorldview() {
  const pid = projectId.value;
  const fileId = '世界观.txt';
  if (!pid) return;
  try {
    const res = await fetchWithAuth('/api/lorebooks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectName: pid, fileName: fileId, content: worldview.value })
    });
    const result = await res.json();
    if (res.ok && result?.success !== false) {
      saveLorebookSnapshot();
    }
  } catch {
    message.error(t('views.common.saveFailed'));
  }
}

watch(worldview, () => {
  if (suppressWorldviewAutoSave) return;
  saveLorebookSnapshot();
  if (worldviewSaveTimer) {
    clearTimeout(worldviewSaveTimer);
  }
  worldviewSaveTimer = setTimeout(() => {
    void saveWorldview();
  }, 600);
});

async function goToSynopsisStep() {
  await saveWorldview();
  const inspiration = (projectStore.boundInspiration || '').trim();
  const payload = {
    projectName: projectStore.currentProject,
    inspiration,
    logline: extractLoglineFromInspiration(inspiration),
    autoGenerateSynopsis: true,
    autoGenerateBeats: true,
  };
  projectStore.setPendingSynopsisAdoption(payload);
  viewStore.setView('synopsis');
  bus.emit('adopt-inspiration', payload);
  scrollToFlowStep(MOBILE_FLOW_STEP.synopsis);
}

// 加载角色
async function loadCharacters() {
  const pid = projectId.value;
  if (!pid) return;
  loading.value = true;
  try {
    const remoteCharacters = await fetchCharacters(pid, true);
    if (!isCreativeCacheEqual(characters.value, remoteCharacters)) {
      characters.value = userCharactersOnly(remoteCharacters);
    }
  } catch {
    characters.value = [];
  } finally {
    loading.value = false;
    saveLorebookSnapshot();
  }
}

async function editCharacter(ch) {
  await characterSaveScheduler.flush();
  editingChar.id = ch.id;
  editingChar.name = ch.name;
  editingChar.content = ch.content;
  showSingleCharDrawer.value = true;
  saveLorebookSnapshot();
}

async function handleDeleteChar() {
    if (!editingChar.id) return;
    try {
        await deleteCharacter(projectId.value, Number(editingChar.id));
        await loadCharacters();
        showSingleCharDrawer.value = false;
        saveLorebookSnapshot();
      message.success(t('views.common.deleted'));
    } catch {
      message.error(t('views.common.deleteFailed'));
    }
}


async function loadData() {
  await Promise.all([loadWorldview(), loadCharacters()]);
}

function onLorebookRefresh() {
  loadData();
}

watch(
  [() => editingChar.name, () => editingChar.content],
  () => {
    saveLorebookSnapshot();
    const pid = projectId.value;
    if (!showSingleCharDrawer.value || !pid || editingChar.id === null) return;
    characterSaveScheduler.schedule({
      projectName: pid,
      id: editingChar.id,
      name: String(editingChar.name || '').trim(),
      content: editingChar.content || '',
    });
  }
);

watch(showSingleCharDrawer, (visible) => {
  saveLorebookSnapshot();
  if (!visible) void characterSaveScheduler.flush();
});

onMounted(() => {
  hydrateLorebookFromCache();
  loadData();
});
onMounted(() => {
  bus.on('lorebook-refresh', onLorebookRefresh);
});

onBeforeUnmount(() => {
  bus.off('lorebook-refresh', onLorebookRefresh);
  if (worldviewSaveTimer) {
    clearTimeout(worldviewSaveTimer);
    worldviewSaveTimer = null;
  }
  void characterSaveScheduler.flush();
});
watch(projectId, () => {
  characterSaveScheduler.cancel();
  if (worldviewSaveTimer) {
    clearTimeout(worldviewSaveTimer);
    worldviewSaveTimer = null;
  }
  hydrateLorebookFromCache();
  loadData();
});
</script>

<style scoped>
/* ... previous styles ... */

.brand-btn {
  background-color: var(--spark-primary);
  border-color: var(--spark-primary);
  color: var(--spark-text-inverse); 
}

.brand-btn:hover, .brand-btn:active {
  background-color: var(--spark-primary-dim);
  border-color: var(--spark-primary-dim);
  color: var(--spark-text-inverse);
}

.char-editor-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 8px 0;
}

.form-item label {
  display: block;
  font-size: var(--spark-fs-base);
  font-weight: 600;
  color: var(--spark-text-muted);
  margin-bottom: 8px;
}

.action-bar {
  display: flex;
  align-items: center;
  margin-top: 12px;
}

.lorebook-mobile-flow {
  display: flex;
  flex-direction: column;
  gap: 20px;
  position: relative;
}

.lorebook-mobile-host {
  position: relative;
  width: calc(100% + 20px);
  margin: 0 -10px;
  padding: 10px;
  box-sizing: border-box;
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

.header-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  margin-left: auto;
}

.section-header .spark-tag {
  margin-left: auto;
}

.character-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.character-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
}

.character-card:active {
  transform: scale(0.98);
  background: rgba(var(--spark-primary-rgb), 0.03);
}

.char-avatar {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: linear-gradient(135deg, 
    rgba(var(--spark-primary-rgb), 0.15),
    rgba(var(--spark-primary-rgb), 0.08)
  );
  color: var(--spark-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.char-info {
  flex: 1;
  min-width: 0;
}

.char-name {
  font-size: var(--spark-fs-base);
  font-weight: 500;
  color: var(--spark-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.char-desc {
  font-size: var(--spark-fs-xs);
  color: var(--spark-text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.char-arrow {
  color: var(--spark-text-muted);
  flex-shrink: 0;
}

.more-hint {
  text-align: center;
  padding: 12px;
  font-size: var(--spark-fs-sm);
  color: var(--spark-primary);
  cursor: pointer;
}

.action-buttons-row {
  display: flex;
  gap: 12px;
}

.action-buttons-row .action-btn {
  flex: 1;
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
