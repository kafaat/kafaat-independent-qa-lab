# PostgreSQL migration — experimental

The current v0.4.5 API and worker use SQLite. A new optional PostgreSQL queue adapter is being tested offline and is **not** wired to the live execution worker. Its claim uses `FOR UPDATE SKIP LOCKED`; RUNNING work must not be requeued automatically after a crash. NATS is optional and not yet part of a proven transactional outbox. No Railway deployment or production acceptance is claimed.

Acceptance before activation: real PostgreSQL concurrency, crash-before-ack, restart reconciliation, receipt binding, Compose network isolation, and source SHA verification.
