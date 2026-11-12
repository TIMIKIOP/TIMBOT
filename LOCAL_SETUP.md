# Local setup

## Dependencies

This project was verified with Python 3.14.3, Node.js 24.14.1, and npm 11.11.0. Python 3.11+ and Node.js 20+ are required.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

If `.env` already exists, keep it and edit it rather than overwriting it. SQLite is the local default. Local AI uses Ollama and requires no API key. Set a unique `APP_SECRET_KEY` before production.

## Install local Ollama models

Install Ollama for Windows from [ollama.com/download](https://ollama.com/download), then use PowerShell:

```powershell
ollama pull nomic-embed-text
ollama pull llama3.2:1b
ollama list
```

Start Ollama if it is not already running, then verify its local API:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
```

The verified local configuration uses `AI_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://localhost:11434/v1`, `OLLAMA_CHAT_MODEL=llama3.2:1b`, `OLLAMA_EMBEDDING_MODEL=nomic-embed-text`, and `EMBEDDING_DIMENSIONS=768`. These are also the defaults in `.env.example`. Qwen 3:8B is installed on the checked host but was CPU-bound and did not finish a useful inference within the verification window; use it only on hardware where it responds well. Chat and embedding model availability is shown in the dashboard and at `http://localhost:8000/health/ai`. Local Ollama serves development workloads; response time and concurrency depend on available RAM/VRAM and it is not an unlimited multi-tenant production service.

Product prices are INR. The dashboard price input/display uses rupees (for example `1799.00`); the API's legacy `price_cents` integer stores paise (`179900`). In catalogue imports, a decimal `price` value is rupees, while `price_cents` is already paise.

## Run TIMBOT

Backend terminal, from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn timbot.main:app --reload --app-dir backend
```

Frontend terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. API docs are at `http://localhost:8000/docs`.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

```powershell
cd frontend
npm run build
```

Development creates local tables automatically. For a new production database, configure PostgreSQL/pgvector and run `alembic upgrade head` before the API with `APP_ENV=production`.

## PostgreSQL/pgvector and Redis on Windows

Install and start Docker Desktop, then from the repository root create a local `.env` with a random database password. `.env` is ignored by Git. This example generates a password without printing it:

```powershell
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  $dbPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
  $settings = Get-Content .env -Raw
  $settings = $settings.Replace('replace-with-a-local-development-password', $dbPassword)
  $settings = $settings.Replace('DATABASE_URL=sqlite:///./timbot.db', "DATABASE_URL=postgresql+psycopg://timbot:$dbPassword@localhost:5432/timbot")
  $settings = $settings.Replace('REDIS_URL=', 'REDIS_URL=redis://localhost:6379/0')
  Set-Content .env $settings
} else {
  Write-Output 'Preserving existing .env; verify POSTGRES_USER, POSTGRES_PASSWORD, DATABASE_URL, and REDIS_URL match.'
}
docker compose up -d postgres redis
```

The Compose file binds services to loopback and uses named volumes for PostgreSQL and Redis persistence. After they become healthy, run migrations and the API in separate PowerShell terminals:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
uvicorn timbot.main:app --reload --app-dir backend
```

For background document indexing, start Celery with its Windows-compatible solo worker pool in another terminal:

```powershell
.\.venv\Scripts\celery.exe -A timbot.worker:celery_app worker --pool=solo --loglevel=INFO
```

Set `TIMBOT_TEST_POSTGRES_URL` to a dedicated disposable PostgreSQL database using the pgvector image, then run `pytest -q -p no:cacheprovider`. The opt-in integration test creates and drops its own uniquely named schema and checks that pgvector retrieval cannot return another workspace's document. It remains skipped if the variable is unset. Do not point this variable at production or a database containing user data.

When Docker is unavailable, keep `DATABASE_URL=sqlite:///./timbot.db` and leave `REDIS_URL` blank; knowledge indexing runs synchronously and no queue worker is required.

## Ollama integration test

Automated tests mock provider calls and do not need Ollama or paid API access. After pulling the local chat and embedding models, run the opt-in local inference and API acceptance checks:

```powershell
$env:TIMBOT_TEST_OLLAMA = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_ollama_integration.py -q -p no:cacheprovider
```

To exercise an actual model-generated inventory tool call (the lightweight default model may not emit compatible tool calls):

```powershell
$env:TIMBOT_TEST_OLLAMA_TOOLS = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_ollama_integration.py -q -p no:cacheprovider
```

The database records the embedding provider/model/dimension profile on every knowledge chunk. Retrieval filters by the exact profile and verifies dimensions. A model change never mixes old vectors into retrieval; documents from a previous profile show “Needs indexing” in the dashboard and must be reindexed. PostgreSQL stores an unbounded pgvector type so different profiles can coexist safely; every insert and query is dimension-checked before vector comparison. There is no ANN vector index yet.
