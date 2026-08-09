# 企业资料管理系统（私有化部署）— 产品规格书

> 状态：产品规格定稿（基于 docs/DESIGN.md v4，面向开发的产品化浓缩与补全）
> 配套文档：`docs/DESIGN.md` 为技术设计权威依据；本 spec 定义"做什么、验收什么"。

## 1. 产品概述

### 1.1 定位

**企业资料管理系统**是一款可本地私有化部署的中小企业内部文档管理与 AI 知识库平台。它把散落在员工电脑、零散网盘中的业务文档、技术资料统一收拢到一个私有的"企业知识库"中，提供**全文检索、在线预览下载、收藏管理、AI 问答**，并通过**审批流 + 三级角色 + 部门隔离**保证资料安全、可控地流转。

### 1.2 解决的核心痛点

| 痛点 | 本产品解法 |
|---|---|
| 文档分散、查找缓慢 | 统一入库 + 混合检索（关键词 + 语义）毫秒级定位 |
| 文件重复、版本混乱 | sha256 文件级去重 + 统一存储 |
| 资料泄露风险高 | 私有化部署 + 部门隔离 + 审批流 + 鉴权下载 + 审计日志 |
| 缺少统一推送分发渠道 | 管理员按部门定向推送，通知中心统一触达 |

### 1.3 核心价值

- **对员工**：一个入口搜遍全公司资料，AI 问答直接给答案、带引用来源；
- **对部门**：资料按部门隔离可见，重点资料精准定向推送；
- **对管理员**：可视化配置爬虫自动扩充知识库、批量维护资料、全量审计可追溯；
- **对组织**：数据不出内网，隐私可控，私有化部署零外部依赖。

### 1.4 产品目标

- 样例库万级 ~ 十万级企业文档**检索 p95 < 500ms**（关键词 + 语义混合检索，基准定义见 §7.1）；
- 用户自主完成检索、收藏、下载、AI 问答；
- 管理员可视化配置爬虫、批量维护资料、精准部门推送；
- 权限分级清晰（三级角色 + 部门隔离），审计可追溯，私有化杜绝资料外流。

---

## 2. 用户角色与权限矩阵

### 2.1 三级角色

| 角色 | 定位 |
|---|---|
| **管理员（admin）** | 全权：审批、文档管理、爬虫、用户管理、推送、审计 |
| **部门管理员（dept_admin）** | 本部门管理：审批本部门文档、管理本部门文档、本部门推送 |
| **普通用户（user）** | 使用 + 上传待审批：检索、预览、下载、收藏、AI 问答、我的上传 |

### 2.2 权限矩阵（角色 × 功能）

| 功能 | 管理员 | 部门管理员 | 普通用户 |
|---|---|---|---|
| 检索 / 预览 / 下载 / 收藏 / AI 问答 | ✅ | ✅ | ✅（仅可见文档） |
| 上传文档 | ✅ 直入库 | ✅ 直入库 | ✅ 需审批 |
| 撤回自己的待审批上传 | ✅ | ✅ | ✅ |
| 审批上传文档 | ✅ 全部 | ✅ 本部门 | — |
| 文档下架 / 删除 | ✅ 任意 | ✅ 本部门 | — |
| 重点资料标记 | ✅ 任意 | ✅ 本部门 | — |
| 爬虫任务配置与定时抓取 | ✅ | — | — |
| 部门定向推送 | ✅ 任意部门 | ✅ 本部门 | — |
| 用户管理（建号 / 角色 / 部门） | ✅ | — | — |
| 审计日志查看 | ✅ | — | — |
| 工作台统计 | ✅ | ✅（本部门视图） | — |

### 2.3 可见范围规则（安全核心）

可见性由 **部门维度 + 状态维度** 双重决定，在**检索、预览、下载、问答、收藏、通知**所有环节强制生效（后端权限过滤，前端隐藏不构成安全边界）。

**部门维度**：
- 文档 `department_id` **为空 = 公开**：全员可见；
- 文档 `department_id` **非空 = 仅本部门成员可见**；
- 管理员可见全部文档；部门管理员可见本部门 approved 文档 + 待其审批的 pending/rejected/processing。

**状态维度**：见 §2.4 状态-可见性矩阵。

### 2.4 状态-可见性矩阵

| 文档状态 | 检索 | 预览 / 下载 | 可见者 |
|---|---|---|---|
| approved | ✅ | ✅ | 全员（公开）或本部门成员、管理员 |
| pending | ❌ | ❌（审批者可预览原文审阅） | 上传者、审批者（admin / 本部门 dept_admin） |
| processing | ❌ | ❌ | 上传者、审批者（入库管线运行中） |
| rejected | ❌ | ❌ | 上传者（可看拒绝原因）、审批者 |
| offline | ❌ | ❌ | 全角色不可见（仅管理端列表可见，数据保留） |
| failed | ❌ | ❌ | 上传者、审批者（可看错误信息） |

> 审批者例外：审批中心可预览 `pending` 文档原文以作判断，但不可下载。

### 2.5 文档部门归属规则

- **普通用户上传**：`department_id` = 上传者所在部门（用户无部门时默认公开）；
- **管理员 / 部门管理员直入库**（批量上传）：上传时显式指定目标部门或"公开"；
- **爬虫抓取**：任务配置 `target_department_id`（空 = 公开），抓取入库文档继承任务目标部门；
- **改部门**（管理端）：立即切换可见性——原部门成员即刻失去访问权（预期行为，验收见 F8）。

### 2.6 账号机制

- **仅管理员建号**，系统不提供开放注册；
- 首次启动自动播种内置 `admin` 账号（随机初始密码打印到启动日志，首登强制改密）；
- 账号维度：用户名、密码（bcrypt 哈希）、角色、所属部门。

