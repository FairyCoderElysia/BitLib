<template>
  <div class="change-password-page">
    <el-card class="change-password-card" shadow="always">
      <div class="header">
        <el-icon :size="32" :color="'var(--brand)'"><Lock /></el-icon>
        <h2>修改初始密码</h2>
        <p class="sub">首次登录必须先修改密码，完成后即可正常使用系统</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="原密码" prop="oldPassword">
          <el-input
            v-model="form.oldPassword"
            type="password"
            placeholder="请输入原密码"
            show-password
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item label="新密码" prop="newPassword">
          <el-input
            v-model="form.newPassword"
            type="password"
            placeholder="至少 6 位，且不能与旧密码相同"
            show-password
          >
            <template #prefix><el-icon><Key /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="再次输入新密码"
            show-password
            @keyup.enter="onSubmit"
          >
            <template #prefix><el-icon><Key /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-button
          type="primary"
          class="submit-btn"
          size="large"
          :loading="loading"
          @click="onSubmit"
        >
          确认修改
        </el-button>
        <el-button
          class="logout-btn"
          size="large"
          @click="onLogout"
        >
          退出登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock, Key } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })

const rules = {
  oldPassword: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '新密码长度至少 6 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value !== form.newPassword) {
          callback(new Error('两次输入的新密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch (e) {
    return
  }
  loading.value = true
  try {
    await userStore.changePassword(form.oldPassword, form.newPassword)
    ElMessage.success('密码修改成功')
    router.push('/')
  } catch (e) {
    /* 错误提示由 http 拦截器统一弹出 */
  } finally {
    loading.value = false
  }
}

function onLogout() {
  userStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.change-password-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, var(--brand-hover) 0%, var(--brand) 60%, var(--el-color-primary-light-3) 100%);
}

.change-password-card {
  width: 420px;
  padding: 12px 8px;
}

.header {
  text-align: center;
  margin-bottom: 20px;
}

.header h2 {
  margin: 8px 0 4px;
  font-size: 20px;
  color: var(--ink-900);
}

.sub {
  margin: 0;
  font-size: 13px;
  color: var(--ink-400);
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
}

.logout-btn {
  width: 100%;
  margin-top: 10px;
  margin-left: 0;
}
</style>
