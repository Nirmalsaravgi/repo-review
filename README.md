# Repo Understanding

Web app that connects to GitHub, clones a selected repository, and (across Phases 0–4)
helps engineers understand it through conversation, architecture maps, and git-history
intelligence.

Companion docs:
- [`repo-understanding-prd.md`](./repo-understanding-prd.md)
- [`implementation-plan.md`](./implementation-plan.md)

## Current status

**Phase 0 / Week 1 foundations** are scaffolded:

- Docker Compose: Postgres 16 (`pgvector`, `pg_trgm`) + Redis
- FastAPI API: GitHub App login, webhooks, repo sync/select, shallow clone via pygit2
- Next.js shell: auth + repo picker
- Alembic migration with tenant RLS policies
- Celery worker stub (for later phases)

Not yet: agent chat (Week 3), eval harness (Week 4), git intelligence (Phase 1).

## Prerequisites

- Python 3.12+ (3.13 works)
- Node 20+
- Docker Desktop
- A [GitHub App](https://github.com/settings/apps/new) (see below)

## Setup

```powershell
# 1. venv
& d:/projects/gitHubRev/repo-review/.venv/Scripts/Activate.ps1
pip install -e ".[dev]"

# 2. env
copy .env.example .env
# edit .env — fill GitHub App fields; place private-key.pem at repo root

# 3. infra
docker compose up -d

# 4. migrations
$env:PYTHONPATH = "packages/core/src;apps/api/src;apps/worker/src"
alembic upgrade head

# 5. API (port 8001 — 8000 is often taken locally)
uvicorn api:app --reload --port 8001 --app-dir apps/api/src

# 6. web (separate terminal)
cd apps/web
npm install
$env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8001"
$env:API_BASE_URL = "http://localhost:8001"
npm run dev
```

Open http://localhost:3000

> **Ports:** Postgres is on **5433**, API on **8001**, to avoid clashes with other local Docker stacks that already use 5432/8000.

## GitHub App configuration

Create a GitHub App with:

| Setting | Value |
| :-- | :-- |
| Homepage URL | `http://localhost:3000` |
| Callback URL | `http://localhost:8001/auth/callback` |
| Setup URL (optional) | `http://localhost:3000` |
| Webhook URL | `https://<tunnel>/webhooks/github` (use ngrok/cloudflared locally) |
| Webhook secret | same as `GITHUB_APP_WEBHOOK_SECRET` |
| Permissions | Repository contents (read), Metadata (read), Pull requests (read), Issues (read) |
| Events | `push`, `installation`, `installation_repositories` |
| Where can this App be installed? | Any account |

Generate a private key, save as `private-key.pem` in the repo root.

`.env` fields:

```
GITHUB_APP_ID=...
GITHUB_APP_CLIENT_ID=...
GITHUB_APP_CLIENT_SECRET=...
GITHUB_APP_PRIVATE_KEY_PATH=./private-key.pem
GITHUB_APP_WEBHOOK_SECRET=...
GITHUB_APP_SLUG=your-app-slug
```

## Week 1 exit criteria

You can log in, install the App, pick a repo, and see it shallow-cloned under
`data/clones/<org_id>/<github_repo_id>/`.

## Layout

```
apps/api          FastAPI
apps/worker       Celery (stub)
apps/web          Next.js UI
packages/core     Shared models, GitHub App helpers, clone service
alembic           Migrations
infra/postgres    DB init (extensions + app role)
evals             (Week 4)
```
