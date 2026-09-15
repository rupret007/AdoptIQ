# Historical only — Build 115 must not be run

Build 115 was invalidated by Round 168 runtime changes. Its immutable identity is
preserved in `release_candidates/macos-build115/`, but it is **NO-GO** for install,
acceptance, promotion, publication, or deployment.

Use `WORK_MACHINE_BUILD117_PROMPT.md`. Build 116 was superseded while source-only;
Build 117 remains source-only until an exact clean source commit is packaged and new
candidate evidence is created. Never recreate or
overwrite Build 115 evidence.
