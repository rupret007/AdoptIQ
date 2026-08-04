"""Guarded deterministic data lab for local AdoptIQ runtime acceptance.

This module never activates itself.  A caller must explicitly request fixture
activation, target loopback, and run from source (never a frozen application).
The data is synthetic and every returned frame is stamped accordingly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = (REPO_ROOT / "tests" / "fixtures").resolve()
DEFAULT_MANIFEST_PATH = (
    FIXTURE_ROOT / "local_acceptance" / "v1" / "manifest.json"
)
SCHEMA_VERSION = "local_acceptance/v1"
SOURCE_MODE = "local_acceptance_fixture"
LOCAL_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_SOURCE_STATES = frozenset(
    {"available", "zero", "partial", "stale", "failed", "unavailable", "truncated"}
)
ALLOWED_PROVIDER_STATES = frozenset(
    {"available", "timeout", "rate_limited", "unavailable", "malformed"}
)
REQUIRED_SCENARIOS = frozenset(
    {
        "healthy",
        "true_zero",
        "partial",
        "stale",
        "failed",
        "unavailable",
        "truncated",
        "duplicate_records",
        "ambiguous_customer",
        "conflicting_mapping",
        "missing_id",
        "malformed_value",
        "timezone_boundary",
        "multicurrency",
        "large_volume",
        "long_text",
        "prompt_injection",
        "provider_timeout",
        "provider_rate_limit",
        "provider_unavailable",
        "provider_malformed",
    }
)
MUTATION_KINDS = frozenset(
    {"clear", "take", "duplicate_first", "add", "set", "repeat_to", "append_text"}
)


class LocalAcceptanceError(ValueError):
    """The local acceptance contract is malformed or cannot be reconciled."""


class LocalAcceptanceSafetyError(RuntimeError):
    """Fixture activation was attempted outside the explicit safe boundary."""


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_schema_fingerprint(datasets: Mapping[str, Any]) -> str:
    """Hash only the declared schemas/lineage, never fixture record values."""

    projection: dict[str, Any] = {}
    for name, raw_spec in sorted(datasets.items()):
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        projection[str(name)] = {
            "origin": str(spec.get("origin") or ""),
            "primary_key": str(spec.get("primary_key") or ""),
            "required_columns": [str(item) for item in spec.get("required_columns") or []],
        }
    return _canonical_digest(projection)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LocalAcceptanceError(f"{label} must be an object")
    return value


def _resolve_fixture_path(path: Path, *, relative_to: Path | None = None) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raw = (relative_to or REPO_ROOT) / raw
    resolved = raw.expanduser().resolve()
    if resolved != FIXTURE_ROOT and FIXTURE_ROOT not in resolved.parents:
        raise LocalAcceptanceError("local acceptance files must stay under tests/fixtures")
    return resolved


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    """Load and fully validate the versioned local acceptance manifest."""

    manifest_path = _resolve_fixture_path(Path(path))
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalAcceptanceError("local acceptance manifest is unreadable") from exc
    root = _require_mapping(payload, "manifest")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise LocalAcceptanceError("unsupported local acceptance schema version")
    if root.get("sanitized") is not True:
        raise LocalAcceptanceError("local acceptance data must declare sanitized=true")
    try:
        clock = datetime.fromisoformat(
            str(root.get("deterministic_clock_utc") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise LocalAcceptanceError("deterministic_clock_utc must be ISO-8601") from exc
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise LocalAcceptanceError("deterministic_clock_utc must be timezone-aware")

    datasets = _require_mapping(root.get("datasets"), "datasets")
    if not datasets:
        raise LocalAcceptanceError("datasets cannot be empty")
    for name, raw_spec in datasets.items():
        spec = _require_mapping(raw_spec, f"datasets.{name}")
        columns = spec.get("required_columns")
        if not isinstance(columns, list) or not columns or not all(
            isinstance(column, str) and column.strip() for column in columns
        ):
            raise LocalAcceptanceError(f"datasets.{name}.required_columns is invalid")
        if not str(spec.get("origin") or "").strip():
            raise LocalAcceptanceError(f"datasets.{name}.origin is required")
        if not str(spec.get("primary_key") or "").strip():
            raise LocalAcceptanceError(f"datasets.{name}.primary_key is required")
        if not str(spec.get("canonical_metric") or "").strip():
            raise LocalAcceptanceError(f"datasets.{name}.canonical_metric is required")
        canonical_count = spec.get("expected_canonical_count")
        if not isinstance(canonical_count, int) or canonical_count < 0:
            raise LocalAcceptanceError(
                f"datasets.{name}.expected_canonical_count is invalid"
            )
        warnings = spec.get("expected_warning_codes")
        if not isinstance(warnings, list) or not all(
            isinstance(code, str) and code.strip() for code in warnings
        ):
            raise LocalAcceptanceError(
                f"datasets.{name}.expected_warning_codes is invalid"
            )
        lineage = _require_mapping(spec.get("lineage"), f"datasets.{name}.lineage")
        if lineage.get("origin") != spec.get("origin"):
            raise LocalAcceptanceError(f"datasets.{name}.lineage origin mismatch")
        if lineage.get("stable_source_id") != spec.get("primary_key"):
            raise LocalAcceptanceError(
                f"datasets.{name}.lineage stable_source_id mismatch"
            )
        if lineage.get("fixture_record_id") != "LOCAL_ACCEPTANCE_RECORD_ID":
            raise LocalAcceptanceError(
                f"datasets.{name}.lineage fixture_record_id is invalid"
            )
    actual_fingerprint = compute_schema_fingerprint(datasets)
    if root.get("schema_fingerprint") != actual_fingerprint:
        raise LocalAcceptanceError("local acceptance schema fingerprint mismatch")

    base_fixture = _resolve_fixture_path(
        Path(str(root.get("base_fixture") or "")),
        relative_to=manifest_path.parent,
    )
    if not base_fixture.is_file():
        raise LocalAcceptanceError("base_fixture does not exist")

    healthy_counts = _require_mapping(root.get("healthy_expected_counts"), "healthy counts")
    if set(healthy_counts) != set(datasets):
        raise LocalAcceptanceError("healthy counts must cover every dataset exactly")
    if any(not isinstance(value, int) or value < 0 for value in healthy_counts.values()):
        raise LocalAcceptanceError("healthy expected counts must be non-negative integers")

    inline = _require_mapping(root.get("inline_records"), "inline_records")
    for name, rows in inline.items():
        if name not in datasets:
            raise LocalAcceptanceError(f"inline_records references unknown dataset: {name}")
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise LocalAcceptanceError(f"inline_records.{name} must be an array of objects")
    supplemental = _require_mapping(
        root.get("supplemental_records"), "supplemental_records"
    )
    for name, rows in supplemental.items():
        if name not in datasets:
            raise LocalAcceptanceError(
                f"supplemental_records references unknown dataset: {name}"
            )
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise LocalAcceptanceError(
                f"supplemental_records.{name} must be an array of objects"
            )

    scenarios = _require_mapping(root.get("scenarios"), "scenarios")
    missing = REQUIRED_SCENARIOS.difference(scenarios)
    if missing:
        raise LocalAcceptanceError(f"required scenarios are missing: {', '.join(sorted(missing))}")
    for scenario_name, raw_scenario in scenarios.items():
        scenario = _require_mapping(raw_scenario, f"scenarios.{scenario_name}")
        provider_state = str(scenario.get("provider_state") or "")
        if provider_state not in ALLOWED_PROVIDER_STATES:
            raise LocalAcceptanceError(
                f"scenarios.{scenario_name}.provider_state is invalid"
            )
        state_overrides = _require_mapping(
            scenario.get("source_state_overrides"),
            f"scenarios.{scenario_name}.source_state_overrides",
        )
        count_overrides = _require_mapping(
            scenario.get("expected_count_overrides"),
            f"scenarios.{scenario_name}.expected_count_overrides",
        )
        canonical_overrides = _require_mapping(
            scenario.get("expected_canonical_count_overrides"),
            f"scenarios.{scenario_name}.expected_canonical_count_overrides",
        )
        warning_overrides = _require_mapping(
            scenario.get("expected_warning_overrides"),
            f"scenarios.{scenario_name}.expected_warning_overrides",
        )
        provider_warning = scenario.get("provider_warning_code")
        if provider_warning is not None and not (
            isinstance(provider_warning, str) and provider_warning.strip()
        ):
            raise LocalAcceptanceError(
                f"scenarios.{scenario_name}.provider_warning_code is invalid"
            )
        for dataset, state in state_overrides.items():
            if dataset not in datasets or state not in ALLOWED_SOURCE_STATES:
                raise LocalAcceptanceError(
                    f"scenarios.{scenario_name} has invalid source-state override"
                )
        for dataset, count in count_overrides.items():
            if dataset not in datasets or not isinstance(count, int) or count < 0:
                raise LocalAcceptanceError(
                    f"scenarios.{scenario_name} has invalid expected-count override"
                )
        for dataset, count in canonical_overrides.items():
            if dataset not in datasets or not isinstance(count, int) or count < 0:
                raise LocalAcceptanceError(
                    f"scenarios.{scenario_name} has invalid canonical-count override"
                )
        for dataset, codes in warning_overrides.items():
            if dataset not in datasets or not isinstance(codes, list) or not all(
                isinstance(code, str) and code.strip() for code in codes
            ):
                raise LocalAcceptanceError(
                    f"scenarios.{scenario_name} has invalid warning override"
                )
        mutations = scenario.get("mutations")
        if not isinstance(mutations, list):
            raise LocalAcceptanceError(f"scenarios.{scenario_name}.mutations must be an array")
        for mutation in mutations:
            item = _require_mapping(mutation, f"scenarios.{scenario_name}.mutation")
            if item.get("kind") not in MUTATION_KINDS:
                raise LocalAcceptanceError(
                    f"scenarios.{scenario_name} has unsupported mutation kind"
                )
            if item.get("dataset") not in datasets:
                raise LocalAcceptanceError(
                    f"scenarios.{scenario_name} mutation references unknown dataset"
                )
    return dict(payload)


def assert_safe_activation(
    *,
    explicit: bool,
    host: str,
    frozen: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail closed unless fixture activation is explicit, source-only, and local."""

    env = environ if environ is not None else os.environ
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if not explicit:
        raise LocalAcceptanceSafetyError("--enable-local-fixtures is required")
    if is_frozen:
        raise LocalAcceptanceSafetyError("local acceptance fixtures are forbidden when frozen")
    if str(host or "").strip().casefold() not in LOCAL_LOOPBACK_HOSTS:
        raise LocalAcceptanceSafetyError("local acceptance must bind to loopback")
    if str(env.get("ADOPTIQ_BIND_PUBLIC") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise LocalAcceptanceSafetyError("public-bind mode is incompatible with local fixtures")
    if str(env.get("ADOPTIQ_ENV") or "").strip().casefold() in {"prod", "production"}:
        raise LocalAcceptanceSafetyError("production environment forbids local fixtures")


@dataclass
class LocalAcceptanceBundle:
    """One fully materialized and reconciled synthetic runtime scenario."""

    scenario: str
    as_of_utc: str
    provider_state: str
    schema_fingerprint: str
    frames: dict[str, pd.DataFrame]
    expected_counts: dict[str, int]
    expected_canonical_counts: dict[str, int]
    warning_codes: dict[str, tuple[str, ...]]
    source_states: dict[str, str]
    provider_warning_code: str | None

    def frame(self, dataset: str) -> pd.DataFrame:
        if dataset not in self.frames:
            raise KeyError(dataset)
        result = self.frames[dataset].copy(deep=True)
        result.attrs.update(self.frames[dataset].attrs)
        return result

    def records(self, dataset: str) -> list[dict[str, Any]]:
        return self.frame(dataset).where(pd.notna(self.frame(dataset)), None).to_dict(
            orient="records"
        )

    def assert_reconciled(self) -> None:
        if set(self.frames) != set(self.expected_counts):
            raise LocalAcceptanceError("materialized dataset inventory does not reconcile")
        for name, expected in self.expected_counts.items():
            frame = self.frames[name]
            if len(frame) != expected:
                raise LocalAcceptanceError(
                    f"{self.scenario}.{name} count mismatch: {len(frame)} != {expected}"
                )
            if frame.attrs.get("source_mode") != SOURCE_MODE:
                raise LocalAcceptanceError(f"{self.scenario}.{name} is not fixture-stamped")
            if frame.attrs.get("source_state") != self.source_states[name]:
                raise LocalAcceptanceError(f"{self.scenario}.{name} state mismatch")
            primary_key = str(frame.attrs.get("primary_key") or "")
            if not primary_key or primary_key not in frame.columns:
                raise LocalAcceptanceError(
                    f"{self.scenario}.{name} primary-key lineage is missing"
                )
            keys = frame[primary_key].fillna("").astype(str).str.strip()
            canonical_count = int(keys.loc[keys.ne("")].nunique()) + int(
                keys.eq("").sum()
            )
            expected_canonical = self.expected_canonical_counts[name]
            if canonical_count != expected_canonical:
                raise LocalAcceptanceError(
                    f"{self.scenario}.{name} canonical count mismatch: "
                    f"{canonical_count} != {expected_canonical}"
                )
            required_warnings: set[str] = set()
            if keys.loc[keys.ne("")].duplicated().any():
                required_warnings.add("duplicate_source_id")
            if keys.eq("").any():
                required_warnings.add("missing_source_id")
            state = self.source_states[name]
            if state != "available":
                required_warnings.add("true_zero" if state == "zero" else f"source_{state}")
            declared_warnings = set(self.warning_codes[name])
            if not required_warnings.issubset(declared_warnings):
                missing = sorted(required_warnings - declared_warnings)
                raise LocalAcceptanceError(
                    f"{self.scenario}.{name} undeclared warning(s): {', '.join(missing)}"
                )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "sanitized": True,
            "live_validation_performed": False,
            "scenario": self.scenario,
            "as_of_utc": self.as_of_utc,
            "provider_state": self.provider_state,
            "schema_fingerprint": self.schema_fingerprint,
            "counts": dict(sorted(self.expected_counts.items())),
            "canonical_counts": dict(sorted(self.expected_canonical_counts.items())),
            "warning_codes": {
                name: list(codes) for name, codes in sorted(self.warning_codes.items())
            },
            "source_states": dict(sorted(self.source_states.items())),
            "provider_warning_code": self.provider_warning_code,
        }


