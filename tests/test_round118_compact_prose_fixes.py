"""Round 118 / Build 87 -- Compact report prose-quality fixes.

The Build 86 Compact acceptance audit (All_Managers / All Contact Center)
surfaced three genuine, model-independent regressions in the Compact
report.  All three are pinned here:

F1  Risk Summary band vocabulary: the lone "Medium Risk (band)" label in
    the executive dashboard disagreed with the canonical MODERATE
    vocabulary the rest of the Compact narrative + the Renewal report
    already use (R67/B1 cross-format parity).

F2  TAC Lifecycle Snapshot triplication: case # 700840277 (WINTRUST)
    rendered three byte-identical times because the R40 per-customer
    TAC -> subscription join fans a single case out to one row per matched
    subscription, and ``canonical_metrics.count_total_tac`` is
    ``_safe_len`` so the inflated row set ALSO over-counted the
    "Total Support Cases" KPI.  ``_r118_dedup_tac_cases`` collapses on the
    case identifier with ``keep='first'`` (mirrors the R78/B2 Leader
    Action_Plans dedup-by-ID contract).

F3  429 envelope leak: R112 stripped SECRETS from the upstream 429 body
    but left the raw machine JSON/dict (``Error code: 429 - {'error':
    {'message': ...}}``) intact, so the Compact "Non-AI fallback summary"
    (P26) dumped the envelope into customer-facing prose.
    ``_r69_sanitize_llm_error`` now humanizes the envelope down to its
    inner ``message`` while preserving the R112 secret-redaction +
    plain-prose contracts.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# F1 -- MODERATE band vocabulary (source-shape pin)
# ---------------------------------------------------------------------------


def test_r118_dashboard_uses_moderate_not_medium_band_label():
    """The executive-dashboard Risk Summary render path MUST emit
    ``Moderate Risk (band)`` (canonical MODERATE vocabulary) and MUST NOT
    emit the legacy ``Medium Risk (band)`` label."""
    src = (REPO_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert 'f"Moderate Risk (band): {format_number(_medium_band, decimals=0)}"' in src, (
        "Compact dashboard Risk Summary must render the MODERATE band label"
    )
    assert 'f"Medium Risk (band): {format_number(_medium_band, decimals=0)}"' not in src, (
        "legacy MEDIUM band label still present -- R67/B1 parity violated"
    )


def test_r118_moderate_label_source_marker_present():
    src = (REPO_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 118 / Build 87", label='src')


# ---------------------------------------------------------------------------
# F2 -- TAC case dedup helper
# ---------------------------------------------------------------------------


def _tac_frame(case_numbers):
    return pd.DataFrame(
        {
            "Case #": list(case_numbers),
            "customer_name": [f"CUST {i}" for i in range(len(case_numbers))],
            "Status": ["Closed"] * len(case_numbers),
        }
    )


def test_r118_dedup_collapses_triplicated_case():
    """THE regression: a case # repeated 3x (cross-subscription fan-out)
    collapses to a single row with ``keep='first'``."""
    from executive_intelligence_formatter import _r118_dedup_tac_cases

    df = _tac_frame(["700840277", "700840277", "700840277", "700822701"])
    out = _r118_dedup_tac_cases(df)
    assert len(out) == 2
    assert list(out["Case #"]) == ["700840277", "700822701"]
    # keep='first' preserves the earliest row's payload.
    assert out.iloc[0]["customer_name"] == "CUST 0"


def test_r118_dedup_keep_first_is_deterministic():
    """The retained row MUST be the first occurrence (deterministic against
    the upstream walk order)."""
    from executive_intelligence_formatter import _r118_dedup_tac_cases

    df = pd.DataFrame(
        {
            "Case #": ["A", "A", "B"],
            "Status": ["FIRST", "SECOND", "ONLY"],
        }
    )
    out = _r118_dedup_tac_cases(df)
    statuses = dict(zip(out["Case #"], out["Status"]))
    assert statuses["A"] == "FIRST"
    assert statuses["B"] == "ONLY"


def test_r118_dedup_count_parity_matches_table():
    """After dedup, ``count_total_tac`` MUST equal the unique-case row count
    so the KPI agrees with the rendered table (the Build 86 bug had the
    count inflated by the fan-out)."""
    import canonical_metrics as cm
    from executive_intelligence_formatter import _r118_dedup_tac_cases

    df = _tac_frame(["C1", "C1", "C2", "C3", "C3", "C3"])
    out = _r118_dedup_tac_cases(df)
    assert cm.count_total_tac(out) == 3


def test_r118_dedup_noop_without_case_id_column():
    """A frame with NO recognised case-id column MUST pass through
    unchanged (non-TAC callers are unaffected)."""
    from executive_intelligence_formatter import _r118_dedup_tac_cases

    df = pd.DataFrame({"foo": [1, 1, 2], "bar": ["x", "x", "y"]})
    out = _r118_dedup_tac_cases(df)
    assert len(out) == 3
    assert list(out["foo"]) == [1, 1, 2]


def test_r118_dedup_noop_on_empty_and_none():
    from executive_intelligence_formatter import _r118_dedup_tac_cases

    assert _r118_dedup_tac_cases(None) is None
    empty = pd.DataFrame()
    out = _r118_dedup_tac_cases(empty)
    assert out is empty or out.empty


def test_r118_dedup_uses_sr_number_when_case_hash_absent():
    """Fallback case-id columns are honored in priority order."""
    from executive_intelligence_formatter import _r118_dedup_tac_cases

    df = pd.DataFrame(
        {
            "SR Number": ["S1", "S1", "S2"],
            "Status": ["Open", "Open", "Closed"],
        }
    )
    out = _r118_dedup_tac_cases(df)
    assert len(out) == 2


def test_r118_dedup_wired_into_both_ingestion_points():
    """Both ``csone_norm`` creation sites MUST route through the dedup so
    the count KPI and the lifecycle table agree."""
    src = (REPO_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert src.count("_r118_dedup_tac_cases(csone_norm)") >= 2, (
        "dedup must be applied at BOTH csone_norm ingestion points"
    )


# ---------------------------------------------------------------------------
# F3 -- 429 / provider-envelope humanization
# ---------------------------------------------------------------------------


_BUILD86_429_ENVELOPE = (
    "ERROR: llm.rate_limit_429: Error code: 429 - {'error': {'message': "
    "'Requests have exceeded monthly throughput completion limit on your "
    "deployment: month starts on 1st', 'type': '', 'param': '', 'code': "
    "'RateLimitReached'}}"
)


def test_r118_sanitizer_collapses_429_envelope():
    """The raw provider JSON/dict envelope MUST NOT survive into prose,
    but the human signal (rate_limit_429 + the message) MUST."""
    from app_simple import _r69_sanitize_llm_error

    out = _r69_sanitize_llm_error(_BUILD86_429_ENVELOPE, max_len=500)
    # Machine envelope structure gone.
    assert "{'error'" not in out
    assert "'param'" not in out
    assert "'code':" not in out
    assert "{" not in out and "}" not in out
    # Human signal preserved.
    assert "rate_limit_429" in out
    assert "monthly throughput completion limit" in out


def test_r118_plain_prose_429_still_preserved_r112_contract():
    """R112 ``test_sanitizer_preserves_non_credential_text`` regression
    guard: a plain-prose 429 (no JSON envelope) MUST pass through
    untouched -- humanization is gated on the ``{`` envelope."""
    from app_simple import _r69_sanitize_llm_error

    raw = "ERROR: llm.rate_limit_429: Requests have exceeded monthly throughput"
    out = _r69_sanitize_llm_error(raw, max_len=500)
    assert "rate_limit_429" in out
    assert "monthly throughput" in out
    # No envelope was present, so the string is unchanged.
    assert out == raw


def test_r118_user_blob_redaction_marker_preserved():
    """R112 ``test_sanitizer_redacts_circuit_user_json_blob`` regression
    guard: the collapsed ``<redacted>`` marker MUST still appear (that
    case has no ``{`` after redaction, so humanization is a no-op)."""
    from app_simple import _r69_sanitize_llm_error

    raw = (
        "Error: 'user': '{\"appkey\": \"secret-key-xyz\", "
        "\"session_id\": \"sess-abc-123\", \"user\": \"\", "
        "\"prompt_truncate\": \"yes\"}' more text"
    )
    out = _r69_sanitize_llm_error(raw, max_len=500)
    assert "secret-key-xyz" not in out
    assert "sess-abc-123" not in out
    assert "<redacted>" in out


def test_r118_appkey_secret_still_redacted_even_with_humanize():
    """The 429-envelope-with-appkey case MUST still have the secret values
    stripped after humanization runs (redaction happens first)."""
    from app_simple import _r69_sanitize_llm_error

    raw = (
        "Error code: 429 - {'error': {'message': 'rate limited'}, "
        "'user': '{\"appkey\": \"egai-prd-cx-020054933-summarize-1753732881229\"}'}"
    )
    out = _r69_sanitize_llm_error(raw, max_len=500)
    assert "egai-prd-cx-020054933" not in out
    assert "1753732881229" not in out
    # The human message survives the collapse.
    assert "rate limited" in out


def test_r118_sanitizer_marker_present():
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 118 / Build 87: humanize provider error envelopes", label='src')
