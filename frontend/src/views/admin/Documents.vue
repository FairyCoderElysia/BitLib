<template>
  <div class="page-card tk-card">
    <!-- 筛选 + 上传 -->
    <div class="toolbar">
      <div class="filters">
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 110px" @change="onFilterChange">
          <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <el-select v-model="filters.department_id" placeholder="部门" clearable style="width: 130px" @change="onFilterChange">
          <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
        <el-select v-model="filters.file_type" placeholder="格式" clearable style="width: 100px" @change="onFilterChange">
          <el-option v-for="(label, value) in FILE_TYPE_LABEL" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-model="filters.source" placeholder="来源" clearable style="width: 120px" @change="onFilterChange">
          <el-option label="用户上传" value="upload" />
          <el-option label="爬虫抓取" value="crawl" />
        </el-select>
        <el-select v-model="filters.is_featured" placeholder="重点" clearable style="width: 100px" @change="onFilterChange">
          <el-option label="重点" :value="true" />
          <el-option label="非重点" :value="false" />
        </el-select>
      </div>

      <div class="toolbar-actions">
        <el-button type="primary" :icon="Upload" @click="uploadVisible = true">上传文档</el-button>
        <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
      </div>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="暂无文档" />

      <template v-else>
        <el-table :data="items">
          <el-table-column label="标题" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="doc-title">
                <span>{{ row.title }}</span>
                <el-icon v-if="row.is_featured" class="star" :title="'重点文档'"><StarFilled /></el-icon>
              </div>
              <div class="doc-file muted">{{ row.file_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="格式" width="76">
            <template #default="{ row }">
              <el-tag :type="FILE_TYPE_TAG[row.file_type] || 'info'" size="small">
                {{ FILE_TYPE_LABEL[row.file_type] || row.file_type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="90">
            <template #default="{ row }">{{ SOURCE_LABEL[row.source] || row.source }}</template>
          </el-table-column>
          <el-table-column label="部门" width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.department_name || '公开' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="92">
            <template #default="{ row }">
              <el-tag :type="DOC_STATUS_TAG[row.status] || 'info'" size="small">
                {{ DOC_STATUS_LABEL[row.status] || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="重点" width="64" align="center">
            <template #default="{ row }">
              <el-button
                text
                :icon="row.is_featured ? StarFilled : Star"
                :class="row.is_featured ? 'star-on' : 'star-off'"
                title="切换重点标记"
                @click="toggleFeatured(row)"
              />
            </template>
          </el-table-column>
          <el-table-column label="大小" width="86">
            <template #default="{ row }"><span class="num">{{ formatSize(row.file_size) }}</span></template>
          </el-table-column>
          <el-table-column label="上传时间" width="140">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="300" align="center">
            <template #default="{ row }">
              <el-button size="small" :icon="View" :disabled="row.status === 'offline'" @click="openPreview(row)">预览</el-button>
              <el-button
                v-if="row.status === 'approved'"
                size="small"
                type="warning"
                plain
                @click="toggleStatus(row, 'offline')"
              >下架</el-button>
              <el-button
                v-else-if="row.status === 'offline'"
                size="small"
                type="success"
                plain
                @click="toggleStatus(row, 'approved')"
              >重新上架</el-button>
              <el-button
                v-if="row.status === 'failed' || row.status === 'offline'"
                size="small"
                :icon="RefreshRight"
                @click="handleReprocess(row)"
              >重新入库</el-button>
              <el-dropdown trigger="click" @command="(cmd) => onMoreCommand(cmd, row)">
                <el-button size="small">更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="dept" :icon="OfficeBuilding">改部门</el-dropdown-item>
                    <el-dropdown-item command="delete" :icon="Delete" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
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

    <!-- 上传弹窗 -->
    <el-dialog v-model="uploadVisible" title="上传文档（直接入库）" width="520px" destroy-on-close>
      <el-form label-width="80px">
        <el-form-item label="文件">
          <el-upload
            ref="uploadRef"
            drag
            :auto-upload="false"
            :multiple="true"
            :http-request="customUpload"
            :show-file-list="true"
            accept=".txt,.md,.pdf,.docx"
            :file-list="uploadFileList"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
            <template #tip>
              <div class="el-upload__tip">支持 txt / md / pdf / docx，单个不超过 20MB</div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="uploadForm.title" placeholder="留空则取文件名" />
        </el-form-item>
        <el-form-item label="部门">
          <el-select
            v-model="uploadForm.department_id"
            placeholder="留空 = 公开"
            clearable
            style="width: 100%"
            :disabled="isDeptAdmin"
          >
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
          <div v-if="isDeptAdmin" class="form-tip">部门管理员仅可向本部门或公开直入库</div>
        </el-form-item>
        <el-form-item v-if="uploading" label="进度">
          <el-progress :percentage="uploadProgress" :stroke-width="10" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFileList.length" @click="submitUpload">
          开始上传
        </el-button>
      </template>
    </el-dialog>

    <!-- 预览弹窗 -->
    <el-dialog v-model="previewVisible" :title="previewTitle" width="860px" top="6vh" destroy-on-close>
      <PreviewDocument
        v-if="previewVisible"
        :document-id="previewDoc?.id"
        :file-type="previewDoc?.file_type"
        :title="previewDoc?.title"
      />
    </el-dialog>

    <!-- 改部门弹窗 -->
    <el-dialog v-model="deptVisible" title="修改文档所属部门" width="400px" destroy-on-close>
      <el-select v-model="newDeptId" placeholder="选择部门" style="width: 100%">
        <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
      </el-select>
      <template #footer>
        <el-button @click="deptVisible = false">取消</el-button>
        <el-button type="primary" :loading="deptLoading" :disabled="newDeptId == null" @click="submitChangeDept">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { adminApi, authApi } from '@/api/modules'
import http from '@/api/http'
import PreviewDocument from '@/components/PreviewDocument.vue'
import {
  Upload, Refresh, View, Star, StarFilled, RefreshRight, ArrowDown,
  OfficeBuilding, Delete, UploadFilled,
} from '@element-plus/icons-vue'
import {
  FILE_TYPE_LABEL, FILE_TYPE_TAG, SOURCE_LABEL,
  DOC_STATUS_LABEL, DOC_STATUS_TAG, formatSize, formatTime,
} from '@/utils/format'

const userStore = useUserStore()
const isDeptAdmin = computed(() => userStore.role === 'dept_admin')

const statusOptions = Object.entries(DOC_STATUS_LABEL).map(([value, label]) => ({ value, label }))

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const departments = ref([])
const filters = ref({ status: '', department_id: null, file_type: '', source: '', is_featured: null })

// 上传
const uploadVisible = ref(false)
const uploadRef = ref(null)
const uploadFileList = ref([])
const uploadForm = ref({ title: '', department_id: null })
const uploading = ref(false)
const uploadProgress = ref(0)

// 预览
const previewVisible = ref(false)
const previewDoc = ref(null)
const previewTitle = computed(() =>
  previewDoc.value ? `原文预览：${previewDoc.value.title}` : '原文预览')

// 改部门
const deptVisible = ref(false)
const deptDoc = ref(null)
const newDeptId = ref(null)
const deptLoading = ref(false)

async function fetchList() {
  loading.value = true
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
      status: filters.value.status || undefined,
      department_id: filters.value.department_id ?? undefined,
      file_type: filters.value.file_type || undefined,
      source: filters.value.source || undefined,
      is_featured: filters.value.is_featured ?? undefined,
    }
    const res = await adminApi.documents(params)
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

/** 重点标记切换 */
async function toggleFeatured(row) {
  try {
    await adminApi.patchDocument(row.id, { is_featured: !row.is_featured })
    row.is_featured = !row.is_featured
    ElMessage.success(row.is_featured ? '已标记为重点' : '已取消重点')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

/** 下架 / 重新上架 */
async function toggleStatus(row, status) {
  const label = status === 'offline' ? '下架' : '重新上架'
  try {
    await ElMessageBox.confirm(`确定${label}「${row.title}」吗？`, `${label}确认`, { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await adminApi.patchDocument(row.id, { status })
    ElMessage.success(`已${label}`)
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

/** 重新入库（failed / offline） */
async function handleReprocess(row) {
  try {
    await ElMessageBox.confirm(`确定对「${row.title}」重新入库吗？`, '重新入库', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await adminApi.reprocess(row.id)
    ElMessage.success('已触发重新入库')
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function openPreview(row) {
  previewDoc.value = row
  previewVisible.value = true
}

/** 更多操作：改部门 / 删除 */
function onMoreCommand(cmd, row) {
  if (cmd === 'dept') {
    deptDoc.value = row
    newDeptId.value = row.department_id
    deptVisible.value = true
  } else if (cmd === 'delete') {
    handleDelete(row)
  }
}

async function submitChangeDept() {
  deptLoading.value = true
  try {
    await adminApi.patchDocument(deptDoc.value.id, { department_id: newDeptId.value })
    ElMessage.success('部门已更新')
    deptVisible.value = false
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    deptLoading.value = false
  }
}

/** 删除（确认弹窗） */
async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.title}」吗？删除后文件将被移除且不可恢复。`,
      '删除确认',
      { type: 'error', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' }
    )
  } catch (e) {
    return
  }
  try {
    await adminApi.deleteDocument(row.id)
    ElMessage.success('已删除')
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

// ---------------- 上传 ----------------
function onFileChange(file, fileList) {
  uploadFileList.value = fileList
}
function onFileRemove(_file, fileList) {
  uploadFileList.value = fileList
}

/** 自定义上传：带进度 POST /admin/documents/upload */
async function customUpload(options) {
  const fd = new FormData()
  fd.append('file', options.file)
  if (uploadForm.value.title.trim()) fd.append('title', uploadForm.value.title.trim())
  if (uploadForm.value.department_id != null) fd.append('department_id', uploadForm.value.department_id)
  uploadProgress.value = 0
  try {
    const res = await http.post('/admin/documents/upload', fd, {
      onUploadProgress: (e) => {
        if (e.total) uploadProgress.value = Math.round((e.loaded / e.total) * 100)
      },
    })
    const doc = res.data?.data
    ElMessage.success(`「${doc?.title || options.file.name}」上传成功`)
    return doc
  } catch (e) {
    // 失败信息由拦截器弹出，向上抛以停止剩余文件
    throw e
  }
}

/** 提交文件列表（逐文件上传） */
async function submitUpload() {
  uploading.value = true
  uploadProgress.value = 0
  let ok = 0
  let failed = 0
  const files = uploadFileList.value.slice()
  for (const f of files) {
    try {
      await customUpload({ file: f.raw })
      ok += 1
    } catch (e) {
      failed += 1
    }
  }
  uploading.value = false
  if (failed) {
    ElMessage.warning(`上传完成：成功 ${ok} 个，失败 ${failed} 个`)
  } else {
    ElMessage.success(`全部上传成功（${ok} 个）`)
  }
  uploadVisible.value = false
  uploadFileList.value = []
  uploadForm.value = { title: '', department_id: null }
  page.value = 1
  fetchList()
}

/** 拉取部门列表 */
async function fetchDepartments() {
  try {
    departments.value = (await authApi.departments()) || []
    if (isDeptAdmin.value && userStore.user?.department_id != null) {
      // 部门管理员默认上传到本部门
      uploadForm.value.department_id = userStore.user.department_id
    }
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

.doc-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--ink-900);
}

.star {
  color: var(--warn);
  flex-shrink: 0;
}

.doc-file {
  font-size: 12px;
  margin-top: 2px;
}

.muted {
  color: var(--ink-400);
}

.star-on {
  color: var(--warn);
}

.star-off {
  color: var(--ink-400);
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

.form-tip {
  font-size: 12px;
  color: var(--ink-400);
  line-height: 1.6;
  width: 100%;
}
</style>
