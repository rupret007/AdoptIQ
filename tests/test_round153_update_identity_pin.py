"""Round 153 / security -- macOS auto-update signing-identity pin.

``codesign --verify --deep --strict`` proves the staged bundle is validly
signed and its seal is intact.  It does NOT prove *who* signed it: any artifact
signed with any Apple Developer ID passes.  Because the update artifact is
sourced from the OneDrive ``releases_folder`` and its sha256 lives in a
manifest alongside it, a rogue-but-validly-signed bundle with a matching
sha256 would otherwise self-replace the installed app.

The pin enforces the Team Identifier hard when one is configured
(``ADOPTIQ_MACOS_TEAM_ID`` / Config / bundled sentinel), and logs a loud
warning when none is, rather than silently trusting any valid Apple signature.
"""

from __future__ import annotations

import logging

import pytest

import auto_updater


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner_with_team(team_line: str):
    """A fake runner whose ``codesign -dv`` emits ``team_line`` on stderr."""

    def runner(argv, **kwargs):
        if argv[:2] == ["codesign", "-dv"]:
            return _Result(returncode=0, stderr=team_line)
        return _Result(returncode=0)

    return runner


def test_round153_parse_team_identifier() -> None:
    assert auto_updater._parse_team_identifier("TeamIdentifier=ABCDE12345\n") == "ABCDE12345"
    assert auto_updater._parse_team_identifier("TeamIdentifier=not set") == ""
    assert auto_updater._parse_team_identifier("no identity here") == ""


def test_round153_matching_team_id_passes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADOPTIQ_MACOS_TEAM_ID", "CISCO12345")
    app = tmp_path / "AdoptIQ.app"
    app.mkdir()
    # Must not raise.
    auto_updater._verify_macos_signing_identity(
        app, _runner_with_team("Authority=Developer ID\nTeamIdentifier=CISCO12345\n")
    )


def test_round153_mismatched_team_id_is_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADOPTIQ_MACOS_TEAM_ID", "CISCO12345")
    app = tmp_path / "AdoptIQ.app"
    app.mkdir()
    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater._verify_macos_signing_identity(
            app, _runner_with_team("TeamIdentifier=EVILC0RP99\n")
        )
    assert ei.value.error_kind == "codesign_identity_mismatch"


def test_round153_unsigned_bundle_is_blocked_when_pinned(tmp_path, monkeypatch) -> None:
    """An ad-hoc / unsigned bundle has no Team ID and must be rejected."""
    monkeypatch.setenv("ADOPTIQ_MACOS_TEAM_ID", "CISCO12345")
    app = tmp_path / "AdoptIQ.app"
    app.mkdir()
    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater._verify_macos_signing_identity(
            app, _runner_with_team("TeamIdentifier=not set\n")
        )
    assert ei.value.error_kind == "codesign_identity_mismatch"


def test_round153_unpinned_warns_but_does_not_block(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.delenv("ADOPTIQ_MACOS_TEAM_ID", raising=False)
    monkeypatch.setattr(auto_updater, "_expected_macos_team_id", lambda: "")
    app = tmp_path / "AdoptIQ.app"
    app.mkdir()
    with caplog.at_level(logging.WARNING):
        auto_updater._verify_macos_signing_identity(
            app, _runner_with_team("TeamIdentifier=WHATEVER99\n")
        )
    assert any("UNPINNED" in r.getMessage() for r in caplog.records)


def test_round153_stage_mac_app_enforces_identity_end_to_end(tmp_path, monkeypatch) -> None:
    """Through _stage_mac_app: a valid --verify but wrong Team ID must block."""
    monkeypatch.setenv("ADOPTIQ_MACOS_TEAM_ID", "CISCO12345")

    real_mkdtemp = auto_updater.tempfile.mkdtemp

    def fake_mkdtemp(*a, **k):
        from pathlib import Path

        d = real_mkdtemp(*a, **k)
        (Path(d) / "AdoptIQ.app").mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(auto_updater.tempfile, "mkdtemp", fake_mkdtemp)

    def fake_runner(argv, **kwargs):
        # hdiutil/ditto/xattr/codesign --verify all succeed; identity is wrong.
        if argv[:2] == ["codesign", "-dv"]:
            return _Result(returncode=0, stderr="TeamIdentifier=EVILC0RP99\n")
        return _Result(returncode=0)

    updates = tmp_path / "updates"
    updates.mkdir()
    (updates / "AdoptIQ.app").mkdir()  # emulate ditto output
    dmg = tmp_path / "AdoptIQ.dmg"
    dmg.write_bytes(b"fake")

    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater._stage_mac_app(dmg, updates, runner=fake_runner)
    assert ei.value.error_kind == "codesign_identity_mismatch"


def test_round153_swapper_shell_quotes_paths(tmp_path) -> None:
    """A path containing shell metacharacters must not break out of the script."""
    from pathlib import Path

    evil = tmp_path / 'AdoptIQ";touch /tmp/pwned;".app'
    target = tmp_path / "Applications" / "AdoptIQ.app"
    target.parent.mkdir(parents=True, exist_ok=True)
    script = auto_updater.write_swapper_macos(
        pid=1234, staged_app=Path(evil), target_app=target, swapper_dir=tmp_path
    )
    body = script.read_text()
    # The dangerous substring must be single-quoted, not sitting in a bare
    # double-quoted assignment where the embedded quote would terminate it.
    assert 'touch /tmp/pwned' not in body.replace(shlex_quote(str(evil)), "")
    assert shlex_quote(str(evil)) in body


def shlex_quote(s):  # local helper mirror
    import shlex
    return shlex.quote(s)
