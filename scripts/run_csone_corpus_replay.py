#!/usr/bin/env python3
"""Validate production CSOne loading and build a safe real-shape replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import adoptiq_backend as backend  # noqa: E402
import canonical_metrics as canonical  # noqa: E402
from data_normalization import add_case_lifecycle_fields  # noqa: E402
from csone_corpus_replay import (  # noqa: E402
    replay_bundle_from_corpus,
    validate_representative_loaders,
)
from local_acceptance_lab import SOURCE_MODE, build_scenario_bundle  # noqa: E402


def run_replay(corpus_dir: Path, *, max_rows: int) -> dict[str, Any]:
    bundle = build_scenario_bundle("multi_manager")
    loader_contract = validate_representative_loaders(
        corpus_dir,
        loader=backend.load_csone_excel,
    )
    replayed = replay_bundle_from_corpus(
        bundle,
        corpus_dir,
        loader=backend.load_csone_excel,
        max_rows=max_rows,
    )
    tac = replayed.frame("tac_cases")
    bems = replayed.frame("bems_cases")
    normalized_tac = add_case_lifecycle_fields(
        tac,
        as_of=replayed.as_of_utc,
    )
    operating_health = canonical.tac_operating_health(
        normalized_tac,
        as_of=replayed.as_of_utc,
    )
    joined_text = "\n".join(
        tac.fillna("").astype(str).head(min(len(tac), 1000)).to_numpy().ravel()
    )
    safe_domains_only = all(
        "@" not in value
        or value.endswith("@example.invalid")
        for value in tac.fillna("").astype(str).to_numpy().ravel()
        if "@" in value
    )
    pseudonym_contract = bool(
        tac.attrs.get("corpus_replay") is True
        and tac.attrs.get("raw_values_retained") is False
        and tac.attrs.get("source_mode") == SOURCE_MODE
        and tac.attrs.get("live_validation_performed") is False
        and safe_domains_only
        and "Pseudonymized" in joined_text
        and tac["SR Number"].fillna("").astype(str).str.match(
            r"^(?:SIM-SR-\d{6})?$"
        ).all()
        and tac["Transaction ID"].fillna("").astype(str).str.match(
            r"^(?:SIM-TXN-\d{6})?$"
        ).all()
    )
    all_passed = bool(
        loader_contract["all_nonempty"]
        and loader_contract["no_footer_rows_remaining"]
        and loader_contract["consistent_schema"]
        and pseudonym_contract
        and len(tac) > 0
    )
    return {
        "schema_version": "csone-corpus-replay/v1",
        "sanitized": True,
        "do_not_commit": True,
        "source_mode": SOURCE_MODE,
        "live_snowflake_validation_performed": False,
        "source_rows_exported": False,
        "source_values_exported": False,
        "raw_values_retained": False,
        "production_accuracy_claimed": False,
        "all_passed": all_passed,
        "loader_contract": loader_contract,
        "replay": {
            "row_count": int(len(tac)),
            "bems_row_count": int(len(bems)),
            "source_row_count": int(tac.attrs.get("source_row_count") or 0),
            "excluded_non_record_rows": int(
                tac.attrs.get("excluded_non_record_rows") or 0
            ),
            "pseudonym_contract_ok": pseudonym_contract,
            "missing_record_id_rows": int(
                tac["SR Number"].fillna("").astype(str).str.strip().eq("").sum()
            ),
            "missing_status_rows": int(
                tac["Case Status"].fillna("").astype(str).str.strip().eq("").sum()
            ),
            "case_type_distribution": dict(
                tac.attrs.get("case_type_distribution") or {}
            ),
            "case_type_distribution_reconciled": bool(
                tac.attrs.get("case_type_distribution_reconciled") is True
            ),
            # Aggregate-only evidence that the real-shape replay exercises the
            # manager-facing support operating-health path.  No case value or
            # row identity leaves memory.
            "operating_health": {
                "available": operating_health is not None,
                "closed_case_count": int(
                    (operating_health or {}).get("closed_case_count") or 0
                ),
                "close_time_median_days": (operating_health or {}).get(
                    "close_time_median_days"
                ),
                "close_time_p90_days": (operating_health or {}).get(
                    "close_time_p90_days"
                ),
                "ownership_observed_count": int(
                    (operating_health or {}).get("ownership_observed_count") or 0
                ),
                "ownership_churn_count": int(
                    (operating_health or {}).get("ownership_churn_count") or 0
                ),
                "ownership_churn_rate_percent": (operating_health or {}).get(
                    "ownership_churn_rate_percent"
                ),
            },
            "frame_sha256": hashlib.sha256(
                tac.to_json(orient="split", date_format="iso").encode("utf-8")
            ).hexdigest(),
        },
    }


def _write_summary(path: Path, payload: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    if REPO_ROOT == resolved or REPO_ROOT in resolved.parents:
        relative = resolved.relative_to(REPO_ROOT)
        if not str(relative).startswith(".adoptiq-acceptance/"):
            raise ValueError("corpus replay summaries inside the repo must use .adoptiq-acceptance")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the privacy-preserving CSOne corpus replay contract.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--max-rows", type=int, default=600)
    parser.add_argument(
        "--summary",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "csone-corpus-replay" / "summary.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= int(args.max_rows) <= 10000:
        raise ValueError("--max-rows must be between 1 and 10000")
    payload = run_replay(args.input_dir.expanduser().resolve(), max_rows=args.max_rows)
    _write_summary(args.summary, payload)
    print(
        json.dumps(
            {
                "all_passed": payload["all_passed"],
                "representative_workbook_count": payload["loader_contract"][
                    "representative_workbook_count"
                ],
                "replay_row_count": payload["replay"]["row_count"],
                "summary": str(args.summary.expanduser().resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if payload["all_passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
