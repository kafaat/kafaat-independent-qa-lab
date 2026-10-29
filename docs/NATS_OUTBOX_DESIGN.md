# NATS integration contract — NOT IMPLEMENTED

PostgreSQL is the sole authority for job state. A future outbox table will be written in the same transaction as a state transition. A relay will publish events to NATS JetStream using stable event IDs; consumers will deduplicate by durable inbox keys. NATS delivery acknowledgement alone must not mark a job PASSED.

## Required acceptance tests
1. Database commit followed by publisher crash.
2. NATS publish followed by crash before outbox acknowledgement.
3. Duplicate and reordered delivery.
4. NATS restart and reconnection.
5. Missing consumer and delayed replay.
6. No automatic replay of RUNNING execution after worker death.

No NATS integration or exactly-once guarantee is claimed by this document.
