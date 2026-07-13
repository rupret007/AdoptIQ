"""Round 71 / Phase 6 (#33) -- help.html advertises R68/R69 features.

Pre-R71 the in-app help page was missing documentation for several
recent features that operators routinely ask about:

* The R68 build label footer on every report (App_Version,
  App_Build, Process_Started_At_UTC, Report_Generated_At_UTC).
* The R68 restart-after-update banner on the analyze page.
* The R69 LLM model preferences panels (ask_ai_model_name,
  report_model_name).
* The clean-quit path (instead of force-killing the process).

Round 71 / Phase 6 (#33) extends help.html with a dedicated
"Advanced: Build Provenance & Model Selection" section.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_help() -> str:
    return (REPO_ROOT / "templates" / "help.html").read_text(encoding="utf-8", errors="replace")


def test_round71_help_documents_build_label_footer() -> None:
    """The help page MUST describe the R68 build label footer."""
    src = _read_help()
    assert "Build Label on Every Report" in src, (
        "Round 71 / Phase 6 (#33): help.html must describe the R68 "
        "build label footer."
    )
    for field in ("App_Version", "App_Build", "Process_Started_At_UTC", "Report_Generated_At_UTC"):
        assert field in src, (
            f"Round 71 / Phase 6 (#33): help.html must enumerate the "
            f"build label field {field!r}."
        )


def test_round71_help_documents_restart_after_update_banner() -> None:
    """The help page MUST describe the R68 restart-after-update banner."""
    src = _read_help()
    assert "Restart-After-Update Banner" in src, (
        "Round 71 / Phase 6 (#33): help.html must describe the R68 "
        "restart-after-update banner."
    )


def test_round71_help_documents_r69_model_preferences() -> None:
    """The help page MUST describe both R69 model-selection knobs."""
    src = _read_help()
    assert "ask_ai_model_name" in src, (
        "Round 71 / Phase 6 (#33): help.html must mention "
        "``ask_ai_model_name`` (the Ask AI model knob)."
    )
    assert "report_model_name" in src, (
        "Round 71 / Phase 6 (#33): help.html must mention "
        "``report_model_name`` (the report-narrative model knob)."
    )


def test_round71_help_documents_clean_quit_path() -> None:
    """The help page MUST describe the clean-quit path."""
    src = _read_help()
    assert "Quit AdoptIQ Cleanly" in src or "Quit AdoptIQ" in src, (
        "Round 71 / Phase 6 (#33): help.html must describe the clean-quit "
        "path so operators know not to force-kill the process."
    )
    # Specific behaviours of the clean-quit path.
    assert "analysis_status" in src, (
        "Round 71 / Phase 6 (#33): help.html must explain that clean "
        "quit flushes ``analysis_status`` (so prior runs survive)."
    )
    assert "scrub" in src, (
        "Round 71 / Phase 6 (#33): help.html must explain that clean "
        "quit scrubs the in-memory plaintext corpus temp file."
    )


def test_round71_help_marker_present() -> None:
    """The help page MUST carry a Round 71 marker comment."""
    src = _read_help()
    assert "Round 71 / Phase 6 (#33)" in src, (
        "templates/help.html must carry a ``Round 71 / Phase 6 (#33)`` "
        "marker so the audit grep finds the change point."
    )
