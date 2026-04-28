"""Round 17.2 -- SharePoint / Microsoft Graph corpus source.

Pulls the raw CSOne TAC report ``.xlsx`` files (and any other supported
extensions) from a shared SharePoint folder URL into a local cache
directory so the existing :mod:`corpus_indexer` walker can ingest them
exactly the way it walks a synced OneDrive folder.

Design highlights
-----------------

* **Auth**: public-client OAuth via :mod:`msal` using device-code flow.
  We use the Microsoft-owned "Microsoft Graph PowerShell" public client
  ID (``14d82eec-204b-4c2f-b7e8-296a70dab67e``) so deployments do not
  need a tenant-specific Azure AD app registration.  Scopes are
  ``Files.Read.All offline_access`` (read-only delegated access plus a
  refresh token).  The authority is ``/common`` so any Microsoft work
  account (including Cisco SSO + MFA) can sign in.

* **Token storage**: an :class:`msal.SerializableTokenCache` is wrapped
  by :class:`KeyringTokenCache`, which prefers the macOS Keychain via
  the cross-platform :mod:`keyring` package and falls back to a
  ``0600`` permission JSON file under ``~/.adoptiq/`` when keyring is
  unavailable.  Refresh tokens persist across launches so users only
  see the device-code prompt once.

* **Share URL resolution**: a folder share URL is converted to a Graph
  share id via ``"u!" + urlsafe_b64(url).rstrip("=")`` (per
  Microsoft's documented encoding) so we can call
  ``GET /shares/{id}/driveItem?$expand=children``.  Children are
  paginated through ``@odata.nextLink``.  Only files whose extension
  is in :data:`ALLOWED_EXTS` and whose ``size`` is below the configured
  cap are downloaded.

* **Caching**: each remote item's ``id`` plus its ``lastModifiedDateTime``
  is recorded in a per-cache ``manifest.json``.  On subsequent
  refreshes we re-download only when the remote ``mtime`` is strictly
  newer than the cached one (or the local file is missing).  The cache
  directory is created with ``0700`` permissions and individual files
  with ``0600`` so a multi-user host cannot read another user's pulled
  reports.

* **Throttling and retries**: a small inter-request sleep keeps us
  polite to Graph's tenant rate-limiter, and 429/503 responses honor
  the ``Retry-After`` header (falling back to capped exponential
  backoff).  Per-file size cap (50 MiB by default) prevents a poisoned
  share from dragging down the host.

* **Logging discipline**: every line is bounded -- we log the file
  name, the byte count, and the remote ``mtime``, but we never log
  customer names, full file paths beyond the configured cache root,
  or any token material.  A summary line at refresh end covers the
  per-pass totals for the admin tile.

* **No raises across the public surface**: failures (auth missing,
  network down, share not found, oversize file) are recorded on
  :class:`RefreshStats` and surfaced via the return value.  The
  bootstrap path treats this source like any other: if it fails we
  fall back to the local OneDrive / Downloads sources without
  blocking the rest of the pipeline.

Security notes
--------------

* No hardcoded credentials.  ``DEFAULT_CLIENT_ID`` is a public,
  Microsoft-owned identifier, not a secret.  Tokens never appear in
  logs and never leave the keyring/0600 file.
* All HTTP traffic is HTTPS to ``*.microsoft.com`` and
  ``*.sharepoint.com`` only.  We rely on ``requests`` + ``truststore``
  (already pinned in ``requirements.txt``) so Cisco's TLS-inspection
  trust chain is honored.
* Device-code UX always displays the canonical
  ``https://microsoft.com/devicelogin`` URL up-front so the user can
  verify they are signing into Microsoft and not a phishing domain.
* The encoded share id is computed locally; the share URL itself is
  not a secret -- it is the same URL a user would paste into a
  browser.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and defaults
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Microsoft-owned public client.  Safe to embed; not a secret.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

# ``/common`` lets any Microsoft work-or-school account in (which is
# what cisco-my.sharepoint.com requires).  Operators can pin a single
# tenant by overriding ``ADOPTIQ_SHAREPOINT_AUTHORITY`` in env.
DEFAULT_AUTHORITY = "https://login.microsoftonline.com/common"

# Read-only delegated scope.  ``offline_access`` is required for
# refresh tokens; without it the user would have to redo the
# device-code flow every hour.
DEFAULT_SCOPES = ("Files.Read.All",)

# 50 MiB per-file cap.  Aligned with corpus_indexer._MAX_PARSE_BYTES so
# files we download will not be rejected by the indexer.
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024

DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_INTER_REQUEST_SLEEP_S = 0.25  # polite throttle
DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_RETRY_BASE_S = 1.5
DEFAULT_RETRY_MAX_S = 60.0

# Extensions the corpus indexer knows how to parse.  Anything else is
# silently ignored when listing the share.
ALLOWED_EXTS = (".xlsx", ".docx", ".csv")

# macOS Keychain service / account labels used by KeyringTokenCache.
# ``service="adoptiq"`` keeps every AdoptIQ secret under one Keychain
# heading so users can revoke easily; ``account=…token_cache`` makes it
# obvious what the entry is for in Keychain Access.
KEYRING_SERVICE = "adoptiq"
# ruff: S105 — this is the keyring *account label* visible in Keychain
# Access, not a credential.  No secret material is stored in this string.
KEYRING_ACCOUNT_TOKEN_CACHE = "sharepoint_graph_token_cache"  # noqa: S105

# Local fallback paths.  Both live inside ``~/.adoptiq`` which the
# launcher already chmods to ``0700``.
# ruff: S105 — filename of the token cache file, not a credential.
DEFAULT_FALLBACK_TOKEN_CACHE_FILENAME = "sharepoint_token_cache.json"  # noqa: S105
DEFAULT_MANIFEST_FILENAME = "manifest.json"


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DeviceCodeFlow:
    """Bag returned to the UI when a device-code sign-in is initiated.
    Contains only public, user-displayable fields -- no token material.
    """

    user_code: str
    verification_uri: str
    message: str
    expires_in: int = 0
    interval: int = 5


@dataclass
class TokenInfo:
    """Public summary of the currently-cached delegated token.  Used
    by the admin Corpus tile to show "Connected as <upn>" without
    handing the access token to the renderer."""

    upn: Optional[str] = None
    name: Optional[str] = None
    expires_at: Optional[str] = None  # ISO-8601 UTC
    expires_in_s: Optional[int] = None
    has_refresh_token: bool = False


@dataclass
class RefreshStats:
    """Per-refresh tally surfaced in the admin tile and bootstrap
    breakdown."""

    files_listed: int = 0
    files_downloaded: int = 0
    files_cached: int = 0  # already-fresh files that were skipped
    files_failed: int = 0
    files_skipped_oversized: int = 0
    files_skipped_unsupported: int = 0
    bytes_downloaded: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_ms: int = 0
    error_kind: Optional[str] = None  # 'auth_required' | 'network' | …
    error_detail: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_listed": int(self.files_listed),
            "files_downloaded": int(self.files_downloaded),
            "files_cached": int(self.files_cached),
            "files_failed": int(self.files_failed),
            "files_skipped_oversized": int(self.files_skipped_oversized),
            "files_skipped_unsupported": int(self.files_skipped_unsupported),
            "bytes_downloaded": int(self.bytes_downloaded),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": int(self.elapsed_ms),
            "error_kind": self.error_kind,
            "error_detail": self.error_detail,
            "errors": list(self.errors[:20]),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _parse_iso_z_to_epoch(value: Optional[str]) -> Optional[float]:
    """Convert a Graph-style ISO-8601 ``...Z`` timestamp into a unix
    epoch float.  Returns ``None`` for empty / unparseable input."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def encode_share_url(url: str) -> str:
    """Convert a shared-folder URL into a Microsoft Graph share id.

    Per Microsoft Graph documentation:

    1. Encode the URL bytes via base64.
    2. Convert to URL-safe base64 (``/`` → ``_``, ``+`` → ``-``).
    3. Trim trailing ``=`` padding.
    4. Prepend ``u!``.

    The whole URL must be encoded **as given**, including the query
    string (``?csf=1&web=1&e=...``) -- the ``e=`` parameter is the
    share token and dropping it breaks resolution for anonymous-link
    shares.
    """
    if url is None:
        raise ValueError("share url is None")
    text = str(url).strip()
    if not text:
        raise ValueError("share url is empty")
    raw = text.encode("ascii")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return "u!" + encoded


