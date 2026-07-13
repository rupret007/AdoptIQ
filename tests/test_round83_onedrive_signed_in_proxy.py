"""Round 83 / Build 59 — OneDrive sign-in proxy tests.

Pins the cross-platform ``_r83_onedrive_signed_in_proxy`` behavior:

* macOS arm walks ``~/Library/CloudStorage/OneDrive-*`` and the
  legacy ``~/OneDrive - *`` glob patterns, classifying any
  ``OneDrive-Cisco*`` hit as ``signed_in_cisco`` and a non-Cisco
  hit as ``signed_in_other``.
* Windows arm reads ``HKCU\\Software\\Microsoft\\OneDrive\\Accounts``
  registry slots existence-only (no value reads, no PII).
* Defensive fallbacks: ``OSError`` collapses to ``not_signed_in``,
  unexpected exceptions collapse to ``unknown``.
* No PII in logs / no plist parsing / no shell-out.

Round 83 / Build 59
"""
# Round 83
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import corpus_bootstrap


# ---------------------------------------------------------------------------
# 1. macOS arm: signed_in_cisco
# ---------------------------------------------------------------------------


def _make_fake_home(tmp_path, leaf_names):
    """Build a fake home with the named subdirectories."""
    cloudstorage = tmp_path / "Library" / "CloudStorage"
    cloudstorage.mkdir(parents=True, exist_ok=True)
    for name in leaf_names:
        if name.startswith("Library/"):
            target = tmp_path / name
        else:
            target = tmp_path / name
        target.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_macos_signed_in_cisco_via_cloudstorage(tmp_path):
    """A ``OneDrive-Cisco*`` directory under
    ``~/Library/CloudStorage/`` returns ``signed_in_cisco``."""
    # Round 83
    home = _make_fake_home(tmp_path, ["Library/CloudStorage/OneDrive-Cisco"])
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "signed_in_cisco"


def test_macos_signed_in_cisco_via_pre_big_sur(tmp_path):
    """Pre-Big-Sur ``~/OneDrive - Cisco*`` also wins
    ``signed_in_cisco``."""
    # Round 83
    home = _make_fake_home(tmp_path, ["OneDrive - Cisco"])
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "signed_in_cisco"


def test_macos_signed_in_cisco_with_tenant_suffix(tmp_path):
    """``OneDrive-CiscoSystems`` (tenant variant) also matches the
    Cisco pattern via the wildcard ``OneDrive-Cisco*`` glob."""
    # Round 83
    home = _make_fake_home(
        tmp_path, ["Library/CloudStorage/OneDrive-CiscoSystems"],
    )
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "signed_in_cisco"


# ---------------------------------------------------------------------------
# 2. macOS arm: signed_in_other (non-Cisco OneDrive sync root)
# ---------------------------------------------------------------------------


def test_macos_signed_in_other_personal(tmp_path):
    """A consumer ``OneDrive-Personal`` returns
    ``signed_in_other`` -- the user is signed in but not with the
    Cisco account."""
    # Round 83
    home = _make_fake_home(
        tmp_path, ["Library/CloudStorage/OneDrive-Personal"],
    )
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "signed_in_other"


def test_macos_signed_in_other_via_pre_big_sur(tmp_path):
    """Pre-Big-Sur ``~/OneDrive - SomeOtherTenant`` returns
    ``signed_in_other``."""
    # Round 83
    home = _make_fake_home(tmp_path, ["OneDrive - SomeOtherTenant"])
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "signed_in_other"


# ---------------------------------------------------------------------------
# 3. macOS arm: not_signed_in
# ---------------------------------------------------------------------------


def test_macos_not_signed_in_no_onedrive_dirs(tmp_path):
    """Empty home directory returns ``not_signed_in``."""
    # Round 83
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(tmp_path)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "not_signed_in"


def test_macos_not_signed_in_unrelated_cloud_storage(tmp_path):
    """``CloudStorage`` without any OneDrive sync roots returns
    ``not_signed_in`` -- iCloud / Dropbox / GoogleDrive don't
    count."""
    # Round 83
    home = _make_fake_home(
        tmp_path, [
            "Library/CloudStorage/iCloud Drive",
            "Library/CloudStorage/Dropbox",
            "Library/CloudStorage/GoogleDrive",
        ],
    )
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "not_signed_in"


def test_macos_not_signed_in_when_home_missing(tmp_path):
    """Missing home directory returns ``not_signed_in`` (defensive
    OSError fallback)."""
    # Round 83
    bogus = tmp_path / "nonexistent"
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(bogus)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "not_signed_in"


# ---------------------------------------------------------------------------
# 4. Cisco wins over Other when both present
# ---------------------------------------------------------------------------


def test_macos_cisco_wins_over_personal(tmp_path):
    """When both Cisco and Personal sync roots exist, Cisco wins
    (the Cisco glob pattern is checked first)."""
    # Round 83
    home = _make_fake_home(
        tmp_path, [
            "Library/CloudStorage/OneDrive-Cisco",
            "Library/CloudStorage/OneDrive-Personal",
        ],
    )
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_macos_signed_in_proxy()
    assert result == "signed_in_cisco"


