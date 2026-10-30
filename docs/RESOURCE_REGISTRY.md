# External resource declaration qualification

`qa_lab.resource_registry.evaluate(resources)` performs an offline advisory
validation of proposed external tools, datasets and references. The result is
never authorization to install, download, execute or approve a resource.

Each entry requires `id`, HTTPS `url`, SPDX-like `license`, a full 40/64-character
hex `revision`, and explicit `license_reviewed` and `maintenance_reviewed`
booleans. Those booleans are caller claims, not authenticated independent reviews.
Duplicate normalized URLs and case-insensitive IDs are rejected.

Dataset repository commits alone do not pin mutable data assets: each external
artifact requires an immutable SHA-256 and separate license, privacy and
redistribution review before ingestion. Link availability and upstream maintenance
are not verified by this offline checker.

No network calls, automatic execution, approval, CI hookup, hospital case closure,
or release authority are added. Execute tests with:
`python -m unittest discover -s tests -p 'test_resource_registry.py'`.
