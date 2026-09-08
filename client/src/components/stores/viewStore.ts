import { defineStore } from 'pinia';
import { ref } from 'vue';

export type AppViewKey =
  | 'home'
  | 'muse'
  | 'world'
  | 'characters'
  | 'lorebook'
  | 'synopsis'
  | 'structure'
  | 'production'
  | 'engine'
  | 'style'
  | 'blueprint'
  | 'player'
  | 'settings'
  | 'dashboard'
  | 'chat';

export const useViewStore = defineStore('view', () => {
  // 'home' | 'muse' | 'world' | 'synopsis' | 'structure' | 'production' | 'style' | 'blueprint' | 'settings' | 'dashboard' | 'chat'
  // 首页 Home 是应用内默认落地页：中央大输入框 + 最近项目/灵感，发送后过渡到 chat。
  const currentView = ref<AppViewKey>('home');
  const pendingChatAgentId = ref<string | null>(null);

  function setView(view: AppViewKey) {
    pendingChatAgentId.value = null;
    currentView.value = view;
  }

  function openChatView(agentId?: string | null) {
    pendingChatAgentId.value = agentId || null;
    currentView.value = 'chat';
  }

  function consumePendingChatAgentId() {
    const agentId = pendingChatAgentId.value;
    pendingChatAgentId.value = null;
    return agentId;
  }

  return {
    currentView,
    setView,
    openChatView,
    consumePendingChatAgentId
  };
});
