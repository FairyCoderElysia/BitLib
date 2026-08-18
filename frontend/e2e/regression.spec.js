import { test as base, expect } from '@playwright/test';

// Playwright 原生 tag 要求 '@' 前缀（如 '@L0'），而 sprint contract E4 固定格式为
// tag: ['L0', 'affects:auth,search']。这里用一层极薄包装，把 contract 格式归一化为
// Playwright 原生 tag（每个 tag 前补 '@'），测试文件源码保持 contract 要求的格式。
const test = (title, details, body) => {
  const nativeDetails = { ...details };
  if (Array.isArray(details.tag)) {
    nativeDetails.tag = details.tag.map((t) => (t.startsWith('@') ? t : `@${t}`));
  } else if (typeof details.tag === 'string') {
    nativeDetails.tag = details.tag
      .split(/\s+/)
      .filter(Boolean)
      .map((t) => (t.startsWith('@') ? t : `@${t}`))
      .join(' ');
  }
  return base(title, nativeDetails, body);
};

const API = 'http://127.0.0.1:8000/api';
const WEB = 'http://127.0.0.1:5173';
const ADMIN_INITIAL_PASSWORD = 'Admin@123456';
const ADMIN_CHANGED_PASSWORD = 'Admin@654321';

// 测试按声明顺序串行执行（playwright.config.js: workers=1, fullyParallel=false）。
// 首个 L1 用例会把 admin 密码从初始值改为固定新值，后续用例共享该新值。
let adminPassword = ADMIN_INITIAL_PASSWORD;

test('L0 健康检查与登录页可达', { tag: ['L0', 'affects:auth,infra'] }, async ({ request, page }) => {
  const res = await request.get(`${API}/health`);
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.code).toBe(0);

  await page.goto(`${WEB}/login`);
  await expect(page.getByText('企业资料管理系统')).toBeVisible();
  await expect(page.getByLabel('账号')).toBeVisible();
  await expect(page.getByLabel('密码')).toBeVisible();
});

test('L1 admin 首登强制改密并进入检索页', { tag: ['L1', 'affects:auth,search'] }, async ({ page }) => {
  await page.goto(`${WEB}/login`);
  await page.getByLabel('账号').fill('admin');
  await page.getByLabel('密码').fill(ADMIN_INITIAL_PASSWORD);
  await page.getByRole('button', { name: '登 录' }).click();

  // 首登强制改密：登录后必须跳转改密页
  await expect(page).toHaveURL(/change-password/);
  await expect(page.getByText('修改初始密码')).toBeVisible();

  await page.getByLabel('原密码').fill(ADMIN_INITIAL_PASSWORD);
  await page.getByPlaceholder('至少 6 位，且不能与旧密码相同').fill(ADMIN_CHANGED_PASSWORD);
  await page.getByPlaceholder('再次输入新密码').fill(ADMIN_CHANGED_PASSWORD);
  await page.getByRole('button', { name: '确认修改' }).click();

  // 改密成功进入检索页（真实浏览器断言）
  await expect(page).toHaveURL(/search/);
  await expect(page.getByPlaceholder('输入关键词搜索文档（支持标题 / 内容全文检索）')).toBeVisible();

  adminPassword = ADMIN_CHANGED_PASSWORD;
});

