"""
Tests for config module.
Validates Config class defaults, version_string, and environment overrides.
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
        expected = os.environ.get('CIRCUIT_MODEL_NAME', 'gpt-5-nano')
        assert Config.CIRCUIT_CONFIG['model_name'] == expected

    def test_no_hardcoded_secrets_in_snowflake(self):
        """Snowflake config values should come from env, never hardcoded credentials."""
        sc = Config.SNOWFLAKE_CONFIG
        for key in ('user', 'account', 'password'):
            val = sc[key]
            assert val == (os.environ.get(f'SNOWFLAKE_{key.upper()}') or ''), \
                f"SNOWFLAKE_{key.upper()} should come from env"

    def test_secret_key_not_static_dev_literal(self):
        assert Config.SECRET_KEY != "dev-secret-key-change-in-production"

    def test_verbose_debug_from_env_flag(self):
        expected = os.environ.get('ADOPTIQ_VERBOSE_DEBUG', 'false').lower() in ('true', '1', 'yes', 'on')
        assert Config.VERBOSE_DEBUG is expected
