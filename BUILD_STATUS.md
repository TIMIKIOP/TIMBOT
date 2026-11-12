# TIMBOT build status

Updated: 2026-11-12

## Sprint result

| Workstream | Status | Evidence |
|---|---|---|
| Local Ollama | **PASS** | Ollama API responds. `llama3.2:1b` and `nomic-embed-text` are installed and verified with live inference. `qwen3:8b` is installed but runs on 100% CPU on this host and was unresponsive during the direct inference check; the tested 1B model is the local default. |
| End-to-end chatbot | **PASS** | Real local API test registered/logged in a workspace, created a product, uploaded/indexed an FAQ, asked product and policy questions, checked unknown product escalation, read persisted history, took over manually, sent agent reply, and returned to AI. |
| Grounding and tenant safety | **PASS for tested paths** | Product price/stock are rendered from the tenant's live DB record; policy answer quotes a retrieved tenant FAQ; unknown product asks for a human. Ollama tenant-scoped retrieval test passed. This is not an order-management integration. |
| Ollama tool calling | **PASS** | Actual 1B model returned a JSON inventory-tool request. Provider normalizes that local-model format into a tool call only when the name is declared by the current request. Live tool test passed. |
| Automated tests | **PASS** | Standard suite: **10 passed, 5 skipped, 1 warning**. The skipped tests are opt-in Ollama integration cases and PostgreSQL integration without Docker/database. Actual Ollama suite with tool checks: **4 passed, 1 warning** before the INR display change; the pricing-specific live Ollama acceptance was rerun and passed afterward. |
| Python/migrations | **PASS** | `pip check`, compileall, and `alembic check` passed. SQLite app workflow was exercised. |
| Dashboard TypeScript | **PASS** | `tsc --noEmit` passed. Dashboard data flows call the backend APIs; tested backend endpoints cover signup/login, workspace selection, products/import, knowledge, conversations, human mode/replies, analytics, and AI readiness. |
| INR pricing | **PASS** | Existing integer `price_cents` values are preserved and defined as paise. The dashboard accepts rupees and displays INR; API product responses identify INR; customer-facing product facts are grounded and formatted as INR. API, formatter, conversion, and live Ollama answer assertions pass. No migration or data rewrite was needed. |
| Local web delivery status | **PASS** | AI and agent outbound web messages now persist as `stored`. Inbox renders stored and historical queued web messages as “Saved in web chat”; non-web queued statuses remain delivery queue states. No history migration was performed. |
| RAG answer quality | **PASS** | Policy retrieval passes only query-relevant facts into the Ollama prompt; policy output is composed from tenant-scoped retrieved facts rather than model evidence or raw documents. Domestic shipping questions receive only matching domestic facts. International destinations require explicit cross-border evidence; without it, the customer is offered human support. Return, shipping, refund, unknown-policy, and international-shipping regressions pass. |
| Product catalogue routing | **PASS** | Stock, price, SKU, and size questions use the workspace-filtered product catalogue before policy retrieval. Product-name matching rejects partial generic overlap such as “Nike shoes” against “Running Shoes”; missing or ambiguous catalogue facts use human fallback. Tenant isolation is covered by backend tests. |
| Product follow-up context | **PASS** | Recent product names/SKUs in the conversation are resolved against the current workspace catalogue for references such as “them,” “it,” “those,” “its price,” and “tell me more.” Description, price, size, and stock follow-ups are answered from the matched record; ambiguous or unavailable references use human support. |
| Frontend production build | **BLOCKED** | `npm run build` fails at Vite config loading with `spawn EPERM` while esbuild starts its child service. The Vite `--configLoader runner` workaround also failed at a child `execFile` call. No permissions or security controls were changed. |
| PostgreSQL/Redis/Celery | **NOT VERIFIED** | Docker CLI is not installed/available. Compose definitions and migrations exist, but no containers, persistence, Redis queue, worker, or PostgreSQL tenant-isolation integration test could be run. |
| GPU | **NOT VISIBLE** | `nvidia-smi` is not installed/available and Windows video-controller inventory returned access denied. Ollama reports the 8B and 1B chat models running on CPU. No GPU/VRAM claim is made. |

## What is implemented

