<template>
  <div class="admin-layout">
    <!-- 左侧菜单 -->
    <aside class="sidebar">
      <div class="brand" @click="router.push('/admin/dashboard')">
        <el-icon :size="22"><DataBoard /></el-icon>
        <span>资料库管理后台</span>
      </div>

      <el-menu
        class="side-menu"
        :default-active="route.path"
        router
        background-color="transparent"
      >
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.title }}</span>
        </el-menu-item>
      </el-menu>
    </aside>

    <!-- 右侧内容 -->
    <div class="main">
      <!-- 顶栏：面包屑 / 返回用户端 / 用户菜单 -->
      <header class="topbar">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item>管理后台</el-breadcrumb-item>
          <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
        </el-breadcrumb>

        <div class="topbar-actions">
          <el-button text :icon="House" @click="router.push('/')">返回用户端</el-button>
          <el-dropdown trigger="click" @command="onCommand">
            <span class="user-trigger">
              <el-icon><User /></el-icon>
              <span class="username">{{ userStore.displayName }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>角色：{{ roleText }}</el-dropdown-item>
                <el-dropdown-item command="change-password" :icon="Lock">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import {
  Odometer, Stamp, FolderOpened, Promotion, User, Bell, Document,
  DataBoard, House, ArrowDown, OfficeBuilding, Lock,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const roleText = computed(() => {
  const map = { admin: '管理员', dept_admin: '部门管理员', user: '普通用户' }
  return map[userStore.role] || userStore.role
})

// 全部菜单；dept_admin 隐藏「用户管理 / 审计日志」（spec §2.2）
const ALL_MENUS = [
  { path: '/admin/dashboard', title: '工作台', icon: Odometer },
  { path: '/admin/approvals', title: '审批中心', icon: Stamp },
  { path: '/admin/documents', title: '文档管理', icon: FolderOpened },
  { path: '/admin/crawl-tasks', title: '爬虫任务', icon: Promotion },
  { path: '/admin/users', title: '用户管理', icon: User, adminOnly: true },
  { path: '/admin/departments', title: '部门管理', icon: OfficeBuilding, adminOnly: true },
  { path: '/admin/push', title: '部门推送', icon: Bell },
  { path: '/admin/audit-logs', title: '审计日志', icon: Document, adminOnly: true },
]

const menus = computed(() =>
  ALL_MENUS.filter((m) => !m.adminOnly || userStore.role === 'admin'))

const currentTitle = computed(() => {
  const m = menus.value.find((x) => route.path.startsWith(x.path))
  return m ? m.title : ''
})

function onCommand(cmd) {
  if (cmd === 'change-password') {
    router.push('/change-password')
  } else if (cmd === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.admin-layout {
  display: flex;
  min-height: 100vh;
  background: var(--fill);
}

/* ---------------- 侧栏 ---------------- */
.sidebar {
  width: 216px;
  flex-shrink: 0;
  background: var(--ink-900);
  color: #fff;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 18px;
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  cursor: pointer;
  border-bottom: 1px solid rgb(255 255 255 / 0.08);
  white-space: nowrap;
}

.side-menu {
  flex: 1;
  padding: 8px;
  border-right: none;
  overflow-y: auto;
}

/* 深色侧栏菜单：Element 深色模式下的文字颜色统一由 token 强调 */
.side-menu :deep(.el-menu-item) {
  color: rgb(255 255 255 / 0.72);
  border-radius: var(--radius);
  margin-bottom: 2px;
}

.side-menu :deep(.el-menu-item:hover) {
  background: rgb(255 255 255 / 0.08);
  color: #fff;
}

.side-menu :deep(.el-menu-item.is-active) {
  background: var(--brand);
  color: #fff;
}

/* ---------------- 顶栏 ---------------- */
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 20px;
  background: var(--card);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 100;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
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
  padding: 20px;
  min-width: 0;
}
</style>
