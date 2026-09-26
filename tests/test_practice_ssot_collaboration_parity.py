"""Golden parity: Collaboration practice pack matches pre-Slice-1 constants."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import adoptiq_backend as ab
import practice_config as pc
from config import Config
from practices import collaboration as collab

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _sha16(obj) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# Captured from origin/main pre-Slice-1 (2026-09-26).
_GOLDEN_BACKEND_TECH_CHOICES = "439e2d087f725118"
_GOLDEN_CONFIG_TECH_CHOICES = "17839122522017a3"
_GOLDEN_BACKEND_FILTER_KEYS = "eaaa1485fa772acc"
_GOLDEN_SUB_TECH = "809e5b3df7b5c38c"
_GOLDEN_MATRIX = (
    "Webex Meetings & Messaging",
    "Webex Calling",
    "Webex Contact Center",
    "Webex Contact Center Enterprise",
    "Cisco UCCE",
    "Cisco UCCX",
    "All Contact Center",
    "All",
)


class TestCollaborationPackFrozen:
    def test_backend_tech_choices_tuple_unchanged(self):
        assert tuple(ab.TECH_CHOICES) == collab.BACKEND_TECH_CHOICES
        assert _sha16(list(ab.TECH_CHOICES)) == _GOLDEN_BACKEND_TECH_CHOICES

    def test_config_tech_choices_tuple_unchanged(self):
        assert tuple(Config.TECH_CHOICES) == collab.CONFIG_TECH_CHOICES
        assert _sha16(list(Config.TECH_CHOICES)) == _GOLDEN_CONFIG_TECH_CHOICES

    def test_backend_filter_keys_unchanged(self):
        keys = sorted(ab.TECH_FILTERS.keys())
        assert keys == sorted(collab.BACKEND_TECH_FILTERS.keys())
        assert _sha16(keys) == _GOLDEN_BACKEND_FILTER_KEYS

    def test_sub_technology_mappings_hash(self):
        assert _sha16(Config.SUB_TECHNOLOGY_MAPPINGS) == _GOLDEN_SUB_TECH
        assert Config.SUB_TECHNOLOGY_MAPPINGS == collab.SUB_TECHNOLOGY_MAPPINGS

    def test_matrix_technology_choices_tuple(self):
        assert pc.get_matrix_technology_choices() == _GOLDEN_MATRIX
        assert collab.MATRIX_TECHNOLOGY_CHOICES == _GOLDEN_MATRIX

    def test_analysis_form_choices_match_matrix(self):
        labels = [pair[0] for pair in pc.get_analysis_form_technology_choices()]
        assert labels == list(_GOLDEN_MATRIX)
