"""Round 152 -- decision-report clarity and disclosure regression pins.

Four findings, all in ``decision_report_delivery.build_concise_word_document``
-- which is now the single Word renderer for all eight report families
(Leader Team/Member/Customer, Comprehensive, Compact, Renewal portfolio,
Renewal customer, Subscription), so each one affected every report.

* **C1** the Data Coverage Warning table silently truncated at five rows.
  Every other truncated section in the document discloses its overflow;
  this is the one section whose entire purpose is honest disclosure.

* **C2** member rows do not sum to the team totals -- a record shared by
  two team members is attributed to both (DSM secondary attribution) while
  the team total counts it once.  Correct by design, but undisclosed in
  both the Word report and ``Metric_Lineage``, so a manager who added the
  column could not tell a design choice from a bug.  Measured on the
  offline fixture: team open/overdue 4/2 vs member sum 5/3.

* **C4** an empty account summary emitted a header-only grid under a
  "Ranked by canonical risk score descending" claim, while the adjacent
  section printed the correct empty-state sentence.  The team branch had
  always handled this; the account branch had no ``else``.

* **C5** withheld-value sentinels were substituted into the middle of noun
  phrases, producing "The selected scope covers Unavailable (Partial)
  customers" and "Known activity total: Unavailable (Incomplete coverage)
  distinct records".  The withholding is correct and is preserved; only
  the grammar changes.
"""

from __future__ import annotations

import pytest

import decision_report_delivery as delivery
from tests.test_round142_decision_report_delivery import (
    AS_OF,
    _fake_chart_renderer,
    _team_fixture,
)


def _facts(scope_type: str = "team", **overrides):
    """Build facts from the canonical shared team fixture.

    Reusing ``_team_fixture`` (rather than a hand-rolled bundle) keeps these
    assertions on the same source shape every other decision-report test
    uses, so a fixture change cannot silently diverge this file.
    """
    facts = delivery.build_report_facts(
        _team_fixture(),
        report_type="Leader",
        scope_type=scope_type,
        scope_value="Dana Manager team" if scope_type == "team" else "Acme Corporation",
        manager_name="Dana Manager",
        days=90,
        as_of=AS_OF,
    )
    facts.update(overrides)
    return facts


def _document(facts, monkeypatch):
    monkeypatch.setattr(delivery, "_render_chart_image", _fake_chart_renderer)
    return delivery.build_concise_word_document(facts)


def _body_text(document) -> str:
    return "\n".join(p.text for p in document.paragraphs)


# ---------------------------------------------------------------------------
# C1 -- coverage-warning truncation is disclosed
# ---------------------------------------------------------------------------


def test_round152_extra_coverage_warnings_are_disclosed(monkeypatch) -> None:
    """Seven warnings render five rows plus an explicit pointer."""
    facts = _facts()
    facts["partial_data_warnings"] = [
        {"dataset": f"source_{n}", "kind": "fetch_error", "detail": f"detail {n}"}
        for n in range(7)
    ]
    document = _document(facts, monkeypatch)

    warning_table = next(
        table
        for table in document.tables
        if [cell.text for cell in table.rows[0].cells] == ["Source", "Coverage", "What this means"]
    )
    assert len(warning_table.rows) - 1 == 5, "the visible warning table still caps at five rows"
    assert "2 additional coverage warning(s)" in _body_text(document)
    assert "Report_Info" in _body_text(document)
    assert "[Source: Report_Info Partial_Data_Warning rows]" in _body_text(document)


def test_round152_no_overflow_note_when_nothing_is_truncated(monkeypatch) -> None:
    """Do not add noise when every warning is already visible."""
    facts = _facts()
    facts["partial_data_warnings"] = [
        {"dataset": "source_a", "kind": "fetch_error", "detail": "detail"}
    ]
    document = _document(facts, monkeypatch)
    assert "additional coverage warning(s)" not in _body_text(document)


