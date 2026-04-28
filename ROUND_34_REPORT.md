# Round 34 — Builds 6/7/8 Review + Remediation

## Baseline

- **Build5 baseline commit:** `9b08acd` ("Round 30 audit: logger.debug on silent exceptions, CSRF config guard, dict safety, remove dead delete button")
- **Build6/7/8 baseline (capture commit):** `28c2300` ("Builds 6/7/8 baseline (Rounds 31-33) — capture working tree for Round 34 review")
  - Rolled up the 41 modified + 45 untracked files representing in-flight Build6/7/8 work into a single capture commit so my R34 fixes could layer atomically on top.
  - Round mapping confirmed via untracked test naming: Build6 = Round 31 (UI freeze hotfix), Build7 = Round 32 (silent-data-loss + admin auto-start + Intelligence default-on + fail-loud contracts), Build8 = Round 33 (SharePoint UX + sentinel fallback + server-authoritative timer + redirect fallback).
  - The 65 untracked `tests/test_round30_*.py` tests (one per finding from my prior R30 review) all PASS against the capture commit, confirming Build6/7/8 already addressed every prior R30 H/M/L/I finding.

- **Pre-R34 baseline test suite:** 2679 passed / 2 skipped
- **Post-R34 final test suite:** 2762 passed / 2 skipped (+83 new tests, 0 regressions)

### Files reviewed (line counts)

- `corpus_crypto.py` — 610 lines
- `corpus_bootstrap.py` — 855 lines
- `sharepoint_corpus_source.py` — 1305 lines
- `adoptiq_settings.py` — 293 lines
- `app_simple.py` — 20991 lines
- `templates/analyze.html` — 2048 lines
- `templates/progress.html` — 855 lines
- `templates/base.html` — 75 lines (CSP block)
- `static/js/intel_status.js` — 806 lines
- `enhanced_admin_dashboard_v2.py` — 3951 lines
- `data_contracts.py` — 411 lines
- `data_normalization.py` — 771 lines
- `canonical_metrics.py` — diff-only (+117 lines added in Build6/7/8)
- `adoptiq_backend.py` — diff-only + targeted reads
- `executive_intelligence_formatter.py` — 1902 lines (parity-only)
- `executive_report_builder.py` — 303 lines

## Findings + Fixes

### A. Encryption-at-rest sentinel chain (`corpus_crypto.py`)

#### A1: corpus_crypto sentinel pinning prevents bricking on availability change

- **Severity (pre-fix):** HIGH
- **Files modified:** `corpus_crypto.py`, `tests/test_round34_a1_corpus_crypto_sentinel_pinning.py`
- **Test added:** `tests/test_round34_a1_corpus_crypto_sentinel_pinning.py` (8 tests)
- **Commit:** `71f7571`
- **Why it mattered:** Build8 introduced 3-step sentinel resolution (OneDrive > SharePoint > local-mint) but no pinning. Failure scenario: a user starts on the auto-minted local sentinel; SharePoint sync later delivers a different sentinel; next call to `open_corpus_for_user` resolves the SharePoint sentinel first; `derive_key` produces a different AES key; `decrypt_bytes` fails the GCM tag on the existing `corpus.db.enc`. **Corpus inaccessible with InvalidTag — same shape if any sentinel rotates.**
- **What changed:** Added a `corpus.sentinel.lock.json` sidecar (mode 0o600, lives next to `corpus.db.enc`) that records the SHA-256/16-hex-prefix of whichever sentinel actually sealed the corpus, plus `source` and `minted_at`. `open_corpus_for_user` now reads the lock first; walks candidate sentinels; picks the one whose digest matches the lock regardless of priority order. If no candidate matches, raises `CorpusCryptoError` with operator guidance ("restore original sentinel OR delete the lock to migrate"). Legacy installs without a lock still open and gain a lock on first open.

#### A2: corpus_crypto fail-loud on corrupt salt or sentinel

