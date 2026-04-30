"""Round 54 / F4 -- pin the typed-confirmation gate on
``scripts/mint_corpus_sentinel.py --force``.

Background
----------
Round 53 / Phase 53.0 introduced the mint CLI.  ``--force`` rotates
the canonical sentinel and brick every previously-baked corpus on
every shipped install.  Pre-Round-54 a single keystroke
(``--force``) was enough to commit the rotation; a sleep-deprived
operator who tab-completed the wrong command line could nuke a
working share with no chance to back out.

Round 54 / F4 adds a typed-confirmation gate ("type ROTATE to
confirm") in interactive mode and a ``--yes`` bypass for unattended
build pipelines.  These tests pin the gate semantics:

* Interactive tty + correct token -> rotation proceeds (exit 0).
* Interactive tty + wrong token -> rotation aborts (exit 5);
  sentinel bytes unchanged.
* Interactive tty + Ctrl-C / EOF -> rotation aborts (exit 5).
* Non-tty without ``--yes`` -> rotation refused (exit 5);
  sentinel bytes unchanged.  Defends against pipe-confirmation
  attacks where ``echo ROTATE | mint --force`` would otherwise
  silently confirm.
* Non-tty with ``--yes`` -> rotation proceeds (build pipeline path).
* ``--yes`` without ``--force`` -> exit 5; refuses to act on a
  confused command line.
* Fresh-mint path (no existing sentinel) is NEVER prompted, even
  with ``--force``, even on a tty.  ``--force`` is destructive
  only when there is something to destroy.
"""

# Round 54

from __future__ import annotations

import logging
import secrets
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import mint_corpus_sentinel as mint_module  # noqa: E402


def _seed_synced_onedrive_with_sentinel(root: Path) -> Path:
    """Seed a OneDrive root with a non-empty file (so the sync
    heuristic considers it synced) plus a 32-byte canonical sentinel.
    Returns the sentinel path so the test can read its bytes."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "synced.docx").write_bytes(b"x" * 16)
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    sentinel_path = root / DEFAULT_SENTINEL_NAME
    sentinel_path.write_bytes(secrets.token_bytes(32))
    return sentinel_path


# ---------------------------------------------------------------------------
# Constant + helper sanity
# ---------------------------------------------------------------------------


def test_force_confirm_token_pinned():
    """Pin the literal token so a future copy edit is a deliberate
    breaking change.  Tests below depend on this exact value."""
    assert mint_module._FORCE_CONFIRM_TOKEN == "ROTATE"


def test_yes_only_with_force_is_rejected(tmp_path):
    """``--yes`` without ``--force`` is a confused command line --
    refuse rather than silently swallow."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive_with_sentinel(onedrive_root)
    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root),
        "--yes",  # no --force
    ])
    assert rc == 5, f"--yes without --force must exit 5, got {rc}"


# ---------------------------------------------------------------------------
# Interactive tty path
# ---------------------------------------------------------------------------


def test_force_interactive_correct_token_rotates(tmp_path, monkeypatch):
    """Operator types the literal token at the prompt -> rotation
    proceeds."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    # Force interactive mode + scripted input.
    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: True, raising=False,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "ROTATE")

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 0, f"correct confirmation must rotate; got {rc}"
    assert sentinel_path.read_bytes() != old_bytes, (
        "rotation must have replaced the sentinel bytes"
    )


def test_force_interactive_wrong_token_aborts(tmp_path, monkeypatch):
    """Operator mistypes the token -> rotation aborts.  Sentinel
    bytes must remain unchanged."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: True, raising=False,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "rotate")  # wrong case

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 5, f"wrong token must exit 5; got {rc}"
    assert sentinel_path.read_bytes() == old_bytes, (
        "aborted rotation must NOT touch the sentinel bytes"
    )


def test_force_interactive_token_with_whitespace_accepted(tmp_path, monkeypatch):
    """Trim whitespace around the typed token so a stray return key
    or trailing space does not abort a legitimate confirmation."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: True, raising=False,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "  ROTATE  \n")

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 0
    assert sentinel_path.read_bytes() != old_bytes


def test_force_interactive_eof_aborts(tmp_path, monkeypatch):
    """EOF on stdin (Ctrl-D) -> abort with exit 5, no rotation."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: True, raising=False,
    )

    def raise_eof(_prompt):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 5
    assert sentinel_path.read_bytes() == old_bytes


def test_force_interactive_keyboard_interrupt_aborts(tmp_path, monkeypatch):
    """Ctrl-C at the prompt -> abort with exit 5, no rotation."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: True, raising=False,
    )

    def raise_kbi(_prompt):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", raise_kbi)

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 5
    assert sentinel_path.read_bytes() == old_bytes


# ---------------------------------------------------------------------------
# Non-tty path
# ---------------------------------------------------------------------------


def test_force_non_tty_without_yes_refused(tmp_path, monkeypatch):
    """Non-tty stdin (pipe, captured pipeline) without ``--yes`` ->
    refuse.  Defends against pipe-confirmation:
    ``echo ROTATE | mint --force`` MUST NOT silently confirm."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: False, raising=False,
    )
    # Even if the input function would return the right token, the
    # gate must refuse because we cannot trust a non-tty source.

    def fake_input(_prompt):
        return "ROTATE"  # would be valid if interactive
    monkeypatch.setattr("builtins.input", fake_input)

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 5, (
        f"non-tty --force without --yes must refuse; got {rc}"
    )
    assert sentinel_path.read_bytes() == old_bytes, (
        "refused rotation must NOT touch the sentinel bytes"
    )


