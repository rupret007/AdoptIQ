"""Round 169 artifact-root, OOXML expansion, and R114 output bounds."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import report_source_parity as parity
from scripts import run_report_option_matrix as matrix_runner


def _archive(path: Path, members: dict[str, bytes] | None = None) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in (members or {"[Content_Types].xml": b"<Types/>"}).items():
            archive.writestr(name, payload)


def _bounded_result(stdout: bytes, *, truncated: bool = False) -> matrix_runner._BoundedProcessResult:
    text = stdout.decode("utf-8", "strict")
    markers = re.findall(
        r"(?m)^CRITICAL_ISSUES_FOUND=(True|False)[ \t\r]*$",
        text,
    )
    return matrix_runner._BoundedProcessResult(
        returncode=0,
        stdout_marker=markers[0] if markers else None,
        stdout_marker_count=len(markers),
        stdout_utf8_valid=True,
        stdout_bytes=len(stdout),
        stdout_sha256="" if truncated else hashlib.sha256(stdout).hexdigest(),
        stderr_bytes=0,
        stderr_sha256=hashlib.sha256(b"").hexdigest(),
        timed_out=False,
        output_truncated=truncated,
    )


def test_parity_rejects_workbook_outside_explicit_artifact_root(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    workbook = outside / "source.xlsx"
    workbook.write_bytes(b"not parsed")

    with pytest.raises(parity.ParityContractError) as caught:
        parity.build_workbook_parity_signature(workbook, allowed_root=allowed)

    assert caught.value.kind == "workbook_outside_allowed_root"
    assert str(workbook) not in str(caught.value)


def test_parity_rejects_symlinked_workbook(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = tmp_path / "target.xlsx"
    target.write_bytes(b"not parsed")
    link = allowed / "source.xlsx"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(parity.ParityContractError) as caught:
        parity.build_workbook_parity_signature(link, allowed_root=allowed)

    assert caught.value.kind == "workbook_symlink_rejected"


def test_parity_rejects_oversized_workbook_before_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"x" * 33)
    monkeypatch.setattr(parity, "PARITY_MAX_WORKBOOK_BYTES", 32)

    with pytest.raises(parity.ParityContractError) as caught:
        parity.build_workbook_parity_signature(workbook, allowed_root=tmp_path)

    assert caught.value.kind == "workbook_size_out_of_bounds"


def test_parity_rejects_high_expansion_zip_before_pandas(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    _archive(workbook, {"xl/sharedStrings.xml": b"0" * (1024 * 1024)})

    with pytest.raises(parity.ParityContractError) as caught:
        parity.build_workbook_parity_signature(workbook, allowed_root=tmp_path)

    assert caught.value.kind == "workbook_zip_compression_ratio_exceeded"


def test_r114_summary_never_retains_sensitive_child_output(tmp_path: Path) -> None:
    docx = tmp_path / "pair.docx"
    xlsx = tmp_path / "pair.xlsx"
    docx.write_bytes(b"placeholder")
    xlsx.write_bytes(b"placeholder")
    sensitive = b"Customer Secret /Users/person/report.xlsx\n"
    completed = _bounded_result(
        sensitive + b"CRITICAL_ISSUES_FOUND=False\n"
    )

    with patch.object(
        matrix_runner,
        "_run_bounded_process",
        return_value=completed,
    ):
        audit = matrix_runner._run_r114_audit(docx, xlsx)

    serialized = json.dumps(audit, sort_keys=True)
    assert audit["ok"] is True
    assert audit["stdout_bytes"] == len(sensitive) + len(b"CRITICAL_ISSUES_FOUND=False\n")
    assert audit["stdout_sha256"] == hashlib.sha256(
        sensitive + b"CRITICAL_ISSUES_FOUND=False\n"
    ).hexdigest()
    assert "stdout_tail" not in audit
    assert "stderr_tail" not in audit
    assert "Customer Secret" not in serialized
    assert "/Users/person" not in serialized


def test_r114_fails_closed_on_truncated_output_without_retaining_it(
    tmp_path: Path,
) -> None:
    docx = tmp_path / "pair.docx"
    xlsx = tmp_path / "pair.xlsx"
    docx.write_bytes(b"placeholder")
    xlsx.write_bytes(b"placeholder")
    completed = _bounded_result(b"sensitive" * 1000, truncated=True)

    with patch.object(
        matrix_runner,
        "_run_bounded_process",
        return_value=completed,
    ):
        audit = matrix_runner._run_r114_audit(docx, xlsx)

    assert audit["ok"] is False
    assert audit["reason"] == "audit_output_limit"
    assert audit["stdout_sha256"] == ""
    assert "sensitive" not in json.dumps(audit)


def test_bounded_process_stops_at_first_byte_beyond_cap() -> None:
    result = matrix_runner._run_bounded_process(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*1048576)"],
        timeout_seconds=10,
        output_limit_bytes=1024,
    )

    assert result.output_truncated is True
    assert not hasattr(result, "stdout")
    assert result.stdout_bytes == 1025
    assert result.stdout_sha256 == ""


def test_bounded_process_retains_only_marker_counts_and_digests() -> None:
    stdout = b"Fake Customer Secret\nCRITICAL_ISSUES_FOUND=False\n"
    stderr = b"Fake provider path: /Users/person/private\n"
    code = (
        "import sys;"
        f"sys.stdout.buffer.write(bytes.fromhex('{stdout.hex()}'));"
        f"sys.stderr.buffer.write(bytes.fromhex('{stderr.hex()}'))"
    )

    result = matrix_runner._run_bounded_process(
        [sys.executable, "-c", code],
        timeout_seconds=10,
        output_limit_bytes=1024,
    )

    serialized = repr(result)
    assert result.returncode == 0
    assert result.stdout_marker == "False"
    assert result.stdout_marker_count == 1
    assert result.stdout_bytes == len(stdout)
    assert result.stdout_sha256 == hashlib.sha256(stdout).hexdigest()
    assert result.stderr_bytes == len(stderr)
    assert result.stderr_sha256 == hashlib.sha256(stderr).hexdigest()
    assert not hasattr(result, "stdout")
    assert "Fake Customer" not in serialized
    assert "/Users/person" not in serialized


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group timing assertion")
def test_bounded_process_timeout_reaps_child() -> None:
    result = matrix_runner._run_bounded_process(
        [sys.executable, "-c", "import time;time.sleep(30)"],
        timeout_seconds=1,
        output_limit_bytes=1024,
    )

    assert result.timed_out is True
    assert result.output_truncated is False
