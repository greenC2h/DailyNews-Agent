<p align="center">
  <img src="assets/logo.svg" width="112" height="112" alt="DailyNews logo">
</p>

<h1 align="center">DailyNews</h1>

<p align="center"><strong>让关心的新闻，每天如约而至。</strong></p>
<p align="center">从新闻采集、事件整理到邮件推送，一个可以自行部署的 AI 新闻助手。</p>

<p align="center">
  简体中文 · <a href="README.en.md">English</a>
</p>

<p align="center">
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square">
  <img alt="FastAPI" src="https://img.shields.io/badge/API-FastAPI-009688?style=flat-square">
  <img alt="Vue 3" src="https://img.shields.io/badge/Web-Vue_3-42B883?style=flat-square">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/Storage-PostgreSQL-4169E1?style=flat-square">
</p>

## ✨ 少一些信息追赶，多一些有效阅读

把关注的话题和关键词交给 DailyNews。它会采集新闻、整理同一事件的报道，生成带来源链接的 HTML 简报，让日常阅读有一个固定入口。你可以立即生成，也可以设定每天的阅读时间，通过邮箱接收。

| | 你可以做什么 |
| :--- | :--- |
| 🎯 **围绕兴趣订阅** | 设置话题、关键词、语言和简报风格，让阅读围绕你的关注点展开。 |
| 📰 **按事件理解新闻** | 将文章整理为事件资料和摘要，保留来源链接、差异与不确定性。 |
| ⏰ **每天如约整理** | 支持每日和一次性任务；每日规则按任务时区整理昨天自然日的新闻。 |
| 💬 **用对话安排工作** | 通过助手创建简报、管理任务、调整偏好和查询历史；需要补充信息或执行审批时继续对话。 |
| 🔎 **让历史成为资料库** | 检索已保存事件，利用历史内容生成专题简报；可接入 Milvus 混合检索。 |
| 📬 **阅读与分发自由选择** | 在网页预览、下载独立 HTML，或通过 SMTP 发送到账号邮箱。 |

### 为持续运行而设计

任务、偏好、简报和对话保存在 PostgreSQL 中。独立 Worker 执行采集、生成和定时扫描，支持租约恢复与任务幂等；邮件结果不确定时进入待确认状态，由用户核实后再决定是否重试。模型和外部服务由你配置，HTML 文件保存在本地受控目录。


## 🧭 从新闻源到你的收件箱

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

## 🚀 启动指南

### Windows 一键启动

完成首次配置后，可直接双击项目根目录的 `启动DailyNews.bat`。脚本会自动同步依赖、构建前端、执行数据库迁移、启动 Web 服务和 Worker，并在 `http://127.0.0.1:8000/healthz` 就绪后打开浏览器。关闭启动窗口会停止本次启动的两个服务进程。

启动日志位于 `data/logs/`；若启动失败，优先查看 `web-error.log` 和 `worker-error.log`。首次启动可能需要较长时间安装依赖和构建前端。

### 环境要求

- Git、Python 3.12 或更新版本，以及 [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 或更新版本，以及 npm
- 可访问的 PostgreSQL 数据库
- 生成简报所需的模型 API 凭据。SMTP、Milvus 和 OpenSandbox 可在需要相应功能时再配置。

启动前，请准备 PostgreSQL 数据库和模型服务凭据。

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

## 🔌 按需连接你的服务

- **邮件投递：** 设置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD` 和 `SMTP_FROM`。SMTP 成功仅代表中继接受邮件，不代表邮件最终进入收件箱。
- **语义事件检索：** 设置 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION`，并配置 `MILVUS_URI` 及所需的 `MILVUS_TOKEN`。使用 `uv run python -m app.cli init-milvus` 显式初始化 collection。更换 Embedding 维度时使用新的 collection 名称，不要删除或覆盖现有 collection。
- **沙箱助手工具：** 配置 `OPEN_SANDBOX_API_KEY`、`OPEN_SANDBOX_BASE_URL`，并在需要时设置 `OPEN_SANDBOX_ID`。

全部支持的配置项及默认值见 `.env.example`。简报 HTML 默认保存在 `./data/html`，可通过 `HTML_ROOT` 修改。

## 🗄️ 业务数据库结构

[database/schema.sql](database/schema.sql) 包含 Alembic 版本 `c3e8f1a4b209` 对应的 9 张 PostgreSQL 业务表、外键和索引，不包含 LangGraph 自行维护的表或业务数据。常规部署优先使用前述 Alembic 迁移；如需手动初始化空数据库：

```powershell
psql -h localhost -U postgres -d dailynews -v ON_ERROR_STOP=1 -f database/schema.sql
uv run alembic stamp c3e8f1a4b209
```

登记版本前请确保 `.env` 指向同一个数据库。不要对已有业务表的数据库执行此快照，也不要导入后再次执行从零建表迁移。后续升级仍使用 `uv run alembic upgrade head`；框架表由应用启动时初始化。

## 📁 目录导览

```text
DailyNews-Agent/
├── app/             # 后端 API、业务服务、数据库模型与 Worker
├── frontend/        # Vue 工作台
├── alembic/         # 数据库版本迁移（结构变更入口）
├── database/        # 业务建表 SQL 快照
├── assets/          # README Logo 等项目展示资源
├── scripts/         # 隔离环境验收和运行辅助脚本
├── skills/          # 新闻简报技能说明
├── tests/           # 后端自动化测试
├── main.py          # Web 服务入口
├── .env.example     # 配置模板
└── README*.md       # 中英文项目介绍
```

本地生成的简报和验收产物位于 `data/`；本地项目文档位于 `doc/`。这些目录、`.env` 和 `AGENTS.md` 均不纳入版本管理。前端测试与组件放在 `frontend/src/`，数据库迁移与 SQL 快照分别维护。

## 🛠️ 开发与测试

```powershell
uv run python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
```

自动化测试覆盖应用行为。模型生成、SMTP 提交和 Milvus 索引需使用实际配置的服务单独验证。

## 🔐 部署说明

- 本地开发之外请使用独立且足够长的 `SESSION_SECRET`；通过 HTTPS 提供服务时设置 `COOKIE_SECURE=true`。
- 不要提交 `.env`、模型凭据、SMTP 密码或本地验收数据。
- 如需开放给本机以外访问，请通过配置妥当的 HTTPS 反向代理提供服务。
