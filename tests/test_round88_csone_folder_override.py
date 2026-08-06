"""Round 88 / F5 (P1) — operator-flippable CSOne OneDrive folder
override.

Brian Frazier (Build 63 acceptance audit): "So still can't connect
for the OneDrive ... the file location for my machine is
``/Users/brfrazie/Library/CloudStorage/OneDrive-Cisco/Jeffrey Story
(jestory) - AdoptIQ_CSOne_Reports``".  Pre-R88 the only escape hatch
was the ``CSONE_ONEDRIVE_FOLDER`` env variable (requires shell rc
edit + .app restart).  R88/F5 layers a UI-flippable override on top
via ``settings.json``.

Three-pronged pin:

1. **Schema**: ``csone_onedrive_folder`` is in ``_SCHEMA``,
   ``_VALIDATORS``, and ``__all__``.

2. **Validator**: ``is_valid_csone_folder_path`` accepts empty string,
   absolute POSIX paths, Windows drive paths, tilde-prefixed paths;
   rejects relative paths, control characters, and shell metas.

3. **Resolution**: ``config._resolve_csone_onedrive_folder`` honors
   ``settings.json`` BEFORE the env override, expands ``~/`` paths,
   and silently falls through on a corrupt / missing settings.json.

4. **Endpoint**: ``GET / POST /api/settings/csone-onedrive-folder``
   returns the active path + source-precedence label, and on POST
   mutates ``Config.CSONE_ONEDRIVE_FOLDER`` in-process so the
   daily-refresh worker sees the new value without a restart.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Schema + validator pins
# ---------------------------------------------------------------------------


def test_r88_f5_schema_includes_csone_onedrive_folder() -> None:
    """Source-shape pin: ``csone_onedrive_folder`` must be in
    ``_SCHEMA``, ``_VALIDATORS``, and ``__all__``.
    """

    text = (_REPO_ROOT / "adoptiq_settings.py").read_text(encoding="utf-8")
    assert_in_source(text, '"csone_onedrive_folder": (str, "")', label='text')
    assert_in_source(text, '"csone_onedrive_folder": _is_valid_csone_folder_path', label='text')
    assert_in_source(text, '"is_valid_csone_folder_path"', label='text')


def test_r88_f5_schema_keys_includes_csone_onedrive_folder() -> None:
    """Behavior pin: ``schema_keys()`` must include the new key so
    callers can iterate the allow-list without hard-coding strings.
    """

    import adoptiq_settings  # noqa: PLC0415

    keys = adoptiq_settings.schema_keys()
    assert "csone_onedrive_folder" in keys, (
        f"Round 88 / F5: schema_keys() returned {keys!r} but "
        "``csone_onedrive_folder`` is missing."
    )


@pytest.mark.parametrize("value", [
    "",
    "/Users/foo/Library/CloudStorage/OneDrive-Cisco/AdoptIQ_CSOne_Reports",
    "/Users/brfrazie/Library/CloudStorage/OneDrive-Cisco/Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports",
    "~/Library/CloudStorage/OneDrive-Cisco/AdoptIQ_CSOne_Reports",
    "C:\\Users\\foo\\OneDrive - Cisco\\AdoptIQ_CSOne_Reports",
    "C:/Users/foo/OneDrive - Cisco/AdoptIQ_CSOne_Reports",
    "/path/with spaces/and-dashes_and.dots",
    "   ",  # whitespace-only treated as empty/unset
])
def test_r88_f5_validator_accepts_valid_paths(value: str) -> None:
    """Validator must accept empty string, absolute POSIX paths,
    Windows drive paths, tilde paths, and paths with spaces/dashes/dots.
    """

    import adoptiq_settings  # noqa: PLC0415

    assert adoptiq_settings.is_valid_csone_folder_path(value), (
        f"Round 88 / F5: validator should accept {value!r} but rejected it."
    )


@pytest.mark.parametrize("value", [
    "relative/path/no/leading/slash",
    "another-relative",
    "../parent-relative",
    "/path/with\x00null/byte",  # NUL byte injection
    "/path/with\nnewline",
    "/path/with\rcarriage-return",
    "/path/with|pipe",
    "/path/with;semicolon",
    "/path/with&ampersand",
    "/path/with`backtick",
    "/path/with$dollar",
    "/path/with<lt",
    "/path/with>gt",
    "/path/with*glob",
    "/path/with?question",
    "/path/with\"quote",
    "x" * 5000,  # > 4096 bytes
    123,
    [],
    {},
])
def test_r88_f5_validator_rejects_invalid_paths(value: object) -> None:
    """Validator must reject relative paths, NUL bytes, shell metas,
    oversize values, and non-string types.

    Note: ``None`` is INTENTIONALLY accepted as the unset sentinel
    (consistent with ``_is_valid_model_name`` and
    ``_is_valid_sharepoint_url`` -- ``settings.json`` represents a
    cleared override as either ``""`` or absent-key, so the
    validator treats both equivalently to a freshly-installed app
    that has never had the key set).
    """

    import adoptiq_settings  # noqa: PLC0415

    assert not adoptiq_settings.is_valid_csone_folder_path(value), (
        f"Round 88 / F5: validator should reject {value!r} but accepted it."
    )


def test_r88_f5_validator_treats_none_as_unset_sentinel() -> None:
    """``None`` is the canonical unset sentinel (consistent with the
    R69 model-name validator and R84 share-url validator).  Pin the
    contract so a future refactor cannot regress it.
    """

    import adoptiq_settings  # noqa: PLC0415

    assert adoptiq_settings.is_valid_csone_folder_path(None) is True, (
        "Round 88 / F5: ``None`` must be accepted as the unset sentinel "
        "for parity with the other validators (`_is_valid_model_name`, "
        "`_is_valid_sharepoint_url`)."
    )


# ---------------------------------------------------------------------------
# Resolution precedence pins
# ---------------------------------------------------------------------------


def test_r88_f5_resolver_honors_settings_json_above_env() -> None:
    """When ``settings.json`` carries a valid path AND the env var
    is set, settings.json MUST win.
    """

    from config import _resolve_csone_onedrive_folder  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        # Synthetic settings.json valid path
        synth_path = os.path.join(tmp, "fromSettings")
        os.makedirs(synth_path, exist_ok=True)
        env_path = os.path.join(tmp, "fromEnv")
        os.makedirs(env_path, exist_ok=True)

        with patch.dict(os.environ, {"CSONE_ONEDRIVE_FOLDER": env_path}, clear=False):
            with patch("adoptiq_settings.get") as mock_get, \
                 patch("adoptiq_settings.is_valid_csone_folder_path", return_value=True):
                mock_get.return_value = synth_path
                resolved = _resolve_csone_onedrive_folder()
                assert resolved == synth_path, (
                    f"Round 88 / F5: settings.json ({synth_path}) must "
                    f"win over env ({env_path}) but resolver returned {resolved}."
                )


def test_r88_f5_resolver_falls_through_to_env_when_settings_empty() -> None:
    """When ``settings.json['csone_onedrive_folder']`` is empty, the
    env override must be used.
    """

    from config import _resolve_csone_onedrive_folder  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        env_path = os.path.join(tmp, "fromEnv")
        os.makedirs(env_path, exist_ok=True)

        with patch.dict(os.environ, {"CSONE_ONEDRIVE_FOLDER": env_path}, clear=False):
            with patch("adoptiq_settings.get", return_value=""):
                resolved = _resolve_csone_onedrive_folder()
                assert resolved == env_path, (
                    f"Round 88 / F5: empty settings should fall through to "
                    f"env ({env_path}) but got {resolved}."
                )


def test_r88_f5_resolver_expands_tilde_in_settings() -> None:
    """A tilde-prefixed settings.json value must be expanded via
    ``os.path.expanduser`` so ``~/Library/...`` works.
    """

    from config import _resolve_csone_onedrive_folder  # noqa: PLC0415

    tilde_path = "~/path/to/onedrive/folder"
    expected = os.path.expanduser(tilde_path)

    with patch("adoptiq_settings.get", return_value=tilde_path), \
         patch("adoptiq_settings.is_valid_csone_folder_path", return_value=True):
        # Clear env so settings.json is the only signal
        env_clear = {k: v for k, v in os.environ.items() if k != "CSONE_ONEDRIVE_FOLDER"}
        with patch.dict(os.environ, env_clear, clear=True):
            resolved = _resolve_csone_onedrive_folder()
            assert resolved == expected, (
                f"Round 88 / F5: tilde path must expand to {expected} "
                f"but got {resolved}."
            )


def test_r88_f5_resolver_silent_on_settings_module_unavailable() -> None:
    """A ``ModuleNotFoundError`` for ``adoptiq_settings`` must not
    raise — the resolver MUST fall through to env.
    """

    from config import _resolve_csone_onedrive_folder  # noqa: PLC0415

    with patch("adoptiq_settings.get", side_effect=ImportError("synthetic missing module")):
        # Should not raise
        try:
            resolved = _resolve_csone_onedrive_folder()
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"Round 88 / F5: resolver raised {e!r} on missing settings")
        assert isinstance(resolved, str), "Resolver must always return a string"


# ---------------------------------------------------------------------------
# Endpoint pins
# ---------------------------------------------------------------------------


def test_r88_f5_get_endpoint_returns_active_path_and_source(client) -> None:
    """``GET /api/settings/csone-onedrive-folder`` returns
    ``{ok, folder_path, source, persisted_value, path_exists, env_var,
    env_value_set}``.
    """

    response = client.get("/api/settings/csone-onedrive-folder")
    assert response.status_code == 200, response.data
    body = response.get_json()
    assert body["ok"] is True, body
    assert_in_source(body, "folder_path", label='body')
    assert isinstance(body["folder_path"], str), body
    assert body["source"] in ("settings.json", "env", "auto-discovery", "fallback"), body
    assert_in_source(body, "persisted_value", label='body')
    assert isinstance(body["path_exists"], bool), body
    assert body["env_var"] == "CSONE_ONEDRIVE_FOLDER", body
    assert isinstance(body["env_value_set"], bool), body


def test_r88_f5_post_endpoint_rejects_invalid_path(client) -> None:
    """A POST with a relative path or shell metas MUST return 400 +
    ``error: "invalid_folder_path"``.
    """

    response = client.post(
        "/api/settings/csone-onedrive-folder",
        json={"folder_path": "relative/path/no/leading/slash"},
        headers={"X-AdoptIQ-Internal": "1"},
    )
    assert response.status_code == 400, response.data
    body = response.get_json()
    assert body["ok"] is False, body
    assert body["error"] == "invalid_folder_path", body
    assert_in_source(body, "detail", label='body')


def test_r88_f5_post_endpoint_rejects_non_string_payload(client) -> None:
    """Non-string ``folder_path`` value returns 400."""

    response = client.post(
        "/api/settings/csone-onedrive-folder",
        json={"folder_path": 12345},
        headers={"X-AdoptIQ-Internal": "1"},
    )
    assert response.status_code == 400, response.data
    body = response.get_json()
    assert body["ok"] is False, body
    assert body["error"] == "folder_path_must_be_string", body


def test_r88_f5_post_endpoint_persists_valid_path(client) -> None:
    """A valid path is persisted to settings.json AND the response
    carries ``source: "settings.json"`` plus the new path.
    """

    import adoptiq_settings as _settings  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        target_path = os.path.join(tmp, "AdoptIQ_CSOne_Reports")
        os.makedirs(target_path, exist_ok=True)

        # Patch the settings module's path resolver so we don't pollute
        # the real ~/Library/Application Support/AdoptIQ/settings.json
        synth_settings_dir = Path(tmp) / "adoptiq_settings_root"
        synth_settings_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(_settings, "_app_support_dir", return_value=synth_settings_dir):
            response = client.post(
                "/api/settings/csone-onedrive-folder",
                json={"folder_path": target_path},
                headers={"X-AdoptIQ-Internal": "1"},
            )
            assert response.status_code == 200, response.data
            body = response.get_json()
            assert body["ok"] is True, body
            assert body["folder_path"] == target_path, body
            assert body["source"] == "settings.json", body
            assert body["persisted_value"] == target_path, body
            assert body["path_exists"] is True, body

            # Verify the on-disk file actually carries the value
            persisted = json.loads(
                (synth_settings_dir / "settings.json").read_text(encoding="utf-8")
            )
            assert persisted.get("csone_onedrive_folder") == target_path, (
                f"Round 88 / F5: settings.json should carry the new path "
                f"but contents were {persisted}"
            )


def test_r88_f5_post_endpoint_clears_override_on_empty_string(client) -> None:
    """An empty-string POST clears the override (settings.json value
    becomes empty string, resolver falls through to env / auto-disco).
    """

    import adoptiq_settings as _settings  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        synth_settings_dir = Path(tmp) / "adoptiq_settings_root"
        synth_settings_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(_settings, "_app_support_dir", return_value=synth_settings_dir):
            response = client.post(
                "/api/settings/csone-onedrive-folder",
                json={"folder_path": ""},
                headers={"X-AdoptIQ-Internal": "1"},
            )
            assert response.status_code == 200, response.data
            body = response.get_json()
            assert body["ok"] is True, body
            assert body["persisted_value"] == "", body
            assert body["source"] != "settings.json", (
                "Empty persisted value must fall through past settings.json"
            )


def test_r88_f5_endpoint_registered_in_sensitive_endpoints() -> None:
    """The new endpoint name must be in ``_SENSITIVE_ENDPOINTS`` so the
    CSRF + loopback gates apply.
    """

    text = (_REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(text, "'api_settings_csone_onedrive_folder'", label='text')


def test_r88_f5_round_88_marker_present_in_app_simple() -> None:
    """Audit pin: the R88/F5 source marker must be present in
    ``app_simple.py`` so ``git diff app_simple.py | grep 'Round 88'``
    surfaces the change in the per-file footprint audit.
    """

    text = (_REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(text, "Round 88 / F5", label='text')


def test_r88_f5_round_88_marker_present_in_config() -> None:
    """Audit pin: the R88/F5 source marker must be present in
    ``config.py`` so the resolver-precedence change is greppable.
    """

    text = (_REPO_ROOT / "config.py").read_text(encoding="utf-8")
    assert_in_source(text, "Round 88 / F5", label='text')


def test_r88_f5_round_88_marker_present_in_adoptiq_settings() -> None:
    """Audit pin: the R88/F5 source marker must be present in
    ``adoptiq_settings.py`` so the schema addition is greppable.
    """

    text = (_REPO_ROOT / "adoptiq_settings.py").read_text(encoding="utf-8")
    assert_in_source(text, "Round 88 / F5", label='text')
