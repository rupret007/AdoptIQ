#!/usr/bin/env python3
"""Round 114 / Build 83 -- read-only Build-82 acceptance audit.

Scans the four most-recent Build-82 report artifacts for the documented
known-issue checklist (mid-string citation injection, residual per-cell
clutter, markdown chrome leakage, stub bullets, Snowflake global-config
tokens, NaN/Unknown customer rows, HTML leakage, duplicate XLSX IDs,
risk-score saturation).  Pure read-only; mutates nothing.  Made-with: Cursor.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from openpyxl import load_workbook

REPORTS = Path.home() / "Documents" / "AdoptIQ Reports"

TARGETS = {
    "Leader": REPORTS / "Brian_Frazier/Leader/AdoptIQ_Report_Leader_Brian_Frazier_90d_20260528_204710",
    "Comprehensive": REPORTS / "All_Managers/Comprehensive/AdoptIQ_Report_All_Managers_Webex_Calling_90d_20260528_154614",
    "Compact": REPORTS / "All_Managers/Compact/AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_20260528_154646",
    "Renewal": REPORTS / "All_Managers/Renewal/AdoptIQ_Report_Renewal_Portfolio_All_Managers_All_Contact_Center_90d_20260528_154629",
}

# Round 115 / Build 84: filename-token -> report-type classifier for the
# ``--auto`` discovery mode so the audit always targets the most-recent
# artifact of each type without a hand-edited TARGETS map.
_AUTO_TYPE_MATCHERS = (
    ("Leader", re.compile(r"AdoptIQ_Report_Leader_", re.IGNORECASE)),
    ("Compact", re.compile(r"AdoptIQ_Report_Compact_", re.IGNORECASE)),
    ("Renewal", re.compile(r"AdoptIQ_Report_Renewal_", re.IGNORECASE)),
    # Comprehensive has no explicit token; it's the AdoptIQ_Report_<Manager>
    # shape that is NOT Leader/Compact/Renewal.  Matched last as the default.
    ("Comprehensive", re.compile(r"AdoptIQ_Report_", re.IGNORECASE)),
)


def _classify_report_type(stem: str) -> str | None:
    for name, rx in _AUTO_TYPE_MATCHERS:
        if rx.search(stem):
            return name
    return None


def discover_latest_targets(root: Path = REPORTS) -> dict[str, Path]:
    """Round 115: find the most-recent ``.docx`` of each report type under
    ``root`` (by mtime) and return ``{type: base_path_without_ext}``.

    Pairs a ``.docx`` with its sibling ``.xlsx`` via the shared stem so the
    audit can cross-check narrative + workbook.  Never raises; missing types
    are simply absent from the returned map.
    """
    latest: dict[str, tuple[float, Path]] = {}
    if not root.is_dir():
        return {}
    for docx in root.rglob("*.docx"):
        try:
            mtime = docx.stat().st_mtime
        except OSError:
            continue
        rtype = _classify_report_type(docx.stem)
        if not rtype:
            continue
        if rtype not in latest or mtime > latest[rtype][0]:
            latest[rtype] = (mtime, docx.with_suffix(""))
    return {name: base for name, (_, base) in latest.items()}

# Citation chrome immediately GLUED to an alphanumeric word with no
# separator -> the true mid-string injection signature (R64/B4, R66/B1,
# R90: ``90 [Source: ...]Days`` / ``0-10 [Source: ...]scale``).  A
# trailing space / newline / punctuation / quote / pipe / backtick is the
# CORRECT end-of-segment placement and must NOT flag.
MID_STRING_RE = re.compile(r"\[source:[^\]]*\][A-Za-z0-9]", re.IGNORECASE)
SOURCE_RE = re.compile(r"\[source:", re.IGNORECASE)
# Standalone *italic* or **bold** markdown chrome (R88). Avoid matching
# bullet glyphs / multiplication; require letter-adjacent asterisks.
MD_BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*")
MD_ITALIC_RE = re.compile(r"(?<![*\w])\*([^*\n]{2,}?)\*(?![*\w])")
STUB_RE = re.compile(r"^\s*\*{0,2}[A-Z][A-Za-z /]+:\*{0,2}\s*\*{0,2}Data unavailable\.?\*{0,2}\s*$")
GLOBAL_TOKENS = (
    "not authorized",
    "does not exist",
    "refused by table policy",
    "invalid identifier",
    "column_missing",
    "blocked by policy",
    "not in allowlist policy",
)
# R76 tokens only matter when they surface as a *Snowflake section error*
# (the suppression-banner context).  The SAME phrases legitimately appear
# in customer-facing case content (SIP "Does Not Exist" error narrative,
# a "Agent Can't Login - Not Authorized" TAC subject) and MUST NOT flag.
# NB: do NOT include "data source" here -- the citation chrome
# "[Source: AdoptIQ Report Data Sources]" carries that phrase and would
# wrongly re-arm the token on a cited case subject.
SNOWFLAKE_ERR_CONTEXT_RE = re.compile(
    r"section|snowflake|insight|unavailable|could not retrieve|suppress|"
    r"allowlist|table policy",
    re.IGNORECASE,
)
CASE_CONTENT_CONTEXT_RE = re.compile(
    r"root cause|sip|error code|tac|severity:|status:|subject|"
    r"can'?t login|response in|signaling",
    re.IGNORECASE,
)
HTML_RE = re.compile(r"<br\s*/?>|&#\d+;|<script|<style|<agent", re.IGNORECASE)
NANISH = {"nan", "none", "unknown", "n/a", "null", "<na>"}


def _para_texts(doc: Document) -> list[str]:
    out = [p.text for p in doc.paragraphs if (p.text or "").strip()]
    return out


def _cell_texts(doc: Document) -> list[str]:
    out = []
    for t in doc.tables:
        for r in t.rows:
            for c in r.cells:
                if (c.text or "").strip():
                    out.append(c.text)
    return out


def _nanish_cell_context(doc: Document) -> list[str]:
    """Return ``table#/row#: col0='..' | <nanish cell>`` for each nanish data cell."""
    out: list[str] = []
    for ti, t in enumerate(doc.tables):
        rows = list(t.rows)
        header = [c.text.strip() for c in rows[0].cells] if rows else []
        for ri, r in enumerate(rows):
            cells = [c.text.strip() for c in r.cells]
            for ci, val in enumerate(cells):
                if val.lower() in NANISH:
                    col = header[ci] if ci < len(header) else f"col{ci}"
                    label = cells[0] if cells else ""
                    out.append(f"t{ti}r{ri} col='{col}' row0='{label[:30]}' -> '{val}'")
    return out


