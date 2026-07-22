import json

import pytest

from backend import council


def _members():
    return [
        {"member_id": "member-1", "primary_model": "openai/a", "fallback_models": []},
        {"member_id": "member-2", "primary_model": "google/b", "fallback_models": []},
        {"member_id": "member-3", "primary_model": "anthropic/c", "fallback_models": []},
    ]


def _completed(model, content, attempts=1):
    return {
        "model": model,
        "status": "completed",
        "attempts": attempts,
        "response": {"content": content},
        "error": None,
    }


def _failed(model, status_code=503):
    return {
        "model": model,
        "status": "failed",
        "attempts": 3,
        "response": None,
        "error": {
            "http_status": status_code,
            "message": "temporarily unavailable",
            "type": "http_error",
        },
    }


def _stage1_result(member_id, model, content):
    provider, _, model_name = model.partition("/")
    return {
        "member_id": member_id,
        "primary_model": model,
        "model": model,
        "actual_model": model,
        "provider": provider,
        "model_name": model_name,
        "display_name": f"{provider} / {model_name}",
        "response": content,
    }


def test_validate_council_configuration_rejects_ambiguous_members():
    errors = council.validate_council_configuration([
        {"member_id": "member-1", "primary_model": "openrouter/free", "fallback_models": []},
        {"member_id": "member-2", "primary_model": "openai/gpt-4o", "fallback_models": []},
        {"member_id": "member-3", "primary_model": "openai/gpt-4o:free", "fallback_models": []},
    ])

    assert any("openrouter/free" in error for error in errors)
    assert any("Duplicate council model name" in error for error in errors)


def test_validate_council_configuration_rejects_duplicate_real_models_across_seats():
    errors = council.validate_council_configuration([
        {"member_id": "member-1", "primary_model": "openai/a", "fallback_models": []},
        {"member_id": "member-2", "primary_model": "google/b", "fallback_models": ["openai/a"]},
    ])

    assert any("Duplicate council model route" in error for error in errors)


@pytest.mark.asyncio
async def test_stage1_keeps_failed_member_id_without_copying_another_response(monkeypatch):
    members = _members()

    async def fake_query(model, messages, **kwargs):
        if model == "openai/a":
            return _completed(model, "answer-a")
        if model == "google/b":
            return _completed(model, "answer-b")
        return _failed(model, 429)

    monkeypatch.setattr(council, "COUNCIL_MEMBERS", members)
    monkeypatch.setattr(council, "MIN_SUCCESSFUL_RESPONSES", 3)
    monkeypatch.setattr(council, "CONTINUE_MIN_SUCCESSFUL_RESPONSES", 2)
    monkeypatch.setattr(council, "query_model_with_retries", fake_query)

    result = await council.stage1_collect_responses_detailed("Türkçe kısa cevap ver")

    assert [response["member_id"] for response in result["responses"]] == [
        "member-1",
        "member-2",
    ]
    assert [response["response"] for response in result["responses"]] == [
        "answer-a",
        "answer-b",
    ]
    failed_status = result["statuses"][2]
    assert failed_status["member_id"] == "member-3"
    assert failed_status["status"] == "failed"
    assert failed_status["error"]["http_status"] == 429
    assert failed_status["response"] is None
    assert result["summary"]["blocked"] is True
    assert result["summary"]["requires_continue"] is True


@pytest.mark.asyncio
async def test_one_successful_response_does_not_offer_continue(monkeypatch):
    members = _members()

    async def fake_query(model, messages, **kwargs):
        if model == "openai/a":
            return _completed(model, "answer-a")
        return _failed(model, 503)

    monkeypatch.setattr(council, "COUNCIL_MEMBERS", members)
    monkeypatch.setattr(council, "query_model_with_retries", fake_query)

    result = await council.stage1_collect_responses_detailed("Kısa cevap")

    assert len(result["responses"]) == 1
    assert result["summary"]["blocked"] is True
    assert result["summary"]["requires_continue"] is False
    assert result["summary"]["override_available"] is False


