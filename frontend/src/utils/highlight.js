// 检索结果关键词高亮：先转义 HTML 防 XSS，再包裹 <mark>
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 将 text 中出现的 keyword 分词逐段高亮，返回安全 HTML 字符串（配合 v-html 使用）。
 * 无关键词时仅做 HTML 转义后原样返回。
 */
export function highlight(text, keyword) {
  const escaped = escapeHtml(text)
  const words = String(keyword || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (!words.length) return escaped
  let out = escaped
  for (const w of words) {
    // 用转义后的关键词做正则匹配，替换为 <mark>（防止匹配到 &amp; 等已转义序列）
    const target = escapeHtml(w)
    if (!target) continue
    out = out.replace(new RegExp(escapeReg(target), 'g'), `<mark>${target}</mark>`)
  }
  return out
}

function escapeReg(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
