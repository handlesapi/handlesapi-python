# Contributing to handlesapi-python

Thanks for helping. This is the Python client for the [Handles API](https://handlesapi.com).

## Setup

```bash
git clone https://github.com/handlesapi/handlesapi-python.git
cd handlesapi-python
pip install -e ".[dev]"
mypy
pytest
```

Python 3.9 or newer. The only runtime dependency is `httpx`; please keep it that way.

## How the code is organised

- `src/handlesapi/_core.py`, `client.py`, `errors.py` are hand-written: transport, retries, credit headers, pagination.
- `_types.py`, `_operations.py` and `_methods.py` are produced from the OpenAPI spec by `scripts/generate.py`. Do not edit them by hand; change the generator and run `python scripts/generate.py`.
- `tests/` runs against `httpx.MockTransport`, so tests need no API key and no network.

## Pull requests

- One change per pull request, with a test for behaviour changes.
- `mypy` and `pytest` must pass.
- If the API changed, regenerate from the latest [spec](https://github.com/handlesapi/openapi) and say so in the description.
- Add a line to `CHANGELOG.md` under Unreleased.

Bugs and ideas go in [issues](https://github.com/handlesapi/handlesapi-python/issues). For account or billing questions, email support@handlesapi.com.
