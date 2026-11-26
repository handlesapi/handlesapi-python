"""Transport-independent pieces: response model, request building, parsing and retry policy."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Generic, List, Mapping, Optional, Tuple, TypeVar, Union
from urllib.parse import quote

from ._operations import OPERATIONS, Operation
from ._types import ApiMeta
from .errors import HandlesApiConnectionError, HandlesApiError

VERSION = "0.1.0"
DEFAULT_BASE_URL = "https://api.handlesapi.com/v1"

T = TypeVar("T")


@dataclass(frozen=True)
class Credits:
    """What the API reports in ``X-Credits-*``. Fields are None when a header is missing."""

    cost: Optional[int]
    """Credits this call used."""
    remaining: Optional[int]
    """Plan credits left in the current period."""
    period_end: Optional[str]
    """When the current period ends, ISO 8601."""


@dataclass(frozen=True)
class RateLimit:
    """``X-RateLimit-*``: the per-minute window."""

    limit: Optional[int]
    remaining: Optional[int]
    reset: Optional[int]
    """Unix seconds at which the window resets."""


@dataclass(frozen=True)
class Response(Generic[T]):
    """A successful ``{ data, meta }`` answer plus the credit and rate-limit headers."""

    data: T
    meta: ApiMeta
    credits: Credits
    rate_limit: RateLimit
    request_id: Optional[str]
    headers: Mapping[str, str]


def _int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """``Retry-After`` is delta-seconds or an HTTP date. Returns seconds."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return max(0.0, (at - (now or datetime.now(timezone.utc))).total_seconds())


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_request(
    name: str, args: Mapping[str, Any]
) -> Tuple[Operation, str, Dict[str, str], Optional[Dict[str, Any]]]:
    """Maps one operation plus keyword arguments to (operation, path, query, body)."""
    op = OPERATIONS[name]
    path = op.path
    for key in op.path_params:
        value = args.get(key)
        if value is None or value == "":
            raise TypeError(f"{name}() missing path parameter {key!r}")
        path = path.replace("{" + key + "}", quote(_stringify(value), safe=""))
    query: Dict[str, str] = {}
    for key in op.query_params:
        value = args.get(key)
        if value is None:
            continue
        query[key] = ",".join(_stringify(v) for v in value) if isinstance(value, (list, tuple)) else _stringify(value)
    body: Optional[Dict[str, Any]] = None
    if op.body_params:
        body = {k: args[k] for k in op.body_params if args.get(k) is not None}
    elif op.method == "POST":
        body = {}
    return op, path, query, body


def parse_response(status: int, headers: Mapping[str, str], text: str) -> Union[Response[Any], HandlesApiError]:
    """Turns an HTTP answer into a Response, or the HandlesApiError to raise."""
    low = {k.lower(): v for k, v in headers.items()}
    payload: Any = None
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    if 200 <= status < 300 and isinstance(payload, dict) and "data" in payload:
        meta: ApiMeta = payload.get("meta") or {}  # type: ignore[assignment]
        return Response(
            data=payload["data"],
            meta=meta,
            credits=Credits(
                cost=_int(low.get("x-credits-cost")),
                remaining=_int(low.get("x-credits-remaining")),
                period_end=low.get("x-credits-period-end"),
            ),
            rate_limit=RateLimit(
                limit=_int(low.get("x-ratelimit-limit")),
                remaining=_int(low.get("x-ratelimit-remaining")),
                reset=_int(low.get("x-ratelimit-reset")),
            ),
            request_id=meta.get("request_id") or low.get("x-request-id"),
            headers=low,
        )
    err = payload.get("error") if isinstance(payload, dict) else None
    err = err if isinstance(err, dict) else {}
    fallback = "Response was not a { data, meta } envelope" if 200 <= status < 300 else (text[:200] or f"HTTP {status}")
    return HandlesApiError(
        status=status,
        code=str(err.get("code") or "unknown"),
        message=str(err.get("message") or fallback),
        request_id=err.get("request_id") or low.get("x-request-id"),
        details=err.get("details"),
        retry_after=parse_retry_after(low.get("retry-after")),
        headers=low,
    )


def next_cursor(page: "Response[Any]") -> Optional[str]:
    """``meta.page.next_cursor``, or None when the result set is exhausted."""
    info = page.meta.get("page")
    return info.get("next_cursor") if info else None


def is_retryable(err: Union[HandlesApiError, HandlesApiConnectionError]) -> bool:
    return True if isinstance(err, HandlesApiConnectionError) else err.retryable


def retry_delay(err: Union[HandlesApiError, HandlesApiConnectionError], attempt: int) -> float:
    """429 honours Retry-After; everything else backs off exponentially with jitter (0.5 s, 1 s, 2 s... capped at 8 s)."""
    if isinstance(err, HandlesApiError) and err.retry_after is not None:
        return err.retry_after
    base = min(8.0, 0.5 * (2.0**attempt))
    return base / 2 + random.random() * (base / 2)


def default_headers(api_key: str, extra: Optional[Mapping[str, str]]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"handlesapi-python/{VERSION}",
    }
    if extra:
        headers.update(extra)
    return headers


__all__: List[str] = [
    "Credits",
    "DEFAULT_BASE_URL",
    "RateLimit",
    "Response",
    "VERSION",
    "build_request",
    "next_cursor",
    "parse_response",
    "parse_retry_after",
    "retry_delay",
]
