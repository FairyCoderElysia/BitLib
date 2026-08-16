import { defineStore } from 'pinia'
import { authApi } from '@/api/modules'

const TOKEN_KEY = 'token'
const USER_KEY = 'user'

/** 安全读取持久化的用户信息（损坏 JSON 时返回 null） */
function loadUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    return null
  }
}

// 用户状态：token / user 持久化到 localStorage，提供登录 / 登出 / 恢复会话
export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: loadUser(),
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    /** 角色：admin / dept_admin / user */
    role: (s) => s.user?.role || '',
    /** 管理端（admin 或部门管理员） */
    isAdmin: (s) => s.user?.role === 'admin' || s.user?.role === 'dept_admin',
    /** 首登强制改密标志（F1 修复） */
    mustChangePassword: (s) => !!s.user?.must_change_password,
    displayName: (s) => s.user?.username || '未登录',
  },
  actions: {
    /** 登录：保存 token + 用户信息 */
    async login(username, password) {
      const res = await authApi.login(username, password)
      this.token = res.token
      this.user = res.user
      localStorage.setItem(TOKEN_KEY, res.token)
      localStorage.setItem(USER_KEY, JSON.stringify(res.user))
      return res
    },
    /** 刷新用户信息（页面刷新后 token 仍在、user 丢失时调用） */
    async fetchMe() {
      if (!this.token) return null
      const user = await authApi.me()
      this.user = user
      localStorage.setItem(USER_KEY, JSON.stringify(user))
      return user
    },
    /** 自助修改密码（F1 修复）：成功后更新本地用户信息与强制改密标志 */
    async changePassword(oldPassword, newPassword) {
      const user = await authApi.changePassword(oldPassword, newPassword)
      this.user = user
      localStorage.setItem(USER_KEY, JSON.stringify(user))
      return user
    },
    /** 登出：清空本地状态 */
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    },
  },
})
