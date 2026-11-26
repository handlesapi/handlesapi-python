"""Synchronous and asynchronous clients."""
from __future__ import annotations

import asyncio
import os
import time
from types import TracebackType
from typing import Any, AsyncIterator, Callable, Dict, Iterator, Mapping, Optional, Type, TypeVar

import httpx

from ._core import (
    DEFAULT_BASE_URL,
    Response,
    build_request,
    default_headers,
    next_cursor,
    is_retryable,
    parse_response,
    retry_delay,
)
from ._methods import AsyncOperations, SyncOperations
from .errors import HandlesApiConnectionError, HandlesApiError

T = TypeVar("T")


def _resolve_key(api_key: Optional[str]) -> str:
    key = api_key or os.environ.get("HANDLES_API_KEY")
    if not key:
        raise ValueError("Handles API key missing: pass api_key=... or set the HANDLES_API_KEY environment variable.")
    return key


class _Config:
    def __init__(
        self,
        api_key: Optional[str],
        base_url: str,
        max_retries: int,
        timeout: float,
        max_retry_after: float,
        headers: Optional[Mapping[str, str]],
    ) -> None:
        self.api_key = _resolve_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_retry_after = max_retry_after
        self.headers = default_headers(self.api_key, headers)

    def should_retry(self, err: "HandlesApiError | HandlesApiConnectionError", attempt: int) -> bool:
        if attempt >= self.max_retries or not is_retryable(err):
            return False
        if isinstance(err, HandlesApiError) and err.retry_after is not None and err.retry_after > self.max_retry_after:
            return False
        return True


def _connection_error(exc: Exception, timeout: float) -> HandlesApiConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return HandlesApiConnectionError(f"Request timed out after {timeout} s", timed_out=True)
    return HandlesApiConnectionError(f"Network error: {exc}")


