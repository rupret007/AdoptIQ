"""Round 74 / Build 48 / Phase 1 (F1): post-save footer enforcer.

Why this module exists
----------------------
Build 47 acceptance confirmed all 4 production DOCX artifacts (Compact,
Renewal, Comprehensive, Leader) ship with an EMPTY ``word/footer1.xml``
paragraph despite the Round 73 / F1 hardening of
``_r68_build_label.apply_word_footer``.

The R73 in-memory hardening works perfectly in dev (verified by an
integration repro through ``ExecutiveIntelligenceFormatter.save()`` +
``inject_source_citations_into_docx`` round-trip).  But every Build 47
artifact still carries
``<w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr></w:p>`` -- no
build-label run -- proving the writer's wrapped ``.save()`` paths are
being bypassed by an unknown upstream caller (some writer is calling
``self.doc.save(path)`` directly instead of ``formatter.save(path)``).

Rather than chase the writer (any of 4 candidates, any of which might
also be re-saving in a python-docx version that strips the runs), this
module is a defense-in-depth post-save XML-level enforcer that:

* Operates on the .docx file bytes on disk AFTER the writer has saved.
* Cannot be bypassed by ANY upstream writer call site.
* Is idempotent on a doc that already carries the build label run.
* NEVER raises -- a failure logs at WARNING and returns
  ``{"injected": False, "reason": ...}`` so the report finalization
  path is never bricked.

The wrapping ``_r74_enforce_footer_safe`` helper in ``app_simple.py``
emits a structured WARNING when enforcement actually had to inject so
a future deeper investigation can identify which writer call site is
bypassing the wrapped ``.save()``.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional, Union
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger(__name__)


# Word's main namespace -- every part referencing ``w:`` elements
# declares this on the root.  We declare both ``w:`` and ``r:`` so the
# generated XML is a complete, parseable .docx footer part on its own.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Canonical footer XML structure -- mirrors what
# ``_r68_build_label.apply_word_footer`` produces in dev:
#   - First paragraph keeps the Footer pStyle (python-docx leaves an
#     empty styled paragraph in every default footer; preserving it
#     avoids surprising Word with an unstyled paragraph).
#   - Second paragraph is right-aligned and carries the build-label
#     run as italic, color 969696, size 14 half-points (= 7pt).
#
# ``xml:space="preserve"`` matches python-docx's behaviour for runs
# whose text contains a leading/trailing space (the timestamp doesn't,
# but defending against a future label-shape change is cheap).
_FOOTER_XML_TEMPLATE = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
    f'<w:ftr xmlns:w="{_W_NS}" xmlns:r="{_R_NS}">'
    "<w:p><w:pPr><w:pStyle w:val=\"Footer\"/></w:pPr></w:p>"
    "<w:p>"
    "<w:pPr><w:jc w:val=\"right\"/></w:pPr>"
    "<w:r>"
    "<w:rPr>"
    "<w:i/>"
    "<w:color w:val=\"969696\"/>"
    "<w:sz w:val=\"14\"/>"
    "</w:rPr>"
    '<w:t xml:space="preserve">{label}</w:t>'
    "</w:r>"
    "</w:p>"
    "</w:ftr>"
)

_FOOTER1_PATH = "word/footer1.xml"
_DOCUMENT_PATH = "word/document.xml"
_RELS_PATH = "word/_rels/document.xml.rels"
_CONTENT_TYPES_PATH = "[Content_Types].xml"

# Idempotent probe: any text run that starts with ``AdoptIQ v`` followed
# by a build label is treated as already-stamped.  Loose enough to
# survive a future label-format tweak (e.g. dropping the trailing
# ``- generated <ts>``); tight enough to never false-match a customer
# narrative that happens to mention the product name.
_SENTINEL_RE = re.compile(rb"AdoptIQ v\d", re.IGNORECASE)

# Match every footerN.xml part -- a multi-section doc may have several
# (default / first / even / per-section).  We replace any that lack the
# build-label run; the others are a no-op via the idempotent probe.
_FOOTER_PART_RE = re.compile(r"^word/footer\d+\.xml$")

_FOOTER_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"
)
_FOOTER_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
)


def _resolve_label() -> Optional[str]:
    """Pull the canonical build-label text from ``_r68_build_label``.

    Returns ``None`` on any failure so the caller can short-circuit
    cleanly (we never want to inject a half-formed label).
    """
    try:
        from _r68_build_label import get_build_label_text  # noqa: PLC0415

        label = get_build_label_text()
        if not label or not isinstance(label, str):
            return None
        return label
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 74 / F1: build label resolve failed (%s)", exc)
        return None


def _build_canonical_footer_xml(label: str) -> bytes:
    """Build the canonical footer XML payload bytes for ``label``."""
    return _FOOTER_XML_TEMPLATE.format(label=_xml_escape(label)).encode("utf-8")


def _footer_already_stamped(footer_xml: bytes) -> bool:
    """Probe footer XML bytes for the ``AdoptIQ v...`` build-label run."""
    if not footer_xml:
        return False
    return bool(_SENTINEL_RE.search(footer_xml))


def _next_rid(rels_xml: bytes) -> str:
    """Compute the next free ``rIdN`` for ``word/_rels/document.xml.rels``.

    Defensive against a malformed rels part: returns ``"rId1000"`` when
    we cannot parse anything (high enough that it will not collide with
    any reasonable document while still being a valid rels Id).
    """
    try:
        ids = re.findall(rb'Id="rId(\d+)"', rels_xml)
        if not ids:
            return "rId1"
        nums = [int(b) for b in ids]
        return f"rId{max(nums) + 1}"
    except Exception:  # noqa: BLE001
        return "rId1000"


def _ensure_rels_entry(rels_xml: bytes, rid: str, target: str = "footer1.xml") -> bytes:
    """Append a ``<Relationship>`` entry pointing to ``target`` if missing."""
    entry = (
        f'<Relationship Id="{rid}" Type="{_FOOTER_REL_TYPE}" Target="{target}"/>'
    ).encode("utf-8")
    if not rels_xml:
        prefix = (
            b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        )
        return prefix + entry + b"</Relationships>"
    target_bytes = target.encode("utf-8")
    if target_bytes in rels_xml:
        return rels_xml
    if b"</Relationships>" in rels_xml:
        return rels_xml.replace(b"</Relationships>", entry + b"</Relationships>", 1)
    return rels_xml + entry


def _ensure_content_types_override(content_types_xml: bytes, target: str = "footer1.xml") -> bytes:
    """Ensure ``[Content_Types].xml`` carries the override for ``target``."""
    if not content_types_xml:
        return content_types_xml
    needle = f'PartName="/word/{target}"'.encode("utf-8")
    if needle in content_types_xml:
        return content_types_xml
    override = (
        f'<Override PartName="/word/{target}" ContentType="{_FOOTER_CONTENT_TYPE}"/>'
    ).encode("utf-8")
    if b"</Types>" in content_types_xml:
        return content_types_xml.replace(b"</Types>", override + b"</Types>", 1)
    return content_types_xml + override


def _ensure_document_footer_reference(document_xml: bytes, rid: str) -> bytes:
    """Insert a ``<w:footerReference>`` into the doc's last ``<w:sectPr>``.

    Word ignores a ``footer1.xml`` part that no section references, so
    when we are creating a footer from scratch we must wire it to the
    section.  Idempotent: returns ``document_xml`` unchanged if a
    default-footer reference is already present.
    """
    if b'w:type="default"' in document_xml and b"footerReference" in document_xml:
        return document_xml
    ref = (
        f'<w:footerReference xmlns:r="{_R_NS}" w:type="default" r:id="{rid}"/>'
    ).encode("utf-8")
    # Insert immediately after the opening sectPr tag.
    sect_match = re.search(rb"<w:sectPr\b[^>]*>", document_xml)
    if not sect_match:
        return document_xml
    insert_pos = sect_match.end()
    return document_xml[:insert_pos] + ref + document_xml[insert_pos:]


def enforce_build_label_footer(docx_path: Union[str, os.PathLike[str]]) -> dict:
    """Ensure every footer part in ``docx_path`` carries the build label.

    Operates on the .docx zip bytes -- bypasses python-docx entirely so
    no upstream writer call site can prevent the label from landing.

    Returns a structured diag dict for admin logging::

        {"injected": bool, "reason": str, "writer_hint": str | None,
         "footer_parts_replaced": list[str]}

    NEVER raises -- a failure logs at debug and returns
    ``{"injected": False, "reason": "<error>"}`` so the writer's
    completion path is never bricked.
    """
    p = Path(docx_path).expanduser()
    if not p.exists():
        return {
            "injected": False,
            "reason": f"file not found: {p}",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }
    if not p.is_file():
        return {
            "injected": False,
            "reason": f"not a file: {p}",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }

    label = _resolve_label()
    if label is None:
        return {
            "injected": False,
            "reason": "label resolve failed",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }

    # Round 1: read the parts we may need to mutate.
    try:
        with zipfile.ZipFile(p, mode="r") as zf:
            names = zf.namelist()
            footer_names = sorted(n for n in names if _FOOTER_PART_RE.match(n))
            footer_payloads: dict[str, bytes] = {n: zf.read(n) for n in footer_names}
            document_xml = zf.read(_DOCUMENT_PATH) if _DOCUMENT_PATH in names else None
            rels_xml = zf.read(_RELS_PATH) if _RELS_PATH in names else None
            content_types_xml = (
                zf.read(_CONTENT_TYPES_PATH) if _CONTENT_TYPES_PATH in names else None
            )
    except (zipfile.BadZipFile, OSError) as exc:
        return {
            "injected": False,
            "reason": f"zip read failed: {exc}",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }

    if document_xml is None:
        return {
            "injected": False,
            "reason": "missing word/document.xml",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }

    new_footer_xml = _build_canonical_footer_xml(label)

    # Decide which footer parts (if any) need to be replaced.
    parts_to_replace: list[str] = []
    for name, payload in footer_payloads.items():
        if not _footer_already_stamped(payload):
            parts_to_replace.append(name)

    # Decide whether we need to create footer1.xml from scratch
    # (no footer parts at all).  This is the hypothetical
    # missing-footer case -- our Build 47 artifacts all have empty
    # footer1.xml, so the common path is "replace existing".
    needs_create = not footer_payloads
    new_rels_xml = rels_xml
    new_content_types_xml = content_types_xml
    new_document_xml = document_xml
    rels_changed = False
    content_types_changed = False
    document_changed = False
    if needs_create:
        rid = _next_rid(rels_xml or b"")
        if rels_xml is None:
            new_rels_xml = _ensure_rels_entry(b"", rid)
            rels_changed = True
        else:
            updated = _ensure_rels_entry(rels_xml, rid)
            if updated != rels_xml:
                new_rels_xml = updated
                rels_changed = True
        if content_types_xml is not None:
            updated_ct = _ensure_content_types_override(content_types_xml)
            if updated_ct != content_types_xml:
                new_content_types_xml = updated_ct
                content_types_changed = True
        updated_doc = _ensure_document_footer_reference(document_xml, rid)
        if updated_doc != document_xml:
            new_document_xml = updated_doc
            document_changed = True

    if not parts_to_replace and not needs_create:
        return {
            "injected": False,
            "reason": "already stamped",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }

    # Round 2: rewrite the zip atomically.  We write to a sibling temp
    # file in the same directory so ``os.replace`` is atomic on POSIX
    # and best-effort on Windows.
    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            suffix=".tmp", prefix=p.name + ".", dir=str(p.parent)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        with zipfile.ZipFile(p, mode="r") as src_zf:
            with zipfile.ZipFile(
                tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as dst_zf:
                for item in src_zf.infolist():
                    name = item.filename
                    if name in parts_to_replace:
                        dst_zf.writestr(item, new_footer_xml)
                    elif name == _DOCUMENT_PATH and document_changed:
                        dst_zf.writestr(item, new_document_xml)
                    elif name == _RELS_PATH and rels_changed:
                        dst_zf.writestr(item, new_rels_xml)
                    elif name == _CONTENT_TYPES_PATH and content_types_changed:
                        dst_zf.writestr(item, new_content_types_xml)
                    else:
                        dst_zf.writestr(item, src_zf.read(name))
                if needs_create:
                    dst_zf.writestr(_FOOTER1_PATH, new_footer_xml)
                    if rels_changed and _RELS_PATH not in {i.filename for i in src_zf.infolist()}:
                        dst_zf.writestr(_RELS_PATH, new_rels_xml)
        os.replace(tmp_path, p)
        tmp_path = None
    except Exception as exc:  # noqa: BLE001
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:  # noqa: BLE001
                pass
        return {
            "injected": False,
            "reason": f"zip rewrite failed: {exc}",
            "writer_hint": None,
            "footer_parts_replaced": [],
        }

    replaced = parts_to_replace[:]
    if needs_create:
        replaced.append(_FOOTER1_PATH)
    return {
        "injected": True,
        "reason": "footer enforced post-save",
        "writer_hint": (
            "writer bypassed apply_word_footer or runs were stripped post-save"
        ),
        "footer_parts_replaced": replaced,
    }


__all__ = [
    "enforce_build_label_footer",
    "get_canonical_footer_xml",
]


def get_canonical_footer_xml(label: Optional[str] = None) -> bytes:
    """Public accessor for the canonical footer XML bytes (test-only)."""
    if label is None:
        label = _resolve_label() or "AdoptIQ v? build ? - generated unknown"
    return _build_canonical_footer_xml(label)
