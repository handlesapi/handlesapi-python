import asyncio
import json
from typing import Any, Callable, List

import httpx
import pytest

import handlesapi
from handlesapi import AsyncHandlesApi, HandlesApi, HandlesApiConnectionError, HandlesApiError, OPERATIONS

CREDIT_HEADERS = {
    "x-credits-cost": "1",
    "x-credits-remaining": "99",
    "x-credits-period-end": "2100-01-01T00:00:00.000Z",
    "x-ratelimit-limit": "10",
    "x-ratelimit-remaining": "9",
    "x-ratelimit-reset": "1795713960",
}


def ok(data: Any, **meta: Any) -> httpx.Response:
    body = {"data": data, "meta": {"request_id": "req_1", "generated_at": "2026-01-01T00:00:00Z", "took_ms": 3, **meta}}
    return httpx.Response(200, json=body, headers=CREDIT_HEADERS)


def err(status: int, code: str, headers: Any = None) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": "nope", "request_id": "req_9"}}, headers=headers or {}
    )


def make(handler: Callable[[httpx.Request], httpx.Response], **kw: Any) -> "tuple[HandlesApi, List[httpx.Request]]":
    seen: List[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(wrapped))
    return HandlesApi("hk_live_test", http_client=http, **kw), seen


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("handlesapi.client.time.sleep", lambda s: None)

    async def instant(s: float) -> None:
        return None

    monkeypatch.setattr("handlesapi.client.asyncio.sleep", instant)


def test_covers_every_endpoint() -> None:
    assert len(OPERATIONS) == 113
    api = HandlesApi("k")
    for name in OPERATIONS:
        assert callable(getattr(api, name)), name


def test_path_query_auth_and_credit_headers() -> None:
    api, seen = make(lambda r: ok({"handle": "nasa"}))
    res = api.accounts_check("x", "nasa")
    req = seen[0]
    assert str(req.url) == "https://api.handlesapi.com/v1/accounts/x/nasa"
    assert req.headers["authorization"] == "Bearer hk_live_test"
    assert req.headers["user-agent"].startswith("handlesapi-python/")
    assert res.data == {"handle": "nasa"}
    assert (res.credits.cost, res.credits.remaining, res.credits.period_end) == (1, 99, "2100-01-01T00:00:00.000Z")
    assert (res.rate_limit.limit, res.rate_limit.remaining, res.rate_limit.reset) == (10, 9, 1795713960)
    assert res.request_id == "req_1"


def test_query_encoding_booleans_and_none() -> None:
    api, seen = make(lambda r: ok({}))
    api.x_users_tweets("a b", limit=5, include_replies=True)
    assert seen[0].url.raw_path.split(b"?")[0] == b"/v1/x/users/a%20b/tweets"
    assert dict(seen[0].url.params) == {"limit": "5", "include_replies": "true"}


def test_post_sends_body_params_as_json() -> None:
    api, seen = make(lambda r: ok({"requested": 1}))
    api.accounts_bulk(targets=[{"platform": "x", "handle": "nasa"}])
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/accounts/check"
    assert json.loads(seen[0].content) == {"targets": [{"platform": "x", "handle": "nasa"}]}


def test_error_has_stable_code_and_request_id() -> None:
    api, _ = make(lambda r: err(404, "not_found"))
    with pytest.raises(HandlesApiError) as info:
        api.accounts_check("x", "nobody")
    e = info.value
    assert (e.status, e.code, e.request_id, e.retryable) == (404, "not_found", "req_9", False)


@pytest.mark.parametrize("status,code", [(402, "quota_exceeded"), (422, "invalid_request"), (401, "unauthorized")])
def test_no_retry_for_client_errors(status: int, code: str) -> None:
    api, seen = make(lambda r: err(status, code))
    with pytest.raises(HandlesApiError) as info:
        api.accounts_platforms()
    assert info.value.code == code
    assert len(seen) == 1


def test_retries_5xx_then_succeeds() -> None:
    n = {"i": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return err(503, "upstream_timeout") if n["i"] < 3 else ok({"fine": True})

    api, seen = make(handler)
    assert api.accounts_platforms().data == {"fine": True}
    assert len(seen) == 3


def test_honours_retry_after_and_refuses_long_waits() -> None:
    n = {"i": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return err(429, "rate_limited", {"retry-after": "0"}) if n["i"] == 1 else ok({})

    api, seen = make(handler)
    api.accounts_platforms()
    assert len(seen) == 2

    api2, seen2 = make(lambda r: err(429, "rate_limited", {"retry-after": "120"}))
    with pytest.raises(HandlesApiError) as info:
        api2.accounts_platforms()
    assert info.value.retry_after == 120
    assert len(seen2) == 1


def test_gives_up_after_max_retries() -> None:
    api, seen = make(lambda r: err(502, "upstream_error"), max_retries=1)
    with pytest.raises(HandlesApiError) as info:
        api.accounts_platforms()
    assert info.value.code == "upstream_error"
    assert len(seen) == 2


def test_non_json_gateway_page_is_unknown_and_retried() -> None:
    n = {"i": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(502, text="Bad gateway") if n["i"] == 1 else ok({})

    api, seen = make(handler)
    api.accounts_platforms()
    assert len(seen) == 2


def test_network_failure_is_connection_error() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    api, seen = make(handler, max_retries=1)
    with pytest.raises(HandlesApiConnectionError) as info:
        api.accounts_platforms()
    assert not info.value.timed_out
    assert len(seen) == 2


def test_paginate_follows_cursor_until_null() -> None:
    pages = [
        ok({"n": 1}, page={"limit": 2, "count": 2, "next_cursor": "c2"}),
        ok({"n": 2}, page={"limit": 2, "count": 2, "next_cursor": "c3"}),
        ok({"n": 3}, page={"limit": 2, "count": 1, "next_cursor": None}),
    ]
    it = iter(pages)
    api, seen = make(lambda r: next(it))
    got = [p.data["n"] for p in api.paginate(api.x_users_tweets, handle="nasa", limit=2)]
    assert got == [1, 2, 3]
    assert seen[1].url.params["cursor"] == "c2"
    assert seen[2].url.params["cursor"] == "c3"


def test_paginate_max_pages() -> None:
    api, seen = make(lambda r: ok({}, page={"limit": 1, "count": 1, "next_cursor": "more"}))
    assert len(list(api.paginate(api.x_users_tweets, handle="nasa", max_pages=2))) == 2
    assert len(seen) == 2


def test_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HANDLES_API_KEY", raising=False)
    with pytest.raises(ValueError):
        HandlesApi()


def test_async_client() -> None:
    n = {"i": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return err(503, "service_unavailable")
        return ok({"handle": "mrbeast"}, page={"limit": 1, "count": 1, "next_cursor": None})

    async def run() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with AsyncHandlesApi("k", http_client=http) as api:
            res = await api.accounts_check("youtube", "mrbeast")
            assert res.data == {"handle": "mrbeast"}
            assert res.credits.remaining == 99
            pages = [p async for p in api.paginate(api.x_users_tweets, handle="nasa")]
            assert len(pages) == 1
        await http.aclose()

    asyncio.run(run())
    assert n["i"] == 3


def test_version_exposed() -> None:
    assert handlesapi.__version__ == "0.1.0"
