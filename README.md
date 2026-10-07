# AdoptIQ

**Version 1.0.4 / source build 116** — shown in the app footer.

AdoptIQ is a Flask desktop web app (macOS `.app` / Windows `.exe`) that generates
renewal-risk and adoption reports from CSOne exports, Snowflake/CSConsole data,
support cases, and a local encrypted knowledge corpus. End users open the packaged
app; the browser UI is `http://127.0.0.1:5151`.

Build 116 is **source-only**. No Build 116 release candidate, artifact hash, live
acceptance, or production approval exists yet. Build 115 is **invalidated / NO-GO**
and must not be installed or promoted.

All GitHub-hosted Actions builds are **developer-candidate-only** native macOS and
Windows artifacts. Hosted jobs reject release/tag modes, never receive release
credentials, and contain no prebaked customer corpus or embedding cache. They are for
portable fixture validation only—not live validation, production packaging, release,
or deployment. Production candidates are built only on an authorized local work
machine after the source, corpus, credential, native, and live gates in
`NEXT_MACHINE_PROMPT.md` pass with explicit approval.

**Honesty:** local `make verify` and offline fixtures are regression evidence.
`ready_for_live_cisco` stays **false**. Sim ≠ live Cisco / Snowflake / CircuIT /
CSConsole / CSOne accuracy.

---

## Features

- **Decision reports** — Compact, Comprehensive, Leader, Renewal, and Subscription.
  Word stays executive (KPIs, charts, actions). A paired
  `AdoptIQ_Source_Data_*.xlsx` holds the exact 17-sheet inventory.
- **Canonical counts and risk** — `canonical_metrics` and `risk_scoring` are the
  single sources of truth. Health grades stamp from risk bands, not free-form LLM.
- **Ask AI** — retrieval-first, citation-checked answers (sync + stream) over the
  same scoped evidence as the reports. Uncited claims go to Evidence Gaps.
- **Observed-in-peers guidance** — on Ask AI, Customer 360, and Historical Context:
  next-step / likely-next from similar accounts, fail-closed when evidence is thin,
  mixed, already-lived, or the question names an unlived path. Local corpus only;
  never a new report page.
- **Customer 360, History, External Intelligence** — timelines, prior reports, and
  Webex status/help intel on existing surfaces.
- **Operator UX** — in-page report jobs, Preferences (model, CSOne folder, outputs,
  aliases, product practice, auto-update), dark/light theme, mobile + keyboard
  polish (Rounds 184–187).
- **Product practice** — Preferences: Collaboration (Webex technology roster) or
  Security (technology pack pending). New report starts fail closed until the
  Security pack ships (AJAX/JSON **HTTP 409**; HTML `/start_analysis` may
  redirect; Compact/Renewal still validate CSRF first). The shared start gate
  captures one immutable snapshot (practice + pack + filters) and queues that
  exact pack; a Preferences switch during upload/discovery/validation cannot
  change it. New requests still resolve live practice. Slice 2 still pending.
- **Local knowledge corpus** — AES-256-GCM encrypted SQLite; packaged builds can
  ship a prebaked snapshot. OneDrive is optional refresh, not a first-launch gate.

---

## Status and limits

| Item | Truth |
|------|--------|
| Version / build | `1.0.4` / `116` in `config.py` |
| Release candidate | None. Do not invent a hash, size, or approval. |
| Build 115 | Invalidated historical evidence under `release_candidates/macos-build115/` |
| Live Cisco validation | **NOT RUN** on this source tree. `ready_for_live_cisco=false` |
| Hosted CI | Developer-candidate fixtures only (`PR Quality Checks` → `make verify`) |
| Packaging | Authorized work machine only. Ad-hoc signed; not notarized. |
| Peer guidance | Fixture / local-corpus evidence. Not live Cisco accuracy. |

Use `WORK_MACHINE_BUILD116_PROMPT.md` (copy-ready) and `NEXT_MACHINE_PROMPT.md`
(authoritative runbook). Older `WORK_MACHINE_ROLLOUT.md`, `BUILD_WINDOWS.md`, and
`CURSOR_*_BUILD_INSTRUCTIONS.md` files are historical and must not be executed.

### Recent source changes (Rounds 169–190)

- **Local truth (169–174)** — stable-ID reconciliation, 17-sheet cross-family
  parity, digest-bound CSOne replay, support/operating-health corpus receipts.
- **Peer guidance (175–182)** — likely-next / next-step on existing surfaces;
  barrier-status join; outcome-aware copy; this-account lived paths; Ask AI uses
  the path the question names.
- **Operator polish (184–187)** — fixture-stale / empty-state labels; mobile
  scroll and 390px layout; heading order, focus-visible, and keyboard flow.
