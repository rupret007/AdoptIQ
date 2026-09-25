# Peer-guidance combined candidate — 2026-09-12

Markers: `BOB_FREELANE_CLOSEOUT_CODEX_20260912`,
`JEFF_YES_ADOPTIQ_COMBINE_20260912`.

This is a source-only **OPEN DRAFT / PRE_KAREN** consolidation on main
`a2a19b05e525d4821e88bd9b34bac05b0f623702`. It is not merged, packaged,
installed, signed, or live-validated. The PR body and coordination AFTER pin
the resulting full head SHA; this file cannot contain its own commit hash.

## Preserved work

| Predecessor | Exact reviewed tip | Capability retained | Regression coverage |
| --- | --- | --- | --- |
| #14 | `2b3a6926a3c601199063fc012d0492cb91f12ef8` | Outcome-aware peer trials and stalled-path caution; receipt fingerprints outcome basis and counts | `test_round178_peer_path_decisions.py` |
| #15 | `03814d0e39275784dfcc437ec26eda6b31f89b7c` | This-account completed/open paths; current unfinished work ranking; no invented future | `test_round179_lived_peer_paths.py` |
| #16 | `2aa15932bdfc44fec9522b1f6708c2a183c1773d` | Barrier-severity evidence, schema v4 reparse, read-only PR quality workflow | `test_round180_comparable_severity_peer_paths.py`, `test_ci_quality_gates.py` |
| #17 | `d2ae6ff31e0b7baf0f98322ef237c3a4ede3be3a` | TAC-severity fallback; authored support-case hold copy survives the Word safety filter | `test_round181_comparable_case_severity.py` |
| #18 | `30ffa9c69fcd38a3e4681874b956a2921d68b443` | Strict named-question path; no stronger unrelated fallback | `test_round182_question_path_peer_guidance.py` |
| #22 | `96599eb1d45ad978b7158decb612342ee71dea6d` | Optional topical ranking preference and full Ask AI on-topic regression | `test_round175_peer_guidance_knowledge.py` |
| #20 | `272310d68c181e3b0bde3911165d46f8ef291237` | Runnable synthetic report/app demo and README entry; corrected scope, CDN and answer-validation disclosures | `DEMO.md`; Round 142/145 report and fixture contracts |

Predecessors may be closed only **as superseded by this combined draft** after
the replacement is pushed and tested. They are not superseded by main. Original
commits and PR discussions retain their historical evidence. Their old test
totals are not this candidate's validation.

Parked #2/#3 and work-Cursor/NO-GO #19/#21 remain separate and untouched. Do not
replay their integration/build changes into this candidate. Build 116 source
identity and native/live approval gates remain as documented in
`NEXT_MACHINE_PROMPT.md`.

## Conflict decisions and fixes

- Ask AI supplies both the strict `prefer_theme` filter and the optional
  `question_theme` preference. Strict scope wins. Soft-only callers retain
  their explicit unmatched-hint fallback. General questions rank unfinished
  work before completed paths.
- A completed this-account path yields `already_lived`; Ask AI publishes no
  recommendation or peer receipt. An already-open path says `do_not_repeat`.
  Ask AI summary metadata now agrees with its rendered card about that open
  path, even when peers closed theirs.
- Mixed/thin evidence may retain a method observation in the structured card;
  Ask AI's grounding block withholds its recommendation and peer SourceID.
  Older assertions expecting that method in the grounding block were reconciled
  with this stricter publication rule, without relaxing privacy or scope tests.
- Case fallback uses the intersection of barrier- and case-comparable peers.
  A peer excluded by the barrier gate cannot support or poison the TAC cohort.
  Barrier/pulse evidence still uses its own basis; a valid barrier majority does
  not depend on TAC severity agreement.
- Multiple known severities inside a peer or looked-up target remain mixed;
  they must not collapse into the all-unknown compatibility fallback.
- Positive severity fixtures now use a fresh account and genuinely comparable
  peers. Completed-account and mismatch refusals retain dedicated tests.
- Receipts include account path, both severity gates, and outcome counts.
  Existing alias exclusion, aggregate-only output, escaping, and
  `ready_for_live_cisco=false` remain enforced.

## Validation and repeatable checks

Use Python 3.11 in an isolated environment, install `requirements.txt` plus
`ruff`, `bandit`, and `pip-audit`, then run `make verify`. It includes lint,
Bandit HIGH/MED, dependency audit, the default pytest suite, and the deterministic
Ask AI evaluation suite. See the newest Round 183 entry in `QUALITY_AUDIT.md`
for actual results and any command/environment qualifications.

Round 184 / CI honesty: hosted Actions run
`https://github.com/rupret007/AdoptIQ/actions/runs/34729348282/job/103649153773`
failed in ~2s with no executable steps because private-repo Actions billing /
spending was blocked. Treat that as an infrastructure hold, not a quality-gate
verdict. Do not claim Quality Checks passed (or failed on code) until a billed
rerun executes real workflow steps and publishes normal logs.

The focused contracts are rounds 175–182 plus
`tests/test_peer_guidance_combined.py`. The seven additional cross-draft tests
cover both severity gates, mixed-within-peer evidence, strict versus soft
question scope, lived-path publication, and receipt identity.

The loopback browser check uses a fresh headless profile and synthetic data/model
adapters. Card checks exercise completed, still-open, fresh, mixed-severity, and
unmatched-path states at desktop and phone widths. Injected method text must
remain text, not HTML. This verifies rendering and fixture behavior only.

`DEMO.md` distinguishes the report generator from the app demo: the latter needs
public CDN UI assets or a cache. Its deterministic Ask AI adapter can return
HTTP 200 with a terminal `validation_failed` and Low confidence. That response
is not grounding certification and must not be presented as a fully validated
answer. No live keys should be added to make the fixture appear green.

## Review and work-machine gates

1. Review the exact combined PR head, especially corpus cohort selection,
   this-account publication, and schema v3→v4 reparse behavior. A schema refresh
   must rebuild from approved source files; do not mutate a live store here.
2. Require a real hosted run on that head for Karen PASS. An empty rollup or a
   job with no runner/steps is not a product failure or a pass. Preserve the
   exact billing annotation if Actions never starts. Do not enable spending,
   dispatch packaging, tag, or repeatedly rerun an unfunded workflow.
3. Keep this OPEN DRAFT until review and approvals. Do not auto-merge or enable
   any live integration. A fixture pass is not work-Mac build acceptance.
4. Work Cursor must fetch the approved source ref and preserve local settings
   and secrets. Any separate Snowflake credential refresh uses that machine's
   approved secret entry flow, never chat, committed files, command arguments,
   or logs. This combine does not rotate or request a token.
5. Native packaging, customer corpus reconciliation, Snowflake/CircuIT/CSConsole
   validation, installation, and promotion require their existing work-machine
   runbook and Jeff's gates. No such approval is implied by this draft.

Rollback is a source checkout change on an isolated branch, not a reset of the
work machine or deletion of settings/corpora. Preserve predecessor refs and the
last approved native artifact. No production rollback or schema mutation was
performed in this session.
