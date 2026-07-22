"""OpenRouter API client for making LLM requests."""

import asyncio
import httpx
from typing import Awaitable, Callable, List, Dict, Any, Optional
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL

RETRYABLE_STATUS_CODES = {429, 502, 503}


def split_model_id(model: str) -> Dict[str, str]:
    """Split an OpenRouter model identifier into display-friendly parts."""
    provider, _, model_name = model.partition("/")
    return {
        "provider": provider or "unknown",
        "model_name": model_name or model,
    }


def build_model_status(
    model: str,
    status: str,
    attempts: int = 0,
    response: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create the model status payload consumed by the API and UI."""
    identity = split_model_id(model)
    return {
        "model": model,
        "provider": identity["provider"],
        "model_name": identity["model_name"],
        "status": status,
        "attempts": attempts,
        "response": response,
        "error": error,
    }


def _short_http_error(response: httpx.Response) -> str:
    detail = response.text.strip()
    if len(detail) > 180:
        detail = detail[:177] + "..."
    return detail or response.reason_phrase or "HTTP request failed"


def _error_payload(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        return {
            "http_status": exc.response.status_code,
            "message": _short_http_error(exc.response),
            "type": "http_error",
        }

    if isinstance(exc, httpx.TimeoutException):
        return {
            "http_status": None,
            "message": "Request timed out",
            "type": "timeout",
        }

    return {
        "http_status": None,
        "message": str(exc) or exc.__class__.__name__,
        "type": "request_error",
    }


def _is_retryable_error(error: Dict[str, Any]) -> bool:
    return (
        error.get("type") == "timeout"
        or error.get("http_status") in RETRYABLE_STATUS_CODES
    )


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    try:
        return await _query_model_once(model, messages, timeout=timeout)

    except Exception as e:
        print(f"Error querying model {model}: {e}")
        return None


async def _query_model_once(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload
        )
        response.raise_for_status()

        data = response.json()
        message = data['choices'][0]['message']

        return {
            'content': message.get('content'),
            'reasoning_details': message.get('reasoning_details')
        }


async def query_model_with_retries(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    max_retries: int = 2,
    backoff_base: float = 1.0,
    status_callback: Optional[
        Callable[[Dict[str, Any]], Awaitable[None]]
    ] = None,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Dict[str, Any]:
    """
    Query a single model with retry metadata.

    Retries happen for 429, 502, 503 and timeout errors. max_retries=2 means
    at most three total attempts.
    """
    attempts = 0
    last_error = None

    for attempt_index in range(max_retries + 1):
        attempts = attempt_index + 1
        status = "answering" if attempt_index == 0 else "retrying"
        if status_callback:
            await status_callback(build_model_status(model, status, attempts))

        try:
            response = await _query_model_once(model, messages, timeout=timeout)
            completed = build_model_status(
                model,
                "completed",
                attempts=attempts,
                response=response,
            )
            if status_callback:
                await status_callback(completed)
            return completed
        except Exception as exc:
            last_error = _error_payload(exc)
            if attempt_index < max_retries and _is_retryable_error(last_error):
                await sleep_func(backoff_base * (2 ** attempt_index))
                continue
            break

    failed = build_model_status(
        model,
        "failed",
        attempts=attempts,
        error=last_error or {
            "http_status": None,
            "message": "Unknown error",
            "type": "request_error",
        },
    )
    if status_callback:
        await status_callback(failed)
    print(f"Error querying model {model}: {failed['error']}")
    return failed


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    # Create tasks for all models
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}


async def query_models_parallel_with_retries(
    models: List[str],
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    max_retries: int = 2,
    backoff_base: float = 1.0,
    status_callback: Optional[
        Callable[[Dict[str, Any]], Awaitable[None]]
    ] = None,
) -> Dict[str, Dict[str, Any]]:
    """Query multiple models in parallel with per-model status payloads."""
    tasks = [
        query_model_with_retries(
            model,
            messages,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            status_callback=status_callback,
        )
        for model in models
    ]

    responses = await asyncio.gather(*tasks)
    return {model: response for model, response in zip(models, responses)}
