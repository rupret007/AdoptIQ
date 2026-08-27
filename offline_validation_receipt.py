"""Signed, redacted receipts for AdoptIQ guarded offline validation.

This contract is deliberately narrower than a release receipt.  It can attest
that one exact, sanitized fixture report passed deterministic offline gates; it
cannot attest live Cisco accuracy, manual source reconciliation, release
approval, or permission to share with a customer.

The private signing key belongs to an external trusted acceptance runner and is
never loaded from application settings by this module.  Callers provide an
explicit allow-list of Ed25519 public keys when persisting or reloading a
receipt.  Missing trust configuration therefore fails closed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


SCHEMA_VERSION = "adoptiq-offline-fixture-validation-receipt/v1"
RECEIPT_KIND = "guarded_offline_fixture"
SIGNATURE_ALGORITHM = "Ed25519"
FIXTURE_MANIFEST_SCHEMA = "adoptiq-sanitized-offline-fixture/v1"
MAX_RECEIPT_BYTES = 16 * 1024
MAX_RECEIPT_LIFETIME = timedelta(days=7)
MAX_FUTURE_SKEW = timedelta(minutes=5)

_SIGNATURE_DOMAIN = b"AdoptIQ offline fixture validation receipt v1\x00"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_REPORT_TYPES = {
    "leader",
    "comprehensive",
    "compact",
    "renewal_portfolio",
    "renewal",
    "subscription",
}
# This receipt is the foundation for the exact customer/subscription web view
# added in #5. Team/member/portfolio output remains internal and cannot acquire
# a receipt that might later be confused with customer-output validation.
_SCOPE_TYPES = {"customer", "subscription"}

_REQUIRED_GATES: Mapping[str, bool] = {
    "canonical_snapshot_verified": True,
    "decision_insight_integrity_verified": True,
    "evidence_integrity_verified": True,
    "formula_free_verified": True,
    "guarded_fixture_mode_verified": True,
    "no_disallowed_source_warnings_verified": True,
    "persisted_artifact_hashes_verified": True,
    "report_completed_verified": True,
    "fixture_source_states_bounded_verified": True,
}

_HONESTY_ASSERTIONS: Mapping[str, bool] = {
    "customer_shareable": False,
    "fixture_validation_passed": True,
    "fixture_validation_performed": True,
    "live_validation_attempted": False,
    "live_validation_passed": False,
    "live_validation_performed": False,
    "manual_source_reconciliation_complete": False,
    "owner_customer_share_approved": False,
    "production_accuracy_claimed": False,
    "ready_for_live_cisco": False,
    "release_ready": False,
}


class OfflineValidationReceiptError(ValueError):
    """A receipt is malformed, untrusted, stale, or bound to another run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReceiptExpectation:
    """Server-owned identity that a signed receipt must match exactly."""

    report_history_id: int
    analysis_id: str
    report_type: str
    scope_type: str
    fact_fingerprint: str
    word_sha256: str
    excel_sha256: str
    data_as_of_utc: str
    scope_identity_sha256: str
    web_projection_sha256: str
    source_commit_sha: str
    validator_build_sha256: str
    fixture_manifest_sha256: str
    gate_spec_sha256: str


@dataclass(frozen=True)
class VerifiedOfflineValidationReceipt:
    """Immutable, signature-verified receipt metadata safe for server logic."""

    receipt_id: str
    signer_key_id: str
    report_history_id: int
    analysis_id_sha256: str
    report_type: str
    scope_type: str
    scope_identity_sha256: str
    fact_fingerprint: str
    word_sha256: str
    excel_sha256: str
    web_projection_sha256: str
    data_as_of_utc: str
    issued_at_utc: str
    expires_at_utc: str
    source_commit_sha: str
    validator_build_sha256: str
    fixture_manifest_sha256: str
    gate_spec_sha256: str
    canonical_json: str

    def public_summary(self) -> dict[str, Any]:
        """Return a path-, signature-, identity-, and customer-free summary."""

        return {
            "state": "verified_offline_fixture",
            "fixture_validation_performed": True,
            "fixture_validation_passed": True,
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "release_ready": False,
            "customer_shareable": False,
            "ready_for_live_cisco": False,
        }


