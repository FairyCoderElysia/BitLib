<template>
  <div class="dashboard" v-loading="loading">
    <template v-if="stats">
      <!-- 统计卡片 -->
      <div class="stat-grid">
        <div class="tk-card stat-card">
          <div class="stat-icon brand"><el-icon :size="22"><FolderOpened /></el-icon></div>
          <div class="stat-body">
            <div class="stat-num num">{{ stats.document_total || 0 }}</div>
            <div class="stat-label">文档总数</div>
          </div>
        </div>

        <div class="tk-card stat-card">
          <div class="stat-icon warn"><el-icon :size="22"><Stamp /></el-icon></div>
          <div class="stat-body">
            <div class="stat-num num">{{ stats.pending_count || 0 }}</div>
            <div class="stat-label">待审批</div>
          </div>
        </div>

        <div class="tk-card stat-card">
          <div class="stat-icon info"><el-icon :size="22"><Promotion /></el-icon></div>
          <div class="stat-body">
            <div class="stat-num num">
              {{ stats.crawl_task_count?.enabled || 0 }}
              <span class="stat-sub">/ {{ stats.crawl_task_count?.disabled || 0 }}</span>
            </div>
            <div class="stat-label">爬虫任务（启用 / 停用）</div>
          </div>
        </div>

        <div class="tk-card stat-card">
          <div class="stat-icon ok"><el-icon :size="22"><OfficeBuilding /></el-icon></div>
          <div class="stat-body">
            <div class="stat-num num">{{ stats.department_count || 0 }}</div>
            <div class="stat-label">部门数</div>
          </div>
        </div>

        <div class="tk-card stat-card">
          <div class="stat-icon ok"><el-icon :size="22"><User /></el-icon></div>
          <div class="stat-body">
            <div class="stat-num num">{{ stats.user_count || 0 }}</div>
            <div class="stat-label">用户数</div>
          </div>
        </div>
      </div>

      <!-- 近 7 日趋势 + 待审批快捷入口 -->
      <div class="row">
        <div class="tk-card trend-card">
          <div class="card-head">
            <span class="card-title">近 7 日操作趋势</span>
          </div>
          <el-empty v-if="!trend.length" description="近 7 日暂无操作记录" :image-size="60" />
          <div v-else class="trend-chart">
            <div v-for="t in trend" :key="t.date" class="trend-col">
              <div class="trend-bar" :style="{ height: barHeight(t.count) }" :title="`${t.date}：${t.count} 次`" />
              <div class="trend-label num">{{ t.count }}</div>
              <div class="trend-date num">{{ t.date.slice(5) }}</div>
            </div>
          </div>
        </div>

        <div class="tk-card quick-card">
          <div class="card-head">
            <span class="card-title">快捷入口</span>
          </div>
          <el-button type="primary" class="quick-btn" :icon="Stamp" @click="router.push('/admin/approvals')">
            处理待审批（{{ stats.pending_count || 0 }}）
          </el-button>
          <el-button class="quick-btn" :icon="FolderOpened" @click="router.push('/admin/documents')">
            文档管理
          </el-button>
          <el-button v-if="isAdmin" class="quick-btn" :icon="Promotion" @click="router.push('/admin/crawl-tasks')">
            爬虫任务
          </el-button>
        </div>
      </div>
    </template>

    <el-empty v-else-if="!loading" description="暂无统计数据" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { adminApi } from '@/api/modules'
import { FolderOpened, Stamp, Promotion, OfficeBuilding, User } from '@element-plus/icons-vue'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const stats = ref(null)

const isAdmin = computed(() => userStore.role === 'admin')

/** 趋势数据（按日期升序） */
const trend = computed(() => (stats.value?.trend_7d || []).slice().sort((a, b) => a.date.localeCompare(b.date)))

/** 柱高：相对最大值等比（纯 CSS 柱状图，不引图表库） */
function barHeight(count) {
  const max = Math.max(1, ...trend.value.map((t) => t.count))
  const h = Math.max(8, Math.round((count / max) * 160))
  return `${h}px`
}

async function fetchStats() {
  loading.value = true
  try {
    stats.value = await adminApi.stats()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

onMounted(fetchStats)
</script>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px;
}

.stat-icon {
  width: 46px;
  height: 46px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-icon.brand { background: var(--brand-weak); color: var(--brand); }
.stat-icon.warn { background: #fef3e2; color: var(--warn); }
.stat-icon.info { background: var(--fill-2); color: var(--info); }
.stat-icon.ok { background: #e6f7f1; color: var(--ok); }

.stat-body {
  min-width: 0;
}

.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--ink-900);
  line-height: 1.2;
}

.stat-sub {
  font-size: 13px;
  font-weight: 400;
  color: var(--ink-400);
}

.stat-label {
  font-size: 13px;
  color: var(--ink-600);
  margin-top: 2px;
}

.row {
  display: flex;
  gap: 14px;
  margin-top: 14px;
  align-items: stretch;
}

.trend-card {
  flex: 1;
  padding: 16px 20px;
  min-height: 260px;
}

.quick-card {
  width: 260px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.card-head {
  margin-bottom: 14px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-900);
}

.quick-btn {
  width: 100%;
  margin-left: 0;
}

/* 纯 CSS 柱状图 */
.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  height: 190px;
  padding-top: 8px;
}

.trend-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.trend-bar {
  width: 60%;
  max-width: 44px;
  min-height: 4px;
  background: var(--brand);
  border-radius: 3px 3px 0 0;
  transition: height 0.25s ease;
}

.trend-label {
  font-size: 12px;
  color: var(--ink-600);
}

.trend-date {
  font-size: 11px;
  color: var(--ink-400);
}
</style>
