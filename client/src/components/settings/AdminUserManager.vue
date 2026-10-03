<template>
  <n-card :title="t('views.dashboard.desktop.adminUsers.title')" size="small" class="admin-user-manager">
    <template #header-extra>
      <n-text depth="3">{{ t('views.dashboard.desktop.totalUsers', { count: rows.length }) }}</n-text>
    </template>

    <!-- 概览统计：点击可快速套用筛选 -->
    <div class="stat-row">
      <button type="button" class="stat-chip" :class="{ 'is-active': !roleFilter && !statusFilter }" @click="resetFilters">
        <span class="stat-chip__num">{{ stats.total }}</span>
        <span class="stat-chip__label">{{ t('views.dashboard.desktop.adminUsers.statTotal') }}</span>
      </button>
      <button type="button" class="stat-chip stat-chip--success" :class="{ 'is-active': statusFilter === 'active' }" @click="toggleStatus('active')">
        <span class="stat-chip__num">{{ stats.active }}</span>
        <span class="stat-chip__label">{{ t('views.dashboard.desktop.adminUsers.statActive') }}</span>
      </button>
      <button type="button" class="stat-chip stat-chip--danger" :class="{ 'is-active': statusFilter === 'banned' }" @click="toggleStatus('banned')">
        <span class="stat-chip__num">{{ stats.banned }}</span>
        <span class="stat-chip__label">{{ t('views.dashboard.desktop.adminUsers.statBanned') }}</span>
      </button>
      <button type="button" class="stat-chip stat-chip--warning" :class="{ 'is-active': roleFilter === 'admin' }" @click="toggleRole('admin')">
        <span class="stat-chip__num">{{ stats.admin }}</span>
        <span class="stat-chip__label">{{ t('views.dashboard.desktop.adminUsers.statAdmin') }}</span>
      </button>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <n-input
        v-model:value="keyword"
        size="small"
        clearable
        class="filter-bar__search"
        :placeholder="t('views.dashboard.desktop.adminUsers.searchPlaceholder')"
      >
        <template #prefix><n-icon><Search /></n-icon></template>
      </n-input>
      <n-select v-model:value="roleFilter" size="small" clearable class="filter-bar__select" :options="roleOptions" :placeholder="t('views.dashboard.desktop.adminUsers.roleAll')" />
      <n-select v-model:value="statusFilter" size="small" clearable class="filter-bar__select" :options="statusOptions" :placeholder="t('views.dashboard.desktop.adminUsers.statusAll')" />
      <n-text depth="3" class="filter-bar__count">{{ t('views.dashboard.desktop.adminUsers.filteredCount', { count: filteredRows.length }) }}</n-text>
      <n-button v-if="hasFilter" size="small" quaternary @click="resetFilters">
        <template #icon><n-icon><FilterX /></n-icon></template>
        {{ t('views.dashboard.desktop.adminUsers.resetFilters') }}
      </n-button>
    </div>

    <!-- 批量操作栏：有选中项时出现 -->
    <transition name="bulk-fade">
      <div v-if="checkedKeys.length" class="bulk-bar">
        <span class="bulk-bar__count">{{ t('views.dashboard.desktop.adminUsers.selectedCount', { count: checkedKeys.length }) }}</span>
        <n-popconfirm @positive-click="runBatchActive(false)">
          <template #trigger>
            <n-button size="tiny" type="error" secondary :loading="batchBusy">
              <template #icon><n-icon><Ban /></n-icon></template>
              {{ t('views.dashboard.desktop.adminUsers.batchBan') }}
            </n-button>
          </template>
          {{ t('views.dashboard.desktop.adminUsers.batchBanConfirm', { count: checkedKeys.length }) }}
        </n-popconfirm>
        <n-popconfirm @positive-click="runBatchActive(true)">
          <template #trigger>
            <n-button size="tiny" type="success" secondary :loading="batchBusy">
              <template #icon><n-icon><Unlock /></n-icon></template>
              {{ t('views.dashboard.desktop.adminUsers.batchUnban') }}
            </n-button>
          </template>
          {{ t('views.dashboard.desktop.adminUsers.batchUnbanConfirm', { count: checkedKeys.length }) }}
        </n-popconfirm>
        <n-popconfirm @positive-click="runBatchDelete">
          <template #trigger>
            <n-button size="tiny" type="error" tertiary :loading="batchBusy">
              <template #icon><n-icon><Trash /></n-icon></template>
              {{ t('views.dashboard.desktop.adminUsers.batchDelete') }}
            </n-button>
          </template>
          {{ t('views.dashboard.desktop.adminUsers.batchDeleteConfirm', { count: checkedKeys.length }) }}
        </n-popconfirm>
        <n-button size="tiny" quaternary @click="checkedKeys = []">{{ t('views.dashboard.desktop.adminUsers.clearSelection') }}</n-button>
      </div>
    </transition>

    <n-data-table
      class="user-table"
      size="small"
      :columns="columns"
      :data="filteredRows"
      :row-key="(row: UserRow) => row.user_id"
      v-model:checked-row-keys="checkedKeys"
      :pagination="pagination"
      :scroll-x="880"
      :max-height="520"
      :bordered="false"
      :single-line="false"
    >
      <template #empty>
        <n-empty :description="t('views.dashboard.desktop.adminUsers.empty')" />
      </template>
    </n-data-table>
  </n-card>
