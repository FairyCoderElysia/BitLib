// 通用格式化工具（大小 / 时间 / 各类枚举标签）

/** 文件大小 → 可读字符串 */
export function formatSize(bytes) {
  if (bytes == null || isNaN(bytes)) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/** 时间 → YYYY-MM-DD HH:mm */
export function formatTime(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (isNaN(d.getTime())) return String(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 文件类型标签 / tag 类型 */
export const FILE_TYPE_LABEL = { txt: 'TXT', md: 'MD', pdf: 'PDF', docx: 'DOCX' }
export const FILE_TYPE_TAG = { txt: 'info', md: 'info', pdf: 'danger', docx: 'primary' }

/** 来源标签 */
export const SOURCE_LABEL = { upload: '用户上传', crawl: '爬虫抓取' }

/** 文档状态标签 / tag 类型 */
export const DOC_STATUS_LABEL = {
  pending: '待审批',
  processing: '处理中',
  approved: '已通过',
  rejected: '已拒绝',
  offline: '已下架',
  failed: '解析失败',
}
export const DOC_STATUS_TAG = {
  pending: 'warning',
  processing: 'info',
  approved: 'success',
  rejected: 'danger',
  offline: 'info',
  failed: 'danger',
}
