"""Round 66 / Pass 4 - Predicate evaluators for the Ask AI eval framework.

Predicates are how the eval runner judges each question's answer. They
are intentionally simple boolean functions that take the rendered
answer string + the structured composer outputs (claims, allowed_ids,
rejected_count) and return a (passed, reason) tuple.

Design contract:
- Predicates NEVER raise on malformed inputs; they return ``(False, reason)``
  so a single bad fixture cannot poison the whole scorecard.
- Predicates are pure functions; no I/O, no module-level state.
- New predicate types are added by registering a callable in
  ``PREDICATE_REGISTRY``; the runner dispatches by ``type:`` key in the
  question YAML.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


# Result type: (passed, reason). ``reason`` is a one-line explanation
# rendered into the per-question scorecard row.
PredicateResult = Tuple[bool, str]


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def must_contain_phrase(answer: str, phrase: Any, **_: Any) -> PredicateResult:
    """Pass if ``phrase`` (case-insensitive) appears in ``answer``."""
    needle = _coerce_str(phrase).strip()
    if not needle:
        return (False, "predicate: phrase is empty")
    haystack = _coerce_str(answer)
    if not haystack:
        return (False, "predicate: answer is empty")
    return (
        needle.lower() in haystack.lower(),
        f"phrase {needle!r} present" if needle.lower() in haystack.lower()
        else f"phrase {needle!r} missing",
    )


def must_cite_source_id(
    answer: str,
    expected_id: Any,
    sources_seen: Iterable[str] = (),
    **_: Any,
) -> PredicateResult:
    """Pass if ``expected_id`` appears in the answer text or in
    ``sources_seen`` (set of allowed_ids that survived validation).

    Special token: ``expected_id == "any"`` passes when ANY non-empty
    citation marker is present in the answer (the ``[Sources: ...]``
    chrome rendered by ``compose_grounded_answer``).
    """
    expected = _coerce_str(expected_id).strip()
    if not expected:
        return (False, "predicate: expected_id is empty")
    answer_text = _coerce_str(answer)
    if expected.lower() == "any":
        match = bool(re.search(r"\[Sources:[^\]]+\]", answer_text))
        return (match, "any citation present" if match else "no citations rendered")
    if expected.upper() in answer_text.upper():
        return (True, f"id {expected} cited in answer")
    for seen in sources_seen or ():
        if _coerce_str(seen).upper() == expected.upper():
            return (True, f"id {expected} present in allowed_ids")
    return (False, f"id {expected} not cited and not in allowed_ids")


def must_not_render_pii(
    answer: str,
    pii_patterns: Iterable[Any] = (),
    **_: Any,
) -> PredicateResult:
    """Pass if NONE of ``pii_patterns`` (case-insensitive substrings)
    appears in the answer."""
    answer_text = _coerce_str(answer)
    for raw in pii_patterns or ():
        needle = _coerce_str(raw).strip()
        if not needle:
            continue
        if needle.lower() in answer_text.lower():
            return (False, f"PII pattern {needle!r} leaked into answer")
    return (True, "no PII patterns present")


_NUMBER_RE = re.compile(r"(?<![\w.])(-?\d{1,3}(?:[,_]\d{3})+|-?\d+)(?:\.\d+)?")


def _extract_numbers(text: str) -> List[float]:
    """Return every numeric token in ``text`` as float, ignoring commas
    and underscores. Skip dates/years (4-digit ints in the year range)
    when not the only numeric token present."""
    out: List[float] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).replace(",", "").replace("_", "")
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def must_render_number_within_tolerance(
    answer: str,
    value: Any,
    tolerance_pct: Any = 2,
    **_: Any,
) -> PredicateResult:
    """Pass if SOME numeric token in ``answer`` is within
    ``tolerance_pct`` of ``value``. Tolerance is symmetric and absolute
    when ``value`` is 0."""
    try:
        expected = float(value)
    except (TypeError, ValueError):
        return (False, f"predicate: value {value!r} not numeric")
    try:
        tol_pct = float(tolerance_pct)
    except (TypeError, ValueError):
        tol_pct = 2.0
    answer_text = _coerce_str(answer)
    nums = _extract_numbers(answer_text)
    if not nums:
        return (False, f"no numeric token in answer; expected ~{expected}")
    if expected == 0:
        # Absolute tolerance: any token within +/- 1 unit passes.
        for n in nums:
            if abs(n) <= 1.0:
                return (True, f"found {n} (zero with abs tol 1)")
        return (False, f"no zero-class token found; expected ~0")
    tol = abs(expected) * (tol_pct / 100.0)
    for n in nums:
        if abs(n - expected) <= tol:
            return (True, f"found {n} (within {tol_pct}% of {expected})")
    return (False, f"no number within {tol_pct}% of {expected}; got {nums[:5]}")


PREDICATE_REGISTRY: Dict[str, Any] = {
    "must_contain_phrase": must_contain_phrase,
    "must_cite_source_id": must_cite_source_id,
    "must_not_render_pii": must_not_render_pii,
    "must_render_number_within_tolerance": must_render_number_within_tolerance,
}


def evaluate(
    answer: str,
    predicate_specs: Iterable[Dict[str, Any]],
    sources_seen: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """Evaluate every predicate against the answer; return per-predicate
    results. Never raises - unknown predicate types fail with a reason."""
    results: List[Dict[str, Any]] = []
    for spec in predicate_specs or []:
        if not isinstance(spec, dict):
            results.append({"type": "unknown", "passed": False, "reason": "spec not a dict"})
            continue
        kind = _coerce_str(spec.get("type")).strip()
        fn = PREDICATE_REGISTRY.get(kind)
        if fn is None:
            results.append({"type": kind or "unknown", "passed": False, "reason": f"unknown predicate type {kind!r}"})
            continue
        try:
            kwargs = {k: v for k, v in spec.items() if k != "type"}
            kwargs["sources_seen"] = sources_seen
            passed, reason = fn(answer, **kwargs)
        except Exception as e:  # noqa: BLE001 - intentional broad catch; never poison scorecard
            results.append({"type": kind, "passed": False, "reason": f"predicate raised: {type(e).__name__}: {e}"})
            continue
        results.append({"type": kind, "passed": bool(passed), "reason": reason})
    return results


__all__ = [
    "PredicateResult",
    "PREDICATE_REGISTRY",
    "evaluate",
    "must_contain_phrase",
    "must_cite_source_id",
    "must_not_render_pii",
    "must_render_number_within_tolerance",
]
