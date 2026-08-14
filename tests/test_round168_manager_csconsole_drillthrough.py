"""Round 168 manager evidence drill-through contracts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import decision_report_delivery as delivery
import manager_decision_workspace as workspace
from source_record_links import (
    SOURCE_RECORD_URL_COLUMN,
    build_source_record_url,
    is_allowed_source_record_url,
)
from tests.test_round142_decision_report_delivery import _facts


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_JS_PATH = ROOT / "static" / "js" / "manager_decision_workspace.js"


def test_exact_evidence_exposes_only_the_canonical_csconsole_url(tmp_path: Path) -> None:
    source_path = tmp_path / "AdoptIQ_Source_Data_Round168.xlsx"
    delivery.write_source_data_workbook(
        source_path,
        delivery.build_source_data_sheets(_facts()),
    )
    snapshot = workspace.load_workbook_snapshot(source_path)

    evidence = workspace.load_workbook_evidence(
        source_path,
        "kpi.action_plans_overdue",
        expected_fingerprint=snapshot["fact_fingerprint"],
    )

    assert evidence["records"]
    for record in evidence["records"]:
        expected = build_source_record_url(record["source_sheet"], record["record_id"])
        assert record["source_record_url"] == expected
        assert is_allowed_source_record_url(record["source_record_url"])


def test_unsupported_source_without_a_url_remains_unlinked() -> None:
    assert (
        workspace._verified_source_record_url(  # noqa: SLF001
            source_sheet="TAC_Cases",
            record_id="SR-001",
            evidence_link={SOURCE_RECORD_URL_COLUMN: ""},
            source_record={SOURCE_RECORD_URL_COLUMN: ""},
        )
        == ""
    )


@pytest.mark.parametrize(
    ("linked_url", "record_url", "message"),
    [
        (
            build_source_record_url("Action_Plans", "AP-001"),
            build_source_record_url("Action_Plans", "AP-002"),
            "does not match",
        ),
        ("javascript:alert(1)", "javascript:alert(1)", "noncanonical"),
        (
            "https://evil.test/lightning/r/C360_CS_Task__c/AP-001/view",
            "https://evil.test/lightning/r/C360_CS_Task__c/AP-001/view",
            "noncanonical",
        ),
    ],
    ids=("link-row-mismatch", "javascript", "off-domain"),
)
def test_evidence_source_record_url_tampering_fails_closed(
    linked_url: str,
    record_url: str,
    message: str,
) -> None:
    with pytest.raises(workspace.EvidenceIntegrityError, match=message):
        workspace._verified_source_record_url(  # noqa: SLF001
            source_sheet="Action_Plans",
            record_id="AP-001",
            evidence_link={SOURCE_RECORD_URL_COLUMN: linked_url},
            source_record={SOURCE_RECORD_URL_COLUMN: record_url},
        )


def test_supported_record_missing_its_canonical_url_fails_closed() -> None:
    with pytest.raises(workspace.EvidenceIntegrityError, match="missing"):
        workspace._verified_source_record_url(  # noqa: SLF001
            source_sheet="Action_Plans",
            record_id="AP-001",
            evidence_link={SOURCE_RECORD_URL_COLUMN: ""},
            source_record={SOURCE_RECORD_URL_COLUMN: ""},
        )


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def test_workspace_ui_declares_a_safe_accessible_csconsole_action() -> None:
    source = WORKSPACE_JS_PATH.read_text(encoding="utf-8")
    assert "function safeCsconsoleHref(value)" in source
    assert "record.source_record_url" in source
    assert "Open CSConsole record" in source
    assert "sourceRecordLink.target = '_blank'" in source
    assert "sourceRecordLink.rel = 'noopener noreferrer'" in source
    assert "sourceRecordLink.referrerPolicy = 'no-referrer'" in source
    assert "in a new tab" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_workspace_ui_renders_only_an_allowlisted_csconsole_action() -> None:
    source = WORKSPACE_JS_PATH.read_text(encoding="utf-8")
    helpers = "\n\n".join(
        _function_source(source, name)
        for name in (
            "text",
            "element",
            "safeCsconsoleHref",
            "appendEvidenceDefinition",
            "renderEvidenceRecord",
        )
    )
    valid_url = build_source_record_url("Action_Plans", "AP-001")
    harness = f"""
'use strict';
var CSCONSOLE_HOST = 'ciscosales.lightning.force.com';
var CSCONSOLE_OBJECTS = {{
  C360_CS_Task__c: true,
  ESA_C360_Customer_Pulse__c: true,
  ESA_C360_SUCCESS_PRIORITY__C: true
}};
function makeNode(tag) {{
  return {{
    tagName: String(tag).toLowerCase(),
    className: '',
    textContent: '',
    children: [],
    attributes: {{}},
    appendChild(child) {{ this.children.push(child); }},
    setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  }};
}}
var document = {{ createElement: makeNode }};
{helpers}
function links(root) {{
  var found = [];
  function visit(node) {{
    if (node.tagName === 'a') {{ found.push(node); }}
    (node.children || []).forEach(visit);
  }}
  visit(root);
  return found;
}}
var valid = renderEvidenceRecord({{
  source_sheet: 'Action_Plans', source_row_number: 2,
  record_id: 'AP-001', title: 'Validate adoption',
  source_record_url: {valid_url!r}
}}, 0);
var validLinks = links(valid);
if (validLinks.length !== 1) {{ throw new Error('canonical link was not rendered'); }}
if (validLinks[0].href !== {valid_url!r}) {{ throw new Error('canonical href changed'); }}
if (validLinks[0].textContent !== 'Open CSConsole record') {{ throw new Error('link text missing'); }}
if (validLinks[0].target !== '_blank' || validLinks[0].rel !== 'noopener noreferrer') {{
  throw new Error('external-link protections missing');
}}
if (!String(validLinks[0].attributes['aria-label'] || '').includes('AP-001')) {{
  throw new Error('accessible record identity missing');
}}
var hostile = renderEvidenceRecord({{
  source_sheet: 'Action_Plans', source_row_number: 2,
  record_id: 'AP-001', title: 'Validate adoption',
  source_record_url: 'javascript:alert(1)'
}}, 0);
if (links(hostile).length !== 0) {{ throw new Error('hostile link was rendered'); }}
"""
    subprocess.run(
        ["node", "-e", harness],
        check=True,
        capture_output=True,
        text=True,
    )
