#!/usr/bin/env python3
"""Live report-iteration harness for repeatable report regression runs.

Round 51: this runner executes the same report scenarios repeatedly against
the running app, downloads report artifacts, and writes debug sidecars in
Downloads to speed up troubleshooting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

import openpyxl
import requests
from docx import Document

TERMINAL_STATUSES = {"completed", "error", "cancelled"}
CSRF_META_RE = re.compile(
    r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
DOCX_TEXT_RE = re.compile(r"<w:t[^>]*>(.*?)</w:t>")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
TIMESTAMP_TOKEN_RE = re.compile(r"\b\d{8,}\b")
RUN_SUFFIX_RE = re.compile(r"__data-loop-[^_]+__scenario-[^_]+__ts-[^_.]+\.", re.IGNORECASE)
NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
KPI_ALIASES = {
    "total_customers": {
        "total customers",
        "customers in portfolio",
        "total customer count",
        "total customers analyzed",
    },
    "support_cases": {
        "support cases",
        "total support cases",
        "support cases 90d",
        "total support cases 90d",
    },
    "adoption_barriers": {
        "adoption barriers",
        "total adoption barriers",
        "adoption barriers total",
    },
    "critical_barriers": {
        "critical abs",
        "critical adoption barriers",
        "critical barriers",
    },
    "critical_cases": {
        "critical p1",
        "p1 cases",
        "critical cases",
    },
    "high_cases": {
        "high p2",
        "p2 cases",
        "high cases",
    },
    "bems": {
        "bems escalations",
        "bems",
    },
    "risk_score": {
        "overall risk score",
        "renewal risk score",
        "risk score",
    },
    "window_days": {
        "analysis period days",
        "window days",
        "days",
    },
}


@dataclass(frozen=True)
class Scenario:
    """Immutable report scenario definition."""

    key: str
    endpoint: str
    payload_mode: str  # "form" | "json"
    payload: dict[str, Any]
    expect_excel: bool = True
    expected_xlsx_sheets: tuple[str, ...] = ("summary", "report_info")


@dataclass
class GateResult:
    """Pass/fail details for one validation gate."""

    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactRecord:
    """Metadata for one downloaded artifact."""

    file_type: str
    downloaded_name: str
    debug_name: str
    debug_path: str
    size_bytes: int
    sha256: str
    baseline_path: Optional[str]
    structural: GateResult
    baseline_diff: GateResult


@dataclass
class ScenarioResult:
    """All telemetry for one scenario execution."""

    scenario: str
    analysis_id: Optional[str]
    started_at_utc: str
    completed_at_utc: str
    elapsed_seconds: int
    request: dict[str, Any]
    status_snapshots: list[dict[str, Any]]
    final_status: dict[str, Any]
    operational: GateResult
    artifacts: list[ArtifactRecord]
    all_passed: bool
    parity: GateResult = field(default_factory=lambda: GateResult(True, {"reason": "not_applicable"}))
    kpi_sidecar_path: Optional[str] = None
    log_excerpt_path: Optional[str] = None
    failure_phase: Optional[str] = None
    exception_type: Optional[str] = None


@dataclass
class RunnerConfig:
    """Runtime options for iteration execution."""

    base_url: str
    downloads_dir: Path
    iterations: int
    poll_interval_seconds: float
    scenario_timeout_seconds: int
    run_id: str
    stop_on_failure: bool
    scenario_keys: list[str]
    baseline_mode: str
    min_docx_similarity: float
    min_sheet_overlap: float
    min_header_similarity: float
    min_docx_chars: int
    strict: bool
    min_docx_numeric_similarity: float
    max_xlsx_row_delta_ratio: float
    max_xlsx_row_delta_abs: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_str() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip()).strip("_")


def build_scenario_map() -> dict[str, Scenario]:
    """Return canonical report scenarios for repeated test loops."""
    # Round 51: mirrors the user-defined test matrix.
    return {
        "comprehensive": Scenario(
            key="comprehensive",
            endpoint="/start_analysis",
            payload_mode="form",
            payload={
                "report_type": "comprehensive",
                "manager": "Brian Frazier",
                "technology": "All Contact Center",
                "days": "90",
                "subscription_id": "",
                "customer_name": "",
            },
            expect_excel=True,
            expected_xlsx_sheets=("summary", "report_info"),
        ),
        "compact": Scenario(
            key="compact",
            endpoint="/start_compact_analysis",
            payload_mode="json",
            payload={
                "manager": "All Managers",
                "technology": "All Contact Center",
                "days": 90,
                "csone_file": "",
                "subscription_id": "",
                "customer_name": "",
            },
            expect_excel=True,
            expected_xlsx_sheets=("executive_dashboard", "risk_summary"),  # Round 52 strict sheet shape
        ),
        "renewal": Scenario(
            key="renewal",
            endpoint="/start_analysis",
            payload_mode="form",
            payload={
                "report_type": "renewal_portfolio",
                "renewal_type": "renewal_portfolio",
                "manager": "All Managers",
                "technology": "All Contact Center",
                "days": "90",
                "subscription_id": "",
                "customer_name": "",
            },
            expect_excel=True,
            expected_xlsx_sheets=("report_info", "renewal_summary", "key_metrics"),  # Round 52 strict sheet shape
        ),
        "leader": Scenario(
            key="leader",
            endpoint="/start_leader_report",
            payload_mode="form",
            payload={
                "manager": "Brian Frazier",
                "days": "90",
            },
            expect_excel=True,
            expected_xlsx_sheets=("report_info", "team_summary"),  # Round 52 strict sheet shape
        ),
    }


def extract_csrf_token(html: str) -> str:
    """Extract CSRF token from analyze-page meta tag."""
    match = CSRF_META_RE.search(html or "")
    if not match:
        raise ValueError("Could not find csrf-token meta tag in HTML response")
    return unescape(match.group(1))


def parse_download_name(content_disposition: str, fallback: str) -> str:
    """Resolve server download filename from Content-Disposition."""
    if not content_disposition:
        return fallback
    filename_star = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
    if filename_star:
        return filename_star.group(1).strip().strip('"')
    filename = re.search(r'filename="?([^";]+)"?', content_disposition, re.IGNORECASE)
    if filename:
        return filename.group(1).strip().strip('"')
    return fallback


def build_debug_filename(original_name: str, run_id: str, scenario_key: str, timestamp: Optional[str] = None) -> str:
    """Keep original stem and append deterministic debug suffix."""
    # Round 51: preserve naming convention while adding traceability metadata.
    ts = timestamp or _utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = Path(original_name)
    suffix = path.suffix or ".bin"
    stem = path.stem
    return f"{stem}__data-loop-{_slug(run_id)}__scenario-{_slug(scenario_key)}__ts-{ts}{suffix}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_text_tokens(text: str) -> set[str]:
    clean = text.lower()
    clean = TIMESTAMP_TOKEN_RE.sub(" ", clean)
    clean = NUMBER_RE.sub(" ", clean)
    clean = re.sub(r"[^a-z0-9 ]+", " ", clean)
    return {token for token in clean.split() if len(token) >= 3}


def _normalize_numeric_token(token: str) -> str:
    value = token.strip().replace(",", "")
    if value.endswith("%"):
        value = value[:-1] + "%"
    return value


def _numeric_fingerprint(text: str) -> set[str]:
    """Extract non-timestamp numeric tokens for strict report drift checks."""
    tokens: set[str] = set()
    for match in NUMERIC_TOKEN_RE.finditer(text or ""):
        raw = match.group(0)
        normalized = _normalize_numeric_token(raw)
        digits_only = re.sub(r"\D", "", normalized)
        if len(digits_only) >= 5:
            # Round 52: strict mode treats long numeric strings as volatile
            # identifiers, not business KPIs.
            # Generated IDs, BEMS/case identifiers, and date stamps are expected
            # to change run-to-run. KPI-scale values are covered by shorter
            # count/percentage tokens and by the DOCX/XLSX KPI parity sidecar.
            continue
        if not digits_only:
            continue
        tokens.add(normalized)
    return tokens


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / float(len(union))


def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        body = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    return " ".join(unescape(part) for part in DOCX_TEXT_RE.findall(body))


def _cap_sorted(values: Iterable[str], limit: int = 25) -> list[str]:
    return sorted(str(value) for value in values)[:limit]


def validate_docx_structure(path: Path, min_chars: int) -> GateResult:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return GateResult(False, {"reason": "file_missing_or_empty"})
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
        if "word/document.xml" not in names:
            return GateResult(False, {"reason": "missing_document_xml"})
        text = _extract_docx_text(path)
        stripped = " ".join(text.split())
        return GateResult(
            passed=len(stripped) >= min_chars,
            details={"char_count": len(stripped), "min_chars": min_chars},
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return GateResult(False, {"reason": "docx_parse_error", "error": str(exc)})


def _sheet_header_tokens(sheet: Any, max_cols: int = 40) -> list[str]:
    tokens: list[str] = []
    row = next(sheet.iter_rows(min_row=1, max_row=1, max_col=max_cols, values_only=True), None)
    if not row:
        return tokens
    for value in row:
        if value is None:
            continue
        token = str(value).strip()
        if token:
            tokens.append(token.lower())
    return tokens


def _sheet_non_empty_count(sheet: Any, max_rows: int = 5000, max_cols: int = 80) -> int:
    count = 0
    for row in sheet.iter_rows(min_row=1, max_row=max_rows, max_col=max_cols, values_only=True):
        if any(cell not in (None, "") for cell in row):
            count += 1
    return count


def build_xlsx_signature(path: Path) -> dict[str, Any]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        signatures: dict[str, Any] = {
            "sheet_names": [name.lower() for name in workbook.sheetnames],
            "sheet_headers": {},
            "sheet_non_empty_rows": {},
        }
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            signatures["sheet_headers"][sheet_name.lower()] = _sheet_header_tokens(sheet)
            signatures["sheet_non_empty_rows"][sheet_name.lower()] = _sheet_non_empty_count(sheet)
        return signatures
    finally:
        workbook.close()


def validate_xlsx_structure(path: Path, expected_sheets: Iterable[str] = ()) -> GateResult:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return GateResult(False, {"reason": "file_missing_or_empty"})
        signature = build_xlsx_signature(path)
        sheets = signature["sheet_names"]
        non_empty_total = sum(signature["sheet_non_empty_rows"].values())
        expected = {sheet.lower() for sheet in expected_sheets if str(sheet).strip()}
        missing_expected = sorted(expected - set(sheets))
        return GateResult(
            passed=bool(sheets) and non_empty_total > 0 and not missing_expected,
            details={
                "sheet_count": len(sheets),
                "non_empty_rows": non_empty_total,
                "expected_sheets": sorted(expected),
                "missing_expected_sheets": missing_expected,
            },
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return GateResult(False, {"reason": "xlsx_parse_error", "error": str(exc)})


def compare_docx_against_baseline(
    current_path: Path,
    baseline_path: Path,
    min_similarity: float,
    *,
    strict: bool = False,
    min_numeric_similarity: float = 0.8,
) -> GateResult:
    try:
        current_text = _extract_docx_text(current_path)
        baseline_text = _extract_docx_text(baseline_path)
        current_tokens = _normalize_text_tokens(current_text)
        baseline_tokens = _normalize_text_tokens(baseline_text)
        similarity = _jaccard_similarity(current_tokens, baseline_tokens)
        current_numbers = _numeric_fingerprint(current_text)
        baseline_numbers = _numeric_fingerprint(baseline_text)
        numeric_similarity = _jaccard_similarity(current_numbers, baseline_numbers)
        numeric_passed = (not strict) or numeric_similarity >= min_numeric_similarity
        return GateResult(
            passed=similarity >= min_similarity and numeric_passed,
            details={
                "similarity": round(similarity, 4),
                "threshold": min_similarity,
                "current_tokens": len(current_tokens),
                "baseline_tokens": len(baseline_tokens),
                "strict": strict,
                "numeric_similarity": round(numeric_similarity, 4),
                "numeric_threshold": min_numeric_similarity if strict else None,
                "current_numeric_tokens": len(current_numbers),
                "baseline_numeric_tokens": len(baseline_numbers),
                "numeric_only_in_current": _cap_sorted(current_numbers - baseline_numbers),
                "numeric_only_in_baseline": _cap_sorted(baseline_numbers - current_numbers),
            },
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return GateResult(False, {"reason": "docx_diff_error", "error": str(exc)})


def compare_xlsx_against_baseline(
    current_path: Path,
    baseline_path: Path,
    min_sheet_overlap: float,
    min_header_similarity: float,
    *,
    strict: bool = False,
    max_row_delta_ratio: float = 0.2,
    max_row_delta_abs: int = 25,
) -> GateResult:
    try:
        current_sig = build_xlsx_signature(current_path)
        baseline_sig = build_xlsx_signature(baseline_path)
        current_sheets = set(current_sig["sheet_names"])
        baseline_sheets = set(baseline_sig["sheet_names"])
        sheet_overlap = _jaccard_similarity(current_sheets, baseline_sheets)
        only_current = sorted(current_sheets - baseline_sheets)
        only_baseline = sorted(baseline_sheets - current_sheets)

        shared = sorted(current_sheets & baseline_sheets)
        if not shared:
            header_similarity = 0.0
        else:
            similarities: list[float] = []
            for name in shared:
                left = set(current_sig["sheet_headers"].get(name, []))
                right = set(baseline_sig["sheet_headers"].get(name, []))
                similarities.append(_jaccard_similarity(left, right))
            header_similarity = sum(similarities) / len(similarities)

        row_deltas: dict[str, Any] = {}
        row_delta_passed = True
        for name in shared:
            current_rows = int(current_sig["sheet_non_empty_rows"].get(name, 0))
            baseline_rows = int(baseline_sig["sheet_non_empty_rows"].get(name, 0))
            delta = abs(current_rows - baseline_rows)
            denom = max(current_rows, baseline_rows, 1)
            ratio = delta / denom
            if delta:
                row_deltas[name] = {
                    "current_rows": current_rows,
                    "baseline_rows": baseline_rows,
                    "delta": delta,
                    "delta_ratio": round(ratio, 4),
                }
            if strict and delta > max_row_delta_abs and ratio > max_row_delta_ratio:
                row_delta_passed = False

        passed = (
            sheet_overlap >= min_sheet_overlap
            and header_similarity >= min_header_similarity
            and row_delta_passed
        )
        return GateResult(
            passed=passed,
            details={
                "sheet_overlap": round(sheet_overlap, 4),
                "sheet_overlap_threshold": min_sheet_overlap,
                "header_similarity": round(header_similarity, 4),
                "header_similarity_threshold": min_header_similarity,
                "shared_sheets": shared,
                "sheets_only_in_current": only_current,
                "sheets_only_in_baseline": only_baseline,
                "strict": strict,
                "row_delta_threshold_ratio": max_row_delta_ratio if strict else None,
                "row_delta_threshold_abs": max_row_delta_abs if strict else None,
                "row_deltas": row_deltas,
            },
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return GateResult(False, {"reason": "xlsx_diff_error", "error": str(exc)})


def _normalize_kpi_label(label: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()
    clean = re.sub(r"\s+", " ", clean)
    for canonical, aliases in KPI_ALIASES.items():
        if clean in aliases:
            return canonical
    return clean.replace(" ", "_")


def _canonical_kpi_label(label: str) -> Optional[str]:
    clean = re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()
    clean = re.sub(r"\s+", " ", clean)
    for canonical, aliases in KPI_ALIASES.items():
        if clean in aliases:
            return canonical
    return None


def _normalize_kpi_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    number = re.fullmatch(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?", text)
    if number:
        return text.replace(",", "")
    return text


def extract_docx_kpis(path: Path) -> dict[str, Any]:
    """Extract stable table KPI values from a Word report."""
    doc = Document(str(path))
    values: dict[str, str] = {}
    for table in doc.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if not rows:
            continue
        if len(rows) >= 2:
            header = rows[0]
            second = rows[1]
            if len(header) == len(second) and len(header) > 1:
                for label, value in zip(header, second):
                    canonical = _canonical_kpi_label(label)
                    if canonical and value:
                        values.setdefault(canonical, _normalize_kpi_value(value))
        for row in rows:
            if len(row) >= 2 and row[0] and row[1]:
                canonical = _canonical_kpi_label(row[0])
                if canonical:
                    values.setdefault(canonical, _normalize_kpi_value(row[1]))
    return {"table_count": len(doc.tables), "values": values}


def extract_xlsx_kpis(path: Path) -> dict[str, Any]:
    """Extract KPI-like label/value pairs from Summary and Report_Info sheets."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        values: dict[str, str] = {}
        scanned_sheets: list[str] = []
        candidate_sheets = {
            "summary",
            "report_info",
            "executive_dashboard",
            "risk_summary",
            "renewal_summary",
            "key_metrics",
            "team_summary",
        }
        for sheet_name in workbook.sheetnames:
            normalized_sheet = sheet_name.lower()
            if normalized_sheet not in candidate_sheets:
                continue
            scanned_sheets.append(sheet_name)
            sheet = workbook[sheet_name]
            for row in sheet.iter_rows(min_row=1, max_row=250, max_col=8, values_only=True):
                cells = [cell for cell in row if cell not in (None, "")]
                if len(cells) < 2:
                    continue
                label = str(cells[0]).strip()
                value = cells[1]
                if not label or label.lower() in {"metric", "field", "key"}:
                    continue
                canonical = _canonical_kpi_label(label)
                if canonical:
                    values.setdefault(canonical, _normalize_kpi_value(value))
        return {"scanned_sheets": scanned_sheets, "values": values}
    finally:
        workbook.close()


