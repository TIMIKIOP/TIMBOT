# Security notes

## Implemented in this increment

- Passwords use `pwdlib`'s recommended Argon2-backed hash.
- Access tokens are signed, expire after 12 hours, and carry a workspace scope. Workspace switching verifies membership before issuing a new token.
- Product, document, conversation, message, and analytics queries include the workspace derived from the verified membership.
- Owner/admin checks protect catalogue writes, imports, document indexing/deletion, and workspace renaming. Agent access is limited to support workflows.
- Uploads have a 10 MB limit. XLSX archives are bounded by entry count and expanded size; extracted knowledge is capped at 1 MB. No files are saved to executable paths.
- OpenAI credentials and model settings are read from environment configuration only. No live request is made until the API is invoked with credentials.
- Production startup refuses the development secret and does not auto-create schema; Alembic migrations manage production schema.

## Still required before production

- Add rate limiting, email verification, password reset, token revocation, invite lifecycle, audit logging, and stronger abuse protection.
- Encrypt social access tokens at rest and validate Meta webhook signatures before accepting provider events.
- Add durable inbound event idempotency, outbox delivery, worker retries, and monitoring.
- Run against managed PostgreSQL/pgvector and Redis, then perform security and load testing.
- Serve behind HTTPS with strict trusted origins/hosts and managed secrets.

The current app does not connect Meta accounts and does not display simulated channel activity. Analytics values are derived from stored workspace rows; conversions and AI token usage show as untracked until actual events/usage accounting is implemented.