---

## 3. 功能需求清单（P0 / P1 / P2）

> 验收标准遵循 Given-When-Then 或验收点列表，全部为可自动化验证的接口/行为级标准。

## P0 — 核心功能（必须实现）

### F1 认证与角色权限

**User Story**: 作为一个系统用户，我想要用账号密码安全登录并按角色访问功能，以便系统区分谁能看什么、做什么。

**Acceptance Criteria**:
- Given 首次启动，Then 自动播种 admin 账号（配置默认密码或随机密码打印启动日志）；
- Given 正确账号密码调用 `/auth/login`，Then 返回 JWT token（默认 12h 过期）与用户信息（角色 / 部门 / 用户名）；
- Given 密码错误，Then 返回 401 统一错误提示，不泄露账号是否存在；
- Given 未携带或携带过期 token 访问受保护接口，Then 返回 401；
- Given 普通用户访问管理端接口（`/admin/*`），Then 返回 403；
- 系统无任何开放注册入口，`/auth/register` 不存在或返回 403。

### F2 文档上传与审批流

**User Story**: 作为一个普通用户，我想要上传资料并经管理员审批后进入知识库，以便保证入库内容受控、安全。

**Acceptance Criteria**:
- 上传仅接受 txt / docx / pdf / md，其余类型返回 400；单文件大小上限 200MB，超限拒绝；
- 上传成功后文档状态为 `pending`：文件私有存储，**不解析、不分块、不向量化、不参与检索**；`department_id` 取上传者所在部门（用户无部门时默认公开，见 §2.5）；
- 文件级去重：sha256 与库内已入库文件重复时拒绝，提示"该文件已存在"；若重复且当前用户持有该文档上传权限，可改用"更新为新版本"（保留原文档记录、替换正文与分块/向量，见 F8）；
- 管理员 / 部门管理员审批通过 → 状态 `processing` → 后台异步执行 解析 → 清洗 → 父子分片 → 向量化 → 状态 `approved`，立即可检索；`processing` 期间文档不参与检索；
- 审批拒绝 → 状态 `rejected` 并附拒绝原因，上传者在"我的上传"可见原因；
- 用户可撤回自己的 `pending` 文档，撤回后删除文件与记录，状态不可再审批；
- `processing` 解析失败 → 状态 `failed` 并记录错误信息（`error_message`）；管理端可对 `failed` / `offline` 文档执行"重新入库"重试（重新走解析入库管线，见 F8）；
- 部门管理员仅能审批**本部门用户**的文档；管理员可审批全部；
- 上传、撤回、审批通过/拒绝均写入审计日志（含操作人、IP）。

### F3 混合检索

**User Story**: 作为一个用户，我想要输入关键词快速找到资料，以便秒级定位所需文档。

**Acceptance Criteria**:
- 查询走双路召回：关键词路（jieba 分词 + FTS5 bm25）与语义路（Embedding + Chroma）各召回 top30；**双路均在召回阶段即按当前用户可见性过滤**（Chroma metadata / FTS 行冗余 `department_id` + `status`，仅取 status=approved 且公开或本部门，管理员全量）；
- RRF（Reciprocal Rank Fusion）融合去重 → CrossEncoder 重排 → 返回 top5（可配置分页）；重排后仅做兜底权限校验（防脏数据），不可见文档绝不进入返回；
- 结果**仅含当前用户可见文档**（公开 + 本部门 + 管理员全量），不可见文档绝不出现在结果中；
- 重点标记文档（`is_featured`）加权置顶显示：重排阶段对 featured 文档得分加固定权重 +1.0（同等相关度下 featured 排前，可自动化断言）；
- 结果卡片展示：标题、命中摘要片段（高亮关键词）、文档来源（上传/爬虫）、格式、大小、更新时间；
- 万级 ~ 十万级文档规模下，本地部署检索 p95 < 500ms；
- 热点搜索词命中缓存（Redis 或内存 TTL）时直接返回，不重复全链路计算；
- 无可见结果或重排最高分低于阈值（默认 0.5，可配置）时返回空结果并提示"未找到相关内容"，不返回噪音结果。

### F4 文档预览与下载

**User Story**: 作为一个用户，我想要在线预览并下载文档原文，以便不离开系统获取资料内容。

**Acceptance Criteria**:
- 预览按格式渲染：txt / md 文本直渲，pdf 用 pdf.js，docx 用 docx-preview；
- 预览与下载均经鉴权 API（`GET /documents/{id}/preview`、`/download`），未登录或不可见返回 403/404（不泄露存在性）；可见性按 §2.4 状态-可见性矩阵执行：仅 `approved` 文档可被普通可见者预览/下载；`pending`/`rejected`/`failed` 仅上传者与审批者可预览（审批用途），不可下载；`offline` 全角色不可访问；
- 文件存储于私有目录（如 `data/uploads/`），任何静态路径不可直接访问，下载仅经鉴权路由；
- 下载成功记录审计日志（操作人、文档、IP、时间）；
- 文件损坏/解析失败时预览给出友好错误提示，不影响其他功能。

### F5 资料收藏与收藏夹

**User Story**: 作为一个用户，我想要把常用资料收藏到个人收藏夹分类管理，以便快速回看。

**Acceptance Criteria**:
- 收藏夹：创建、重命名、删除（删除时其下收藏条目一并处理）；
- 收藏 / 取消收藏可见文档；同一用户对同一文档在收藏夹内唯一（重复收藏幂等提示）；
- 收藏夹页展示文档卡片（标题、缩略信息），点击进入文档详情；
- 文档被下架或删除后，收藏条目显示"已失效"状态，页面不报错；
- 仅能收藏当前用户可见文档，不可见文档收藏返回 403。