@pytest.mark.asyncio
async def test_user_override_uses_only_successful_two_members(monkeypatch):
    queried_models = []

    async def fake_query(model, messages, **kwargs):
        queried_models.append(model)
        member_id = "member-1" if model == "openai/a" else "member-2"
        return _completed(model, json.dumps({
            "evaluator_member_id": member_id,
            "evaluations": [
                {
                    "evaluated_member_id": "member-1",
                    "constraint_compliance": 8,
                    "language_quality": 8,
                    "completeness": 8,
                    "correctness": 8,
                    "conciseness": 8,
                    "truncation_penalty": 0,
                    "quality_warnings": [],
                    "strengths": "iyi",
                    "weaknesses": "az",
                },
                {
                    "evaluated_member_id": "member-2",
                    "constraint_compliance": 7,
                    "language_quality": 7,
                    "completeness": 7,
                    "correctness": 7,
                    "conciseness": 7,
                    "truncation_penalty": 0,
                    "quality_warnings": [],
                    "strengths": "iyi",
                    "weaknesses": "az",
                },
            ],
            "ranking": ["member-1", "member-2"],
        }))

    async def fake_stage3(user_query, stage1_results, stage2_results, constraints=None, **kwargs):
        return {
            "model": "chair/model",
            "actual_model": "chair/model",
            "provider": "chair",
            "model_name": "model",
            "display_name": "chair / model",
            "response": "final",
        }

    monkeypatch.setattr(council, "query_model_with_retries", fake_query)
    monkeypatch.setattr(council, "stage3_synthesize_final", fake_stage3)

    stage1_results = [
        _stage1_result("member-1", "openai/a", "answer-a"),
        _stage1_result("member-2", "google/b", "answer-b"),
    ]
    stage2, stage3, metadata = await council.run_remaining_council(
        "Kısa cevap ver",
        stage1_results,
        user_override=True,
    )

    assert queried_models == ["openai/a", "google/b"]
    assert [result["member_id"] for result in stage2] == ["member-1", "member-2"]
    assert stage3["response"] == "final"
    assert metadata["user_override"] is True
    assert metadata["stage1_summary"]["continued_with"] == 2


@pytest.mark.asyncio
async def test_constraints_are_carried_to_stage_prompts(monkeypatch):
    prompts = []

    async def fake_query_model(model, messages, **kwargs):
        prompts.append(messages[0]["content"])
        if len(prompts) == 1:
            return _completed(model, "## Kısa ortak cevap\n\nTaslak cevap")
        return _completed(model, "## Kısa ortak cevap\n\nDüzeltilmiş cevap")

    monkeypatch.setattr(council, "query_model_with_retries", fake_query_model)
    monkeypatch.setattr(council, "CHAIRMAN_PRIMARY_MODEL", "google/chair")

    result = await council.stage3_synthesize_final(
        "Bana Türkçe kısa anlat, tablo olmasın",
        [
            _stage1_result("member-1", "openai/a", "Cevap"),
            _stage1_result("member-2", "google/b", "Cevap"),
        ],
        [],
    )

    assert "Structured user constraints" in prompts[0]
    assert "explicit_exclusions" in prompts[0]
    assert "Yeni bilgi" in prompts[1]
    assert result["response"] == "## Kısa ortak cevap\n\nDüzeltilmiş cevap"


@pytest.mark.asyncio
async def test_chairman_primary_429_uses_fallback(monkeypatch):
    calls = []

    async def fake_query(model, messages, **kwargs):
        calls.append(model)
        if model == "chair/primary":
            return _failed(model, 429)
        return _completed(model, "final text")

    monkeypatch.setattr(council, "CHAIRMAN_PRIMARY_MODEL", "chair/primary")
    monkeypatch.setattr(council, "CHAIRMAN_FALLBACK_MODELS", ["chair/fallback"])
    monkeypatch.setattr(council, "query_model_with_retries", fake_query)

    result = await council.stage3_synthesize_final(
        "Kısa cevap",
        [_stage1_result("member-1", "openai/a", "A"), _stage1_result("member-2", "google/b", "B")],
        [],
    )

    assert calls[0:2] == ["chair/primary", "chair/fallback"]
    assert result["actual_model"] == "chair/fallback"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_chairman_quality_failure_keeps_synthesis(monkeypatch):
    calls = []

    async def fake_query(model, messages, **kwargs):
        calls.append(messages[0]["content"])
        if len(calls) == 1:
            return _completed(model, "first synthesis")
        return _failed(model, 503)

    monkeypatch.setattr(council, "CHAIRMAN_PRIMARY_MODEL", "chair/primary")
    monkeypatch.setattr(council, "CHAIRMAN_FALLBACK_MODELS", [])
    monkeypatch.setattr(council, "query_model_with_retries", fake_query)

    result = await council.stage3_synthesize_final(
        "Kısa cevap",
        [_stage1_result("member-1", "openai/a", "A"), _stage1_result("member-2", "google/b", "B")],
        [],
    )

    assert result["response"] == "first synthesis"
    assert result["synthesis_text"] == "first synthesis"
    assert result["quality_review_status"] == "failed"
    assert "ilk sentez" in result["quality_warning"]


