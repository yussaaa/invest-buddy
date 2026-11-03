"""Which provider errors are worth retrying."""

from __future__ import annotations

import pytest
from newsapi.newsapi_exception import NewsAPIException

from app.tools.retry_decorator import _is_retryable


def _newsapi(code: str) -> NewsAPIException:
    return NewsAPIException({"status": "error", "code": code, "message": "x"})


@pytest.mark.parametrize("code", ["apiKeyInvalid", "apiKeyMissing", "apiKeyDisabled",
                                  "apiKeyExhausted", "parameterInvalid"])
def test_permanent_newsapi_errors_are_not_retried(code):
    """apiKeyInvalid carries no "401" in its text, which is how it slipped through."""
    assert _is_retryable(_newsapi(code)) is False


def test_a_newsapi_rate_limit_is_retried():
    assert _is_retryable(_newsapi("rateLimited")) is True


def test_network_errors_are_retried_and_value_errors_are_not():
    assert _is_retryable(ConnectionError("reset")) is True
    assert _is_retryable(ValueError("unknown ticker")) is False


def _http_error(status: int):
    import httpx

    request = httpx.Request("GET", "https://example.test/")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_an_http_4xx_is_permanent(status):
    """A Cloudflare 403 was retried three times before the caller saw it."""
    assert _is_retryable(_http_error(status)) is False


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_rate_limits_and_server_errors_are_retried(status):
    assert _is_retryable(_http_error(status)) is True