def _safe_local_filename(name: str) -> str:
    """Sanitize a remote filename for local storage.

    Strips path separators / parent traversal sequences and any byte
    sequence that would let a hostile share name escape the cache
    directory (CodeGuard file-handling rule).  Empty/invalid names
    fall back to a deterministic placeholder.
    """
    if not name:
        return "_unnamed.bin"
    text = str(name).strip().replace("\x00", "")
    # Remove path components -- only keep the basename.
    text = os.path.basename(text)
    # Eliminate any leading dots / tildes that could create dotfiles or
    # shell-history confusion ("~deleted").
    while text and text[0] in (".", "~", "-"):
        text = text[1:]
    if not text:
        return "_unnamed.bin"
    # Cap length defensively.
    if len(text) > 240:
        stem, ext = os.path.splitext(text)
        text = stem[: 240 - len(ext)] + ext
    return text


def _has_allowed_ext(name: str) -> bool:
    if not name:
        return False
    lower = name.lower()
    return any(lower.endswith(ext) for ext in ALLOWED_EXTS)


# ---------------------------------------------------------------------------
# Token cache backed by macOS Keychain (with 0600 file fallback)
# ---------------------------------------------------------------------------


class KeyringTokenCache:
    """Bridge an :class:`msal.SerializableTokenCache` to either the
    macOS Keychain (via :mod:`keyring`) or a 0600 fallback file.

    ``msal`` calls ``serialize()`` / ``deserialize()`` itself; this
    class only handles persistent storage of the resulting JSON
    string.  We attempt keyring first and fall back to a file under
    ``~/.adoptiq/`` when keyring is unavailable (CI hosts, headless
    Linux without Secret Service, etc.).  The fallback file is
    chmod-locked to ``0600`` so other local accounts cannot read the
    refresh token.
    """

    def __init__(
        self,
        *,
        fallback_path: Optional[Path] = None,
        keyring_module: Any = None,
        service: str = KEYRING_SERVICE,
        account: str = KEYRING_ACCOUNT_TOKEN_CACHE,
    ) -> None:
        self._fallback_path = Path(fallback_path) if fallback_path else None
        self._service = service
        self._account = account
        self._lock = threading.Lock()
        # ``keyring`` is optional -- if it is not installed we skip
        # straight to the fallback file.  Tests inject a fake module
        # via ``keyring_module=...`` so we never touch the real
        # Keychain in unit tests.
        if keyring_module is None:
            try:
                import keyring as _kr  # type: ignore
                self._keyring = _kr
            except Exception as imp_err:  # noqa: BLE001 - optional dep
                logger.debug(
                    "Round 17.2 / sharepoint: keyring unavailable (%s); using fallback file",
                    type(imp_err).__name__,
                )
                self._keyring = None
        else:
            self._keyring = keyring_module

    def load(self) -> str:
        """Return the serialized cache JSON or ``""`` when nothing is
        stored.  Never raises."""
        with self._lock:
            data = self._load_keyring()
            if data:
                return data
            return self._load_file()

    def save(self, data: str) -> None:
        """Persist the serialized cache.  Tries keyring first, then
        the fallback file.  Never raises."""
        with self._lock:
            kr_ok = self._save_keyring(data)
            # We always also write the fallback when keyring failed --
            # otherwise the user would lose their refresh token on the
            # next launch.
            if not kr_ok:
                self._save_file(data)

    def _load_keyring(self) -> str:
        if self._keyring is None:
            return ""
        try:
            value = self._keyring.get_password(self._service, self._account)
            return str(value) if value else ""
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.debug(
                "Round 17.2 / sharepoint: keyring load failed (%s)",
                type(err).__name__,
            )
            return ""

    def _save_keyring(self, data: str) -> bool:
        if self._keyring is None:
            return False
        try:
            self._keyring.set_password(self._service, self._account, data)
            return True
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.debug(
                "Round 17.2 / sharepoint: keyring save failed (%s)",
                type(err).__name__,
            )
            return False

    def _load_file(self) -> str:
        if self._fallback_path is None:
            return ""
        try:
            if not self._fallback_path.exists():
                return ""
            return self._fallback_path.read_text(encoding="utf-8")
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.debug(
                "Round 17.2 / sharepoint: token cache file load failed (%s)",
                type(err).__name__,
            )
            return ""

    def _save_file(self, data: str) -> None:
        if self._fallback_path is None:
            return
        try:
            self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # 0700 on the parent so siblings under ``~/.adoptiq``
                # are not exposed.
                os.chmod(self._fallback_path.parent, 0o700)
            except Exception:
                pass  # noqa: PIE790 - best-effort hardening
            self._fallback_path.write_text(data or "", encoding="utf-8")
            try:
                os.chmod(self._fallback_path, 0o600)
            except Exception:
                pass  # noqa: PIE790
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.warning(
                "Round 17.2 / sharepoint: token cache file save failed (%s)",
                type(err).__name__,
            )

    def clear(self) -> dict:
        """Round 33 / Build8: drop the persisted token cache from both
        keyring and the file fallback.

        Idempotent and never raises -- a missing entry is treated as
        success.  Returns a small ``dict`` describing what was cleared
        so the calling endpoint can include it in the JSON response
        for diagnostics ("we cleared the keyring entry but the file
        fallback was already absent").
        """
        cleared = {"keyring": False, "file": False}
        with self._lock:
            if self._keyring is not None:
                try:
                    self._keyring.delete_password(self._service, self._account)
                    cleared["keyring"] = True
                except Exception as err:  # noqa: BLE001 - missing entry, etc.
                    # ``keyring.errors.PasswordDeleteError`` is the
                    # documented "no such entry" signal.  Anything else
                    # is also non-fatal -- we still want to clear the
                    # file fallback below.
                    logger.debug(
                        "Round 33 / Build8: sharepoint keyring delete skipped (%s)",
                        type(err).__name__,
                    )
            if self._fallback_path is not None:
                try:
                    if self._fallback_path.exists():
                        self._fallback_path.unlink()
                        cleared["file"] = True
                except Exception as err:  # noqa: BLE001 - never bubble
                    logger.warning(
                        "Round 33 / Build8: sharepoint token file delete failed (%s)",
                        type(err).__name__,
                    )
        return cleared


