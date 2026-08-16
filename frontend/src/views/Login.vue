<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-header">
        <el-icon :size="34" :color="'var(--brand)'"><Document /></el-icon>
        <h2>企业资料管理系统</h2>
        <p class="sub">统一检索 · 智能问答 · 资料管理</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="账号" prop="username">
          <el-input v-model="form.username" placeholder="请输入账号" clearable>
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            @keyup.enter="onSubmit"
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-button
          type="primary"
          class="login-btn"
          size="large"
          :loading="loading"
          @click="onSubmit"
        >
          登 录
        </el-button>
      </el-form>

      <p class="tip">请使用管理员分配的账号登录；密码错误时会统一提示"用户名或密码错误"。</p>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch (e) {
    return
  }
  loading.value = true
  try {
    await userStore.login(form.username.trim(), form.password)
    ElMessage.success('登录成功')
    // F1 首登强制改密：优先进入改密流程（守卫也会兜底强制跳转）
    if (userStore.mustChangePassword) {
      router.push('/change-password')
      return
    }
    // 支持登录后回跳原目标页
    const redirect = route.query.redirect
    router.push(typeof redirect === 'string' ? redirect : '/')
  } catch (e) {
    /* 错误提示由 http 拦截器统一弹出 */
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  /* 品牌深蓝渐变：仅用 token 色阶，保持单一强调色 */
  background: linear-gradient(135deg, var(--brand-hover) 0%, var(--brand) 60%, var(--el-color-primary-light-3) 100%);
}

.login-card {
  width: 400px;
  padding: 12px 8px;
}

.login-header {
  text-align: center;
  margin-bottom: 20px;
}

.login-header h2 {
  margin: 8px 0 4px;
  font-size: 20px;
  color: var(--ink-900);
}

.sub {
  margin: 0;
  font-size: 13px;
  color: var(--ink-400);
}

.login-btn {
  width: 100%;
  margin-top: 8px;
}

.tip {
  margin: 16px 0 0;
  font-size: 12px;
  color: var(--ink-400);
  text-align: center;
}
</style>
