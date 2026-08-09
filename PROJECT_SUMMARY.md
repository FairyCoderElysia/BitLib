# 企业资料管理系统 · 项目总结（PROJECT_SUMMARY）

> 里程碑：M1–M5 后端 + M6 前端 全部完成 · Sprint 6（M7 部署配置 + 文档 + 论文图表素材）收尾
> 状态：**功能全部落地，44 断言全绿，Evaluator PASS 8.9/10，部署文档齐备**

---

## 1. 项目概述

**企业资料管理系统**是一款可本地私有化部署的中小企业内部文档管理与 AI 知识库平台。系统把散落在员工电脑、网盘中的业务文档统一收拢到私有知识库，提供全文检索、在线预览下载、收藏管理、AI 问答，并通过**审批流 + 三级角色 + 部门隔离**保证资料安全、可控流转。技术形态为前后端分离（FastAPI + Vue3），数据默认存 SQLite（FTS5 关键词索引 + Chroma 向量库），全链路可离线运行。

## 2. 功能清单（P0 8 项全部完成）

| 编号 | P0 功能 | 状态 | 说明 |
|---|---|---|---|
| F1 | 认证与角色权限 | ✅ 完成 | JWT(12h)+bcrypt；三级角色 admin/dept_admin/user；仅管理员建号，无开放注册；首次启动自动播种 admin |
| F2 | 文档上传与审批流 | ✅ 完成 | 白名单 txt/docx/pdf/md、≤200MB、sha256 去重；pending → 审批 → processing → approved/rejected(+原因)；用户可撤回；部门管理员仅本部门待审 |
| F3 | 混合检索 | ✅ 完成 | jieba+FTS5 bm25 与 Embedding+Chroma 双路召回（召回阶段权限过滤）→ RRF 融合 → CrossEncoder 重排 → featured +1.0 加权 → 兜底校验 → top5；热点缓存；语义阈值兜底 |
| F4 | 文档预览与下载 | ✅ 完成 | txt/md 直渲、pdf(pdf.js)、docx(docx-preview)；预览/下载经鉴权 API；下载写审计；pending/rejected 仅上传者+审批者可预览不可下载；offline 全角色不可见 |
| F5 | 资料收藏与收藏夹 | ✅ 完成 | 收藏夹 CRUD；收藏幂等(409)；不可见文档收藏 403；下架/删除后收藏条目显示"已失效" |
| F6 | AI 问答（RAG） | ✅ 完成 | 混合检索召回(权限过滤) → RRF → 重排 → 回溯 parent(small-to-big) → prompt 前二次兜底权限过滤 → LLM 生成；回答附引用来源可点击跳转；找不到时明确提示不编造；LLM/Embedding 双模式可切换 |
| F7 | 文档解析入库管线 | ✅ 完成 | 4 格式解析 → 清洗(控制字符/噪音) → 过短拦截(<50字符→failed) → 父子分片(child~250token 入 Chroma / parent~1200token 入 SQLite) → 向量化；ChunkChild metadata 与 FTS 行冗余 department_id/status 支撑召回阶段过滤；十万字级完整分片；failed/offline 可"重新入库"重试 |
| F8 | 管理端文档管理 | ✅ 完成 | 批量直入库(跳过审批)；列表筛选(状态/部门/来源/格式/重点)；重点标记/改部门/下架上架/重新入库/删除(级联清理文件+分块+向量+收藏)；更新为新版本；审计全记录 |

> 另含爬虫自动入库（Scrapling + APScheduler + SSRF 域名白名单 + sha256 去重）、部门定向推送、审计日志、工作台统计、问答会话历史、部门管理（admin）等扩展功能。

## 3. 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.10 · FastAPI · SQLAlchemy 2.x · SQLite(WAL+FTS5) |
| 检索 | jieba 分词 · FTS5 bm25 · Chroma(HNSW) · RRF 融合 · CrossEncoder(bge-reranker-base) |
| AI | Embedding 双模式（bge-small-zh-v1.5 本地 512 维 / API）· LLM 双模式（Ollama qwen2.5 / OpenAI 兼容）|
| 爬虫 | Scrapling（adaptive/stealthy/JS 渲染，离线降级纯 HTTP）+ APScheduler |
| 前端 | Vue 3 · Vite · Element Plus · Pinia · pdf.js · docx-preview |
| 部署 | Docker Compose（可选 redis，默认 SQLite 无需 postgres）· 裸机脚本 + nginx |
| 文档 | spec.md / docs/DESIGN.md / README / docs/diagrams 论文素材 |

## 4. 测试统计

### 4.1 后端自动化测试（TestClient + assert，独立测试库，运行结束自动清理）

| 脚本 | 覆盖范围 | 断言数 |
|---|---|---|
| `scripts/test_m1m2.py` | M1+M2：认证/角色/上传/审批/部门隔离/撤回/直入库/审计/用户管理 | **21** |
| `scripts/test_m3.py` | M3：4 格式解析入库/清洗拦截/分块/FTS+Chroma/超长还原/重新入库 | **8** |
| `scripts/test_m4.py` | M4：双路检索/权限过滤/featured 置顶/状态可见矩阵/收藏夹/RAG 引用/缓存/SearchLog | **9** |
| `scripts/test_m5.py` | M5：爬虫入库/去重/SSRF 白名单/推送/已读/统计/审计筛选 | **6** |
| **合计** | | **44 断言全绿** |

### 4.2 评估与 E2E