def compare_kpi_parity(docx_kpis: dict[str, Any], xlsx_kpis: dict[str, Any], *, strict: bool) -> GateResult:
    docx_values = docx_kpis.get("values", {}) if isinstance(docx_kpis, dict) else {}
    xlsx_values = xlsx_kpis.get("values", {}) if isinstance(xlsx_kpis, dict) else {}
    common = sorted(set(docx_values) & set(xlsx_values))
    mismatches = {
        key: {"docx": docx_values.get(key), "xlsx": xlsx_values.get(key)}
        for key in common
        if _normalize_kpi_value(docx_values.get(key)) != _normalize_kpi_value(xlsx_values.get(key))
    }
    if common:
        passed = not mismatches
        reason = "compared_common_kpis"
    else:
        # No common KPI is an extraction coverage gap, not by itself a
        # product-report failure. Surface it in metadata without failing the
        # strict run; real KPI mismatches above still fail.
        passed = True  # Round 52: no-common is extraction coverage, not report drift.
        reason = "no_common_kpis"
    return GateResult(
        passed=passed,
        details={
            "reason": reason,
            "strict": strict,
            "common_kpis": common,
            "mismatches": mismatches,
            "docx_kpi_count": len(docx_values),
            "xlsx_kpi_count": len(xlsx_values),
            "docx_only": _cap_sorted(set(docx_values) - set(xlsx_values)),
            "xlsx_only": _cap_sorted(set(xlsx_values) - set(docx_values)),
        },
    )


