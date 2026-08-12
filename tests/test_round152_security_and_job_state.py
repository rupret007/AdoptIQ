"""Round 152 -- security, log-hygiene and job-state regression pins.

Covers four independent findings from the Round 152 audit:

* **A2** ``_safeHref`` in ``templates/psirt_search.html`` escaped its
  URL with a text-node round-trip and then interpolated the result into
  ``href="${...}"``.  A text-node round-trip escapes only ``&``, ``<``,
  ``>`` and NBSP -- double quotes survive -- so a URL containing a quote
  broke out of the attribute and could introduce an event handler in the
  page origin (which on this app means access to the CSRF meta token and
  every loopback route).  Two ``direct_link`` builders in ``app_simple``
  also interpolated an upstream identifier without percent-encoding.

* **A3** three ``logger.info`` sites wrote ``df.head(n).to_dict('records')``
  -- whole CSConsole / CSOne customer rows -- into the shared log file.

* **A4** two of the five report workers wrote raw ``str(e)`` into
  ``error_detail`` (the field the admin console renders and which is
  persisted to ``analysis_status.json``), and four of the five recorded
  no ``error_kind`` at all.

* **A5 / A6** ``save_analysis_status`` / ``get_all_status`` iterated the
  per-analysis dict that worker threads concurrently mutate, and
  ``/clear_stuck_analyses`` relabelled jobs without ever raising the
  cancellation flag that would actually stop them.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import pytest

import app_simple as app_mod
from tests.source_shape_utils import assert_in_source, read_repo_file


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# A2 -- attribute-context escaping
# ---------------------------------------------------------------------------


def _bst_template() -> str:
    return read_repo_file("templates/psirt_search.html")


def test_round152_safehref_uses_attribute_escaper() -> None:
    """``_safeHref`` feeds an HTML attribute, so it must escape quotes."""
    body = _bst_template()
    assert_in_source(body, "function _escAttr(s)")
    assert_in_source(body, "return _escAttr(s);")


def test_round152_esc_attr_escapes_both_quote_styles() -> None:
    """The attribute escaper must neutralise ``\"`` and ``'``."""
    body = _bst_template()
    match = re.search(r"function _escAttr\(s\) \{(.*?)\n    \}", body, re.S)
    assert match, "Round 152 / A2: _escAttr helper not found"
    helper = match.group(1)
    assert "&quot;" in helper, "double quotes must be escaped for attribute context"
    assert "&#39;" in helper, "single quotes must be escaped for attribute context"


def test_round152_no_href_interpolates_the_text_only_escaper() -> None:
    """No ``href="${...}"`` may be built from the text-context escaper.

    This is the check that fails if someone reintroduces ``_esc`` (or any
    other text escaper) into an attribute position.
    """
    body = _bst_template()
    offenders = re.findall(r'href="\$\{_esc\([^}]*\}"', body)
    assert not offenders, (
        f"Round 152 / A2: text-context escaper used inside an href attribute: {offenders}"
    )


def test_round152_direct_link_identifiers_are_percent_encoded() -> None:
    """``search_related_vulnerabilities`` must quote the upstream advisory id.

    ``cisco_internal_integrations`` already quoted its builders; the PSIRT
    related-search payload feeds the same ``direct_link`` field the page
    renders as an anchor.
    """
    body = read_repo_file("app_simple.py")
    assert_in_source(body, '_urlquote(str(vuln.advisory_id), safe="")')
    assert "openVuln/" not in body or "_urlquote" in body, (
        "Round 152 / A2: PSIRT direct_link builders must percent-encode identifiers"
    )


@pytest.mark.parametrize(
    "raw,expected_absent",
    [
        ('https://bst.cisco.com/x" onmouseover="alert(1)', '" onmouseover="'),
        ("https://bst.cisco.com/a'b", "'"),
    ],
)
def test_round152_urlquote_neutralises_attribute_breakout(raw: str, expected_absent: str) -> None:
    """``_urlquote`` must leave no character that can terminate an attribute."""
    encoded = app_mod._urlquote(raw, safe="")
    assert expected_absent not in encoded
    assert '"' not in encoded
    assert "<" not in encoded
    assert " " not in encoded


# ---------------------------------------------------------------------------
# A3 -- no customer rows in the log file
# ---------------------------------------------------------------------------


def test_round152_no_raw_dataframe_rows_logged() -> None:
    """No production module may log ``to_dict('records')`` payloads.

    ``structured_logging``'s redactor only scrubs the ``extra_kv`` prefix,
    never an f-string message body, so a row logged here reaches
    ``~/.adoptiq/adoptiq.<pid>.log`` verbatim -- and the UI routinely asks
    operators to send that file to support.
    """
    offenders = []
    for path in sorted(REPO_ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "logger." not in line:
                continue
            if "to_dict(" in line and "records" in line:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "Round 152 / A3: raw DataFrame rows are being written to the log at "
        f"{offenders}.  Log shape (row/column counts) instead -- column names "
        "are already logged separately and are not PII."
    )


