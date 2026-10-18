<p align="center">
  <img src="assets/logo.svg" width="112" height="112" alt="DailyNews logo">
</p>

<h1 align="center">DailyNews</h1>

<p align="center"><strong>Your interests. Your daily briefing.</strong></p>
<p align="center">A self-hosted AI news assistant, from article collection to a briefing in your inbox.</p>

<p align="center">
  <a href="README.md">简体中文</a> · English
</p>

<p align="center">
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square">
  <img alt="FastAPI" src="https://img.shields.io/badge/API-FastAPI-009688?style=flat-square">
  <img alt="Vue 3" src="https://img.shields.io/badge/Web-Vue_3-42B883?style=flat-square">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/Storage-PostgreSQL-4169E1?style=flat-square">
</p>

## ✨ Less catching up. More understanding.

Tell DailyNews which topics and keywords matter to you. It collects articles, organizes coverage into events, and produces an HTML briefing with source links. Generate one when you need it, or set a daily reading time and receive it by email.

| | What you can do |
| :--- | :--- |
| 🎯 **Follow your interests** | Choose topics, keywords, language, and briefing style around what matters to you. |
| 📰 **Read by event** | Turn articles into structured events and summaries, preserving sources, differences, and uncertainty. |
| ⏰ **Build a daily habit** | Run daily or one-time tasks. Daily schedules cover the previous calendar day in the task's timezone. |
| 💬 **Work through conversation** | Ask the assistant to create briefings, manage schedules, update preferences, and query history, with clarification and execution approval flows. |
| 🔎 **Build a useful archive** | Search saved events and create topic briefings from your history, with optional Milvus hybrid search. |
| 📬 **Read and share your way** | Preview in the browser, download a standalone HTML file, or send it to your account email through SMTP. |

### Built for ongoing operation

PostgreSQL persists tasks, preferences, briefings, and conversations. A dedicated Worker handles collection, generation, and scheduling with task leases, recovery, and idempotency. Uncertain mail outcomes require user confirmation before a retry is allowed. You configure the model and external services; HTML files remain in a controlled local directory.


## 🧭 From news feeds to your inbox

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

## 🚀 Getting Started

### One-click startup on Windows

After the initial configuration, double-click `启动DailyNews.bat` in the repository root. The launcher synchronizes dependencies, builds the frontend, applies database migrations, starts the Web service and Worker, then opens the browser after `http://127.0.0.1:8000/healthz` is ready. Closing the launcher window stops the two processes started by that run.

Startup logs are written to `data/logs/`; check `web-error.log` and `worker-error.log` first when startup fails. The first run may take longer while dependencies are installed and the frontend is built.

### Requirements

- Git, Python 3.12 or newer, and [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 or newer and npm
- A reachable PostgreSQL database
- Model API credentials to generate briefings. SMTP, Milvus, and OpenSandbox are optional until their features are needed.

Before starting, provision a PostgreSQL database and obtain credentials for your model provider.

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

## 🔌 Connect your services

- **Email delivery:** set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`. Successful SMTP submission means the relay accepted the message; it does not prove final inbox delivery.
- **Semantic event search:** set `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSION`, then set `MILVUS_URI` and any required `MILVUS_TOKEN`. Initialize a collection explicitly with `uv run python -m app.cli init-milvus`. Do not delete or overwrite an existing collection when changing embedding dimensions; use a new collection name.
- **Sandbox-backed assistant tools:** configure `OPEN_SANDBOX_API_KEY`, `OPEN_SANDBOX_BASE_URL`, and, when applicable, `OPEN_SANDBOX_ID`.

All supported settings and defaults are listed in `.env.example`. Store briefing HTML under `HTML_ROOT` (default: `./data/html`).

## 🗄️ Database Schema

[database/schema.sql](database/schema.sql) contains the nine PostgreSQL business tables, foreign keys, and indexes at Alembic revision `c3e8f1a4b209`. It excludes LangGraph-managed tables and business data. Normal deployments should use Alembic as described above. For manual initialization of an empty database:

```powershell
psql -h localhost -U postgres -d dailynews -v ON_ERROR_STOP=1 -f database/schema.sql
uv run alembic stamp c3e8f1a4b209
```

Configure `.env` for that same database before stamping. Do not run this snapshot against an existing database or run the initial migrations again after importing it. Future schema upgrades still use `uv run alembic upgrade head`; framework tables are initialized by the application.

## 📁 Repository Guide

```text
DailyNews-Agent/
├── app/             # API, business services, database models, and Worker
├── frontend/        # Vue workspace
├── alembic/         # Versioned database migrations
├── database/        # Business schema SQL snapshot
├── assets/          # Project branding and README assets
├── scripts/         # Isolated acceptance and operational helpers
├── skills/          # News briefing skill instructions
├── tests/           # Backend automated tests
├── main.py          # Web service entry point
├── .env.example     # Configuration template
└── README*.md       # Chinese and English project guides
```

Generated briefings and acceptance artifacts live in `data/`; local project documents live in `doc/`. These directories, `.env`, and `AGENTS.md` are excluded from version control. Frontend tests live alongside modules in `frontend/src/`. Migrations and the SQL snapshot have separate responsibilities.

## 🛠️ Development and Tests

```powershell
uv run python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
```

Automated tests cover application behavior. Verify model generation, SMTP submission, and Milvus indexing separately against your configured services.

## 🔐 Deployment Notes

- Use a unique, long `SESSION_SECRET` outside local development and set `COOKIE_SECURE=true` when serving over HTTPS.
- Do not commit `.env`, provider credentials, SMTP passwords, or local acceptance data.
- Expose the service through a properly secured HTTPS reverse proxy when making it available beyond localhost.

