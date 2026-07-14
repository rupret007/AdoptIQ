# Handoff: Port AdoptIQ-style auto-update to TACTrack

**Source repo:** AdoptIQ (`/Users/jestory/AdoptIQ/AdoptIQ`)  
**Target:** TACTrack (staging: `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/TACTrack_MAC/`; also check `~/AdoptIQ/TACTrack/`)  
**Written:** 2026-06-04 (after live AdoptIQ Build 100 → 101 auto-update test)

---

## Goal

Add in-app auto-update to TACTrack comparable to AdoptIQ **Round 119 / Build 88+** (Tier C: verify → stage → detached swapper → relaunch), **without** sharing AdoptIQ’s `OUTBOX/latest.json` (that causes manifest collisions).

---

## Reference implementation (AdoptIQ repo)

Use these as canonical templates — **port/adapt, do not import across repos**.

| What | AdoptIQ file |
|------|----------------|
| Update engine (stdlib + config only) | `auto_updater.py` |
| HTTP wiring | `app_simple.py` — search `Round 119`, routes `/api/update/status`, `/api/update/apply`, `/api/settings/auto-update-mode`, `start_update_check_worker()` |
| Releases folder resolution | `config.py` — `_resolve_releases_folder()`, `_releases_candidates()` |
| Settings keys | `adoptiq_settings.py` — `auto_update_mode` (`off` \| `notify` \| `auto`), `releases_folder` |
| Manifest writer (merge-aware) | `scripts/write_release_manifest.py` |
| Build integration | `build_mac_dmg.sh` — calls `write_release_manifest.py --platform mac` |
| UI banner | `templates/base.html` + `static/js/r68_restart_banner.js` |
| Preferences UI | `templates/preferences.html` + `static/js/r119_auto_update.js` |
| Tests | `tests/test_round119_auto_update_*.py`, `tests/test_round128_relaunch_update_banner.py`, `tests/test_round125_settings_and_manifest.py` |
| Docs | `CLAUDE.md` (Round 119 section), `QUALITY_AUDIT.md` (Round 119 + Round 129.1 manifest collision) |

---

## Safety contract (must preserve)

1. **Never swap an unverified artifact** — sha256 mandatory; macOS `codesign --verify --deep --strict`.
2. **Never auto-update mid-analysis** — idle gate (mirror AdoptIQ `/api/shutdown` running-check).
3. **Any failure → notify banner**, not a half-installed app.
4. **Swapper detached** from `$TMPDIR`, keeps `<AppName>.old` for one boot rollback.
5. **`attempted_builds` only on terminal failures** (Round 125 — retry transient sync misses like `artifact_missing` / `sha256_mismatch`).

---

## Manifest strategy for TACTrack (critical)

**Do NOT write TACTrack into shared `AI Projects/OUTBOX/latest.json`.** AdoptIQ owns that file (schema v1, `mac`/`pc` slots for `AdoptIQ/...` artifacts). TACTrack already has a separate layout:

- `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/TACTrack_Releases/mac/latest.json`
- `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/TACTrack_Releases/windows/latest.json`

Current TACTrack schema is **flat** (not AdoptIQ’s v1):

```json
{
  "version": "1.2",
  "build": 84,
  "platform": "mac",
  "artifact": "TACTrack-v1.2-build84.dmg",
  "sha256": "...",
  "size": 231454496,
  "published_at": "2026-06-04T13:40:15Z",
  "min_supported_build": null,
  "notes": "TACTrack v1.2 build 84"
}
```

AdoptIQ schema v1 (for comparison — **do not share this file with TACTrack**):

```json
{
  "schema": 1,
  "version": "1.0.4",
  "build": 101,
  "channel": "stable",
  "mac": {
    "build": 101,
    "artifact": "AdoptIQ/AdoptIQ-v1.0.4-build101.dmg",
    "sha256": "...",
    "size_bytes": 1296007509,
    "version": "1.0.4",
    "released_at_utc": "..."
  },
  "pc": { "...": "..." }
}
```

**Pick one approach:**