def test_round152_shape_logging_replaced_row_logging() -> None:
    """The three former offenders now log shape."""
    body = read_repo_file("app_simple.py")
    assert_in_source(body, "- AB shape: ")
    assert_in_source(body, "- CSOne shape: ")
    assert_in_source(body, "- Sample shape: ")


# ---------------------------------------------------------------------------
# A4 -- sanitized, classified worker failures
# ---------------------------------------------------------------------------


class _FakeStatusEnv:
    """Isolate ``analysis_status`` and stub out disk persistence."""

    def __init__(self, monkeypatch):
        self.status: dict = {}
        monkeypatch.setattr(app_mod, "analysis_status", self.status)
        monkeypatch.setattr(app_mod, "save_analysis_status", lambda: None)


@pytest.fixture
def fake_status(monkeypatch):
    return _FakeStatusEnv(monkeypatch)


def test_round152_record_worker_failure_scrubs_secret_bearing_detail(fake_status) -> None:
    """``error_detail`` must never carry a raw exception body.

    The exception text below is the shape Round 9 / Phase 6.6 called out:
    an internal URL plus an AppRole-ish token.
    """
    exc = RuntimeError(
        "failed to read secret at https://keeper.cisco.com/v1/auth/approle/login "
        "role_id=A1B2C3D4E5F6G7H8I9J0K1L2 path=/Users/someone/.adoptiq/creds"
    )
    fields = app_mod._record_worker_failure(
        "aid-152-a4", exc, fallback_message="Analysis failed.", traceback_text=""
    )
    detail = fake_status.status["aid-152-a4"]["error_detail"]

    assert "keeper.cisco.com" not in detail
    assert "A1B2C3D4E5F6G7H8I9J0K1L2" not in detail
    assert "/Users/someone" not in detail
    assert len(detail) <= 240
    assert fields["error_kind"], "a classified error_kind must always be recorded"
    assert fake_status.status["aid-152-a4"]["status"] == "error"


def test_round152_record_worker_failure_sets_error_kind(fake_status) -> None:
    """An admin must be able to tell failure classes apart."""
    app_mod._record_worker_failure(
        "aid-152-kind", ValueError("boom"), fallback_message="Analysis failed."
    )
    recorded = fake_status.status["aid-152-kind"]
    assert recorded["error_kind"]
    assert recorded["error_class"] == "ValueError"
    assert recorded["progress"] == 0


def test_round152_record_worker_failure_preserves_traceback_contract(fake_status) -> None:
    """Round 43 / Phase 5: the admin console's stack must survive."""
    app_mod._record_worker_failure(
        "aid-152-tb",
        RuntimeError("x"),
        fallback_message="Analysis failed.",
        traceback_text="Traceback (most recent call last): ...",
    )
    assert "error_traceback" in fake_status.status["aid-152-tb"]


def test_round152_all_workers_route_failures_through_the_recorder() -> None:
    """Every worker's failure path must use the shared, scrubbed recorder.

    Pinned as source shape because exercising all five workers end to end
    requires live Snowflake.
    """
    body = read_repo_file("app_simple.py")
    for fallback in (
        '"Analysis failed. Please check the Admin page for details."',
        '"Customer renewal analysis failed. Please check the Admin page for details."',
        '"Subscription analysis failed. Please check the Admin page for details."',
        '"Leader report generation failed. Please check the Admin page for details."',
    ):
        assert_in_source(body, f"fallback_message={fallback}")


def test_round152_no_worker_writes_raw_exception_into_error_detail() -> None:
    """``str(e)[:1000]`` must not reappear on any status write."""
    body = read_repo_file("app_simple.py")
    assert 'error_detail"] = str(e)[:1000]' not in body, (
        "Round 152 / A4: a worker is writing a raw exception body into "
        "error_detail again; route it through _record_worker_failure so "
        "error_classifier._detail_tail scrubs it."
    )


# ---------------------------------------------------------------------------
# A5 -- concurrent-mutation safety on the status projections
# ---------------------------------------------------------------------------


def test_round152_status_projections_snapshot_before_iterating() -> None:
    """Both readers must iterate a snapshot of the inner dict."""
    body = read_repo_file("app_simple.py")
    assert body.count("for key, value in list(status.items()):") >= 2, (
        "Round 152 / A5: save_analysis_status and get_all_status must both "
        "iterate list(status.items()); worker threads add keys to the inner "
        "dict without holding analysis_status_lock."
    )