</template>

<script setup lang="ts">
import { computed, h, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  NAvatar, NButton, NCard, NDataTable, NEmpty, NIcon, NInput, NPopconfirm,
  NSelect, NTag, NText, NTooltip, useMessage,
} from 'naive-ui';
import type { DataTableColumns } from 'naive-ui';
import { Ban, Coins, FilterX, Search, Trash, Unlock, UserCog } from '@lucide/vue';
import { formatTokens, formatPrice } from '../../services/adminService';

type UserItem = { user_id: number; username: string; is_admin: boolean; is_active: boolean };
type CreditAccountItem = {
  user?: UserItem;
  account?: { credit_balance?: number; credit_total_used?: number };
};
type UsageRow = {
  user: UserItem;
  last_24h?: { tokens?: number };
  total?: { tokens?: number; requests?: number };
};
type BatchResult = { ok: number; fail: number };

/** 合并后的用户行：把用户、火柴账户、用量三份数据按 user_id 拍平成一行 */
type UserRow = UserItem & {
  balance: number;
  used: number;
  tokens24h: number;
  tokensTotal: number;
  requestsTotal: number;
  creditItem: CreditAccountItem;
};

const props = defineProps<{
  users: UserItem[];
  creditAccounts: CreditAccountItem[];
  usageRows: UsageRow[];
  currentUserId: number | null;
  batchSetActive: (ids: number[], active: boolean) => Promise<BatchResult>;
  batchDelete: (ids: number[]) => Promise<BatchResult>;
}>();

const emit = defineEmits<{
  (e: 'adjust-credit', item: CreditAccountItem): void;
  (e: 'toggle-admin', user: UserItem): void;
  (e: 'toggle-active', user: UserItem): void;
  (e: 'delete', user: UserItem): void;
}>();

const { t } = useI18n();
const message = useMessage();

const keyword = ref('');
const roleFilter = ref<'admin' | 'user' | null>(null);
const statusFilter = ref<'active' | 'banned' | null>(null);
const checkedKeys = ref<Array<string | number>>([]);
const page = ref(1);
const pageSize = ref(20);
const batchBusy = ref(false);

const rows = computed<UserRow[]>(() => {
  const creditMap = new Map<number, CreditAccountItem>();
  props.creditAccounts.forEach(item => {
    if (item.user) creditMap.set(item.user.user_id, item);
  });
  const usageMap = new Map<number, UsageRow>();
  props.usageRows.forEach(item => usageMap.set(item.user.user_id, item));

  return props.users.map(user => {
    const credit = creditMap.get(user.user_id);
    const usage = usageMap.get(user.user_id);
    return {
      ...user,
      balance: Number(credit?.account?.credit_balance || 0),
      used: Number(credit?.account?.credit_total_used || 0),
      tokens24h: Number(usage?.last_24h?.tokens || 0),
      tokensTotal: Number(usage?.total?.tokens || 0),
      requestsTotal: Number(usage?.total?.requests || 0),
      creditItem: credit || { user, account: {} },
    };
  });
});

