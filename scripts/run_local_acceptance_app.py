#!/usr/bin/env python3
"""Run the real AdoptIQ Flask app with explicit loopback fixture adapters."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    LocalAcceptanceError,
    LocalAcceptanceSafetyError,
    assert_safe_activation,
    build_scenario_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run AdoptIQ's real Flask routes with sanitized deterministic local "
            "acceptance data. This is not live Snowflake validation."
        )
    )
    parser.add_argument("--enable-local-fixtures", action="store_true")
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5153)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Install adapters, emit a redacted summary, and exit without binding.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    temporary_state: tempfile.TemporaryDirectory[str] | None = None
    app_module = None
    try:
        assert_safe_activation(
            explicit=bool(args.enable_local_fixtures),
            host=args.host,
        )
        if not 1 <= int(args.port) <= 65535:
            raise LocalAcceptanceSafetyError("port must be between 1 and 65535")
        bundle = build_scenario_bundle(args.scenario, args.manifest)

        # Prevent corpus/network background work during app import. The fixture
        # corpus adapters are installed immediately afterwards.
        os.environ["CORPUS_KNOWLEDGE_ENABLED"] = "false"
        os.environ["ADOPTIQ_BIND_PUBLIC"] = "0"
        os.environ["ADOPTIQ_ASK_AI_ALLOW_LEGACY_FALLBACK"] = "0"
        os.environ["ADOPTIQ_LOCAL_ACCEPTANCE_ACTIVE"] = "1"

        state_text = str(
            os.environ.get("ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR") or ""
        ).strip()
        if state_text:
            state_dir = Path(state_text).expanduser().resolve()
            state_dir.mkdir(parents=True, exist_ok=True)
        else:
            temporary_state = tempfile.TemporaryDirectory(
                prefix="adoptiq-local-acceptance-state-"
            )
            state_dir = Path(temporary_state.name).resolve()
        # Relative SQLite/settings paths must never resolve into the checkout.
        # The source modules remain importable through REPO_ROOT on sys.path.
        os.chdir(state_dir)
        os.environ["ADOPTIQ_LOG_FILE"] = str(state_dir / "adoptiq.log")

        # app_simple deliberately suppresses corpus background workers under
        # test execution. Reuse that import-time guard only inside this
        # explicit acceptance runner, then remove it before serving requests.
        prior_pytest_marker = os.environ.get("PYTEST_CURRENT_TEST")
        os.environ["PYTEST_CURRENT_TEST"] = "round145-local-acceptance-import"

        import app_simple  # noqa: PLC0415
        app_module = app_simple
        import enhanced_admin_dashboard_v2 as admin_dashboard  # noqa: PLC0415
        from local_acceptance_runtime import (  # noqa: PLC0415
            install_runtime_adapters,
            installation_summary,
        )

        # Development imports use the repository as _APP_SUPPORT. Repoint all
        # mutable status/upload/history state before serving the first request,
        # and discard any statuses that were read from the checkout at import.
        app_simple._APP_SUPPORT = state_dir  # noqa: SLF001
        app_simple.STATUS_FILE = str(state_dir / "analysis_status.json")
        app_simple.app.config["UPLOAD_FOLDER"] = str(state_dir / "uploads")
        Path(app_simple.app.config["UPLOAD_FOLDER"]).mkdir(
            parents=True, exist_ok=True
        )
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()
        admin_dashboard.DB_PATH = str(state_dir / "admin_monitoring_v2.db")
        admin_dashboard._db_initialized_for_path = None  # noqa: SLF001
        admin_dashboard.init_database()

        if prior_pytest_marker is None:
            os.environ.pop("PYTEST_CURRENT_TEST", None)
        else:
            os.environ["PYTEST_CURRENT_TEST"] = prior_pytest_marker

        installation = install_runtime_adapters(bundle, app_simple)
        summary = installation_summary(installation)
        summary.update(
            {
                "host": args.host,
                "port": int(args.port),
                "url": f"http://{args.host}:{int(args.port)}",
            }
        )
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
        if args.validate_only:
            installation.restore()
            return 0
        app_simple.app.run(
            host=args.host,
            port=int(args.port),
            debug=False,
            use_reloader=False,
            threaded=True,
        )
        return 0
    except (LocalAcceptanceSafetyError, LocalAcceptanceError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_kind": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if temporary_state is not None:
            # app_simple registers its status save with atexit.  A local
            # TemporaryDirectory would otherwise be deleted here first, then
            # the atexit callback would try to write into the vanished path and
            # misleadingly log a successful shutdown.  Flush while the state
            # directory exists and unregister only this runner's later copy.
            if app_module is not None:
                try:
                    app_module._shutdown_handler()  # noqa: SLF001
                finally:
                    atexit.unregister(app_module._shutdown_handler)  # noqa: SLF001
            temporary_state.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
