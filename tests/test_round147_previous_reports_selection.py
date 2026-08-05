"""Previous Reports selects canonical, newest artifacts deterministically."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _artifact(root: Path, name: str, *, modified: float) -> Path:
    path = root / name
    path.write_bytes(name.encode("utf-8"))
    os.utime(path, (modified, modified))
    return path


@pytest.mark.flask
def test_previous_reports_prefers_newest_word_and_canonical_source_data(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(outputs))

    newest_word = _artifact(
        outputs,
        "AdoptIQ_Report_Leader_Test_Manager_90d_20260804_120000.docx",
        modified=500.0,
    )
    older_word = _artifact(
        outputs,
        "AdoptIQ_Report_Leader_Test_Manager_90d_20260803_120000.docx",
        modified=100.0,
    )
    newest_canonical = _artifact(
        outputs,
        "AdoptIQ_Source_Data_Leader_Test_Manager_90d_20260804_120000.xlsx",
        modified=300.0,
    )
    older_canonical = _artifact(
        outputs,
        "AdoptIQ_Source_Data_Leader_Test_Manager_90d_20260803_120000.xlsx",
        modified=200.0,
    )
    newer_legacy_source = _artifact(
        outputs,
        "AdoptIQ_Data_Leader_Test_Manager_90d_20260805_120000.xlsx",
        modified=400.0,
    )
    retained_summary = _artifact(
        outputs,
        "AdoptIQ_Report_Leader_Test_Manager_90d_20260802_120000.xlsx",
        modified=50.0,
    )

    response = client.get("/previous-reports")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f"/download-file/{newest_word.name}" in body
    assert f"/download-file/{newest_canonical.name}" in body
    for unselected in (
        older_word,
        older_canonical,
        newer_legacy_source,
        retained_summary,
    ):
        assert f"/download-file/{unselected.name}" not in body
    assert str(outputs) not in body