- **Practice SSoT (Slice 1 / Rounds 188–190)** — Collaboration pack is the only
  technology roster. Security fail-closes new report starts (including Leader).
  Switching practice restores or clears live filters without a restart. Round 190
  captures the pack once at the shared start gate and persists that snapshot —
  not a second live read at status creation. Live Cisco/Snowflake/CircuIT **NOT RUN**.

Full round journal: `QUALITY_AUDIT.md` (append-only; do not rewrite history).

---

## Quick start (packaged app)

No Python is required for end users. There is **no approved Build 116 DMG/EXE**
yet; only install a candidate your work machine has packaged and labeled.

### macOS

1. Open the AdoptIQ DMG for the **approved** build (not Build 115).
2. Drag **AdoptIQ.app** to Applications.
3. Double-click **Unblock AdoptIQ.command** (clears Gatekeeper quarantine on the
   ad-hoc-signed app). Apple Silicon otherwise bounces once and exits.
4. Eject the DMG. Launch from Applications. Browser opens `http://127.0.0.1:5151`.

Manual unblock if the DMG is gone:

```bash
xattr -dr com.apple.quarantine /Applications/AdoptIQ.app
open /Applications/AdoptIQ.app
```

### Windows

1. Open the folder with `AdoptIQ.exe`.
2. Run **AdoptIQ.exe**. If SmartScreen appears: **More info** → **Run anyway**.

### In the app

1. Keep the process running while you use the browser UI.
2. Upload the **AdoptIQ Enhanced/Premium Collab Summary** CSOne `.xlsx`, or let
   OneDrive autodiscovery pick it up. Export help is on Analyze / Leader / Help
   (`CSONE_REPORT_URL`, default report `00OfX000001Nnh2UAC`).
3. Choose Compact / Comprehensive / Leader / Renewal / Subscription and generate.
4. Downloads and **Previous Reports** list completed jobs. Packaged reports default
   to `~/Documents/AdoptIQ Reports` (override in Preferences or `ADOPTIQ_OUTPUTS_DIR`).

