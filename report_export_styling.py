"""Round 15 / Phase 2 -- Excel visual polish helpers.

The pre-Round-15 workbook shipped:

* every range as plain ``df.to_excel(...)`` writes (no native Excel
  Tables, no banded rows, no built-in filter UX);
* zero column-level number / date / percent formats;
* zero conditional formatting (the gold xlsx returned
  ``conditional formatting rules: 0`` on every sheet);
* ``Report_Info`` as the first tab, with no portfolio summary.

This module is the single place that converts a written xlsxwriter
sheet into a real Excel Table, applies per-column number formats
(currency / date / percent / integer), layers conditional-formatting
rules anchored to ``canonical_metrics.RISK_BAND_THRESHOLDS`` (no
magic numbers), and renders a workbook-level ``Summary`` tab pulled
from the canonical-metrics helpers.

Design contract
---------------

* Every helper is *defensive*. Visual polish must never fail the
  export -- if anything raises, the un-polished sheet still ships.
* Format strings are anchored to a small, explicit lookup so the
  rule set is easy to audit. New keywords are added to
  ``COLUMN_FORMAT_RULES`` instead of being sprinkled into the
  writer.
* Conditional formatting derives its thresholds from
  ``canonical_metrics.RISK_BAND_THRESHOLDS`` so the workbook can
  never drift away from the report narrative.
* The ``Summary`` sheet aggregates portfolio counts using the
  ``canonical_metrics`` helpers (``count_customers``, ``count_p1``,
  ``count_open_tac``, ``count_total_barriers`` etc.).  Any KPI we
  cannot compute defensively is rendered as ``"--"`` rather than
  faked.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format detection -- column-name-driven number / date / percent / integer
# ---------------------------------------------------------------------------

#: xlsxwriter format string for ARR / currency columns.  Negative
#: values render in red parentheses to surface debits / refunds.
_FORMAT_CURRENCY: str = '"$"#,##0;[Red]("$"#,##0)'

#: ISO date.  Pre-Round-15 the writer left dates as raw timestamps
#: (``2024-08-13 00:00:00``); ``yyyy-mm-dd`` collapses that to the
#: customer-facing form already used in the Word report.
_FORMAT_DATE: str = "yyyy-mm-dd"

#: One-decimal percent.  Pulled out so percent / ratio columns
#: don't read as ``0.736`` when they meant ``73.6%``.
_FORMAT_PERCENT: str = "0.0%"

#: Plain integer with thousands separator (case counts, days, etc.).
_FORMAT_INTEGER: str = "#,##0"

#: One-decimal float for risk scores (0.0--10.0).
_FORMAT_FLOAT_1: str = "0.0"

#: Lookup tuple: each entry is ``(matcher, format_string)``. The
#: first hit wins, so order from most-specific to most-generic.
#: Matchers are *callables* that take the lower-cased column name and
#: return ``True`` if the format applies.  We keep them tiny and
#: explicit so a unit test can iterate every rule and assert it
#: matches the columns it's meant to match.
COLUMN_FORMAT_RULES: tuple[tuple[Callable[[str], bool], str], ...] = (
    # Risk score -- one decimal. Match BEFORE the generic numeric so
    # we don't accidentally render a score as currency.
    (lambda c: "risk_score" in c or c.endswith("risk_numeric"), _FORMAT_FLOAT_1),
    # Currency / ARR -- match common ARR / amount / revenue / value
    # column patterns.  Avoid matching ``risk_value`` (no dollar
    # column ever uses that name in this codebase).
    (
        lambda c: any(
            tok in c
            for tok in (
                "arr",
                "annual_contract_value",
                "annual_recurring_revenue",
                "amount",
                "revenue",
                "aov",
                "tcv",
            )
        ),
        _FORMAT_CURRENCY,
    ),
    # Percent / ratio columns
    (
        lambda c: c.endswith("_pct")
        or c.endswith("_percent")
        or c.endswith("rate")
        or "percentage" in c
        or "_ratio" in c,
        _FORMAT_PERCENT,
    ),
    # Date / time columns -- ``date``, ``_dt``, ``timestamp``,
    # ``opened`` / ``closed`` (which the CSOne export uses) plus the
    # ``modstamp`` / ``activity_date`` SF audit columns that survive
    # despite the denylist (they're caught by exact match upstream;
    # this is belt-and-braces in case a new sheet adds them).
    (
        lambda c: any(
            tok in c
            for tok in (
                "date",
                "_dt",
                "timestamp",
                "opened",
                "_open_",
                "closed_",
                "discovered",
                "published",
                "first_seen",
                "last_seen",
                "resolved_at",
            )
        ),
        _FORMAT_DATE,
    ),
    # Integer / count / age columns
    (
        lambda c: any(
            tok in c
            for tok in (
                "count_of_",
                "_count",
                "age_days",
                "open_age",
                "closed_age",
                "days_in_",
                "hold_days",
                "# of",
            )
        ),
        _FORMAT_INTEGER,
    ),
)

#: Columns whose values are *low-cardinality categoricals* used by
#: the conditional-formatting rules (severity, status).
_SEVERITY_TOKENS: tuple[str, ...] = (
    "severity",
    "severity_norm",
    "severity_c",
    "priority",
    "priority_norm",
    "case_priority_norm",
    "highest priority",
    "case_priority",
)

_STATUS_TOKENS: tuple[str, ...] = (
    "status",
    "status_norm",
    "case_status",
    "case_status_norm",
    "ab_status",
    "ab_status_c",
)

_RISK_TOKENS: tuple[str, ...] = (
    "risk_score",
    "risk_score_0_10",
    "risk_score_0_100",
    "risk_numeric",
)

_DAYS_OPEN_TOKENS: tuple[str, ...] = (
    "open_age_days",
    "age_days",
    "days_in_stage",
    "hold_days",
    "days_open",
    "open_age",
)


def _norm(col: object) -> str:
    """Lower-case the column name; defensive against non-strings."""
    if col is None:
        return ""
    try:
        return str(col).strip().lower()
    except Exception:
        return ""


def detect_column_format(col: object) -> Optional[str]:
    """Return the xlsxwriter ``num_format`` string for ``col``, or
    ``None`` if no rule applies.  Never raises.
    """
    name = _norm(col)
    if not name:
        return None
    for matcher, fmt in COLUMN_FORMAT_RULES:
        try:
            if matcher(name):
                return fmt
        except Exception:
            continue
    return None


def _classify_column(col: object) -> str:
    """Classify ``col`` for conditional-formatting purposes.

    Returns one of: ``"risk"``, ``"severity"``, ``"status"``,
    ``"days_open"``, or ``""``.  Used by ``apply_conditional_formatting``
    to decide which rule (if any) to layer onto the column.
    """
    name = _norm(col)
    if not name:
        return ""
    # Risk takes precedence -- it's the most specific signal.
    if any(tok == name or tok in name for tok in _RISK_TOKENS):
        return "risk"
    # Days-open uses ``in`` so SF-suffixed columns like ``DAYS_IN_STAGE_C``
    # still classify (they end with ``_c``, not ``days_in_stage``).
    if any(tok == name or tok in name for tok in _DAYS_OPEN_TOKENS):
        return "days_open"
    if any(tok == name or tok in name for tok in _SEVERITY_TOKENS):
        return "severity"
    if any(tok == name or tok in name for tok in _STATUS_TOKENS):
        return "status"
    return ""


# ---------------------------------------------------------------------------
# Conditional-formatting rule builders
# ---------------------------------------------------------------------------

#: Cisco-portfolio palette used everywhere else in the report.  Kept
#: as a single private dict so tests can pin the exact hex values.
_PALETTE: Mapping[str, str] = {
    "green": "#28B463",
    "yellow": "#FFB81C",
    "orange": "#F39C12",
    "red": "#E74C3C",
    "grey": "#95A5A6",
    "white": "#FFFFFF",
    "black": "#000000",
}


def _risk_threshold_high_0_to_10() -> float:
    """Mid-point for the 3-color risk scale, derived from the
    canonical 0-100 ``HIGH`` band cutoff.  Falls back to 5.5 if
    ``risk_scoring`` cannot be imported (matches the
    ``canonical_metrics`` default in that case).
    """
    try:
        from risk_scoring import RISK_BAND_THRESHOLDS

        return float(RISK_BAND_THRESHOLDS["HIGH"]) / 10.0
    except Exception:
        return 5.5


def _risk_threshold_medium_0_to_10() -> float:
    """Lower mid-point (``MEDIUM`` band cutoff) used as the green->
    yellow transition on the 3-color scale.  Falls back to 3.5.
    """
    try:
        from risk_scoring import RISK_BAND_THRESHOLDS

        return float(RISK_BAND_THRESHOLDS["MEDIUM"]) / 10.0
    except Exception:
        return 3.5


def _excel_col_letter(idx: int) -> str:
    """0-indexed column index -> Excel letter (A, B, ..., AA, AB)."""
    if idx < 0:
        raise ValueError(f"Column index must be >= 0; got {idx!r}")
    letters = ""
    n = idx
    while True:
        letters = chr(ord("A") + (n % 26)) + letters
        n = (n // 26) - 1
        if n < 0:
            break
    return letters


def _data_range(col_idx: int, n_rows: int, startrow: int = 0) -> str:
    """``A2:A101``-style data-row range for column ``col_idx`` over
    ``n_rows`` data rows.

    Round 16 / Phase 5.1 -- ``startrow`` is the 0-indexed row where the
    *header* sits (matching the ``pd.DataFrame.to_excel`` ``startrow``
    parameter).  Default ``0`` preserves the historical behavior:
    header at row 0 -> data at rows 1..n -> Excel range ``X2:X(n+1)``.
    Callers that emit a merged-title row at row 0 and pass
    ``startrow=1`` to ``to_excel`` get the corresponding shift here so
    conditional rules anchor to the actual data, not to the title row.
    """
    letter = _excel_col_letter(col_idx)
    sr = max(0, int(startrow))
    return f"{letter}{sr + 2}:{letter}{sr + n_rows + 1}"


def _add_risk_3_color_scale(
    workbook: Any, worksheet: Any, col_idx: int, n_rows: int, startrow: int = 0
) -> None:
    """Risk score 0-10: green at MEDIUM/2, yellow at MEDIUM, red at
    HIGH.  Anchored to ``RISK_BAND_THRESHOLDS`` so the workbook
    never disagrees with the narrative classifier.
    """
    high = _risk_threshold_high_0_to_10()
    medium = _risk_threshold_medium_0_to_10()
    rng = _data_range(col_idx, n_rows, startrow)
    worksheet.conditional_format(
        rng,
        {
            "type": "3_color_scale",
            "min_type": "num",
            "min_value": max(0.0, medium / 2.0),
            "min_color": _PALETTE["green"],
            "mid_type": "num",
            "mid_value": medium,
            "mid_color": _PALETTE["yellow"],
            "max_type": "num",
            "max_value": high,
            "max_color": _PALETTE["red"],
        },
    )


def _add_severity_bands(
    workbook: Any, worksheet: Any, col_idx: int, n_rows: int, startrow: int = 0
) -> None:
    """Severity / priority column: P1/Critical=red, P2/High=orange,
    P3/Medium=yellow, P4/Low=grey.  Uses ``text contains`` rules so
    we match both ``P1`` / ``P-1`` / ``Critical`` variants.
    """
    rng = _data_range(col_idx, n_rows, startrow)
    rules = (
        # (pattern, fill_color)
        ("Critical", _PALETTE["red"]),
        ("P1", _PALETTE["red"]),
        ("High", _PALETTE["orange"]),
        ("P2", _PALETTE["orange"]),
        ("Medium", _PALETTE["yellow"]),
        ("P3", _PALETTE["yellow"]),
        ("Low", _PALETTE["grey"]),
        ("P4", _PALETTE["grey"]),
    )
    for needle, color in rules:
        try:
            fmt = workbook.add_format(
                {"bg_color": color, "font_color": _PALETTE["black"]}
            )
        except Exception:
            continue
        try:
            worksheet.conditional_format(
                rng,
                {
                    "type": "text",
                    "criteria": "containing",
                    "value": needle,
                    "format": fmt,
                },
            )
        except Exception:
            continue


def _add_status_bands(
    workbook: Any, worksheet: Any, col_idx: int, n_rows: int, startrow: int = 0
) -> None:
    """Status column: closed/resolved=grey, open/in-progress=yellow,
    escalated/at-risk=red.  Anything else stays uncolored.
    """
    rng = _data_range(col_idx, n_rows, startrow)
    rules = (
        # red (escalated / at-risk first so it overrides "open")
        ("Escalated", _PALETTE["red"]),
        ("At Risk", _PALETTE["red"]),
        ("at_risk", _PALETTE["red"]),
        ("Blocked", _PALETTE["red"]),
        # grey -- closed states
        ("Closed", _PALETTE["grey"]),
        ("Resolved", _PALETTE["grey"]),
        ("Cancelled", _PALETTE["grey"]),
        ("closed", _PALETTE["grey"]),
        # yellow -- open states
        ("Open", _PALETTE["yellow"]),
        ("In Progress", _PALETTE["yellow"]),
        ("Pending", _PALETTE["yellow"]),
        ("Waiting", _PALETTE["yellow"]),
        ("open", _PALETTE["yellow"]),
    )
    for needle, color in rules:
        try:
            fmt = workbook.add_format(
                {"bg_color": color, "font_color": _PALETTE["black"]}
            )
        except Exception:
            continue
        try:
            worksheet.conditional_format(
                rng,
                {
                    "type": "text",
                    "criteria": "containing",
                    "value": needle,
                    "format": fmt,
                },
            )
        except Exception:
            continue


def _add_days_open_data_bar(
    workbook: Any, worksheet: Any, col_idx: int, n_rows: int, startrow: int = 0
) -> None:
    """Days-open column: amber data bar.  Doesn't replace cell
    formatting; layers an inline bar so a 90-day-old barrier is
    visually distinct from a 5-day-old one.
    """
    rng = _data_range(col_idx, n_rows, startrow)
    try:
        worksheet.conditional_format(
            rng,
            {
                "type": "data_bar",
                "bar_color": _PALETTE["orange"],
                "bar_only": False,
            },
        )
    except Exception:
        pass


def apply_conditional_formatting(
    workbook: Any,
    worksheet: Any,
    df_columns: Sequence[object],
    n_rows: int,
    startrow: int = 0,
) -> dict[str, list[int]]:
    """Apply Round-15 conditional-formatting rules to ``worksheet``.

    Returns a dict mapping rule kind -> list of column indices the
    rule was applied to (handy for tests).  Never raises; missing
    rules just don't get applied.

    Round 16 / Phase 5.1 -- ``startrow`` matches the
    ``DataFrame.to_excel`` ``startrow`` (0-indexed header row); rules
    anchor to the actual data range, not to a merged title row.
    """
    applied: dict[str, list[int]] = {
        "risk": [],
        "severity": [],
        "status": [],
        "days_open": [],
    }
    if worksheet is None or n_rows <= 0:
        return applied
    sr = max(0, int(startrow))
    for col_idx, col_name in enumerate(df_columns):
        kind = _classify_column(col_name)
        if not kind:
            continue
        try:
            if kind == "risk":
                _add_risk_3_color_scale(workbook, worksheet, col_idx, n_rows, sr)
            elif kind == "severity":
                _add_severity_bands(workbook, worksheet, col_idx, n_rows, sr)
            elif kind == "status":
                _add_status_bands(workbook, worksheet, col_idx, n_rows, sr)
            elif kind == "days_open":
                _add_days_open_data_bar(workbook, worksheet, col_idx, n_rows, sr)
            else:
                continue
            applied[kind].append(col_idx)
        except Exception as err:  # pragma: no cover - defensive only
            logger.debug("R15 conditional rule %s skipped on col %s: %s", kind, col_name, err)
    return applied


# ---------------------------------------------------------------------------
# Excel Table writer -- converts the written range to a real Table
# ---------------------------------------------------------------------------


_TABLE_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize_table_name(sheet_name: str, used: Optional[set[str]] = None) -> str:
    """Build an Excel-valid Table name.

    Excel rules: must start with a letter or underscore, no spaces,
    no special characters, <= 255 chars, and unique across the
    workbook.  We prefix with ``tbl_`` to keep the namespace
    distinct from any user-defined names.
    """
    raw = str(sheet_name) if sheet_name else "Sheet"
    cleaned = _TABLE_NAME_SAFE_RE.sub("_", raw)
    cleaned = cleaned.strip("_") or "Sheet"
    candidate = f"tbl_{cleaned}"[:255]
    if used is None:
        return candidate
    name = candidate
    suffix = 2
    while name in used:
        name = f"{candidate[:250]}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def apply_excel_polish(
    workbook: Any,
    worksheet: Any,
    df: Any,
    sheet_name: str,
    used_table_names: Optional[set[str]] = None,
    startrow: int = 0,
) -> dict[str, Any]:
    """Convert ``worksheet`` into a real Excel Table and apply
    Round-15 column formats + conditional formatting.

    Caller contract:
      * ``df`` is the DataFrame already written via ``df.to_excel(
        writer, sheet_name=sheet_name, index=False, startrow=startrow)``
        -- header at row ``startrow``, data at rows
        ``startrow + 1 .. startrow + n``.
      * ``worksheet`` is the xlsxwriter ``Worksheet`` object for
        ``sheet_name``.
      * ``workbook`` is the parent ``xlsxwriter.Workbook``.

    Round 16 / Phase 5.1 -- ``startrow`` (default ``0``, behavior
    preserving) lets callers that emit a merged title row at row 0 and
    write the dataframe with ``startrow=1`` keep the Excel-table
    bounds and conditional-format anchors in sync with the actual
    header position.  The historical ``startrow=0`` callers continue
    to work unchanged.

    Returns a dict describing what was applied (handy for tests
    and for the Round-15 verification log).
    """
    result: dict[str, Any] = {
        "sheet": sheet_name,
        "table_name": None,
        "table_added": False,
        "formats_applied": {},
        "conditional_rules": {},
        "startrow": int(startrow) if startrow is not None else 0,
    }
    if worksheet is None or df is None:
        return result
    try:
        n_rows = int(getattr(df, "shape", (0, 0))[0])
        n_cols = int(getattr(df, "shape", (0, 0))[1])
    except Exception:
        return result
    if n_cols <= 0:
        return result
    sr = max(0, int(startrow) if startrow is not None else 0)

    # Build per-column spec for the Excel Table.  Cache the format
    # objects so a 30-column workbook doesn't allocate 30 duplicate
    # ``add_format`` instances.
    columns_spec: list[dict[str, Any]] = []
    fmt_cache: dict[str, Any] = {}
    formats_by_col: dict[str, str] = {}
    seen_headers: set[str] = set()
    try:
        col_iter: Iterable[object] = list(df.columns)
    except Exception:
        return result
    for raw in col_iter:
        header = str(raw) if raw is not None else ""
        if not header:
            header = f"Column{len(columns_spec) + 1}"
        # xlsxwriter Tables require unique headers; suffix duplicates.
        unique = header
        suffix = 2
        while unique in seen_headers:
            unique = f"{header}_{suffix}"
            suffix += 1
        seen_headers.add(unique)
        spec: dict[str, Any] = {"header": unique}
        fmt_str = detect_column_format(raw)
        if fmt_str:
            if fmt_str not in fmt_cache:
                try:
                    fmt_cache[fmt_str] = workbook.add_format({"num_format": fmt_str})
                except Exception:
                    fmt_cache[fmt_str] = None
            obj = fmt_cache.get(fmt_str)
            if obj is not None:
                spec["format"] = obj
                formats_by_col[unique] = fmt_str
        columns_spec.append(spec)

    # Round 16 / Phase 5.1: header sits at row ``sr``; data rows at
    # ``sr + 1 .. sr + n_rows``; last data row index = ``sr + n_rows``.
    header_row = sr
    last_row = sr + n_rows
    last_col = n_cols - 1
    table_name = _sanitize_table_name(sheet_name, used_table_names)
    if last_row <= header_row:
        # Excel Tables require at least one data row; add a single
        # blank data row so we still get the banded-header style.
        try:
            worksheet.write_blank(header_row + 1, 0, None)
            last_row = header_row + 1
        except Exception:
            return result
    options: dict[str, Any] = {
        "name": table_name,
        "columns": columns_spec,
        "style": "Table Style Medium 9",
        "banded_rows": True,
        "first_column": False,
        "last_column": False,
    }
    try:
        worksheet.add_table(header_row, 0, last_row, last_col, options)
        result["table_added"] = True
        result["table_name"] = table_name
        result["formats_applied"] = formats_by_col
    except Exception as err:
        # If add_table fails (e.g. xlsxwriter unhappy with a header
        # name), don't bail out -- still apply per-column formats so
        # the workbook at least has currency / date display.
        logger.debug("R15 add_table failed on sheet %s: %s", sheet_name, err)
        try:
            for col_idx, spec in enumerate(columns_spec):
                obj = spec.get("format")
                if obj is None:
                    continue
                worksheet.set_column(col_idx, col_idx, None, obj)
        except Exception:
            pass

    cf = apply_conditional_formatting(
        workbook, worksheet, list(df.columns), n_rows, startrow=sr
    )
    result["conditional_rules"] = cf
    return result


# ---------------------------------------------------------------------------
# Summary sheet -- workbook KPIs sourced from canonical_metrics
# ---------------------------------------------------------------------------


def _safe_canonical_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Wrap a ``canonical_metrics`` call so an upstream import or
    runtime hiccup doesn't crash the Summary sheet.  Returns
    ``None`` on any exception.

    Round 15 / Phase 6.1: ``TypeError`` is logged at WARNING (not
    DEBUG) because it strictly indicates a *call-site* contract
    mismatch -- the canonical-metrics function exists but was
    invoked with the wrong arity, the wrong keywords, or a
    positional call against a keyword-only signature.  That class
    of bug was R15-015 (the ``count_customers`` positional call
    site that silently rendered ``"--"`` in every Summary tab for
    a full phase).  Surfacing the call-signature mismatch at
    WARNING makes the next instance immediately visible in the
    operator log without flipping every "empty frame returns
    ``None``" defensive path into noise.
    """
    try:
        return fn(*args, **kwargs)
    except TypeError as err:
        logger.warning(
            "Round 15 / Phase 6.1: canonical_metrics call signature mismatch: %s(%s) -> %s",
            getattr(fn, "__name__", fn),
            ", ".join(
                [type(a).__name__ for a in args]
                + [f"{k}={type(v).__name__}" for k, v in kwargs.items()]
            ),
            err,
        )
        return None
    except Exception as err:  # pragma: no cover - defensive only
        logger.debug("canonical_metrics call %s failed: %s", getattr(fn, "__name__", fn), err)
        return None


