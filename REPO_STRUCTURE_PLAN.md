# AdoptIQ Repository Structure Plan

This plan captures the recommended path for improving repository organization without disrupting the current flat-module runtime, PyInstaller packaging, or the 5,500+ regression-test floor.

## Current Shape

- AdoptIQ is a flat Python desktop app: root-level modules, no application package hierarchy, Flask entry points in `app_simple.py` and `enhanced_admin_dashboard_v2.py`, and PyInstaller specs that assume this layout.
- Tests are organized mostly by historical round (`tests/test_roundNN_*.py`) plus domain fixtures and Ask AI eval assets. That convention is useful for audit traceability and should not be renamed casually.
- Root docs are long and round-accretive. `README.md` is operator-facing, `CLAUDE.md` is the invariant guide, and `QUALITY_AUDIT.md` is the Cursor-to-Claude audit journal.
- Runtime and generated artifacts are excluded by `.gitignore`; committed baselines under `baselines/` and replay cassettes under `tests/ask_ai_eval/` are intentional test assets.

## Principles

- Keep product changes and repository moves separate. A structural migration should not share a commit with report logic, corpus behavior, or build changes.
- Preserve import compatibility until PyInstaller, tests, and local launch scripts have been updated together.
- Prefer documentation and ignore hygiene before moving files.
- Keep the round-based test convention unless a later migration provides an automated mapping from old round tests to new domain folders.

## Phased Migration

### Phase 1: Artifact And Planning Hygiene

- Keep `.cursor/plans/` ignored so local Cursor plan files do not become product docs by accident.
- Continue excluding build/runtime outputs: `build/`, `dist/`, `OUTBOX/`, `bake/`, `outputs/`, `uploads/`, `*.dmg`, `*.exe`, `*.db`, and local audit scratch files.
- Do not ignore `baselines/` or `tests/ask_ai_eval/cassettes/`; those are committed verification assets.

### Phase 2: Documentation Index

- Add a lightweight docs index before moving historical files.
- Keep `README.md`, `CLAUDE.md`, `QUALITY_AUDIT.md`, and platform build guides at the root until links and references are audited.
- If root cleanup is needed, move frozen `CODE_REVIEW*.md` files to a `docs/archive/` folder in a docs-only commit.

### Phase 3: Stable Architecture Docs

- Extract durable architecture material from `CLAUDE.md` into focused docs such as `docs/architecture.md`, `docs/corpus.md`, `docs/report-storage.md`, and `docs/build-release.md`.
- Leave round-specific lessons and critical rules in `CLAUDE.md`; use extracted docs for stable onboarding content.
- Update root docs to point to the extracted pages instead of duplicating long sections.

### Phase 4: Package Layout Feasibility

- Prototype a `src/adoptiq/` package in a branch or worktree only after the docs index exists.
- Add import shims or compatibility modules so existing flat imports keep working during transition.
- Update PyInstaller hidden imports, specs, test `sys.path` setup, local run commands, and build scripts in the same migration branch.
- Run `make verify`, a macOS build, and a launch smoke before considering merge.

### Phase 5: Monolith Splits Last

- Split `app_simple.py`, `adoptiq_backend.py`, and other large modules only after the package layout is proven.
- Extract by stable domain boundary first: settings/resolvers, corpus, report outputs, routes, report builders, and admin diagnostics.
- Keep public route names, generated file names, report schema, and helper APIs stable unless a migration explicitly replaces them.

## Do Not Do In Opportunistic Commits

- Do not move root Python modules into a package while also changing report or corpus behavior.
- Do not rename round-based test files as drive-by cleanup.
- Do not move PyInstaller specs or build scripts without a release build verification plan.
- Do not remove historical audit content from `QUALITY_AUDIT.md`; append new handoffs instead.

## Verification For Any Structural Phase

- Run targeted tests for touched domains first.
- Run `make verify`.
- For packaging or import-path changes, run a macOS build and app-launch smoke.
- Document remaining risks in `QUALITY_AUDIT.md` before commit.
