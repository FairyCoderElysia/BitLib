# E2E `data-testid` 登记表

本 sprint 未新增任何 `data-testid`。E2E 用例全部使用 Playwright 文本 / 角色 / 标签定位：

| 定位目标 | 定位方式 | 用例 |
|---|---|---|
| 登录页标题「企业资料管理系统」 | `getByText` | L0 健康检查与登录页可达 |
| 登录表单「账号 / 密码」 | `getByLabel` | L0 / L1 admin 首登 / L1 资料主流程 |
| 登录按钮「登 录」 | `getByRole('button', { name: '登 录' })` | L1 admin 首登 / L1 资料主流程 |
| 改密表单「原密码」 | `getByLabel` | L1 admin 首登 |
| 改密表单「新密码」 | `getByPlaceholder('至少 6 位，且不能与旧密码相同')` | L1 admin 首登 |
| 改密表单「确认新密码」 | `getByPlaceholder('再次输入新密码')` | L1 admin 首登 |
| 改密提交按钮「确认修改」 | `getByRole('button', { name: '确认修改' })` | L1 admin 首登 |
| 检索输入框 | `getByPlaceholder('输入关键词搜索文档（支持标题 / 内容全文检索）')` | L1 admin 首登 / L1 资料主流程 |
| 搜索按钮「搜索」 | `getByRole('button', { name: '搜索' })` | L1 资料主流程 |
| 审批中心标题「审批中心」 | `getByRole('heading', { name: '审批中心' })` | L1 资料主流程 |
| 待审批行 / 「通过」按钮 | `getByRole('row', { name: ... })` + `getByRole('button', { name: '通过' })` | L1 资料主流程 |
| 确认弹窗「确定」 | `getByRole('button', { name: '确定' })` | L1 资料主流程 |
| 审批成功（待审批行消失） | `expect(row).toHaveCount(0)` | L1 资料主流程 |
| 检索结果标题 | `getByText(title)` | L1 资料主流程 |