def _format_kpi(value: Any) -> str:
    """Render a KPI cell; ``None`` / unknown / non-finite -> ``"--"``.

    Round 15 / Phase 5.2: previously this helper called ``int(value)``
    on any float, which raises ``ValueError`` for ``float('nan')``
    *and* ``OverflowError`` for ``float('inf')`` / ``float('-inf')``.
    A canonical-metrics call that returned a NaN (e.g. an ARR sum
    over an empty frame, or a percentage where the denominator was
    zero) would therefore crash the entire Summary sheet write.
    Treat any non-finite float the same as ``None`` -- render it as
    ``"--"`` -- so the Summary tab stays defensive even when an
    upstream KPI degrades.
    """
    if value is None:
        return "--"
    if isinstance(value, bool):
        # Round 15 / Phase 5.2: ``bool`` is a subclass of ``int``;
        # render explicitly so a stray ``True`` doesn't surface as
        # ``"1"`` in a KPI cell that's meant to carry counts.
        return "Yes" if value else "No"
    if isinstance(value, float):
        if value != value:  # NaN check (NaN != NaN)
            return "--"
        if value in (float("inf"), float("-inf")):
            return "--"
        if value == int(value):
            return f"{int(value):,}"
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        return str(value)
    except Exception:
        return "--"


