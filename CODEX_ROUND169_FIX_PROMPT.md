# Codex Round 169 fix prompt

## Your mission

Fetch branch **`cursor/round169-sanitized-report-audit`** from **`rupret007/AdoptIQ`**. Read **`ROUND169_REPORT_AUDIT.md`** on that branch. Implement ranked fixes with regression tests on base **`4350b39`** (`codex/round168-evidence-led-improvements`). Do **not** claim production accuracy until live VPN acceptance passes.

## Base and scope

- External product base: **`4350b39`**
- Audited build: **116** (fixture simulation, Aug-14 session)
- **`live_validation_performed=false`** for the audited corpus — use synthetic/local fixtures only for repro

## Priority fixes (in order)

### P1 — R169-001: Missing Word charts (20/24 pairs)

**Symptom:** Technology-filtered Comprehensive (and other families in matrix) show **`Chart_Data` rows** in Source Data but **zero embedded DOCX charts**; only Technology=`All` Comprehensive pairs embed four charts.

**Suspect paths:** `decision_report_delivery.py` chart render/embedding; Round 168 CSOne activity-mix gating in `csone_corpus_replay.py` / `adoptiq_backend.py`.

**Acceptance:** Every decision-report DOCX that ships non-empty `Chart_Data` embeds four charts **or** carries an explicit coverage paragraph per chart ID. Add pytest pinning filtered-scope chart embedding.

### P2 — R169-002 / R169-003: CSOne malformed headers

**Symptom:** Customer A/B session CSOne uploads parse with **`Unnamed` columns** (misaligned header rows).

**Fix:** Robust header-row detection in CSOne load path; regression test with sanitized fixture mimicking export shape. Document operator export steps if code cannot repair.

### P3 — R169-004: Debug-folder drift

**Symptom:** Equivalent Report_Info scopes differ in sheet row counts across `debug-b-comp-all` vs `debug-matrix2` folders.

**Fix:** Ensure acceptance writes to a single canonical output root; add test that two runs with identical inputs produce identical sheet row counts (offline).

### P4 — R169-005: Live customer audit (operator gate)

No Customer A/B AdoptIQ outputs existed on disk. After fixes, operator must regenerate customer-scoped reports on VPN before claiming live accuracy.

## Verification loop (required)

```bash
make verify
make production-simulation
```

Run offline acceptance (`scripts/generate_offline_acceptance_artifacts.py` or `run_decision_report_acceptance.py --mode offline`). Visually inspect DOCX chart embedding for Technology=`All Contact Center` Comprehensive pair.

## Constraints

- Preserve public APIs unless required
- Never weaken tests to go green
- Keep PII out of commits — use aliases (Manager A, Customer A/B)
- Do not merge to `main` without Cisco review

## References

- Audit doc: `ROUND169_REPORT_AUDIT.md` (same branch)
- SSoT modules: `canonical_metrics`, `decision_report_delivery`, `report_completeness_audit`
- Round 142 17-sheet contract: `decision_report_delivery.SOURCE_DATA_SHEET_NAMES`

**Trailer:** Made-with: Cursor
