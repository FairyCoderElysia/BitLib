# 企业资料管理系统 — 需求规格与技术设计（v4 定稿）

> 状态：需求规格已定稿（全部决策已确认），代码尚未开始。
> 本文件是"需要做什么"的权威依据，实现阶段以本文档为准。

## 1. 项目背景

中小型企业内部业务文档、技术资料分散存储，存在查找缓慢、文件重复、资料泄露风险高、
缺少统一推送分发渠道等痛点。本系统提供可本地私有化部署的企业资料管理系统，
结合向量知识库与 RAG AI 问答，解决资料存储、检索、运维管理难题。

## 2. 角色与权限矩阵（已确认）

| 功能 | 管理员 | 部门管理员 | 普通用户 |
|---|---|---|---|
| 检索 / 下载 / 收藏 / AI 问答 | ✅ | ✅ | ✅（仅可见文档） |
| 上传文档 | ✅ | ✅ | ✅（需审批） |
| 撤回自己的待审批上传 | ✅ | ✅ | ✅ |
| 审批上传文档 | ✅ 全部 | ✅ 本部门 | — |
| 文档下架/删除 | ✅ 任意 | ✅ 本部门 | — |
| 重点资料标记 | ✅ 任意 | ✅ 本部门 | — |
| 爬虫任务配置 | ✅ | — | — |
| 部门定向推送 | ✅ 任意部门 | ✅ 本部门 | — |
| 用户管理（建号/角色/部门） | ✅ | — | — |
| 审计日志查看 | ✅ | — | — |

**可见范围**：文档 `department_id` 为空 = 公开，全员可见；非空 = 仅该部门成员可见。
管理员可见全部，部门管理员可见本部门全部。

**账号创建（已确认）**：仅管理员建号，系统不做开放注册。首次启动自动播种 admin 账号。

## 3. 功能需求

### 3.1 用户端（普通用户/部门管理员）
- **全文检索**：关键词 + 语义双路召回，仅返回可见文档，重点标记加权
- **原文在线预览 + 下载**：权限校验后预览/下载
- **资料收藏**：收藏到个人收藏夹；收藏夹增删改
- **AI 问答**：RAG，仅基于可见文档回答，附引用来源
- **我的上传**：审批状态查看（待审批/通过/被拒+原因），撤回待审批文档
- **通知中心**：部门定向推送，轮询获取，已读/未读

### 3.2 审批流
```
普通用户上传 → pending（仅存文件，不解析不入库）
    → 管理员/部门管理员审批
        ├─ 通过：触发 解析 → 分块 → 向量化 → 入库(approved) → 可检索
        └─ 拒绝：rejected，附拒绝原因
用户可撤回自己 pending 的文档（撤回后删除文件）
```

### 3.3 管理端
- **工作台**：文档数/待审批数/爬虫状态等统计
- **审批中心**：待审批列表、查看原文、批量通过/拒绝（管理员全部，部门管理员仅本部门）
- **文档管理**：批量上传（管理员/部门管理员直接入库）、多格式导入、重点标记、下架/删除
- **爬虫任务**：配置（起始 URL/允许域名/深度/选择器/cron）、启停、运行记录
- **用户管理**：创建账号、分配角色与部门、重置密码
- **部门推送**：新建推送（可选关联文档）、历史记录
- **审计日志**：关键操作查询（见 3.6）

### 3.4 爬虫（Scrapling 引擎）
- 管理员配置任务：起始 URL、允许域名、抓取深度、正文选择器（可留空=智能提取）、
  cron 定时表达式
- 抓取引擎用 **Scrapling**：adaptive 抓取 + stealthy 反爬 + JS 渲染 + 智能正文提取
- 抓取内容**清洗后直接入库**（无需审批，管理员配置任务即授权行为）
- 调度：APScheduler 按 cron 执行

### 3.5 AI / 知识库层
- 向量化存储：所有文档（上传+爬虫）分块后转向量存 Chroma
- RAG 问答：问题 → 向量召回 TopK（权限过滤）→ LLM 生成回答 + 引用来源
- 超长文本：**父子分片（small-to-big）**——child 小块检索（~250 token）+ parent 大块送 LLM（~1200 token），详见第 8 章
- 多格式解析：txt / docx / pdf / md