# ---------------------------------------------------------------------------
# 5. Top-level dispatch: defensive fallback
# ---------------------------------------------------------------------------


def test_dispatch_collapses_unexpected_exception_to_unknown():
    """An unexpected exception in the macOS arm collapses to
    ``unknown`` so the boot path never raises."""
    # Round 83
    with patch.object(sys, "platform", "darwin"), \
         patch.object(
             corpus_bootstrap, "_r83_macos_signed_in_proxy",
             side_effect=RuntimeError("boom"),
         ):
        result = corpus_bootstrap._r83_onedrive_signed_in_proxy()
    assert result == "unknown"


def test_dispatch_routes_darwin_to_macos_arm():
    """``sys.platform == 'darwin'`` -> ``_r83_macos_signed_in_proxy``."""
    # Round 83
    with patch.object(sys, "platform", "darwin"), \
         patch.object(
             corpus_bootstrap, "_r83_macos_signed_in_proxy",
             return_value="signed_in_cisco",
         ) as mac_arm:
        result = corpus_bootstrap._r83_onedrive_signed_in_proxy()
    assert result == "signed_in_cisco"
    mac_arm.assert_called_once()


def test_dispatch_routes_win32_to_windows_arm():
    """``sys.platform == 'win32'`` -> ``_r83_windows_signed_in_proxy``."""
    # Round 83
    with patch.object(sys, "platform", "win32"), \
         patch.object(
             corpus_bootstrap, "_r83_windows_signed_in_proxy",
             return_value="signed_in_cisco",
         ) as win_arm:
        result = corpus_bootstrap._r83_onedrive_signed_in_proxy()
    assert result == "signed_in_cisco"
    win_arm.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Windows arm: filesystem fallback (registry import not available)
# ---------------------------------------------------------------------------


def test_windows_filesystem_fallback_signed_in_cisco(tmp_path):
    """When ``winreg`` is unavailable (e.g. on a non-Windows test
    host), the Windows arm falls back to filesystem probes for
    ``%USERPROFILE%/OneDrive - Cisco``."""
    # Round 83
    home = _make_fake_home(tmp_path, ["OneDrive - Cisco"])
    # Patch the import so the registry path is NOT taken.
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict,
    ) else __builtins__.__import__

    def import_blocking_winreg(name, *args, **kwargs):
        if name == "winreg":
            raise ImportError("no winreg on this host")
        return real_import(name, *args, **kwargs)
    with patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}), \
         patch("os.path.expanduser", return_value=str(home)), \
         patch("builtins.__import__", side_effect=import_blocking_winreg):
        result = corpus_bootstrap._r83_windows_signed_in_proxy()
    assert result == "signed_in_cisco"


def test_windows_filesystem_fallback_not_signed_in(tmp_path):
    """No OneDrive directories anywhere -> ``not_signed_in``."""
    # Round 83
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict,
    ) else __builtins__.__import__

    def import_blocking_winreg(name, *args, **kwargs):
        if name == "winreg":
            raise ImportError("no winreg on this host")
        return real_import(name, *args, **kwargs)
    with patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}), \
         patch("os.path.expanduser", return_value=str(tmp_path)), \
         patch("builtins.__import__", side_effect=import_blocking_winreg):
        result = corpus_bootstrap._r83_windows_signed_in_proxy()
    assert result == "not_signed_in"


# ---------------------------------------------------------------------------
# 7. Boot-state default + get_state projection
# ---------------------------------------------------------------------------


def test_corpus_boot_state_signed_in_proxy_defaults_to_none():
    """A fresh ``CorpusBootState`` has ``signed_in_proxy = None``."""
    # Round 83
    state = corpus_bootstrap.CorpusBootState()
    assert state.signed_in_proxy is None


def test_get_state_projects_signed_in_proxy():
    """``get_state()`` carries the ``signed_in_proxy`` field through
    the snapshot."""
    # Round 83
    with corpus_bootstrap._BOOT_LOCK:
        original = corpus_bootstrap._STATE.signed_in_proxy
        corpus_bootstrap._STATE.signed_in_proxy = "signed_in_cisco"
    try:
        snap = corpus_bootstrap.get_state()
        assert snap.signed_in_proxy == "signed_in_cisco"
    finally:
        with corpus_bootstrap._BOOT_LOCK:
            corpus_bootstrap._STATE.signed_in_proxy = original


# ---------------------------------------------------------------------------
# 8. Privacy: no PII written to logs
# ---------------------------------------------------------------------------


def test_no_email_or_account_value_returned_anywhere(tmp_path):
    """The proxy never returns a string containing an email-shaped
    token or an OneDrive account name -- only the four enum values."""
    # Round 83
    valid = {
        "signed_in_cisco", "signed_in_other", "not_signed_in", "unknown",
    }
    home = _make_fake_home(
        tmp_path, [
            "Library/CloudStorage/OneDrive-Cisco",
            "Library/CloudStorage/OneDrive-Personal",
        ],
    )
    with patch.object(sys, "platform", "darwin"), \
         patch("os.path.expanduser", return_value=str(home)):
        result = corpus_bootstrap._r83_onedrive_signed_in_proxy()
    assert result in valid
    # Defensive: the value must not contain any email-shaped tokens.
    assert "@" not in result
