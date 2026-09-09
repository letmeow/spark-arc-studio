<template>
  <div :class="wrapperClass" v-show="visible">
    <n-card 
      v-if="!compact"
      title="快速模型选择" 
      :segmented="{ content: true }" 
      :bordered="false"
      size="small"
    >
      <template #header-extra>
        <n-icon :component="Zap" size="20" />
      </template>

      <n-spin :show="loading">
        <n-form label-placement="top" size="small">
          <!-- 快速选择预设用途 -->
          <n-form-item label="选择用途">
            <n-select 
              v-model:value="selectedUsageKey" 
              placeholder="选择工作用途" 
              :options="usageOptions"
              @update:value="handleUsageChange"
            />
          </n-form-item>

          <n-divider style="margin: 12px 0;">或直接选择模型</n-divider>

          <!-- 直接选择具体模型 -->
          <n-form-item label="选择平台">
            <n-select 
              v-model:value="selectedPlatformId" 
              placeholder="选择 AI 平台" 
              :options="platformOptions"
              filterable
              @update:value="handlePlatformChange"
            />
          </n-form-item>

          <n-form-item label="选择模型">
            <n-select 
              v-model:value="selectedModelId" 
              placeholder="选择模型" 
              :options="modelOptions"
              filterable
              :disabled="!selectedPlatformId"
              @update:value="handleModelChange"
            />
          </n-form-item>

          <SparkAlert type="info" style="margin-top: 8px;">
            当前修改将应用于「{{ getUsageKeyDisplayName(selectedUsageKey, aiStore.usageSelections) }}」
          </SparkAlert>
        </n-form>
      </n-spin>
    </n-card>

    <!-- 紧凑模式：用于各个视图的头部 / 工具栏 / 聊天输入栏，只保留一行选择 -->
    <div v-else class="compact-wrapper">
      <n-popover trigger="click" :placement="placement" :show-arrow="false" style="padding: 0;">
        <template #trigger>
          <slot name="trigger" :current-model-name="currentModelName">
            <!-- 图标按钮模式：专用于聊天输入栏等紧凑场景 -->
            <n-tooltip v-if="trigger === 'icon'" trigger="hover">
              <template #trigger>
                <n-button
                  size="small"
                  quaternary
                  circle
                  class="model-config-icon-btn"
                  :aria-label="computedTooltipText"
                >
                  <template #icon>
                    <n-icon :component="Cpu" :size="16" />
                  </template>
                </n-button>
              </template>
              {{ computedTooltipText }}
            </n-tooltip>

            <!-- 默认按钮模式：用于各页面顶部工具栏 -->
            <n-button v-else-if="trigger !== 'pill'" size="small" quaternary class="model-selector-btn">
              <template #icon>
                <n-icon :component="Zap" />
              </template>
              {{ currentModelName || '选择模型' }}
            </n-button>

            <!-- 模型名 pill：专用于聊天输入框气泡内，显示当前模型名，点击弹出同一张卡片 -->
            <button v-else type="button" class="model-name-pill" :title="computedTooltipText">
              <span class="model-name-pill-dot"></span>
              <span class="model-name-pill-text">{{ currentModelName || '选择模型' }}</span>
            </button>
          </slot>
        </template>

        <div class="compact-popover-content">
          <div class="compact-popover-head">
            <div class="compact-popover-title">快速模型选择</div>
            <span class="compact-popover-current">{{ currentModelName || '未指定模型' }}</span>
          </div>

          <n-tabs
            type="segment"
            animated
            :value="compactMode"
            @update:value="handleCompactModeChange"
            size="small"
            class="spark-segment-tabs"
          >
            <n-tab-pane name="usage" tab="按用途">
              <div class="compact-pane compact-pane--single">
                <div class="compact-field">
                  <span class="compact-field-label">用途</span>
                  <n-select
                    v-model:value="selectedUsageKey"
                    :options="usageOptions"
                    placeholder="选择用途"
                    @update:value="handleUsageChange"
                  />
                </div>
              </div>
            </n-tab-pane>

            <n-tab-pane name="direct" tab="直接指定">
              <div class="compact-pane compact-pane--double">
                <div class="compact-field">
                  <n-select
                    v-model:value="selectedPlatformId"
                    :options="platformOptions"
                    placeholder="选择平台"
                    filterable
                    @update:value="handlePlatformChange"
                  />
                </div>
                <div class="compact-field">
                  <n-select
                    v-model:value="selectedModelId"
                    :options="modelOptions"
                    :disabled="!selectedPlatformId"
                    placeholder="选择模型"
                    filterable
                    @update:value="handleModelChange"
                  />
                </div>
              </div>
            </n-tab-pane>
          </n-tabs>
        </div>
      </n-popover>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick, type PropType } from 'vue';
