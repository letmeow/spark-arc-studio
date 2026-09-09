import { defineStore } from 'pinia';
import { ref } from 'vue';

export type AppViewKey =
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
  // 'muse' | 'world' | 'synopsis' | 'structure' | 'production' | 'style' | 'blueprint' | 'settings' | 'dashboard' | 'chat'
  // 首页概念已删除：聊天页空态欢迎（ChatWelcomeScreen）即首页，无需独立视图与跳转。
  const currentView = ref<AppViewKey>('chat');
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