@pytest.mark.asyncio
async def test_chairman_failure_returns_detailed_metadata(monkeypatch):
    async def fake_query(model, messages, **kwargs):
        return _failed(model, 401)

    monkeypatch.setattr(council, "CHAIRMAN_PRIMARY_MODEL", "chair/primary")
    monkeypatch.setattr(council, "CHAIRMAN_FALLBACK_MODELS", ["chair/fallback"])
    monkeypatch.setattr(council, "query_model_with_retries", fake_query)

    result = await council.stage3_synthesize_final(
        "Kısa cevap",
        [_stage1_result("member-1", "openai/a", "A"), _stage1_result("member-2", "google/b", "B")],
        [],
    )

    assert result["status"] == "failed"
    assert result["stage"] == "synthesis"
    assert result["http_status"] == 401
    assert result["attempts"] == 3
    assert result["raw_error_preview"]


def test_short_request_extracts_structured_length_and_prompt_limit():
    constraints = council.extract_user_constraints("Cevap kısa, gerçekçi ve Türkçe olsun.")

    assert constraints["requested_length"] == "short"
    assert council._length_instruction("x", constraints).startswith("En fazla 250 kelime")


@pytest.mark.asyncio
async def test_finish_reason_length_marks_response_truncated(monkeypatch):
    members = [{"member_id": "member-1", "primary_model": "openai/a", "fallback_models": []}]

    async def fake_query(model, messages, **kwargs):
        result = _completed(model, "cut off")
        result["response"]["finish_reason"] = "length"
        return result

    monkeypatch.setattr(council, "COUNCIL_MEMBERS", members)
    monkeypatch.setattr(council, "query_model_with_retries", fake_query)

    result = await council.stage1_collect_responses_detailed("Kısa cevap")

    assert result["responses"][0]["truncated"] is True
    assert result["responses"][0]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_truncated_response_is_marked_in_stage2_prompt(monkeypatch):
    prompts = []

    async def fake_query(model, messages, **kwargs):
        prompts.append(messages[0]["content"])
        return _completed(model, "eval\n\nFINAL RANKING:\n1. Response A")

    monkeypatch.setattr(council, "query_model_with_retries", fake_query)
    await council.stage2_collect_rankings(
        "Türkçe kısa cevap",
        [{**_stage1_result("member-1", "openai/a", "cut"), "truncated": True}],
    )

    assert "truncated: true" in prompts[0]
    assert "If the user language is Turkish" in prompts[0]


@pytest.mark.asyncio
async def test_stage2_exception_does_not_drop_other_evaluators(monkeypatch):
    async def fake_query(model, messages, **kwargs):
        if model == "openai/a":
            raise RuntimeError("boom")
        return _completed(model, json.dumps({
            "evaluator_member_id": "member-2",
            "evaluations": [{
                "evaluated_member_id": "member-1",
                "constraint_compliance": 5,
                "language_quality": 5,
                "completeness": 5,
                "correctness": 5,
                "conciseness": 5,
                "truncation_penalty": 0,
                "quality_warnings": [],
                "strengths": "iyi",
                "weaknesses": "az",
            }, {
                "evaluated_member_id": "member-2",
                "constraint_compliance": 7,
                "language_quality": 7,
                "completeness": 7,
                "correctness": 7,
                "conciseness": 7,
                "truncation_penalty": 0,
                "quality_warnings": [],
                "strengths": "iyi",
                "weaknesses": "az",
            }],
            "ranking": ["member-2", "member-1"],
        }))

    monkeypatch.setattr(council, "query_model_with_retries", fake_query)
    results, _, metadata = await council.stage2_collect_rankings(
        "Türkçe kısa cevap",
        [
            _stage1_result("member-1", "openai/a", "A"),
            _stage1_result("member-2", "google/b", "B"),
        ],
    )

    assert [result["member_id"] for result in results] == ["member-2"]
    assert metadata["peer_stage_summary"]["low_confidence"] is True
    assert {status["status"] for status in metadata["peer_evaluator_statuses"]} == {"failed", "completed"}


