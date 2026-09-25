<template>
  <div class="relation-checker-mobile">
    <header class="relation-control-bar">
      <n-select
        v-model:value="selectedFilePath"
        :options="groupedStoryOptions"
        :placeholder="t('views.blueprint.mobileNew.selectFile')"
        size="medium"
        clearable
        @update:value="handleFileChange"
      />

      <div v-if="diagnostics.sceneCount" class="relation-health" :class="{ 'has-issues': diagnostics.issueCount > 0 }">
        <n-icon :component="diagnostics.issueCount > 0 ? TriangleAlert : ShieldCheck" size="18" />
        <div class="relation-health-copy">
          <strong>
            {{ diagnostics.issueCount > 0
              ? t('views.blueprint.mobileNew.healthIssues', { count: diagnostics.issueCount })
              : t('views.blueprint.mobileNew.healthClear') }}
          </strong>
          <span>
            {{ t('views.blueprint.mobileNew.relationSummary', {
              scenes: diagnostics.sceneCount,
              jumps: diagnostics.jumpCount,
            }) }}
          </span>
        </div>
        <span v-if="diagnostics.brokenJumpCount" class="health-count">
          {{ diagnostics.brokenJumpCount }}
        </span>
      </div>

      <SparkSegment
        v-if="diagnostics.sceneCount"
        v-model="filterMode"
        :options="filterOptions"
        size="small"
        block
      />
    </header>

    <main v-if="filteredDiagnostics.length" class="relation-list">
      <button
        v-for="item in filteredDiagnostics"
        :key="`${item.name}-${item.index}`"
        type="button"
        class="relation-row"
        @click="openDiagnostic(item)"
      >
        <span class="scene-order">{{ item.index + 1 }}</span>
        <span class="relation-row-copy">
          <span class="relation-row-title">
            {{ item.name || t('views.blueprint.mobileNew.untitledScene', { index: item.index + 1 }) }}
          </span>
          <span class="relation-row-meta">
            {{ t('views.blueprint.mobileNew.edgeSummary', {
              incoming: item.incoming.length,
              outgoing: item.jumps.length,
            }) }}
          </span>
        </span>
        <span class="relation-row-status">
          <SparkTag v-if="item.brokenTargets.length" type="danger" size="small">
            {{ t('views.blueprint.mobileNew.brokenJump') }}
          </SparkTag>
          <SparkTag v-else-if="item.duplicateName" type="warning" size="small">
            {{ t('views.blueprint.mobileNew.duplicateScene') }}
          </SparkTag>
          <SparkTag v-else-if="item.isolated" type="warning" size="small">
            {{ t('views.blueprint.mobileNew.isolatedScene') }}
          </SparkTag>
          <n-icon v-else :component="CircleCheck" size="17" class="status-ok" />
          <n-icon :component="ChevronRight" size="16" class="row-chevron" />
        </span>
      </button>
    </main>

    <div v-else class="relation-empty">
      <n-icon :component="filterMode === 'issues' ? ShieldCheck : GitBranch" size="38" />
      <strong>
        {{ filterMode === 'issues'
          ? t('views.blueprint.mobileNew.noIssues')
          : (selectedFilePath ? t('views.blueprint.mobileNew.noScenes') : t('views.blueprint.mobileNew.selectFileFirst')) }}
      </strong>
      <n-button
        v-if="filterMode === 'issues' && diagnostics.sceneCount"
        secondary
        size="small"
        @click="filterMode = 'all'"
      >
        {{ t('views.blueprint.mobileNew.showAll') }}
      </n-button>
      <n-button
        v-else-if="!diagnostics.sceneCount"
        type="primary"
        secondary
        size="small"
        @click="openSceneInProduction()"
      >
        {{ t('views.blueprint.mobileNew.goProduction') }}
      </n-button>
    </div>

    <n-drawer v-model:show="detailVisible" placement="bottom" height="72%" class="relation-detail-drawer">
      <n-drawer-content closable :native-scrollbar="false">
        <template #header>
          <span class="detail-drawer-title">
            {{ selectedDiagnostic?.name || t('views.blueprint.mobileNew.relationDetails') }}
          </span>
        </template>

        <div v-if="selectedDiagnostic" class="relation-detail">
          <div v-if="selectedDiagnostic.hasIssue" class="issue-strip">
            <n-icon :component="TriangleAlert" size="16" />
            <div class="issue-tags">
              <SparkTag v-if="selectedDiagnostic.brokenTargets.length" type="danger" size="small">
                {{ t('views.blueprint.mobileNew.brokenJumpCount', { count: selectedDiagnostic.brokenTargets.length }) }}
              </SparkTag>
              <SparkTag v-if="selectedDiagnostic.duplicateName" type="warning" size="small">
                {{ t('views.blueprint.mobileNew.duplicateScene') }}
              </SparkTag>
              <SparkTag v-if="selectedDiagnostic.isolated" type="warning" size="small">
                {{ t('views.blueprint.mobileNew.isolatedScene') }}
              </SparkTag>
            </div>
          </div>

          <section class="edge-section">
            <div class="edge-section-heading">
              <n-icon :component="CornerUpLeft" size="16" />
              <strong>{{ t('views.blueprint.mobileNew.incoming') }}</strong>
              <span>{{ selectedDiagnostic.incoming.length }}</span>
            </div>
            <div v-if="selectedDiagnostic.incoming.length" class="edge-list">
              <div v-for="source in selectedDiagnostic.incoming" :key="source" class="edge-row">
                <span>{{ source }}</span>
                <n-icon :component="ArrowRight" size="15" />
                <strong>{{ selectedDiagnostic.name }}</strong>
              </div>
            </div>
            <p v-else class="edge-empty">{{ t('views.blueprint.mobileNew.noIncoming') }}</p>
          </section>

          <section class="edge-section">
            <div class="edge-section-heading">
              <n-icon :component="CornerDownRight" size="16" />
              <strong>{{ t('views.blueprint.mobileNew.outgoing') }}</strong>
              <span>{{ selectedDiagnostic.jumps.length }}</span>
            </div>
            <div v-if="selectedDiagnostic.jumps.length" class="edge-list">
              <div
                v-for="jump in selectedDiagnostic.jumps"
                :key="`${jump.target}-${jump.type}`"
                class="edge-row"
                :class="{ 'is-broken': selectedDiagnostic.brokenTargets.includes(jump.target) }"
              >
                <n-icon :component="ArrowRight" size="15" />
                <strong>{{ jump.target }}</strong>
                <SparkTag :type="jump.type === 'option' ? 'success' : 'info'" size="small">
                  {{ jump.type === 'option' ? t('views.blueprint.mobileNew.optionJump') : t('views.blueprint.mobileNew.directJump') }}
                </SparkTag>
                <SparkTag v-if="selectedDiagnostic.brokenTargets.includes(jump.target)" type="danger" size="small">
                  {{ t('views.blueprint.mobileNew.targetMissing') }}
                </SparkTag>
              </div>
            </div>
            <p v-else class="edge-empty">{{ t('views.blueprint.mobileNew.noOutgoing') }}</p>
          </section>

          <n-button type="primary" block @click="openSceneInProduction(selectedDiagnostic.scene)">
            <template #icon><n-icon :component="SquarePen" /></template>
            {{ t('views.blueprint.mobileNew.openInWriting') }}
          </n-button>
        </div>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref, watch, type Ref } from 'vue';
