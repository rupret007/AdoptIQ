"""Pin the contract that data-source truncations are surfaced to users.

The previous round set ``df.attrs['was_truncated']`` on the
``fetch_support_cases_snowflake`` return value, but the renewal code
path called the helper and never read the flag — so a renewal report
could happily say "X cases" while X was actually a hard fetch cap.

These tests do NOT exercise Snowflake (which is unavailable in CI).
Instead they verify the *contract*:
  1. ``fetch_support_cases_snowflake`` annotates the returned frame.
  2. The renewal path in ``app_simple`` reads that annotation and
     records a visible warning in the analysis status payload.
  3. The new ``COLLAB_ARR_CON_SKU`` and ``RENEWAL_DATA`` blocks set
     ``was_truncated`` / ``fetch_limit`` on their result dicts.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd

import adoptiq_backend


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_fetch_support_cases_snowflake_documents_truncation_attr() -> None:
    """The helper's docstring must promise the ``was_truncated`` annotation
    so consumers know to read it.
    """
    doc = inspect.getdoc(adoptiq_backend.fetch_support_cases_snowflake) or ""
    assert "was_truncated" in doc
    # The promised pattern: result equal to limit triggers the flag.
    assert "limit" in doc.lower()


def test_fetch_support_cases_normalize_empty_frame_carries_truncation_attrs() -> None:
    """When the Snowflake fetch happens and returns no rows (the
    ``_normalize_cases_df`` empty branch), the resulting frame must
    still carry ``was_truncated`` / ``fetch_limit`` so downstream
    consumers can branch on a single attribute consistently rather
    than guessing from len()/columns.

    We exercise this by source-grepping for the contract — invoking
    ``_normalize_cases_df`` would require monkey-patching internals,
    but the substring is small and unique so the contract test stays
    cheap and stable.
    """
    source = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The empty-branch contract appears explicitly in the helper.
    assert "empty.attrs['was_truncated'] = False" in source
    assert "empty.attrs['fetch_limit'] = limit" in source
    # And the populated path sets the flag based on len >= limit.
    assert "out.attrs['was_truncated'] = bool(len(out) >= limit)" in source


def test_renewal_path_reads_was_truncated_attr_from_app_simple() -> None:
    """The renewal code path in app_simple was missing a read of
    ``df.attrs['was_truncated']``; the round 2 fix added it.

    We verify by source-grepping (the renewal block is hundreds of lines
    long and resists easy unit-level invocation): the substring
    ``sf_cases.attrs.get('was_truncated'`` MUST appear in the source.
    """
    source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "sf_cases.attrs.get('was_truncated'" in source or \
           'sf_cases.attrs.get("was_truncated"' in source, (
        "Renewal path must read sf_cases.attrs['was_truncated']; "
        "without it, support-case fetches can silently under-report."
    )
    # Defense in depth: the message we surface must call out under-reporting
    # so the user understands the consequence rather than seeing a generic
    # warning.
    assert "may under-report" in source


def test_collab_arr_con_sku_block_emits_was_truncated() -> None:
    """The COLLAB_ARR_CON_SKU LIMIT 50 fetch must annotate its result
    so a renewal report can show a "data was truncated" banner.
    """
    source = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The block uses a named constant for the limit so the test isn't
    # tied to the literal "50".
    assert "_CONTRACT_FETCH_LIMIT" in source
    # The result dict must propagate the flag.
    assert "'was_truncated': _was_truncated" in source
    assert "'fetch_limit': _CONTRACT_FETCH_LIMIT" in source


def test_renewal_data_block_emits_was_truncated() -> None:
    """RENEWAL_DATA LIMIT 50 fetch: same contract as COLLAB_ARR_CON_SKU."""
    source = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "_RENEWAL_FETCH_LIMIT" in source
    assert "'fetch_limit': _RENEWAL_FETCH_LIMIT" in source


def test_simulated_truncated_frame_round_trips_attr_through_rename() -> None:
    """Sanity check: pandas preserves ``df.attrs`` through the column
    renames the renewal code performs on the returned frame BEFORE it
    reads the truncation flag.

    This is the read-order contract that the round 2 fix relies on:
    the renewal path reads ``sf_cases.attrs.get('was_truncated')``
    on the raw return value of ``fetch_support_cases_snowflake``,
    *before* doing any merges. So we only need ``rename`` to preserve
    attrs (merge attrs handling differs across pandas versions).
    """
    df = pd.DataFrame({"CASE_ID": ["C1"], "ACCOUNT_ID": ["A1"]})
    df.attrs["was_truncated"] = True
    df.attrs["fetch_limit"] = 50000

    renamed = df.rename(columns={"CASE_ID": "Case #"})
    assert renamed.attrs.get("was_truncated") is True, (
        "If this fails, the renewal warning will not fire even when "
        "fetch_support_cases_snowflake hits the cap."
    )
    assert renamed.attrs.get("fetch_limit") == 50000

    # Also: the original frame's attrs are preserved (no in-place mutation).
    assert df.attrs.get("was_truncated") is True
    assert df.attrs.get("fetch_limit") == 50000
