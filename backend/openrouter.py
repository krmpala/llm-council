"""Validated OpenRouter API client for LLM requests."""

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL


RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_PREVIEW_LIMIT = 480


class OpenRouterMessage(BaseModel):
    """The validated message portion of an OpenRouter completion choice."""

    model_config = ConfigDict(extra="allow")

    content: Optional[StrictStr] = None
    reasoning_details: Any = None


class OpenRouterChoice(BaseModel):
    """The validated first completion choice returned by OpenRouter."""

    model_config = ConfigDict(extra="allow")

    message: Optional[OpenRouterMessage] = None
    finish_reason: Optional[StrictStr] = None


class OpenRouterRawResponse(BaseModel):
    """The subset of an OpenRouter response that the application consumes."""

    model_config = ConfigDict(extra="allow")

    choices: Optional[List[OpenRouterChoice]] = None
    usage: Optional[Dict[str, Any]] = None


class ModelCallResult(BaseModel):
    """A successfully validated model response."""

    content: StrictStr
    reasoning_details: Any = None
    finish_reason: Optional[StrictStr] = None
    usage: Optional[Dict[str, Any]] = None


class ModelCallError(Exception):
    """A structured, user-safe error from a model request or response."""

    def __init__(
        self,
        error_type: str,
        error_message: str,
        *,
        http_status: Optional[int] = None,
        provider_error_code: Optional[Any] = None,
        provider_error_message: Optional[str] = None,
        retryable: bool = False,
        raw_response_preview: Optional[str] = None,
    ) -> None:
        super().__init__(error_message)
        self.error_type = error_type
        self.error_message = error_message
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider_error_message = provider_error_message
        self.retryable = retryable
        self.raw_response_preview = raw_response_preview

    def to_dict(self) -> Dict[str, Any]:
        return {
            "http_status": self.http_status,
            "type": self.error_type,
            "message": self.error_message,
            "provider_error_code": self.provider_error_code,
            "provider_error_message": self.provider_error_message,
            "retryable": self.retryable,
            "raw_response_preview": self.raw_response_preview,
        }


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
    **extra: Any,
) -> Dict[str, Any]:
    """Create the model status payload consumed by the API and UI."""
    identity = split_model_id(model)
    payload = {
        "model": model,
        "provider": identity["provider"],
        "model_name": identity["model_name"],
        "status": status,
        "attempts": attempts,
        "response": response,
        "error": error,
    }
    if error:
        payload.update({
            "http_status": error.get("http_status"),
            "error_type": error.get("type"),
            "error_message": error.get("message"),
            "provider_error_code": error.get("provider_error_code"),
            "provider_error_message": error.get("provider_error_message"),
            "retryable": error.get("retryable", False),
            "raw_response_preview": error.get("raw_response_preview"),
        })
    payload.update(extra)
    return payload


