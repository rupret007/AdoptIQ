"""Round 145 renewal gauge readability contract."""

from __future__ import annotations

import pandas as pd
import pytest


def test_renewal_score_gauge_has_center_well_and_contrast_text(
    tmp_path, monkeypatch
) -> None:
    pytest.importorskip("matplotlib")
    from matplotlib.axes import Axes

    from app_simple import create_renewal_charts

    captured_pies: list[dict] = []
    captured_text: list[tuple[str, dict]] = []
    original_pie = Axes.pie
    original_text = Axes.text

    def recording_pie(self, *args, **kwargs):
        captured_pies.append(dict(kwargs))
        return original_pie(self, *args, **kwargs)

    def recording_text(self, x, y, value, *args, **kwargs):
        captured_text.append((str(value), dict(kwargs)))
        return original_text(self, x, y, value, *args, **kwargs)

    monkeypatch.setattr(Axes, "pie", recording_pie)
    monkeypatch.setattr(Axes, "text", recording_text)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs").mkdir()

    paths = create_renewal_charts(
        pd.DataFrame(),
        pd.DataFrame(),
        {"renewal_risk_score": 37.0, "renewal_risk_category": "MEDIUM"},
    )

    assert paths
    assert captured_pies[0]["wedgeprops"]["width"] == pytest.approx(0.34)
    score_labels = [
        kwargs
        for value, kwargs in captured_text
        if value == "37.0/100\nMODERATE"
    ]
    assert score_labels
    assert score_labels[0]["color"] == "#1f2937"
