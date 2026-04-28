# Cross-Machine Branch Workflow (PC + Mac)

Use this workflow when you are actively coding on both Windows and macOS.

## Branch strategy

- Do daily development on machine-specific branches, not on `master`.
- Recommended naming:
  - `pc-sync-YYYY-MM-DD`
  - `mac-sync-YYYY-MM-DD`
- Keep `master` as the stable integration branch.

## Documentation ownership policy (split-only)

- `CURSOR_MAC_BUILD_INSTRUCTIONS.md` is Mac-owned and updated on `mac-sync-*`.
- `CURSOR_PC_BUILD_INSTRUCTIONS.md` is PC-owned and updated on `pc-sync-*`.
- Shared docs (`README.md`, `BRANCH_WORKFLOW.md`) can be updated from either machine branch when workflow policy changes.
- Keep platform docs neutral on `master`; merge only validated machine-specific updates.

## First-time setup per machine

### PC

```bash
git fetch origin
git checkout -b pc-sync-YYYY-MM-DD origin/master
git push -u origin pc-sync-YYYY-MM-DD
```

### Mac

```bash
git fetch origin
git checkout -b mac-sync-YYYY-MM-DD origin/master
git push -u origin mac-sync-YYYY-MM-DD
```

## Daily workflow on each machine

1. Switch to the machine branch.
2. Pull latest for that branch.
3. Commit and push frequently.

```bash
git checkout <machine-branch>
git pull
# work, test, commit
git push
```

## Bring changes from one machine to the other

### Option A (recommended): cherry-pick specific commits

Use this when you only want selected changes from the other machine:

```bash
git fetch origin
git checkout <your-current-machine-branch>
git cherry-pick <commit-hash>
```

### Option B: merge entire machine branch

Use this when the full branch should be shared:

```bash
git fetch origin
git checkout <your-current-machine-branch>
git merge origin/<other-machine-branch>
```

## Integrate to master

Only integrate after validation passes on the integration branch:

1. `make verify` (runs `pytest`, `ruff check`, `bandit -ll`, and `pip-audit -r requirements.txt` — all gates must pass; current baseline is 2445 passed / 2 skipped at Round 28 close-out)
2. Platform build (`build_pc.bat` on Windows or `./build_mac.sh` on macOS; optionally `./build_mac_dmg.sh` for a DMG)
3. Confirm artifacts and runtime smoke check
4. Add a Round entry to `QUALITY_AUDIT.md` if the integration includes audit-level fixes

Then merge to `master`:

```bash
git checkout master
git pull origin master
git merge <validated-branch>
git push origin master
```

## Guardrails

- Do not commit generated runtime artifacts such as `outputs/`.
- Keep commits focused and small so cherry-picking stays easy.
- If both machine branches touch the same file, merge early to resolve conflicts before they grow.
