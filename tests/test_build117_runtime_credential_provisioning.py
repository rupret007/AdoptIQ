"""Build 117: provisioning runtime-only credentials into the app .env.

Every authentication value is excluded from packaged builds, so the frozen app
reads them from the owner-protected Application Support ``.env``.  These tests
pin the safety contract of that provisioning step: owner-only permissions, no
secret values in stdout, unrelated keys preserved, and fail-closed behaviour
when required credentials are missing.

All fixtures use clearly synthetic values.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import provision_runtime_credentials as prov  # noqa: E402

SYNTHETIC_APPROLE_SECRET = "synthetic-approle-secret-value"
SYNTHETIC_PAT = "synthetic-snowflake-pat-value"


def _source(tmp_path: Path, *, secret: bool = True, pat: bool = False) -> Path:
    lines = [
        "ADOPTIQ_SECRET_KEY=synthetic-app-key",
        "ADOPTIQ_ADMIN_SECRET_KEY=synthetic-admin-key",
        "CIRCUIT_APP_KEY=synthetic-circuit-app",
        "CIRCUIT_CLIENT_ID=synthetic-client-id",
        "CIRCUIT_CLIENT_SECRET=synthetic-client-secret",
        "PSIRT_API_KEY=synthetic-psirt-key",
        "PSIRT_CLIENT_SECRET=synthetic-psirt-secret",
        "SNOWFLAKE_USER=synthetic-user",
        "SNOWFLAKE_ACCOUNT=synthetic-account",
        "KEEPER_ROLE_ID=synthetic-role-id",
    ]
    if secret:
        lines.append(f"KEEPER_SECRET_ID={SYNTHETIC_APPROLE_SECRET}")
    if pat:
        lines.append(f"SNOWFLAKE_PASSWORD={SYNTHETIC_PAT}")
    path = tmp_path / "secrets.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_apply_writes_owner_only_file_without_leaking_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    target = tmp_path / "support" / ".env"

    rc = prov.provision(_source(tmp_path, pat=True), target, apply=True)

    assert rc == 0
    written = prov.read_env_file(target)
    assert written["KEEPER_SECRET_ID"] == SYNTHETIC_APPROLE_SECRET
    assert written["SNOWFLAKE_PASSWORD"] == SYNTHETIC_PAT
    assert written["ADOPTIQ_SECRET_KEY"] == "synthetic-app-key"
    assert written["CIRCUIT_CLIENT_SECRET"] == "synthetic-client-secret"
    assert written["KEEPER_ROLE_ID"] == "synthetic-role-id"
    # Bundled Snowflake identifiers must not be copied into the runtime .env.
    assert "SNOWFLAKE_USER" not in written

    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    out = capsys.readouterr().out
    assert SYNTHETIC_APPROLE_SECRET not in out
    assert SYNTHETIC_PAT not in out
    assert "KEEPER_SECRET_ID" in out


def test_dry_run_does_not_create_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    target = tmp_path / "support" / ".env"

    rc = prov.provision(_source(tmp_path), target, apply=False)

    assert rc == 0
    assert not target.exists()
    assert "Dry run only" in capsys.readouterr().out


def test_unrelated_existing_keys_are_preserved(tmp_path: Path):
    target = tmp_path / ".env"
    target.write_text(
        "ADOPTIQ_OPERATOR_NOTE=keep-me\nKEEPER_SECRET_ID=stale-value\n",
        encoding="utf-8",
    )

    rc = prov.provision(_source(tmp_path), target, apply=True)

    assert rc == 0
    written = prov.read_env_file(target)
    assert written["ADOPTIQ_OPERATOR_NOTE"] == "keep-me"
    # The rotated secret must replace the stale one.
    assert written["KEEPER_SECRET_ID"] == SYNTHETIC_APPROLE_SECRET


def test_missing_source_file_fails_closed(tmp_path: Path):
    rc = prov.provision(tmp_path / "absent.env", tmp_path / ".env", apply=True)

    assert rc == 1
    assert not (tmp_path / ".env").exists()


def test_missing_required_runtime_credential_fails_closed(tmp_path: Path):
    target = tmp_path / ".env"

    rc = prov.provision(_source(tmp_path, secret=False), target, apply=True)

    assert rc == 1
    assert not target.exists()


def test_empty_credential_fails_closed_and_leaves_existing_file_intact(tmp_path: Path):
    target = tmp_path / ".env"
    target.write_text("KEEPER_SECRET_ID=previous-value\n", encoding="utf-8")

    rc = prov.provision(_source(tmp_path, secret=False), target, apply=True)

    assert rc == 1
    # A fail-closed run must not clobber a working installation.
    assert prov.read_env_file(target)["KEEPER_SECRET_ID"] == "previous-value"


def test_runtime_env_path_matches_the_app_support_location():
    path = prov.runtime_env_path()

    assert path.name == ".env"
    assert path.parent.name == "AdoptIQ"


def test_rendered_file_documents_the_no_embed_contract(tmp_path: Path):
    rendered = prov.render_env_file({"KEEPER_SECRET_ID": SYNTHETIC_APPROLE_SECRET})

    assert "never commit" in rendered
    assert "NOT embedded" in rendered