### F6 AI 问答（RAG）

**User Story**: 作为一个用户，我想要用自然语言向资料库提问，以便直接获得带引用来源的答案，而不是翻遍文档。

**Acceptance Criteria**:
- 问答流程：问题 → 混合检索（**召回阶段即按可见性过滤**，同 F3，仅 status=approved 可见文档进入召回）→ RRF 融合 → CrossEncoder 重排 → 回溯 parent 块（small-to-big）→ 组装 prompt 前**二次兜底权限过滤**（确认所有 parent 所属文档 approved 且当前用户可见）→ LLM 生成；
- 回答必须附**引用来源**：文档标题 + 来源片段，来源可点击跳转原文详情；
- 仅基于当前用户可见文档回答，不可见内容绝不进入 prompt（可见性过滤先于召回与 prompt 组装，见 §6.4）；
- LLM 双模式可切换（配置项切换，重启生效）：本地 Ollama qwen2.5 / OpenAI 兼容 API；
- **Embedding 维度为配置项 `embedding_dim`**（本地 bge-small-zh-v1.5 = 512；API 模式必须配置为与当前向量库一致的维度）；**切换 embedding 模式/维度 ⇒ 触发全量重建向量索引**（后台任务，见 §7.2），重建期间检索降级为关键词路并在界面提示"向量索引重建中"；
- 超长文档问答：命中 child 检索块后回溯对应 parent 上下文块（~1200 token）送 LLM，保证上下文完整；
- 资料库中找不到可信答案时，明确回答"未在资料库中找到相关内容"，不编造；判定依据：重排最高分低于阈值（默认 0.5，可配置）或无可召回内容；
- 问答接口有超时与失败兜底（LLM 不可用时返回友好错误，不影响检索功能）。

### F7 文档解析入库管线

**User Story**: 作为一个系统，我想要自动解析清洗多格式文档并分块向量化，以便文档可被检索与问答。

**Acceptance Criteria**:
- 解析支持 4 种格式：txt（chardet 编码探测）、docx（python-docx）、pdf（PyMuPDF）、md；
- 入库前统一清洗：去控制字符、去多余空白、统一换行符；爬虫文本剥离 HTML 残留与导航/页脚/广告噪音；
- 清洗后有效文本 < 50 字符的文档标记 `failed` 不入库，并在文档记录中留错误原因（`error_message`）；
- 父子分片（small-to-big）：child 检索块 ~250 token（overlap 30）存 Chroma；parent 上下文块 ~1200 token 存 SQLite；**ChunkChild metadata 冗余 `document_id, parent_id, chunk_index, department_id(可空), status`，FTS 行冗余 `department_id, status`**（支撑召回阶段权限过滤）；
- 十万字级文档可完整分片入库（child 数约 400 块量级），超大文档不丢失内容；
- 解析/分片失败不影响其他文档入库，该文档标记 `failed` 并记录 `error_message`，可在管理端"重新入库"重试。

### F8 管理端文档管理

**User Story**: 作为一个管理员，我想要批量导入并维护资料（标记重点、下架、删除），以便持续建设企业知识库。

**Acceptance Criteria**:
- 管理员 / 部门管理员可批量上传文档**直接入库**（跳过审批，走完整解析入库管线）；
- 文档列表支持筛选：状态（pending/processing/approved/rejected/offline/failed）、部门、来源（上传/爬虫）、格式、是否重点；
- 重点资料标记：管理员任意文档、部门管理员本部门文档；标记后检索加权生效；
- 下架（offline）：文档立即不可检索/预览/下载（见 §2.4 矩阵），但数据保留；可重新上架；
- 重新入库：对 `failed` / `offline` 文档提供"重新入库"操作（重新走解析→分块→向量化管线，成功后状态恢复 `approved`）；
- 更新为新版本：重复文件重新上传时可选"以新版本更新"——保留文档记录，替换正文与分块/向量（审计日志记录"更新"动作）；
- 改部门：修改 `department_id` 后可见性立即切换（原部门成员即刻失去访问权，预期行为）；
- 删除：管理员任意、部门管理员本部门；删除同步清理文件、SQLite 分块、Chroma 向量与收藏引用；
- 批量上传、标记、下架、删除均写审计日志。

---

## P1 — 重要功能（应该实现）

### F9 爬虫任务配置与定时抓取（管理员）

**User Story**: 作为一个管理员，我想要配置定时爬虫自动抓取指定网站内容，以便自动化扩充资料库。

**Acceptance Criteria**:
- 任务配置字段：任务名、起始 URL（支持多个）、允许域名白名单、抓取深度、正文选择器（留空 = 智能提取）、cron 定时表达式、启用开关、**目标部门 `target_department_id`（空 = 公开）**；
- 抓取引擎 Scrapling：adaptive 抓取 + stealthy 反爬 + JS 渲染 + 智能正文提取；
- APScheduler 按 cron 定时执行；支持手动立即执行（`POST /run`）；
- 抓取内容清洗后**直接入库**（管理员配置任务即授权行为，不走审批），走标准解析入库管线；入库文档继承任务目标部门（见 §2.5）；
- SSRF 防护：仅允许抓取任务配置的域名白名单内的地址；
- 运行记录：每次执行写入 `CrawlRunLog`（开始/结束时间、抓取数、入库数、去重跳过数、状态、错误信息）；
- 抓取到 sha256 已存在内容自动去重跳过；
- 任务创建、启停、执行结果写审计日志。

### F10 部门定向推送与通知中心

**User Story**: 作为一个部门管理员，我想要向本部门成员推送资料通知，以便统一分发重点资料。

