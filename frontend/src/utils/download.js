// 下载辅助：blob 触发浏览器保存（对齐 DocumentDetail.vue 现有写法）

/** 触发浏览器保存 blob（createObjectURL + <a download> + revoke） */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 批量下载默认文件名：documents-YYYYMMDD-HHmmss.zip（英文文件名规避中文下载兼容问题） */
export function timestampedZipName() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `documents-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-`
    + `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.zip`
}