import { NButton, NDrawer, NDrawerContent, NIcon, NSelect } from 'naive-ui';
import {
  ArrowRight,
  ChevronRight,
  CircleCheck,
  CornerDownRight,
  CornerUpLeft,
  GitBranch,
  ShieldCheck,
  SquarePen,
  TriangleAlert,
} from '@lucide/vue';
import { useI18n } from 'vue-i18n';
import { useSceneStore, type SceneWithClientId } from '../../components/stores/sceneStore';
import { useFileStore } from '../../components/stores/fileStore';
import { useViewStore } from '../../components/stores/viewStore';
import SparkSegment from '../../components/share/SparkSegment.vue';
import SparkTag from '../../components/share/SparkTag.vue';
import { useStoryFileOptions } from '../../composables/useStoryFileOptions';
import { MOBILE_FLOW_STEP, scrollToFlowStep } from '../../utils/mobileFlow';
import {
  buildRelationDiagnostics,
  type RelationDiagnostic,
  type RelationScene,
} from './relationDiagnostics';

const { t } = useI18n();
const sceneStore = useSceneStore();
const fileStore = useFileStore();
const viewStore = useViewStore();
const projectId = inject<Ref<string | null>>('projectId', ref<string | null>(null));

const selectedFilePath = ref('');
const filterMode = ref<'all' | 'issues'>('all');
const detailVisible = ref(false);
const selectedDiagnostic = ref<RelationDiagnostic | null>(null);

const scenes = computed<SceneWithClientId[]>(() => (
  Array.isArray(sceneStore.scriptData) ? sceneStore.scriptData : []
));
const diagnostics = computed(() => buildRelationDiagnostics(scenes.value as RelationScene[]));
const filteredDiagnostics = computed(() => (
  filterMode.value === 'issues'
    ? diagnostics.value.items.filter(item => item.hasIssue)
    : diagnostics.value.items
));
const filterOptions = computed(() => [
  { value: 'all' as const, label: t('views.blueprint.mobileNew.allScenes', { count: diagnostics.value.sceneCount }) },
  { value: 'issues' as const, label: t('views.blueprint.mobileNew.issuesOnly', { count: diagnostics.value.issueCount }) },
]);

const { flatOptions, groupedOptions } = useStoryFileOptions(() => t('views.production.mobile.rootFiles'));
const groupedStoryOptions = computed(() => groupedOptions.value);