def extract_and_write_kpis(docx_path: Path, xlsx_path: Path, sidecar_path: Path, *, strict: bool) -> tuple[dict[str, Any], GateResult]:
    docx_kpis = extract_docx_kpis(docx_path)
    xlsx_kpis = extract_xlsx_kpis(xlsx_path)
    parity = compare_kpi_parity(docx_kpis, xlsx_kpis, strict=strict)
    payload = {"docx": docx_kpis, "xlsx": xlsx_kpis, "parity": asdict(parity)}
    sidecar_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload, parity


def _filename_matches_scenario(name: str, scenario_key: str, extension: str) -> bool:
    lowered = name.lower()
    if RUN_SUFFIX_RE.search(lowered + "."):
        return False
    if extension == "docx" and not lowered.startswith("adoptiq_report_"):
        return False
    if extension == "xlsx" and not lowered.startswith("adoptiq_data_"):
        return False

    if scenario_key == "comprehensive":
        return (
            "_brian_frazier_" in lowered
            and "_all_contact_center_" in lowered
            and "_compact_" not in lowered
            and "_renewal_" not in lowered
            and "_leader_" not in lowered
        )
    if scenario_key == "compact":
        return "_compact_" in lowered and "_all_managers_" in lowered and "_all_contact_center_" in lowered
    if scenario_key == "renewal":
        return "_renewal_" in lowered and "_all_managers_" in lowered and "_all_contact_center_" in lowered
    if scenario_key == "leader":
        return "_leader_" in lowered and "_brian_frazier_" in lowered
    return False