test('L1 资料主流程：普通用户上传 → 管理员审批 → 检索可见', { tag: ['L1', 'affects:auth,approval,search'] }, async ({ request, page, browser }) => {
  const unique = Date.now();
  const username = `e2e_user_${unique}`;
  const userPassword = 'User@123456';
  const title = `E2E回归测试文档_${unique}`;
  const uniquePhrase = `蓝鲸回归验证串${unique}`;
  const content = `${uniquePhrase}。本文档用于验证企业资料管理系统端到端回归链路：普通用户上传文档后，管理员在审批中心审批通过，文档完成解析入库，随后普通用户能够在检索页搜索到该文档。`;

  // ---------- API 准备：建普通用户 + 普通用户上传（关键步骤走 UI） ----------
  const adminLogin = await request.post(`${API}/auth/login`, {
    data: { username: 'admin', password: adminPassword },
  });
  expect(adminLogin.status()).toBe(200);
  const adminLoginBody = await adminLogin.json();
  expect(adminLoginBody.code).toBe(0);
  const adminToken = adminLoginBody.data.token;
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const deptRes = await request.get(`${API}/auth/departments`, { headers: adminHeaders });
  expect(deptRes.status()).toBe(200);
  const deptBody = await deptRes.json();
  const departmentId = deptBody.data[0].id;

  const createRes = await request.post(`${API}/admin/users`, {
    headers: adminHeaders,
    data: { username, password: userPassword, role: 'user', department_id: departmentId },
  });
  expect(createRes.status()).toBe(200);
  expect((await createRes.json()).code).toBe(0);

  const userLogin = await request.post(`${API}/auth/login`, {
    data: { username, password: userPassword },
  });
  expect(userLogin.status()).toBe(200);
  const userLoginBody = await userLogin.json();
  expect(userLoginBody.code).toBe(0);
  const userToken = userLoginBody.data.token;
  const userHeaders = { Authorization: `Bearer ${userToken}` };

  const uploadRes = await request.post(`${API}/documents/upload`, {
    headers: userHeaders,
    multipart: {
      file: {
        name: 'e2e-regression.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from(content, 'utf-8'),
      },
      title,
    },
  });
  expect(uploadRes.status()).toBe(200);
  const uploadBody = await uploadRes.json();
  expect(uploadBody.code).toBe(0);
  const documentId = uploadBody.data.id;

  // ---------- UI：管理员审批通过 ----------
  await page.goto(`${WEB}/login`);
  await page.getByLabel('账号').fill('admin');
  await page.getByLabel('密码').fill(adminPassword);
  await page.getByRole('button', { name: '登 录' }).click();
  await expect(page).toHaveURL(/search/);

  await page.goto(`${WEB}/admin/approvals`);
  await expect(page.getByRole('heading', { name: '审批中心' })).toBeVisible();

  const row = page.getByRole('row', { name: new RegExp(title) });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '通过' }).click();
  await page.getByRole('button', { name: '确定' }).click();
  // 审批成功后待审批列表会刷新，该行消失（比等待 ElMessage 瞬时消息更稳健）
  await expect(row).toHaveCount(0, { timeout: 45_000 });

  // ---------- UI：普通用户登录并检索到该文档（真实浏览器断言） ----------
  const userContext = await browser.newContext();
  try {
    const userPage = await userContext.newPage();
    await userPage.goto(`${WEB}/login`);
    await userPage.getByLabel('账号').fill(username);
    await userPage.getByLabel('密码').fill(userPassword);
    await userPage.getByRole('button', { name: '登 录' }).click();
    await expect(userPage).toHaveURL(/search/);

    await userPage.getByPlaceholder('输入关键词搜索文档（支持标题 / 内容全文检索）').fill(uniquePhrase);
    await userPage.getByRole('button', { name: '搜索' }).click();
    await expect(userPage.getByText(title)).toBeVisible();
  } finally {
    await userContext.close();
  }
});

