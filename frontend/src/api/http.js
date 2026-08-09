import axios from 'axios'
import { ElMessage } from 'element-plus'

// axios 实例：统一 baseURL（Vite 代理 /api → 后端）、请求附加 Bearer token、
// 响应统一处理业务码（code !== 0 报错）与 401 跳登录。
const http = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

// 请求拦截：附加登录 token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：统一处理
http.interceptors.response.use(
  (response) => {
    // 二进制响应（blob 下载 / arraybuffer 在线预览）直接放行，无业务码
    const binType = response.config.responseType
    if (binType === 'blob' || binType === 'arraybuffer') {
      return response
    }
    const res = response.data
    // 统一成功响应 { code: 0, message: 'ok', data }
    if (res && res.code !== 0) {
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message || '请求失败'))
    }
    return response
  },
  async (error) => {
    const status = error.response?.status
    let data = error.response?.data
    let message = error.message || '网络错误'

    // blob 响应里包裹的错误 JSON：转文本解析出可读信息
    if (data instanceof Blob) {
      try {
        const text = await data.text()
        const parsed = JSON.parse(text)
        if (parsed?.message) message = parsed.message
      } catch (e) {
        /* 非 JSON 内容，忽略 */
      }
    } else if (data?.message) {
      message = data.message
    }

    // 401：清除本地登录态；非登录页跳转登录，登录页（登录失败）只提示
    if (status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      ElMessage.error(message || '未登录或登录已过期')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    } else if (message) {
      ElMessage.error(message)
    }
    return Promise.reject(error)
  }
)

export default http
