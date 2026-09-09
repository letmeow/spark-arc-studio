/**
 * useHomeLogic.ts - 聊天欢迎页（空态首页）数据逻辑
 *
 * 职责（仅数据，不做视图跳转）：
 * 1. 问候语（按时间段取 i18n）。
 * 2. 最近项目（projectStore.projects + last_project 缓存排序，取前 3）。
 * 3. 最近灵感草稿（scope=drafts，取前 3；语义见 aiContracts：project_links 为空即草稿）。
 * 4. 存灵感（createInspiration(source)，纯记一条草稿，不点燃、不建项目）。
 * 5. 取发送文本（beginSendToDirector 纯取文本；实际发送由 ChatDesktopIndex 经 chatStore 统一收口）。
 */

import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import bus from '@/eventBus';
import { useProjectStore } from '@/components/stores/projectStore';
import { useChatStore } from '@/components/stores/chatStore';
import { getInspirations, createInspiration } from '@/services/storyService';
import type { InspirationEntry } from '@/services/aiContracts';
import { getUserId } from '@/services/apiClient';

const LAST_PROJECT_KEY_PREFIX = 'sparkarc_last_project';

function getLastProjectKey(): string {
  try {
    const uid = getUserId();
    return uid ? `${LAST_PROJECT_KEY_PREFIX}:${uid}` : LAST_PROJECT_KEY_PREFIX;
  } catch {
    return LAST_PROJECT_KEY_PREFIX;
  }
}

export function useHomeLogic() {
  const { t } = useI18n();
  const projectStore = useProjectStore();
  const chatStore = useChatStore();

  const draft = ref('');
  const sending = ref(false);
  const savingMuse = ref(false);
  const loadingRecents = ref(false);
  const recentMuses = ref<InspirationEntry[]>([]);

  // ── 问候语 ──
  const greetWord = computed(() => {
    const h = new Date().getHours();
    if (h < 6) return t('views.home.greetNight');
    if (h < 12) return t('views.home.greetMorning');
    if (h < 14) return t('views.home.greetNoon');
    if (h < 18) return t('views.home.greetAfternoon');
    return t('views.home.greetEvening');
  });

  // ── 输入框占位符（无项目时提示先聊想法，有项目时提示发给导演） ──
  const placeholder = computed(() => {
    if (!projectStore.currentProject) return t('views.home.placeholderNoProject');
    return t('views.home.placeholderDefault');
  });

  // ── 最近项目：上次项目置顶，其余按原序，取前 3 ──
  const recentProjects = computed<string[]>(() => {
    const list = [...(projectStore.projects || [])];
    try {
      const last = localStorage.getItem(getLastProjectKey());
      if (last && list.includes(last)) {
        return [last, ...list.filter((p) => p !== last)].slice(0, 3);
      }
    } catch { /* 忽略存储异常 */ }
    return list.slice(0, 3);
  });

  function openProject(name: string) {
    if (!name || name === projectStore.currentProject) return;
    void projectStore.setCurrentProject(name);
  }

  // ── 最近灵感草稿：只取 drafts（未绑定任何项目），取前 3 ──
  async function refreshMuses() {
    loadingRecents.value = true;
    try {
      const result = await getInspirations({ scope: 'drafts' });
      const items = Array.isArray(result?.inspirations) ? result.inspirations : [];
      recentMuses.value = items.slice(0, 3);
    } catch {
      recentMuses.value = [];
    } finally {
      loadingRecents.value = false;
    }
  }

  // ── 存灵感：纯记一条草稿（createInspiration 默认即草稿，project_links 为空） ──
  async function saveAsMuse(): Promise<boolean> {
    const text = draft.value.trim();
    if (!text) {
      bus.emit('toast', { type: 'info', message: t('views.home.museEmptyHint') });
      return false;
    }
    savingMuse.value = true;
    try {
      await createInspiration(text);
      draft.value = '';
      await refreshMuses();
      bus.emit('toast', { type: 'success', message: t('views.home.museSavedHint') });
      return true;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e || '');
      bus.emit('toast', { type: 'error', message: msg || t('views.home.museSaveFailed') });
      return false;
    } finally {
      savingMuse.value = false;
    }
  }

  // ── 取发送文本（纯取文本，不做视图跳转；发送由 ChatDesktopIndex 经 chatStore 统一收口） ──
  function beginSendToDirector(): string | null {
    const text = draft.value.trim();
    if (!text || sending.value) return null;
    return text;
  }

  onMounted(() => {
    void refreshMuses();
  });

  return {
    draft,
    sending,
    savingMuse,
    loadingRecents,
    greetWord,
    placeholder,
    recentProjects,
    recentMuses,
    openProject,
    refreshMuses,
    saveAsMuse,
    beginSendToDirector,
    projectStore,
    chatStore,
  };
}
