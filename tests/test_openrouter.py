import httpx
import pytest

from backend import openrouter


def _http_status_error(status_code):
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status_code, request=request, text="rate limited")
    return httpx.HTTPStatusError("failed", request=request, response=response)


@pytest.mark.asyncio
async def test_query_model_with_retries_uses_exponential_backoff(monkeypatch):
    calls = []
    delays = []
    statuses = []

    async def fake_query_once(model, messages, timeout, max_tokens=None):
        calls.append((model, messages, timeout))
        if len(calls) < 3:
            raise _http_status_error(429)
        return {"content": "ok"}

    async def fake_sleep(delay):
        delays.append(delay)

    async def status_callback(status):
        statuses.append(status)

    monkeypatch.setattr(openrouter, "_query_model_once", fake_query_once)

    result = await openrouter.query_model_with_retries(
        "openai/test-model",
        [{"role": "user", "content": "hi"}],
        timeout=7,
        max_retries=2,
        backoff_base=0.5,
        sleep_func=fake_sleep,
        status_callback=status_callback,
    )

    assert result["status"] == "completed"
    assert result["attempts"] == 3
    assert result["response"]["content"] == "ok"
    assert delays == [0.5, 1.0]
    assert [status["status"] for status in statuses] == [
        "answering",
        "retrying",
        "retrying",
        "completed",
    ]


@pytest.mark.asyncio
async def test_query_model_with_retries_does_not_retry_non_retryable_http(monkeypatch):
    calls = []

    async def fake_query_once(model, messages, timeout, max_tokens=None):
        calls.append(model)
        raise _http_status_error(400)

    monkeypatch.setattr(openrouter, "_query_model_once", fake_query_once)

    result = await openrouter.query_model_with_retries(
        "openai/test-model",
        [],
        max_retries=2,
    )

    assert len(calls) == 1
    assert result["status"] == "failed"
    assert result["attempts"] == 1
    assert result["error"]["http_status"] == 400