def test_round167_fixture_provenance_is_never_hidden_by_warning_overflow(
    monkeypatch,
) -> None:
    """The concise report must identify offline data even when warnings overflow."""
    facts = _facts()
    facts["partial_data_warnings"] = [
        {
            "dataset": f"scope_source_{n}",
            "kind": "tech_filter_scope_excluded",
            "detail": f"scope detail {n}",
        }
        for n in range(7)
    ] + [
        {
            "dataset": "Live source validation",
            "kind": "local_acceptance_fixture",
            "effect": "No live Cisco sources were queried.",
        }
    ]

    document = _document(facts, monkeypatch)
    warning_table = next(
        table
        for table in document.tables
        if [cell.text for cell in table.rows[0].cells]
        == ["Source", "Coverage", "What this means"]
    )
    visible_rows = [[cell.text for cell in row.cells] for row in warning_table.rows[1:]]

    assert visible_rows[0][0] == "Live source validation"
    assert visible_rows[0][1] == "Offline test data"
    assert len(visible_rows) == 5
    assert "3 additional coverage warning(s)" in _body_text(document)


@pytest.mark.parametrize("count,expected", [(6, "1 additional"), (12, "7 additional")])
def test_round152_overflow_count_is_exact(monkeypatch, count: int, expected: str) -> None:
    facts = _facts()
    facts["partial_data_warnings"] = [
        {"dataset": f"s{n}", "kind": "fetch_error", "detail": "d"} for n in range(count)
    ]
    assert expected in _body_text(_document(facts, monkeypatch))


# ---------------------------------------------------------------------------
# C2 -- shared attribution is disclosed in Word and in Metric_Lineage
# ---------------------------------------------------------------------------


def test_round152_member_table_discloses_shared_attribution(monkeypatch) -> None:
    facts = _facts("team")
    if not facts.get("member_summary"):
        pytest.skip("fixture produced no member rows")
    # Make the condition explicit: the current shared fixture has two roster
    # members but no account that is simultaneously attributed to both.
    # The disclosure belongs only when one retained record is genuinely
    # multi-attributed; otherwise it adds noise and can orphan the final note.
    facts["frames"]["action_plans"].loc[
        facts["frames"]["action_plans"].index[0],
        "Attributed_Team_Members",
    ] = "Alex Rivera; Morgan Lee"
    body = _body_text(_document(facts, monkeypatch))
    assert "attributed to each of them" in body
    assert "team total counts each record once" in body


def test_round167_single_member_scope_omits_irrelevant_shared_attribution(
    monkeypatch,
) -> None:
    facts = _facts("member")
    facts["scope_type"] = "team"
    body = _body_text(_document(facts, monkeypatch))
    assert "attributed to each of them" not in body
    assert "team total counts each record once" not in body


def test_round152_member_lineage_rows_carry_the_caveat() -> None:
    """The workbook must be auditable without reading the Word prose."""
    facts = _facts("team")
    if not facts.get("member_summary"):
        pytest.skip("fixture produced no member rows")
    lineage = facts["metric_lineage"]
    member_rows = [
        row
        for _, row in lineage.iterrows()
        if str(row["Metric_Key"]).startswith("summary.member.")
    ]
    assert member_rows, "expected summary.member.* lineage rows"
    for row in member_rows:
        assert "Shared records are attributed to every named team member" in str(row["Caveat"])


def test_round152_member_lineage_caveat_does_not_replace_withheld_note() -> None:
    """A withheld member metric must keep BOTH caveats."""
    facts = _facts("team")
    if not facts.get("member_summary"):
        pytest.skip("fixture produced no member rows")
    lineage = facts["metric_lineage"]
    withheld = [
        str(row["Caveat"])
        for _, row in lineage.iterrows()
        if str(row["Metric_Key"]).startswith("summary.member.")
        and "withheld" in str(row["Caveat"]).lower()
    ]
    for caveat in withheld:
        assert "Shared records are attributed" in caveat
        assert "withheld" in caveat.lower()


def test_round152_member_lineage_reference_stays_adjacent_to_the_table(monkeypatch) -> None:
    """Round 147 evidence contract: the source reference must follow the table.

    The Round 152 disclosure goes *after* the reference so this holds.
    """
    facts = _facts("team")
    if not facts.get("member_summary"):
        pytest.skip("fixture produced no member rows")
    document = _document(facts, monkeypatch)
    member_table = next(
        table
        for table in document.tables
        if [cell.text for cell in table.rows[0].cells]
        == [
            "Team member",
            "Customers",
            "Open AP",
            "Overdue AP",
            "Barriers",
            "TAC",
            "Manager intervention",
        ]
    )
    adjacent = "".join(member_table._tbl.getnext().itertext()).strip()  # noqa: SLF001
    assert adjacent.startswith("[Source: Source Data File → Metric_Lineage /")


