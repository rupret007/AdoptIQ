# QUALITY_AUDIT.md — Round 14

Working note. Not committed unless explicitly requested.

## Stack
- Python 3.11.9
- Flask web app (single-process), CLI entry, PyInstaller mac/win bundles
- Local persistence: sqlite (`admin_monitoring_v2.db`, `external_intelligence.db`, `incident_storage.py`)
- External integrations: Snowflake, Cisco internal services (cisco_internal_integrations.py), CircuIT LLM (adoptiq_backend.py / ask_ai_grounded.py)

## Package boundaries
- Single-package "flat" layout. ~30 top-level modules at repo root + `tests/`. No installable package metadata.
- Hot files (>2k lines): `app_simple.py` (~18.8k), `adoptiq_backend.py` (~11.6k), `leader_report_generator.py` (~6.2k), `compact_report_formatter.py` (~2.9k), `executive_intelligence_formatter.py` (~1.7k).

## Verification commands
- `make test` — pytest (1674 passed / 2 skipped baseline as of Round 13).
- `make lint` — ruff with the rules in `pyproject.toml`.
- `make security` — bandit HIGH/MED gate (`bandit.yaml`); `-x _bundled_secrets.py`.
- `make audit` — pip-audit on `requirements.txt`.
- `make verify` — all of the above.

## Phase-1 baselines (raw)

| Tool | Result |
| --- | --- |
| `pytest -q` | 1674 passed / 2 skipped (warning: `ValueError: I/O operation on closed file.` from `app_simple._shutdown_handler` at the very end of the run) |
| `ruff check .` | 2074 errors before per-file ignores; 1843 auto-fixable |
| `bandit -ll` | 0 HIGH / 4 MED (all `B104` bind-all) / 10 LOW (all false positives or sentinel strings) |
| `pip-audit -r requirements.txt` | No known vulnerabilities |

### Ruff top buckets (before fixes)

| Count | Code | Severity | Notes |
| --- | --- | --- | --- |
| 765 | UP006 | low / cosmetic | `List[str]` → `list[str]`. Mass auto-fix candidate. |
| 316 | I001 | low / cosmetic | unsorted imports |
| 255 | UP045 | low / cosmetic | `Optional[X]` → `X \| None`. Pair with UP007. |
| 190 | F541 | low | f-string with no placeholder. Cosmetic but exposes dead-format calls. |
| 123 | UP017 | low | `datetime.timezone.utc` → `datetime.UTC`. Auto-fix safe. |
| 74 | F401 | medium | unused imports |
| 69 | UP035 | low | deprecated import (`typing.List`) |
| 62 | RUF100 | low | unused `noqa` |
| 35 | B905 | low | `zip(..., strict=)` missing |
| 22 | F841 | medium | unused local variable — sometimes a real bug |
| 18 | RUF059 | low | unused unpacked variable |
| **13** | **F821** | **HIGH** | **undefined name — real bugs** |
| 9 | S104/S105/S106 | mixed | bind-all / hardcoded passwords (false positives reviewed) |

## Adversarial triage findings

