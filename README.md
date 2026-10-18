# DailyNews

**A self-hosted, scheduled news briefing service.** DailyNews collects articles from news feeds, turns them into concise HTML briefings, and can deliver them by email. It also supports on-demand briefings, recurring schedules, briefing history, semantic search, and persistent assistant conversations.

[简体中文](#dailynews-1) | [English](#dailynews)

## Features

- Generate a briefing on demand or on a daily schedule, using each schedule's timezone and calendar-day window.
- Collect and extract articles, then use a configured language model to organize events and produce an HTML briefing.
- Preview and download saved briefings; browse history and search previously collected events.
- Send briefings through SMTP, with tracked delivery states and guarded recovery for uncertain send outcomes.
- Persist user preferences, scheduled tasks, task executions, briefings, and assistant conversations in PostgreSQL.
- Optionally index events in Milvus for hybrid semantic search and enable sandbox-backed assistant tools.

## Architecture

```text
Browser (Vue 3 / Vite)
        | same-origin HTTP /api
        v
FastAPI service -------------------- PostgreSQL
        |                                  | persistent tasks, users,
        |                                  | briefings, conversations
        +--> News feeds and language model
        +--> Milvus (optional event search)
        +--> SMTP relay (optional email delivery)
        +--> OpenSandbox (optional assistant tools)

Persistent Worker --> schedule scan, task execution, indexing,
                      mail delivery and recovery
```

The web service and Worker are separate processes and must both remain running for scheduled briefings. PostgreSQL is required. Milvus, SMTP, model credentials, and OpenSandbox are configured for the features that use them.

## Quick Start (5 Minutes)

### Requirements

- Git, Python 3.12 or newer, and [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 or newer and npm
- A reachable PostgreSQL database
- Model API credentials to generate briefings. SMTP, Milvus, and OpenSandbox are optional until their features are needed.

The five-minute path assumes PostgreSQL and the model API credentials are already available. First-time database or provider provisioning is not included in that estimate.

### 1. Configure the environment

Clone the project, then copy `.env.example` to `.env`. Commands below use PowerShell:

```powershell
git clone https://github.com/greenC2h/DailyNews-Agent.git
cd DailyNews-Agent
Copy-Item .env.example .env
```

Set at least the database connection and model values in `.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
SESSION_SECRET=replace-with-a-long-random-secret
MODEL_API_KEY=your-model-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=your-provider-model-name
NEWS_MODEL_NAME=your-provider-model-name
```

`DATABASE_URL` takes precedence over the `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` fields. Keep `.env` private; it is ignored by Git.

Create the database first; Alembic creates tables, not the database itself. The database user needs permission to create tables. URL-encode special characters in connection URL passwords, or use the separate `DB_*` settings. Replace both model names with a model your provider supports; for another compatible provider, also replace `DEEPSEEK_BASE_URL`. `MODEL_NAME` serves the assistant; `NEWS_MODEL_NAME` serves briefing generation.

### 2. Install, migrate, and build

```powershell
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
uv run alembic upgrade head
```

### 3. Start the API and Worker

Open two terminals in the repository root.

Terminal 1, web service:

```powershell
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Terminal 2, persistent Worker:

```powershell
uv run python -m app.worker
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), create an account, and configure your news preferences. Check service readiness at [http://127.0.0.1:8000/healthz](http://127.0.0.1:8000/healthz).

For your first briefing, choose a topic and an explicit news time range, then submit an on-demand task. Keep the Worker terminal open while it runs; preview or download the result after completion. Configure SMTP before enabling email delivery or a scheduled push. `/healthz` checks the web service's database connection; it does not test the Worker or external providers.

To use the Vite development server instead of the built frontend, run `npm --prefix frontend run dev` and open [http://localhost:5173](http://localhost:5173). The development server proxies `/api` to port 8000; keep the API process running as well.

## Optional Integrations

- **Email delivery:** set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`. Successful SMTP submission means the relay accepted the message; it does not prove final inbox delivery.
- **Semantic event search:** set `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSION`, then set `MILVUS_URI` and any required `MILVUS_TOKEN`. Initialize a collection explicitly with `uv run python -m app.cli init-milvus`. Do not delete or overwrite an existing collection when changing embedding dimensions; use a new collection name.
- **Sandbox-backed assistant tools:** configure `OPEN_SANDBOX_API_KEY`, `OPEN_SANDBOX_BASE_URL`, and, when applicable, `OPEN_SANDBOX_ID`.

All supported settings and defaults are listed in `.env.example`. Store briefing HTML under `HTML_ROOT` (default: `./data/html`).

## Development and Tests

### Business Database SQL

The root [schema.sql](schema.sql) contains the nine PostgreSQL business tables, foreign keys, and indexes at Alembic revision `c3e8f1a4b209`. It excludes LangGraph-managed tables and business data. Normal deployments should use Alembic as described above. For manual initialization of an empty database:

```powershell
psql -h localhost -U postgres -d dailynews -v ON_ERROR_STOP=1 -f schema.sql
uv run alembic stamp c3e8f1a4b209
```

Configure `.env` for that same database before stamping. Do not run this snapshot against an existing database or run the initial migrations again after importing it. Future schema upgrades still use `uv run alembic upgrade head`; framework tables are initialized by the application.

```powershell
uv run python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
```

Automated tests do not replace verification against external model, SMTP, or Milvus services. Project acceptance notes and remaining items are maintained locally in `doc/实施进度.md`; the `doc/` directory is intentionally excluded from GitHub.

## Security Notes

- Use a unique, long `SESSION_SECRET` outside local development and set `COOKIE_SECURE=true` when serving over HTTPS.
- Do not commit `.env`, provider credentials, SMTP passwords, or local acceptance data.
- Expose the service through a properly secured HTTPS reverse proxy when making it available beyond localhost.

---

# DailyNews

**可自行部署的定时新闻简报服务。** DailyNews 从新闻源采集文章，整理生成精简的 HTML 简报，并可通过邮件发送。它还支持立即生成、每日定时任务、简报历史、语义检索和持久化助手对话。

[English](#dailynews) | [简体中文](#dailynews-1)

## 功能

- 按需生成或按每日规则生成简报；定时规则使用自身时区和自然日时间窗口。
- 采集并提取文章，调用已配置的大语言模型整理事件并生成 HTML 简报。
- 预览和下载已保存的简报，浏览历史并检索已采集事件。
- 通过 SMTP 发送简报，跟踪投递状态，并对发送结果不确定的情况进行保护性恢复。
- 使用 PostgreSQL 持久化用户偏好、定时任务、任务执行记录、简报和助手对话。
- 可选使用 Milvus 建立事件索引以支持混合语义检索，也可启用基于 OpenSandbox 的助手工具。

## 项目架构

```text
浏览器（Vue 3 / Vite）
        | 同源 HTTP /api
        v
FastAPI 服务 ----------------------- PostgreSQL
        |                                  | 用户、任务、简报、对话
        +--> 新闻源与大语言模型
        +--> Milvus（可选：事件检索）
        +--> SMTP 中继（可选：邮件投递）
        +--> OpenSandbox（可选：助手工具）

常驻 Worker --> 定时扫描、任务执行、索引、邮件发送与恢复
```

Web 服务和 Worker 是两个独立进程；要执行定时简报，二者都必须持续运行。PostgreSQL 为必需依赖。Milvus、SMTP、模型凭据和 OpenSandbox 按需配置。

## 五分钟快速启动

### 环境要求

- Git、Python 3.12 或更新版本，以及 [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 或更新版本，以及 npm
- 可访问的 PostgreSQL 数据库
- 生成简报所需的模型 API 凭据。SMTP、Milvus 和 OpenSandbox 可在需要相应功能时再配置。

以下五分钟流程假设 PostgreSQL 和模型 API 凭据已经准备好；首次开通数据库或第三方服务不计入此时间。

### 1. 配置环境

克隆项目，再复制 `.env.example` 为 `.env`。以下命令使用 PowerShell：

```powershell
git clone https://github.com/greenC2h/DailyNews-Agent.git
cd DailyNews-Agent
Copy-Item .env.example .env
```

在 `.env` 中至少设置数据库连接和模型参数：

```dotenv
DATABASE_URL=postgresql+psycopg://用户名:密码@主机:5432/数据库名
SESSION_SECRET=替换为足够长的随机密钥
MODEL_API_KEY=你的模型API密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=your-provider-model-name
NEWS_MODEL_NAME=your-provider-model-name
```

设置 `DATABASE_URL` 后会优先使用它；否则可配置 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER` 和 `DB_PASSWORD`。请妥善保管 `.env`，该文件已被 Git 忽略。

请先创建数据库；Alembic 负责创建表，不会创建数据库本身。数据库账号需要建表权限。连接 URL 密码中的特殊字符需要 URL 编码，也可改用分开的 `DB_*` 参数。两个模型名称均需替换为供应商实际支持的名称；使用其他兼容供应商时，同时修改 `DEEPSEEK_BASE_URL`。`MODEL_NAME` 用于助手对话，`NEWS_MODEL_NAME` 用于简报生成。

### 2. 安装依赖、迁移数据库并构建前端

```powershell
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
uv run alembic upgrade head
```

### 3. 启动 API 和 Worker

在仓库根目录打开两个终端。

终端一，启动 Web 服务：

```powershell
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

终端二，启动常驻 Worker：

```powershell
uv run python -m app.worker
```

访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)，注册账号并配置新闻偏好。可通过 [http://127.0.0.1:8000/healthz](http://127.0.0.1:8000/healthz) 检查服务就绪状态。

首次体验时填写主题和明确的新闻起止时间，提交立即生成任务。生成期间保持 Worker 终端运行，完成后即可预览或下载简报。启用邮件发送或定时推送前，请先配置 SMTP。`/healthz` 只检查 Web 服务的数据库连接，不检查 Worker 或外部供应商。

开发前端时可改用 Vite：执行 `npm --prefix frontend run dev` 并访问 [http://localhost:5173](http://localhost:5173)。开发服务器会将 `/api` 请求代理到 8000 端口；API 进程仍需保持运行。

## 可选集成

- **邮件投递：** 设置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD` 和 `SMTP_FROM`。SMTP 成功仅代表中继接受邮件，不代表邮件最终进入收件箱。
- **语义事件检索：** 设置 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION`，并配置 `MILVUS_URI` 及所需的 `MILVUS_TOKEN`。使用 `uv run python -m app.cli init-milvus` 显式初始化 collection。更换 Embedding 维度时使用新的 collection 名称，不要删除或覆盖现有 collection。
- **沙箱助手工具：** 配置 `OPEN_SANDBOX_API_KEY`、`OPEN_SANDBOX_BASE_URL`，并在需要时设置 `OPEN_SANDBOX_ID`。

全部支持的配置项及默认值见 `.env.example`。简报 HTML 默认保存在 `./data/html`，可通过 `HTML_ROOT` 修改。

## 开发与测试

### 业务数据库建表 SQL

项目根目录的 [schema.sql](schema.sql) 包含 Alembic 版本 `c3e8f1a4b209` 对应的 9 张 PostgreSQL 业务表、外键和索引，不包含 LangGraph 自行维护的表或业务数据。常规部署优先使用前述 Alembic 迁移；如需手动初始化空数据库：

```powershell
psql -h localhost -U postgres -d dailynews -v ON_ERROR_STOP=1 -f schema.sql
uv run alembic stamp c3e8f1a4b209
```

登记版本前请确保 `.env` 指向同一个数据库。不要对已有业务表的数据库执行此快照，也不要导入后再次执行从零建表迁移。后续升级仍使用 `uv run alembic upgrade head`；框架表由应用启动时初始化。

```powershell
uv run python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
```

自动化测试不能替代对模型、SMTP 或 Milvus 服务的真实验收。项目验收记录和待办事项仅保存在本地 `doc/实施进度.md`；`doc/` 目录不会上传到 GitHub。

## 安全说明

- 本地开发之外请使用独立且足够长的 `SESSION_SECRET`；通过 HTTPS 提供服务时设置 `COOKIE_SECURE=true`。
- 不要提交 `.env`、模型凭据、SMTP 密码或本地验收数据。
- 如需开放给本机以外访问，请通过配置妥当的 HTTPS 反向代理提供服务。