test('L1 多部门可见：普通用户多部门上传 → 审批 → 两部门可检索、第三部门不可见', { tag: ['L1', 'affects:departments,approval,search'] }, async ({ request, page }) => {
  const unique = Date.now();
  const userPassword = 'User@123456';
  const title = `E2E多部门文档_${unique}`;
  const uniquePhrase = `多部门回归验证串${unique}`;
  const content = `${uniquePhrase}。本文档用于验证 S7 文档多部门可见：普通用户上传时勾选两个可见部门，审批通过后两个部门成员均可检索，第三部门成员不可检索。`;

  // ---------- API 准备：管理员登录 → 查部门 → 建用户 ----------
  const adminLogin = await request.post(`${API}/auth/login`, {
    data: { username: 'admin', password: adminPassword },
  });
  expect(adminLogin.status()).toBe(200);
  const adminToken = (await adminLogin.json()).data.token;
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const deptRes = await request.get(`${API}/auth/departments`, { headers: adminHeaders });
  expect(deptRes.status()).toBe(200);
  const depts = (await deptRes.json()).data;
  expect(depts.length).toBeGreaterThanOrEqual(3);
  const [deptA, deptB, deptZ] = depts.map((d) => d.id);

  const usernameA = `e2e_multi_a_${unique}`;
  const usernameB = `e2e_multi_b_${unique}`;
  const usernameZ = `e2e_multi_z_${unique}`;
  for (const [name, dept] of [[usernameA, deptA], [usernameB, deptB], [usernameZ, deptZ]]) {
    const r = await request.post(`${API}/admin/users`, {
      headers: adminHeaders,
      data: { username: name, password: userPassword, role: 'user', department_id: dept },
    });
    expect(r.status()).toBe(200);
  }

  const loginHeaders = async (name) => {
    const r = await request.post(`${API}/auth/login`, { data: { username: name, password: userPassword } });
    expect(r.status()).toBe(200);
    return { Authorization: `Bearer ${(await r.json()).data.token}` };
  };
  const [headersA, headersB, headersZ] = await Promise.all([
    loginHeaders(usernameA), loginHeaders(usernameB), loginHeaders(usernameZ),
  ]);

  // ---------- API：普通用户多部门上传（department_ids 为 JSON 数组字符串） ----------
  const uploadRes = await request.post(`${API}/documents/upload`, {
    headers: headersA,
    multipart: {
      file: {
        name: 'e2e-multidept.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from(content, 'utf-8'),
      },
      title,
      department_ids: JSON.stringify([deptA, deptB]),
    },
  });
  expect(uploadRes.status()).toBe(200);
  const uploadBody = await uploadRes.json();
  expect(uploadBody.code).toBe(0);
  expect(uploadBody.data.department_ids).toEqual([deptA, deptB]);
  const documentId = uploadBody.data.id;

  // ---------- UI：管理员审批通过 ----------
  await page.goto(`${WEB}/login`);
  await page.getByLabel('账号').fill('admin');
  await page.getByLabel('密码').fill(adminPassword);
  await page.getByRole('button', { name: '登 录' }).click();
  await expect(page).toHaveURL(/search/);

  await page.goto(`${WEB}/admin/approvals`);
  await expect(page.getByRole('heading', { name: '审批中心' })).toBeVisible();
  const row = page.getByRole('row', { name: new RegExp(title) });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '通过' }).click();
  await page.getByRole('button', { name: '确定' }).click();
  await expect(row).toHaveCount(0, { timeout: 45_000 });

  // ---------- 断言：A/B 可检索，第三部门不可见（API 检索权限口径） ----------
  const search = async (headers) => request.get(`${API}/search`, { headers, params: { q: uniquePhrase } });
  const idsOf = async (headers) => {
    const r = await search(headers);
    expect(r.status()).toBe(200);
    const body = await r.json();
    return ((body.data && body.data.items) || []).map((it) => it.id);
  };
  const [idsA, idsB, idsZ] = await Promise.all([idsOf(headersA), idsOf(headersB), idsOf(headersZ)]);
  expect(idsA).toContain(documentId);
  expect(idsB).toContain(documentId);
  expect(idsZ).not.toContain(documentId);
});