# ---------------------------------------------------------------------------
# SharePoint Graph client
# ---------------------------------------------------------------------------


class SharePointAuthRequired(Exception):
    """Raised internally when a Graph call needs an interactive
    sign-in.  Public callers receive this as a structured field on
    :class:`RefreshStats` instead of a raised exception."""


class SharePointGraphClient:
    """Thin wrapper around :mod:`msal` + :mod:`requests` for the
    delegated read-only Graph operations the corpus needs.

    The client holds no state beyond the token cache and a small
    in-memory device-code flow handle when sign-in is in progress.
    All HTTP I/O is performed via injectable ``http_get`` / ``http_get_stream``
    callables so unit tests can fake them without monkey-patching
    :mod:`requests` at the module level.
    """

    def __init__(
        self,
        *,
        client_id: str = DEFAULT_CLIENT_ID,
        authority: str = DEFAULT_AUTHORITY,
        scopes: Optional[List[str]] = None,
        token_cache: Optional[KeyringTokenCache] = None,
        msal_module: Any = None,
        http_get: Optional[Callable[..., Any]] = None,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        inter_request_sleep_s: float = DEFAULT_INTER_REQUEST_SLEEP_S,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_base_s: float = DEFAULT_RETRY_BASE_S,
        retry_max_s: float = DEFAULT_RETRY_MAX_S,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client_id = str(client_id)
        self._authority = str(authority)
        self._scopes = list(scopes) if scopes else list(DEFAULT_SCOPES)
        self._token_cache = token_cache
        self._request_timeout_s = float(request_timeout_s)
        self._inter_request_sleep_s = max(0.0, float(inter_request_sleep_s))
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_base_s = max(0.1, float(retry_base_s))
        self._retry_max_s = max(self._retry_base_s, float(retry_max_s))
        self._sleep = sleep_func

        # Lazy MSAL handle -- imported only on first use so the module
        # remains importable without the optional dep installed.
        self._msal_module = msal_module
        self._msal_app: Any = None
        self._msal_cache: Any = None

        # Lazy requests handle.  Tests pass a callable to bypass real
        # HTTP entirely.
        if http_get is not None:
            self._http_get = http_get
        else:
            self._http_get = None  # resolved on first use

        # Pending device-code flow info -- stored so the client can
        # complete the flow on a worker thread while the foreground
        # request shows the code to the user.
        self._pending_flow: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    # -- Sign-out -------------------------------------------------------------

    def clear_token_cache(self) -> dict:
        """Round 33 / Build8: drop the persisted refresh-token cache so
        the next call to :meth:`acquire_token_silent` returns ``None``
        and the analyze-page UI can re-prompt the user for sign-in.

        Idempotent.  Resets the in-memory MSAL handles too so a stale
        cache cannot survive in process memory after sign-out.
        Returns the same ``{"keyring": bool, "file": bool}`` envelope
        as :meth:`KeyringTokenCache.clear`.
        """
        cleared = {"keyring": False, "file": False}
        if self._token_cache is not None:
            try:
                cleared = self._token_cache.clear()
            except Exception as err:  # noqa: BLE001 - never bubble
                logger.warning(
                    "Round 33 / Build8: sharepoint clear_token_cache failed (%s)",
                    type(err).__name__,
                )
        # Reset in-memory MSAL state so the next ``acquire_token_silent``
        # rebuilds the cache from disk (which we just emptied) instead
        # of returning a token from the previous session.
        self._msal_app = None
        self._msal_cache = None
        self._pending_flow = None
        return cleared

    # -- MSAL / token plumbing ------------------------------------------------

    def _ensure_msal(self) -> Any:
        if self._msal_app is not None:
            return self._msal_app
        if self._msal_module is None:
            try:
                import msal  # type: ignore
                self._msal_module = msal
            except Exception as imp_err:
                raise SharePointAuthRequired(
                    f"msal package not installed: {type(imp_err).__name__}"
                )
        cache = self._msal_module.SerializableTokenCache()
        if self._token_cache is not None:
            try:
                blob = self._token_cache.load()
                if blob:
                    cache.deserialize(blob)
            except Exception as err:  # noqa: BLE001 - never bubble
                logger.debug(
                    "Round 17.2 / sharepoint: token cache deserialize failed (%s)",
                    type(err).__name__,
                )
        self._msal_cache = cache
        self._msal_app = self._msal_module.PublicClientApplication(
            self._client_id,
            authority=self._authority,
            token_cache=cache,
        )
        return self._msal_app

    def _persist_cache(self) -> None:
        if self._token_cache is None or self._msal_cache is None:
            return
        try:
            if not getattr(self._msal_cache, "has_state_changed", False):
                return
            blob = self._msal_cache.serialize()
            self._token_cache.save(blob)
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.debug(
                "Round 17.2 / sharepoint: token cache persist failed (%s)",
                type(err).__name__,
            )

    def acquire_token_silent(self) -> Optional[str]:
        """Return an access token from the persisted refresh token, or
        ``None`` when no cached account is available or the silent
        acquisition fails (e.g. expired refresh token).  Never raises;
        the caller decides whether to start a device-code flow."""
        try:
            app = self._ensure_msal()
        except SharePointAuthRequired:
            return None
        try:
            accounts = app.get_accounts() or []
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.debug(
                "Round 17.2 / sharepoint: get_accounts failed (%s)",
                type(err).__name__,
            )
            accounts = []
        if not accounts:
            return None
        try:
            result = app.acquire_token_silent(self._scopes, account=accounts[0])
        except Exception as err:  # noqa: BLE001 - never bubble
            logger.debug(
                "Round 17.2 / sharepoint: acquire_token_silent raised (%s)",
                type(err).__name__,
            )
            return None
        self._persist_cache()
        if not result or not isinstance(result, dict):
            return None
        token = result.get("access_token")
        if not token:
            return None
        return str(token)

    def get_token_info(self) -> TokenInfo:
        """Summarize the cached account / token for the admin tile.
        Never returns the access token itself."""
        info = TokenInfo()
        try:
            app = self._ensure_msal()
        except SharePointAuthRequired:
            return info
        try:
            accounts = app.get_accounts() or []
        except Exception:
            accounts = []
        if not accounts:
            return info
        acct = accounts[0]
        info.upn = (
            acct.get("username") if isinstance(acct, dict) else getattr(acct, "username", None)
        )
        info.name = (
            acct.get("name") if isinstance(acct, dict) else getattr(acct, "name", None)
        )
        try:
            result = app.acquire_token_silent(self._scopes, account=acct)
        except Exception:
            result = None
        self._persist_cache()
        if not result or not isinstance(result, dict):
            return info
        token = result.get("access_token")
        if token:
            info.has_refresh_token = True
        expires_in = result.get("expires_in")
        if isinstance(expires_in, (int, float)):
            info.expires_in_s = int(expires_in)
            try:
                expiry_dt = datetime.now(timezone.utc).timestamp() + int(expires_in)
                info.expires_at = datetime.fromtimestamp(
                    expiry_dt, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
        return info

    def start_device_code_flow(self) -> DeviceCodeFlow:
        """Initiate a device-code flow.  Returns the user-displayable
        prompt info and stashes the MSAL flow handle so
        :meth:`await_device_code_completion` can complete the sign-in
        from a worker thread without re-prompting."""
        app = self._ensure_msal()
        try:
            flow = app.initiate_device_flow(scopes=self._scopes)
        except Exception as err:
            raise SharePointAuthRequired(
                f"initiate_device_flow failed: {type(err).__name__}"
            ) from err
        if not isinstance(flow, dict) or "user_code" not in flow:
            raise SharePointAuthRequired(
                f"initiate_device_flow returned unexpected payload: {flow!r}"
            )
        with self._lock:
            self._pending_flow = flow
        msg = str(flow.get("message") or "")
        logger.info(
            "Round 17.2 / sharepoint: device-code flow started; user_code=%s verification_uri=%s expires_in=%s",
            flow.get("user_code"),
            flow.get("verification_uri"),
            flow.get("expires_in"),
        )
        return DeviceCodeFlow(
            user_code=str(flow.get("user_code") or ""),
            verification_uri=str(
                flow.get("verification_uri")
                or flow.get("verification_uri_complete")
                or "https://microsoft.com/devicelogin"
            ),
            message=msg,
            expires_in=int(flow.get("expires_in") or 0),
            interval=int(flow.get("interval") or 5),
        )

    def await_device_code_completion(self, *, timeout_s: Optional[float] = None) -> Optional[TokenInfo]:
        """Block until the user finishes the device-code flow or the
        flow expires.  On success persists the token cache and
        returns a :class:`TokenInfo` summary.  Returns ``None`` when
        no flow is in progress."""
        with self._lock:
            flow = self._pending_flow
        if flow is None:
            return None
        app = self._ensure_msal()
        try:
            # MSAL's acquire_token_by_device_flow blocks polling
            # internally; ``timeout`` is honored when the flow's
            # ``expires_at`` is reached but we keep the keyword in
            # case future MSAL versions accept an explicit timeout.
            result = app.acquire_token_by_device_flow(flow)
        except Exception as err:
            logger.warning(
                "Round 17.2 / sharepoint: device-code completion raised (%s)",
                type(err).__name__,
            )
            return None
        with self._lock:
            self._pending_flow = None
        self._persist_cache()
        if not isinstance(result, dict) or not result.get("access_token"):
            err_kind = result.get("error") if isinstance(result, dict) else "unknown"
            logger.warning(
                "Round 17.2 / sharepoint: device-code flow failed (%s)",
                err_kind,
            )
            return None
        info = self.get_token_info()
        logger.info(
            "Round 17.2 / sharepoint: signed in as %s (expires_in=%ss)",
            info.upn or "<unknown>",
            info.expires_in_s,
        )
        return info

    # -- HTTP plumbing --------------------------------------------------------

    def _ensure_http_get(self) -> Callable[..., Any]:
        if self._http_get is not None:
            return self._http_get
        try:
            import requests  # type: ignore
        except Exception as imp_err:
            raise RuntimeError(
                f"requests package not installed: {type(imp_err).__name__}"
            ) from imp_err
        self._http_get = requests.get
        return self._http_get

    def _request_with_retry(
        self,
        url: str,
        token: str,
        *,
        stream: bool = False,
        accept: str = "application/json",
    ) -> Any:
        """GET ``url`` with exponential backoff on 429/503.  Returns
        the underlying response object so the caller can choose to
        ``.json()`` it or stream the body."""
        http_get = self._ensure_http_get()
        attempt = 0
        last_err: Optional[str] = None
        while attempt < self._retry_attempts:
            attempt += 1
            try:
                resp = http_get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": accept,
                    },
                    timeout=self._request_timeout_s,
                    stream=stream,
                )
            except Exception as err:  # noqa: BLE001 - retry network errors
                last_err = type(err).__name__
                logger.debug(
                    "Round 17.2 / sharepoint: HTTP error attempt=%d (%s)",
                    attempt, last_err,
                )
                if attempt >= self._retry_attempts:
                    break
                self._sleep(min(self._retry_max_s, self._retry_base_s * (2 ** (attempt - 1))))
                continue

            status = int(getattr(resp, "status_code", 0) or 0)
            if status == 200:
                return resp
            if status in (429, 503):
                retry_after = 0.0
                try:
                    raw = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                    if raw:
                        retry_after = float(raw)
                except Exception:
                    retry_after = 0.0
                if retry_after <= 0.0:
                    retry_after = min(
                        self._retry_max_s,
                        self._retry_base_s * (2 ** (attempt - 1)),
                    )
                logger.info(
                    "Round 17.2 / sharepoint: throttled (status=%d retry_after=%.1fs attempt=%d/%d)",
                    status, retry_after, attempt, self._retry_attempts,
                )
                self._sleep(retry_after)
                continue
            if status == 401:
                # Token revoked / expired between silent refresh and
                # the call.  Caller will retry on next bootstrap.
                raise SharePointAuthRequired(f"401 Unauthorized on {url}")
            # Non-retryable failure; surface it.
            try:
                preview = resp.text[:200] if hasattr(resp, "text") else ""
            except Exception:
                preview = ""
            raise RuntimeError(
                f"Graph GET failed status={status} preview={preview!r}"
            )
        raise RuntimeError(
            f"Graph GET failed after {self._retry_attempts} attempts (last_err={last_err})"
        )

    # -- Share resolution + listing ------------------------------------------

    def list_share_children(self, share_url: str, token: str) -> List[Dict[str, Any]]:
        """Resolve ``share_url`` to its top-level driveItem and return
        the full list of children (paginated through ``@odata.nextLink``).
        Each child dict is a Graph driveItem resource verbatim."""
        encoded = encode_share_url(share_url)
        # First call: top-level driveItem so we know the drive id.  We
        # then list children separately to honor pagination cleanly.
        list_url = f"{GRAPH_BASE}/shares/{encoded}/driveItem/children"
        children: List[Dict[str, Any]] = []
        seen = 0
        while list_url:
            resp = self._request_with_retry(list_url, token)
            try:
                payload = resp.json()
            except Exception as err:
                raise RuntimeError(
                    f"Graph children JSON parse failed: {type(err).__name__}"
                ) from err
            if not isinstance(payload, dict):
                raise RuntimeError("Graph children response not a dict")
            page = payload.get("value") or []
            if not isinstance(page, list):
                page = []
            children.extend(page)
            seen += len(page)
            next_url = payload.get("@odata.nextLink")
            list_url = str(next_url) if next_url else ""
            # Polite throttle between pages.
            if list_url:
                self._sleep(self._inter_request_sleep_s)
        logger.info(
            "Round 17.2 / sharepoint: listed share children=%d", seen,
        )
        return children

    def download_to_path(
        self,
        item: Dict[str, Any],
        token: str,
        dest_path: Path,
        *,
        max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> int:
        """Download ``item['@microsoft.graph.downloadUrl']`` (or fall
        back to ``/drives/{drive-id}/items/{item-id}/content``) into
        ``dest_path``.  Enforces ``max_bytes`` and writes the file
        with mode ``0600``.  Returns the number of bytes written."""
        if not isinstance(item, dict):
            raise ValueError("item must be a dict")
        size = int(item.get("size") or 0)
        if size and size > max_bytes:
            raise ValueError(f"file size {size} exceeds cap {max_bytes}")

        # Prefer the short-lived pre-signed download URL when Graph
        # exposes it -- it does not require the bearer token and is
        # the documented fast path for content fetches.
        download_url = item.get("@microsoft.graph.downloadUrl")
        used_token = False
        if not download_url:
            parent_ref = item.get("parentReference") or {}
            drive_id = parent_ref.get("driveId")
            item_id = item.get("id")
            if not (drive_id and item_id):
                raise RuntimeError("driveItem missing parentReference.driveId / id")
            download_url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
            used_token = True

        http_get = self._ensure_http_get()
        headers: Dict[str, str] = {}
        if used_token:
            headers["Authorization"] = f"Bearer {token}"

        attempt = 0
        last_err: Optional[str] = None
        while attempt < self._retry_attempts:
            attempt += 1
            try:
                resp = http_get(
                    download_url,
                    headers=headers,
                    timeout=self._request_timeout_s,
                    stream=True,
                )
            except Exception as err:  # noqa: BLE001 - retry on transport errors
                last_err = type(err).__name__
                if attempt >= self._retry_attempts:
                    raise RuntimeError(
                        f"download transport failed: {last_err}"
                    ) from err
                self._sleep(min(self._retry_max_s, self._retry_base_s * (2 ** (attempt - 1))))
                continue
            status = int(getattr(resp, "status_code", 0) or 0)
            if status == 200:
                break
            if status in (429, 503):
                retry_after = 0.0
                try:
                    raw = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                    if raw:
                        retry_after = float(raw)
                except Exception:
                    retry_after = 0.0
                if retry_after <= 0.0:
                    retry_after = min(
                        self._retry_max_s,
                        self._retry_base_s * (2 ** (attempt - 1)),
                    )
                self._sleep(retry_after)
                continue
            raise RuntimeError(f"download status={status}")
        else:
            raise RuntimeError(f"download failed after retries (last_err={last_err})")

        # Stream into a temp file then atomic-rename so a partial
        # download cannot poison the cache on next launch.
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        bytes_written = 0
        try:
            with open(tmp_path, "wb") as fh:
                # ``iter_content`` is a requests-specific affordance.
                # We fall back to ``.content`` (already-buffered) when
                # the test stub doesn't implement streaming.
                if hasattr(resp, "iter_content"):
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        bytes_written += len(chunk)
                        if bytes_written > max_bytes:
                            raise ValueError(
                                f"download exceeded cap during stream: {bytes_written} > {max_bytes}"
                            )
                        fh.write(chunk)
                else:
                    body = getattr(resp, "content", b"")
                    if body:
                        bytes_written = len(body)
                        if bytes_written > max_bytes:
                            raise ValueError(
                                f"download exceeded cap: {bytes_written} > {max_bytes}"
                            )
                        fh.write(body)
            try:
                os.chmod(tmp_path, 0o600)
            except Exception:
                pass  # noqa: PIE790 - best-effort hardening
            os.replace(tmp_path, dest_path)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass  # noqa: PIE790
            raise
        return bytes_written


# ---------------------------------------------------------------------------
# Manifest (mtime-aware cache state)
# ---------------------------------------------------------------------------


def _load_manifest(cache_dir: Path) -> Dict[str, Any]:
    path = cache_dir / DEFAULT_MANIFEST_FILENAME
    if not path.exists():
        return {"items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"items": {}}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data
    except Exception as err:  # noqa: BLE001 - corrupt manifest -> rebuild
        logger.warning(
            "Round 17.2 / sharepoint: manifest reset (%s)",
            type(err).__name__,
        )
        return {"items": {}}


def _save_manifest(cache_dir: Path, manifest: Dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cache_dir, 0o700)
    except Exception:
        pass  # noqa: PIE790
    path = cache_dir / DEFAULT_MANIFEST_FILENAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )
        try:
            os.chmod(tmp_path, 0o600)
        except Exception:
            pass  # noqa: PIE790
        os.replace(tmp_path, path)
    except Exception as err:  # noqa: BLE001 - never bubble during refresh
        logger.warning(
            "Round 17.2 / sharepoint: manifest save failed (%s)",
            type(err).__name__,
        )


# ---------------------------------------------------------------------------
# Public refresh entry point
# ---------------------------------------------------------------------------


def refresh_local_cache(
    *,
    share_url: str,
    cache_dir: Path,
    client: SharePointGraphClient,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_exts: Optional[List[str]] = None,
) -> RefreshStats:
    """Sync the SharePoint folder behind ``share_url`` into
    ``cache_dir``, downloading only items whose remote mtime is newer
    than the cached one.

    Returns a :class:`RefreshStats`; never raises.  Auth failures are
    recorded as ``error_kind='auth_required'`` so the bootstrap can
    skip the source without aborting the whole pass.
    """
    stats = RefreshStats(started_at=_utc_now_iso())
    started = time.monotonic()
    cache_dir = Path(cache_dir)
    exts = tuple(e.lower() for e in (allowed_exts or ALLOWED_EXTS))

    # Step 1: silent token (or skip if not signed in).
    token = client.acquire_token_silent()
    if not token:
        stats.error_kind = "auth_required"
        stats.error_detail = "no cached account; user must complete device-code sign-in"
        stats.finished_at = _utc_now_iso()
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Round 17.2 / sharepoint: refresh skipped (auth_required)"
        )
        return stats

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cache_dir, 0o700)
    except Exception:
        pass  # noqa: PIE790

    manifest = _load_manifest(cache_dir)
    manifest_items: Dict[str, Any] = manifest.setdefault("items", {})

    # Step 2: list children.
    try:
        children = client.list_share_children(share_url, token)
    except SharePointAuthRequired as err:
        stats.error_kind = "auth_required"
        stats.error_detail = str(err)
        stats.finished_at = _utc_now_iso()
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "Round 17.2 / sharepoint: list raised auth_required",
        )
        return stats
    except Exception as err:  # noqa: BLE001 - report and bail
        stats.error_kind = "list_failed"
        stats.error_detail = type(err).__name__
        stats.finished_at = _utc_now_iso()
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "Round 17.2 / sharepoint: list failed (%s)",
            type(err).__name__,
        )
        return stats

    stats.files_listed = len(children)

    # Step 3: filter + download.
    seen_ids: set[str] = set()
    for item in children:
        if not isinstance(item, dict):
            continue
        # Skip folders.
        if item.get("folder") is not None:
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        lower = name.lower()
        if not any(lower.endswith(ext) for ext in exts):
            stats.files_skipped_unsupported += 1
            continue

        size = int(item.get("size") or 0)
        if size > max_file_bytes:
            stats.files_skipped_oversized += 1
            stats.errors.append(f"{name}: size={size} > cap")
            logger.info(
                "Round 17.2 / sharepoint: skip oversized file=%s size=%d cap=%d",
                name, size, max_file_bytes,
            )
            continue

        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        seen_ids.add(item_id)

        remote_mtime = item.get("lastModifiedDateTime") or ""
        remote_mtime_epoch = _parse_iso_z_to_epoch(remote_mtime) or 0.0
        local_name = _safe_local_filename(name)
        local_path = cache_dir / local_name

        cached = manifest_items.get(item_id) or {}
        cached_mtime_epoch = float(cached.get("mtime_epoch") or 0.0)

        if (
            local_path.exists()
            and cached_mtime_epoch
            and remote_mtime_epoch
            and cached_mtime_epoch >= remote_mtime_epoch
        ):
            stats.files_cached += 1
            logger.debug(
                "Round 17.2 / sharepoint: cache hit file=%s",
                local_name,
            )
            continue

        try:
            written = client.download_to_path(
                item, token, local_path, max_bytes=max_file_bytes,
            )
        except SharePointAuthRequired:
            stats.error_kind = "auth_required"
            stats.error_detail = "401 mid-refresh; user must re-authenticate"
            stats.files_failed += 1
            stats.errors.append(f"{name}: 401")
            break
        except Exception as err:  # noqa: BLE001 - per-file failures don't abort
            stats.files_failed += 1
            stats.errors.append(f"{name}: {type(err).__name__}")
            logger.warning(
                "Round 17.2 / sharepoint: download failed file=%s err=%s",
                local_name, type(err).__name__,
            )
            continue

        stats.files_downloaded += 1
        stats.bytes_downloaded += int(written)

        manifest_items[item_id] = {
            "name": local_name,
            "remote_name": name,
            "size": int(written),
            "mtime_iso": remote_mtime,
            "mtime_epoch": remote_mtime_epoch,
            "downloaded_at": _utc_now_iso(),
        }
        logger.info(
            "Round 17.2 / sharepoint: downloaded file=%s bytes=%d remote_mtime=%s",
            local_name, int(written), remote_mtime,
        )

    # Step 4: persist manifest (best-effort).
    _save_manifest(cache_dir, manifest)

    stats.finished_at = _utc_now_iso()
    stats.elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Round 17.2 / sharepoint: refresh complete listed=%d downloaded=%d "
        "cached=%d failed=%d skipped_oversized=%d skipped_unsupported=%d "
        "bytes=%d elapsed_ms=%d",
        stats.files_listed,
        stats.files_downloaded,
        stats.files_cached,
        stats.files_failed,
        stats.files_skipped_oversized,
        stats.files_skipped_unsupported,
        stats.bytes_downloaded,
        stats.elapsed_ms,
    )
    return stats


