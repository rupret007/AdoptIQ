"""Round 140: provenance rows must not inflate TAC/AP detail-sheet KPI counts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import canonical_metrics as cm
from data_normalization import drop_provenance_rows


def _provenance_tac_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "_adoptiq_provenance_row": True,
                "AdoptIQ_Status": "EMPTY",
                "AdoptIQ_Message": "no scoped CSOne for this run",
                "Case #": "PROV-ONLY",
            }
        ]
    )


def _legacy_empty_tac_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "AdoptIQ_Status": "EMPTY",
                "Case #": "LEGACY-PROV",
            }
        ]
    )


def _real_tac_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Case #": "SR-1001",
                "Severity": "P2",
                "Customer Name": "ACME CORP",
            }
        ]
    )


class TestDropProvenanceRows:
    def test_marker_row_removed(self) -> None:
        out = drop_provenance_rows(_provenance_tac_frame())
        assert out is not None and out.empty

    def test_legacy_empty_status_removed(self) -> None:
        out = drop_provenance_rows(_legacy_empty_tac_frame())
        assert out is not None and out.empty

    def test_real_row_preserved(self) -> None:
        src = _real_tac_frame()
        out = drop_provenance_rows(src)
        assert out is not None and len(out) == 1


class TestCountTotalTacProvenance:
    def test_provenance_marker_counts_zero(self) -> None:
        assert cm.count_total_tac(_provenance_tac_frame()) == 0

    def test_legacy_empty_counts_zero(self) -> None:
        assert cm.count_total_tac(_legacy_empty_tac_frame()) == 0

    def test_real_tac_still_counts_one(self) -> None:
        assert cm.count_total_tac(_real_tac_frame()) == 1


class TestHarnessDetailKpis:
    def test_support_cases_zero_on_provenance_only_frame(self) -> None:
        frame = _provenance_tac_frame()
        assert cm.count_total_tac(frame) == 0

    def test_action_plans_provenance_counts_zero(self) -> None:
        ap = pd.DataFrame(
            [
                {
                    "_adoptiq_provenance_row": True,
                    "AdoptIQ_Status": "EMPTY",
                    "ID": "ap-prov",
                    "Status": "Open",
                }
            ]
        )
        assert cm.count_open_action_plans(pd.DataFrame(), ap_df=ap) == 0


def test_round140_source_markers_present() -> None:
    import inspect

    from canonical_metrics import _collapsed_tac_df

    src_cm = inspect.getsource(_collapsed_tac_df)
    assert "drop_provenance_rows" in src_cm
    assert "Round 140" in src_cm

    import data_normalization as dn

    assert "def drop_provenance_rows" in inspect.getsource(dn.drop_provenance_rows)

    import report_iteration_loop as ril

    src_ril = inspect.getsource(ril._extract_source_backed_detail_kpis)
    assert "count_open_action_plans" in src_ril
    assert "drop_provenance_rows" in src_ril


def test_round140_soak_aborts_on_insufficient_disk(tmp_path: Path) -> None:
    import argparse
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_report_soak",
        Path(__file__).resolve().parent.parent / "scripts" / "run_report_soak.py",
    )
    assert spec and spec.loader
    soak = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(soak)

    args = argparse.Namespace(
        run_id="r140-disk",
        downloads_dir=tmp_path / "soak",
        duration_seconds=60,
        min_free_gb=1_000_000.0,
        base_url="http://127.0.0.1:5151",
        scenarios=["compact"],
        baseline_manifest=tmp_path / "manifest.json",
    )
    args.downloads_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")

    summary = soak.run_soak(
        args,
        monotonic=lambda: 0.0,
        command_runner=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no runner")),
        running_probe=lambda *a, **kw: [],
    )
    assert summary["passed"] is False
    assert summary["abort_reason"] == "insufficient_disk"