def _load_base_records(manifest: Mapping[str, Any], manifest_path: Path) -> dict[str, list[dict[str, Any]]]:
    base_path = _resolve_fixture_path(
        Path(str(manifest.get("base_fixture") or "")),
        relative_to=manifest_path.parent,
    )
    try:
        base = json.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalAcceptanceError("base report fixture is unreadable") from exc
    if base.get("sanitized") is not True:
        raise LocalAcceptanceError("base report fixture is not sanitized")
    team_data = _require_mapping(base.get("team_data"), "base team_data")
    records: dict[str, list[dict[str, Any]]] = {
        name: [] for name in manifest["datasets"]
    }
    for member_name, raw_bundle in sorted(team_data.items()):
        bundle = _require_mapping(raw_bundle, f"base team_data.{member_name}")
        for dataset in (
            "subscriptions",
            "action_plans",
            "adoption_barriers",
            "customer_pulse",
            "tac_cases",
            "success_priorities",
        ):
            for raw_row in bundle.get(dataset) or []:
                row = dict(raw_row)
                row["FIXTURE_MEMBER"] = str(member_name)
                records[dataset].append(row)

    customer_by_account: dict[str, str] = {}
    for row in records["subscriptions"]:
        account = str(row.get("ACCOUNT_ID_C") or "").strip()
        customer = str(row.get("BU_NAME") or "").strip()
        if account and customer:
            customer_by_account.setdefault(account, customer)
    for index, (account, customer) in enumerate(sorted(customer_by_account.items()), start=1):
        records["customers"].append(
            {
                "CUSTOMER_ID": f"CUST-{index:03d}",
                "ACCOUNT_ID_C": account,
                "BU_NAME": customer,
            }
        )
    for index, member_name in enumerate(sorted(team_data), start=1):
        records["ownership"].append(
            {
                "OWNER_ID": f"OWNER-{index:03d}",
                "OWNER_NAME": str(member_name),
                "OWNER_EMAIL": f"fixture.owner{index}@example.invalid",
                "MANAGER_NAME": "Local Fixture Manager",
            }
        )
    for row in records["tac_cases"]:
        transaction_id = str(row.get("Transaction ID") or "").strip()
        if transaction_id:
            records["bems_cases"].append(dict(row))
    records["external_incidents"] = [dict(row) for row in base.get("external_incidents") or []]
    records["external_bugs"] = [dict(row) for row in base.get("external_bugs") or []]
    for name, rows in (manifest.get("inline_records") or {}).items():
        records[str(name)] = [dict(row) for row in rows]
    for name, rows in (manifest.get("supplemental_records") or {}).items():
        records[str(name)].extend(dict(row) for row in rows)
    return records