**Acceptance Criteria**:
- 新建推送：标题、内容、可选关联文档、目标范围（管理员可选任意部门或全员；部门管理员仅本部门）；
- 用户端通知中心：轮询拉取（默认间隔 30s，可配置），展示未读数角标、未读/已读状态；
- 通知可标记单条已读 / 全部已读；
- 关联文档的通知可点击跳转文档详情（权限过滤：文档不可见时提示无权访问）；
- 推送创建写审计日志。

### F11 用户管理（管理员）

**User Story**: 作为一个管理员，我想要创建账号并分配角色与部门，以便控制谁能进入系统、看什么。

**Acceptance Criteria**:
- 创建账号：用户名、初始密码、角色（admin / dept_admin / user）、所属部门；
- 编辑账号：修改角色、修改部门、重置密码；
- 用户名全局唯一；可删除用户（admin 自身除外），删除需级联处理其上传、收藏夹与收藏条目（或改为停用并保留历史，二选一在管理端明确提示后果）；
- 无开放注册入口；
- 用户创建、角色/部门变更写审计日志。

### F12 审计日志

**User Story**: 作为一个管理员，我想要查询关键操作记录，以便追溯责任、满足合规。

**Acceptance Criteria**:
- 覆盖动作：上传、撤回、审批通过/拒绝、下架、删除、下载、推送创建、爬虫任务创建/启停/执行、用户创建/角色变更；
- 字段：操作人、动作、对象类型、对象 ID、详情、IP、时间；
- 管理端支持按操作类型、对象类型、操作人、时间范围筛选查询；
- 日志只增不删（管理端无删除日志入口）；日志量大时提供分页。

### F13 管理端工作台统计

**User Story**: 作为一个管理员，我想要一眼看到系统概览，以便掌握知识库运行状况。

**Acceptance Criteria**:
- 展示卡片：文档总数（按状态）、待审批数、爬虫任务数/运行状态、部门数/用户数、近 7 日操作趋势；
- 部门管理员视图仅统计本部门数据；
- 数据为实时统计（或缓存 ≤ 60s），页面刷新即更新。

### F14 我的上传

**User Story**: 作为一个用户，我想要查看自己的上传记录与审批状态，以便跟踪资料是否入库。

**Acceptance Criteria**:
- 列表展示本人所有上传：标题、时间、状态（待审批 / 已通过 / 已拒绝 / 已撤回）；
- 被拒文档展示拒绝原因；待审批文档可一键撤回（撤回后从列表标记"已撤回"）；
- 已通过文档可跳转查看详情。

### F15 批量审批

**User Story**: 作为一个管理员，我想要批量通过/拒绝待审批文档，以便提高审批效率。

**Acceptance Criteria**:
- 待审批列表支持多选，批量通过 / 批量拒绝（拒绝需统一填写原因）；
- 批量操作逐条落审计日志，部分失败给出明细（哪些成功、哪些失败及原因）；
- 权限约束同 F2（部门管理员仅本部门）。

### F16 热点搜索缓存（Redis 可选）

**User Story**: 作为一个系统，我想要缓存热点搜索词，以便高频查询秒回、降低计算压力。

**Acceptance Criteria**:
- 配置 `redis_url` 时使用 Redis 缓存，未配置时自动降级为进程内 TTL 缓存（功能等价）；
- 热点搜索（命中缓存）直接返回，TTL 默认 10 分钟可配置；
- 缓存抽象对业务层透明（同一接口，无感知切换）；
- 文档被删除/下架时相关缓存失效，不返回过期不可见结果。

---

## P2 — 增强功能（锦上添花）

### F17 文档智能摘要

**User Story**: 作为一个用户，我想要在检索结果看到文档摘要，以便快速判断是否点开。

**Acceptance Criteria**:
- 入库时用 LLM 对 parent 块或全文生成 1-3 句摘要，存 `Document.summary` 字段；
- 检索结果与详情页展示摘要（LLM 不可用时降级为开头片段截取，不阻塞入库）。

### F18 相似文档推荐

**User Story**: 作为一个用户，我想要在文档详情看到相似资料，以便发现相关主题文档。

**Acceptance Criteria**:
- 基于文档向量（child 向量聚合或文档级向量）计算相似 TopK；
- 推荐结果同样受权限过滤；
- 在文档详情页"相关推荐"区域展示，点击跳转。

### F19 批量下载

**User Story**: 作为一个用户，我想要多选文档打包下载，以便离线整理资料。

**Acceptance Criteria**:
- 检索结果/收藏夹支持多选，勾选后打包 zip 下载（后端流式打包，限制单次数量 ≤ 50）；
- 打包过程对不可见文档自动剔除并提示数量差异；
- 下载动作逐条写审计日志。

### F20 搜索热词与联想

**User Story**: 作为一个用户，我想要搜索框联想与热词提示，以便更快找到资料。

**Acceptance Criteria**:
- 基于 `SearchLog` 历史搜索记录与 jieba 词频聚合生成热词榜，搜索页展示；
- 输入前缀返回联想候选（基于标题/关键词前缀匹配，缓存加速）；
- 热词榜按可见性聚合，不泄露任何文档内容。

### F21 问答会话历史

**User Story**: 作为一个用户，我想要保留问答会话，以便回溯之前的提问与回答。

**Acceptance Criteria**:
- 用户可查看自己的问答历史（问题、回答、引用来源、时间，存 `QASession`/`QAMessage`），可删除单条或清空；
- 会话历史仅本人可见。

### F22 爬虫增量更新

**User Story**: 作为一个管理员，我想要爬虫只抓取更新内容，以便减少重复抓取与噪音。

