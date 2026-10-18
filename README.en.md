# DailyNews

**A self-hosted, scheduled news briefing service.** DailyNews collects articles from news feeds, turns them into concise HTML briefings, and can deliver them by email. It also supports on-demand briefings, recurring schedules, briefing history, semantic search, and persistent assistant conversations.

English | [简体中文](README.md)

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

## Getting Started

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

## Optional Integrations

- **Email delivery:** set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`. Successful SMTP submission means the relay accepted the message; it does not prove final inbox delivery.
- **Semantic event search:** set `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSION`, then set `MILVUS_URI` and any required `MILVUS_TOKEN`. Initialize a collection explicitly with `uv run python -m app.cli init-milvus`. Do not delete or overwrite an existing collection when changing embedding dimensions; use a new collection name.
- **Sandbox-backed assistant tools:** configure `OPEN_SANDBOX_API_KEY`, `OPEN_SANDBOX_BASE_URL`, and, when applicable, `OPEN_SANDBOX_ID`.

All supported settings and defaults are listed in `.env.example`. Store briefing HTML under `HTML_ROOT` (default: `./data/html`).

## Database Schema

The root [schema.sql](schema.sql) contains the nine PostgreSQL business tables, foreign keys, and indexes at Alembic revision `c3e8f1a4b209`. It excludes LangGraph-managed tables and business data. Normal deployments should use Alembic as described above. For manual initialization of an empty database:

```powershell
psql -h localhost -U postgres -d dailynews -v ON_ERROR_STOP=1 -f schema.sql
uv run alembic stamp c3e8f1a4b209
```

Configure `.env` for that same database before stamping. Do not run this snapshot against an existing database or run the initial migrations again after importing it. Future schema upgrades still use `uv run alembic upgrade head`; framework tables are initialized by the application.

## Development and Tests

```powershell
uv run python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
```

Automated tests cover application behavior. Verify model generation, SMTP submission, and Milvus indexing separately against your configured services.

## Security Notes

- Use a unique, long `SESSION_SECRET` outside local development and set `COOKIE_SECURE=true` when serving over HTTPS.
- Do not commit `.env`, provider credentials, SMTP passwords, or local acceptance data.
- Expose the service through a properly secured HTTPS reverse proxy when making it available beyond localhost.

