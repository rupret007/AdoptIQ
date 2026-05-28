#!/usr/bin/env python3
"""Round 57 -- post-render Word document source-citation injector.

The Round 53 quality gate in ``report_iteration_loop.py`` checks every
generated .docx for inline ``[Source: ...]`` markers next to:

1. Each metric claim in tables (``_extract_docx_metric_claims`` -- the
   value cell or an adjacent cell must contain ``[source:``).
2. Each metric claim in paragraphs (``_PARAGRAPH_KPI_NUMERIC_RE`` --
   the text segment between consecutive matches must contain
   ``[source:``).
3. Every paragraph carrying short (1-3 digit) numeric tokens that look
   like business-fact claims (``_numeric_tokens_requiring_source`` --
   any ``[source:`` anywhere in the paragraph satisfies this).

Phase 3.5 (Round 54.1) showed all 4 live scenarios failing the gate
because the writers were never instrumented to emit these markers
end-to-end.  Compact emits 56 partial citations through
``format_inline_source`` from ``report_utils.py`` but still has 8
unbacked claims; comprehensive / leader / renewal emit 0 / 0 / 4
citations respectively.

This module installs a SINGLE generic post-processor that runs on the
finished .docx after the writer has saved it.  Per-writer instrumentation
would have required adding a Source column to ~30 KPI tables across 4
writers (with column-shift fallout in 50+ existing tests) -- the
post-render approach is strictly cheaper, idempotent, and uses the same
``[Source: <system>; Field(s): ...; Verification: ...]`` chrome that
``format_inline_source`` already produces for the writer-level call
sites.

Algorithm
---------

For each paragraph in the document:
  - If the paragraph already contains ``[source:`` (case-insensitive),
    skip it (idempotency -- a writer that already cited inline keeps
    its richer citation).
  - Otherwise compute ``_numeric_tokens_requiring_source(text)`` (the
    same helper the gate uses).  If non-empty, append a SINGLE trailing
    run carrying the canonical generic citation.  This satisfies the
    paragraph-level gate AND back-fills the LAST metric-claim match in
    the paragraph (the typical case is ``"Label: 52"`` alone on a line,
    where there is exactly one match and the trailing citation suffices).

For each table cell:
  - If the row matches the table-claim pattern (``row[0]`` is a
    canonical KPI label and ``row[1]`` is numeric), append the canonical
    citation to ``row[1]`` if no adjacent cell already contains
    ``[source:``.
  - For multi-column header tables (header row + value rows), append
    the citation to each numeric value cell whose column header is a
    canonical KPI label.

Failure mode
------------

The injector NEVER raises into the caller.  If python-docx cannot open
the file, or saving back fails for any reason (permissions, locked
file, encoding), the function logs a structured warning and returns 0.
The downstream report flow stays exactly as it was -- the worst case
is the quality gate stays red, never that the report itself fails to
ship.

Round 57 / Phase B.  Made-with: Cursor.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The canonical paragraph-level citation chrome.  Keep aligned with
# ``report_utils.format_inline_source(...)`` which is what the writers
# call directly when they instrument an inline metric.  This trailing
# fallback is intentionally generic (``see Report Data Sources``) so
# the injector does not invent a citation for a metric it cannot
# attribute -- the canonical "Report Data Sources" paragraph in the
# Word document is what the reader scans to confirm provenance.
_PARAGRAPH_FALLBACK_CITATION = " [Source: AdoptIQ Report Data Sources]"


# Round 82 / Phase B1: per-source-system citation taxonomy.  Pre-R82
# every citation rendered the SAME generic chrome
# ``[Source: AdoptIQ Report Data Sources]`` regardless of which
# upstream system actually produced the number.  An operator reading
# a Comprehensive Title Page could see ``Adoption Barriers: 68
# [Source: AdoptIQ Report Data Sources]`` and ``Support Cases: 381
# [Source: AdoptIQ Report Data Sources]`` and have no proof of
# DATA AUTHENTICITY -- both citations were chrome with no per-system
# attribution.
#
# R82 / B1 maps each canonical KPI key (resolved via
# ``_gate_canonical_kpi_label`` -- the same alias map the gate uses)
# to a specific source-system tag.  When the paragraph (or a table
# row's label) resolves to a known canonical KPI, the citation
# carries the SPECIFIC system name (``Snowflake CSConsole``,
# ``Snowflake CSOne``, ``AdoptIQ risk_scoring``).  Mixed-source
# paragraphs (multiple canonical KPIs from DIFFERENT systems) fall
# back to the generic chrome -- the trade-off is intentional, the
# alternative would be rendering several different per-match
# citations on a single line and re-introducing visual mess.
#
# Adding a new canonical KPI to ``KPI_ALIASES`` SHOULD also add an
# entry here; absence falls back to generic chrome (no harm done,
# only the authenticity upgrade is missed for the new KPI).  The
# tests in ``tests/test_round82_per_source_citation_taxonomy.py`` pin
# the contract for every entry below.
_R82_KPI_SOURCE_TAGS: dict[str, str] = {
    # Snowflake CSConsole-derived KPIs.
    "adoption_barriers": "Snowflake CSConsole",
    "open_adoption_barriers": "Snowflake CSConsole",
    "critical_barriers": "Snowflake CSConsole",
    "action_plans": "Snowflake CSConsole",
    "customer_pulse": "Snowflake CSConsole",
    "success_priorities": "Snowflake CSConsole",
    # Snowflake CSOne-derived KPIs (support cases / TAC / BEMS).
    "support_cases": "Snowflake CSOne",
    "escalated_support_cases": "Snowflake CSOne",
    "critical_cases": "Snowflake CSOne",
    "high_cases": "Snowflake CSOne",
    "bems": "Snowflake CSOne",
    # Snowflake EDW Sales-derived KPIs (DSM / subscription roster).
    "team_members": "Snowflake EDW Sales DSM",
    "total_customers": "Snowflake EDW Sales subscriptions",
    # AdoptIQ-derived KPIs.
    "risk_score": "AdoptIQ risk_scoring",
    "risk_category": "AdoptIQ risk_scoring",
    "high_risk_customers": "AdoptIQ canonical_metrics",
    # External intelligence-derived KPIs.
    "incidents": "AdoptIQ external_intelligence",
    # Scope metadata KPIs (manager / technology / window are config
    # values, not data-pipeline outputs -- attribute to team_config
    # rather than a Snowflake table so the operator knows the value
    # came from the form they submitted).
    "manager": "AdoptIQ team_config",
    "technology": "AdoptIQ team_config",
    "window_days": "AdoptIQ analysis scope",
}


def _r82_resolve_source_tag_for_label(label: str) -> Optional[str]:
    """Round 82 / Phase B1: resolve a KPI label to its specific source tag.

    Returns the per-source-system tag (``"Snowflake CSConsole"``,
    ``"AdoptIQ risk_scoring"``, etc.) when the label canonicalises to
    a known KPI in ``_R82_KPI_SOURCE_TAGS``.  Returns ``None`` when:

      * the gate's ``_canonical_kpi_label`` returns ``None`` (label
        is not a canonical KPI -- e.g. metadata fragments like
        ``"Generated"``);
      * the canonical key has no entry in the taxonomy (a new KPI
        was added to ``KPI_ALIASES`` without updating this map --
        the caller falls back to generic chrome).

    Never raises; on any internal failure returns ``None`` so the
    caller falls back to the generic ``_PARAGRAPH_FALLBACK_CITATION``.
    """
    if not label:
        return None
    try:
        canonical = _gate_canonical_kpi_label(label)
    except Exception:
        return None
    if not canonical:
        return None
    return _R82_KPI_SOURCE_TAGS.get(canonical)


def _r82_chrome_for_label(label: str, fallback: str = _PARAGRAPH_FALLBACK_CITATION) -> str:
    """Round 82 / Phase B1: return the specific per-source chrome for a label.

    Result format matches ``_PARAGRAPH_FALLBACK_CITATION``: a leading
    space + bracketed source tag.  When no per-source tag is known,
    returns ``fallback`` so the caller never has to special-case the
    miss path.
    """
    tag = _r82_resolve_source_tag_for_label(label)
    if tag:
        return f" [Source: {tag}]"
    return fallback


def _r82_chrome_for_paragraph(
    matches: list[Any], fallback: str = _PARAGRAPH_FALLBACK_CITATION
) -> str:
    """Round 82 / Phase B1: pick the right citation chrome for a paragraph.

    When ALL canonical matches in the paragraph resolve to the SAME
    source tag, return that specific tag's chrome.  Mixed-source
    paragraphs (multiple canonical KPIs from different systems on the
    SAME paragraph) fall back to the generic chrome -- this preserves
    visual density (one chrome per match) without splitting attribution
    across N differently-labelled citations on a single line.

    Empty / no-canonical-match input returns ``fallback`` (which is
    the generic chrome by default).
    """
    if not matches:
        return fallback
    tags: set[str] = set()
    for m in matches:
        try:
            label = m.group("label").strip() if hasattr(m, "group") else ""
        except Exception:
            label = ""
        tag = _r82_resolve_source_tag_for_label(label)
        if tag:
            tags.add(tag)
    if len(tags) == 1:
        return f" [Source: {tags.pop()}]"
    return fallback

# Mirror of ``_PARAGRAPH_KPI_NUMERIC_RE`` from
# ``report_iteration_loop.py``.  Round 94 keeps the R61 case-ID
# negative lookahead here too so the injector and gate segment the same
# paragraphs.
_PARAGRAPH_KPI_NUMERIC_RE = re.compile(
    r"\b(?P<label>[A-Za-z][A-Za-z /()\-]{2,80}?)\s*[:\-]\s*(?P<value>-?\$?\d(?!\d{6})[\d,]*(?:\.\d+)?\s*%?)",
)
_NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
_NUMERIC_KPI_VALUE_RE = re.compile(r"^-?\$?\d[\d,]*(?:\.\d+)?\s*%?$")
_SOURCE_TOKEN_RE = re.compile(r"\[\s*source\s*:", re.IGNORECASE)
_METADATA_PARAGRAPH_MARKERS = (
    "generated",
    "analysis id",
    "report metadata",
    "page ",
    "build ",
    "version ",
)


def _is_numeric_kpi_value(text: str) -> bool:
    if not text:
        return False
    return bool(_NUMERIC_KPI_VALUE_RE.match(text.strip()))


def _numeric_tokens_requiring_source(text: str) -> list[str]:
    """Return short numeric tokens that count as 'business fact' claims.

    Mirrors the gate's helper of the same name in
    ``report_iteration_loop.py`` to keep injection decisions perfectly
    aligned with what the gate is going to check.
    """
    clean = (text or "").strip()
    if not clean or _SOURCE_TOKEN_RE.search(clean):
        return []
    lowered = clean.lower()
    if any(marker in lowered for marker in _METADATA_PARAGRAPH_MARKERS):
        return []
    tokens: list[str] = []
    for token in _NUMERIC_TOKEN_RE.findall(clean):
        stripped = token.replace(",", "").replace("%", "")
        if len(stripped) >= 4 and stripped.isdigit():
            # Long IDs and dates are infrastructure metadata, not a
            # KPI claim -- the gate ignores them too.
            continue
        tokens.append(token)
    return tokens


def _is_canonical_kpi_label(label: str) -> bool:
    """Approximate the gate's ``_canonical_kpi_label`` for table-claim detection.

    The gate's full alias map lives in ``report_iteration_loop.py``.  For
    injection we use a coarse "looks like a KPI label" heuristic: short
    text, no unit suffixes, no obvious metadata markers.  Erring on the
    side of "looks like a KPI" is safe because the worst case is we
    inject a citation onto a non-KPI row -- that does not break
    anything; the gate just ignores it.
    """
    clean = (label or "").strip()
    if not clean:
        return False
    if len(clean) > 80:
        return False
    lowered = clean.lower()
    if any(marker in lowered for marker in _METADATA_PARAGRAPH_MARKERS):
        return False
    # Reject pure numerics, dates, and timestamp-shaped strings.
    if _NUMERIC_KPI_VALUE_RE.match(clean):
        return False
    return True


# Round 57 / Phase B (post-pass-2): pin the KPI alias set the gate
# uses for ``_canonical_kpi_label``.  Lazy-import keeps the production
# runtime free of any hard dep on the test harness module; fallback is
# the empty set, which biases the injector to over-cite (safe) rather
# than under-cite (unsafe -- leaves unbacked claims on the floor).
_GATE_KPI_ALIASES: Optional[dict[str, set[str]]] = None


def _gate_kpi_aliases() -> dict[str, set[str]]:
    global _GATE_KPI_ALIASES
    if _GATE_KPI_ALIASES is None:
        try:
            from report_iteration_loop import KPI_ALIASES  # type: ignore
            _GATE_KPI_ALIASES = {k: set(v) for k, v in KPI_ALIASES.items()}
        except Exception:
            logger.debug(
                "inject_source_citations: gate KPI alias map unavailable; "
                "falling back to permissive injection",
                exc_info=False,
            )
            _GATE_KPI_ALIASES = {}
    return _GATE_KPI_ALIASES


def _gate_canonical_kpi_label(label: str) -> Optional[str]:
    """Mirror the gate's ``_canonical_kpi_label`` exactly.

    Used by the paragraph-injection path to filter ``Label: number``
    matches to ONLY those whose label canonicalizes to a known KPI
    alias.  Without this filter the injector would source-back benign
    metadata fragments like ``"Generated: 2026"`` (label "Generated",
    not a KPI) and pollute the document with citations the gate
    wouldn't have flagged.

    When the alias map is not importable (e.g. trimmed-down build),
    this returns ``None`` for everything, which makes the caller fall
    through to the heuristic ``_is_canonical_kpi_label`` -- biased
    toward cite-anything-that-looks-like-a-KPI rather than skip.
    """
    aliases = _gate_kpi_aliases()
    if not aliases:
        return None
    clean = re.sub(r"[^a-z0-9]+", " ", str(label or "").lower()).strip()
    clean = re.sub(r"\s+", " ", clean)
    if not clean:
        return None
    for canonical, alias_set in aliases.items():
        if clean in alias_set:
            return canonical
    return None


def _paragraph_match_is_canonical_kpi(match: Any) -> bool:
    """True when a ``_PARAGRAPH_KPI_NUMERIC_RE`` match represents a KPI.

    Falls back to the conservative ``_is_canonical_kpi_label`` heuristic
    when the gate's alias map isn't available.  When the alias map IS
    available, only matches whose label canonicalizes are treated as
    KPI claims -- this prevents over-citing benign ``Generated: 2026``
    style fragments while still injecting on real ``Analysis Period: 90``
    style claims.
    """
    label = match.group("label").strip() if hasattr(match, "group") else ""
    aliases = _gate_kpi_aliases()
    if aliases:
        return _gate_canonical_kpi_label(label) is not None
    return _is_canonical_kpi_label(label)


# Round 90 / Build 66: paren-balance filter for spurious mid-parenthetical
# matches.  ``_PARAGRAPH_KPI_NUMERIC_RE``'s label charset includes ``)``
# (so balanced labels like ``Medium Risk (band)`` match), but this also
# lets a closing paren in the middle of a parenthetical extract a
# spurious match.  The Build-65 Compact report's Risk Summary line
# ``"Score 4-6 (Watch, 0-10 scale): 9"`` parsed match #4 as
# ``label='scale)' value='9'`` (because ``scale`` starts after the
# comma and ``)`` is in the charset).  The R66/B1 unit-deferral branch
# then landed the citation right BEFORE ``scale``, producing the
# user-facing ``"0-10 [Source: AdoptIQ Report Data Sources] scale"``
# mid-string injection.  Real KPI labels never start inside an unmatched
# ``(``, so reject when ``)`` count exceeds ``(`` count.
def _paragraph_match_is_well_formed(match: Any) -> bool:
    """Reject ``_PARAGRAPH_KPI_NUMERIC_RE`` matches whose label is a
    mid-parenthetical fragment.

    The regex's label charset includes ``)`` (so balanced labels like
    ``Medium Risk (band)`` match), but this also lets a closing paren
    in the middle of a parenthetical (``"(Watch, 0-10 scale): 9"`` ->
    label=``"scale)"``) extract a spurious match.  Real labels never
    start inside an unmatched ``(``, so reject when ``)`` count exceeds
    ``(`` count.

    Round 90 / Build 66 -- fixes Build 65 acceptance bug where the
    Compact report rendered ``"0-10 [Source: ...] scale"`` mid-string
    on the Risk Summary tile.  Pinned by
    ``tests/test_round90_paren_label_filter.py``.
    """
    try:
        label = match.group("label") or ""
    except (IndexError, AttributeError):
        return True
    return label.count(")") <= label.count("(")


def _append_run(paragraph: Any, text: str) -> None:
    """Append a plain run to ``paragraph`` carrying ``text``.

    python-docx exposes ``add_run`` on Paragraph objects; we use it
    rather than direct XML so the injection inherits the document's
    default font and never confuses the python-docx serializer.  The
    new run carries no explicit formatting -- it inherits the
    paragraph style.
    """
    try:
        paragraph.add_run(text)
    except Exception:
        # python-docx will raise on an inconsistent paragraph state;
        # the cell-level injector treats this as best-effort.  Logging
        # at debug avoids polluting normal report flows.
        logger.debug("inject_source_citations: add_run failed", exc_info=False)


# Round 66 / Pass 1 (B1): regex used to detect a "trailing unit" between a
# KPI value and the start of the next KPI label. When the boundary segment
# between match[i].end() and match[i+1] (logical label start) contains an
# alphabetic token (e.g. ``Days``, ``UTC``, ``hours``), citation injection
# must defer past the unit so we never produce ``"90 [Source: ...] Days"``.
# Pure-punctuation boundaries (``. `` / ``; `` / `` | ``) keep the R57
# sentence-style behavior (citation immediately after the value).
_BOUNDARY_HAS_UNIT_RE = re.compile(r"[A-Za-z]")

# Round 76 / R76-A (P1): bullet-glyph detector for boundaries that
# carry a list separator (``• ``, ``* ``, ``- ``).  Build 47-49 leader
# reports emitted single-paragraph multi-KPI bullet lists like
# ``'• Total team activities: 999• Total Action Plans: 366• ...'``
# where the bullet glyph + next label collectively absorb into the
# next match's label group via the greedy ``[A-Za-z /()\-]{2,80}?``
# quantifier; the boundary segment ``'• Total Action Plans: '`` then
# carries an alphabetic token (``Total``) so the R66/B1 unit-deferral
# branch incorrectly fires and the citation lands as
# ``'999• [Source: ...] Total Action Plans'``.  This regex is checked
# BEFORE the unit branch -- when a bullet glyph is present, we route to
# the R57 sentence-style placement (citation immediately after the
# value, before the bullet) so the rendered output reads
# ``'999 [Source: ...] • Total Action Plans: 366 [Source: ...]'`` and
# the bullet remains attached to its label.
_BOUNDARY_HAS_BULLET_RE = re.compile(r"[\u2022\*]|^-|\s-\s")

# Round 76 / R76-A (P1): paren-group detector.  When an entire line is
# a parenthetical KPI cluster like ``'(APs: 16, ABs: 3, CPs: 1, TAC: 4)'``
# with all matches falling inside the parentheses and boundaries being
# pure-punctuation commas, treat the cluster as ONE KPI: suppress
# per-match interleaving and append a single citation immediately
# after the closing ``)``.  This avoids the visual noise of four
# inline citations sitting between the comma-separated values.  The
# scan is conservative: BOTH a leading ``(`` AND a trailing ``)`` must
# be present AND every match must fall between them.
_PAREN_GROUP_OPEN_RE = re.compile(r"\(")
_PAREN_GROUP_CLOSE_RE = re.compile(r"\)")


def _line_is_paren_kpi_cluster(line: str, line_matches: list[Any]) -> bool:
    """True when ``line`` is a single ``(KPI: v, KPI: v, ...)`` cluster.

    Cluster recognition rules:
      * exactly one ``(`` before the first match AND one matching
        ``)`` after the last match (no nested or split clusters);
      * cluster body contains at least ``len(matches) - 1`` commas
        (one per KPI separator);
      * cluster body contains NO bullet glyph (would indicate the
        bullet branch should fire instead);
      * NO between-match boundary contains an alphabetic word of 2+
        chars (would indicate ``then`` / ``and`` / a unit token).

    Note: the regex's value group ``[\\d,]*`` greedily absorbs commas
    into the value (so ``16,`` becomes part of the value when followed
    by another digit-likely token).  We therefore can't rely on the
    boundary-substring carrying the comma -- we check the cluster
    body as a whole instead.
    """
    if len(line_matches) < 2:
        return False
    first_start = line_matches[0].start()
    last_end = line_matches[-1].end()
    open_idx = line.rfind("(", 0, first_start)
    if open_idx < 0:
        return False
    close_idx = line.find(")", last_end)
    if close_idx < 0:
        return False
    if line.count("(", open_idx, close_idx) != 1:
        return False
    cluster_body = line[open_idx + 1: close_idx]
    if cluster_body.count(",") < len(line_matches) - 1:
        return False
    if _BOUNDARY_HAS_BULLET_RE.search(cluster_body):
        return False
    for i in range(len(line_matches) - 1):
        m = line_matches[i]
        next_m = line_matches[i + 1]
        between = line[m.end():next_m.start()]
        if re.search(r"[A-Za-z]{2,}", between):
            return False
    return True


def _embedded_paren_clusters(line: str, line_matches: list[Any]) -> dict[int, int]:
    """Round 76 / Build 51: identify EMBEDDED ``(KPI: v, KPI: v, ...)``
    clusters within a longer paragraph and return a map of
    ``{cluster_end_match_idx: close_paren_position}``.

    Build 50 acceptance found leader DOCX paragraphs of the form::

        "Total Activities: 45 (APs: 16, ABs: 3, CPs: 1, TAC: 25) | "
        "Warning:  BEMS Escalations: 4"

    Match 0 (``Total Activities: 45``) sits OUTSIDE the parens, matches
    1-4 (``APs: 16`` ... ``TAC: 25``) sit INSIDE, match 5
    (``BEMS Escalations: 4``) sits AFTER the closing ``)``.  The
    whole-line paren cluster check (``_line_is_paren_kpi_cluster``)
    correctly rejects this line because the matches don't all fall
    inside the parens.  Without an embedded-cluster classifier the
    multi-match loop then emits four citations INSIDE the parens
    (one per comma-separated KPI), producing
    ``"(APs: 16, [Source: ...]ABs: 3, [Source: ...]CPs: 1, ...)"``.

    This helper scans for ``(...)`` substrings and returns a mapping of
    the LAST match-index in each qualifying cluster to the position
    of the closing ``)`` so the multi-match loop can:
      * skip per-match citation emission for matches inside the cluster
        (cursor advances cleanly across the cluster body);
      * emit ONE citation immediately after the closing ``)`` once the
        cluster's last match has been processed.

    Cluster qualification (matches the whole-line rules):
      * 2+ matches inside the same ``(...)`` group;
      * no nested ``(`` between cluster open and close;
      * no bullet glyph in the cluster body;
      * no alphabetic word (2+ chars) in any between-match boundary.
    """
    out: dict[int, int] = {}
    if len(line_matches) < 2:
        return out
    i = 0
    n = len(line_matches)
    while i < n - 1:
        m = line_matches[i]
        m_start = m.start()
        # Find the nearest unclosed ``(`` BEFORE this match.
        before = line[:m_start]
        last_open = before.rfind("(")
        last_close = before.rfind(")")
        if last_open <= last_close or last_open < 0:
            i += 1
            continue
        # Find the matching ``)`` AFTER the cluster's start.
        close_idx = line.find(")", m.end())
        if close_idx < 0:
            i += 1
            continue
        # No nested ``(`` inside the cluster body.
        if "(" in line[last_open + 1: close_idx]:
            i += 1
            continue
        # Collect every match index whose start lies inside the cluster.
        cluster_end_idx = i
        for j in range(i + 1, n):
            mj = line_matches[j]
            if mj.start() < close_idx:
                cluster_end_idx = j
            else:
                break
        if cluster_end_idx == i:
            # Only one match in the cluster -- not a multi-KPI cluster.
            i += 1
            continue
        # Cluster body must satisfy the same hygiene as the whole-line
        # detector: no bullets, AND every comma-separated segment of
        # the body must match a simple ``"<Label>: <number>"`` shape
        # so we don't collapse a cluster that contains a sentence
        # fragment (e.g. ``"(Total: 5 then Now: 3)"`` where ``then``
        # got absorbed into the next match's label group by the
        # greedy KPI regex).
        cluster_body = line[last_open + 1: close_idx]
        if _BOUNDARY_HAS_BULLET_RE.search(cluster_body):
            i = cluster_end_idx + 1
            continue
        # Each comma-separated piece must look like ``label: number(unit?)``
        # with at most TWO single-spaced words in the label.  A piece
        # that contains a joining word (``then``, ``and``, ``but``,
        # ``or``, ``with``) is disqualified.  Three+ word labels are
        # also disqualified (real KPIs have at most ~2 words).
        segments = [s.strip() for s in cluster_body.split(",")]
        valid_shape = True
        joiner_re = re.compile(
            r"\b(then|and|but|or|with|while|where|when|after|before)\b",
            re.IGNORECASE,
        )
        kpi_segment_re = re.compile(
            r"^[A-Za-z][A-Za-z /\-]{0,40}:\s*-?\$?\d[\d,]*(?:\.\d+)?\s*[A-Za-z%]*$"
        )
        for seg in segments:
            if not seg:
                valid_shape = False
                break
            if joiner_re.search(seg):
                valid_shape = False
                break
            if not kpi_segment_re.match(seg):
                valid_shape = False
                break
        if not valid_shape:
            i = cluster_end_idx + 1
            continue
        out[cluster_end_idx] = close_idx
        i = cluster_end_idx + 1
    return out

# Round 66 / Pass 1 (B1): the label group in ``_PARAGRAPH_KPI_NUMERIC_RE``
# is greedy across whitespace -- ``[A-Za-z /()\-]{2,80}?``. For an inline
# pair like ``"Period: 90 Days  Customers: 39"`` the regex absorbs ``Days``
# into the next match's label group: m2.group("label") =
# ``"Days  Customers"``. The naive between-segment detection (which uses
# next_m.start("label") as the cut point) then sees only one space and
# incorrectly classifies the boundary as punctuation-style. The fix here:
# detect a "wide gap" (2+ whitespace chars) inside the next label group,
# which signals unit-absorption, and treat the position AFTER the gap as
# the LOGICAL label start. Two-word labels with a SINGLE space between
# words (``"Analysis Period"``, ``"Total Customers"``, ``"Adoption
# Barriers"``) are intentionally excluded -- those are real multi-word KPI
# labels, not unit absorption.
_LABEL_WIDE_GAP_RE = re.compile(r"\s{2,}")


def _logical_label_start_pos(line: str, next_m: Any) -> int:
    """Return the position in ``line`` of the LOGICAL next-KPI label start.

    Compensates for the greedy label-group absorption of preceding units.
    When ``next_m.group("label")`` carries a wide whitespace gap (2+
    spaces), the substring after the gap is the actual label and the
    substring before is the previous KPI's unit. Returns
    ``next_m.start("label")`` unchanged when no wide gap is detected.
    """
    try:
        label_text = next_m.group("label") or ""
        label_start = next_m.start("label")
    except Exception:
        try:
            return next_m.start()
        except Exception:
            return 0
    gap = _LABEL_WIDE_GAP_RE.search(label_text)
    if not gap:
        return label_start
    return label_start + gap.end()


def _value_end_no_trailing_ws(line: str, m: Any) -> int:
    """Return the position in ``line`` immediately after the LAST non-whitespace value char.

    The KPI regex's value group ``(?P<value>-?\\$?\\d[\\d,]*(?:\\.\\d+)?\\s*%?)``
    intentionally consumes trailing whitespace before an optional ``%``
    sign so that ``"32 %"`` matches as cleanly as ``"32%"``. That trailing
    whitespace consumption breaks naive ``line[cursor:m.end()]`` slicing
    for citation insertion -- it would produce ``"52  [Source: ...]"``
    (double space) on a pipe-separated multi-KPI line. This helper
    rewinds past any trailing whitespace inside the value group so
    insertion lands flush with the last value digit / unit char.
    """
    try:
        end = m.end("value")
        start = m.start("value")
    except Exception:
        return m.end()
    while end > start and line[end - 1].isspace():
        end -= 1
    return end


def _rewrite_paragraph_with_inline_citations(
    text: str, matches: list[Any], citation_chrome: str
) -> str:
    """Insert ``citation_chrome`` per-line, respecting end-of-line, unit, and inline-KPI semantics.

    Round 64 / Phase 1 (B4): the original (Round 57) implementation
    inserted the citation immediately after each
    ``_PARAGRAPH_KPI_NUMERIC_RE`` match's value end. That worked for
    the R57 use case ("Total Customers: 52. Adoption Barriers: 68."
    on a SINGLE line with multiple distinct KPIs separated by
    punctuation -- every segment between matches must contain
    ``[source:]`` for the gate to mark it source-backed). It broke
    for the Build-36 Comprehensive Title Page, where the regex value
    group does not include trailing units (``Days``, ``UTC``,
    ``%``-less suffixes), so a line like ``"Analysis Period: 90
    Days"`` was rewritten to ``"Analysis Period: 90 [Source: AdoptIQ
    Report Data Sources] Days"`` -- the citation was jammed mid-string
    between the value and its unit on five consecutive title-page
    lines, breaking readability of the report's primary KPI tile.

    Round 64 fix: split on ``\n`` first; single-match lines append at
    end-of-line; multi-match lines interleave per R57.

    Round 66 / Pass 1 (B1) extension: the multi-match path now
    distinguishes between two kinds of boundaries between consecutive
    matches on the SAME line:

      * ``Customers: 52. Barriers: 68`` -- the boundary ``. `` carries
        no alphabetic token, so this is a sentence-style multi-KPI
        line. R57 behavior preserved: citation immediately after the
        value (``"52 [Source: ...]. 68 [Source: ...]"``).
      * ``Analysis Period: 90 Days  Total Customers: 39`` -- the
        boundary ``" Days  "`` carries an alphabetic token (``Days``)
        which is the unit of the preceding value. Defer the citation
        past the unit, just before the next KPI label, so the
        value-unit pair stays adjacent
        (``"Analysis Period: 90 Days [Source: ...] Total Customers:
        39 [Source: ...]"``).

    Fix: split the paragraph into logical lines on ``\n`` first; for
    each line:
      * Skip lines already carrying ``[source:`` (idempotency).
      * If the line has exactly ONE match: append a single citation at
        end-of-line (preserves value->unit pairing -- the R64 fix).
      * If the line has TWO OR MORE matches: per-pair decide whether
        the boundary contains a unit. If yes, defer citation past the
        unit; if no, citation immediately after the value (R57). The
        last match always gets a trailing end-of-line citation.

    Round-tripping the title-tile string ``"Analysis Period: 90
    Days\\nTotal Customers: 39"`` now produces
    ``"Analysis Period: 90 Days [Source: ...]\\nTotal Customers: 39
    [Source: ...]"`` -- one citation per line, none mid-string.
    Round-tripping ``"Total Customers: 52. Adoption Barriers: 68.
    Support Cases: 381."`` still produces
    ``"Total Customers: 52 [Source: ...]. Adoption Barriers: 68
    [Source: ...]. Support Cases: 381 [Source: ...]."`` -- every
    segment source-backed (R57 contract).
    Round-tripping the SINGLE-LINE dashboard tile ``"Analysis Period:
    90 Days  Total Customers: 39"`` (no newline) now produces
    ``"Analysis Period: 90 Days [Source: ...]  Total Customers: 39
    [Source: ...]"`` -- citation deferred past the ``Days`` unit
    (R66/B1 fix).
    """
    if not matches:
        return f"{text} {citation_chrome}"
    lines = text.split("\n")
    out_lines: list[str] = []
    for line in lines:
        # Round 90 / Build 66: the rewriter re-extracts matches per-line,
        # so the well-formed filter (paren-balance) must apply HERE too,
        # not just on the caller's ``all_matches``.  Without this filter
        # a spurious ``label='scale)' value='9'`` match on a single line
        # leaks past the caller-side filter once the line is split, and
        # the R66/B1 unit-deferral branch produces the buggy
        # ``"0-10 [Source: ...] scale"`` mid-string injection.
        line_matches = [
            m
            for m in _PARAGRAPH_KPI_NUMERIC_RE.finditer(line)
            if _paragraph_match_is_well_formed(m)
        ]
        if not line_matches:
            out_lines.append(line)
            continue
        if _SOURCE_TOKEN_RE.search(line):
            out_lines.append(line)
            continue
        if len(line_matches) == 1:
            out_lines.append(f"{line.rstrip()} {citation_chrome}")
            continue
        # Round 76 / R76-A: paren-cluster recognition.  When the entire
        # line is a single ``(KPI: v, KPI: v, ...)`` group, treat as
        # ONE source-backed chunk: append ONE citation immediately
        # after the closing ``)`` so we don't render four inline
        # citations inside the parentheses (e.g. ``(APs: 16
        # [Source: ...] , ABs: 3 [Source: ...] , ...)``).
        if _line_is_paren_kpi_cluster(line, line_matches):
            stripped = line.rstrip()
            close_idx = stripped.rfind(")")
            if close_idx >= 0:
                trailing = stripped[close_idx + 1:]
                out_lines.append(
                    f"{stripped[:close_idx + 1]} {citation_chrome}{trailing}"
                )
                continue
        # Round 76 / Build 51: identify EMBEDDED paren clusters within
        # a longer paragraph (``"Total Activities: 45 (APs: 16, ABs: 3
        # ...) | BEMS: 4"``) so the multi-match loop can collapse the
        # in-paren KPIs into ONE post-paren citation instead of
        # interleaving citations after every comma inside the parens.
        embedded_map = _embedded_paren_clusters(line, line_matches)
        # Build the set of match indices that are INSIDE any embedded
        # cluster so the loop knows which matches to skip.  The map's
        # value is the close-paren position, mapped from the LAST
        # match index in the cluster.
        in_cluster: set[int] = set()
        for end_idx in embedded_map:
            # walk backwards from end_idx until we leave the paren body
            close_pos = embedded_map[end_idx]
            # find the matching open paren for this close
            open_pos = line.rfind("(", 0, close_pos)
            for k in range(end_idx, -1, -1):
                if line_matches[k].start() < open_pos:
                    break
                in_cluster.add(k)
        out_parts: list[str] = []
        cursor = 0
        for i, m in enumerate(line_matches):
            is_last = i == len(line_matches) - 1
            # Round 76 / Build 51: handle EMBEDDED paren-cluster matches.
            # Mid-cluster matches: do nothing (cursor stays put;
            # cluster body will be flushed at the cluster-end branch
            # below or by the next non-cluster match).  Cluster-end
            # match: flush from cursor through the close paren and
            # append ONE citation, then move on to the next match.
            if i in in_cluster:
                if i in embedded_map:
                    close_idx = embedded_map[i]
                    out_parts.append(line[cursor: close_idx + 1])
                    out_parts.append(f" {citation_chrome}")
                    cursor = close_idx + 1
                    if is_last:
                        # Pick up any trailing chars after the close paren
                        trailing = line[cursor:].rstrip()
                        if trailing:
                            out_parts.append(trailing)
                        continue
                    continue
                # mid-cluster: skip without writing
                continue
            if is_last:
                out_parts.append(line[cursor:].rstrip())
                out_parts.append(f" {citation_chrome}")
                continue
            next_m = line_matches[i + 1]
            value_end = _value_end_no_trailing_ws(line, m)
            next_label_start = _logical_label_start_pos(line, next_m)
            # Round 76 / Build 51: when the next match is inside an
            # embedded paren cluster, the "between" segment ends at
            # the cluster's open paren -- not at the next match's
            # label start.  This keeps the value-and-paren handoff
            # clean (``"Total Activities: 45 [Source: ...] (APs:
            # ...)"``) instead of bleeding the citation into the
            # paren cluster.
            if (i + 1) in in_cluster:
                # Round 112 / Build 81: when the wrapper KPI's value sits
                # IMMEDIATELY before the embedded paren cluster's open
                # paren (whitespace only between value-end and ``(``),
                # suppress this match's citation -- the cluster's
                # post-``)`` citation already covers BOTH the wrapper
                # KPI and the cluster body as a single semantic unit
                # (``"Total Activities: 42 (APs: 18, ABs: 3, CPs: 1,
                # TAC: 20)"`` is one assertion, not two).  Pre-R112 the
                # wrapper got its own citation and the line rendered
                # ``"Total Activities: 42 [Source: ...](APs: 18, ...,
                # TAC: 20) [Source: ...]"`` -- two adjacent citations
                # interrupting the semantic unit.  Post-R112: ONE
                # citation immediately after the closing paren.  When
                # the boundary contains non-whitespace (e.g. the
                # wrapper sits before some other text + then the
                # cluster), preserve the pre-R112 behavior.
                next_match_start = line_matches[i + 1].start()
                cluster_open_pos = line.rfind("(", value_end, next_match_start)
                if cluster_open_pos > 0:
                    boundary_pre_paren = line[value_end:cluster_open_pos]
                    if boundary_pre_paren.strip() == "":
                        # suppress wrapper citation; advance cursor to
                        # ``value_end`` (NOT ``m.end()`` -- the regex's
                        # match span includes trailing whitespace via
                        # ``\s*%?`` and consuming it would eat the
                        # space between ``"42"`` and ``"("``).  The
                        # cluster-end branch later in the loop picks
                        # up from this cursor and flushes
                        # ``" (cluster_body) [Source: ...]"`` in one go,
                        # so the rendered output is
                        # ``"Total Activities: 42 (APs: 18, ABs: 3, CPs: 1,
                        # TAC: 20) [Source: ...]"`` -- one citation, value
                        # and paren still adjacent with their original
                        # whitespace.
                        out_parts.append(line[cursor:value_end])
                        cursor = value_end
                        continue
                # value-end placement preserves the natural paren attach
                out_parts.append(line[cursor:value_end])
                out_parts.append(f" {citation_chrome}")
                cursor = m.end()
                continue
            between = line[value_end:next_label_start]
            # Round 76 / R76-A: bullet-glyph boundary takes precedence
            # over the R66/B1 unit-deferral branch.  When the boundary
            # carries ``• ``, ``* ``, or `` - `` (list separators), the
            # alphabetic tokens after it are NOT a unit -- they are the
            # next KPI's label.  Routing through R57's value-end
            # placement keeps the bullet attached to its label and
            # produces ``999 [Source: ...] • Total Action Plans: 366
            # [Source: ...]`` instead of the buggy mid-string
            # ``999• [Source: ...] Total Action Plans``.
            if _BOUNDARY_HAS_BULLET_RE.search(between):
                out_parts.append(line[cursor:value_end])
                out_parts.append(f" {citation_chrome}")
                cursor = m.end()
            elif _BOUNDARY_HAS_UNIT_RE.search(between):
                out_parts.append(line[cursor:next_label_start].rstrip())
                out_parts.append(f" {citation_chrome} ")
                cursor = next_label_start
            else:
                out_parts.append(line[cursor:value_end])
                out_parts.append(f" {citation_chrome}")
                cursor = m.end()
        out_lines.append("".join(out_parts))
    return "\n".join(out_lines)


def _replace_paragraph_text(paragraph: Any, new_text: str) -> None:
    """Replace ``paragraph``'s text wholesale.

    Clears existing runs and writes a single new run with ``new_text``.
    Inline run formatting is lost for the rewritten paragraph; this is
    only invoked from the multi-match path where we need to interleave
    citations between matches and preserving formatting would require
    XML-level run splitting.
    """
    try:
        for run in list(paragraph.runs):
            run.text = ""
        # python-docx clears the run text; the runs themselves remain in
        # place. Append a new run carrying the rewritten text -- this is
        # safer than removing the old run elements (which can corrupt
        # the underlying XML when the runs participate in numbering or
        # field codes).
        paragraph.add_run(new_text)
    except Exception:
        logger.debug("inject_source_citations: rewrite failed", exc_info=False)


def _inject_into_cell(cell: Any, citation: str) -> bool:
    """Append ``citation`` to the LAST paragraph of ``cell`` if missing.

    Returns True when a citation was appended, False when the cell was
    already source-backed or has no paragraphs to write into.
    """
    if not cell.paragraphs:
        return False
    full = " ".join((p.text or "") for p in cell.paragraphs)
    if _SOURCE_TOKEN_RE.search(full):
        return False
    target = cell.paragraphs[-1]
    _append_run(target, citation)
    return True


def _row_is_metric_claim(row_cells: list[str]) -> bool:
    """Approximate the gate's table-row claim detector for ``label | value`` rows."""
    if len(row_cells) < 2:
        return False
    label = (row_cells[0] or "").strip()
    value = (row_cells[1] or "").strip()
    return _is_canonical_kpi_label(label) and _is_numeric_kpi_value(value)


def _multicolumn_header_columns(header_cells: list[str]) -> list[int]:
    """Return column indices whose header text looks like a KPI label."""
    return [
        idx
        for idx, label in enumerate(header_cells)
        if _is_canonical_kpi_label((label or "").strip())
    ]


def _scenario_citation(scenario_key: Optional[str]) -> str:
    """Resolve the canonical paragraph-level citation for ``scenario_key``.

    All four scenarios share the same generic chrome today; the
    parameter is plumbed so future per-scenario refinements (e.g.
    ``[Source: Snowflake CASE_FACT]`` for case-heavy paragraphs in the
    leader path) can land without changing every call site.
    """
    return _PARAGRAPH_FALLBACK_CITATION


def inject_source_citations_into_docx(
    docx_path: Path,
    *,
    scenario_key: Optional[str] = None,
) -> dict[str, int]:
    """Open ``docx_path``, inject missing ``[Source: ...]`` markers, save back.

    Returns a structured count dict::

        {
            "paragraphs_injected": int,
            "table_cells_injected": int,
            "skipped_already_cited": int,
            "skipped_no_numeric": int,
            "errors": int,
        }

    The function NEVER raises into the caller -- failures are logged
    and the file is left as-is.  Idempotent: running twice on the same
    document only injects on the FIRST pass; subsequent passes return
    zero new injections.
    """

    counts = {
        "paragraphs_injected": 0,
        "table_cells_injected": 0,
        "skipped_already_cited": 0,
        "skipped_no_numeric": 0,
        "errors": 0,
    }

    try:
        from docx import Document  # type: ignore[import-not-found]
    except Exception:
        logger.warning("inject_source_citations: python-docx unavailable; nothing to do")
        counts["errors"] = 1
        return counts

    path = Path(docx_path).expanduser()
    if not path.exists():
        logger.warning("inject_source_citations: docx not found at %s", path)
        counts["errors"] = 1
        return counts

    try:
        doc = Document(str(path))
    except Exception:
        logger.warning("inject_source_citations: failed to open %s", path, exc_info=False)
        counts["errors"] = 1
        return counts

    citation = _scenario_citation(scenario_key)

    for paragraph in doc.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        # Round 73 / Phase 2 (F5): never inject ``[Source: ...]`` chrome
        # into Heading / Title style paragraphs.  Build 46 acceptance
        # surfaced two real Comprehensive headings whose text matched
        # the canonical KPI regex -- e.g. ``"Top 10 Customer Risk
        # Profiles"`` -- and the injector appended a trailing
        # ``[Source: AdoptIQ Report Data Sources]`` after the heading,
        # producing a deeply ugly section title in the rendered Word
        # report.  Citations belong in body paragraphs only; the heading
        # establishes the section context, the body carries the
        # numeric claims that need source-backing.  Defensive against
        # python-docx versions that don't expose ``.style.name`` (older
        # API surface) -- failure to introspect is treated as a body
        # paragraph (preserves the pre-R73 behaviour for unknown
        # paragraph types).
        try:
            style_name = (paragraph.style.name if paragraph.style else "") or ""
        except Exception:  # noqa: BLE001
            style_name = ""
        if style_name.startswith("Heading") or style_name == "Title":
            counts["skipped_no_numeric"] += 1
            continue
        if _SOURCE_TOKEN_RE.search(text):
            counts["skipped_already_cited"] += 1
            continue
        # Round 57: the gate has TWO paragraph-level checks against the
        # same paragraph text, with different metadata-skip semantics:
        #   1. ``_extract_docx_metric_claims`` paragraph case: matches
        #      every ``Label: number`` regardless of the ``Generated:``
        #      / ``Build`` / ``Page`` markers in the same paragraph.
        #      A renewal report header line like
        #      ``"Generated: ... | Analysis Period: 90 days | ..."``
        #      surfaces ``Analysis Period: 90`` as a metric_claim that
        #      the injector MUST source-back, even though the
        #      paragraph also looks like metadata.
        #   2. ``_numeric_tokens_requiring_source`` (the narrative-
        #      token check): silently returns ``[]`` for metadata
        #      paragraphs so they need no citation for that gate.
        # The injector must therefore inject after every metric match
        # (regardless of metadata) AND additionally append a trailing
        # citation only when the narrative gate would actually fire.
        # Round 90 / Build 66: filter spurious mid-parenthetical matches
        # (label has more ``)`` than ``(``) before the rewriter sees
        # them.  Without this filter, a line like
        # ``"Score 4-6 (Watch, 0-10 scale): 9"`` would parse a fourth
        # match with ``label='scale)'`` and the R66/B1 unit-deferral
        # branch in ``_rewrite_paragraph_with_inline_citations`` would
        # land the citation right BEFORE ``scale``, producing the
        # user-facing ``"0-10 [Source: ...] scale"`` mid-string injection
        # observed on the Build 65 Compact Risk Summary tile.
        all_matches = [
            m
            for m in _PARAGRAPH_KPI_NUMERIC_RE.finditer(paragraph.text)
            if _paragraph_match_is_well_formed(m)
        ]
        # Round 57 (post-pass-2): the gate's
        # ``_paragraph_claim_source_backed`` slices the segment from a
        # canonical match to the NEXT regex match -- using the FULL
        # match list, not just canonical matches.  So even if only one
        # match in the paragraph is a canonical KPI (e.g. only
        # ``"Total Action Plans: 889"`` is canonical out of an
        # 8-bullet status breakdown), the next non-canonical match
        # (e.g. ``"Completed - Successful: 583"``) acts as the
        # boundary.  A trailing citation falls AFTER the boundary and
        # leaves the canonical claim unbacked.
        #
        # Decision: only inject into paragraphs that contain at least
        # one canonical KPI claim (this avoids over-citing benign
        # ``"Generated: 2026"`` metadata) but interleave citations
        # between ALL regex matches when injecting (so every canonical
        # match's segment contains ``[source:`` regardless of where
        # the next-match boundary falls).
        canonical_matches = [m for m in all_matches if _paragraph_match_is_canonical_kpi(m)]
        narrative_tokens_need_citation = bool(_numeric_tokens_requiring_source(text))
        if not canonical_matches and not narrative_tokens_need_citation:
            counts["skipped_no_numeric"] += 1
            continue
        if len(all_matches) > 1 and canonical_matches:
            # Multi-match paragraph WITH at least one canonical KPI
            # claim: rewrite inline so every regex-match boundary has
            # a citation immediately on the canonical-claim side.
            # Loses per-run formatting on the rewritten paragraph (the
            # writers producing such paragraphs use plain
            # ``add_paragraph(text)`` so this is acceptable in
            # practice).
            #
            # Round 82 / Phase B2: when ALL canonical matches in the
            # paragraph resolve to the SAME source system (via
            # ``_r82_chrome_for_paragraph``), use that system's
            # specific chrome (e.g. ``[Source: Snowflake CSConsole]``).
            # Mixed-source paragraphs fall back to the generic chrome
            # so we don't render multiple different per-match
            # citations on a single line and re-introduce visual
            # mess (the user-stated R82 acceptance criterion).
            paragraph_chrome = _r82_chrome_for_paragraph(canonical_matches, citation)
            new_text = _rewrite_paragraph_with_inline_citations(
                paragraph.text, all_matches, paragraph_chrome.strip()
            )
            _replace_paragraph_text(paragraph, new_text)
        elif canonical_matches:
            # Single canonical match (and at most one regex match
            # total): append once. The segment from match.end() to
            # end-of-text contains the trailing citation, so the gate
            # marks it source_backed.
            #
            # Round 82 / Phase B3: route the single canonical
            # match's label through ``_r82_chrome_for_label`` so the
            # appended chrome carries the specific per-source
            # attribution when the label maps into the R82 KPI
            # taxonomy.  Falls back to the generic chrome on misses.
            try:
                single_label = canonical_matches[0].group("label").strip()
            except Exception:  # noqa: BLE001 - label extraction never fatal
                single_label = ""
            single_chrome = _r82_chrome_for_label(single_label, citation)
            _append_run(paragraph, single_chrome)
        else:
            # No canonical KPI match -- pure narrative paragraph with
            # short numeric tokens. Trailing citation satisfies the
            # narrative-token gate without rewriting the paragraph
            # (preserves formatting).
            _append_run(paragraph, citation)
        counts["paragraphs_injected"] += 1

    for table in doc.tables:
        rows = list(table.rows)
        if not rows:
            continue

        rows_cells_text = [[cell.text.strip() for cell in row.cells] for row in rows]

        # Multi-column header table: header_row + value_rows.
        if len(rows_cells_text) >= 2 and len(rows_cells_text[0]) >= 3:
            header_cells = rows_cells_text[0]
            kpi_columns = _multicolumn_header_columns(header_cells)
            if kpi_columns:
                for value_row_idx in range(1, len(rows)):
                    value_row_cells = rows[value_row_idx].cells
                    value_row_text = rows_cells_text[value_row_idx]
                    for col_idx in kpi_columns:
                        if col_idx >= len(value_row_text):
                            continue
                        cell_text = value_row_text[col_idx]
                        if not _is_numeric_kpi_value(cell_text):
                            continue
                        # Adjacent-cell already-cited check matches the gate.
                        adj_indices = {col_idx}
                        if col_idx > 0:
                            adj_indices.add(col_idx - 1)
                        if col_idx + 1 < len(value_row_text):
                            adj_indices.add(col_idx + 1)
                        adj_text = " ".join(value_row_text[i] for i in adj_indices)
                        if _SOURCE_TOKEN_RE.search(adj_text):
                            counts["skipped_already_cited"] += 1
                            continue
                        # Round 82 / Phase B3: route per-column header
                        # through the R82 taxonomy so each numeric
                        # cell carries the system-specific chrome
                        # (e.g. ``[Source: Snowflake CSConsole]`` for
                        # an ``Adoption Barriers`` column).  Misses
                        # fall back to the generic chrome silently.
                        col_chrome = _r82_chrome_for_label(
                            header_cells[col_idx] if col_idx < len(header_cells) else "",
                            citation,
                        )
                        if _inject_into_cell(value_row_cells[col_idx], col_chrome):
                            counts["table_cells_injected"] += 1

        # Two-column ``label | value`` rows.
        for row_idx, row_text in enumerate(rows_cells_text):
            if not _row_is_metric_claim(row_text):
                continue
            adj_indices = {1}
            if len(row_text) > 2:
                adj_indices.add(2)
            adj_text = " ".join(row_text[i] for i in adj_indices)
            if _SOURCE_TOKEN_RE.search(adj_text):
                counts["skipped_already_cited"] += 1
                continue
            value_cell = rows[row_idx].cells[1]
            # Round 82 / Phase B3: row label drives per-source chrome
            # for two-column tables.  ``Customer Pulse | 87`` resolves
            # to ``[Source: Snowflake CSConsole]``; ``Total Customers
            # | 39`` resolves to ``[Source: Snowflake EDW Sales
            # subscriptions]``.  Misses fall back to generic.
            row_label = row_text[0] if row_text else ""
            row_chrome = _r82_chrome_for_label(row_label, citation)
            if _inject_into_cell(value_cell, row_chrome):
                counts["table_cells_injected"] += 1

    if counts["paragraphs_injected"] == 0 and counts["table_cells_injected"] == 0:
        # Nothing changed; skip the save to avoid touching mtime + sha256
        # on idempotent re-invocations.
        return counts

    try:
        doc.save(str(path))
    except Exception:
        logger.warning(
            "inject_source_citations: failed to save %s after %d paragraph + %d cell injections",
            path,
            counts["paragraphs_injected"],
            counts["table_cells_injected"],
            exc_info=False,
        )
        counts["errors"] = 1

    return counts