def audit_docx(path: Path) -> dict:
    doc = Document(str(path))
    paras = _para_texts(doc)
    cells = _cell_texts(doc)
    alltext = paras + cells
    findings = {}

    findings["mid_string_citations"] = [t[:160] for t in alltext if MID_STRING_RE.search(t)]
    findings["per_cell_citations"] = sum(1 for c in cells if SOURCE_RE.search(c))
    findings["caption_paragraphs"] = sum(1 for p in paras if p.strip().startswith("Sources:"))
    md_hits = []
    for t in alltext:
        if MD_BOLD_RE.search(t) or MD_ITALIC_RE.search(t):
            md_hits.append(t[:120])
    findings["markdown_chrome"] = md_hits
    findings["stub_bullets"] = [t[:120] for t in alltext if STUB_RE.match(t)]
    gl = []
    for t in alltext:
        low = t.lower()
        for tok in GLOBAL_TOKENS:
            if tok not in low:
                continue
            # Only a Snowflake-section-error context counts (R76).  Skip
            # legitimate customer case content where the phrase appears.
            if CASE_CONTENT_CONTEXT_RE.search(t) and not SNOWFLAKE_ERR_CONTEXT_RE.search(t):
                continue
            gl.append((tok, t[:120]))
    findings["global_config_tokens"] = gl
    findings["html_leakage"] = [t[:120] for t in alltext if HTML_RE.search(t)]
    # NaN/Unknown customer-ish cells: cells whose entire content is a nanish token.
    findings["nanish_cells"] = sum(
        1 for c in cells if c.strip().lower() in NANISH
    )
    findings["nanish_context"] = _nanish_cell_context(doc)
    return findings


def _extract_customers_in_portfolio(rows: list) -> int | None:
    """Round 116 / Build 85 (B): pull the 'Customers in portfolio' count
    from a Summary-style ``(Item, Value)`` sheet so the audit can surface
    the headline customer-count for the ACC count-floor regression check.

    Returns ``None`` when the sheet is not an Item/Value summary or the
    row is absent.  Never raises.
    """
    if not rows:
        return None
    header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    # Item/Value (Report_Info) OR Metric/Value (Summary) sheet shape.
    if "value" not in header:
        return None
    if "item" in header:
        item_idx = header.index("item")
    elif "metric" in header:
        item_idx = header.index("metric")
    else:
        return None
    val_idx = header.index("value")
    for r in rows[1:]:
        if item_idx >= len(r) or val_idx >= len(r):
            continue
        label = str(r[item_idx] or "").strip().lower()
        if "customers in portfolio" in label:
            try:
                return int(str(r[val_idx]).strip().split()[0])
            except (ValueError, IndexError, AttributeError):
                return None
    return None