### 3.6 审计日志（新增，已确认）
记录关键操作，管理端可查询：
- 文档类：上传、撤回、审批通过/拒绝、下架、删除、下载
- 系统类：爬虫任务创建/启停/执行、推送创建、用户创建/角色变更
- 字段：操作人、动作、对象类型、对象 ID、详情、IP、时间

### 3.7 文本清洗规则（细化）
入库前统一清洗：
1. 去除控制字符、多余空白、统一换行符
2. 爬虫文本剥离 HTML 标签残留、导航/页脚/广告噪音（选择器未命中时用智能提取）
3. 空内容/过短内容（<50 字符）标记 failed 不入库
4. 文件级去重：sha256 相同则跳过（新上传提示"已存在"）

## 4. 技术选型（已确认）

| 组件 | 选型 | 理由 |
|---|---|---|
| 后端框架 | FastAPI | 异步高性能、自动 OpenAPI |
| ORM | SQLAlchemy 2.x | 默认 SQLite，生产可切 PostgreSQL |
| 认证 | JWT (pyjwt) + bcrypt | 自建用户表 + 部门 |
| 缓存/会话 | Redis（可选，未配置降级内存 TTL） | 热点搜索加速 |
| 向量数据库 | Chroma | pip 即用、持久化、万~十万级够用 |
| Embedding | bge-small-zh-v1.5（本地）/ OpenAI 兼容 API | 双模式可切换 |
| 重排序 | bge-reranker-base（本地 cross-encoder）/ 可选 API | 召回后精排，仅对候选集，不扫全库 |
| 关键词检索 | jieba 分词 + SQLite FTS5 (bm25) | 中文分词关键词召回；生产切 PostgreSQL tsvector |
| LLM | Ollama qwen2.5 / OpenAI 兼容 API | 双模式可切换 |
| 文档解析 | chardet / python-docx / PyMuPDF | txt/docx/pdf/md |
| 预览 | 前端 pdf.js / docx-preview / 文本直渲 | 原文在线预览 |
| 爬虫 | Scrapling + APScheduler | 智能反爬 + 定时 |
| 前端 | Vue 3 + Vite + Element Plus + Pinia | 中文生态成熟 |
| 部署 | Docker Compose（可选）/ 裸机脚本 | 私有化 |

## 5. 数据模型

- **Department**: id, name
- **User**: id, username, password_hash, role(admin/dept_admin/user), department_id, created_at
- **Document**: id, title, file_name, file_path, file_type, file_size, file_hash,
  content_text, status(pending/approved/rejected/offline), is_featured,
  department_id(空=公开), source(upload/crawl), uploaded_by, approver_id,
  approved_at, reject_reason, created_at, updated_at
- **ChunkParent**（SQLite）: id, document_id, chunk_index, title, text —— 上下文单元（~1200 token），回溯后送 LLM
- **ChunkChild**（Chroma）: document_id, parent_id, chunk_index, text, embedding —— 检索单元（~250 token）
- **FavoriteFolder**: id, user_id, name, created_at
- **Favorite**: id, user_id, folder_id, document_id, created_at（user+doc 唯一）
- **CrawlTask**: id, name, start_urls(JSON), allowed_domains(JSON), selector, max_depth,
  schedule(cron), enabled, status, last_run_at, created_by, created_at
- **PushNotification**: id, title, content, document_id, department_id(空=全员),
  created_by, created_at
- **PushRead**: notification_id, user_id, read_at
- **AuditLog**: id, user_id, action, target_type, target_id, detail, ip, created_at

## 6. 核心流程

### 文档入库（上传路径）
```
用户上传 → pending → 管理员审批
    ├─ 通过 → 解析 → 清洗 → 分块 → Embedding → Chroma + Document(approved)
    └─ 拒绝 → rejected + 原因
```

### 文档入库（爬虫路径）
```
Cron 触发 → Scrapling 抓取 → 清洗 → 直接入库（同解析→分块→向量化管线）
```

### 检索（混合检索 + 重排序）
```
查询 → [关键词: jieba 分词 → FTS5 bm25] + [语义: Embedding → Chroma child 召回] 各 top30
    → RRF 融合去重 → CrossEncoder 重排 → top5 → 权限过滤 → 返回（重点标记加权）
```

