# 企业资料管理系统（私有化部署）

> 一款可本地私有化部署的中小企业内部**文档管理与 AI 知识库平台**：把散落在员工电脑、网盘中的业务文档统一收拢到私有企业知识库，提供**全文检索、在线预览下载、收藏管理、AI 问答**，并通过**审批流 + 三级角色 + 部门隔离**保证资料安全、可控地流转。
>
> 亮点：混合检索（关键词 + 语义双路，RRF 融合 + CrossEncoder 重排）、父子分片 small-to-big 超长文档 RAG、召回阶段权限过滤、双模式 LLM/Embedding 私有化适配、Scrapling 爬虫自动扩充知识库、数据不出内网。

---

## 1. 核心功能

### 1.1 用户端

- **全文检索**：jieba 分词 + FTS5 bm25 关键词路 与 Embedding + Chroma 语义路双路召回，RRF 融合去重 + CrossEncoder 重排，重点文档加权置顶；**召回阶段即按可见性过滤**，不可见文档绝不进入结果；
- **原文在线预览 / 下载**：txt/md 文本直渲、pdf 走 pdf.js、docx 走 docx-preview；预览下载均经鉴权 API，私有目录不暴露静态路径，下载写审计日志；
- **资料收藏**：个人收藏夹分类管理（新建/重命名/删除），同文档收藏幂等；文档下架/删除后条目显示"已失效"；
- **AI 问答（RAG）**：自然语言提问 → 混合检索（权限过滤）→ 回溯 parent 上下文块 → LLM 生成，回答**附引用来源**（标题 + 片段 + 点击跳转原文），找不到可信答案时明确提示不编造；
- **我的上传**：上传记录 + 审批状态（待审批/通过/被拒含原因/失败）+ 撤回待审批文档；
- **通知中心**：管理员部门定向推送，顶栏未读角标（30s 轮询），单条/全部已读，关联文档跳转。

### 1.2 管理端（管理员 / 部门管理员）

- **工作台**：文档数 / 待审批 / 爬虫状态 / 部门用户数 / 近 7 日趋势统计；
- **审批中心**：待审批列表 + 原文预览 + 单条/批量通过·拒绝（附原因），部门管理员仅见本部门待审；
- **文档管理**：批量上传**直接入库**（跳过审批）、列表筛选（状态/部门/来源/格式/重点）、重点标记、改部门、下架/上架、重新入库（failed/offline 重试）、删除；
- **爬虫任务**：配置起始 URL / 域名白名单（SSRF 防护）/ 深度 / 正文选择器 / cron，启停 + 手动执行 + 运行记录；
- **用户管理**：仅管理员建号（角色/部门/重置密码/删除）；
- **部门推送**：定向到部门或全员，可关联文档；
- **审计日志**：关键操作全量记录（操作人/对象/IP/时间），可按动作/对象/时间筛选。

### 1.3 AI 知识库

- 文档解析入库管线：txt（chardet）/ docx / pdf（PyMuPDF）/ md → 清洗 → 过短拦截 → **父子分片**（child ~250 token 检索块入 Chroma，parent ~1200 token 上下文块入 SQLite）→ 向量化 → approved 即可检索；
- **混合检索 + RAG**：召回阶段权限过滤 → RRF 融合 → CrossEncoder 重排 → small-to-big 回溯 parent → prompt 组装前二次兜底权限过滤 → LLM 生成；
- **双模式适配**：LLM / Embedding / Reranker 均支持本地模型（Ollama / sentence-transformers）与 OpenAI 兼容 API 切换；切换 embedding 维度触发全量重建向量索引。

---

## 2. 技术架构

### 2.1 组件表