def _safe_preview(value: Any) -> Optional[str]:
    """Keep a short body preview while removing header-like secrets."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)([^,\s\"}]+)", r"\1[redacted]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s\"}]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)([^,\s\"}]+)", r"\1[redacted]", text)
    return text[:_PREVIEW_LIMIT] + ("..." if len(text) > _PREVIEW_LIMIT else "")


def _friendly_message(error_type: str, detail: Optional[str] = None) -> str:
    messages = {
        "invalid_json": "Model saglayicisi gecersiz JSON dondu.",
        "empty_response": "Model saglayicisi bos bir yanit dondu.",
        "missing_choices": "Model saglayicisinin yanitinda choices alani bulunamadi.",
        "empty_choices": "Model saglayicisinin yanitinda choices listesi bos.",
        "missing_message": "Model saglayicisinin yanitinda message alani bulunamadi.",
        "missing_content": "Model saglayicisinin yanitinda metin icerigi bulunamadi.",
        "empty_content": "Model saglayicisi bos bir metin icerigi dondu.",
        "provider_error_payload": "Model saglayicisi yanit govdesinde bir hata bildirdi.",
        "malformed_response": "Model saglayicisi beklenen cevap bicimini dondurmedi.",
        "network_error": "Model saglayicisina baglanirken ag hatasi olustu.",
        "timeout": "Model saglayicisi zaman asimina ugradi.",
        "rate_limited": "Model saglayicisi istek sinirina ulasti.",
        "authentication_error": "Model saglayicisi kimlik dogrulamayi kabul etmedi.",
        "model_not_found": "Yapilandirilan model saglayicida bulunamadi.",
        "provider_error": "Model saglayicisi istegi isleyemedi.",
    }
    message = messages.get(error_type, "Model istegi kontrollu bicimde basarisiz oldu.")
    if detail and error_type in {"provider_error_payload", "provider_error"}:
        return f"{message} {detail[:180]}"
    return message


def _code_as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _classify_provider_code(code: Any, fallback_type: str = "provider_error") -> tuple[str, bool]:
    numeric_code = _code_as_int(code)
    if numeric_code == 429:
        return "rate_limited", True
    if numeric_code in {502, 503, 504}:
        return fallback_type, True
    if numeric_code in {401, 403}:
        return "authentication_error", False
    if numeric_code == 404:
        return "model_not_found", False
    if numeric_code == 400:
        return "provider_error", False
    return fallback_type, False


def _provider_error_details(payload: Any) -> tuple[Optional[Any], Optional[str]]:
    if isinstance(payload, dict):
        code = payload.get("code") or payload.get("status") or payload.get("status_code")
        message = payload.get("message") or payload.get("detail") or payload.get("error")
        if isinstance(message, (dict, list)):
            message = _safe_preview(message)
        return code, str(message) if message else None
    return None, str(payload) if payload else None


def _is_temporary_provider_message(message: Optional[str]) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in (
        "temporar", "timeout", "timed out", "upstream", "overload", "unavailable",
        "try again", "rate limit", "capacity", "gateway",
    ))


def _provider_payload_error(data: Dict[str, Any], http_status: Optional[int]) -> ModelCallError:
    provider_error = data.get("error")
    code, provider_message = _provider_error_details(provider_error)
    error_type, retryable = _classify_provider_code(code, "provider_error_payload")
    retryable = retryable or _is_temporary_provider_message(provider_message)
    return ModelCallError(
        error_type,
        _friendly_message(error_type, provider_message),
        http_status=http_status,
        provider_error_code=code,
        provider_error_message=provider_message,
        retryable=retryable,
        raw_response_preview=_safe_preview(data),
    )


def _parse_openrouter_payload(data: Any, http_status: Optional[int]) -> ModelCallResult:
    """Validate a successful HTTP body without unguarded nested indexing."""
    if data is None:
        raise ModelCallError(
            "empty_response",
            _friendly_message("empty_response"),
            http_status=http_status,
            retryable=True,
        )
    if not isinstance(data, dict):
        raise ModelCallError(
            "malformed_response",
            _friendly_message("malformed_response"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )
    if "error" in data:
        raise _provider_payload_error(data, http_status)
    if "choices" not in data:
        raise ModelCallError(
            "missing_choices",
            _friendly_message("missing_choices"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )

    choices = data.get("choices")
    if not isinstance(choices, list):
        raise ModelCallError(
            "malformed_response",
            _friendly_message("malformed_response"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )
    if not choices:
        raise ModelCallError(
            "empty_choices",
            _friendly_message("empty_choices"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )

    first_choice = choices[0]
    if not isinstance(first_choice, dict) or "message" not in first_choice:
        raise ModelCallError(
            "missing_message",
            _friendly_message("missing_message"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ModelCallError(
            "missing_message",
            _friendly_message("missing_message"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )
    if "content" not in message or not isinstance(message.get("content"), str):
        raise ModelCallError(
            "missing_content",
            _friendly_message("missing_content"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )
    if not message["content"].strip():
        raise ModelCallError(
            "empty_content",
            _friendly_message("empty_content"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )

    try:
        parsed = OpenRouterRawResponse.model_validate(data)
    except ValidationError:
        raise ModelCallError(
            "malformed_response",
            _friendly_message("malformed_response"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        ) from None

    choice = parsed.choices[0] if parsed.choices else None
    validated_message = choice.message if choice else None
    if not validated_message or not validated_message.content or not validated_message.content.strip():
        raise ModelCallError(
            "empty_content",
            _friendly_message("empty_content"),
            http_status=http_status,
            retryable=True,
            raw_response_preview=_safe_preview(data),
        )
    return ModelCallResult(
        content=validated_message.content,
        reasoning_details=validated_message.reasoning_details,
        finish_reason=choice.finish_reason,
        usage=parsed.usage,
    )


def _http_error_from_response(response: httpx.Response) -> ModelCallError:
    preview = _safe_preview(response.text)
    body: Any = None
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        pass

    if isinstance(body, dict) and "error" in body:
        error = _provider_payload_error(body, response.status_code)
        if error.provider_error_code is None:
            error.provider_error_code = response.status_code
        return error

    error_type, retryable = _classify_provider_code(response.status_code)
    return ModelCallError(
        error_type,
        _friendly_message(error_type),
        http_status=response.status_code,
        provider_error_code=None,
        provider_error_message=None,
        retryable=retryable,
        raw_response_preview=preview,
    )


def _error_payload(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, ModelCallError):
        return exc.to_dict()
    if isinstance(exc, httpx.HTTPStatusError):
        return _http_error_from_response(exc.response).to_dict()
    if isinstance(exc, httpx.TimeoutException):
        return ModelCallError(
            "timeout", _friendly_message("timeout"), retryable=True
        ).to_dict()
    if isinstance(exc, httpx.RequestError):
        return ModelCallError(
            "network_error", _friendly_message("network_error"), retryable=True
        ).to_dict()
    return ModelCallError(
        "network_error", _friendly_message("network_error"), retryable=True
    ).to_dict()


def _is_retryable_error(error: Dict[str, Any]) -> bool:
    return bool(error.get("retryable")) or (
        error.get("type") in {
            "timeout", "network_error", "invalid_json", "empty_response", "missing_choices",
            "empty_choices", "missing_message", "missing_content", "empty_content",
            "malformed_response",
        }
        or error.get("http_status") in RETRYABLE_STATUS_CODES
        or _code_as_int(error.get("provider_error_code")) in RETRYABLE_STATUS_CODES
    )


def is_retryable_error(error: Optional[Dict[str, Any]]) -> bool:
    """Public helper for retry and fallback decisions."""
    return bool(error) and _is_retryable_error(error)


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    max_tokens: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Query once, returning ``None`` for legacy callers on controlled failure."""
    try:
        return await _query_model_once(model, messages, timeout=timeout, max_tokens=max_tokens)
    except Exception as exc:
        error = _error_payload(exc)
        print(f"Model request failed for {model}: {error['type']}")
        return None


