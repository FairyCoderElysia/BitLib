import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function detectChannel() {
  if (process.env.EDMS_E2E_BROWSER) return process.env.EDMS_E2E_BROWSER;
  const candidates = [
    ['msedge', [
      'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
      'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    ]],
    ['chrome', [
      'C:/Program Files/Google/Chrome/Application/chrome.exe',
      'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    ]],
  ];
  for (const [channel, paths] of candidates) {
    if (paths.some((p) => fs.existsSync(p))) return channel;
  }
  return 'msedge';
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  globalSetup: './e2e/global-setup.js',
  globalTeardown: './e2e/global-teardown.js',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: detectChannel(),
    headless: true,
    locale: 'zh-CN',
    screenshot: 'only-on-failure',
    trace: 'off',
  },
});