| 层 | 组件 | 选型 |
|---|---|---|
| 前端 | Vue 3 + Vite + Element Plus + Pinia | pdf.js / docx-preview 在线预览；开发 proxy `/api → localhost:8000` |
| 后端框架 | FastAPI + uvicorn | 异步高性能、自动 OpenAPI |
| ORM / 数据库 | SQLAlchemy 2.x + SQLite（WAL + busy_timeout） | 默认 SQLite，生产可切 PostgreSQL |
| 关键词检索 | jieba 分词 + SQLite FTS5（bm25） | 中文分词；FTS 行冗余 `department_id/status` 支撑召回阶段过滤 |
| 向量库 | Chroma（HNSW） | pip 即用、持久化，万~十万级够用 |
| Embedding | bge-small-zh-v1.5（本地，512 维）/ OpenAI 兼容 API | 双模式可切换，切换维度需重建索引 |
| 重排序 | bge-reranker-base（CrossEncoder）/ 可关闭 | 仅对融合后 ~30 候选精排，不扫全库 |
| LLM | Ollama qwen2.5 / OpenAI 兼容 API | 双模式可切换 |
| 爬虫 | Scrapling（adaptive + stealthy + JS 渲染）+ APScheduler | 智能反爬 + cron 定时；离线自动降级纯 HTTP |
| 缓存 | Redis（可选） | 未配置自动降级内存 TTL 缓存（热点搜索） |
| 文档解析 | chardet / python-docx / PyMuPDF | txt / docx / pdf / md |
| 认证 | JWT（默认 12h）+ bcrypt | 仅管理员建号，无开放注册 |
| 部署 | Docker Compose（可选）/ 裸机脚本 + nginx | 私有化双方案 |

### 2.2 架构图（ASCII）

```
┌────────────────────────────────────────────────────────────┐
│                    企业局域网（数据不出内网）                  │
│                                                            │
│  浏览器 ──HTTP──▶ Nginx :80/443 ──▶ 前端静态资源 dist/       │
│                     │                                      │
│                     └── /api 反代 ──▶ FastAPI 后端 :8000    │
│                                       │                    │
│   ┌───────────────────────────────────┼──────────────────┐ │
│   │  FastAPI 应用层（routers/）                            │ │
│   │  auth · documents · admin(审批/管理) · search · qa     │ │
│   │  favorites · crawl · notifications                    │ │
│   │  ┌────────────────────────────────────────────────┐   │ │
│   │  │ 核心服务                                        │   │ │
│   │  │ parsers → cleaning → chunker → ingest → fts    │   │ │
│   │  │ search_service → rag → llm → embeddings →       │   │ │
│   │  │ rerank → vector_store → crawler → scheduler     │   │ │
│   │  │ cache(Redis/内存) · audit · visibility(权限过滤) │   │ │
│   │  └────────────────────────────────────────────────┘   │ │
│   └───────────┬──────────────┬──────────────┬─────────────┘ │
│               ▼              ▼              ▼                │
│          SQLite data/   Chroma data/    data/uploads/       │
│          (app.db, FTS5)  (向量持久化)      (私有文件)        │
│               │                                            │
│   ┌───────────┼───────────────┐                            │
│   ▼           ▼               ▼                            │
│ Redis(可选)  Ollama(可选)    API(可选)                      │
│ 热点缓存     本地 LLM/嵌入     OpenAI 兼容接口               │
└────────────────────────────────────────────────────────────┘
```

---

## 3. 目录结构

