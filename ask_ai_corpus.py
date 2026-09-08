"""Round 17 / Phase D.1 -- Corpus retrieval extension for the
grounded Ask AI pipeline.

Pure helpers used by ``ask_ai_grounded.run_portfolio_grounded_ask_ai``
to pull historical corpus context (case history, recurring barrier
themes, resolutions) into the LLM prompt without coupling the much
larger Ask AI module to the corpus internals.

The corpus is *additive* context: when the corpus is unavailable
(feature flag off, SharePoint sign-in not completed / OneDrive not
synced, indexing in progress) every helper returns an empty string and
a banner string, and the caller proceeds with today's prompt.

All chunk text is run through
``ai_narrative_validator.is_corpus_chunk_safe`` before it ever
reaches the LLM context, so a hostile sentinel /
poisoned report cannot inject prompt instructions.  Every emitted
chunk carries a synthetic ``CORPUS:<n>`` SourceID so the existing
citation whitelist machinery picks it up unchanged.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


#: Hard cap on chunks per Ask AI call.  Extra chunks beyond this
#: bound are dropped silently to keep the prompt budget predictable.
_MAX_CHUNKS: int = 8

#: Hard cap on per-chunk text length emitted into the prompt.
_MAX_CHUNK_CHARS: int = 600

#: Hard cap on the customer-name guess extracted from the question.
_MAX_NAME_LEN: int = 80

#: Regex that recognises an obvious "customer named X" pattern.
_CUSTOMER_HINT_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:customer|account|client|tenant)\s+(?:named\s+)?"
    r"(?P<name>(?:[A-Z][\w&'\-]+\s*){1,4})",
    flags=re.UNICODE,
)


@dataclass(frozen=True)
class CorpusContext:
    """Bundle returned to the caller.  ``block`` is a ready-to-paste
    prompt fragment; ``allowed_ids`` are the synthetic SourceIDs to
    add to the citation whitelist; ``banner`` carries a UI-facing
    explanation when the corpus is unavailable; ``stats`` is a
    diagnostics payload for logging."""

    block: str
    allowed_ids: tuple[str, ...]
    banner: str
    stats: dict[str, object]


_EMPTY_STATS: dict[str, object] = {
    "available": False,
    "chunks": 0,
    "customer_name": None,
    "themes": 0,
    "history_cases": 0,
}


def _safe_text(value: object, *, limit: int = _MAX_CHUNK_CHARS) -> str:
    """Normalize and truncate untrusted corpus text before it reaches
    the LLM.  Strips control characters and pin tokens that could
    close our outer ``</corpus>`` fence."""
    if value is None:
        return ""
    body = unicodedata.normalize("NFKC", str(value))
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", body)
    body = body.replace("</corpus>", "")
    body = body.replace("=== END CORPUS ===", "")
    if len(body) > limit:
        body = body[: limit - 1] + "…"
    return body.strip()


def _guess_customer(question: str) -> Optional[str]:
    """Cheap heuristic to spot 'customer X' patterns.  We do not try
    to be fancy; the corpus retriever's allow-list regex enforces
    safety on its end and a wrong guess just means we fall back to a
    keyword search."""
    if not question:
        return None
    match = _CUSTOMER_HINT_PATTERN.search(question)
    if not match:
        return None
    name = (match.group("name") or "").strip()
    if not name or len(name) > _MAX_NAME_LEN:
        return None
    return name


def _validate_chunk_safe(text: str) -> bool:
    """Run the chunk through the narrative validator's pre-prompt
    safety pass.  Defaults to ``True`` if the validator is missing
    so a partial deploy never blocks the corpus path entirely; the
    sanitization in ``_safe_text`` provides defense-in-depth."""
    try:
        from ai_narrative_validator import is_corpus_chunk_safe
    except Exception:  # noqa: BLE001 - validator optional
        return True
    try:
        return bool(is_corpus_chunk_safe(text))
    except Exception:  # noqa: BLE001 - never raise on a bad chunk
        return False


def build_corpus_block(
    *,
    question: str,
    technology: object = None,
    enabled: bool = True,
    top_k: int = _MAX_CHUNKS,
) -> CorpusContext:
    """Return a :class:`CorpusContext` for ``question``.

    When ``enabled`` is False or the corpus is otherwise unavailable
    the result is empty (``block=''``, ``allowed_ids=()``, banner
    explains why).
    """
    if not enabled or not question:
        return CorpusContext(
            block="",
            allowed_ids=(),
            banner=(
                "Corpus knowledge is disabled for this run."
                if not enabled else ""
            ),
            stats=dict(_EMPTY_STATS),
        )

    try:
        import corpus_retriever as cr
    except Exception as imp_err:  # noqa: BLE001
        logger.debug("corpus retriever import failed: %s", imp_err)
        return CorpusContext(
            block="",
            allowed_ids=(),
            banner="Corpus knowledge module is unavailable.",
            stats=dict(_EMPTY_STATS),
        )

    if not cr.is_configured():
        return CorpusContext(
            block="",
            allowed_ids=(),
            banner=(
                "Corpus knowledge is still indexing or has no eligible local "
                "files yet. Run or upload an AdoptIQ report, then refresh the "
                "local corpus."
            ),
            stats=dict(_EMPTY_STATS),
        )

    cap = max(1, min(int(top_k), _MAX_CHUNKS))

    customer_guess = _guess_customer(question)
    history = None
    if customer_guess:
        try:
            history = cr.get_customer_history(customer_guess, limit_cases=10, limit_resolutions=4)
        except cr.CorpusUnavailable as miss:
            logger.debug("corpus customer history miss for %r: %s", customer_guess, miss)
            history = None

    themes: list = []
    try:
        themes = cr.get_recurring_themes(technology, top_k=5)
    except cr.CorpusUnavailable as miss:
        logger.debug("corpus themes unavailable: %s", miss)

    chunks: list = []
    try:
        # Round 127 / Build 96 (A6): hybrid corpus rank when available.
        _search_fn = getattr(cr, "search_playbook_hybrid", None)
        if callable(_search_fn):
            chunks = _search_fn(
                question,
                technology=technology,
                top_k=cap,
            )
        else:
            chunks = cr.search_playbook(
                question,
                technology=technology,
                top_k=cap,
            )
    except cr.CorpusUnavailable as miss:
        logger.debug("corpus search unavailable: %s", miss)
        chunks = []

    lines: list[str] = []
    allowed_ids: list[str] = []
    safe_chunks_emitted = 0
    chunks_dropped_unsafe = 0
    stats_peer: dict[str, object] = {}

    if history is not None:
        history_summary = (
            f"  - customer={_safe_text(history.name, limit=120)}; "
            f"manager={_safe_text(history.manager or '-', limit=80)}; "
            f"technology={_safe_text(history.technology or '-', limit=80)}; "
            f"observed_first={_safe_text(history.first_seen or '-', limit=40)}; "
            f"observed_last={_safe_text(history.last_seen or '-', limit=40)}; "
            f"cases={len(history.cases)}; barriers={len(history.barriers)}"
        )
        lines.append("CORPUS_CUSTOMER_HISTORY:")
        lines.append(history_summary)
        # Round 175: rank every barrier theme, not just barriers[0].
        # Thin evidence still emits an explicit insufficient line so Ask AI
        # cannot invent a likely-next from one account.
        try:
            from report_corpus_context import (
                format_peer_guidance_ask_ai_line,
                peer_guidance_source_id,
                peer_path_decision,
                select_ranked_peer_guidance,
                build_peer_guidance_view,
                empty_peer_guidance_view,
                _r182_question_path_theme,
            )
        except Exception:  # noqa: BLE001
            format_peer_guidance_ask_ai_line = None  # type: ignore[assignment]
            peer_guidance_source_id = None  # type: ignore[assignment]
            peer_path_decision = None  # type: ignore[assignment]
            select_ranked_peer_guidance = None  # type: ignore[assignment]
            build_peer_guidance_view = None  # type: ignore[assignment]
            empty_peer_guidance_view = None  # type: ignore[assignment]
            _r182_question_path_theme = None  # type: ignore[assignment]
        evidence = None
        question_theme = ""
        if _r182_question_path_theme is not None:
            try:
                question_theme = str(_r182_question_path_theme(question) or "")
            except Exception:  # noqa: BLE001 - optional path filter fails closed
                question_theme = ""
        if select_ranked_peer_guidance is not None:
            try:
                evidence = select_ranked_peer_guidance(
                    customer=history.name,
                    barriers=history.barriers,
                    # Round 175.4: request-scoped technology is independently
                    # proven; never fill blank barrier tech from last-write
                    # history.technology.
                    fallback_technology="",
                    prefer_theme=question_theme,
                )
            except Exception:  # noqa: BLE001 - optional corpus fails soft
                evidence = None
        path_unlived = bool(question_theme) and (
            evidence is None
            or not bool(getattr(evidence, "evidence_sufficient", False))
        )
        published_evidence = None if path_unlived else evidence
        if format_peer_guidance_ask_ai_line is not None:
            published = format_peer_guidance_ask_ai_line(published_evidence)
            lines.append(published)
            published_thin = (
                path_unlived or "insufficient_peer_evidence=true" in published
            )
            if (
                not published_thin
                and peer_guidance_source_id is not None
            ):
                source_id = peer_guidance_source_id(published_evidence)
                if source_id:
                    allowed_ids.append(source_id)
            stats_peer = {
                "peer_customer_count": int(
                    getattr(published_evidence, "peer_customer_count", 0) or 0
                )
                if published_evidence is not None
                else 0,
                # Round 179: withheld / already-lived lines are not a future.
                "likely_next": (
                    "insufficient"
                    if published_thin or published_evidence is None
                    else str(
                        getattr(published_evidence, "likely_next", "insufficient")
                        or "insufficient"
                    )
                ),
                "insufficient_peer_evidence": published_thin,
                "peer_theme": (
                    question_theme
                    if path_unlived
                    else str(getattr(published_evidence, "theme", "") or "")
                    if published_evidence is not None
                    else ""
                ),
                "peer_technology": str(
                    getattr(published_evidence, "technology", "") or ""
                )
                if published_evidence is not None
                else "",
                # Round 179: account-scoped decision; never a forecast.
                "target_path": str(
                    getattr(published_evidence, "target_path", "") or ""
                )
                if published_evidence is not None
                else "",
                "decision": (
                    peer_path_decision(published_evidence)
                    if peer_path_decision is not None
                    else "not_enough_evidence"
                ),
                "do_not_invent_a_future": True,
                "peer_path_theme": question_theme,
            }
            # Round 177: structured card for Ask AI UI. Public sanitizer
            # stomps any live-Cisco claim before this reaches the client.
            if path_unlived and empty_peer_guidance_view is not None:
                try:
                    stats_peer["peer_guidance"] = empty_peer_guidance_view(
                        reason="unlived_path"
                    )
                except Exception:  # noqa: BLE001
                    stats_peer["peer_guidance"] = {
                        "status": "insufficient",
                        "insufficient_reason": "unlived_path",
                        "ready_for_live_cisco": False,
                    }
            elif build_peer_guidance_view is not None:
                try:
                    stats_peer["peer_guidance"] = build_peer_guidance_view(
                        published_evidence
                    )
                except Exception:  # noqa: BLE001
                    stats_peer["peer_guidance"] = {
                        "status": "insufficient",
                        "insufficient_reason": "unavailable",
                        "ready_for_live_cisco": False,
                    }

    if themes:
        lines.append("")
        lines.append("CORPUS_RECURRING_THEMES (top 5 across all corpus customers):")
        for theme in themes[:5]:
            lines.append(
                f"  - technology={_safe_text(theme.technology, limit=60)}; "
                f"theme={_safe_text(theme.theme, limit=60)}; "
                f"customers={int(theme.customers)}; occurrences={int(theme.occurrences)}"
            )

    if chunks:
        lines.append("")
        lines.append("CORPUS_PLAYBOOK_CHUNKS (hybrid-ranked when available; cite by SourceID):")
        for idx, chunk in enumerate(chunks):
            if safe_chunks_emitted >= cap:
                break
            text = _safe_text(chunk.text, limit=_MAX_CHUNK_CHARS)
            if not text:
                continue
            if not _validate_chunk_safe(text):
                chunks_dropped_unsafe += 1
                continue
            source_id = f"CORPUS:{idx + 1:03d}"
            allowed_ids.append(source_id)
            lines.append(
                f"  - [{source_id}] technology={_safe_text(chunk.technology or '-', limit=60)}; "
                f"theme={_safe_text(chunk.theme or '-', limit=60)}; "
                f"customer={_safe_text(chunk.customer_name or '-', limit=120)}; "
                f"score={float(chunk.score):.3f}; "
                f"text=\"{text}\""
            )
            safe_chunks_emitted += 1

    if not lines:
        return CorpusContext(
            block="",
            allowed_ids=(),
            banner="No corpus context matched this question.",
            stats=dict(_EMPTY_STATS),
        )

    block = (
        "<corpus>\n"
        "=== BEGIN CORPUS ===\n"
        + "\n".join(lines)
        + "\n=== END CORPUS ===\n"
        "</corpus>"
    )
    stats: dict[str, object] = {
        "available": True,
        "chunks": int(safe_chunks_emitted),
        "chunks_dropped_unsafe": int(chunks_dropped_unsafe),
        "customer_name": history.name if history is not None else None,
        "themes": len(themes),
        "history_cases": (len(history.cases) if history is not None else 0),
    }
    if stats_peer:
        stats.update(stats_peer)
    return CorpusContext(
        block=block,
        allowed_ids=tuple(allowed_ids),
        banner="",
        stats=stats,
    )


def render_banner(context: CorpusContext) -> str:
    """Plain-text banner for the UI when ``context.block`` is empty
    but the user explicitly asked for corpus grounding."""
    return context.banner or ""


__all__ = [
    "CorpusContext",
    "build_corpus_block",
    "render_banner",
]