- Modular AI provider abstraction; Ollama is default and has no paid API key requirement. `llama3.2:1b` is configured as the responsive local default; `nomic-embed-text` remains the embedding model.
- Local endpoint is explicitly configured (`http://localhost:11434/v1`); the compatible SDK client disables environment proxies and redirects and rejects OpenAI hostnames. No OpenAI server calls were made.
- Chat, local embeddings, vector dimension/profile checks, reindex signaling, SQLite cosine retrieval, PostgreSQL pgvector retrieval path, model health/readiness, timeouts/retries, inference usage/latency recording, and safe fallback/handover.
- Workspace-filtered product/inventory/order tools. Factual product and inventory output is based on stored rows. Retrieved policy excerpts must match the requested topic. A local JSON tool-call normalization handles Ollama model responses that place an allow-listed tool request in message content.
- Prices are represented by the legacy integer `price_cents` column as paise (100 paise = ₹1), with INR explicit in API responses and AI context. The product form accepts INR amounts and converts to paise; AI product answers format verified records as rupees and cannot leak model-generated USD pricing.
- Local `web` outbound records are stored locally rather than queued for external transport. The Inbox distinguishes this local state while retaining old database values. No Redis/Celery worker or fake delivery behavior was introduced.
- React dashboard uses API-backed authentication, workspace, catalogue, knowledge, inbox, AI status, and analytics. Social channels correctly remain unconnected. Team invitations/role administration are not implemented.

## Windows frontend build restriction

Node/esbuild/Vite cannot create child processes in this managed Windows execution environment. The production build fails with `spawn EPERM` at `esbuild.ensureServiceIsRunning`; bypassing config bundling with Vite's runner still fails at Vite's `execFile` path-resolution subprocess. Direct investigation in the earlier build sprint also found ordinary child-process probes denied, while launching esbuild directly succeeded. The evidence points to a managed process-creation restriction; it does not identify antivirus or another specific endpoint-protection product as the cause. No ACL changes or security disabling were attempted.

## Test results

- Standard: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` — **22 passed, 5 skipped, 1 warning** (Starlette/httpx deprecation notice), including product routing/follow-ups, tenant isolation, domestic and international shipping regressions.
- Actual Ollama: `$env:TIMBOT_TEST_OLLAMA='1'; $env:TIMBOT_TEST_OLLAMA_TOOLS='1'; .\.venv\Scripts\python.exe -m pytest tests\test_ollama_integration.py -v -p no:cacheprovider` — **4 passed, 1 warning** in 74.03 seconds. Tests covered chat/embeddings, real tenant-filtered retrieval and product lookup, model-generated inventory tool call, product catalogue routing and follow-ups, policy grounding, and API acceptance including takeover.
- `.venv\Scripts\python.exe -m pip check` — passed, no broken requirements.
- `compileall` for backend and tests — passed.
- `.venv\Scripts\alembic.exe check` — passed, no new operations detected.
- Frontend TypeScript check — passed.
- Frontend production build — **failed/blocked** by child-process EPERM; bundle was not generated.
- PostgreSQL/pgvector/Redis/Celery integration — **not run**, Docker missing.
- Local web status and history checks: included in the standard backend suite; frontend labels checked with `node frontend/tests/currency.test.js`.
- Real Ollama API acceptance, including stock, price, SKU, and size queries; description/price/size/stock follow-ups; missing-product fallback; concise return-policy answer; unsupported international shipping fallback; persisted message history; and human takeover: **1 passed** (74.03 seconds in the full Ollama suite).

## Pricing verification

- Database schema and migrations retain `price_cents` as an integer; no conversion migration was run. Existing stored values remain unchanged and are interpreted as paise: `179900` -> ₹1,799.00.
- Product API create/read test verified `price_cents=179900` persists and API responses identify `currency: "INR"`.
- Frontend helper checks: `node frontend/tests/currency.test.js` — passed formatting (`₹1,799.00`), paise conversion (`1799.50` -> `179950`), blank values, and invalid precision. Dashboard TypeScript check passed.
- AI grounding test verifies INR output and rejects USD text. Live Ollama API acceptance verified a product answer contains INR pricing and no USD label.

## Hardware and service limits

Ollama reports `llama3.2:1b` running on CPU; a full live API acceptance run took about 71 seconds before tool normalization and about 115–141 seconds in later full integration runs. `qwen3:8b` consumed roughly 5.9 GB in Ollama's process report and remained CPU-bound/unresponsive during the attempted check. Local laptop inference is development-scale, with limited throughput and concurrency; use hosted inference or dedicated GPU capacity before serving multiple businesses at production load.

## Local run commands

Install Ollama for Windows, then:

```powershell
ollama pull llama3.2:1b
ollama pull nomic-embed-text
ollama list
Invoke-RestMethod http://localhost:11434/api/tags
```

From the repository root, create local config once and start the backend:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\alembic.exe upgrade head
uvicorn timbot.main:app --reload --app-dir backend
```

In another PowerShell terminal:

```powershell
cd frontend
npm run dev
```

Dashboard: `http://localhost:5173`; API docs: `http://localhost:8000/docs`; AI health: `http://localhost:8000/health/ai`.

For real local Ollama coverage:

```powershell
$env:TIMBOT_TEST_OLLAMA = '1'
$env:TIMBOT_TEST_OLLAMA_TOOLS = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_ollama_integration.py -v -p no:cacheprovider
```

For PostgreSQL/Redis/worker verification, install and start Docker Desktop, then follow the generated credentials and `docker compose up -d postgres redis` instructions in `LOCAL_SETUP.md`.