def test_force_non_tty_with_yes_proceeds(tmp_path, monkeypatch):
    """Non-tty + ``--yes`` -> rotation proceeds (build pipeline path)."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    old_bytes = sentinel_path.read_bytes()

    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: False, raising=False,
    )

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force", "--yes",
    ])
    assert rc == 0
    assert sentinel_path.read_bytes() != old_bytes


# ---------------------------------------------------------------------------
# Fresh-mint path -- never prompted
# ---------------------------------------------------------------------------


def test_fresh_mint_never_prompts_even_with_force(tmp_path, monkeypatch):
    """``--force`` on an EMPTY OneDrive root (no existing sentinel)
    must not prompt -- the gate is destructive only when there is
    something to destroy."""
    onedrive_root = tmp_path / "onedrive"
    onedrive_root.mkdir()
    (onedrive_root / "synced.docx").write_bytes(b"x" * 16)
    # No sentinel file yet.

    def explode(_prompt):  # pragma: no cover - must NOT be called
        raise RuntimeError("input() must NOT be called on fresh mint")

    monkeypatch.setattr("builtins.input", explode)
    # Pretend tty so the gate WOULD prompt if it ran.
    monkeypatch.setattr(
        mint_module._confirm_force_rotation.__globals__["sys"].stdin,
        "isatty", lambda: True, raising=False,
    )

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root), "--force",
    ])
    assert rc == 0, f"fresh mint with --force must succeed; got {rc}"
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    assert (onedrive_root / DEFAULT_SENTINEL_NAME).exists()


def test_fresh_mint_no_force_no_prompt(tmp_path, monkeypatch):
    """Idempotent fresh-mint also does not prompt."""
    onedrive_root = tmp_path / "onedrive"
    onedrive_root.mkdir()
    (onedrive_root / "synced.docx").write_bytes(b"x" * 16)

    def explode(_prompt):  # pragma: no cover - must NOT be called
        raise RuntimeError("input() must NOT be called on idempotent mint")

    monkeypatch.setattr("builtins.input", explode)

    rc = mint_module.main([
        "--onedrive-root", str(onedrive_root),
    ])
    assert rc == 0


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_confirm_helper_yes_short_circuits():
    """``yes=True`` short-circuits without calling input or isatty.
    Must not raise even if those functions would explode."""
    def explode(*_a, **_k):  # pragma: no cover - sanity
        raise RuntimeError("must not be called")

    assert mint_module._confirm_force_rotation(
        yes=True,
        target=Path("/tmp/x"),
        existing_digest="abcdef0123456789",
        input_fn=explode,
        isatty_fn=explode,
    ) is True


def test_confirm_helper_non_tty_returns_false():
    """Non-tty without yes -> False, no prompt."""
    def fake_input(_p):
        raise RuntimeError("input must not be called on non-tty")

    assert mint_module._confirm_force_rotation(
        yes=False,
        target=Path("/tmp/x"),
        existing_digest="abcdef0123456789",
        input_fn=fake_input,
        isatty_fn=lambda: False,
    ) is False


def test_confirm_helper_logs_warn_not_raw_payload(caplog):
    """The prompt and warnings must include the digest PREFIX (16
    hex chars) but NEVER the raw sentinel bytes -- otherwise a
    rotation log would leak the keying material it claims to
    redact."""
    digest = "abcdef0123456789"
    captured_prompts = []

    def fake_input(prompt):
        captured_prompts.append(prompt)
        return "ROTATE"

    with caplog.at_level(logging.INFO, logger="mint_corpus_sentinel"):
        ok = mint_module._confirm_force_rotation(
            yes=False,
            target=Path("/tmp/x"),
            existing_digest=digest,
            input_fn=fake_input,
            isatty_fn=lambda: True,
        )
    assert ok is True
    # Prompt must mention the digest prefix for forensic correlation.
    joined = "".join(captured_prompts)
    assert digest in joined, (
        "prompt must include the digest prefix so the operator can "
        "compare against the documented current digest"
    )
    # Prompt must NOT contain anything that looks like raw sentinel
    # bytes (e.g. base64 or non-ASCII).  Sanity: 16 hex is fine; any
    # 32+ char base64 string would be suspicious.
    import re
    suspect_b64 = re.findall(r"[A-Za-z0-9+/=]{40,}", joined)
    assert not suspect_b64, (
        f"prompt contains a long base64-like string -- possible raw "
        f"sentinel leak: {suspect_b64!r}"
    )