**Acceptance Criteria**:
- 对已入库页面记录内容哈希/最后修改时间，重复页面跳过；
- 页面内容变化时以"更新"方式重新入库（单一策略：保留文档记录，更新正文、分块与向量）；
- 运行记录区分"新增/更新/跳过"数量。

---

## 4. 技术栈（已确认选型）

| 层级 | 选型 | 理由 |
|---|---|---|
| 前端 | Vue 3 + Vite + Element Plus + Pinia + vue-router | 中文生态成熟、后台管理组件齐全，开发效率高 |
| 后端 | Python 3.10 + FastAPI + SQLAlchemy 2.x + Pydantic v2 | 异步高性能、自动 OpenAPI 文档、与 AI/向量生态无缝衔接 |
| 数据库 | **SQLite（唯一实现目标，FTS5 全文索引，需启用 WAL + busy_timeout 支撑并发写）**；PostgreSQL 仅作论文性能对比实验，不承诺同构切换 | SQLite 零运维、私有化天然契合；FTS5 与 PG tsvector 语法不兼容，避免"可切换"伪抽象返工 |
| 认证 | JWT（pyjwt）+ bcrypt | 无状态鉴权、自带过期，自建用户表 + 部门 |
| 缓存 | Redis（可选）/ 进程内 TTL 缓存降级 | 热点搜索加速；未配置 Redis 也能全功能运行 |
| 向量数据库 | Chroma（持久化） | pip 即用、本地方便、万~十万级足够 |
| Embedding | bge-small-zh-v1.5（本地，512 维）/ OpenAI 兼容 API（维度须与 `embedding_dim` 一致） | 双模式可切换；`embedding_dim` 为配置项，切换维度触发向量库重建（见 §7.2） |
| 重排序 | bge-reranker-base（本地 cross-encoder）/ 可选 API（未配置跳过） | 仅对融合后 ~30 条候选精排，精度高、延迟可控 |
| 关键词检索 | jieba 分词 + SQLite FTS5（bm25），FTS 行冗余可见性列 | 中文分词关键词召回；召回阶段即可按可见性过滤 |
| LLM | Ollama qwen2.5 / OpenAI 兼容 API | 双模式可切换，满足本地私有化与云端 API 两种诉求 |
| 文档解析 | chardet / python-docx / PyMuPDF / markdown | 覆盖 txt/docx/pdf/md 四种格式 |
| 预览 | pdf.js / docx-preview / 文本直渲 | 前端纯浏览器渲染，不依赖后端转换服务 |
| 爬虫 | Scrapling（adaptive + stealthy + JS 渲染 + 智能正文提取）+ APScheduler | 智能反爬、智能正文提取，零额外框架 |
| 部署 | Docker Compose（postgres/redis/chroma/ollama 可选组件）/ 裸机脚本 | 双方案满足不同私有化环境 |

**技术栈一句话**：Vue 3 + FastAPI + SQLite(FTS5) + Chroma 的私有化全栈，LLM/Embedding/Reranker 双模式（本地 Ollama + bge 系列 / OpenAI 兼容 API），Scrapling 爬虫自动扩充知识库。

---

## 5. 数据模型（实体 + 关键字段 + 关系）

### 5.1 实体清单

**Department（部门）**
- 字段：`id`, `name`

**User（用户）**
- 字段：`id`, `username`（唯一）, `password_hash`（bcrypt）, `role`（admin / dept_admin / user）, `department_id`（可空）, `created_at`
- 关系：多对一 → Department；一对多 → 上传 Document、Favorite、收藏夹、推送创建

**Document（文档）**
- 字段：`id`, `title`, `file_name`, `file_path`（私有目录路径）, `file_type`（txt/docx/pdf/md）, `file_size`, `file_hash`（sha256）, `content_text`（清洗后全文，供检索/预览）, `summary`（P2 智能摘要）, `status`（pending/processing/approved/rejected/offline/failed）, `error_message`（processing/failed 的错误信息）, `is_featured`（重点标记）, `department_id`（空 = 公开）, `source`（upload / crawl）, `uploaded_by`, `approver_id`, `approved_at`, `reject_reason`, `created_at`, `updated_at`
- 关系：多对一 → Department、Uploader(User)、Approver(User)；一对多 → ChunkParent、Favorite

**ChunkParent（父块 / 上下文单元，SQLite）**
- 字段：`id`, `document_id`, `chunk_index`, `title`, `text`（~1200 token）
- 关系：多对一 → Document；一对多 → ChunkChild

**ChunkChild（子块 / 检索单元，Chroma）**
- 字段（Chroma metadata + embedding）：`document_id`, `parent_id`, `chunk_index`, `text`（~250 token）, `embedding`, **冗余 `department_id`（可空）, `status`**（支撑召回阶段权限过滤）
- 关系：多对一 → ChunkParent / Document

**FavoriteFolder（收藏夹）**
- 字段：`id`, `user_id`, `name`, `created_at`
- 关系：多对一 → User；一对多 → Favorite

**Favorite（收藏条目）**
- 字段：`id`, `user_id`, `folder_id`, `document_id`, `created_at`
- 约束：`(user_id, document_id)` 唯一
- 关系：多对一 → User / FavoriteFolder / Document

**CrawlTask（爬虫任务）**
- 字段：`id`, `name`, `start_urls`(JSON), `allowed_domains`(JSON), `selector`（正文选择器，可空）, `max_depth`, `target_department_id`（可空 = 公开）, `schedule`（cron）, `enabled`, `status`, `last_run_at`, `created_by`, `created_at`
- 关系：多对一 → User（创建者）、Department（目标部门，可选）；一对多 → CrawlRunLog

