"""Round 162 — CSOne autodiscovery readability probe + AdoptIQ space-prefix skip."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app_simple


def _make_non_zip_xlsx(path: Path, *, size: int = 8192) -> None:
    path.write_bytes(b"NOT-A-ZIP-FILE" + (b"\x00" * max(0, size - 14)))


def _make_tac_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TAC"
    ws.append(["SR Number", "Title", "Severity", "Customer Name"])
    ws.append(["TAC001", "Case one", "P2", "ACME"])
    wb.save(str(path))


@pytest.fixture
def reset_csone_folder():
    saved = app_simple.app.config.get("CSONE_ONEDRIVE_FOLDER")
    yield
    app_simple.app.config["CSONE_ONEDRIVE_FOLDER"] = saved


def test_r162_helper_flags_adoptiq_enhanced_collab_summary_filename() -> None:
    name = "AdoptIQ Enhanced Premium Collab Summary-2026-07-16-07-00-08.xlsx"
    assert app_simple._r161_2_is_adoptiq_output_csone_filename(name) is True


def test_r162_readable_probe_rejects_non_zip(tmp_path) -> None:
    bad = tmp_path / "broken.xlsx"
    _make_non_zip_xlsx(bad)
    assert app_simple._r162_csone_workbook_is_readable(str(bad)) is False


def test_r162_readable_probe_accepts_tac_workbook(tmp_path) -> None:
    good = tmp_path / "CSOne_Export.xlsx"
    _make_tac_workbook(good)
    assert app_simple._r162_csone_workbook_is_readable(str(good)) is True


def test_diag_skips_unreadable_newest_and_picks_next_readable(reset_csone_folder, tmp_path) -> None:
    corrupt = tmp_path / "AdoptIQ Enhanced Premium Collab Summary-2026-07-16.xlsx"
    _make_non_zip_xlsx(corrupt)
    os.utime(corrupt, (time.time(), time.time()))

    real = tmp_path / "CSOne_Export_July2026.xlsx"
    _make_tac_workbook(real)
    os.utime(real, (time.time() - 3600, time.time() - 3600))

    app_simple.app.config["CSONE_ONEDRIVE_FOLDER"] = str(tmp_path)
    path, status, count = app_simple.get_latest_csone_from_folder_diag()
    assert path == str(real)
    assert status == "synced"
    assert count == 1


def test_diag_not_synced_when_only_unreadable_candidates(reset_csone_folder, tmp_path) -> None:
    corrupt = tmp_path / "AdoptIQ Enhanced Premium Collab Summary-2026-07-16.xlsx"
    _make_non_zip_xlsx(corrupt)
    app_simple.app.config["CSONE_ONEDRIVE_FOLDER"] = str(tmp_path)
    path, status, count = app_simple.get_latest_csone_from_folder_diag()
    assert path is None
    assert status == "not_synced"
    assert count == 0
