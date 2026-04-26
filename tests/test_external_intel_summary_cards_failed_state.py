"""Round 2 / Phase 1.10 + Phase 5.6 regression test.

When ``incident_storage._safe`` substitutes a stats dict with
``fetch_error`` set (because the underlying SQL raised), the
external-intelligence template MUST render a failed-state branch
rather than treating the empty payload as "no records yet".

This test covers two surfaces:

  1. The template branches on ``incidents_state == 'failed'`` (and
     similar) so the failed state has a distinct render path.
  2. ``incident_storage.get_all_external_intel`` wraps the global
     stats lookups in ``_safe`` so a failure there also surfaces in
     ``fetch_errors`` instead of crashing the request.
"""
from __future__ import annotations

import inspect
import pathlib

import incident_storage


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_template_branches_on_failed_state() -> None:
    tpl = (REPO_ROOT / "templates" / "external_intelligence.html").read_text(encoding="utf-8")
    for state_var in ("incidents_state", "maintenances_state", "bugs_state"):
        assert f"{state_var} == 'failed'" in tpl, (
            f"Round 2 Phase 1.10: external_intelligence.html must "
            f"have a {{% if {state_var} == 'failed' %}} branch so a "
            f"_safe-substituted fetch_error renders as failed instead "
            f"of indistinguishable from 'no records'."
        )


def test_get_all_external_intel_wraps_global_stats_in_safe() -> None:
    """Round 2 Phase 5.6: ``incident_stats_global`` etc. must go
    through the same ``_safe`` wrapper as the windowed stats so a
    single failure in those lookups cannot break the whole EI page.
    """
    src = inspect.getsource(incident_storage.get_all_external_intel)
    for label in ("incident_stats_global", "bug_stats_global", "maintenance_stats_global"):
        assert f"_safe('{label}'" in src or f"_safe(\"{label}\"" in src, (
            f"Round 2 Phase 5.6: get_all_external_intel must wrap "
            f"{label} in _safe(...) so a failure surfaces in "
            f"fetch_errors and the template can render the failed "
            f"branch."
        )
