# AdoptIQ — Test On-Ramp (local simulation → work-machine live)

## 0. Moving the branch to the work machine and running it

The work machine already has an AdoptIQ checkout (you've run live acceptance there), so you only need the new branch on it, then one command to run.

**Option A — via GitHub (cleanest, if the work machine can reach the repo):**

```bash
# on your Mac (once):
git push -u origin claude/round153-leader-decision-value

# on the work machine:
git fetch origin
git checkout claude/round153-leader-decision-value
./run_dev.sh            # sets up the venv, installs deps, launches on :5151
```

**Option B — via the bundle (if the work machine can't reach the personal repo):**

```bash
# copy .tmp/round153/adoptiq_round153_branch.bundle to the work machine
# (OneDrive/USB), then in the work-machine checkout:
git fetch /path/to/adoptiq_round153_branch.bundle \
    'refs/heads/claude/round153-leader-decision-value:refs/heads/claude/round153-leader-decision-value'
git checkout claude/round153-leader-decision-value
./run_dev.sh
```

`run_dev.sh` is idempotent and safe to re-run: it finds Python 3.11, creates
`.venv311`, installs `requirements.txt` (only when it changed), and launches
`python app_simple.py`. It changes no connection code — on the work machine it
uses your existing Cisco config / Keeper / VPN exactly as the normal app does.
`./run_dev.sh --setup` prepares the venv without launching; `--admin` also
starts the admin dashboard on :5152. No packaged-app rebuild is needed to test
from source; the DMG rebuild is only for shipping.

---

Short answer to "do we need to build a Snowflake simulator?": **No. You already have one, and it's complete.** This doc is the map for using it, and for the live path when you bring the branch to the work machine.

---

## 1. What already exists (don't rebuild this)

`local_acceptance_runtime.py::install_runtime_adapters` is a full Snowflake-connection simulator. It monkeypatches the **real** fetch functions the report workers call —

- `fetch_support_cases_snowflake`, `fetch_csconsole_action_plans`,
  `fetch_csconsole_adoption_barriers`, `fetch_csconsole_customer_pulse`,
  `fetch_csconsole_success_priorities`, `fetch_subscription_data`,
  `fetch_help_webex_bugs`, `fetch_status_incidents`/`_maintenances`,
- the leader-path `fetch_action_plans_snowflake` / `_r65_fetch_aps_snowflake`,
- CSOne/OneDrive path resolution, the corpus bootstrap, and **`TEAM_ROSTER` / `MANAGERS`**

— so the actual `run_leader_report_generation`, `run_comprehensive_analysis`, etc. run **end to end** against versioned synthetic frames. It is not a stub of the output; it exercises the same code path a live run does, minus the network. This is why the guarded HTTP acceptance passes 21/21 scenarios.

The fixtures live at `tests/fixtures/local_acceptance/v1/manifest.json` and `tests/fixtures/report_acceptance/v1/sanitized_portfolio.json`. They are sanitized and synthetic — no customer data.

**What this means for you:** the "plug it in" seam is already the boundary between these patched fetch functions and the real Snowflake calls. On the work machine you simply *don't* install the adapters, and the same workers hit real Snowflake instead. There is nothing new to build for simulation.

---

## 2. Local smoke test — any machine, no VPN

Run these to prove the whole pipeline works before you touch the work network. All are offline and deterministic.

```bash
# fast unit + contract gate (~11 min on 8 cores)
make verify                      # ruff + bandit + pip-audit + pytest + Ask AI eval

# guarded end-to-end via the simulator (the real workers, fixture data)
make local-acceptance-lab        # 21 reconciled source-state scenarios
make local-acceptance-http       # 21/21 loopback HTTP scenarios (real routes)
make local-acceptance-app SCENARIO=healthy   # generate real DOCX/XLSX from a healthy fixture

# paired decision-report contract, offline mode
make decision-report-acceptance MANAGER="Local Fixture Manager" \
     AS_OF=2026-08-03T12:00:00Z MODE=offline CUSTOMER_NAME="Acme Corporation"

# render the four canonical scopes and read the actual Word/Excel
python3 scripts/generate_offline_acceptance_artifacts.py \
     --scope team --as-of 2026-08-03T12:00:00Z --output-dir /tmp/aiq_local
python3 scripts/r114_audit_reports.py --auto --reports-root /tmp/aiq_local
```

Use **Python 3.11** (the Round 150 `pip-audit` failure is a 3.9 resolver quirk on `truststore`, not a real vulnerability).

> Note: the shipped offline fixtures are deliberately *partial* (charts withheld, KPIs unavailable) to test the fail-closed disclosures. To see the reports in their **best** state — full member roster, distinct Tier 1 risk drivers, verified freshness, all four charts — use `local-acceptance-app SCENARIO=healthy`, not the bare artifact generator.

---

## 3. Work-machine live path — with VPN, the real thing

This is Round 154 (see `ROUND154_HANDOFF.md`). The only change from local is: **do not** install the runtime adapters, and provide real Cisco config + VPN. Then:

```bash
git fetch origin && git checkout claude/round153-leader-decision-value
# (or the Round 154 branch once it exists)

# start the normal app (NOT the fixture process)
python app_simple.py            # http://localhost:5151

# live preflight
curl -s localhost:5151/ping
curl -s localhost:5151/api/version
curl -s localhost:5151/api/status/all
curl -s localhost:5151/api/diag/connectivity

# live paired acceptance (real Snowflake)
make decision-report-acceptance MANAGER="<your manager>" \
     AS_OF="<now ISO8601Z>" MODE=live CUSTOMER_NAME="<a real customer>"
make ai-feature-acceptance MANAGER="<your manager>" CUSTOMER_NAME="<real customer>"

# artifact audit on live output (includes Compact/Renewal, which the offline
# generator cannot produce)
python3 scripts/r114_audit_reports.py --auto
```

Keep every generated artifact and summary **outside Git**. Label results `live_cisco_sources`, never `offline_fixture`.

### The three things only the live run can validate

1. **The Round 152 route-gating sweep.** 14 routes moved behind the localhost + Host gate. If any in-app `fetch()` sends an unexpected `Host`, a legitimate call now 403s — invisible offline. Load every page and run one report of each type; watch for 403s. (Full route list in `ROUND154_HANDOFF.md` §Phase 0.)
2. **Real freshness stamps.** Round 153 made Leader/Renewal/Subscription render "Data as of unavailable" offline on purpose (they don't yet thread a real prefetch clock). Round 154 wires the real `snowflake_prefetch` timestamp into those three workers; confirm the subtitle shows the actual retrieval time, and degrade one source to confirm it says so.
3. **The Tier 4 alias fix on real data.** Pick a manager whose portfolio has an org under cross-source name variants. Confirm the decision report's customer count equals `canonical_metrics.count_customers`, that `Account_Summary` has one row for that org, and that its risk score is *higher* than the pre-fix split halves (evidence no longer divided). The synthetic NYU test proves the code; this proves it on your data. The `validate_cross_artifact_contract` guard will hard-block publication if an alias group ever splits again.

### One thing to spot-check live (member customer counts)

In healthy synthetic renders the member table's "Customers" column populates from the shipped fixture but was blank on a hand-built frame that lacked the customer-linkage column. On real Snowflake shapes it populates correctly; just confirm each team member's customer count is non-blank on the first live Leader Team report, since that column depends on the live customer-id column being present.

---

## 4. Making it "just work" when you plug in

The seam is already clean. To make the work-machine run frictionless:

- Keep your Cisco config / Keeper / VPN exactly as the existing chains expect — Round 152/153 changed none of the connection code, table allowlists, or auth.
- The branch needs no new dependencies beyond `requirements.txt`.
- Nothing in Rounds 152–153 requires a rebuild of the packaged app to *test* — you run from source with `python app_simple.py`. The DMG rebuild is only for shipping.

If you want, a future 30-minute task could add a single `make live-smoke` target that chains ping → version → connectivity → one Leader report → r114 audit, so the whole live check is one command. Not required — the pieces above already cover it — but it would turn the on-ramp into a one-liner.

---

**Bottom line:** the simulator exists and is faithful; local testing is three `make` targets; the work-machine path is the same commands minus the adapters plus VPN; and the three things worth your attention live are the route-gating sweep, the real freshness stamps, and the alias fix on your actual portfolio.
