"""Round 17 / Phase B.2 -- CSOne knowledge corpus indexer.

Walks the ``Config.CSONE_ONEDRIVE_FOLDER`` directory, parses every
supported file, and persists the resulting customer / case / barrier /
resolution / sentiment / chunk records into the SQLite database
declared by :mod:`knowledge_schema`.

Design contract
---------------
* Pure functions where possible.  The two side-effecting entry points
  are ``index_folder`` (writes the database) and ``rebuild_index``
  (drops and re-applies the schema).
* Idempotent.  Files whose ``(path, mtime_utc, sha256)`` triple is
  already in ``corpus_files`` are skipped on the next pass.
* Defensive.  A malformed file never aborts the whole index pass; it
  is recorded with ``parse_status='error'`` and the run continues.
* No new dependencies.  BM25 is implemented in-house (see
  ``_tokenize`` and the term-stats persistence below) so the DMG
  footprint stays flat.
* No customer PII at INFO log level; full names at DEBUG only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from _logging_helpers import exit_log_streams_open as _shared_exit_log_streams_open
from _logging_helpers import safe_log_info as _shared_safe_log_info
from knowledge_schema import (
    SCHEMA_VERSION,
    all_table_names,
    apply_schema,
    needs_rebuild,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round 63 / Tier A: closed-stream defense for logger.info during
# pytest teardown.  R62 / A1 originally duplicated ``corpus_bootstrap``'s
# inline implementation here to break the circular-import edge
# (``corpus_bootstrap`` depends on this module).  R63 promotes the
# implementation to ``_logging_helpers`` (which has no AdoptIQ-specific
# imports, so neither side of the cycle is reintroduced) and keeps
# wrapper names of the same shape so existing R62 tests + call sites
# remain stable.  See ``_logging_helpers.safe_log_info`` for the full
# explanation of why ``Handler.handleError()`` cannot be defeated by
# a try/except wrap on ``logger.info`` alone.
# ---------------------------------------------------------------------------


def _exit_log_streams_open() -> bool:
    """Round 63: thin wrapper around
    ``_logging_helpers.exit_log_streams_open``.  Wrapper name preserved
    for back-compat with R62 tests and call sites in this module."""
    return _shared_exit_log_streams_open(logger)


def _safe_log_info(msg: str, *args: object) -> None:
    """Round 63: thin wrapper around ``_logging_helpers.safe_log_info``.

    Wrapper name preserved so the 5 ``_safe_log_info`` call sites in
    this module (introduced by R62 / A1) keep delegating through a
    stable name.  Pinned by
    ``tests/test_round63_corpus_indexer_logger_parity.py``.
    """
    _shared_safe_log_info(logger, msg, *args)


# ---------------------------------------------------------------------------
# Stop signal -- callers can flip ``stop_requested`` to abort a long run
# ---------------------------------------------------------------------------


@dataclass
class IndexSignal:
    """Mutable cancellation signal passed into long-running indexer
    calls.  ``app_simple`` flips ``stop_requested`` from the shutdown
    handler so a background indexer exits cleanly during interpreter
    teardown."""

    stop_requested: bool = False
    progress_files: int = 0


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Maximum file size to attempt to parse.  Files larger than this are
#: recorded with ``parse_status='oversized'`` so the next pass skips
#: them quickly.  Bounded to keep memory predictable (~50 MB).
_MAX_PARSE_BYTES: int = 50 * 1024 * 1024

#: Maximum number of cells / paragraphs to consider in any single
#: file.  Defends against pathological docx / xlsx files that could
#: otherwise inflate ``playbook_chunks`` by orders of magnitude.
_MAX_RECORDS_PER_FILE: int = 20_000

#: Length cap for any free-text field persisted to the database.
#: Bounded so query plans stay predictable and so a hostile file
#: cannot blow up the DB.
_MAX_TEXT_BYTES: int = 4_000

#: BM25 narrative chunk size, measured in tokens.  Chunks shorter than
#: ``_MIN_CHUNK_TOKENS`` are merged with the next one; longer chunks
#: are split.
_TARGET_CHUNK_TOKENS: int = 60
_MIN_CHUNK_TOKENS: int = 12
_MAX_CHUNK_TOKENS: int = 180

#: BM25 hyperparameters.  Standard textbook defaults.
BM25_K1: float = 1.5
BM25_B: float = 0.75

#: File-type registry.  Keyed on lowercased extension.
_SUPPORTED_EXTENSIONS: tuple[str, ...] = (".xlsx", ".docx", ".csv")

#: Heuristic stop-words for the in-house tokenizer.  Lifted from
#: ``ask_ai_grounded._STOP_WORDS`` and broadened.  Kept short so
#: tokens like "case" / "open" stay searchable.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "to", "in", "on",
        "for", "with", "by", "as", "at", "is", "are", "was",
        "were", "be", "been", "being", "this", "that", "these",
        "those", "it", "its", "from", "into", "if", "then", "than",
        "so", "but", "not", "no", "yes", "do", "does", "did",
        "have", "has", "had", "will", "would", "could", "should",
        "may", "might", "shall", "can", "i", "we", "you", "they",
        "he", "she", "their", "them", "us", "our",
    }
)

#: Theme heuristic: short, deduped tags assigned by keyword presence
#: in the barrier / case subject.  Order matters: the first match
#: wins so the most specific theme is captured.
_THEME_HEURISTICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("authentication", ("auth", "sso", "oauth", "saml", "ldap", "login", "credential")),
    ("licensing",      ("license", "licence", "entitlement", "activation", "subscription")),
    ("performance",    ("slow", "latency", "performance", "timeout", "lag")),
    ("connectivity",   ("connectivity", "network", "vpn", "tunnel", "firewall", "dns")),
    ("upgrade",        ("upgrade", "migration", "version", "patch", "rollback")),
    ("integration",    ("integration", "api", "webhook", "connector", "plugin")),
    ("configuration",  ("config", "configuration", "setting", "policy", "rule")),
    ("data_quality",   ("data", "missing", "incorrect", "duplicate", "inconsistent")),
    ("training",       ("training", "documentation", "guide", "tutorial", "kb article")),
    ("billing",        ("billing", "invoice", "renewal", "expir", "auto-renew")),
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusFile:
    """Immutable description of one file on disk.  Returned by
    ``enumerate_corpus_files``; the indexer turns this into a
    ``corpus_files`` row."""

    path: Path
    filename: str
    size_bytes: int
    mtime_utc: str
    sha256: str

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()


@dataclass
class IndexStats:
    """Outcome of one ``index_folder`` call.  Returned to the caller
    so the admin tile can report counts without reopening the DB."""

    files_seen: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    files_empty: int = 0
    files_oversized: int = 0
    customers_added: int = 0
    cases_added: int = 0
    barriers_added: int = 0
    resolutions_added: int = 0
    sentiments_added: int = 0
    chunks_added: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Plain-dict shape for the admin status payload."""
        return {
            "files_seen": self.files_seen,
            "files_parsed": self.files_parsed,
            "files_skipped": self.files_skipped,
            "files_failed": self.files_failed,
            "files_empty": self.files_empty,
            "files_oversized": self.files_oversized,
            "customers_added": self.customers_added,
            "cases_added": self.cases_added,
            "barriers_added": self.barriers_added,
            "resolutions_added": self.resolutions_added,
            "sentiments_added": self.sentiments_added,
            "chunks_added": self.chunks_added,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "errors": list(self.errors[:5]),
        }


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_file(path: Path) -> str:
    """Streaming SHA-256.  Used as the content-drift sentinel."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def enumerate_corpus_files(
    root: Path | str | None,
    *,
    signal: Optional[IndexSignal] = None,
    filename_filter: Optional[re.Pattern[str]] = None,
    recursive: bool = True,
    require_adoptiq_quality_gate: bool = False,
) -> list[CorpusFile]:
    """Walk ``root`` and return every supported file.

    Defensive: returns an empty list when ``root`` is ``None``, does
    not exist, or is not a directory.  Skips files whose extension is
    not in ``_SUPPORTED_EXTENSIONS`` and ignores OneDrive temp files
    (``~$*``, ``.tmp``).

    Round 17.1: callers can pass an optional ``filename_filter``
    compiled regex (matched against the filename, not the full path);
    only filenames that match are emitted.  The downloads enumerator
    uses this to keep unrelated user files out of the corpus.
    """
    if root is None:
        return []
    root_path = Path(root)
    if not root_path.exists() or not root_path.is_dir():
        return []

    out: list[CorpusFile] = []
    try:
        iterator = root_path.rglob("*") if recursive else root_path.iterdir()
        for entry in iterator:
            if signal is not None and signal.stop_requested:
                break
            if not entry.is_file():
                continue
            name = entry.name
            if name.startswith("~$") or name.endswith(".tmp"):
                continue
            ext = entry.suffix.lower()
            if ext not in _SUPPORTED_EXTENSIONS:
                continue
            if filename_filter is not None and not filename_filter.search(name):
                continue
            if (
                require_adoptiq_quality_gate
                and _round94_generated_report_needs_quality_gate(entry)
                and not _round92_quality_sidecar_allows(entry)
            ):
                continue
            try:
                stat = entry.stat()
            except OSError as stat_err:
                logger.debug("stat failed for %s: %s", entry, stat_err)
                continue
            try:
                sha = _hash_file(entry)
            except OSError as hash_err:
                logger.debug("hash failed for %s: %s", entry, hash_err)
                continue
            mtime_utc = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            out.append(
                CorpusFile(
                    path=entry,
                    filename=name,
                    size_bytes=int(stat.st_size),
                    mtime_utc=mtime_utc,
                    sha256=sha,
                )
            )
            if signal is not None:
                signal.progress_files += 1
    except OSError as walk_err:  # pragma: no cover - exotic FS
        logger.warning("corpus walk failed under %s: %s", root_path, walk_err)
    return out


def _round92_quality_sidecar_allows(path: Path) -> bool:
    """Round 92: admit generated reports only when their sidecar passes.

    The local ``AdoptIQ Reports`` source contains app-generated
    artifacts.  Bad generated reports should remain visible to users but
    must not pollute Ask AI's corpus, so the writer creates
    ``<filename>.adoptiq_corpus.json`` with ``corpus_eligible=true`` only
    after the strict runtime diagnostics policy passes.
    """
    try:
        sidecar = path.with_name(path.name + ".adoptiq_corpus.json")
        if not sidecar.is_file():
            return False
        if sidecar.stat().st_size > 16_384:
            return False
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        return bool(data.get("corpus_eligible") is True)
    except Exception:
        return False


# Round 17.1 + 102: named-report walker that admits only AdoptIQ-named
# files.  The runtime Downloads source is retired, but the allow-list
# still protects AdoptIQ-managed local outputs and explicit upload
# directories. The pattern matches three families we observed in the wild:
#   * ``AdoptIQ_Report_*.docx`` / ``AdoptIQ_Source_Data_*.xlsx``
#     / legacy ``AdoptIQ_Data_*.xlsx``                         (rendered)
#   * ``AdoptIQ Enhanced Premium Collab Summary-*.xlsx``     (CSOne dump)
#   * ``AdoptIQ_*.csv`` for any future CSV exports
# Anything else is rejected so we never silently slurp unrelated user files.
_USER_REPORT_NAME_RE: re.Pattern[str] = re.compile(
    r"^AdoptIQ[\s_].+\.(?:xlsx|docx|csv)$",
    re.IGNORECASE,
)
_GENERATED_REPORT_NAME_RE: re.Pattern[str] = re.compile(
    # Round 142: Source_Data is still an app-generated artifact and must never
    # bypass the Round 92/94 corpus-eligibility sidecar gate.
    r"^AdoptIQ_(?:Report|Source_Data|Data)_.*\.(?:xlsx|docx|csv)$",
    re.IGNORECASE,
)


def _round94_generated_report_needs_quality_gate(path: Path) -> bool:
    """Round 94: app-generated reports need sidecar approval in every corpus source."""
    try:
        return bool(_GENERATED_REPORT_NAME_RE.search(path.name))
    except Exception:
        return False


def enumerate_user_report_files(
    downloads_dir: Path | str | None,
    *,
    signal: Optional[IndexSignal] = None,
    recursive: bool = False,
    require_adoptiq_quality_gate: bool = False,
) -> list[CorpusFile]:
    """Round 17.1 + 102: enumerate AdoptIQ-named report files in a
    caller-provided directory.

    Filename allow-list: ``^AdoptIQ[\\s_].+\\.(xlsx|docx|csv)$`` so
    unrelated user files are never opened. Defaults to top-level only
    (``recursive=False``); callers must opt in explicitly before walking
    nested directories.

    Round 81 / Build 57: callers indexing the AdoptIQ-managed
    ``<APP_SUPPORT>/outputs/`` tree pass ``recursive=True`` so the
    new ``<Manager>/<Type>/<file>.docx`` nested layout is fully
    walked.  Defensive: returns ``[]`` when ``downloads_dir`` is
    ``None``, missing, or unreadable.
    """
    return enumerate_corpus_files(
        downloads_dir,
        signal=signal,
        filename_filter=_USER_REPORT_NAME_RE,
        recursive=bool(recursive),
        require_adoptiq_quality_gate=bool(require_adoptiq_quality_gate),
    )


# ---------------------------------------------------------------------------
# Tokenization & theme detection
# ---------------------------------------------------------------------------


_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _tokenize(text: str) -> list[str]:
    """Lowercase + alpha-token splitter used by the in-house BM25 and
    the theme heuristic.  Drops stop words and tokens shorter than 3
    characters.  Pure / deterministic."""
    if not text:
        return []
    body = unicodedata.normalize("NFKC", str(text)).lower()
    return [t for t in _TOKEN_PATTERN.findall(body) if t not in _STOP_WORDS]


def tokenize(text: str) -> list[str]:
    """Public alias of the BM25 tokenizer so the retriever does not
    have to import a private symbol.  Same contract as ``_tokenize``."""
    return _tokenize(text)


def detect_theme(text: str) -> str:
    """Map free-text barrier subjects to one of the
    ``_THEME_HEURISTICS`` tags.  Returns ``"general"`` when nothing
    matches so retrieval queries still have a non-null theme to filter
    on."""
    body = (text or "").casefold()
    if not body:
        return "general"
    for theme, keywords in _THEME_HEURISTICS:
        for kw in keywords:
            if kw in body:
                return theme
    return "general"


def _truncate(text: Any, limit: int = _MAX_TEXT_BYTES) -> str:
    if text is None:
        return ""
    body = str(text)
    if len(body.encode("utf-8", errors="ignore")) <= limit:
        return body
    return body.encode("utf-8", errors="ignore")[:limit].decode("utf-8", errors="ignore")


def _coerce_pulse_score(value: Any) -> Optional[float]:
    """Map the canonical pulse colour ("red"/"amber"/"yellow"/"green")
    to a numeric -1.0/0.0/+1.0 scale.  Returns ``None`` for unknown
    values so the row can still be inserted with a null score."""
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if "red" in raw or "critical" in raw:
        return -1.0
    if "amber" in raw or "yellow" in raw or "medium" in raw:
        return 0.0
    if "green" in raw or "healthy" in raw:
        return 1.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


@dataclass
class ParsedRecord:
    """Intermediate record produced by a parser before it lands in the
    database.  Each parser emits a list of these and the indexer maps
    them onto the right row(s)."""

    customer_name: str
    manager: Optional[str] = None
    technology: Optional[str] = None
    case_number: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    is_open: bool = False
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    summary: Optional[str] = None
    barrier_subject: Optional[str] = None
    barrier_description: Optional[str] = None
    barrier_status: Optional[str] = None  # Round 176: AB_STATUS_C / STATUS_C
    resolution_text: Optional[str] = None
    sentiment: Optional[str] = None
    sentiment_evidence: Optional[str] = None
    snapshot_date: Optional[str] = None


# Round 176: same candidate order as canonical_metrics count_open_barriers
# (STATUS_C before historically-blank AB_STATUS_C). Unknown/blank stays
# unknown — never coerced to closed.
_BARRIER_STATUS_ALIASES: tuple[str, ...] = (
    "STATUS_C",
    "AB_STATUS_C",
    "Status",
    "STATUS",
    "AB Status",
    "Barrier Status",
)


def _clean_barrier_status(status: Any) -> Optional[str]:
    """Persist a real label or NULL. Never the literal string 'nan'. Round 176."""
    text = _truncate(status, 32).strip()
    if not text or text.casefold() in {"nan", "none", "null", "nat"}:
        return None
    return text


def _barrier_open_flag(status: Any) -> Optional[int]:
    """Map a barrier status label to SQLite is_open (1/0/NULL). Round 176."""
    raw = _clean_barrier_status(status)
    if not raw:
        return None
    try:
        from data_normalization import normalize_status_label
    except Exception:  # noqa: BLE001 - indexer must not fail a file on import
        return None
    label = normalize_status_label(raw)
    if label == "Open":
        return 1
    if label == "Closed":
        return 0
    return None


_CSONE_BANNER_MARKERS: tuple[str, ...] = (
    "AdoptIQ Enhanced/Premium",
    "AdoptIQ Enhanced Premium",
    "Filtered By:",
    "Filtered By",
    "Date Field:",
    "Service Tier equals",
)


def _detect_xlsx_layout(xl: Any, sheet_name: str, *, pd_mod: Any) -> tuple[str, int]:
    """Round 17.1: detect which AdoptIQ family an xlsx sheet belongs to.

    Returns ``(layout, header_row)`` where ``header_row`` is the
    zero-indexed row to pass as ``pd.read_excel(..., header=...)``:

    * ``("csone_export", 15)`` -- the CSOne TAC dump from the
      OneDrive macro: rows 1-15 are "Filtered By" / banner text and
      the actual column header lives on Excel row 16.
    * ``("adoptiq_data", 1)`` -- AdoptIQ-rendered ``Data`` xlsx:
      row 1 is a single-cell sheet title (e.g. ``Risk_Summary``)
      and the header is on Excel row 2.
    * ``("plain", 0)`` -- legacy / synthetic CSV-style sheet with
      the header on row 1 (the original Round 17 contract).

    The detector reads at most the first 20 rows with ``header=None``
    so it never disturbs the real column layout.  Any peek-failure
    falls through to ``("plain", 0)`` so we never raise from the
    parser.
    """
    try:
        peek = pd_mod.read_excel(
            xl, sheet_name=sheet_name, header=None, nrows=20
        )
    except Exception:
        return ("plain", 0)
    if peek is None or peek.empty:
        return ("plain", 0)

    def _is_filled(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd_mod.isna(value):
                return False
        except Exception:
            pass
        text = str(value).strip()
        return bool(text)

    # CSOne export: any of the banner markers in rows 0-15 wins.
    for row_idx in range(min(16, len(peek))):
        try:
            row = peek.iloc[row_idx]
        except Exception:
            continue
        for cell in row:
            if not _is_filled(cell):
                continue
            text = str(cell)
            for marker in _CSONE_BANNER_MARKERS:
                if marker in text:
                    return ("csone_export", 15)

    # AdoptIQ Data: row 0 is a single-cell title, row 1 has the real
    # header.  We require row 1 to have at least 3 filled columns so
    # plain sheets (with header on row 0 and data starting on row 1)
    # are never misclassified.
    if len(peek) >= 2:
        try:
            row0 = peek.iloc[0]
            row1 = peek.iloc[1]
        except Exception:
            return ("plain", 0)
        row0_n = sum(1 for v in row0 if _is_filled(v))
        row1_n = sum(1 for v in row1 if _is_filled(v))
        if row0_n <= 2 and row1_n >= 3:
            return ("adoptiq_data", 1)

    return ("plain", 0)


_XLSX_CASE_TOKENS: tuple[str, ...] = (
    "csone", "tac", "case", "support_case",
)
_XLSX_BARRIER_TOKENS: tuple[str, ...] = (
    "barrier", "_ab_", "adoption", "action_plan", "success_priorit",
)
_XLSX_PULSE_TOKENS: tuple[str, ...] = (
    "pulse", "sentiment",
)
# AdoptIQ "Data" workbook narrative sheets -- counts + per-customer
# context that anchors the BM25 corpus to the same numbers the report
# shows the user.
_XLSX_NARRATIVE_TOKENS: tuple[str, ...] = (
    "risk_summary", "high_risk", "risk_components", "risk_band",
    "renewal_summary", "key_metrics", "team_summary", "subscription",
    "recommendation", "executive", "strategic", "outcome",
    "metrics", "all_data", "renewal_priorit",
)
# External-intelligence sheets emit "External Intelligence" as a
# synthetic customer so they remain searchable in the playbook
# without polluting the customers table with a fake account.
_XLSX_EXTERNAL_TOKENS: tuple[str, ...] = (
    "external_bug", "external_incident", "field_notice", "psirt", "bst",
    "external",
)


def _row_to_blob(row: Any, *, pd_mod: Any) -> Optional[str]:
    """Render a row as a human-readable ``key: value`` blob for BM25
    chunking.  Used by the narrative xlsx branches where there is no
    one canonical text column.  Skips empty / NaN cells and ignores
    placeholder column names like ``col_2`` / ``Unnamed: N``."""
    try:
        items = list(row.items())
    except Exception:
        return None
    parts: list[str] = []
    for col_key, raw_val in items:
        if raw_val is None:
            continue
        try:
            if pd_mod.isna(raw_val):
                continue
        except Exception:
            pass
        key_text = str(col_key).strip()
        if not key_text or key_text.lower().startswith("unnamed:") or re.fullmatch(r"col_\d+", key_text, re.IGNORECASE):
            continue
        val_text = str(raw_val).strip()
        if not val_text:
            continue
        parts.append(f"{key_text}: {val_text}")
        if sum(len(p) for p in parts) >= _MAX_TEXT_BYTES:
            break
    if not parts:
        return None
    return " | ".join(parts)


def _emit_case_record(row: Any, _get) -> Optional[ParsedRecord]:
    customer = _get(
        row,
        "customer_name",
        "Customer",
        "Customer Name",
        "Customer Name: Customer Name",
        "ACCOUNT_NAME",
        "ACCOUNT__C",
        "BU_NAME",
    )
    if customer is None or (isinstance(customer, str) and not customer.strip()):
        return None
    rec = ParsedRecord(
        customer_name=_truncate(customer, 200),
        technology=_truncate(
            _get(row, "Tech.", "Sub Technology", "Sub Tech.", "technology", "TECHNOLOGY", "Product: Product Name"),
            100,
        ),
        case_number=_truncate(
            _get(row, "Case Number", "SR Number", "case_number", "CASE_NUMBER", "SR_NUMBER"),
            64,
        ),
        severity=_truncate(_get(row, "Severity", "severity_norm", "SEVERITY"), 32),
        status=_truncate(
            _get(row, "Case Status", "case_status_norm", "status_norm", "STATUS"), 32
        ),
        opened_at=_truncate(
            _get(row, "Date/Time Opened", "open_date", "Created", "Created Date", "OPEN_DATE", "OPEN_DATE_C"),
            64,
        ),
        closed_at=_truncate(
            _get(row, "Date/Time Closed", "closed_date", "CLOSED_DATE"), 64
        ),
        summary=_truncate(
            _get(row, "Title", "Problem Description", "Resolution Summary", "SUBJECT_C"),
            _MAX_TEXT_BYTES,
        ),
        resolution_text=_truncate(
            _get(
                row,
                "Resolution Summary",
                "resolution",  # Round 175: CSOne-shaped barrier/case alias
                "Resolution",
                "RESOLUTION",
                "CSE Action Plan",
                "CLOSURE_COMMENTS_C",
            ),
            _MAX_TEXT_BYTES,
        ),
    )
    rec.is_open = bool(rec.status and "open" in rec.status.lower())
    return rec


def _emit_barrier_record(row: Any, _get) -> Optional[ParsedRecord]:
    customer = _get(
        row,
        "customer_name",
        "Customer",
        "ACCOUNT__C",
        "ACCOUNT_NAME",
        "BU_NAME",
        "NAME",
    )
    if customer is None or (isinstance(customer, str) and not customer.strip()):
        return None
    return ParsedRecord(
        customer_name=_truncate(customer, 200),
        manager=_truncate(_get(row, "ACCOUNT_MANAGER_C", "manager"), 200),
        technology=_truncate(
            _get(row, "BUSINESS_UNIT_C", "sub_technology", "Tech."), 100
        ),
        barrier_subject=_truncate(
            _get(row, "SUBJECT_C", "title", "ACTION_PLAN_TITLE_C", "NAME"),
            _MAX_TEXT_BYTES,
        ),
        barrier_description=_truncate(
            _get(row, "DESCRIPTION_C", "description"), _MAX_TEXT_BYTES
        ),
        barrier_status=_truncate(_get(row, *_BARRIER_STATUS_ALIASES), 32),  # Round 176
        resolution_text=_truncate(
            _get(
                row,
                "resolution",  # Round 175: dedicated resolution column wins
                "Resolution",
                "RESOLUTION",
                "Resolution Summary",
                "ACTION_PLAN_TITLE_C",
                "NEXT_ACTION_C",
                "CLOSURE_COMMENTS_C",
            ),
            _MAX_TEXT_BYTES,
        ),
    )


def _emit_pulse_record(row: Any, _get) -> Optional[ParsedRecord]:
    customer = _get(row, "ACCOUNT__C", "BU_NAME", "NAME", "Customer", "customer_name")
    if customer is None or (isinstance(customer, str) and not customer.strip()):
        return None
    return ParsedRecord(
        customer_name=_truncate(customer, 200),
        sentiment=_truncate(_get(row, "CUSTOMER_PULSE__C", "pulse"), 32),
        sentiment_evidence=_truncate(
            _get(row, "COMMENTS__C", "comments"), _MAX_TEXT_BYTES
        ),
        snapshot_date=_truncate(_get(row, "snapshot_date", "AS_OF_DATE"), 64),
    )


def _emit_narrative_record(row: Any, _get, *, pd_mod: Any) -> Optional[ParsedRecord]:
    customer = _get(
        row,
        "Customer",
        "customer_name",
        "ACCOUNT__C",
        "ACCOUNT_NAME",
        "BU_NAME",
        "Team_Member",
        "NAME",
    )
    if customer is None or (isinstance(customer, str) and not customer.strip()):
        return None
    blob = _row_to_blob(row, pd_mod=pd_mod)
    return ParsedRecord(
        customer_name=_truncate(customer, 200),
        technology=_truncate(
            _get(row, "Tech.", "technology", "TECHNOLOGY", "Sub Technology"), 100
        ),
        summary=_truncate(blob, _MAX_TEXT_BYTES),
    )


def _emit_external_record(row: Any, _get, *, pd_mod: Any) -> Optional[ParsedRecord]:
    title = _get(row, "title", "Title", "name", "NAME", "headline", "subject")
    source = _get(row, "source", "source_url", "link", "url")
    if not _is_meaningful(title) and not _is_meaningful(source):
        return None
    tech = _get(
        row,
        "technology",
        "Tech.",
        "category",
        "impact_level",
        "severity",
        "product",
    )
    blob = _row_to_blob(row, pd_mod=pd_mod)
    return ParsedRecord(
        customer_name="External Intelligence",
        technology=_truncate(tech, 100),
        summary=_truncate(blob, _MAX_TEXT_BYTES),
    )


def _is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text)


def _parse_xlsx(path: Path) -> list[ParsedRecord]:
    """Pandas-based xlsx parser.  Round 17.1 makes it layout-aware:
    detects whether the sheet is a CSOne TAC export (banner rows
    1-15, header on row 16), an AdoptIQ-rendered Data sheet (title
    on row 1, header on row 2), or a plain header-on-row-1 sheet,
    and reads with the correct ``header=`` argument.  Routes each
    sheet to a case / barrier / pulse / narrative / external emitter
    based on the layout and sheet name.  Returns an empty list on any
    structural failure (defensive)."""
    try:
        import pandas as pd
    except Exception as imp_err:  # pragma: no cover - pandas is required
        logger.warning("pandas unavailable for corpus parse: %s", imp_err)
        return []

    out: list[ParsedRecord] = []
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception as open_err:
        logger.debug("ExcelFile open failed for %s: %s", path.name, open_err)
        return []

    for sheet_name in xl.sheet_names:
        if len(out) >= _MAX_RECORDS_PER_FILE:
            break
        try:
            layout, header_row = _detect_xlsx_layout(xl, sheet_name, pd_mod=pd)
        except Exception:
            layout, header_row = ("plain", 0)
        # Round 17.2: per-sheet layout decision so the operator can
        # see how the indexer is interpreting each tab.
        # Round 62 / A1: route through _safe_log_info so the
        # closed-stream race during pytest teardown does not leak
        # tracebacks (see helper docstring).
        _safe_log_info(
            "Round 17.2 / corpus_indexer: xlsx file=%s sheet=%s layout=%s header_row=%d",
            path.name, sheet_name, layout, int(header_row),
        )
        try:
            df = pd.read_excel(
                xl,
                sheet_name=sheet_name,
                header=header_row,
                nrows=_MAX_RECORDS_PER_FILE,
            )
        except Exception as sheet_err:
            logger.warning(
                "Round 17.2 / corpus_indexer: sheet read failed file=%s sheet=%s kind=%s",
                path.name, sheet_name, type(sheet_err).__name__,
            )
            continue
        if df is None or df.empty:
            continue
        df = df.dropna(how="all")
        if df.empty:
            continue

        cols = {str(c).strip(): str(c) for c in df.columns}
        cols_ci = {k.lower(): v for k, v in cols.items()}

        def _get(row: Any, *aliases: str) -> Any:
            for alias in aliases:
                key = cols.get(alias) or cols_ci.get(alias.lower())
                if key is not None and key in row.index:
                    value = row[key]
                    if value is None:
                        continue
                    try:
                        if pd.isna(value):
                            continue
                    except Exception:
                        pass
                    return value
            return None

        sheet_lower = sheet_name.lower().strip()

        # CSOne export: every sheet is the TAC case dump regardless of
        # the macro-supplied sheet name.
        if layout == "csone_export":
            for _, row in df.iterrows():
                if len(out) >= _MAX_RECORDS_PER_FILE:
                    break
                rec = _emit_case_record(row, _get)
                if rec is not None:
                    out.append(rec)
            continue

        # Plain + adoptiq_data layouts: dispatch by sheet name token.
        if any(tok in sheet_lower for tok in _XLSX_CASE_TOKENS):
            for _, row in df.iterrows():
                if len(out) >= _MAX_RECORDS_PER_FILE:
                    break
                rec = _emit_case_record(row, _get)
                if rec is not None:
                    out.append(rec)
            continue

        if any(tok in sheet_lower for tok in _XLSX_BARRIER_TOKENS):
            for _, row in df.iterrows():
                if len(out) >= _MAX_RECORDS_PER_FILE:
                    break
                rec = _emit_barrier_record(row, _get)
                if rec is not None:
                    out.append(rec)
            continue

        if any(tok in sheet_lower for tok in _XLSX_PULSE_TOKENS):
            for _, row in df.iterrows():
                if len(out) >= _MAX_RECORDS_PER_FILE:
                    break
                rec = _emit_pulse_record(row, _get)
                if rec is not None:
                    out.append(rec)
            continue

        if any(tok in sheet_lower for tok in _XLSX_EXTERNAL_TOKENS):
            for _, row in df.iterrows():
                if len(out) >= _MAX_RECORDS_PER_FILE:
                    break
                rec = _emit_external_record(row, _get, pd_mod=pd)
                if rec is not None:
                    out.append(rec)
            continue

        if any(tok in sheet_lower for tok in _XLSX_NARRATIVE_TOKENS):
            for _, row in df.iterrows():
                if len(out) >= _MAX_RECORDS_PER_FILE:
                    break
                rec = _emit_narrative_record(row, _get, pd_mod=pd)
                if rec is not None:
                    out.append(rec)
            continue

    return out


def _parse_docx(path: Path) -> list[ParsedRecord]:
    """Light-weight docx parser.  Extracts paragraph text into a
    single ``ParsedRecord`` per detected customer heading.  AdoptIQ
    docx outputs use H2/H3 headings for customer / technology; the
    rest is narrative we want to preserve as ``summary`` text for
    BM25 retrieval.

    Returns an empty list when ``python-docx`` is not importable
    (e.g. inside a stripped test harness).
    """
    try:
        from docx import Document
    except Exception as imp_err:
        logger.debug("python-docx unavailable: %s", imp_err)
        return []

    try:
        doc = Document(str(path))
    except Exception as open_err:
        logger.debug("docx open failed for %s: %s", path.name, open_err)
        return []

    out: list[ParsedRecord] = []
    current_customer: Optional[str] = None
    current_tech: Optional[str] = None
    paragraph_buffer: list[str] = []

    def _flush() -> None:
        nonlocal paragraph_buffer
        if current_customer and paragraph_buffer:
            text = " ".join(p for p in paragraph_buffer if p).strip()
            if text:
                out.append(
                    ParsedRecord(
                        customer_name=_truncate(current_customer, 200),
                        technology=_truncate(current_tech, 100),
                        summary=_truncate(text, _MAX_TEXT_BYTES),
                    )
                )
        paragraph_buffer = []

    for para in doc.paragraphs:
        if len(out) >= _MAX_RECORDS_PER_FILE:
            break
        text = (para.text or "").strip()
        if not text:
            continue
        style = (getattr(para.style, "name", "") or "").lower()
        if "heading 2" in style:
            _flush()
            current_customer = text
            current_tech = None
        elif "heading 3" in style:
            _flush()
            current_tech = text
        else:
            paragraph_buffer.append(text)
    _flush()
    return out


def _parse_csv(path: Path) -> list[ParsedRecord]:
    """Minimal CSV parser.  Reuses the xlsx column-alias logic by
    routing the data through pandas so we get the same coverage
    without two copies of the alias map."""
    try:
        import pandas as pd
    except Exception as imp_err:  # pragma: no cover - pandas is required
        logger.warning("pandas unavailable for CSV parse: %s", imp_err)
        return []
    try:
        df = pd.read_csv(path, nrows=_MAX_RECORDS_PER_FILE)
    except Exception as csv_err:
        logger.debug("csv read failed for %s: %s", path.name, csv_err)
        return []
    if df is None or df.empty:
        return []
    out: list[ParsedRecord] = []
    cols = {str(c).strip(): str(c) for c in df.columns}
    cols_ci = {k.lower(): v for k, v in cols.items()}

    def _get(row: Any, *aliases: str) -> Any:
        for alias in aliases:
            key = cols.get(alias) or cols_ci.get(alias.lower())
            if key is not None and key in row.index:
                return row[key]
        return None

    def _get_scalar(row: Any, *aliases: str) -> Any:
        # Round 173: NaN-safe variant for the new timestamp fields only, so a
        # blank CSV cell never becomes the literal string "nan" in the corpus.
        # Existing fields keep the legacy _get behavior byte-for-byte.
        value = _get(row, *aliases)
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:  # noqa: BLE001 - non-scalar values pass through
            pass
        return value

    for _, row in df.iterrows():
        if len(out) >= _MAX_RECORDS_PER_FILE:
            break
        customer = _get(row, "customer_name", "Customer", "Customer Name", "NAME")
        if customer is None:
            continue
        rec = ParsedRecord(
            customer_name=_truncate(customer, 200),
            technology=_truncate(_get(row, "technology", "Tech."), 100),
            case_number=_truncate(_get(row, "case_number", "Case Number"), 64),
            severity=_truncate(_get(row, "severity_norm", "Severity"), 32),
            status=_truncate(_get(row, "case_status_norm", "Case Status"), 32),
            # Round 173: carry the same opened/closed timestamps the xlsx path
            # (_emit_case_record) already maps, so CSV-built corpora can back
            # the operating-health closure-precedent claim.  No schema change:
            # the cases table has always had opened_at/closed_at columns.
            opened_at=_truncate(_get_scalar(row, "Date/Time Opened", "open_date", "OPEN_DATE_C"), 64),
            closed_at=_truncate(_get_scalar(row, "Date/Time Closed", "closed_date", "CLOSED_DATE_C"), 64),
            summary=_truncate(_get(row, "Title", "summary"), _MAX_TEXT_BYTES),
            resolution_text=_truncate(
                _get(
                    row,
                    "Resolution Summary",
                    "resolution",  # Round 175: synthetic CSOne barrier alias
                    "Resolution",
                    "RESOLUTION",
                ),
                _MAX_TEXT_BYTES,
            ),
            barrier_subject=_truncate(_get(row, "SUBJECT_C"), _MAX_TEXT_BYTES),
            barrier_status=_truncate(
                _get_scalar(row, *_BARRIER_STATUS_ALIASES), 32
            ),  # Round 176: NaN-safe, same as timestamps
            sentiment=_truncate(_get(row, "CUSTOMER_PULSE__C", "pulse"), 32),
            # Round 175.2: CSV pulse rows carry snapshot_date so trajectory
            # is ordered by date, not insertion order.
            snapshot_date=_truncate(
                _get(row, "snapshot_date", "AS_OF_DATE", "PULSE_DATE_C"),
                64,
            ),
        )
        rec.is_open = bool(rec.status and "open" in rec.status.lower())
        out.append(rec)
    return out


def _parse_dispatch(path: Path) -> list[ParsedRecord]:
    """Route ``path`` to the right parser based on extension."""
    ext = path.suffix.lower()
    if ext == ".xlsx":
        return _parse_xlsx(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".csv":
        return _parse_csv(path)
    return []


# ---------------------------------------------------------------------------
# Customer canonicalization
# ---------------------------------------------------------------------------


def _normalize_customer_name(name: str) -> str:
    """Lowercase + collapse whitespace + strip non-alphanumerics so
    "Acme Corp", "  acme  corp ", and "ACME CORP" all map to the same
    key.  Used for the ``customers.name_norm`` UNIQUE constraint."""
    if not name:
        return ""
    body = unicodedata.normalize("NFKC", str(name)).strip().casefold()
    return re.sub(r"[^a-z0-9]+", "", body)


def _upsert_customer(
    cur: sqlite3.Cursor,
    name: str,
    manager: Optional[str],
    technology: Optional[str],
    *,
    seen_at: str,
    new_count: list[int],
) -> Optional[int]:
    """Insert or update a customer row.  Returns the row id, or
    ``None`` when ``name`` is empty after normalization (defensive)."""
    norm = _normalize_customer_name(name)
    if not norm:
        return None
    row = cur.execute(
        'SELECT "id", "first_seen", "last_seen", "occurrences", "manager", "technology" '
        'FROM "customers" WHERE "name_norm" = ?;',
        (norm,),
    ).fetchone()
    if row is None:
        cur.execute(
            'INSERT INTO "customers" '
            '("name", "name_norm", "manager", "technology", "first_seen", "last_seen", "occurrences") '
            'VALUES (?, ?, ?, ?, ?, ?, 1);',
            (
                _truncate(name, 200),
                norm,
                _truncate(manager, 200) or None,
                _truncate(technology, 100) or None,
                seen_at,
                seen_at,
            ),
        )
        new_count[0] += 1
        return int(cur.lastrowid)
    cust_id = int(row[0])
    cur.execute(
        'UPDATE "customers" SET '
        '"first_seen" = COALESCE(MIN("first_seen", ?), ?), '
        '"last_seen"  = COALESCE(MAX("last_seen", ?), ?), '
        '"manager"    = COALESCE(NULLIF(?, \'\'), "manager"), '
        '"technology" = COALESCE(NULLIF(?, \'\'), "technology"), '
        '"occurrences" = "occurrences" + 1 '
        'WHERE "id" = ?;',
        (
            seen_at, seen_at, seen_at, seen_at,
            _truncate(manager, 200) or "",
            _truncate(technology, 100) or "",
            cust_id,
        ),
    )
    return cust_id


# ---------------------------------------------------------------------------
# Chunk indexing (in-house BM25)
# ---------------------------------------------------------------------------


def _emit_chunks(text: str) -> Iterable[str]:
    """Split ``text`` into BM25 chunks.  Tries to align on sentence
    boundaries; falls back to fixed token windows when prose has no
    punctuation."""
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", str(text).strip())
    if not sentences:
        return []
    out: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for sentence in sentences:
        if not sentence:
            continue
        s_tokens = len(_tokenize(sentence))
        if buf_tokens + s_tokens > _MAX_CHUNK_TOKENS and buf:
            out.append(" ".join(buf).strip())
            buf, buf_tokens = [], 0
        buf.append(sentence)
        buf_tokens += s_tokens
        if buf_tokens >= _TARGET_CHUNK_TOKENS:
            out.append(" ".join(buf).strip())
            buf, buf_tokens = [], 0
    if buf:
        if buf_tokens >= _MIN_CHUNK_TOKENS or not out:
            out.append(" ".join(buf).strip())
        else:
            out[-1] = out[-1] + " " + " ".join(buf).strip()
    return [chunk for chunk in out if chunk]


def _index_chunk(
    cur: sqlite3.Cursor,
    *,
    text: str,
    technology: Optional[str],
    theme: Optional[str],
    customer_id: Optional[int],
    source_file_id: int,
    source_section: Optional[str],
    df_counts: dict[str, int],
) -> int:
    """Persist one playbook chunk and update ``df_counts`` in place.
    Returns 1 on success, 0 on no-op (text too short)."""
    tokens = _tokenize(text)
    if len(tokens) < _MIN_CHUNK_TOKENS:
        return 0
    cur.execute(
        'INSERT INTO "playbook_chunks" '
        '("technology", "theme", "customer_id", "text", "tokens_json", "doc_length", '
        ' "source_file_id", "source_section") '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?);',
        (
            _truncate(technology, 100),
            _truncate(theme, 64),
            customer_id,
            _truncate(text, _MAX_TEXT_BYTES),
            json.dumps(tokens, ensure_ascii=False),
            len(tokens),
            source_file_id,
            _truncate(source_section, 100),
        ),
    )
    for term in set(tokens):
        df_counts[term] = df_counts.get(term, 0) + 1
    return 1


def _flush_term_stats(
    cur: sqlite3.Cursor,
    df_delta: dict[str, int],
    *,
    chunks_added: int,
    total_doc_length_added: int,
) -> None:
    """Apply ``df_delta`` to ``term_stats`` and refresh the
    ``corpus_stats`` aggregates.  Pure SQL; no Python-side state."""
    for term, delta in df_delta.items():
        if delta <= 0:
            continue
        cur.execute(
            'INSERT INTO "term_stats" ("term", "df") VALUES (?, ?) '
            'ON CONFLICT("term") DO UPDATE SET "df" = "df" + excluded."df";',
            (term, delta),
        )
    if chunks_added <= 0:
        return
    row = cur.execute(
        'SELECT "doc_count", "avg_doc_len" FROM "corpus_stats" WHERE "id" = 1;'
    ).fetchone()
    if row is None:
        new_avg = (total_doc_length_added / chunks_added) if chunks_added else 0.0
        cur.execute(
            'INSERT INTO "corpus_stats" ("id", "doc_count", "avg_doc_len", "indexed_at") '
            'VALUES (1, ?, ?, ?);',
            (chunks_added, new_avg, _utc_now_iso()),
        )
        return
    prev_count = int(row[0] or 0)
    prev_avg = float(row[1] or 0.0)
    new_count = prev_count + chunks_added
    if new_count <= 0:
        new_avg = 0.0
    else:
        new_avg = ((prev_avg * prev_count) + total_doc_length_added) / new_count
    cur.execute(
        'UPDATE "corpus_stats" SET "doc_count" = ?, "avg_doc_len" = ?, "indexed_at" = ? '
        'WHERE "id" = 1;',
        (new_count, new_avg, _utc_now_iso()),
    )


def bm25_score(
    query_tokens: Sequence[str],
    chunk_tokens: Sequence[str],
    *,
    chunk_length: int,
    avg_doc_length: float,
    doc_count: int,
    df_lookup: dict[str, int],
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    """Compute the BM25 score for ``query_tokens`` against one chunk.
    Pure / deterministic so the retriever and the unit tests can pin
    expected scores."""
    if not query_tokens or not chunk_tokens or chunk_length <= 0 or doc_count <= 0:
        return 0.0
    tf: dict[str, int] = {}
    for tok in chunk_tokens:
        tf[tok] = tf.get(tok, 0) + 1
    score = 0.0
    avgdl = avg_doc_length if avg_doc_length > 0 else float(chunk_length)
    for q in query_tokens:
        f = tf.get(q, 0)
        if f == 0:
            continue
        df = df_lookup.get(q, 0)
        # Robertson-Sparck-Jones IDF, floored at 0.
        idf = math.log(1.0 + (doc_count - df + 0.5) / (df + 0.5))
        norm = 1.0 - b + b * (chunk_length / avgdl)
        score += idf * ((f * (k1 + 1.0)) / (f + k1 * norm))
    return score


# ---------------------------------------------------------------------------
# Index orchestration
# ---------------------------------------------------------------------------

_INDEX_LOCK = threading.RLock()


def open_corpus_db(path: Path | str, *, isolation_level: str | None = None) -> sqlite3.Connection:
    """Open the corpus database at ``path``, applying the schema if
    needed.  ``check_same_thread=False`` so background indexers and
    request handlers can share the connection (the indexer guards
    writes with ``_INDEX_LOCK``)."""
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=isolation_level,
        timeout=30.0,
    )
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    return conn


def rebuild_index(conn: sqlite3.Connection) -> None:
    """Drop every corpus table and re-apply the schema.  Used when
    ``schema_meta.version`` is older than ``SCHEMA_VERSION``."""
    with _INDEX_LOCK:
        cur = conn.cursor()
        # Round 17 / Phase F.1 -- ``apply_schema`` turns foreign-key
        # enforcement on, which makes ``DROP TABLE`` against a
        # populated parent table fail with ``IntegrityError`` on
        # SQLite.  Disable enforcement for the duration of the drop;
        # ``apply_schema`` re-enables it once the recreated tables
        # are in place.
        try:
            cur.execute('PRAGMA foreign_keys = OFF;')
        except sqlite3.DatabaseError:  # pragma: no cover - exotic FS
            pass
        try:
            for tbl in all_table_names():
                cur.execute(f'DROP TABLE IF EXISTS "{tbl}";')
            # term_stats / corpus_stats are dropped above;
            # apply_schema recreates them.
            apply_schema(conn)
        finally:
            try:
                cur.execute('PRAGMA foreign_keys = ON;')
            except sqlite3.DatabaseError:  # pragma: no cover - exotic FS
                pass


def index_folder(
    conn: sqlite3.Connection,
    root: Path | str | None,
    *,
    signal: Optional[IndexSignal] = None,
    rebuild: bool = False,
    files: Optional[list[CorpusFile]] = None,
) -> IndexStats:
    """Walk ``root`` and persist every parseable file's records.

    Returns an :class:`IndexStats` summary the caller logs / surfaces
    on the admin tile.  Idempotent: re-running with the same folder
    skips files whose ``(path, mtime, sha256)`` triple is unchanged.

    Round 17.1 + 102: callers that need a custom filename filter (for
    example, sidecar-gated generated-report sources) can pass a pre-built
    ``files`` list and ``root`` is used purely for logging / state tracking.
    """
    stats = IndexStats(started_at=_utc_now_iso())
    if conn is None:
        stats.errors.append("conn=None")
        stats.finished_at = _utc_now_iso()
        return stats

    with _INDEX_LOCK:
        if rebuild or needs_rebuild(conn):
            rebuild_index(conn)

        if files is None:
            files = enumerate_corpus_files(root, signal=signal)
        stats.files_seen = len(files)
        cur = conn.cursor()

        df_delta: dict[str, int] = {}
        chunks_added_total = 0
        total_doc_len_added = 0
        new_customer_counter = [0]

        for cf in files:
            if signal is not None and signal.stop_requested:
                break

            existing = cur.execute(
                'SELECT "id", "mtime_utc", "sha256", "schema_version", "parse_status" '
                'FROM "corpus_files" WHERE "path" = ?;',
                (str(cf.path),),
            ).fetchone()

            if existing is not None and (
                existing["sha256"] == cf.sha256
                and int(existing["schema_version"]) >= SCHEMA_VERSION
                and existing["parse_status"] == "ok"
            ):
                stats.files_skipped += 1
                # Round 62 / A1: closed-stream gate via _safe_log_info.
                _safe_log_info(
                    "Round 17.2 / corpus_indexer: skip ext=%s file=%s bytes=%d reason=cache_hit",
                    cf.extension or "",
                    cf.filename,
                    int(cf.size_bytes),
                )
                continue

            if cf.size_bytes > _MAX_PARSE_BYTES:
                stats.files_oversized += 1
                _record_file_state(
                    cur, cf,
                    parse_status="oversized",
                    parse_error=f"size={cf.size_bytes} bytes > cap",
                )
                # Round 62 / A1: closed-stream gate via _safe_log_info.
                _safe_log_info(
                    "Round 17.2 / corpus_indexer: skip ext=%s file=%s bytes=%d reason=oversized cap=%d",
                    cf.extension or "",
                    cf.filename,
                    int(cf.size_bytes),
                    int(_MAX_PARSE_BYTES),
                )
                continue

            try:
                records = _parse_dispatch(cf.path)
            except Exception as parse_err:  # noqa: BLE001 - defensive
                stats.files_failed += 1
                stats.errors.append(f"{cf.filename}: {type(parse_err).__name__}")
                _record_file_state(
                    cur, cf,
                    parse_status="error",
                    parse_error=_truncate(str(parse_err), 500),
                )
                logger.warning(
                    "Round 17.2 / corpus_indexer: failed ext=%s file=%s bytes=%d kind=%s",
                    cf.extension or "",
                    cf.filename,
                    int(cf.size_bytes),
                    type(parse_err).__name__,
                )
                continue

            if not records:
                # Runtime indexing remains tolerant, but release bakes inspect
                # this explicit state and fail closed. Previously a corrupt
                # XLSX/DOCX parser could return [] and be counted as a fully
                # parsed source, silently thinning the shipped corpus.
                stats.files_empty += 1

            file_id = _record_file_state(cur, cf, parse_status="ok", parse_error=None)
            stats.files_parsed += 1
            seen_at = _utc_now_iso()
            # Round 17.2: per-file INFO line so the operator can tail
            # the application log and verify the indexer is making
            # progress without any PII leaking into the message.
            # Round 62 / A1: closed-stream gate via _safe_log_info.
            _safe_log_info(
                "Round 17.2 / corpus_indexer: indexed ext=%s file=%s bytes=%d records=%d",
                cf.extension or "",
                cf.filename,
                int(cf.size_bytes),
                len(records),
            )

            for rec in records:
                cust_id = _upsert_customer(
                    cur, rec.customer_name, rec.manager, rec.technology,
                    seen_at=seen_at,
                    new_count=new_customer_counter,
                )
                if cust_id is None:
                    continue

                if rec.case_number or rec.severity or rec.status:
                    cur.execute(
                        'INSERT INTO "cases" '
                        '("customer_id", "case_number", "severity", "status", "is_open", '
                        ' "opened_at", "closed_at", "summary", "source_file_id", '
                        ' "first_seen", "last_seen") '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);',
                        (
                            cust_id,
                            _truncate(rec.case_number, 64),
                            _truncate(rec.severity, 32),
                            _truncate(rec.status, 32),
                            1 if rec.is_open else 0,
                            _truncate(rec.opened_at, 64),
                            _truncate(rec.closed_at, 64),
                            _truncate(rec.summary, _MAX_TEXT_BYTES),
                            file_id,
                            seen_at,
                            seen_at,
                        ),
                    )
                    stats.cases_added += 1

                if rec.barrier_subject or rec.barrier_description:
                    theme = detect_theme(
                        f"{rec.barrier_subject or ''} {rec.barrier_description or ''}"
                    )
                    barrier_id = cur.execute(
                        'INSERT INTO "barriers" '
                        '("customer_id", "technology", "theme", "status", "is_open", '
                        ' "first_seen", "last_seen", "occurrences", "source_file_id") '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?);',
                        (
                            cust_id,
                            _truncate(rec.technology, 100),
                            theme,
                            _clean_barrier_status(rec.barrier_status),
                            _barrier_open_flag(rec.barrier_status),  # Round 176
                            seen_at,
                            seen_at,
                            file_id,
                        ),
                    ).lastrowid
                    stats.barriers_added += 1

                    if rec.resolution_text:
                        cur.execute(
                            'INSERT INTO "resolutions" '
                            '("barrier_id", "method_text", "source_section", "source_file_id", '
                            ' "first_seen") '
                            'VALUES (?, ?, ?, ?, ?);',
                            (
                                int(barrier_id),
                                _truncate(rec.resolution_text, _MAX_TEXT_BYTES),
                                "barrier",
                                file_id,
                                seen_at,
                            ),
                        )
                        stats.resolutions_added += 1

                if rec.sentiment or rec.sentiment_evidence:
                    cur.execute(
                        'INSERT INTO "sentiments" '
                        '("customer_id", "snapshot_date", "score", "evidence_text", "source_file_id") '
                        'VALUES (?, ?, ?, ?, ?);',
                        (
                            cust_id,
                            _truncate(rec.snapshot_date, 64),
                            _coerce_pulse_score(rec.sentiment),
                            _truncate(rec.sentiment_evidence, _MAX_TEXT_BYTES),
                            file_id,
                        ),
                    )
                    stats.sentiments_added += 1

                # Build playbook chunks from any narrative text we have.
                narrative_blobs: list[tuple[str, str]] = []
                if rec.summary:
                    narrative_blobs.append(("summary", rec.summary))
                if rec.barrier_subject or rec.barrier_description:
                    narrative_blobs.append(
                        (
                            "barrier",
                            (rec.barrier_subject or "") + " " + (rec.barrier_description or ""),
                        )
                    )
                if rec.resolution_text:
                    narrative_blobs.append(("resolution", rec.resolution_text))
                if rec.sentiment_evidence:
                    narrative_blobs.append(("sentiment", rec.sentiment_evidence))

                for section, blob in narrative_blobs:
                    for chunk in _emit_chunks(blob):
                        chunk_added = _index_chunk(
                            cur,
                            text=chunk,
                            technology=rec.technology,
                            theme=detect_theme(chunk),
                            customer_id=cust_id,
                            source_file_id=file_id,
                            source_section=section,
                            df_counts=df_delta,
                        )
                        if chunk_added:
                            chunks_added_total += chunk_added
                            stats.chunks_added += chunk_added
                            total_doc_len_added += len(_tokenize(chunk))

        _flush_term_stats(
            cur,
            df_delta,
            chunks_added=chunks_added_total,
            total_doc_length_added=total_doc_len_added,
        )
        stats.customers_added = new_customer_counter[0]
        conn.commit()

    stats.finished_at = _utc_now_iso()
    # Round 62 / A1: closed-stream gate via _safe_log_info -- this
    # specific call site is the one that surfaced the leak in R62/A1
    # acceptance (pytest teardown coincided with a daemon-thread
    # index pass and the daemon's exit log emission tripped
    # Handler.handleError() -> stderr traceback).
    _safe_log_info(
        "Round 17 / corpus index pass complete: files_seen=%d parsed=%d skipped=%d "
        "failed=%d empty=%d chunks_added=%d schema=%d",
        stats.files_seen, stats.files_parsed, stats.files_skipped,
        stats.files_failed, stats.files_empty, stats.chunks_added, SCHEMA_VERSION,
    )
    return stats


def _record_file_state(
    cur: sqlite3.Cursor,
    cf: CorpusFile,
    *,
    parse_status: str,
    parse_error: Optional[str],
) -> int:
    """Insert / update the ``corpus_files`` row for ``cf``.  Returns
    the row id."""
    parsed_at = _utc_now_iso() if parse_status == "ok" else None
    row = cur.execute(
        'SELECT "id" FROM "corpus_files" WHERE "path" = ?;',
        (str(cf.path),),
    ).fetchone()
    if row is None:
        cur.execute(
            'INSERT INTO "corpus_files" '
            '("path", "filename", "size_bytes", "mtime_utc", "sha256", '
            ' "schema_kind", "schema_version", "parsed_at", "parse_status", "parse_error") '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);',
            (
                str(cf.path), cf.filename, cf.size_bytes, cf.mtime_utc, cf.sha256,
                cf.extension.lstrip("."), SCHEMA_VERSION, parsed_at, parse_status,
                _truncate(parse_error, 500),
            ),
        )
        return int(cur.lastrowid)
    cur.execute(
        'UPDATE "corpus_files" SET '
        '"size_bytes" = ?, "mtime_utc" = ?, "sha256" = ?, '
        '"schema_kind" = ?, "schema_version" = ?, "parsed_at" = ?, '
        '"parse_status" = ?, "parse_error" = ? '
        'WHERE "id" = ?;',
        (
            cf.size_bytes, cf.mtime_utc, cf.sha256,
            cf.extension.lstrip("."), SCHEMA_VERSION, parsed_at, parse_status,
            _truncate(parse_error, 500),
            int(row[0]),
        ),
    )
    return int(row[0])


# ---------------------------------------------------------------------------
# Helpers used by retriever / app_simple
# ---------------------------------------------------------------------------


def get_corpus_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a small status payload for the admin Corpus tile.

    Counts every populated table and reports the most recent
    ``parsed_at`` timestamp.  Never raises; returns minimal payload
    with an ``error`` key on failure."""
    if conn is None:
        return {"available": False, "reason": "no_connection"}
    try:
        cur = conn.cursor()
        files = cur.execute('SELECT COUNT(*) FROM "corpus_files"').fetchone()[0]
        parsed = cur.execute(
            'SELECT COUNT(*) FROM "corpus_files" WHERE "parse_status" = \'ok\''
        ).fetchone()[0]
        last_parsed = cur.execute(
            'SELECT MAX("parsed_at") FROM "corpus_files"'
        ).fetchone()[0]
        customers = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        cases = cur.execute('SELECT COUNT(*) FROM "cases"').fetchone()[0]
        chunks_row = cur.execute(
            'SELECT "doc_count", "indexed_at" FROM "corpus_stats" WHERE "id" = 1'
        ).fetchone()
        doc_count = int(chunks_row[0]) if chunks_row else 0
        indexed_at = chunks_row[1] if chunks_row else None
        return {
            "available": doc_count > 0,
            "files_total": int(files),
            "files_parsed": int(parsed),
            "last_parsed_at": last_parsed,
            "indexed_at": indexed_at,
            "customers": int(customers),
            "cases": int(cases),
            "chunks": int(doc_count),
            "schema_version": SCHEMA_VERSION,
        }
    except sqlite3.DatabaseError as db_err:
        return {"available": False, "reason": "db_error", "error": str(db_err)}


def default_db_path() -> Path:
    """Resolve the default location of the encrypted-at-rest corpus
    database.  Mirrors the AdoptIQ convention of putting per-user
    state under ``~/Library/Application Support/AdoptIQ`` on macOS or
    ``%APPDATA%\\AdoptIQ`` on Windows.

    Tests can opt out by overriding the ``ADOPTIQ_KNOWLEDGE_DIR``
    environment variable."""
    override = os.environ.get("ADOPTIQ_KNOWLEDGE_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "AdoptIQ" / "knowledge"
    else:
        base = Path.home() / "Library" / "Application Support" / "AdoptIQ" / "knowledge"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0700 failed for %s: %s", base, chmod_err)
    return base / "corpus.db"


__all__ = [
    "BM25_B",
    "BM25_K1",
    "CorpusFile",
    "IndexSignal",
    "IndexStats",
    "ParsedRecord",
    "bm25_score",
    "default_db_path",
    "detect_theme",
    "enumerate_corpus_files",
    "enumerate_user_report_files",
    "get_corpus_status",
    "index_folder",
    "open_corpus_db",
    "rebuild_index",
    "tokenize",
]