- **A (recommended):** Keep TACTrack’s flat schema; teach a ported `auto_updater.py` to read `TACTrack_Releases/<platform>/latest.json` and resolve artifacts relative to that folder (or `OUTBOX/TACTrack/`).
- **B:** Adopt AdoptIQ schema v1 but use a **dedicated** manifest path, e.g. `TACTrack_Releases/latest.json` with TACTrack-only slots — never the shared OUTBOX root file.

---

## TACTrack repo locations (this machine)

| Path | Purpose |
|------|---------|
| `AI Projects/Staging/TACTrack_MAC/` | Primary staging source |
| `~/AdoptIQ/TACTrack/` | Possible fork/copy — confirm which is canonical |
| `AI Projects/TACTrack_Releases/mac/` | Mac release manifest + metadata |
| `AI Projects/TACTrack_Releases/windows/` | Windows release manifest |
| `AI Projects/OUTBOX/TACTrack/` | DMG artifacts (shared folder) |
| `AI Projects/OUTBOX/TACTrack_PC/` | Windows EXE artifacts |

**Do not clobber `AI Projects/OUTBOX/latest.json`** — that is AdoptIQ’s updater manifest.

---

## What TACTrack already has vs lacks

**Borrowed from TACTrack into AdoptIQ (for context only):**

- Startup splash + `/ping` (AdoptIQ Round 97 mirrored TACTrack)
- Report storage patterns (AdoptIQ Round 92)

**TACTrack does NOT have today:**

- No `auto_updater.py`
- No `/api/update/status` or `/api/update/apply`
- No `auto_update_mode` in settings
- `build_mac_dmg.sh` only copies DMG to `OUTBOX/` — no merge-aware manifest writer

---

## Build / publish checklist (after implementation)

1. Wire `build_mac_dmg.sh` / `build_pc.bat` to write manifest under `TACTrack_Releases/` (not shared OUTBOX `latest.json`).
2. Add pytest coverage mirroring AdoptIQ Round 119 tests (offline; `testing=True` short-circuit so pytest never spawns a real swapper).
3. Manual test: run frozen app on build N, publish N+1 to `TACTrack_Releases`, confirm `/api/update/status` → `update_available: true`, apply when idle, verify build bumps.
4. Document in TACTrack README that AdoptIQ and TACTrack must **not** share one `latest.json`.

---

## Known pitfall (live-tested on AdoptIQ, 2026-06-04)

When TACTrack overwrote shared `OUTBOX/latest.json` mac slot with `TACTrack/TACTrack-v1.2-build84.dmg`, AdoptIQ build 100 saw **no update** (84 < 100). Fix was restoring AdoptIQ manifest via `scripts/write_release_manifest.py`. Same issue documented in AdoptIQ `QUALITY_AUDIT.md` Round 129.1.

**Prevention:** TACTrack must never write to `AI Projects/OUTBOX/latest.json`.

---

## Verification commands (adapt for TACTrack)

TACTrack default port is **5150** (AdoptIQ uses 5151).

```bash
curl -s http://127.0.0.1:5150/api/version | python3 -m json.tool
curl -s http://127.0.0.1:5150/api/update/status | python3 -m json.tool
```

**Expect when working:**

- `update_available: true` when manifest `build` > running build
- `releases_folder_found: true`
- After apply (idle, frozen `.app`): running build matches manifest; `update_available: false`

**Apply (UI or API):**

- Analyze page banner: “Relaunch to update”
- Or `POST /api/update/apply` with CSRF (mirror AdoptIQ `static/js/r68_restart_banner.js`)

---

## Suggested implementation order

1. Copy/adapt `auto_updater.py` — point manifest reader at `TACTrack_Releases/<platform>/latest.json` (flat schema) or add a thin adapter.
2. Wire routes in `app.py` (status, apply, settings mode) + idle gate for TACTrack’s processing sessions.
3. Add `releases_folder` + `auto_update_mode` to TACTrack settings (mirror `adoptiq_settings.py` patterns).
4. Add manifest writer to build scripts; publish only under `TACTrack_Releases/`.
5. Port UI: banner + Preferences card (adapt from AdoptIQ JS; use port 5150).
6. Port tests from `tests/test_round119_auto_update_*.py`.
7. Run `make verify` / pytest; manual frozen-app test N → N+1.

---

**Trailer:** Made-with: Cursor (AdoptIQ session handoff)
