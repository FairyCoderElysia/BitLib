<template>
  <div class="page-card tk-card">
    <!-- 工具条 -->
    <div class="toolbar">
      <h3 class="page-title">审批中心</h3>
      <div class="toolbar-actions">
        <span v-if="selected.length" class="selected-tip">已选 {{ selected.length }} 项</span>
        <el-button
          type="primary"
          plain
          :disabled="!selected.length"
          :loading="batchLoading"
          @click="handleBatch('approve')"
        >
          批量通过
        </el-button>
        <el-button
          type="danger"
          plain
          :disabled="!selected.length"
          :loading="batchLoading"
          @click="handleBatchReject"
        >
          批量拒绝
        </el-button>
        <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
      </div>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="暂无待审批文档" />

      <template v-else>
        <el-table :data="items" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="44" />
          <el-table-column label="文档" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="doc-title">{{ row.title }}</div>
              <div class="doc-file muted">{{ row.file_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="格式" width="80">
            <template #default="{ row }">
              <el-tag :type="FILE_TYPE_TAG[row.file_type] || 'info'" size="small">
                {{ FILE_TYPE_LABEL[row.file_type] || row.file_type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="部门" width="110">
            <template #default="{ row }">{{ row.department_name || '公开' }}</template>
          </el-table-column>
          <el-table-column label="上传时间" width="150">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="大小" width="90">
            <template #default="{ row }">
              <span class="num">{{ formatSize(row.file_size) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="210" align="center">
            <template #default="{ row }">
              <el-button size="small" :icon="View" @click="openPreview(row)">预览</el-button>
              <el-button size="small" type="primary" @click="handleApprove(row)">通过</el-button>
              <el-button size="small" type="danger" plain @click="openReject(row)">拒绝</el-button>
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

    <!-- 原文预览弹窗 -->
    <el-dialog
      v-model="previewVisible"
      :title="previewTitle"
      width="860px"
      top="6vh"
      destroy-on-close
    >
      <PreviewDocument
        v-if="previewVisible"
        :document-id="previewDoc?.id"
        :file-type="previewDoc?.file_type"
        :title="previewDoc?.title"
      />
    </el-dialog>

    <!-- 拒绝原因弹窗 -->
    <el-dialog v-model="rejectVisible" title="拒绝审批" width="460px" destroy-on-close>
      <el-input
        v-model="rejectReason"
        type="textarea"
        :rows="4"
        maxlength="500"
        show-word-limit
        placeholder="请输入拒绝原因（上传者可见）"
      />
      <template #footer>
        <el-button @click="rejectVisible = false">取消</el-button>
        <el-button type="danger" :loading="rejectLoading" :disabled="!rejectReason.trim()" @click="submitReject">
          确认拒绝
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { View, Refresh } from '@element-plus/icons-vue'
import { adminApi } from '@/api/modules'
import PreviewDocument from '@/components/PreviewDocument.vue'
import { FILE_TYPE_LABEL, FILE_TYPE_TAG, formatSize, formatTime } from '@/utils/format'

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const selected = ref([])
const batchLoading = ref(false)

// 预览
const previewVisible = ref(false)
const previewDoc = ref(null)

// 拒绝
const rejectVisible = ref(false)
const rejectDoc = ref(null)
const rejectReason = ref('')
const rejectLoading = ref(false)

const previewTitle = computed(() =>
  previewDoc.value ? `原文预览：${previewDoc.value.title}` : '原文预览')

async function fetchList() {
  loading.value = true
  try {
    const res = await adminApi.pending({ page: page.value, page_size: pageSize.value })
    items.value = res.items || []
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

function onSelectionChange(rows) {
  selected.value = rows
}

function openPreview(row) {
  previewDoc.value = row
  previewVisible.value = true
}

/** 单条通过 */
async function handleApprove(row) {
  try {
    await ElMessageBox.confirm(`确定通过「${row.title}」的审批吗？`, '审批通过', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await adminApi.approve(row.id)
    ElMessage.success(`「${row.title}」已通过`)
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function openReject(row) {
  rejectDoc.value = row
  rejectReason.value = ''
  rejectVisible.value = true
}

/** 单条 / 批量拒绝 */
async function submitReject() {
  const list = rejectDoc.value ? [rejectDoc.value] : selected.value
  await doReject(list)
}

/** 批量通过 */
async function handleBatch() {
  try {
    await ElMessageBox.confirm(`确定通过选中的 ${selected.value.length} 个文档吗？`, '批量通过', { type: 'warning' })
  } catch (e) {
    return
  }
  await doApprove(selected.value)
}

/** 批量拒绝：先弹原因输入 */
async function handleBatchReject() {
  const { value } = await ElMessageBox.prompt('请输入拒绝原因（上传者可见）', '批量拒绝', {
    inputType: 'textarea',
    inputPlaceholder: '原因将应用到全部选中项',
    inputValidator: (v) => (v && v.trim() ? true : '拒绝原因不能为空'),
  }).catch(() => null)
  if (value == null) return
  rejectReason.value = value
  await doReject(selected.value)
}

/** 循环调用通过；部分失败时提示明细（id + 原因） */
async function doApprove(list) {
  batchLoading.value = true
  const failed = []
  for (const doc of list) {
    try {
      await adminApi.approve(doc.id)
    } catch (e) {
      failed.push({ title: doc.title, msg: e?.message || '请求失败' })
    }
  }
  batchLoading.value = false
  reportResult('通过', list.length, failed)
  closeRejectDialog()
  fetchList()
}

/** 循环调用拒绝；部分失败时提示明细 */
async function doReject(list) {
  batchLoading.value = true
  const failed = []
  for (const doc of list) {
    try {
      await adminApi.reject(doc.id, rejectReason.value.trim())
    } catch (e) {
      failed.push({ title: doc.title, msg: e?.message || '请求失败' })
    }
  }
  batchLoading.value = false
  reportResult('拒绝', list.length, failed)
  closeRejectDialog()
  fetchList()
}

function reportResult(action, totalCount, failed) {
  const ok = totalCount - failed.length
  if (failed.length) {
    const detail = failed.map((f) => `${f.title}（${f.msg}）`).join('；')
    ElMessage.warning(`成功 ${action} ${ok} 个，失败 ${failed.length} 个：${detail}`)
  } else {
    ElMessage.success(`已${action} ${ok} 个文档`)
  }
}

function closeRejectDialog() {
  rejectVisible.value = false
  rejectDoc.value = null
  rejectReason.value = ''
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
  margin-bottom: 14px;
  flex-wrap: wrap;
  gap: 10px;
}

.page-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.selected-tip {
  font-size: 13px;
  color: var(--ink-600);
}

.doc-title {
  font-weight: 600;
  color: var(--ink-900);
}

.doc-file {
  font-size: 12px;
  margin-top: 2px;
}

.muted {
  color: var(--ink-400);
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