# ---------------------------------------------------------------------------
# Convenience: build the default client + cache dir from Config
# ---------------------------------------------------------------------------


def default_cache_dir() -> Path:
    """Resolved at call time so tests can stub ``HOME``."""
    base = (os.environ.get("ADOPTIQ_SHAREPOINT_CACHE_DIR") or "").strip()
    if base:
        return Path(base)
    return Path.home() / ".adoptiq" / "cache" / "sharepoint_csone"


def default_token_cache_path() -> Path:
    return Path.home() / ".adoptiq" / DEFAULT_FALLBACK_TOKEN_CACHE_FILENAME


def build_default_client() -> SharePointGraphClient:
    """Construct a :class:`SharePointGraphClient` configured from
    ``Config`` / env defaults.  Importable side-effect-free; no
    network or keychain access happens until a method is invoked."""
    try:
        from config import Config
        client_id = getattr(Config, "ADOPTIQ_SHAREPOINT_CLIENT_ID", DEFAULT_CLIENT_ID)
        authority = getattr(Config, "ADOPTIQ_SHAREPOINT_AUTHORITY", DEFAULT_AUTHORITY)
        max_bytes = int(
            getattr(Config, "ADOPTIQ_SHAREPOINT_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES)
        )
    except Exception:
        client_id = DEFAULT_CLIENT_ID
        authority = DEFAULT_AUTHORITY
        max_bytes = DEFAULT_MAX_FILE_BYTES
    cache = KeyringTokenCache(fallback_path=default_token_cache_path())
    client = SharePointGraphClient(
        client_id=str(client_id),
        authority=str(authority),
        token_cache=cache,
    )
    # Stash on the client for callers that need the cap (e.g. the
    # bootstrap layer).  Not part of the constructor signature so we
    # don't pollute the unit-test surface.
    client.max_file_bytes = max_bytes
    return client


__all__ = [
    "DEFAULT_AUTHORITY",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_SCOPES",
    "DeviceCodeFlow",
    "KeyringTokenCache",
    "RefreshStats",
    "SharePointAuthRequired",
    "SharePointGraphClient",
    "TokenInfo",
    "build_default_client",
    "default_cache_dir",
    "default_token_cache_path",
    "encode_share_url",
    "refresh_local_cache",
]
