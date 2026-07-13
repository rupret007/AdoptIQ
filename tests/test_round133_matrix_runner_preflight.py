"""Round 133 matrix runner preflight tests."""

from __future__ import annotations

import json
from unittest.mock import patch

from scripts import run_report_option_matrix as matrix_runner


def test_round133_matrix_runner_blocks_when_connectivity_preflight_fails():
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value={"ok": False, "error_kind": "snowflake_credentials_missing"},
        ),
    ):
        code = matrix_runner.main(["--blocks", "A"])
    assert code == 5


def test_round133_matrix_runner_connectivity_probe_parses_json():
    class _Resp:
        def read(self):
            return json.dumps({"ok": True, "stages": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Resp()):
        payload = matrix_runner._probe_connectivity("http://127.0.0.1:5151")
    assert payload.get("ok") is True