### AI 问答（RAG）
```
问题 → 混合检索召回 → RRF 融合 → CrossEncoder 重排 → 回溯 parent 块（small-to-big）
    → 权限过滤 → 组装 prompt → LLM → 回答 + 引用来源
```

### 推送
```
创建推送（可选关联文档）→ 按部门/全员 → 用户端轮询通知中心 + 已读标记
```

## 7. 页面清单

### 用户端
- 登录页
- 检索/首页（搜索 + 结果 + 问答入口）
- 文档详情（**原文在线预览**：pdf→pdf.js，docx→docx-preview，txt/md→直渲 + 下载/收藏）
- 收藏夹（列表 + 管理）
- AI 问答页（对话式）
- 我的上传（状态/撤回/拒绝原因）
- 通知中心（轮询，已读/未读）

### 管理端
- 工作台（统计概览）
- 审批中心（待审批/查看原文/批量通过/拒绝）
- 文档管理（上传/列表/标记重点/下架/删除）
- 爬虫任务（配置/启停/运行记录）
- 用户管理（建号/角色/部门/重置密码）
- 部门推送（新建/历史）
- 审计日志（按操作/对象/时间筛选查询）

## 8. 超长文本处理（父子分片 small-to-big）

**问题**：单一分块大小无法兼顾"检索精准"与"上下文完整"——块小则上下文断裂，
块大则向量语义被稀释。超长文档（数万~数十万字）此矛盾更突出。

**方案**：两层分片，小块检索、大块送 LLM。

| 层级 | 角色 | 大小 | 存储位置 |
|---|---|---|---|
| child 块 | 检索单元，向量化入库 | ~250 token（约 380 中文字符），overlap 30 | Chroma |
| parent 块 | 上下文单元，回溯后送 LLM | ~1200 token（约 1800 中文字符），按标题/段落聚合 | SQLite |

**分块算法**：
1. 按标题/章节/段落边界构建层级：章节 → parent，parent 内按句/段切 child（带 overlap）
2. child 带 metadata：document_id, parent_id, chunk_index
3. parent 全文存 SQLite，child 向量存 Chroma

**检索回溯（small-to-big）**：命中 child → 取回其 parent 全文 → 同文档多命中合并去重
→ 组装 prompt。

**为什么适合超长文本**：
- child 小 → 长文中任意细节（哪怕藏在文档中段）都能被精准命中
- parent 大 → LLM 拿到完整上下文，不因切碎而答错
- 十万字级文档 child 数量约 400 块，向量库规模可控

**重排序（Reranking）**：混合检索双路各召回 top30，RRF（Reciprocal Rank Fusion）
融合去重后，用 cross-encoder（bge-reranker-base）逐对打分精排取 top5——
弥补双塔 embedding 的精度天花板；因只对候选集精排，延迟可接受。

## 9. 目录结构（规划，代码未开始）

```
backend/
  app/
    main.py          # FastAPI 入口
    config.py        # 配置（pydantic-settings + .env）
    db.py            # 数据库会话 + 播种
    models.py        # ORM 模型（含 AuditLog）
    schemas.py       # Pydantic schema
    security.py      # JWT + bcrypt
    deps.py          # 依赖注入（当前用户/角色校验）
    cache.py         # Redis/内存 TTL 缓存抽象
    audit.py         # 审计日志写入
    parsers.py       # txt/docx/pdf/md 解析
    chunker.py       # 超长文本分块
    embeddings.py    # embedding 双模式
    vector_store.py  # Chroma 封装
    llm.py           # LLM 双模式
    rag.py           # RAG 问答管线
    crawler.py       # Scrapling 通用爬虫
    scheduler.py     # APScheduler 定时
    routers/         # auth/documents/search/qa/favorites/admin/crawl
  requirements.txt
  .env.example
frontend/            # Vue3 + Element Plus + pdf.js + docx-preview
docs/
  DESIGN.md          # 本文档
```

## 10. 决策记录