**CSOne report:** [AdoptIQ Enhanced/Premium Collab Summary](https://csone.lightning.force.com/lightning/r/Report/00OfX000001Nnh2UAC/view?queryScope=userFolders)
— Export → Standard → upload the `.xlsx`. Corporate VPN is usually required for
Snowflake/CSOne.

---

## Developer setup and test

Requires **Python 3.11+** (CI uses 3.11; this machine’s venv is 3.12). No
`package.json`.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install ruff bandit pip-audit  # not in requirements.txt; CI installs these
python app_simple.py               # http://127.0.0.1:5151
```

Admin console (loopback): `http://127.0.0.1:5152`.

| Command | What it does |
|---------|----------------|
| `make verify` | Lint + Bandit HIGH/MED + pip-audit + pytest + Ask AI eval. Canonical gate. |
| `make test` | `pytest -q -m 'not eval'` |
| `make lint` / `make lint-fix` | `ruff check` |
| `make security` | Bandit HIGH/MED |
| `make audit` | `pip-audit -r requirements.txt --strict` |
| `make eval-ask-ai` | Offline Ask AI cassette replay (also part of `verify`) |
| `make production-simulation CSONE_CORPUS_DIR=...` | Offline pre-build sim. Needs an **external** CSOne dir. Not live Cisco. |

CI: `.github/workflows/pr-quality.yml` runs `make verify` on every pull request.
`.github/workflows/build.yml` is developer-candidate packaging only.

Native DMG/EXE: `ADOPTIQ_RELEASE_GATE=1 bash build_mac_dmg.sh` or `build_pc.bat`
on an authorized machine after `NEXT_MACHINE_PROMPT.md`. Skip signing/notarization
unless you have those identities. Do not run packaging just to “check the README.”

Branch workflow for dual Mac/PC coding: `BRANCH_WORKFLOW.md` (`main` is the
integration branch).

---

## Navigation

- **Dashboard / Analyze** — start reports; live jobs stay on the page.
- **History** — previous analyses and downloads.
- **Intel** — Webex incidents/bugs/maintenances; export/import; ask about intel.
- **Ask AI** — grounded Q&A plus the Observed-in-peers card when evidence exists.
- **Customer 360** — `/customer/<name>` timeline and peer-guidance card.
- **Playbook** — `/playbook` recurring barriers/resolutions from the local corpus.
- **Preferences** — models, CSOne folder, report outputs, aliases, product
  practice (Collaboration / Security), auto-update.
- **Help** — operator walkthrough.
- **Admin Console** — loopback `:5152` (monitoring; does not shut down the main app).

---

## Ask AI and corpus

Grounded mode is on by default. Set `ADOPTIQ_ASK_AI_V2=0` only to force the
legacy path.

- Source-backed claims carry IDs (AB, case, incident, bug, BEMS).
- Report-bound Ask AI refuses stale/invalid/future evidence for current-state
  questions.
- Peer clauses that are evidence-sufficient carry a `CORPUS:PG-` SourceID; thin
  paths do not.
- Rollback / kill switches stay fail-closed. Public JSON stomps
  `ready_for_live_cisco`.

### CSOne knowledge corpus

Packaged apps may include `Resources/baked_corpus/` (`corpus.db.enc`,
`corpus.db.salt`, `sentinel.json`). Runtime refresh indexes generated reports,
Intelligence uploads, and OneDrive files when present. Dense-vector backfill is
best-effort; lexical retrieval still works if the embedder is missing.

Useful env / settings (highest-wins settings.json → env → `config.py`):

| Name | Role |
|------|------|
| `CSONE_ONEDRIVE_FOLDER` | Override the OneDrive sync path |
| `ADOPTIQ_OUTPUTS_DIR` | Override report output root |
| `CSONE_REPORT_URL` | CSOne export deep link |
| `ADOPTIQ_VERBOSE_DEBUG=1` | Extra diagnostics (also Admin → Debug Controls) |
| `ADOPTIQ_BIND_PUBLIC=1` | Opt-in non-loopback bind for the main app |
| `ADOPTIQ_ASK_AI_V2=0` | Legacy Ask AI |
| `CSONE_INCLUDE_USER_DOWNLOADS` | Retired (Round 102); ignored so Downloads is never re-enabled |

`ADOPTIQ_CORPUS_SHARE_URL` is documentation / bake logging; the runtime walks
`CSONE_ONEDRIVE_FOLDER`. MSAL/Graph SharePoint settings are retired no-ops.

#### Corpus security model

**At-rest.** `~/Library/Application Support/AdoptIQ/knowledge/corpus.db.enc` is
AES-256-GCM ciphertext (mode `0o600`, parent `0o700`). `corpus.db.salt` is the
per-install KDF salt. Packaged builds may bundle a local `sentinel.json` so the
prebaked corpus opens without OneDrive. Treat the DMG as containing the corpus dataset.

**Runtime.** SQLite needs a real file, so AdoptIQ decrypts to an ephemeral
plaintext file under `$TMPDIR/adoptiq_corpus/` (mode `0o600`, parent `0o700`).
On clean Quit / `SIGTERM`, `EncryptedCorpusHandle.close` scrubs then unlinks
that temp file; the scrub is registered with `atexit`. Hard crash (`SIGKILL`)
can leave the temp file until `$TMPDIR` is cleared. AdoptIQ does **not** claim
"never plaintext on disk" — plaintext is ephemeral, single-user, and scrubbed
on clean exit. The SharePoint URL is not the secret.

---

## Where files live

| Purpose | Packaged default | Also |
|---------|------------------|------|
| Generated Word/Excel | `~/Documents/AdoptIQ Reports` (macOS/Windows Documents) | Preferences / `ADOPTIQ_OUTPUTS_DIR`; legacy App Support `outputs/` still searched |
| CSOne uploads | `~/Library/Application Support/AdoptIQ/uploads/` | `%APPDATA%\AdoptIQ\uploads\` |
| Analysis status | App Support `analysis_status.json` | |
| Encrypted corpus | App Support `knowledge/` | |
| External intel DB | App Support `external_intelligence.db` | |
| Settings | App Support `settings.json` (mode `0600`) | |

Dev checkouts write reports under `./outputs` unless you set an override.

---

## Requirements

- macOS or Windows for the packaged app; Python 3.11+ for development
- Corporate VPN for Snowflake / CSOne (not required for offline pytest)
- Cisco CSOne access to export the Collab Summary workbook

---

## Troubleshooting

- **Port 5151 in use:** a second launch should open the existing UI. `lsof -i :5151` on macOS.
- **Dock bounce then exit (macOS):** quarantine on an ad-hoc build — run Unblock (above).
- **Startup crash:** `~/Library/Application Support/AdoptIQ/startup_error.txt`
- **Connection / Snowflake errors:** VPN, then ask for a build packaged with a complete `secrets.env`. Never commit secrets.
- **Partial-data banners:** often scope filters (manager / technology / window), not an outage.
- **Verbose debug:** `ADOPTIQ_VERBOSE_DEBUG=1` or Admin → Debug Controls.

---

## Found an issue?

Internal defect tracker:

[App Defect Report Spreadsheet](https://cisco-my.sharepoint.com/:x:/r/personal/jestory_cisco_com/Documents/AI%20Projects/OUTBOX/_App_Defect_Report_Template.xlsx?d=wc71bd561722b491e9007cf6fe5ba92eb&csf=1&web=1&e=mD1Yga)