**CrawlRunLog（爬虫运行记录）**
- 字段：`id`, `task_id`, `started_at`, `finished_at`, `fetched_count`（抓取数）, `ingested_count`（入库数）, `skipped_count`（去重跳过数）, `status`（running/success/failed）, `error`, `created_at`
- 关系：多对一 → CrawlTask

**PushNotification（推送通知）**
- 字段：`id`, `title`, `content`, `document_id`（可空）, `department_id`（空 = 全员）, `created_by`, `created_at`
- 关系：多对一 → User（创建者）、Document（可选）、Department（可选）；一对多 → PushRead

**PushRead（已读记录）**
- 字段：`notification_id`, `user_id`, `read_at`
- 关系：多对一 → PushNotification / User

**AuditLog（审计日志）**
- 字段：`id`, `user_id`, `action`, `target_type`, `target_id`, `detail`(JSON), `ip`, `created_at`
- 关系：多对一 → User（操作人，可空）；只增不删

**SearchLog（搜索日志）**
- 字段：`id`, `user_id`, `query`, `hit_count`, `created_at`
- 关系：多对一 → User；供热词榜（F20）与搜索行为分析

**QASession / QAMessage（问答会话）**
- QASession：`id`, `user_id`, `title`（首问摘要）, `created_at`
- QAMessage：`id`, `session_id`, `role`（user/assistant）, `content`, `citations`(JSON：document_id, title, snippet, parent_id), `created_at`
- 关系：多对一 → User；QASession 一对多 → QAMessage；供会话历史（F21）

**DocumentFTS（FTS5 虚拟表）**
- external content 表指向 Document；行冗余 `department_id`, `status`（可见性过滤列）
- 分词列：`title`, `content_text`（中文经 jieba 空格分词后写入）
- 同步规则：Document 增/改/删时 FTS 行同步（触发器或应用层同步）；文档删除时 FTS 行一并删除

### 5.2 关系图（概念级）

```
Department 1─* User 1─* Document 1─* ChunkParent 1─* ChunkChild
User 1─* FavoriteFolder 1─* Favorite *─1 Document
User 1─* CrawlTask 1─* CrawlRunLog
User 1─* PushNotification 1─* PushRead *─1 User
User 1─* AuditLog
User 1─* SearchLog
User 1─* QASession 1─* QAMessage
User 1─* Document (uploaded_by) / 1─* Document (approver_id)
Document 1─1 DocumentFTS (external content, 冗余可见性列)
```

---

## 6. 核心流程

### 6.1 上传审批流

```
普通用户上传(txt/docx/pdf/md, ≤200MB, 归属=上传者部门)
  → 校验格式/大小/sha256去重
  → pending（文件私有存储，不解析不检索；仅上传者+审批者可见）
      ├─ 管理员/部门管理员审批通过
      │     → status=processing（检索不可见）
      │     → 解析 → 清洗(<50字符拦截) → 父子分片 → Embedding → Chroma入库
      │     → status=approved → 可检索/预览/下载
      │         └─ 解析失败 → status=failed + error_message → 管理端"重新入库"重试
      ├─ 审批拒绝 → status=rejected + 原因（上传者可见）
      └─ 用户撤回（仅 pending）→ 删除文件与记录
每一步写审计日志；入库时写入冗余可见性字段（ChunkChild metadata / FTS 行）
```

### 6.2 爬虫入库流

```
管理员配置任务(起始URL/域名白名单/深度/选择器/cron/目标部门 target_department_id)
  → APScheduler 按 cron 触发（或手动 run）
  → Scrapling: adaptive 抓取 + stealthy 反爬 + JS 渲染 + 智能正文提取
  → 清洗（HTML噪音/导航/页脚剥离）→ 过短拦截 → sha256 去重
  → 直接入库：解析 → 父子分片 → 向量化（同上传管线，不走审批；文档归属任务目标部门）
  → 写入 CrawlRunLog（开始/结束、抓取数/入库数/跳过数、状态、错误）
```

### 6.3 检索流

```
查询 q + 当前用户可见性条件（department_id ∈ {null, 用户部门}；admin 全量；status=approved）
  → [关键词路] jieba 分词 → FTS5 bm25 召回 top30（WHERE status='approved' AND 可见性过滤，用冗余列）
  → [语义路] Embedding(q) → Chroma child 召回 top30（metadata filter: status=approved + department_id ∈ {null, 用户部门}；admin 全量）
  → 权限过滤已下沉到召回阶段；不可见文档不进入后续计算
  → RRF 融合去重（候选 ~30 条）
  → CrossEncoder（bge-reranker-base）逐对精排 → top5（featured +1.0 加权）
  → 兜底二次权限校验（防脏数据）→ 命中热点缓存则直接返回（Redis / 内存 TTL）
```

### 6.4 RAG 问答流

```
问题 q + 可见性条件（同检索流：status=approved 且公开/本部门/admin 全量）
  → 混合检索召回（召回阶段即按可见性过滤，不可见内容不进入候选）
  → RRF 融合 → CrossEncoder 重排 → topK child 命中
  → small-to-big 回溯 parent 块（~1200 token，同文档合并去重）
  → 组装 prompt 前二次兜底权限过滤（确认 parent 所属文档均 approved 且当前用户可见）
  → 组装 prompt（注入"仅基于资料回答+禁止编造"）
  → LLM（Ollama qwen2.5 / OpenAI 兼容 API）生成
  → 返回 回答 + 引用来源（document_id + title + snippet + parent_id，可点击跳转）
```

### 6.5 推送流

