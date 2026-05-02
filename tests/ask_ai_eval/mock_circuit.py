"""Round 66 / Pass 4 - Record/replay mock CircuIT client for the Ask AI eval.

Modes
-----
- ``replay`` (default): for each ``(question_id, prompt_hash)`` look up
  the cassette under ``tests/ask_ai_eval/cassettes/<question_id>.json``.
  If the file is missing OR the recorded prompt_hash disagrees with the
  current prompt's hash, raise ``CassetteMissError`` so the caller fails
  loud (the alternative - silently fabricating a response - would
  poison the eval scorecard).
- ``record``: append ``(prompt_hash, response_payload)`` to the cassette
  file. Used once by the operator against live CircuIT to seed the
  deterministic baseline. Document the recording step in
  ``QUALITY_AUDIT.md`` rather than wiring it into ``make eval-ask-ai``.

Cassette format (``cassettes/<question_id>.json``)::

    {
      "question_id": "p01_q03",
      "recorded_at": "2026-05-02T08:30:00Z",
      "prompt_hash": "<sha256-hex>",
      "response": { "executive_summary": "...", "claims": [...], ... }
    }

The cassette is intentionally a single object per question (not a list)
because a fresh recording supersedes the previous one. If the operator
wants to compare two recordings, they should diff the git history
rather than accumulate cassette versions in-place.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_CASSETTE_DIR_ENV = "ASK_AI_EVAL_CASSETTE_DIR"
_MODE_ENV = "MOCK_CIRCUIT_MODE"
_DEFAULT_MODE = "replay"


class CassetteMissError(RuntimeError):
    """Raised when replay mode cannot find a matching cassette."""


@dataclass(frozen=True)
class _Cassette:
    question_id: str
    recorded_at: str
    prompt_hash: str
    response: Dict[str, Any]


def _hash_prompt(prompt: str) -> str:
    h = hashlib.sha256()
    h.update((prompt or "").encode("utf-8"))
    return h.hexdigest()


def _default_cassette_dir() -> Path:
    override = os.environ.get(_CASSETTE_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "cassettes"


def _cassette_path(question_id: str, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _default_cassette_dir()
    safe = "".join(ch for ch in str(question_id) if ch.isalnum() or ch in ("_", "-")).strip("_-")
    if not safe:
        raise ValueError(f"invalid question_id: {question_id!r}")
    return base / f"{safe}.json"


def _read_cassette(path: Path) -> Optional[_Cassette]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _Cassette(
        question_id=str(raw.get("question_id") or ""),
        recorded_at=str(raw.get("recorded_at") or ""),
        prompt_hash=str(raw.get("prompt_hash") or ""),
        response=raw.get("response") or {},
    )


def _write_cassette(path: Path, cassette: _Cassette) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "question_id": cassette.question_id,
        "recorded_at": cassette.recorded_at,
        "prompt_hash": cassette.prompt_hash,
        "response": cassette.response,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class MockCircuitClient:
    """Pluggable LLM client for the eval runner.

    The real ``compose_grounded_answer`` consumes a JSON dict (the
    parsed LLM response). This client returns that dict directly via
    ``call(question_id, prompt)``. Production code paths are unaffected
    - this class is only constructed by the eval runner.
    """

    def __init__(
        self,
        mode: Optional[str] = None,
        cassette_dir: Optional[Path] = None,
        live_client: Optional[Any] = None,
        strict_hash: bool = True,
    ) -> None:
        self._mode = (mode or os.environ.get(_MODE_ENV) or _DEFAULT_MODE).strip().lower()
        if self._mode not in {"replay", "record"}:
            raise ValueError(f"unknown mock circuit mode: {self._mode!r}")
        if self._mode == "record" and live_client is None:
            raise ValueError("record mode requires a live_client callable")
        self._cassette_dir = cassette_dir or _default_cassette_dir()
        self._live_client = live_client
        self._strict_hash = bool(strict_hash)
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return self._mode

    def call(self, question_id: str, prompt: str) -> Dict[str, Any]:
        prompt_hash = _hash_prompt(prompt)
        path = _cassette_path(question_id, self._cassette_dir)
        if self._mode == "record":
            return self._record(path, question_id, prompt, prompt_hash)
        return self._replay(path, question_id, prompt_hash)

    def _replay(self, path: Path, question_id: str, prompt_hash: str) -> Dict[str, Any]:
        with self._lock:
            cassette = _read_cassette(path)
        if cassette is None:
            raise CassetteMissError(
                f"no cassette for question_id={question_id!r} at {path} (replay mode)"
            )
        if self._strict_hash and cassette.prompt_hash and cassette.prompt_hash != prompt_hash:
            raise CassetteMissError(
                f"prompt drift for question_id={question_id!r}: "
                f"recorded={cassette.prompt_hash[:12]}..., current={prompt_hash[:12]}... "
                "(re-record cassette with MOCK_CIRCUIT_MODE=record)"
            )
        return dict(cassette.response or {})

    def _record(
        self,
        path: Path,
        question_id: str,
        prompt: str,
        prompt_hash: str,
    ) -> Dict[str, Any]:
        if self._live_client is None:  # pragma: no cover - validated in __init__
            raise RuntimeError("record mode reached without a live_client")
        try:
            response = self._live_client(prompt)
        except Exception as e:  # noqa: BLE001 - bubble live errors loudly
            raise RuntimeError(f"live circuit call failed for {question_id!r}: {e}") from e
        if not isinstance(response, dict):
            raise RuntimeError(
                f"live circuit returned non-dict ({type(response).__name__}) for {question_id!r}"
            )
        cassette = _Cassette(
            question_id=str(question_id),
            recorded_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            prompt_hash=prompt_hash,
            response=response,
        )
        with self._lock:
            _write_cassette(path, cassette)
        return dict(response)


__all__ = [
    "CassetteMissError",
    "MockCircuitClient",
    "_default_cassette_dir",
    "_cassette_path",
    "_hash_prompt",
]
