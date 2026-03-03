# AdoptIQ v1.0.1 — Restore Point

**Date:** February 2026  
**Version:** 1.0.1 (Build 1)

This document marks the v1.0.1 restore point for the AdoptIQ Windows app.

## What's in v1.0.1

- **Reports:** Comprehensive, Compact, Renewal (single/portfolio), Leader — all four use shared data sources (Snowflake, CSOne, CSConsole, external intel, software defects, PSIRT).
- **TAC & BEMS:** TAC cases and BEMS escalations are analyzed in every report (CSOne; BEMS via Transaction ID and bemscsc_refs).
- **CSOne flow:** “Access latest AdoptIQ Export from CSOne” link opens SharePoint; user downloads .xlsx and uses Browse to select file.
- **Leader report:** Word doc includes External Intelligence (defects, PSIRT, incidents); Excel includes External_Bugs, External_Incidents, Software_Defects, PSIRT_Vulnerabilities.
- **Validation:** Centralized data source validation; TAC/BEMS documented in `data_source_validator.py`.

## Restore to this point

```bash
git checkout v1.0.1
```

Or create a branch from this tag:

```bash
git checkout -b branch-from-v1.0.1 v1.0.1
```

## Build

- **Version/build:** Set in `config.py` (`ADOPTIQ_VERSION`, `ADOPTIQ_BUILD`) and `build_pc.bat`.
- **Build exe:** Run `build_pc.bat`; output in `OUTBOX\AdoptIQ.exe`.
