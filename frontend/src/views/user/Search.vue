<template>
  <div class="page-container">
    <!-- 检索区 -->
    <div class="search-area">
      <div class="search-bar">
        <el-autocomplete
          v-model="keyword"
          size="large"
          class="search-autocomplete"
          placeholder="输入关键词搜索文档（支持标题 / 内容全文检索）"
          clearable
          :fetch-suggestions="fetchSuggestions"
          @select="onSelectCandidate"
          @keyup.enter="onSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-autocomplete>
        <el-button type="primary" size="large" :loading="loading" @click="onSearch">
          搜索
        </el-button>
        <el-button size="large" plain :icon="Upload" @click="uploadVisible = true">
          上传文档
        </el-button>
      </div>

      <!-- 热门搜索（空态隐藏） -->
      <div v-if="hotWords.length" class="hot-words">
        <span class="hot-label">热门搜索</span>
        <el-tag
          v-for="w in hotWords"
          :key="w"
          class="hot-tag"
          effect="plain"
          size="small"
          @click="onHotWordClick(w)"
        >{{ w }}</el-tag>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <el-select v-model="fileType" placeholder="格式" clearable style="width: 120px">
          <el-option label="TXT" value="txt" />
          <el-option label="DOCX" value="docx" />
          <el-option label="PDF" value="pdf" />
          <el-option label="MD" value="md" />
        </el-select>
        <el-select v-model="source" placeholder="来源" clearable style="width: 140px">
          <el-option label="用户上传" value="upload" />
          <el-option label="爬虫抓取" value="crawl" />
        </el-select>
        <el-radio-group v-model="sort">
          <el-radio-button value="relevance">按相关度</el-radio-button>
          <el-radio-button value="time">按时间</el-radio-button>
        </el-radio-group>
        <span v-if="total" class="total-text">共 {{ total }} 条结果</span>
      </div>
    </div>

    <!-- 结果列表 -->
    <div v-loading="loading" class="result-area">
      <el-empty v-if="!loading && !items.length" description="没有找到相关文档，换个关键词试试" />

      <template v-else>
        <div
          v-for="doc in items"
          :key="doc.id"
          class="result-card"
          @click="goDetail(doc.id)"
        >
          <div class="card-head">
            <!-- 标题（关键词高亮） -->
            <span class="title" v-html="highlight(doc.title, keyword)" />
            <!-- 重点星标 -->
            <el-tag v-if="doc.is_featured" type="warning" effect="dark" size="small">
              <el-icon><StarFilled /></el-icon>&nbsp;重点
            </el-tag>
          </div>

          <!-- 摘要（关键词高亮） -->
          <p class="snippet" v-html="highlight(doc.snippet, keyword)" />

          <div class="card-meta">
            <el-tag :type="FILE_TYPE_TAG[doc.file_type] || 'info'" size="small">
              {{ FILE_TYPE_LABEL[doc.file_type] || doc.file_type }}
            </el-tag>
            <el-tag size="small" effect="plain">
              {{ SOURCE_LABEL[doc.source] || doc.source }}
            </el-tag>
            <span class="meta-item">{{ formatSize(doc.file_size) }}</span>
            <span class="meta-item">{{ formatTime(doc.created_at) }}</span>
            <span v-if="doc.score != null" class="meta-item score">
              相关度 {{ doc.score }}
            </span>
          </div>
        </div>

        <!-- 分页 -->
        <div class="pagination-wrap" v-if="total > pageSize">
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

    <!-- 上传文档弹窗（普通用户上传走审批） -->
    <el-dialog v-model="uploadVisible" title="上传文档（提交审批）" width="500px" destroy-on-close>
      <el-form label-width="70px">
        <el-form-item label="文件">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            :limit="1"
            accept=".txt,.md,.pdf,.docx"
            :on-change="onFileSelect"
            :on-exceed="onFileExceed"
            :on-remove="onFileRemove"
          >
            <el-button :icon="UploadFilled">选择文件</el-button>
          </el-upload>
          <div v-if="selectedFile" class="file-name">已选择：{{ selectedFile.name }}</div>
          <div class="form-tip">支持 txt / md / pdf / docx；提交后需管理员审批，通过后即可检索与预览</div>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="uploadTitle" placeholder="留空则取文件名" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!selectedFile" @click="submitUpload">
          提交审批
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Upload, UploadFilled } from '@element-plus/icons-vue'
import { searchApi, docApi } from '@/api/modules'
import { FILE_TYPE_LABEL, FILE_TYPE_TAG, SOURCE_LABEL, formatSize, formatTime } from '@/utils/format'
import { highlight } from '@/utils/highlight'

const route = useRoute()
const router = useRouter()

const keyword = ref(route.query.q || '')
const fileType = ref('')
const source = ref('')
const sort = ref('relevance')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const items = ref([])
const loading = ref(false)

