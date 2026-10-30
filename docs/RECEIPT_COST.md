# Receipt-bound estimated cost

`qa_lab.receipt_cost.estimate(lab, run_ids, usd_per_hour=...)` verifies saved SQLite
run receipts through the existing `Lab.verify` method, checks the run status
and timestamps, then estimates **wall-clock compute cost** using an explicit
caller-supplied USD/hour rate. Duplicate run IDs and absent/invalid receipts
fail closed. This is read-only: it does not modify receipts or the run table.

No actual CPU allocation or cloud usage is measured, no provider rate is fetched,
no invoice is reconciled, and no cost is used to change test verdicts. The receipt
hash only provides consistency, not independent host identity. RUNNING, PENDING and
BLOCKED runs are excluded because they have no confirmed executed interval.

Full legacy parity remains NOT_RUN/PARTIAL as declared in
`docs/LEGACY_PARITY_PROGRESS.json`; this feature does not execute legacy profiles.

Test: `python -m unittest discover -s tests -p 'test_receipt_cost.py' -v`.
