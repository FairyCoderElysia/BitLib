import http from './http'

// 统一取响应体中的 data 字段
function unwrap(promise) {
  return promise.then((res) => res.data?.data)
}

// ---------------- 认证 ----------------
export const authApi = {
  /** 账号密码登录 → { token, user } */
  login(username, password) {
    return unwrap(http.post('/auth/login', { username, password }))
  },
  /** 当前用户信息 */
  me() {
    return unwrap(http.get('/auth/me'))
  },
  /** 部门列表 */
  departments() {
    return unwrap(http.get('/auth/departments'))
  },
}

// ---------------- 文档 ----------------
export const docApi = {
  /** 上传（multipart：file + title） */
  upload(formData) {
    return unwrap(http.post('/documents/upload', formData))
  },
  /** 我的上传（分页） */
  mine(params) {
    return unwrap(http.get('/documents/mine', { params }))
  },
  /** 文档详情（approved 时含 content_text） */
  detail(id) {
    return unwrap(http.get(`/documents/${id}`))
  },
  /** 相似文档推荐（F18）：top_k ≤ 5，distance 升序 */
  related(id) {
    return unwrap(http.get(`/documents/${id}/related`))
  },
  /** 撤回（仅本人 pending） */
  withdraw(id) {
    return unwrap(http.delete(`/documents/${id}`))
  },
  /** 下载：返回 axios response（data 为 blob） */
  download(id) {
    return http.get(`/documents/${id}/download`, { responseType: 'blob' })
  },
  /** 原文在线预览：返回 axios response（data 为 ArrayBuffer） */
  preview(id) {
    return http.get(`/documents/${id}/preview`, { responseType: 'arraybuffer' })
  },
}

// ---------------- 管理端（Sprint 5b） ----------------
export const adminApi = {
  /** 工作台统计 */
  stats() {
    return unwrap(http.get('/admin/stats'))
  },
  /** 待审批列表（分页） */
  pending(params) {
    return unwrap(http.get('/admin/pending', { params }))
  },
  /** 审批通过 */
  approve(id) {
    return unwrap(http.post(`/admin/pending/${id}/approve`))
  },
  /** 审批拒绝（附原因） */
  reject(id, reason) {
    return unwrap(http.post(`/admin/pending/${id}/reject`, { reason }))
  },
  /** 文档管理列表（分页 + 筛选） */
  documents(params) {
    return unwrap(http.get('/admin/documents', { params }))
  },
  /** 管理端直入库上传（multipart：file + title + department_id） */
  upload(formData) {
    return http.post('/admin/documents/upload', formData)
  },
  /** 文档管理操作：重点标记 / 上架下架 / 改部门 */
  patchDocument(id, payload) {
    return unwrap(http.patch(`/admin/documents/${id}`, payload))
  },
  /** 删除文档 */
  deleteDocument(id) {
    return unwrap(http.delete(`/admin/documents/${id}`))
  },
  /** 重新入库（failed/offline） */
  reprocess(id) {
    return unwrap(http.post(`/admin/documents/${id}/reprocess`))
  },
  regenerateSummary(id) {
    return unwrap(http.post(`/admin/documents/${id}/regenerate-summary`))
  },
  /** 爬虫任务列表（分页） */
  crawlTasks(params) {
    return unwrap(http.get('/admin/crawl-tasks', { params }))
  },
  /** 新建爬虫任务 */
  createCrawlTask(body) {
    return unwrap(http.post('/admin/crawl-tasks', body))
  },
  /** 编辑爬虫任务（含启停） */
  patchCrawlTask(id, body) {
    return unwrap(http.patch(`/admin/crawl-tasks/${id}`, body))
  },
  /** 删除爬虫任务 */
  deleteCrawlTask(id) {
    return unwrap(http.delete(`/admin/crawl-tasks/${id}`))
  },
  /** 手动执行爬虫任务 */
  runCrawlTask(id) {
    return unwrap(http.post(`/admin/crawl-tasks/${id}/run`))
  },
  /** 任务运行记录（分页） */
  crawlLogs(id, params) {
    return unwrap(http.get(`/admin/crawl-tasks/${id}/logs`, { params }))
  },
  /** 用户列表（分页） */
  users(params) {
    return unwrap(http.get('/admin/users', { params }))
  },
  /** 创建账号 */
  createUser(body) {
    return unwrap(http.post('/admin/users', body))
  },
  /** 修改用户：角色 / 部门 / 重置密码 */
  patchUser(id, body) {
    return unwrap(http.patch(`/admin/users/${id}`, body))
  },
  /** 删除用户（admin 自身除外） */
  deleteUser(id) {
    return unwrap(http.delete(`/admin/users/${id}`))
  },
  /** 部门推送（空 department_id = 全员） */
  push(body) {
    return unwrap(http.post('/admin/push', body))
  },
  /** 审计日志（分页 + 筛选） */
  auditLogs(params) {
    return unwrap(http.get('/admin/audit-logs', { params }))
  },
}