@dataclass(frozen=True)
class OfflineValidationReceiptStatus:
    """Fail-closed persistence/verifier result used by the web projection."""

    state: str
    receipt: VerifiedOfflineValidationReceipt | None = None

    @property
    def verified(self) -> bool:
        return (
            self.state == "verified_offline_fixture"
            and isinstance(self.receipt, VerifiedOfflineValidationReceipt)
        )

    def public_summary(self) -> dict[str, Any]:
        if self.verified:
            # Construct the fixed public truth surface here instead of
            # forwarding through a caller-supplied object.
            return {
                "state": "verified_offline_fixture",
                "fixture_validation_performed": True,
                "fixture_validation_passed": True,
                "live_validation_performed": False,
                "production_accuracy_claimed": False,
                "release_ready": False,
                "customer_shareable": False,
                "ready_for_live_cisco": False,
            }
        state = self.state if self.state in {
            "missing",
            "unconfigured",
            "invalid",
            "stale",
            "artifact_mismatch",
            "conflict",
        } else "invalid"
        return {
            "state": state,
            "fixture_validation_performed": False,
            "fixture_validation_passed": False,
            "live_validation_performed": False,
            "production_accuracy_claimed": False,
            "release_ready": False,
            "customer_shareable": False,
            "ready_for_live_cisco": False,
        }


def analysis_id_sha256(analysis_id: object) -> str:
    value = str(analysis_id or "").strip()
    if not value or len(value) > 500:
        raise OfflineValidationReceiptError("invalid", "analysis id is missing or oversized")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def scope_identity_sha256(
    *,
    report_type: object,
    scope_type: object,
    scope_value: object = "",
    scope_member: object = "",
    manager: object = "",
    technology: object = "",
    days: object = None,
) -> str:
    """Hash exact report selectors without persisting their private values."""

    try:
        normalized_days = int(days) if days not in (None, "") and not isinstance(days, bool) else None
    except (TypeError, ValueError) as exc:
        raise OfflineValidationReceiptError("invalid", "scope days must be a whole number") from exc
    if normalized_days is not None and not 1 <= normalized_days <= 3650:
        raise OfflineValidationReceiptError("invalid", "scope days are outside the supported range")

    def normalized(value: object, *, maximum: int, casefold: bool = False) -> str:
        text = str(value or "").strip()
        if len(text) > maximum or any(ord(character) < 32 for character in text):
            raise OfflineValidationReceiptError("invalid", "scope identity field is invalid")
        return text.casefold() if casefold else text

    identity = {
        "days": normalized_days,
        "manager": normalized(manager, maximum=240),
        "report_type": normalized(report_type, maximum=80, casefold=True),
        "scope_member": normalized(scope_member, maximum=320, casefold=True),
        "scope_type": normalized(scope_type, maximum=80, casefold=True),
        "scope_value": normalized(scope_value, maximum=300),
        "technology": normalized(technology, maximum=240),
    }
    if identity["report_type"] not in _REPORT_TYPES or identity["scope_type"] not in _SCOPE_TYPES:
        raise OfflineValidationReceiptError("invalid", "scope identity is unsupported")
    if not identity["scope_value"]:
        raise OfflineValidationReceiptError("invalid", "scope identity value is required")
    return hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()


def trusted_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def gate_spec_sha256() -> str:
    """Return the digest of the exact offline gate and honesty inventory."""

    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "gates": dict(_REQUIRED_GATES),
                "honesty": dict(_HONESTY_ASSERTIONS),
                "schema_version": SCHEMA_VERSION,
            }
        )
    ).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OfflineValidationReceiptError("invalid", "receipt is not canonical JSON") from exc


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise OfflineValidationReceiptError("invalid", f"{label} fields do not match the required schema")


