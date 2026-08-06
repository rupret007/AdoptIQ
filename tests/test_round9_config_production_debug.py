"""Round 9 / Phase 1.4: config.py forces DEBUG=False when ADOPTIQ_PRODUCTION_READY=1.

Marker + behavioral test.  The hardened resolver must:
1. Force DEBUG=False when ADOPTIQ_PRODUCTION_READY=1 even if DEBUG=true is set.
2. Refuse to boot via enforce_production_safety when both are still truthy.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import importlib
import os
import pathlib
import sys
from contextlib import contextmanager

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_marker_config_production_debug() -> None:
    src = REPO_ROOT.joinpath('config.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 1.4' in src, 'Round 9 / Phase 1.4 marker missing in config.py'
    assert_in_source(src, '_resolve_debug_flag', label='src')
    assert_in_source(src, '_is_production_env', label='src')
    assert_in_source(src, 'enforce_production_safety', label='src')
    assert_in_source(src, 'ADOPTIQ_PRODUCTION_READY', label='src')


@contextmanager
def _env(**overrides):
    """Temporarily override environment variables; restore after the test."""
    saved = {}
    for k, v in overrides.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_debug_forced_false_in_production() -> None:
    with _env(ADOPTIQ_PRODUCTION_READY='1', DEBUG='true'):
        # Force a fresh import so the class-level attribute reflects the env.
        sys.modules.pop('config', None)
        config = importlib.import_module('config')
        assert config.Config.DEBUG is False, 'DEBUG should be forced to False in production'
        assert config.Config.TESTING is False, 'TESTING should be forced to False in production'


def test_enforce_production_safety_no_op_when_dev() -> None:
    with _env(ADOPTIQ_PRODUCTION_READY=None, FLASK_ENV='development', DEBUG='true'):
        sys.modules.pop('config', None)
        config = importlib.import_module('config')
        # Should not raise outside production
        config.enforce_production_safety()