| ID | Severity | Surface | Phase | Finding | Action |
| --- | --- | --- | --- | --- | --- |
| R14-001 | LOW | runtime / observability | 3 | `app_simple._shutdown_handler` calls `logger.info` after stdout/stderr close at interpreter shutdown → `ValueError: I/O operation on closed file` printed at the end of every pytest run. | Guard logger call against closed streams. |
| R14-002 | HIGH | correctness | 2 | `data_contracts.validate_row_contract` references undefined `columns` on the success-return path → raises `NameError` every time the contract validates a frame with all required slots. **Reproduced.** | Replace with `columns_raw`. |
| R14-003 | HIGH | observability / admin UX | 2 | `enhanced_admin_dashboard_v2.get_report_history` and `get_analytics` reference module-level `_utc_iso_z` and `_tz`, but those are only defined locally inside `record_report_completion`. Both endpoints throw `NameError`, fall through to broad `except`, log an error and return placeholders. The History "last 7 days" tile and the analytics endpoint are silently broken. **Reproduced.** | Promote helpers to module level. |
| R14-004 | MEDIUM | correctness | 2 | `enhanced_admin_dashboard_v2.record_report_completion` uses `Any` in a nested-function annotation but `typing.Any` is not imported. Annotation only fires on `inspect.signature` / `get_type_hints`, but it is still wrong. | Import `Any`. |
| R14-005 | MEDIUM | correctness | 2 | `adoptiq_backend.py:848,878` annotation strings reference unaliased `OrderedDict`. The module imports it as `_OrderedDictForSchemaCache`. Fires on `get_type_hints` only. | Import `OrderedDict` directly or update the quoted annotation. |
| R14-006 | MEDIUM | correctness | 2 | `app_simple.py:10179, 17598, 17792, 17822` use the `X if 'X' in locals() else Y` defensive antipattern where `X` is never bound in the enclosing function. The branch is dead and the intent is opaque. | Replace with `locals().get('X', Y)`. |
| R14-007 | MEDIUM | security defaults | 1 | `app_simple.py:18768` defaults Flask bind host to `0.0.0.0`. Sensitive endpoints rely on a separate `_is_local_client` gate. The admin dashboard already defaults to `127.0.0.1` and gates public binding behind `ADOPTIQ_ADMIN_BIND_PUBLIC=1`. Default the main app the same way. | Default to `127.0.0.1`; opt-in public bind via `ADOPTIQ_BIND_PUBLIC=1` env. |
| R14-008 | LOW | hygiene | 4 | `bandit` flags `_bundled_secrets.py` even though it is gitignored / generated. | Excluded via `bandit.yaml` + `-x _bundled_secrets.py`. (Done.) |
| R14-009 | LOW | hygiene | 4 | Ruff is not on the gate; `make lint` introduced. Many cosmetic issues remain. | Configure conservative ruff ruleset; defer mass auto-fix to a separate cleanup. |

## Adversarial review notes (Phase 2)

Areas walked, in order. Findings folded into the table above where they led to a fix.

- **HTTP surface (`app_simple.py`, `enhanced_admin_dashboard_v2.py`)**: CSRF middleware is in place (Round 5/6 work). `_is_local_client` gate is the defensive ring around download/cancel/intel endpoints. The bind-host default is the one improvement landing in Phase 1.
- **Outbound integrations (`cisco_internal_integrations.py`, `ask_ai_grounded.py`, `adoptiq_backend.py`)**: timeouts are set; retries have jitter (Round 6). No raw URL inputs; allow-listed hosts. No new finding.
- **Persistence (`incident_storage.py`, `enhanced_admin_dashboard_v2.py` sqlite)**: parameterized queries throughout. R14-003 is the only sqlite-adjacent finding.
- **File I/O (`download_*` routes, `OUTBOX`, uploads)**: path traversal already mitigated (`_resolve_csone_path_safe`). No new finding.
- **Concurrency (`analysis_status` dict, `_TABLE_COLUMN_*` LRUs)**: Round 13 added LRU caps and TTL eviction. R14-005 is the cosmetic annotation fix; no real concurrency bug observed.
- **Time / timezone**: 123 UP017 flags are the `timezone.utc` → `UTC` modernization. Defer to Phase 4 polish; not a behavior change.
- **Error swallowing**: R14-002 / R14-003 confirm the cost — silent NameError disguised as "soft failure." Fix the F821 set; the swallowing pattern itself is necessary and stays.
- **Logging redaction**: Round 13 redacted `[[FILTER]]` PII. Spot-check shows no new INFO-level leaks.
- **Crypto / certs**: usage limited to `cryptography`/`hvac`/`truststore`; no hardcoded keys; no deprecated algos surfaced by ruff/bandit.

## Round 14 phases (final)

