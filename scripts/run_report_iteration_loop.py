#!/usr/bin/env python3
"""CLI entrypoint for the Round 51 report iteration harness."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_repo_root_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def main() -> int:
    _ensure_repo_root_on_path()
    from report_iteration_loop import main as runner_main

    return int(runner_main())


if __name__ == "__main__":
    raise SystemExit(main())