def _apply_mutation(records: dict[str, list[dict[str, Any]]], mutation: Mapping[str, Any]) -> None:
    dataset = str(mutation["dataset"])
    rows = records[dataset]
    kind = str(mutation["kind"])
    if kind == "clear":
        rows.clear()
    elif kind == "take":
        del rows[max(int(mutation.get("count") or 0), 0) :]
    elif kind == "duplicate_first":
        if rows:
            rows.append(copy.deepcopy(rows[0]))
    elif kind == "add":
        rows.append(copy.deepcopy(dict(_require_mapping(mutation.get("record"), "record"))))
    elif kind == "set":
        index = int(mutation.get("index") or 0)
        if not 0 <= index < len(rows):
            raise LocalAcceptanceError(f"mutation index is out of bounds for {dataset}")
        rows[index][str(mutation.get("field") or "")] = copy.deepcopy(mutation.get("value"))
    elif kind == "repeat_to":
        target = int(mutation.get("count") or 0)
        id_field = str(mutation.get("id_field") or "ID")
        seeds = copy.deepcopy(rows)
        if target and not seeds:
            raise LocalAcceptanceError(f"repeat_to requires seed rows for {dataset}")
        while len(rows) < target:
            row = copy.deepcopy(seeds[len(rows) % len(seeds)])
            row[id_field] = f"{row.get(id_field) or dataset}-{len(rows) + 1:04d}"
            rows.append(row)
        del rows[target:]
    elif kind == "append_text":
        index = int(mutation.get("index") or 0)
        if not 0 <= index < len(rows):
            raise LocalAcceptanceError(f"mutation index is out of bounds for {dataset}")
        field = str(mutation.get("field") or "")
        rows[index][field] = str(rows[index].get(field) or "") + str(
            mutation.get("repeat") or ""
        ) * max(int(mutation.get("count") or 0), 0)
    else:  # pragma: no cover - validated by load_manifest
        raise LocalAcceptanceError(f"unsupported mutation kind: {kind}")