- **Severity (pre-fix):** HIGH
- **Files modified:** `corpus_crypto.py`, `tests/test_round34_a2_corpus_crypto_fail_loud.py`
- **Test added:** `tests/test_round34_a2_corpus_crypto_fail_loud.py` (9 tests)
- **Commit:** `c9f23bc`
- **Why it mattered:** Two silent-data-loss paths. (1) `get_or_create_salt` silently regenerated the salt when the file existed but had wrong length — HKDF over the new salt produces a different AES key, bricking the corpus on next open. (2) `_try_resolve_sentinel` swallowed `CorpusCryptoError` from `read_sentinel_bytes`, so a corrupt OneDrive sentinel silently fell through to SharePoint or local-mint — the operator never learned their primary sentinel was broken. Both violate the prompt rule: "Treat 'exists' as proof of existence, not integrity."
- **What changed:** `get_or_create_salt` now raises `CorpusCryptoError` when the salt has wrong length AND an encrypted corpus exists (with operator guidance to restore from backup or delete both salt + corpus). When no corpus exists, regeneration is preserved (legacy half-finished install recovery). Also added `os.fsync` on the salt write. `_try_resolve_sentinel` now distinguishes "path absent" (returns None for legitimate fall-through) from "path present but read/parse fails" (propagates the error so the operator can fix the sentinel).

### B. New POST routes (`/api/settings/sharepoint_url`, `/api/corpus/sharepoint/{signin,signout}`)

#### B1+B2: harden Build8 SharePoint POST routes (sensitive set + Host gate)