// 热门搜索（F20）：空态/失败时隐藏，不打扰
const hotWords = ref([])

// 联想竞态防护：递增请求序号，仅采纳最后一次发起请求的响应
let suggestSeq = 0
let suggestTimer = null

// 上传文档（走审批）
const uploadVisible = ref(false)
const selectedFile = ref(null)
const uploadTitle = ref('')
const uploading = ref(false)

// 顶栏搜索跳转 /search?q=xxx 时同步关键词并重新检索
watch(
  () => route.query.q,
  (q) => {
    if (q !== undefined && q !== keyword.value) {
      keyword.value = q
      page.value = 1
      fetchList()
    }
  }
)

// 筛选 / 排序变化重置到第一页
watch([fileType, source, sort], () => {
  page.value = 1
  fetchList()
})

async function fetchList() {
  const q = keyword.value.trim()
  if (!q) {
    items.value = []
    total.value = 0
    return
  }
  loading.value = true
  try {
    const res = await searchApi.search({
      q,
      page: page.value,
      page_size: pageSize.value,
      file_type: fileType.value || undefined,
      source: source.value || undefined,
      sort: sort.value,
    })
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  fetchList()
}

function onPageChange(p) {
  page.value = p
  fetchList()
}

function goDetail(id) {
  router.push(`/documents/${id}`)
}

// ---------------- 热门搜索 + 输入联想（F20） ----------------
async function loadHotWords() {
  try {
    const res = await searchApi.hotWords()
    hotWords.value = res.items || []
  } catch (e) {
    hotWords.value = [] // 加载失败静默隐藏，不打扰
  }
}

function onHotWordClick(word) {
  keyword.value = word
  onSearch()
}

/** el-autocomplete 联想：300ms 防抖 + 递增序号竞态防护 */
function fetchSuggestions(queryString, callback) {
  const q = (queryString || '').trim()
  clearTimeout(suggestTimer)
  if (!q) {
    suggestSeq++ // 使在途响应过期（Evaluator 发现：清空输入竞态）
    callback([])
    return
  }
  const seq = ++suggestSeq
  suggestTimer = setTimeout(async () => {
    try {
      const res = await searchApi.suggest(q)
      if (seq !== suggestSeq) return // 已发起新请求，丢弃过期响应
      callback((res.items || []).map((it) => ({
        id: it.id,
        title: it.title,
        value: it.title, // el-autocomplete 下拉展示字段
      })))
    } catch (e) {
      if (seq === suggestSeq) callback([])
    }
  }, 300)
}

function onSelectCandidate(item) {
  keyword.value = item.title
  onSearch()
}

// URL 直接带 q 进入时自动检索
onMounted(() => {
  loadHotWords()
  if (keyword.value.trim()) fetchList()
})

// ---------------- 上传文档 ----------------
function onFileSelect(file) {
  selectedFile.value = file
}

function onFileExceed() {
  ElMessage.warning('一次只能选择 1 个文件')
}

function onFileRemove() {
  selectedFile.value = null
}

/** 普通用户上传：POST /documents/upload → pending 待审批 */
async function submitUpload() {
  if (!selectedFile.value) return
  uploading.value = true
  const fd = new FormData()
  fd.append('file', selectedFile.value.raw)
  if (uploadTitle.value.trim()) fd.append('title', uploadTitle.value.trim())
  try {
    await docApi.upload(fd)
    ElMessage.success('上传成功，已提交审批')
    uploadVisible.value = false
    selectedFile.value = null
    uploadTitle.value = ''
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.search-area {
  background: var(--card);
  border-radius: var(--radius);
  padding: 24px;
  box-shadow: var(--shadow-sm);
}

.search-bar {
  display: flex;
  gap: 12px;
}

.search-autocomplete {
  flex: 1;
}

.hot-words {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.hot-label {
  font-size: 12px;
  color: var(--ink-400);
}

.hot-tag {
  cursor: pointer;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.total-text {
  font-size: 13px;
  color: var(--ink-400);
  margin-left: auto;
}

.result-area {
  margin-top: 16px;
  min-height: 200px;
}

.result-card {
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px 20px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: box-shadow 0.2s;
  box-shadow: var(--shadow-sm);
}

.result-card:hover {
  box-shadow: var(--shadow-md);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title {
  font-size: 16px;
  font-weight: 600;
  color: var(--brand);
}

.snippet {
  margin: 8px 0;
  font-size: 13px;
  color: var(--ink-600);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.meta-item {
  font-size: 12px;
  color: var(--ink-400);
}

.score {
  color: var(--warn);
}

.file-name {
  font-size: 13px;
  color: var(--ink-600);
  margin-top: 6px;
}

.form-tip {
  font-size: 12px;
  color: var(--ink-400);
  line-height: 1.6;
  margin-top: 4px;
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 20px;
}
</style>
