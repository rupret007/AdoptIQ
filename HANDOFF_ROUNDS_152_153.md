# AdoptIQ — Consolidated Handoff: Rounds 152 & 153

Everything done across the recent Cowork work, in one place. Written so you can act on it from the work machine, hand it to the next session, or brief your manager.

---

## 0. Push / transfer (do this first)

The branch has two security commits ahead of `origin`. Push from your **Mac**:

```bash
cd ~/Documents/AdoptIQ
git push origin claude/round153-leader-decision-value
```

That publishes `55192ac` and `c8b2210` (the auto-update identity pin + swapper quoting). After it, `origin/claude/round153-leader-decision-value == c8b2210`.

On the **work machine**:

```bash
git fetch origin
git checkout claude/round153-leader-decision-value
./run_dev.sh          # sets up venv + deps, launches on :5151
```

`main` is untouched at `9907db1` everywhere. Nothing here has merged to `main`; that waits for live validation (Round 154).

---

## 1. Branch / commit map

| Ref | SHA | Contents |
|---|---|---|
| `main` (approved) | `9907db1` | untouched baseline |
| `origin/claude/round152-product-excellence` | `4f2830c` | Round 152 (pushed) |
| `claude/round153-leader-decision-value` (tip) | `c8b2210` | Round 152 + all of Round 153 + security |

Round 153 branch commits, oldest → newest:

- `fd1b92b` Tier 1 — decision table shows customer-specific risk drivers
- `a64f316` Tier 2 — Leader Team report shows the whole team
- `8dcd69c` Tier 3 — freshness fails closed
- `1a14d21` Tier 4 — decision report honours the customer alias registry
- `ec3c0f6`, `0fa16c8`, `2c8847b` — Round 153 docs / result / audit entry
- `085b947` — `run_dev.sh` + on-ramp transfer section
- `55192ac` — **security: pin macOS auto-update signing identity**
- `c8b2210` — **security: shell-quote paths in the update swapper**

---

## 2. What Round 152 delivered (already on GitHub, `4f2830c`)

23 substantiated defects fixed, 116 tests. Full detail in `ROUND152_PRODUCT_IMPROVEMENT_HANDOFF.md`. Headlines:

- **A1** — 14 data-bearing routes were bypassing the localhost + Host + no-store gate; moved behind it and replaced the allowlist pin with a default-deny completeness check over `app.url_map`.
- **A2** — attribute-context XSS in BST/PSIRT (`_safeHref` text-escaper in `href=`); real attribute escaper + percent-encoded `direct_link`.
- **A3** — whole customer rows logged at INFO; now shape-only.
- **A4** — 2 workers wrote raw exceptions into the admin console, 4 recorded no `error_kind`; shared `_record_worker_failure` scrubs + classifies for all five.
- **B1/B2** — evidence-row forgery via a customer name, and the intel citation whitelist derived by regex over prompt text; both closed.
- **B3/B4** — history-skewed retrieval; canonical corrections computed but never shown.
- **C1–C5** — report clarity (coverage-warning truncation disclosed, shared-attribution disclosed, empty-state grid, sentinel grammar).
- **E1–E9** — progress-page live regions, persistent error toasts, typeahead failure feedback, plain-language errors, debug button gated, skip link, `aria-current`, `th scope`, focus-ring contrast.

Verification: ruff 0, bandit 0, pip-audit clean, pytest 6,974 pass, Ask AI eval 14, four scopes 23/23, decision acceptance 4×2, r114 clean.

---

## 3. What Round 153 delivered (branch `c8b2210`)

### Leader decision value (the manager's original concern)

- **Tier 1** — the decision table's "Evidence-backed next action" was a band-level constant (same-band customers got identical rows). `risk_scoring.risk_factors` was already computed and discarded. Added a **"Top risk drivers"** column ("3 critical/high adoption barriers; 2 escalated TAC cases"), chrome stripped, built in the shared row builder so renderer + contract stay in lockstep.
- **Tier 2** — `TOP_ITEM_LIMIT=5` capped members, accounts and action plans together, so a Leader Team report showed only 5 of a manager's direct reports. New `MEMBER_ITEM_LIMIT=15` for members only; accounts/action plans keep 5.

### Accuracy

