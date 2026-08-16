import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const E2E_DIR = __dirname;
const RUN_ENV_FILE = path.join(E2E_DIR, '.run-env.json');
const RUN_PROCS_FILE = path.join(E2E_DIR, '.run-procs.json');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function killTree(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      process.kill(pid, 'SIGTERM');
    }
  } catch {
    // ignore kill errors
  }
}

export default async function globalTeardown() {
  // 1) 停止前后端进程（无论用例 PASS / FAIL）
  let procs = {};
  try {
    procs = JSON.parse(fs.readFileSync(RUN_PROCS_FILE, 'utf8'));
  } catch {
    // missing file: nothing to kill
  }
  killTree(procs.backendPid);
  killTree(procs.frontendPid);
  await sleep(1500);

  // 2) 清理独立测试数据根目录（保留 .run-env.json 与 e2e 目录本身）
  let runEnv = {};
  try {
    runEnv = JSON.parse(fs.readFileSync(RUN_ENV_FILE, 'utf8'));
  } catch {
    // missing env file: nothing to clean
  }
  const dataRoot = runEnv.dataRoot;
  if (dataRoot && fs.existsSync(dataRoot)) {
    let removed = false;
    for (let i = 0; i < 5 && !removed; i++) {
      try {
        fs.rmSync(dataRoot, { recursive: true, force: true });
        removed = !fs.existsSync(dataRoot);
      } catch {
        await sleep(1200);
      }
    }
    console.log('[e2e] teardown data root cleaned: ' + !fs.existsSync(dataRoot));
  } else if (dataRoot) {
    console.log('[e2e] teardown data root already cleaned: true');
  }

  // 3) 删除进程标记，保留 .run-env.json 供 Evaluator 核查
  try {
    fs.rmSync(RUN_PROCS_FILE, { force: true });
  } catch {
    // ignore
  }
}
