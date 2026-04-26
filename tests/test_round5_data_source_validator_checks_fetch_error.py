"""Round 5 / Phase 4.2 regression test.

``data_source_validator`` for the adoption_barriers and CSOne paths
must check ``df.attrs['fetch_error']`` and emit a distinct
``fetch_error_details`` field, mirroring the team_subscriptions
validator.  Without this, an upstream Snowflake outage and a
legitimate "0 rows in period" both render as the same empty box.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_validator_consults_fetch_error_attr() -> None:
    src = (REPO_ROOT / "data_source_validator.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 4.2" in src, (
        "Round 5 Phase 4.2 marker missing in data_source_validator.py."
    )
    assert "fetch_error" in src, (
        "Round 5 Phase 4.2: data_source_validator must consult "
        "df.attrs['fetch_error'] on adoption_barriers and CSOne paths."
    )
