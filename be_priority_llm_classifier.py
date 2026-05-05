"""Round 79 / Build 55 / Phase 2 (B2): LLM second-pass classifier for the
top-N BE-priority adoption barriers.

Why this module exists
----------------------
The deterministic ranker in ``be_priority_scorer`` answers "which 50 ABs
should backend engineering look at first?".  But it cannot answer
"is each of those 50 actually a TRUE_BLOCKER, or is some of it
TRAINING_GAP / FEATURE_REQUEST / NOT_A_BARRIER mis-categorised in
CSConsole?".  That triage label is exactly what an experienced
engineering leader produces in their head when they read the title +
description.  The LLM second-pass automates that read.

By design, this LLM call ONLY LABELS -- it never changes the
deterministic score.  The XLSX rank, the Word section ordering, and
the per-customer briefing all read ``be_priority_score`` from the
deterministic ranker.  The LLM tag (``be_llm_class``) is purely
descriptive metadata: it informs the ``True_Blocker_Count`` cluster
weight and gives the BE engineer a "what kind of barrier is this?"
chip in the XLSX, but it cannot promote a low-scored AB up the
ranking or demote a high-scored one.

Hybrid contract:
* deterministic_score : float 0-100  (canonical, sort key)
* llm_class           : str          (decorative, 6-enum allowlist)

Validation
----------
* The 6-enum allowlist is enforced at parse time (any other value is
  dropped to ``UNCLASSIFIED``).
* IDs absent from the input set are dropped (prevents the LLM from
  hallucinating IDs that the operator could not cross-reference).
* Missing required keys per record -> dropped, not patched.
* Drop counts surface in the per-call diag dict so the operator can
  correlate a sudden drop in classification quality with model drift.

R64 retry contract
------------------
The LLM call is wrapped to mirror the R64/B3 + R68/A4 retry pattern
used elsewhere in the codebase.  This module does NOT import
``app_simple._r64_call_llm_with_retry`` directly (that would create a
circular import); instead it accepts an ``llm_callable`` parameter so
the caller can thread the retry-wrapped callable through.  The default
``llm_callable=None`` falls through to a single attempt via
``adoptiq_backend.generate_llm_response`` so this module is also
runnable standalone for ad-hoc testing.

Cost cap
--------
Default ``n=50`` keeps the prompt at ~25k tokens (Title + Description
averaging ~250 tokens / AB), comfortably under the 120k-tokens/min
CircuIT free-tier ceiling.  Operators can override via
``Config.BE_PRIORITY_LLM_TOP_N`` if they need to broaden the triage
(e.g. n=100 for a portfolio with multiple BU-engineering teams).

Made-with: Cursor.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round 79 / B2: 6-enum allowlist.  ANY other value (or a missing key)
# drops the record from the result and is counted in the diag dict.
# ---------------------------------------------------------------------------


_ALLOWED_CLASSES: frozenset[str] = frozenset({
    "TRUE_BLOCKER",
    "TRAINING_GAP",
    "FEATURE_REQUEST",
    "DUPLICATE",
    "AMBIGUOUS",
    "NOT_A_BARRIER",
})

# Round 79 / B2: confidence enum.  Any other value is normalised to
# ``unknown`` rather than dropping the whole record -- the class is
# the decisive field, confidence is just metadata.
_ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"high", "medium", "low"})


# Round 79 / B2: hardcoded LLM system prompt + JSON contract.
_SYSTEM_PROMPT = (
    "You are a Cisco backend-engineering triage analyst. For each "
    "adoption barrier provided, classify it as ONE of these enum "
    "values, and ONLY these values:\n"
    "\n"
    "- TRUE_BLOCKER: production / data / integration issue that requires "
    "engineering intervention (NOT training, NOT a wishlist, NOT a "
    "documentation gap).\n"
    "- TRAINING_GAP: customer education or enablement gap; closeable by "
    "the CSE/CSM without engineering work.\n"
    "- FEATURE_REQUEST: enhancement, wish-list, or 'would-like'; not a "
    "real barrier.\n"
    "- DUPLICATE: matches another barrier in this list (cite the "
    "duplicate's ID in the reason).\n"
    "- AMBIGUOUS: cannot decide from the title / description provided.\n"
    "- NOT_A_BARRIER: status seems mis-categorised (e.g. a closed "
    "barrier, training-only ticket, or non-issue).\n"
    "\n"
    "Output ONLY a JSON ARRAY, one object per barrier, no markdown, no "
    "commentary outside the array. Each object must carry exactly these "
    "four keys (no others):\n"
    '{"id": "<exact AB ID>", "class": "<ENUM>", "reason": '
    '"<<=20 words>", "confidence": "high|medium|low"}\n'
    "\n"
    "Do NOT invent IDs that are not in the input list. Do NOT change "
    "the IDs. Do NOT add extra keys. If you cannot classify, use the "
    "AMBIGUOUS enum value (do NOT omit the record). Return one object "
    "per input barrier in the same order.\n"
)


def _format_briefing(top_n_df: pd.DataFrame) -> str:
    """Render the top-N AB briefing block fed to the LLM.

    Each AB renders ~6 lines: ID + score header, customer + severity,
    days open, title, description.  Title and description are truncated
    to 600 chars each so a single noisy AB does not blow the prompt
    budget.  Customer name is included verbatim (this is an operator-
    visible audit log; PII is acceptable in the LLM call but the
    diagnostic surface that records the result digests the name --
    see ``_build_per_record_diag``).
    """

    blocks: List[str] = []
    for _, row in top_n_df.iterrows():
        ab_id = str(row.get("ID") or row.get("id") or "").strip() or "UNKNOWN"
        try:
            score_val = float(row.get("be_priority_score") or 0.0)
        except (TypeError, ValueError):
            score_val = 0.0
        customer = str(
            row.get("customer_name")
            or row.get("BU_NAME")
            or row.get("CUSTOMER_NAME")
            or "Unknown Customer"
        ).strip() or "Unknown Customer"
        severity = str(row.get("severity_norm") or "Unknown").strip()
        try:
            age = int(float(row.get("open_age_days") or 0))
        except (TypeError, ValueError):
            age = 0
        title = str(row.get("title") or "").strip()
        if len(title) > 600:
            title = title[:597] + "..."
        description = str(row.get("description") or "").strip()
        if len(description) > 600:
            description = description[:597] + "..."

        blocks.append(
            f"[{ab_id} - score {score_val:.1f}]\n"
            f"Customer: {customer}\n"
            f"CSConsole_Severity: {severity}\n"
            f"Days_Open: {age}\n"
            f"Title: {title}\n"
            f"Description: {description}"
        )
    return "\n---\n".join(blocks)


def _normalise_class(raw: Any) -> str:
    """Project an LLM-emitted class string to the 6-enum allowlist or
    return an empty string if it falls outside (caller drops the record)."""

    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip().upper().replace(" ", "_").replace("-", "_")
    return cleaned if cleaned in _ALLOWED_CLASSES else ""


def _normalise_confidence(raw: Any) -> str:
    if not isinstance(raw, str):
        return "unknown"
    cleaned = raw.strip().lower()
    return cleaned if cleaned in _ALLOWED_CONFIDENCE else "unknown"


def _truncate_reason(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if len(text) > 200:
        text = text[:197] + "..."
    return text


_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]", re.MULTILINE)


def _extract_json_array(raw: str) -> Optional[List[Dict[str, Any]]]:
    """Round 79 / B2: pull a JSON array out of an LLM response.

    LLMs commonly wrap the array in markdown fences or prepend a brief
    rationale; this helper accepts the first balanced ``[...]`` block in
    document order and returns ``None`` only when no parseable array
    exists.  Conservative on purpose -- the caller treats ``None`` as
    a hard failure (every record drops to UNCLASSIFIED).
    """

    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    # Direct parse first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
    except json.JSONDecodeError:
        pass
    # Greedy match for the first balanced array
    match = _JSON_ARRAY_RE.search(text)
    if not match:
        return None
    candidate = match.group(0)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
    except json.JSONDecodeError:
        return None
    return None


def _validate_records(
    records: List[Dict[str, Any]],
    allowed_ids: set[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Filter the LLM-emitted records to the allowlists and tally drops.

    Returns ``(valid_records, drops_by_reason)``.  Each valid record
    carries the four canonical keys (id / class / reason / confidence)
    after normalisation.
    """

    valid: List[Dict[str, Any]] = []
    drops: Dict[str, int] = {
        "missing_keys": 0,
        "id_not_in_input": 0,
        "class_not_in_allowlist": 0,
        "duplicate_id": 0,
    }
    seen_ids: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            drops["missing_keys"] += 1
            continue
        ab_id_raw = rec.get("id")
        if not isinstance(ab_id_raw, str) or not ab_id_raw.strip():
            drops["missing_keys"] += 1
            continue
        ab_id = ab_id_raw.strip()
        if ab_id not in allowed_ids:
            drops["id_not_in_input"] += 1
            continue
        if ab_id in seen_ids:
            drops["duplicate_id"] += 1
            continue
        cls = _normalise_class(rec.get("class"))
        if not cls:
            drops["class_not_in_allowlist"] += 1
            continue
        valid.append({
            "id": ab_id,
            "class": cls,
            "reason": _truncate_reason(rec.get("reason")),
            "confidence": _normalise_confidence(rec.get("confidence")),
        })
        seen_ids.add(ab_id)
    return valid, drops


