# Cross-Machine Branch Workflow (PC + Mac)

Use this workflow when you are actively coding on both Windows and macOS.

## Branch strategy

- Do daily development on machine-specific branches, not on `main`.
- Recommended naming:
  - `pc-sync-YYYY-MM-DD`
  - `mac-sync-YYYY-MM-DD`
- Keep `main` as the stable integration branch.

## Documentation ownership policy (split-only)

- `CURSOR_MAC_BUILD_INSTRUCTIONS.md` is Mac-owned and updated on `mac-sync-*`.
- `CURSOR_PC_BUILD_INSTRUCTIONS.md` is PC-owned and updated on `pc-sync-*`.
- Shared docs (`README.md`, `BRANCH_WORKFLOW.md`) can be updated from either machine branch when workflow policy changes.
- Keep platform docs neutral on `main`; merge only validated machine-specific updates.

## First-time setup per machine

### PC

```bash
git fetch origin
git checkout -b pc-sync-YYYY-MM-DD origin/main
git push -u origin pc-sync-YYYY-MM-DD
```

### Mac

```bash
git fetch origin
git checkout -b mac-sync-YYYY-MM-DD origin/main
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

## Integrate to main

Only integrate after validation passes on the integration branch:

1. `make verify` (runs pytest, Ruff, Bandit HIGH/MEDIUM, strict dependency audit,
   and deterministic Ask AI eval; all gates and the current `QUALITY_AUDIT.md`
   Round 167.4 floor must pass)
2. Platform build:
   - **Mac shipping:** `ADOPTIQ_RELEASE_GATE=1 HF_HUB_DISABLE_XET=1 ./build_mac_dmg.sh` with `ADOPTIQ_BUILD` set — **must** rebake corpus (default `ADOPTIQ_BAKE_CORPUS=1`). Do **not** ship from `./build_mac.sh` alone (Round 137).
   - **Windows:** `build_pc.bat`
3. Confirm artifacts and runtime smoke check (`scripts/test_build_smoke.sh` on Mac)
4. Add a Round entry to `QUALITY_AUDIT.md` if the integration includes audit-level fixes

### Windows Build 115 parity (Round 167.4)

After the exact Mac Build 115 candidate passes its separate live and promotion gates,
the PC host may package the same source/build and publish the matching **pc** slot in
`AI Projects/OUTBOX/latest.json` without clobbering **mac**. Do not infer Windows
approval from the Mac result.

1. Merge validated Mac branch into `pc-sync-YYYY-MM-DD`.
2. `build_pc.bat` with `ADOPTIQ_BUILD=115`; run the Windows frozen/runtime smoke and
   the same mandatory production simulation before packaging.
3. Keep packaging stage-only by default. After separate serialized publication
   approval, set `ADOPTIQ_PUBLISH_RELEASE=1` and
   `ADOPTIQ_RELEASE_PUBLICATION_APPROVED=PUBLISH`; `build_pc.bat` copies and
   verifies the exact EXE first, then writes `latest.json` last under the same
   portable lock used by the Mac promoter. Never publish Mac and PC concurrently.
4. Confirm `latest.json` has both `mac.build` and `pc.build` at **115**, with each
   artifact's independently verified hash and size.

Then merge to `main`:

```bash
git checkout main
git pull --ff-only origin main
git merge <validated-branch>
git push origin main
```

## Guardrails

- Do not commit generated runtime artifacts such as `outputs/`.
- Keep commits focused and small so cherry-picking stays easy.
- If both machine branches touch the same file, merge early to resolve conflicts before they grow.
