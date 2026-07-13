"""Pure compatibility projections for :mod:`decision_intelligence`.

The frozen :class:`~decision_intelligence.AnalysisBundle` is the only factual
input accepted by this module.  The adapters do not read source frames, call a
scorer, inspect process globals, save files, or perform network I/O.

Every stamped projection carries a deterministic manifest.  The manifest ties
the payload to one analysis fingerprint, the bundle customer universe, the
canonical portfolio metrics, and an explicit evidence whitelist.  This lets
``validate_cross_output_reconciliation`` detect both cross-run mixing and
payload mutation after projection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import copy
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from typing import Any, Optional

import pandas as pd

from decision_intelligence import AnalysisBundle, CustomerAnalysis, DecisionBrief


_MANIFEST_KEY = "_decision_intelligence"
_MANIFEST_VERSION = "1.0"
_RISK_BANDS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY", "UNKNOWN")
_UNAVAILABLE_STATES = frozenset(
    {"UNAVAILABLE", "INSUFFICIENT_EVIDENCE", "N/A", "UNKNOWN"}
)


def _plain(value: Any, *, text_limit: Optional[int] = None) -> Any:
    """Return a deterministic JSON-compatible copy of a contract value."""

    if isinstance(value, pd.DataFrame):
        return {
            "columns": [str(column) for column in value.columns],
            "rows": [
                [_plain(cell, text_limit=text_limit) for cell in row]
                for row in value.itertuples(index=False, name=None)
            ],
        }
    if isinstance(value, Mapping):
        return {
            str(key): _plain(item, text_limit=text_limit)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) != _MANIFEST_KEY
        }
    if isinstance(value, (tuple, list)):
        return [_plain(item, text_limit=text_limit) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_plain(item, text_limit=text_limit) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, (datetime, date, pd.Timestamp)):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        if isinstance(value, str) and text_limit is not None and len(value) > text_limit:
            return value[: max(0, text_limit - 1)] + "…"
        return value
    if isinstance(value, float):
        return round(value, 10) if math.isfinite(value) else None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return _plain(value.item(), text_limit=text_limit)
        except (TypeError, ValueError):
            pass
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _plain(value.to_dict(), text_limit=text_limit)
        except (TypeError, ValueError):
            pass
    text = str(value)
    if text_limit is not None and len(text) > text_limit:
        return text[: max(0, text_limit - 1)] + "…"
    return text


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _finite_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer_count(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"{name} must be a non-negative integer")
    number = int(numeric)
    if number < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return number


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)] if str(value).strip() else []
    if isinstance(value, Iterable):
        return [_plain(item) for item in value]
    return [_plain(value)]


def _metric_value(customer: CustomerAnalysis, *names: str) -> Any:
    for name in names:
        value = customer.metric(name, None)
        if value is not None:
            return _plain(value)
    return None


def _legacy_band_presentation(band: str) -> tuple[str, str]:
    return {
        "CRITICAL": ("Red", "Critical Risk - Immediate Action Required"),
        "HIGH": ("Red", "High Risk - Urgent Attention Needed"),
        "MEDIUM": ("Yellow", "Moderate Risk - Monitor Closely"),
        "LOW": ("Green", "Low Risk - Standard Monitoring"),
        "HEALTHY": ("Gray", "No Renewal Risk - Minimal Engagement"),
        "UNKNOWN": ("Gray", "Risk Unavailable - Validate Evidence"),
    }[band]


def project_legacy_risk_profile(customer: CustomerAnalysis) -> dict[str, Any]:
    """Project one frozen customer profile to the legacy dual-scale shape.

    No band is inferred from a score.  The frozen canonical band remains the
    authority; an absent, unsupported, or unavailable band becomes ``UNKNOWN``.
    When exactly one numeric scale is present, its compatibility alias is a
    mechanical unit conversion rather than a new risk calculation.
    """

    profile = _plain(customer.risk_profile)
    if not isinstance(profile, dict):
        profile = {}

    band = str(profile.get("risk_band") or "UNKNOWN").strip().upper()
    if band not in _RISK_BANDS:
        band = "UNKNOWN"
    state = str(profile.get("risk_assessment_state") or "").strip().upper()
    score_100 = _finite_number(
        profile.get("risk_score_0_100", customer.metric("risk_score_0_100", None))
    )
    score_10 = _finite_number(
        profile.get(
            "risk_score_0_10",
            profile.get("score", customer.metric("risk_score_0_10", None)),
        )
    )

    if band == "UNKNOWN" or state in _UNAVAILABLE_STATES:
        band = "UNKNOWN"
        state = state or "INSUFFICIENT_EVIDENCE"
        score_100 = None
        score_10 = None
    else:
        state = state or "AVAILABLE"
        if score_100 is not None and score_10 is not None:
            # The legacy 0-10 contract is rounded to one decimal while the
            # canonical 0-100 score may retain one decimal.  That creates up
            # to 0.5 points of legitimate scale-rounding difference.
            if not math.isclose(score_100, score_10 * 10.0, abs_tol=0.51):
                raise ValueError(
                    f"Customer {customer.customer_id} has inconsistent risk scales: "
                    f"{score_100} vs {score_10}"
                )
        elif score_100 is not None:
            score_10 = score_100 / 10.0
        elif score_10 is not None:
            score_100 = score_10 * 10.0

    score_100 = round(score_100, 1) if score_100 is not None else None
    score_10 = round(score_10, 1) if score_10 is not None else None
    color, category = _legacy_band_presentation(band)

    components_raw = profile.get("components")
    components = dict(components_raw) if isinstance(components_raw, Mapping) else {}
    engagement = components.get("engagement")
    activity = components.get("activity_volume")
    if engagement is None and activity is not None:
        components["engagement"] = copy.deepcopy(activity)
    if activity is None and engagement is not None:
        components["activity_volume"] = copy.deepcopy(engagement)

    recommendations = _list_value(profile.get("recommendations"))
    if not recommendations:
        recommendations = [action.specific_action for action in customer.recommended_actions]
    next_best_actions = _list_value(profile.get("next_best_actions"))
    if not next_best_actions:
        next_best_actions = [action.to_dict() for action in customer.recommended_actions]

    return {
        "customer_name": customer.customer_name,
        "customer_id": customer.customer_id,
        "score": score_10,
        "risk_score_0_10": score_10,
        "risk_score_0_100": score_100,
        "risk_band": band,
        "risk_assessment_state": state,
        "color": color,
        "category": category,
        "risk_factors": _list_value(profile.get("risk_factors")),
        "key_findings": _list_value(profile.get("key_findings")),
        "recommendations": recommendations,
        "next_best_actions": next_best_actions,
        "components": _plain(components),
        "evidence_quality": _plain(profile.get("evidence_quality") or {}),
        "ab_count": _metric_value(
            customer, "adoption_barriers_count", "total_barriers", "ab_count"
        ),
        "case_count": _metric_value(
            customer, "support_cases_count", "total_cases", "case_count"
        ),
        "pulse_count": _metric_value(
            customer, "customer_pulse_count", "pulse_count"
        ),
        "ap_count": _metric_value(customer, "action_plans_count", "ap_count"),
    }


def project_legacy_risk_profiles(bundle: AnalysisBundle) -> dict[str, dict[str, Any]]:
    """Return deterministic legacy profiles keyed by canonical customer name."""

    result: dict[str, dict[str, Any]] = {}
    for customer in sorted(
        bundle.customers,
        key=lambda item: (item.customer_name.casefold(), item.customer_id),
    ):
        if customer.customer_name in result:
            raise ValueError(
                "Legacy risk projection requires unique canonical customer names; "
                f"duplicate {customer.customer_name!r}"
            )
        result[customer.customer_name] = project_legacy_risk_profile(customer)
    return result


def _portfolio_metric_map(bundle: AnalysisBundle) -> dict[str, Any]:
    return {metric.name: _plain(metric.value) for metric in bundle.portfolio.metrics}


def _validate_metric_alias(
    metrics: Mapping[str, Any], names: Sequence[str], expected: Any, *, label: str
) -> None:
    for name in names:
        if name not in metrics or metrics[name] is None:
            continue
        actual = metrics[name]
        if _plain(actual) != _plain(expected):
            raise ValueError(
                f"Portfolio metric {name!r} ({actual!r}) conflicts with {label} ({expected!r})"
            )


def project_canonical_portfolio_metrics(bundle: AnalysisBundle) -> dict[str, Any]:
    """Project the bundle's frozen portfolio facts to canonical metric keys."""

    source_metrics = _portfolio_metric_map(bundle)
    customer_count = int(bundle.portfolio.customer_count)
    _validate_metric_alias(
        source_metrics,
        ("total_customers", "customer_count"),
        customer_count,
        label="portfolio.customer_count",
    )

    raw_distribution = _plain(bundle.portfolio.risk_distribution)
    distribution = {band: 0 for band in _RISK_BANDS}
    if isinstance(raw_distribution, Mapping):
        for raw_band, raw_count in raw_distribution.items():
            band = str(raw_band).strip().upper()
            if band not in _RISK_BANDS:
                raise ValueError(f"Unsupported portfolio risk band: {band!r}")
            distribution[band] = _integer_count(
                raw_count, name=f"risk_distribution[{band!r}]"
            )
    if sum(distribution.values()) != customer_count:
        raise ValueError(
            "Portfolio risk_distribution must reconcile to portfolio.customer_count"
        )

    high_risk = distribution["CRITICAL"] + distribution["HIGH"]
    scored = customer_count - distribution["UNKNOWN"]
    _validate_metric_alias(
        source_metrics,
        ("high_risk_customers",),
        high_risk,
        label="CRITICAL + HIGH risk distribution",
    )
    _validate_metric_alias(
        source_metrics,
        ("unknown_risk_customers",),
        distribution["UNKNOWN"],
        label="UNKNOWN risk distribution",
    )
    for metric_name, band in (
        ("critical_risk_customers", "CRITICAL"),
        ("high_only_risk_customers", "HIGH"),
        ("medium_risk_customers", "MEDIUM"),
        ("low_risk_customers", "LOW"),
        ("healthy_customers", "HEALTHY"),
    ):
        _validate_metric_alias(
            source_metrics,
            (metric_name,),
            distribution[band],
            label=f"{band} risk distribution",
        )
    _validate_metric_alias(
        source_metrics,
        ("scored_customers",),
        scored,
        label="non-UNKNOWN risk distribution",
    )
    if "risk_band_counts" in source_metrics:
        raw_counts = source_metrics["risk_band_counts"]
        if not isinstance(raw_counts, Mapping):
            raise ValueError("Portfolio metric 'risk_band_counts' must be a mapping")
        supplied_counts = {band: 0 for band in _RISK_BANDS}
        for key, value in raw_counts.items():
            band = str(key).upper()
            if band not in _RISK_BANDS:
                raise ValueError(f"Unsupported portfolio risk band: {band!r}")
            supplied_counts[band] = _integer_count(
                value, name=f"risk_band_counts[{band!r}]"
            )
        if supplied_counts != distribution:
            raise ValueError(
                "Portfolio metric 'risk_band_counts' conflicts with risk_distribution"
            )

    derived_names = {
        "total_customers",
        "customer_count",
        "high_risk_customers",
        "critical_risk_customers",
        "high_only_risk_customers",
        "medium_risk_customers",
        "low_risk_customers",
        "healthy_customers",
        "unknown_risk_customers",
        "scored_customers",
        "risk_band_counts",
    }
    result: dict[str, Any] = {
        "total_customers": customer_count,
        "high_risk_customers": high_risk,
        "critical_risk_customers": distribution["CRITICAL"],
        "high_only_risk_customers": distribution["HIGH"],
        "medium_risk_customers": distribution["MEDIUM"],
        "low_risk_customers": distribution["LOW"],
        "healthy_customers": distribution["HEALTHY"],
        "unknown_risk_customers": distribution["UNKNOWN"],
        "scored_customers": scored,
        "risk_band_counts": {
            band: distribution[band]
            for band in _RISK_BANDS
        },
    }
    for name in sorted(source_metrics):
        if name not in derived_names:
            result[name] = source_metrics[name]
    return result