| 决策点 | 结论 |
|---|---|
| 前端技术栈 | Vue 3 + Element Plus |
| LLM/Embedding 部署 | 双模式：本地（Ollama/sentence-transformers）+ OpenAI 兼容 API 可切换 |
| 爬虫方案 | Scrapling 通用爬虫 + APScheduler 定时 |
| 认证方式 | JWT 账号密码，仅管理员建号 |
| 可见范围 | 部门隔离 + 公开文档 |
| 爬虫内容审批 | 直接入库，不走审批 |
| 角色体系 | 三级：管理员 / 部门管理员 / 普通用户 |
| 删除规则 | 管理员全权；部门管理员本部门；用户可撤回自己待审稿 |
| 推送实时性 | 前端轮询通知中心（不引入长连接） |
| 文档预览 | 原文在线预览（pdf.js / docx-preview） |
| 审计日志 | 记录关键操作，管理端可查 |
| 数据库 | 默认 SQLite，生产可切 PostgreSQL（SQLAlchemy 抽象） |

## 11. 环境检查结论（2025-08 实测）

| 项 | 状态 | 说明 |
|---|---|---|
| Python 3.10.11 | ✅ | 满足 |
| scrapling 0.4.12 | ✅ **已装** | 爬虫方案零额外依赖 |
| fastapi / uvicorn / sqlalchemy / pyjwt / bcrypt / chardet / python-docx / bs4 / httpx | ✅ 已装 | 核心后端依赖就绪 |
| chromadb | ❌ 待装 | 向量库 |
| PyMuPDF (fitz) | ❌ 待装 | pdf 解析 |
| sentence-transformers | ❌ 待装 | 本地 embedding（自动带入 torch，体积约 2GB+） |
| redis-py | ❌ 待装 | 可选（未配置降级内存缓存） |
| apscheduler | ❌ 待装 | 爬虫定时 |
| NVIDIA GPU | ✅ 存在 | Ollama 本地 7B 模型可行 |
| Ollama CLI | ❌ 未装 | 本地 LLM 模式需安装；也可直接用 API 模式 |
| Node v24.9.0 / npm 11.6.0 | ✅ | 前端就绪 |

**实现前安装清单**：
```bash
pip install chromadb PyMuPDF sentence-transformers redis apscheduler jieba
# 本地 reranker 模型（首次运行时自动下载 ~1.1GB，可选）
# python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-base')"
# 可选：本地 LLM 模式
# winget install Ollama.Ollama && ollama pull qwen2.5:7b
```

## 12. 非功能需求

### 性能
- 目标：万~十万级文档检索毫秒级响应
- 手段：Chroma HNSW 向量索引 + SQLite FTS5 (bm25) 关键词索引 + Redis 热点搜索缓存
- 重排序仅对融合后 ~30 条候选精排（cross-encoder 每对约几十 ms），不扫描全库
- 生产切 PostgreSQL 可再提升（tsvector 替代 FTS5）

### 安全
- bcrypt 密码哈希、JWT 过期（默认 12h）
- 全部接口角色/权限校验；文档可见性过滤（部门隔离）
- 上传白名单：仅 txt/docx/pdf/md，大小上限 200MB
- 文件存储于私有目录，下载仅经 API 鉴权路由，不暴露静态路径
- 爬虫 SSRF 防护：仅允许任务配置的域名白名单
- 关键操作审计日志

### 备份
- `data/` 目录（app.db + uploads/ + chroma/）整体备份，提供备份脚本
- 建议每日定时备份至异地/云盘

### 部署
- Docker Compose（postgres / redis / chroma / ollama 可选组件）
- 或裸机脚本：`pip install -r requirements.txt` + uvicorn + npm build + nginx

## 13. API 接口契约（规划）

> 前后端分离依据。统一前缀 `/api`，鉴权用 `Authorization: Bearer <token>`。