```
管理员/部门管理员创建推送(标题/内容/关联文档/目标部门或全员)
  → 生成 PushNotification + 目标用户可见性（部门匹配）
  → 用户端轮询(30s)通知中心 → 未读角标
  → 用户标记已读/全部已读（PushRead）
  → 关联文档可跳转详情（权限过滤）
```

---

## 7. 非功能需求

### 7.1 性能
- 性能基准：样例库 5 万文档 + 指定硬件（如 i5 / 16GB / SSD）下检索 p95 < 500ms；M3 里程碑做规模压测验证（万级 / 5 万 / 十万级各测一轮）；
- 手段：Chroma HNSW 向量索引 + SQLite FTS5(bm25) + Redis/内存热点缓存；SQLite 启用 WAL + busy_timeout 支撑并发写；
- 重排序仅对融合后 ~30 条候选精排（cross-encoder 每对几十 ms），不扫描全库；
- 单文件上传上限 200MB，上传与解析入库异步解耦（后台任务），不阻塞接口响应。

### 7.2 安全
- bcrypt 密码哈希；JWT 过期（默认 12h）；
- 全部接口做角色/权限校验；文档可见性后端强制过滤（部门 + 状态双重维度，见 §2.4），**过滤下沉到召回阶段**（ChunkChild metadata / FTS 行冗余可见性字段），问答先过滤再组装 prompt；
- 上传白名单：仅 txt/docx/pdf/md，大小上限 200MB；
- 文件存储私有目录，下载/预览仅经鉴权 API，不暴露静态路径；
- 爬虫 SSRF 防护：仅允许任务配置域名白名单；
- 关键操作全量审计日志（只增不删）；
- 私有化部署：可选完全离线运行（本地模型模式），数据不出内网；
- Embedding 维度一致性：`embedding_dim` 为配置项（本地 bge-small-zh-v1.5 = 512）；切换 embedding 模式/维度 ⇒ 触发全量重建向量索引（后台任务，重建期间检索降级为关键词路，界面提示"向量索引重建中"），避免新旧向量维度不一致导致检索报错。

### 7.3 备份与恢复
- `data/` 目录（app.db + uploads/ + chroma/）整体备份，提供备份脚本（可 cron 化）；
- 建议每日备份至异地/云盘；恢复流程在文档中说明（停服 → 还原 data/ → 重启）。

### 7.4 部署
- Docker Compose（可选组件：redis / chroma / ollama；postgres 仅作论文对比实验可选）或裸机脚本（`pip install -r requirements.txt` + uvicorn + npm build + nginx）双方案；
- 一键启动脚本 + 首次启动自动播种 admin；
- 提供 `.env.example` 配置模板（数据库、Redis、LLM/Embedding 模式、`embedding_dim`、上传限制等）；本地模型模式需前置检查磁盘/内存（sentence-transformers+torch ~2GB、reranker ~1.1GB、Ollama 7B 需 GPU）；
- 爬虫 JS 渲染：首次启用需联网下载浏览器二进制（~100MB+），离线环境自动降级为纯 HTTP 抓取 + 智能正文提取（备选路径：Playwright + BeautifulSoup）。

### 7.5 其他
- 响应式设计（桌面端优先，后台管理系统）;
- 前端构建产物静态部署，前后端分离（统一 `/api` 前缀 + Bearer token）;
- 面向论文与交付：提供自检/冒烟脚本，每里程碑可运行 demo。

---

## 8. 页面清单

### 8.1 用户端（普通用户 / 部门管理员）

| 页面 | 核心元素 |
|---|---|
| 登录页 | 账号/密码表单、错误提示 |
| 检索首页 | 搜索框、热词（P2）、结果列表（标题/摘要高亮/格式/重点角标）、筛选（部门/格式/来源）、分页 |
| 文档详情 | 原文在线预览（pdf.js / docx-preview / 文本直渲）、元信息、下载按钮、收藏按钮、相关推荐（P2） |
| 收藏夹 | 收藏夹列表、新建/重命名/删除、夹内文档卡片、取消收藏 |
| AI 问答页 | 对话式界面、问题输入、回答 + 引用来源列表（可点击）、会话历史（P2） |
| 我的上传 | 上传记录列表、状态标签、拒绝原因、撤回按钮、再上传入口 |
| 通知中心 | 通知列表、未读角标、已读/全部已读、关联文档跳转 |

### 8.2 管理端（管理员 / 部门管理员）

| 页面 | 核心元素 |
|---|---|
| 工作台 | 统计卡片（文档数/待审批/爬虫状态/部门用户数/近 7 日趋势） |
| 审批中心 | 待审批列表、查看原文预览、单条通过/拒绝（附原因）、批量操作（P1）、权限过滤（本部门） |
| 文档管理 | 批量上传（直接入库）、文档列表与筛选、重点标记、改部门/下架/删除、上传进度反馈 |
| 爬虫任务 | 任务列表、新建/编辑（URL/域名白名单/深度/选择器/cron）、启停、手动执行、运行记录 |
| 用户管理 | 用户列表、创建账号（角色/部门）、编辑角色/部门、重置密码 |
| 部门推送 | 新建推送（标题/内容/关联文档/目标部门）、历史推送列表 |
| 审计日志 | 筛选查询（操作类型/对象/操作人/时间范围）、分页列表 |

---