import { NCard, NForm, NFormItem, NSelect, NIcon, NDivider, NSpin, useMessage, NPopover, NButton, NTabs, NTabPane, NTooltip } from 'naive-ui';
import SparkAlert from '@/components/share/SparkAlert.vue';
import { Info, Zap, Cpu } from '@lucide/vue';
import { useAiStore } from '@/components/stores/aiStore';
import { useUsageDisplay } from '@/composables/useUsageDisplay';
import { fetchAgentUsageBindings, getDefaultAgentUsageKey, saveAgentBinding } from '@/services/agentUsage';
import bus from '@/eventBus';

const props = defineProps({ 
  visible: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  agentName: { type: String, default: null },
  placement: { type: String as PropType<any>, default: 'bottom-start' },
  trigger: { type: String as PropType<'button' | 'icon' | 'pill'>, default: 'button' },
  tooltipText: { type: String, default: '' },
});
const message = useMessage();
const aiStore = useAiStore();
const { getUsageDisplayLabel, getUsageKeyDisplayName } = useUsageDisplay();

function toStoreId(value: unknown): string | null {
  if (value == null || value === '') return null;
  return String(value);
}

// 数据
const selectedUsageKey = ref(getDefaultAgentUsageKey(props.agentName));
const selectedPlatformId = ref<string | null>(null);
const selectedModelId = ref<string | null>(null);

// 状态
const loading = computed(() => aiStore.loading);
let internalUpdate = false; // 避免 watch 循环触发
const isDirectBinding = ref(false);
const panelId = `ai-settings-${Math.random().toString(36).slice(2, 10)}`;

// Usage options (presets)
const usageOptions = computed(() => 
  aiStore.usageSelections.map(u => ({
    label: getUsageDisplayLabel(u),
    value: u.usage_key
  }))
);

// Platform options
const platformOptions = computed(() => aiStore.languageModelPlatformOptions);

// Model options for selected platform
const modelOptions = computed(() => aiStore.getLanguageModelsForPlatform(selectedPlatformId.value));

const currentModelName = computed(() => {
  if (!selectedModelId.value) return '';
  const m = aiStore.allModels.find(x => x.model_id === selectedModelId.value);
  return m ? (m.display_name || m.model_name) : '';
});

const computedTooltipText = computed(() => {
  if (props.tooltipText) return props.tooltipText;
  const name = currentModelName.value || '未指定模型';
  return `配置模型 (当前: ${name})`;
});

const wrapperClass = computed(() =>
  props.compact ? 'right-panel-section compact-mode' : 'right-panel-section'
);

// Compact mode tab state
const compactMode = ref('usage'); // 'usage' or 'direct'

function getResolvedUsageKey(usageKey = selectedUsageKey.value) {
  const hasUsage = aiStore.usageSelections.some(u => u.usage_key === usageKey);
  if (hasUsage) return usageKey;
  return aiStore.usageSelections[0]?.usage_key ?? getDefaultAgentUsageKey(props.agentName);
}

async function applyUsageSelection(usageKey = selectedUsageKey.value) {
  const resolvedUsageKey = getResolvedUsageKey(usageKey);
  const usage = aiStore.usageSelections.find(u => u.usage_key === resolvedUsageKey);
  if (!usage) return null;

  internalUpdate = true;
  selectedUsageKey.value = resolvedUsageKey;
  selectedPlatformId.value = usage.platform_id ?? null;
  selectedModelId.value = usage.model_id ?? null;
  await nextTick();
  internalUpdate = false;
  return usage;
}