- **Tier 3** — the report published the evaluation/generation clock as verified source freshness. Defaults now fail closed: a missing retrieval clock renders "Data as of unavailable" instead of "Data as of \<now\>". **Intended offline side effect:** Leader/Renewal/Subscription show "unavailable" offline until Round 154 wires their real prefetch clock. Don't treat it as a defect.
- **Tier 4** — `_canonical_customer_identities` never consulted the Round 132 alias registry, so an org under name variants (the NYU case) was counted twice and both its risk halves understated. Now keys ID-less rows by alias group; NYU variants collapse to one identity, Acme/Beta/Gamma untouched. **Durable guard:** `validate_cross_artifact_contract` hard-blocks publication if any alias group splits into two identities. **Zero oracle churn** — the shipped fixture has no registered names, proven by a synthetic NYU test rather than a blind re-pin.

### Security (this session, commits `55192ac` + `c8b2210`)

- **Auto-update signing-identity pin.** `codesign --verify` proved validity, not *who signed*. Any Apple-signed bundle passed, and the update artifact comes from the OneDrive `releases_folder` with an adjacent sha256 — so a rogue-but-valid bundle could self-replace the app. Added `_verify_macos_signing_identity`: parses `codesign -dv` Team Identifier and enforces it against `ADOPTIQ_MACOS_TEAM_ID` / `Config` / bundled `expected_team_id.txt`. Configured → hard block on mismatch or unsigned; unconfigured → loud warning.
- **Swapper path quoting.** The detached self-replace script interpolated app-internal paths unquoted; now `shlex.quote`'d (defense in depth).

Verification: ruff 0, bandit 0; new tests `test_round153_leader_decision_value.py` (20) + `test_round153_update_identity_pin.py` (7); the full non-eval suite was confirmed **6,988 pass / 0 fail** before the security commits (which touch only `auto_updater.py`, covered by the 45 updater tests).

---

## 4. Open items — what needs you / the work machine

### Action item (turns the security fix from "warn" to "block")

**Write Cisco's Apple Team ID into the bundle.** The identity pin enforces only when a value is configured. Set it one of three ways:

- env: `export ADOPTIQ_MACOS_TEAM_ID=<TEAMID>`, or
- `Config.ADOPTIQ_MACOS_TEAM_ID = "<TEAMID>"`, or
- write `<TEAMID>` into `expected_team_id.txt` in the app bundle at build time (best — on by default in shipped builds).

Find your Team ID on an installed build with: `codesign -dv --verbose=4 /Applications/AdoptIQ.app 2>&1 | grep TeamIdentifier`.

### Reconfirm the full suite

Run `make verify` on the Mac (Python 3.11) after checkout — fast there. Expect the 6,988 floor + the new tests, 0 failures.

### Live validation (Round 154 — `ROUND154_HANDOFF.md`)

Three things only a VPN run can prove:

1. **Route-gating sweep** — the 14 routes Round 152 moved behind the Host gate: load every page + one report of each type, watch for 403s (a legitimate 403 is a regression to fix).
2. **Real freshness stamps** — wire the `snowflake_prefetch` clock into Leader/Renewal/Subscription so their subtitle returns to a real retrieval time; degrade a source to confirm it says so.
3. **Alias fix on real data** — pick a manager with a cross-variant org; confirm the customer count equals `canonical_metrics.count_customers`, one `Account_Summary` row, and a higher (un-split) risk score.

Plus a spot-check: the member table "Customers" column populates from live customer-id columns (was blank only on a hand-built synthetic frame).

---

## 5. Testing without the work network

You do **not** need to build a Snowflake simulator — `local_acceptance_runtime.install_runtime_adapters` already patches every `fetch_*_snowflake` function + the roster, so the real workers run end-to-end on fixtures. Local smoke test (no VPN):

```bash
make local-acceptance-http                     # 21/21 real routes on fixtures
make local-acceptance-app SCENARIO=healthy     # real DOCX/XLSX, best-state
make decision-report-acceptance MANAGER="Local Fixture Manager" \
     AS_OF=2026-08-03T12:00:00Z MODE=offline CUSTOMER_NAME="Acme Corporation"
```

Full detail in `WORK_MACHINE_TEST_ONRAMP.md`.

---

## 6. Recommended next moves (from the strategic review)

1. **Live validation** (you) — the single highest-leverage action; unblocks the merge to `main`.
2. **Trend / period-over-period** (next Fable round, offline) — the last big "amazing" lever; the code exists but is orphaned (`leader_report_components.py` is imported by nothing; `_add_period_delta_section` / `_add_aging_section` / `_add_customer_health_section` are dead). This is what makes the Leader report a *decision* document, not a status report.
3. **Worker refactor** (Fable, *after* live validation) — collapse the five copy-adapted 2–4k-line report workers into one shared skeleton. Root cause of the "fix lands on one path not its sibling" pattern; too risky before a known-good live baseline.

Do **not** merge to `main` or open a PR until Round 154's live matrix is green.

---

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