const stats = computed(() => ({
  total: rows.value.length,
  active: rows.value.filter(r => r.is_active).length,
  banned: rows.value.filter(r => !r.is_active).length,
  admin: rows.value.filter(r => r.is_admin).length,
}));

const filteredRows = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return rows.value.filter(r => {
    if (roleFilter.value === 'admin' && !r.is_admin) return false;
    if (roleFilter.value === 'user' && r.is_admin) return false;
    if (statusFilter.value === 'active' && !r.is_active) return false;
    if (statusFilter.value === 'banned' && r.is_active) return false;
    if (!kw) return true;
    return r.username.toLowerCase().includes(kw) || String(r.user_id) === kw;
  });
});

const hasFilter = computed(() => !!(keyword.value.trim() || roleFilter.value || statusFilter.value));

const roleOptions = computed(() => [
  { label: t('views.dashboard.desktop.adminUsers.roleAdmin'), value: 'admin' },
  { label: t('views.dashboard.desktop.adminUsers.roleUser'), value: 'user' },
]);
const statusOptions = computed(() => [
  { label: t('views.dashboard.desktop.adminUsers.statusActive'), value: 'active' },
  { label: t('views.dashboard.desktop.adminUsers.statusBanned'), value: 'banned' },
]);

const pagination = computed(() => ({
  page: page.value,
  pageSize: pageSize.value,
  pageSizes: [20, 50, 100, 200],
  showSizePicker: true,
  pageSlot: 5,
  onUpdatePage: (p: number) => {
    page.value = p;
  },
  onUpdatePageSize: (size: number) => {
    pageSize.value = size;
    page.value = 1;
  },
}));

// 筛选条件变化后回到第一页，并清理已不在当前结果里的选择，避免误操作不可见的用户
watch([keyword, roleFilter, statusFilter], () => {
  page.value = 1;
  const visible = new Set(filteredRows.value.map(r => r.user_id));
  checkedKeys.value = checkedKeys.value.filter(k => visible.has(Number(k)));
});

function resetFilters() {
  keyword.value = '';
  roleFilter.value = null;
  statusFilter.value = null;
}
function toggleStatus(value: 'active' | 'banned') {
  statusFilter.value = statusFilter.value === value ? null : value;
}
function toggleRole(value: 'admin' | 'user') {
  roleFilter.value = roleFilter.value === value ? null : value;
}

/** 受保护用户：自己与初始管理员不可封禁/删除 */
function isProtected(row: UserItem) {
  return row.user_id === 1 || (props.currentUserId !== null && row.user_id === props.currentUserId);
}

function reportBatch(result: BatchResult) {
  const text = t('views.dashboard.desktop.adminUsers.batchResult', result);
  if (result.fail > 0) message.warning(text);
  else message.success(text);
}

async function runBatchActive(active: boolean) {
  batchBusy.value = true;
  try {
    reportBatch(await props.batchSetActive(checkedKeys.value.map(Number), active));
    checkedKeys.value = [];
  } finally {
    batchBusy.value = false;
  }
}

async function runBatchDelete() {
  batchBusy.value = true;
  try {
    reportBatch(await props.batchDelete(checkedKeys.value.map(Number)));
    checkedKeys.value = [];
  } finally {
    batchBusy.value = false;
  }
}

/** 带 Tooltip 的圆形图标按钮 */
function iconButton(opts: {
  icon: any; tip: string; type: 'primary' | 'warning' | 'error' | 'success';
  disabled?: boolean; tertiary?: boolean; onClick?: () => void;
}) {
  const btn = h(NButton, {
    size: 'tiny', circle: true, type: opts.type, secondary: !opts.tertiary, tertiary: opts.tertiary,
    disabled: opts.disabled, onClick: opts.onClick,
  }, { icon: () => h(NIcon, null, () => h(opts.icon)) });
  return h(NTooltip, null, {
    trigger: () => h('span', { class: 'action-wrap' }, [btn]),
    default: () => opts.tip,
  });
}

