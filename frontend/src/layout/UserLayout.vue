<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="brand" @click="router.push('/')">
        <el-icon :size="22"><Document /></el-icon>
        <span>企业资料库</span>
      </div>

      <!-- 全局搜索框：回车跳转检索页 -->
      <div class="search-box">
        <el-input
          v-model="keyword"
          placeholder="搜索文档标题 / 内容…"
          clearable
          @keyup.enter="goSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>

      <div class="actions">
        <!-- AI 问答入口 -->
        <el-button text :icon="ChatDotRound" @click="router.push('/qa')">
          AI 问答
        </el-button>

        <!-- 管理后台入口（admin / dept_admin 可见） -->
        <el-button v-if="userStore.isAdmin" text :icon="DataBoard" @click="router.push('/admin/dashboard')">
          管理后台
        </el-button>

        <!-- 通知角标：未读数轮询刷新 -->
        <el-badge :value="unreadCount" :hidden="unreadCount <= 0" class="notif-badge">
          <el-button text circle title="通知中心" @click="router.push('/notifications')">
            <el-icon :size="20"><Bell /></el-icon>
          </el-button>
        </el-badge>

        <!-- 用户菜单 -->
        <el-dropdown trigger="click" @command="onCommand">
          <span class="user-trigger">
            <el-icon><User /></el-icon>
            <span class="username">{{ userStore.displayName }}</span>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>角色：{{ roleText }}</el-dropdown-item>
              <el-dropdown-item command="favorites" :icon="Star">我的收藏</el-dropdown-item>
              <el-dropdown-item command="my-uploads" :icon="UploadFilled">我的上传</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 内容区 -->
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DataBoard, ChatDotRound, Star, UploadFilled } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { notifApi } from '@/api/modules'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const keyword = ref(route.query.q || '')
const unreadCount = ref(0)
let timer = null

const roleText = computed(() => {
  const map = { admin: '管理员', dept_admin: '部门管理员', user: '普通用户' }
  return map[userStore.role] || userStore.role
})

/** 顶栏搜索跳转检索页 */
function goSearch() {
  const q = keyword.value.trim()
  router.push({ path: '/search', query: q ? { q } : {} })
}

/** 拉取未读通知数（GET /notifications?page_size=1 返回 unread_count） */
async function fetchUnread() {
  try {
    const res = await notifApi.list({ page: 1, page_size: 1 })
    unreadCount.value = res.unread_count || 0
  } catch (e) {
    /* 拦截器已提示 */
  }
}

function onCommand(cmd) {
  if (cmd === 'favorites') {
    router.push('/favorites')
  } else if (cmd === 'my-uploads') {
    router.push('/my-uploads')
  } else if (cmd === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}

/** 通知已读后由 Notifications 页派发事件，角标立即刷新（修复#2） */
function onNotifChanged() {
  fetchUnread()
}

onMounted(() => {
  fetchUnread()
  // 每 30 秒轮询刷新未读数
  timer = setInterval(fetchUnread, 30000)
  window.addEventListener('notif-changed', onNotifChanged)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('notif-changed', onNotifChanged)
})
</script>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  height: 56px;
  padding: 0 20px;
  background: var(--card);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 100;
}

.brand {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 18px;
  font-weight: 700;
  color: var(--brand);
  cursor: pointer;
  white-space: nowrap;
}

.search-box {
  flex: 1;
  max-width: 520px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}

.notif-badge :deep(.el-badge__content) {
  z-index: 10;
}

.user-trigger {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: var(--ink-600);
  outline: none;
}

.username {
  font-size: 14px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content {
  flex: 1;
}
</style>