def _stamp_frame(
    frame: pd.DataFrame,
    *,
    dataset: str,
    state: str,
    as_of_utc: str,
    healthy_count: int,
    primary_key: str,
) -> pd.DataFrame:
    frame.attrs.update(
        {
            "sanitized": True,
            "source_mode": SOURCE_MODE,
            "source_state": state,
            "source_dataset": dataset,
            "data_as_of_utc": as_of_utc,
            "live_validation_performed": False,
            "primary_key": primary_key,
        }
    )
    if state == "zero":
        frame.attrs["true_zero"] = True
    elif state == "partial":
        frame.attrs.update({"partial": True, "expected_full_rows": healthy_count})
    elif state == "stale":
        frame.attrs["stale"] = True
    elif state == "failed":
        frame.attrs.update(
            {
                "fetch_error": "sanitized synthetic source failure",
                "fetch_error_dataset": dataset,
                "fetch_error_kind": "fixture_failure",
            }
        )
    elif state == "unavailable":
        frame.attrs.update(
            {
                "source_unavailable": True,
                "source_unavailable_detail": "sanitized synthetic source unavailable",
            }
        )
    elif state == "truncated":
        frame.attrs.update(
            {"partial": True, "was_truncated": True, "expected_full_rows": healthy_count}
        )
    return frame


