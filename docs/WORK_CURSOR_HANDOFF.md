# Work Cursor handoff — AdoptIQ (live Cisco path)

**Audience:** Jeff’s work-instance Cursor (Work Mac / Cisco-capable checkout).  
**Author:** Bob the Bot (home / offline stack)  
**Written:** 2026-09-07 12:11 CT  
**Coord SoT:** [Bob-the-Bot #4](https://github.com/rupret007/Bob-the-Bot/issues/4) — chat is not state; post BEFORE/AFTER there.

Paste this whole file into Work Cursor as the session brief, or open it from the repo after `git pull`.

---

## 1. Why you are here

Home/Bob maximized **offline fixture simulation** so Work Cursor can pick up **live Cisco reconciliation, packaging, and promote** without rediscovering product intent.

- Home may deepen offline peer-guidance logic and open drafts.
- **Only Work Cursor + Jeff** touch live CSOne / Cisco surfaces, packaging, and promote.
- `ready_for_live_cisco` stays **false** on all home drafts until Work Cursor deliberately flips that contract after live validation.
- **sim ≠ live accuracy.** Never claim offline green equals live green.

---

## 2. Product north star (locked)

AdoptIQ’s differentiator is **knowledge/logic engineering on existing surfaces**, not a new customer report or page.

Deepen guidance already in the product so it actually uses:

1. Peer outcomes (CSOne + other available data in corpus)
2. **Next-step** and **likely-next** from peers who already lived that path
3. A hard **“not enough evidence”** when peer evidence is thin — never invent a fake future

Surfaces in play: **Ask AI**, **Customer 360**, **Historical Context**, Word/insight paths that already exist.

---

## 3. Exact git pins (do not guess)

| Ref | SHA |
|-----|-----|
| `main` (landed through Round 177) | `5abeea4d4e174da192e29622752691db03531476` |
| Latest home draft tip (#18 R182) | `30ffa9c69fcd38a3e4681874b956a2921d68b443` |
| Prior draft tip (#17 R181) | `d2ae6ff31e0b7baf0f98322ef237c3a4ede3be3a` |
| #16 R180 tip | `2aa15932bdfc44fec9522b1f6708c2a183c1773d` |
| #15 R179 tip | `03814d0e39275784dfcc437ec26eda6b31f89b7c` |
| #14 R178 tip | `2b3a6926a3c601199063fc012d0492cb91f12ef8` |

Repo: `https://github.com/rupret007/AdoptIQ`  
Default branch: `main`  
Local home checkout (Mini only — not Work Mac): `~/AdoptIQ`

**Work Mac first pulls:**
```bash
git fetch origin
git checkout main
git pull --ff-only origin main
# expect 5abeea4d4e174da192e29622752691db03531476
git rev-parse HEAD
```

Optional review of latest home leftover (do **not** merge until hosted + Karen):
```bash
git fetch origin pull/18/head:review-r182
git log --oneline main..review-r182
```

---

## 4. Open draft stack (home) — leave untouched unless Jeff says otherwise

All OPEN DRAFT. **None** are PRE_KAREN. Hosted checks are empty or billing-unexecuted. Do **not** squash from Work Cursor without Jeff + Karen.

| PR | Title | Tip | Notes |
|----|-------|-----|-------|
| [#18](https://github.com/rupret007/AdoptIQ/pull/18) | Round 182: Ask AI peer guidance uses the question's lived path | `30ffa9c6…` | Newest; prefer_theme on named Ask AI paths; fail closed `unlived_path` |
| [#17](https://github.com/rupret007/AdoptIQ/pull/17) | Round 181: comparable TAC-case severity | `d2ae6ff3…` | Case-basis severity bands; no schema bump |
| [#16](https://github.com/rupret007/AdoptIQ/pull/16) | Round 180: comparable-severity peer paths | `2aa15932…` | Barrier SEVERITY_C / schema v4 territory; Quality Checks FAILURE = billing empty-runner |
| [#15](https://github.com/rupret007/AdoptIQ/pull/15) | Round 179: this-account lived paths | `03814d0e…` | |
| [#14](https://github.com/rupret007/AdoptIQ/pull/14) | Round 178: turn peer paths into decisions | `2b3a6926…` | |
| [#3](https://github.com/rupret007/AdoptIQ/pull/3) | Fail closed on contradictory offline gate evidence | parked | **PARKED — do not revive** |
| [#2](https://github.com/rupret007/AdoptIQ/pull/2) | Round 169.5 offline Bob sim + Grok review | parked | **PARKED — do not revive** |

Merged on main already (context): #13 R177, #11 R175, #10–#4 earlier rounds.

**Stacking rule:** Do not rebase #18 onto #14–#17 (or vice versa) without an explicit Jeff plan. Each leftover was branched off the same main pin and left siblings untouched on purpose.

---

## 5. Round 182 contract (latest home leftover — read before live work)

Branch: `cursor/round182-question-path-peers-0fdb`  
Files touched: `ask_ai_corpus.py`, `report_corpus_context.py`, tests + README/CLAUDE/QUALITY_AUDIT.

### Behavior
- General/status questions (`detect_theme` → `general` or empty) keep Round 175 ranking.
- Named question theme (SSO/login → `authentication`, latency → `performance`) is passed as `prefer_theme`.
- Matching-theme ranking **only**. No fallback to a stronger unrelated theme.
- Named path with no matching barrier, or `evidence_sufficient=false`: hard stop — no next-step, no likely-next, no `CORPUS:PG-` receipt, card reason `unlived_path`, copy **Not enough evidence from peers who lived that path.**
- Customer 360 / Historical Context / insight suffixes stay unfiltered (no question → no `prefer_theme`).
- Method-only on a *matching* named path still publishes method-only.
- No schema bump (avoids colliding with #16’s v4).

### Local evidence claimed on tip `30ffa9c6`
- `pytest tests/test_round182_question_path_peer_guidance.py` — 10 passed
- R175/R176/R177 cluster — 86 passed
- Ask AI grounding + sanitization — 25 passed
- ruff / bandit clean on touched modules

### Hosted honesty
- `build.yml` is `workflow_dispatch` + `v*` tags only — PRs do not get a real quality job unless a separate PR workflow exists.
- `statusCheckRollup` on #18 is **[]** (empty).
- Sibling drafts hit: *“The job was not started because recent account payments have failed or your spending limit needs to be increased.”*
- **Empty / 2–9s billing red ≠ product FAIL.** Do not Karen empty rollups. Do not merge on empty.

---

## 6. GitHub Actions billing gate (blocks home ship)

Jeff must fix **GitHub → Settings → Billing & plans** (payment method / spending limit) before home can get real hosted greens on AdoptIQ (and TACTrack / Vault / StoryOps).

Until then, Work Cursor should:
1. Prefer **local certify** on Work Mac for any live path.
2. Treat home draft CI badges as non-authoritative.
3. Not spend or retry Actions to “fix” empty runners.

---

## 7. Work Cursor mission (what to do on the Work Mac)

### A. Immediate orientation (read-only)
1. Pull `main` @ `5abeea4d…` and skim README + CLAUDE.md + QUALITY_AUDIT.md.
2. Skim PR #18 body (and #17 if stacking live guidance).
3. Confirm no secrets / customer rows / raw CSOne landed in public draft diffs.

### B. Live Cisco path (Jeff-gated)
Only after Jeff explicitly opens live work:
1. Use Work Mac Cisco / CSOne credentials — **never** paste secrets into home Bob/Cloud/public PRs.
2. Reconcile offline peer-guidance behavior against live corpus / CSOne reality.
3. Document deltas: what offline got right, what sim lied about, what must change before `ready_for_live_cisco`.
4. Packaging / promote stays Jeff decision. Bob does not promote from home.

### C. If extending product offline from Work Cursor
1. Claim lease on Bob-the-Bot #4 (`agent: …`, 4h CT, claimed_scope).
2. Branch off **current** `origin/main` (or Jeff-named tip) — do not silently pile onto parked #2/#3.
3. Fixtures-first tests; fail closed on thin evidence.
4. Draft PR → exact-tip hosted SUCCESS (after billing fixed) → Karen leftover+security → only then squash.
5. AFTER comment on #4 with tip SHA + holds.

### D. Suggested Work Cursor paste prompt
```
You are Work Cursor on Jeff’s Cisco-capable AdoptIQ checkout.

Read docs/WORK_CURSOR_HANDOFF.md (this file) end-to-end first.

Repo: rupret007/AdoptIQ
main pin: 5abeea4d4e174da192e29622752691db03531476
Coord: https://github.com/rupret007/Bob-the-Bot/issues/4

North star: deepen existing Ask AI / Customer 360 / Historical Context peer guidance
(next-step + likely-next from peers who lived that path; hard not-enough-evidence).
Do NOT invent a new customer report/page.

Latest home leftover (OPEN DRAFT, not PRE_KAREN): PR #18 tip 30ffa9c69fcd38a3e4681874b956a2921d68b443
— named Ask AI paths must not publish unrelated stronger themes; unlived_path fail-closed.

Holds:
- Parked PR #2 / #3 — do not revive
- Do not merge #14–#18 until hosted SUCCESS + Karen PASS + Jeff yes
- No secrets, customer rows, raw CSOne in git
- ready_for_live_cisco stays false until Jeff flips after live validation
- Empty Actions / billing red ≠ product fail
- sim ≠ live

Your job now: [Jeff fills: live reconcile | package | promote | local certify | specific bug]
Post BEFORE/AFTER on Bob-the-Bot #4. Do not auto-merge.
```

---

## 8. Security / trust fences

- No peer names, emails, case IDs, or raw CSOne in published copy.
- Receipt prefix stays `CORPUS:PG-` where applicable.
- JS cards remain `textContent` only (R177).
- Public JSON must not claim live-Cisco readiness while offline.
- Karen = leftover + security ship-gate before anything is called good for merge.
- Bob home never auto-promotes; Jeff owns live / spend / promote.

---

## 9. Verify commands (offline / Work Mac local)

```bash
# Round 182 slice (on #18 tip worktree)
python3 -m pytest tests/test_round182_question_path_peer_guidance.py -q --tb=short

# Peer-guidance regression cluster
python3 -m pytest tests/test_round175_peer_guidance_knowledge.py \
  tests/test_round176_barrier_status_peer_join.py \
  tests/test_round177_peer_guidance_surfaces.py -q --tb=line

ruff check report_corpus_context.py ask_ai_corpus.py
bandit -c bandit.yaml -r report_corpus_context.py ask_ai_corpus.py -ll

# Prefer full verify when venv available
make verify   # if present / documented in README
```

Live certify steps are Work Mac–local and must not be inventively documented here with fake Cisco paths — follow the in-repo live runbook Jeff already uses on that machine.

---

## 10. Coordination lease snippet (copy into #4 when claiming)

```
BEFORE <timestamp CT>
- agent: work-cursor (Jeff Work Mac)
- claimed_scope: <one sentence>
- base: main 5abeea4d… (or exact tip)
- holds: parked #2/#3; no merge of #14–#18 without hosted+Karen+Jeff; no secrets in git
- lease_until: <now+4h CT>
```

```
AFTER <timestamp CT>
- delta: <what changed / PR / honest no-PR>
- tip_sha: <exact>
- next_action: <…>
- agent: none   # when done
```

---

## 11. What home will keep doing

- Offline fixture leftovers on FREE lease when Actions billing allows real CI.
- Will **not** merge #14–#18 on empty checks.
- Will **not** touch live Cisco from Mini/Cloud.
- Will update this handoff or #4 AFTER when main advances past `5abeea4d…`.

---

## 12. Quick links

- Repo: https://github.com/rupret007/AdoptIQ
- Coord: https://github.com/rupret007/Bob-the-Bot/issues/4
- Latest draft: https://github.com/rupret007/AdoptIQ/pull/18
- Main at handoff: https://github.com/rupret007/AdoptIQ/commit/5abeea4d4e174da192e29622752691db03531476
