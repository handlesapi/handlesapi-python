# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-11-26

### Added

- Python client for all 113 Handles API endpoints, generated from the OpenAPI spec, with a sync client (`HandlesApi`) and an async client (`AsyncHandlesApi`) on `httpx`.
- `TypedDict` response types and `py.typed`; `mypy --strict` clean.
- `X-Credits-*` and `X-RateLimit-*` parsing on every response.
- Retries: `Retry-After` honoured up to 30 seconds, exponential backoff with jitter for 5xx and network failures, never for 401, 402, 404 or 422.
- Cursor pagination helper that follows `meta.page.next_cursor`.
- Typed `HandlesApiError` with the stable error `code` and `request_id`.
- Generator (`scripts/generate.py`) and a test suite; CI on Python 3.9 to 3.13.