def _require_string(value: Any, label: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise OfflineValidationReceiptError("invalid", f"{label} must be a canonical string")
    return value


def _require_sha256(value: Any, label: str) -> str:
    digest = _require_string(value, label, maximum=64)
    if _SHA256_RE.fullmatch(digest) is None:
        raise OfflineValidationReceiptError("invalid", f"{label} must be a lowercase SHA-256 digest")
    return digest


def _require_timestamp(value: Any, label: str) -> tuple[str, datetime]:
    timestamp = _require_string(value, label, maximum=27)
    if _UTC_TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise OfflineValidationReceiptError(
            "invalid",
            f"{label} must use a canonical UTC ISO-8601 Z timestamp",
        )
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError as exc:
        raise OfflineValidationReceiptError("invalid", f"{label} is not a valid UTC timestamp") from exc
    return timestamp, parsed


def canonical_utc_timestamp(value: object, label: str = "timestamp") -> str:
    """Normalize a server-owned aware ISO timestamp to canonical UTC ``Z``."""

    raw = _require_string(value, label, maximum=35)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfflineValidationReceiptError("invalid", f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise OfflineValidationReceiptError("invalid", f"{label} must include a UTC offset")
    normalized = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    _require_timestamp(normalized, label)
    return normalized


def _require_exact_bool_map(
    value: Any,
    expected: Mapping[str, bool],
    label: str,
) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise OfflineValidationReceiptError("invalid", f"{label} must be an object")
    _require_exact_keys(value, set(expected), label)
    for key, expected_value in expected.items():
        if type(value[key]) is not bool or value[key] is not expected_value:
            raise OfflineValidationReceiptError("invalid", f"{label}.{key} must be exact")
    return dict(expected)


def _validate_payload(payload: Any, *, now_utc: datetime) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise OfflineValidationReceiptError("invalid", "receipt payload must be an object")
    _require_exact_keys(
        payload,
        {
            "analysis_id_sha256",
            "data_as_of_utc",
            "excel_sha256",
            "expires_at_utc",
            "fact_fingerprint",
            "fixture",
            "gate_spec_sha256",
            "gates",
            "honesty",
            "issued_at_utc",
            "kind",
            "report_history_id",
            "report_type",
            "scope_identity_sha256",
            "scope_type",
            "source_commit_sha",
            "validator_build_sha256",
            "web_projection_sha256",
            "word_sha256",
        },
        "receipt payload",
    )
    if payload["kind"] != RECEIPT_KIND:
        raise OfflineValidationReceiptError("invalid", "receipt kind is unsupported")
    if isinstance(payload["report_history_id"], bool) or not isinstance(payload["report_history_id"], int) or payload["report_history_id"] <= 0:
        raise OfflineValidationReceiptError("invalid", "report_history_id must be a positive integer")
    report_type = _require_string(payload["report_type"], "report_type", maximum=80)
    scope_type = _require_string(payload["scope_type"], "scope_type", maximum=80)
    if report_type not in _REPORT_TYPES or scope_type not in _SCOPE_TYPES:
        raise OfflineValidationReceiptError("invalid", "report or scope type is unsupported")
    source_commit = _require_string(payload["source_commit_sha"], "source_commit_sha", maximum=40)
    if _SOURCE_COMMIT_RE.fullmatch(source_commit) is None:
        raise OfflineValidationReceiptError("invalid", "source_commit_sha must be a full lowercase Git SHA")
    _require_sha256(payload["validator_build_sha256"], "validator_build_sha256")

    fixture = payload["fixture"]
    if not isinstance(fixture, Mapping):
        raise OfflineValidationReceiptError("invalid", "fixture must be an object")
    _require_exact_keys(fixture, {"manifest_schema", "manifest_sha256"}, "fixture")
    if fixture["manifest_schema"] != FIXTURE_MANIFEST_SCHEMA:
        raise OfflineValidationReceiptError("invalid", "fixture manifest schema is unsupported")
    _require_sha256(fixture["manifest_sha256"], "fixture.manifest_sha256")
    _require_exact_bool_map(payload["gates"], _REQUIRED_GATES, "gates")
    _require_exact_bool_map(payload["honesty"], _HONESTY_ASSERTIONS, "honesty")
    expected_gate_spec = gate_spec_sha256()
    if not secrets.compare_digest(
        _require_sha256(payload["gate_spec_sha256"], "gate_spec_sha256"),
        expected_gate_spec,
    ):
        raise OfflineValidationReceiptError("invalid", "gate specification digest is unsupported")

    for key in (
        "analysis_id_sha256",
        "excel_sha256",
        "fact_fingerprint",
        "scope_identity_sha256",
        "web_projection_sha256",
        "word_sha256",
    ):
        _require_sha256(payload[key], key)
    _require_timestamp(payload["data_as_of_utc"], "data_as_of_utc")
    issued_text, issued = _require_timestamp(payload["issued_at_utc"], "issued_at_utc")
    expires_text, expires = _require_timestamp(payload["expires_at_utc"], "expires_at_utc")
    now = now_utc.astimezone(timezone.utc)
    if issued > now + MAX_FUTURE_SKEW:
        raise OfflineValidationReceiptError("stale", "receipt issue time is in the future")
    if expires <= issued or expires - issued > MAX_RECEIPT_LIFETIME:
        raise OfflineValidationReceiptError("stale", "receipt lifetime is invalid")
    if now >= expires:
        raise OfflineValidationReceiptError("stale", "receipt has expired")

    normalized = json.loads(_canonical_json_bytes(payload).decode("ascii"))
    normalized["issued_at_utc"] = issued_text
    normalized["expires_at_utc"] = expires_text
    return normalized


def _public_key_from_value(value: object) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if isinstance(value, bytes) and len(value) == 32:
        try:
            return Ed25519PublicKey.from_public_bytes(value)
        except ValueError as exc:
            raise OfflineValidationReceiptError("unconfigured", "trusted public key is invalid") from exc
    raise OfflineValidationReceiptError("unconfigured", "trusted public key is unavailable")


def _normalized_now(now_utc: datetime | None) -> datetime:
    value = now_utc or datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise OfflineValidationReceiptError("invalid", "verification clock must be a datetime")
    if value.tzinfo is None:
        raise OfflineValidationReceiptError("invalid", "verification clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def issue_offline_validation_receipt(
    *,
    private_key: Ed25519PrivateKey,
    report_history_id: int,
    analysis_id: str,
    report_type: str,
    scope_type: str,
    fact_fingerprint: str,
    word_sha256: str,
    excel_sha256: str,
    web_projection_sha256: str,
    data_as_of_utc: str,
    scope_identity_sha256: str,
    source_commit_sha: str,
    validator_build_sha256: str,
    fixture_manifest_sha256: str,
    issued_at_utc: str,
    expires_at_utc: str,
) -> dict[str, Any]:
    """Sign one fixture-only receipt without accepting positive live claims."""

    if not isinstance(private_key, Ed25519PrivateKey):
        raise OfflineValidationReceiptError("invalid", "an Ed25519 private key is required")
    public_key = private_key.public_key()
    key_id = trusted_key_id(public_key)
    payload = {
        "analysis_id_sha256": analysis_id_sha256(analysis_id),
        "data_as_of_utc": data_as_of_utc,
        "excel_sha256": excel_sha256,
        "expires_at_utc": expires_at_utc,
        "fact_fingerprint": fact_fingerprint,
        "fixture": {
            "manifest_schema": FIXTURE_MANIFEST_SCHEMA,
            "manifest_sha256": fixture_manifest_sha256,
        },
        "gate_spec_sha256": gate_spec_sha256(),
        "gates": dict(_REQUIRED_GATES),
        "honesty": dict(_HONESTY_ASSERTIONS),
        "issued_at_utc": issued_at_utc,
        "kind": RECEIPT_KIND,
        "report_history_id": report_history_id,
        "report_type": report_type,
        "scope_identity_sha256": scope_identity_sha256,
        "scope_type": scope_type,
        "source_commit_sha": source_commit_sha,
        "validator_build_sha256": validator_build_sha256,
        "web_projection_sha256": web_projection_sha256,
        "word_sha256": word_sha256,
    }
    _, issued = _require_timestamp(issued_at_utc, "issued_at_utc")
    normalized_payload = _validate_payload(payload, now_utc=issued)
    payload_bytes = _canonical_json_bytes(normalized_payload)
    signed_bytes = _SIGNATURE_DOMAIN + payload_bytes
    receipt_id = hashlib.sha256(signed_bytes).hexdigest()
    signature = private_key.sign(signed_bytes)
    return {
        "payload": normalized_payload,
        "receipt_id": receipt_id,
        "schema_version": SCHEMA_VERSION,
        "signature": base64.b64encode(signature).decode("ascii"),
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signer_key_id": key_id,
    }


def verify_offline_validation_receipt(
    envelope: Any,
    *,
    trusted_public_keys: Mapping[str, object],
    expectation: ReceiptExpectation | None = None,
    now_utc: datetime | None = None,
) -> VerifiedOfflineValidationReceipt:
    """Verify schema, signature, time window, honesty, and exact run binding."""

    if not isinstance(envelope, Mapping):
        raise OfflineValidationReceiptError("invalid", "receipt must be an object")
    _require_exact_keys(
        envelope,
        {
            "payload",
            "receipt_id",
            "schema_version",
            "signature",
            "signature_algorithm",
            "signer_key_id",
        },
        "receipt",
    )
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise OfflineValidationReceiptError("invalid", "receipt schema is unsupported")
    if envelope["signature_algorithm"] != SIGNATURE_ALGORITHM:
        raise OfflineValidationReceiptError("invalid", "receipt signature algorithm is unsupported")
    receipt_id = _require_sha256(envelope["receipt_id"], "receipt_id")
    key_id = _require_sha256(envelope["signer_key_id"], "signer_key_id")
    if not isinstance(trusted_public_keys, Mapping) or key_id not in trusted_public_keys:
        raise OfflineValidationReceiptError("unconfigured", "receipt signer is not trusted")
    public_key = _public_key_from_value(trusted_public_keys[key_id])
    if not secrets.compare_digest(trusted_key_id(public_key), key_id):
        raise OfflineValidationReceiptError("unconfigured", "trusted key id does not match its public key")

    payload = _validate_payload(envelope["payload"], now_utc=_normalized_now(now_utc))
    payload_bytes = _canonical_json_bytes(payload)
    signed_bytes = _SIGNATURE_DOMAIN + payload_bytes
    if not secrets.compare_digest(hashlib.sha256(signed_bytes).hexdigest(), receipt_id):
        raise OfflineValidationReceiptError("invalid", "receipt id does not match its signed payload")
    signature_text = _require_string(envelope["signature"], "signature", maximum=128)
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OfflineValidationReceiptError("invalid", "receipt signature is not canonical base64") from exc
    if len(signature) != 64:
        raise OfflineValidationReceiptError("invalid", "receipt signature length is invalid")
    if not secrets.compare_digest(
        signature_text,
        base64.b64encode(signature).decode("ascii"),
    ):
        raise OfflineValidationReceiptError("invalid", "receipt signature is not canonical base64")
    try:
        public_key.verify(signature, signed_bytes)
    except InvalidSignature as exc:
        raise OfflineValidationReceiptError("invalid", "receipt signature verification failed") from exc

    if expectation is not None:
        expected = {
            "report_history_id": expectation.report_history_id,
            "analysis_id_sha256": analysis_id_sha256(expectation.analysis_id),
            "report_type": expectation.report_type,
            "scope_type": expectation.scope_type,
            "scope_identity_sha256": expectation.scope_identity_sha256,
            "fact_fingerprint": expectation.fact_fingerprint,
            "word_sha256": expectation.word_sha256,
            "excel_sha256": expectation.excel_sha256,
            "data_as_of_utc": expectation.data_as_of_utc,
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                raise OfflineValidationReceiptError("artifact_mismatch", "receipt does not match the persisted report")
        current_code_expected = {
            "web_projection_sha256": expectation.web_projection_sha256,
            "source_commit_sha": expectation.source_commit_sha,
            "validator_build_sha256": expectation.validator_build_sha256,
            "gate_spec_sha256": expectation.gate_spec_sha256,
        }
        for key, value in current_code_expected.items():
            if payload.get(key) != value:
                raise OfflineValidationReceiptError("artifact_mismatch", "receipt does not match current validation code")
        if payload["fixture"]["manifest_sha256"] != expectation.fixture_manifest_sha256:
            raise OfflineValidationReceiptError("artifact_mismatch", "receipt fixture identity does not match")

    canonical_json = _canonical_json_bytes(dict(envelope)).decode("ascii")
    return VerifiedOfflineValidationReceipt(
        receipt_id=receipt_id,
        signer_key_id=key_id,
        report_history_id=payload["report_history_id"],
        analysis_id_sha256=payload["analysis_id_sha256"],
        report_type=payload["report_type"],
        scope_type=payload["scope_type"],
        scope_identity_sha256=payload["scope_identity_sha256"],
        fact_fingerprint=payload["fact_fingerprint"],
        word_sha256=payload["word_sha256"],
        excel_sha256=payload["excel_sha256"],
        web_projection_sha256=payload["web_projection_sha256"],
        data_as_of_utc=payload["data_as_of_utc"],
        issued_at_utc=payload["issued_at_utc"],
        expires_at_utc=payload["expires_at_utc"],
        source_commit_sha=payload["source_commit_sha"],
        validator_build_sha256=payload["validator_build_sha256"],
        fixture_manifest_sha256=payload["fixture"]["manifest_sha256"],
        gate_spec_sha256=payload["gate_spec_sha256"],
        canonical_json=canonical_json,
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OfflineValidationReceiptError("invalid", "receipt contains duplicate JSON fields")
        result[key] = value
    return result


def parse_offline_validation_receipt_json(raw: object) -> dict[str, Any]:
    """Decode bounded UTF-8 JSON while rejecting duplicate fields and NaN."""

    if isinstance(raw, str):
        try:
            body = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise OfflineValidationReceiptError("invalid", "receipt is not valid UTF-8") from exc
    elif isinstance(raw, bytes):
        body = raw
    else:
        raise OfflineValidationReceiptError("invalid", "receipt JSON must be text or bytes")
    if not body or len(body) > MAX_RECEIPT_BYTES:
        raise OfflineValidationReceiptError("invalid", "receipt JSON is empty or oversized")

    def reject_constant(_value: str) -> None:
        raise OfflineValidationReceiptError("invalid", "receipt JSON contains a non-finite number")

    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=reject_constant,
        )
    except OfflineValidationReceiptError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineValidationReceiptError("invalid", "receipt is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise OfflineValidationReceiptError("invalid", "receipt JSON root must be an object")
    return payload


def canonical_receipt_json(envelope: Any) -> str:
    """Return deterministic compact JSON after enforcing the byte bound."""

    body = _canonical_json_bytes(envelope)
    if len(body) > MAX_RECEIPT_BYTES:
        raise OfflineValidationReceiptError("invalid", "receipt JSON exceeds the storage limit")
    return body.decode("ascii")


__all__ = [
    "FIXTURE_MANIFEST_SCHEMA",
    "MAX_RECEIPT_BYTES",
    "OfflineValidationReceiptError",
    "OfflineValidationReceiptStatus",
    "ReceiptExpectation",
    "SCHEMA_VERSION",
    "VerifiedOfflineValidationReceipt",
    "analysis_id_sha256",
    "canonical_utc_timestamp",
    "canonical_receipt_json",
    "gate_spec_sha256",
    "issue_offline_validation_receipt",
    "parse_offline_validation_receipt_json",
    "scope_identity_sha256",
    "trusted_key_id",
    "verify_offline_validation_receipt",
]