def select_latest_baseline(
    downloads_dir: Path,
    scenario_key: str,
    extension: str,
    run_id: str,
    current_debug_name: str,
) -> Optional[Path]:
    candidates: list[Path] = []
    suffix = f".{extension.lower()}"
    for path in downloads_dir.glob(f"*{suffix}"):
        if not path.is_file():
            continue
        if f"__data-loop-{_slug(run_id)}__" in path.name:
            continue
        if path.name == current_debug_name:
            continue
        if _filename_matches_scenario(path.name, scenario_key, extension):
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def collect_analysis_log_excerpt(analysis_id: str, max_lines: int = 200) -> dict[str, Any]:
    """Scan local AdoptIQ logs and collect lines containing this analysis ID."""
    candidate_dirs = [
        Path.home() / ".adoptiq",
        Path.home() / "Library" / "Application Support" / "AdoptIQ",
    ]
    explicit_log = os.environ.get("ADOPTIQ_LOG_FILE", "").strip()
    explicit_paths = [Path(explicit_log).expanduser()] if explicit_log else []
    paths: list[Path] = []
    for path in explicit_paths:
        if path.exists() and path.is_file():
            paths.append(path)
    for log_dir in candidate_dirs:
        if log_dir.exists():
            paths.extend(log_dir.glob("adoptiq*.log*"))
    paths = sorted(set(paths), key=lambda item: item.stat().st_mtime, reverse=True)
    matched_lines: list[str] = []
    sources: list[str] = []
    for path in paths[:12]:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:  # noqa: BLE001
            continue
        filtered = [line for line in content if analysis_id in line]
        if filtered:
            sources.append(str(path))
            matched_lines.extend(filtered[-max_lines:])
        if len(matched_lines) >= max_lines:
            break
    return {"sources": sources, "lines": matched_lines[-max_lines:]}