async function handleCompactModeChange(mode) {
  if (compactMode.value === mode) return;

  const previousState = {
    compactMode: compactMode.value,
    isDirectBinding: isDirectBinding.value,
    selectedUsageKey: selectedUsageKey.value,
    selectedPlatformId: selectedPlatformId.value,
    selectedModelId: selectedModelId.value,
  };

  compactMode.value = mode;

  try {
    if (mode === 'usage') {
      const usage = await applyUsageSelection(selectedUsageKey.value);
      if (!usage) {
        throw new Error('当前没有可用用途');
      }

      if (props.agentName) {
        await saveAgentUsageBinding(selectedUsageKey.value, {
          silentSuccess: true,
          rethrow: true,
        });
      } else if (props.compact) {
        await saveToUsage('main', usage.platform_id, usage.model_id, {
          silentSuccess: true,
          rethrow: true,
        });
      }
      return;
    }

    if (!selectedPlatformId.value || !selectedModelId.value) {
      await applyUsageSelection(selectedUsageKey.value);
    }

    if (selectedPlatformId.value && selectedModelId.value) {
      if (props.agentName) {
        await saveAgentDirectBinding(selectedPlatformId.value, selectedModelId.value, {
          silentSuccess: true,
          rethrow: true,
        });
      } else if (props.compact) {
        await saveToUsage('main', selectedPlatformId.value, selectedModelId.value, {
          silentSuccess: true,
          rethrow: true,
        });
      }
    }
  } catch (err: unknown) {
    const caughtError = toCaughtError(err);
    internalUpdate = true;
    compactMode.value = previousState.compactMode;
    isDirectBinding.value = previousState.isDirectBinding;
    selectedUsageKey.value = previousState.selectedUsageKey;
    selectedPlatformId.value = previousState.selectedPlatformId;
    selectedModelId.value = previousState.selectedModelId;
    await nextTick();
    internalUpdate = false;

    if (!caughtError.__shownToUser) {
      message.error(caughtError.message || '切换模型模式失败');
    }
  }
}

async function syncSelectionFromStore() {
  if (isDirectBinding.value) return;
  await applyUsageSelection(selectedUsageKey.value);
}

async function loadAgentBinding() {
  if (!props.agentName) return;
  try {
    const bindings = await fetchAgentUsageBindings();
    const binding = bindings?.[props.agentName];

    if (binding && typeof binding === 'object' && binding.direct) {
      isDirectBinding.value = true;
      compactMode.value = 'direct';
      selectedPlatformId.value = toStoreId(binding.direct.platform_id);
      selectedModelId.value = toStoreId(binding.direct.model_id);
      return;
    }

    // 默认使用用途绑定
    isDirectBinding.value = false;
    compactMode.value = 'usage';
    selectedUsageKey.value = getResolvedUsageKey(
      typeof binding === 'string' && binding ? binding : getDefaultAgentUsageKey(props.agentName),
    );
    await syncSelectionFromStore();
  } catch (err: unknown) {
    // 绑定加载失败时回退到该 Agent 的默认用途
    isDirectBinding.value = false;
    selectedUsageKey.value = getResolvedUsageKey(getDefaultAgentUsageKey(props.agentName));
    await syncSelectionFromStore();
  }
}

function notifyAgentBindingChanged() {
  if (!props.agentName) return;
  bus.emit('agent-binding-changed', {
    agentName: props.agentName,
    sourceId: panelId
  });
}

async function loadData() {
  await aiStore.loadData();
  if (props.agentName) {
    await loadAgentBinding();
  } else {
    await syncSelectionFromStore();
  }
}

// Handle usage preset selection
async function handleUsageChange(usageKey) {
  if (internalUpdate) return;

  const resolvedUsageKey = getResolvedUsageKey(usageKey);
  const usage = aiStore.usageSelections.find(u => u.usage_key === resolvedUsageKey);
  if (!usage) return;

  if (props.agentName) {
    await saveAgentUsageBinding(resolvedUsageKey);
    return;
  }

  await applyUsageSelection(resolvedUsageKey);
  
  // In compact mode with usage tab, update the main usage to match selected usage
  if (props.compact && compactMode.value === 'usage') {
    await saveToUsage('main', usage.platform_id, usage.model_id);
  }
}