# ---------------------------------------------------------------------------
# C4 -- empty account summary renders an empty state, not a header-only grid
# ---------------------------------------------------------------------------


def test_round152_empty_account_summary_renders_empty_state(monkeypatch) -> None:
    facts = _facts("customer")
    facts["account_summary"] = []
    facts["account_summary_omitted"] = 0
    document = _document(facts, monkeypatch)

    headers = [[cell.text for cell in table.rows[0].cells] for table in document.tables]
    account_header = [
        "Account",
        "Risk band",
        "Risk score",
        "Open AP",
        "Overdue AP",
        "Critical/high barriers",
        "TAC",
    ]
    assert account_header not in headers, (
        "Round 152 / C4: an empty account summary must not emit a header-only table."
    )
    assert "No account could be resolved to a canonical customer identity" in _body_text(document)


def test_round152_empty_account_summary_matches_the_contract(monkeypatch) -> None:
    """``validate_word_semantics`` must accept the new empty-state shape."""
    facts = _facts("customer")
    facts["account_summary"] = []
    facts["account_summary_omitted"] = 0
    document = _document(facts, monkeypatch)
    result = delivery.validate_word_semantics(facts, document)
    assert result["ok"], result["errors"]


def test_round152_populated_account_summary_still_renders(monkeypatch) -> None:
    """The non-empty path is unchanged."""
    facts = _facts("customer")
    if not facts.get("account_summary"):
        pytest.skip("fixture produced no account rows")
    document = _document(facts, monkeypatch)
    headers = [[cell.text for cell in table.rows[0].cells] for table in document.tables]
    assert [
        "Account",
        "Risk band",
        "Risk score",
        "Open AP",
        "Overdue AP",
        "Critical/high barriers",
        "TAC",
    ] in headers
    assert delivery.validate_word_semantics(facts, document)["ok"]


# ---------------------------------------------------------------------------
# C5 -- withheld values never land mid-sentence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope_type", ["team", "customer"])
def test_round152_executive_summary_has_no_sentinel_noun_phrases(
    monkeypatch, scope_type: str
) -> None:
    """No state label may sit where a number belongs."""
    body = _body_text(_document(_facts(scope_type), monkeypatch))
    assert "covers Unavailable (" not in body, (
        "Round 152 / C5: a withheld sentinel is being substituted into a noun phrase."
    )
    assert "distinct records" not in body or "Known activity total:" in body
    assert "Unavailable (Incomplete coverage) distinct records" not in body


def test_round152_withheld_customer_count_is_still_withheld(monkeypatch) -> None:
    """The fail-closed posture is preserved -- only the wording changed."""
    facts = _facts("team")
    body = _body_text(_document(facts, monkeypatch))
    # The offline fixture's sources are partial, so the count must not be
    # published as a bare number in the coverage sentence.
    if "customer count for the selected scope is unavailable" in body:
        assert "source coverage)" in body
    else:
        # Fully-available fixture: the count is published normally.
        assert "The selected scope covers" in body


def test_round152_incomplete_activity_mix_says_so_in_a_sentence(monkeypatch) -> None:
    facts = _facts("team")
    facts["activity_mix"] = dict(facts["activity_mix"])
    facts["activity_mix"]["is_complete"] = False
    body = _body_text(_document(facts, monkeypatch))
    assert "Known retained activity lower bound" in body
    assert "incomplete sources prevent a complete total" in body
    assert "Unavailable (Incomplete coverage) distinct records" not in body


def test_round152_complete_activity_mix_publishes_the_total(monkeypatch) -> None:
    facts = _facts("team")
    facts["activity_mix"] = dict(facts["activity_mix"])
    facts["activity_mix"]["is_complete"] = True
    body = _body_text(_document(facts, monkeypatch))
    assert "Known activity total:" in body
