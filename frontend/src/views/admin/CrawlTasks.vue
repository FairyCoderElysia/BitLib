<template>
  <div class="page-card tk-card">
    <div class="toolbar">
      <h3 class="page-title">爬虫任务</h3>
      <div class="toolbar-actions">
        <el-button type="primary" :icon="Plus" @click="openCreate">新建任务</el-button>
        <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
      </div>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="暂无爬虫任务" />

      <template v-else>
        <el-table :data="items" row-key="id">
          <!-- 运行记录展开 -->
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="run-logs">
                <div class="run-logs-head">
                  <span>运行记录</span>
                  <el-button size="small" :loading="row._logsLoading" @click="loadLogs(row)">刷新</el-button>
                </div>
                <el-table v-if="row._logs?.length" :data="row._logs" size="small">
                  <el-table-column label="开始时间" width="160">
                    <template #default="{ row: l }">{{ formatTime(l.started_at) }}</template>
                  </el-table-column>
                  <el-table-column label="结束时间" width="160">
                    <template #default="{ row: l }">{{ formatTime(l.finished_at) }}</template>
                  </el-table-column>
                  <el-table-column label="状态" width="90">
                    <template #default="{ row: l }">
                      <el-tag :type="runStatusTag(l.status)" size="small">{{ RUN_STATUS_LABEL[l.status] || l.status }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="抓取" prop="fetched_count" width="70" align="center">
                    <template #default="{ row: l }"><span class="num">{{ l.fetched_count }}</span></template>
                  </el-table-column>
                  <el-table-column label="入库" prop="ingested_count" width="70" align="center">
                    <template #default="{ row: l }"><span class="num">{{ l.ingested_count }}</span></template>
                  </el-table-column>
                  <el-table-column label="更新" prop="updated_count" width="70" align="center">
                    <template #default="{ row: l }"><span class="num">{{ l.updated_count }}</span></template>
                  </el-table-column>
                  <el-table-column label="跳过" prop="skipped_count" width="70" align="center">
                    <template #default="{ row: l }"><span class="num">{{ l.skipped_count }}</span></template>
                  </el-table-column>
                  <el-table-column label="错误信息" min-width="180" show-overflow-tooltip>
                    <template #default="{ row: l }">
                      <span v-if="l.error" class="err-text">{{ l.error }}</span>
                      <span v-else class="muted">-</span>
                    </template>
                  </el-table-column>
                </el-table>
                <el-empty v-else-if="!row._logsLoading" description="暂无运行记录" :image-size="50" />
              </div>
            </template>
          </el-table-column>

          <el-table-column label="名称" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="task-name">{{ row.name }}</div>
              <div class="task-url muted">{{ (row.start_urls || []).join('，') }}</div>
            </template>
          </el-table-column>
          <el-table-column label="目标部门" width="180">
            <template #default="{ row }">{{ deptNames(row) }}</template>
          </el-table-column>
          <el-table-column label="cron" width="110">
            <template #default="{ row }">
              <span class="num">{{ row.schedule || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="深度" width="60">
            <template #default="{ row }"><span class="num">{{ row.max_depth }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
                {{ row.enabled ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="70" align="center">
            <template #default="{ row }">
              <el-switch v-model="row.enabled" @change="(v) => onToggle(row, v)" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="210" align="center">
            <template #default="{ row }">
              <el-button size="small" type="primary" plain :icon="VideoPlay" :loading="row._running" @click="handleRun(row)">
                执行
              </el-button>
              <el-button size="small" :icon="Edit" @click="openEdit(row)">编辑</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="handleDelete(row)">删除</el-button>
            </template>
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

    <!-- 新建 / 编辑弹窗 -->
    <el-dialog v-model="formVisible" :title="editingId ? '编辑任务' : '新建任务'" width="560px" destroy-on-close>
      <el-form :model="form" label-width="130px">
        <el-form-item label="任务名称" required>
          <el-input v-model="form.name" placeholder="任务名称" maxlength="128" />
        </el-form-item>
        <el-form-item label="起始 URL" required>
          <el-input
            v-model="form.startUrls"
            type="textarea"
            :rows="2"
            placeholder="每行一个，或逗号分隔；如 https://example.com"
          />
        </el-form-item>
        <el-form-item label="域名白名单" required>
          <el-input
            v-model="form.allowedDomains"
            type="textarea"
            :rows="2"
            placeholder="每行一个，或逗号分隔；如 example.com（SSRF 防护）"
          />
        </el-form-item>
        <el-form-item label="正文选择器">
          <el-input v-model="form.selector" placeholder="CSS 选择器，留空 = 智能提取" />
        </el-form-item>
        <el-form-item label="最大深度">
          <el-input-number v-model="form.max_depth" :min="0" :max="5" />
        </el-form-item>
        <el-form-item label="cron 表达式">
          <el-input v-model="form.schedule" placeholder="如 0 9 * * *；留空 = 仅手动执行" />
        </el-form-item>
        <el-form-item label="目标部门">
          <el-select
            v-model="form.target_department_ids"
            placeholder="留空 = 公开（全员可见）"
            multiple
            clearable
            collapse-tags
            collapse-tags-tooltip
            style="width: 100%"
          >
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.name.trim() || !form.startUrls.trim() || !form.allowedDomains.trim()" @click="submitForm">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, VideoPlay, Edit, Delete } from '@element-plus/icons-vue'
import { adminApi, authApi } from '@/api/modules'
import { formatTime } from '@/utils/format'

const RUN_STATUS_LABEL = { running: '运行中', success: '成功', failed: '失败' }
function runStatusTag(s) {
  return { running: 'warning', success: 'success', failed: 'danger' }[s] || 'info'
}

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const departments = ref([])

const formVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = ref({
  name: '',
  startUrls: '',
  allowedDomains: '',
  selector: '',
  max_depth: 1,
  schedule: '',
  enabled: false,
  target_department_ids: [],
})

/** 列表「目标部门」列：优先多部门展示，空/无集合 → 公开 */
function deptNames(row) {
  const ids = row.target_department_ids?.length
    ? row.target_department_ids
    : (row.target_department_id != null ? [row.target_department_id] : [])
  if (!ids.length) return '公开'
  return ids.map((id) => {
    const d = departments.value.find((x) => x.id === id)
    return d ? d.name : String(id)
  }).join('、')
}

/** 逗号 / 换行分隔 → 数组 */
function splitList(text) {
  return text.split(/[\n,，]/).map((s) => s.trim()).filter(Boolean)
}

async function fetchList() {
  loading.value = true
  try {
    const res = await adminApi.crawlTasks({ page: page.value, page_size: pageSize.value })
    items.value = (res.items || []).map((t) => ({ ...t, _logs: null, _logsLoading: false, _running: false }))
    total.value = res.total || 0
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function onPageChange(p) {
  page.value = p
  fetchList()
}

function openCreate() {
  editingId.value = null
  form.value = {
    name: '', startUrls: '', allowedDomains: '', selector: '',
    max_depth: 1, schedule: '', enabled: false, target_department_ids: [],
  }
  formVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.value = {
    name: row.name,
    startUrls: (row.start_urls || []).join('\n'),
    allowedDomains: (row.allowed_domains || []).join('\n'),
    selector: row.selector || '',
    max_depth: row.max_depth,
    schedule: row.schedule || '',
    enabled: row.enabled,
    target_department_ids: row.target_department_ids?.length
      ? [...row.target_department_ids]
      : (row.target_department_id != null ? [row.target_department_id] : []),
  }
  formVisible.value = true
}

async function submitForm() {
  saving.value = true
  const payload = {
    name: form.value.name.trim(),
    start_urls: splitList(form.value.startUrls),
    allowed_domains: splitList(form.value.allowedDomains),
    selector: form.value.selector.trim(),
    max_depth: form.value.max_depth,
    schedule: form.value.schedule.trim(),
    enabled: form.value.enabled,
    target_department_ids: form.value.target_department_ids?.length
      ? [...form.value.target_department_ids]
      : [],
  }
  try {
    if (editingId.value) {
      await adminApi.patchCrawlTask(editingId.value, payload)
      ElMessage.success('任务已更新')
    } else {
      await adminApi.createCrawlTask(payload)
      ElMessage.success('任务已创建')
    }
    formVisible.value = false
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

/** 启停切换：失败时回滚开关 */
async function onToggle(row, v) {
  try {
    await adminApi.patchCrawlTask(row.id, { enabled: v })
    ElMessage.success(v ? '已启用' : '已停用')
  } catch (e) {
    row.enabled = !v
  }
}

/** 手动执行：显示结果摘要 */
async function handleRun(row) {
  row._running = true
  try {
    const res = await adminApi.runCrawlTask(row.id)
    const parts = [
      `状态：${RUN_STATUS_LABEL[res.status] || res.status}`,
      `抓取 ${res.fetched_count} · 入库 ${res.ingested_count} · 更新 ${res.updated_count} · 跳过 ${res.skipped_count}`,
      res.error ? `错误：${res.error}` : '',
    ].filter(Boolean).join('\n')
    ElMessageBox.alert(parts, `执行结果：${row.name}`, { confirmButtonText: '知道了' })
    await loadLogs(row)
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    row._running = false
  }
}

/** 运行记录（展开时拉取） */
async function loadLogs(row) {
  row._logsLoading = true
  try {
    const res = await adminApi.crawlLogs(row.id, { page: 1, page_size: 20 })
    row._logs = res.items || []
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    row._logsLoading = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除爬虫任务「${row.name}」吗？`, '删除确认', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await adminApi.deleteCrawlTask(row.id)
    ElMessage.success('已删除')
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function fetchDepartments() {
  try {
    departments.value = (await authApi.departments()) || []
  } catch (e) {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  fetchList()
  fetchDepartments()
})
</script>

<style scoped>
.page-card {
  padding: 18px 20px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.page-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.task-name {
  font-weight: 600;
}

.task-url {
  font-size: 12px;
  margin-top: 2px;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.muted {
  color: var(--ink-400);
}

.err-text {
  color: var(--danger);
  font-size: 12px;
}

.run-logs {
  padding: 4px 24px 12px 48px;
}

.run-logs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