### Security (Phase 1.x)
- **Phase 1.1** — Default `app_simple` Flask bind host to `127.0.0.1`; require `ADOPTIQ_BIND_PUBLIC=1` (or explicit `ADOPTIQ_BIND_HOST`) to expose externally. (R14-007)

### Correctness (Phase 2.x)
- **Phase 2.1** — Fix `data_contracts.validate_row_contract` `columns` → `columns_raw`. Was raising `NameError` on the success path. (R14-002)
- **Phase 2.2** — Promote `_utc_iso_z` and `_tz` to module level in `enhanced_admin_dashboard_v2.py`; import `Any`. Two admin dashboard endpoints (`get_report_history`, `get_analytics`) were silently failing with `NameError` and returning placeholders. (R14-003, R14-004)
- **Phase 2.3** — Bind `OrderedDict` at module scope in `adoptiq_backend.py` so quoted annotations on the LRU caches resolve under `get_type_hints`. (R14-005)
- **Phase 2.4** — Replace `X if 'X' in locals() else Y` antipattern with `locals().get('X', Y)` at four sites in `app_simple.py`. Removes dead bare-name references. (R14-006)

### Reliability (Phase 3.x)
- **Phase 3.1** — Guard `app_simple._shutdown_handler` logger call against closed streams using `logging.raiseExceptions=False`. Removes the trailing `--- Logging error ---` diagnostic at every shutdown. (R14-001)

### Polish (Phase 4.x)
- **Phase 4.1** — Drop redundant duplicate `from config import Config` in `adoptiq_backend.py`. (F811)
- **Phase 4.2** — Replace misleading `part.strip('**')` with explicit `part[2:-2]` in `app_simple.py` Word-render bold-tag handler. (B005)
- **Phase 4.3** — Remove duplicate `"Worker"` and `"Mode"` tokens from the technical-name allowlist `frozenset` in `structured_logging.py`. (B033)
- **Phase 4.4** — Document false-positive S105/S106 findings (OAuth `/token` URL, `_kind_token` enum label, developer smoke-test placeholders) with per-line `# noqa: S10x -- rationale`. (S105, S106)
- **Phase 4.5** — Annotate the existing opt-in admin `0.0.0.0` bind with `# noqa: S104 # nosec B104` so bandit and ruff both stop alerting; the public-bind branch is gated by `ADOPTIQ_ADMIN_BIND_PUBLIC=1`. (S104, B104)
- **Phase 4.6** — Surface CLI `_integrity_checks` reason at WARNING in `adoptiq_backend.py`. The result was being assigned to `reason` and discarded — the integrity gate was a silent no-op on the CLI report path while the Flask flow inspects it correctly. (F841)

## Files changed

### New (untracked)
- `Makefile` — `test`, `lint`, `security`, `audit`, `verify` targets.
- `pyproject.toml` — ruff config (E/F/B/S selected; documented ignore list).
- `bandit.yaml` — bandit config (HIGH/MED gate; documented skips).
- `QUALITY_AUDIT.md` — this document.
- `tests/test_round14_markers.py` — marker presence tests for every Round 14 fix.
- `tests/test_round14_behavioral.py` — behavioral regression tests.

### Edited
- `app_simple.py` — Phase 1.1, 2.4 (×4 sites), 3.1, 4.2.
- `adoptiq_backend.py` — Phase 2.3, 4.1, 4.4, 4.6.
- `enhanced_admin_dashboard_v2.py` — Phase 2.2, 4.5.
- `data_contracts.py` — Phase 2.1.
- `cisco_internal_integrations.py` — Phase 4.4 (×3 sites).
- `structured_logging.py` — Phase 4.3.

## Verification commands & results

```
$ make verify
... pytest 1689 passed, 2 skipped in 5.03s
... ruff: All checks passed!
... bandit: 0 HIGH, 0 MED, 10 LOW (all triaged false positives, see Deferrals)
... pip-audit: No known vulnerabilities found.
```

