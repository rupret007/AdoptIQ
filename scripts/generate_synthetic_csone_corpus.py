#!/usr/bin/env python3
"""Build a checked-in synthetic CSOne-shaped corpus from Round 145 fixtures.

This is not a real CSOne export and is not live-source evidence.  It only
projects the already-committed sanitized local-acceptance TAC rows
(Acme / Beta / Gamma, ``@example.invalid``) into the production workbook
shape so Cloud/Bob can exercise ``load_csone_excel`` and the
privacy-preserving replay without Jeff's work-Mac folder.

Bake/mint scripts cannot produce CSOne workbooks (they seal the Ask AI
knowledge corpus).  Do not replace this generator with ``bake_corpus.py``
or ``mint_corpus_sentinel.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_acceptance_lab import SOURCE_MODE, build_scenario_bundle  # noqa: E402

DEFAULT_OUTPUT_DIR = REPO_ROOT / "testdata" / "synthetic_csone"
SCHEMA_VERSION = "synthetic-csone-corpus/v1"
# Round 168: production-shaped headers only. Values stay fixture-owned.
CSONE_COLUMNS = (
    "SR Number",
    "Case Number",
    "Title",
    "Severity",
    "Case Status",
    "Date/Time Opened",
    "Date/Time Closed",
    "Transaction ID",
    "Tech.",
    "Sub Technology",
    "Sub Tech.",
    "Service Tier",
    "Product: Product Name",
    "Highest Priority",
    "Problem Code",
    "Resolution Code",
    "Case Origin",
    "# of Case Owner Changes",
    "Customer",
    "Customer Name: Customer Name",
    "Problem Description",
    "Problem Details",
    "CSE Action Plan",
    "Last Cisco Update",
    "Resolution Summary",
    "Customer Activity",
    "Current Contact Email",
    "Case Owner: Full Name",
    "Subscription Reference Id",
)
TECH_BY_CUSTOMER = {
    "acme corporation": "Webex Calling",
    "beta industries": "Webex Meetings",
    "gamma public sector": "Webex Contact Center",
}
WORKBOOK_NAMES = (
    "AdoptIQ_Synthetic_CSOne_Fixture-2026-08-01-12-00-00.xlsx",
    "AdoptIQ_Synthetic_CSOne_Fixture-2026-08-02-12-00-00.xlsx",
    "AdoptIQ_Synthetic_CSOne_Fixture-2026-08-03-12-00-00.xlsx",
)


def _clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _tech_for_row(row: pd.Series) -> str:
    for column in ("Tech.", "Technology", "Sub Technology", "Sub Tech."):
        raw = _clean(row.get(column))
        if raw:
            return raw
    customer = _clean(row.get("Customer") or row.get("BU_NAME")).casefold()
    return TECH_BY_CUSTOMER.get(customer, "Unclassified technology")


def project_tac_to_csone_shape(tac: pd.DataFrame) -> pd.DataFrame:
    """Project sanitized fixture TAC rows into the CSOne export column set."""

    rows: list[dict[str, str]] = []
    for _index, row in tac.iterrows():
        sr = _clean(row.get("SR Number") or row.get("Case Number"))
        customer = _clean(row.get("Customer") or row.get("BU_NAME"))
        tech = _tech_for_row(row)
        sub_tech = _clean(row.get("Sub Technology") or row.get("Sub Tech.")) or tech
        title = _clean(row.get("Title"))
        problem = _clean(row.get("Problem Description"))
        rows.append(
            {
                "SR Number": sr,
                "Case Number": sr,
                "Title": title,
                "Severity": _clean(row.get("Severity")),
                "Case Status": _clean(row.get("Case Status") or row.get("status")),
                "Date/Time Opened": _clean(row.get("Date/Time Opened")),
                "Date/Time Closed": _clean(row.get("Date/Time Closed")),
                "Transaction ID": _clean(row.get("Transaction ID")),
                "Tech.": tech,
                "Sub Technology": sub_tech,
                "Sub Tech.": sub_tech,
                "Service Tier": _clean(row.get("Service Tier")) or "Standard",
                "Product: Product Name": tech,
                "Highest Priority": _clean(row.get("Severity")),
                "Problem Code": "",
                "Resolution Code": "",
                "Case Origin": "Synthetic fixture",
                "# of Case Owner Changes": _clean(
                    row.get("# of Case Owner Changes") or 0
                )
                or "0",
                "Customer": customer,
                "Customer Name: Customer Name": customer,
                "Problem Description": problem,
                "Problem Details": problem,
                "CSE Action Plan": "",
                "Last Cisco Update": "",
                "Resolution Summary": "",
                "Customer Activity": "",
                "Current Contact Email": "fixture.contact1@example.invalid",
                "Case Owner: Full Name": _clean(row.get("FIXTURE_MEMBER")),
                "Subscription Reference Id": _clean(
                    row.get("Subscription Reference Id")
                    or row.get("SUBSCRIPTION_ID")
                    or row.get("ACCOUNT_ID_C")
                ),
            }
        )
    frame = pd.DataFrame(rows, columns=list(CSONE_COLUMNS))
    frame.attrs.update(
        {
            "sanitized": True,
            "source_mode": SOURCE_MODE,
            "live_validation_performed": False,
            "synthetic_csone": True,
        }
    )
    return frame


def footer_rows() -> list[dict[str, str]]:
    """Non-record export chrome that the production loader must drop."""

    empty = {column: "" for column in CSONE_COLUMNS}
    total = dict(empty)
    total["Title"] = ""
    total["Case Origin"] = "Grand Total"
    spacer = dict(empty)
    return [spacer, total]


def write_csone_workbook(path: Path, records: pd.DataFrame) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cases"
    sheet.append(list(CSONE_COLUMNS))
    for row in records.to_dict(orient="records"):
        sheet.append([row.get(column, "") for column in CSONE_COLUMNS])
    for footer in footer_rows():
        sheet.append([footer.get(column, "") for column in CSONE_COLUMNS])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()


def write_manifest(output_dir: Path, workbook_names: tuple[str, ...], row_count: int) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sanitized": True,
        "synthetic": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "release_ready": False,
        "source": "local_acceptance_lab.healthy.tac_cases",
        "source_mode": SOURCE_MODE,
        "customer_names": [
            "Acme Corporation",
            "Beta Industries",
            "Gamma Public Sector",
        ],
        "email_domain": "example.invalid",
        "workbook_count": len(workbook_names),
        "record_rows_per_workbook": row_count,
        "footer_rows_per_workbook": len(footer_rows()),
        "workbooks": list(workbook_names),
        "notes": (
            "Fixture-projected CSOne shape for Cloud/Bob loader+replay only. "
            "Not a real Cisco export. Never use this path to claim live accuracy."
        ),
    }
    dest = output_dir / "MANIFEST.json"
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def generate_synthetic_csone_corpus(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    scenario: str = "healthy",
) -> dict[str, Any]:
    bundle = build_scenario_bundle(scenario)
    projected = project_tac_to_csone_shape(bundle.frame("tac_cases"))
    if projected.empty:
        raise ValueError("synthetic CSOne projection produced no fixture rows")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name in WORKBOOK_NAMES:
        dest = output_dir / name
        write_csone_workbook(dest, projected)
        written.append(name)
    manifest = write_manifest(output_dir, WORKBOOK_NAMES, int(len(projected)))
    readme = output_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Synthetic CSOne corpus (Round 168)\n\n"
            "Generated from Round 145 local-acceptance fixtures. "
            "Not live CSOne. `live_validation_performed=false`.\n",
            encoding="utf-8",
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "sanitized": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "output_dir": str(output_dir),
        "workbooks": written,
        "record_rows_per_workbook": int(len(projected)),
        "manifest": str(manifest),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a fixture-only synthetic CSOne corpus for Cloud/Bob."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--scenario", default="healthy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = generate_synthetic_csone_corpus(args.output_dir, scenario=str(args.scenario))
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