```
E:\毕业论文\
├─ spec.md                    # 产品规格（功能/数据模型/API 契约）
├─ spec-review.md             # 需求评审记录
├─ docs/
│  ├─ DESIGN.md               # 技术设计 v4（架构/流程/部署）
│  └─ diagrams/               # 论文图表素材（mermaid 源，可贴 mermaid.live 渲染）
│     ├─ arch.mmd             # 系统架构图
│     ├─ er.mmd               # 数据模型 ER 图
│     ├─ approval-flow.mmd    # 上传审批时序图
│     ├─ search-flow.mmd      # 混合检索流程图
│     ├─ rag-flow.mmd         # RAG 问答流程图
│     └─ deploy.mmd           # 私有化部署拓扑图
├─ backend/
│  ├─ app/
│  │  ├─ main.py              # FastAPI 入口（lifespan 建表播种/预热/路由/异常）
│  │  ├─ config.py            # 配置（pydantic-settings + .env）
│  │  ├─ db.py                # 引擎/会话/建表播种 + DocumentFTS
│  │  ├─ models.py            # ORM 模型（15 张表 + FTS5 虚拟表）
│  │  ├─ security.py / deps.py / errors.py / audit.py
│  │  ├─ parsers.py / cleaning.py / chunker.py / ingest.py / fts.py
│  │  ├─ embeddings.py / vector_store.py / rerank.py / llm.py / rag.py
│  │  ├─ search_service.py / visibility.py / cache.py
│  │  ├─ crawler.py / scheduler.py
│  │  └─ routers/             # auth/documents/admin/search/qa/favorites/crawl/notifications
│  ├─ scripts/
│  │  ├─ test_m1m2.py         # M1+M2 认证+上传审批流（21 断言）
│  │  ├─ test_m3.py           # M3 解析入库管线（8 断言）
│  │  ├─ test_m4.py           # M4 检索+RAG+收藏（9 断言）
│  │  ├─ test_m5.py           # M5 爬虫+推送+审计（6 断言）
│  │  ├─ seed_demo.py         # 演示数据种子（Evaluator E2E 用）
│  │  ├─ start.bat            # Windows 一键启动后端
│  │  └─ backup.py            # data/ 备份脚本
│  ├─ data/                   # 运行时数据（SQLite/上传文件/Chroma，备份对象）
│  ├─ requirements.txt        # 依赖清单（M3+ 依赖需取消注释安装，见 §6.1）
│  └─ .env.example            # 环境配置模板（全字段）
├─ frontend/
│  ├─ src/
│  │  ├─ views/user/          # 登录/检索/文档详情/收藏夹/AI 问答/我的上传/通知
│  │  ├─ views/admin/         # 工作台/审批/文档管理/爬虫/用户/部门/推送/审计
│  │  ├─ components/          # PreviewDocument（pdf/docx/txt 三格式预览）
│  │  ├─ api/ · stores/ · router/ · utils/ · styles/ · layout/
│  ├─ package.json            # vite build；开发 proxy /api → localhost:8000
│  └─ dist/                   # 前端构建产物（生产部署用）
├─ contracts/                 # 各里程碑契约与交付摘要
├─ eval-reports/              # Evaluator 评估报告
├─ docker-compose.yml         # Docker Compose（可选 redis，默认 SQLite 无需 postgres）
└─ PROJECT_SUMMARY.md         # 项目总结（harness Phase 3）
```

---

## 4. 快速开始（Windows 裸机）

### 4.1 后端

