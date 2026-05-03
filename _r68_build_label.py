"""Round 68 / Build 42 (Phase 0 / A1): build label helpers shared by every
report writer.

Why this module exists
----------------------
Build 41 acceptance surfaced a process-trap class bug: the operator built and
copied the new ``.app`` into ``/Applications`` but did not quit and re-launch
the running AdoptIQ process. The result was that every report produced
between the install and the next restart came from the *previous* binary --
none of the Round 67 source patches reached those reports, but the user had
no in-band way to spot it. The Round 67 source markers were correctly wired
(verified by ``grep 'Round 67' app_simple.py``); they just never executed.

This trap has bitten the project before (Round 61 / "version footgun" closed
the BUILD-script side; Round 68 / Phase 0 closes the INSTALL side). The
defense is to stamp the running binary's version + build + start time into
*every* artifact so an auditor can spot a stale-binary report at a glance:

* Excel: 4 rows in the canonical ``Report_Info`` sheet.
* Word: a small right-aligned section footer on every page.

Both helpers are deliberately defensive -- a failure to attach the label
must never abort report generation, since a slightly-less-traceable artifact
is much better than no artifact at all.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple

logger = logging.getLogger(__name__)

# Module-level cache of the process start time.  Captured the first time
# this module is imported (which happens during ``app_simple.py`` startup
# because ``adoptiq_backend`` and the per-report writers import it).  We
# also honor ``ADOPTIQ_PROCESS_STARTED_AT_UTC`` if the parent process set
# it explicitly -- this lets the admin sub-process inherit the main app's
# start time so both surfaces report the same value.
_ENV_KEY = "ADOPTIQ_PROCESS_STARTED_AT_UTC"


def _now_iso_z() -> str:
    """UTC ISO-8601 with second precision and a trailing ``Z``."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _capture_process_start() -> str:
    existing = os.environ.get(_ENV_KEY)
    if existing:
        return existing
    stamp = _now_iso_z()
    # ``setdefault`` so a parent already-set value wins; we only mint when
    # we are the first caller in the process.
    os.environ.setdefault(_ENV_KEY, stamp)
    return os.environ[_ENV_KEY]


_PROCESS_STARTED_AT_UTC: str = _capture_process_start()


def get_process_started_at_utc() -> str:
    """Return the UTC ISO-Z timestamp captured at first import."""

    return _PROCESS_STARTED_AT_UTC


def _resolve_version_build() -> Tuple[str, str]:
    """Pull (version, build) from ``config.py`` defensively.

    A failure here returns ``("?", "?")`` so the report writer can still
    stamp *something* -- a "?" is loud enough to prompt an investigation.
    """

    try:
        from config import ADOPTIQ_BUILD, ADOPTIQ_VERSION  # noqa: PLC0415

        return str(ADOPTIQ_VERSION), str(ADOPTIQ_BUILD)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 68 / A1: version constants unreadable (%s)", exc)
        return "?", "?"


def _resolve_code_loaded_at_utc() -> str:
    """Best-effort mtime of the ``app_simple.py`` source on disk."""

    try:
        # Importing here keeps the helper standalone-safe.
        import app_simple  # noqa: PLC0415

        path_str = getattr(app_simple, "__file__", None)
        if not path_str:
            return ""
        ts = Path(path_str).stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 68 / A1: code mtime unreadable (%s)", exc)
        return ""


def _resolve_dmg_install_at_utc() -> str:
    """Mtime of ``sys.executable`` -- when frozen this is the .app binary
    that was installed.  Returns ``""`` on dev (where it is the python
    interpreter and the value would be misleading)."""

    try:
        import sys  # noqa: PLC0415

        if not getattr(sys, "frozen", False):
            return ""
        ts = Path(sys.executable).stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 68 / A1: dmg install mtime unreadable (%s)", exc)
        return ""


def get_build_label_rows() -> list[Tuple[str, str]]:
    """Return the 4 canonical (item, value) rows the Excel writers stamp.

    Order is stable so downstream tests can pin positions if needed:

    * ``App_Version``           -- ``ADOPTIQ_VERSION`` constant
    * ``App_Build``             -- ``ADOPTIQ_BUILD`` constant
    * ``Process_Started_At_UTC`` -- captured at first import
    * ``Report_Generated_At_UTC`` -- ``now()`` per call
    """

    version, build = _resolve_version_build()
    return [
        ("App_Version", version),
        ("App_Build", build),
        ("Process_Started_At_UTC", _PROCESS_STARTED_AT_UTC),
        ("Report_Generated_At_UTC", _now_iso_z()),
    ]


def get_build_label_text() -> str:
    """Return the one-line label used in Word footers."""

    version, build = _resolve_version_build()
    return f"AdoptIQ v{version} build {build} - generated {_now_iso_z()}"