def audit_xlsx(path: Path) -> dict:
    wb = load_workbook(str(path), read_only=True, data_only=True)
    findings = {
        "dup_ids": {},
        "risk_saturation": {},
        "html_cells": 0,
        "sheets": wb.sheetnames,
        "customers_in_portfolio": None,
    }
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        # Round 116 / B: surface the headline customer count from the
        # Summary sheet so the ACC count-floor regression is auditable.
        if findings["customers_in_portfolio"] is None:
            _cust = _extract_customers_in_portfolio(rows)
            if _cust is not None:
                findings["customers_in_portfolio"] = _cust
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        lower = [h.lower() for h in header]
        # Duplicate IDs.
        if "id" in lower:
            idx = lower.index("id")
            ids = [r[idx] for r in rows[1:] if idx < len(r) and r[idx] not in (None, "")]
            seen, dups = set(), 0
            for v in ids:
                if v in seen:
                    dups += 1
                seen.add(v)
            if dups:
                findings["dup_ids"][ws.title] = dups
        # Risk-score 0-10 saturation: any 0-10 column value > 10.
        for ci, h in enumerate(lower):
            if (h == "risk_score_0_10" or h == "overall_risk_score") and "0_100" not in h:
                bad = 0
                for r in rows[1:]:
                    if ci < len(r) and isinstance(r[ci], (int, float)) and r[ci] > 10.0001:
                        bad += 1
                if bad:
                    findings["risk_saturation"][f"{ws.title}.{header[ci]}"] = bad
        # HTML leakage in any cell.
        for r in rows[1:]:
            for v in r:
                if isinstance(v, str) and HTML_RE.search(v):
                    findings["html_cells"] += 1
    wb.close()
    return findings


def _resolve_targets(args: argparse.Namespace) -> dict[str, Path]:
    """Round 115 / Build 84: resolve the audit target map from CLI args.

    Precedence: explicit ``--target NAME=BASE`` overrides win; otherwise
    ``--auto`` discovers the latest of each type; otherwise the hard-coded
    ``TARGETS`` map is used (back-compat with the Build-83 invocation).
    """
    if args.target:
        out: dict[str, Path] = {}
        for spec in args.target:
            if "=" not in spec:
                print(f"  (ignoring malformed --target {spec!r}; expected NAME=BASE)")
                continue
            name, base = spec.split("=", 1)
            # Strip a trailing .docx/.xlsx so callers can paste a full path.
            base_path = Path(base)
            if base_path.suffix.lower() in (".docx", ".xlsx"):
                base_path = base_path.with_suffix("")
            out[name.strip()] = base_path
        return out
    if args.auto:
        root = Path(args.reports_root) if args.reports_root else REPORTS
        discovered = discover_latest_targets(root)
        if not discovered:
            print(f"  (--auto found no AdoptIQ reports under {root})")
        return discovered
    return dict(TARGETS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AdoptIQ report acceptance audit (R114/R115).")
    parser.add_argument(
        "--auto", action="store_true",
        help="Discover the latest .docx of each report type under the reports root.",
    )
    parser.add_argument(
        "--target", action="append", metavar="NAME=BASE",
        help="Explicit target: report type name = base path (no extension). Repeatable.",
    )
    parser.add_argument(
        "--reports-root", default=None,
        help=f"Override the reports root for --auto (default: {REPORTS}).",
    )
    args = parser.parse_args(argv)
    targets = _resolve_targets(args)

    any_critical = False
    for name, base in targets.items():
        docx = Path(str(base) + ".docx")
        xlsx = Path(str(base) + ".xlsx")
        print(f"\n{'='*70}\n{name}\n{'='*70}")
        if docx.exists():
            d = audit_docx(docx)
            print(f"DOCX {docx.name}")
            print(f"  per_cell_citations: {d['per_cell_citations']}  (R114 pre-fix clutter)")
            print(f"  caption_paragraphs: {d['caption_paragraphs']}")
            for key in ("mid_string_citations", "markdown_chrome", "stub_bullets",
                        "global_config_tokens", "html_leakage"):
                items = d[key]
                flag = "  <-- REVIEW" if items else ""
                print(f"  {key}: {len(items)}{flag}")
                for it in items[:6]:
                    print(f"      {it}")
                if items and key in ("mid_string_citations", "markdown_chrome",
                                     "global_config_tokens", "html_leakage", "stub_bullets"):
                    any_critical = True
            print(f"  nanish_cells: {d['nanish_cells']}")
            for it in d["nanish_context"][:12]:
                print(f"      {it}")
        else:
            print(f"DOCX MISSING: {docx}")
        if xlsx.exists():
            x = audit_xlsx(xlsx)
            print(f"XLSX {xlsx.name}")
            print(f"  dup_ids: {x['dup_ids'] or 'none'}")
            print(f"  risk_saturation: {x['risk_saturation'] or 'none'}")
            print(f"  html_cells: {x['html_cells']}")
            # Round 116 / B: ACC count-floor visibility.  A Comprehensive /
            # Compact "All Contact Center" run that shows a suspiciously low
            # headline (the Build-83 "24" regression) is now surfaced here.
            _cust = x.get("customers_in_portfolio")
            if _cust is not None:
                _low = "  <-- LOW? confirm against team CC roster" if (
                    "contact_center" in xlsx.name.lower() and _cust < 30
                ) else ""
                print(f"  customers_in_portfolio: {_cust}{_low}")
            if x["dup_ids"] or x["risk_saturation"] or x["html_cells"]:
                any_critical = True
        else:
            print(f"XLSX MISSING: {xlsx}")
    print(f"\n{'='*70}\nCRITICAL_ISSUES_FOUND={any_critical}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