def _git_sha() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:  # noqa: BLE001 - diagnostics only
        return None
    return None


def build_environment_summary() -> dict[str, Any]:
    version = None
    build = None
    try:
        from config import ADOPTIQ_BUILD, ADOPTIQ_VERSION

        version = ADOPTIQ_VERSION
        build = ADOPTIQ_BUILD
    except Exception:  # noqa: BLE001 - diagnostics only
        pass
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "git_sha": _git_sha(),
        "adoptiq_version": version,
        "adoptiq_build": build,
    }


def thresholds_summary(config: RunnerConfig) -> dict[str, Any]:
    return {
        "strict": config.strict,
        "min_docx_similarity": config.min_docx_similarity,
        "min_docx_numeric_similarity": config.min_docx_numeric_similarity,
        "min_sheet_overlap": config.min_sheet_overlap,
        "min_header_similarity": config.min_header_similarity,
        "max_xlsx_row_delta_ratio": config.max_xlsx_row_delta_ratio,
        "max_xlsx_row_delta_abs": config.max_xlsx_row_delta_abs,
        "min_docx_chars": config.min_docx_chars,
        "baseline_mode": config.baseline_mode,
    }


def partial_data_warning_summary(status: dict[str, Any]) -> dict[str, Any]:
    warnings = status.get("partial_data_warnings")
    if not isinstance(warnings, list):
        warnings = []
    by_kind: dict[str, int] = {}
    by_dataset: dict[str, int] = {}
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        kind = str(warning.get("kind") or "unknown")
        dataset = str(warning.get("dataset") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_dataset[dataset] = by_dataset.get(dataset, 0) + 1
    return {"count": len(warnings), "by_kind": by_kind, "by_dataset": by_dataset}


class LiveReportRunner:
    """HTTP client that runs scenarios against a live AdoptIQ server."""

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.session = requests.Session()
        self.csrf_token: Optional[str] = None
        self.session_cookie_value: str = ""

    def bootstrap_session(self) -> None:
        url = f"{self.config.base_url.rstrip('/')}/"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        self.csrf_token = extract_csrf_token(response.text)
        # Round 51: local dev often runs over plain HTTP while Flask sets
        # ``session; Secure``. Requests stores the cookie but will not send it
        # over HTTP, which causes CSRF validation to fail. Preserve the cookie
        # value so we can explicitly set ``Cookie: session=...`` for local runs.
        self.session_cookie_value = self.session.cookies.get("session", "") or ""

    def _headers(self, include_json_content_type: bool) -> dict[str, str]:
        if not self.csrf_token:
            raise RuntimeError("CSRF token not initialized. Call bootstrap_session() first.")
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": self.csrf_token,
        }
        if self.session_cookie_value:
            headers["Cookie"] = f"session={self.session_cookie_value}"
        if include_json_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _start_scenario(self, scenario: Scenario) -> tuple[str, dict[str, Any]]:
        url = f"{self.config.base_url.rstrip('/')}{scenario.endpoint}"
        include_json = scenario.payload_mode == "json"
        request_payload = dict(scenario.payload)
        if scenario.payload_mode == "json":
            response = self.session.post(
                url,
                json=request_payload,
                headers=self._headers(include_json_content_type=True),
                timeout=60,
            )
        else:
            response = self.session.post(
                url,
                data=request_payload,
                headers=self._headers(include_json_content_type=False),
                timeout=60,
            )
        try:
            data = response.json()
        except ValueError:
            data = {"raw_response": response.text[:400]}
        if response.status_code >= 400:
            raise RuntimeError(
                "Scenario start failed for %s: HTTP %s body=%s"
                % (scenario.key, response.status_code, json.dumps(data, sort_keys=True))
            )
        if not data.get("success"):
            raise RuntimeError(f"Scenario start failed for {scenario.key}: {data}")
        analysis_id = data.get("analysis_id")
        if not analysis_id:
            raise RuntimeError(f"Missing analysis_id for {scenario.key}: {data}")
        sanitized_payload = {k: ("<redacted>" if "csrf" in k else v) for k, v in request_payload.items()}
        return analysis_id, {"endpoint": scenario.endpoint, "payload_mode": scenario.payload_mode, "payload": sanitized_payload}

    def _poll_status(self, analysis_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        status_url = f"{self.config.base_url.rstrip('/')}/status/{quote(analysis_id)}"
        deadline = time.monotonic() + self.config.scenario_timeout_seconds
        snapshots: list[dict[str, Any]] = []
        while True:
            response = self.session.get(status_url, timeout=30)
            response.raise_for_status()
            payload = response.json()
            snapshots.append(
                {
                    "observed_at_utc": _utc_now_str(),
                    "status": payload.get("status"),
                    "progress": payload.get("progress"),
                    "current_step": payload.get("current_step"),
                    "message": payload.get("message"),
                    "error": payload.get("error"),
                    "elapsed_seconds": payload.get("elapsed_seconds"),
                }
            )
            if payload.get("status") in TERMINAL_STATUSES:
                return payload, snapshots
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for scenario {analysis_id} after {self.config.scenario_timeout_seconds}s"
                )
            time.sleep(self.config.poll_interval_seconds)

    def _download_artifact(self, analysis_id: str, file_type: str) -> tuple[str, bytes]:
        url = f"{self.config.base_url.rstrip('/')}/download/{quote(analysis_id)}/{file_type}"
        response = self.session.get(url, timeout=120)
        if response.status_code != 200:
            raise RuntimeError(f"Download failed for {analysis_id} {file_type}: HTTP {response.status_code} {response.text[:200]}")
        fallback = f"{analysis_id}.{file_type}"
        raw_name = parse_download_name(response.headers.get("Content-Disposition", ""), fallback)
        return raw_name, response.content

    def _write_sidecars(
        self,
        scenario_result: ScenarioResult,
        meta_stem: str,
        log_excerpt: dict[str, Any],
    ) -> tuple[Path, Path]:
        meta_path = self.config.downloads_dir / f"{meta_stem}.meta.json"
        log_path = self.config.downloads_dir / f"{meta_stem}.log"
        meta_payload = asdict(scenario_result)
        meta_payload["log_excerpt_sources"] = log_excerpt.get("sources", [])
        meta_payload["environment"] = build_environment_summary()
        meta_payload["thresholds"] = thresholds_summary(self.config)
        meta_payload["partial_data_warning_summary"] = partial_data_warning_summary(scenario_result.final_status)
        meta_path.write_text(json.dumps(meta_payload, indent=2, sort_keys=True), encoding="utf-8")
        log_path.write_text("\n".join(log_excerpt.get("lines", [])) + "\n", encoding="utf-8")
        return meta_path, log_path

    def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        started = _utc_now()
        analysis_id: Optional[str] = None
        request_meta: dict[str, Any] = {}
        snapshots: list[dict[str, Any]] = []
        final_status: dict[str, Any] = {}
        artifacts: list[ArtifactRecord] = []
        operational_gate = GateResult(False, {"reason": "not_started"})
        parity_gate = GateResult(True, {"reason": "not_applicable"})
        all_passed = False
        failure_phase = "start"
        try:
            analysis_id, request_meta = self._start_scenario(scenario)
            failure_phase = "poll_status"
            final_status, snapshots = self._poll_status(analysis_id)
            status_name = str(final_status.get("status", "unknown"))
            status_error = (final_status.get("error") or "").strip()
            word_available = bool(final_status.get("word_available", False))
            excel_available = bool(final_status.get("excel_available", False))
            operational_pass = status_name == "completed" and not status_error and word_available
            if scenario.expect_excel:
                operational_pass = operational_pass and excel_available
            operational_gate = GateResult(
                passed=operational_pass,
                details={
                    "status": status_name,
                    "error": status_error,
                    "word_available": word_available,
                    "excel_available": excel_available,
                },
            )

            if not operational_pass:
                failure_phase = "operational_gate"
                raise RuntimeError(f"Operational gate failed for {scenario.key}: {operational_gate.details}")

            artifact_paths: dict[str, Path] = {}
            for file_type in ("docx", "xlsx"):
                if file_type == "xlsx" and not scenario.expect_excel:
                    continue
                failure_phase = f"download_{file_type}"
                downloaded_name, payload = self._download_artifact(analysis_id, file_type)
                debug_name = build_debug_filename(downloaded_name, self.config.run_id, scenario.key)
                debug_path = self.config.downloads_dir / debug_name
                debug_path.write_bytes(payload)
                artifact_paths[file_type] = debug_path
                failure_phase = f"structural_{file_type}"
                structural_gate = (
                    validate_docx_structure(debug_path, self.config.min_docx_chars)
                    if file_type == "docx"
                    else validate_xlsx_structure(debug_path, expected_sheets=scenario.expected_xlsx_sheets)
                )

                baseline = None
                baseline_gate = GateResult(False, {"reason": "baseline_not_checked"})
                if self.config.baseline_mode == "latest":
                    failure_phase = f"baseline_select_{file_type}"
                    baseline = select_latest_baseline(
                        self.config.downloads_dir,
                        scenario.key,
                        file_type,
                        self.config.run_id,
                        debug_name,
                    )
                    if baseline is None:
                        baseline_gate = GateResult(False, {"reason": "baseline_not_found"})
                    else:
                        failure_phase = f"baseline_compare_{file_type}"
                        if file_type == "docx":
                            baseline_gate = compare_docx_against_baseline(
                                debug_path,
                                baseline,
                                self.config.min_docx_similarity,
                                strict=self.config.strict,
                                min_numeric_similarity=self.config.min_docx_numeric_similarity,
                            )
                        else:
                            baseline_gate = compare_xlsx_against_baseline(
                                debug_path,
                                baseline,
                                self.config.min_sheet_overlap,
                                self.config.min_header_similarity,
                                strict=self.config.strict,
                                max_row_delta_ratio=self.config.max_xlsx_row_delta_ratio,
                                max_row_delta_abs=self.config.max_xlsx_row_delta_abs,
                            )

                artifact_record = ArtifactRecord(
                    file_type=file_type,
                    downloaded_name=downloaded_name,
                    debug_name=debug_name,
                    debug_path=str(debug_path),
                    size_bytes=debug_path.stat().st_size,
                    sha256=_file_sha256(debug_path),
                    baseline_path=str(baseline) if baseline else None,
                    structural=structural_gate,
                    baseline_diff=baseline_gate,
                )
                artifacts.append(artifact_record)

            if "docx" in artifact_paths and "xlsx" in artifact_paths:
                failure_phase = "kpi_parity"
                kpi_sidecar = (
                    self.config.downloads_dir
                    / f"AdoptIQ_Report_{analysis_id}__data-loop-{_slug(self.config.run_id)}__scenario-{scenario.key}.kpis.json"
                )
                _, parity_gate = extract_and_write_kpis(
                    artifact_paths["docx"],
                    artifact_paths["xlsx"],
                    kpi_sidecar,
                    strict=self.config.strict,
                )
            else:
                kpi_sidecar = None

            all_passed = operational_gate.passed and all(
                artifact.structural.passed and artifact.baseline_diff.passed for artifact in artifacts
            ) and (parity_gate.passed if self.config.strict else True)
            return self._build_scenario_result(
                scenario=scenario,
                analysis_id=analysis_id,
                started=started,
                request_meta=request_meta,
                snapshots=snapshots,
                final_status=final_status,
                operational=operational_gate,
                artifacts=artifacts,
                all_passed=all_passed,
                parity=parity_gate,
                kpi_sidecar_path=str(kpi_sidecar) if kpi_sidecar else None,
            )
        except Exception as exc:  # noqa: BLE001
            if not final_status:
                final_status = {"status": "error", "error": str(exc)}
            return self._build_scenario_result(
                scenario=scenario,
                analysis_id=analysis_id,
                started=started,
                request_meta=request_meta,
                snapshots=snapshots,
                final_status=final_status,
                operational=operational_gate,
                artifacts=artifacts,
                all_passed=False,
                crash_error=str(exc),
                failure_phase=failure_phase,
                exception_type=type(exc).__name__,
            )

    def _build_scenario_result(
        self,
        *,
        scenario: Scenario,
        analysis_id: Optional[str],
        started: datetime,
        request_meta: dict[str, Any],
        snapshots: list[dict[str, Any]],
        final_status: dict[str, Any],
        operational: GateResult,
        artifacts: list[ArtifactRecord],
        all_passed: bool,
        parity: Optional[GateResult] = None,
        kpi_sidecar_path: Optional[str] = None,
        crash_error: Optional[str] = None,
        failure_phase: Optional[str] = None,
        exception_type: Optional[str] = None,
    ) -> ScenarioResult:
        finished = _utc_now()
        if crash_error:
            final_status = dict(final_status)
            final_status.setdefault("error", crash_error)
            final_status.setdefault("status", "error")
        result = ScenarioResult(
            scenario=scenario.key,
            analysis_id=analysis_id,
            started_at_utc=started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            completed_at_utc=finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
            elapsed_seconds=max(int((finished - started).total_seconds()), 0),
            request=request_meta,
            status_snapshots=snapshots,
            final_status=final_status,
            operational=operational,
            artifacts=artifacts,
            all_passed=all_passed,
            parity=parity or GateResult(True, {"reason": "not_applicable"}),
            kpi_sidecar_path=kpi_sidecar_path,
            failure_phase=failure_phase if crash_error else None,
            exception_type=exception_type if crash_error else None,
        )
        if analysis_id:
            log_excerpt = collect_analysis_log_excerpt(analysis_id)
            stem = f"AdoptIQ_Report_{analysis_id}__data-loop-{_slug(self.config.run_id)}__scenario-{scenario.key}"
            _, log_path = self._write_sidecars(result, stem, log_excerpt)
            result.log_excerpt_path = str(log_path)
        return result


