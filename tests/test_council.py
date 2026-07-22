import pytest

from backend import council


def _completed(model, content):
    provider, _, model_name = model.partition("/")
    return {
        "model": model,
        "provider": provider,
        "model_name": model_name,
        "status": "completed",
        "attempts": 1,
        "response": {"content": content},
        "error": None,
    }


def _failed(model, status_code=503):
    provider, _, model_name = model.partition("/")
    return {
        "model": model,
        "provider": provider,
        "model_name": model_name,
        "status": "failed",
        "attempts": 3,
        "response": None,
        "error": {
            "http_status": status_code,
            "message": "temporarily unavailable",
            "type": "http_error",
        },
    }


def test_validate_council_configuration_rejects_ambiguous_members():
    errors = council.validate_council_configuration([
        "openrouter/free",
        "openai/gpt-4o",
        "openai/gpt-4o:free",
    ])

    assert any("openrouter/free" in error for error in errors)
    assert any("Duplicate council model name" in error for error in errors)


@pytest.mark.asyncio
async def test_stage1_blocks_when_fewer_than_minimum_successes(monkeypatch):
    models = ["openai/a", "google/b", "anthropic/c"]

    async def fake_parallel(models_arg, messages, **kwargs):
        assert models_arg == models
        return {
            "openai/a": _completed("openai/a", "one"),
            "google/b": _completed("google/b", "two"),
            "anthropic/c": _failed("anthropic/c"),
        }

    monkeypatch.setattr(council, "COUNCIL_MODELS", models)
    monkeypatch.setattr(council, "MIN_SUCCESSFUL_RESPONSES", 3)
    monkeypatch.setattr(council, "query_models_parallel_with_retries", fake_parallel)

    result = await council.stage1_collect_responses_detailed("Türkçe kısa cevap ver")

    assert len(result["responses"]) == 2
    assert result["summary"]["blocked"] is True
    assert result["summary"]["can_continue"] is False
    assert result["statuses"][2]["error"]["http_status"] == 503


@pytest.mark.asyncio
async def test_stage1_requires_user_continue_when_some_models_fail(monkeypatch):
    models = ["openai/a", "google/b", "anthropic/c", "x-ai/d"]

    async def fake_parallel(models_arg, messages, **kwargs):
        return {
            "openai/a": _completed("openai/a", "one"),
            "google/b": _completed("google/b", "two"),
            "anthropic/c": _completed("anthropic/c", "three"),
            "x-ai/d": _failed("x-ai/d", 502),
        }

    monkeypatch.setattr(council, "COUNCIL_MODELS", models)
    monkeypatch.setattr(council, "MIN_SUCCESSFUL_RESPONSES", 3)
    monkeypatch.setattr(council, "query_models_parallel_with_retries", fake_parallel)

    result = await council.stage1_collect_responses_detailed("Ne önerirsin?")

    assert len(result["responses"]) == 3
    assert result["summary"]["blocked"] is False
    assert result["summary"]["requires_continue"] is True


@pytest.mark.asyncio
async def test_chairman_prompt_and_quality_control_keep_synthesis_separate(monkeypatch):
    prompts = []

    async def fake_query_model(model, messages, timeout=120.0):
        prompts.append(messages[0]["content"])
        if len(prompts) == 1:
            return {"content": "## Kısa ortak cevap\n\nTaslak cevap"}
        return {"content": "## Kısa ortak cevap\n\nDüzeltilmiş cevap"}

    monkeypatch.setattr(council, "query_model", fake_query_model)
    monkeypatch.setattr(council, "CHAIRMAN_MODEL", "google/chair")

    result = await council.stage3_synthesize_final(
        "Bana Türkçe kısa anlat",
        [
            {
                "model": "openai/a",
                "provider": "openai",
                "model_name": "a",
                "response": "Cevap",
            },
            {
                "model": "google/b",
                "provider": "google",
                "model_name": "b",
                "response": "Cevap",
            },
            {
                "model": "anthropic/c",
                "provider": "anthropic",
                "model_name": "c",
                "response": "Cevap",
            },
        ],
        [],
    )

    assert "Do not mention \"Response A\"" in prompts[0]
    assert "Yeni bilgi" in prompts[1]
    assert result["response"] == "## Kısa ortak cevap\n\nDüzeltilmiş cevap"