Run twice consecutively, both clean.

| Tool | Pre-Round-14 | Post-Round-14 |
| --- | --- | --- |
| `pytest -q` | 1674 passed / 2 skipped (+ trailing `ValueError` on shutdown) | **1689 passed / 2 skipped** (clean shutdown) |
| `ruff check .` | n/a (not configured); 2074 errors at first run | **All checks passed** |
| `bandit -ll` | 0 HIGH / 4 MED / 10 LOW | **0 HIGH / 0 MED / 10 LOW** |
| `pip-audit -r requirements.txt` | clean | **clean** |

## Deferrals (knowingly not addressed in Round 14)

| Item | Why deferred |
| --- | --- |
| **UP rules** (UP006/UP017/UP035/UP045): ~1,210 cosmetic PEP-585/604/UTC modernizations | Pure style; touching this many lines risks review fatigue and would obscure Round 14's behavioral fixes. Track as a follow-up PR scoped to a single ruleset at a time. |
| **I001** import sorting (~317 occurrences) | Mass refactor of every module's import block; non-functional. Follow-up PR. |
| **F541** f-string-without-placeholder (~190) | Cosmetic; many are intentional in error messages. Follow-up PR. |
| **B905** `zip(..., strict=)` (~35) | Adding `strict=` could change behavior on unequal-length zips. Audit each call site separately. |
| **F841** non-bug unused locals (~21 remaining) | Mostly heading-capture idiom (`var = self.doc.add_heading(...)`). One real bug (Phase 4.6) was extracted; the rest are cosmetic. |
| **F401** unused imports (~74) | Many are intentional cross-module re-exports (e.g. `from openai import APITimeoutError` for `except` clauses elsewhere). Manual review needed; bandit/SCA already cover the security risk surface. |
| **`# nosec` parser warnings** | bandit emits "Test in comment: X is not a test name" for our human-readable rationale text; bandit still exits 0 since the matched test code is recognized. Cosmetic noise, not a failure. |
| **PyInstaller / build pipeline polish** | Out of scope per plan §"Out of scope". |

## Residual risks

- **Bind-host default**: changes from `0.0.0.0` to `127.0.0.1`. Anyone deploying the Flask app behind a reverse proxy on a single host without `ADOPTIQ_BIND_HOST=0.0.0.0` (or `ADOPTIQ_BIND_PUBLIC=1`) will see "no route to host" from the proxy. Mitigation: `ADOPTIQ_BIND_PUBLIC=1` is documented as the opt-in.
- **CLI integrity gate** still does not abort on integrity violations — Phase 4.6 only surfaces a WARNING. The Flask path is unchanged. A follow-up could decide whether the CLI should also abort.
- **Ruff `select` is narrowed** to E/F/B/S. Re-introducing UP/I/RUF on a future round will surface ~1,800 cosmetic findings — track as a separate effort.
- **Bandit LOW findings (10)** are documented false positives (test fixtures, defensive `try/except/pass` paths). Re-triage if any new appears.

## Recommended follow-ups

- Add `make verify` to a CI gate (the plan kept this out of scope, but the harness is ready).
- Schedule a "Round 14 polish — cosmetic ruff fixes" PR per ruleset (UP, I, RUF) so each is reviewed in isolation.
- Decide on CLI vs Flask integrity-gate parity for `_integrity_checks` (currently asymmetric).
- Audit all `# noqa: S10x` and `# nosec B10x` annotations annually — the rationale text should still match the surrounding code.

## Final verification checklist
- [x] `make lint` clean
- [x] `make security` clean (0 HIGH / 0 MED; LOW triaged in Deferrals)
- [x] `make audit` clean
- [x] `make test` clean — two consecutive runs (1689 passed / 2 skipped)
- [x] `git diff --stat` reviewed for accidental scope creep — all Round 14 lines carry a `Round 14 / Phase X.Y` marker
- [x] Adversarial self-review of the diff — every changed line maps to a backlog finding
