"""Round 8 compatibility and secret-lint tests for embed_credentials.

Credential generation is retired. These tests pin the refusal path while
retaining coverage for the legacy helpers and ``ci_lint`` secret scanner.

We deliberately use synthetic, clearly-fake values so the test fixtures
themselves never contain anything resembling a real credential format.
``ci_lint`` is run against an isolated tmp directory so we never accuse
the live repo of leaking secrets in the assertions below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import embed_credentials as ec


def test_xor_encode_decode_roundtrip_ascii():
    plaintext = "hello world"
    encoded = ec._encode_value(plaintext)
    assert encoded != plaintext
    assert ec._decode_value(encoded) == plaintext


def test_xor_encode_decode_roundtrip_unicode():
    # Cover non-ASCII so a future change to an ASCII-only codec breaks fast.
    plaintext = "tëst-✓-π-€"
    encoded = ec._encode_value(plaintext)
    assert ec._decode_value(encoded) == plaintext


def test_xor_encode_is_not_identity():
    # The XOR-with-static-key obfuscator must change the bytes; if a
    # future refactor accidentally drops the XOR step we'd silently
    # ship plaintext base64.
    encoded = ec._encode_value("password-123")
    import base64
    raw = base64.b64decode(encoded.encode("ascii"))
    assert raw != b"password-123"


def test_parse_env_file_filters_unknown_keys(tmp_path: Path):
    env = tmp_path / "secrets.env"
    env.write_text(
        "# comment\n"
        "ADOPTIQ_SECRET_KEY=abc-123\n"
        "NOT_AN_ALLOWED_KEY=value-should-be-dropped\n"
        "EMPTY_VALUE=\n"
        "QUOTED='quoted-val'\n",
        encoding="utf-8",
    )
    parsed = ec._parse_env_file(env)
    assert parsed.get("ADOPTIQ_SECRET_KEY") == "abc-123"
    # Keys outside ENV_KEYS allowlist must be dropped.
    assert "NOT_AN_ALLOWED_KEY" not in parsed
    # Empty values are dropped.
    assert "EMPTY_VALUE" not in parsed


def test_parse_env_file_handles_utf8_bom(tmp_path: Path):
    env = tmp_path / "secrets.env"
    env.write_bytes(
        b"\xef\xbb\xbfADOPTIQ_SECRET_KEY=value-with-bom\n"
    )
    parsed = ec._parse_env_file(env)
    assert parsed.get("ADOPTIQ_SECRET_KEY") == "value-with-bom"


# --------------------------------------------------------------------------
# CI lint behaviour
# --------------------------------------------------------------------------

def test_ci_lint_clean_tree_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # Empty tree must pass.
    (tmp_path / "README.md").write_text("# nothing sensitive here", encoding="utf-8")
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 0


def test_ci_lint_flags_aws_access_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    bad = tmp_path / "leaked.py"
    synthetic_aws_id = "AKIA" + "IOSFODNN7EXAMPLE"
    bad.write_text(
        # Synthetic but matches AWS access key id shape.
        f'AWS_KEY = "{synthetic_aws_id}"\n',
        encoding="utf-8",
    )
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 1
    captured = capsys.readouterr()
    assert "AWS access key id" in captured.err
    # The full secret must not be echoed back verbatim.
    assert synthetic_aws_id not in captured.err


def test_ci_lint_flags_github_pat(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    bad = tmp_path / "config.yml"
    bad.write_text(
        # ghp_ + 36 chars = matches the documented GitHub PAT shape.
        "token: ghp_" + ("A" * 36) + "\n",
        encoding="utf-8",
    )
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 1
    captured = capsys.readouterr()
    assert "GitHub personal access token" in captured.err


def test_ci_lint_flags_pem_private_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    bad = tmp_path / "key.pem"
    pem_header = "-----BEGIN " + "RSA PRIVATE KEY-----"
    bad.write_text(
        f"{pem_header}\nfake-body\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 1
    captured = capsys.readouterr()
    assert "PEM private key block" in captured.err


def test_ci_lint_skips_template_and_self(tmp_path: Path):
    # secrets.env.template intentionally contains key NAMES; it must not
    # trigger the lint just because it mentions "password" etc.
    template = tmp_path / "secrets.env.template"
    template.write_text(
        "ADOPTIQ_SECRET_KEY=\nSNOWFLAKE_PASSWORD=\nKEEPER_SECRET_ID=\n",
        encoding="utf-8",
    )
    # And embed_credentials.py itself must be skipped.
    self_copy = tmp_path / "embed_credentials.py"
    self_copy.write_text(
        # Even with a regex-shaped string inside, this file is exempt.
        'pattern = r"AKIA[A-Z0-9]{16}"\n',
        encoding="utf-8",
    )
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 0


def test_ci_lint_skips_vendor_and_build_dirs(tmp_path: Path):
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "bad.py").write_text(
        'AWS = "AKIA' + 'IOSFODNN7EXAMPLE' + '"\n',
        encoding="utf-8",
    )
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "leaked.js").write_text(
        'const t = "ghp_' + ("B" * 36) + '";\n',
        encoding="utf-8",
    )
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 0


def test_ci_lint_flags_populated_bundled_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # A populated _bundled_secrets.py living next to source is a strong
    # signal that the build artifact was committed by mistake.
    bs = tmp_path / "_bundled_secrets.py"
    bs.write_text(
        "_DATA = {\n"
        "    'ADOPTIQ_SECRET_KEY': 'someBase64XOROutput==',\n"
        "    'KEEPER_SECRET_ID': 'anotherBase64XOROutput==',\n"
        "}\n",
        encoding="utf-8",
    )
    rc = ec.ci_lint(root=tmp_path)
    assert rc == 1
    captured = capsys.readouterr()
    assert "_bundled_secrets.py" in captured.err


def test_main_recognises_ci_lint_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Ensure ``python embed_credentials.py --ci-lint`` short-circuits to
    # the lint path instead of trying to embed a missing secrets.env.
    monkeypatch.setattr(sys, "argv", ["embed_credentials.py", "--ci-lint"])
    monkeypatch.chdir(tmp_path)
    # Point __file__-derived root at the empty tmp_path tree so the lint
    # has nothing to flag.
    monkeypatch.setattr(ec, "__file__", str(tmp_path / "embed_credentials.py"))
    rc = ec.main()
    assert rc == 0


def test_main_refuses_generation_and_creates_no_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Even a historical secrets.env input must not recreate the retired
    # reversible bundle artifact.
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("ADOPTIQ_SECRET_KEY=clearly-fake-value\n", encoding="utf-8")
    before = {path.name for path in tmp_path.iterdir()}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["embed_credentials.py"])
    monkeypatch.setattr(ec, "__file__", str(tmp_path / "embed_credentials.py"))

    rc = ec.main()

    assert rc == 2
    assert {path.name for path in tmp_path.iterdir()} == before
    assert not (tmp_path / "_bundled_secrets.py").exists()
    assert "Credential embedding is permanently disabled" in capsys.readouterr().err