async def _query_model_once(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
        if response.is_error:
            raise _http_error_from_response(response)
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            raise ModelCallError(
                "invalid_json",
                _friendly_message("invalid_json"),
                http_status=response.status_code,
                retryable=True,
                raw_response_preview=_safe_preview(response.text),
            ) from None
        return _parse_openrouter_payload(data, response.status_code).model_dump()


async def query_model_with_retries(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    max_tokens: Optional[int] = None,
    max_retries: int = 2,
    backoff_base: float = 1.0,
    status_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Dict[str, Any]:
    """Query a model with retryable schema, network, and provider failures."""
    attempts = 0
    last_error: Optional[Dict[str, Any]] = None

    for attempt_index in range(max_retries + 1):
        attempts = attempt_index + 1
        status = "answering" if attempt_index == 0 else "retrying"
        if status_callback:
            await status_callback(build_model_status(model, status, attempts))
        try:
            response = await _query_model_once(
                model, messages, timeout=timeout, max_tokens=max_tokens
            )
            completed = build_model_status(model, "completed", attempts=attempts, response=response)
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
        error=last_error or ModelCallError(
            "network_error", _friendly_message("network_error"), retryable=True
        ).to_dict(),
    )
    if status_callback:
        await status_callback(failed)
    print(f"Model request failed for {model}: {failed['error']['type']}")
    return failed


async def query_models_parallel(
    models: List[str], messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    tasks = [query_model(model, messages) for model in models]
    responses = await asyncio.gather(*tasks)
    return {model: response for model, response in zip(models, responses)}


async def query_models_parallel_with_retries(
    models: List[str],
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    max_tokens: Optional[int] = None,
    max_retries: int = 2,
    backoff_base: float = 1.0,
    status_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Dict[str, Any]]:
    tasks = [
        query_model_with_retries(
            model,
            messages,
            timeout=timeout,
            max_tokens=max_tokens,
            max_retries=max_retries,
            backoff_base=backoff_base,
            status_callback=status_callback,
        )
        for model in models
    ]
    responses = await asyncio.gather(*tasks)
    return {model: response for model, response in zip(models, responses)}
