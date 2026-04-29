"""Round 44 / Phase 5 regression test.

Pin the renewal Word source-citation italics so they NEVER render the
raw Snowflake ``_C``-suffixed column tokens that director-level
audiences cannot interpret.  The two audited Build-20 sites:

  - ``app_simple.py:9981`` (Adoption Barriers italics):
    ``[Field(s): SEVERITY_C, AB_STATUS_C, CREATED_DATE/CLOSED_DATE; ...]``
  - ``app_simple.py:10417`` (Customer Pulse italics):
    ``[Field(s): PULSE_RATING__C, COMMENTS__C, CREATED_DATE/CLOSED_DATE; ...]``

Round 44 / Phase 5 replaces the raw ``_C`` columns with director-
friendly labels (Severity, Status, Created/Closed dates, Pulse
Rating, Comments) while preserving the citation chrome.

The underlying Snowflake schema is still documented in
``QUALITY_AUDIT.md`` and the ``leader_report_generator._fetch_*``
helpers for the ops audience.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"


def _read() -> str:
    return APP_SIMPLE.read_text(encoding="utf-8")


# Match a "Field(s): ..." citation that contains at least one _C-suffixed
# column token.  The pattern allows arbitrary field-list separators and
# whitespace so it survives small wording changes.
_C_SUFFIX_FIELDS_PATTERN = re.compile(
    r"\[Field\(s\):[^\]]*?\b[A-Z][A-Z0-9_]*_C\b[^\]]*?\]",
    re.DOTALL,
)


def test_renewal_ab_italic_uses_friendly_field_labels() -> None:
    """The renewal AB italic at ``app_simple.py:9981`` must use the
    friendly labels ``Severity, Status, Created/Closed dates``."""

    src = _read()
    assert (
        "[Field(s): Severity, Status, Created/Closed dates;" in src
    ), (
        "Round 44 / Phase 5: renewal AB italic must use friendly "
        "labels (Severity, Status, Created/Closed dates) instead of "
        "raw Snowflake _C-suffixed columns."
    )
    # Defense in depth: the pre-fix raw substring must not appear.
    assert (
        "[Field(s): SEVERITY_C, AB_STATUS_C, CREATED_DATE/CLOSED_DATE;"
        not in src
    ), (
        "Round 44 / Phase 5: pre-fix raw 'SEVERITY_C, AB_STATUS_C' "
        "field-list must not appear in the renewal AB italic."
    )


def test_renewal_cp_italic_uses_friendly_field_labels() -> None:
    """The renewal CP italic at ``app_simple.py:10417`` must use the
    friendly labels ``Pulse Rating, Comments, Created/Closed dates``."""

    src = _read()
    assert (
        "[Field(s): Pulse Rating, Comments, Created/Closed dates;" in src
    ), (
        "Round 44 / Phase 5: renewal CP italic must use friendly "
        "labels (Pulse Rating, Comments, Created/Closed dates) "
        "instead of raw Snowflake _C-suffixed columns."
    )
    # Defense in depth.
    assert (
        "[Field(s): PULSE_RATING__C, COMMENTS__C, "
        "CREATED_DATE/CLOSED_DATE;"
        not in src
    ), (
        "Round 44 / Phase 5: pre-fix raw 'PULSE_RATING__C, "
        "COMMENTS__C' field-list must not appear in the renewal CP "
        "italic."
    )


def test_no_remaining_C_suffix_field_lists_in_renewal_italics() -> None:
    """Defense-in-depth scan: no ``[Field(s): ... XXX_C ... ]`` or
    ``[Field(s): ... XXX__C ... ]`` substring should appear ANYWHERE in
    ``app_simple.py``.  Existing legitimate uses of ``_C``-suffixed
    columns in code (DataFrame access, column resolution, etc.) are
    fine -- only the *citation chrome* needs friendlying."""

    src = _read()
    matches = _C_SUFFIX_FIELDS_PATTERN.findall(src)
    assert not matches, (
        "Round 44 / Phase 5: no [Field(s): ... <RAW_C_COLUMN> ... ] "
        "citation chrome should appear in app_simple.py.  Found:\n  "
        + "\n  ".join(matches)
    )
