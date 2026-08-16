import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const E2E_DIR = __dirname;
const FRONTEND_DIR = path.resolve(__dirname, '..');
const BACKEND_DIR = path.resolve(__dirname, '..', '..', 'backend');
const RUN_ENV_FILE = path.join(E2E_DIR, '.run-env.json');
const RUN_PROCS_FILE = path.join(E2E_DIR, '.run-procs.json');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForHttp(url, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return res;
    } catch {
      // service not ready yet
    }
    await sleep(1000);
  }
  throw new Error(`${label} not ready after ${timeoutMs}ms: ${url}`);
}

function pipeOutput(proc, prefix) {
  proc.stdout.on('data', (d) => process.stdout.write(`${prefix}${d}`));
  proc.stderr.on('data', (d) => process.stdout.write(`${prefix}${d}`));
}

function hasDefaultEmbeddingModelCache() {
  // 默认本地 embedding 模型 BAAI/bge-small-zh-v1.5 的 HF 缓存检测。
  // 已缓存则设置 HF_HUB_OFFLINE=1 避免无网时长时间重试；未缓存则允许首次运行在线下载。
  const snapshotsDir = path.join(
    os.homedir(),
    '.cache',
    'huggingface',
    'hub',
    'models--BAAI--bge-small-zh-v1.5',
    'snapshots',
  );
  try {
    for (const snap of fs.readdirSync(snapshotsDir)) {
      if (fs.existsSync(path.join(snapshotsDir, snap, 'model.safetensors'))) return true;
    }
  } catch {
    // ignore
  }
  return false;
}

export default async function globalSetup() {
  // 1) 创建独立测试数据根目录（系统临时目录，绝不落入 backend/data）
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'edms-e2e-'));
  const databaseUrl = 'sqlite:///' + path.join(dataRoot, 'db', 'app.db').split(path.sep).join('/');
  const uploadDir = path.join(dataRoot, 'uploads');
  const chromaDir = path.join(dataRoot, 'chroma');
  for (const dir of [path.dirname(databaseUrl.replace(/^sqlite:\/\/\//, '')), uploadDir, chromaDir]) {
    fs.mkdirSync(dir, { recursive: true });
  }

  // 2) 启动后端前：打印三个实际生效路径并写入标记文件（E2/E3 核查依据）
  console.log('[e2e] E2E_DATA_ROOT=' + dataRoot);
  console.log('[e2e] DATABASE_URL=' + databaseUrl);
  console.log('[e2e] UPLOAD_DIR=' + uploadDir);
  console.log('[e2e] CHROMA_DIR=' + chromaDir);

  const runEnv = {
    dataRoot,
    databaseUrl,
    uploadDir,
    chromaDir,
    backendUrl: 'http://127.0.0.1:8000',
    frontendUrl: 'http://127.0.0.1:5173',
  };
  fs.writeFileSync(RUN_ENV_FILE, JSON.stringify(runEnv, null, 2) + '\n');
  fs.writeFileSync(RUN_PROCS_FILE, JSON.stringify({ backendPid: null, frontendPid: null }, null, 2) + '\n');

  // 3) 端口预检：自包含启动，不抢占他人服务；占用则直接失败并提示
  try {
    const occupied = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(800) });
    if (occupied.ok) throw new Error('8000 端口已有服务在运行，请先停止后再执行 npm run test:e2e');
  } catch (e) {
    if (e.message.includes('8000 端口已有服务')) throw e;
    // fetch failed = port free
  }

  // 4) 启动后端（uvicorn，显式隔离环境变量）
  const backendEnv = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    UPLOAD_DIR: uploadDir,
    CHROMA_DIR: chromaDir,
    ADMIN_INITIAL_PASSWORD: 'Admin@123456',
    SECRET_KEY: 'test-secret',
    ENABLE_SCHEDULER: 'false',
    PYTHONUNBUFFERED: '1',
    // E2E 提速/离线稳定：关闭重排模型，使用本地 embedding；LLM 指向本机关闭端口，摘要自动降级
    RERANKER_ENABLED: 'false',
    EMBEDDING_MODE: 'local',
    EMBEDDING_DIM: '512',
    ...(hasDefaultEmbeddingModelCache() ? { HF_HUB_OFFLINE: '1' } : {}),
    LLM_BASE_URL: 'http://127.0.0.1:9/v1',
    LLM_API_KEY: '',
  };
  const python = process.env.PYTHON || 'python';
  const backend = spawn(
    python,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'],
    {
      cwd: BACKEND_DIR,
      env: backendEnv,
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: false,
    },
  );
  pipeOutput(backend, '[backend] ');

  // 5) 等待后端健康检查
  await waitForHttp('http://127.0.0.1:8000/api/health', 90_000, 'backend');

  // 6) 启动前端 Vite（严格端口，防止端口被占时静默漂移）
  const frontendEnv = { ...process.env, BROWSER: 'none' };
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const frontend = spawn(
    npm,
    ['run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173', '--strictPort'],
    {
      cwd: FRONTEND_DIR,
      env: frontendEnv,
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32',
    },
  );
  pipeOutput(frontend, '[frontend] ');

  // 7) 等待前端登录页可达
  await waitForHttp('http://127.0.0.1:5173/login', 60_000, 'frontend');

  // 8) 记录 PID 供 teardown 使用
  fs.writeFileSync(
    RUN_PROCS_FILE,
    JSON.stringify({ backendPid: backend.pid, frontendPid: frontend.pid }, null, 2) + '\n',
  );
  console.log('[e2e] services started: backend pid=' + backend.pid + ', frontend pid=' + frontend.pid);
}
