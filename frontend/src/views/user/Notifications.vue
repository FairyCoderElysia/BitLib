<template>
  <div class="page-container">
    <div class="notif-head">
      <h3 class="page-title" style="margin-bottom: 0">通知中心</h3>
      <el-badge :value="unreadCount" :hidden="unreadCount <= 0" style="margin-right: auto; margin-left: 10px" />
      <el-button size="small" type="primary" plain :disabled="unreadCount <= 0" @click="handleReadAll">
        全部已读
      </el-button>
    </div>

    <div v-loading="loading" class="notif-list">
      <el-empty v-if="!loading && !items.length" description="暂无通知" />

      <template v-else>
        <div
          v-for="n in items"
          :key="n.id"
          class="notif-item"
          :class="{ unread: !n.is_read }"
          @click="handleClick(n)"
        >
          <span class="unread-dot" v-if="!n.is_read" />
          <div class="notif-main">
            <div class="notif-title">
              {{ n.title }}
              <el-tag v-if="!n.is_read" type="danger" size="small" effect="light">未读</el-tag>
            </div>
            <p class="notif-content">{{ n.content }}</p>
            <div class="notif-meta">
              <span>{{ formatTime(n.created_at) }}</span>
              <span v-if="n.document_id" class="doc-link">关联文档可查看</span>
            </div>
          </div>
          <el-button v-if="!n.is_read" size="small" @click.stop="handleRead(n)">标为已读</el-button>
        </div>

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
import { ElMessage } from 'element-plus'
import { notifApi } from '@/api/modules'
import { formatTime } from '@/utils/format'

const router = useRouter()

const items = ref([])
const total = ref(0)
const unreadCount = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)

async function fetchList() {
  loading.value = true
  try {
    const res = await notifApi.list({ page: page.value, page_size: pageSize.value })
    items.value = res.items || []
    total.value = res.total || 0
    unreadCount.value = res.unread_count || 0
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

/** 点击：未读先标已读，有关联文档则跳转详情 */
async function handleClick(n) {
  if (!n.is_read) {
    await markRead(n)
  }
  if (n.document_id) {
    router.push(`/documents/${n.document_id}`)
  }
}

async function markRead(n) {
  try {
    await notifApi.markRead(n.id)
    n.is_read = true
    unreadCount.value = Math.max(0, unreadCount.value - 1)
    window.dispatchEvent(new CustomEvent('notif-changed')) // 通知顶栏角标立即刷新（修复#2）
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function handleRead(n) {
  await markRead(n)
  if (n.document_id) router.push(`/documents/${n.document_id}`)
}

async function handleReadAll() {
  try {
    const res = await notifApi.markAllRead()
    await fetchList()
    window.dispatchEvent(new CustomEvent('notif-changed')) // 通知顶栏角标立即刷新（修复#2）
    ElMessage.success(`已将 ${res.marked || 0} 条通知标记为已读`)
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function onPageChange(p) {
  page.value = p
  fetchList()
}

onMounted(fetchList)
</script>

<style scoped>
.notif-head {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}

.notif-list {
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 8px 20px;
  min-height: 200px;
}

.notif-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 6px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
}

.notif-item.unread {
  background: var(--brand-weak);
}

.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--danger);
  margin-top: 8px;
  flex-shrink: 0;
}

.notif-main {
  flex: 1;
  min-width: 0;
}

.notif-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-900);
}

.notif-content {
  margin: 6px 0;
  font-size: 13px;
  color: var(--ink-600);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.notif-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--ink-400);
}

.doc-link {
  color: var(--brand);
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  padding: 16px 0;
}
</style>