## 9. 里程碑建议（按可交付增量）

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M1 后端骨架 + 认证** | FastAPI 工程、配置（.env）、ORM 模型、JWT 认证、三级角色、admin 播种、部署冒烟 | 登录/鉴权/角色冒烟测试通过；越权访问返回 401/403；`uvicorn` 一键起服务 |
| **M2 上传审批流** | 上传 → pending → 审批通过/拒绝 → 撤回、批量审批（P1） | 全流程 API 测试通过；部门管理员仅见本部门待审；去重生效 |
| **M3 解析入库管线** | parsers / 清洗 / 父子分片 / embedding / vector_store / **权限字段冗余入库**（ChunkChild metadata + FTS 行） | 4 种格式样例文档入库；**冗余可见性字段（department_id/status）正确写入 Chroma metadata 与 FTS 行**；向量可召回；过短文本拦截；超长文档完整分片；规模压测（万级 / 5 万 / 十万级） |
| **M4 检索 + RAG** | 混合检索（双路 + RRF + 重排）、**召回阶段权限过滤**、重点加权、AI 问答 | 样例文档命中且排序合理；问答引用来源正确；不可见内容不出现（召回阶段即被过滤，接口层验证） |
| **M5 爬虫 + 推送 + 审计** | Scrapling 抓取入库、APScheduler cron、部门推送、审计日志 | 真实网页抓取入库可检索；推送未读数正确；审计记录完整 |
| **M6a 前端-用户端** | 登录、检索首页、文档详情+原文预览、收藏夹、AI 问答页、我的上传、通知中心 | 用户端主流程 E2E：登录 → 检索 → 预览 → 下载 → 收藏 → 问答 |
| **M6b 前端-管理端** | 工作台、审批中心、文档管理、爬虫任务、用户管理、部门推送、审计日志 | 管理端主流程 E2E：登录 → 审批 → 批量上传 → 爬虫配置 → 推送 → 审计查询 |
| **M7 部署 + 论文素材** | Docker Compose / 裸机脚本、备份脚本、README、架构图/ER 图/流程图 | 干净环境可一键启动；论文图表素材齐备（架构图、ER、时序、流程、拓扑） |

每里程碑交付：可运行 demo + 自检脚本（assert 冒烟）；M3 起每里程碑包含论文素材沉淀（见 docs/DESIGN.md §15 论文支撑映射）。

---

## 10. API 契约（补充契约，开发依据）

> 统一前缀 `/api`；鉴权 `Authorization: Bearer <token>`；完整接口以 DESIGN.md §13 为基础，本节补全其缺口，二者冲突时以本节为准。

### 10.1 通用约定

- **分页**：列表接口统一参数 `page`（默认 1）、`page_size`（默认 20，最大 100）；响应含 `total / page / page_size`；
- **统一成功响应**：`{"code": 0, "message": "ok", "data": {...}}`；
- **统一错误响应**：`{"code": <业务码>, "message": "<可读错误>", "detail": {...}}`；HTTP 状态码：400 参数错误 / 401 未认证 / 403 无权限（含不可见文档）/ 404 不存在（不可见文档一律返回 403/404 不泄露存在性）/ 500 内部错误；
- **可见性**：文档类接口统一按 §2.4 状态-可见性矩阵执行。

### 10.2 收藏夹 CRUD（补全）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/favorites/folders` | 收藏夹列表（含各夹文档数） |
| POST | `/favorites/folders` | 新建（body: name） |
| PATCH | `/favorites/folders/{id}` | 重命名（body: name） |
| DELETE | `/favorites/folders/{id}` | 删除（级联删除夹内收藏条目） |

### 10.3 通知（补全）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/notifications/read-all` | 全部已读 |

### 10.4 用户管理（补全）

| 方法 | 路径 | 说明 |
|---|---|---|
| DELETE | `/admin/users/{id}` | 删除用户（admin 自身除外；级联处理其上传/收藏夹/收藏） |

### 10.5 文档管理（补全）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/documents/{id}/reprocess` | 重新入库（重试 failed / offline 文档，触发解析入库管线） |

### 10.6 检索筛选参数（补全）

`GET /search` 支持参数：`q`（必填）、`page`、`page_size`、`department_id`（可选，仅 admin 可指定跨部门）、`file_type`（txt/docx/pdf/md）、`source`（upload/crawl）、`is_featured`（true/false）、`sort`（relevance 默认 / time）。

### 10.7 AI 问答 schema（补全）

`POST /qa/ask`

请求：
```json
{
  "session_id": "可选，传入则续接会话，缺省新建",
  "question": "公司 VPN 的接入流程是什么？"
}
```

响应：
```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "session_id": "s-123",
    "answer": "……（基于资料的回答，找不到时返回明确提示）",
    "citations": [
      {"document_id": 12, "title": "网络接入指南", "snippet": "……命中片段……", "parent_id": 34, "chunk_index": 7}
    ],
    "confidence": 0.86
  }
}
```

引用字段 `document_id + parent_id` 供前端"点击跳转原文"定位。

### 10.8 批量下载（P2，补全）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents/batch-download` | body: {document_ids: [...]}，流式返回 zip；不可见文档自动剔除并在响应头提示剔除数量 |

---

## 附录 A：与 DESIGN.md 的映射

| spec 章节 | DESIGN.md 章节 |
|---|---|
| §2 角色权限矩阵 | §2 角色与权限矩阵 |
| §3 功能清单 | §3 功能需求 + §3.4 爬虫 + §3.5 AI/知识库 + §3.6 审计 + §3.7 清洗 |
| §4 技术栈 | §4 技术选型 |
| §5 数据模型 | §5 数据模型 |
| §6 核心流程 | §6 核心流程 + §8 超长文本处理 |
| §7 非功能需求 | §12 非功能需求 |
| §8 页面清单 | §7 页面清单 |
| §9 里程碑 | §14 开发里程碑与验收标准 |
| API 契约 | §13 API 接口契约 + 本 spec §10 补全（收藏夹 CRUD / 全部已读 / 删除用户 / 重新入库 / /search 筛选 / /qa/ask schema / 分页与错误响应） |
