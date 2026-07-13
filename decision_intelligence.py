"""Request-scoped canonical decision intelligence for AdoptIQ.

This module deliberately contains no connector code.  Callers provide the
source frames that were authorized for one request; the builder normalizes and
partitions those frames once and returns an effectively immutable
``AnalysisBundle``.  Reports, exports, dashboards, renewal views, and Ask AI
consume projections of that bundle instead of rebuilding authoritative facts.

The serialized bundle is also the temporal snapshot.  It contains canonical
metrics and bounded evidence references, never connector credentials or raw
source rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import unicodedata
from numbers import Number

import pandas as pd


ANALYSIS_SCHEMA_VERSION = "2.0.0"
ANALYSIS_ENGINE_VERSION = "decision-intelligence-v2"
DEFAULT_SOURCE_TYPES: Tuple[str, ...] = (
    "subscriptions",
    "adoption_barriers",
    "support_cases",
    "customer_pulse",
    "action_plans",
    "success_priorities",
    "external_incidents",
)

_NULL_TEXT = frozenset({"", "none", "null", "nan", "nat", "n/a", "na", "unknown"})
_MATERIAL_FINDING_KINDS = frozenset(
    {
        "observed_fact",
        "deterministic_calculation",
        "relationship",
        "inferred_explanation",
        "risk_signal",
        "opportunity_signal",
        "temporal_change",
        "scenario",
        "recommendation",
        "data_quality",
    }
)
_TEMPORAL_STATES = frozenset(
    {
        "new",
        "worsening",
        "improving",
        "persistent",
        "recurring",
        "recovering",
        "resolved",
        "stale",
        "uncertain",
        "not_comparable",
    }
)


def _clean_text(value: Any, *, limit: Optional[int] = None) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.casefold() in _NULL_TEXT:
        return ""
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _iso_z(value: Any, *, default_now: bool = False) -> str:
    """Return a stable UTC ISO-8601 string or an empty string."""

    parsed: Optional[datetime] = None
    if isinstance(value, datetime):
        parsed = value
    elif value is not None and _clean_text(value):
        text = _clean_text(value)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            try:
                parsed_ts = pd.to_datetime(text, utc=True, errors="coerce")
                if not pd.isna(parsed_ts):
                    parsed = parsed_ts.to_pydatetime()
            except Exception:
                parsed = None
    if parsed is None and default_now:
        parsed = datetime.now(timezone.utc)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _stable_number(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 10)
    if hasattr(value, "item"):
        try:
            return _stable_number(value.item())
        except Exception:
            return _clean_text(value)
    return value


def _freeze(value: Any) -> Any:
    """Recursively freeze JSON-like values for frozen dataclasses."""

    value = _stable_number(value)
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=lambda item: repr(item)))
    if isinstance(value, datetime):
        return _iso_z(value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, datetime):
        return _iso_z(value)
    value = _stable_number(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _clean_text(value)


class FrozenDict(Mapping[str, Any]):
    """A deterministic, hashable mapping used inside frozen contracts."""

    __slots__ = ("_items", "_dict", "_hash")

    def __init__(self, value: Optional[Mapping[str, Any]] = None) -> None:
        items = tuple(
            sorted(
                ((str(key), _freeze(item)) for key, item in (value or {}).items()),
                key=lambda pair: pair[0],
            )
        )
        self._items = items
        self._dict = dict(items)
        self._hash = hash(items)

    def __getitem__(self, key: str) -> Any:
        return self._dict[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenDict({self._dict!r})"

    def to_dict(self) -> Dict[str, Any]:
        return _plain(self)


def _tuple_text(values: Iterable[Any], *, preserve_order: bool = False) -> Tuple[str, ...]:
    cleaned = [_clean_text(value) for value in values]
    cleaned = [value for value in cleaned if value]
    if preserve_order:
        return tuple(dict.fromkeys(cleaned))
    return tuple(sorted(set(cleaned), key=lambda value: (value.casefold(), value)))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _fingerprint(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _safe_id(*parts: Any, prefix: str = "id") -> str:
    material = "\x1f".join(_clean_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class AnalysisRequest:
    """Explicit request scope.  Empty scopes never imply a wider scope."""

    tenant_scope: str = ""
    organization_scope: str = ""
    customer_scope: Tuple[str, ...] = ()
    account_scope: Tuple[str, ...] = ()
    subscription_scope: Tuple[str, ...] = ()
    portfolio_scope: str = ""
    team_scope: Tuple[str, ...] = ()
    leader_scope: Tuple[str, ...] = ()
    technology_scope: Tuple[str, ...] = ()
    renewal_scope: Tuple[str, ...] = ()
    time_range_start: str = ""
    time_range_end: str = ""
    as_of_time: str = ""
    allowed_source_types: Tuple[str, ...] = DEFAULT_SOURCE_TYPES
    report_mode: str = "decision_brief"
    comparison_snapshot: str = ""
    feature_configuration: Mapping[str, Any] = field(default_factory=FrozenDict)
    schema_version: str = ANALYSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "tenant_scope",
            "organization_scope",
            "portfolio_scope",
        ):
            raw_value = getattr(self, name)
            if raw_value is not None and not isinstance(raw_value, str):
                raise TypeError(f"{name} must be a scalar string")
            object.__setattr__(self, name, _clean_text(raw_value))
        for name in (
            "customer_scope",
            "account_scope",
            "subscription_scope",
            "team_scope",
            "leader_scope",
            "technology_scope",
            "renewal_scope",
            "allowed_source_types",
        ):
            raw_values = getattr(self, name)
            if not isinstance(
                raw_values,
                (tuple, list, set, frozenset),
            ):
                raise TypeError(
                    f"{name} must be a sequence of scalar strings"
                )
            if any(not isinstance(value, str) for value in raw_values):
                raise TypeError(
                    f"{name} must contain only scalar strings"
                )
            object.__setattr__(self, name, _tuple_text(raw_values))
        object.__setattr__(self, "report_mode", _clean_text(self.report_mode) or "decision_brief")
        object.__setattr__(self, "time_range_start", _iso_z(self.time_range_start))
        object.__setattr__(self, "time_range_end", _iso_z(self.time_range_end))
        object.__setattr__(self, "as_of_time", _iso_z(self.as_of_time, default_now=True))
        object.__setattr__(self, "comparison_snapshot", _clean_text(self.comparison_snapshot))
        object.__setattr__(self, "feature_configuration", FrozenDict(self.feature_configuration))
        object.__setattr__(self, "schema_version", _clean_text(self.schema_version) or ANALYSIS_SCHEMA_VERSION)
        if not self.has_explicit_scope:
            raise ValueError(
                "AnalysisRequest requires an explicit tenant, organization, customer, "
                "account, subscription, portfolio, team, leader, technology, or renewal scope"
            )
        if self.time_range_start and self.time_range_end:
            if self.time_range_start > self.time_range_end:
                raise ValueError("time_range_start must not be after time_range_end")

    @property
    def has_explicit_scope(self) -> bool:
        return bool(
            self.tenant_scope
            or self.organization_scope
            or self.customer_scope
            or self.account_scope
            or self.subscription_scope
            or self.portfolio_scope
            or self.team_scope
            or self.leader_scope
            or self.technology_scope
            or self.renewal_scope
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_scope": self.tenant_scope,
            "organization_scope": self.organization_scope,
            "customer_scope": list(self.customer_scope),
            "account_scope": list(self.account_scope),
            "subscription_scope": list(self.subscription_scope),
            "portfolio_scope": self.portfolio_scope,
            "team_scope": list(self.team_scope),
            "leader_scope": list(self.leader_scope),
            "technology_scope": list(self.technology_scope),
            "renewal_scope": list(self.renewal_scope),
            "time_range_start": self.time_range_start,
            "time_range_end": self.time_range_end,
            "as_of_time": self.as_of_time,
            "allowed_source_types": list(self.allowed_source_types),
            "report_mode": self.report_mode,
            "comparison_snapshot": self.comparison_snapshot,
            "feature_configuration": self.feature_configuration.to_dict(),
            "schema_version": self.schema_version,
        }

    def fingerprint_payload(self) -> Dict[str, Any]:
        return self.to_dict()

    def comparison_scope_payload(self) -> Dict[str, Any]:
        payload = self.to_dict()
        payload.pop("as_of_time", None)
        # Observation-window endpoints advance between snapshots.  They are
        # analysis time, not entity scope; retaining them would make every
        # legitimate later observation falsely cross-scope.
        payload.pop("time_range_start", None)
        payload.pop("time_range_end", None)
        payload.pop("comparison_snapshot", None)
        payload.pop("report_mode", None)
        return payload

    @property
    def request_fingerprint(self) -> str:
        return _fingerprint("request", self.fingerprint_payload())

    @property
    def comparison_scope_fingerprint(self) -> str:
        return _fingerprint("scope", self.comparison_scope_payload())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AnalysisRequest":
        return cls(**dict(payload))


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    source_type: str
    source_record: str
    stable_source_identifier: str
    customer_identity: str = ""
    subscription_identity: str = ""
    relevant_field: str = ""
    observed_value: Any = None
    observation_timestamp: str = ""
    ingestion_timestamp: str = ""
    source_freshness: str = "unknown"
    source_authority: str = "source_record"
    source_scope: str = "customer"
    conflict_status: str = "none"
    safe_excerpt: str = ""
    provenance: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "source_type",
            "source_record",
            "stable_source_identifier",
            "customer_identity",
            "subscription_identity",
            "relevant_field",
            "source_freshness",
            "source_authority",
            "source_scope",
            "conflict_status",
        ):
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        if not self.evidence_id or not self.source_type or not self.stable_source_identifier:
            raise ValueError("EvidenceReference requires evidence_id, source_type, and stable_source_identifier")
        object.__setattr__(self, "observed_value", _freeze(self.observed_value))
        object.__setattr__(self, "observation_timestamp", _iso_z(self.observation_timestamp))
        object.__setattr__(self, "ingestion_timestamp", _iso_z(self.ingestion_timestamp))
        object.__setattr__(self, "safe_excerpt", _clean_text(self.safe_excerpt, limit=320))
        object.__setattr__(self, "provenance", FrozenDict(self.provenance))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_record": self.source_record,
            "stable_source_identifier": self.stable_source_identifier,
            "customer_identity": self.customer_identity,
            "subscription_identity": self.subscription_identity,
            "relevant_field": self.relevant_field,
            "observed_value": _plain(self.observed_value),
            "observation_timestamp": self.observation_timestamp,
            "ingestion_timestamp": self.ingestion_timestamp,
            "source_freshness": self.source_freshness,
            "source_authority": self.source_authority,
            "source_scope": self.source_scope,
            "conflict_status": self.conflict_status,
            "safe_excerpt": self.safe_excerpt,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceReference":
        return cls(**dict(payload))


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: Any
    unit: str = "count"
    state: str = "observed"
    evidence_ids: Tuple[str, ...] = ()
    as_of_time: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_text(self.name))
        if not self.name:
            raise ValueError("MetricValue.name is required")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "unit", _clean_text(self.unit) or "count")
        object.__setattr__(self, "state", _clean_text(self.state) or "observed")
        object.__setattr__(self, "evidence_ids", _tuple_text(self.evidence_ids))
        object.__setattr__(self, "as_of_time", _iso_z(self.as_of_time))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": _plain(self.value),
            "unit": self.unit,
            "state": self.state,
            "evidence_ids": list(self.evidence_ids),
            "as_of_time": self.as_of_time,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MetricValue":
        return cls(**dict(payload))


@dataclass(frozen=True)
class Finding:
    finding_id: str
    scope_kind: str
    scope_id: str
    kind: str
    category: str
    title: str
    structured_value: Any = None
    severity: str = "informational"
    importance: int = 0
    direction: str = "stable"
    evidence_ids: Tuple[str, ...] = ()
    source_freshness: str = "unknown"
    confidence: str = "LOW"
    data_coverage: float = 0.0
    conflicting_evidence: Tuple[str, ...] = ()
    explanation: str = ""
    generated_by: str = ANALYSIS_ENGINE_VERSION
    lifecycle_state: str = "active"
    first_observed_time: str = ""
    last_observed_time: str = ""

    def __post_init__(self) -> None:
        for name in (
            "finding_id",
            "scope_kind",
            "scope_id",
            "kind",
            "category",
            "title",
            "severity",
            "direction",
            "source_freshness",
            "confidence",
            "explanation",
            "generated_by",
            "lifecycle_state",
        ):
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        if self.kind not in _MATERIAL_FINDING_KINDS:
            raise ValueError(f"Unsupported finding kind: {self.kind!r}")
        object.__setattr__(self, "structured_value", _freeze(self.structured_value))
        object.__setattr__(self, "importance", max(0, min(100, int(self.importance))))
        object.__setattr__(self, "data_coverage", round(max(0.0, min(1.0, float(self.data_coverage))), 3))
        object.__setattr__(self, "evidence_ids", _tuple_text(self.evidence_ids))
        object.__setattr__(self, "conflicting_evidence", _tuple_text(self.conflicting_evidence))
        object.__setattr__(self, "first_observed_time", _iso_z(self.first_observed_time))
        object.__setattr__(self, "last_observed_time", _iso_z(self.last_observed_time))
        if not self.finding_id or not self.scope_id or not self.title:
            raise ValueError("Finding requires finding_id, scope_id, and title")

    @property
    def semantic_key(self) -> str:
        return f"{self.scope_kind}:{self.scope_id}:{self.category}:{self.kind}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "kind": self.kind,
            "category": self.category,
            "title": self.title,
            "structured_value": _plain(self.structured_value),
            "severity": self.severity,
            "importance": self.importance,
            "direction": self.direction,
            "evidence_ids": list(self.evidence_ids),
            "source_freshness": self.source_freshness,
            "confidence": self.confidence,
            "data_coverage": self.data_coverage,
            "conflicting_evidence": list(self.conflicting_evidence),
            "explanation": self.explanation,
            "generated_by": self.generated_by,
            "lifecycle_state": self.lifecycle_state,
            "first_observed_time": self.first_observed_time,
            "last_observed_time": self.last_observed_time,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Finding":
        return cls(**dict(payload))


@dataclass(frozen=True)
class TemporalChange:
    change_id: str
    scope_kind: str
    scope_id: str
    category: str
    classification: str
    title: str
    previous_value: Any = None
    current_value: Any = None
    effective_time: str = ""
    evidence_ids: Tuple[str, ...] = ()
    why_it_matters: str = ""
    confidence: str = "LOW"
    statement_kind: str = "deterministic_calculation"
    age_days: Optional[int] = None
    persistence_days: Optional[int] = None
    recurrence_count: Optional[int] = None
    momentum: str = "not_comparable"
    acceleration: str = "not_comparable"
    time_since_improvement_days: Optional[int] = None
    time_since_deterioration_days: Optional[int] = None
    time_since_authoritative_evidence_days: Optional[int] = None

    def __post_init__(self) -> None:
        for name in (
            "change_id",
            "scope_kind",
            "scope_id",
            "category",
            "classification",
            "title",
            "why_it_matters",
            "confidence",
            "statement_kind",
            "momentum",
            "acceleration",
        ):
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        if self.classification not in _TEMPORAL_STATES:
            raise ValueError(f"Unsupported temporal classification: {self.classification!r}")
        object.__setattr__(self, "previous_value", _freeze(self.previous_value))
        object.__setattr__(self, "current_value", _freeze(self.current_value))
        object.__setattr__(self, "effective_time", _iso_z(self.effective_time))
        object.__setattr__(self, "evidence_ids", _tuple_text(self.evidence_ids))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_id": self.change_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "category": self.category,
            "classification": self.classification,
            "title": self.title,
            "previous_value": _plain(self.previous_value),
            "current_value": _plain(self.current_value),
            "effective_time": self.effective_time,
            "evidence_ids": list(self.evidence_ids),
            "why_it_matters": self.why_it_matters,
            "confidence": self.confidence,
            "statement_kind": self.statement_kind,
            "age_days": self.age_days,
            "persistence_days": self.persistence_days,
            "recurrence_count": self.recurrence_count,
            "momentum": self.momentum,
            "acceleration": self.acceleration,
            "time_since_improvement_days": self.time_since_improvement_days,
            "time_since_deterioration_days": self.time_since_deterioration_days,
            "time_since_authoritative_evidence_days": self.time_since_authoritative_evidence_days,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TemporalChange":
        return cls(**dict(payload))


@dataclass(frozen=True)
class RecommendedAction:
    action_id: str
    scope_kind: str
    scope_id: str
    action_type: str
    specific_action: str
    rationale: str
    triggering_finding_ids: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]
    proposed_owner: str
    owner_confidence: str
    urgency: str
    rank: int
    priority_score: float
    ranking_factors: Mapping[str, Any]
    dependencies: Tuple[str, ...]
    expected_outcome: str
    measurable_success_signal: str
    timing_window: str
    effort: str
    confidence: str
    recommendation_source: str = ANALYSIS_ENGINE_VERSION
    lifecycle_state: str = "proposed"

    def __post_init__(self) -> None:
        for name in (
            "action_id",
            "scope_kind",
            "scope_id",
            "action_type",
            "specific_action",
            "rationale",
            "proposed_owner",
            "owner_confidence",
            "urgency",
            "expected_outcome",
            "measurable_success_signal",
            "timing_window",
            "effort",
            "confidence",
            "recommendation_source",
            "lifecycle_state",
        ):
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        if not self.action_id or not self.specific_action or not self.triggering_finding_ids:
            raise ValueError("Actions require an id, specific action, and triggering finding")
        if not self.evidence_ids:
            raise ValueError("Actions must link to canonical evidence")
        object.__setattr__(self, "triggering_finding_ids", _tuple_text(self.triggering_finding_ids))
        object.__setattr__(self, "evidence_ids", _tuple_text(self.evidence_ids))
        object.__setattr__(self, "dependencies", _tuple_text(self.dependencies, preserve_order=True))
        object.__setattr__(self, "rank", max(1, int(self.rank)))
        object.__setattr__(self, "priority_score", round(max(0.0, min(100.0, float(self.priority_score))), 2))
        object.__setattr__(self, "ranking_factors", FrozenDict(self.ranking_factors))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "action_type": self.action_type,
            "specific_action": self.specific_action,
            "rationale": self.rationale,
            "triggering_finding_ids": list(self.triggering_finding_ids),
            "evidence_ids": list(self.evidence_ids),
            "proposed_owner": self.proposed_owner,
            "owner_confidence": self.owner_confidence,
            "urgency": self.urgency,
            "rank": self.rank,
            "priority_score": self.priority_score,
            "ranking_factors": self.ranking_factors.to_dict(),
            "dependencies": list(self.dependencies),
            "expected_outcome": self.expected_outcome,
            "measurable_success_signal": self.measurable_success_signal,
            "timing_window": self.timing_window,
            "effort": self.effort,
            "confidence": self.confidence,
            "recommendation_source": self.recommendation_source,
            "lifecycle_state": self.lifecycle_state,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecommendedAction":
        return cls(**dict(payload))


@dataclass(frozen=True)
class DataQualitySummary:
    coverage_ratio: float
    confidence: str
    source_states: Mapping[str, str]
    source_freshness: Mapping[str, str]
    missing_sources: Tuple[str, ...] = ()
    stale_sources: Tuple[str, ...] = ()
    conflicted_sources: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage_ratio", round(max(0.0, min(1.0, float(self.coverage_ratio))), 3))
        object.__setattr__(self, "confidence", _clean_text(self.confidence) or "LOW")
        object.__setattr__(self, "source_states", FrozenDict(self.source_states))
        object.__setattr__(self, "source_freshness", FrozenDict(self.source_freshness))
        for name in ("missing_sources", "stale_sources", "conflicted_sources", "warnings"):
            object.__setattr__(self, name, _tuple_text(getattr(self, name), preserve_order=name == "warnings"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coverage_ratio": self.coverage_ratio,
            "confidence": self.confidence,
            "source_states": self.source_states.to_dict(),
            "source_freshness": self.source_freshness.to_dict(),
            "missing_sources": list(self.missing_sources),
            "stale_sources": list(self.stale_sources),
            "conflicted_sources": list(self.conflicted_sources),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DataQualitySummary":
        return cls(**dict(payload))


@dataclass(frozen=True)
class DecisionBrief:
    scope_kind: str
    scope_id: str
    what_changed: Tuple[str, ...]
    why_it_matters: Tuple[str, ...]
    next_action_ids: Tuple[str, ...]
    what_remains_uncertain: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]
    confidence: str
    synthesis: str

    def __post_init__(self) -> None:
        for name in ("scope_kind", "scope_id", "confidence", "synthesis"):
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        for name in (
            "what_changed",
            "why_it_matters",
            "next_action_ids",
            "what_remains_uncertain",
            "evidence_ids",
        ):
            object.__setattr__(self, name, _tuple_text(getattr(self, name), preserve_order=True))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "what_changed": list(self.what_changed),
            "why_it_matters": list(self.why_it_matters),
            "next_action_ids": list(self.next_action_ids),
            "what_remains_uncertain": list(self.what_remains_uncertain),
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "synthesis": self.synthesis,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionBrief":
        return cls(**dict(payload))


@dataclass(frozen=True)
class CustomerAnalysis:
    customer_id: str
    customer_name: str
    aliases: Tuple[str, ...]
    subscriptions: Tuple[str, ...]
    technologies: Tuple[str, ...]
    responsible_teams: Tuple[str, ...]
    metrics: Tuple[MetricValue, ...]
    risk_profile: Mapping[str, Any]
    adoption_state: Mapping[str, Any]
    pulse_state: Mapping[str, Any]
    renewal_context: Mapping[str, Any]
    engagement_state: Mapping[str, Any]
    findings: Tuple[Finding, ...]
    temporal_changes: Tuple[TemporalChange, ...]
    recommended_actions: Tuple[RecommendedAction, ...]
    evidence_ids: Tuple[str, ...]
    data_quality: DataQualitySummary
    unresolved_conflicts: Tuple[str, ...]
    executive_synthesis: str
    decision_brief: DecisionBrief

    def __post_init__(self) -> None:
        object.__setattr__(self, "customer_id", _clean_text(self.customer_id))
        object.__setattr__(self, "customer_name", _clean_text(self.customer_name))
        for name in ("aliases", "subscriptions", "technologies", "responsible_teams", "evidence_ids", "unresolved_conflicts"):
            object.__setattr__(self, name, _tuple_text(getattr(self, name)))
        object.__setattr__(self, "metrics", tuple(sorted(self.metrics, key=lambda metric: metric.name)))
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda item: item.finding_id)))
        object.__setattr__(self, "temporal_changes", tuple(sorted(self.temporal_changes, key=lambda item: item.change_id)))
        object.__setattr__(self, "recommended_actions", tuple(sorted(self.recommended_actions, key=lambda item: (item.rank, item.action_id))))
        for name in ("risk_profile", "adoption_state", "pulse_state", "renewal_context", "engagement_state"):
            object.__setattr__(self, name, FrozenDict(getattr(self, name)))
        object.__setattr__(self, "executive_synthesis", _clean_text(self.executive_synthesis, limit=1200))
        if not self.customer_id or not self.customer_name:
            raise ValueError("CustomerAnalysis requires a canonical id and name")

    def metric(self, name: str, default: Any = None) -> Any:
        for metric in self.metrics:
            if metric.name == name:
                return metric.value
        return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "aliases": list(self.aliases),
            "subscriptions": list(self.subscriptions),
            "technologies": list(self.technologies),
            "responsible_teams": list(self.responsible_teams),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "risk_profile": self.risk_profile.to_dict(),
            "adoption_state": self.adoption_state.to_dict(),
            "pulse_state": self.pulse_state.to_dict(),
            "renewal_context": self.renewal_context.to_dict(),
            "engagement_state": self.engagement_state.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "temporal_changes": [change.to_dict() for change in self.temporal_changes],
            "recommended_actions": [action.to_dict() for action in self.recommended_actions],
            "evidence_ids": list(self.evidence_ids),
            "data_quality": self.data_quality.to_dict(),
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "executive_synthesis": self.executive_synthesis,
            "decision_brief": self.decision_brief.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CustomerAnalysis":
        data = dict(payload)
        data["metrics"] = tuple(MetricValue.from_dict(item) for item in data.get("metrics", ()))
        data["findings"] = tuple(Finding.from_dict(item) for item in data.get("findings", ()))
        data["temporal_changes"] = tuple(TemporalChange.from_dict(item) for item in data.get("temporal_changes", ()))
        data["recommended_actions"] = tuple(RecommendedAction.from_dict(item) for item in data.get("recommended_actions", ()))
        data["data_quality"] = DataQualitySummary.from_dict(data.get("data_quality", {}))
        data["decision_brief"] = DecisionBrief.from_dict(data.get("decision_brief", {}))
        return cls(**data)


@dataclass(frozen=True)
class PortfolioAnalysis:
    portfolio_id: str
    portfolio_scope: str
    customer_count: int
    customer_ids: Tuple[str, ...]
    metrics: Tuple[MetricValue, ...]
    risk_distribution: Mapping[str, int]
    opportunity_distribution: Mapping[str, int]
    temporal_changes: Tuple[TemporalChange, ...]
    emerging_patterns: Tuple[str, ...]
    recurring_barriers: Tuple[str, ...]
    concentration_risks: Tuple[str, ...]
    cross_customer_themes: Tuple[str, ...]
    ranked_customer_ids: Tuple[str, ...]
    recommended_actions: Tuple[RecommendedAction, ...]
    data_quality: DataQualitySummary
    evidence_gaps: Tuple[str, ...]
    executive_synthesis: str
    decision_brief: DecisionBrief

    def __post_init__(self) -> None:
        object.__setattr__(self, "portfolio_id", _clean_text(self.portfolio_id))
        object.__setattr__(self, "portfolio_scope", _clean_text(self.portfolio_scope))
        object.__setattr__(self, "customer_count", max(0, int(self.customer_count)))
        for name in (
            "customer_ids",
            "emerging_patterns",
            "recurring_barriers",
            "concentration_risks",
            "cross_customer_themes",
            "ranked_customer_ids",
            "evidence_gaps",
        ):
            object.__setattr__(self, name, _tuple_text(getattr(self, name), preserve_order=name in {"ranked_customer_ids", "emerging_patterns", "concentration_risks"}))
        object.__setattr__(self, "metrics", tuple(sorted(self.metrics, key=lambda metric: metric.name)))
        object.__setattr__(self, "risk_distribution", FrozenDict(self.risk_distribution))
        object.__setattr__(self, "opportunity_distribution", FrozenDict(self.opportunity_distribution))
        object.__setattr__(self, "temporal_changes", tuple(sorted(self.temporal_changes, key=lambda item: item.change_id)))
        object.__setattr__(self, "recommended_actions", tuple(sorted(self.recommended_actions, key=lambda item: (item.rank, item.action_id))))
        object.__setattr__(self, "executive_synthesis", _clean_text(self.executive_synthesis, limit=1600))
        if self.customer_count != len(self.customer_ids):
            raise ValueError("Portfolio customer_count must equal the included customer_ids")

    def metric(self, name: str, default: Any = None) -> Any:
        for metric in self.metrics:
            if metric.name == name:
                return metric.value
        return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "portfolio_scope": self.portfolio_scope,
            "customer_count": self.customer_count,
            "customer_ids": list(self.customer_ids),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "risk_distribution": self.risk_distribution.to_dict(),
            "opportunity_distribution": self.opportunity_distribution.to_dict(),
            "temporal_changes": [change.to_dict() for change in self.temporal_changes],
            "emerging_patterns": list(self.emerging_patterns),
            "recurring_barriers": list(self.recurring_barriers),
            "concentration_risks": list(self.concentration_risks),
            "cross_customer_themes": list(self.cross_customer_themes),
            "ranked_customer_ids": list(self.ranked_customer_ids),
            "recommended_actions": [action.to_dict() for action in self.recommended_actions],
            "data_quality": self.data_quality.to_dict(),
            "evidence_gaps": list(self.evidence_gaps),
            "executive_synthesis": self.executive_synthesis,
            "decision_brief": self.decision_brief.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PortfolioAnalysis":
        data = dict(payload)
        data["metrics"] = tuple(MetricValue.from_dict(item) for item in data.get("metrics", ()))
        data["temporal_changes"] = tuple(TemporalChange.from_dict(item) for item in data.get("temporal_changes", ()))
        data["recommended_actions"] = tuple(RecommendedAction.from_dict(item) for item in data.get("recommended_actions", ()))
        data["data_quality"] = DataQualitySummary.from_dict(data.get("data_quality", {}))
        data["decision_brief"] = DecisionBrief.from_dict(data.get("decision_brief", {}))
        return cls(**data)


@dataclass(frozen=True)
class AnalysisDiagnostics:
    missing_evidence: Tuple[str, ...] = ()
    contradictory_evidence: Tuple[str, ...] = ()
    stale_evidence: Tuple[str, ...] = ()
    quarantined_records: Tuple[str, ...] = ()
    unresolved_ownership_conflicts: Tuple[str, ...] = ()
    unsupported_relationships: Tuple[str, ...] = ()
    skipped_records: Tuple[str, ...] = ()
    degraded_calculations: Tuple[str, ...] = ()
    confidence_reductions: Tuple[str, ...] = ()
    source_failures: Tuple[str, ...] = ()
    validation_failures: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _tuple_text(getattr(self, name), preserve_order=True))

    def to_dict(self) -> Dict[str, Any]:
        return {name: list(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AnalysisDiagnostics":
        return cls(**dict(payload))


@dataclass(frozen=True)
class AnalysisContext:
    request_fingerprint: str
    comparison_scope_fingerprint: str
    analysis_fingerprint: str
    generated_time: str
    as_of_time: str
    source_cutoff_time: str
    selected_customers: Tuple[str, ...]
    selected_subscriptions: Tuple[str, ...]
    selected_technologies: Tuple[str, ...]
    selected_teams: Tuple[str, ...]
    source_availability: Mapping[str, str]
    source_freshness: Mapping[str, str]
    configuration_version: str
    analysis_engine_version: str
    warnings: Tuple[str, ...]
    degraded_mode_indicators: Tuple[str, ...]
    data_quality_summary: DataQualitySummary

    def __post_init__(self) -> None:
        for name in (
            "request_fingerprint",
            "comparison_scope_fingerprint",
            "analysis_fingerprint",
            "source_cutoff_time",
            "configuration_version",
            "analysis_engine_version",
        ):
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        object.__setattr__(self, "generated_time", _iso_z(self.generated_time, default_now=True))
        object.__setattr__(self, "as_of_time", _iso_z(self.as_of_time, default_now=True))
        for name in ("selected_customers", "selected_subscriptions", "selected_technologies", "selected_teams"):
            object.__setattr__(self, name, _tuple_text(getattr(self, name)))
        for name in ("warnings", "degraded_mode_indicators"):
            object.__setattr__(self, name, _tuple_text(getattr(self, name), preserve_order=True))
        object.__setattr__(self, "source_availability", FrozenDict(self.source_availability))
        object.__setattr__(self, "source_freshness", FrozenDict(self.source_freshness))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_fingerprint": self.request_fingerprint,
            "comparison_scope_fingerprint": self.comparison_scope_fingerprint,
            "analysis_fingerprint": self.analysis_fingerprint,
            "generated_time": self.generated_time,
            "as_of_time": self.as_of_time,
            "source_cutoff_time": self.source_cutoff_time,
            "selected_customers": list(self.selected_customers),
            "selected_subscriptions": list(self.selected_subscriptions),
            "selected_technologies": list(self.selected_technologies),
            "selected_teams": list(self.selected_teams),
            "source_availability": self.source_availability.to_dict(),
            "source_freshness": self.source_freshness.to_dict(),
            "configuration_version": self.configuration_version,
            "analysis_engine_version": self.analysis_engine_version,
            "warnings": list(self.warnings),
            "degraded_mode_indicators": list(self.degraded_mode_indicators),
            "data_quality_summary": self.data_quality_summary.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AnalysisContext":
        data = dict(payload)
        data["data_quality_summary"] = DataQualitySummary.from_dict(data.get("data_quality_summary", {}))
        return cls(**data)


@dataclass(frozen=True)
class AnalysisBundle:
    schema_version: str
    request: AnalysisRequest
    context: AnalysisContext
    customers: Tuple[CustomerAnalysis, ...]
    portfolio: PortfolioAnalysis
    evidence: Tuple[EvidenceReference, ...]
    diagnostics: AnalysisDiagnostics

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _clean_text(self.schema_version) or ANALYSIS_SCHEMA_VERSION)
        object.__setattr__(self, "customers", tuple(sorted(self.customers, key=lambda item: item.customer_id)))
        object.__setattr__(self, "evidence", tuple(sorted(self.evidence, key=lambda item: item.evidence_id)))
        errors = self.reconciliation_errors()
        if errors:
            raise ValueError("AnalysisBundle reconciliation failed: " + "; ".join(errors))

    @property
    def analysis_fingerprint(self) -> str:
        return self.context.analysis_fingerprint

    def customer_by_id(self, customer_id: str) -> Optional[CustomerAnalysis]:
        target = _clean_text(customer_id)
        for customer in self.customers:
            if customer.customer_id == target or customer.customer_name == target:
                return customer
        return None

    def evidence_by_id(self) -> Dict[str, EvidenceReference]:
        return {item.evidence_id: item for item in self.evidence}

    def canonical_payload(self) -> Dict[str, Any]:
        """Payload used for deterministic factual fingerprinting.

        Generation time, runtime metadata, and prose synthesis are excluded.
        Structured metrics/findings/actions/evidence remain authoritative.
        """

        customers = []
        for customer in self.customers:
            customers.append(
                {
                    "customer_id": customer.customer_id,
                    "aliases": list(customer.aliases),
                    "subscriptions": list(customer.subscriptions),
                    "technologies": list(customer.technologies),
                    "metrics": [metric.to_dict() for metric in customer.metrics],
                    "findings": [
                        {
                            key: value
                            for key, value in finding.to_dict().items()
                            if key not in {"title", "explanation"}
                        }
                        for finding in customer.findings
                    ],
                    "temporal_changes": [
                        {
                            key: value
                            for key, value in change.to_dict().items()
                            if key not in {"title", "why_it_matters"}
                        }
                        for change in customer.temporal_changes
                    ],
                    "actions": [
                        {
                            key: value
                            for key, value in action.to_dict().items()
                            if key not in {"specific_action", "rationale", "expected_outcome"}
                        }
                        for action in customer.recommended_actions
                    ],
                    "data_quality": customer.data_quality.to_dict(),
                    "conflicts": list(customer.unresolved_conflicts),
                }
            )
        return {
            "schema_version": self.schema_version,
            "request": self.request.fingerprint_payload(),
            "customers": customers,
            "portfolio": {
                "portfolio_id": self.portfolio.portfolio_id,
                "customer_ids": list(self.portfolio.customer_ids),
                "metrics": [metric.to_dict() for metric in self.portfolio.metrics],
                "risk_distribution": self.portfolio.risk_distribution.to_dict(),
                "opportunity_distribution": self.portfolio.opportunity_distribution.to_dict(),
                "ranked_customer_ids": list(self.portfolio.ranked_customer_ids),
                "actions": [
                    {
                        key: value
                        for key, value in action.to_dict().items()
                        if key not in {"specific_action", "rationale", "expected_outcome"}
                    }
                    for action in self.portfolio.recommended_actions
                ],
                "data_quality": self.portfolio.data_quality.to_dict(),
            },
            "evidence": [
                {
                    key: value
                    for key, value in item.to_dict().items()
                    if key not in {"safe_excerpt", "ingestion_timestamp"}
                }
                for item in self.evidence
            ],
            "diagnostics": self.diagnostics.to_dict(),
        }

    def reconciliation_errors(self) -> List[str]:
        errors: List[str] = []
        customer_ids = tuple(customer.customer_id for customer in self.customers)
        if tuple(sorted(customer_ids)) != tuple(sorted(self.portfolio.customer_ids)):
            errors.append("portfolio customer IDs do not match customer analyses")
        if self.portfolio.customer_count != len(self.customers):
            errors.append("portfolio customer count does not match customer analyses")
        if set(self.context.selected_customers) != set(customer_ids):
            errors.append("context selected customers do not match customer analyses")
        if self.context.request_fingerprint != self.request.request_fingerprint:
            errors.append("context request fingerprint does not match request")
        if (
            self.context.comparison_scope_fingerprint
            != self.request.comparison_scope_fingerprint
        ):
            errors.append("context comparison scope fingerprint does not match request")
        evidence = self.evidence_by_id()
        findings: Dict[str, Finding] = {}
        for customer in self.customers:
            allowed = set(customer.evidence_ids)
            customer_findings = {
                finding.finding_id: finding for finding in customer.findings
            }
            for evidence_id in allowed:
                item = evidence.get(evidence_id)
                if item is None:
                    errors.append(f"customer {customer.customer_id} references missing evidence {evidence_id}")
                elif item.customer_identity not in {"", "Portfolio", customer.customer_id, customer.customer_name}:
                    errors.append(f"customer {customer.customer_id} contains out-of-scope evidence {evidence_id}")
            for finding in customer.findings:
                findings[finding.finding_id] = finding
                if finding.kind != "data_quality" and not finding.evidence_ids:
                    errors.append(f"material finding {finding.finding_id} has no evidence")
                for evidence_id in finding.evidence_ids:
                    if evidence_id not in allowed:
                        errors.append(f"finding {finding.finding_id} uses evidence outside customer scope")
            for metric in customer.metrics:
                if any(evidence_id not in allowed for evidence_id in metric.evidence_ids):
                    errors.append(
                        f"metric {customer.customer_id}:{metric.name} uses evidence outside customer scope"
                    )
            for change in customer.temporal_changes:
                if any(evidence_id not in allowed for evidence_id in change.evidence_ids):
                    errors.append(
                        f"change {change.change_id} uses evidence outside customer scope"
                    )
            for action in customer.recommended_actions:
                if any(
                    finding_id not in customer_findings
                    for finding_id in action.triggering_finding_ids
                ):
                    errors.append(f"action {action.action_id} references unknown finding")
                if any(evidence_id not in allowed for evidence_id in action.evidence_ids):
                    errors.append(f"action {action.action_id} uses evidence outside customer scope")
            customer_action_ids = {
                action.action_id for action in customer.recommended_actions
            }
            if any(
                action_id not in customer_action_ids
                for action_id in customer.decision_brief.next_action_ids
            ):
                errors.append(
                    f"customer {customer.customer_id} brief references unknown action"
                )
            if any(
                evidence_id not in allowed
                for evidence_id in customer.decision_brief.evidence_ids
            ):
                errors.append(
                    f"customer {customer.customer_id} brief uses evidence outside customer scope"
                )
        for action in self.portfolio.recommended_actions:
            if any(finding_id not in findings for finding_id in action.triggering_finding_ids):
                errors.append(f"portfolio action {action.action_id} references unknown finding")
            if any(evidence_id not in evidence for evidence_id in action.evidence_ids):
                errors.append(f"portfolio action {action.action_id} references unknown evidence")
        portfolio_action_ids = {
            action.action_id for action in self.portfolio.recommended_actions
        }
        if any(
            action_id not in portfolio_action_ids
            for action_id in self.portfolio.decision_brief.next_action_ids
        ):
            errors.append("portfolio brief references unknown action")
        if any(
            evidence_id not in evidence
            for evidence_id in self.portfolio.decision_brief.evidence_ids
        ):
            errors.append("portfolio brief references unknown evidence")
        for metric in self.portfolio.metrics:
            if any(evidence_id not in evidence for evidence_id in metric.evidence_ids):
                errors.append(f"portfolio metric {metric.name} references unknown evidence")
        exact_total_names = (
            "total_barriers",
            "open_barriers",
            "critical_barriers",
            "critical_high_barriers",
            "total_cases",
            "open_cases",
            "p1_cases",
            "p2_cases",
            "bems_count",
            "break_fix_cases",
            "provisioning_cases",
            "total_action_plans",
            "open_action_plans",
            "completed_action_plans",
            "overdue_action_plans",
            "success_priority_count",
            "subscription_count",
        )
        portfolio_metric_names = {metric.name for metric in self.portfolio.metrics}
        for metric_name in exact_total_names:
            if metric_name not in portfolio_metric_names or not all(
                any(metric.name == metric_name for metric in customer.metrics)
                for customer in self.customers
            ):
                continue
            expected = _integral_if_whole(
                _numeric_metric_total(self.customers, metric_name)
            )
            if self.portfolio.metric(metric_name) != expected:
                errors.append(
                    f"portfolio metric {metric_name} does not equal customer-derived total"
                )
        if sum(int(value) for value in self.portfolio.risk_distribution.values()) != len(
            self.customers
        ):
            errors.append("portfolio risk distribution does not equal customer count")
        return list(dict.fromkeys(errors))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request": self.request.to_dict(),
            "context": self.context.to_dict(),
            "customers": [customer.to_dict() for customer in self.customers],
            "portfolio": self.portfolio.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "diagnostics": self.diagnostics.to_dict(),
        }

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AnalysisBundle":
        data = dict(payload)
        data["request"] = AnalysisRequest.from_dict(data.get("request", {}))
        data["context"] = AnalysisContext.from_dict(data.get("context", {}))
        data["customers"] = tuple(CustomerAnalysis.from_dict(item) for item in data.get("customers", ()))
        data["portfolio"] = PortfolioAnalysis.from_dict(data.get("portfolio", {}))
        data["evidence"] = tuple(EvidenceReference.from_dict(item) for item in data.get("evidence", ()))
        data["diagnostics"] = AnalysisDiagnostics.from_dict(data.get("diagnostics", {}))
        return cls(**data)

    @classmethod
    def from_json(cls, payload: str) -> "AnalysisBundle":
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Serialized AnalysisBundle must be a JSON object")
        return cls.from_dict(decoded)

    @classmethod
    def load(cls, path: Path | str) -> "AnalysisBundle":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def safe_projection(self) -> Dict[str, Any]:
        """Bounded request-safe projection for dashboard and Ask AI."""

        allowed = set(self.context.selected_customers)
        evidence_rows = []
        for item in self.evidence:
            if item.customer_identity not in {"", "Portfolio"} and item.customer_identity not in allowed:
                continue
            evidence_rows.append(
                {
                    "evidence_id": item.evidence_id,
                    "source_type": item.source_type,
                    "source_id": item.stable_source_identifier,
                    "customer": item.customer_identity,
                    "field": item.relevant_field,
                    "value": _plain(item.observed_value),
                    "observed_at": item.observation_timestamp,
                    "freshness": item.source_freshness,
                    "conflict_status": item.conflict_status,
                    "excerpt": item.safe_excerpt,
                }
            )
        return {
            "schema_version": self.schema_version,
            "request_fingerprint": self.context.request_fingerprint,
            "analysis_fingerprint": self.context.analysis_fingerprint,
            "as_of_time": self.context.as_of_time,
            "scope": {
                "customers": list(self.context.selected_customers),
                "subscriptions": list(self.context.selected_subscriptions),
                "technologies": list(self.context.selected_technologies),
                "teams": list(self.context.selected_teams),
            },
            "customers": [
                {
                    "customer_id": item.customer_id,
                    "customer_name": item.customer_name,
                    "metrics": {metric.name: _plain(metric.value) for metric in item.metrics},
                    "findings": [finding.to_dict() for finding in item.findings],
                    "changes": [change.to_dict() for change in item.temporal_changes],
                    "actions": [action.to_dict() for action in item.recommended_actions],
                    "decision_brief": item.decision_brief.to_dict(),
                    "data_quality": item.data_quality.to_dict(),
                }
                for item in self.customers
            ],
            "portfolio": self.portfolio.to_dict(),
            "evidence": evidence_rows,
            "diagnostics": self.diagnostics.to_dict(),
        }


@dataclass
class AnalysisSources:
    """Authorized source frames for exactly one ``AnalysisRequest``.

    This is an ephemeral input object, not part of the immutable result.  Each
    frame is copied before normalization; callers retain ownership of the
    supplied DataFrames.
    """

    subscriptions: Optional[pd.DataFrame] = None
    adoption_barriers: Optional[pd.DataFrame] = None
    support_cases: Optional[pd.DataFrame] = None
    customer_pulse: Optional[pd.DataFrame] = None
    action_plans: Optional[pd.DataFrame] = None
    success_priorities: Optional[pd.DataFrame] = None
    external_incidents: Optional[
        Tuple[Mapping[str, Any], ...] | pd.DataFrame
    ] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AnalysisSources":
        def _frames(*names: str) -> Optional[pd.DataFrame]:
            candidates: List[pd.DataFrame] = []
            seen: set[int] = set()
            for name in names:
                raw = payload.get(name)
                raw_values = raw if isinstance(raw, (list, tuple)) else (raw,)
                for item in raw_values:
                    if isinstance(item, pd.DataFrame) and id(item) not in seen:
                        candidates.append(item)
                        seen.add(id(item))
            return _combine_source_frames(candidates)

        incidents_present = "external_incidents" in payload or "incidents" in payload
        incidents_raw = (
            payload.get("external_incidents")
            if "external_incidents" in payload
            else payload.get("incidents")
        )
        if isinstance(incidents_raw, pd.DataFrame):
            incidents = _collapse_duplicate_schema_columns(
                incidents_raw,
                label_style="incident",
            )
        elif isinstance(incidents_raw, (list, tuple)):
            incidents = tuple(item for item in incidents_raw if isinstance(item, Mapping))
        else:
            incidents = () if incidents_present else None
        return cls(
            subscriptions=_frames("subscriptions", "team_subs_df", "subscription_data"),
            adoption_barriers=_frames(
                "adoption_barriers",
                "ab_data",
                "ab_norm",
                "csconsole_adoption_barriers",
            ),
            support_cases=_frames(
                "support_cases",
                "csone_data",
                "csone_df",
                "support_cases_snowflake",
            ),
            customer_pulse=_frames(
                "customer_pulse",
                "csconsole_customer_pulse",
                "pulse_df",
            ),
            action_plans=_frames(
                "action_plans",
                "csconsole_action_plans",
                "ap_df",
            ),
            success_priorities=_frames(
                "success_priorities",
                "csconsole_success_priorities",
                "sp_df",
            ),
            external_incidents=incidents,
            metadata=dict(payload.get("source_metadata") or payload.get("metadata") or {}),
        )

    def frames(self) -> Dict[str, Optional[pd.DataFrame]]:
        return {
            "subscriptions": self.subscriptions,
            "adoption_barriers": self.adoption_barriers,
            "support_cases": self.support_cases,
            "customer_pulse": self.customer_pulse,
            "action_plans": self.action_plans,
            "success_priorities": self.success_priorities,
        }


def _combine_source_frames(frames: Sequence[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if not frames:
        return None
    copied = []
    attrs: Dict[str, Any] = {}
    fetch_errors: List[str] = []
    schema_diags: List[Mapping[str, Any]] = []
    for frame in frames:
        normalized = _collapse_duplicate_schema_columns(frame)
        if normalized is None:
            continue
        current = normalized.copy()
        current.attrs.update(getattr(normalized, "attrs", {}) or {})
        copied.append(current)
        frame_attrs = getattr(normalized, "attrs", {}) or {}
        for key, value in frame_attrs.items():
            attrs.setdefault(str(key), value)
        if frame_attrs.get("fetch_error"):
            fetch_errors.append(_clean_text(frame_attrs.get("fetch_error"), limit=400))
        schema_diag = frame_attrs.get("duplicate_schema_conflicts") or {}
        if schema_diag:
            schema_diags.append(schema_diag)
    if not copied:
        return None
    if len(copied) == 1:
        result = copied[0]
    else:
        result = pd.concat(copied, ignore_index=True, sort=False)
    result.attrs.update(attrs)
    if fetch_errors:
        result.attrs["fetch_error"] = "; ".join(dict.fromkeys(fetch_errors))
        result.attrs.setdefault("fetch_error_kind", "partial_source_failure")
    if schema_diags:
        conflicting_groups: Dict[str, int] = {}
        for diag in schema_diags:
            for group, count in dict(
                diag.get("conflicting_groups") or {}
            ).items():
                conflicting_groups[str(group)] = (
                    conflicting_groups.get(str(group), 0) + int(count or 0)
                )
        result.attrs["duplicate_schema_conflicts"] = {
            "policy": "coalesce_agreeing_quarantine_conflicting",
            "duplicate_group_count": sum(
                int(diag.get("duplicate_group_count", 0) or 0)
                for diag in schema_diags
            ),
            "conflict_count": sum(
                int(diag.get("conflict_count", 0) or 0)
                for diag in schema_diags
            ),
            "quarantined_rows": sum(
                int(diag.get("quarantined_rows", 0) or 0)
                for diag in schema_diags
            ),
            "conflicting_groups": dict(sorted(conflicting_groups.items())),
            "conflicting_customer_labels": sorted(
                {
                    str(label)
                    for diag in schema_diags
                    for label in (
                        diag.get("conflicting_customer_labels") or []
                    )
                }
            ),
            "conflicting_account_ids": sorted(
                {
                    str(account_id)
                    for diag in schema_diags
                    for account_id in (
                        diag.get("conflicting_account_ids") or []
                    )
                }
            ),
        }
    result.attrs["source_frame_count"] = len(copied)
    return result


def _schema_column_key(value: Any) -> str:
    try:
        text = unicodedata.normalize("NFKC", str(value))
    except Exception:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _cell_is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _NULL_TEXT
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _duplicate_cell_token(value: Any, *, schema_key: str) -> str:
    numeric_schema = (
        "id" not in schema_key
        and any(
            marker in schema_key
            for marker in (
                "score",
                "count",
                "rate",
                "percent",
                "amount",
                "risk",
                "rating",
                "priority",
                "severity",
            )
        )
    )
    datetime_schema = any(
        marker in schema_key
        for marker in (
            "date",
            "time",
            "updated",
            "modified",
            "created",
            "observed",
            "timestamp",
            "renewal",
            "expiration",
            "published",
        )
    )
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value).strip()
        if numeric_schema and re.fullmatch(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text
        ):
            try:
                return f"number:{format(Decimal(text).normalize(), 'f')}"
            except InvalidOperation:
                pass
        if datetime_schema and re.match(
            r"^\d{4}-\d{2}-\d{2}(?:[T\s]|$)", text
        ):
            try:
                timestamp = pd.Timestamp(text)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize("UTC")
                else:
                    timestamp = timestamp.tz_convert("UTC")
                return f"datetime:{timestamp.isoformat()}"
            except Exception:
                pass
        return f"str:{text}"
    if (
        isinstance(value, Number)
        and not isinstance(value, bool)
    ):
        try:
            return f"number:{format(Decimal(str(value)).normalize(), 'f')}"
        except InvalidOperation:
            pass
    if datetime_schema and isinstance(value, (datetime, pd.Timestamp)):
        try:
            timestamp = pd.Timestamp(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            return f"datetime:{timestamp.isoformat()}"
        except Exception:
            pass
    try:
        return f"{type(value).__name__}:{repr(value)}"
    except Exception:
        return f"{type(value).__module__}.{type(value).__qualname__}"


def _preferred_duplicate_position(
    columns: Sequence[Any],
    positions: Sequence[int],
    *,
    label_style: str,
) -> int:
    """Prefer the source-family canonical label deterministically."""

    def _score(position: int) -> Tuple[int, int, int, int]:
        label = str(columns[position])
        letters = [character for character in label if character.isalpha()]
        uppercase = bool(letters) and all(
            character.isupper() for character in letters
        )
        lowercase = bool(letters) and all(
            character.islower() for character in letters
        )
        return (
            int(lowercase if label_style == "incident" else uppercase),
            int(" " not in label),
            int("_" in label),
            -position,
        )

    return max(positions, key=_score)


def _collapse_duplicate_schema_columns(
    frame: Optional[pd.DataFrame],
    *,
    label_style: str = "legacy",
) -> Optional[pd.DataFrame]:
    """Collapse agreeing duplicate schema columns and quarantine conflicts.

    Pandas returns a DataFrame for ``frame[label]`` when a physical label is
    duplicated.  Many mature metric helpers reasonably expect a Series, so a
    request boundary normalizes duplicates once before identity resolution or
    canonical math.  Blank-plus-value and identical duplicates coalesce;
    rows with contradictory populated values are removed with diagnostics.
    """

    if frame is None:
        return None
    original_attrs = dict(getattr(frame, "attrs", {}) or {})
    columns = list(frame.columns)
    groups: Dict[str, List[int]] = {}
    group_order: List[str] = []
    for position, column in enumerate(columns):
        key = _schema_column_key(column) or f"__position_{position}"
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(position)
    duplicate_groups = {
        key: positions
        for key, positions in groups.items()
        if len(positions) > 1
    }
    if not duplicate_groups:
        result = frame.copy()
        result.attrs.update(original_attrs)
        return result

    conflict_positions: set[int] = set()
    coalesced_by_position: Dict[int, List[Any]] = {}
    conflict_groups: Dict[str, int] = {}
    for key, positions in duplicate_groups.items():
        representative_position = _preferred_duplicate_position(
            columns,
            positions,
            label_style=label_style,
        )
        chosen_values: List[Any] = []
        group_conflicts = 0
        for row_position in range(len(frame)):
            populated = [
                frame.iloc[row_position, position]
                for position in positions
                if not _cell_is_missing(frame.iloc[row_position, position])
            ]
            tokens = {
                _duplicate_cell_token(value, schema_key=key)
                for value in populated
            }
            if len(tokens) > 1:
                conflict_positions.add(row_position)
                group_conflicts += 1
            chosen_values.append(populated[0] if populated else None)
        coalesced_by_position[representative_position] = chosen_values
        if group_conflicts:
            conflict_groups[
                str(columns[representative_position])
            ] = group_conflicts

    keep_positions = [
        _preferred_duplicate_position(
            columns,
            groups[key],
            label_style=label_style,
        )
        for key in group_order
    ]
    result = frame.iloc[:, keep_positions].copy()
    result_position_by_original = {
        original_position: result_position
        for result_position, original_position in enumerate(keep_positions)
    }
    for original_position, values in coalesced_by_position.items():
        result.iloc[:, result_position_by_original[original_position]] = values
    if conflict_positions:
        retained_rows = [
            position
            for position in range(len(result))
            if position not in conflict_positions
        ]
        result = result.iloc[retained_rows].copy()
    customer_schema_keys = {
        "buname",
        "customer",
        "customername",
        "accountname",
        "relatedcustomerc",
        "customerdatanamec",
        "affectedcustomer",
    }
    account_schema_keys = {
        "accountidc",
        "accountc",
        "dsmaccountidc",
        "accountid",
        "account",
    }
    conflicting_customer_labels: set[str] = set()
    conflicting_account_ids: set[str] = set()
    for row_position in sorted(conflict_positions):
        row_labels = {
            unicodedata.normalize("NFKC", str(frame.iloc[row_position, position])).strip()
            for position, column in enumerate(columns)
            if _schema_column_key(column) in customer_schema_keys
            and isinstance(frame.iloc[row_position, position], str)
            and str(frame.iloc[row_position, position]).strip()
        }
        conflicting_customer_labels.update(row_labels)
        conflicting_account_ids.update(
            unicodedata.normalize(
                "NFKC",
                str(frame.iloc[row_position, position]),
            ).strip()
            for position, column in enumerate(columns)
            if _schema_column_key(column) in account_schema_keys
            and isinstance(frame.iloc[row_position, position], str)
            and str(frame.iloc[row_position, position]).strip()
        )
    result.attrs.update(original_attrs)
    result.attrs["duplicate_schema_conflicts"] = {
        "policy": "coalesce_agreeing_quarantine_conflicting",
        "duplicate_group_count": len(duplicate_groups),
        "conflict_count": len(conflict_positions),
        "quarantined_rows": len(conflict_positions),
        "conflicting_groups": dict(sorted(conflict_groups.items())),
        "conflicting_row_positions": sorted(conflict_positions),
        "conflicting_customer_labels": sorted(
            conflicting_customer_labels
        ),
        "conflicting_account_ids": sorted(conflicting_account_ids),
    }
    return result


def _duplicate_schema_conflict_customer_keys(
    frame: Optional[pd.DataFrame],
    *,
    customer_lookup: Optional[Mapping[str, Any]] = None,
) -> set[str]:
    if frame is None:
        return set()
    labels = (
        (getattr(frame, "attrs", {}) or {})
        .get("duplicate_schema_conflicts", {})
        .get("conflicting_customer_labels", [])
    )
    from data_normalization import (
        account_ids_equivalent,
        customer_ownership_key,
        load_customer_alias_registry,
    )

    registry = load_customer_alias_registry()
    keys = {
        key
        for label in labels
        if (key := customer_ownership_key(label, registry=registry))
    }
    diag = (getattr(frame, "attrs", {}) or {}).get(
        "duplicate_schema_conflicts", {}
    )
    account_ids = diag.get("conflicting_account_ids", []) or []
    lookup = customer_lookup or {}
    account_to_customer = lookup.get("account_to_customer", {}) or {}
    ambiguous_account_ids = lookup.get("ambiguous_account_ids", {}) or {}
    for account_id in account_ids:
        candidate_names: set[str] = set()
        for observed_id, customer_name in account_to_customer.items():
            if account_ids_equivalent(account_id, observed_id):
                candidate_names.add(str(customer_name))
        for observed_id, customer_names in ambiguous_account_ids.items():
            if account_ids_equivalent(account_id, observed_id):
                candidate_names.update(str(name) for name in customer_names)
        keys.update(
            key
            for name in candidate_names
            if (key := customer_ownership_key(name, registry=registry))
        )
    return keys


_SOURCE_ID_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "subscriptions": (
        "SUBSCRIPTION_ID",
        "SUBSCRIPTION_ID_C",
        "Subscription ID",
        "ID",
    ),
    "adoption_barriers": (
        "ID",
        "ADOPTION_BARRIER_ID",
        "BARRIER_ID",
        "AB_ID",
    ),
    "support_cases": (
        "SR_NUMBER",
        "Case #",
        "CASE_NUMBER",
        "CASE_ID",
        "CaseNumber",
        "ID",
    ),
    "customer_pulse": (
        "CUSTOMER_PULSE_ID_C",
        "PULSE_ID",
        "ID",
    ),
    "action_plans": (
        "ACTION_PLAN_ID",
        "ACTION_PLAN_ID_C",
        "ID",
    ),
    "success_priorities": (
        "SUCCESS_PRIORITY_ID",
        "SUCCESS_PRIORITY_ID_C",
        "ID",
    ),
    "external_incidents": ("id", "incident_id", "INCIDENT_ID"),
}

_SOURCE_VALUE_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "subscriptions": (
        "RENEWAL_RISK_LEVEL",
        "SUBSCRIPTION_STATUS",
        "STATUS",
        "END_DATE",
        "RENEWAL_DATE",
    ),
    "adoption_barriers": (
        "SEVERITY_C",
        "PRIORITY_C",
        "AB_STATUS_C",
        "STATUS_C",
        "TITLE_C",
    ),
    "support_cases": (
        "PRIORITY",
        "Severity",
        "SEVERITY",
        "STATUS",
        "Status",
        "case_status_norm",
    ),
    "customer_pulse": (
        "PULSE_RATING__C",
        "RATING_C",
        "SENTIMENT",
        "STATUS_C",
    ),
    "action_plans": (
        "STATUS_C",
        "AP_STATUS_C",
        "Status",
        "DUE_DATE_C",
        "DUE_DATE",
    ),
    "success_priorities": (
        "STATUS_C",
        "PRIORITY_C",
        "TITLE_C",
        "NAME",
    ),
    "external_incidents": ("impact_level", "status", "title"),
}

_SOURCE_TIMESTAMP_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "subscriptions": (
        "LAST_MODIFIED_DATE",
        "UPDATED_AT",
        "RENEWAL_DATE",
        "END_DATE",
        "SUBSCRIPTION_END_DATE",
    ),
    "adoption_barriers": (
        "LAST_MODIFIED_DATE",
        "LASTMODIFIEDDATE",
        "UPDATED_AT",
        "CREATED_DATE",
        "CREATEDDATE",
        "CLOSED_DATE_C",
    ),
    "support_cases": (
        "LAST_MODIFIED_DATE",
        "LASTMODIFIEDDATE",
        "UPDATED_AT",
        "Date/Time Opened",
        "OPEN_DATE",
        "Date/Time Closed",
        "CLOSED_DATE",
    ),
    "customer_pulse": (
        "LAST_MODIFIED_DATE",
        "LASTMODIFIEDDATE",
        "UPDATED_AT",
        "PULSE_DATE_C",
        "CREATED_DATE",
    ),
    "action_plans": (
        "LAST_MODIFIED_DATE",
        "LASTMODIFIEDDATE",
        "UPDATED_AT",
        "DUE_DATE_C",
        "DUE_DATE",
        "CREATED_DATE",
    ),
    "success_priorities": (
        "LAST_MODIFIED_DATE",
        "LASTMODIFIEDDATE",
        "UPDATED_AT",
        "CREATED_DATE",
    ),
    "external_incidents": ("updated_at", "published", "created_at"),
}

_SUBSCRIPTION_COLUMNS = (
    "SUBSCRIPTION_ID",
    "SUBSCRIPTION_ID_C",
    "Subscription ID",
)
_TECHNOLOGY_COLUMNS = (
    "TECHNOLOGY",
    "TECHNOLOGY_C",
    "SUB_TECHNOLOGY",
    "SUB_TECHNOLOGY_C",
    "Technology",
)
_TEAM_COLUMNS = (
    "TEAM",
    "TEAM_NAME",
    "MANAGER",
    "MANAGER_NAME",
    "CSSM_EMAIL",
    "CSSM_NAME",
    "OWNER_EMAIL",
)
_TEAM_NAME_SCOPE_COLUMNS = (
    "TEAM",
    "TEAM_NAME",
    "CSSM_NAME",
)
_TEAM_EMAIL_SCOPE_COLUMNS = (
    "CSSM_EMAIL",
    "OWNER_EMAIL",
)
_LEADER_SCOPE_COLUMNS = (
    "MANAGER",
    "MANAGER_NAME",
    "CSSM_MANAGER",
)
_RENEWAL_DATE_COLUMNS = (
    "RENEWAL_DATE",
    "SUBSCRIPTION_END_DATE",
    "END_DATE",
    "CONTRACT_END_DATE",
    "EXPIRATION_DATE",
)
_ACTION_DUE_COLUMNS = (
    "DUE_DATE_C",
    "DUE_DATE",
    "TARGET_DATE_C",
    "TARGET_DATE",
)


def _matching_columns(columns: Iterable[Any], candidates: Sequence[str]) -> List[str]:
    try:
        from data_normalization import matching_schema_columns

        return [str(value) for value in matching_schema_columns(columns, candidates)]
    except Exception:
        normalized = {
            re.sub(r"[^a-z0-9]+", "", str(column).casefold()): str(column)
            for column in columns
        }
        result = []
        for candidate in candidates:
            match = normalized.get(re.sub(r"[^a-z0-9]+", "", candidate.casefold()))
            if match and match not in result:
                result.append(match)
        return result


def _matching_positions(
    columns: Iterable[Any], candidates: Sequence[str]
) -> List[int]:
    """Match every physical column while preserving candidate priority."""

    column_values = list(columns)
    try:
        from data_normalization import matching_schema_column_positions

        positions: List[int] = []
        seen: set[int] = set()
        for candidate in candidates:
            for position in matching_schema_column_positions(
                column_values,
                (candidate,),
            ):
                if position not in seen:
                    positions.append(position)
                    seen.add(position)
        return positions
    except Exception:
        normalized = [
            re.sub(r"[^a-z0-9]+", "", str(column).casefold())
            for column in column_values
        ]
        positions = []
        seen = set()
        for candidate in candidates:
            key = re.sub(r"[^a-z0-9]+", "", candidate.casefold())
            for position, column_key in enumerate(normalized):
                if column_key == key and position not in seen:
                    positions.append(position)
                    seen.add(position)
        return positions


def _first_value(row: pd.Series, candidates: Sequence[str]) -> Tuple[str, str]:
    for position in _matching_positions(row.index, candidates):
        value = _clean_text(row.iloc[position], limit=240)
        if value:
            actual_key = _schema_column_key(row.index[position])
            canonical_label = next(
                (
                    candidate
                    for candidate in candidates
                    if _schema_column_key(candidate) == actual_key
                ),
                str(row.index[position]),
            )
            return canonical_label, value
    return "", ""


def _latest_timestamp(frame: Optional[pd.DataFrame], source_type: str) -> str:
    if frame is None or frame.empty:
        return ""
    # Candidate order is authoritative: modification/observation timestamps
    # precede business milestone dates such as renewal or Action Plan due
    # dates.  Mixing every date and taking the maximum made a future renewal
    # falsely refresh otherwise stale source evidence.
    for position in _matching_positions(
        frame.columns,
        _SOURCE_TIMESTAMP_COLUMNS[source_type],
    ):
        try:
            parsed = pd.to_datetime(
                frame.iloc[:, position], errors="coerce", utc=True
            ).dropna()
        except Exception:
            continue
        if not parsed.empty:
            return _iso_z(parsed.max().to_pydatetime())
    return ""


def _ingestion_timestamp(frame: Optional[pd.DataFrame], metadata: Mapping[str, Any], source_type: str) -> str:
    source_meta = metadata.get(source_type) if isinstance(metadata.get(source_type), Mapping) else {}
    for value in (
        source_meta.get("ingestion_timestamp") if source_meta else None,
        source_meta.get("retrieved_at") if source_meta else None,
        (getattr(frame, "attrs", {}) or {}).get("ingestion_timestamp") if frame is not None else None,
        (getattr(frame, "attrs", {}) or {}).get("retrieved_at") if frame is not None else None,
        metadata.get("ingestion_timestamp"),
        metadata.get("retrieved_at"),
    ):
        normalized = _iso_z(value)
        if normalized:
            return normalized
    return ""


def _days_between(later: str, earlier: str) -> Optional[int]:
    try:
        later_dt = datetime.fromisoformat(later.replace("Z", "+00:00"))
        earlier_dt = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
        return max(0, int((later_dt - earlier_dt).total_seconds() // 86400))
    except (TypeError, ValueError):
        return None


def _freshness_state(
    frame: Optional[pd.DataFrame],
    source_type: str,
    *,
    as_of_time: str,
    metadata: Mapping[str, Any],
    stale_after_days: int,
) -> str:
    observed = _latest_timestamp(frame, source_type)
    if not observed:
        observed = _ingestion_timestamp(frame, metadata, source_type)
    if not observed:
        return "unknown"
    age = _days_between(as_of_time, observed)
    if age is None:
        return "unknown"
    return "stale" if age > stale_after_days else "current"


def _source_state(frame: Optional[pd.DataFrame], *, allowed: bool) -> str:
    if not allowed:
        return "excluded_by_request"
    if frame is None:
        return "missing"
    attrs = getattr(frame, "attrs", {}) or {}
    if attrs.get("fetch_error"):
        return "fetch_failed"
    diag = attrs.get("cross_customer_id_conflicts") or {}
    schema_diag = attrs.get("duplicate_schema_conflicts") or {}
    if frame.empty and (
        int(diag.get("quarantined_rows", 0) or 0) > 0
        or int(schema_diag.get("quarantined_rows", 0) or 0) > 0
    ):
        return "conflict_only"
    if frame.empty:
        return "observed_empty"
    return "available"


def _canonicalize_source_frame(source_type: str, frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    if source_type in {"support_cases", "customer_pulse", "action_plans"}:
        # These sources have authoritative, source-specific conflict and
        # tie-break rules.  If that logic is unavailable or rejects malformed
        # input, fail the bundle so the caller can expose its explicit legacy
        # fallback.  Silently replacing it with generic latest-ID dedupe would
        # still label the result canonical while changing which row wins.
        import canonical_metrics as cm

        if source_type == "support_cases":
            return cm.deduplicate_tac_cases(frame)
        if source_type == "customer_pulse":
            return cm.deduplicate_customer_pulse(frame)
        return cm.deduplicate_action_plans(frame)
    if frame.empty:
        return frame.copy()
    safe = frame.copy()
    safe.attrs.update(getattr(frame, "attrs", {}) or {})
    id_positions = _matching_positions(
        safe.columns,
        _SOURCE_ID_COLUMNS[source_type],
    )
    if not id_positions:
        return safe
    identifiers = pd.Series(
        ["" for _ in range(len(safe))], index=safe.index, dtype="string"
    )
    for position in id_positions:
        values = safe.iloc[:, position].map(_clean_text).astype("string")
        identifiers = identifiers.mask(identifiers.eq(""), values)
    with_id = safe.loc[identifiers.ne("")].copy()
    without_id = safe.loc[identifiers.eq("")].copy()
    if not with_id.empty:
        with_id["__decision_id"] = identifiers.loc[identifiers.ne("")].str.upper().values
        timestamps = pd.Series(pd.NaT, index=with_id.index, dtype="datetime64[ns, UTC]")
        for position in _matching_positions(
            with_id.columns,
            _SOURCE_TIMESTAMP_COLUMNS[source_type],
        ):
            parsed = pd.to_datetime(
                with_id.iloc[:, position], errors="coerce", utc=True
            )
            timestamps = timestamps.fillna(parsed)
        with_id["__decision_ts"] = timestamps
        with_id["__decision_sig"] = with_id.astype(str).apply(
            lambda row: hashlib.sha256(
                "\x1f".join(f"{key}={row[key]}" for key in sorted(row.index)).encode("utf-8")
            ).hexdigest(),
            axis=1,
        )
        with_id = (
            with_id.sort_values(
                ["__decision_id", "__decision_ts", "__decision_sig"],
                ascending=[True, False, True],
                kind="mergesort",
                na_position="last",
            )
            .drop_duplicates("__decision_id", keep="first")
            .drop(columns=["__decision_id", "__decision_ts", "__decision_sig"])
        )
    result = pd.concat([with_id, without_id], axis=0).sort_index(kind="mergesort")
    result.attrs.update(getattr(safe, "attrs", {}) or {})
    return result


def _row_signature(row: pd.Series, source_type: str) -> str:
    selected: Dict[str, str] = {}
    candidates = (
        _SOURCE_ID_COLUMNS[source_type]
        + _SOURCE_VALUE_COLUMNS[source_type]
        + _SOURCE_TIMESTAMP_COLUMNS[source_type]
    )
    candidate_labels: Dict[str, str] = {}
    for candidate in candidates:
        schema_key = _schema_column_key(candidate)
        if schema_key:
            candidate_labels.setdefault(schema_key, str(candidate))
    physical_labels = [str(value) for value in row.index]
    canonical_labels = [
        candidate_labels.get(
            _schema_column_key(label),
            _schema_column_key(label) or label,
        )
        for label in physical_labels
    ]
    label_counts = Counter(canonical_labels)
    for position in _matching_positions(row.index, candidates):
        column = canonical_labels[position]
        key = (
            column
            if label_counts[column] == 1
            else f"{column}[{position}]"
        )
        value = _clean_text(row.iloc[position], limit=240)
        if value:
            selected[key] = value
    if not selected:
        for position, column in sorted(
            enumerate(canonical_labels), key=lambda item: (item[1], item[0])
        ):
            key = (
                column
                if label_counts[column] == 1
                else f"{column}[{position}]"
            )
            value = _clean_text(row.iloc[position], limit=120)
            if value:
                selected[key] = value
            if len(selected) >= 8:
                break
    return hashlib.sha256(_canonical_json(selected).encode("utf-8")).hexdigest()[:24]


def _record_evidence(
    *,
    source_type: str,
    frame: Optional[pd.DataFrame],
    customer_id: str,
    source_state: str,
    freshness: str,
    ingestion_time: str,
    authority: str,
) -> Tuple[EvidenceReference, ...]:
    records: List[EvidenceReference] = []
    state_id = _safe_id(source_type, customer_id, "source-state", prefix="evidence")
    records.append(
        EvidenceReference(
            evidence_id=state_id,
            source_type=source_type,
            source_record="source-state",
            stable_source_identifier=f"{source_type}:source-state",
            customer_identity=customer_id,
            relevant_field="availability",
            observed_value=source_state,
            observation_timestamp=_latest_timestamp(frame, source_type),
            ingestion_timestamp=ingestion_time,
            source_freshness=freshness,
            source_authority="analysis_source_metadata",
            source_scope="customer",
            conflict_status=(
                "quarantined"
                if source_state in {"conflict_only", "ownership_conflict"}
                else "none"
            ),
            provenance={"state_evidence": True},
        )
    )
    canonical = _canonicalize_source_frame(source_type, frame)
    for position in range(len(canonical)):
        row = canonical.iloc[position]
        id_column, source_id = _first_value(row, _SOURCE_ID_COLUMNS[source_type])
        if not source_id:
            source_id = f"ROW-{_row_signature(row, source_type)}"
        value_column, observed_value = _first_value(row, _SOURCE_VALUE_COLUMNS[source_type])
        timestamp_column, timestamp_value = _first_value(row, _SOURCE_TIMESTAMP_COLUMNS[source_type])
        evidence_id = _safe_id(source_type, source_id.casefold(), customer_id, prefix="evidence")
        excerpt_parts = []
        if id_column:
            excerpt_parts.append(f"{id_column}={source_id}")
        if value_column:
            excerpt_parts.append(f"{value_column}={observed_value}")
        records.append(
            EvidenceReference(
                evidence_id=evidence_id,
                source_type=source_type,
                source_record=id_column or "derived-row-id",
                stable_source_identifier=source_id,
                customer_identity=customer_id,
                subscription_identity=(source_id if source_type == "subscriptions" else ""),
                relevant_field=value_column,
                observed_value=observed_value or None,
                observation_timestamp=timestamp_value,
                ingestion_timestamp=ingestion_time,
                source_freshness=freshness,
                source_authority=authority,
                source_scope="customer",
                conflict_status="none",
                safe_excerpt="; ".join(excerpt_parts),
                provenance={
                    "id_column": id_column,
                    "timestamp_column": timestamp_column,
                    "canonicalized": True,
                },
            )
        )
    return tuple(records)


def _filter_incidents_for_customer(
    incidents: Sequence[Mapping[str, Any]], customer_id: str, customer_name: str
) -> Tuple[Mapping[str, Any], ...]:
    if not incidents:
        return ()
    customer_fields = (
        "customer",
        "customer_name",
        "BU_NAME",
        "CUSTOMER_NAME",
        "affected_customer",
    )
    try:
        from data_normalization import customer_identity_key

        target_keys = {customer_id, customer_identity_key(customer_name)}
        return tuple(
            item
            for item in incidents
            if not any(_clean_text(item.get(field)) for field in customer_fields)
            or any(
                customer_identity_key(item.get(field)) in target_keys
                for field in customer_fields
            )
        )
    except Exception:
        target = customer_name.casefold()
        return tuple(
            item
            for item in incidents
            if not any(_clean_text(item.get(field)) for field in customer_fields)
            or any(
                _clean_text(item.get(field)).casefold() == target
                for field in customer_fields
            )
        )


def _incidents_frame(
    incidents: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> Optional[pd.DataFrame]:
    if incidents is None:
        return None
    if isinstance(incidents, pd.DataFrame):
        result = incidents.copy()
        result.attrs.update(getattr(incidents, "attrs", {}) or {})
        return result
    frame = pd.DataFrame(list(incidents))
    return frame


def _values_from_frame(frame: Optional[pd.DataFrame], candidates: Sequence[str]) -> Tuple[str, ...]:
    if frame is None or frame.empty:
        return ()
    values: List[str] = []
    for position in _matching_positions(frame.columns, candidates):
        values.extend(
            _clean_text(value)
            for value in frame.iloc[:, position].tolist()
        )
    return _tuple_text(value for value in values if value)


def _days_until_nearest(frame: Optional[pd.DataFrame], candidates: Sequence[str], as_of_time: str) -> Optional[int]:
    if frame is None or frame.empty:
        return None
    dates: List[datetime] = []
    for position in _matching_positions(frame.columns, candidates):
        parsed = pd.to_datetime(
            frame.iloc[:, position], errors="coerce", utc=True
        ).dropna()
        dates.extend(value.to_pydatetime() for value in parsed)
    if not dates:
        return None
    try:
        as_of = datetime.fromisoformat(as_of_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    future = [value for value in dates if value >= as_of]
    target = min(future) if future else max(dates)
    return int((target - as_of).total_seconds() // 86400)


def _count_overdue_actions(frame: Optional[pd.DataFrame], as_of_time: str) -> int:
    if frame is None or frame.empty:
        return 0
    due_positions = _matching_positions(frame.columns, _ACTION_DUE_COLUMNS)
    if not due_positions:
        return 0
    due = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    for position in due_positions:
        due = due.fillna(
            pd.to_datetime(
                frame.iloc[:, position], errors="coerce", utc=True
            )
        )
    try:
        as_of = pd.Timestamp(as_of_time)
    except Exception:
        return 0
    closed = pd.Series(False, index=frame.index)
    status_positions = _matching_positions(
        frame.columns,
        ("STATUS_C", "AP_STATUS_C", "Status", "STATUS", "status", "status_norm"),
    )
    for position in status_positions:
        closed |= frame.iloc[:, position].fillna("").astype(str).str.casefold().str.contains(
            r"completed|closed|resolved|cancelled|canceled",
            regex=True,
        )
    return int(((due < as_of) & due.notna() & ~closed).sum())


def _confidence_rank(value: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(_clean_text(value).upper(), 1)


def _source_evidence_ids(
    records: Sequence[EvidenceReference], source_type: str, *, include_state: bool = True
) -> Tuple[str, ...]:
    return tuple(
        item.evidence_id
        for item in records
        if item.source_type == source_type
        and (include_state or item.source_record != "source-state")
    )


def _observation_range(
    records: Sequence[EvidenceReference], evidence_ids: Sequence[str]
) -> Tuple[str, str]:
    allowed = set(evidence_ids)
    timestamps = sorted(
        item.observation_timestamp
        for item in records
        if item.evidence_id in allowed and item.observation_timestamp
    )
    if not timestamps:
        return "", ""
    return timestamps[0], timestamps[-1]


def _scope_safe_identity_frame(
    frame: Optional[pd.DataFrame],
) -> Optional[pd.DataFrame]:
    """Blank non-string customer/account cells before identity resolution."""

    if frame is None:
        return None
    from data_normalization import (
        LIKELY_ACCOUNT_ID_COLS,
        LIKELY_CUSTOMER_COLS,
        account_ids_equivalent,
        customer_ownership_key,
        load_customer_alias_registry,
        strict_scope_text,
    )

    safe = frame.copy()
    safe.attrs.update(getattr(frame, "attrs", {}) or {})
    customer_positions = _matching_positions(
        safe.columns,
        tuple(LIKELY_CUSTOMER_COLS),
    )
    account_positions = _matching_positions(
        safe.columns,
        tuple(LIKELY_ACCOUNT_ID_COLS),
    )
    positions = list(dict.fromkeys(customer_positions + account_positions))
    for position in positions:
        normalized: List[Any] = []
        for raw in safe.iloc[:, position].tolist():
            valid, value = strict_scope_text(raw)
            normalized.append(value if valid and value else pd.NA)
        safe.isetitem(
            position,
            pd.Series(
                normalized,
                index=safe.index,
                dtype=object,
            ),
        )

    registry = load_customer_alias_registry()
    conflict_positions: List[int] = []
    for row_position in range(len(safe)):
        customer_keys = {
            customer_ownership_key(
                safe.iloc[row_position, column_position],
                registry=registry,
            )
            for column_position in customer_positions
            if not pd.isna(safe.iloc[row_position, column_position])
        }
        customer_keys.discard("")
        account_values = [
            str(safe.iloc[row_position, column_position])
            for column_position in account_positions
            if not pd.isna(safe.iloc[row_position, column_position])
        ]
        account_conflict = bool(account_values) and not all(
            account_ids_equivalent(account_values[0], value)
            for value in account_values[1:]
        )
        if len(customer_keys) > 1 or account_conflict:
            conflict_positions.append(row_position)

    # Contradictory populated aliases cannot be resolved by column order or
    # by an otherwise-valid account lookup.  Blank every identity route in
    # this private view so lookup construction cannot reuse the row.
    for row_position in conflict_positions:
        for column_position in positions:
            safe.iat[row_position, column_position] = pd.NA
    safe.attrs["scope_identity_conflict_positions"] = conflict_positions
    return safe


def _account_equivalence_key(value: Any) -> str:
    """Return the core ownership key used by account-scope matching.

    The shared comparator treats non-Salesforce account IDs as
    case-insensitive full tokens, while a checksum-valid Salesforce 18-char
    ID is equivalent to its case-sensitive 15-char prefix.  Customer lookup
    ownership must use that same relation or two spellings of one account can
    acquire different owners before an account-only request is evaluated.
    """

    from data_normalization import (
        clean_logical_record_id,
        salesforce_18_id_prefix,
    )

    token = clean_logical_record_id(value)
    if not token:
        return ""
    salesforce_prefix = salesforce_18_id_prefix(token)
    if salesforce_prefix:
        return f"salesforce:{salesforce_prefix}"
    if len(token) == 15 and re.fullmatch(r"[A-Za-z0-9]+", token):
        return f"salesforce:{token}"
    return f"generic:{token.casefold()}"


def _equivalence_aware_customer_lookup(
    lookup: Mapping[str, Any],
    *ownership_sources: Any,
) -> Dict[str, Any]:
    """Add a conservative, core-local account ownership equivalence index."""

    from data_normalization import (
        customer_ownership_key,
        load_customer_alias_registry,
    )

    result = dict(lookup)
    registry = load_customer_alias_registry()
    owners_by_equivalence: Dict[str, Dict[str, str]] = {}

    def _observe(account_id: Any, customer_name: Any) -> None:
        account_key = _account_equivalence_key(account_id)
        owner_key = customer_ownership_key(
            customer_name,
            registry=registry,
        )
        if not account_key or not owner_key:
            return
        display = _clean_text(customer_name)
        current = owners_by_equivalence.setdefault(account_key, {}).get(
            owner_key
        )
        if current is None or (
            len(display), display.casefold(), display
        ) > (
            len(current), current.casefold(), current
        ):
            owners_by_equivalence[account_key][owner_key] = display

    inputs = (lookup,) + ownership_sources
    for source in inputs:
        if not isinstance(source, Mapping):
            continue
        for account_id, customer_name in dict(
            source.get("account_to_customer", {}) or {}
        ).items():
            _observe(account_id, customer_name)
        for account_id, customer_names in dict(
            source.get("ambiguous_account_ids", {}) or {}
        ).items():
            if isinstance(customer_names, str):
                customer_names = (customer_names,)
            try:
                values = tuple(customer_names or ())
            except TypeError:
                values = ()
            for customer_name in values:
                _observe(account_id, customer_name)

    equivalent_owners = {
        account_key: tuple(
            display
            for _, display in sorted(
                owner_map.items(),
                key=lambda item: (
                    item[1].casefold(),
                    item[1],
                    item[0],
                ),
            )
        )
        for account_key, owner_map in owners_by_equivalence.items()
    }
    result["_equivalent_account_owners"] = equivalent_owners

    conflicts = {
        account_key: owners
        for account_key, owners in equivalent_owners.items()
        if len(owners) > 1
    }
    if conflicts:
        warnings = list(result.get("warnings") or ())
        collisions = list(result.get("collisions") or ())
        for account_key, owners in sorted(conflicts.items()):
            warning = (
                "equivalent account identifier ownership conflict for "
                f"{account_key!r}: {list(owners)!r}; account scope fails closed"
            )
            if warning not in warnings:
                warnings.append(warning)
            collision = {
                "kind": "equivalent_account_to_customer",
                "key": account_key,
                "alternatives": list(owners),
            }
            if collision not in collisions:
                collisions.append(collision)
        result["warnings"] = warnings
        result["collisions"] = collisions
    return result


def _equivalent_account_owners(
    customer_lookup: Mapping[str, Any],
    account_id: Any,
) -> Tuple[str, ...]:
    account_key = _account_equivalence_key(account_id)
    if not account_key:
        return ()
    owners = (
        customer_lookup.get("_equivalent_account_owners", {}) or {}
    ).get(account_key, ())
    if isinstance(owners, str):
        return (owners,)
    try:
        return tuple(str(value) for value in owners if _clean_text(value))
    except TypeError:
        return ()


def _resolve_customer_name_core(
    row: pd.Series,
    customer_lookup: Mapping[str, Any],
    *,
    registry: Any,
) -> str:
    """Resolve account aliases through the equivalence-aware core index."""

    from data_normalization import (
        LIKELY_ACCOUNT_ID_COLS,
        LIKELY_CUSTOMER_COLS,
        customer_ownership_key,
        resolve_customer_name,
    )

    account_values = _strict_scope_values(row, LIKELY_ACCOUNT_ID_COLS)
    if account_values is None:
        return "Unknown"
    owner_names = {
        owner
        for account_id in account_values
        for owner in _equivalent_account_owners(
            customer_lookup,
            account_id,
        )
    }
    owner_names_by_key = {
        customer_ownership_key(owner, registry=registry): owner
        for owner in owner_names
        if customer_ownership_key(owner, registry=registry)
    }
    if len(owner_names_by_key) == 1:
        return next(iter(owner_names_by_key.values()))
    if len(owner_names_by_key) > 1:
        explicit_values = _strict_scope_values(row, LIKELY_CUSTOMER_COLS)
        if explicit_values is None:
            return "Unknown"
        explicit_by_key = {
            customer_ownership_key(value, registry=registry): value
            for value in explicit_values
            if customer_ownership_key(value, registry=registry)
        }
        matching_keys = set(explicit_by_key).intersection(
            owner_names_by_key
        )
        if len(explicit_by_key) == 1 and len(matching_keys) == 1:
            return explicit_by_key[next(iter(matching_keys))]
        return "Unknown"
    return resolve_customer_name(
        row,
        dict(customer_lookup),
        registry=registry,
    )


def _partition_source_strict(
    frame: Optional[pd.DataFrame],
    *,
    customer_lookup: Mapping[str, Any],
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Tuple[str, ...]]]:
    """Quarantine once, then partition by conservative ownership identity.

    ``data_normalization.partition_customer_frame`` intentionally uses a
    fuzzy join key for legacy report compatibility.  A canonical ownership
    boundary cannot use that key because it removes legal suffixes (``Inc``
    and ``LLC``).  This V2 partition keeps those entities distinct while
    configured aliases still share an alias-group key.
    """

    from data_normalization import (
        LIKELY_ACCOUNT_ID_COLS,
        LIKELY_CUSTOMER_COLS,
        customer_ownership_key,
        load_customer_alias_registry,
        quarantine_cross_customer_record_ids,
    )

    if frame is None:
        return pd.DataFrame(), {}, {}, {}
    original = frame.copy()
    original.attrs.update(getattr(frame, "attrs", {}) or {})
    if original.empty:
        safe = quarantine_cross_customer_record_ids(original, customer_lookup=dict(customer_lookup))
        return safe, {}, {}, {}
    identity_frame = _scope_safe_identity_frame(original)
    if identity_frame is None:
        identity_frame = original
    registry = load_customer_alias_registry()
    identity_conflict_positions = set(
        (getattr(identity_frame, "attrs", {}) or {}).get(
            "scope_identity_conflict_positions"
        )
        or []
    )
    customer_positions = _matching_positions(
        identity_frame.columns,
        LIKELY_CUSTOMER_COLS,
    )
    account_positions = _matching_positions(
        identity_frame.columns,
        LIKELY_ACCOUNT_ID_COLS,
    )
    resolved_names: List[str] = []
    ownership_keys: List[str] = []
    aliases: Dict[str, List[str]] = {}
    display_names: Dict[str, str] = {}
    for position in range(len(original)):
        row = identity_frame.iloc[position]
        resolved = "Unknown"
        key = ""
        if position not in identity_conflict_positions:
            customer_only = row.copy()
            for column_position in account_positions:
                customer_only.iloc[column_position] = pd.NA
            explicit_owner = _resolve_customer_name_core(
                customer_only,
                customer_lookup,
                registry=registry,
            )
            explicit_key = customer_ownership_key(
                explicit_owner,
                registry=registry,
            )

            account_only = row.copy()
            for column_position in customer_positions:
                account_only.iloc[column_position] = pd.NA
            account_owner = _resolve_customer_name_core(
                account_only,
                customer_lookup,
                registry=registry,
            )
            account_key = customer_ownership_key(
                account_owner,
                registry=registry,
            )
            if explicit_key and account_key and explicit_key != account_key:
                identity_conflict_positions.add(position)
            else:
                resolved = _resolve_customer_name_core(
                    row,
                    customer_lookup,
                    registry=registry,
                )
                key = customer_ownership_key(resolved, registry=registry)
        resolved_names.append(resolved)
        ownership_keys.append(key)
        if key:
            aliases.setdefault(key, []).append(resolved)
            current = display_names.get(key)
            if current is None or (len(resolved), resolved.casefold(), resolved) > (
                len(current),
                current.casefold(),
                current,
            ):
                display_names[key] = resolved
    safe = quarantine_cross_customer_record_ids(
        original,
        customer_lookup=dict(customer_lookup),
        customer_columns=LIKELY_CUSTOMER_COLS,
        account_columns=LIKELY_ACCOUNT_ID_COLS,
        resolved_customer_keys=pd.Series(ownership_keys, dtype=str),
        registry=registry,
    )
    quarantined_positions = set(
        (getattr(safe, "attrs", {}) or {}).get("_last_cross_customer_quarantined_positions")
        or []
    )
    cross_safe_original_positions = [
        position for position in range(len(original)) if position not in quarantined_positions
    ]
    if identity_conflict_positions:
        safe_attrs = dict(getattr(safe, "attrs", {}) or {})
        keep_safe_positions = [
            safe_position
            for safe_position, original_position in enumerate(
                cross_safe_original_positions
            )
            if original_position not in identity_conflict_positions
        ]
        safe = safe.iloc[keep_safe_positions].copy()
        safe.attrs.update(safe_attrs)
        safe.attrs["scope_identity_conflicts"] = {
            "quarantined_rows": len(identity_conflict_positions),
            "row_positions": sorted(identity_conflict_positions)[:100],
        }
    original_positions = [
        position
        for position in cross_safe_original_positions
        if position not in identity_conflict_positions
    ]
    positions_by_key: Dict[str, List[int]] = {}
    for safe_position, original_position in enumerate(original_positions):
        key = ownership_keys[original_position]
        if key:
            positions_by_key.setdefault(key, []).append(safe_position)
    partitions: Dict[str, pd.DataFrame] = {}
    diag = dict((getattr(safe, "attrs", {}) or {}).get("cross_customer_id_conflicts") or {})
    for key, positions in positions_by_key.items():
        partition = safe.iloc[positions].copy()
        partition.attrs.update(getattr(safe, "attrs", {}) or {})
        partition.attrs["cross_customer_id_conflicts"] = dict(diag)
        partition.attrs["canonical_partition_customer_id"] = key
        partition.attrs["canonical_partition_provenance"] = "decision-intelligence-v2"
        partitions[key] = partition
    alias_result = {key: _tuple_text(values) for key, values in aliases.items()}
    return safe, partitions, display_names, alias_result


def _conflict_customer_keys(
    original: Optional[pd.DataFrame],
    safe: Optional[pd.DataFrame],
    *,
    customer_lookup: Mapping[str, Any],
) -> set[str]:
    if original is None or original.empty or safe is None:
        return set()
    from data_normalization import (
        LIKELY_LOGICAL_RECORD_ID_COLS,
        coalesce_logical_record_ids,
        customer_ownership_key,
        load_customer_alias_registry,
    )

    identifiers, _ = coalesce_logical_record_ids(original, LIKELY_LOGICAL_RECORD_ID_COLS)
    registry = load_customer_alias_registry()
    row_owners: List[str] = []
    owners_by_identifier: Dict[str, set[str]] = {}
    for position in range(len(original)):
        resolved = _resolve_customer_name_core(
            original.iloc[position], customer_lookup, registry=registry
        )
        key = customer_ownership_key(resolved, registry=registry)
        row_owners.append(key)
        identifier = _clean_text(identifiers.iloc[position]).upper()
        if identifier and key:
            owners_by_identifier.setdefault(identifier, set()).add(key)
    conflicting = {
        identifier
        for identifier, owners in owners_by_identifier.items()
        if len(owners) > 1
    }
    return {
        owner
        for position, owner in enumerate(row_owners)
        if owner
        and _clean_text(identifiers.iloc[position]).upper() in conflicting
    }


def _row_matches_any(row: pd.Series, candidates: Sequence[str], requested: Sequence[str]) -> bool:
    wanted = {_clean_text(value).casefold() for value in requested if _clean_text(value)}
    if not wanted:
        return True
    for position in _matching_positions(row.index, candidates):
        raw = _clean_text(row.iloc[position]).casefold()
        if raw in wanted:
            return True
    return False


def _strict_scope_values(
    row: pd.Series,
    candidates: Sequence[str],
) -> Optional[List[str]]:
    """Return populated string scope cells, or ``None`` for invalid cells."""

    from data_normalization import strict_scope_text

    values: List[str] = []
    for position in _matching_positions(row.index, candidates):
        valid, value = strict_scope_text(row.iloc[position])
        if not valid:
            return None
        if value:
            values.append(value)
    return values


def _row_values_match_scope_conjunctive(
    row: pd.Series,
    candidates: Sequence[str],
    requested: Sequence[str],
) -> bool:
    """Require every populated physical alias to match an exact scope value."""

    wanted = {
        _clean_text(value).casefold()
        for value in requested
        if _clean_text(value)
    }
    if not wanted:
        return True
    strict_values = _strict_scope_values(row, candidates)
    if strict_values is None:
        return False
    values = [value.casefold() for value in strict_values]
    return bool(values) and all(value in wanted for value in values)


def _row_team_matches_scope(
    row: pd.Series,
    requested: Sequence[str],
) -> bool:
    """Match team ownership within comparable email or name classes."""

    requested_emails = {
        _clean_text(value).casefold()
        for value in requested
        if "@" in _clean_text(value)
    }
    requested_names = {
        _clean_text(value).casefold()
        for value in requested
        if _clean_text(value) and "@" not in _clean_text(value)
    }
    strict_email_values = _strict_scope_values(
        row,
        _TEAM_EMAIL_SCOPE_COLUMNS,
    )
    strict_name_values = _strict_scope_values(
        row,
        _TEAM_NAME_SCOPE_COLUMNS,
    )
    if strict_email_values is None or strict_name_values is None:
        return False
    email_values = [value.casefold() for value in strict_email_values]
    name_values = [value.casefold() for value in strict_name_values]
    email_match = bool(requested_emails and email_values) and all(
        value in requested_emails for value in email_values
    )
    name_match = bool(requested_names and name_values) and all(
        value in requested_names for value in name_values
    )
    return email_match or name_match


def _classified_technology_scopes(value: Any) -> set[str]:
    """Classify explicit product text without overlapping WxCCE and UCCE."""

    text = _clean_text(value).casefold()
    if not text:
        return set()
    scopes: set[str] = set()
    if re.search(
        r"\b(webex\s*(meetings?|messag(?:e|ing)|app|suite)|collaboration)\b",
        text,
    ):
        scopes.add("Webex Meetings & Messaging")
    if re.search(
        r"\b(webex\s*calling|cloud\s*calling|telephony|cloud\s*pbx)\b",
        text,
    ):
        scopes.add("Webex Calling")

    is_wxcce = bool(
        re.search(
            r"\b(webex\s*(?:contact\s*center\s*enterprise|cce)|"
            r"wxcc\s*enterprise)\b",
            text,
        )
    )
    if is_wxcce:
        scopes.add("Webex Contact Center Enterprise")
    if re.search(
        r"\b(ucce|unified\s*contact\s*center\s*enterprise|"
        r"cisco\s*contact\s*center\s*enterprise)\b",
        text,
    ):
        scopes.add("Cisco UCCE")
    if re.search(
        r"\b(uccx|unified\s*contact\s*center\s*express|"
        r"contact\s*center\s*express)\b",
        text,
    ):
        scopes.add("Cisco UCCX")
    if not is_wxcce and re.search(
        r"\b(webex\s*contact\s*center|wxcc|cloud\s*contact\s*center)\b",
        text,
    ):
        scopes.add("Webex Contact Center")
    return scopes


def _row_technology_matches_scope(
    row: pd.Series,
    candidates: Sequence[str],
    requested: Sequence[str],
) -> bool:
    """Intersect every populated technology alias with the request scope."""

    from data_normalization import strict_scope_text

    wanted = {
        _clean_text(value).casefold()
        for value in requested
        if _clean_text(value)
    }
    if not wanted:
        return True
    accepted_scopes: set[str] = set()
    all_contact_center = "all contact center" in wanted
    for value in requested:
        accepted_scopes.update(_classified_technology_scopes(value))
    if all_contact_center:
        accepted_scopes.update(
            {
                "Webex Contact Center",
                "Webex Contact Center Enterprise",
                "Cisco UCCE",
                "Cisco UCCX",
            }
        )

    values: List[str] = []
    for position in _matching_positions(row.index, candidates):
        valid, value = strict_scope_text(row.iloc[position])
        if not valid:
            return False
        if value:
            values.append(value)
    if not values:
        return False
    compatible = False
    for value in values:
        normalized = value.casefold()
        exact = normalized in wanted
        classified = _classified_technology_scopes(value)
        if classified:
            if accepted_scopes and (
                classified - accepted_scopes
            ) and not exact:
                return False
            if classified & accepted_scopes or exact:
                compatible = True
        elif exact:
            compatible = True
        elif all_contact_center and re.search(
            r"\b(?:contact|call)\s*center\b",
            normalized,
        ):
            compatible = True
    return compatible


def _row_account_ids_match_scope(
    row: pd.Series,
    candidates: Sequence[str],
    requested: Sequence[str],
) -> bool:
    """Require every populated account alias to remain inside the scope."""

    from data_normalization import account_id_matches_scope

    values = _strict_scope_values(row, candidates)
    if values is None:
        return False
    return bool(values) and all(
        account_id_matches_scope(value, requested) for value in values
    )


def _renewal_subscription_ids(values: Sequence[str]) -> set[str]:
    """Separate explicit subscription IDs from descriptive renewal windows."""

    return {
        value
        for raw in values
        if (value := _clean_text(raw))
        and re.match(r"(?i)^sub(?:scription)?[\s:_-]*[a-z0-9]", value)
    }


def _subscription_scope_keys(
    subscriptions: Optional[pd.DataFrame],
    *,
    customer_lookup: Mapping[str, Any],
    request: AnalysisRequest,
) -> Tuple[Optional[set[str]], List[str]]:
    """Resolve conjunctive account/subscription/technology/team scopes."""

    from data_normalization import (
        LIKELY_ACCOUNT_ID_COLS,
        customer_ownership_key,
        load_customer_alias_registry,
    )

    restrictions_requested = bool(
        request.account_scope
        or request.subscription_scope
        or request.technology_scope
        or request.team_scope
        or request.leader_scope
        or request.renewal_scope
    )
    if not restrictions_requested:
        return None, []
    requested_renewal_subscription_ids = _renewal_subscription_ids(
        request.renewal_scope
    )
    if subscriptions is None or subscriptions.empty:
        if (
            request.account_scope
            or request.subscription_scope
            or request.technology_scope
            or request.team_scope
            or request.leader_scope
            or requested_renewal_subscription_ids
        ):
            return set(), [
                "Account/subscription/technology/team/leader scope could not "
                "be resolved "
                "because subscription evidence is unavailable."
            ]
        return None, []
    registry = load_customer_alias_registry()
    matched_keys: set[str] = set()
    has_tech_columns = bool(
        _matching_positions(subscriptions.columns, _TECHNOLOGY_COLUMNS)
    )
    has_team_scope_columns = bool(
        _matching_positions(
            subscriptions.columns,
            _TEAM_EMAIL_SCOPE_COLUMNS + _TEAM_NAME_SCOPE_COLUMNS,
        )
    )
    has_leader_scope_columns = bool(
        _matching_positions(subscriptions.columns, _LEADER_SCOPE_COLUMNS)
    )
    warnings: List[str] = []
    ambiguous_requested_accounts = {
        value: owners
        for value in request.account_scope
        if len(
            owners := _equivalent_account_owners(
                customer_lookup,
                value,
            )
        )
        > 1
    }
    if ambiguous_requested_accounts:
        warnings.append(
            "Account scope was excluded because equivalent account "
            "identifier forms have conflicting customer owners: "
            + "; ".join(
                f"{account_id} -> {', '.join(owners)}"
                for account_id, owners in sorted(
                    ambiguous_requested_accounts.items()
                )
            )
        )
        return set(), warnings
    if request.technology_scope and not has_tech_columns:
        warnings.append(
            "Technology scope had no subscription technology column; subscription rows were excluded because their scope could not be verified."
        )
    if request.team_scope and not has_team_scope_columns:
        warnings.append(
            "Team scope had no team roster column; subscription rows were "
            "excluded because their scope could not be verified."
        )
    if request.leader_scope and not has_leader_scope_columns:
        warnings.append(
            "Leader scope had no manager roster column; subscription rows "
            "were excluded because their scope could not be verified."
        )
    renewal_ids = {_clean_text(value) for value in request.renewal_scope}
    from data_normalization import strict_scope_text

    observed_subscription_ids: set[str] = set()
    for position in _matching_positions(
        subscriptions.columns,
        _SUBSCRIPTION_COLUMNS,
    ):
        for raw in subscriptions.iloc[:, position].tolist():
            valid, value = strict_scope_text(raw)
            if valid and value:
                observed_subscription_ids.add(value)
    explicit_renewal_ids = (
        renewal_ids.intersection(observed_subscription_ids)
        | requested_renewal_subscription_ids
    )
    unmatched_renewal_ids = (
        explicit_renewal_ids - observed_subscription_ids
    )
    if unmatched_renewal_ids:
        warnings.append(
            "Explicit renewal subscription scope did not match observed "
            "subscription evidence: "
            + ", ".join(sorted(unmatched_renewal_ids))
        )
    for position in range(len(subscriptions)):
        row = subscriptions.iloc[position]
        if request.account_scope and not _row_account_ids_match_scope(
            row,
            LIKELY_ACCOUNT_ID_COLS,
            request.account_scope,
        ):
            continue
        if request.subscription_scope and not _row_values_match_scope_conjunctive(
            row,
            _SUBSCRIPTION_COLUMNS,
            request.subscription_scope,
        ):
            continue
        if request.technology_scope and not _row_technology_matches_scope(
            row,
            _TECHNOLOGY_COLUMNS,
            request.technology_scope,
        ):
            continue
        if request.team_scope and not _row_team_matches_scope(
            row,
            request.team_scope,
        ):
            continue
        if request.leader_scope and not _row_values_match_scope_conjunctive(
            row,
            _LEADER_SCOPE_COLUMNS,
            request.leader_scope,
        ):
            continue
        if explicit_renewal_ids:
            # Renewal scope may be a subscription identifier or a named
            # portfolio window.  Apply it only when it matches an explicit
            # subscription ID; descriptive windows remain request metadata.
            strict_row_subs = _strict_scope_values(
                row,
                _SUBSCRIPTION_COLUMNS,
            )
            if strict_row_subs is None:
                continue
            row_subs = set(strict_row_subs)
            if not row_subs or not all(
                value in explicit_renewal_ids for value in row_subs
            ):
                continue
        resolved = _resolve_customer_name_core(
            row,
            customer_lookup,
            registry=registry,
        )
        key = customer_ownership_key(resolved, registry=registry)
        if key:
            matched_keys.add(key)
    if (
        request.account_scope
        or request.subscription_scope
        or request.technology_scope
        or request.team_scope
        or request.leader_scope
        or explicit_renewal_ids
    ):
        return matched_keys, warnings
    if matched_keys:
        return matched_keys, warnings
    # Broader scope columns were unavailable or intentionally pre-filtered by
    # the caller.  Do not turn the absence of a matching optional column into
    # an empty portfolio; retain the explicit source boundary with a warning.
    return None, warnings


def _resolve_customer_universe(
    request: AnalysisRequest,
    partitions: Mapping[str, Mapping[str, pd.DataFrame]],
    display_names: Mapping[str, str],
    subscriptions: Optional[pd.DataFrame],
    customer_lookup: Mapping[str, Any],
) -> Tuple[Tuple[str, ...], Dict[str, str], List[str]]:
    from data_normalization import customer_ownership_key, load_customer_alias_registry

    all_keys: set[str] = set()
    for source_partitions in partitions.values():
        all_keys.update(source_partitions.keys())
    display = dict(display_names)
    warnings: List[str] = []
    if request.customer_scope:
        registry = load_customer_alias_registry()
        requested_keys: set[str] = set()
        for name in request.customer_scope:
            key = customer_ownership_key(name, registry=registry)
            if key:
                requested_keys.add(key)
                display.setdefault(key, name)
        allowed_keys = requested_keys
    else:
        allowed_keys = set(all_keys)
    subscription_keys, scope_warnings = _subscription_scope_keys(
        subscriptions,
        customer_lookup=customer_lookup,
        request=request,
    )
    warnings.extend(scope_warnings)
    if subscription_keys is not None:
        if request.customer_scope:
            allowed_keys &= subscription_keys
        else:
            allowed_keys = subscription_keys
    if not request.customer_scope and subscription_keys is None:
        allowed_keys = set(all_keys)
    missing_requested = set()
    if request.customer_scope:
        missing_requested = allowed_keys - all_keys
        for key in missing_requested:
            warnings.append(
                f"Requested customer {display.get(key, key)!r} has no usable source records in this analysis."
            )
    ordered = tuple(sorted(allowed_keys, key=lambda key: (display.get(key, key).casefold(), key)))
    return ordered, display, warnings


def _severity_for_band(band: str) -> str:
    return {
        "CRITICAL": "critical",
        "HIGH": "high",
        "MEDIUM": "medium",
        "LOW": "low",
        "HEALTHY": "informational",
        "UNKNOWN": "unknown",
    }.get(_clean_text(band).upper(), "unknown")


def _finding(
    *,
    customer_id: str,
    category: str,
    kind: str,
    title: str,
    value: Any,
    severity: str,
    importance: int,
    evidence_ids: Sequence[str],
    confidence: str,
    coverage: float,
    explanation: str,
    records: Sequence[EvidenceReference],
    direction: str = "stable",
    conflicts: Sequence[str] = (),
) -> Finding:
    first, last = _observation_range(records, evidence_ids)
    return Finding(
        finding_id=_safe_id(customer_id, category, kind, prefix="finding"),
        scope_kind="customer",
        scope_id=customer_id,
        kind=kind,
        category=category,
        title=title,
        structured_value=value,
        severity=severity,
        importance=importance,
        direction=direction,
        evidence_ids=tuple(evidence_ids),
        source_freshness=(
            "stale"
            if any(
                item.source_freshness == "stale" and item.evidence_id in set(evidence_ids)
                for item in records
            )
            else "current"
        ),
        confidence=confidence,
        data_coverage=coverage,
        conflicting_evidence=tuple(conflicts),
        explanation=explanation,
        lifecycle_state="active",
        first_observed_time=first,
        last_observed_time=last,
    )


def _metric_ready_subscriptions(
    frame: Optional[pd.DataFrame],
) -> Optional[pd.DataFrame]:
    """Add canonical scoring aliases without removing source columns."""

    if frame is None:
        return None
    result = frame.copy()
    result.attrs.update(getattr(frame, "attrs", {}) or {})
    if "RENEWAL_RISK_CATEGORY" not in result.columns:
        risk_positions = _matching_positions(
            result.columns,
            (
                "RENEWAL_RISK_LEVEL",
                "RENEWAL_RISK",
                "RENEWAL_RISK_C",
                "Renewal Risk",
            ),
        )
        if risk_positions:
            result["RENEWAL_RISK_CATEGORY"] = result.iloc[
                :, risk_positions[0]
            ]
    if "STATUS_C" not in result.columns:
        status_positions = _matching_positions(
            result.columns,
            ("SUBSCRIPTION_STATUS", "SUBSCRIPTION_STATE", "STATUS", "Status"),
        )
        if status_positions:
            result["STATUS_C"] = result.iloc[:, status_positions[0]]
    return result


def _canonical_pulse_summary(
    frame: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    """Summarize numeric or categorical Pulse evidence on one 0-10 scale."""

    import canonical_metrics as cm

    summary = dict(cm.pulse_sentiment(frame))
    if summary.get("count") or frame is None or frame.empty:
        return summary
    rating_positions = _matching_positions(
        frame.columns,
        (
            "PULSE_RATING__C",
            "PULSE_RATING",
            "Customer Pulse",
            "Customer Pulse Color",
            "SENTIMENT",
        ),
    )
    if not rating_positions:
        return summary
    mapped: List[float] = []
    for value in frame.iloc[:, rating_positions[0]].tolist():
        text = _clean_text(value).casefold()
        try:
            numeric = float(text)
        except (TypeError, ValueError):
            numeric = math.nan
        if math.isfinite(numeric) and 0.0 <= numeric <= 10.0:
            mapped.append(numeric)
        elif text in {"green", "good", "positive", "healthy"}:
            mapped.append(9.0)
        elif text in {"yellow", "amber", "neutral", "mixed"}:
            mapped.append(6.0)
        elif text in {"red", "poor", "bad", "negative", "critical"}:
            mapped.append(2.0)
    if not mapped:
        return summary
    mean_value = round(sum(mapped) / len(mapped), 2)
    positive = sum(value >= 7.5 for value in mapped)
    negative = sum(value <= 5.0 for value in mapped)
    neutral = len(mapped) - positive - negative
    return {
        "count": len(mapped),
        "mean_0_to_10": mean_value,
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
        "sentiment": (
            "Positive"
            if mean_value >= 7.5
            else "Negative"
            if mean_value <= 5.0
            else "Neutral"
        ),
        "categorical_mapping": "decision-intelligence-v2",
    }


def _build_customer_base(
    *,
    request: AnalysisRequest,
    customer_id: str,
    customer_name: str,
    aliases: Sequence[str],
    partitions: Mapping[str, Mapping[str, pd.DataFrame]],
    safe_frames: Mapping[str, Optional[pd.DataFrame]],
    global_states: Mapping[str, str],
    global_freshness: Mapping[str, str],
    conflict_customer_keys: Mapping[str, set[str]],
    sources: AnalysisSources,
) -> Tuple[CustomerAnalysis, Tuple[EvidenceReference, ...], Dict[str, str]]:
    import canonical_metrics as cm
    from risk_scoring import compute_customer_risk_profile

    frames: Dict[str, Optional[pd.DataFrame]] = {}
    states: Dict[str, str] = {}
    freshness: Dict[str, str] = {}
    conflict_sources: List[str] = []
    for source_type in (
        "subscriptions",
        "adoption_barriers",
        "support_cases",
        "customer_pulse",
        "action_plans",
        "success_priorities",
    ):
        safe = safe_frames.get(source_type)
        partition = partitions.get(source_type, {}).get(customer_id)
        if partition is None and safe is not None:
            partition = safe.iloc[0:0].copy()
            partition.attrs.update(getattr(safe, "attrs", {}) or {})
        involved = customer_id in conflict_customer_keys.get(source_type, set())
        if partition is not None:
            attrs = dict(getattr(partition, "attrs", {}) or {})
            if not involved:
                attrs["cross_customer_id_conflicts"] = {
                    "record_id_columns_used": [],
                    "conflicting_ids": [],
                    "quarantined_rows": 0,
                    "conflict_count": 0,
                }
            partition.attrs.update(attrs)
        frames[source_type] = partition
        global_state = global_states.get(source_type, "missing")
        if involved:
            conflict_sources.append(source_type)
            states[source_type] = (
                "conflict_only"
                if partition is None or partition.empty
                else "ownership_conflict"
            )
        elif global_state == "available" and (partition is None or partition.empty):
            states[source_type] = "observed_empty"
        else:
            states[source_type] = global_state
        local_freshness = _freshness_state(
            partition,
            source_type,
            as_of_time=request.as_of_time,
            metadata=sources.metadata,
            stale_after_days=int(
                sources.metadata.get("stale_after_days")
                or request.feature_configuration.get("stale_after_days", 45)
                or 45
            ),
        )
        freshness[source_type] = (
            local_freshness
            if local_freshness != "unknown"
            else global_freshness.get(source_type, "unknown")
        )

    customer_incidents: Optional[Tuple[Mapping[str, Any], ...]]
    if "external_incidents" not in request.allowed_source_types:
        customer_incidents = None
        incident_frame = None
        incident_state = "excluded_by_request"
    elif sources.external_incidents is None:
        customer_incidents = None
        incident_frame = None
        incident_state = "missing"
    else:
        customer_incidents = _filter_incidents_for_customer(
            sources.external_incidents, customer_id, customer_name
        )
        incident_frame = _incidents_frame(customer_incidents)
        incident_state = "available" if customer_incidents else "observed_empty"
    incident_conflict = customer_id in conflict_customer_keys.get(
        "external_incidents", set()
    )
    if incident_conflict:
        conflict_sources.append("external_incidents")
        incident_state = (
            "conflict_only"
            if incident_frame is None or incident_frame.empty
            else "ownership_conflict"
        )
    frames["external_incidents"] = incident_frame
    states["external_incidents"] = incident_state
    freshness["external_incidents"] = _freshness_state(
        incident_frame,
        "external_incidents",
        as_of_time=request.as_of_time,
        metadata=sources.metadata,
        stale_after_days=int(
            sources.metadata.get("stale_after_days")
            or request.feature_configuration.get("stale_after_days", 45)
            or 45
        ),
    )

    # Evidence, risk, and individual canonical metrics all consume these
    # logical-record views. Materialize them once so later helpers reuse the
    # same selected TAC, Pulse, and Action Plan rows.
    for logical_source in ("support_cases", "customer_pulse", "action_plans"):
        logical_frame = frames.get(logical_source)
        if logical_frame is not None:
            frames[logical_source] = _canonicalize_source_frame(
                logical_source, logical_frame
            )

    evidence: List[EvidenceReference] = []
    for source_type in DEFAULT_SOURCE_TYPES:
        if source_type not in request.allowed_source_types:
            continue
        frame = frames.get(source_type)
        source_meta = (
            sources.metadata.get(source_type)
            if isinstance(sources.metadata.get(source_type), Mapping)
            else {}
        )
        authority = _clean_text(source_meta.get("authority")) or "authorized_request_source"
        evidence.extend(
            _record_evidence(
                source_type=source_type,
                frame=frame,
                customer_id=customer_id,
                source_state=states.get(source_type, "missing"),
                freshness=freshness.get(source_type, "unknown"),
                ingestion_time=_ingestion_timestamp(frame, sources.metadata, source_type),
                authority=authority,
            )
        )
    evidence = list({item.evidence_id: item for item in evidence}.values())
    evidence.sort(key=lambda item: item.evidence_id)

    ab = frames.get("adoption_barriers")
    # Canonicalize the three fan-out-prone logical-record sources once per
    # customer. Canonical metric/risk helpers recognize the immutable marker
    # and return copies without repeating quarantine/normalize/sort work for
    # every individual count.
    support_raw = frames.get("support_cases")
    pulse_raw = frames.get("customer_pulse")
    actions_raw = frames.get("action_plans")
    support = (
        None if support_raw is None else cm.deduplicate_tac_cases(support_raw)
    )
    pulse = (
        None if pulse_raw is None else cm.deduplicate_customer_pulse(pulse_raw)
    )
    actions = (
        None if actions_raw is None else cm.deduplicate_action_plans(actions_raw)
    )
    subscriptions = frames.get("subscriptions")
    scoring_subscriptions = _metric_ready_subscriptions(subscriptions)
    priorities = frames.get("success_priorities")
    risk_profile = compute_customer_risk_profile(
        customer_name=customer_name,
        customer_ab=ab,
        customer_csone=support,
        customer_pulse=pulse,
        customer_action_plans=actions,
        customer_subs=scoring_subscriptions,
        ext_incidents=(list(customer_incidents) if customer_incidents is not None else None),
        recent_window_days=max(
            1,
            int(
                request.feature_configuration.get("recent_window_days")
                or request.feature_configuration.get("days")
                or 30
            ),
        ),
    )
    evidence_quality = dict(risk_profile.get("evidence_quality") or {})
    coverage = float(evidence_quality.get("coverage_ratio", 0.0) or 0.0)
    confidence = _clean_text(evidence_quality.get("confidence_band")).upper() or "LOW"

    pulse_summary = _canonical_pulse_summary(pulse)
    total_barriers = cm.count_total_barriers(ab)
    critical_barriers = cm.count_critical_barriers(
        ab, mode=cm.CRITICAL_AB_MODE_CRITICAL_ONLY
    )
    critical_high_barriers = cm.count_critical_barriers(
        ab, mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH
    )
    open_barriers = cm.count_open_barriers(ab)
    total_cases = cm.count_total_tac(support)
    open_cases = cm.count_open_tac(support)
    p1_cases = cm.count_p1(support)
    p2_cases = cm.count_p2(support)
    bems_count = cm.count_bems(support)
    break_fix_cases = cm.count_break_fix(support)
    provisioning_cases = cm.count_provisioning(support)
    total_action_plans = cm.count_total_action_plans(actions)
    open_action_plans = cm.count_open_action_plans(None, actions)
    completed_action_plans = cm.count_action_plan_completed(actions)
    overdue_action_plans = _count_overdue_actions(actions, request.as_of_time)
    success_priority_count = len(_canonicalize_source_frame("success_priorities", priorities))
    subscription_ids = _values_from_frame(subscriptions, _SUBSCRIPTION_COLUMNS)
    technologies = _values_from_frame(subscriptions, _TECHNOLOGY_COLUMNS)
    teams = _values_from_frame(subscriptions, _TEAM_COLUMNS)
    renewal_days = _days_until_nearest(subscriptions, _RENEWAL_DATE_COLUMNS, request.as_of_time)

    by_source = {
        source_type: _source_evidence_ids(evidence, source_type)
        for source_type in DEFAULT_SOURCE_TYPES
    }
    all_evidence_ids = tuple(item.evidence_id for item in evidence)
    risk_score = risk_profile.get("risk_score_0_100")
    risk_score_10 = risk_profile.get("risk_score_0_10")
    risk_band = _clean_text(risk_profile.get("risk_band")).upper() or "UNKNOWN"
    assessment_state = _clean_text(risk_profile.get("risk_assessment_state")).upper() or "UNAVAILABLE"

    metrics = (
        MetricValue("risk_score_0_100", risk_score, unit="score_0_100", state=assessment_state, evidence_ids=all_evidence_ids, as_of_time=request.as_of_time),
        MetricValue("risk_score_0_10", risk_score_10, unit="score_0_10", state=assessment_state, evidence_ids=all_evidence_ids, as_of_time=request.as_of_time),
        MetricValue("risk_band", risk_band, unit="classification", state=assessment_state, evidence_ids=all_evidence_ids, as_of_time=request.as_of_time),
        MetricValue("evidence_coverage", coverage, unit="ratio", state="calculated", evidence_ids=all_evidence_ids, as_of_time=request.as_of_time),
        MetricValue("total_barriers", total_barriers, evidence_ids=by_source["adoption_barriers"], as_of_time=request.as_of_time),
        MetricValue("open_barriers", open_barriers, evidence_ids=by_source["adoption_barriers"], as_of_time=request.as_of_time),
        MetricValue("critical_barriers", critical_barriers, evidence_ids=by_source["adoption_barriers"], as_of_time=request.as_of_time),
        MetricValue("critical_high_barriers", critical_high_barriers, evidence_ids=by_source["adoption_barriers"], as_of_time=request.as_of_time),
        MetricValue("total_cases", total_cases, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("open_cases", open_cases, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("p1_cases", p1_cases, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("p2_cases", p2_cases, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("bems_count", bems_count, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("break_fix_cases", break_fix_cases, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("provisioning_cases", provisioning_cases, evidence_ids=by_source["support_cases"], as_of_time=request.as_of_time),
        MetricValue("pulse_count", int(pulse_summary.get("count", 0) or 0), evidence_ids=by_source["customer_pulse"], as_of_time=request.as_of_time),
        MetricValue("pulse_mean_0_to_10", pulse_summary.get("mean_0_to_10"), unit="score_0_10", evidence_ids=by_source["customer_pulse"], as_of_time=request.as_of_time),
        MetricValue("pulse_sentiment", pulse_summary.get("sentiment", "Unknown"), unit="classification", evidence_ids=by_source["customer_pulse"], as_of_time=request.as_of_time),
        MetricValue("total_action_plans", total_action_plans, evidence_ids=by_source["action_plans"], as_of_time=request.as_of_time),
        MetricValue("open_action_plans", open_action_plans, evidence_ids=by_source["action_plans"], as_of_time=request.as_of_time),
        MetricValue("completed_action_plans", completed_action_plans, evidence_ids=by_source["action_plans"], as_of_time=request.as_of_time),
        MetricValue("overdue_action_plans", overdue_action_plans, evidence_ids=by_source["action_plans"], as_of_time=request.as_of_time),
        MetricValue("success_priority_count", success_priority_count, evidence_ids=by_source["success_priorities"], as_of_time=request.as_of_time),
        MetricValue("subscription_count", len(subscription_ids), evidence_ids=by_source["subscriptions"], as_of_time=request.as_of_time),
        MetricValue("days_to_nearest_renewal", renewal_days, unit="days", state="calculated" if renewal_days is not None else "unknown", evidence_ids=by_source["subscriptions"], as_of_time=request.as_of_time),
    )

    missing = tuple(
        source_type
        for source_type, state in states.items()
        if source_type in request.allowed_source_types and state in {"missing", "fetch_failed"}
    )
    stale = tuple(
        source_type
        for source_type, state in freshness.items()
        if source_type in request.allowed_source_types and state == "stale"
    )
    warnings = list(evidence_quality.get("caveats") or ())
    warnings.extend(
        f"{source_type}: {states[source_type]}"
        for source_type in missing
    )
    data_quality = DataQualitySummary(
        coverage_ratio=coverage,
        confidence=confidence,
        source_states={
            key: value for key, value in states.items() if key in request.allowed_source_types
        },
        source_freshness={
            key: value for key, value in freshness.items() if key in request.allowed_source_types
        },
        missing_sources=missing,
        stale_sources=stale,
        conflicted_sources=tuple(conflict_sources),
        warnings=tuple(warnings),
    )

    findings: List[Finding] = []
    if risk_score is not None:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="customer_risk",
                kind="deterministic_calculation",
                title=f"Canonical customer risk is {risk_band}",
                value={"score_0_100": risk_score, "band": risk_band},
                severity=_severity_for_band(risk_band),
                importance=int(float(risk_score)),
                evidence_ids=all_evidence_ids,
                confidence=confidence,
                coverage=coverage,
                explanation=(
                    f"The deterministic risk engine calculated {risk_score}/100 from the request-scoped canonical evidence."
                ),
                records=evidence,
            )
        )
    else:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="risk_unknown",
                kind="data_quality",
                title="Customer risk is not reliably assessable",
                value={"assessment_state": assessment_state, "coverage": coverage},
                severity="unknown",
                importance=70,
                evidence_ids=all_evidence_ids,
                confidence="LOW",
                coverage=coverage,
                explanation="Missing, conflicted, stale, or unusable evidence prevents a defensible health conclusion.",
                records=evidence,
                conflicts=conflict_sources,
            )
        )
    if p1_cases or bems_count:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="severe_service_evidence",
                kind="risk_signal",
                title="Active severe service evidence requires attention",
                value={"p1_cases": p1_cases, "bems_count": bems_count},
                severity="high",
                importance=95 if p1_cases else 85,
                evidence_ids=by_source["support_cases"],
                confidence=confidence,
                coverage=coverage,
                explanation="Active P1 or BEMS evidence creates a conservative risk and action floor.",
                records=evidence,
                direction="worsening",
            )
        )
    if critical_high_barriers:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="active_adoption_barrier",
                kind="risk_signal",
                title="Critical or high adoption barriers remain active",
                value={"critical_high": critical_high_barriers, "open": open_barriers},
                severity="high" if critical_barriers else "medium",
                importance=90 if critical_barriers else 75,
                evidence_ids=by_source["adoption_barriers"],
                confidence=confidence,
                coverage=coverage,
                explanation="High-severity adoption barriers can block verified customer outcomes until they have an owned remediation path.",
                records=evidence,
                direction="worsening",
            )
        )
    negative_pulse = int(pulse_summary.get("negative", 0) or 0)
    if negative_pulse:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="negative_customer_pulse",
                kind="risk_signal",
                title="Customer Pulse indicates a negative experience",
                value={"negative_records": negative_pulse, "mean_0_to_10": pulse_summary.get("mean_0_to_10")},
                severity="medium",
                importance=75,
                evidence_ids=by_source["customer_pulse"],
                confidence=confidence,
                coverage=coverage,
                explanation="The latest canonical Customer Pulse evidence is negative and should be tied to a measurable recovery check.",
                records=evidence,
                direction="worsening",
            )
        )
    if overdue_action_plans or open_action_plans:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="unresolved_action_plan",
                kind="risk_signal",
                title="Action Plan commitments remain unresolved",
                value={"open": open_action_plans, "overdue": overdue_action_plans},
                severity="high" if overdue_action_plans else "medium",
                importance=85 if overdue_action_plans else 65,
                evidence_ids=by_source["action_plans"],
                confidence=confidence,
                coverage=coverage,
                explanation="Open or overdue commitments need an authoritative owner and observable completion signal.",
                records=evidence,
                direction="worsening" if overdue_action_plans else "stable",
            )
        )
    attention_days = int(request.feature_configuration.get("renewal_attention_days", 120) or 120)
    if renewal_days is not None and 0 <= renewal_days <= attention_days:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="renewal_attention_window",
                kind="risk_signal",
                title="Renewal is inside the configured attention window",
                value={"days_to_nearest_renewal": renewal_days, "attention_window_days": attention_days},
                severity="high" if renewal_days <= 45 else "medium",
                importance=90 if renewal_days <= 45 else 70,
                evidence_ids=by_source["subscriptions"],
                confidence=confidence,
                coverage=coverage,
                explanation="The subscription evidence places the renewal inside the configured preparation window.",
                records=evidence,
                direction="worsening",
            )
        )
    if success_priority_count:
        findings.append(
            _finding(
                customer_id=customer_id,
                category="verified_adoption_opportunity",
                kind="opportunity_signal",
                title="Success Priority evidence identifies an adoption opportunity",
                value={"success_priorities": success_priority_count},
                severity="informational",
                importance=55,
                evidence_ids=by_source["success_priorities"],
                confidence=confidence,
                coverage=coverage,
                explanation="An explicit Success Priority can support a measurable adoption action when its current state is verified.",
                records=evidence,
                direction="improving",
            )
        )
    if negative_pulse and (p1_cases or p2_cases):
        combined_ids = tuple(
            dict.fromkeys(by_source["customer_pulse"] + by_source["support_cases"])
        )
        findings.append(
            _finding(
                customer_id=customer_id,
                category="pulse_service_relationship",
                kind="relationship",
                title="Negative Pulse and active escalated service evidence coincide",
                value={"negative_pulse": negative_pulse, "p1": p1_cases, "p2": p2_cases},
                severity="high",
                importance=92,
                evidence_ids=combined_ids,
                confidence=confidence,
                coverage=coverage,
                explanation="The two independently observed signals justify one coordinated recovery review; they do not prove causation.",
                records=evidence,
                direction="worsening",
            )
        )
    if conflict_sources:
        conflict_ids = tuple(
            evidence_id
            for source_type in conflict_sources
            for evidence_id in by_source.get(source_type, ())
        )
        findings.append(
            _finding(
                customer_id=customer_id,
                category="ownership_conflict",
                kind="data_quality",
                title="Logical records have contradictory customer ownership",
                value={"sources": sorted(conflict_sources)},
                severity="high",
                importance=100,
                evidence_ids=conflict_ids or all_evidence_ids,
                confidence="LOW",
                coverage=coverage,
                explanation="The same logical identifier appears under multiple resolved customers and remains quarantined from scoring.",
                records=evidence,
                conflicts=conflict_sources,
            )
        )
    if missing:
        missing_ids = tuple(
            evidence_id
            for source_type in missing
            for evidence_id in by_source.get(source_type, ())
        )
        findings.append(
            _finding(
                customer_id=customer_id,
                category="missing_evidence",
                kind="data_quality",
                title="Important evidence sources are unavailable",
                value={"sources": sorted(missing)},
                severity="unknown",
                importance=80,
                evidence_ids=missing_ids or all_evidence_ids,
                confidence="LOW",
                coverage=coverage,
                explanation="Unavailable evidence is an explicit unknown and cannot be interpreted as zero activity or healthy status.",
                records=evidence,
            )
        )
    if stale:
        stale_ids = tuple(
            evidence_id
            for source_type in stale
            for evidence_id in by_source.get(source_type, ())
        )
        findings.append(
            _finding(
                customer_id=customer_id,
                category="stale_evidence",
                kind="data_quality",
                title="One or more evidence sources are stale",
                value={"sources": sorted(stale)},
                severity="unknown",
                importance=70,
                evidence_ids=stale_ids or all_evidence_ids,
                confidence="LOW" if coverage < 0.75 else confidence,
                coverage=coverage,
                explanation="Stale evidence lowers confidence until a current authoritative observation is obtained.",
                records=evidence,
                direction="stale",
            )
        )

    active_risk_findings = [
        finding
        for finding in findings
        if finding.kind in {"risk_signal", "relationship"}
        and finding.importance >= 75
        and finding.evidence_ids
    ]
    if active_risk_findings:
        scenario_evidence = tuple(
            dict.fromkeys(
                evidence_id
                for finding in active_risk_findings
                for evidence_id in finding.evidence_ids
            )
        )
        findings.append(
            _finding(
                customer_id=customer_id,
                category="unchanged_risk_scenario",
                kind="scenario",
                title="Conditional next state if material blockers remain unchanged",
                value={
                    "condition": "material_findings_unresolved_at_next_review",
                    "expected_direction": "risk_not_improving",
                    "probability": None,
                    "triggering_categories": sorted(
                        finding.category for finding in active_risk_findings
                    ),
                },
                severity="medium",
                importance=max(60, max(finding.importance for finding in active_risk_findings) - 10),
                evidence_ids=scenario_evidence,
                confidence=confidence,
                coverage=coverage,
                explanation=(
                    "If the current evidence-backed blockers remain unresolved through the next review, "
                    "the available signals do not support expecting risk to improve. This is a conditional "
                    "scenario, not a probability estimate or causal claim."
                ),
                records=evidence,
                direction="stable",
            )
        )

    if risk_score is None:
        risk_text = "risk is UNKNOWN"
    else:
        risk_text = f"risk is {risk_band} at {float(risk_score):.1f}/100"
    synthesis = (
        f"{customer_name}: {risk_text}; {len(findings)} structured finding(s); "
        f"evidence coverage {coverage:.0%} ({confidence})."
    )
    provisional_brief = DecisionBrief(
        scope_kind="customer",
        scope_id=customer_id,
        what_changed=("No prior comparable snapshot was supplied.",),
        why_it_matters=tuple(finding.explanation for finding in findings[:3]),
        next_action_ids=(),
        what_remains_uncertain=tuple(
            list(data_quality.missing_sources)
            + list(data_quality.stale_sources)
            + list(data_quality.conflicted_sources)
        ),
        evidence_ids=all_evidence_ids,
        confidence=confidence,
        synthesis=synthesis,
    )
    customer = CustomerAnalysis(
        customer_id=customer_id,
        customer_name=customer_name,
        aliases=tuple(aliases),
        subscriptions=subscription_ids,
        technologies=technologies or request.technology_scope,
        responsible_teams=teams or request.team_scope or request.leader_scope,
        metrics=metrics,
        risk_profile=risk_profile,
        adoption_state={
            "total_barriers": total_barriers,
            "open_barriers": open_barriers,
            "critical_high_barriers": critical_high_barriers,
            "success_priorities": success_priority_count,
        },
        pulse_state=pulse_summary,
        renewal_context={
            "days_to_nearest_renewal": renewal_days,
            "attention_window_days": attention_days,
            "subscription_count": len(subscription_ids),
        },
        engagement_state={
            "activity_volume": dict((risk_profile.get("components") or {}).get("activity_volume") or {}),
            "open_action_plans": open_action_plans,
            "overdue_action_plans": overdue_action_plans,
        },
        findings=tuple(findings),
        temporal_changes=(),
        recommended_actions=(),
        evidence_ids=all_evidence_ids,
        data_quality=data_quality,
        unresolved_conflicts=tuple(conflict_sources),
        executive_synthesis=synthesis,
        decision_brief=provisional_brief,
    )
    return customer, tuple(evidence), states


def _metric_map(customer: CustomerAnalysis) -> Dict[str, Any]:
    return {metric.name: metric.value for metric in customer.metrics}


def _band_rank(value: Any) -> int:
    return {
        "UNKNOWN": -1,
        "HEALTHY": 0,
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
        "CRITICAL": 4,
    }.get(_clean_text(value).upper(), -1)


def _change(
    *,
    customer: CustomerAnalysis,
    category: str,
    classification: str,
    title: str,
    previous: Any,
    current: Any,
    evidence_ids: Sequence[str],
    why: str,
    as_of_time: str,
    confidence: Optional[str] = None,
    persistence_days: Optional[int] = None,
) -> TemporalChange:
    latest_evidence = ""
    for finding in customer.findings:
        if finding.category == category and finding.last_observed_time:
            latest_evidence = max(latest_evidence, finding.last_observed_time)
    since_evidence = _days_between(as_of_time, latest_evidence) if latest_evidence else None
    return TemporalChange(
        change_id=_safe_id(customer.customer_id, category, prefix="change"),
        scope_kind="customer",
        scope_id=customer.customer_id,
        category=category,
        classification=classification,
        title=title,
        previous_value=previous,
        current_value=current,
        effective_time=as_of_time,
        evidence_ids=tuple(evidence_ids),
        why_it_matters=why,
        confidence=confidence or customer.data_quality.confidence,
        statement_kind="deterministic_calculation",
        persistence_days=persistence_days,
        momentum=(
            "negative"
            if classification == "worsening"
            else "positive"
            if classification in {"improving", "recovering", "resolved"}
            else "stable"
            if classification == "persistent"
            else "not_comparable"
        ),
        acceleration="not_comparable",
        time_since_improvement_days=(0 if classification in {"improving", "recovering", "resolved"} else None),
        time_since_deterioration_days=(0 if classification == "worsening" else None),
        time_since_authoritative_evidence_days=since_evidence,
    )


def _customer_temporal_changes(
    current: CustomerAnalysis,
    prior: Optional[CustomerAnalysis],
    *,
    as_of_time: str,
    prior_as_of_time: str = "",
    no_prior_reason: str = "",
) -> Tuple[TemporalChange, ...]:
    evidence_ids = current.evidence_ids
    if prior is None:
        classification = "new" if no_prior_reason == "customer_added" else "not_comparable"
        title = (
            "Customer entered the analyzed scope"
            if classification == "new"
            else "No comparable prior canonical snapshot is available"
        )
        return (
            _change(
                customer=current,
                category="scope_comparison",
                classification=classification,
                title=title,
                previous=None,
                current={"customer_id": current.customer_id},
                evidence_ids=evidence_ids,
                why=(
                    "The customer is new to this canonical scope."
                    if classification == "new"
                    else no_prior_reason
                    or "A single observation can describe current state but cannot establish a trend."
                ),
                as_of_time=as_of_time,
            ),
        )

    current_metrics = _metric_map(current)
    prior_metrics = _metric_map(prior)
    changes: List[TemporalChange] = []
    persistence_days = _days_between(as_of_time, prior_as_of_time) if prior_as_of_time else None
    current_score = current_metrics.get("risk_score_0_100")
    prior_score = prior_metrics.get("risk_score_0_100")
    current_band = current_metrics.get("risk_band")
    prior_band = prior_metrics.get("risk_band")
    if current_score is None and prior_score is not None:
        changes.append(
            _change(
                customer=current,
                category="customer_risk",
                classification="uncertain",
                title="Risk became unassessable",
                previous={"score": prior_score, "band": prior_band},
                current={"score": None, "band": "UNKNOWN"},
                evidence_ids=evidence_ids,
                why="Current evidence no longer supports the prior scored conclusion.",
                as_of_time=as_of_time,
            )
        )
    elif current_score is not None and prior_score is None:
        changes.append(
            _change(
                customer=current,
                category="customer_risk",
                classification="recovering",
                title="Risk assessment became available",
                previous={"score": None, "band": prior_band},
                current={"score": current_score, "band": current_band},
                evidence_ids=evidence_ids,
                why="Current evidence is sufficient to replace the prior UNKNOWN state with a canonical score.",
                as_of_time=as_of_time,
            )
        )
    elif current_score is not None and prior_score is not None:
        delta = round(float(current_score) - float(prior_score), 1)
        band_delta = _band_rank(current_band) - _band_rank(prior_band)
        if abs(delta) >= 5.0 or band_delta:
            classification = "worsening" if delta > 0 or band_delta > 0 else "improving"
            changes.append(
                _change(
                    customer=current,
                    category="customer_risk",
                    classification=classification,
                    title=("Customer risk worsened" if classification == "worsening" else "Customer risk improved"),
                    previous={"score": prior_score, "band": prior_band},
                    current={"score": current_score, "band": current_band, "delta": delta},
                    evidence_ids=evidence_ids,
                    why="The canonical score or risk band changed materially between comparable snapshots.",
                    as_of_time=as_of_time,
                )
            )

    metric_rules = (
        ("critical_high_barriers", "adoption_barrier", "Critical/high Adoption Barrier count"),
        ("overdue_action_plans", "overdue_action_plan", "Overdue Action Plan count"),
        ("p1_cases", "severe_service_evidence", "Active P1 count"),
        ("bems_count", "severe_service_evidence", "Active BEMS count"),
    )
    for metric_name, category, label in metric_rules:
        old = prior_metrics.get(metric_name)
        new = current_metrics.get(metric_name)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or old == new:
            continue
        classification = "worsening" if new > old else "improving"
        if new == 0 and old > 0:
            classification = "resolved"
        changes.append(
            _change(
                customer=current,
                category=category,
                classification=classification,
                title=f"{label} {'increased' if new > old else 'decreased'}",
                previous=old,
                current=new,
                evidence_ids=evidence_ids,
                why=f"The structured {metric_name} metric changed from {old} to {new}.",
                as_of_time=as_of_time,
            )
        )
    old_pulse = prior_metrics.get("pulse_mean_0_to_10")
    new_pulse = current_metrics.get("pulse_mean_0_to_10")
    if isinstance(old_pulse, (int, float)) and isinstance(new_pulse, (int, float)):
        delta = round(float(new_pulse) - float(old_pulse), 1)
        if abs(delta) >= 1.0:
            classification = "improving" if delta > 0 else "worsening"
            changes.append(
                _change(
                    customer=current,
                    category="customer_pulse",
                    classification=classification,
                    title=("Customer Pulse recovered" if classification == "improving" else "Customer Pulse deteriorated"),
                    previous=old_pulse,
                    current=new_pulse,
                    evidence_ids=evidence_ids,
                    why="The canonical Customer Pulse mean changed by at least one point on the 0–10 scale.",
                    as_of_time=as_of_time,
                )
            )
    old_renewal = prior_metrics.get("days_to_nearest_renewal")
    new_renewal = current_metrics.get("days_to_nearest_renewal")
    attention = int(current.renewal_context.get("attention_window_days", 120) or 120)
    if (
        isinstance(old_renewal, (int, float))
        and isinstance(new_renewal, (int, float))
        and old_renewal > attention
        and 0 <= new_renewal <= attention
    ):
        changes.append(
            _change(
                customer=current,
                category="renewal_attention_window",
                classification="new",
                title="Renewal entered the attention window",
                previous=old_renewal,
                current=new_renewal,
                evidence_ids=evidence_ids,
                why="The nearest renewal is now inside the configured preparation window.",
                as_of_time=as_of_time,
            )
        )

    old_coverage = prior.data_quality.coverage_ratio
    new_coverage = current.data_quality.coverage_ratio
    if abs(new_coverage - old_coverage) >= 0.1:
        classification = "improving" if new_coverage > old_coverage else "worsening"
        changes.append(
            _change(
                customer=current,
                category="evidence_quality",
                classification=classification,
                title=("Evidence coverage improved" if classification == "improving" else "Evidence coverage declined"),
                previous=old_coverage,
                current=new_coverage,
                evidence_ids=evidence_ids,
                why="The share of usable configured evidence changed materially.",
                as_of_time=as_of_time,
            )
        )
    prior_conflicts = set(prior.unresolved_conflicts)
    current_conflicts = set(current.unresolved_conflicts)
    if current_conflicts - prior_conflicts:
        changes.append(
            _change(
                customer=current,
                category="ownership_conflict",
                classification="new",
                title="Customer ownership became ambiguous",
                previous=sorted(prior_conflicts),
                current=sorted(current_conflicts),
                evidence_ids=evidence_ids,
                why="A logical identifier now appears under contradictory customer owners.",
                as_of_time=as_of_time,
            )
        )
    elif prior_conflicts and not current_conflicts:
        changes.append(
            _change(
                customer=current,
                category="ownership_conflict",
                classification="resolved",
                title="Customer ownership conflict resolved",
                previous=sorted(prior_conflicts),
                current=[],
                evidence_ids=evidence_ids,
                why="The current canonical source no longer contains the prior ownership contradiction.",
                as_of_time=as_of_time,
            )
        )

    current_findings = {item.semantic_key: item for item in current.findings}
    prior_findings = {item.semantic_key: item for item in prior.findings}
    already_categories = {item.category for item in changes}
    for key in sorted(current_findings.keys() - prior_findings.keys()):
        finding = current_findings[key]
        if finding.category in already_categories or finding.category == "customer_risk":
            continue
        changes.append(
            _change(
                customer=current,
                category=finding.category,
                classification="new",
                title=f"New: {finding.title}",
                previous=None,
                current=finding.structured_value,
                evidence_ids=finding.evidence_ids,
                why=finding.explanation,
                as_of_time=as_of_time,
                confidence=finding.confidence,
            )
        )
    for key in sorted(prior_findings.keys() - current_findings.keys()):
        finding = prior_findings[key]
        if finding.category in already_categories or finding.category == "customer_risk":
            continue
        changes.append(
            _change(
                customer=current,
                category=finding.category,
                classification="resolved",
                title=f"Resolved: {finding.title}",
                previous=finding.structured_value,
                current=None,
                evidence_ids=evidence_ids,
                why="The prior finding is absent from the current canonical structured state.",
                as_of_time=as_of_time,
                confidence=current.data_quality.confidence,
            )
        )
    for key in sorted(current_findings.keys() & prior_findings.keys()):
        finding = current_findings[key]
        if finding.category in already_categories or finding.importance < 60:
            continue
        changes.append(
            _change(
                customer=current,
                category=finding.category,
                classification="persistent",
                title=f"Persistent: {finding.title}",
                previous=prior_findings[key].structured_value,
                current=finding.structured_value,
                evidence_ids=finding.evidence_ids,
                why="The material finding remains active across comparable canonical snapshots.",
                as_of_time=as_of_time,
                confidence=finding.confidence,
                persistence_days=persistence_days,
            )
        )

    if not changes:
        changes.append(
            _change(
                customer=current,
                category="material_state",
                classification="persistent",
                title="No material canonical change detected",
                previous={"analysis_fingerprint": "prior"},
                current={"analysis_fingerprint": "current"},
                evidence_ids=evidence_ids,
                why="Canonical metrics, active findings, conflicts, and evidence quality did not cross a material-change rule.",
                as_of_time=as_of_time,
                persistence_days=persistence_days,
            )
        )
    return tuple({item.change_id: item for item in changes}.values())


_ACTION_SPECS: Dict[str, Dict[str, Any]] = {
    "ownership_conflict": {
        "type": "resolve_ownership_conflict",
        "action": "Reconcile the conflicting logical record against the authoritative customer owner and republish the corrected source mapping.",
        "owner": "Data steward (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "IMMEDIATE",
        "window": "immediate",
        "outcome": "One authoritative customer owner is recorded and the quarantined evidence can be safely reconsidered.",
        "success": "Ownership-conflict count is zero for the affected logical identifiers in the next canonical analysis.",
        "effort": "Medium",
        "dependencies": (),
    },
    "missing_evidence": {
        "type": "obtain_missing_evidence",
        "action": "Restore or obtain the missing authorized evidence before making a customer-health decision.",
        "owner": "Report operator or data steward (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "BEFORE_DECISION_REVIEW",
        "window": "before next customer review",
        "outcome": "The next analysis can distinguish observed zero from unavailable data.",
        "success": "Previously missing source states are current or explicitly observed-empty in the next bundle.",
        "effort": "Low",
        "dependencies": (),
    },
    "stale_evidence": {
        "type": "refresh_stale_evidence",
        "action": "Obtain a current authoritative observation for each stale source before relying on fine-grained comparisons.",
        "owner": "Data steward or source owner (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "BEFORE_DECISION_REVIEW",
        "window": "within the current reporting period",
        "outcome": "Confidence reflects current rather than expired evidence.",
        "success": "Each affected source is classified current in the next canonical bundle.",
        "effort": "Low",
        "dependencies": (),
    },
    "risk_unknown": {
        "type": "establish_decision_evidence",
        "action": "Resolve the documented evidence-quality blockers before assigning a health classification.",
        "owner": "Report operator or data steward (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "BEFORE_DECISION_REVIEW",
        "window": "before next customer review",
        "outcome": "The customer is either defensibly scored or remains explicitly UNKNOWN with a narrower reason.",
        "success": "Risk assessment state is SCORED or every remaining blocker is explicitly documented.",
        "effort": "Low",
        "dependencies": (),
    },
    "severe_service_evidence": {
        "type": "resolve_severe_service_evidence",
        "action": "Assign the active P1/BEMS resolution path to an accountable role and establish a recurring status checkpoint.",
        "owner": "TAC or engineering escalation owner (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "IMMEDIATE",
        "window": "immediate",
        "outcome": "Critical service work has explicit accountability and a current resolution path.",
        "success": "Active P1 and BEMS counts reach zero, or each remaining item has a current owner and authoritative next checkpoint.",
        "effort": "High",
        "dependencies": (),
    },
    "active_adoption_barrier": {
        "type": "remediate_adoption_barrier",
        "action": "Create or update an owned Action Plan for every active critical/high Adoption Barrier.",
        "owner": "Customer Success Manager and barrier owner (roles; confirm named owners)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "CURRENT_REPORTING_PERIOD",
        "window": "within the current reporting period",
        "outcome": "Each material adoption blocker has accountable, measurable recovery work.",
        "success": "Every active critical/high barrier is linked to an open owned plan or is authoritatively closed.",
        "effort": "Medium",
        "dependencies": (),
    },
    "negative_customer_pulse": {
        "type": "address_customer_pulse",
        "action": "Run a customer recovery review, record the agreed next step, and obtain a follow-up Customer Pulse.",
        "owner": "Customer Success Manager (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "NEXT_CUSTOMER_REVIEW",
        "window": "before next customer review",
        "outcome": "The customer’s concern is tied to an owned recovery step and a new observation.",
        "success": "A newer Customer Pulse records the outcome of the recovery action and no longer remains negative, or the unresolved concern is explicitly documented.",
        "effort": "Medium",
        "dependencies": ("Customer availability",),
    },
    "unresolved_action_plan": {
        "type": "complete_action_plan",
        "action": "Confirm the authoritative owner and next step for each open Action Plan, prioritizing overdue commitments.",
        "owner": "Current Action Plan owner (from source; verify before assignment)",
        "owner_confidence": "SOURCE_QUALIFIED",
        "urgency": "CURRENT_REPORTING_PERIOD",
        "window": "within the current reporting period",
        "outcome": "Open commitments have an accountable completion path.",
        "success": "Overdue Action Plan count reaches zero and every remaining open plan has a current next step.",
        "effort": "Medium",
        "dependencies": (),
    },
    "renewal_attention_window": {
        "type": "prepare_renewal_readiness",
        "action": "Complete a renewal-readiness review using the current risk, adoption, service, and evidence-quality findings.",
        "owner": "Renewal owner and account team (roles; confirm named owners)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "BEFORE_RENEWAL_REVIEW",
        "window": "before renewal-readiness review",
        "outcome": "The renewal posture, decision owner, evidence gaps, and next milestone are explicit.",
        "success": "A current renewal-readiness record names the decision owner, next milestone, and unresolved evidence-backed risks.",
        "effort": "Medium",
        "dependencies": (),
    },
    "verified_adoption_opportunity": {
        "type": "validate_adoption_opportunity",
        "action": "Validate the active Success Priority with the customer and define one observable adoption milestone.",
        "owner": "Customer Success Manager (role; confirm named owner)",
        "owner_confidence": "ROLE_INFERRED",
        "urgency": "NEXT_CUSTOMER_REVIEW",
        "window": "before next customer review",
        "outcome": "The opportunity becomes a customer-validated adoption objective rather than an unqualified assumption.",
        "success": "The Success Priority has a current customer-confirmed milestone and observable completion signal.",
        "effort": "Medium",
        "dependencies": ("Customer validation",),
    },
}


def _candidate_for_finding(
    customer: CustomerAnalysis,
    finding: Finding,
) -> Optional[Dict[str, Any]]:
    spec = _ACTION_SPECS.get(finding.category)
    if spec is None or not finding.evidence_ids:
        return None
    related_changes = [
        item for item in customer.temporal_changes if item.category == finding.category
    ]
    change_state = related_changes[0].classification if related_changes else "not_comparable"
    urgency_factor = {
        "critical": 100.0,
        "high": 90.0,
        "medium": 65.0,
        "low": 35.0,
        "unknown": 75.0,
        "informational": 35.0,
    }.get(finding.severity.casefold(), 50.0)
    persistence_factor = {
        "worsening": 100.0,
        "new": 85.0,
        "persistent": 75.0,
        "recurring": 90.0,
        "stale": 65.0,
        "uncertain": 70.0,
        "improving": 30.0,
        "recovering": 25.0,
        "resolved": 0.0,
        "not_comparable": 45.0,
    }.get(change_state, 45.0)
    renewal_days = customer.metric("days_to_nearest_renewal")
    renewal_factor = 0.0
    if isinstance(renewal_days, (int, float)) and renewal_days >= 0:
        renewal_factor = max(0.0, 100.0 - min(365.0, float(renewal_days)) / 365.0 * 100.0)
    freshness_factor = 35.0 if finding.source_freshness == "stale" else 80.0
    confidence_factor = float(_confidence_rank(finding.confidence)) / 3.0 * 100.0
    dependencies = tuple(spec["dependencies"])
    actionability_factor = max(35.0, 100.0 - 30.0 * len(dependencies))
    effort_factor = {"Low": 100.0, "Medium": 65.0, "High": 35.0}.get(spec["effort"], 50.0)
    score = (
        0.34 * float(finding.importance)
        + 0.20 * urgency_factor
        + 0.14 * persistence_factor
        + 0.10 * renewal_factor
        + 0.07 * freshness_factor
        + 0.07 * confidence_factor
        + 0.05 * actionability_factor
        + 0.03 * effort_factor
    )
    uncertainty_gate = "none"
    if customer.data_quality.confidence == "LOW" and finding.category not in {
        "ownership_conflict",
        "missing_evidence",
        "stale_evidence",
        "risk_unknown",
        "severe_service_evidence",
    }:
        score = min(score, 64.0)
        uncertainty_gate = "remediation_capped_until_evidence_improves"
    if finding.category in {"ownership_conflict", "missing_evidence", "stale_evidence", "risk_unknown"}:
        score = max(score, 78.0)
        uncertainty_gate = "evidence_establishment_floor"
    if finding.category == "severe_service_evidence":
        score = max(score, 90.0)
    factor_payload = {
        "customer_impact": round(float(finding.importance), 2),
        "urgency": urgency_factor,
        "persistence_or_momentum": persistence_factor,
        "renewal_proximity": round(renewal_factor, 2),
        "source_freshness": freshness_factor,
        "confidence": round(confidence_factor, 2),
        "actionability": actionability_factor,
        "effort": effort_factor,
        "uncertainty_gate": uncertainty_gate,
        "temporal_state": change_state,
    }
    action_id = _safe_id(customer.customer_id, spec["type"], prefix="action")
    return {
        "action_id": action_id,
        "customer": customer,
        "finding": finding,
        "spec": spec,
        "score": round(score, 2),
        "factors": factor_payload,
    }


def _rank_actions(
    customers: Sequence[CustomerAnalysis],
) -> Tuple[Tuple[CustomerAnalysis, ...], Tuple[RecommendedAction, ...]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    for customer in customers:
        for finding in customer.findings:
            candidate = _candidate_for_finding(customer, finding)
            if candidate is None:
                continue
            existing = candidates.get(candidate["action_id"])
            if existing is None or candidate["score"] > existing["score"]:
                candidates[candidate["action_id"]] = candidate
    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            -float(item["score"]),
            item["customer"].customer_name.casefold(),
            item["action_id"],
        ),
    )
    actions: List[RecommendedAction] = []
    for rank, item in enumerate(ordered, 1):
        customer = item["customer"]
        finding = item["finding"]
        spec = item["spec"]
        confidence = (
            finding.confidence
            if _confidence_rank(finding.confidence)
            <= _confidence_rank(customer.data_quality.confidence)
            else customer.data_quality.confidence
        )
        actions.append(
            RecommendedAction(
                action_id=item["action_id"],
                scope_kind="customer",
                scope_id=customer.customer_id,
                action_type=spec["type"],
                specific_action=spec["action"],
                rationale=finding.explanation,
                triggering_finding_ids=(finding.finding_id,),
                evidence_ids=finding.evidence_ids,
                proposed_owner=spec["owner"],
                owner_confidence=spec["owner_confidence"],
                urgency=spec["urgency"],
                rank=rank,
                priority_score=item["score"],
                ranking_factors=item["factors"],
                dependencies=tuple(spec["dependencies"]),
                expected_outcome=spec["outcome"],
                measurable_success_signal=spec["success"],
                timing_window=spec["window"],
                effort=spec["effort"],
                confidence=confidence,
            )
        )
    by_customer: Dict[str, List[RecommendedAction]] = {}
    for action in actions:
        by_customer.setdefault(action.scope_id, []).append(action)
    updated: List[CustomerAnalysis] = []
    for customer in customers:
        customer_actions = tuple(by_customer.get(customer.customer_id, ()))
        change_lines = tuple(
            item.title
            for item in customer.temporal_changes
            if item.classification != "persistent"
        ) or tuple(item.title for item in customer.temporal_changes[:3])
        uncertainty = tuple(
            dict.fromkeys(
                list(customer.data_quality.missing_sources)
                + list(customer.data_quality.stale_sources)
                + list(customer.data_quality.conflicted_sources)
            )
        )
        brief = DecisionBrief(
            scope_kind="customer",
            scope_id=customer.customer_id,
            what_changed=change_lines[:5],
            why_it_matters=tuple(
                finding.explanation
                for finding in sorted(
                    customer.findings,
                    key=lambda item: (-item.importance, item.finding_id),
                )[:4]
            ),
            next_action_ids=tuple(action.action_id for action in customer_actions[:5]),
            what_remains_uncertain=uncertainty or ("No material evidence-quality uncertainty is currently identified.",),
            evidence_ids=customer.evidence_ids,
            confidence=customer.data_quality.confidence,
            synthesis=customer.executive_synthesis,
        )
        updated.append(
            replace(
                customer,
                recommended_actions=customer_actions,
                decision_brief=brief,
            )
        )
    return tuple(updated), tuple(actions)


def _numeric_metric_total(
    customers: Sequence[CustomerAnalysis], metric_name: str
) -> float:
    total = 0.0
    for customer in customers:
        value = customer.metric(metric_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _integral_if_whole(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(float(value), 3)


def _aggregate_source_state(values: Sequence[str]) -> str:
    states = {_clean_text(value) for value in values if _clean_text(value)}
    if not states:
        return "missing"
    if states == {"excluded_by_request"}:
        return "excluded_by_request"
    if states.intersection({"ownership_conflict", "conflict_only"}):
        return "ownership_conflict"
    if "fetch_failed" in states:
        return "fetch_failed"
    if states == {"observed_empty"}:
        return "observed_empty"
    if "available" in states and states.intersection({"missing", "observed_empty"}):
        return "partially_available"
    if "available" in states:
        return "available"
    if "missing" in states:
        return "missing"
    return sorted(states)[0]


def _aggregate_data_quality(
    customers: Sequence[CustomerAnalysis],
    allowed_sources: Sequence[str],
) -> DataQualitySummary:
    source_states: Dict[str, str] = {}
    source_freshness: Dict[str, str] = {}
    for source_type in allowed_sources:
        source_states[source_type] = _aggregate_source_state(
            [customer.data_quality.source_states.get(source_type, "") for customer in customers]
        )
        freshness_values = {
            _clean_text(customer.data_quality.source_freshness.get(source_type, "unknown"))
            or "unknown"
            for customer in customers
        }
        source_freshness[source_type] = (
            "stale"
            if "stale" in freshness_values
            else "current"
            if "current" in freshness_values
            else "unknown"
        )
    coverage = (
        sum(customer.data_quality.coverage_ratio for customer in customers) / len(customers)
        if customers
        else 0.0
    )
    confidence = "LOW"
    if customers:
        rank = min(_confidence_rank(customer.data_quality.confidence) for customer in customers)
        confidence = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}.get(rank, "LOW")
    missing = tuple(
        sorted(
            {
                source
                for customer in customers
                for source in customer.data_quality.missing_sources
            }
        )
    )
    stale = tuple(
        sorted(
            {
                source
                for customer in customers
                for source in customer.data_quality.stale_sources
            }
        )
    )
    conflicted = tuple(
        sorted(
            {
                source
                for customer in customers
                for source in customer.data_quality.conflicted_sources
            }
        )
    )
    warnings = tuple(
        dict.fromkeys(
            warning
            for customer in customers
            for warning in customer.data_quality.warnings
        )
    )
    return DataQualitySummary(
        coverage_ratio=coverage,
        confidence=confidence,
        source_states=source_states,
        source_freshness=source_freshness,
        missing_sources=missing,
        stale_sources=stale,
        conflicted_sources=conflicted,
        warnings=warnings,
    )


def _portfolio_temporal_change(
    *,
    portfolio_id: str,
    category: str,
    classification: str,
    title: str,
    previous: Any,
    current: Any,
    evidence_ids: Sequence[str],
    why: str,
    as_of_time: str,
    confidence: str,
) -> TemporalChange:
    return TemporalChange(
        change_id=_safe_id(
            portfolio_id,
            category,
            classification,
            _canonical_json(previous),
            _canonical_json(current),
            prefix="change",
        ),
        scope_kind="portfolio",
        scope_id=portfolio_id,
        category=category,
        classification=classification,
        title=title,
        previous_value=previous,
        current_value=current,
        effective_time=as_of_time,
        evidence_ids=tuple(evidence_ids),
        why_it_matters=why,
        confidence=confidence,
        statement_kind="deterministic_calculation",
        momentum=classification if classification in {"worsening", "improving"} else "stable",
        acceleration="not_comparable",
    )


def _portfolio_temporal_changes(
    *,
    portfolio_id: str,
    current_metrics: Mapping[str, Any],
    current_customer_ids: Sequence[str],
    evidence_ids: Sequence[str],
    confidence: str,
    as_of_time: str,
    prior: Optional[PortfolioAnalysis],
    no_prior_reason: str,
) -> Tuple[TemporalChange, ...]:
    if prior is None:
        return (
            _portfolio_temporal_change(
                portfolio_id=portfolio_id,
                category="scope_comparison",
                classification="not_comparable",
                title="No comparable prior portfolio snapshot is available",
                previous=None,
                current={"customer_count": len(current_customer_ids)},
                evidence_ids=evidence_ids,
                why=no_prior_reason
                or "A single observation can describe current state but cannot establish a trend.",
                as_of_time=as_of_time,
                confidence=confidence,
            ),
        )

    changes: List[TemporalChange] = []
    prior_metrics = {metric.name: metric.value for metric in prior.metrics}
    rules = (
        ("customer_count", "scope_size", 1.0),
        ("average_known_risk_score", "portfolio_risk", 5.0),
        ("critical_high_barriers", "adoption_barrier", 1.0),
        ("p1_cases", "severe_service_evidence", 1.0),
        ("bems_count", "severe_service_evidence", 1.0),
        ("overdue_action_plans", "overdue_action_plan", 1.0),
    )
    for metric_name, category, threshold in rules:
        old = prior_metrics.get(metric_name)
        new = current_metrics.get(metric_name)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        delta = float(new) - float(old)
        if abs(delta) < threshold:
            continue
        if metric_name == "customer_count":
            classification = "new" if delta > 0 else "resolved"
        else:
            classification = "worsening" if delta > 0 else "improving"
            if new == 0 and old > 0:
                classification = "resolved"
        changes.append(
            _portfolio_temporal_change(
                portfolio_id=portfolio_id,
                category=category,
                classification=classification,
                title=f"Portfolio {metric_name.replace('_', ' ')} {'increased' if delta > 0 else 'decreased'}",
                previous=old,
                current=new,
                evidence_ids=evidence_ids,
                why=f"The exact customer-derived {metric_name} metric changed from {old} to {new}.",
                as_of_time=as_of_time,
                confidence=confidence,
            )
        )
    prior_ids = set(prior.customer_ids)
    current_ids = set(current_customer_ids)
    added = sorted(current_ids - prior_ids)
    removed = sorted(prior_ids - current_ids)
    if added or removed:
        changes.append(
            _portfolio_temporal_change(
                portfolio_id=portfolio_id,
                category="scope_membership",
                classification="new" if added else "resolved",
                title="Portfolio customer membership changed",
                previous={"removed": removed},
                current={"added": added},
                evidence_ids=evidence_ids,
                why="Membership changes are separated from within-customer performance changes.",
                as_of_time=as_of_time,
                confidence=confidence,
            )
        )
    if not changes:
        changes.append(
            _portfolio_temporal_change(
                portfolio_id=portfolio_id,
                category="material_state",
                classification="persistent",
                title="No material portfolio change detected",
                previous={"customer_count": prior.customer_count},
                current={"customer_count": len(current_customer_ids)},
                evidence_ids=evidence_ids,
                why="Exact aggregate metrics and portfolio membership did not cross a material-change rule.",
                as_of_time=as_of_time,
                confidence=confidence,
            )
        )
    return tuple(changes)


def _build_portfolio(
    *,
    request: AnalysisRequest,
    customers: Sequence[CustomerAnalysis],
    actions: Sequence[RecommendedAction],
    prior: Optional[PortfolioAnalysis],
    no_prior_reason: str,
) -> PortfolioAnalysis:
    customer_ids = tuple(customer.customer_id for customer in customers)
    portfolio_id = _safe_id(
        request.comparison_scope_fingerprint, prefix="portfolio"
    )
    all_evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for customer in customers
            for evidence_id in customer.evidence_ids
        )
    )
    risk_distribution = {band: 0 for band in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY", "UNKNOWN")}
    known_scores: List[float] = []
    opportunity_distribution = {"identified": 0, "not_identified": 0, "unknown": 0}
    for customer in customers:
        band = _clean_text(customer.metric("risk_band", "UNKNOWN")).upper() or "UNKNOWN"
        risk_distribution[band if band in risk_distribution else "UNKNOWN"] += 1
        score = customer.metric("risk_score_0_100")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            known_scores.append(float(score))
        priority_count = customer.metric("success_priority_count")
        priority_state = customer.data_quality.source_states.get(
            "success_priorities", "missing"
        )
        if isinstance(priority_count, (int, float)) and priority_count > 0:
            opportunity_distribution["identified"] += 1
        elif priority_state in {"missing", "fetch_failed", "conflict_only", "ownership_conflict"}:
            opportunity_distribution["unknown"] += 1
        else:
            opportunity_distribution["not_identified"] += 1

    total_metric_names = (
        "total_barriers",
        "open_barriers",
        "critical_barriers",
        "critical_high_barriers",
        "total_cases",
        "open_cases",
        "p1_cases",
        "p2_cases",
        "bems_count",
        "break_fix_cases",
        "provisioning_cases",
        "total_action_plans",
        "open_action_plans",
        "completed_action_plans",
        "overdue_action_plans",
        "success_priority_count",
        "subscription_count",
    )
    metric_values: Dict[str, Any] = {
        "customer_count": len(customers),
        "known_risk_count": len(known_scores),
        "unknown_risk_count": len(customers) - len(known_scores),
        "average_known_risk_score": (
            round(sum(known_scores) / len(known_scores), 2) if known_scores else None
        ),
    }
    for metric_name in total_metric_names:
        metric_values[metric_name] = _integral_if_whole(
            _numeric_metric_total(customers, metric_name)
        )
    metrics = tuple(
        MetricValue(
            name,
            value,
            unit=("score_0_100" if name == "average_known_risk_score" else "count"),
            state=("unknown" if value is None else "calculated"),
            evidence_ids=all_evidence_ids,
            as_of_time=request.as_of_time,
        )
        for name, value in metric_values.items()
    )

    ranked = tuple(
        customer.customer_id
        for customer in sorted(
            customers,
            key=lambda customer: (
                -float(customer.metric("risk_score_0_100"))
                if isinstance(customer.metric("risk_score_0_100"), (int, float))
                else 1.0,
                -int(customer.metric("p1_cases", 0) or 0),
                -int(customer.metric("critical_high_barriers", 0) or 0),
                customer.customer_name.casefold(),
                customer.customer_id,
            ),
        )
    )
    category_counts = Counter(
        finding.category
        for customer in customers
        for finding in customer.findings
        if finding.importance >= 55
    )
    emerging_patterns = tuple(
        f"{category.replace('_', ' ').title()} affects {count} customers"
        for category, count in sorted(
            category_counts.items(), key=lambda item: (-item[1], item[0])
        )
        if count >= 2
    )
    barrier_customers = [
        customer
        for customer in customers
        if int(customer.metric("critical_high_barriers", 0) or 0) > 0
    ]
    recurring_barriers = (
        (
            f"Critical/high adoption barriers recur across {len(barrier_customers)} customers",
        )
        if len(barrier_customers) >= 2
        else ()
    )
    concentration_risks: List[str] = []
    if len(known_scores) > 1 and sum(known_scores) > 0 and ranked:
        top = next(customer for customer in customers if customer.customer_id == ranked[0])
        top_score = float(top.metric("risk_score_0_100") or 0)
        if top_score / sum(known_scores) >= 0.5:
            concentration_risks.append(
                f"{top.customer_name} represents at least half of the portfolio's summed known risk score"
            )
    total_critical_high = int(metric_values["critical_high_barriers"] or 0)
    if total_critical_high and len(barrier_customers) == 1:
        concentration_risks.append(
            f"All critical/high adoption barriers are concentrated in {barrier_customers[0].customer_name}"
        )
    elevated_customers = [
        customer
        for customer in customers
        if _clean_text(customer.metric("risk_band", "UNKNOWN")).upper()
        in {"MEDIUM", "HIGH", "CRITICAL"}
    ]
    if len(elevated_customers) >= 2:
        for label, values in (
            (
                "technology",
                [
                    value
                    for customer in elevated_customers
                    for value in customer.technologies
                ],
            ),
            (
                "team",
                [
                    value
                    for customer in elevated_customers
                    for value in customer.responsible_teams
                ],
            ),
        ):
            counts = Counter(values)
            if not counts:
                continue
            value, count = sorted(
                counts.items(), key=lambda item: (-item[1], item[0].casefold())
            )[0]
            if count >= 2 and count / len(elevated_customers) >= 0.6:
                concentration_risks.append(
                    f"{count} of {len(elevated_customers)} elevated-risk customers share {label} {value}"
                )
    cross_customer_themes = tuple(
        category.replace("_", " ").title()
        for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2
    )
    data_quality = _aggregate_data_quality(customers, request.allowed_source_types)
    temporal_changes = _portfolio_temporal_changes(
        portfolio_id=portfolio_id,
        current_metrics=metric_values,
        current_customer_ids=customer_ids,
        evidence_ids=all_evidence_ids,
        confidence=data_quality.confidence,
        as_of_time=request.as_of_time,
        prior=prior,
        no_prior_reason=no_prior_reason,
    )
    portfolio_actions = tuple(actions)
    evidence_gaps = tuple(
        dict.fromkeys(
            list(data_quality.missing_sources)
            + list(data_quality.stale_sources)
            + list(data_quality.conflicted_sources)
        )
    )
    synthesis = (
        f"{len(customers)} customer(s); {len(known_scores)} scored and "
        f"{len(customers) - len(known_scores)} UNKNOWN; "
        f"{int(metric_values['critical_high_barriers'])} critical/high barrier(s), "
        f"{int(metric_values['p1_cases'])} active P1 case(s), and "
        f"{len(portfolio_actions)} ranked action(s)."
    )
    why_lines = list(emerging_patterns[:3])
    if not why_lines:
        why_lines.append(
            "Portfolio conclusions are exact aggregates of the included customer analyses."
        )
    uncertainty = evidence_gaps or (
        "No material portfolio evidence-quality uncertainty is currently identified.",
    )
    brief = DecisionBrief(
        scope_kind="portfolio",
        scope_id=portfolio_id,
        what_changed=tuple(change.title for change in temporal_changes[:6]),
        why_it_matters=tuple(why_lines),
        next_action_ids=tuple(action.action_id for action in portfolio_actions[:10]),
        what_remains_uncertain=uncertainty,
        evidence_ids=all_evidence_ids,
        confidence=data_quality.confidence,
        synthesis=synthesis,
    )
    return PortfolioAnalysis(
        portfolio_id=portfolio_id,
        portfolio_scope=(
            request.portfolio_scope
            or request.tenant_scope
            or request.organization_scope
            or "explicit-request-scope"
        ),
        customer_count=len(customers),
        customer_ids=customer_ids,
        metrics=metrics,
        risk_distribution=risk_distribution,
        opportunity_distribution=opportunity_distribution,
        temporal_changes=temporal_changes,
        emerging_patterns=emerging_patterns,
        recurring_barriers=recurring_barriers,
        concentration_risks=tuple(concentration_risks),
        cross_customer_themes=cross_customer_themes,
        ranked_customer_ids=ranked,
        recommended_actions=portfolio_actions,
        data_quality=data_quality,
        evidence_gaps=evidence_gaps,
        executive_synthesis=synthesis,
        decision_brief=brief,
    )


def _schema_major(version: str) -> Optional[int]:
    match = re.match(r"^\s*(\d+)", _clean_text(version))
    return int(match.group(1)) if match else None


def _coerce_prior_bundle(value: Any) -> Optional[AnalysisBundle]:
    if value is None or value == "":
        return None
    if isinstance(value, AnalysisBundle):
        return value
    if isinstance(value, Mapping):
        return AnalysisBundle.from_dict(value)
    if isinstance(value, (str, os.PathLike, Path)):
        return AnalysisBundle.load(Path(value))
    raise TypeError("prior_bundle must be an AnalysisBundle, mapping, or snapshot path")


def _merge_customer_lookup(
    generated: Mapping[str, Any], provided: Any
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        key: (dict(value) if isinstance(value, Mapping) else list(value) if isinstance(value, list) else value)
        for key, value in generated.items()
    }
    if not isinstance(provided, Mapping):
        return result
    for key, value in provided.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            merged = dict(result[key])
            merged.update(dict(value))
            result[str(key)] = merged
        else:
            result[str(key)] = value
    return result


def _load_compatible_prior(
    request: AnalysisRequest,
    prior_value: Any,
) -> Tuple[Optional[AnalysisBundle], str, Tuple[str, ...]]:
    if prior_value is None and request.comparison_snapshot:
        prior_value = request.comparison_snapshot
    if prior_value is None or prior_value == "":
        return None, "No prior canonical snapshot was supplied.", ()
    try:
        prior = _coerce_prior_bundle(prior_value)
    except Exception as exc:
        message = f"Prior snapshot could not be loaded: {_clean_text(exc, limit=320)}"
        return None, message, (message,)
    if prior is None:
        return None, "No prior canonical snapshot was supplied.", ()
    current_major = _schema_major(ANALYSIS_SCHEMA_VERSION)
    prior_major = _schema_major(prior.schema_version)
    if current_major is None or prior_major != current_major:
        message = (
            f"Prior schema {prior.schema_version or 'unknown'} is not comparable with "
            f"{ANALYSIS_SCHEMA_VERSION}; current state remains authoritative."
        )
        return None, message, (message,)
    if (
        prior.context.comparison_scope_fingerprint
        != request.comparison_scope_fingerprint
    ):
        message = (
            "Prior snapshot has a different explicit comparison scope; no cross-scope "
            "trend was inferred."
        )
        return None, message, (message,)
    return prior, "", ()


def _source_cutoff(
    frames: Mapping[str, Optional[pd.DataFrame]],
    metadata: Mapping[str, Any],
) -> str:
    timestamps: List[str] = []
    for source_type, frame in frames.items():
        if source_type not in _SOURCE_TIMESTAMP_COLUMNS:
            continue
        observed = _latest_timestamp(frame, source_type)
        ingested = _ingestion_timestamp(frame, metadata, source_type)
        if observed:
            timestamps.append(observed)
        if ingested:
            timestamps.append(ingested)
    return max(timestamps) if timestamps else ""


def build_analysis_bundle(
    request: AnalysisRequest,
    sources: AnalysisSources | Mapping[str, Any],
    *,
    prior_bundle: AnalysisBundle | Mapping[str, Any] | Path | str | None = None,
    generated_time: Any = None,
) -> AnalysisBundle:
    """Build one immutable, request-scoped canonical analysis bundle.

    Identity resolution and ownership quarantine happen once per source before
    customer partitioning.  Every downstream aggregate, brief, and projection
    is then derived from the resulting customer analyses.
    """

    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be an AnalysisRequest")
    if isinstance(sources, Mapping):
        sources = AnalysisSources.from_mapping(sources)
    if not isinstance(sources, AnalysisSources):
        raise TypeError("sources must be AnalysisSources or a source mapping")
    unsupported = sorted(set(request.allowed_source_types) - set(DEFAULT_SOURCE_TYPES))
    if unsupported:
        raise ValueError(f"Unsupported allowed_source_types: {unsupported}")

    metadata = dict(sources.metadata or {})
    raw_frames = {
        source_type: _collapse_duplicate_schema_columns(frame)
        for source_type, frame in sources.frames().items()
    }
    from data_normalization import build_customer_lookup

    generated_lookup = build_customer_lookup(
        _scope_safe_identity_frame(raw_frames.get("subscriptions"))
    )
    provided_lookup = metadata.get("customer_lookup")
    customer_lookup = _equivalence_aware_customer_lookup(
        _merge_customer_lookup(generated_lookup, provided_lookup),
        generated_lookup,
        provided_lookup,
    )
    warnings: List[str] = list(customer_lookup.get("warnings") or ())
    safe_frames: Dict[str, Optional[pd.DataFrame]] = {}
    partitions: Dict[str, Dict[str, pd.DataFrame]] = {}
    display_names: Dict[str, str] = {}
    aliases: Dict[str, set[str]] = {}
    conflict_keys: Dict[str, set[str]] = {}
    global_states: Dict[str, str] = {}
    global_freshness: Dict[str, str] = {}
    quarantined: List[str] = []
    skipped: List[str] = []
    source_failures: List[str] = []
    stale_after_days = int(
        metadata.get("stale_after_days")
        or request.feature_configuration.get("stale_after_days", 45)
        or 45
    )
    for source_type, raw in raw_frames.items():
        allowed = source_type in request.allowed_source_types
        if not allowed:
            safe_frames[source_type] = None
            partitions[source_type] = {}
            conflict_keys[source_type] = set()
            global_states[source_type] = "excluded_by_request"
            global_freshness[source_type] = "unknown"
            continue
        safe, source_partitions, source_display, source_aliases = _partition_source_strict(
            raw, customer_lookup=customer_lookup
        )
        safe_frames[source_type] = safe if raw is not None else None
        partitions[source_type] = source_partitions
        display_names.update(source_display)
        for key, values in source_aliases.items():
            aliases.setdefault(key, set()).update(values)
        involved = _conflict_customer_keys(
            raw, safe, customer_lookup=customer_lookup
        )
        involved.update(
            _duplicate_schema_conflict_customer_keys(
                raw,
                customer_lookup=customer_lookup,
            )
        )
        conflict_keys[source_type] = involved
        global_states[source_type] = _source_state(raw if raw is None else safe, allowed=True)
        global_freshness[source_type] = _freshness_state(
            safe if raw is not None else None,
            source_type,
            as_of_time=request.as_of_time,
            metadata=metadata,
            stale_after_days=stale_after_days,
        )
        diag = dict((getattr(safe, "attrs", {}) or {}).get("cross_customer_id_conflicts") or {})
        quarantined_rows = int(diag.get("quarantined_rows", 0) or 0)
        if quarantined_rows:
            quarantined.append(
                f"{source_type}: {quarantined_rows} row(s) quarantined across "
                f"{int(diag.get('conflict_count', 0) or 0)} conflicting logical identifier(s)"
            )
        schema_diag = dict(
            (getattr(safe, "attrs", {}) or {}).get(
                "duplicate_schema_conflicts"
            )
            or {}
        )
        schema_quarantined_rows = int(
            schema_diag.get("quarantined_rows", 0) or 0
        )
        if schema_quarantined_rows:
            quarantined.append(
                f"{source_type}: {schema_quarantined_rows} row(s) "
                "quarantined for conflicting duplicate schema columns"
            )
        identity_diag = dict(
            (getattr(safe, "attrs", {}) or {}).get(
                "scope_identity_conflicts"
            )
            or {}
        )
        identity_quarantined_rows = int(
            identity_diag.get("quarantined_rows", 0) or 0
        )
        if identity_quarantined_rows:
            quarantined.append(
                f"{source_type}: {identity_quarantined_rows} row(s) "
                "quarantined for conflicting customer/account identity aliases"
            )
        resolved_rows = sum(len(frame) for frame in source_partitions.values())
        unresolved_rows = max(0, len(safe) - resolved_rows)
        if unresolved_rows:
            skipped.append(
                f"{source_type}: {unresolved_rows} row(s) had no unambiguous customer owner"
            )
        fetch_error = _clean_text(
            (getattr(raw, "attrs", {}) or {}).get("fetch_error") if raw is not None else "",
            limit=320,
        )
        if fetch_error:
            source_failures.append(f"{source_type}: {fetch_error}")

    incident_raw = (
        _collapse_duplicate_schema_columns(
            _incidents_frame(sources.external_incidents),
            label_style="incident",
        )
        if sources.external_incidents is not None
        else None
    )
    safe_incidents = incident_raw
    incident_partitions: Dict[str, pd.DataFrame] = {}
    incident_conflicts: set[str] = (
        _duplicate_schema_conflict_customer_keys(
            incident_raw,
            customer_lookup=customer_lookup,
        )
    )
    incident_schema_diag = dict(
        (getattr(incident_raw, "attrs", {}) or {}).get(
            "duplicate_schema_conflicts"
        )
        or {}
    )
    incident_schema_quarantined = int(
        incident_schema_diag.get("quarantined_rows", 0) or 0
    )
    if incident_schema_quarantined:
        quarantined.append(
            "external_incidents: "
            f"{incident_schema_quarantined} row(s) quarantined for "
            "conflicting duplicate schema columns"
        )
    incident_allowed = "external_incidents" in request.allowed_source_types
    if not incident_allowed:
        safe_incidents = None
        global_states["external_incidents"] = "excluded_by_request"
        global_freshness["external_incidents"] = "unknown"
    elif incident_raw is not None:
        customer_fields = (
            "customer",
            "customer_name",
            "BU_NAME",
            "CUSTOMER_NAME",
            "affected_customer",
        )
        attribution_positions = _matching_positions(
            incident_raw.columns,
            customer_fields,
        )
        attributed_mask = pd.Series(False, index=incident_raw.index, dtype=bool)
        for position in attribution_positions:
            attributed_mask |= incident_raw.iloc[:, position].map(
                lambda value: bool(_clean_text(value))
            )
        attributed_incidents = incident_raw.loc[attributed_mask].copy()
        untagged_incidents = incident_raw.loc[~attributed_mask].copy()
        if not attributed_incidents.empty:
            (
                safe_attributed_incidents,
                incident_partitions,
                incident_display,
                incident_aliases,
            ) = _partition_source_strict(
                attributed_incidents, customer_lookup=customer_lookup
            )
            safe_incidents = pd.concat(
                [safe_attributed_incidents, untagged_incidents],
                ignore_index=True,
                sort=False,
            )
            safe_incidents.attrs.update(
                getattr(safe_attributed_incidents, "attrs", {}) or {}
            )
            display_names.update(incident_display)
            for key, values in incident_aliases.items():
                aliases.setdefault(key, set()).update(values)
            incident_conflicts.update(
                _conflict_customer_keys(
                    attributed_incidents,
                    safe_attributed_incidents,
                    customer_lookup=customer_lookup,
                )
            )
            diag = dict(
                (getattr(safe_attributed_incidents, "attrs", {}) or {}).get(
                    "cross_customer_id_conflicts"
                )
                or {}
            )
            if int(diag.get("quarantined_rows", 0) or 0):
                quarantined.append(
                    "external_incidents: "
                    f"{int(diag.get('quarantined_rows', 0))} row(s) quarantined"
                )
        global_states["external_incidents"] = _source_state(
            safe_incidents, allowed=True
        )
        global_freshness["external_incidents"] = _freshness_state(
            safe_incidents,
            "external_incidents",
            as_of_time=request.as_of_time,
            metadata=metadata,
            stale_after_days=stale_after_days,
        )
    else:
        global_states["external_incidents"] = "missing"
        global_freshness["external_incidents"] = "unknown"
    partitions["external_incidents"] = incident_partitions
    conflict_keys["external_incidents"] = incident_conflicts
    effective_sources = AnalysisSources(
        subscriptions=safe_frames.get("subscriptions"),
        adoption_barriers=safe_frames.get("adoption_barriers"),
        support_cases=safe_frames.get("support_cases"),
        customer_pulse=safe_frames.get("customer_pulse"),
        action_plans=safe_frames.get("action_plans"),
        success_priorities=safe_frames.get("success_priorities"),
        external_incidents=(
            tuple(safe_incidents.to_dict(orient="records"))
            if safe_incidents is not None
            else None
        ),
        metadata=metadata,
    )

    customer_ids, resolved_display, scope_warnings = _resolve_customer_universe(
        request,
        partitions,
        display_names,
        safe_frames.get("subscriptions"),
        customer_lookup,
    )
    if (
        incident_schema_quarantined
        and not incident_schema_diag.get("conflicting_customer_labels")
        and not incident_schema_diag.get("conflicting_account_ids")
    ):
        incident_conflicts.update(customer_ids)
        conflict_keys["external_incidents"] = incident_conflicts
    display_names.update(resolved_display)
    warnings.extend(scope_warnings)
    comparable_prior, no_prior_reason, comparison_warnings = _load_compatible_prior(
        request, prior_bundle
    )
    warnings.extend(comparison_warnings)
    prior_customers = (
        {customer.customer_id: customer for customer in comparable_prior.customers}
        if comparable_prior is not None
        else {}
    )
    customers: List[CustomerAnalysis] = []
    all_evidence: Dict[str, EvidenceReference] = {}
    for customer_id in customer_ids:
        customer_name = display_names.get(customer_id) or customer_id
        customer_aliases = tuple(aliases.get(customer_id, set())) or (customer_name,)
        customer, evidence, _ = _build_customer_base(
            request=request,
            customer_id=customer_id,
            customer_name=customer_name,
            aliases=customer_aliases,
            partitions=partitions,
            safe_frames=safe_frames,
            global_states=global_states,
            global_freshness=global_freshness,
            conflict_customer_keys=conflict_keys,
            sources=effective_sources,
        )
        prior_customer = prior_customers.get(customer_id)
        change_reason = (
            "customer_added"
            if comparable_prior is not None and prior_customer is None
            else no_prior_reason
        )
        customer = replace(
            customer,
            temporal_changes=_customer_temporal_changes(
                customer,
                prior_customer,
                as_of_time=request.as_of_time,
                prior_as_of_time=(
                    comparable_prior.context.as_of_time
                    if comparable_prior is not None
                    else ""
                ),
                no_prior_reason=change_reason,
            ),
        )
        customers.append(customer)
        for item in evidence:
            all_evidence[item.evidence_id] = item
    ranked_customers, actions = _rank_actions(customers)
    portfolio = _build_portfolio(
        request=request,
        customers=ranked_customers,
        actions=actions,
        prior=(comparable_prior.portfolio if comparable_prior is not None else None),
        no_prior_reason=no_prior_reason,
    )

    missing_evidence = tuple(
        f"{customer.customer_name}: {source_type}"
        for customer in ranked_customers
        for source_type in customer.data_quality.missing_sources
    )
    contradictory = tuple(
        f"{customer.customer_name}: {source_type} ownership conflict"
        for customer in ranked_customers
        for source_type in customer.data_quality.conflicted_sources
    )
    stale_evidence = tuple(
        f"{customer.customer_name}: {source_type}"
        for customer in ranked_customers
        for source_type in customer.data_quality.stale_sources
    )
    degraded = tuple(
        f"{customer.customer_name}: canonical risk is UNKNOWN"
        for customer in ranked_customers
        if customer.metric("risk_score_0_100") is None
    )
    confidence_reductions = tuple(
        f"{customer.customer_name}: {customer.data_quality.confidence} confidence at "
        f"{customer.data_quality.coverage_ratio:.0%} coverage"
        for customer in ranked_customers
        if customer.data_quality.confidence != "HIGH"
    )
    diagnostics = AnalysisDiagnostics(
        missing_evidence=missing_evidence,
        contradictory_evidence=contradictory,
        stale_evidence=stale_evidence,
        quarantined_records=tuple(quarantined),
        unresolved_ownership_conflicts=contradictory,
        skipped_records=tuple(skipped),
        degraded_calculations=degraded,
        confidence_reductions=confidence_reductions,
        source_failures=tuple(source_failures),
    )
    cutoff_frames = dict(safe_frames)
    cutoff_frames["external_incidents"] = safe_incidents
    configuration_version = _clean_text(metadata.get("configuration_version")) or _fingerprint(
        "config", request.feature_configuration
    )
    generated = _iso_z(generated_time, default_now=True)
    context = AnalysisContext(
        request_fingerprint=request.request_fingerprint,
        comparison_scope_fingerprint=request.comparison_scope_fingerprint,
        analysis_fingerprint="",
        generated_time=generated,
        as_of_time=request.as_of_time,
        source_cutoff_time=_source_cutoff(cutoff_frames, metadata),
        selected_customers=tuple(customer.customer_id for customer in ranked_customers),
        selected_subscriptions=tuple(
            subscription
            for customer in ranked_customers
            for subscription in customer.subscriptions
        ),
        selected_technologies=tuple(
            technology
            for customer in ranked_customers
            for technology in customer.technologies
        ),
        selected_teams=tuple(
            team
            for customer in ranked_customers
            for team in customer.responsible_teams
        ),
        source_availability=global_states,
        source_freshness=global_freshness,
        configuration_version=configuration_version,
        analysis_engine_version=ANALYSIS_ENGINE_VERSION,
        warnings=tuple(dict.fromkeys(_clean_text(item) for item in warnings if _clean_text(item))),
        degraded_mode_indicators=tuple(
            dict.fromkeys(list(degraded) + list(source_failures))
        ),
        data_quality_summary=portfolio.data_quality,
    )
    provisional = AnalysisBundle(
        schema_version=ANALYSIS_SCHEMA_VERSION,
        request=request,
        context=context,
        customers=ranked_customers,
        portfolio=portfolio,
        evidence=tuple(all_evidence.values()),
        diagnostics=diagnostics,
    )
    analysis_fingerprint = _fingerprint("analysis", provisional.canonical_payload())
    final = AnalysisBundle(
        schema_version=provisional.schema_version,
        request=provisional.request,
        context=replace(
            provisional.context, analysis_fingerprint=analysis_fingerprint
        ),
        customers=provisional.customers,
        portfolio=provisional.portfolio,
        evidence=provisional.evidence,
        diagnostics=provisional.diagnostics,
    )
    if _fingerprint("analysis", final.canonical_payload()) != final.analysis_fingerprint:
        raise ValueError("Analysis fingerprint failed deterministic self-verification")
    return final


def _secure_atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                _plain(payload),
                handle,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


class AnalysisSnapshotStore:
    """Local atomic persistence for canonical bundles and comparison pointers.

    Snapshot payloads are the bounded ``AnalysisBundle`` contract; raw source
    frames, connector credentials, and process-local mutable state are never
    serialized by this store.
    """

    def __init__(self, root_directory: Path | str | None = None) -> None:
        configured = root_directory or os.getenv("ADOPTIQ_ANALYSIS_SNAPSHOT_DIR")
        self.root_directory = Path(
            configured
            or Path.home() / ".adoptiq" / "decision_intelligence" / "snapshots"
        ).expanduser()

    @staticmethod
    def _scope_token(scope_fingerprint: str) -> str:
        fingerprint = _clean_text(scope_fingerprint)
        digest = fingerprint.split(":", 1)[-1]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        return digest.lower()

    def _scope_directory(self, scope_fingerprint: str) -> Path:
        return self.root_directory / self._scope_token(scope_fingerprint)

    @staticmethod
    def _snapshot_name(bundle: AnalysisBundle) -> str:
        as_of = re.sub(r"[^0-9TZ]", "", bundle.context.as_of_time) or "unknown"
        digest = bundle.analysis_fingerprint.split(":", 1)[-1][:20]
        return f"snapshot-{as_of}-{digest}.json"

    @staticmethod
    def _verify_bundle(bundle: AnalysisBundle) -> None:
        errors = bundle.reconciliation_errors()
        if errors:
            raise ValueError("Snapshot reconciliation failed: " + "; ".join(errors))
        expected = _fingerprint("analysis", bundle.canonical_payload())
        if not bundle.analysis_fingerprint or expected != bundle.analysis_fingerprint:
            raise ValueError("Snapshot analysis fingerprint does not match its canonical payload")

    def persist(self, bundle: AnalysisBundle) -> Path:
        if not isinstance(bundle, AnalysisBundle):
            raise TypeError("bundle must be an AnalysisBundle")
        self._verify_bundle(bundle)
        self.root_directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root_directory, 0o700)
        except OSError:
            pass
        scope_directory = self._scope_directory(
            bundle.context.comparison_scope_fingerprint
        )
        snapshot_path = scope_directory / self._snapshot_name(bundle)
        _secure_atomic_json_write(snapshot_path, bundle.to_dict())
        manifest = {
            "manifest_schema_version": 1,
            "snapshot_filename": snapshot_path.name,
            "analysis_schema_version": bundle.schema_version,
            "analysis_fingerprint": bundle.analysis_fingerprint,
            "request_fingerprint": bundle.context.request_fingerprint,
            "comparison_scope_fingerprint": bundle.context.comparison_scope_fingerprint,
            "as_of_time": bundle.context.as_of_time,
            "generated_time": bundle.context.generated_time,
        }
        _secure_atomic_json_write(scope_directory / "_latest.json", manifest)
        return snapshot_path

    @staticmethod
    def load(path: Path | str) -> AnalysisBundle:
        bundle = AnalysisBundle.load(Path(path))
        AnalysisSnapshotStore._verify_bundle(bundle)
        return bundle

    def load_latest(self, request: AnalysisRequest) -> Optional[AnalysisBundle]:
        scope_directory = self._scope_directory(
            request.comparison_scope_fingerprint
        )
        candidates: List[Path] = []
        manifest_path = scope_directory / "_latest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                filename = _clean_text(manifest.get("snapshot_filename"))
                candidate = scope_directory / filename
                if filename and candidate.parent == scope_directory and candidate.is_file():
                    candidates.append(candidate)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        if scope_directory.is_dir():
            candidates.extend(
                sorted(
                    scope_directory.glob("snapshot-*.json"),
                    key=lambda path: path.name,
                    reverse=True,
                )
            )
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                bundle = self.load(candidate)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            if (
                bundle.context.comparison_scope_fingerprint
                == request.comparison_scope_fingerprint
                and _schema_major(bundle.schema_version)
                == _schema_major(ANALYSIS_SCHEMA_VERSION)
            ):
                return bundle
        return None

    def list_metadata(self, request: AnalysisRequest) -> Tuple[FrozenDict, ...]:
        scope_directory = self._scope_directory(
            request.comparison_scope_fingerprint
        )
        results: List[FrozenDict] = []
        if not scope_directory.is_dir():
            return ()
        for path in sorted(scope_directory.glob("snapshot-*.json"), reverse=True):
            try:
                bundle = self.load(path)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            if (
                bundle.context.comparison_scope_fingerprint
                != request.comparison_scope_fingerprint
            ):
                continue
            results.append(
                FrozenDict(
                    {
                        "path": str(path),
                        "schema_version": bundle.schema_version,
                        "analysis_fingerprint": bundle.analysis_fingerprint,
                        "request_fingerprint": bundle.context.request_fingerprint,
                        "comparison_scope_fingerprint": bundle.context.comparison_scope_fingerprint,
                        "as_of_time": bundle.context.as_of_time,
                        "generated_time": bundle.context.generated_time,
                        "customer_count": bundle.portfolio.customer_count,
                    }
                )
            )
        return tuple(results)


def persist_analysis_snapshot(
    bundle: AnalysisBundle,
    root_directory: Path | str | None = None,
) -> Path:
    return AnalysisSnapshotStore(root_directory).persist(bundle)


__all__ = [
    "ANALYSIS_ENGINE_VERSION",
    "ANALYSIS_SCHEMA_VERSION",
    "DEFAULT_SOURCE_TYPES",
    "AnalysisBundle",
    "AnalysisContext",
    "AnalysisDiagnostics",
    "AnalysisRequest",
    "AnalysisSnapshotStore",
    "AnalysisSources",
    "CustomerAnalysis",
    "DataQualitySummary",
    "DecisionBrief",
    "EvidenceReference",
    "Finding",
    "FrozenDict",
    "MetricValue",
    "PortfolioAnalysis",
    "RecommendedAction",
    "TemporalChange",
    "build_analysis_bundle",
    "persist_analysis_snapshot",
]
