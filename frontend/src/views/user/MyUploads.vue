<template>
  <div class="page-container">
    <h3 class="page-title">我的上传</h3>

    <div v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="还没有上传记录" />

      <template v-else>
        <el-table :data="items" stripe @row-click="onRowClick" style="width: 100%">
          <el-table-column label="标题" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="doc-title" :class="{ approved: row.status === 'approved' }">
                {{ row.title }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag :type="FILE_TYPE_TAG[row.file_type] || 'info'" size="small">
                {{ FILE_TYPE_LABEL[row.file_type] || row.file_type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="DOC_STATUS_TAG[row.status] || 'info'" size="small">
                {{ DOC_STATUS_LABEL[row.status] || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="160">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="拒绝原因 / 备注" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.status === 'rejected' && row.reject_reason" class="reject-reason">
                {{ row.reject_reason }}
              </span>
              <span v-else-if="row.error_message" class="reject-reason">{{ row.error_message }}</span>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" align="center">
            <template #default="{ row }">
              <!-- pending 可撤回；approved 可查看详情 -->
              <el-button v-if="row.status === 'pending'" size="small" type="danger" plain @click.stop="handleWithdraw(row)">
                撤回
              </el-button>
              <el-button v-else-if="row.status === 'approved'" size="small" type="primary" plain @click.stop="goDetail(row.id)">
                查看
              </el-button>
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { docApi } from '@/api/modules'
import { FILE_TYPE_LABEL, FILE_TYPE_TAG, DOC_STATUS_LABEL, DOC_STATUS_TAG, formatTime } from '@/utils/format'

const router = useRouter()

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)

async function fetchList() {
  loading.value = true
  try {
    const res = await docApi.mine({ page: page.value, page_size: pageSize.value })
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function handleWithdraw(row) {
  try {
    await ElMessageBox.confirm(`确定撤回上传「${row.title}」吗？撤回后文件将被删除。`, '撤回确认', {
      type: 'warning',
    })
  } catch (e) {
    return
  }
  try {
    await docApi.withdraw(row.id)
    ElMessage.success('已撤回')
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function onPageChange(p) {
  page.value = p
  fetchList()
}

function goDetail(id) {
  router.push(`/documents/${id}`)
}

function onRowClick(row) {
  if (row.status === 'approved') goDetail(row.id)
}

onMounted(fetchList)
</script>

<style scoped>
.doc-title {
  font-weight: 600;
}

.doc-title.approved {
  color: var(--brand);
  cursor: pointer;
}

.reject-reason {
  color: var(--danger);
  font-size: 13px;
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