// ---------------- 检索 ----------------
export const searchApi = {
  /** 混合检索 GET /search */
  search(params) {
    return unwrap(http.get('/search', { params }))
  },
  /** 热词榜 GET /search/hot-words → { items: string[] } */
  hotWords() {
    return unwrap(http.get('/search/hot-words'))
  },
  /** 输入联想 GET /search/suggest?q= → { items: [{id, title}] } */
  suggest(q) {
    return unwrap(http.get('/search/suggest', { params: { q } }))
  },
}

// ---------------- AI 问答 ----------------
export const qaApi = {
  /** 提问 → { session_id, answer, citations, confidence } */
  ask(body) {
    return unwrap(http.post('/qa/ask', body))
  },
  /** 会话列表（修复#4） */
  listSessions(params) {
    return unwrap(http.get('/qa/sessions', { params }))
  },
  /** 会话消息历史（修复#4） */
  getMessages(sessionId) {
    return unwrap(http.get(`/qa/sessions/${sessionId}/messages`))
  },
  /** 删除单条会话（F21） */
  deleteSession(sessionId) {
    return unwrap(http.delete(`/qa/sessions/${sessionId}`))
  },
  /** 清空全部会话（F21） */
  clearSessions() {
    return unwrap(http.delete('/qa/sessions'))
  },
}

// ---------------- 收藏夹 / 收藏 ----------------
export const favApi = {
  listFolders() {
    return unwrap(http.get('/favorites/folders'))
  },
  createFolder(name) {
    return unwrap(http.post('/favorites/folders', { name }))
  },
  renameFolder(id, name) {
    return unwrap(http.patch(`/favorites/folders/${id}`, { name }))
  },
  deleteFolder(id) {
    return unwrap(http.delete(`/favorites/folders/${id}`))
  },
  listFavorites() {
    return unwrap(http.get('/favorites'))
  },
  /** { document_id, folder_id? } */
  addFavorite(payload) {
    return unwrap(http.post('/favorites', payload))
  },
  removeFavorite(documentId) {
    return unwrap(http.delete(`/favorites/${documentId}`))
  },
}

// ---------------- 通知 ----------------
export const notifApi = {  list(params) {
    return unwrap(http.get('/notifications', { params }))
  },
  markRead(id) {
    return unwrap(http.post(`/notifications/${id}/read`))
  },
  markAllRead() {
    return unwrap(http.post('/notifications/read-all'))
  },
}

// ---------------- 部门管理（修复#1，admin 专属） ----------------
export const deptAdminApi = {
  list() {
    return unwrap(http.get('/admin/departments'))
  },
  create(name) {
    return unwrap(http.post('/admin/departments', { name }))
  },
  rename(id, name) {
    return unwrap(http.patch(`/admin/departments/${id}`, { name }))
  },
  remove(id) {
    return unwrap(http.delete(`/admin/departments/${id}`))
  },
}