| 模块 | 方法与路径 | 说明 | 角色 |
|---|---|---|---|
| 认证 | POST /auth/login | 登录返回 token+用户信息 | 公开 |
| | GET /auth/me | 当前用户 | 登录 |
| | GET /auth/departments | 部门列表 | 登录 |
| 文档 | POST /documents/upload | 用户上传（→pending） | 登录 |
| | GET /documents/mine | 我的上传（含审批状态） | 登录 |
| | GET /documents/{id} | 详情（含解析文本） | 可见者 |
| | GET /documents/{id}/preview | 原文在线预览（原文件流） | 可见者 |
| | GET /documents/{id}/download | 下载 | 可见者 |
| | DELETE /documents/{id} | 撤回自己的待审稿 | 上传者 |
| 检索 | GET /search?q=&page= | 关键词+语义双路 | 登录 |
| 问答 | POST /qa/ask | RAG 问答 | 登录 |
| 收藏 | GET/POST /favorites/folders | 收藏夹列表/新建 | 登录 |
| | GET/POST/DELETE /favorites | 收藏/取消/列表 | 登录 |
| 通知 | GET /notifications | 通知列表（含未读数） | 登录 |
| | POST /notifications/{id}/read | 标记已读 | 登录 |
| 审批 | GET /admin/pending | 待审批列表 | admin/dept_admin |
| | POST /admin/pending/{id}/approve | 通过（触发入库管线） | admin/dept_admin |
| | POST /admin/pending/{id}/reject | 拒绝（附原因） | admin/dept_admin |
| | POST /admin/pending/batch | 批量通过/拒绝 | admin/dept_admin |
| 文档管理 | POST /admin/documents/upload | 批量上传（直接入库） | admin/dept_admin |
| | PATCH /admin/documents/{id} | 标记重点/改部门/下架 | 按权限 |
| | DELETE /admin/documents/{id} | 删除 | admin |
| 爬虫 | GET/POST /admin/crawl-tasks | 列表/新建 | admin |
| | PATCH/DELETE /admin/crawl-tasks/{id} | 修改/删除 | admin |
| | POST /admin/crawl-tasks/{id}/run | 手动执行 | admin |
| 用户 | GET/POST /admin/users | 列表/建号 | admin |
| | PATCH /admin/users/{id} | 改角色/部门/重置密码 | admin |
| 推送 | POST /admin/push | 新建推送（可选关联文档/部门） | admin/dept_admin |
| 审计 | GET /admin/audit-logs | 审计查询（筛选） | admin |
| 统计 | GET /admin/stats | 工作台统计 | admin |

## 14. 开发里程碑与验收标准

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| M1 后端骨架+认证 | FastAPI 工程、配置、ORM、JWT、角色 | 登录/鉴权/角色冒烟测试通过 |
| M2 上传审批流 | 上传→pending→审批→入库/拒绝、撤回 | 全流程 API 测试（含权限：dept_admin 仅见本部门待审） |
| M3 解析入库管线 | parsers/chunker/embedding/vector_store、清洗 | 4 种格式样例文档入库，向量可检索召回 |
| M4 检索+RAG | 双路检索、权限过滤、重点加权、问答 | 样例文档命中 + 问答引用来源正确 |
| M5 爬虫+推送+审计 | Scrapling 抓取入库、cron、部门推送、审计日志 | 真实网页抓取入库可检索；推送未读数正确 |
| M6 前端 | 用户端+管理端全部页面、原文预览 | 主流程 E2E 走通：登录→上传→审批→检索→预览→下载→收藏→问答→推送 |
| M7 部署+论文素材 | Docker/裸机部署、README、架构图/ER 图/流程图素材 | 干净环境可一键启动；论文图表齐备 |

每里程碑交付时可运行的 demo + 自检脚本（assert 冒烟）。

## 15. 论文支撑映射

| 论文章节 | 素材来源 |
|---|---|
| 相关技术 | FastAPI、向量检索 HNSW、RAG 架构、Scrapling 反爬 |
| 需求分析 | 本文档第 1/2/3 章 + 权限矩阵 + 用例 |
| 系统设计 | 本文档第 4/5/6 章：架构图、ER 图、时序图、接口契约 |
| 系统实现 | M1-M6 模块代码与说明 |
| 系统测试 | 验收标准 + 冒烟/接口测试结果 |
| 创新点候选 | ① 混合检索（RRF 融合）+ 父子分片 + 重排序的 RAG 管线 ② 部门隔离+审批流+三级角色权限体系 ③ 双模式 LLM/Embedding/Reranker 私有化适配 ④ Scrapling 通用爬虫与向量库一体化管线 |

论文图表清单：系统架构图、数据模型 ER 图、审批流时序图、检索流程图、RAG 问答流程图、部署拓扑图。


