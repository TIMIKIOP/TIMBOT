# TIMBOT

TIMBOT is a multi-tenant AI customer support and sales automation platform.

## Run locally

Requirements: Python 3.11+ and Node.js 20+.

Backend terminal, from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn timbot.main:app --reload --app-dir backend
```

Frontend terminal:

```powershell
cd frontend
npm run dev
```

For PostgreSQL/pgvector and queued document indexing, see [LOCAL_SETUP.md](LOCAL_SETUP.md) for Docker Compose and the Windows Celery worker command. The SQLite workflow remains available without Docker. PostgreSQL and Redis have not yet been runtime-verified in this environment because Docker is unavailable here.

Open `http://localhost:5173`; the API and interactive docs are at `http://localhost:8000` and `http://localhost:8000/docs`.

For a fresh setup, create `.venv`, install `pip install -e ".[dev]"`, copy `.env.example` to `.env`, then run `npm ci` in `frontend`. The API uses SQLite for local development. Production uses PostgreSQL with pgvector and must run `alembic upgrade head` first.

## Current behavior

Account signup/login, workspace selection, product CRUD/import, document upload/indexing, tenant-scoped conversations, manual takeover, and analytics are connected to the API. The local end-to-end acceptance flow passed against Ollama using `llama3.2:1b` and `nomic-embed-text`; it verified exact product facts, retrieved policy text, unknown-product escalation, stored history, and human handover. `llama3.2:1b` is the default because Qwen 3:8B was CPU-bound on the verified host. Local inference uses Ollama at `http://localhost:11434/v1`; no OpenAI server calls or paid API credentials are used. Social integrations are not implemented yet.

See [BUILD_STATUS.md](BUILD_STATUS.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [LOCAL_SETUP.md](LOCAL_SETUP.md).
