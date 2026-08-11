<template>
  <div class="page-container fav-wrap">
    <div class="fav-layout">
      <!-- 左：收藏夹列表 -->
      <div class="folder-panel">
        <h3 class="panel-title">我的收藏夹</h3>

        <div class="folder-create">
          <el-input
            v-model="newFolderName"
            size="small"
            placeholder="新建收藏夹"
            @keyup.enter="handleCreateFolder"
          >
            <template #append>
              <el-button :icon="Plus" @click="handleCreateFolder" />
            </template>
          </el-input>
        </div>

        <div
          class="folder-item"
          :class="{ active: activeFolder === null }"
          @click="activeFolder = null"
        >
          <span>全部收藏</span>
          <el-tag size="small" effect="plain">{{ favorites.length }}</el-tag>
        </div>

        <div
          v-for="f in folders"
          :key="f.id"
          class="folder-item"
          :class="{ active: activeFolder === f.id }"
          @click="activeFolder = f.id"
        >
          <span class="folder-name">{{ f.name }}</span>
          <el-tag size="small" effect="plain">{{ f.count }}</el-tag>
          <el-dropdown trigger="click" class="folder-ops" @click.stop>
            <el-icon class="more"><MoreFilled /></el-icon>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="handleRenameFolder(f)">重命名</el-dropdown-item>
                <el-dropdown-item divided @click="handleDeleteFolder(f)">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <!-- 右：文档列表 -->
      <div class="doc-panel" v-loading="loading">
        <div class="doc-panel-head">
          <h3 class="panel-title">{{ currentFolderTitle }}</h3>
          <!-- 批量下载（F19）：跨收藏夹保留选中，仅统计可下载项 -->
          <el-button
            type="primary"
            plain
            size="small"
            :disabled="!selectedIds.size"
            :loading="downloading"
            @click="handleBatchDownload"
          >
            批量下载（{{ selectedIds.size }}）
          </el-button>
        </div>

        <el-empty v-if="!loading && !filtered.length" description="暂无收藏文档" />

        <div v-for="item in filtered" :key="item.id" class="fav-card">
          <!-- 多选下载复选框（失效条目禁用且不可勾选；@click.stop 阻止触发卡片跳转） -->
          <el-checkbox
            class="select-check"
            :model-value="selectedIds.has(item.document_id ?? item.document?.id)"
            :disabled="!item.is_valid"
            @click.stop
            @change="(v) => onToggleSelect(item, v)"
          />
          <div v-if="!item.is_valid" class="fav-card-main fav-card-invalid">
            <div class="fav-card-title">
              文档已失效（已被下架或删除）
            </div>
          </div>
          <div v-else class="fav-card-main" @click="goDetail(item.document.id)">
            <div class="fav-card-title">
              {{ item.document.title }}
              <el-tag v-if="item.document.is_featured" type="warning" effect="dark" size="small">
                <el-icon><StarFilled /></el-icon>&nbsp;重点
              </el-tag>
            </div>
            <div class="fav-card-meta">
              <el-tag :type="FILE_TYPE_TAG[item.document.file_type] || 'info'" size="small">
                {{ FILE_TYPE_LABEL[item.document.file_type] || item.document.file_type }}
              </el-tag>
              <span>{{ formatSize(item.document.file_size) }}</span>
              <span>{{ formatTime(item.document.created_at) }}</span>
            </div>
          </div>
          <el-button
            size="small"
            type="danger"
            plain
            @click="handleRemove(item)"
          >
            取消收藏
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, MoreFilled } from '@element-plus/icons-vue'
import { favApi, docApi } from '@/api/modules'
import { FILE_TYPE_LABEL, FILE_TYPE_TAG, formatSize, formatTime } from '@/utils/format'
import { saveBlob, timestampedZipName } from '@/utils/download'

const router = useRouter()

const folders = ref([])
const favorites = ref([])
const activeFolder = ref(null) // null = 全部收藏
const newFolderName = ref('')
const loading = ref(false)

// 批量下载（F19）：选中集合跨收藏夹保留
const selectedIds = ref(new Set())
const downloading = ref(false)

const currentFolderTitle = computed(() => {
  if (activeFolder.value === null) return '全部收藏'
  const f = folders.value.find((x) => x.id === activeFolder.value)
  return f ? f.name : '全部收藏'
})

