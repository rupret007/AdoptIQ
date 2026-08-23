#!/usr/bin/env python3
"""Validate production CSOne loading and build a safe real-shape replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import adoptiq_backend as backend  # noqa: E402
import canonical_metrics as canonical  # noqa: E402
from data_normalization import add_case_lifecycle_fields  # noqa: E402
from csone_corpus_replay import (  # noqa: E402
    apply_prepared_csone_replay,
    prepare_csone_replay,
    prepared_replay_summary,
)
from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    SOURCE_MODE,
    build_scenario_bundle,
)


def run_replay(
    corpus_dir: Path,
    *,
    max_rows: int,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    prepared_sink: Callable[[bytes], None] | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    bundle = build_scenario_bundle("multi_manager", manifest_path)
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    prepared = prepare_csone_replay(
        bundle,
        corpus_dir,
        loader=backend.load_csone_excel,
        max_rows=max_rows,
        manifest_sha256=manifest_sha256,
        strict_breadth=True,
    )
    replayed = apply_prepared_csone_replay(
        bundle,
        prepared,
        manifest_sha256=manifest_sha256,
    )
    tac = replayed.frame("tac_cases")
    bems = replayed.frame("bems_cases")
    loader_contract = dict(tac.attrs.get("corpus_loader_contract") or {})
    normalized_tac = add_case_lifecycle_fields(
        tac,
        as_of=replayed.as_of_utc,
    )
    operating_health = canonical.tac_operating_health(
        normalized_tac,
        as_of=replayed.as_of_utc,
    )
    privacy_contract = dict(tac.attrs.get("privacy_contract") or {})
    raw_values_retained = tac.attrs.get("raw_values_retained") is not False
    safe_values = tac.astype(object).where(tac.notna(), "").astype(str)
    joined_text = "\n".join(
        safe_values.head(min(len(tac), 1000)).to_numpy().ravel()
    )
    safe_domains_only = all(
        "@" not in value
        or value.endswith("@example.invalid")
        for value in safe_values.to_numpy().ravel()
        if "@" in value
    )
    pseudonym_contract = bool(
        tac.attrs.get("corpus_replay") is True
        and not raw_values_retained
        and privacy_contract.get("validated") is True
        and privacy_contract.get("raw_values_retained") is False
        and privacy_contract.get("row_count") == len(tac)
        and privacy_contract.get("column_count") == len(tac.columns)
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
    corpus_coverage = dict(tac.attrs.get("corpus_replay_coverage") or {})
    status_coverage = dict(tac.attrs.get("tac_status_coverage") or {})
    all_passed = bool(
        loader_contract["all_nonempty"]
        and loader_contract["no_footer_rows_remaining"]
        and loader_contract["consistent_schema"]
        and loader_contract["breadth_ok"]
        and corpus_coverage.get("breadth_ok") is True
        and pseudonym_contract
        and len(tac) > 0
    )
    summary = prepared_replay_summary(prepared)
    summary.update(
        {
            "source_mode": SOURCE_MODE,
            "raw_values_retained": raw_values_retained,
            "all_passed": all_passed,
        }
    )
    summary["replay"].update(
        {
            "row_count": int(len(tac)),
            "bems_row_count": int(len(bems)),
            "source_row_count": int(tac.attrs.get("source_row_count") or 0),
            "excluded_non_record_rows": int(
                tac.attrs.get("excluded_non_record_rows") or 0
            ),
            "pseudonym_contract_ok": pseudonym_contract,
            "privacy_contract": privacy_contract,
            "missing_record_id_rows": int(
                tac["SR Number"].fillna("").astype(str).str.strip().eq("").sum()
            ),
            # Missing source values and populated-but-unclassified values both
            # normalize to ``Unknown``.  Keep both exact aggregates instead of
            # falsely reporting zero after canonicalization.
            "missing_status_rows": int(
                status_coverage.get("normalized_unknown_count") or 0
            ),
            "raw_missing_status_rows": int(
                status_coverage.get("raw_missing_count") or 0
            ),
            "status_coverage": status_coverage,
            "status_coverage_sha256": str(
                tac.attrs.get("status_coverage_sha256") or ""
            ),
            "case_type_distribution": dict(
                tac.attrs.get("case_type_distribution") or {}
            ),
            "case_type_distribution_reconciled": bool(
                tac.attrs.get("case_type_distribution_reconciled") is True
            ),
            "corpus_coverage": corpus_coverage,
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
        }
    )
    if prepared_sink is not None:
        if not all_passed:
            raise ValueError("refusing to emit a prepared replay from a failed gate")
        prepared_sink(prepared.payload)
    return summary


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
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--prepared-stdout",
        action="store_true",
        help="Emit only canonical prepared JSON bytes on stdout; diagnostics use stderr.",
    )
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
    prepared_payloads: list[bytes] = []
    if args.prepared_stdout:
        with redirect_stdout(sys.stderr):
            payload = run_replay(
                args.input_dir.expanduser().resolve(),
                max_rows=args.max_rows,
                manifest_path=args.manifest,
                prepared_sink=prepared_payloads.append,
            )
    else:
        payload = run_replay(
            args.input_dir.expanduser().resolve(),
            max_rows=args.max_rows,
            manifest_path=args.manifest,
        )
    _write_summary(args.summary, payload)
    if args.prepared_stdout:
        if len(prepared_payloads) != 1:
            raise ValueError("prepared replay producer did not emit exactly once")
        sys.stdout.buffer.write(prepared_payloads[0])
        sys.stdout.buffer.flush()
        return 0 if payload["all_passed"] else 5
    print(
        json.dumps(
            {
                "all_passed": payload["all_passed"],
                "representative_workbook_count": payload["loader_contract"][
                    "representative_workbook_count"
                ],
                "replay_row_count": payload["replay"]["row_count"],
                "privacy_contract_ok": payload["replay"]["pseudonym_contract_ok"],
            },
            sort_keys=True,
        )
    )
    return 0 if payload["all_passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
