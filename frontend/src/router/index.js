import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'
import UserLayout from '@/layout/UserLayout.vue'
import AdminLayout from '@/layout/AdminLayout.vue'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/change-password',
    name: 'change-password',
    component: () => import('@/views/ChangePassword.vue'),
  },
  {
    path: '/',
    component: UserLayout,
    redirect: '/search',
    children: [
      { path: 'search', name: 'search', component: () => import('@/views/user/Search.vue') },
      { path: 'documents/:id', name: 'document-detail', component: () => import('@/views/user/DocumentDetail.vue') },
      { path: 'favorites', name: 'favorites', component: () => import('@/views/user/Favorites.vue') },
      { path: 'qa', name: 'qa', component: () => import('@/views/user/QA.vue') },
      { path: 'my-uploads', name: 'my-uploads', component: () => import('@/views/user/MyUploads.vue') },
      { path: 'notifications', name: 'notifications', component: () => import('@/views/user/Notifications.vue') },
    ],
  },
  // 管理端（Sprint 5b）：admin / dept_admin，默认跳工作台
  {
    path: '/admin',
    component: AdminLayout,
    redirect: '/admin/dashboard',
    meta: { roles: ['admin', 'dept_admin'] },
    children: [
      { path: 'dashboard', name: 'admin-dashboard', component: () => import('@/views/admin/Dashboard.vue') },
      { path: 'approvals', name: 'admin-approvals', component: () => import('@/views/admin/Approvals.vue') },
      { path: 'documents', name: 'admin-documents', component: () => import('@/views/admin/Documents.vue') },
      { path: 'crawl-tasks', name: 'admin-crawl-tasks', component: () => import('@/views/admin/CrawlTasks.vue'), meta: { roles: ['admin'] } },
      { path: 'users', name: 'admin-users', component: () => import('@/views/admin/Users.vue'), meta: { roles: ['admin'] } },
      { path: 'departments', name: 'admin-departments', component: () => import('@/views/admin/Departments.vue'), meta: { roles: ['admin'] } },
      { path: 'push', name: 'admin-push', component: () => import('@/views/admin/Push.vue') },
      { path: 'audit-logs', name: 'admin-audit-logs', component: () => import('@/views/admin/AuditLogs.vue'), meta: { roles: ['admin'] } },
    ],
  },
  // 兜底：未知路径回首页
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：
// 1) 未登录 → /login（带 redirect 回跳参数）
// 2) 已登录访问 /login → 重定向 /
// 3) 管理端 /admin 前缀页面仅 admin / dept_admin 可访问（5b 使用）
router.beforeEach(async (to) => {
  const userStore = useUserStore()

  if (!userStore.isLoggedIn) {
    if (to.meta.public) return true
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  if (to.path === '/login') {
    return { path: '/' }
  }

  // 刷新后恢复用户信息（失败时 401 由 http 拦截器统一处理）
  if (!userStore.user) {
    try {
      await userStore.fetchMe()
    } catch (e) {
      /* 拦截器已提示并处理 */
    }
  }

  // F1 首登强制改密：完成改密前除 /change-password、/login 外一律跳转改密页。
  // D3：本地快照为 true 时，在关键导航上重新向 /auth/me 确认，避免 A 标签页
  // 已改密、B 标签页仍被旧快照强制跳转 /change-password。
  if (userStore.user?.must_change_password && to.path !== '/change-password') {
    try {
      await userStore.fetchMe()
    } catch (e) {
      /* /auth/me 失败时保持旧快照兜底（宁可多改一次，也不放行业务） */
    }
    if (userStore.user?.must_change_password && to.path !== '/change-password') {
      return { path: '/change-password' }
    }
  }

  if (to.path.startsWith('/admin')) {
    // 非管理端角色 → 回用户端
    if (!userStore.isAdmin) return { path: '/' }
    // admin 专属子页面（用户管理/审计日志）对 dept_admin 隐藏
    if (to.meta.roles && !to.meta.roles.includes(userStore.role)) {
      return { path: '/admin/dashboard' }
    }
  }
  return true
})

export default router