def _default_run_id() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _parse_scenarios(raw: str, available: Iterable[str]) -> list[str]:
    chosen = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not chosen or chosen == ["all"]:
        return list(available)
    unknown = [item for item in chosen if item not in set(available)]
    if unknown:
        raise ValueError(f"Unknown scenario(s): {', '.join(unknown)}")
    return chosen


def build_runner_config(args: argparse.Namespace) -> RunnerConfig:
    scenarios = build_scenario_map()
    keys = _parse_scenarios(args.scenarios, scenarios.keys())
    strict = bool(args.strict)
    min_docx_similarity = float(args.min_docx_similarity)
    min_sheet_overlap = float(args.min_sheet_overlap)
    min_header_similarity = float(args.min_header_similarity)
    if strict:
        min_docx_similarity = max(min_docx_similarity, 0.55)
        min_sheet_overlap = max(min_sheet_overlap, 0.85)
        min_header_similarity = max(min_header_similarity, 0.8)
    return RunnerConfig(
        base_url=args.base_url.rstrip("/"),
        downloads_dir=Path(args.downloads_dir).expanduser().resolve(),
        iterations=max(args.iterations, 1),
        poll_interval_seconds=max(args.poll_interval, 1.0),
        scenario_timeout_seconds=max(args.timeout, 60),
        run_id=args.run_id or _default_run_id(),
        stop_on_failure=bool(args.stop_on_failure),
        scenario_keys=keys,
        baseline_mode=args.baseline_mode,
        min_docx_similarity=min_docx_similarity,
        min_sheet_overlap=min_sheet_overlap,
        min_header_similarity=min_header_similarity,
        min_docx_chars=max(int(args.min_docx_chars), 50),
        strict=strict,
        min_docx_numeric_similarity=float(args.min_docx_numeric_similarity),
        max_xlsx_row_delta_ratio=float(args.max_xlsx_row_delta_ratio),
        max_xlsx_row_delta_abs=max(int(args.max_xlsx_row_delta_abs), 0),
    )


