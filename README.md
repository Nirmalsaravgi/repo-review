# Repo Understanding

Web app that connects to GitHub, clones a selected repository, and (across Phases 0–4)
helps engineers understand it through conversation, architecture maps, and git-history
intelligence.

Companion docs:
- [`repo-understanding-prd.md`](./repo-understanding-prd.md)
- [`implementation-plan.md`](./implementation-plan.md)

## Current status

**Phase 0** (agentic chat) is complete: GitHub App auth, shallow clone, filesystem agent
tools, Gemini tool loop, SSE chat UI, conversation persistence, eval baseline (~0.97 recall@10).

**Phase 1 G2–G7** (git intelligence) is implemented:

- Celery `index_history` pipeline: deepen → commit walk → PR GraphQL backfill → ownership
- Agent tools: `git_log`, `git_blame`, `who_owns`, `why_here`, `explain_diff`, `compare_releases`
- Read APIs: `/repos/{id}/ownership`, `/bus-factor`, `/contributions`, `POST .../index-history`
- Web: History panel beside chat (contributors, ownership bars, bus-factor)
- Eval: `evals/datasets/repo_review_history.json` + `history_hit_rate` in the harness

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
# Exclude data/ so writing clones does not trigger --reload and kill the clone task.
$env:PYTHONPATH = "packages/core/src;apps/api/src;apps/worker/src"
uvicorn api:app --reload --reload-exclude "data/*" --port 8001 --app-dir apps/api/src

# 6. Celery worker (history deepen + ingest — required for ownership / why_here)
# On Windows use --pool=solo (prefork hits PermissionError with billiard).
$env:PYTHONPATH = "packages/core/src;apps/api/src;apps/worker/src"
celery -A worker worker --loglevel=info --pool=solo

# 7. web (separate terminal)
cd apps/web
npm install
$env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8001"
$env:API_BASE_URL = "http://localhost:8001"
npm run dev
```

Open http://localhost:3000

> **Ports:** Postgres is on **5433**, API on **8001**, to avoid clashes with other local Docker stacks that already use 5432/8000.
>
> After you select a repo, the API shallow-clones immediately (chat works), then enqueues
> `index_history` on the Celery worker (deepen + commits + PRs + ownership).

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

## Exit criteria (current)

- Phase 0: log in, pick a repo, get a cited chat answer.
- Phase 1: with Celery running, history indexes after select; chat can answer ownership /
  blame / diff questions; `GET /repos/{id}/ownership` returns scores.

## Layout

```
apps/api          FastAPI (auth, repos, chat, history)
apps/worker       Celery (deepen, history walk, PRs, ownership)
apps/web          Next.js UI
packages/core     Shared models, GitHub App helpers, clone service
packages/providers LLM provider interface (Gemini)
alembic           Migrations (0001–0003)
infra/postgres    DB init (extensions + app role)
evals             Phase 0 eval harness + baseline
```