def build_summary_rows(
    sheets: Mapping[str, Any],
    csconsole_data: Optional[Mapping[str, Any]] = None,
    *,
    manager: Optional[str] = None,
    tech: Optional[str] = None,
    days: Optional[Any] = None,
    generated_at_utc_iso_z: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Build the (label, value) rows for the Summary sheet.

    Pulls every KPI from ``canonical_metrics`` so the workbook
    summary aligns with the Word report numbers.  Returns a list
    of ``(label, formatted_value)`` tuples in the order they should
    appear.
    """
    sheets = sheets or {}
    csconsole_data = csconsole_data or {}
    ab_df = sheets.get("AB_Detail_All")
    csone_df = sheets.get("CSOne_Detail_All")
    ext_bugs = sheets.get("External_Bugs")
    ext_incidents = sheets.get("External_Incidents")
    cs_pulse = csconsole_data.get("customer_pulse") if csconsole_data else None

    customers: Any = None
    total_barriers: Any = None
    critical_barriers: Any = None
    open_barriers: Any = None
    total_tac: Any = None
    p1_tac: Any = None
    open_tac: Any = None
    escalated: Any = None
    bems: Any = None
    open_action_plans: Any = None  # Round 62 / B

    try:
        import canonical_metrics as cm  # noqa: PLC0415 -- lazy import keeps
        # the styling module usable without canonical_metrics in tests
        # Round 15 / Phase 5.3: ``cm.count_customers`` is *keyword-only*
        # (signature is ``count_customers(*, ab_df=..., csone_df=...,
        # pulse_df=...)``).  The Phase-2 wiring passed the frames
        # positionally, which raised ``TypeError`` -- silently swallowed
        # by ``_safe_canonical_call`` -- so the Summary sheet has been
        # rendering "Customers in portfolio: --" since Round 15 / Phase 2
        # landed.  Pass the frames as keyword args so the canonical
        # count actually surfaces.
        #
        # Round 25 / Phase A: this ``(ab_df, csone_df, pulse_df)`` shape
        # is now the SINGLE canonical "displayed customer universe" for
        # both the Excel ``Summary`` sheet and the Compact Word headline
        # ``Total Customers`` tile.  Pre-Round 25 the Word path widened
        # the count via ``extra_frames`` (team subs, action plans,
        # success priorities, csconsole adoption barriers) which produced
        # ``49`` in the reference Brian Frazier / 90d report while this
        # row showed ``37``.  The Word headline now mirrors this call
        # exactly so any reader can manually reconcile the headline by
        # counting unique customers across the three detail sheets the
        # report actually displays (AB_Detail_All, CSOne_Detail_All,
        # CSConsole_Customer_Pulse).  Do NOT widen this call without
        # also widening the Compact Word headline -- the cross-format
        # parity invariant in
        # ``report_consistency.validate_report_consistency`` will fail.
        customers = _safe_canonical_call(
            cm.count_customers,
            ab_df=ab_df,
            csone_df=csone_df,
            pulse_df=cs_pulse,
        )
        total_barriers = _safe_canonical_call(cm.count_total_barriers, ab_df)
        critical_barriers = _safe_canonical_call(cm.count_critical_barriers, ab_df)
        open_barriers = _safe_canonical_call(cm.count_open_barriers, ab_df)
        total_tac = _safe_canonical_call(cm.count_total_tac, csone_df)
        p1_tac = _safe_canonical_call(cm.count_p1, csone_df)
        open_tac = _safe_canonical_call(cm.count_open_tac, csone_df)
        escalated = _safe_canonical_call(cm.count_escalated, csone_df)
        bems = _safe_canonical_call(cm.count_bems, csone_df)
        # Round 62 / B: deterministic action-plan count derived from
        # AB_Detail_All so the comprehensive XLSX Summary sheet
        # surfaces a structured-data anchor for the action_plans KPI
        # regardless of LLM narrative phrasing variations.  Pre-R62
        # the comprehensive scenario carried action_plans only inside
        # the LLM narrative (R58 soak event: LLM emitted "0 Action
        # Plans" but the canonical KPI extractor missed it because no
        # XLSX cell carried the value).
        open_action_plans = _safe_canonical_call(cm.count_open_action_plans, ab_df)
    except Exception as err:  # pragma: no cover - defensive only
        logger.debug("Round 15 summary canonical import failed: %s", err)

    def _len_or_none(obj: Any) -> Optional[int]:
        if obj is None:
            return None
        try:
            n = len(obj)
            return int(n)
        except Exception:
            return None

    # Round 48 / F-COMP-AB-SUMMARY-VS-DETAIL-4: keep the canonical
    # "(total)" label that downstream golden-fixture tests pin (round
    # 15 / 16 / 19 / 21 / 23) and add a follow-up "(detail rows)" row
    # that explicitly exposes the AB_Detail_All row count plus the
    # multi-assignee delta.  The disclosure row is computed here (not
    # at render time) so the Excel ``Summary`` sheet, the Word leader
    # and comprehensive headlines, and any downstream reader who
    # parses the workbook all see the same string.  ``count_total_
    # barriers`` already deduplicates by the ``ID`` column (Round 25 /
    # Phase F), so the gap between ``total_barriers`` and
    # ``len(ab_df)`` is exactly the number of duplicate assignee rows
    # that fanned out from a single barrier.
    ab_detail_rows = _len_or_none(ab_df)
    ab_distinct = total_barriers if isinstance(total_barriers, int) else None
    if (
        isinstance(ab_distinct, int)
        and isinstance(ab_detail_rows, int)
        and ab_detail_rows > ab_distinct
    ):
        ab_delta = ab_detail_rows - ab_distinct
        ab_detail_label = (
            f"{ab_detail_rows:,} ({ab_delta:,} multi-assignee duplicate"
            f"{'s' if ab_delta != 1 else ''})"
        )
    elif isinstance(ab_detail_rows, int):
        ab_detail_label = f"{ab_detail_rows:,} (no multi-assignee duplicates)"
    else:
        ab_detail_label = "--"

    rows: list[tuple[str, str]] = []
    if generated_at_utc_iso_z:
        rows.append(("Report generated (UTC)", str(generated_at_utc_iso_z)))
    rows.extend(
        [
            ("Manager scope", str(manager) if manager else "All"),
            ("Technology scope", str(tech) if tech else "All"),
            ("Window (days)", _format_kpi(days) if days is not None else "--"),
            ("Customers in portfolio", _format_kpi(customers)),
            ("Adoption barriers (total)", _format_kpi(total_barriers)),
            ("Adoption barriers (detail rows)", ab_detail_label),
            ("Adoption barriers (critical)", _format_kpi(critical_barriers)),
            ("Adoption barriers (open)", _format_kpi(open_barriers)),
            # Round 62 / B: deterministic action_plans anchor for the
            # comprehensive scenario.  Inserted after the AB cluster
            # (because the count is derived from AB_Detail_All's
            # "Action Plan Title" column) and before the TAC cluster
            # so the Summary still reads as Customers -> Barriers ->
            # APs -> TAC -> Escalations -> BEMS.
            ("Action plans (open)", _format_kpi(open_action_plans)),
            ("TAC cases (total)", _format_kpi(total_tac)),
            ("TAC cases (P1)", _format_kpi(p1_tac)),
            ("TAC cases (open)", _format_kpi(open_tac)),
            ("Escalations", _format_kpi(escalated)),
            ("BEMS / break-fix", _format_kpi(bems)),
            ("External bugs (rows)", _format_kpi(_len_or_none(ext_bugs))),
            ("External incidents (rows)", _format_kpi(_len_or_none(ext_incidents))),
        ]
    )
    return rows


def write_summary_sheet(
    writer: Any,
    sheets: Mapping[str, Any],
    csconsole_data: Optional[Mapping[str, Any]] = None,
    *,
    manager: Optional[str] = None,
    tech: Optional[str] = None,
    days: Optional[Any] = None,
    generated_at_utc_iso_z: Optional[str] = None,
    sheet_name: str = "Summary",
) -> bool:
    """Write the Round-15 Summary sheet as the *first* tab.

    Returns ``True`` if the sheet was written, ``False`` otherwise
    (e.g. ``writer`` doesn't expose the xlsxwriter book interface).
    Defensive -- never raises.
    """
    try:
        import pandas as pd  # noqa: PLC0415 -- lazy keeps test imports light
    except Exception:
        return False
    try:
        rows = build_summary_rows(
            sheets,
            csconsole_data,
            manager=manager,
            tech=tech,
            days=days,
            generated_at_utc_iso_z=generated_at_utc_iso_z,
        )
    except Exception as err:  # pragma: no cover - defensive only
        logger.debug("Round 15 summary build failed: %s", err)
        return False
    try:
        df = pd.DataFrame(rows, columns=["Metric", "Value"])
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    except Exception as err:
        logger.debug("Round 15 summary write failed: %s", err)
        return False
    try:
        book = getattr(writer, "book", None)
        ws = writer.sheets.get(sheet_name) if hasattr(writer, "sheets") else None
        if book is not None and ws is not None:
            header_fmt = book.add_format(
                {
                    "bold": True,
                    "bg_color": "#0076CE",
                    "font_color": _PALETTE["white"],
                    "border": 1,
                    "align": "center",
                    "valign": "vcenter",
                }
            )
            ws.write(0, 0, "Metric", header_fmt)
            ws.write(0, 1, "Value", header_fmt)
            ws.set_column(0, 0, 36)
            ws.set_column(1, 1, 28)
            ws.freeze_panes(1, 0)
    except Exception as err:  # pragma: no cover - defensive only
        logger.debug("Round 15 summary header style failed: %s", err)
    return True


__all__ = [
    "COLUMN_FORMAT_RULES",
    "apply_conditional_formatting",
    "apply_excel_polish",
    "build_summary_rows",
    "detect_column_format",
    "write_summary_sheet",
]
