"""Round 132 / Build 102: customer alias normalization (NYU/NYULH hotfix)."""

from __future__ import annotations

import pandas as pd
import pytest

from adoptiq_backend import _apply_scope_filter_csone
from canonical_metrics import count_customers, list_customers
from data_normalization import (
    alias_join_keys_for_name,
    canonical_customer_name,
    collapse_customer_name_set,
    customer_names_match,
    invalidate_customer_alias_registry_cache,
    load_customer_alias_registry,
    normalize_customer_name,
)


@pytest.fixture(autouse=True)
def _fresh_alias_registry():
    invalidate_customer_alias_registry_cache()
    yield
    invalidate_customer_alias_registry_cache()


def _nyu_subs_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "BU_NAME": ["NYU LANGONE HEALTH SYSTEMS"],
            "SUBSCRIPTION_ID": ["SUB123"],
            "ACCOUNT_ID_C": ["ACC-NYU"],
        }
    )


class TestRound132AliasRegistry:
    def test_bundled_nyu_group_loads(self):
        reg = load_customer_alias_registry()
        assert reg.has_groups()
        assert "nyu_langone" in reg.group_id_to_aliases
        aliases = reg.aliases_for_group("nyu_langone")
        assert "NYU MEDICAL CENTER" in aliases
        assert "NYU LANGONE HEALTH SYSTEMS" in aliases

    def test_alias_join_keys_expand_nyu_variants(self):
        keys_med = alias_join_keys_for_name("NYU MEDICAL CENTER")
        keys_langone = alias_join_keys_for_name("NYU LANGONE HEALTH SYSTEMS")
        assert keys_med == keys_langone
        assert len(keys_med) >= 3

    def test_dsm_auto_canonical_prefers_longest_dsm_bu_name(self):
        subs = _nyu_subs_df()
        assert (
            canonical_customer_name("NYU MEDICAL CENTER", team_subs_df=subs)
            == "NYU LANGONE HEALTH SYSTEMS"
        )
        assert (
            canonical_customer_name("NYU HOSPITALS CENTER", team_subs_df=subs)
            == "NYU LANGONE HEALTH SYSTEMS"
        )

    def test_customer_names_match_across_aliases(self):
        subs = _nyu_subs_df()
        assert customer_names_match(
            "NYU MEDICAL CENTER",
            "NYU LANGONE HEALTH SYSTEMS",
            team_subs_df=subs,
        )
        assert not customer_names_match("NYU MEDICAL CENTER", "CISCO SYSTEMS", team_subs_df=subs)

    def test_unrelated_customer_unchanged(self):
        assert canonical_customer_name("CISCO SYSTEMS INC") == "CISCO SYSTEMS INC"
        assert normalize_customer_name("CISCO SYSTEMS INC") == "CISCO SYSTEMS INC"


class TestRound132CsoneScopeFilter:
    def test_csone_scope_filter_keeps_alias_variant_rows(self):
        csone = pd.DataFrame(
            {
                "customer_name": [
                    "NYU MEDICAL CENTER",
                    "NYU HOSPITALS CENTER",
                    "UNRELATED CUSTOMER",
                ],
                "Date/Time Opened": pd.to_datetime(
                    ["2026-02-01", "2026-02-02", "2026-02-03"], utc=True
                ),
            }
        )
        filtered = _apply_scope_filter_csone(
            csone,
            tech="All",
            days=90,
            sub_ids=[],
            team_customer_names=["NYU LANGONE HEALTH SYSTEMS"],
            include_all_cases=True,
        )
        names = set(filtered["customer_name"].astype(str))
        assert "NYU MEDICAL CENTER" in names
        assert "NYU HOSPITALS CENTER" in names
        assert "UNRELATED CUSTOMER" not in names


class TestRound132CanonicalMetrics:
    def test_count_customers_collapses_nyu_variants(self):
        subs = _nyu_subs_df()
        csone = pd.DataFrame({"customer_name": ["NYU MEDICAL CENTER", "NYU HOSPITALS CENTER"]})
        ab = pd.DataFrame({"customer_name": ["NYU LANGONE HEALTH SYSTEMS"]})
        assert count_customers(ab_df=ab, csone_df=csone, subs_df=subs) == 1
        customers = list_customers(ab_df=ab, csone_df=csone, subs_df=subs)
        assert customers == ["NYU LANGONE HEALTH SYSTEMS"]

    def test_id_first_identity_contract_preserves_ids_and_registered_aliases(self):
        same_id_aliases = pd.DataFrame(
            {
                "ACCOUNT_ID_C": ["ACC-1", "ACC-1"],
                "BU_NAME": ["Example Incorporated", "Example Inc"],
            }
        )
        assert len(list_customers(subs_df=same_id_aliases)) == 1

        same_name_distinct_ids = pd.DataFrame(
            {
                "ACCOUNT_ID_C": ["ACC-1", "ACC-2"],
                "BU_NAME": ["Shared Customer", "Shared Customer"],
            }
        )
        distinct_labels = list_customers(subs_df=same_name_distinct_ids)
        assert len(distinct_labels) == 2
        assert {label.rsplit(" (", 1)[-1].rstrip(")") for label in distinct_labels} == {
            "acc-1",
            "acc-2",
        }

        id_backed_nyu = _nyu_subs_df()
        idless_known_aliases = pd.DataFrame(
            {"customer_name": ["NYU MEDICAL CENTER", "NYU HOSPITALS CENTER"]}
        )
        assert list_customers(
            subs_df=id_backed_nyu,
            csone_df=idless_known_aliases,
        ) == ["NYU LANGONE HEALTH SYSTEMS"]

    def test_collapse_customer_name_set(self):
        subs = _nyu_subs_df()
        collapsed = collapse_customer_name_set(
            ["NYU MEDICAL CENTER", "NYU LANGONE HEALTH SYSTEMS", "CISCO"],
            team_subs_df=subs,
        )
        assert collapsed == {"NYU LANGONE HEALTH SYSTEMS", "CISCO"}


class TestRound132LeaderTacKeys:
    def test_alias_join_keys_cover_csone_variant_for_dsm_roster_name(self):
        roster_key = alias_join_keys_for_name("NYU LANGONE HEALTH SYSTEMS")
        csone_key = alias_join_keys_for_name("NYU MEDICAL CENTER")
        assert roster_key & csone_key
