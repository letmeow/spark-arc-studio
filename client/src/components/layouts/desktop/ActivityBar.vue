<template>
  <div class="activity-bar">
    <transition-group name="list" tag="div" class="activity-list">
      <div v-for="item in sortedItems" :key="item.id" class="activity-transition-item">
        <n-tooltip trigger="hover" placement="right">
          <template #trigger>
            <div
              class="activity-item"
              :class="{
                active: viewStore.currentView === item.view,
                dragging: draggingId === item.id
              }"
              :data-view="item.view"
              @click="viewStore.setView(item.view)"
              draggable="true"
              @dragstart="onDragStart($event, item)"
              @dragenter.prevent="onDragEnter(item)"
              @dragover.prevent
              @dragend="onDragEnd"
            >
              <n-icon size="24">
                <component :is="item.icon" />
              </n-icon>
            </div>
          </template>
          {{ item.title }}
        </n-tooltip>
      </div>
    </transition-group>

    <div style="flex: 1"></div>

    <!-- 创作工具与系统功能的视觉分隔 -->
    <div class="activity-separator"></div>

    <n-tooltip trigger="hover" placement="right">
      <template #trigger>
        <div
          class="activity-item"
          :class="{ active: viewStore.currentView === 'dashboard' }"
          data-view="dashboard"
          @click="viewStore.setView('dashboard')"
        >
          <n-icon size="24">
            <Gauge />
          </n-icon>
        </div>
      </template>
      {{ t('activityBar.dashboard') }}
    </n-tooltip>

    <n-tooltip trigger="hover" placement="right">
      <template #trigger>
        <div
          class="activity-item"
          :class="{ active: viewStore.currentView === 'settings' }"
          data-view="settings"
          @click="viewStore.setView('settings')"
        >
          <n-icon size="24">
            <Settings />
          </n-icon>
        </div>
      </template>
      {{ t('activityBar.settings') }}
    </n-tooltip>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, markRaw, watch } from 'vue';
import { NIcon, NTooltip } from 'naive-ui';
import { useI18n } from 'vue-i18n';
import { Activity, Code, Gauge, Globe2, Library, List, Map as MapIcon, MessagesSquare, Settings, SquarePen, UsersRound } from '@lucide/vue';
import { useViewStore, type AppViewKey } from '../../stores/viewStore';

type ActivityItem = {
  id: string;
  view: AppViewKey;
  title: string;
  icon: unknown;
};

defineProps({});

const viewStore = useViewStore();
const { t, locale } = useI18n();

defineEmits(['open-settings']);

// 统一图标配置 - 双端共用
function buildDefaultItems(): ActivityItem[] {
  return [
    { id: 'chat', view: 'chat', title: t('activityBar.chat'), icon: markRaw(MessagesSquare) },
    { id: 'world', view: 'world', title: t('activityBar.world'), icon: markRaw(Globe2) },
    { id: 'characters', view: 'characters', title: t('activityBar.characters'), icon: markRaw(UsersRound) },
    { id: 'synopsis', view: 'synopsis', title: t('activityBar.synopsis'), icon: markRaw(Activity) },
    { id: 'structure', view: 'structure', title: t('activityBar.structure'), icon: markRaw(List) },
    { id: 'production', view: 'production', title: t('activityBar.production'), icon: markRaw(SquarePen) },
    { id: 'blueprint', view: 'blueprint', title: t('activityBar.blueprint'), icon: markRaw(MapIcon) },
    { id: 'style', view: 'style', title: t('activityBar.style'), icon: markRaw(Library) },
    { id: 'engine', view: 'engine', title: t('activityBar.engine'), icon: markRaw(Code) }
  ];
}

const items = ref<ActivityItem[]>(loadInitialItems());
const draggingId = ref<string | null>(null);

function loadInitialItems(): ActivityItem[] {
  const defaultItems = buildDefaultItems();
  try {
    const savedOrder = localStorage.getItem('activityBarOrder');
    if (savedOrder) {
      const orderIds = JSON.parse(savedOrder) as string[];
      // Sort items based on saved order, append any new items at the end
      const ordered: ActivityItem[] = [];
      const remaining: ActivityItem[] = [...defaultItems];
      
      orderIds.forEach(id => {
        const idx = remaining.findIndex(item => item.id === id);
        if (idx !== -1) {
          ordered.push(remaining[idx]);
          remaining.splice(idx, 1);
        }
      });
      
      return [...ordered, ...remaining];
    }
  } catch (e: unknown) {
    console.error('Failed to load activity bar order', e);
  }
  return [...defaultItems];
}

function refreshItemTitles(): void {
  const latest = buildDefaultItems();
  const titleMap = new Map(latest.map(item => [item.id, item.title]));
  items.value = items.value.map(item => ({
    ...item,
    title: titleMap.get(item.id) || item.title,
  }));
}

const sortedItems = computed(() => items.value);

// Drag and Drop Logic
let lastSwapTime = 0;
let lastSwappedId: string | null = null;

function onDragStart(event: DragEvent, item: ActivityItem) {
  draggingId.value = item.id;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
  }
}

function onDragEnter(targetItem: ActivityItem) {
  if (!draggingId.value || draggingId.value === targetItem.id) return;

  // Prevent flickering: Don't swap with the same item we just swapped with immediately
  // This prevents the item animating under the cursor from triggering a reverse swap
  if (targetItem.id === lastSwappedId && Date.now() - lastSwapTime < 300) {
    return;
  }

  const sourceIndex = items.value.findIndex(i => i.id === draggingId.value);
  const targetIndex = items.value.findIndex(i => i.id === targetItem.id);

  if (sourceIndex !== -1 && targetIndex !== -1) {
    const newItems = [...items.value];
    const [movedItem] = newItems.splice(sourceIndex, 1);
    newItems.splice(targetIndex, 0, movedItem);
    items.value = newItems;
    
    lastSwapTime = Date.now();
    lastSwappedId = targetItem.id;
  }
}

function onDragEnd() {
  draggingId.value = null;
  localStorage.setItem('activityBarOrder', JSON.stringify(items.value.map(i => i.id)));
}

watch(locale, () => {
  refreshItemTitles();
});
</script>

<style scoped>
/* Styles are handled globally in studio.css for .activity-bar and .activity-item */

.activity-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}

.activity-transition-item {
  display: flex;
  justify-content: center;
  width: 100%;
}

.list-move {
  transition: transform 0.3s cubic-bezier(0.25, 0.8, 0.5, 1);
}

.activity-item.dragging {
  opacity: 0.5;
  background: var(--n-color-hover);
}

.activity-separator {
  width: 32px;
  height: 1px;
  background: var(--spark-border);
  margin: 4px 0 12px;
  opacity: 0.6;
}
</style>
