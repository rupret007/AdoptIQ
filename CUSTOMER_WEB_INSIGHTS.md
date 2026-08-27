# AdoptIQ customer web insights

## Completed in this development round

- Reused the existing canonical report pipeline; no second report calculator or LLM
  path was added.
- Projected only the four frozen, cross-artifact insight keys already written to
  `Metric_Lineage`: support themes, support operating health, window momentum, and
  30-day predictive outlook.
- Preserved the exact fingerprinted claim text, label, caveat, source state/sheets,
  and evidence key/count. Unknown keys are ignored and duplicate supported keys fail
  closed instead of choosing one claim.
- Added those insights to the web Decision Workspace and Previous Reports detail view.
  The existing completed-job polling means the view updates when a report completes.
- Added a non-actionable customer-share readiness assessment. It does not send,
  publish, deploy, or export a webpage.
- Added focused projection, tamper, duplicate, receipt-binding, accessibility/source-
  shape, JavaScript parse, and offline fail-closed tests.

## Customer-share contract

The current offline result is intentionally:

```text
live_validation_performed=false
production_accuracy_claimed=false
release_ready=false
customer_shareable=false
```

A future exact report may become eligible only when every gate agrees: completed
customer/subscription scope; current canonical workbook; valid fact fingerprint;
persisted hash equal to current bytes; verified sheet digests and Evidence Links;
zero formulas; complete data-as-of and source states; unique allowlisted insights with
canonical evidence; no source warnings; and a content-bound live receipt containing
explicit live validation, manual source reconciliation, production-accuracy approval,
release approval, and owner customer-share approval. Missing, legacy, malformed,
string-ish, fixture, stale, partial, or mismatched evidence stays closed.

The readiness label is evidence status, not permission to contact a customer. There is
still no customer delivery action in this feature.

## Remaining work before customer use

The signed, redacted **offline fixture** receipt foundation is now implemented and
documented in `OFFLINE_VALIDATION_RECEIPTS.md`. It survives restart, binds exact
scope/fingerprint/current-artifact/code/fixture/build hashes plus the complete final
path-free public web projection, and stores no rows, paths, customer content,
free-text build labels, or error text. The official
guarded fixture's explicitly partial source states remain fixture-valid because they
are signed and projection-bound; they never become live-complete or customer-ready.
The receipt deliberately cannot satisfy any live or sharing gate.

1. Resolve the owner-held AdoptIQ drafts and establish the authorized work-machine
   trust-key/configuration process. Never commit the private signing key.
2. Run authorized work-machine live source reconciliation and manual customer-output
   review. Offline fixtures cannot satisfy this step.
3. Define a separate signed live/reviewer/owner receipt. The offline fixture schema
   cannot be mode-switched or interpreted as that approval.
4. Add a customer-scoped, server-rendered presentation only after access control,
   privacy, print, and route-level hash rechecks are approved. Team/member/portfolio
   reports must remain internal.
5. Keep Word, Source Data, and web claims on the same canonical fact contract; never
   summarize or regenerate a displayed insight in the browser.
6. Require a separate exact owner approval before any hosting, publishing, sending, or
   production deployment.

## Reused code and patterns

- AdoptIQ: `decision_report_delivery.py` canonical facts/lineage/evidence and
  `manager_decision_workspace.py` complete path-free public web snapshot.
- TACTrack pattern: one allowlisted web DTO plus content-bound readiness evidence.
- StoryOps-AI pattern: conspicuous sandbox/internal-preview language with no customer
  contact inferred from fixture success.
- CSS Conductor pattern: parse/render contract tests and fail-closed fixture behavior.
