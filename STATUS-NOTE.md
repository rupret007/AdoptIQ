# AdoptIQ #26 refresh against #25 — completed

## Result

Published a **replacement draft PR** with linear history on current #25 head. No force-push, no merge, no retarget of #25/#26.

| Item | Value |
|------|--------|
| **Replacement draft PR** | https://github.com/rupret007/AdoptIQ/pull/27 |
| **Original draft PR #26** | https://github.com/rupret007/AdoptIQ/pull/26 (unchanged; still `cursor/security-practice-slice2` @ `772a9c7`) |
| **Parent draft PR #25** | https://github.com/rupret007/AdoptIQ/pull/25 (`cursor/security-practice-ssot-slice1` @ `0be8b9c`) |
| **Pushed branch** | `draft/codex-adoptiq-26-onto-25-dee0e86a` @ `d55b9b3a8e07e6712e7ec9361db7a1566db1904f` |

## Ancestry

- **#25 head:** `0be8b9c01fd2f5e7b0b6f02db51ebee7f7d33715`
- **Published #26 head:** `772a9c7f9874a3cfe830f5b9aced433cd052d52e` (merge commit + duplicate CI commit + slice2 feature)
- **Common ancestor (#25 ∩ published #26):** `9b73d6ed40e5ec2d772a97a40dd476bf7d9f24fe`
- **Replacement branch:** single commit on #25: `d55b9b3` → parent `0be8b9c` (fast-forward from #25 once #25 is the base)

Rewriting published `cursor/security-practice-slice2` would require force-push; avoided per rules. New branch + PR #27 instead.

## Tree vs published #26

Only diff vs `772a9c7`: `tests/test_round169_metamorphic_truth.py` (+600s hang-bound from #25). Slice 2 feature content preserved in `d55b9b3`.

## Validation (local)

- `git diff --check`: pass
- Focused pytest: **95 passed**, 145.94s, Python 3.9.6  
  `test_practice_external_intelligence`, `test_practice_security_fail_closed`, `test_practice_settings_precedence`, `test_practice_ssot_collaboration_parity`, `test_round144_external_intel_background_refresh`, `test_round169_metamorphic_truth`
- Full `make verify`: not run (defer to PR #27 CI)

## Actions taken

1. Fetched `origin/cursor/security-practice-ssot-slice1` and `origin/cursor/security-practice-slice2`
2. Confirmed local replacement already prepared on assigned worktree branch
3. `git push -u origin HEAD:refs/heads/draft/codex-adoptiq-26-onto-25-dee0e86a` (new remote branch, no force)
4. Opened draft PR #27 base `cursor/security-practice-ssot-slice1`, head `draft/codex-adoptiq-26-onto-25-dee0e86a`

No merge, force-push, orchestrator/live conductor changes, or other workers.
