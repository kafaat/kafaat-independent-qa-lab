# Reliability, cost and parity: bounded advisory primitives

- `qa_lab.reliability_policy` is an in-memory breaker and *read-only* retry policy, not durable circuit state, a scheduler, or automatic retry. Mutations must be reconciled with their authoritative storage after unknown outcomes; an idempotency key alone is not sufficient.
- `qa_lab.cost_accounting` sums supplied decimal events by unique event ID and a single currency; it does not fetch real prices or attest invoices.
- `qa_lab.parity_inventory` audits declared progress and does not run legacy engines, turn PARTIAL into VERIFIED, or establish deployed parity.
- These modules are additive and are not wired to API/worker routes. In particular they do not change existing state machine, receipts or case closure authority.
- Release use requires separate integration tests and source-bound evidence. No n8n execution or dataset ingestion is implied.

Run: `python -m unittest discover -s tests -p 'test_qa_improvements.py' -v`.