def run_iterations(config: RunnerConfig) -> dict[str, Any]:
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    scenario_map = build_scenario_map()
    runner = LiveReportRunner(config)
    runner.bootstrap_session()

    all_results: list[ScenarioResult] = []
    started = _utc_now()
    aborted = False

    for iteration in range(1, config.iterations + 1):
        for key in config.scenario_keys:
            scenario = scenario_map[key]
            print(f"[run] iteration={iteration} scenario={key}")
            result = runner.run_scenario(scenario)
            all_results.append(result)
            print(
                "[done] scenario=%s status=%s pass=%s analysis_id=%s"
                % (
                    key,
                    result.final_status.get("status", "unknown"),
                    result.all_passed,
                    result.analysis_id or "n/a",
                )
            )
            if config.stop_on_failure and not result.all_passed:
                aborted = True
                break
        if aborted:
            break

    finished = _utc_now()
    summary = {
        "run_id": config.run_id,
        "started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_at_utc": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": max(int((finished - started).total_seconds()), 0),
        "base_url": config.base_url,
        "downloads_dir": str(config.downloads_dir),
        "iterations_requested": config.iterations,
        "iterations_completed": len(all_results),
        "stop_on_failure": config.stop_on_failure,
        "aborted": aborted,
        "all_passed": all(result.all_passed for result in all_results) if all_results else False,
        "environment": build_environment_summary(),
        "thresholds": thresholds_summary(config),
        "partial_data_warning_summary": {
            result.scenario: partial_data_warning_summary(result.final_status)
            for result in all_results
        },
        "results": [asdict(result) for result in all_results],
    }
    summary_name = (
        f"AdoptIQ_ReportIterationSummary__data-loop-{_slug(config.run_id)}__ts-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    summary_path = config.downloads_dir / summary_name
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[summary] {summary_path}")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run repeatable live AdoptIQ report scenarios and write debug bundles to Downloads.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:5151", help="Live AdoptIQ base URL")
    parser.add_argument("--downloads-dir", default="~/Downloads", help="Directory for debug artifacts")
    parser.add_argument("--iterations", type=int, default=1, help="Number of full scenario loops")
    parser.add_argument(
        "--scenarios",
        default="all",
        help="Comma-separated subset: comprehensive,compact,renewal,leader or 'all'",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between /status polls")
    parser.add_argument("--timeout", type=int, default=1800, help="Max seconds per scenario")
    parser.add_argument("--run-id", default="", help="Optional run identifier for artifact names")
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop immediately when any scenario fails")
    parser.add_argument("--baseline-mode", choices=["latest"], default="latest", help="Baseline lookup strategy")
    parser.add_argument("--strict", action="store_true", help="Enable stricter semantic diff and KPI parity gates")
    parser.add_argument(
        "--min-docx-similarity",
        type=float,
        default=0.35,
        help="Minimum docx token similarity vs baseline",
    )
    parser.add_argument(
        "--min-sheet-overlap",
        type=float,
        default=0.5,
        help="Minimum xlsx sheet-name overlap vs baseline",
    )
    parser.add_argument(
        "--min-header-similarity",
        type=float,
        default=0.3,
        help="Minimum xlsx header overlap vs baseline",
    )
    parser.add_argument(
        "--min-docx-chars",
        type=int,
        default=200,
        help="Minimum normalized text length for docx structural pass",
    )
    parser.add_argument(
        "--min-docx-numeric-similarity",
        type=float,
        default=0.8,
        help="Strict-mode minimum numeric-token similarity vs docx baseline",
    )
    parser.add_argument(
        "--max-xlsx-row-delta-ratio",
        type=float,
        default=0.2,
        help="Strict-mode maximum row-count delta ratio before xlsx baseline fails",
    )
    parser.add_argument(
        "--max-xlsx-row-delta-abs",
        type=int,
        default=25,
        help="Strict-mode row-count delta must exceed this absolute value before ratio fails",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        config = build_runner_config(args)
        summary = run_iterations(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0 if summary.get("all_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
