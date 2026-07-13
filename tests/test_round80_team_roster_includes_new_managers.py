"""Round 80 / Build 56: pin the team-roster expansion.

Round 80 adds two managers (Paresh Jadhav, Mithun Sakthivel
Subramanian) and 16 direct reports to ``team_config.json``, and
brings the ``adoptiq_backend._get_default_team_config`` fallback
into byte-for-byte parity with the JSON SSoT (the pre-R80 fallback
referenced a non-existent ``Josh Horowitz`` manager and placed
``Asad Sarfaraz`` under Dee Kindrick).

Two members move from Brian Frazier to Mithun's team:
``Angelica Hernandez Becerra`` and ``Samuel Tamayo``.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

# Round 80
_TEAM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "team_config.json"


def _load_json_roster() -> tuple[list[str], list[dict]]:
    with _TEAM_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data["managers"], data["team_roster"]


def test_managers_list_includes_paresh_jadhav():
    managers, _ = _load_json_roster()
    assert "Paresh Jadhav" in managers, (
        "Round 80: Paresh Jadhav must appear in team_config.json managers list"
    )


def test_managers_list_includes_mithun_sakthivel_subramanian():
    managers, _ = _load_json_roster()
    assert "Mithun Sakthivel Subramanian" in managers, (
        "Round 80: Mithun Sakthivel Subramanian must appear in "
        "team_config.json managers list"
    )


def test_paresh_jadhav_has_eight_direct_reports():
    _, roster = _load_json_roster()
    expected = {
        "Avinash Vinu",
        "Gagandeep Kaur Walia",
        "Ian Gagnon",
        "Kohei Kobayashi",
        "Samuel Sugandaran",
        "Sei Tonomi",
        "Seitaro Shinagawa",
        "Timothy Brown",
    }
    actual = {row["name"] for row in roster if row["manager"] == "Paresh Jadhav"}
    assert actual == expected, (
        f"Round 80: Paresh Jadhav direct-report set drift. "
        f"Expected {sorted(expected)}, got {sorted(actual)}"
    )


def test_mithun_sakthivel_subramanian_has_eight_direct_reports():
    _, roster = _load_json_roster()
    expected = {
        "Angelica Hernandez Becerra",
        "Asad Sarfaraz",
        "Balaji Kandasamy Shanmugam",
        "Mariyam Hachikyan",
        "Prashant Yadav",
        "Ramon Gonzalez Reyes",
        "Samuel Tamayo",
        "Shagul Hameed",
    }
    actual = {
        row["name"]
        for row in roster
        if row["manager"] == "Mithun Sakthivel Subramanian"
    }
    assert actual == expected, (
        f"Round 80: Mithun Sakthivel Subramanian direct-report set drift. "
        f"Expected {sorted(expected)}, got {sorted(actual)}"
    )


def test_angelica_and_samuel_tamayo_moved_off_brian_frazier():
    """Org change: both names move from Brian Frazier to Mithun
    Sakthivel Subramanian.  Each name MUST appear exactly once in
    the roster, under the new manager."""
    _, roster = _load_json_roster()
    angelica_rows = [r for r in roster if r["name"] == "Angelica Hernandez Becerra"]
    samtam_rows = [r for r in roster if r["name"] == "Samuel Tamayo"]
    assert len(angelica_rows) == 1, (
        f"Round 80: Angelica Hernandez Becerra appears {len(angelica_rows)} "
        f"times in roster; expected exactly 1 (under Mithun Sakthivel "
        f"Subramanian post-R80)"
    )
    assert angelica_rows[0]["manager"] == "Mithun Sakthivel Subramanian", (
        f"Round 80: Angelica Hernandez Becerra still listed under "
        f"{angelica_rows[0]['manager']}; should be Mithun Sakthivel Subramanian"
    )
    assert len(samtam_rows) == 1, (
        f"Round 80: Samuel Tamayo appears {len(samtam_rows)} times in "
        f"roster; expected exactly 1 (under Mithun Sakthivel Subramanian "
        f"post-R80)"
    )
    assert samtam_rows[0]["manager"] == "Mithun Sakthivel Subramanian", (
        f"Round 80: Samuel Tamayo still listed under "
        f"{samtam_rows[0]['manager']}; should be Mithun Sakthivel Subramanian"
    )
    # Defense in depth: the two names MUST NOT appear under Brian
    # Frazier in any row (no leftover orphan rows).
    brian_names = {r["name"] for r in roster if r["manager"] == "Brian Frazier"}
    assert "Angelica Hernandez Becerra" not in brian_names
    assert "Samuel Tamayo" not in brian_names


def test_default_fallback_matches_json_roster_byte_for_byte():
    """SSoT non-drift: ``adoptiq_backend._get_default_team_config()``
    MUST produce the same (manager, name, email) tuples and the same
    ``managers`` list as ``team_config.json`` parses to.  Pre-R80
    these drifted (Josh Horowitz manager that didn't exist, Asad
    Sarfaraz under Dee Kindrick instead of Mithun, missing entire
    Shams block).  Round 80 brings them back into byte-for-byte
    parity per ``.cursor/rules/adoptiq.mdc`` Tier 3."""
    backend = importlib.import_module("adoptiq_backend")
    fb_roster, fb_managers = backend._get_default_team_config()
    json_managers, json_roster_dicts = _load_json_roster()

    # Managers list parity (order matters; both should have the
    # same canonical ordering).
    assert fb_managers == json_managers, (
        f"Round 80: managers list drift between fallback and JSON.\n"
        f"  fallback: {fb_managers}\n  JSON:     {json_managers}"
    )

    # Convert JSON dicts to (manager, name, email) tuples and sort
    # both sides on (manager, name) so the comparison is order-
    # independent within each manager block.
    json_tuples = sorted(
        (row["manager"], row["name"], row["email"]) for row in json_roster_dicts
    )
    fb_tuples = sorted(fb_roster)
    assert fb_tuples == json_tuples, (
        f"Round 80: roster drift between fallback and JSON.\n"
        f"  Only in fallback: {set(fb_tuples) - set(json_tuples)}\n"
        f"  Only in JSON:     {set(json_tuples) - set(fb_tuples)}"
    )

    # Cardinality sanity check: 42 entries post-R80
    # (12 Dee + 9 Brian + 8 Paresh + 8 Mithun + 5 Shams).
    assert len(fb_tuples) == 42, (
        f"Round 80: expected 42 roster tuples, got {len(fb_tuples)}"
    )
