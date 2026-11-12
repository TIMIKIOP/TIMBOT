# TIMBOT architecture

## Structure

- `backend/timbot`: FastAPI routes, configuration, SQLAlchemy models, authentication, knowledge ingestion, and AI services.
- `backend/timbot/migrations`: Alembic environment and schema history.
- `backend/timbot/worker.py`: Celery task for retryable, workspace-checked knowledge indexing.
- `frontend`: React, TypeScript, Vite, and Tailwind dashboard.
- `tests`: API tests for auth, tenant isolation, catalogue imports, and human handover.

## Runtime design

Browser requests use bearer authentication. Access tokens carry a selected workspace ID; membership is verified on each request. Tenant-owned queries filter with the workspace resolved from the verified token. SQLAlchemy is the storage boundary. SQLite is the local development default; PostgreSQL with pgvector is the production target.

Messages and conversations are persisted. Outbound messages in local `web` conversations use `delivery_status=stored` because no external transport is involved; historical `queued` rows are retained and rendered as saved locally. Future external-channel adapters can use delivery queue states independently. Human takeover disables AI for that conversation; manual replies are stored as agent messages. AI services use a provider interface; local development defaults to Ollama's OpenAI-compatible endpoint at `http://localhost:11434/v1`. The OpenAI SDK is only the HTTP client for that explicit Ollama URL, with environment proxies and redirects disabled. The provider rejects OpenAI hosts and never uses an OpenAI API key. Chat uses JSON structured responses and product/inventory/order tools; unavailable order state escalates because no order integration exists.

## Integration boundaries

- Instagram and Messenger adapters are future provider-specific boundaries feeding shared conversations.
- Knowledge documents are parsed into bounded text, split into chunks, and indexed with Ollama `nomic-embed-text` embeddings. Retrieval uses pgvector cosine distance on PostgreSQL and in-process cosine scoring for local SQLite.
- Ollama calls use configured timeouts and bounded retries. API failures return a human-handover response after the incoming message is stored. Workspace-scoped token totals and inference latency are stored when provided by Ollama.
- Embeddings are tagged by provider, model, and dimension. Retrieval filters on the active profile and workspace. Indexing validates every returned vector before atomically replacing a document's chunks; incompatible legacy vectors are skipped and documents require reindexing.
- Product facts are read from the current tenant database at answer time; policy answers also use retrieved tenant documents.
- Analytics are calculated from persisted message and conversation records. Conversions and token usage remain explicitly untracked.

## Security invariants

1. Tenant IDs come from verified workspace membership, not request bodies.
2. Catalogue and knowledge mutations require owner/admin membership.
3. Local Ollama mode requires no provider secret; its base URL is explicit, redirects and proxy environment use are disabled, and OpenAI hostnames are rejected.
4. AI answers must use provided product/reference facts; low confidence or uncertainty escalates to a human.
5. Upload size, XLSX archive expansion, and extracted text are bounded.

## Database migrations

Alembic migrations live in `backend/timbot/migrations`. Development uses SQLAlchemy `create_all` for a zero-setup SQLite database. Production runs `alembic upgrade head` before starting with `APP_ENV=production`; production startup does not create tables. PostgreSQL migrations enable pgvector before creating the vector column.

## Deployment target

`compose.yaml` provides local PostgreSQL/pgvector and Redis services with named persistent volumes. The FastAPI server and Celery solo worker run as local Windows processes. PostgreSQL/Redis execution is not verified in the current environment because Docker is unavailable. No public deployment or paid service has been used.