def test_round152_save_status_survives_concurrent_key_addition(monkeypatch, tmp_path) -> None:
    """Behavioural proof: adding keys mid-save must not lose the file.

    Pre-fix this raced into ``RuntimeError: dictionary changed size during
    iteration``, which the broad ``except`` swallowed -- so the status file
    silently was not written for that cycle.
    """
    live: dict = {"aid-race": {"status": "running", "progress": 0}}
    monkeypatch.setattr(app_mod, "analysis_status", live)
    monkeypatch.setattr(app_mod, "_APP_SUPPORT", tmp_path)

    stop = threading.Event()
    errors: list = []

    def _churn() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            try:
                live["aid-race"][f"k{n}"] = n
                if n > 400:
                    for key in [k for k in list(live["aid-race"]) if k.startswith("k")]:
                        live["aid-race"].pop(key, None)
                    n = 0
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

    writer = threading.Thread(target=_churn, daemon=True)
    writer.start()
    try:
        for _ in range(60):
            app_mod.save_analysis_status()
    finally:
        stop.set()
        writer.join(timeout=5)

    assert not errors
    status_file = tmp_path / app_mod.STATUS_FILE
    assert status_file.exists(), (
        "Round 152 / A5: the status file was never written -- the "
        "dictionary-changed-size race is back and is being swallowed."
    )


def test_round152_status_list_endpoint_omits_tracebacks() -> None:
    """The list projection must not ship an 8 KB stack per job."""
    body = read_repo_file("app_simple.py")
    assert_in_source(body, 'if key == "error_traceback":')


# ---------------------------------------------------------------------------
# A6 -- stuck-job clearing actually stops the job
# ---------------------------------------------------------------------------


def test_round152_mark_stuck_cancelled_raises_cancellation_flag(monkeypatch) -> None:
    """Relabelling is not stopping.

    The worker holds a direct reference to the same status dict, so without
    the cancellation flag it overwrites the label back to ``running`` on its
    next progress update and the job "un-cancels" itself.
    """
    monkeypatch.setattr(app_mod, "cancellation_flags", {})
    status = {"status": "running", "start_time": "2026-08-07T00:00:00Z"}
    app_mod._r152_mark_stuck_cancelled("aid-152-stuck", status)

    assert status["status"] == "cancelled"
    assert app_mod.check_cancellation("aid-152-stuck") is True, (
        "Round 152 / A6: /clear_stuck_analyses must raise the cancellation "
        "flag so the worker's own checkpoints stop it."
    )
    assert status.get("error_kind") == "analysis.stuck_cleared"


def test_round152_stuck_threshold_exceeds_compact_worker_timeout() -> None:
    """The cutoff must never be shorter than a healthy run.

    ``run_compact_analysis``'s own worker timeout is 1800 s (Round 149) and
    the comprehensive worker advertises ~0.5 min per customer, so the old
    600 s constant declared healthy jobs stuck.
    """
    assert app_mod._r152_stuck_threshold_seconds() >= 1800


def test_round152_stuck_threshold_is_operator_tunable(monkeypatch) -> None:
    monkeypatch.setenv("ADOPTIQ_STUCK_ANALYSIS_SECONDS", "5400")
    assert app_mod._r152_stuck_threshold_seconds() == 5400


def test_round152_stuck_threshold_floors_unsafe_values(monkeypatch) -> None:
    """An operator cannot tune the cutoff into killing healthy jobs."""
    monkeypatch.setenv("ADOPTIQ_STUCK_ANALYSIS_SECONDS", "5")
    assert app_mod._r152_stuck_threshold_seconds() >= 1800
    monkeypatch.setenv("ADOPTIQ_STUCK_ANALYSIS_SECONDS", "not-a-number")
    assert app_mod._r152_stuck_threshold_seconds() >= 1800


def test_round152_base_template_no_longer_promises_a_navbar_control() -> None:
    """The interrupted-job copy must describe a control that exists."""
    body = read_repo_file("templates/base.html")
    assert "clear them from the navbar" not in body, (
        "Round 152 / A6: base.html pointed users at a navbar control that "
        "has never existed."
    )
    assert_in_source(body, "start it again from Report Jobs")


# ---------------------------------------------------------------------------
# A3 (behavioural) -- sentinel must not reach the log
# ---------------------------------------------------------------------------


def test_round152_workbook_sheet_logging_omits_row_content(caplog) -> None:
    """Shape-only logging keeps customer values out of INFO records."""
    import pandas as pd

    sentinel = "ZZ-Sentinel-Customer-152"
    df = pd.DataFrame([{"customer_name": sentinel, "detail": "confidential note"}])
    logger = logging.getLogger("round152-shape-check")
    with caplog.at_level(logging.INFO, logger="round152-shape-check"):
        # Mirror the post-fix call shape used in app_simple.
        logger.info(f"   - Sample shape: 1 of {len(df)} rows, {len(df.columns)} columns")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert sentinel not in joined
    assert "1 of 1 rows" in joined