def _add_stable_fixture_record_ids(
    frame: pd.DataFrame,
    *,
    dataset: str,
    primary_key: str,
) -> pd.DataFrame:
    """Add deterministic lineage IDs without replacing the source primary key."""

    seen: dict[str, int] = {}
    stable_ids: list[str] = []
    for row in frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records"):
        raw_key = str(row.get(primary_key) or "").strip()
        if raw_key:
            base = f"{dataset}:{raw_key}"
        else:
            digest_row = {
                key: value
                for key, value in row.items()
                if key not in {"FIXTURE_MEMBER", "LOCAL_ACCEPTANCE_RECORD_ID"}
            }
            base = f"{dataset}:missing:{_canonical_digest(digest_row)[:16]}"
        occurrence = seen.get(base, 0) + 1
        seen[base] = occurrence
        stable_ids.append(base if occurrence == 1 else f"{base}:occurrence:{occurrence}")
    frame["LOCAL_ACCEPTANCE_RECORD_ID"] = stable_ids
    return frame


def build_scenario_bundle(
    scenario_name: str,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> LocalAcceptanceBundle:
    """Materialize, stamp, and reconcile one declared local scenario."""

    resolved_manifest = _resolve_fixture_path(Path(manifest_path))
    manifest = load_manifest(resolved_manifest)
    scenarios = manifest["scenarios"]
    if scenario_name not in scenarios:
        raise LocalAcceptanceError(f"unknown local acceptance scenario: {scenario_name}")
    scenario = scenarios[scenario_name]
    records = _load_base_records(manifest, resolved_manifest)
    for mutation in scenario.get("mutations") or []:
        _apply_mutation(records, mutation)

    expected_counts = {
        str(name): int(count)
        for name, count in manifest["healthy_expected_counts"].items()
    }
    expected_counts.update(
        {str(name): int(count) for name, count in scenario["expected_count_overrides"].items()}
    )
    expected_canonical_counts = {
        str(name): int(spec["expected_canonical_count"])
        for name, spec in manifest["datasets"].items()
    }
    expected_canonical_counts.update(
        {
            str(name): int(count)
            for name, count in scenario["expected_canonical_count_overrides"].items()
        }
    )
    warning_codes = {
        str(name): tuple(str(code) for code in spec["expected_warning_codes"])
        for name, spec in manifest["datasets"].items()
    }
    warning_codes.update(
        {
            str(name): tuple(str(code) for code in codes)
            for name, codes in scenario["expected_warning_overrides"].items()
        }
    )
    source_states = {name: "available" for name in manifest["datasets"]}
    source_states.update(
        {str(name): str(state) for name, state in scenario["source_state_overrides"].items()}
    )
    frames: dict[str, pd.DataFrame] = {}
    for name, spec in manifest["datasets"].items():
        rows = records.get(name) or []
        frame = pd.DataFrame(copy.deepcopy(rows))
        for column in spec["required_columns"]:
            if column not in frame.columns:
                frame[column] = pd.Series(dtype="object")
        frame = _add_stable_fixture_record_ids(
            frame,
            dataset=name,
            primary_key=str(spec["primary_key"]),
        )
        frames[name] = _stamp_frame(
            frame,
            dataset=name,
            state=source_states[name],
            as_of_utc=str(manifest["deterministic_clock_utc"]),
            healthy_count=int(manifest["healthy_expected_counts"][name]),
            primary_key=str(spec["primary_key"]),
        )
    bundle = LocalAcceptanceBundle(
        scenario=scenario_name,
        as_of_utc=str(manifest["deterministic_clock_utc"]),
        provider_state=str(scenario["provider_state"]),
        schema_fingerprint=str(manifest["schema_fingerprint"]),
        frames=frames,
        expected_counts=expected_counts,
        expected_canonical_counts=expected_canonical_counts,
        warning_codes=warning_codes,
        source_states=source_states,
        provider_warning_code=scenario["provider_warning_code"],
    )
    bundle.assert_reconciled()
    return bundle


def validate_all_scenarios(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, dict[str, Any]]:
    """Materialize and reconcile every declared scenario."""

    manifest = load_manifest(manifest_path)
    return {
        name: build_scenario_bundle(name, manifest_path).redacted_summary()
        for name in sorted(manifest["scenarios"])
    }
