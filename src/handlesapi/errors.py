"""Exceptions raised by the Handles API client."""
from __future__ import annotations

from typing import Dict, Mapping, Optional

RETRYABLE_CODES = frozenset(
    {
        "rate_limited",
        "live_limit_reached",
        "platform_unavailable",
        "upstream_timeout",
        "upstream_error",
        "service_unavailable",
        "internal_error",
    }
)


class HandlesApiError(Exception):
    """A non-2xx answer from the API. Branch on ``code``, never on the message.

    Attributes:
        status: HTTP status code.
        code: Stable machine-readable code from ``error.code`` (``"unknown"`` when the body was not JSON).
        message: Human-readable explanation. Prose, not a contract.
        request_id: Quote this to support: it identifies your exact call.
        details: Field-level detail on ``invalid_request``, parameter name to reason.
        retry_after: Seconds to wait, from the ``Retry-After`` header, when the API sent one.
        headers: Response headers.
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, str]] = None,
        retry_after: Optional[float] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(f"{code} (HTTP {status}): {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.details = details
        self.retry_after = retry_after
        self.headers: Mapping[str, str] = headers or {}

    @property
    def retryable(self) -> bool:
        """True for codes where waiting and retrying can succeed. ``quota_exceeded`` is not one of them."""
        return self.code in RETRYABLE_CODES or (self.code == "unknown" and self.status >= 500)


class HandlesApiConnectionError(Exception):
    """The request never produced an HTTP answer: DNS, TLS, a reset, or the client's own timeout."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out