/** 按选中收藏夹过滤 */
const filtered = computed(() => {
  if (activeFolder.value === null) return favorites.value
  return favorites.value.filter((f) => f.folder_id === activeFolder.value)
})

async function fetchData() {
  loading.value = true
  try {
    const [folderRes, favRes] = await Promise.all([
      favApi.listFolders(),
      favApi.listFavorites(),
    ])
    folders.value = folderRes.items || []
    favorites.value = favRes.items || []
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function handleCreateFolder() {
  const name = newFolderName.value.trim()
  if (!name) return
  try {
    await favApi.createFolder(name)
    newFolderName.value = ''
    await fetchData()
    ElMessage.success('收藏夹已创建')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function handleRenameFolder(folder) {
  try {
    const { value } = await ElMessageBox.prompt('请输入新的收藏夹名称', '重命名收藏夹', {
      inputValue: folder.name,
      inputValidator: (v) => (v && v.trim() ? true : '名称不能为空'),
    })
    await favApi.renameFolder(folder.id, value.trim())
    await fetchData()
    ElMessage.success('已重命名')
  } catch (e) {
    /* 用户取消或请求失败 */
  }
}

async function handleDeleteFolder(folder) {
  try {
    await ElMessageBox.confirm(
      `删除收藏夹「${folder.name}」将同时移除夹内收藏，确定删除吗？`,
      '删除收藏夹',
      { type: 'warning' }
    )
  } catch (e) {
    return
  }
  try {
    await favApi.deleteFolder(folder.id)
    if (activeFolder.value === folder.id) activeFolder.value = null
    await fetchData()
    ElMessage.success('已删除')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function handleRemove(item) {
  const docId = item.document_id ?? item.document?.id
  const title = item.document?.title || '该文档'
  try {
    await ElMessageBox.confirm(`取消收藏「${title}」？`, '提示', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await favApi.removeFavorite(docId)
    await fetchData()
    ElMessage.success('已取消收藏')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function goDetail(id) {
  router.push(`/documents/${id}`)
}

// ---------------- 批量下载（F19） ----------------
function onToggleSelect(item, checked) {
  const docId = item.document_id ?? item.document?.id
  if (!item.is_valid || docId == null) return
  const next = new Set(selectedIds.value)
  if (checked) next.add(docId)
  else next.delete(docId)
  selectedIds.value = next
}

async function handleBatchDownload() {
  if (!selectedIds.value.size) return
  downloading.value = true
  try {
    const res = await docApi.batchDownload([...selectedIds.value])
    saveBlob(res.data, timestampedZipName())
    const skipped = Number(res.headers['x-skipped-count'] || 0)
    if (skipped > 0) {
      ElMessage.warning(`${skipped} 个文档不可下载，已自动剔除`)
    } else {
      ElMessage.success('批量下载已开始')
    }
    selectedIds.value = new Set() // 成功清空选中
  } catch (e) {
    /* 拦截器已提示；失败保留选中便于改选 */
  } finally {
    downloading.value = false
  }
}

onMounted(fetchData)
</script>

<style scoped>
.fav-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.folder-panel {
  width: 240px;
  flex-shrink: 0;
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px;
  box-shadow: var(--shadow-sm);
}

.panel-title {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.folder-create {
  margin-bottom: 10px;
}

.folder-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--ink-600);
}

.folder-item:hover {
  background: var(--fill-2);
}

.folder-item.active {
  background: var(--brand-weak);
  color: var(--brand);
}

.folder-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.folder-ops {
  display: none;
}

.folder-item:hover .folder-ops {
  display: inline-flex;
}

.more {
  color: var(--ink-400);
}

.doc-panel {
  flex: 1;
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px 20px;
  min-height: 400px;
  box-shadow: var(--shadow-sm);
}

.doc-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.doc-panel-head .panel-title {
  margin: 0;
}

.select-check {
  flex-shrink: 0;
}

.fav-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 8px;
  border-bottom: 1px solid var(--line);
}

.fav-card-main {
  flex: 1;
  cursor: pointer;
  min-width: 0;
}

.fav-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-900);
}

.fav-card-main:hover .fav-card-title {
  color: var(--brand);
}

/* 已失效收藏条目（下架/删除的文档） */
.fav-card-invalid {
  cursor: default;
  color: var(--ink-400);
}
.fav-card-invalid .fav-card-title {
  color: var(--ink-400);
  font-weight: 400;
}

.fav-card-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-400);
}
</style>
