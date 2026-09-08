/**
 * useHomeLogic.ts - 创作首页数据逻辑
 *
 * 职责（仅数据，不含动画与发送过渡）：
 * 1. 问候语（按时间段取 i18n）与当前用户名。
 * 2. 最近项目（projectStore.projects + last_project 缓存排序，取前 3）。
 * 3. 最近灵感草稿（scope=drafts，取前 3；语义见 aiContracts：project_links 为空即草稿）。
 * 4. 存灵感（createInspiration(source)，纯记一条草稿，不点燃、不建项目）。
 * 5. 建议片（按 A 无项目 / B 有项目 / C 回访 三态取 i18n）。
 * 6. 发送给导演：切到 chat 视图，由 ChatDesktop 走统一 chatStore.send（本文件只做视图切换与过渡标记）。
 *
 * 发送过渡动画见 components/home/HomeSendTransition.ts。
 */

import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import bus from '@/eventBus';
import { useViewStore } from '@/components/stores/viewStore';
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

export type HomeState = 'A' | 'B' | 'C';

export function useHomeLogic() {
  const { t } = useI18n();
  const viewStore = useViewStore();
  const projectStore = useProjectStore();
  const chatStore = useChatStore();

  const draft = ref('');
  const sending = ref(false);
  const savingMuse = ref(false);
  const loadingRecents = ref(false);
  const recentMuses = ref<InspirationEntry[]>([]);
  const museTotal = ref(0);

  // ── 问候语 ──
  const greetWord = computed(() => {
    const h = new Date().getHours();
    if (h < 6) return t('views.home.greetNight');
    if (h < 12) return t('views.home.greetMorning');
    if (h < 14) return t('views.home.greetNoon');
    if (h < 18) return t('views.home.greetAfternoon');
    return t('views.home.greetEvening');
  });

  // ── 三态：A 无项目 / B 有项目 / C 回访（有历史） ──
  const homeState = computed<HomeState>(() => {
    if (!projectStore.currentProject) return 'A';
    if ((chatStore.history || []).length > 0) return 'C';
    return 'B';
  });

  // ── 输入框占位符（上下文感知） ──
  const placeholder = computed(() => {
    if (homeState.value === 'A') return t('views.home.placeholderNoProject');
    if (homeState.value === 'C') return t('views.home.placeholderResume');
    return t('views.home.placeholderDefault');
  });

  // ── 建议片（单击填入输入框，不直接发送） ──
  const chips = computed<string[]>(() => {
    if (homeState.value === 'A') return [t('views.home.chipA1'), t('views.home.chipA2'), t('views.home.chipA3')];
    if (homeState.value === 'C') return [t('views.home.chipC1'), t('views.home.chipC2'), t('views.home.chipC3')];
    return [t('views.home.chipB1'), t('views.home.chipB2'), t('views.home.chipB3')];
  });

  function fillChip(text: string) {
    draft.value = `${text}：`;
  }

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
      museTotal.value = items.length;
      recentMuses.value = items.slice(0, 3);
    } catch {
      recentMuses.value = [];
      museTotal.value = 0;
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

  // ── 发送给导演：只做视图切换 + 过渡标记，实际发送由调用方在 chat 页触发 ──
  // 返回发送文本，调用方（HomeDesktop）负责 FLIP 动画后再 setView + send。
  function beginSendToDirector(): string | null {
    const text = draft.value.trim();
    if (!text || sending.value) return null;
    return text;
  }

  function commitSendToDirector(text: string) {
    draft.value = '';
    viewStore.openChatView('agent_director');
    // 聊天页挂载后由 HomeSendTransition 完成 draft 预填与自动发送（见 HomeDesktop）。
    bus.emit('home-send-to-director', { text });
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
    homeState,
    placeholder,
    chips,
    recentProjects,
    recentMuses,
    museTotal,
    fillChip,
    openProject,
    refreshMuses,
    saveAsMuse,
    beginSendToDirector,
    commitSendToDirector,
    viewStore,
    projectStore,
    chatStore,
  };
}
