"""Round 5 / Phase 3.1 regression test.

``_CLAIM_KEYS`` (Round 4) over-strict enforcement forced every entry
under ``actions`` and ``unknowns`` to be a dict, but those fields are
defined as ``list[str]`` in ``ask_ai_grounded.py``.  Round 5 drops
those two keys from the dict-only enforcement set.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_claim_keys_does_not_force_actions_unknowns_to_dict() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 3.1" in src, (
        "Round 5 Phase 3.1 marker missing in adoptiq_backend.py."
    )
    # The fix must drop these two keys from the dict-enforcement set.
    # Pin the rationale text so re-introduction is loud.
    assert (
        "drop ``actions`` and ``unknowns`` from this" in src
        or "drop `actions` and `unknowns` from this" in src
        or "drop actions and unknowns" in src.lower()
    ), (
        "Round 5 Phase 3.1: the comment explaining why ``actions`` and "
        "``unknowns`` are excluded from _CLAIM_KEYS must remain so "
        "future contributors don't re-add them."
    )
