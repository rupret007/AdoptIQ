"""Round 126 / Build 95 (L1) -- aging TOTAL row cross-CSSM dedup-by-ID."""
from source_shape_utils import assert_in_source

import inspect

from leader_report_generator import LeaderReportGenerator


def test_r126_dedupe_helper_drops_duplicate_ids():
    import pandas as pd

    ap1 = pd.DataFrame(
        {
            "ID": ["AP-1", "AP-2"],
            "STATUS_C": ["Open", "Open"],
            "CREATED_DATE": ["2026-01-01", "2026-01-02"],
        }
    )
    ap2 = pd.DataFrame(
        {
            "ID": ["AP-1", "AP-3"],
            "STATUS_C": ["Open", "Open"],
            "CREATED_DATE": ["2026-01-01", "2026-01-03"],
        }
    )
    out = LeaderReportGenerator._r126_dedupe_team_frames_by_id([ap1, ap2])
    assert len(out) == 3
    assert set(out["ID"].astype(str)) == {"AP-1", "AP-2", "AP-3"}


def test_aging_section_uses_deduped_total_path():
    src = inspect.getsource(LeaderReportGenerator._add_aging_section)
    assert_in_source(src, "Round 126 / Build 95 (L1)", label='src')
    assert_in_source(src, "_r126_dedupe_team_frames_by_id", label='src')
    assert_in_source(src, "_compute_aging_buckets(_r126_deduped)", label='src')


def test_r126_l1_marker_on_static_helper():
    src = inspect.getsource(LeaderReportGenerator._r126_dedupe_team_frames_by_id)
    assert_in_source(src, "Round 126 / Build 95 (L1)", label='src')
