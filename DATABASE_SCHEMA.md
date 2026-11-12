# Database schema

- `users`: unique normalized email and password hash.
- `workspaces`: tenant container.
- `memberships`: user/workspace join with owner/admin/agent role; unique per pair.
- `products`: workspace-scoped product name, SKU, description, `price_cents` integer storing INR paise (100 paise = ₹1), and stock. The legacy column name is retained for compatibility; no migration or data rewrite is needed.
- `knowledge_documents`: workspace-owned parsed source text and file metadata.
- `knowledge_chunks`: workspace/document-scoped chunks with ordinal, embedding profile, and embedding.
- `ai_usage`: workspace-scoped embedding/generation token totals and inference latency by operation.
- `conversations`: workspace-scoped channel, customer, status, AI mode, and creation time.
- `messages`: workspace/conversation-scoped body, direction, delivery status, author type, and timestamp.

Tenant-owned records include `workspace_id`; lookups filter with the workspace from the verified bearer token. Local Nomic embeddings default to 768 dimensions. Embeddings are JSON on SQLite and an unbounded `VECTOR` on PostgreSQL, tagged with `provider:model:dimensions`. Each retrieval filters by both the tenant and the exact profile and validates vector dimensions before cosine comparison. Old vectors without the active profile are ignored until documents are reindexed; incompatible embeddings are never silently mixed.

Product API requests continue to use `price_cents` as integer paise and product API responses include `currency: "INR"`. CSV `price_cents` is interpreted as paise; the decimal CSV `price` column is interpreted as rupees and converted to paise. The dashboard accepts decimal INR and converts it to paise before saving.

Alembic history is in `backend/timbot/migrations/versions`. Local development creates schema automatically. Production applies migrations explicitly before startup.

Redis stores Celery broker messages. Knowledge indexing tasks record the workspace and document IDs and re-check their association before reading content. The PostgreSQL/pgvector integration test is opt-in with `TIMBOT_TEST_POSTGRES_URL` and uses a temporary schema. Local Ollama integration tests are opt-in with `TIMBOT_TEST_OLLAMA=1`.

