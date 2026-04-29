"""Round 44 / Phase 6 regression test.

Pin the compact + executive-intelligence Word source-citation paragraphs
so they NEVER render the raw Snowflake ``_C``-suffixed column tokens
that director-level audiences cannot interpret.  The audited Build-20
sites:

  - ``compact_report_formatter.py``: "Total Customers" metric
    backing -- pre-fix used ``fields=['BU_NAME', 'customer_name',
    'ACCOUNT_ID_C']``.
  - ``compact_report_formatter.py``: critical-renewal-concerns inline
    source claim -- pre-fix used ``fields=['customer_name',
    'ACCOUNT_ID_C']``.
  - ``executive_intelligence_formatter.py``: "Total Customers" metric
    backing -- pre-fix used ``fields=['BU_NAME', 'customer_name',
    'ACCOUNT_ID_C']``.
  - ``executive_intelligence_formatter.py``: severity provenance
    paragraph -- pre-fix used ``fields=['Severity', 'PULSE_RATING__C',
    'CREATEDDATE']``.

Round 44 / Phase 6 swaps these for director-friendly labels (Customer
Name, Account ID, Pulse Rating, Created Date).
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPACT = REPO_ROOT / "compact_report_formatter.py"
EI_FMT = REPO_ROOT / "executive_intelligence_formatter.py"


# Match a python literal-list of field labels that contains at least one
# _C-suffixed column token.  Allows arbitrary whitespace/quoting inside
# the brackets.  We only flag matches inside ``fields=[...]`` kwargs --
# the actual Source citation chrome -- not legitimate column-resolver
# code (``_first_avail(row, ['SEVERITY_C', ...])`` style).
_FIELDS_KW_C_SUFFIX_PATTERN = re.compile(
    r"fields\s*=\s*\[\s*[^\]]*?\b['\"][A-Z][A-Z0-9_]*_C['\"][^\]]*?\]",
    re.DOTALL,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_compact_total_customers_uses_friendly_field_labels() -> None:
    """The compact "Total Customers" metric backing must use friendly
    labels.  Pre-fix used ``["BU_NAME", "customer_name",
    "ACCOUNT_ID_C"]``."""

    src = _read(COMPACT)
    assert (
        'fields=["Customer Name", "Account ID"]' in src
    ), (
        "Round 44 / Phase 6: compact 'Total Customers' metric backing "
        "must use friendly fields=['Customer Name', 'Account ID'] "
        "instead of raw Snowflake _C-suffixed columns."
    )
    assert (
        'fields=["BU_NAME", "customer_name", "ACCOUNT_ID_C"]'
        not in src
    ), (
        "Round 44 / Phase 6: pre-fix raw fields=['BU_NAME', "
        "'customer_name', 'ACCOUNT_ID_C'] kwarg must not appear in "
        "compact_report_formatter.py."
    )


def test_compact_concerns_inline_source_uses_friendly_field_labels() -> None:
    """The critical-renewal-concerns inline source claim must use the
    friendly ``['Customer Name', 'Account ID']`` field list."""

    src = _read(COMPACT)
    assert (
        "fields=['Customer Name', 'Account ID']" in src
    ), (
        "Round 44 / Phase 6: compact concerns inline source claim must "
        "use friendly fields=['Customer Name', 'Account ID']."
    )
    assert (
        "fields=['customer_name', 'ACCOUNT_ID_C']"
        not in src
    ), (
        "Round 44 / Phase 6: pre-fix raw fields=['customer_name', "
        "'ACCOUNT_ID_C'] kwarg must not appear in "
        "compact_report_formatter.py."
    )


def test_ei_total_customers_uses_friendly_field_labels() -> None:
    """Same fix for the executive-intelligence formatter Total
    Customers metric backing."""

    src = _read(EI_FMT)
    assert (
        'fields=["Customer Name", "Account ID"]' in src
    ), (
        "Round 44 / Phase 6: executive_intelligence_formatter 'Total "
        "Customers' metric backing must use friendly "
        "fields=['Customer Name', 'Account ID']."
    )
    assert (
        'fields=["BU_NAME", "customer_name", "ACCOUNT_ID_C"]'
        not in src
    ), (
        "Round 44 / Phase 6: pre-fix raw fields=['BU_NAME', "
        "'customer_name', 'ACCOUNT_ID_C'] kwarg must not appear in "
        "executive_intelligence_formatter.py."
    )


def test_ei_severity_provenance_uses_friendly_field_labels() -> None:
    """The severity provenance paragraph must use friendly labels
    ``['Severity', 'Pulse Rating', 'Created Date']``."""

    src = _read(EI_FMT)
    assert (
        "fields=['Severity', 'Pulse Rating', 'Created Date']" in src
    ), (
        "Round 44 / Phase 6: executive_intelligence_formatter severity "
        "provenance must use friendly fields=['Severity', 'Pulse "
        "Rating', 'Created Date']."
    )
    assert (
        "fields=['Severity', 'PULSE_RATING__C', 'CREATEDDATE']"
        not in src
    ), (
        "Round 44 / Phase 6: pre-fix raw fields=['Severity', "
        "'PULSE_RATING__C', 'CREATEDDATE'] kwarg must not appear in "
        "executive_intelligence_formatter.py."
    )


def test_no_remaining_C_suffix_fields_kwargs_in_compact_formatters() -> None:
    """Defense-in-depth scan across both compact-path formatters.  No
    ``fields=[..., '*_C', ...]`` source-citation kwarg should survive."""

    for path in (COMPACT, EI_FMT):
        src = _read(path)
        matches = _FIELDS_KW_C_SUFFIX_PATTERN.findall(src)
        assert not matches, (
            f"Round 44 / Phase 6: no fields=[..., '<RAW_C_COLUMN>', "
            f"...] source-citation kwarg should appear in {path.name}. "
            f"Found:\n  " + "\n  ".join(matches)
        )
