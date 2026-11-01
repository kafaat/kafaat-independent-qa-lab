# DO NOT MERGE — disposable new-policy acceptance PR

Purpose: observe GitHub's merge eligibility under live Ruleset #26292547 after
the owner set required approvals to 0 (2026-11-01 20:00:25 +03:00).

1. This commit is harmless: all required checks should pass and the PR should
   be mergeable with **zero** human reviews.
2. A second commit adds a deliberately failing test: the required `test`
   check should fail and merging should be **blocked**.

The PR is closed without merging and its branch deleted afterwards.
