"""Round 17.1 -- user-downloads filename filter contracts.

The corpus indexer also walks the runtime user's Downloads folder so
AdoptIQ-rendered reports (Word + Excel) feed Ask AI, Customer 360, and
Playbook RAG.  But Downloads is shared with the user's personal files
(resumes, screenshots, vendor exports, ...), so we must:

* Only admit ``AdoptIQ`` filenames (allow-list, not deny-list).
* Walk the top level only -- never recurse into subfolders.
* Honor ``CSONE_INCLUDE_USER_DOWNLOADS=false`` to skip Downloads
  entirely without changing other code paths.

Pinning these contracts here protects the user's privacy.
"""

from __future__ import annotations

from pathlib import Path

import pytest


from corpus_indexer import enumerate_user_report_files


def test_user_downloads_filter_admits_only_adoptiq_named_files(tmp_path: Path):
    # Mix of AdoptIQ-named files and unrelated user content.
    fixtures = [
        "AdoptIQ_Data_2026-04-25.xlsx",
        "AdoptIQ_Report_2026-04-25.docx",
        "AdoptIQ Enhanced Premium Collab Summary-2026-04-25.xlsx",
        "AdoptIQ_Notes.csv",
        "Resume.pdf",
        "Untitled.xlsx",
        "Quarterly_Plan.docx",
        "vendor_export_2026.csv",
        "~$AdoptIQ_Report.docx",  # Office lock file -- skip
        ".DS_Store",
    ]
    for name in fixtures:
        (tmp_path / name).write_bytes(b"x" * 16)

    files = enumerate_user_report_files(tmp_path)
    names = sorted(f.path.name for f in files)
    assert names == sorted(
        [
            "AdoptIQ_Data_2026-04-25.xlsx",
            "AdoptIQ_Report_2026-04-25.docx",
            "AdoptIQ Enhanced Premium Collab Summary-2026-04-25.xlsx",
            "AdoptIQ_Notes.csv",
        ]
    ), f"unexpected enumeration: {names!r}"


def test_user_downloads_filter_does_not_recurse(tmp_path: Path):
    """Files nested in subdirectories must be ignored -- AdoptIQ
    writes reports to the root of Downloads, and recursing risks
    opening unrelated archives the user dropped into a sub-folder."""
    (tmp_path / "AdoptIQ_Top.docx").write_bytes(b"x" * 16)
    nested = tmp_path / "Photos"
    nested.mkdir()
    (nested / "AdoptIQ_Nested.docx").write_bytes(b"x" * 16)
    (nested / "vacation.jpg").write_bytes(b"x" * 16)

    files = enumerate_user_report_files(tmp_path)
    names = [f.path.name for f in files]
    assert names == ["AdoptIQ_Top.docx"], f"unexpected enumeration: {names!r}"


def test_user_downloads_filter_returns_empty_for_missing_dir(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    files = enumerate_user_report_files(missing)
    assert files == []


def test_user_downloads_filter_returns_empty_for_none(tmp_path: Path):
    assert enumerate_user_report_files(None) == []


def test_user_downloads_filter_rejects_office_lock_files(tmp_path: Path):
    """Office leaves ``~$Foo.docx`` lock files on the disk while a
    document is open.  These must be rejected even though the
    extension matches -- they contain header bytes, not document
    content, and would corrupt the corpus."""
    (tmp_path / "AdoptIQ_Report.docx").write_bytes(b"x" * 16)
    (tmp_path / "~$AdoptIQ_Report.docx").write_bytes(b"x" * 16)

    files = enumerate_user_report_files(tmp_path)
    names = [f.path.name for f in files]
    assert names == ["AdoptIQ_Report.docx"]
