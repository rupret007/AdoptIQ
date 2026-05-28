"""Round 79 / Build 55 / Phase 5 (B5): BE Priority Focus Areas Word section.

Renders a self-contained Word section that names the BE engineering
team's top 5-10 focus areas for the analysed scope:

* Heading: "BE Engineering Priority Focus Areas"
* Optional intro paragraph (canonical totals + LLM diagnostic).
* Per-technology sub-section: heading + banded top-N table with
  Theme / Cluster Score / Open ABs / Customers / Sample issue.

The same helper runs in both the Comprehensive flow (after the
per-customer narrative loop, before the closing paragraphs) and the
Leader flow (after the team summary, before per-CSSM detail) so the
operator-facing prose stays consistent.

This module deliberately ships zero LLM calls: the LLM classifier ran
during the XLSX integration step (R79/B2) and the resulting tags / scores
are read off the focus-areas DataFrame.  Any failure short-circuits to
a single fallback paragraph so the docx never ships an empty section.

Tests: ``tests/test_round79_b5_be_word_section.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from report_word_styling import add_banded_top_n_table

logger = logging.getLogger(__name__)


_HEADING = "BE Engineering Priority Focus Areas"
_FALLBACK_HEADING_ONLY_TEXT = (
    "BE engineering priority focus areas were not computed for this "
    "scope (no scored adoption barriers met the cluster-score "
    "threshold). Re-run the analysis with a wider lookback or lower "
    "the BE_PRIORITY_MIN_CLUSTER_SCORE threshold to surface clusters."
)
_TABLE_HEADERS = (
    "Theme",
    "Cluster Score",
    "Open ABs",
    "Customers",
    "Sample Issue",
)


def _truncate(value: Any, max_len: int) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)] + "..."


def _round_score(value: Any) -> str:
    """Render the cluster-focus score as a one-decimal string."""

    if value is None:
        return "0.0"
    try:
        if isinstance(value, float) and pd.isna(value):
            return "0.0"
    except Exception:
        pass
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _format_int(value: Any) -> str:
    if value is None:
        return "0"
    try:
        if isinstance(value, float) and pd.isna(value):
            return "0"
    except Exception:
        pass
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return "0"


def _is_provenance_only(focus_areas: Optional[pd.DataFrame]) -> bool:
    """Detect the provenance-only fallback shape."""

    if focus_areas is None or getattr(focus_areas, "empty", True):
        return True
    if "_adoptiq_provenance_row" in focus_areas.columns:
        try:
            return bool(focus_areas["_adoptiq_provenance_row"].iloc[0])
        except Exception:  # noqa: BLE001
            return True
    return False


def _technologies(focus_areas: pd.DataFrame) -> Iterable[str]:
    """Yield the unique technologies present in the focus-areas frame.

    Sort order honours the R79 / B3 SSoT determinism rule (Technology ASC).
    """

    if focus_areas is None or focus_areas.empty:
        return []
    if "Technology" not in focus_areas.columns:
        return ["(unknown)"]
    techs = (
        focus_areas["Technology"]
        .fillna("(unknown)")
        .astype(str)
        .map(lambda s: s.strip() or "(unknown)")
        .unique()
        .tolist()
    )
    return sorted(techs, key=lambda s: s.lower())


def _intro_text(
    barriers_df: Optional[pd.DataFrame],
    focus_areas_df: pd.DataFrame,
    diag: Optional[Dict[str, Any]] = None,
) -> str:
    """Render the section's intro paragraph."""

    rows_scored = 0
    top_n = 0
    classified_count = 0
    llm_disabled = False
    llm_error = ""
    if isinstance(diag, dict):
        try:
            rows_scored = int(diag.get("rows_scored") or 0)
        except (TypeError, ValueError):
            rows_scored = 0
        try:
            top_n = int(diag.get("top_n_selected") or 0)
        except (TypeError, ValueError):
            top_n = 0
        # Round 112 / Build 81: pull the LLM classifier outcome from the
        # diag dict (R79 wires ``be_priority_diag.llm_diag`` with
        # ``classified_count`` / ``llm_disabled`` / ``llm_error``) so
        # the intro narrative reflects WHAT ACTUALLY HAPPENED, not what
        # we asked the LLM to do.  Pre-R112 the narrative claimed "the
        # top {top_n} barriers were also tagged by an LLM classifier"
        # whenever ``top_n_selected`` was non-zero -- even when the LLM
        # 429'd (Build 80 acceptance) and produced zero tags.  The
        # Build 80 Comprehensive docx (P210) shipped exactly this
        # dishonest wording: "11 barriers were also tagged by an LLM
        # classifier (TRUE_BLOCKER / TRAINING_GAP / FEATURE_REQUEST)"
        # while ``classified_count == 0`` and ``llm_error`` carried
        # the rate-limit body.  Post-R112: emit one of three honest
        # variants depending on the actual outcome.
        llm_diag = diag.get("llm_diag")
        if isinstance(llm_diag, dict):
            try:
                classified_count = int(llm_diag.get("classified_count") or 0)
            except (TypeError, ValueError):
                classified_count = 0
            llm_disabled = bool(llm_diag.get("llm_disabled"))
            llm_error = str(llm_diag.get("llm_error") or "").strip()

    n_techs = len(list(_technologies(focus_areas_df)))
    n_clusters = int(len(focus_areas_df))

    parts = []
    parts.append(
        f"AdoptIQ scored {rows_scored} adoption barriers on an "
        "independent BE-engineering priority formula and rolled the "
        f"results up into {n_clusters} focus-area clusters across "
        f"{n_techs} technologies."
    )
    if top_n:
        # Round 112 / Build 81: three-state honest copy -- LLM tagged
        # the barriers (success), LLM was disabled by config / kill-
        # switch, OR LLM was attempted but returned no tags (rate
        # limit, content filter, transient error).  In the third case
        # the deterministic ranking is still valid -- tags are merely
        # absent for this run.
        if classified_count > 0:
            parts.append(
                f" The top {classified_count} of {top_n} barriers were also tagged "
                "by an LLM classifier (TRUE_BLOCKER / TRAINING_GAP / "
                "FEATURE_REQUEST) to help triage; tags inform the "
                "narrative below but do NOT change the score."
            )
        elif llm_disabled:
            parts.append(
                f" LLM classification was disabled for this run; the {top_n} "
                "barriers shown were ranked by the deterministic BE-engineering "
                "priority formula without LLM tagging."
            )
        else:
            # LLM was attempted but produced zero tags (e.g. CircuIT 429
            # rate-limit on Build 80).  Name the failure mode honestly
            # so the operator can root-cause; the deterministic ranking
            # is unaffected.
            parts.append(
                f" LLM classification was attempted on the top {top_n} barriers "
                "but did not return any tags for this run (likely upstream LLM "
                "unavailability such as a 429 rate-limit). The deterministic "
                "ranking remains valid; tags will reappear once the LLM is "
                "reachable."
            )
    parts.append(
        " Within each technology section, clusters are sorted by "
        "Cluster_Focus_Score (composite of summed priority, true-blocker "
        "count, customers affected, and open count); only clusters above "
        "the configured threshold appear."
    )
    return "".join(parts)


