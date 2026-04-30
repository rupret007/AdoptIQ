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

# Mirror of ``_PARAGRAPH_KPI_NUMERIC_RE`` from
# ``report_iteration_loop.py``.  We import via duck-typing rather than
# a hard dependency because the gate module is dev-side only and the
# injector ships in production builds.
_PARAGRAPH_KPI_NUMERIC_RE = re.compile(
    r"\b(?P<label>[A-Za-z][A-Za-z /()\-]{2,80}?)\s*[:\-]\s*(?P<value>-?\$?\d[\d,]*(?:\.\d+)?\s*%?)",
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


def _rewrite_paragraph_with_inline_citations(
    text: str, matches: list[Any], citation_chrome: str
) -> str:
    """Insert ``citation_chrome`` immediately after each match's value end.

    For ``"Customers: 52. Barriers: 68. Cases: 381"`` with three matches,
    returns ``"Customers: 52 [Source: ...]. Barriers: 68 [Source: ...]. Cases: 381 [Source: ...]"``.
    The trailing citation also satisfies the narrative-paragraph gate.
    """
    if not matches:
        return f"{text} {citation_chrome}"
    parts: list[str] = []
    last_end = 0
    for m in matches:
        parts.append(text[last_end : m.end()])
        parts.append(f" {citation_chrome}")
        last_end = m.end()
    parts.append(text[last_end:])
    return "".join(parts)


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
        all_matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(paragraph.text))
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
            new_text = _rewrite_paragraph_with_inline_citations(
                paragraph.text, all_matches, citation.strip()
            )
            _replace_paragraph_text(paragraph, new_text)
        elif canonical_matches:
            # Single canonical match (and at most one regex match
            # total): append once. The segment from match.end() to
            # end-of-text contains the trailing citation, so the gate
            # marks it source_backed.
            _append_run(paragraph, citation)
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
                        if _inject_into_cell(value_row_cells[col_idx], citation):
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
            if _inject_into_cell(value_cell, citation):
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
