# Round 144 work-machine goal

## Recommended execution settings

- Model: `gpt-5.6-sol`
- Reasoning: `xhigh`

Use the strongest coding model available if that exact model is unavailable.
Keep reasoning at the highest practical level because this task combines source
reconciliation, document quality, AI-grounding review, packaging, deployment,
and rollback safety.

## Goal prompt

Work from the latest reviewed commit on
`origin/codex/round-144-work-machine-live-acceptance` in
`rupret007/AdoptIQ`. Pin and report the exact commit SHA before making changes.
The candidate must descend from Round 143 source commit
`515d6d5e5739f477dd7acb35472108d41c586a5d`. Use
`WORK_MACHINE_ROLLOUT.md` as the controlling runbook. Do not merge `main`,
force-push, overwrite uncommitted work, or deploy from a moving branch tip.

The purpose of this round is to turn the manager feedback into a materially
better, genuinely useful, and source-accurate release. The manager was
primarily referring to the Leader report, so treat Leader as the principal
usability target. Evaluate every report and every AI-assisted feature as part
of the same effort.

Preserve the currently deployed work-machine app and its data first. Create a
separate checkout, isolated environment, candidate runtime directory, output
directory, and loopback port. Record the current deployed version/build and a
tested rollback path. Never paste a GitHub token into a command, URL, file, log,
or prompt; use the approved credential helper or authenticated GitHub CLI.

Review and validate these product requirements against the actual candidate:

1. Leader must be detailed but not overwhelming. Its Word report should
   prioritize decisions, metrics, usable charts, account exceptions, risks,
   and tracked Action Plans. Repetitive activities and raw records belong in
   the paired Source Data workbook, with clear traceability from summaries to
   source rows.
2. Preserve the Action Plan tracking and data-source visibility the manager
   liked.
3. Fix missing, blank, clipped, or misleading chart data. A chart may not imply
   zero when a required source is unavailable, stale, partial, or failed.
4. Support useful Leader drill-down at team, individual member, and individual
   customer scope. The reader should be able to move from an executive result
   to the underlying account and source records.
5. Apply the same hierarchy and source-record separation to Comprehensive
   where it improves that report, while preserving Comprehensive's broader
   analytical purpose. Do not make every report identical.
6. Evaluate Comprehensive, Compact, Renewal portfolio, Renewal
   single-customer, subscription analysis, and every supported option family.
   For each product, measure page count, visible word count, repeated/raw-record
   content, chart quality, action/decision usefulness, accessibility, and
   Source Data traceability.
7. Evaluate the full AI surface: Ask AI sync and streaming, multi-turn context,
   suggestions, citations and evidence drill-through, support-case search, Ask
   Intel, External Intelligence, Customer 360, Playbook, encrypted corpus
   status/retrieval, model preferences and provider connectivity, report
   narratives/recommendations, diagnostics, BE-priority classification where
   enabled, and degraded/failure behavior.

Begin with the complete local quality gate, deterministic two-pass offline
four-scope report acceptance, and the 75-question portable Ask AI replay. Start
the candidate separately and run the two-pass live report and AI acceptance
harnesses from the runbook. Exercise the complete live report option matrix.
Render and inspect every representative Word page and all Source Data sheets.
Reconcile every material number, customer, ID, date, owner, status, source
state, risk statement, chart, narrative, and AI answer to the live Snowflake,
corpus, intelligence, or Source Data record that supports it.

The preparation branch includes a two-pass AI feature acceptance runner and a
fix that makes stale/cold External Intelligence render stored data immediately
while a single background refresh runs. Verify both from source and from the
packaged candidate. Do not silently revert these protections. If you find a
real defect, reproduce it, implement the smallest coherent fix, add regression
coverage, and rerun every affected gate.

Fail closed. Missing data is not zero; a successful HTTP response is not proof
of usable corpus content; a provider ping is not AI feature acceptance; fixture
consistency is not live accuracy; and plausible prose is not evidence. Reject
fabricated IDs, unsupported numbers, scope leakage, hidden truncation, silent
legacy/ungrounded fallback, raw provider errors, prompt-injection obedience,
and conclusions drawn from unavailable sources. Keep customer-level reports,
screenshots, evidence, logs, credentials, and acceptance artifacts outside
Git in the approved work-machine location.

Build and smoke a signed, production-configured candidate only after the live
gates pass. Do not replace the deployed app until every report/AI requirement,
visual and accessibility review, exact source reconciliation, packaged-app
smoke, backup, and rollback check is green. After deployment, repeat critical
smoke and reconciliation checks against the installed app.

Return a concise, redacted handoff containing:

- exact starting and ending SHAs, branch, version/build, and deployed path;
- a requirement-by-requirement disposition of the manager feedback;
- an acceptance row for every report product/scope and every AI feature;
- two-pass report and AI repeatability results;
- source-reconciliation, visual, accessibility, security, dependency, build,
  packaged-smoke, deployment, and rollback evidence;
- every limitation or not-applicable case with a precise reason;
- the exact files changed and tests added.

Do not claim production accuracy, release readiness, or deployment success
until the live evidence proves it. Do not commit or push customer data or
sensitive evidence. Stop and report the exact blocking gate if any required
source, permission, live reconciliation, packaging, deployment, or rollback
proof cannot be completed.

## Completion criteria

The goal is complete only when all supported reports and AI features have been
evaluated, every material claim has been reconciled to live evidence, the
manager's Leader-report concerns have a verified disposition, the packaged and
installed candidates pass, rollback is proven, and the reviewed code-only
changes plus redacted handoff are pushed without secrets or customer data.
