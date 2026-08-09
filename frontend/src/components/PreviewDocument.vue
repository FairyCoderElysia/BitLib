<template>
  <div class="preview-wrap">
    <!-- 加载中 -->
    <div v-if="loading" v-loading="true" class="preview-loading">
      <span class="loading-text">正在加载原文…</span>
    </div>

    <!-- 加载失败 / 无权限：友好错误，不影响其他功能 -->
    <el-empty v-else-if="error" :description="error" />

    <!-- PDF：逐页 canvas 渲染 + 页导航 / 缩放 -->
    <div v-else-if="fileType === 'pdf'" class="pdf-panel">
      <div class="pdf-toolbar">
        <el-button size="small" :icon="ArrowLeft" :disabled="page <= 1" @click="page--">上一页</el-button>
        <span class="num page-num">{{ page }} / {{ totalPages }}</span>
        <el-button size="small" :disabled="page >= totalPages" @click="page++">下一页<el-icon class="el-icon--right"><ArrowRight /></el-icon></el-button>
        <span class="toolbar-sep" />
        <el-button size="small" :icon="ZoomOut" @click="zoom(-0.15)">缩小</el-button>
        <span class="num">{{ Math.round(scale * 100) }}%</span>
        <el-button size="small" :icon="ZoomIn" @click="zoom(0.15)">放大</el-button>
      </div>
      <div class="pdf-canvas-wrap">
        <canvas ref="pdfCanvas" class="pdf-canvas" />
      </div>
    </div>

    <!-- DOCX：docx-preview 渲染 -->
    <div v-else-if="fileType === 'docx'" ref="docxContainer" class="docx-container" />

    <!-- TXT / MD：TextDecoder 解码直显，保留换行 -->
    <pre v-else-if="fileType === 'txt' || fileType === 'md'" class="text-preview">{{ textContent }}</pre>

    <el-empty v-else description="暂不支持该格式在线预览" />
  </div>
</template>

