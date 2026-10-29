# Railway acceptance gates
The API, Worker, PostgreSQL and NATS deployments reporting SUCCESS are not end-to-end acceptance.

Required independent evidence: authenticated POST /v1/runs with a unique idempotency key; worker terminal receipt; source SHA and environment binding; outbox relay publishing to JetStream; inbox durable consumption and deduplication; NATS restart with persistent volume; negative authorization test; PostgreSQL restart/recovery; rollback and resource limits.

RAG in this branch is deterministic lexical retrieval only. Dashboard is a minimal informational shell, not a functional control panel. Legacy parity remains NOT_RUN until the original source inventory and fixtures are independently verified.