def classify_top_n_be_barriers(
    ab_top_n: pd.DataFrame,
    *,
    n: int = 50,
    use_llm: bool = True,
    llm_callable: Optional[Callable[[str, str], str]] = None,
    correlation_id: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Round 79 / B2: classify the top-N BE-priority barriers via LLM.

    Args:
        ab_top_n: DataFrame already enriched with ``be_priority_score``
            (typically the top-N rows by score; this helper ALSO
            re-applies the ``head(n)`` cap as a safety net so a caller
            that forgets cannot blow the prompt budget).
        n: cap on records sent to the LLM.  Defaults to 50; tunable via
            ``Config.BE_PRIORITY_LLM_TOP_N``.
        use_llm: kill-switch.  When ``False``, every row is classified
            ``UNCLASSIFIED`` with reason "LLM disabled" -- handy for
            offline tests AND for the operator who wants to skip the
            LLM cost on a quick scope.
        llm_callable: optional ``(system_prompt, briefing) -> str``
            callable.  When ``None``, falls through to
            ``adoptiq_backend.generate_llm_response``.  Tests pin this
            so they can simulate the LLM offline; production wires
            ``app_simple._r64_call_llm_with_retry`` here so the call
            inherits R64's bounded retry / fallback contract.
        correlation_id: passed through to log lines for cross-record
            correlation.  Optional.

    Returns:
        ``(ab_top_n_classified, diag)`` where ``diag`` records:
            - ``input_count``        (int)
            - ``classified_count``   (int)
            - ``llm_dropped``        (int, total drops)
            - ``llm_dropped_by_reason`` (dict)
            - ``llm_disabled``       (bool, True when use_llm=False)
            - ``llm_error``          (str|None, ERROR: prefix or None)
            - ``records``            (list of per-record digests, R65/C-3 pattern)

    The returned DataFrame is ``ab_top_n.head(n)`` enriched with three
    new columns: ``be_llm_class``, ``be_llm_reason``,
    ``be_llm_confidence``.  Rows that the LLM dropped get
    ``UNCLASSIFIED`` / "" / "unknown" so downstream consumers always
    see the column.
    """

    diag: Dict[str, Any] = {
        "input_count": 0,
        "classified_count": 0,
        "llm_dropped": 0,
        "llm_dropped_by_reason": {},
        "llm_disabled": not use_llm,
        "llm_error": None,
        "records": [],
        "n_cap": int(n) if n else 50,
        "correlation_id": correlation_id,
    }

    if ab_top_n is None or ab_top_n.empty:
        return pd.DataFrame(), diag

    df = ab_top_n.head(int(n) if n else 50).copy()
    diag["input_count"] = int(len(df))

    # Round 79 / B2: every row gets the column up-front so the schema is
    # consistent regardless of branch.  Defaults populated below.
    df["be_llm_class"] = "UNCLASSIFIED"
    df["be_llm_reason"] = ""
    df["be_llm_confidence"] = "unknown"

    if not use_llm:
        df["be_llm_reason"] = "LLM disabled by operator"
        diag["records"] = [
            {"id_digest": _digest_id(str(r.get("ID") or "")), "outcome": "skipped"}
            for _, r in df.iterrows()
        ]
        return df, diag

    briefing = _format_briefing(df)
    if not briefing.strip():
        df["be_llm_reason"] = "LLM skipped: empty briefing"
        diag["llm_error"] = "empty_briefing"
        return df, diag

    if llm_callable is None:
        # Lazy import to avoid pulling adoptiq_backend at module load
        # time.  Tests inject ``llm_callable=`` so this branch is only
        # exercised in production.
        try:
            from adoptiq_backend import generate_llm_response as _gen_response

            def _default(system: str, brief: str) -> str:
                return _gen_response(system, brief)

            llm_callable = _default
        except Exception as exc:  # noqa: BLE001
            df["be_llm_reason"] = f"LLM unavailable ({exc})"
            diag["llm_error"] = f"import_error:{exc}"
            logger.warning(
                "Round 79 / B2: generate_llm_response unavailable (%s); "
                "all records UNCLASSIFIED",
                exc,
            )
            return df, diag

    try:
        raw = llm_callable(_SYSTEM_PROMPT, briefing)
    except Exception as exc:  # noqa: BLE001
        df["be_llm_reason"] = f"LLM call raised ({type(exc).__name__})"
        diag["llm_error"] = f"exception:{type(exc).__name__}"
        logger.warning(
            "Round 79 / B2: LLM call raised %s; all records UNCLASSIFIED; "
            "correlation_id=%s",
            type(exc).__name__,
            correlation_id or "n/a",
        )
        return df, diag

    if not raw or (isinstance(raw, str) and raw.startswith("ERROR:")):
        df["be_llm_reason"] = "LLM returned empty or ERROR response"
        diag["llm_error"] = str(raw or "empty")[:200]
        logger.warning(
            "Round 79 / B2: LLM returned %s; all records UNCLASSIFIED; "
            "correlation_id=%s",
            "ERROR" if isinstance(raw, str) and raw.startswith("ERROR:") else "empty",
            correlation_id or "n/a",
        )
        return df, diag

    parsed = _extract_json_array(str(raw))
    if parsed is None:
        df["be_llm_reason"] = "LLM response not parseable as JSON array"
        diag["llm_error"] = "json_parse_error"
        logger.warning(
            "Round 79 / B2: LLM response not parseable as JSON array; "
            "all records UNCLASSIFIED; correlation_id=%s",
            correlation_id or "n/a",
        )
        return df, diag

    allowed_ids: set[str] = {
        str(v).strip()
        for v in df.get("ID", pd.Series(dtype=str))
        if isinstance(v, str) and v.strip()
    }
    valid_records, drops = _validate_records(parsed, allowed_ids)
    diag["llm_dropped_by_reason"] = drops
    diag["llm_dropped"] = int(sum(drops.values()))

    by_id: Dict[str, Dict[str, Any]] = {r["id"]: r for r in valid_records}
    classified = 0
    record_digests: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        ab_id_raw = row.get("ID")
        if not isinstance(ab_id_raw, str) or not ab_id_raw.strip():
            record_digests.append({
                "id_digest": _digest_id(""),
                "outcome": "missing_id",
            })
            continue
        ab_id = ab_id_raw.strip()
        rec = by_id.get(ab_id)
        if rec:
            df.at[idx, "be_llm_class"] = rec["class"]
            df.at[idx, "be_llm_reason"] = rec["reason"]
            df.at[idx, "be_llm_confidence"] = rec["confidence"]
            classified += 1
            record_digests.append({
                "id_digest": _digest_id(ab_id),
                "outcome": "classified",
                "class": rec["class"],
                "confidence": rec["confidence"],
            })
        else:
            record_digests.append({
                "id_digest": _digest_id(ab_id),
                "outcome": "dropped_or_missing_from_response",
            })

    diag["classified_count"] = int(classified)
    diag["records"] = record_digests
    return df, diag


def _digest_id(ab_id: str) -> str:
    """Round 79 / B2: 8-char digest of an AB ID for diagnostic surfaces.

    Mirrors the R65/C-3 pattern: PII-bearing identifiers (and AB IDs
    can echo customer numbers in some Snowflake exports) are routed
    through a one-way digest before persisting to ``analysis_status``
    so the operator-visible diagnostic surface carries no PII.
    """

    import hashlib

    if not isinstance(ab_id, str):
        ab_id = str(ab_id or "")
    digest = hashlib.sha256(ab_id.encode("utf-8")).hexdigest()[:8]
    return digest


__all__ = [
    "classify_top_n_be_barriers",
]