def _customer_order(bundle: AnalysisBundle) -> list[CustomerAnalysis]:
    by_id = {customer.customer_id: customer for customer in bundle.customers}
    ordered: list[CustomerAnalysis] = []
    seen: set[str] = set()
    for customer_id in bundle.portfolio.ranked_customer_ids:
        customer = by_id.get(customer_id)
        if customer is not None and customer_id not in seen:
            ordered.append(customer)
            seen.add(customer_id)
    for customer in sorted(
        bundle.customers,
        key=lambda item: (item.customer_name.casefold(), item.customer_id),
    ):
        if customer.customer_id not in seen:
            ordered.append(customer)
            seen.add(customer.customer_id)
    return ordered


def _join_excel(values: Iterable[Any]) -> str:
    return "\n".join(str(value).strip() for value in values if str(value).strip())


def _brief_action_text(
    brief: DecisionBrief, actions: Iterable[Any]
) -> tuple[str, str]:
    action_map = {action.action_id: action.specific_action for action in actions}
    ids = list(brief.next_action_ids)
    return _join_excel(ids), _join_excel(action_map[action_id] for action_id in ids if action_id in action_map)


def customer_decision_brief_frame(bundle: AnalysisBundle) -> pd.DataFrame:
    """Return one deterministic, Excel-safe row per customer decision brief."""

    columns = [
        "Analysis_Fingerprint",
        "Customer_ID",
        "Customer",
        "Risk_Score_0_10",
        "Risk_Score_0_100",
        "Risk_Band",
        "Risk_Assessment_State",
        "Confidence",
        "Data_Coverage",
        "What_Changed",
        "Why_It_Matters",
        "Next_Action_IDs",
        "Next_Actions",
        "What_Remains_Uncertain",
        "Evidence_IDs",
        "Synthesis",
        "Metrics_JSON",
    ]
    rows: list[dict[str, Any]] = []
    for customer in _customer_order(bundle):
        profile = project_legacy_risk_profile(customer)
        action_ids, action_text = _brief_action_text(
            customer.decision_brief, customer.recommended_actions
        )
        rows.append(
            {
                "Analysis_Fingerprint": bundle.analysis_fingerprint,
                "Customer_ID": customer.customer_id,
                "Customer": customer.customer_name,
                "Risk_Score_0_10": profile["risk_score_0_10"],
                "Risk_Score_0_100": profile["risk_score_0_100"],
                "Risk_Band": profile["risk_band"],
                "Risk_Assessment_State": profile["risk_assessment_state"],
                "Confidence": customer.decision_brief.confidence,
                "Data_Coverage": customer.data_quality.coverage_ratio,
                "What_Changed": _join_excel(customer.decision_brief.what_changed),
                "Why_It_Matters": _join_excel(customer.decision_brief.why_it_matters),
                "Next_Action_IDs": action_ids,
                "Next_Actions": action_text,
                "What_Remains_Uncertain": _join_excel(
                    customer.decision_brief.what_remains_uncertain
                ),
                "Evidence_IDs": _join_excel(customer.decision_brief.evidence_ids),
                "Synthesis": customer.decision_brief.synthesis,
                "Metrics_JSON": _canonical_json(
                    {metric.name: _plain(metric.value) for metric in customer.metrics}
                ),
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    return stamp_projection(bundle, "customer_decision_briefs", frame)


def portfolio_decision_brief_frame(bundle: AnalysisBundle) -> pd.DataFrame:
    """Return the portfolio Decision Brief as a one-row Excel-safe frame."""

    metrics = project_canonical_portfolio_metrics(bundle)
    brief = bundle.portfolio.decision_brief
    action_ids, action_text = _brief_action_text(
        brief, bundle.portfolio.recommended_actions
    )
    row = {
        "Analysis_Fingerprint": bundle.analysis_fingerprint,
        "Portfolio_ID": bundle.portfolio.portfolio_id,
        "Portfolio_Scope": bundle.portfolio.portfolio_scope,
        "Total_Customers": metrics["total_customers"],
        "High_Risk_Customers": metrics["high_risk_customers"],
        "Unknown_Risk_Customers": metrics["unknown_risk_customers"],
        "Confidence": brief.confidence,
        "Data_Coverage": bundle.portfolio.data_quality.coverage_ratio,
        "What_Changed": _join_excel(brief.what_changed),
        "Why_It_Matters": _join_excel(brief.why_it_matters),
        "Next_Action_IDs": action_ids,
        "Next_Actions": action_text,
        "What_Remains_Uncertain": _join_excel(brief.what_remains_uncertain),
        "Evidence_IDs": _join_excel(brief.evidence_ids),
        "Synthesis": brief.synthesis,
        "Canonical_Metrics_JSON": _canonical_json(metrics),
    }
    frame = pd.DataFrame([row], columns=list(row))
    return stamp_projection(bundle, "portfolio_decision_brief", frame)


def decision_brief_excel_frames(bundle: AnalysisBundle) -> dict[str, Any]:
    """Return stamped Word/Excel-compatible Decision Brief sheet frames."""

    payload = {
        "Customer_Decision_Briefs": customer_decision_brief_frame(bundle),
        "Portfolio_Decision_Brief": portfolio_decision_brief_frame(bundle),
        "canonical_metrics": project_canonical_portfolio_metrics(bundle),
    }
    return stamp_projection(bundle, "export", payload)


def _brief_content(
    brief: DecisionBrief,
    actions: Iterable[Any],
) -> dict[str, Any]:
    action_map = {action.action_id: action.specific_action for action in actions}
    return {
        "scope_kind": brief.scope_kind,
        "scope_id": brief.scope_id,
        "synthesis": brief.synthesis,
        "what_changed": list(brief.what_changed),
        "why_it_matters": list(brief.why_it_matters),
        "next_actions": [
            {
                "action_id": action_id,
                "action": action_map.get(action_id, "Action details unavailable"),
            }
            for action_id in brief.next_action_ids
        ],
        "what_remains_uncertain": list(brief.what_remains_uncertain),
        "evidence_ids": list(brief.evidence_ids),
        "confidence": brief.confidence,
    }


def _render_bullets(document: Any, heading: str, values: Iterable[str], *, level: int) -> None:
    items = [str(value) for value in values if str(value).strip()]
    document.add_heading(heading, level=level)
    if not items:
        document.add_paragraph("None identified from the available evidence.")
        return
    for item in items:
        document.add_paragraph(item, style="List Bullet")


def _render_brief_content(document: Any, content: Mapping[str, Any], *, heading_level: int) -> None:
    if content.get("synthesis"):
        document.add_paragraph(str(content["synthesis"]))
    document.add_paragraph(f"Confidence: {content.get('confidence') or 'UNKNOWN'}")
    _render_bullets(
        document,
        "What changed",
        content.get("what_changed") or (),
        level=heading_level,
    )
    _render_bullets(
        document,
        "Why it matters",
        content.get("why_it_matters") or (),
        level=heading_level,
    )
    next_actions = [
        f"{item.get('action_id')}: {item.get('action')}"
        for item in content.get("next_actions") or ()
        if isinstance(item, Mapping)
    ]
    _render_bullets(document, "Next actions", next_actions, level=heading_level)
    _render_bullets(
        document,
        "What remains uncertain",
        content.get("what_remains_uncertain") or (),
        level=heading_level,
    )


def render_decision_brief_word(
    document: Any,
    bundle: AnalysisBundle,
    *,
    include_customers: bool = True,
    max_customers: Optional[int] = None,
) -> dict[str, Any]:
    """Render Decision Brief sections into an in-memory python-docx Document.

    The helper deliberately never calls ``Document.save``.  It returns the
    exact stamped content model used for rendering so reconciliation can verify
    the report without reading a generated file.
    """

    if not all(hasattr(document, name) for name in ("add_heading", "add_paragraph", "add_table")):
        raise TypeError("document must provide the python-docx Document API")
    if max_customers is not None:
        max_customers = _validated_limit(
            max_customers,
            name="max_customers",
            maximum=500,
        )

    ordered = _customer_order(bundle)
    if not include_customers:
        selected: list[CustomerAnalysis] = []
    elif max_customers is None:
        selected = ordered
    else:
        selected = ordered[: int(max_customers)]

    content = {
        "analysis_fingerprint": bundle.analysis_fingerprint,
        "as_of_time": bundle.context.as_of_time,
        "canonical_metrics": project_canonical_portfolio_metrics(bundle),
        "portfolio": _brief_content(
            bundle.portfolio.decision_brief,
            bundle.portfolio.recommended_actions,
        ),
        "customers": [
            {
                "customer_id": customer.customer_id,
                "customer_name": customer.customer_name,
                "risk": project_legacy_risk_profile(customer),
                "brief": _brief_content(
                    customer.decision_brief,
                    customer.recommended_actions,
                ),
            }
            for customer in selected
        ],
    }

    document.add_heading("Portfolio Decision Brief", level=1)
    document.add_paragraph(f"As of: {bundle.context.as_of_time}")
    metrics_table = document.add_table(rows=1, cols=2)
    metrics_table.cell(0, 0).text = "Metric"
    metrics_table.cell(0, 1).text = "Value"
    for name, value in content["canonical_metrics"].items():
        if isinstance(value, Mapping):
            continue
        cells = metrics_table.add_row().cells
        cells[0].text = str(name)
        cells[1].text = "n/a" if value is None else str(value)
    _render_brief_content(document, content["portfolio"], heading_level=2)

    for customer_content in content["customers"]:
        document.add_heading(
            f"Customer Decision Brief: {customer_content['customer_name']}",
            level=1,
        )
        risk = customer_content["risk"]
        score = risk["risk_score_0_10"]
        score_text = "n/a" if score is None else f"{score:.1f}/10"
        document.add_paragraph(
            f"Risk: {risk['risk_band']} ({score_text}); "
            f"assessment state: {risk['risk_assessment_state']}"
        )
        _render_brief_content(document, customer_content["brief"], heading_level=2)

    evidence_ids = set(bundle.portfolio.decision_brief.evidence_ids)
    for action in bundle.portfolio.recommended_actions:
        evidence_ids.update(action.evidence_ids)
    for customer in selected:
        evidence_ids.update(customer.evidence_ids)
        for action in customer.recommended_actions:
            evidence_ids.update(action.evidence_ids)
    return stamp_projection(
        bundle,
        "report",
        content,
        customer_ids=[customer.customer_id for customer in selected],
        evidence_ids=evidence_ids,
    )


def _validated_limit(value: int, *, name: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer between 0 and {maximum}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer between 0 and {maximum}") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"{name} must be an integer between 0 and {maximum}")
    parsed = int(numeric)
    if parsed < 0 or parsed > maximum:
        raise ValueError(f"{name} must be an integer between 0 and {maximum}")
    return parsed


def _safe_brief(
    brief: DecisionBrief,
    *,
    evidence_whitelist: set[str],
    allowed_action_ids: set[str],
) -> dict[str, Any]:
    return {
        "what_changed": [_plain(value, text_limit=500) for value in brief.what_changed[:12]],
        "why_it_matters": [_plain(value, text_limit=500) for value in brief.why_it_matters[:12]],
        "next_action_ids": [
            action_id for action_id in brief.next_action_ids[:12]
            if action_id in allowed_action_ids
        ],
        "what_remains_uncertain": [
            _plain(value, text_limit=500)
            for value in brief.what_remains_uncertain[:12]
        ],
        "evidence_ids": [
            evidence_id for evidence_id in brief.evidence_ids
            if evidence_id in evidence_whitelist
        ],
        "confidence": brief.confidence,
        "synthesis": _plain(brief.synthesis, text_limit=1_200),
    }


def _whitelist_evidence_fields(value: Any, whitelist: set[str]) -> Any:
    """Bound nested compatibility data and remove non-whitelisted citations."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            key_text = str(key)
            key_norm = key_text.casefold()
            if key_norm == "evidence_id":
                if str(item) in whitelist:
                    result[key_text] = str(item)
                continue
            if key_norm in {"evidence_ids", "citations", "evidence_whitelist"}:
                values = item if isinstance(item, (list, tuple, set, frozenset)) else ()
                result[key_text] = [str(candidate) for candidate in values if str(candidate) in whitelist]
                continue
            result[key_text] = _whitelist_evidence_fields(item, whitelist)
            if len(result) >= 60:
                break
        return result
    if isinstance(value, (list, tuple)):
        return [_whitelist_evidence_fields(item, whitelist) for item in value[:60]]
    return _plain(value, text_limit=500)


def ask_ai_safe_projection(
    bundle: AnalysisBundle,
    *,
    allowed_evidence_ids: Optional[Iterable[str]] = None,
    max_customers: int = 100,
    max_evidence: int = 200,
    max_findings_per_customer: int = 12,
    max_actions_per_customer: int = 12,
) -> dict[str, Any]:
    """Return a bounded, citation-whitelisted Ask AI projection."""

    max_customers = _validated_limit(max_customers, name="max_customers", maximum=500)
    max_evidence = _validated_limit(max_evidence, name="max_evidence", maximum=1_000)
    max_findings_per_customer = _validated_limit(
        max_findings_per_customer,
        name="max_findings_per_customer",
        maximum=100,
    )
    max_actions_per_customer = _validated_limit(
        max_actions_per_customer,
        name="max_actions_per_customer",
        maximum=100,
    )

    selected_customers = _customer_order(bundle)[:max_customers]
    selected_ids = {customer.customer_id for customer in selected_customers}
    selected_names = {customer.customer_name for customer in selected_customers}
    requested = (
        {str(value) for value in allowed_evidence_ids}
        if allowed_evidence_ids is not None
        else None
    )
    referenced: set[str] = set(bundle.portfolio.decision_brief.evidence_ids)
    for action in bundle.portfolio.recommended_actions:
        referenced.update(action.evidence_ids)
    for customer in selected_customers:
        referenced.update(customer.evidence_ids)
        referenced.update(customer.decision_brief.evidence_ids)
        for finding in customer.findings:
            referenced.update(finding.evidence_ids)
        for action in customer.recommended_actions:
            referenced.update(action.evidence_ids)

    scoped_evidence = [
        item
        for item in bundle.evidence
        if item.evidence_id in referenced
        and (requested is None or item.evidence_id in requested)
        and item.customer_identity in {"", "Portfolio", *selected_ids, *selected_names}
    ]
    scoped_evidence.sort(key=lambda item: item.evidence_id)
    scoped_evidence = scoped_evidence[:max_evidence]
    whitelist = {item.evidence_id for item in scoped_evidence}

    customer_rows: list[dict[str, Any]] = []
    for customer in selected_customers:
        findings = []
        for finding in customer.findings:
            if len(findings) >= max_findings_per_customer:
                break
            evidence_ids = [
                evidence_id for evidence_id in finding.evidence_ids
                if evidence_id in whitelist
            ]
            if finding.kind != "data_quality" and not evidence_ids:
                continue
            findings.append(
                {
                    "finding_id": finding.finding_id,
                    "kind": finding.kind,
                    "category": finding.category,
                    "title": _plain(finding.title, text_limit=500),
                    "structured_value": _plain(
                        finding.structured_value, text_limit=500
                    ),
                    "severity": finding.severity,
                    "importance": finding.importance,
                    "direction": finding.direction,
                    "confidence": finding.confidence,
                    "evidence_ids": evidence_ids,
                }
            )

        actions = []
        for action in customer.recommended_actions:
            if len(actions) >= max_actions_per_customer:
                break
            evidence_ids = [
                evidence_id for evidence_id in action.evidence_ids
                if evidence_id in whitelist
            ]
            if not evidence_ids:
                continue
            actions.append(
                {
                    "action_id": action.action_id,
                    "specific_action": _plain(action.specific_action, text_limit=500),
                    "rationale": _plain(action.rationale, text_limit=500),
                    "proposed_owner": action.proposed_owner,
                    "urgency": action.urgency,
                    "rank": action.rank,
                    "priority_score": action.priority_score,
                    "evidence_ids": evidence_ids,
                }
            )
        action_ids = {item["action_id"] for item in actions}
        safe_risk_profile = _whitelist_evidence_fields(
            project_legacy_risk_profile(customer),
            whitelist,
        )
        # The structured actions above are the only action records admitted to
        # the Ask projection.  Replacing this legacy alias prevents a small
        # evidence cap from leaking a dropped action's citation through the
        # compatibility profile.
        safe_risk_profile["next_best_actions"] = copy.deepcopy(actions)
        customer_rows.append(
            {
                "customer_id": customer.customer_id,
                "customer_name": customer.customer_name,
                "risk_profile": safe_risk_profile,
                "metrics": {
                    metric.name: _plain(metric.value, text_limit=500)
                    for metric in customer.metrics[:50]
                },
                "findings": findings,
                "actions": actions,
                "decision_brief": _safe_brief(
                    customer.decision_brief,
                    evidence_whitelist=whitelist,
                    allowed_action_ids=action_ids,
                ),
                "data_quality": customer.data_quality.to_dict(),
            }
        )

    portfolio_actions = []
    for action in bundle.portfolio.recommended_actions[:max_actions_per_customer]:
        evidence_ids = [
            evidence_id for evidence_id in action.evidence_ids
            if evidence_id in whitelist
        ]
        if evidence_ids:
            portfolio_actions.append(
                {
                    "action_id": action.action_id,
                    "specific_action": _plain(action.specific_action, text_limit=500),
                    "rank": action.rank,
                    "priority_score": action.priority_score,
                    "evidence_ids": evidence_ids,
                }
            )
    portfolio_action_ids = {item["action_id"] for item in portfolio_actions}
    payload = {
        "schema_version": bundle.schema_version,
        "analysis_fingerprint": bundle.analysis_fingerprint,
        "request_fingerprint": bundle.context.request_fingerprint,
        "as_of_time": bundle.context.as_of_time,
        "canonical_metrics": project_canonical_portfolio_metrics(bundle),
        "customers": customer_rows,
        "portfolio": {
            "portfolio_id": bundle.portfolio.portfolio_id,
            "portfolio_scope": bundle.portfolio.portfolio_scope,
            "decision_brief": _safe_brief(
                bundle.portfolio.decision_brief,
                evidence_whitelist=whitelist,
                allowed_action_ids=portfolio_action_ids,
            ),
            "actions": portfolio_actions,
            "risk_distribution": _plain(bundle.portfolio.risk_distribution),
            "data_quality": bundle.portfolio.data_quality.to_dict(),
        },
        "evidence_whitelist": sorted(whitelist),
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "source_type": item.source_type,
                "source_id": item.stable_source_identifier,
                "customer": item.customer_identity,
                "field": item.relevant_field,
                "value": _plain(item.observed_value, text_limit=500),
                "observed_at": item.observation_timestamp,
                "freshness": item.source_freshness,
                "conflict_status": item.conflict_status,
                "excerpt": _plain(item.safe_excerpt, text_limit=320),
            }
            for item in scoped_evidence
        ],
        "truncation": {
            "customers_total": len(bundle.customers),
            "customers_returned": len(selected_customers),
            "customers_truncated": len(selected_customers) < len(bundle.customers),
            "evidence_eligible": len(
                [
                    item
                    for item in bundle.evidence
                    if item.evidence_id in referenced
                    and (requested is None or item.evidence_id in requested)
                    and item.customer_identity
                    in {"", "Portfolio", *selected_ids, *selected_names}
                ]
            ),
            "evidence_returned": len(scoped_evidence),
        },
    }
    return stamp_projection(
        bundle,
        "ask_ai",
        payload,
        customer_ids=[customer.customer_id for customer in selected_customers],
        evidence_ids=whitelist,
    )


def build_projection_manifest(
    bundle: AnalysisBundle,
    projection_kind: str,
    payload: Any,
    *,
    customer_ids: Optional[Iterable[str]] = None,
    evidence_ids: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Build a deterministic, source-free reconciliation manifest."""

    kind = str(projection_kind or "").strip()
    if not kind:
        raise ValueError("projection_kind is required")
    all_customers = tuple(bundle.portfolio.customer_ids)
    requested_customers = (
        {str(value) for value in customer_ids}
        if customer_ids is not None
        else set(all_customers)
    )
    unknown_customers = sorted(requested_customers - set(all_customers))
    if unknown_customers:
        raise ValueError(
            f"Projection contains customers outside the bundle: {unknown_customers}"
        )
    projected_customers = tuple(
        customer_id for customer_id in all_customers
        if customer_id in requested_customers
    )
    projected_evidence = tuple(
        sorted(set(str(value) for value in (evidence_ids or ())))
    )
    known_evidence = {item.evidence_id for item in bundle.evidence}
    unknown_evidence = sorted(set(projected_evidence) - known_evidence)
    if unknown_evidence:
        raise ValueError(
            f"Projection contains evidence outside the bundle: {unknown_evidence}"
        )
    metrics = project_canonical_portfolio_metrics(bundle)
    return {
        "manifest_version": _MANIFEST_VERSION,
        "projection_kind": kind,
        "schema_version": bundle.schema_version,
        "analysis_fingerprint": bundle.analysis_fingerprint,
        "request_fingerprint": bundle.context.request_fingerprint,
        "bundle_customer_ids": list(all_customers),
        "projected_customer_ids": list(projected_customers),
        "evidence_ids": list(projected_evidence),
        "canonical_metrics": _plain(metrics),
        "canonical_metrics_digest": _digest(metrics),
        "payload_digest": _digest(payload),
    }


def stamp_projection(
    bundle: AnalysisBundle,
    projection_kind: str,
    payload: Any,
    *,
    customer_ids: Optional[Iterable[str]] = None,
    evidence_ids: Optional[Iterable[str]] = None,
) -> Any:
    """Return a copy of a mapping/DataFrame with a reconciliation manifest."""

    if isinstance(payload, pd.DataFrame):
        result = payload.copy(deep=True)
        result.attrs = copy.deepcopy(getattr(payload, "attrs", {}) or {})
        result.attrs[_MANIFEST_KEY] = build_projection_manifest(
            bundle,
            projection_kind,
            result,
            customer_ids=customer_ids,
            evidence_ids=evidence_ids,
        )
        return result
    if isinstance(payload, Mapping):
        result = copy.deepcopy(dict(payload))
        result.pop(_MANIFEST_KEY, None)
        result[_MANIFEST_KEY] = build_projection_manifest(
            bundle,
            projection_kind,
            result,
            customer_ids=customer_ids,
            evidence_ids=evidence_ids,
        )
        return result
    raise TypeError("Only mappings and pandas DataFrames can be stamped")


@dataclass(frozen=True)
class ReconciliationResult:
    """Result of validating cross-output provenance and factual parity."""

    ok: bool
    analysis_fingerprint: str
    checked_projections: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "analysis_fingerprint": self.analysis_fingerprint,
            "checked_projections": list(self.checked_projections),
            "errors": list(self.errors),
        }


def _projection_parts(value: Any) -> tuple[Optional[Mapping[str, Any]], Any]:
    if isinstance(value, pd.DataFrame):
        return (getattr(value, "attrs", {}) or {}).get(_MANIFEST_KEY), value
    if isinstance(value, Mapping):
        manifest = value.get(_MANIFEST_KEY)
        payload = {key: item for key, item in value.items() if key != _MANIFEST_KEY}
        return manifest, payload
    return None, value


def _collect_referenced_evidence(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, pd.DataFrame):
        return found
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_norm = str(key).casefold()
            if key_norm == "evidence_id" and isinstance(item, str):
                found.add(item)
            elif key_norm in {"evidence_ids", "evidence_whitelist", "citations"}:
                if isinstance(item, (list, tuple, set, frozenset)):
                    found.update(str(value) for value in item)
            found.update(_collect_referenced_evidence(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_collect_referenced_evidence(item))
    return found


def _validate_projection(
    bundle: AnalysisBundle,
    expected_kind: str,
    projection: Any,
) -> list[str]:
    manifest, payload = _projection_parts(projection)
    if not isinstance(manifest, Mapping):
        return [f"{expected_kind}: reconciliation manifest is missing"]
    errors: list[str] = []
    expected_metrics = project_canonical_portfolio_metrics(bundle)
    if manifest.get("manifest_version") != _MANIFEST_VERSION:
        errors.append(f"{expected_kind}: unsupported manifest version")
    if manifest.get("projection_kind") != expected_kind:
        errors.append(
            f"{expected_kind}: manifest kind is {manifest.get('projection_kind')!r}"
        )
    if manifest.get("schema_version") != bundle.schema_version:
        errors.append(f"{expected_kind}: schema version does not match bundle")
    if manifest.get("analysis_fingerprint") != bundle.analysis_fingerprint:
        errors.append(f"{expected_kind}: analysis fingerprint does not match bundle")
    if manifest.get("request_fingerprint") != bundle.context.request_fingerprint:
        errors.append(f"{expected_kind}: request fingerprint does not match bundle")
    if list(manifest.get("bundle_customer_ids") or []) != list(
        bundle.portfolio.customer_ids
    ):
        errors.append(f"{expected_kind}: bundle customer universe does not match")
    projected_customers = set(manifest.get("projected_customer_ids") or [])
    if not projected_customers.issubset(set(bundle.portfolio.customer_ids)):
        errors.append(f"{expected_kind}: projected customers escape bundle scope")
    if _plain(manifest.get("canonical_metrics")) != _plain(expected_metrics):
        errors.append(f"{expected_kind}: canonical metrics do not match bundle")
    if manifest.get("canonical_metrics_digest") != _digest(expected_metrics):
        errors.append(f"{expected_kind}: canonical metrics digest does not match")
    if manifest.get("payload_digest") != _digest(payload):
        errors.append(f"{expected_kind}: payload changed after projection")

    known_evidence = {item.evidence_id: item for item in bundle.evidence}
    projected_names = {
        customer.customer_name
        for customer in bundle.customers
        if customer.customer_id in projected_customers
    }
    for evidence_id in manifest.get("evidence_ids") or []:
        item = known_evidence.get(str(evidence_id))
        if item is None:
            errors.append(f"{expected_kind}: evidence {evidence_id!r} is not in bundle")
        elif item.customer_identity not in {
            "",
            "Portfolio",
            *projected_customers,
            *projected_names,
        }:
            errors.append(
                f"{expected_kind}: evidence {evidence_id!r} escapes projected scope"
            )

    if isinstance(payload, Mapping):
        for metric_key in ("canonical_metrics", "portfolio_metrics"):
            if metric_key in payload and _plain(payload[metric_key]) != _plain(
                expected_metrics
            ):
                errors.append(
                    f"{expected_kind}: payload {metric_key} does not match bundle"
                )
        if "analysis_fingerprint" in payload and payload["analysis_fingerprint"] != bundle.analysis_fingerprint:
            errors.append(f"{expected_kind}: payload analysis fingerprint does not match")
        if "risk_profiles" in payload:
            expected_profiles = project_legacy_risk_profiles(bundle)
            if _plain(payload["risk_profiles"]) != _plain(expected_profiles):
                errors.append(f"{expected_kind}: legacy risk profiles do not match bundle")
    if isinstance(payload, pd.DataFrame):
        if "Analysis_Fingerprint" in payload.columns:
            values = set(payload["Analysis_Fingerprint"].dropna().astype(str))
            if values - {bundle.analysis_fingerprint}:
                errors.append(f"{expected_kind}: DataFrame mixes analysis fingerprints")
        if "Customer_ID" in payload.columns:
            values = set(payload["Customer_ID"].dropna().astype(str))
            if values != projected_customers:
                errors.append(f"{expected_kind}: DataFrame customer IDs do not match manifest")
        if "Total_Customers" in payload.columns:
            try:
                values = {
                    _integer_count(value, name="Total_Customers")
                    for value in payload["Total_Customers"].dropna()
                }
            except ValueError:
                values = set()
                errors.append(f"{expected_kind}: DataFrame total customers are invalid")
            if values - {int(expected_metrics["total_customers"])}:
                errors.append(f"{expected_kind}: DataFrame total customers do not match")

    if expected_kind == "ask_ai" and isinstance(payload, Mapping):
        whitelist = {str(value) for value in payload.get("evidence_whitelist") or []}
        manifest_evidence = {str(value) for value in manifest.get("evidence_ids") or []}
        if whitelist != manifest_evidence:
            errors.append("ask_ai: evidence whitelist does not match manifest")
        referenced = _collect_referenced_evidence(payload)
        if not referenced.issubset(whitelist):
            errors.append("ask_ai: payload references evidence outside its whitelist")
    return errors


def validate_cross_output_reconciliation(
    bundle: AnalysisBundle,
    *,
    report: Any = None,
    export: Any = None,
    renewal: Any = None,
    dashboard: Any = None,
    ask_ai: Any = None,
    require_all: bool = True,
    raise_on_error: bool = False,
) -> ReconciliationResult:
    """Prove supplied projections came from and still match one bundle."""

    projections = (
        ("report", report),
        ("export", export),
        ("renewal", renewal),
        ("dashboard", dashboard),
        ("ask_ai", ask_ai),
    )
    checked: list[str] = []
    errors: list[str] = []
    for kind, projection in projections:
        if projection is None:
            if require_all:
                errors.append(f"{kind}: required projection was not supplied")
            continue
        checked.append(kind)
        errors.extend(_validate_projection(bundle, kind, projection))
    errors = list(dict.fromkeys(errors))
    result = ReconciliationResult(
        ok=not errors,
        analysis_fingerprint=bundle.analysis_fingerprint,
        checked_projections=tuple(checked),
        errors=tuple(errors),
    )
    if raise_on_error and errors:
        raise ValueError("Cross-output reconciliation failed: " + "; ".join(errors))
    return result


# Short aliases make the migration call sites readable while preserving the
# explicit ``project_*`` names for discoverability.
legacy_risk_profile = project_legacy_risk_profile
legacy_risk_profiles = project_legacy_risk_profiles
canonical_portfolio_metrics = project_canonical_portfolio_metrics


__all__ = [
    "ReconciliationResult",
    "ask_ai_safe_projection",
    "build_projection_manifest",
    "canonical_portfolio_metrics",
    "customer_decision_brief_frame",
    "decision_brief_excel_frames",
    "legacy_risk_profile",
    "legacy_risk_profiles",
    "portfolio_decision_brief_frame",
    "project_canonical_portfolio_metrics",
    "project_legacy_risk_profile",
    "project_legacy_risk_profiles",
    "render_decision_brief_word",
    "stamp_projection",
    "validate_cross_output_reconciliation",
]