async function handleFileChange(val: string | null) {
  detailVisible.value = false;
  selectedDiagnostic.value = null;
  if (!val || !projectId.value) return;
  await fileStore.setCurrentFile(projectId.value, val);
  selectedFilePath.value = val;
}

function openDiagnostic(item: RelationDiagnostic) {
  selectedDiagnostic.value = item;
  sceneStore.selectScene(item.scene as SceneWithClientId);
  detailVisible.value = true;
}

async function openSceneInProduction(scene?: RelationScene) {
  if (scene) sceneStore.selectScene(scene as SceneWithClientId);
  detailVisible.value = false;
  viewStore.setView('production');
  await nextTick();
  scrollToFlowStep(MOBILE_FLOW_STEP.production);
}

onMounted(async () => {
  if (!projectId.value) return;
  await fileStore.loadFileTree(projectId.value);
  if (fileStore.selectedFile?.path) {
    selectedFilePath.value = fileStore.selectedFile.path;
  } else if (flatOptions.value[0]?.value) {
    await handleFileChange(flatOptions.value[0].value);
  }
});

watch(projectId, async newId => {
  selectedFilePath.value = '';
  selectedDiagnostic.value = null;
  detailVisible.value = false;
  if (newId) await fileStore.loadFileTree(newId);
});

watch(() => fileStore.selectedFile?.path, path => {
  if (path) selectedFilePath.value = path;
});

watch(diagnostics, nextDiagnostics => {
  if (!selectedDiagnostic.value) return;
  const next = nextDiagnostics.items.find(item => (
    item.index === selectedDiagnostic.value?.index && item.name === selectedDiagnostic.value?.name
  ));
  selectedDiagnostic.value = next || null;
  if (!next) detailVisible.value = false;
});
</script>

<style scoped>
.relation-checker-mobile {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--spark-bg);
}

.relation-control-bar {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 2px 2px 10px;
  border-bottom: 1px solid var(--spark-border);
}

.relation-health {
  min-height: 48px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border-left: 3px solid var(--spark-success);
  background: color-mix(in srgb, var(--spark-success) 7%, var(--spark-panel-bg));
  color: var(--spark-success);
}

.relation-health.has-issues {
  border-left-color: var(--spark-warning);
  background: color-mix(in srgb, var(--spark-warning) 8%, var(--spark-panel-bg));
  color: var(--spark-warning);
}

.relation-health-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.relation-health-copy strong {
  color: var(--spark-text);
  font-size: var(--spark-fs-sm);
}

.relation-health-copy span {
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
}

.health-count {
  min-width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: color-mix(in srgb, currentColor 14%, transparent);
  font-size: var(--spark-fs-xs);
  font-weight: 700;
}

.relation-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 4px 0 10px;
}

.relation-row {
  width: 100%;
  min-height: 64px;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 9px 8px;
  border: 0;
  border-bottom: 1px solid color-mix(in srgb, var(--spark-border) 72%, transparent);
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
  -webkit-tap-highlight-color: transparent;
}

.relation-row:active {
  background: color-mix(in srgb, var(--spark-primary) 7%, transparent);
}

.scene-order {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--spark-primary-dim);
  color: var(--spark-primary);
  font-size: var(--spark-fs-xs);
  font-weight: 700;
}

.relation-row-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.relation-row-title,
.relation-row-meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.relation-row-title {
  color: var(--spark-text);
  font-size: var(--spark-fs-base);
  font-weight: 650;
}

.relation-row-meta {
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
}

.relation-row-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.status-ok { color: var(--spark-success); }
.row-chevron { color: var(--spark-text-muted); }

.relation-empty {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  color: var(--spark-text-muted);
  text-align: center;
}

.relation-empty strong {
  color: var(--spark-text-secondary);
  font-size: var(--spark-fs-base);
}

.detail-drawer-title {
  display: block;
  max-width: min(70vw, 360px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.relation-detail {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-bottom: calc(var(--sab, 0px) + 8px);
}

.issue-strip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 9px 10px;
  border-left: 3px solid var(--spark-warning);
  background: color-mix(in srgb, var(--spark-warning) 8%, transparent);
  color: var(--spark-warning);
}

.issue-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.edge-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.edge-section-heading {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 7px;
  color: var(--spark-text-secondary);
}

.edge-section-heading strong {
  font-size: var(--spark-fs-base);
}

.edge-section-heading span {
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-xs);
}

.edge-list {
  display: flex;
  flex-direction: column;
}

.edge-row {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 4px;
  border-bottom: 1px solid var(--spark-border);
  color: var(--spark-text-secondary);
  font-size: var(--spark-fs-sm);
}

.edge-row strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--spark-text);
}

.edge-row.is-broken {
  color: var(--spark-danger);
}

.edge-empty {
  margin: 0;
  padding: 9px 4px;
  color: var(--spark-text-muted);
  font-size: var(--spark-fs-sm);
}

:global(html.viewport-mobile .relation-detail-drawer .n-drawer-body-content-wrapper) {
  padding: 12px !important;
}
</style>