def apply_word_footer(doc) -> bool:
    """Attach a small right-aligned build label to every section footer.

    Returns ``True`` on success and ``False`` on any failure (logged at
    warning -- *never* raises).  Idempotent: if a section already carries
    the AdoptIQ build-label run, the helper skips it so a re-save does
    not duplicate the line.

    Round 73 / Phase 1 (F1) hardening:

    * Explicitly clear ``section.footer.is_linked_to_previous`` so a
      multi-section document with one section linked-to-previous cannot
      hide the label by inheriting an unstamped footer from an earlier
      section.
    * Explicitly clear ``section.different_first_page_header_footer`` so
      a document with a special first-page footer (the executive
      intelligence formatter sets one) does not silently route the
      label into the unused per-section default while the rendered
      first page stays unstamped.
    * When the footer's first paragraph already exists but carries no
      runs, force ``footer.add_paragraph()`` instead of reusing the
      empty paragraph -- some python-docx versions silently no-op
      ``.text = ""`` followed by ``.add_run(...)`` on a linked-empty
      paragraph, which would leave the rendered footer blank even
      though our test asserts the run was added.
    * Wrap each section-level setter in its own try/except so a single
      python-docx API drift cannot cascade into "no sections stamped"
      -- worst case a partially-stamped document still beats an
      entirely unstamped one.
    * Promote the outer swallow log from ``debug`` to ``warning`` so
      the next regression surfaces in the admin error log instead of
      hiding under the default debug threshold.
    """

    try:
        # python-docx imports kept local so this module can be imported in
        # places that do not depend on docx (e.g. early CLI / test paths).
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415
        from docx.shared import Pt, RGBColor  # noqa: PLC0415

        label = get_build_label_text()
        sentinel_prefix = "AdoptIQ v"
        for section in doc.sections:
            # Round 73 / Phase 1 (F1): clear inheritance flags BEFORE we
            # read ``section.footer`` so the footer object we touch is
            # the section's own and not a linked reference.  Each setter
            # is wrapped so a python-docx version that raises on the
            # write does not abort the whole stamping pass.
            try:
                section.footer.is_linked_to_previous = False
            except Exception as _link_err:  # noqa: BLE001
                logger.debug(
                    "Round 73 / F1: clear is_linked_to_previous skipped (%s)",
                    _link_err,
                )
            try:
                section.different_first_page_header_footer = False
            except Exception as _first_err:  # noqa: BLE001
                logger.debug(
                    "Round 73 / F1: clear different_first_page_header_footer skipped (%s)",
                    _first_err,
                )

            footer = section.footer
            already = False
            for para in footer.paragraphs:
                if para.text.strip().startswith(sentinel_prefix):
                    already = True
                    break
            if already:
                continue
            # Round 73 / Phase 1 (F1): if the footer carries an existing
            # paragraph but it has zero runs, treat it as an unhealthy
            # linked-empty paragraph and add a fresh one instead -- some
            # python-docx versions silently no-op ``.text = ""`` followed
            # by ``.add_run(...)`` on such a paragraph.  When the first
            # paragraph already has a run we reuse it (preserves the R68
            # behaviour of avoiding an empty leading line python-docx
            # would otherwise add).
            existing_paragraphs = list(footer.paragraphs)
            if existing_paragraphs and existing_paragraphs[0].runs:
                target_para = existing_paragraphs[0]
                target_para.text = ""
            else:
                target_para = footer.add_paragraph()
            target_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = target_para.add_run(label)
            run.font.size = Pt(7)
            run.font.color.rgb = RGBColor(150, 150, 150)
            run.font.italic = True
        return True
    except Exception as exc:  # noqa: BLE001
        # Round 73 / Phase 1 (F1): promoted from debug to warning -- a
        # missing footer means the operator's report has lost the
        # stale-binary trap detection mechanism, which is the single
        # most expensive class of audit bug we have shipped (Build 41,
        # Build 43, Build 46).  The next regression must be loud.
        logger.warning("Round 68 / A1: word footer skipped (%s)", exc)
        return False


def append_build_label_records(records: list, *, key_field: str = "Item", value_field: str = "Value") -> None:
    """Append build label rows to a list-of-dicts records buffer.

    Used by the Renewal / Leader writers which build a list of dict
    records before constructing a DataFrame.  Defensive against any
    unexpected ``records`` shape -- a failure logs and returns silently.
    """

    try:
        for item, value in get_build_label_rows():
            records.append({key_field: item, value_field: value})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 68 / A1: append build label rows skipped (%s)", exc)


def append_build_label_records_4col(records: list) -> None:
    """Append build label rows in the Leader writer's 4-column shape.

    Round 73 / Phase 3 (F6): the first column is now the canonical ``Item``
    header (was ``Field`` pre-R73).  The Leader writer keeps its 4-column
    legacy shape (``Item / Value / Detail / Generated_At``) -- the extra
    Detail and Generated_At slots carry per-row provenance the other
    writers don't need -- but the FIRST TWO columns now match the
    canonical Compact / Renewal / Comprehensive ``Item / Value`` schema
    so a downstream consumer running
    ``pd.read_excel("Report_Info")[["Item", "Value"]]`` gets the same
    projection across every report format.
    """

    try:
        for item, value in get_build_label_rows():
            records.append({
                "Item": item,
                "Value": value,
                "Detail": None,
                "Generated_At": None,
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 68 / A1: append build label rows (4col) skipped (%s)", exc)


def append_build_label_rows_pairs(rows: list) -> None:
    """Append build label rows to a list-of-pairs ``[item, value]`` buffer.

    Used by ``adoptiq_backend.write_excel_workbook`` which keeps Report_Info
    as a list of two-element lists before DataFrame conversion.
    """

    try:
        for item, value in get_build_label_rows():
            rows.append([item, value])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 68 / A1: append build label rows (pairs) skipped (%s)", exc)