function withConfirm(trigger: () => any, text: string, onConfirm: () => void) {
  return h(NPopconfirm, { onPositiveClick: onConfirm }, { trigger, default: () => text });
}

const columns = computed<DataTableColumns<UserRow>>(() => [
  {
    type: 'selection',
    width: 40,
    fixed: 'left',
    disabled: (row: UserRow) => isProtected(row),
  },
  {
    title: 'ID',
    key: 'user_id',
    width: 64,
    align: 'center',
    sorter: 'default',
    defaultSortOrder: 'descend',
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colUser'),
    key: 'username',
    minWidth: 160,
    ellipsis: { tooltip: true },
    sorter: (a, b) => a.username.localeCompare(b.username),
    render: (row) => h('div', { class: 'user-cell' }, [
      h(NAvatar, { size: 24, round: true, class: 'user-cell__avatar' }, () => (row.username || '?').slice(0, 1).toUpperCase()),
      h('span', { class: 'user-cell__name' }, row.username),
      props.currentUserId === row.user_id
        ? h(NTag, { size: 'tiny', type: 'info', bordered: false, round: true }, () => t('views.dashboard.desktop.adminUsers.selfTag'))
        : null,
    ]),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colRole'),
    key: 'is_admin',
    width: 92,
    align: 'center',
    sorter: (a, b) => Number(a.is_admin) - Number(b.is_admin),
    render: (row) => h(NTag, {
      size: 'small', round: true, bordered: false, type: row.is_admin ? 'warning' : 'default',
    }, () => row.is_admin ? t('views.dashboard.desktop.adminUsers.roleAdmin') : t('views.dashboard.desktop.adminUsers.roleUser')),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colStatus'),
    key: 'is_active',
    width: 84,
    align: 'center',
    sorter: (a, b) => Number(a.is_active) - Number(b.is_active),
    render: (row) => h(NTag, {
      size: 'small', round: true, bordered: false, type: row.is_active ? 'success' : 'error',
    }, () => row.is_active ? t('views.dashboard.desktop.adminUsers.statusActive') : t('views.dashboard.desktop.adminUsers.statusBanned')),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colBalance'),
    key: 'balance',
    width: 104,
    align: 'right',
    sorter: 'default',
    render: (row) => h('span', { class: 'num' }, formatPrice(row.balance)),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colUsed'),
    key: 'used',
    width: 104,
    align: 'right',
    sorter: 'default',
    render: (row) => h('span', { class: 'num' }, formatPrice(row.used)),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colTokens24h'),
    key: 'tokens24h',
    width: 108,
    align: 'right',
    sorter: 'default',
    render: (row) => h('span', { class: 'num' }, formatTokens(row.tokens24h)),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colTokensTotal'),
    key: 'tokensTotal',
    width: 112,
    align: 'right',
    sorter: 'default',
    render: (row) => h('span', { class: 'num' }, formatTokens(row.tokensTotal)),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colRequestsTotal'),
    key: 'requestsTotal',
    width: 104,
    align: 'right',
    sorter: 'default',
    render: (row) => h('span', { class: 'num' }, String(row.requestsTotal)),
  },
  {
    title: t('views.dashboard.desktop.adminUsers.colActions'),
    key: 'actions',
    width: 148,
    fixed: 'right',
    align: 'center',
    render: (row) => {
      const protectedRow = isProtected(row);
      const protectReason = row.user_id === 1
        ? t('views.dashboard.desktop.protectedInitialAdmin')
        : t('views.dashboard.desktop.protectedSelf');

      const creditBtn = iconButton({
        icon: Coins, type: 'primary', tip: t('views.dashboard.desktop.adminUsers.adjustCredit'),
        onClick: () => emit('adjust-credit', row.creditItem),
      });
      const adminBtn = iconButton({
        icon: UserCog, type: row.is_admin ? 'warning' : 'primary',
        tip: row.is_admin ? t('views.dashboard.desktop.cancelAdmin') : t('views.dashboard.desktop.setAdmin'),
        disabled: row.user_id === 1 && row.is_admin,
        onClick: () => emit('toggle-admin', row),
      });
      const banTrigger = () => iconButton({
        icon: row.is_active ? Ban : Unlock, type: row.is_active ? 'error' : 'success',
        tip: protectedRow ? protectReason : (row.is_active ? t('views.dashboard.desktop.banUser') : t('views.dashboard.desktop.unbanUser')),
        disabled: protectedRow,
      });
      const banBtn = protectedRow
        ? banTrigger()
        : withConfirm(
          banTrigger,
          row.is_active
            ? t('views.dashboard.desktop.adminUsers.banConfirm', { username: row.username })
            : t('views.dashboard.desktop.adminUsers.unbanConfirm', { username: row.username }),
          () => emit('toggle-active', row),
        );
      const delTrigger = () => iconButton({
        icon: Trash, type: 'error', tertiary: true,
        tip: protectedRow ? protectReason : t('views.dashboard.desktop.deleteUser'),
        disabled: protectedRow,
      });
      const delBtn = protectedRow
        ? delTrigger()
        : withConfirm(delTrigger, t('views.dashboard.desktop.adminUsers.deleteConfirm', { username: row.username }), () => emit('delete', row));

      return h('div', { class: 'action-row' }, [creditBtn, adminBtn, banBtn, delBtn]);
    },
  },
]);
</script>

