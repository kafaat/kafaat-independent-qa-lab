# Impeccable 4.8.0 — isolated static HTML quality E2E

This experiment uses the real [pbakaus/impeccable](https://github.com/pbakaus/impeccable) **4.8.0 deterministic detector** on two synthetic Field Inspection HTML variants. It does not run the LLM critique, browser overlay, modify SAHOOL source, inspect a user's data, or touch production services.

The baseline intentionally includes AI-style gradients, overused font, nested cards, an omitted image alt and an empty accessible label. The candidate uses explicit text hierarchy, accessible labels, a local preview asset and restrained CSS. The paired files are not copies of Field Management or MapHub.

A disposable CI Docker image installs the exact npm CLI version and its optional native detector package with npm install scripts disabled; the actual scan runs as nonroot with network disabled, a read-only fixture mount, dropped capabilities, no new privileges and memory/PID/CPU constraints. The verifier reads at most 2 MiB from each JSON result, rejects duplicate/nonfinite JSON, forged exit status, source/image mismatches and unverifiable findings counts. It checks that **the actual detector** observes fewer primary findings in the candidate.

A baseline detector exit 2 means findings, not an operational tool failure; exit 1 is a failure. Advisory/subjective findings are not used as a blocking decision, with `--no-advisory --no-config`.

Run the standalone regression tests:

```bash
python -m unittest discover -s experiments/impeccable-ui-e2e -p 'test_*.py' -v
```

The only positive result is `OBSERVED_UI_IMPROVEMENT`. The result explicitly marks `visual_browser_acceptance=false`, `wcag_conformance_proven=false`, `sahool_functional_acceptance=false` and `production_accepted=false`.

**Next genuine SAHOOL acceptance:** after this baseline/candidate detector contract is confirmed, scan scoped Field Management, MapHub, Irrigation and Remote Sensing frontend files at an immutable SAHOOL commit, and separately execute browser keyboard/focus, responsive viewports and user-workflow regressions. No static-only result may authorize a SAHOOL release or redesign.
