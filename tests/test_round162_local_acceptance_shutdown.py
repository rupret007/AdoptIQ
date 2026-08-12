from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_validate_only_flushes_status_before_temporary_state_cleanup() -> None:
    env = dict(os.environ)
    env.pop("ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR", None)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_local_acceptance_app.py",
            "--enable-local-fixtures",
            "--validate-only",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    output = completed.stdout + "\n" + completed.stderr
    assert completed.returncode == 0, output
    assert "Error saving analysis status" not in output
    assert "No such file or directory" not in output
