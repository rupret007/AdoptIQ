#!/usr/bin/env python3
"""Validate Round 145's guarded, sanitized local acceptance scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    SCHEMA_VERSION,
    LocalAcceptanceError,
    LocalAcceptanceSafetyError,
    assert_safe_activation,
    build_scenario_bundle,
    load_manifest,
    validate_all_scenarios,
)


def _safe_summary_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != ".adoptiq-acceptance":
        raise LocalAcceptanceSafetyError(
            "in-repository summaries must stay under .adoptiq-acceptance"
        )
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate sanitized local AdoptIQ acceptance data; never live validation.",
    )
    parser.add_argument(
        "--enable-local-fixtures",
        action="store_true",
        help="Required explicit acknowledgement that synthetic fixtures will be used.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--scenario", default="all")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--summary-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        assert_safe_activation(
            explicit=bool(args.enable_local_fixtures),
            host=str(args.host),
        )
        manifest = load_manifest(args.manifest)
        if args.scenario == "all":
            scenarios = validate_all_scenarios(args.manifest)
        else:
            scenarios = {
                str(args.scenario): build_scenario_bundle(
                    str(args.scenario),
                    args.manifest,
                ).redacted_summary()
            }
        summary = {
            "schema_version": SCHEMA_VERSION,
            "sanitized": True,
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "manifest_schema_fingerprint": manifest["schema_fingerprint"],
            "all_reconciled": True,
            "scenario_count": len(scenarios),
            "scenarios": scenarios,
        }
        if args.summary_path:
            output = _safe_summary_path(args.summary_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (LocalAcceptanceError, LocalAcceptanceSafetyError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "all_reconciled": False,
                    "error_kind": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