test('L1 通知详情页：点击进入详情不自动已读，标为已读并跳转关联文档', { tag: ['L1', 'affects:notifications,documents'] }, async ({ request, page }) => {
  const unique = Date.now();
  const username = `e2e_notif_${unique}`;
  const userPassword = 'User@123456';
  const docTitle = `通知关联文档_${unique}`;
  const notifTitle = `通知详情回归_${unique}`;
  const notifTitleNoDoc = `无关联通知_${unique}`;
  const notifContent = `这是通知详情页回归验证的完整正文_${unique}。用于验证点击通知行进入详情页、不自动标已读、手动标为已读以及跳转关联文档。`;

  // ---------- API 准备：管理员建用户、用户上传文档、管理员发两条通知 ----------
  const adminLogin = await request.post(`${API}/auth/login`, {
    data: { username: 'admin', password: adminPassword },
  });
  expect(adminLogin.status()).toBe(200);
  const adminToken = (await adminLogin.json()).data.token;
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const deptRes = await request.get(`${API}/auth/departments`, { headers: adminHeaders });
  expect(deptRes.status()).toBe(200);
  const deptId = (await deptRes.json()).data[0].id;

  const createRes = await request.post(`${API}/admin/users`, {
    headers: adminHeaders,
    data: { username, password: userPassword, role: 'user', department_id: deptId },
  });
  expect(createRes.status()).toBe(200);
  expect((await createRes.json()).code).toBe(0);

  const userLogin = await request.post(`${API}/auth/login`, {
    data: { username, password: userPassword },
  });
  expect(userLogin.status()).toBe(200);
  const userToken = (await userLogin.json()).data.token;
  const userHeaders = { Authorization: `Bearer ${userToken}` };

  const uploadRes = await request.post(`${API}/documents/upload`, {
    headers: userHeaders,
    multipart: {
      file: {
        name: 'notif-related.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from(`通知关联文档内容_${unique}`, 'utf-8'),
      },
      title: docTitle,
    },
  });
  expect(uploadRes.status()).toBe(200);
  const uploadBody = await uploadRes.json();
  expect(uploadBody.code).toBe(0);
  const documentId = uploadBody.data.id;

  const pushRes = await request.post(`${API}/admin/push`, {
    headers: adminHeaders,
    data: { title: notifTitle, content: notifContent, document_id: documentId, department_id: deptId },
  });
  expect(pushRes.status()).toBe(200);
  const notificationId = (await pushRes.json()).data.id;

  const pushNoDocRes = await request.post(`${API}/admin/push`, {
    headers: adminHeaders,
    data: { title: notifTitleNoDoc, content: `无关联通知正文_${unique}`, department_id: deptId },
  });
  expect(pushNoDocRes.status()).toBe(200);
  const notificationNoDocId = (await pushNoDocRes.json()).data.id;

  // ---------- UI：普通用户登录进入通知中心 ----------
  await page.goto(`${WEB}/login`);
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(userPassword);
  await page.getByRole('button', { name: '登 录' }).click();
  await expect(page).toHaveURL(/search/);

  await page.goto(`${WEB}/notifications`);
  await expect(page.getByText('通知中心')).toBeVisible();

  // F1-6：列表页“标为已读”按钮保留，且点击按钮不触发行跳转
  const rowNoDoc = page.locator('.notif-item', { hasText: notifTitleNoDoc });
  await expect(rowNoDoc).toBeVisible();
  await rowNoDoc.getByRole('button', { name: '标为已读' }).click();
  await expect(page).toHaveURL(/notifications$/);
  await expect(rowNoDoc.locator('.el-tag', { hasText: '未读' })).toHaveCount(0);

  // F1-1/F1-2：点击通知行进入详情页，不自动标已读
  const rowWithDoc = page.locator('.notif-item', { hasText: notifTitle });
  await expect(rowWithDoc).toBeVisible();
  await rowWithDoc.locator('.notif-main').click();
  await expect(page).toHaveURL(new RegExp(`/notifications/${notificationId}$`));

  // F1-3：详情页完整展示标题、正文、发送时间与未读状态
  await expect(page.getByRole('heading', { name: notifTitle })).toBeVisible();
  await expect(page.getByText(notifContent)).toBeVisible();
  await expect(page.getByText(/发送时间：/)).toBeVisible();
  await expect(page.locator('.el-tag', { hasText: '未读' })).toBeVisible();
  const beforeRead = await request.get(`${API}/notifications/${notificationId}`, { headers: userHeaders });
  expect(beforeRead.status()).toBe(200);
  expect((await beforeRead.json()).data.is_read).toBe(false);

  // F1-5：详情页标为已读，状态持久化到后端
  await page.getByRole('button', { name: '标为已读' }).click();
  await expect(page.locator('.el-tag', { hasText: '已读' })).toBeVisible();
  const afterRead = await request.get(`${API}/notifications/${notificationId}`, { headers: userHeaders });
  expect(afterRead.status()).toBe(200);
  expect((await afterRead.json()).data.is_read).toBe(true);

  // F1-4：有关联文档时显示“查看关联文档”并跳转；无关联文档不显示该入口
  await page.getByRole('button', { name: '查看关联文档' }).click();
  await expect(page).toHaveURL(new RegExp(`/documents/${documentId}$`));
  await expect(page.getByRole('heading', { name: docTitle })).toBeVisible();

  await page.goto(`${WEB}/notifications/${notificationNoDocId}`);
  await expect(page.getByRole('heading', { name: notifTitleNoDoc })).toBeVisible();
  await expect(page.getByRole('button', { name: '查看关联文档' })).toHaveCount(0);
});

