"""Phase 6.1 HIGH: _bundled_secrets documented as not confidential + 0o600 perms + import warning.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib
import shutil
import stat
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker__bundled_secrets_py(tmp_path: pathlib.Path) -> None:
    """Round 141: validate a generated artifact without committing secrets."""
    generator = tmp_path / "embed_credentials.py"
    shutil.copy2(REPO_ROOT / "embed_credentials.py", generator)
    fake_secret = "round-141-generated-test-secret"
    (tmp_path / "secrets.env").write_text(
        f"ADOPTIQ_SECRET_KEY={fake_secret}\n"
        "ADOPTIQ_ADMIN_SECRET_KEY=round-141-admin-test-secret\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(generator)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    bundled_path = tmp_path / "_bundled_secrets.py"
    src = bundled_path.read_text(encoding="utf-8")
    assert 'NOT A CONFIDENTIALITY BOUNDARY' in src, 'Round 8 pattern missing in _bundled_secrets.py: NOT A CONFIDENTIALITY BOUNDARY'
    assert '0o600' in src, 'Round 8 pattern missing in _bundled_secrets.py: 0o600'
    assert 'logging' in src, 'Round 8 pattern missing in _bundled_secrets.py: logging'
    assert fake_secret not in src, "generated bundle must not contain plaintext secrets"
    if sys.platform != "win32":
        assert stat.S_IMODE(bundled_path.stat().st_mode) == 0o600