<script setup>
import { ref, watch, onUnmounted, nextTick } from 'vue'
import { ArrowLeft, ArrowRight, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import { docApi } from '@/api/modules'
// pdfjs-dist：4.x 命名空间导入，worker 走 Vite `?url` 静态资源
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
// docx-preview：二进制流直接渲染到容器
import { renderAsync } from 'docx-preview'

// 配置 PDF worker（Vite 打包时该 URL 会被解析为静态资源）
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const props = defineProps({
  /** 文档 id */
  documentId: { type: [Number, String], required: true },
  /** 文件类型：pdf / docx / txt / md */
  fileType: { type: String, default: '' },
  /** 文档标题（用于提示） */
  title: { type: String, default: '' },
})

const loading = ref(false)
const error = ref('')
const textContent = ref('')

// PDF 状态
const pdfCanvas = ref(null)
let pdfDoc = null // 当前 PDF 文档对象（异步加载）
const page = ref(1)
const totalPages = ref(0)
const scale = ref(1.2)

// DOCX 容器
const docxContainer = ref(null)

/** 从错误对象提取可读信息（arraybuffer 错误响应内嵌 JSON 时需解码） */
function extractError(err) {
  const data = err?.response?.data
  if (data instanceof ArrayBuffer) {
    try {
      const json = JSON.parse(new TextDecoder().decode(new Uint8Array(data)))
      return json.message || '文件加载失败'
    } catch (e) {
      /* 非 JSON 内容，忽略 */
    }
  }
  return err?.response?.data?.message || err?.message || '文件加载失败，请确认文档状态或稍后重试'
}

/** 请求带 Authorization 的原文二进制流 */
async function load() {
  loading.value = true
  error.value = ''
  textContent.value = ''
  resetPdf()
  try {
    const res = await docApi.preview(props.documentId)
    const buffer = res.data
    const type = props.fileType

    if (type === 'pdf') {
      await renderPdf(buffer)
    } else if (type === 'docx') {
      await renderDocx(buffer)
    } else if (type === 'txt' || type === 'md') {
      // TextDecoder 解码文本（UTF-8）
      textContent.value = new TextDecoder('utf-8').decode(new Uint8Array(buffer))
    }
  } catch (err) {
    error.value = extractError(err)
  } finally {
    loading.value = false
  }
}

/** PDF：加载文档 + 渲染当前页 */
async function renderPdf(buffer) {
  pdfDoc = await pdfjsLib.getDocument({ data: new Uint8Array(buffer) }).promise
  totalPages.value = pdfDoc.numPages
  page.value = 1
  await nextTick()
  await drawPage()
}

/** 将当前页画到 canvas */
async function drawPage() {
  if (!pdfDoc || !pdfCanvas.value) return
  const pg = await pdfDoc.getPage(page.value)
  const viewport = pg.getViewport({ scale: scale.value })
  const canvas = pdfCanvas.value
  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.floor(viewport.width * dpr)
  canvas.height = Math.floor(viewport.height * dpr)
  canvas.style.width = `${Math.floor(viewport.width)}px`
  canvas.style.height = `${Math.floor(viewport.height)}px`
  const ctx = canvas.getContext('2d')
  const task = pg.render({ canvasContext: ctx, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined })
  // 兼容不同版本返回结构：有的直接返回 Promise，有的返回带 .promise 的 RenderTask
  if (task && task.promise) await task.promise
  else await task
}

/** PDF 缩放（0.5 ~ 3 之间） */
function zoom(delta) {
  scale.value = Math.min(3, Math.max(0.5, +(scale.value + delta).toFixed(2)))
}

/** 重置 PDF 状态 */
function resetPdf() {
  if (pdfDoc) {
    try { pdfDoc.destroy() } catch (e) { /* 忽略 */ }
    pdfDoc = null
  }
  totalPages.value = 0
  page.value = 1
}

/** DOCX：docx-preview renderAsync 渲染到容器（data, container, styleContainer?, options?） */
async function renderDocx(buffer) {
  if (!docxContainer.value) return
  docxContainer.value.innerHTML = ''
  await renderAsync(buffer, docxContainer.value, undefined, {
    className: 'docx-preview', // 内部元素统一前缀，方便 scoped 深度样式
    inWrapper: true,           // 生成 .docx-wrapper 包裹层（容器 display:flex 居中）
    ignoreLastRenderedPageBreak: true,
  })
}

// documentId / fileType 变化时重新加载
watch(() => [props.documentId, props.fileType], () => load(), { immediate: true })
// 页码 / 缩放变化 → 重绘当前页
watch([page, scale], () => drawPage())

onUnmounted(resetPdf)
</script>

<style scoped>
.preview-wrap {
  min-height: 200px;
  display: flex;
  flex-direction: column;
}

.preview-loading {
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.loading-text {
  margin-left: 8px;
  color: var(--ink-600);
  font-size: 13px;
}

/* ---------------- PDF ---------------- */
.pdf-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pdf-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--fill-2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}

.page-num {
  min-width: 64px;
  text-align: center;
}

.toolbar-sep {
  width: 1px;
  height: 18px;
  background: var(--line);
  margin: 0 4px;
}

.pdf-canvas-wrap {
  overflow: auto;
  max-height: 65vh;
  background: var(--fill-2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 12px;
  text-align: center;
}

.pdf-canvas {
  background: var(--card);
  box-shadow: var(--shadow-md);
}

/* ---------------- DOCX ---------------- */
.docx-container {
  display: flex;
  justify-content: center;
  overflow: auto;
  max-height: 68vh;
  background: var(--fill-2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px;
}

.docx-container :deep(.docx-wrapper) {
  background: var(--card);
  padding: 28px 32px;
  box-shadow: var(--shadow-md);
  min-width: 420px;
}

.docx-container :deep(.docx-preview) {
  font-family: var(--font-sans);
  color: var(--ink-900);
}

/* ---------------- TXT / MD ---------------- */
.text-preview {
  margin: 0;
  padding: 16px 20px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  font-family: var(--font-num);
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-900);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 68vh;
  overflow: auto;
}
</style>