test('L1 文档详情相关推荐：approved 始终渲染区块，空态或推荐卡片可见', { tag: ['L1', 'affects:documents,related'] }, async ({ request, page }) => {
  const unique = Date.now();
  const username = `e2e_rel_${unique}`;
  const userPassword = 'User@123456';
  const docTitle = `推荐回归文档_${unique}`;
  const docTitle2 = `推荐回归文档2_${unique}`;
  const pendingTitle = `推荐回归待审_${unique}`;
  const docContent = `推荐回归唯一内容串${unique}。用于验证文档详情页相关推荐区块在 approved 文档中始终可见，并展示空态或推荐卡片。`.repeat(30);

  // ---------- API 准备：管理员直入库两篇 approved 文档 + 建同部门普通用户 ----------
  const adminLogin = await request.post(`${API}/auth/login`, {
    data: { username: 'admin', password: adminPassword },
  });
  expect(adminLogin.status()).toBe(200);
  const adminToken = (await adminLogin.json()).data.token;
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const deptRes = await request.get(`${API}/auth/departments`, { headers: adminHeaders });
  expect(deptRes.status()).toBe(200);
  const deptId = (await deptRes.json()).data[0].id;

  const createRes = await request.post(`${API}/admin/users`, {
    headers: adminHeaders,
    data: { username, password: userPassword, role: 'user', department_id: deptId },
  });
  expect(createRes.status()).toBe(200);
  expect((await createRes.json()).code).toBe(0);

  const userLogin = await request.post(`${API}/auth/login`, {
    data: { username, password: userPassword },
  });
  expect(userLogin.status()).toBe(200);
  const userToken = (await userLogin.json()).data.token;
  const userHeaders = { Authorization: `Bearer ${userToken}` };

  const uploadApproved = async (title, filename) => {
    const r = await request.post(`${API}/admin/documents/upload`, {
      headers: adminHeaders,
      multipart: {
        file: {
          name: filename,
          mimeType: 'text/plain',
          buffer: Buffer.from(`${title}-${docContent}`, 'utf-8'),
        },
        title,
        department_id: String(deptId),
      },
    });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.code).toBe(0);
    expect(body.data.status).toBe('approved');
    return body.data.id;
  };

  const documentId = await uploadApproved(docTitle, 'related-regression.txt');
  await uploadApproved(docTitle2, 'related-regression-2.txt');

  // F2-4 准备：同用户上传一篇 pending 文档
  const pendingUpload = await request.post(`${API}/documents/upload`, {
    headers: userHeaders,
    multipart: {
      file: {
        name: 'related-pending.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from(`${pendingTitle}-${docContent}`, 'utf-8'),
      },
      title: pendingTitle,
    },
  });
  expect(pendingUpload.status()).toBe(200);
  const pendingBody = await pendingUpload.json();
  expect(pendingBody.code).toBe(0);
  expect(pendingBody.data.status).toBe('pending');
  const pendingId = pendingBody.data.id;

  // ---------- UI：普通用户查看 approved 文档详情 ----------
  await page.goto(`${WEB}/login`);
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(userPassword);
  await page.getByRole('button', { name: '登 录' }).click();
  await expect(page).toHaveURL(/search/);

  // F2-1：approved 文档详情始终渲染“相关推荐”区块
  await page.goto(`${WEB}/documents/${documentId}`);
  await expect(page.getByRole('heading', { name: docTitle })).toBeVisible();
  const block = page.locator('.related-block');
  await expect(block).toBeVisible();
  await expect(block.getByText('相关推荐')).toBeVisible();

  // F2-2/F2-3：先通过 API 获知推荐数据源结果，再严格断言 UI 一致（避免 hasCard||hasEmpty 弱断言）
  const relatedRes = await request.get(`${API}/documents/${documentId}/related`, { headers: userHeaders });
  expect(relatedRes.status()).toBe(200);
  const relatedData = (await relatedRes.json()).data || [];
  if (relatedData.length > 0) {
    await expect(block.locator('.related-card').first()).toBeVisible({ timeout: 30_000 });
    expect(await block.locator('.related-card').count()).toBeGreaterThan(0);
  } else {
    await expect(block.getByText('暂无相关推荐')).toBeVisible({ timeout: 30_000 });
  }

  // F2-4：非 approved 文档不渲染推荐区块
  await page.goto(`${WEB}/documents/${pendingId}`);
  await expect(page.getByRole('heading', { name: pendingTitle })).toBeVisible();
  await expect(page.locator('.related-block')).toHaveCount(0);
});

