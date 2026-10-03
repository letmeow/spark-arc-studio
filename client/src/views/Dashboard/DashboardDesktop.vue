<template>
  <div class="view-container">
    <div class="panel-header spark-desktop-header">
      <div class="spark-desktop-header__left">
        <div class="spark-desktop-header__title-row">
          <h2 class="spark-desktop-title">{{ t('views.dashboard.desktop.title') }}</h2>
          <OnboardingHelpButton scene-id="page-dashboard" />
        </div>
        <p class="spark-desktop-subtitle">{{ t('views.dashboard.desktop.subtitle') }}</p>
      </div>
      <div class="header-actions spark-desktop-header__actions">
        <SparkTag v-if="isAdmin" type="success" size="small">{{ t('views.dashboard.desktop.adminTag') }}</SparkTag>
        <n-button quaternary size="small" @click="showPasswordModal = true">
          <template #icon>
            <n-icon><Key /></n-icon>
          </template>
          {{ t('views.dashboard.desktop.changePassword') }}
        </n-button>
        <n-button quaternary size="small" @click="refreshData">
          <template #icon>
            <n-icon><RefreshCw /></n-icon>
          </template>
          {{ t('views.common.refresh') }}
        </n-button>
      </div>
    </div>

    <div class="content-area">
      <n-spin :show="loading">
        <div class="admin-container">
          <div class="admin-column">
            <n-card :title="t('views.dashboard.desktop.myUsageStats')" size="small">
              <template #header-extra>
                <n-space :size="6" align="center">
                  <SparkSegment
                    :model-value="usageRange"
                    :options="usageRangeOptions"
                    size="tiny"
                    @update:model-value="handleUsageRangeChange"
                  />
                  <n-tooltip trigger="hover">
                    <template #trigger>
                      <n-button circle quaternary size="tiny" @click="fetchMyUsageOnly()">
                        <template #icon><n-icon><RefreshCw /></n-icon></template>
                      </n-button>
                    </template>
                    {{ t('views.dashboard.desktop.refreshStats') }}
                  </n-tooltip>
                </n-space>
              </template>

              <n-space vertical>
                <n-statistic :label="usageRangeLabel">
                  {{ formatTokenWithCredit(myUsage?.range_stats?.tokens || 0, myUsage?.range_stats?.credit_cost || 0) }}<n-tooltip trigger="hover"><template #trigger><SparkIcon style="cursor:help" /></template>{{ t('views.dashboard.desktop.creditIconHint') }}</n-tooltip>
                </n-statistic>

                <n-grid :cols="2" :x-gap="12">
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.requestCount')" tabular-nums>
                      {{ myUsage?.range_stats?.requests || 0 }}
                    </n-statistic>
                  </n-gi>
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.errorCount')" tabular-nums>
                      <n-text :type="(myUsage?.range_stats?.errors || 0) > 0 ? 'error' : 'default'">
                        {{ myUsage?.range_stats?.errors || 0 }}
                      </n-text>
                    </n-statistic>
                  </n-gi>
                </n-grid>

                <n-grid :cols="3" :x-gap="12">
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.systemCreditBalance')" tabular-nums>
                      {{ formatCreditExact(myCreditStatus?.credit_balance || 0) }}<n-tooltip trigger="hover"><template #trigger><SparkIcon style="cursor:help" /></template>{{ t('views.dashboard.desktop.creditIconHint') }}</n-tooltip>
                    </n-statistic>
                  </n-gi>
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.totalGrantedCredit')" tabular-nums>
                      {{ formatCreditExact(myCreditStatus?.credit_total_granted || 0) }}
                    </n-statistic>
                  </n-gi>
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.systemPaidRequests')" tabular-nums>
                      {{ myCreditStatus?.requests || 0 }}
                    </n-statistic>
                  </n-gi>
                </n-grid>

                <n-grid :cols="2" :x-gap="12">
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.systemPaid')" tabular-nums>
                      {{ formatTokenWithCredit(myQuotaStatus?.sys_paid?.total?.usage?.tokens || 0, myCreditStatus?.credit_used_from_usage || 0) }}<n-tooltip trigger="hover"><template #trigger><SparkIcon style="cursor:help" /></template>{{ t('views.dashboard.desktop.creditIconHint') }}</n-tooltip>
                    </n-statistic>
                  </n-gi>
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.selfPaid')" tabular-nums>
                      {{ formatTokenWithCredit(myQuotaStatus?.self_paid?.total?.usage?.tokens || 0, null, true) }}
                    </n-statistic>
                  </n-gi>
                </n-grid>

                <n-grid :cols="2" :x-gap="12">
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.systemRequestCount')" tabular-nums>
                      {{ myCreditStatus?.requests || 0 }}
                    </n-statistic>
                  </n-gi>
                  <n-gi>
                    <n-statistic :label="t('views.dashboard.desktop.totalErrorCount')" tabular-nums>
                      {{ myUsage?.range_stats?.errors || 0 }}
                    </n-statistic>
                  </n-gi>
                </n-grid>
              </n-space>
            </n-card>

            <UserRedeemCard style="margin-top: 16px;" />

            <n-card :title="t('views.dashboard.desktop.byModel')" size="small" style="margin-top: 16px;">
              <n-data-table
                class="usage-model-table"
                :columns="modelColumnsForTable"
                :data="myUsage?.by_model || []"
                :pagination="false"
                size="small"
                :max-height="300"
              />
            </n-card>

            <n-card :title="t('views.dashboard.desktop.byAgent')" size="small" style="margin-top: 16px;">
              <n-data-table
                :columns="agentColumns"
                :data="myUsage?.by_agent || []"
                :pagination="false"
                size="small"
                :max-height="200"
              />
            </n-card>

          </div>

          <div class="admin-column">
            <MCPConnectCard />
            <FeedbackCard :is-admin="isAdmin" style="margin-top: 16px;" />
            <AdminRedeemCodeManager v-if="isAdmin" style="margin-top: 16px;" />
          </div>

        </div>

        <!-- 管理员用户管理：数据量大时需要横向空间，独占整行 -->
        <AdminUserManager
          v-if="isAdmin"
          class="admin-user-section"
          :users="allUsers"
          :credit-accounts="userCreditAccounts"
          :usage-rows="allUsersUsage"
          :current-user-id="currentUserId"
          :batch-set-active="batchSetUserActive"
          :batch-delete="batchDeleteUsers"
          @adjust-credit="openCreditAdjustModal"
          @toggle-admin="toggleAdmin"
          @toggle-active="toggleUserActive"
          @delete="deleteUser"
        />
      </n-spin>
    </div>

    <n-modal v-model:show="showCreditAdjustModal">
      <n-card
        style="width: 520px; max-width: calc(100vw - 48px);"
        :title="t('views.dashboard.desktop.adjustUserCreditTitle', { username: activeCreditUser?.user?.username || '' })"
        :bordered="false"
        size="huge"
        role="dialog"
        aria-modal="true"
      >
        <n-form :model="creditAdjustForm" label-placement="top">
          <n-form-item :label="t('views.dashboard.desktop.creditDelta')">
            <n-input-number v-model:value="creditAdjustForm.deltaCredit" style="width: 100%" />
          </n-form-item>
          <n-form-item :label="t('views.common.remark')">
            <n-input v-model:value="creditAdjustForm.remark" :placeholder="t('views.dashboard.desktop.remarkPlaceholder')" />
          </n-form-item>
        </n-form>

        <template #footer>
          <div style="display: flex; justify-content: flex-end; gap: 12px;">
            <n-button @click="showCreditAdjustModal = false">{{ t('views.common.cancel') }}</n-button>
            <n-button type="primary" :loading="creditAdjustSaving" @click="submitCreditAdjust">{{ t('views.common.save') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showPasswordModal">
      <n-card
        style="width: 440px; max-width: calc(100vw - 48px);"
        :title="t('views.dashboard.desktop.changePassword')"
        :bordered="false"
        size="huge"
        role="dialog"
        aria-modal="true"
      >
        <n-form :model="passwordForm" label-placement="top">
          <n-form-item :label="t('views.dashboard.desktop.currentPassword')">
            <n-input v-model:value="passwordForm.currentPassword" type="password" show-password-on="click" :placeholder="t('views.dashboard.desktop.currentPasswordPlaceholder')" />
          </n-form-item>
          <n-form-item :label="t('views.dashboard.desktop.newPassword')">
            <n-input v-model:value="passwordForm.newPassword" type="password" show-password-on="click" :placeholder="t('views.dashboard.desktop.newPasswordPlaceholder')" />
          </n-form-item>
          <n-form-item :label="t('views.dashboard.desktop.confirmPassword')" :feedback="passwordConfirmError" :validation-status="passwordConfirmError ? 'error' : undefined">
            <n-input v-model:value="passwordForm.confirmPassword" type="password" show-password-on="click" :placeholder="t('views.dashboard.desktop.confirmPasswordPlaceholder')" />
          </n-form-item>
        </n-form>

        <template #footer>
          <div style="display: flex; justify-content: flex-end; gap: 12px;">
            <n-button @click="showPasswordModal = false">{{ t('views.common.cancel') }}</n-button>
            <n-button type="primary" :loading="passwordSaving" :disabled="!canSubmitPassword" @click="handleSubmitPassword">{{ t('views.dashboard.desktop.changePasswordButton') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { 
  NCard, 
  NButton, 
  NIcon, 
  NStatistic, 
  NGrid, 
  NGi, 
  NDataTable, 
  NSpin, 
  NSpace, 
  NText, 
  NPopconfirm, 
  NInputNumber, 
  NInput,
  NSelect, 
  NRadioGroup, 
  NRadio, 
  NModal, 
  NTooltip,
  NForm,
  NFormItem,
  useMessage
} from 'naive-ui';
import SparkTag from '../../components/share/SparkTag.vue';
import SparkSegment from '../../components/share/SparkSegment.vue';
import OnboardingHelpButton from '../../onboarding/components/OnboardingHelpButton.vue';
import SparkAlert from '../../components/share/SparkAlert.vue';
import SparkIcon from '../../components/share/CreditIcon.vue';
import { Key, RefreshCw, ShieldCheck } from '@lucide/vue';
import MCPConnectCard from '../../components/settings/MCPConnectCard.vue';
import FeedbackCard from '../../components/settings/FeedbackCard.vue';
import AdminRedeemCodeManager from '../../components/settings/AdminRedeemCodeManager.vue';
import UserRedeemCard from '../../components/settings/UserRedeemCard.vue';
import AdminUserManager from '../../components/settings/AdminUserManager.vue';
import { useAdminLogic } from '../../composables/useAdminLogic';
import { changePassword } from '../../services/authService';

const { t } = useI18n();
const message = useMessage();

const {
  loading,
  isAdmin,
  currentUserId,
  myUsage,
  myQuotaStatus,
  myCreditStatus,
  usageRange,
  allUsers,
  allUsersUsage,
  userCreditAccounts,
  showCreditAdjustModal,
  creditAdjustSaving,
  activeCreditUser,
  creditAdjustForm,
  usageRangeLabel,
  refreshData,
  fetchMyUsageOnly,
  modelColumns,
  agentColumns,
  openCreditAdjustModal,
  submitCreditAdjust,
  toggleAdmin,
  toggleUserActive,
  deleteUser,
  batchSetUserActive,
  batchDeleteUsers,
} = useAdminLogic();

const modelColumnsForTable = modelColumns;

const showPasswordModal = ref(false);
const passwordSaving = ref(false);
const passwordForm = ref({
  currentPassword: '',
  newPassword: '',
  confirmPassword: '',
});

const passwordConfirmError = computed(() => {
  if (!passwordForm.value.confirmPassword) return '';
  if (passwordForm.value.newPassword !== passwordForm.value.confirmPassword) {
    return t('views.dashboard.desktop.passwordMismatch');
  }
  return '';
});

const canSubmitPassword = computed(() => {
  return passwordForm.value.currentPassword
    && passwordForm.value.newPassword
    && passwordForm.value.confirmPassword
    && !passwordConfirmError.value
    && passwordForm.value.newPassword.length >= 6;
});

async function handleSubmitPassword() {
  if (!canSubmitPassword.value) return;
  passwordSaving.value = true;
  try {
    await changePassword(passwordForm.value.currentPassword, passwordForm.value.newPassword);
    message.success(t('views.dashboard.desktop.passwordChangeSuccess'));
    passwordForm.value = { currentPassword: '', newPassword: '', confirmPassword: '' };
    showPasswordModal.value = false;
  } catch (e: any) {
    message.error(e.message || t('views.dashboard.desktop.passwordChangeFailed'));
  } finally {
    passwordSaving.value = false;
  }
}

const usageRangeOptions = computed(() => [
  { value: '24h', label: '24h' },
  { value: '7d', label: t('views.dashboard.desktop.rangeWeek') },
  { value: '30d', label: t('views.dashboard.desktop.rangeMonth') },
  { value: 'total', label: t('views.dashboard.desktop.rangeAll') },
]);

function handleUsageRangeChange(v: string) {
  if (v === '24h' || v === '7d' || v === '30d' || v === 'total') {
    usageRange.value = v;
    fetchMyUsageOnly();
  }
}

function formatTokens(value) {
  const num = Number(value) || 0;
  if (num >= 1_000_000) {
    const v = (num / 1_000_000).toFixed(1).replace(/\.0$/, '');
    return `${v}M`;
  }
  if (num >= 1_000) {
    const v = (num / 1_000).toFixed(1).replace(/\.0$/, '');
    return `${v}K`;
  }
  return `${num}`;
}

function formatCredit(value) {
  const num = Number(value) || 0;
  if (Number.isInteger(num)) return `${num}`;
  return `${num.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}`;
}

function formatCreditExact(value) {
  const num = Number(value) || 0;
  if (Number.isInteger(num)) return `${num}`;
  return `${num.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}`;
}

function formatTokenWithCredit(tokens, credit, noCredit = false) {
  const tokenText = `${formatTokens(tokens)} Token`;
  if (noCredit) return tokenText;
  return `${tokenText}/${formatCredit(credit || 0)}`;
}
</script>

<style scoped>
.view-container {
  height: 100%;
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background-color: var(--spark-bg);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.content-area {
  flex: 1;
  width: 100%;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--spark-panel-padding);
}

.admin-container {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  max-width: 100%;
}

.admin-user-section {
  margin-top: 16px;
}

.admin-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 16px;
}

.admin-column > * {
  min-width: 0;
  max-width: 100%;
}

.usage-model-table {
  min-width: 0;
}

.usage-model-table :deep(.n-data-table-th),
.usage-model-table :deep(.n-data-table-td) {
  white-space: normal;
}

.usage-model-table :deep(.n-data-table-td__ellipsis) {
  white-space: normal;
}

@media (max-width: 1200px) {
  .admin-container {
    grid-template-columns: 1fr;
  }
}
</style>