def add_be_priority_focus_areas_section(
    doc: Any,
    *,
    barriers_df: Optional[pd.DataFrame] = None,
    focus_areas_df: Optional[pd.DataFrame] = None,
    diag: Optional[Dict[str, Any]] = None,
    heading_level: int = 1,
) -> bool:
    """Append the BE Priority Focus Areas section to ``doc``.

    Returns ``True`` if at least the heading was rendered, ``False`` if
    ``doc`` did not support the docx surface area or the rendering raised
    an unrecoverable error.  A provenance-only ``focus_areas_df`` still
    renders the heading + a single fallback paragraph so the operator
    sees honest provenance instead of a missing section.
    """

    if doc is None:
        return False

    try:
        doc.add_heading(_HEADING, level=int(heading_level))
    except Exception as err:  # noqa: BLE001
        logger.debug(
            "Round 79 / B5: doc.add_heading failed (%s); aborting section.",
            err,
        )
        return False

    if _is_provenance_only(focus_areas_df):
        try:
            doc.add_paragraph(_FALLBACK_HEADING_ONLY_TEXT)
        except Exception as err:  # noqa: BLE001
            logger.debug(
                "Round 79 / B5: provenance fallback paragraph failed (%s).",
                err,
            )
        return True

    # We have a real focus-areas frame.  Render an intro paragraph then
    # one sub-section per technology.
    try:
        doc.add_paragraph(_intro_text(barriers_df, focus_areas_df, diag))
    except Exception as err:  # noqa: BLE001
        logger.debug("Round 79 / B5: intro paragraph failed (%s).", err)

    sub_heading_level = min(int(heading_level) + 1, 6)

    for tech in _technologies(focus_areas_df):
        try:
            tech_rows = focus_areas_df[focus_areas_df.get(
                "Technology",
                pd.Series([None] * len(focus_areas_df)),
            ).fillna("(unknown)").astype(str).str.strip().str.lower() == tech.strip().lower()]
        except Exception as err:  # noqa: BLE001
            logger.debug(
                "Round 79 / B5: technology slice failed (%s); skipping %s.",
                err,
                tech,
            )
            continue
        if tech_rows is None or tech_rows.empty:
            continue

        try:
            doc.add_heading(str(tech).strip() or "(unknown)", level=sub_heading_level)
        except Exception as err:  # noqa: BLE001
            logger.debug(
                "Round 79 / B5: sub-heading failed (%s); using paragraph.",
                err,
            )
            try:
                doc.add_paragraph(str(tech))
            except Exception:
                continue

        rows_payload: list[list[str]] = []
        for _, row in tech_rows.iterrows():
            theme = _truncate(row.get("Theme"), max_len=80)
            cluster_score = _round_score(row.get("Cluster_Focus_Score"))
            open_count = _format_int(row.get("Open_Count"))
            customers_count = _format_int(row.get("Customers_Affected"))
            sample = _truncate(row.get("Sample_Issues"), max_len=240)
            rows_payload.append(
                [theme, cluster_score, open_count, customers_count, sample]
            )

        try:
            add_banded_top_n_table(doc, _TABLE_HEADERS, rows_payload)
        except Exception as err:  # noqa: BLE001
            logger.debug(
                "Round 79 / B5: top-N table render failed (%s); falling "
                "back to bullet list.",
                err,
            )
            try:
                for row_values in rows_payload:
                    doc.add_paragraph(
                        " | ".join(str(v) for v in row_values),
                        style="List Bullet",
                    )
            except Exception:
                pass

    return True


__all__ = [
    "add_be_priority_focus_areas_section",
]