test('L1 管理端直入库多选部门：下架/上架与重复文件更新', { tag: ['L1', 'affects:admin,upload,documents'] }, async ({ request, page }) => {
  const unique = Date.now();
  const title = `管理端多选直入库_${unique}`;
  const content = `管理端直入库多部门回归验证内容串${unique}，用于验证勾选多个部门、下架重新上架、以及重复文件更新为新版本。`.repeat(5);

  // 管理员登录（沿用首登改密后的新密码）
  await page.goto(`${WEB}/login`);
  await page.getByLabel('账号').fill('admin');
  await page.getByLabel('密码').fill(adminPassword);
  await page.getByRole('button', { name: '登 录' }).click();
  await expect(page).toHaveURL(/search/);

  // 取部门
  const adminLogin = await request.post(`${API}/auth/login`, { data: { username: 'admin', password: adminPassword } });
  const adminToken = (await adminLogin.json()).data.token;
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };
  const deptRes = await request.get(`${API}/auth/departments`, { headers: adminHeaders });
  const depts = (await deptRes.json()).data;
  expect(depts.length).toBeGreaterThanOrEqual(2);
  const dept1Name = depts[0].name;
  const dept2Name = depts[1].name;

  // 打开管理端文档管理并直入库
  await page.goto(`${WEB}/admin/documents`);
  await page.getByRole('button', { name: '上传文档' }).click();
  const dialog = page.locator('.el-dialog', { hasText: '上传文档（直接入库）' });
  await dialog.waitFor();

  // 选择文件
  await dialog.locator('input[type=file]').setInputFiles({
    name: 'admin-multi-dept.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(content, 'utf-8'),
  });
  await dialog.getByPlaceholder('留空则取文件名').fill(title);

  // 多选两个部门
  await dialog.locator('.el-select').click();
  await page.locator('.el-select-dropdown__item:visible', { hasText: dept1Name }).click();
  await page.locator('.el-select-dropdown__item:visible', { hasText: dept2Name }).click();
  await page.keyboard.press('Escape');

  await dialog.getByRole('button', { name: '开始上传' }).click();
  await expect(page.getByText('全部上传成功（1 个）')).toBeVisible({ timeout: 60_000 });

  // 列表展示多部门
  const row = page.locator('.el-table__row', { hasText: title }).first();
  await expect(row).toBeVisible({ timeout: 60_000 });
  await expect(row.getByText(`${dept1Name}、${dept2Name}`)).toBeVisible();

  // 下架 -> 重新上架
  await row.getByRole('button', { name: '下架' }).click();
  await page.getByRole('button', { name: '确定' }).click();
  await expect(row.getByRole('button', { name: '重新上架' })).toBeVisible({ timeout: 30_000 });

  await row.getByRole('button', { name: '重新上架' }).click();
  await page.getByRole('button', { name: '确定' }).click();
  await expect(row.getByRole('button', { name: '下架' })).toBeVisible({ timeout: 30_000 });

  // 重复文件更新为新版本：再次上传同一内容
  await page.getByRole('button', { name: '上传文档' }).click();
  const dialog2 = page.locator('.el-dialog', { hasText: '上传文档（直接入库）' });
  await dialog2.waitFor();
  await dialog2.locator('input[type=file]').setInputFiles({
    name: 'admin-multi-dept.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(content, 'utf-8'),
  });
  await dialog2.getByPlaceholder('留空则取文件名').fill(title);
  // 重复文件可只保留一个部门；这里沿用空=公开，只要能触发更新通道即可
  await dialog2.getByRole('button', { name: '开始上传' }).click();

  // 重复文件确认框：更新为新版本
  await page.getByRole('button', { name: '更新为新版本' }).click();
  await expect(page.getByText('已更新为新版本')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('.el-table__row', { hasText: title })).toHaveCount(1);
});

test('L0 失败注入冒烟（teardown 清理验证专用）', { tag: ['L0', 'affects:infra'] }, async () => {
  base.skip(process.env.EDMS_E2E_FORCE_FAIL !== '1', '未设置 EDMS_E2E_FORCE_FAIL，跳过失败注入');
  expect(1).toBe(2);
});
