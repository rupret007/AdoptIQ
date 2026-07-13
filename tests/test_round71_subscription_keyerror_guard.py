"""Round 71 / Phase 3 (#17) -- subscription except branch is KeyError-safe.

Pre-R71 the subscription analysis except branch used
``sub_data['customer_name']`` directly.  When the resolver returned
a row that lacked the ``customer_name`` key (e.g. partial Snowflake
hit, scope-filter retained only the subscription id), the except
branch raised ``KeyError`` from inside the recovery path, hiding the
original failure under a noisy traceback.

Round 71 / Phase 3 (#17) replaces every ``sub_data['customer_name']``
read inside the analysis path with ``sub_data.get('customer_name',
subscription_id)`` so the recovery path always names something.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_subscription_except_branch_uses_get() -> None:
    """The subscription except branch MUST use
    ``sub_data.get('customer_name', subscription_id)`` instead of
    ``sub_data['customer_name']``."""
    src = _read_app_simple()
    # The recovery line that used to KeyError.
    assert (
        "Analysis completed for {sub_data.get('customer_name', subscription_id)} "
        "(Subscription: {subscription_id})" in src
    ), (
        "Round 71 / Phase 3 (#17): subscription except branch must "
        "use ``sub_data.get('customer_name', subscription_id)`` so the "
        "recovery path never KeyErrors when the resolver omits the "
        "customer_name key."
    )


def test_round71_subscription_briefing_uses_get() -> None:
    """The subscription briefing line MUST also use ``.get(...)``."""
    src = _read_app_simple()
    assert (
        "## Subscription Briefing: {sub_data.get('customer_name', subscription_id)}"
        in src
    ), (
        "Round 71 / Phase 3 (#17): subscription briefing header line "
        "must use ``.get('customer_name', subscription_id)`` so the "
        "briefing renders something even when the row lacks customer_name."
    )


def test_round71_subscription_format_call_uses_get() -> None:
    """The PROMPT_PER_SUBSCRIPTION_TEMPLATE.format() call MUST also
    use ``.get(...)`` for the CUSTOMER_NAME slot."""
    src = _read_app_simple()
    assert "CUSTOMER_NAME=sub_data.get('customer_name', subscription_id)" in src, (
        "Round 71 / Phase 3 (#17): the LLM prompt template format call "
        "must use ``.get('customer_name', subscription_id)`` so the "
        "prompt always names a subject."
    )


def test_round71_no_unguarded_sub_data_customer_name_in_subscription_path() -> None:
    """No ``sub_data['customer_name']`` literal should remain in the
    subscription analysis function body (catch any future regressions
    that sneak the bracket access back in)."""
    src = _read_app_simple()
    # Locate the run_subscription_analysis function.
    idx = src.find("def run_subscription_analysis(")
    assert idx > 0, "run_subscription_analysis function not found"
    # Look at the next ~15000 chars (the function body fits comfortably).
    body = src[idx : idx + 15000]
    # Allow the negative comment text that mentions the old pattern.
    # We're scanning for the actual code use, not the docstring/comment.
    # The ONLY allowed use of the literal sub_data['customer_name'] in
    # the body is inside a comment line ('#' prefix).  Walk line-by-line.
    bad_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "sub_data['customer_name']" in line or 'sub_data["customer_name"]' in line:
            bad_lines.append(line)
    assert not bad_lines, (
        "Round 71 / Phase 3 (#17): run_subscription_analysis must not "
        "reference sub_data['customer_name'] directly (use .get() with "
        "subscription_id default).  Offending lines:\n"
        + "\n".join(bad_lines)
    )