```bat
cd backend
python -m venv .venv
.venv\Scripts\activate

REM 安装依赖（M3+ 解析/向量/爬虫等依赖在 requirements.txt 中注释，需一并安装）
pip install -r requirements.txt
pip install python-docx PyMuPDF chromadb jieba sentence-transformers redis apscheduler scrapling

REM 配置环境（复制模板 → 设置演示管理员初始密码）
copy .env.example .env
REM 编辑 .env，至少设置：
REM   ADMIN_INITIAL_PASSWORD=admin123

REM 启动（或直接运行 scripts\start.bat）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

首次启动自动建表、播种 3 个默认部门与 `admin` 账号（密码取 `ADMIN_INITIAL_PASSWORD`，缺省随机生成并打印到启动日志），并后台预热 embedding 模型。

### 4.2 前端

```bat
cd frontend
npm install
npm run dev
```

浏览器访问 **http://localhost:5173**（开发代理 `/api → localhost:8000`）。

### 4.3 演示账号

| 账号 | 密码 | 角色 | 说明 |
|---|---|---|---|
| admin | admin123 | 管理员 | 首次播种内置，可审批/管理/爬虫/推送/审计 |
| zhangsan | zs123456 | 普通用户（技术部） | 运行 `python scripts\seed_demo.py` 播种演示数据后可用 |

> 演示数据种子：admin 直入库 3 个文档（技术部 2 + 公开 1）+ 普通用户 1 个待审批 + 1 条部门推送。生产环境请务必修改初始密码。

---

## 5. 配置说明（.env 全字段）

复制 `backend/.env.example` 为 `backend/.env` 后按需修改；未配置项使用内置默认值。配置优先级：显式传入环境变量 > `.env` 文件 > 内置默认值。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `APP_NAME` | 企业资料管理系统 | 应用名称 |
| `DEBUG` | false | 调试模式开关 |
| `DATABASE_URL` | `sqlite:///./data/app.db` | 数据库连接串；默认 SQLite（启用 WAL + busy_timeout），生产可切 PostgreSQL |
| `SECRET_KEY` | 空（随机生成） | JWT 签名密钥；缺省每次启动随机生成（重启后旧 token 失效），生产请固定 |
| `JWT_EXPIRE_MINUTES` | 720 | Token 有效期（分钟），默认 12h |
| `UPLOAD_DIR` | `./data/uploads` | 文件私有存储目录（不暴露静态路径） |
| `MAX_UPLOAD_MB` | 200 | 单文件上传大小上限（MB） |
| `CHROMA_DIR` | `./data/chroma` | Chroma 向量库持久化目录 |
| `ENABLE_SCHEDULER` | false | 是否启用 APScheduler 爬虫定时调度；false 时手动 run 仍可用 |
| `ADMIN_INITIAL_PASSWORD` | 空（随机生成） | 首次播种 admin 的初始密码（仅首启生效），建议设置后登录立即修改 |
| `REDIS_URL` | 空 | 可选 Redis 地址（如 `redis://localhost:6379/0`）；留空则降级为内存 TTL 缓存 |
| `LLM_MODE` | local | LLM 模式：`local`（Ollama）/ `api`（OpenAI 兼容） |
| `LLM_MODEL` | qwen2.5 | LLM 模型名 |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | LLM 服务地址（Ollama 的 /v1 兼容端点） |
| `LLM_API_KEY` | 空 | API 模式密钥 |
| `EMBEDDING_MODE` | local | Embedding 模式：`local` / `api` |
| `EMBEDDING_MODEL` | BAAI/bge-small-zh-v1.5 | 本地 embedding 模型（首次运行自动下载） |
| `EMBEDDING_DIM` | 512 | 向量维度，**必须与向量库中已有向量一致**；切换维度需全量重建索引 |
| `EMBEDDING_API_BASE_URL` | 空（回退 LLM_BASE_URL） | API 模式 embedding 地址 |
| `EMBEDDING_API_KEY` | 空 | API 模式 embedding 密钥 |
| `EMBEDDING_API_MODEL` | 空 | API 模式 embedding 模型名 |
| `RERANKER_MODEL` | BAAI/bge-reranker-base | 重排模型（CrossEncoder，首次运行自动下载 ~1.1GB） |
| `RERANKER_ENABLED` | true | 是否启用重排；false 时 RRF 融合后直接取 top5 |
| `SEARCH_THRESHOLD` | 0.5 | 语义相似度阈值：最高分低于阈值视为"未找到相关内容" |

---

## 6. 测试

四套后端自动化测试（TestClient + assert），各自使用独立测试库（`data/test_*/`），运行结束自动清理，**不污染正式数据**：

```bat
cd backend
python scripts\test_m1m2.py    REM M1+M2 认证与角色权限 + 上传审批流 —— 21 断言
python scripts\test_m3.py      REM M3 解析入库管线（4 格式/清洗/分块/向量/FTS/超长/重试）—— 8 断言
python scripts\test_m4.py      REM M4 混合检索 + RAG + 收藏夹 + 缓存 —— 9 断言
python scripts\test_m5.py      REM M5 爬虫入库/去重/SSRF + 推送 + 审计 + 统计 —— 6 断言
```

**合计 44 断言，全部 PASS**；另有 `python scripts\seed_demo.py` 播种演示数据。评估结论：**Evaluator PASS 8.9/10**（见 `eval-reports/eval-5.md`），前端经 Playwright E2E 抽查（登录 → 检索 → 预览 → 下载 → 收藏 → 问答 → 审批 → 推送）。

---

## 7. 部署

### 7.1 裸机生产（Windows / Linux）

```bash
# 后端
cd backend && pip install -r requirements.txt   # + M3+ 依赖（见 §4.1）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 建议用 systemd / nssm / supervisord 守护

# 前端
cd frontend && npm install && npm run build     # 产出 dist/
```

nginx 配置示例（前端静态 + `/api` 反代）：

```nginx
server {
    listen 80;
    server_name your-domain;

    root /path/to/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # SPA 路由
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;            # 问答长响应
        client_max_body_size 200m;          # 上传上限与 MAX_UPLOAD_MB 一致
    }
}
```

