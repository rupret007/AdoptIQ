# Try AdoptIQ offline (no Cisco access required)

This is the fastest honest way to see what AdoptIQ actually does — no VPN, no
Snowflake, no CSOne export, no LLM credentials, and no packaged `.app`. Everything
below runs the real product code (`app_simple.py`, the same report writers, the
same Customer 360 / Ask AI / Playbook routes) against small, synthetic, checked-in
fixture data. It is **not** live Cisco data and it is **not** a production build —
every page you'll see says so explicitly, because AdoptIQ is built to disclose its
own data state rather than paper over it.

Requirements: Python 3 with `pip install -r requirements.txt` run once (or an
existing dev checkout). Both commands below were verified on macOS under Python
3.9.6 for this doc; the code has no macOS-only dependency, but Windows/Linux are
not re-verified here. (The project's own `make verify` gate targets Python 3.11+
for lint/tests — that's a separate, stricter requirement than just running the
demo commands below.) Run everything from the repo root.

## Path A — see a real report in ~30 seconds, no server

This generates an actual current-format Leader Decision Report (Word) and its
paired Source Data workbook (Excel) straight from the fixture data, with no Flask
server and no network calls:

```bash
python3 scripts/generate_offline_acceptance_artifacts.py \
  --scope team --as-of "2026-08-03T21:00:00Z" --output-dir /tmp/adoptiq-demo
```

Open the two files it prints (`word_path` / `source_data_path` in the JSON it
emits, both under `/tmp/adoptiq-demo/`). You'll get:

- A **Word report** that opens with an Executive Summary and a Decision Brief
  ("Manager focus: intervene first on the highest-risk accounts..."), a table of
  the top overdue/blocked Action Plans with an owner and a first move for each,
  and a `[Source: ...]` citation under every claim pointing at the exact sheet
  and cell in the paired workbook. It also opens with an explicit **Data
  Coverage Warning** naming which sources are partial/offline and stating plainly
  that unavailable data is never shown as a zero.
- An **Excel workbook** with the full 17-sheet inventory the product always
  produces alongside the Word file: `Report_Info`, `Metric_Lineage`,
  `Chart_Data`, `Evidence_Links`, `Action_Plans`, `Adoption_Barriers`,
  `Customer_Pulse`, `TAC_Cases`, `BEMS`, `Subscriptions`, `Success_Priorities`,
  `External_Incidents`, `External_Bugs`, `Defect_Correlations`,
  `Risk_Components`, `Member_Summary`, `Account_Summary`. `Metric_Lineage` maps
  every number in the Word doc back to the exact source rows that produced it —
  that traceability is the point of the product, not an afterthought.

Change `--scope` to `member`, `customer`, or `comprehensive` to see the other
report shapes from the same fixture. This is the same generator the release
acceptance gate (`make decision-report-acceptance`) uses, so what you're looking
at is the real report contract, not a mockup.

## Path B — click around the real running app (~2 minutes)

This boots the actual Flask app (the same one the packaged `.app` runs) on
`127.0.0.1`, wired to the same synthetic fixture instead of Snowflake/CSOne:

```bash
python3 scripts/run_local_acceptance_app.py --enable-local-fixtures --scenario healthy
```

It prints a redacted summary and then serves at **http://127.0.0.1:5153**.
Ctrl+C to stop it; it binds to loopback only and refuses to start unless you pass
`--enable-local-fixtures` explicitly (see `local_acceptance_lab.assert_safe_activation`
for the guardrails — it also refuses to run at all from a packaged/frozen build).

Worth clicking:

- **Dashboard** (`/`) — the "Manager Decision Workspace" home page. Notice the
  **"Controlled local fixture"** banner near the top — the product tells you
  when you're looking at fixture data instead of hiding it.
- **Customer 360** (`/customer/Acme%20Corporation`, or `Beta%20Industries` /
  `Gamma%20Public%20Sector`) — per-customer history, barriers, and an
  **"Observed-in-peers"** card that surfaces next-step guidance the product has
  seen work for similar accounts elsewhere in its knowledge corpus. With this
  small fixture there isn't enough peer history yet, so the card honestly says
  *"insufficient evidence"* instead of guessing — that fail-closed behavior
  (never inventing a forecast when the evidence is thin) is a deliberate design
  choice, not a bug in the demo.
- **Playbook** (`/playbook`) — recurring barriers and resolutions across the
  indexed corpus, browsable by technology/theme.
- **Ask AI** (`/ask-ai`) — the question box and the suggested-question chips
  (e.g. *"Which customer in my portfolio carries the highest renewal risk right
  now..."*) render normally. **Actually answering a question needs a live
  CircuIT LLM connection**, which this offline demo intentionally does not have
  — you'll see a validation error, not a hallucinated answer. That's the same
  fail-closed contract as the peer-guidance card: no grounded evidence, no
  claim.
- **History** (`/history`) — lists past analyses; empty in a fresh fixture run.

The fixture portfolio is manager "Dana Manager" with two team members (Alex
Rivera, Morgan Lee) covering three accounts (Acme Corporation, Beta Industries,
Gamma Public Sector) — small on purpose, so it's fast and legible, not because
that's a production-scale portfolio.

## What this demo is not

- Not live Cisco/Snowflake/CSOne/Keeper data — every synthetic record is stamped
  as such in code and disclosed on every page.
- Not a production build, not a release candidate, and running it proves nothing
  about the packaged `.app`'s Snowflake/CircuIT connectivity.
- Not evidence for `ready_for_live_cisco` — that flag stays `false` until the
  authorized live-data gates in `NEXT_MACHINE_PROMPT.md` pass on a work machine.

For the real packaged app against live Cisco data, see **Quick Start** in
[README.md](README.md#quick-start).
