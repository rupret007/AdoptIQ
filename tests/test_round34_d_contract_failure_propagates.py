"""Round 34 / D -- fail-loud propagation when annotate_with_contract crashes.

The Build7 fail-loud data contract has TWO halves:

1. ``data_contracts.annotate_with_contract`` itself stamps
   ``df.attrs['fetch_error']`` + ``fetch_error_kind='schema_drift'``
   when a non-empty frame is missing required columns.
2. The four call sites in ``adoptiq_backend.py`` (CSOne load,
   team_subs, ARR empty, ARR data) wrap the annotate call so a
   crash inside the contract code can never break the load path.

Pre-Round-34 the second half had a hole: the wrappers
``except Exception: logger.debug(...)`` caught any failure and
silently dropped the schema_drift signal.  The "thin report"
symptom -- executive summary rendering almost nothing because a
critical dataframe lost its annotation -- followed.

Round 34 / D centralizes the wrappers in
``_safe_annotate_with_contract`` which:

* Logs at WARNING (not DEBUG) so operators see the failure.
* Stamps ``fetch_error`` + ``fetch_error_kind='contract_annotation_failure'``
  on the dataframe so the existing partial-data banner pipeline
  (analyze.html banner + Word "⚠ Partial Data Warning" + Excel
  Data_Unavailable) actually surfaces the loss.
* Never raises -- best-effort about the stamping itself.

These tests pin the contract by patching
``data_contracts.annotate_with_contract`` to raise and verifying
the helper does the right thing.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import logging

import pandas as pd

import adoptiq_backend as ab


def test_d_helper_stamps_fetch_error_when_annotate_raises(monkeypatch, caplog):
    """The fail-loud contract: when annotate_with_contract crashes,
    the dataframe must come back with fetch_error + the
    ``contract_annotation_failure`` kind so the partial-data banner
    pipeline picks it up."""

    def _explode(*a, **kw):
        raise RuntimeError("simulated annotate failure")

    # Patch the import inside the helper.
    monkeypatch.setattr(
        "data_contracts.annotate_with_contract",
        _explode,
        raising=True,
    )

    df = pd.DataFrame({"BU_NAME": ["Acme"], "AB_STATUS_C": ["Open"]})
    with caplog.at_level(logging.WARNING, logger="adoptiq_backend"):
        result = ab._safe_annotate_with_contract(
            df,
            dataset="adoption_barriers",
            source_label="test_source",
        )

    # Same df returned (in-place semantics).
    assert result is df
    # fetch_error + kind stamped.
    assert df.attrs.get("fetch_error_kind") == "contract_annotation_failure"
    assert "adoption_barriers" in df.attrs.get("fetch_error", "")
    # Operator visibility: WARNING-level log, not DEBUG.
    assert any(
        "Round 34 / D" in r.getMessage()
        and r.levelno >= logging.WARNING
        for r in caplog.records
    )


def test_d_helper_does_not_clobber_pre_existing_fetch_error(monkeypatch):
    """If the dataframe already carries a fetch_error (e.g., the
    fetcher already flagged a network failure), the contract-level
    stamp must NOT overwrite it -- the original failure is more
    informative for the operator."""

    def _explode(*a, **kw):
        raise RuntimeError("simulated annotate failure")

    monkeypatch.setattr(
        "data_contracts.annotate_with_contract",
        _explode,
        raising=True,
    )

    df = pd.DataFrame({"BU_NAME": ["Acme"]})
    df.attrs["fetch_error"] = "snowflake_timeout"
    df.attrs["fetch_error_kind"] = "timeout"
    df.attrs["fetch_error_dataset"] = "adoption_barriers"

    ab._safe_annotate_with_contract(
        df,
        dataset="adoption_barriers",
        source_label="test_source",
    )

    # Original fetch_error preserved.
    assert df.attrs["fetch_error"] == "snowflake_timeout"
    assert df.attrs["fetch_error_kind"] == "timeout"


def test_d_helper_succeeds_silently_on_happy_path(caplog):
    """A real (non-crashing) annotate call must NOT log at WARNING --
    we don't want to spam operators with a warning every time the
    contract is satisfied."""
    df = pd.DataFrame({
        "BU_NAME": ["Acme"],
        "ID": [1],
        "SUBJECT_C": ["Issue"],
        "AB_STATUS_C": ["Open"],
        "SEVERITY_C": ["High"],
    })
    with caplog.at_level(logging.WARNING, logger="adoptiq_backend"):
        ab._safe_annotate_with_contract(
            df,
            dataset="adoption_barriers",
            source_label="test_source",
        )
    # No fetch_error stamp on a successful annotate (frame is
    # contract-conformant).
    assert df.attrs.get("fetch_error_kind") != "contract_annotation_failure"
    # And no warning.
    assert not any(
        r.levelno >= logging.WARNING and "Round 34 / D" in r.getMessage()
        for r in caplog.records
    )


def test_d_helper_never_raises_even_when_attrs_assignment_fails(monkeypatch, caplog):
    """The wrapper is a load-path safety net -- it must NEVER raise.
    Even if both annotate AND the stamping fail, the load itself
    must complete."""

    def _explode(*a, **kw):
        raise RuntimeError("annotate exploded")

    monkeypatch.setattr(
        "data_contracts.annotate_with_contract",
        _explode,
        raising=True,
    )

    class _FakeDfNoAttrs:
        """Pandas-like object without .attrs to force the inner
        try/except in the helper to fall through."""

        def __init__(self):
            pass

    df = _FakeDfNoAttrs()
    # Must not raise.
    result = ab._safe_annotate_with_contract(
        df,  # type: ignore[arg-type]
        dataset="adoption_barriers",
        source_label="test_source",
    )
    assert result is df


def test_d_csone_loader_uses_fail_loud_helper():
    """Source-shape pin: the four call sites in adoptiq_backend.py
    must route through ``_safe_annotate_with_contract`` rather than
    the pre-Round-34 inline ``try/except logger.debug`` pattern.
    A future refactor that re-introduces the silent pattern will
    fail this test."""
    import inspect

    src = inspect.getsource(ab)
    # The fail-loud helper must exist and be called at least 4 times
    # (csone twice for tac_cases + bems_rows, team_subs, arr_empty,
    # arr_data).
    assert_in_source(src, "_safe_annotate_with_contract(", label='src')
    helper_calls = src.count("_safe_annotate_with_contract(")
    # 1 def line + at least 4 callsites = 5 occurrences.
    assert helper_calls >= 5, (
        f"expected helper to be called from at least 4 sites; "
        f"found {helper_calls - 1} calls"
    )
    # The pre-R34 silent pattern must NOT come back.
    assert "annotate_with_contract(csone) skipped" not in src
    assert "annotate_with_contract(subscriptions) skipped" not in src
    assert "annotate_with_contract(arr empty) skipped" not in src
    assert "annotate_with_contract(arr) skipped" not in src