### 7.2 Docker Compose

项目根执行（详见 `docker-compose.yml` 注释）：

```bash
docker compose up -d          # 默认仅 backend（SQLite 内置，无需 postgres）
# 如需 Redis 缓存：取消 compose 中 redis 服务注释并设 REDIS_URL
```

默认 SQLite 不需要 PostgreSQL；`data/` 卷持久化 `app.db + uploads/ + chroma/`，**备份整个 `data/` 目录即可**。

### 7.3 备份与恢复

```bash
cd backend && python scripts\backup.py        # 打包 data/ 为 zip（按日期命名）到 backup/
```

恢复：停服 → 将备份 zip 解压回 `backend/data/` → 重启。

---

## 8. AI 问答启用

### 8.1 全本地模式（完全离线，推荐）

```dotenv
# 1) 安装 Ollama 并拉取模型
#    winget install Ollama.Ollama && ollama pull qwen2.5:7b
# 2) .env 配置
LLM_MODE=local
LLM_BASE_URL=http://localhost:11434/v1
EMBEDDING_MODE=local            # 自动下载 BAAI/bge-small-zh-v1.5（~100MB）
EMBEDDING_DIM=512
RERANKER_ENABLED=true           # 自动下载 BAAI/bge-reranker-base（~1.1GB，可选）
```

首次启动后台预热 embedding；本地模型需预留磁盘/内存（sentence-transformers + torch 约 2GB，reranker 约 1.1GB，7B LLM 建议 GPU）。

### 8.2 API 模式（OpenAI 兼容）

```dotenv
LLM_MODE=api
LLM_BASE_URL=https://your-gateway/v1
LLM_API_KEY=sk-xxx
EMBEDDING_MODE=api
EMBEDDING_API_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536              # 必须与向量库现有维度一致！
```

> 切换 embedding 模式/维度 ⇒ 触发全量重建向量索引（后台任务）；重建期间检索自动降级为关键词路。

---

## 9. 常见问题（FAQ）

1. **Redis 未安装 / 未配置**：`REDIS_URL` 留空即可，热点搜索缓存自动降级为内存 TTL 缓存，功能不受影响。
2. **embedding 模型下载失败**：首次使用 `EMBEDDING_MODE=local` 时会自动下载 `BAAI/bge-small-zh-v1.5`（约 100MB），需联网；离线环境可预先把模型放入 HuggingFace 缓存目录后设置 `HF_HUB_OFFLINE=1`。
3. **重排模型体积大 / 不需要**：`RERANKER_ENABLED=false` 可关闭 CrossEncoder（跳过 ~1.1GB 模型下载），检索在 RRF 融合后直接取 top5。
4. **`SECRET_KEY` 未配置导致重启后登录失效**：缺省时每次启动随机生成密钥，旧 token 全部失效属预期；生产请在 `.env` 固定 `SECRET_KEY`。
5. **admin 初始密码不知道**：未设置 `ADMIN_INITIAL_PASSWORD` 时，首次启动日志会打印随机初始密码（仅首启播种生效）；请登录后立即修改。
6. **爬虫 JS 渲染站点抓不到**：首次启用 JS 渲染需联网下载浏览器二进制（约 100MB+）；离线环境自动降级为纯 HTTP 抓取 + 智能正文提取。
7. **上传大文件超时**：nginx `client_max_body_size` 需与 `MAX_UPLOAD_MB`（默认 200MB）一致；上传后解析入库在后台异步执行，不阻塞接口。

---

## 10. 相关文档

| 文档 | 内容 |
|---|---|
| `spec.md` | 产品规格：功能需求（P0/P1/P2）、数据模型、API 契约、验收标准 |
| `docs/DESIGN.md` | 技术设计 v4：架构选型、数据模型、核心流程、里程碑 |
| `PROJECT_SUMMARY.md` | 项目总结：功能清单、测试统计、修复记录、论文素材索引 |
| `docs/diagrams/*.mmd` | 论文图表素材（架构/ER/时序/流程/拓扑），可直接粘贴 mermaid.live 渲染 |
