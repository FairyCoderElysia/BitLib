<template>
  <div class="page-card tk-card">
    <!-- 筛选 -->
    <div class="toolbar">
      <div class="filters">
        <el-select v-model="filters.action" placeholder="操作类型" clearable filterable style="width: 150px" @change="onFilterChange">
          <el-option v-for="a in ACTION_OPTIONS" :key="a" :label="ACTION_LABEL[a] || a" :value="a" />
        </el-select>
        <el-select v-model="filters.target_type" placeholder="对象类型" clearable style="width: 130px" @change="onFilterChange">
          <el-option v-for="(label, value) in TARGET_OPTIONS" :key="value" :label="label" :value="value" />
        </el-select>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          style="width: 260px"
          @change="onFilterChange"
        />
      </div>
      <div class="toolbar-actions">
        <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
      </div>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="暂无审计日志" />

      <template v-else>
        <el-table :data="items">
          <el-table-column label="操作人" width="130">
            <template #default="{ row }">{{ row.username || '系统' }}</template>
          </el-table-column>
          <el-table-column label="动作" width="140">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ ACTION_LABEL[row.action] || row.action }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="对象类型" width="110">
            <template #default="{ row }">{{ TARGET_OPTIONS[row.target_type] || row.target_type || '-' }}</template>
          </el-table-column>
          <el-table-column label="对象 ID" width="80">
            <template #default="{ row }"><span class="num">{{ row.target_id ?? '-' }}</span></template>
          </el-table-column>
          <el-table-column label="详情" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="detail-text">{{ formatDetail(row.detail) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="IP" width="120">
            <template #default="{ row }"><span class="num">{{ row.ip || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="时间" width="150">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>

        <div v-if="total > pageSize" class="pagination-wrap">
          <el-pagination
            background
            layout="prev, pager, next, total"
            :total="total"
            :page-size="pageSize"
            :current-page="page"
            @current-change="onPageChange"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { adminApi } from '@/api/modules'
import { formatTime } from '@/utils/format'

// 动作/对象类型文案映射（覆盖常见操作，未知原样显示）
const ACTION_LABEL = {
  login: '登录',
  upload: '上传',
  approve: '审批通过',
  reject: '审批拒绝',
  withdraw: '撤回',
  direct_upload: '直入库上传',
  reprocess: '重新入库',
  patch_document: '修改文档',
  document_delete: '删除文档',
  download: '下载',
  user_create: '创建用户',
  user_update: '修改用户',
  user_delete: '删除用户',
  crawl_task_create: '新建爬虫任务',
  crawl_task_update: '修改爬虫任务',
  crawl_task_delete: '删除爬虫任务',
  push_create: '创建推送',
  favorite_add: '添加收藏',
  favorite_remove: '取消收藏',
}
const ACTION_OPTIONS = Object.keys(ACTION_LABEL)

const TARGET_OPTIONS = {
  document: '文档',
  user: '用户',
  crawl_task: '爬虫任务',
  push_notification: '推送',
  favorite: '收藏',
}

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const filters = ref({ action: '', target_type: '' })
const dateRange = ref(null)

/** detail 为 JSON 对象，压缩为可读文本 */
function formatDetail(detail) {
  if (!detail) return '-'
  if (typeof detail !== 'object') return String(detail)
  return Object.entries(detail)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join('，')
}

async function fetchList() {
  loading.value = true
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
      action: filters.value.action || undefined,
      target_type: filters.value.target_type || undefined,
    }
    // 时间范围：当天 00:00 起 / 次日 00:00 止
    if (dateRange.value && dateRange.value.length === 2) {
      params.created_from = `${dateRange.value[0]}T00:00:00`
      params.created_to = `${dateRange.value[1]}T23:59:59`
    }
    const res = await adminApi.auditLogs(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  page.value = 1
  fetchList()
}

function onPageChange(p) {
  page.value = p
  fetchList()
}

onMounted(fetchList)
</script>

<style scoped>
.page-card {
  padding: 18px 20px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.detail-text {
  font-size: 12px;
  color: var(--ink-600);
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
