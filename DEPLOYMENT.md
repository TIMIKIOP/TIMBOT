# Deployment readiness

No public deployment has been performed. A starter Alembic migration is included, and production startup requires a non-development `APP_SECRET_KEY` and will not create tables automatically.

Before production, provision and verify PostgreSQL with pgvector and Redis; add a durable job worker, inbound webhook idempotency, outgoing outbox and retries, encrypted provider credentials, audit logging, rate limiting, monitoring, account recovery, and security/load tests. Configure secrets through the deployment platform, serve behind HTTPS, and run `alembic upgrade head` before starting the API.

Instagram and Messenger OAuth/webhooks/outbound delivery remain a separate next milestone requiring a Meta app and authorized business accounts. Local development uses Ollama and has no paid inference dependency. Production should use a capacity-managed hosted or dedicated inference provider rather than assuming a developer laptop can serve multiple tenants. No live OpenAI request or paid usage is used by the local provider.