@pytest.mark.asyncio
async def test_invalid_json_is_repaired_once(monkeypatch):
    calls = []

    async def fake_query(model, messages, **kwargs):
        calls.append(messages[0]["content"])
        if len(calls) == 1:
            return _completed(model, "not json")
        return _completed(model, json.dumps({
            "evaluator_member_id": "member-1",
            "evaluations": [{
                "evaluated_member_id": "member-1",
                "constraint_compliance": 8,
                "language_quality": 8,
                "completeness": 8,
                "correctness": 8,
                "conciseness": 8,
                "truncation_penalty": 0,
                "quality_warnings": [],
                "strengths": "iyi",
                "weaknesses": "az",
            }],
            "ranking": ["member-1"],
        }))

    monkeypatch.setattr(council, "query_model_with_retries", fake_query)
    results, _, metadata = await council.stage2_collect_rankings(
        "Türkçe kısa cevap",
        [_stage1_result("member-1", "openai/a", "A")],
    )

    assert len(calls) == 2
    assert len(results) == 1
    assert metadata["peer_evaluator_statuses"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_invalid_json_repair_failure_becomes_invalid_output(monkeypatch):
    async def fake_query(model, messages, **kwargs):
        return _completed(model, "still not json")

    monkeypatch.setattr(council, "query_model_with_retries", fake_query)
    results, _, metadata = await council.stage2_collect_rankings(
        "Türkçe kısa cevap",
        [_stage1_result("member-1", "openai/a", "A")],
    )

    assert results == []
    assert metadata["peer_evaluator_statuses"][0]["status"] == "invalid_output"
    assert metadata["peer_stage_summary"]["requires_user_choice"] is True


def test_stage2_json_rejects_duplicate_or_unknown_member_ids():
    duplicate_payload = json.dumps({
        "evaluator_member_id": "member-1",
        "evaluations": [{
            "evaluated_member_id": "member-1",
            "constraint_compliance": 8,
            "language_quality": 8,
            "completeness": 8,
            "correctness": 8,
            "conciseness": 8,
            "truncation_penalty": 0,
            "quality_warnings": [],
            "strengths": "iyi",
            "weaknesses": "az",
        }],
        "ranking": ["member-1", "member-1"],
    })
    parsed, error = council.parse_peer_evaluation_payload(
        duplicate_payload,
        "member-1",
        ["member-1"],
    )

    assert parsed is None
    assert "duplicate" in error


@pytest.mark.asyncio
async def test_fallback_runs_only_for_temporary_errors(monkeypatch):
    member = {
        "member_id": "member-1",
        "primary_model": "provider/primary",
        "fallback_models": ["provider/fallback"],
    }
    calls = []

    async def temporary_failure_then_success(model, messages, **kwargs):
        calls.append(model)
        if model == "provider/primary":
            return _failed(model, 503)
        return _completed(model, "fallback answer")

    monkeypatch.setattr(council, "query_model_with_retries", temporary_failure_then_success)

    result = await council._query_member_with_fallback(member, [], max_tokens=None)

    assert calls == ["provider/primary", "provider/fallback"]
    assert result["status"] == "completed"
    assert result["actual_model"] == "provider/fallback"
    assert result["used_fallback"] is True


@pytest.mark.asyncio
async def test_fallback_does_not_run_for_auth_or_config_errors(monkeypatch):
    member = {
        "member_id": "member-1",
        "primary_model": "provider/primary",
        "fallback_models": ["provider/fallback"],
    }
    calls = []

    async def permanent_failure(model, messages, **kwargs):
        calls.append(model)
        return _failed(model, 401)

    monkeypatch.setattr(council, "query_model_with_retries", permanent_failure)

    result = await council._query_member_with_fallback(member, [], max_tokens=None)

    assert calls == ["provider/primary"]
    assert result["status"] == "failed"
    assert result["actual_model"] == "provider/primary"
