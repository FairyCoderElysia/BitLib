<template>
  <div class="page-container detail-wrap">
    <div v-loading="loading">
      <!-- 未加载完成不渲染 -->
      <template v-if="detail">
        <!-- 标题与操作 -->
        <div class="head">
          <div class="head-left">
            <h1 class="doc-title">{{ detail.title }}</h1>
            <div class="tags">
              <el-tag :type="DOC_STATUS_TAG[detail.status] || 'info'" size="small">
                {{ DOC_STATUS_LABEL[detail.status] || detail.status }}
              </el-tag>
              <el-tag :type="FILE_TYPE_TAG[detail.file_type] || 'info'" size="small">
                {{ FILE_TYPE_LABEL[detail.file_type] || detail.file_type }}
              </el-tag>
              <el-tag size="small" effect="plain">
                {{ SOURCE_LABEL[detail.source] || detail.source }}
              </el-tag>
              <el-tag v-if="detail.is_featured" type="warning" effect="dark" size="small">
                <el-icon><StarFilled /></el-icon>&nbsp;重点
              </el-tag>
            </div>
          </div>

          <div class="head-actions">
            <!-- 下载：仅 approved -->
            <el-button
              type="primary"
              :icon="Download"
              :disabled="detail.status !== 'approved'"
              :loading="downloading"
              @click="handleDownload"
            >
              下载
            </el-button>

            <!-- 收藏 / 取消收藏 -->
            <el-button
              v-if="isFavorited"
              type="warning"
              plain
              @click="handleRemoveFavorite"
            >
              <el-icon><StarFilled /></el-icon>&nbsp;已收藏
            </el-button>
            <el-popover v-else v-model="favVisible" trigger="click" width="300" :teleported="true">
              <template #reference>
                <el-button plain>
                  <el-icon><Star /></el-icon>&nbsp;收藏
                </el-button>
              </template>
              <div class="fav-pop">
                <el-select
                  v-model="favFolderId"
                  placeholder="选择收藏夹（可不选）"
                  clearable
                  style="width: 100%"
                >
                  <el-option v-for="f in folders" :key="f.id" :label="f.name" :value="f.id" />
                </el-select>
                <el-input
                  v-model="newFolderName"
                  placeholder="或输入新收藏夹名称"
                  clearable
                  style="margin-top: 8px"
                />
                <div class="fav-actions">
                  <el-button size="small" @click="favVisible = false">取消</el-button>
                  <el-button size="small" type="primary" :loading="favLoading" @click="handleFavorite">
                    确定收藏
                  </el-button>
                </div>
              </div>
            </el-popover>
          </div>
        </div>

        <!-- 元信息 -->
        <el-descriptions class="meta" :column="4" border>
          <el-descriptions-item label="文件名">{{ detail.file_name }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{ formatSize(detail.file_size) }}</el-descriptions-item>
          <el-descriptions-item label="所属部门">{{ detail.department_name || '公开' }}</el-descriptions-item>
          <el-descriptions-item label="上传时间">{{ formatTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.reject_reason" label="拒绝原因" :span="4">
            <span class="reject-reason">{{ detail.reject_reason }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 文档摘要（F17）：approved 且有摘要时展示（pending/rejected 不渲染） -->
        <div v-if="detail.status === 'approved' && detail.summary" class="preview summary-block">
          <div class="preview-title">
            <el-icon><Document /></el-icon>&nbsp;文档摘要
          </div>
          <p class="summary-text">{{ detail.summary }}</p>
        </div>

        <!-- 预览区 -->
        <div class="preview">
          <div class="preview-title">
            <el-icon><View /></el-icon>&nbsp;原文预览
          </div>

          <!-- txt / md：直显文本 -->
          <pre v-if="canPreviewText" class="preview-text">{{ detail.content_text }}</pre>

          <!-- pdf / docx：走 5b 在线预览组件（pdf.js / docx-preview） -->
          <PreviewDocument
            v-else-if="detail.status !== 'offline' && ['pdf', 'docx'].includes(detail.file_type)"
            :document-id="documentId"
            :file-type="detail.file_type"
            :title="detail.title"
          />

          <!-- 非 approved / 无预览权限 -->
          <el-empty v-else-if="detail.status !== 'approved'" description="文档当前不可预览（仅已通过审批的文档可预览）" />
        </div>

        <!-- 相关推荐（F18）：approved 请求中显示 loading；有推荐时展示，点击跳转目标详情 -->
        <div v-if="related.length || relatedLoading" class="preview related-block" v-loading="relatedLoading">
          <div class="preview-title">
            <el-icon><Connection /></el-icon>&nbsp;相关推荐
          </div>
          <div class="related-list">
            <div v-for="item in related" :key="item.id" class="related-card" @click="goDoc(item.id)">
              <div class="related-line">
                <span class="related-title">{{ item.title }}</span>
                <el-tag :type="FILE_TYPE_TAG[item.file_type] || 'info'" size="small">
                  {{ FILE_TYPE_LABEL[item.file_type] || item.file_type }}
                </el-tag>
              </div>
              <p class="related-summary">{{ item.summary }}</p>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Download, Document, Star, StarFilled, View } from '@element-plus/icons-vue'
import { docApi, favApi } from '@/api/modules'
import PreviewDocument from '@/components/PreviewDocument.vue'
import { FILE_TYPE_LABEL, FILE_TYPE_TAG, SOURCE_LABEL, DOC_STATUS_LABEL, DOC_STATUS_TAG, formatSize, formatTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const documentId = ref(Number(route.params.id))
let detailSeq = 0  // 请求序号：防快速连点推荐卡片时旧响应覆盖新详情（Evaluator eval-9 发现）

const detail = ref(null)
const loading = ref(false)
const downloading = ref(false)
const favLoading = ref(false)

// F18：相关推荐（仅 approved 请求，失败静默保持空）
const related = ref([])
const relatedLoading = ref(false)

// 收藏相关
const folders = ref([])
const favFolderId = ref(null)
const newFolderName = ref('')
const favVisible = ref(false)
// 当前文档的收藏条目 id（存在即已收藏）
const favoriteId = ref(null)

const isFavorited = computed(() => favoriteId.value != null)

// txt / md 且已通过审批 → 直显文本
const canPreviewText = computed(
  () => detail.value?.status === 'approved'
    && ['txt', 'md'].includes(detail.value.file_type)
    && detail.value.content_text != null
)

async function fetchDetail() {
  const seq = ++detailSeq
  loading.value = true
  try {
    const d = await docApi.detail(documentId.value)
    if (seq !== detailSeq) return // 过期响应丢弃（快速切换文档时）
    detail.value = d
    fetchRelated()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    if (seq === detailSeq) loading.value = false
  }
}

/** 相关推荐：仅 approved 请求；失败静默，不影响详情区 */
async function fetchRelated() {
  related.value = []
  if (detail.value?.status !== 'approved') return
  relatedLoading.value = true
  try {
    const items = await docApi.related(documentId.value)
    related.value = Array.isArray(items) ? items : []
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    relatedLoading.value = false
  }
}

/** 点击推荐卡片 → 跳转目标文档详情（watch 路由触发重载） */
function goDoc(id) {
  router.push({ name: 'document-detail', params: { id } })
}

/** 加载收藏夹与当前文档是否已收藏 */
async function fetchFavState() {
  try {
    const [folderRes, favRes] = await Promise.all([
      favApi.listFolders(),
      favApi.listFavorites(),
    ])
    folders.value = folderRes.items || []
    const hit = (favRes.items || []).find((f) => f.document.id === documentId.value)
    favoriteId.value = hit ? hit.id : null
  } catch (e) {
    /* 忽略 */
  }
}

/** 下载：axios blob → 触发浏览器保存 */
async function handleDownload() {
  downloading.value = true
  try {
    const res = await docApi.download(documentId.value)
    const blob = res.data
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = detail.value?.file_name || `document-${documentId.value}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('下载已开始')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    downloading.value = false
  }
}

/** 收藏：新建夹（可选）→ POST /favorites */
async function handleFavorite() {
  favLoading.value = true
  try {
    let folderId = favFolderId.value
    const name = newFolderName.value.trim()
    if (name) {
      const created = await favApi.createFolder(name)
      folderId = created.id
      folders.value.push(created)
    }
    const res = await favApi.addFavorite({ document_id: documentId.value, folder_id: folderId })
    favoriteId.value = res.id
    favVisible.value = false
    favFolderId.value = null
    newFolderName.value = ''
    ElMessage.success('收藏成功')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    favLoading.value = false
  }
}

/** 取消收藏 */
async function handleRemoveFavorite() {
  try {
    await ElMessageBox.confirm('确定取消收藏该文档吗？', '提示', { type: 'warning' })
  } catch (e) {
    return // 用户取消
  }
  try {
    await favApi.removeFavorite(documentId.value)
    favoriteId.value = null
    ElMessage.success('已取消收藏')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  fetchDetail()
  fetchFavState()
})

// F18 必要前置：详情页内跳转（如点击相关推荐）时路由参数变化，需重载详情/收藏/推荐
watch(() => route.params.id, (nid) => {
  documentId.value = Number(nid)
  fetchDetail()
  fetchFavState()
})
</script>

<style scoped>
.detail-wrap {
  max-width: 960px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.doc-title {
  margin: 0 0 8px;
  font-size: 22px;
  color: var(--ink-900);
}

.tags {
  display: flex;
  gap: 8px;
}

.head-actions {
  display: flex;
  gap: 10px;
}

.meta {
  margin-top: 16px;
}

.reject-reason {
  color: var(--danger);
}

/* F17：文档摘要区块（元信息下方、预览区上方） */
.summary-block {
  margin-top: 20px;
}

.summary-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.8;
  color: var(--ink-800);
  white-space: pre-wrap;
  word-break: break-word;
}

.preview {
  margin-top: 20px;
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}

.preview-title {
  display: flex;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}

.preview-text {
  margin: 0;
  padding: 16px;
  background: var(--fill-2);
  border-radius: var(--radius);
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-900);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 70vh;
  overflow: auto;
}

/* F18：相关推荐区块（预览区之后；复用 .preview 卡片，无渐变/动画） */
.related-block {
  margin-top: 20px;
}

.related-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.related-card {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  background: var(--card);
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.related-card:hover {
  border-color: var(--brand-border);
  background: var(--brand-weak);
}

.related-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.related-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-900);
}

.related-summary {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--ink-600);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fav-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