// Handle direct platform selection
async function handlePlatformChange(platformId) {
  selectedPlatformId.value = toStoreId(platformId);
  const models = aiStore.getLanguageModelsForPlatform(platformId);
  
  if (models && models.length > 0) {
      selectedModelId.value = toStoreId(models[0].value);
      if (props.agentName) {
        await saveAgentDirectBinding(selectedPlatformId.value, selectedModelId.value);
      } else {
        const targetUsage = props.compact && compactMode.value === 'direct' ? 'main' : selectedUsageKey.value;
        await saveToUsage(targetUsage, selectedPlatformId.value, selectedModelId.value);
      }
  } else {
    selectedModelId.value = null;
  }
}

// Handle direct model selection
async function handleModelChange(modelId) {
  if (internalUpdate) return;
  selectedModelId.value = toStoreId(modelId);
  
  if (props.agentName) {
    await saveAgentDirectBinding(selectedPlatformId.value, selectedModelId.value);
  } else {
    // In compact mode with direct tab, save to main usage
    const targetUsage = props.compact && compactMode.value === 'direct' ? 'main' : selectedUsageKey.value;
    await saveToUsage(targetUsage, selectedPlatformId.value, selectedModelId.value);
  }
}

type SaveOptions = {
  silentSuccess?: boolean;
  rethrow?: boolean;
};

type CaughtError = Error & { __shownToUser?: boolean };

function toCaughtError(err: unknown): CaughtError {
  if (err instanceof Error) return err as CaughtError;
  return new Error(String(err || '未知错误')) as CaughtError;
}

async function saveAgentUsageBinding(usageKey, options: SaveOptions = {}) {
  const { silentSuccess = false, rethrow = false } = options;

  try {
    const resolvedUsageKey = getResolvedUsageKey(usageKey);
    await saveAgentBinding(props.agentName, resolvedUsageKey);
    isDirectBinding.value = false;
    selectedUsageKey.value = resolvedUsageKey;
    await syncSelectionFromStore();
    if (!silentSuccess) {
      message.success('已更新当前页面所用 Agent 设置');
    }
    notifyAgentBindingChanged();
    return true;
  } catch (err: unknown) {
    const caughtError = toCaughtError(err);
    caughtError.__shownToUser = true;
    message.error('保存失败: ' + caughtError.message);
    if (rethrow) throw caughtError;
    return false;
  }
}

async function saveAgentDirectBinding(platformId, modelId, options: SaveOptions = {}) {
  const { silentSuccess = false, rethrow = false } = options;

  try {
    const resolvedPlatformId = toStoreId(platformId);
    const resolvedModelId = toStoreId(modelId);
    if (!resolvedPlatformId || !resolvedModelId) {
      throw new Error('缺少平台或模型');
    }
    await saveAgentBinding(props.agentName, {
      binding: props.agentName,
      direct: { platform_id: resolvedPlatformId, model_id: resolvedModelId }
    });
    selectedPlatformId.value = resolvedPlatformId;
    selectedModelId.value = resolvedModelId;
    isDirectBinding.value = true;
    if (!silentSuccess) {
      message.success('已更新当前页面所用 Agent 设置');
    }
    notifyAgentBindingChanged();
    return true;
  } catch (err: unknown) {
    const caughtError = toCaughtError(err);
    caughtError.__shownToUser = true;
    message.error('保存失败: ' + caughtError.message);
    if (rethrow) throw caughtError;
    return false;
  }
}

// Save selection to specific usage
async function saveToUsage(usageKey, platformId, modelId, options: SaveOptions = {}) {
  const { silentSuccess = false, rethrow = false } = options;

  try {
    const resolvedPlatformId = toStoreId(platformId);
    const resolvedModelId = toStoreId(modelId);
    if (!resolvedPlatformId || !resolvedModelId) {
      throw new Error('缺少平台或模型');
    }
    await aiStore.updateSelection(usageKey, resolvedPlatformId, resolvedModelId);
    if (!silentSuccess) {
      message.success(`已更新 ${getUsageKeyDisplayName(usageKey, aiStore.usageSelections)} 设置`);
    }
    return true;
  } catch (err: unknown) {
    const caughtError = toCaughtError(err);
    caughtError.__shownToUser = true;
    message.error('保存失败: ' + caughtError.message);
    if (rethrow) throw caughtError;
    return false;
  }
}

