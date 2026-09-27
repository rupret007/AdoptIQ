"""Round 170: Leader report form honors persisted default scope (R113)."""

from __future__ import annotations

import re

import app_simple


def _read_leader_form(client):
    resp = client.get("/leader_report_form")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_leader_form_preselects_valid_manager_and_days(client, monkeypatch):
    import adoptiq_settings as s

    valid_mgr = next(m for m in app_simple.MANAGERS if m != "All Managers")
    monkeypatch.setattr(
        s,
        "load_settings",
        lambda: {
            "default_days": 120,
            "default_manager": valid_mgr,
            "default_technology": "All",
        },
    )
    html = _read_leader_form(client)
    assert f'<option value="{valid_mgr}" selected>' in html
    assert 'id="days" name="days"' in html
    assert re.search(r'id="days"[^>]*value="120"', html)


def test_leader_form_skips_all_managers_default(client, monkeypatch):
    import adoptiq_settings as s

    monkeypatch.setattr(
        s,
        "load_settings",
        lambda: {
            "default_days": 90,
            "default_manager": "All Managers",
            "default_technology": "",
        },
    )
    html = _read_leader_form(client)
    assert 'selected>All Managers' not in html
    assert 'value="All Managers"' not in html


def test_leader_form_wires_round170_scope_loader(client):
    html = _read_leader_form(client)
    assert "Round 170: pre-selected manager from persisted defaults" in html
