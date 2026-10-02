"""Stable error taxonomy for the Sponsored Runtime boundary.

The runtime returns machine-readable ``error.code`` values. Keeping the
mapping in one place lets the provider decide whether a failure may fall back
to the user's own provider (BYOK / Ollama) without pattern-matching strings.

Client-side contract mistakes (``CONTRACT_MISMATCH``) must never fall back:
they mean the shipped prompt and the signed policy disagree, and silently
spending the user's own model budget would hide a release bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


class SponsoredError(Exception):
    """Base class for every Sponsored Runtime failure."""

    code = "SPONSORED_ERROR"
    fallback_allowed = True

    def __init__(self, message: str = "", *, task_id: str = "") -> None:
        super().__init__(message or self.code)
        self.task_id = task_id
        self.actual_code = self.code


class SponsoredUnavailableError(SponsoredError):
    """Runtime binary missing, crashed, timed out, or protocol-incompatible."""

    code = "UNAVAILABLE"


class SponsoredUnsupportedTaskError(SponsoredError):
    """The task/version is not present in the signed policy."""

    code = "UNSUPPORTED_TASK"


class SponsoredContractMismatchError(SponsoredError):
    """Prompt hash, topology, or version disagrees with the signed policy.

    Deliberately not fallback-allowed: this is a shipped-core bug, not an
    availability problem.
    """

    code = "CONTRACT_MISMATCH"
    fallback_allowed = False


class SponsoredRequestTooLargeError(SponsoredError):
    """The serialized request exceeds the contract's local size budget."""

    code = "REQUEST_TOO_LARGE"


class SponsoredQuotaExceededError(SponsoredError):
    """Local per-install budget (or account balance) is exhausted."""

    code = "QUOTA_EXCEEDED"


class SponsoredRateLimitedError(SponsoredError):
    """Local sliding-window limit or upstream HTTP 429."""

    code = "RATE_LIMITED"


class SponsoredAuthError(SponsoredError):
    """Sponsored key invalid, deleted, or account locked."""

    code = "AUTH_FAILED"


class SponsoredBalanceLowError(SponsoredError):
    """Account balance is below the policy floor."""

    code = "BALANCE_LOW"


class SponsoredLedgerTamperedError(SponsoredError):
    """Local quota ledger failed integrity checks."""

    code = "LEDGER_TAMPERED"


class SponsoredUpstreamError(SponsoredError):
    """SiliconFlow transport/5xx/failure after bounded retries."""

    code = "UPSTREAM_ERROR"


class SponsoredProtocolError(SponsoredError):
    """Malformed IPC frame or unexpected runtime response."""

    code = "PROTOCOL_ERROR"


_ERROR_TYPES: dict[str, type[SponsoredError]] = {
    cls.code: cls
    for cls in (
        SponsoredUnavailableError,
        SponsoredUnsupportedTaskError,
        SponsoredContractMismatchError,
        SponsoredRequestTooLargeError,
        SponsoredQuotaExceededError,
        SponsoredRateLimitedError,
        SponsoredAuthError,
        SponsoredBalanceLowError,
        SponsoredLedgerTamperedError,
        SponsoredUpstreamError,
        SponsoredProtocolError,
    )
}


def error_from_payload(payload: Mapping[str, Any], *, task_id: str = "") -> SponsoredError:
    """Build the typed exception for a runtime ``error`` object."""

    code = str(payload.get("code") or SponsoredUpstreamError.code).strip().upper()
    message = str(payload.get("message") or "").strip()
    error_type = _ERROR_TYPES.get(code)
    if error_type is None:
        error = SponsoredUpstreamError(message or f"upstream error ({code})", task_id=task_id)
        error.actual_code = code
        return error
    return error_type(message, task_id=task_id)


def is_fallback_allowed(error: BaseException) -> bool:
    """Whether this failure may be retried on the user's own provider."""

    return isinstance(error, SponsoredError) and error.fallback_allowed
