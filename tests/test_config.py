"""
Tests for config module.
Validates Config class defaults, version_string, and environment overrides.

``Config`` captures ``os.environ`` once at module import time. Tests therefore
compare values against the state captured by the Config class rather than
depending on later environment mutations or test collection order. Packaged
credentials now come only from the process environment or per-user AdoptIQ
``.env``; no bundled-secret import mutates the environment.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config, ADOPTIQ_VERSION, ADOPTIQ_BUILD, version_string


class TestVersionString:
    """Test version_string helper."""

    def test_format(self):
        vs = version_string()
        assert vs.startswith('v')
        assert 'build' in vs

    def test_includes_version_and_build(self):
        vs = version_string()
        assert ADOPTIQ_VERSION in vs
        assert ADOPTIQ_BUILD in vs


class TestConfigDefaults:
    """Test Config class default values."""

    def test_analysis_timeout_default(self):
        assert Config.ANALYSIS_TIMEOUT == int(os.environ.get('ANALYSIS_TIMEOUT', '300'))

    def test_step_timeout_default(self):
        assert Config.STEP_TIMEOUT == int(os.environ.get('STEP_TIMEOUT', '60'))

    def test_tech_choices_has_entries(self):
        assert len(Config.TECH_CHOICES) >= 5
        assert 'All' not in Config.TECH_CHOICES

    def test_official_categories_structure(self):
        assert isinstance(Config.OFFICIAL_CATEGORIES, dict)
        assert len(Config.OFFICIAL_CATEGORIES) > 0
        for key, values in Config.OFFICIAL_CATEGORIES.items():
            assert isinstance(values, list)

    def test_keeper_config_keys(self):
        kc = Config.KEEPER_CONFIG
        assert 'url' in kc
        assert 'namespace' in kc
        assert 'role_id' in kc
        assert 'secret_id' in kc

    def test_snowflake_config_keys(self):
        sc = Config.SNOWFLAKE_CONFIG
        for key in ('user', 'account', 'role', 'warehouse', 'password'):
            assert key in sc

    def test_circuit_config_keys(self):
        cc = Config.CIRCUIT_CONFIG
        for key in ('client_id', 'client_secret', 'app_key', 'model_name'):
            assert key in cc

    def test_circuit_model_default(self):
        """``CIRCUIT_MODEL_NAME`` must either match the env var captured
        at config-load time, or be the documented fallback default
        ``'gemini-3.1-flash-lite'``.  We accept either because
        import-order varies (Round 30 import-pollution-resilience pin).

        Round 77 / Build 53: hardcoded fallback flipped from
        ``gpt-5-nano`` to ``gemini-3.1-flash-lite`` (operator testing
        showed materially lower per-customer LLM latency on the
        comprehensive report's per-customer storyboard loop).
        ``gpt-5-nano`` remains a one-click toggle in the
        ``[data-r69-model-input]`` dropdowns.
        """
        actual = Config.CIRCUIT_CONFIG['model_name']
        # Either the value matches the current runtime env, or it is the
        # fallback default captured before a later test env mutation. Both are
        # legitimate end-states; the test ensures the value is sourced
        # from the env-aware getter and is not a different hardcoded
        # literal.
        env_val = os.environ.get('CIRCUIT_MODEL_NAME')
        _r77_default = 'gemini-3.1-flash-lite'
        assert actual == (env_val or _r77_default) or actual == _r77_default, (
            f"CIRCUIT_CONFIG['model_name']={actual!r} did not match "
            f"either the live CIRCUIT_MODEL_NAME env ({env_val!r}) or "
            f"the Round 77 fallback default {_r77_default!r}."
        )

    def test_no_hardcoded_secrets_in_snowflake(self):
        """Snowflake config values should come from env, never hardcoded credentials.

        We accept that values may be either:
        - empty string (env wasn't set when config loaded), or
        - whatever is in the live process environment or per-user .env when
          config was imported.
        We REJECT any other literal that would indicate a hardcoded
        credential leaked into ``config.py``.
        """
        sc = Config.SNOWFLAKE_CONFIG
        for key in ('user', 'account', 'password'):
            val = sc[key]
            env_val = os.environ.get(f'SNOWFLAKE_{key.upper()}') or ''
            # Value must be either '' (env-empty) or match current env.
            # A third value would indicate a hardcoded literal.
            assert val == '' or val == env_val, (
                f"SNOWFLAKE_{key.upper()}={val!r} is neither empty nor "
                f"the env value {env_val!r}; this suggests a hardcoded "
                f"credential leaked into ``config.py``."
            )

    def test_secret_key_not_static_dev_literal(self):
        assert Config.SECRET_KEY != "dev-secret-key-change-in-production"

    def test_verbose_debug_from_env_flag(self):
        expected = os.environ.get('ADOPTIQ_VERBOSE_DEBUG', 'false').lower() in ('true', '1', 'yes', 'on')
        assert Config.VERBOSE_DEBUG is expected
