"""Round 43 / Phase 8 meta-test.

Asserts that no ``portfolio_metrics`` literal-dict assignment in
``app_simple.py`` carries a hand-rolled ``len(...)`` value for the
canonical-helper-sourced keys (``total_barriers``, ``total_cases``,
``bems_count``).

This catches the next bug class -- a fresh hand-rolled PM dict that
bypasses ``canonical_metrics`` and ends up disagreeing with the validator.
The validator was hardened in Round 42 / Phase 1 to use
``count_total_barriers`` (distinct ID count); any new caller that uses
``len(_ab)`` (raw rowcount) will resurface the build-19 demo crash.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"


def _read() -> str:
    return APP_SIMPLE.read_text(encoding="utf-8")


def test_no_hand_rolled_total_barriers_len_assignment() -> None:
    """No site in ``app_simple.py`` may assign ``'total_barriers'`` from
    a raw ``len(...)`` call; the canonical helper
    ``cm.count_total_barriers(...)`` must be used instead.
    """
    src = _read()
    pattern = re.compile(r"['\"]total_barriers['\"]\s*:\s*len\s*\(", re.MULTILINE)
    matches = pattern.findall(src)
    assert not matches, (
        "Round 43 / Phase 8: found a hand-rolled "
        "'total_barriers': len(...) assignment in app_simple.py.  Use "
        "cm.count_total_barriers(...) instead -- otherwise the validator "
        "(post-Round-42-Phase-1) will raise 'Portfolio metric mismatch: "
        "total_barriers does not match normalized adoption barriers.' as "
        "soon as a multi-assignee barrier appears in the data."
    )


def test_no_hand_rolled_total_cases_len_assignment() -> None:
    src = _read()
    pattern = re.compile(r"['\"]total_cases['\"]\s*:\s*len\s*\(", re.MULTILINE)
    matches = pattern.findall(src)
    assert not matches, (
        "Round 43 / Phase 8: found a hand-rolled 'total_cases': len(...) "
        "assignment in app_simple.py.  Use cm.count_total_tac(...) instead."
    )


def test_no_hand_rolled_bems_count_len_assignment() -> None:
    src = _read()
    pattern = re.compile(r"['\"]bems_count['\"]\s*:\s*len\s*\(", re.MULTILINE)
    matches = pattern.findall(src)
    assert not matches, (
        "Round 43 / Phase 8: found a hand-rolled 'bems_count': len(...) "
        "assignment in app_simple.py.  Use cm.count_bems(...) instead."
    )


def test_no_renewal_extra_customer_frames_in_pm_call() -> None:
    """No ``cm.build_portfolio_metrics(...)`` call inside ``app_simple.py``
    that lives in the renewal or leader path may carry
    ``extra_customer_frames=`` -- doing so widens ``total_customers`` past
    the Round 25 / Phase A canonical AB ∪ CSOne ∪ Pulse universe.

    The compact path's ``_enh_portfolio_metrics`` may legitimately pass
    ``extra_customer_frames=_enh_extra_frames`` since it operates on a
    different universe contract; this test allow-lists that one site by
    name.
    """
    src = _read()
    # Find every cm.build_portfolio_metrics(...) call.
    calls: list[tuple[int, str]] = []
    start = 0
    while True:
        idx = src.find("cm.build_portfolio_metrics(", start)
        if idx < 0:
            break
        # Walk forward to the matching close paren.
        depth = 0
        end = idx
        for j in range(idx, min(len(src), idx + 4000)):
            ch = src[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        snippet = src[idx:end]
        calls.append((idx, snippet))
        start = end

    assert calls, "expected at least one cm.build_portfolio_metrics call"

    offenders: list[str] = []
    for idx, snippet in calls:
        if "extra_customer_frames" not in snippet:
            continue
        # Allow-list the compact ``_enh_extra_frames`` site.
        if "_enh_extra_frames" in snippet:
            continue
        offenders.append(snippet[:300])

    assert not offenders, (
        "Round 43 / Phase 8: found cm.build_portfolio_metrics(...) call(s) "
        "outside the compact ``_enh_extra_frames`` allow-list that pass "
        "extra_customer_frames=.  This widens 'total_customers' past the "
        "Round 25 / Phase A canonical universe and reintroduces the build-19 "
        f"demo total_customers mismatch.  Offenders: {offenders!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