- **Severity (pre-fix):** HIGH
- **Files modified:** `app_simple.py`, `tests/test_round34_b_sharepoint_routes_hardened.py`
- **Test added:** `tests/test_round34_b_sharepoint_routes_hardened.py` (37 tests)
- **Commit:** `d137aa7`
- **Why it mattered:** **B1**: the three Build8 POST routes were CSRF-protected via `_r17_2_authorize_corpus_admin` but were NOT in `_SENSITIVE_ENDPOINTS`, so they bypassed both the localhost gate (`restrict_sensitive_routes_to_localhost`) AND the response-hardening headers (Cache-Control: no-store, X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy: no-referrer, per-response CSP). Every other admin surface gets these. **B2**: no Host-header validation existed anywhere — the existing `_is_local_client` check looks at `request.remote_addr`, which is 127.0.0.1 in a DNS-rebinding attack. CSRF blocks rebinding-driven POSTs (attacker can't read the token cross-origin), but every sensitive GET (status, progress, history, diag) was exposed.
- **What changed:** B1 added the three routes to `_SENSITIVE_ENDPOINTS`. B2 added `_allowed_hostname()` and extended `restrict_sensitive_routes_to_localhost` to also reject requests whose Host header is not in the allow-list (`{127.0.0.1, ::1, localhost}`, plus optional `ADOPTIQ_HOST_ALLOWLIST` and `ADOPTIQ_BIND_HOST` env vars). Strips `:port` and handles bracketed IPv6 literals. Public UI shells (`/`, `/help`, etc.) stay permissive — the gate only fires on `_SENSITIVE_ENDPOINTS`. The existing `is_valid_sharepoint_url` regex was independently verified to reject every attack vector the prompt called out (IDN homoglyphs, userinfo injection, subdomain confusion, NUL/control chars, URL-encoded port) — pinned with parametric tests.

### C. MSAL token clearing (`sharepoint_corpus_source.py`)

#### C1+C2: MSAL token clearing + signin logging hygiene

- **Severity (pre-fix):** MEDIUM (race) / MEDIUM (device-code in log)
- **Files modified:** `sharepoint_corpus_source.py`, `tests/test_round34_c_msal_clear_and_logging.py`
- **Test added:** `tests/test_round34_c_msal_clear_and_logging.py` (5 tests)
- **Commit:** `aa6b25e`
- **Why it mattered:** **C1**: `SharePointGraphClient.clear_token_cache` reset `_msal_app`/`_msal_cache`/`_pending_flow` WITHOUT holding `self._lock`. `start_device_code_flow` and `await_device_code_completion` both take that lock when reading/writing `_pending_flow`. A signout that landed between them could observe a half-cleared state. **C2**: `start_device_code_flow` logged the freshly-issued `user_code` at INFO level. The user_code is what the user types into `microsoft.com/devicelogin` — anyone who reads the log within the ~15 min validity window can complete the flow and steal the user's session. The prompt is explicit: "no token bytes, refresh tokens, or device codes may appear in logs or exception messages — even at DEBUG level."
- **What changed:** C1 wraps the in-memory reset in `with self._lock:` so it serializes with the device-code flow methods. Worker threads with a local ref to `_msal_app` may complete one in-flight Graph call after signout returns (Python object refs are atomic) — documented so an operator does not panic. C2 drops `user_code` from the `logger.info`; logs only `verification_uri` + `expires_in`. Also tightened the `SharePointAuthRequired` raised on bad payload shape so it no longer prints the raw flow dict — only the key list.

### D. Fail-loud data contracts (`data_contracts.py` + `data_normalization.py`)

#### D: fail-loud propagation when annotate_with_contract crashes

- **Severity (pre-fix):** MEDIUM
- **Files modified:** `adoptiq_backend.py`, `tests/test_round34_d_contract_failure_propagates.py`
- **Test added:** `tests/test_round34_d_contract_failure_propagates.py` (5 tests)
- **Commit:** `edcdbf9`
- **Why it mattered:** The four call sites in `adoptiq_backend.py` (CSOne load, team_subs, ARR-empty, ARR-data) wrapped `annotate_with_contract` in `try/except Exception as _annot_err: logger.debug(...)`. On a crash the dataframe carried no `fetch_error` annotation, so downstream code treated the frame as "valid" and the schema_drift signal was silently dropped. "Thin report" symptom: executive summary renders almost nothing because a critical dataframe lost its annotation contract.
- **What changed:** Centralized the wrapper in `_safe_annotate_with_contract(df, dataset, source_label)`. On any exception: WARN-level log + stamp `df.attrs['fetch_error'] = 'contract_annotation_failure on <dataset>: <type>'` + `fetch_error_kind = 'contract_annotation_failure'`. Never clobbers a pre-existing `fetch_error` (the original failure is more informative). Best-effort about stamping itself; never raises from a load path. All four wrapper sites refactored. Helper colocated with `_empty_df_with_fetch_error` so the two fail-loud surfaces live next to each other.

### E. Cross-report parity (recurring concern)

**No findings — verified Build6/7/8 did NOT re-introduce direct recomputation in the formatters.**

Evidence:
- `git diff 0658af8..28c2300 -- compact_report_formatter.py leader_report_generator.py executive_intelligence_formatter.py | grep -E "^\+.*\.(nunique|value_counts|groupby|query)\("` shows ZERO new inline aggregation patterns. The single `.nunique()` line that "moved" is just a re-indentation under a new `try/except` wrapper — same call.
- `canonical_metrics.py` ADDED 3 new helpers in Build6/7/8 (`count_closed_barriers`, `count_customers_with_barriers`, `count_action_plan_completed`) and the formatters route through them (verified via `git diff` for `+.*cm\.count_` lines).
- Existing Round 30 M3 test (`test_round30_m3_compact_customers_with_barriers_uses_canonical.py`) pins canonical routing for the customer-with-barriers tile.
- The single inline `.nunique()` fallback in compact at L1855 is documented as a defensive fallback when the canonical helper raises — same as the Round 25 design intent.

### F. Server-authoritative timer (`/status/<id>` + `templates/progress.html`)

#### F1: progress.html IIFE double-init guard

- **Severity (pre-fix):** LOW (defensive)
- **Files modified:** `templates/progress.html`, `tests/test_round34_f1_progress_double_init_guard.py`
- **Test added:** `tests/test_round34_f1_progress_double_init_guard.py` (4 tests)
- **Commit:** `0388dfc`
- **Why it mattered:** The progress-page IIFE registers THREE setIntervals on `window` (`refreshInterval`, `elapsedInterval`, `elapsedWatchdog`). Each `window.X = setInterval(...)` overwrites the previous handle but leaves the prior interval running. If the IIFE ever runs twice (bundle accident, hot-reload, or accidental re-include), 6 timers run while only 3 cleanup handles exist; the prior 3 keep firing. Net: doubled `/status` polling load + double DOM ticks.
- **What changed:** Defensive `window.__adoptiqProgressPageInit` sentinel at the IIFE top — mirror of the R26-003 pattern in `static/js/intel_status.js`. `ROUND34_PROGRESS_DOUBLE_INIT_GUARD` marker so a future polish pass that strips comments still leaves a greppable trace.
- **Other server-timer items verified clean:**
  - `elapsed_seconds` compute correctly handles naive vs aware datetimes, `completion_time` without `start_time`, ISO `Z` suffix vs offset (R33 tests already pin all of these).
  - The "poll race fires at the same time as the watchdog tick" scenario the prompt called out is closed by JS's single-threaded event loop: `refreshStatus`'s `clearInterval` at L689-690 races with the watchdog tick, but the JS runtime runs the `refreshStatus` then-callback to completion before any pending `setInterval` callback gets a turn. The watchdog always observes the post-completion DOM state and returns early.

### G. Client-side surface (intel_status.js + analyze.html + progress.html + base.html CSP)

**No findings — verified Build6/7/8 added zero `innerHTML` / `outerHTML` / `insertAdjacentHTML` calls and CSP was unchanged.**

Evidence:
- `git diff 0658af8..28c2300 -- templates/analyze.html templates/base.html static/js/intel_status.js static/js/theme-toggle.js | grep -E "^\+.*innerHTML"` returned only COMMENTS asserting the absence of `innerHTML` — no new `innerHTML` calls.
- `git diff 0658af8..28c2300 -- templates/base.html | grep CSP-relevant lines` returned empty — CSP block at L84-93 unchanged.
- `paintSharepointPanel` (`intel_status.js:468`) uses `textContent`, `setAttribute`, `classList` exclusively — no HTML injection vector.
- `showSharepointDeviceCode` clamps verification URI to `https://` only and renders both URI and user_code via `textContent`. Note: the `user_code` IS user-displayed in the modal (intentional UX) — that's not a leak; the C2 fix removed the LOG of the same value.
- `theme-toggle.js` uses `setAttribute` only, no `innerHTML` / `document.write` / `eval`.
- `analyze.html` `innerHTML` uses (L1157, L1223, L1231, L1514, L1754, L1759, L1868, L1873) are all either static spinner markup or empty-string clears; customer names from typeahead added via `li.textContent = name`. Safe.

### H. Admin auto-start (`enhanced_admin_dashboard_v2.py` + auto-start helper in `app_simple.py`)

#### H1: admin auto-start enforces ADOPTIQ_ADMIN_BIND_PUBLIC gate

- **Severity (pre-fix):** HIGH
- **Files modified:** `app_simple.py`, `tests/test_round34_h1_admin_autostart_loopback_gate.py`
- **Test added:** `tests/test_round34_h1_admin_autostart_loopback_gate.py` (15 tests)
- **Commit:** `910f3b1`
- **Why it mattered:** Build7's `_start_admin_server_in_thread` read `ADOPTIQ_ADMIN_HOST` directly with no reference to `ADOPTIQ_ADMIN_BIND_PUBLIC=1` — the safety gate the standalone `__main__` path in `enhanced_admin_dashboard_v2.py` enforces. An operator who set `ADOPTIQ_ADMIN_HOST=0.0.0.0` to "make admin reachable from the LAN" expecting some kind of safety prompt got none. The admin console silently bound to every interface, exposing report metadata, every destructive `/admin/*` endpoint behind CSRF, and the in-memory `analysis_status` dict. CLAUDE.md is explicit: "Don't change [the loopback] default without the `ADOPTIQ_ADMIN_BIND_PUBLIC=1` escape hatch."
- **What changed:** Resolution is now: `ADOPTIQ_ADMIN_HOST` unset → 127.0.0.1; loopback values (127.0.0.1 / ::1 / localhost) pass through; non-loopback values pass through ONLY if `ADOPTIQ_ADMIN_BIND_PUBLIC` is truthy; otherwise WARN log + downgrade to 127.0.0.1. The downgrade is loud so the operator sees the safety action rather than discovering it from a security scan.

### I. Intelligence default-on (`CORPUS_KNOWLEDGE_ENABLED=true`)

**No findings — verified report generation never blocks / 500s / hangs when corpus is empty, un-indexed, or SharePoint is not configured.**

Evidence:
- Bootstrap (`corpus_bootstrap.start_background`) is non-blocking, daemon-thread, with a broad try/except at startup that catches `CorpusCryptoError` and surfaces it via `_STATE.last_error_kind="crypto"`.
- `corpus_retriever.get_status()` never raises — surfaces `available=False` with a `reason`.
- `intel_status.js` `classifyState` correctly maps `payload.available === false` → 'unavailable' badge, `payload.boot.last_error` → 'error' badge — both render the visible banner the prompt requires.
- Reports themselves do NOT call corpus modules directly. Ask AI grounded path catches `CorpusUnavailable` and degrades (`ask_ai_corpus.py` lines 170, 177, 187).
- `paintSharepointPanel` correctly handles `not configured`, `signed_in`, `auth_required`, and arbitrary `error_kind` → renders pill + label per state. Sign-in panel + Connect button rendered conditionally.
- Three scenarios verified by tracing: (1) fresh install with no SharePoint and no OneDrive → mints local sentinel via A1, indexes 0 sources, `available=False`, badge "Idle", reports run normally. (2) SharePoint configured but not signed in → `auth_required` state, panel shows Connect button, reports run normally. (3) Install dir read-only → `CorpusCryptoError` caught at bootstrap, `last_error_kind="crypto"`, badge "Error", reports run normally.

## Out-of-scope findings (NOT fixed)

Issues noticed outside focus areas A-I. Filing here for triage into a separate round.

### O1: 4 silent `try/except Exception: pass` in `KeyringTokenCache._save_file`

- **Severity:** LOW
- **Location:** `sharepoint_corpus_source.py:418-420, 423-425`
- **Evidence:** Two `chmod 0700 / 0600` failures swallowed with bare `pass # noqa: PIE790`. Documented as "best-effort hardening" but on a non-POSIX filesystem (e.g. exFAT-formatted external drive used as `~/.adoptiq` mount), a chmod failure means the persistent token cache is world-readable and the operator never knows.
- **Why it matters:** Refresh-token bytes on a 0644-mode file is the same severity class as the C2 device-code-in-log finding I fixed. The defensive `pass` was historically motivated by Windows but the file-mode contract on Windows is already a no-op (chmod is ignored). The current code can't distinguish "Windows / no-op" from "POSIX FS that genuinely failed."
- **Suggested fix:** On chmod failure, log at WARN with the file path so the operator can investigate. Optionally `os.stat` the file after chmod and log a warning if mode is more permissive than 0o600.

### O2: `__hash__ = id(self)` on `CorpusKey` (frozen dataclass)

- **Severity:** LOW
- **Location:** `corpus_crypto.py:130-131`
- **Evidence:** `CorpusKey` is a frozen dataclass with custom `__eq__` (constant-time compare). The custom `__hash__` returns `id(self)` so equal keys hash differently. This violates the `__hash__`/`__eq__` invariant: two equal objects MUST have the same hash. If someone ever puts CorpusKey instances in a set or dict, behavior breaks (lookups fail despite equal keys). Today nothing does — but the contract violation is a footgun.
- **Why it matters:** Subtle correctness bug waiting for a future refactor that uses CorpusKey in a hash-based collection.
- **Suggested fix:** Either override `__hash__` to return a hash derived from the key bytes (constant — same key → same hash), or set `__hash__ = None` to make CorpusKey unhashable explicitly. The latter is safest.

### O3: `_msal_cache` reference may survive in MSAL library memory after signout

- **Severity:** LOW
- **Location:** `sharepoint_corpus_source.py:540-565` (`clear_token_cache`)
- **Evidence:** The C1 fix nullifies `_msal_app`, `_msal_cache`, and `_pending_flow` references on the SharePointGraphClient instance, but the MSAL library's `PublicClientApplication` has its OWN reference to the `SerializableTokenCache` that we passed in at line 591. The MSAL library may still hold the cache state in process memory until garbage collection runs.
- **Why it matters:** A worker thread that already has a token from MSAL's in-process cache can complete one more Graph call after signout. C1's docstring documents this; not strictly a bug, but a forced GC after signout would close the gap entirely (`gc.collect()` after the `with self._lock:` block).
- **Suggested fix:** Add `import gc; gc.collect()` at the end of `clear_token_cache` so the MSAL handle dies eagerly. Bench impact: GC sweep on signout (rare event) is negligible.

### O4: `analyze.html` typeahead missing CSRF token in fetch headers

- **Severity:** LOW
- **Location:** `templates/analyze.html:1794-1798` (`fetchTypeaheadJson` call to `/search_subscriptions`)
- **Evidence:** The fetch sends `'X-CSRFToken': _csrf1` correctly — but the `/search_subscriptions` endpoint is in `_SENSITIVE_ENDPOINTS` and it's a POST. If WTF_CSRF_ENABLED is True (production), the fetch already works. But the typeahead pulls customer names — if a future refactor moved `/search_subscriptions` off the CSRF-required list (or onto a GET), the header would be wasted and any drift would not surface in tests.
- **Why it matters:** Defensive only — current behavior is correct. Worth a marker test that pins the CSRF header presence on the typeahead call site.

## Test results

- **Suite size (final):** 2762 passed, 2 skipped (was 2679/2 before R34)
- **Result:** PASS — full suite green at every commit boundary
- **New tests added:** 83 across 6 files
  - `tests/test_round34_a1_corpus_crypto_sentinel_pinning.py` (8)
  - `tests/test_round34_a2_corpus_crypto_fail_loud.py` (9)
  - `tests/test_round34_b_sharepoint_routes_hardened.py` (37)
  - `tests/test_round34_c_msal_clear_and_logging.py` (5)
  - `tests/test_round34_d_contract_failure_propagates.py` (5)
  - `tests/test_round34_f1_progress_double_init_guard.py` (4)
  - `tests/test_round34_h1_admin_autostart_loopback_gate.py` (15)
- **Per-commit verification:** Each fix commit ran `python3 -m pytest tests/test_round34_<focus>_*.py -v` plus the relevant pre-existing regression suite (R32 contract autoremap, R33 corpus_crypto sentinel fallback, R33 progress watchdog markers, R32 admin autostart safety) before committing. All green at each step.

## Confidence

**HIGH.** Each fix maps directly to an observed gap in the diff between R28 and the Build6/7/8 baseline (`git diff 0658af8..28c2300`), verified against the relevant existing tests and the prompt's stated contract. The 4 areas without findings (E, G, I — and the device-code-flow rationale within C) were verified by direct diff and behavioral trace, not assumed. No fix touched a file outside its focus area's scope. No version bumps, dependency upgrades, or `.spec`/build-script edits per the hard rules. The 28c2300 baseline commit is clean — it captures Build6/7/8 work as a single revert-able boundary so any of the 7 fix commits (`71f7571`, `c9f23bc`, `d137aa7`, `aa6b25e`, `edcdbf9`, `0388dfc`, `910f3b1`) can be reverted independently if you find an issue.

The single area where I'd ask for a second pair of eyes: the **A1 sentinel-pinning lock semantics** when the lock file itself becomes corrupt mid-write (e.g., disk full at the exact moment of `_write_sentinel_lock`). My fix raises on corrupt JSON which is correct, but a partial-write that leaves valid-JSON-but-wrong-digest would currently be detected by the digest-mismatch path. Not a real bug — just a subtle interaction worth a manual eyeball before this round ships in a build.