// Watch for store changes to stay in sync
watch(() => aiStore.usageSelections, async () => {
  if (!internalUpdate && !isDirectBinding.value) {
    await syncSelectionFromStore();
  }
}, { deep: true });

watch(() => props.visible, (v) => {
  if (v && (props.agentName || aiStore.usageSelections.length === 0)) {
    loadData();
  }
}, { immediate: true });

watch(() => props.agentName, () => {
  if (props.visible) {
    loadData();
  }
});

onMounted(() => {
  if (props.visible) {
    loadData();
  }
  bus.on('agent-binding-changed', handleAgentBindingChanged);
});

function handleAgentBindingChanged(payload) {
  if (!props.agentName) return;
  if (!payload || payload.agentName !== props.agentName) return;
  if (payload.sourceId === panelId) return;
  if (props.visible) {
    loadData();
  }
}

onBeforeUnmount(() => {
  bus.off('agent-binding-changed', handleAgentBindingChanged);
});
</script>

<style scoped>
.right-panel-section {
  padding: 0;
}

.right-panel-section.compact-mode {
  padding: 0;
}

.compact-wrapper {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  height: 100%;
}

.compact-popover-content {
  width: 420px;
  background: var(--spark-panel-bg);
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius);
  box-shadow: var(--spark-shadow);
  overflow: hidden;
}

.compact-popover-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--spark-border);
  background: color-mix(in srgb, var(--spark-panel-bg), var(--spark-primary) 5%);
}

.compact-popover-title {
  font-size: var(--spark-fs-sm);
  font-weight: 700;
  color: var(--spark-primary);
}

.compact-popover-current {
  min-width: 0;
  max-width: 220px;
  font-size: var(--spark-fs-2xs);
  color: var(--spark-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: right;
}

.compact-popover-content :deep(.n-tabs-nav) {
  padding: 10px 12px 0;
}

.compact-popover-content :deep(.n-tabs-rail) {
  margin: 10px 12px 0;
}

.compact-popover-content :deep(.n-tabs-pane-wrapper) {
  padding: 0;
}

.compact-popover-content :deep(.n-tab-pane) {
  padding: 0;
}

.compact-pane {
  display: grid;
  gap: 10px;
  padding: 10px 12px 12px;
}

.compact-pane--single {
  grid-template-columns: 1fr;
}

.compact-pane--double {
  grid-template-columns: 1fr;
}

.compact-field {
  min-width: 0;
}

.compact-field-label {
  display: inline-flex;
  align-items: center;
  margin-bottom: 6px;
  font-size: var(--spark-fs-2xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--spark-text-muted);
}

.compact-field :deep(.n-base-selection) {
  width: 100%;
  min-height: 30px;
  font-size: var(--spark-fs-xs);
}

.compact-field :deep(.n-base-selection-label) {
  height: 30px;
  line-height: 30px;
}

.model-selector-btn {
  font-size: var(--spark-fs-sm);
  height: 24px;
  line-height: 22px;
  padding: 0 10px;
  border-radius: var(--spark-radius);
  transition: background 0.15s ease, border-color 0.15s ease;
  background: var(--spark-panel-bg);
  border: 1px solid transparent;
  margin-left: 0;
  margin-top: 3px;
  user-select: none;
  cursor: pointer;
  vertical-align: middle;
}

.model-selector-btn:hover {
  background: var(--spark-primary-glow);
  border-color: var(--spark-border-hover);
}

.model-config-icon-btn {
  width: 28px;
  height: 28px;
  color: var(--spark-primary);
  transition: color 0.15s ease, background 0.15s ease, opacity 0.15s ease;
  opacity: 0.9;
}

.model-config-icon-btn:hover {
  color: var(--spark-primary);
  background: var(--spark-primary-glow);
  opacity: 1;
}

/* 模型名 pill：聊天输入框气泡内右下角，扁平无浮雕，与主题同色系 */
.model-name-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 180px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.model-name-pill:hover {
  border-color: var(--spark-border);
  color: var(--spark-text);
}

.model-name-pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--spark-text-muted);
  opacity: 0.6;
}

.model-name-pill-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 640px) {
  .compact-popover-content {
    width: min(420px, calc(100vw - 24px));
  }

  .compact-pane--double {
    grid-template-columns: 1fr;
  }
}
</style>
