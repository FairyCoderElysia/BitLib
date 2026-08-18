<template>
  <div class="page-container notif-detail-wrap">
    <div v-loading="loading" class="notif-detail-card">
      <template v-if="detail">
        <div class="notif-detail-head">
          <h3 class="notif-detail-title">{{ detail.title }}</h3>
          <el-tag :type="detail.is_read ? 'info' : 'danger'" size="small" effect="light">
            {{ detail.is_read ? '已读' : '未读' }}
          </el-tag>
        </div>

        <div class="notif-detail-meta">
          <el-icon><Clock /></el-icon>
          <span>发送时间：{{ formatTime(detail.created_at) }}</span>
        </div>

        <div class="notif-detail-content">{{ detail.content }}</div>

        <div class="notif-detail-actions">
          <el-button
            v-if="!detail.is_read"
            type="primary"
            :loading="marking"
            @click="markRead"
          >
            标为已读
          </el-button>
          <el-button
            v-if="detail.document_id"
            type="success"
            plain
            @click="goDoc"
          >
            查看关联文档
          </el-button>
          <el-button @click="goBack">返回列表</el-button>
        </div>
      </template>

      <el-empty v-else-if="!loading && loadError" description="通知不存在或无权访问">
        <el-button @click="goBack">返回列表</el-button>
      </el-empty>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Clock } from '@element-plus/icons-vue'
import { notifApi } from '@/api/modules'
import { formatTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const notificationId = ref(Number(route.params.id))
const detail = ref(null)
const loading = ref(false)
const marking = ref(false)
const loadError = ref(false)
let detailSeq = 0 // 请求序号：快速切换通知时丢弃过期响应（与 DocumentDetail 同款防护）

async function fetchDetail() {
  const seq = ++detailSeq
  loading.value = true
  loadError.value = false
  detail.value = null
  try {
    const n = await notifApi.detail(notificationId.value)
    if (seq !== detailSeq) return // 过期响应丢弃
    detail.value = n
  } catch (e) {
    if (seq !== detailSeq) return
    loadError.value = true
    /* 拦截器已提示具体错误 */
  } finally {
    if (seq === detailSeq) loading.value = false
  }
}

async function markRead() {
  if (!detail.value || detail.value.is_read) return
  marking.value = true
  try {
    await notifApi.markRead(detail.value.id)
    detail.value.is_read = true
    window.dispatchEvent(new CustomEvent('notif-changed')) // 通知顶栏角标立即刷新
    ElMessage.success('已标为已读')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    marking.value = false
  }
}

function goDoc() {
  if (detail.value?.document_id) {
    router.push(`/documents/${detail.value.document_id}`)
  }
}

function goBack() {
  router.push('/notifications')
}

// 详情页内路由参数变化时重新加载（复用组件场景）
watch(() => route.params.id, (nid) => {
  notificationId.value = Number(nid)
  fetchDetail()
})

fetchDetail()
</script>

<style scoped>
.notif-detail-wrap {
  max-width: 860px;
}

.notif-detail-card {
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 20px 24px;
  min-height: 220px;
}

.notif-detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.notif-detail-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--ink-900);
  line-height: 1.5;
  word-break: break-word;
}

.notif-detail-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 13px;
  color: var(--ink-400);
}

.notif-detail-content {
  margin-top: 18px;
  font-size: 14px;
  line-height: 1.9;
  color: var(--ink-800);
  white-space: pre-wrap;
  word-break: break-word;
}

.notif-detail-actions {
  display: flex;
  gap: 10px;
  margin-top: 24px;
}
</style>