- **Evaluator PASS 8.9/10**（`eval-reports/eval-5.md`）：功能完整性 9 / 交互可用性 9 / 视觉设计 9 / 工程健壮性 8.5；
- **Playwright E2E 抽查**：登录 → 检索 → 预览 → 下载 → 收藏 → 问答 → 审批 → 推送 主流程生效；
- 前端 `npm run build` 通过（dist/ 产物齐全含 pdf.worker）。

## 5. 体验后修复的 7 个问题记录

用户体验阶段共发现 7 项问题，已全部处理：

| # | 问题 | 结论 | 修复方案 |
|---|---|---|---|
| 1 | 无法管理部门 | 真实缺口 | 后端部门管理 4 接口（含引用保护 409）+ 前端部门管理页/菜单/路由，CRUD 实测通过 |
| 2 | 通知红点不消失 | 真实 bug | 已读后派发 `notif-changed` 事件联动顶栏角标**立即刷新** |
| 3 | 收藏页无入口 | 真实缺口 | 顶栏新增"我的收藏"入口 |
| 4 | AI 对话无法保存 | 真实缺口 | 后端会话列表/历史 API + ask 会话续接（含跨用户 400 保护）+ 前端会话侧边栏，点击历史会话可完整还原对话与引用 |
| 5 | admin 搜索受限 | 🔴 严重 bug | `keyword_recall` 将 admin 错误限制为仅公开文档 → 修复为 admin 跳过部门过滤，实测全量返回且相关文档排第一 |
| 6 | 入库解析失败 | 已修复的服务竞态 | 根因是 uvicorn 首启 embedding 加载竞态 → **新增启动后台预热根治** |
| 7 | zhangsan 权限异常 | 非 bug（数据误解） | 实测语义路过滤正确（市场部文档对 zhangsan 双路拦截），原"全可见"系演示数据无跨部门文档，已补跨部门文档验证隔离 |

> 另顺手修复：`QARequest.session_id` int/str 类型不匹配（前端续接 422）、favicon 404、废弃死代码清理。

## 6. 部署方式

- **裸机（Windows 快速开始）**：`pip install` 后端依赖 → 配置 `.env` → `python -m uvicorn app.main:app --port 8000`（或 `scripts\start.bat`）；前端 `npm install && npm run dev`（开发）或 `npm run build` + nginx 反代（生产，见 README §7 nginx 示例）；
- **Docker Compose**：`docker compose up -d`，`data/` 卷持久化 app.db + uploads/ + chroma/；redis 为可选服务；
- **备份/恢复**：`python scripts\backup.py` 打包 `data/` 为按日期命名的 zip；恢复 = 停服 → 解压回 `data/` → 重启；
- **AI 问答启用**：本地模式（Ollama qwen2.5 + bge-small-zh-v1.5 + bge-reranker-base，完全离线）或 API 模式（OpenAI 兼容，注意 `EMBEDDING_DIM` 与向量库一致）。

## 7. 论文素材索引（docs/diagrams/，mermaid 源，可直接粘贴 mermaid.live 渲染）

| 文件 | 对应论文章节 | 内容 |
|---|---|---|
| `arch.mmd` | 系统设计·总体架构 | 前端 → Nginx → FastAPI 各模块 → SQLite/Redis/Chroma/Ollama |
| `er.mmd` | 系统设计·数据库设计 | 15 张业务表 + DocumentFTS 虚拟表 + ChunkChild(Chroma) 全关系 |
| `approval-flow.mmd` | 系统设计·核心流程 | 上传 → 待审批 → 审批 → 解析入库管线 → approved 时序 |
| `search-flow.mmd` | 系统实现·检索 | 关键词路+语义路 → RRF → 重排 → 权限兜底 → 结果 流程图 |
| `rag-flow.mmd` | 系统实现·AI 问答 | 问题 → 召回 → 回溯 parent → LLM → 回答+引用 流程图 |
| `deploy.mmd` | 系统实现·私有化部署 | 局域网客户端 → 内网服务器拓扑图 |

## 8. 后续建议（P1 / P2）

| 优先级 | 建议 | 说明 |
|---|---|---|
| P1 | 爬虫 stealthy 增强 | JS 渲染站点反爬兜底需浏览器二进制；可增加采集策略模板与重试/代理 |
| P1 | 会话历史增强 | 前端会话重命名/删除、问答导出、会话维度全文检索 |
| P2 | 热词统计 | 基于 SearchLog 聚合热词榜（含部门维度）并展示在检索首页 |
| P2 | 批量下载 | `POST /documents/batch-download` 流式 zip（spec §10.8），不可见文档自动剔除 |
| P2 | 相关推荐 | 基于文档向量聚合的"相似文档"推荐位 |
| P2 | 智能摘要 | `Document.summary` 字段已预留，接 LLM 生成摘要供检索卡片展示 |
| P2 | 数据库演进 | 生产规模增长可切 PostgreSQL（tsvector）并做检索压测对比（论文对比实验） |
| P2 | 前端自动化 | 补前端单元/E2E 测试资产，与后端 M1–M5 脚本对齐 |

## 9. 关键文档索引

| 文档 | 位置 |
|---|---|
| 产品规格书 | `spec.md` |
| 技术设计 v4 | `docs/DESIGN.md` |
| 部署与使用指南 | `README.md` |
| 各里程碑契约与交付摘要 | `contracts/` |
| 评估报告 | `eval-reports/eval-5.md` |
