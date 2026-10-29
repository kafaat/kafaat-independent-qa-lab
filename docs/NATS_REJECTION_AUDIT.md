# Durable review evidence before JetStream termination

## Resume point and defect
Continues `6b5c0a9b748e9432df2a3387d286a855376bbeb5` after PRs #18/#19.
The consumer previously called `term()` for invalid JSON, envelope or event-ID
collisions without a durable operator record. A fake-I/O run of that exact
baseline produced `CREATE -> term -> drain` and no audit INSERT. This is not a
production failure injection.

## Contract
- Valid delivery: commit Inbox, then ACK. Legitimate duplicate: ACK only after
  existing subject/payload match. Previous collision and idle-timeout guards stay.
- Invalid delivery: record `qa_nats_rejections` in a separate transaction, exit
  both transaction/connection contexts successfully, then TERM.
- Failure to identify or commit the rejection: delayed NAK (2 seconds), never TERM.
  When NAK itself cannot be sent, leave the message unacknowledged and log a fixed
  code; broker redelivery is still subject to its configured policy.
- A committed rejection followed by failure to send TERM stays durable. Redelivery
  updates the same review item's observation count. No automatic replay/closure.
- Audit state is `REVIEW_REQUIRED`, not `TERM_CONFIRMED`: sending TERM is not
  independent proof of durable server-side termination.

Records contain stream, consumer, stream sequence, fixed reason code, byte size,
SHA-256 of subject and raw bytes, and observation/delivery counts. Only the two
allowlisted subjects are stored literally; others are `other` plus a digest.
Raw payloads, headers, reply subjects, tokens and exception text are not stored
or logged. Rejection identity includes broker coordinates AND content hashes;
reused UUIDs or changed bytes cannot overwrite an earlier review item.

## Operator read-only review
```sql
SELECT reason_code, count(*) AS review_items, sum(observations) AS observations
FROM qa_nats_rejections GROUP BY reason_code ORDER BY reason_code;

SELECT rejection_id, stream_name, consumer_name, stream_sequence, subject_tag,
       reason_code, payload_size, payload_sha256, first_seen, last_seen,
       observations, max_delivery_count
FROM qa_nats_rejections
WHERE review_state = 'REVIEW_REQUIRED'
ORDER BY first_seen LIMIT 100;
```

This is a metadata audit index, NOT a payload-retaining dead-letter queue. Retain
JetStream data according to an independently reviewed policy to investigate raw
messages while they are available. No dashboard mutation route, alert delivery,
retention/purge job, or production approval is added by this patch. Review storage
limits and least-privilege database roles before wider rollout.

## Schema and validation
The single new table is additive and initialized beside Inbox on consumer startup.
An old consumer can still run, but would resume the old unaudited TERM behaviour;
rolling back requires an explicit operational decision. Do not drop audit data.

29 stdlib behavioural cases exercise commit/TERM ordering, commit/write failures,
redelivery, collisions, auth-like content redaction, malformed JSON and cancellation.
A separate CI job uses real PostgreSQL 18 + JetStream for three cases: collision
and malformed delivery recording; read-only database write failure followed by
redelivery; and simulated TERM transport failure after commit followed by replay.
The TERM exception is injected, not a process-kill test. Local fake-I/O tests are
not claimed to replace these live checks. Live acceptance tests create and drop
only uniquely named disposable schemas/streams in CI, not Railway production.

Reference: NATS documents `AckTerm` as stopping redelivery and emitting a
termination advisory. See https://docs.nats.io/using-nats/developer/develop_jetstream/consumers.
The database record here does not depend on anyone subscribing to that advisory.