<style scoped>
.admin-user-manager {
  min-width: 0;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.stat-chip {
  --chip-color: var(--spark-primary);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 12px;
  border: 1px solid var(--spark-border);
  border-radius: var(--spark-radius-sm);
  background: color-mix(in srgb, var(--chip-color), transparent 94%);
  color: var(--spark-text);
  cursor: pointer;
  font-family: inherit;
  transition: border-color 0.15s, background 0.15s, transform 0.15s;
}

.stat-chip:hover {
  border-color: var(--chip-color);
  transform: translateY(-1px);
}

.stat-chip.is-active {
  border-color: var(--chip-color);
  background: color-mix(in srgb, var(--chip-color), transparent 84%);
}

.stat-chip--success { --chip-color: var(--spark-success); }
.stat-chip--danger { --chip-color: var(--spark-danger); }
.stat-chip--warning { --chip-color: var(--spark-warning); }

.stat-chip__num {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--chip-color);
  font-variant-numeric: tabular-nums;
}

.stat-chip__label {
  font-size: var(--spark-fs-xs);
  opacity: 0.75;
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.filter-bar__search {
  flex: 1 1 180px;
  min-width: 160px;
}

.filter-bar__select {
  width: 118px;
}

.filter-bar__count {
  margin-left: auto;
  font-size: var(--spark-fs-xs);
}

.bulk-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  margin-bottom: 10px;
  border: 1px solid var(--spark-primary-subtle);
  border-radius: var(--spark-radius-sm);
  background: var(--spark-primary-container);
}

.bulk-bar__count {
  margin-right: 4px;
  font-size: var(--spark-fs-sm);
  font-weight: 600;
  color: var(--spark-primary);
}

.bulk-fade-enter-active,
.bulk-fade-leave-active {
  transition: opacity 0.15s, transform 0.15s;
}

.bulk-fade-enter-from,
.bulk-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.user-table :deep(.user-cell) {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.user-table :deep(.user-cell__avatar) {
  flex-shrink: 0;
  font-size: 12px;
  background: var(--spark-primary-container);
  color: var(--spark-primary);
}

.user-table :deep(.user-cell__name) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-table :deep(.num) {
  font-variant-numeric: tabular-nums;
}

.user-table :deep(.action-row) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.user-table :deep(.action-wrap) {
  display: inline-flex;
}
</style>
