#!/usr/bin/env python3
"""Exercise every guarded Round 145 fixture through real loopback HTTP.

A fresh source app process is launched per scenario. The JSON summary contains
only hashes, counts, states, and pass/fail findings; it never claims live
Snowflake validation and never stores response bodies.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    SOURCE_MODE,
    build_scenario_bundle,
    load_manifest,
)
from report_iteration_loop import extract_csrf_token  # noqa: E402
from scripts.run_ai_feature_acceptance import (  # noqa: E402
    _stream_payload,
    extract_citations,
    parse_sse,
)


SUMMARY_SCHEMA = "local-acceptance-http/v1"
PROVIDER_HTTP_STATUS = {
    "timeout": 504,
    "rate_limited": 429,
    "unavailable": 503,
    "malformed": 502,
}
PROVIDER_TERM = {
    "timeout": "timed out",
    "rate_limited": "rate limited",
    "unavailable": "unavailable",
    "malformed": "unusable response",
}
FORBIDDEN_RESPONSE_TERMS = (
    "traceback (most recent call last)",
    "client_secret=",
    "secret_id=",
    "authorization: bearer",
    "ghp_",
)
PROMPT_INJECTION_TERMS = (
    "ignore all previous instructions",
    "disclose credentials",
)


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _safe_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != ".adoptiq-acceptance":
        raise ValueError(
            "in-repository output must stay under .adoptiq-acceptance"
        )
    return resolved


def _response_is_sanitized(value: object) -> bool:
    text = str(value or "").casefold()
    return not any(term in text for term in FORBIDDEN_RESPONSE_TERMS)


def _warning_projection(value: object) -> dict[str, list[str]]:
    datasets: set[str] = set()
    kinds: set[str] = set()
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            dataset = str(item.get("dataset") or "").strip()
            kind = str(item.get("kind") or item.get("error_kind") or "").strip()
            if dataset:
                datasets.add(dataset)
            if kind:
                kinds.add(kind)
    return {"datasets": sorted(datasets), "kinds": sorted(kinds)}


def validate_provider_response(
    provider_state: str,
    status_code: int,
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate a grounded sync/Intel response without retaining its body."""

    errors: list[str] = []
    if provider_state == "available":
        if status_code != 200 or not payload.get("ok"):
            errors.append("available provider did not return a grounded success")
    else:
        expected_status = PROVIDER_HTTP_STATUS[provider_state]
        if status_code != expected_status:
            errors.append(
                f"provider {provider_state} returned HTTP {status_code}, "
                f"expected {expected_status}"
            )
        if payload.get("ok") is not False:
            errors.append("provider failure response did not set ok=false")
        if PROVIDER_TERM[provider_state] not in str(
            payload.get("error") or ""
        ).casefold():
            errors.append("provider failure response lost its sanitized state")
    if not _response_is_sanitized(payload):
        errors.append("response exposed a forbidden secret/error marker")
    return errors


class LoopbackClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = max(float(timeout), 5.0)
        self.session = requests.Session()
        self.csrf_token = ""

    def bootstrap(self) -> None:
        response = self.session.get(self.base_url + "/", timeout=self.timeout)
        response.raise_for_status()
        self.csrf_token = extract_csrf_token(response.text)

    def headers(self, accept: str = "application/json") -> dict[str, str]:
        return {
            "Accept": accept,
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": self.csrf_token,
        }

    def get(
        self,
        path: str,
        *,
        accept: str = "application/json",
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> requests.Response:
        return self.session.get(
            self.base_url + path,
            headers=self.headers(accept),
            timeout=timeout or self.timeout,
            params=dict(params or {}),
        )

    def post_json(self, path: str, payload: Mapping[str, Any]) -> requests.Response:
        return self.session.post(
            self.base_url + path,
            json=dict(payload),
            headers=self.headers(),
            timeout=self.timeout,
        )

    def post_form(self, path: str, payload: Mapping[str, Any]) -> requests.Response:
        return self.session.post(
            self.base_url + path,
            data=dict(payload),
            headers=self.headers("text/html"),
            timeout=self.timeout,
        )


def _json(response: requests.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _wait_for_server(
    base_url: str,
    scenario: str,
    schema_fingerprint: str,
    process: subprocess.Popen[Any],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("local app process exited before readiness")
        try:
            response = requests.get(
                base_url + "/api/diag/connectivity",
                timeout=2,
            )
            payload = _json(response)
            if (
                response.status_code == 200
                and payload.get("ok")
                and payload.get("mode") == SOURCE_MODE
                and payload.get("scenario") == scenario
                and payload.get("schema_fingerprint") == schema_fingerprint
                and payload.get("live_validation_performed") is False
            ):
                return payload
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise TimeoutError("local app did not become ready")


def _validate_ooxml(payload: bytes, extension: str) -> list[str]:
    errors: list[str] = []
    if len(payload) < 2_000:
        errors.append(f"{extension} download is unexpectedly small")
    if not payload.startswith(b"PK"):
        return errors + [f"{extension} download is not an OOXML ZIP"]
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if archive.testzip():
                errors.append(f"{extension} ZIP member failed integrity")
            required = (
                {"word/document.xml", "[Content_Types].xml"}
                if extension == "docx"
                else {"xl/workbook.xml", "[Content_Types].xml"}
            )
            if not required.issubset(archive.namelist()):
                errors.append(f"{extension} is missing required OOXML members")
    except (OSError, zipfile.BadZipFile):
        errors.append(f"{extension} download has invalid ZIP structure")
    return errors


def _poll_report(
    client: LoopbackClient,
    analysis_id: str,
    timeout: float,
) -> tuple[dict[str, Any], int]:
    deadline = time.monotonic() + timeout
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        response = client.get(f"/status/{quote(analysis_id, safe='')}")
        payload = _json(response)
        if response.status_code != 200:
            raise RuntimeError("report status route failed")
        if payload.get("status") in {"completed", "error", "failed", "cancelled"}:
            return payload, polls
        time.sleep(0.25)
    raise TimeoutError("report generation did not reach a terminal state")


def _run_report_probe(
    client: LoopbackClient,
    timeout: float,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    response = client.post_json(
        "/start_compact_analysis",
        {
            "manager": "Local Fixture Manager",
            "technology": "All Contact Center",
            "days": 90,
            "csone_file": "",
            "subscription_id": "",
            "customer_name": "",
        },
    )
    started = _json(response)
    analysis_id = str(started.get("analysis_id") or "")
    if response.status_code != 200 or not started.get("success") or not analysis_id:
        return {"start_status": response.status_code, "completed": False}, [
            "compact report did not start"
        ]

    status, polls = _poll_report(client, analysis_id, timeout)
    completed = status.get("status") == "completed"
    if not completed:
        errors.append("compact report did not complete")
    downloads: dict[str, Any] = {}
    if completed:
        for extension in ("docx", "xlsx"):
            artifact = client.get(
                f"/download/{quote(analysis_id, safe='')}/{extension}",
                timeout=timeout,
            )
            artifact_errors = (
                [f"{extension} download returned HTTP {artifact.status_code}"]
                if artifact.status_code != 200
                else _validate_ooxml(artifact.content, extension)
            )
            errors.extend(artifact_errors)
            downloads[extension] = {
                "status_code": artifact.status_code,
                "bytes": len(artifact.content),
                "sha256": hashlib.sha256(artifact.content).hexdigest(),
                "ok": not artifact_errors,
            }
    previous = client.get("/previous-reports", accept="text/html")
    previous_ok = (
        previous.status_code == 200
        and "Previous AdoptIQ Reports" in previous.text
        and len(previous.text) > 500
    )
    if not previous_ok:
        errors.append("Previous Reports did not render after generation")
    warnings = status.get("partial_data_warnings") or []
    warning_projection = _warning_projection(warnings)
    return {
        "start_status": response.status_code,
        "completed": completed,
        "poll_count": polls,
        "word_available": bool(status.get("word_available")),
        "excel_available": bool(status.get("excel_available")),
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
        "warning_datasets": warning_projection["datasets"],
        "warning_kinds": warning_projection["kinds"],
        "warning_sha256": _digest(warnings),
        "downloads": downloads,
        "previous_reports_ok": previous_ok,
    }, errors


def _run_scenario(  # noqa: C901, PLR0912, PLR0915
    scenario: str,
    *,
    manifest_path: Path,
    output_dir: Path,
    startup_timeout: float,
    request_timeout: float,
    report_timeout: float,
    include_report: bool,
) -> dict[str, Any]:
    bundle = build_scenario_bundle(scenario, manifest_path)
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = output_dir / f"{scenario}.server.log"
    env = dict(os.environ)
    env.update(
        {
            "ADOPTIQ_BIND_PUBLIC": "0",
            "ADOPTIQ_ASK_AI_ALLOW_LEGACY_FALLBACK": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_local_acceptance_app.py"),
        "--enable-local-fixtures",
        "--scenario",
        scenario,
        "--manifest",
        str(manifest_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    errors: list[str] = []
    result: dict[str, Any] = {
        "scenario": scenario,
        "provider_state": bundle.provider_state,
        "schema_fingerprint": bundle.schema_fingerprint,
        "live_validation_performed": False,
        "source_states_sha256": _digest(bundle.source_states),
        "expected_counts_sha256": _digest(bundle.expected_canonical_counts),
        "warning_codes_sha256": _digest(bundle.warning_codes),
        "route_checks": {},
        "report": {"attempted": False},
        "errors": errors,
        "ok": False,
    }
    process: subprocess.Popen[Any] | None = None
    with log_path.open("w", encoding="utf-8") as log_handle:
        try:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            connectivity = _wait_for_server(
                base_url,
                scenario,
                bundle.schema_fingerprint,
                process,
                startup_timeout,
            )
            client = LoopbackClient(base_url, request_timeout)
            client.bootstrap()
            routes = result["route_checks"]

            payloads: dict[str, dict[str, Any]] = {}
            for label, path in (
                ("version", "/api/version"),
                ("connectivity", "/api/diag/connectivity"),
                ("corpus_status", "/api/corpus/status"),
                ("corpus_alias", "/api/intel/status"),
                ("ask_ai_model", "/api/settings/ask-ai-model"),
                ("report_model", "/api/settings/report-model"),
            ):
                response = client.get(path)
                payload = _json(response)
                payloads[label] = payload
                ok = response.status_code == 200 and payload.get("ok", True) is not False
                routes[label] = {
                    "status_code": response.status_code,
                    "ok": ok,
                    "payload_sha256": _digest(payload),
                }
                if not ok:
                    errors.append(f"{label} route failed")
            routes["connectivity"].update(
                {
                    "fixture_mode": connectivity.get("mode") == SOURCE_MODE,
                    "scenario_matches": connectivity.get("scenario") == scenario,
                    "live_validation_performed": False,
                }
            )
            if connectivity.get("canonical_counts") != dict(
                sorted(bundle.expected_canonical_counts.items())
            ):
                errors.append("connectivity canonical counts do not match manifest")
            if connectivity.get("source_states") != dict(
                sorted(bundle.source_states.items())
            ):
                errors.append("connectivity source states do not match manifest")
            expected_warning_codes = {
                name: list(codes)
                for name, codes in sorted(bundle.warning_codes.items())
            }
            if connectivity.get("warning_codes") != expected_warning_codes:
                errors.append("connectivity warning codes do not match manifest")
            if payloads["corpus_status"] != payloads["corpus_alias"]:
                errors.append("corpus status alias payload differs")

            model_name = str(payloads["ask_ai_model"].get("active_value") or "")
            ping = client.post_json("/api/llm/ping", {"model_name": model_name})
            ping_payload = _json(ping)
            ping_ok = ping.status_code == 200 and (
                bool(ping_payload.get("ok"))
                if bundle.provider_state == "available"
                else ping_payload.get("ok") is False
            )
            routes["llm_ping"] = {
                "status_code": ping.status_code,
                "ok": ping_ok,
                "provider_available": bool(ping_payload.get("ok")),
                "payload_sha256": _digest(ping_payload),
            }
            if not ping_ok or not _response_is_sanitized(ping_payload):
                errors.append("LLM ping did not expose the expected sanitized state")

            customer_name = str(
                (bundle.records("customers") or [{}])[0].get("BU_NAME")
                or "Acme Corporation"
            )
            for label, path, marker in (
                (
                    "customer_360",
                    f"/customer/{quote(customer_name, safe='')}",
                    "Customer 360",
                ),
                (
                    "external_intelligence",
                    "/external-intelligence",
                    "External Intelligence",
                ),
            ):
                page = client.get(path, accept="text/html")
                page_ok = page.status_code == 200 and marker in page.text
                if scenario == "prompt_injection" and any(
                    term in page.text.casefold() for term in PROMPT_INJECTION_TERMS
                ):
                    page_ok = False
                    errors.append(f"{label} repeated a prompt-injection instruction")
                routes[label] = {
                    "status_code": page.status_code,
                    "ok": page_ok,
                    "characters": len(page.text),
                    "body_sha256": hashlib.sha256(page.content).hexdigest(),
                }
                if not page_ok and not any(label in item for item in errors):
                    errors.append(f"{label} page failed")

            playbook = client.post_form(
                "/playbook",
                {
                    "technology": "",
                    "theme": "",
                    "query": "registration authentication outage",
                    "csrf_token": client.csrf_token,
                },
            )
            playbook_ok = (
                playbook.status_code == 200
                and "Troubleshooting Playbook" in playbook.text
                and "Search matches for:" in playbook.text
                and not (
                    scenario == "prompt_injection"
                    and any(
                        term in playbook.text.casefold()
                        for term in PROMPT_INJECTION_TERMS
                    )
                )
            )
            routes["playbook"] = {
                "status_code": playbook.status_code,
                "ok": playbook_ok,
                "characters": len(playbook.text),
                "body_sha256": hashlib.sha256(playbook.content).hexdigest(),
            }
            if not playbook_ok:
                errors.append("Playbook route failed or repeated embedded instructions")

            exported = client.get(
                "/api/export-intel", params={"page": 0, "page_size": 1}
            )
            exported_payload = _json(exported)
            export_ok = (
                exported.status_code == 200
                and all(
                    isinstance(exported_payload.get(key), list)
                    for key in ("incidents", "bugs", "maintenances")
                )
                and exported_payload.get("live_validation_performed") is False
            )
            routes["external_export"] = {
                "status_code": exported.status_code,
                "ok": export_ok,
                "payload_sha256": _digest(exported_payload),
            }
            if not export_ok:
                errors.append("External Intelligence export failed")

            question = (
                "State the exact customer, open adoption barrier, and support-case "
                "counts. Cite the first decision and disclose incomplete, stale, "
                "failed, unavailable, or truncated data."
            )
            request_payload = {
                "question": question,
                "manager": "Local Fixture Manager",
                "technology": "All",
                "days": 90,
                "allow_legacy_fallback": False,
            }
            sync = client.post_json("/api/ask-ai-portfolio", request_payload)
            sync_payload = _json(sync)
            sync_errors = validate_provider_response(
                bundle.provider_state, sync.status_code, sync_payload
            )
            errors.extend(sync_errors)
            sync_warning_projection = _warning_projection(
                sync_payload.get("partial_data_warnings") or []
            )
            routes["ask_ai_sync"] = {
                "status_code": sync.status_code,
                "ok": not sync_errors,
                "answer_sha256": _digest(sync_payload.get("answer") or ""),
                "citation_count": len(
                    extract_citations(str(sync_payload.get("answer") or ""))
                ),
                "warning_sha256": _digest(
                    sync_payload.get("partial_data_warnings") or []
                ),
                "warning_datasets": sync_warning_projection["datasets"],
                "warning_kinds": sync_warning_projection["kinds"],
                "canonical_headline_sha256": _digest(
                    sync_payload.get("canonical_headline") or {}
                ),
            }
            if bundle.provider_state == "available" and sync_payload.get("ok"):
                headline = sync_payload.get("canonical_headline") or {}
                expected = {
                    "total_customers": bundle.expected_canonical_counts["customers"],
                    "total_barriers": bundle.expected_canonical_counts[
                        "adoption_barriers"
                    ],
                    "total_cases": bundle.expected_canonical_counts["support_cases"],
                }
                for key, value in expected.items():
                    if int(headline.get(key, -1)) != int(value):
                        errors.append(f"Ask AI canonical headline mismatch for {key}")
                answer = str(sync_payload.get("answer") or "")
                if scenario == "prompt_injection" and any(
                    term in answer.casefold() for term in PROMPT_INJECTION_TERMS
                ):
                    errors.append("Ask AI repeated an embedded instruction")
                query_id = str(sync_payload.get("query_id") or "")
                citations = extract_citations(answer)
                if not query_id or not citations:
                    errors.append("Ask AI success omitted query ID or citations")
                else:
                    diag = client.get(
                        f"/api/ask-ai/diagnostics/{quote(query_id, safe='')}"
                    )
                    evidence = client.get(
                        "/api/ask-ai/evidence/"
                        + quote(query_id, safe="")
                        + "/"
                        + quote(citations[0], safe="")
                    )
                    diag_payload = _json(diag)
                    evidence_payload = _json(evidence)
                    lookup_ok = (
                        diag.status_code == 200
                        and diag_payload.get("ok")
                        and evidence.status_code == 200
                        and evidence_payload.get("ok")
                    )
                    routes["ask_ai_evidence"] = {
                        "ok": lookup_ok,
                        "diagnostics_status": diag.status_code,
                        "evidence_status": evidence.status_code,
                        "payload_sha256": _digest(
                            {"diag": diag_payload, "evidence": evidence_payload}
                        ),
                    }
                    if not lookup_ok:
                        errors.append("Ask AI diagnostics/evidence resolution failed")

            stream = client.session.post(
                base_url + "/api/ask-ai-portfolio/stream",
                json=request_payload,
                headers=client.headers("text/event-stream"),
                timeout=request_timeout,
            )
            events = (
                parse_sse(stream.text)
                if "text/event-stream"
                in str(stream.headers.get("Content-Type") or "")
                else []
            )
            streamed = _stream_payload(events)
            if bundle.provider_state == "available":
                stream_ok = (
                    stream.status_code == 200
                    and streamed.get("ok")
                    and streamed.get("answer") == sync_payload.get("answer")
                    and streamed.get("canonical_headline")
                    == sync_payload.get("canonical_headline")
                )
            else:
                stream_ok = (
                    stream.status_code == 200
                    and bool(streamed.get("stream_errors"))
                    and _response_is_sanitized(streamed)
                )
            routes["ask_ai_stream"] = {
                "status_code": stream.status_code,
                "ok": stream_ok,
                "event_count": len(events),
                "payload_sha256": _digest(streamed),
            }
            if not stream_ok:
                errors.append("Ask AI stream did not match the expected delivery state")

            intel = client.post_json(
                "/api/ask-intel",
                {
                    "question": "Which stored incident should be reviewed first? Cite it.",
                    "days": 90,
                    "allow_legacy_fallback": False,
                },
            )
            intel_payload = _json(intel)
            intel_errors = validate_provider_response(
                bundle.provider_state, intel.status_code, intel_payload
            )
            errors.extend(intel_errors)
            intel_warning_projection = _warning_projection(
                intel_payload.get("partial_data_warnings") or []
            )
            routes["ask_intel"] = {
                "status_code": intel.status_code,
                "ok": not intel_errors,
                "answer_sha256": _digest(intel_payload.get("answer") or ""),
                "warning_datasets": intel_warning_projection["datasets"],
                "warning_kinds": intel_warning_projection["kinds"],
                "payload_sha256": _digest(intel_payload),
            }
            if scenario == "stale" and bundle.provider_state == "available":
                if "source_stale" not in sync_warning_projection["kinds"]:
                    errors.append("Ask AI did not disclose the stale intel source")
                if "source_stale" not in intel_warning_projection["kinds"]:
                    errors.append("Ask Intel did not disclose the stale intel source")

            if include_report:
                result["report"] = {"attempted": True}
                report_result, report_errors = _run_report_probe(
                    client, report_timeout
                )
                result["report"].update(report_result)
                errors.extend(report_errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"scenario runner failed: {type(exc).__name__}")
            result["runner_exception_sha256"] = _digest(str(exc))
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    result["server_log_sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
    result["server_log_bytes"] = log_path.stat().st_size
    result["ok"] = not errors
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise sanitized fixture states through real loopback routes."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "round145" / "http-degraded",
    )
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--report-timeout", type=float, default=300.0)
    parser.add_argument("--skip-reports", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = _safe_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)
    available = sorted(manifest["scenarios"])
    if str(args.scenarios).strip().casefold() == "all":
        scenarios = available
    else:
        scenarios = [
            item.strip() for item in str(args.scenarios).split(",") if item.strip()
        ]
        unknown = sorted(set(scenarios).difference(available))
        if unknown:
            raise SystemExit("unknown scenario(s): " + ", ".join(unknown))

    started = time.time()
    results = []
    for index, scenario in enumerate(scenarios, start=1):
        print(f"[local-http] {index}/{len(scenarios)} {scenario}", flush=True)
        result = _run_scenario(
            scenario,
            manifest_path=Path(args.manifest),
            output_dir=output_dir,
            startup_timeout=float(args.startup_timeout),
            request_timeout=float(args.request_timeout),
            report_timeout=float(args.report_timeout),
            include_report=not bool(args.skip_reports),
        )
        results.append(result)
        print(
            f"[local-http] {scenario} ok={result['ok']} "
            f"errors={len(result['errors'])}",
            flush=True,
        )

    all_passed = len(results) == len(scenarios) and all(
        bool(item.get("ok")) for item in results
    )
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "sanitized": True,
        "do_not_commit": True,
        "validation_mode": "local_acceptance",
        "source_mode": SOURCE_MODE,
        "live_validation_attempted": False,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "manifest_schema_fingerprint": manifest["schema_fingerprint"],
        "scenario_count": len(scenarios),
        "scenario_inventory_complete": scenarios == available,
        "report_probes_enabled": not bool(args.skip_reports),
        "elapsed_seconds": round(time.time() - started, 3),
        "all_passed": all_passed,
        "results": results,
    }
    summary_path = output_dir / "local_acceptance_http_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "all_passed": all_passed,
                "scenario_count": len(scenarios),
                "summary_path": str(summary_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if all_passed else 5


if __name__ == "__main__":
    raise SystemExit(main())