class HandlesApi(SyncOperations):
    """Client for the Handles API (https://handlesapi.com/docs).

    There is one method per endpoint, named after its ``operationId`` in snake_case:
    ``GET /accounts/{platform}/{handle}`` is ``api.accounts_check("youtube", "mrbeast")``.
    Path, query and body parameters are all plain arguments. Every method returns a
    :class:`Response` with ``data``, ``meta``, ``credits``, ``rate_limit`` and ``request_id``.

    Args:
        api_key: Your key (``hk_live_...``). Defaults to the ``HANDLES_API_KEY`` environment variable.
        base_url: Defaults to ``https://api.handlesapi.com/v1``.
        max_retries: Retries after a 429, a 5xx or a network failure. Default 2; 0 disables.
        timeout: Per-attempt timeout in seconds. Default 60: live reads can take ten seconds or more.
        max_retry_after: Longest ``Retry-After`` the client sleeps through; a longer wait raises. Default 30.
        headers: Extra headers for every request.
        http_client: Your own ``httpx.Client`` (proxies, transport, mounts).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = 2,
        timeout: float = 60.0,
        max_retry_after: float = 30.0,
        headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._cfg = _Config(api_key, base_url, max_retries, timeout, max_retry_after, headers)
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client()

    def __enter__(self) -> "HandlesApi":
        return self

    def __exit__(
        self, exc_type: Optional[Type[BaseException]], exc: Optional[BaseException], tb: Optional[TracebackType]
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> Response[Any]:
        """Calls any path below the base URL, for example ``api.request("GET", "/accounts/platforms")``."""
        q = {k: str(v) for k, v in (query or {}).items() if v is not None}
        return self._send(method, path, q, body, timeout)

    def _op(self, name: str, args: Mapping[str, Any], timeout: Optional[float] = None) -> Any:
        op, path, query, body = build_request(name, args)
        return self._send(op.method, path, query, body, timeout)

    def _send(
        self, method: str, path: str, query: Dict[str, str], body: Optional[Any], timeout: Optional[float]
    ) -> Response[Any]:
        cfg = self._cfg
        wait = timeout if timeout is not None else cfg.timeout
        attempt = 0
        while True:
            err: "HandlesApiError | HandlesApiConnectionError"
            try:
                res = self._http.request(
                    method, cfg.base_url + path, params=query or None, json=body, headers=cfg.headers, timeout=wait
                )
            except httpx.HTTPError as exc:
                err = _connection_error(exc, wait)
            else:
                parsed = parse_response(res.status_code, res.headers, res.text)
                if isinstance(parsed, Response):
                    return parsed
                err = parsed
            if not cfg.should_retry(err, attempt):
                raise err
            time.sleep(retry_delay(err, attempt))
            attempt += 1

    def paginate(
        self, method: Callable[..., Response[Any]], *, max_pages: Optional[int] = None, **kwargs: Any
    ) -> Iterator[Response[Any]]:
        """Follows ``meta.page.next_cursor``, yielding one :class:`Response` per page.

        Stops when the cursor is ``None``, the only reliable end of a result set, or after ``max_pages``::

            for page in api.paginate(api.x_users_tweets, handle="nasa", limit=100, max_pages=3):
                print(len(page.data.get("tweets") or []), page.credits.remaining)
        """
        cursor = kwargs.pop("cursor", None)
        pages = 0
        while max_pages is None or pages < max_pages:
            page = method(cursor=cursor, **kwargs)
            yield page
            pages += 1
            cursor = next_cursor(page)
            if not cursor:
                return


class AsyncHandlesApi(AsyncOperations):
    """Asynchronous twin of :class:`HandlesApi`: the same methods, awaited, on ``httpx.AsyncClient``."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = 2,
        timeout: float = 60.0,
        max_retry_after: float = 30.0,
        headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._cfg = _Config(api_key, base_url, max_retries, timeout, max_retry_after, headers)
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient()

    async def __aenter__(self) -> "AsyncHandlesApi":
        return self

    async def __aexit__(
        self, exc_type: Optional[Type[BaseException]], exc: Optional[BaseException], tb: Optional[TracebackType]
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> Response[Any]:
        """Calls any path below the base URL."""
        q = {k: str(v) for k, v in (query or {}).items() if v is not None}
        return await self._send(method, path, q, body, timeout)

    async def _op(self, name: str, args: Mapping[str, Any], timeout: Optional[float] = None) -> Any:
        op, path, query, body = build_request(name, args)
        return await self._send(op.method, path, query, body, timeout)

    async def _send(
        self, method: str, path: str, query: Dict[str, str], body: Optional[Any], timeout: Optional[float]
    ) -> Response[Any]:
        cfg = self._cfg
        wait = timeout if timeout is not None else cfg.timeout
        attempt = 0
        while True:
            err: "HandlesApiError | HandlesApiConnectionError"
            try:
                res = await self._http.request(
                    method, cfg.base_url + path, params=query or None, json=body, headers=cfg.headers, timeout=wait
                )
            except httpx.HTTPError as exc:
                err = _connection_error(exc, wait)
            else:
                parsed = parse_response(res.status_code, res.headers, res.text)
                if isinstance(parsed, Response):
                    return parsed
                err = parsed
            if not cfg.should_retry(err, attempt):
                raise err
            await asyncio.sleep(retry_delay(err, attempt))
            attempt += 1

    async def paginate(
        self, method: Callable[..., Any], *, max_pages: Optional[int] = None, **kwargs: Any
    ) -> AsyncIterator[Response[Any]]:
        """Async version of :meth:`HandlesApi.paginate`: ``async for page in api.paginate(api.x_users_tweets, ...)``."""
        cursor = kwargs.pop("cursor", None)
        pages = 0
        while max_pages is None or pages < max_pages:
            page: Response[Any] = await method(cursor=cursor, **kwargs)
            yield page
            pages += 1
            cursor = next_cursor(page)
            if not cursor:
                return
