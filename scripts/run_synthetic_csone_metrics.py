#!/usr/bin/env python3
"""Load checked-in synthetic CSOne through canonical_metrics. Round 169.2.

Widens the offline sim from "workbook exists" to "loader + SSoT counts".
Uses only ``testdata/synthetic_csone`` (Acme / Beta / Gamma,
``@example.invalid``). Never a live Cisco export. Honesty stamps stay false.
"""
# Round 169.2

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adoptiq_backend import load_csone_excel  # noqa: E402
from canonical_metrics import count_customers, count_total_tac, list_customers  # noqa: E402
from csone_corpus_replay import discover_corpus_workbooks  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "testdata" / "synthetic_csone"
ALLOWED_CUSTOMERS = frozenset(
    {"Acme Corporation", "Beta Industries", "Gamma Public Sector"}
)
SCHEMA_VERSION = "synthetic-csone-metrics/v1"


def run_synthetic_csone_metrics(corpus_dir: Path | None = None) -> dict[str, Any]:
    """Load the newest synthetic workbook and count customers via SSoT."""
    # Round 169.2
    corpus = Path(corpus_dir or DEFAULT_CORPUS)
    workbooks = discover_corpus_workbooks(corpus)
    if not workbooks:
        return {
            "schema_version": SCHEMA_VERSION,
            "round": "169.2",
            "ok": False,
            "detail": "no synthetic CSOne workbooks",
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "release_ready": False,
        }

    latest = workbooks[0]
    loaded = load_csone_excel(latest)
    customers = list_customers(csone_df=loaded)
    customer_count = count_customers(csone_df=loaded)
    tac_count = count_total_tac(loaded)
    emails = []
    if loaded is not None and not loaded.empty and "Current Contact Email" in loaded.columns:
        emails = loaded["Current Contact Email"].fillna("").astype(str).tolist()
    email_ok = all(
        (not value) or value.endswith("@example.invalid") for value in emails
    )
    customer_ok = set(customers) <= ALLOWED_CUSTOMERS and customer_count == len(customers)
    nonempty = loaded is not None and not loaded.empty
    ok = bool(nonempty and customer_ok and email_ok and customer_count >= 1 and tac_count >= 1)
    return {
        "schema_version": SCHEMA_VERSION,
        "round": "169.2",
        "ok": ok,
        "workbook": latest.name,
        "row_count": 0 if loaded is None else int(len(loaded)),
        "customer_count": int(customer_count),
        "customers": customers,
        "emails": [value for value in emails if value],
        "tac_count": int(tac_count),
        "email_domain_ok": email_ok,
        "synthetic_only": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON (default; accepted so callers can pass --json)",
    )
    args = parser.parse_args(argv)
    payload = run_synthetic_csone_metrics(args.corpus_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
