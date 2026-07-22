"""3-stage LLM Council orchestration."""

import json
import re
import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable, List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field, ValidationError, field_validator

from .openrouter import (
    build_model_status,
    is_retryable_error,
    query_model,
    query_model_with_retries,
)
from .language_quality import (
    evaluate_response_quality,
    language_instruction as target_language_instruction,
    normalize_for_matching,
    resolve_requested_language,
)
from .config import (
    CHAIRMAN_BACKOFF_SECONDS,
    CHAIRMAN_FALLBACK_MODELS,
    CHAIRMAN_MODEL,
    CHAIRMAN_PRIMARY_MODEL,
    CHAIRMAN_RETRY_COUNT,
    CONTINUE_MIN_SUCCESSFUL_RESPONSES,
    COUNCIL_MEMBERS,
    MIN_SUCCESSFUL_RESPONSES,
    MODEL_MAX_RETRIES,
    MODEL_RETRY_BACKOFF_BASE,
    STAGE2_EVALUATOR_TIMEOUT_SECONDS,
    STAGE2_REQUEST_TIMEOUT_SECONDS,
)


StatusCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


class PeerEvaluationItem(BaseModel):
    evaluated_member_id: str
    constraint_compliance: int = Field(ge=0, le=10)
    language_quality: int = Field(ge=0, le=10)
    completeness: int = Field(ge=0, le=10)
    correctness: int = Field(ge=0, le=10)
    conciseness: int = Field(ge=0, le=10)
    truncation_penalty: int = Field(ge=0, le=10)
    quality_warnings: List[str] = []
    strengths: str
    weaknesses: str


class PeerEvaluationPayload(BaseModel):
    evaluator_member_id: str
    evaluations: List[PeerEvaluationItem]
    ranking: List[str]

    @field_validator("ranking")
    @classmethod
    def ranking_has_unique_ids(cls, ranking):
        if len(ranking) != len(set(ranking)):
            raise ValueError("ranking contains duplicate member_id values")
        return ranking


def _model_identity(model: str) -> Dict[str, str]:
    provider, _, model_name = model.partition("/")
    return {
        "provider": provider or "unknown",
        "model_name": model_name or model,
    }


def _display_name(model: str) -> str:
    identity = _model_identity(model)
    return f"{identity['provider']} / {identity['model_name']}"


def _warn_invariant(message: str) -> None:
    print(f"[llm-council invariant] {message}")


def unique_by_member_id(items: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    """Dedupe payloads by member_id without falling back to indexes."""
    seen = set()
    unique = []
    for item in items:
        member_id = item.get("member_id")
        if not member_id:
            _warn_invariant(f"{label} item missing member_id; dropped")
            continue
        if member_id in seen:
            _warn_invariant(f"duplicate member_id in {label}: {member_id}; dropped")
            continue
        seen.add(member_id)
        unique.append(item)
    return unique


def validate_stage_invariants(
    responses: List[Dict[str, Any]],
    statuses: List[Dict[str, Any]],
    stage2_results: Optional[List[Dict[str, Any]]] = None,
) -> None:
    response_ids = [response.get("member_id") for response in responses]
    if len(response_ids) != len(set(response_ids)):
        _warn_invariant("successful response count differs from unique member_id count")
    failed_ids = {
        status.get("member_id")
        for status in statuses
        if status.get("status") == "failed"
    }
    overlap = failed_ids & set(response_ids)
    if overlap:
        _warn_invariant(f"failed member_id present in successful responses: {sorted(overlap)}")
    if stage2_results is not None:
        evaluator_ids = [result.get("member_id") for result in stage2_results]
        if len(evaluator_ids) != len(set(evaluator_ids)):
            _warn_invariant("peer evaluator count differs from unique member_id count")


def _normalize_model_route(model: str) -> str:
    return model.strip().lower()


def _normalize_model_core(model: str) -> str:
    normalized = _normalize_model_route(model)
    provider, _, model_name = normalized.partition("/")
    return model_name.split(":")[0] or provider


def get_council_members() -> List[Dict[str, Any]]:
    """Return normalized council seats with stable member_id values."""
    members = []
    for index, member in enumerate(COUNCIL_MEMBERS, start=1):
        primary_model = member.get("primary_model") or member.get("model")
        member_id = member.get("member_id") or f"member-{index}"
        fallback_models = member.get("fallback_models") or []
        members.append({
            "member_id": member_id,
            "primary_model": primary_model,
            "fallback_models": list(fallback_models),
        })
    return members


def _member_status_payload(
    member: Dict[str, Any],
    actual_model: Optional[str],
    status: str,
    attempts: int = 0,
    response: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
    used_fallback: bool = False,
    fallback_index: Optional[int] = None,
    attempted_models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    model = actual_model or member["primary_model"]
    identity = _model_identity(model)
    payload = build_model_status(
        model,
        status,
        attempts=attempts,
        response=response,
        error=error,
        member_id=member["member_id"],
        primary_model=member["primary_model"],
        actual_model=actual_model,
        used_fallback=used_fallback,
        fallback_index=fallback_index if used_fallback else None,
        fallback_models=member.get("fallback_models", []),
        attempted_models=attempted_models or [],
        display_name=_display_name(model),
    )
    payload["provider"] = identity["provider"]
    payload["model_name"] = identity["model_name"]
    return payload


def pending_member_statuses() -> List[Dict[str, Any]]:
    return [
        _member_status_payload(member, None, "pending")
        for member in get_council_members()
    ]


def _successful_stage1_results(
    member_results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    results = []
    for member in get_council_members():
        member_result = member_results.get(member["member_id"])
        if not member_result or member_result.get("status") != "completed":
            continue

        response = member_result.get("response") or {}
        actual_model = member_result.get("actual_model") or member_result["model"]
        identity = _model_identity(actual_model)
        results.append({
            "member_id": member["member_id"],
            "primary_model": member["primary_model"],
            "model": actual_model,
            "actual_model": actual_model,
            "provider": identity["provider"],
            "model_name": identity["model_name"],
            "display_name": _display_name(actual_model),
            "used_fallback": member_result.get("used_fallback", False),
            "fallback_index": member_result.get("fallback_index"),
            "response": response.get("content", ""),
            "finish_reason": response.get("finish_reason"),
            "truncated": member_result.get("truncated", response.get("finish_reason") == "length"),
            "was_repaired": member_result.get("was_repaired", False),
            "quality_status": member_result.get("quality_status", "passed"),
            "quality_issues": member_result.get("quality_issues", []),
            "expected_language": member_result.get("expected_language"),
            "detected_primary_language": member_result.get("detected_primary_language"),
            "detected_languages": member_result.get("detected_languages", []),
            "script_distribution": member_result.get("script_distribution", {}),
            "language_confidence": member_result.get("language_confidence"),
            "repair_reason": member_result.get("repair_reason"),
        })
    return results


def _stage1_statuses(member_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    statuses = []
    for member in get_council_members():
        statuses.append(
            member_results.get(member["member_id"])
            or _member_status_payload(member, None, "pending")
        )
    return statuses


def _stage1_summary(
    successful_count: int,
    total_count: int,
    user_override: bool = False,
) -> Dict[str, Any]:
    failed_count = max(total_count - successful_count, 0)
    override_available = (
        failed_count > 0
        and successful_count >= CONTINUE_MIN_SUCCESSFUL_RESPONSES
        and not user_override
    )
    blocked = successful_count < MIN_SUCCESSFUL_RESPONSES and not user_override
    requires_continue = override_available

    if requires_continue:
        message = (
            f"{successful_count} model responded successfully. You can continue "
            f"with the remaining {successful_count} members if you approve."
        )
    elif blocked:
        message = (
            f"At least {MIN_SUCCESSFUL_RESPONSES} successful independent "
            f"responses are required before Stage 2 and Stage 3 can start."
        )
    elif user_override:
        message = (
            f"The council was completed with {successful_count} members after "
            "user approval."
        )
    else:
        message = "All required council responses were collected."

    return {
        "successful": successful_count,
        "failed": failed_count,
        "total": total_count,
        "minimum_required": MIN_SUCCESSFUL_RESPONSES,
        "continue_minimum_required": CONTINUE_MIN_SUCCESSFUL_RESPONSES,
        "blocked": blocked or requires_continue,
        "can_continue": successful_count >= MIN_SUCCESSFUL_RESPONSES or user_override,
        "override_available": override_available,
        "requires_continue": requires_continue,
        "user_override": user_override,
        "continued_with": successful_count if user_override else None,
        "message": message,
    }


def _constraints_text(constraints: Dict[str, Any]) -> str:
    return json.dumps(constraints, ensure_ascii=False, indent=2)


def extract_user_constraints(
    user_query: str,
    conversation_user_messages: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Extract user constraints and resolve a target language for every stage."""
    normalized = normalize_for_matching(user_query)
    language = resolve_requested_language(user_query, conversation_user_messages)

    length_match = re.search(r"(\d+)\s*(kelime|word|worter|woerter|sentence|cumle|paragraph|paragraf)", normalized)
    explicit_word_limit = int(length_match.group(1)) if length_match else None
    if explicit_word_limit:
        requested_length = "explicit"
    elif any(marker in normalized for marker in ("cok kisa", "very short", "sehr kurz", "tres court")):
        requested_length = "very_short"
    elif any(marker in normalized for marker in ("kisaca", "kisa", "ozet", "brief", "short", "concise", "kurz")):
        requested_length = "short"
    elif any(marker in normalized for marker in ("detayli", "kapsamli", "detailed", "comprehensive", "ausfuhrlich", "ausfuehrlich", "long")):
        requested_length = "detailed"
    else:
        requested_length = "unrestricted"

    budget_match = re.search(
        r"(\$|eur|tl|usd|lira)\s?\d[\d.,]*|\d[\d.,]*\s?(\$|eur|tl|usd|lira)",
        normalized,
    )
    geography_match = re.search(
        r"\b(turkiye|turkey|istanbul|ankara|izmir|abd|usa|united states|avrupa|europe)\b",
        normalized,
    )
    format_markers = [
        marker for marker in ("json", "tablo", "table", "liste", "list", "madde", "markdown", "csv")
        if marker in normalized
    ]
    exclusions = []
    for pattern in (
        r"([^.!?\n]+)\s+istemiyorum",
        r"([^.!?\n]+)\s+olmasin",
        r"without\s+([^.!?\n]+)",
        r"exclude\s+([^.!?\n]+)",
        r"ohne\s+([^.!?\n]+)",
    ):
        exclusions.extend(match.strip() for match in re.findall(pattern, normalized))

    return {
        **language,
        "requested_length": requested_length,
        "explicit_word_limit": explicit_word_limit,
        "budget_constraint": budget_match.group(0) if budget_match else None,
        "geography": geography_match.group(0) if geography_match else None,
        "requested_format": ", ".join(format_markers) if format_markers else None,
        "explicit_exclusions": exclusions,
    }


def _language_instruction(user_query: str, constraints: Optional[Dict[str, Any]] = None) -> str:
    del user_query
    constraints = constraints or {}
    instruction = target_language_instruction(constraints)
    if constraints.get("requested_language_code") == "tr":
        return (
            "If the user language is Turkish, write natural Turkish and avoid broken English mixtures. "
            f"{instruction}"
        )
    return instruction


def _length_instruction(user_query: str, constraints: Optional[Dict[str, Any]] = None) -> str:
    del user_query
    constraints = constraints or {}
    requested_length = constraints.get("requested_length")
    is_turkish = constraints.get("requested_language_code") == "tr"
    if requested_length == "very_short":
        if is_turkish:
            return "En fazla 120 kelime kullan ve tam bir kapanış cümlesiyle bitir."
        return "Use at most 120 words and end with a complete closing sentence."
    if requested_length == "short":
        if is_turkish:
            return "En fazla 250 kelime kullan. Gereksiz ayrıntı ekleme ve cümle ortasında bırakma."
        return "Use at most 250 words. Avoid unnecessary detail and do not stop mid-sentence."
    if requested_length == "medium":
        if is_turkish:
            return "Yaklaşık 400-600 kelime kullan."
        return "Use approximately 400 to 600 words."
    if requested_length == "detailed":
        if is_turkish:
            return "Gerekçeli ve kapsamlı yanıt ver."
        return "Provide a reasoned and thorough answer."
    if requested_length == "explicit" and constraints.get("explicit_word_limit"):
        if is_turkish:
            return f"En fazla {constraints['explicit_word_limit']} kelime kullan."
        return f"Use at most {constraints['explicit_word_limit']} words."
    if is_turkish:
        return "Basit sorularda nihai cevap 500 kelimeyi geçmesin."
    return "Keep the final answer below 500 words for simple questions."


def _max_tokens_for_constraints(constraints: Dict[str, Any]) -> Optional[int]:
    requested_length = constraints.get("requested_length")
    if requested_length == "very_short":
        return 512
    if requested_length == "short":
        return 900
    if requested_length == "medium":
        return 1800
    if requested_length == "explicit" and constraints.get("explicit_word_limit"):
        return max(512, int(constraints["explicit_word_limit"] * 4))
    return None


def validate_council_configuration(
    members: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Return configuration errors that would make the council ambiguous."""
    members = members or get_council_members()
    errors = []
    member_ids = [member["member_id"] for member in members]
    if len(member_ids) != len(set(member_ids)):
        errors.append("Council member_id values must be unique.")

    model_to_members = defaultdict(list)
    model_core_to_members = defaultdict(list)
    for member in members:
        candidates = [member["primary_model"], *member.get("fallback_models", [])]
        for model in candidates:
            normalized = _normalize_model_route(model)
            if normalized == "openrouter/free":
                errors.append("openrouter/free cannot be used as a council member or fallback.")
            model_to_members[normalized].append(member["member_id"])
            model_core_to_members[_normalize_model_core(model)].append(member["member_id"])

    duplicate_routes = sorted(
        model for model, owners in model_to_members.items()
        if len(set(owners)) > 1
    )
    duplicate_cores = sorted(
        model for model, owners in model_core_to_members.items()
        if len(set(owners)) > 1
    )

    if duplicate_routes:
        errors.append(f"Duplicate council model route(s): {', '.join(duplicate_routes)}.")
    if duplicate_cores:
        errors.append(f"Duplicate council model name(s): {', '.join(duplicate_cores)}.")

    return errors


def _stage1_messages(user_query: str, constraints: Dict[str, Any]) -> List[Dict[str, str]]:
    system_prompt = "\n".join([
        "You are an independent council member. Answer the user's question directly.",
        "Do not mention the council process or other models.",
        (
            "Kullanıcının uzunluk, dil, bütçe, ülke, yaş, biçim, tarih ve diğer "
            "açık kısıtlarını zorunlu kabul et. Kullanıcı kısa cevap istiyorsa "
            "gereksiz ayrıntı ekleme."
        ),
        _language_instruction(user_query, constraints),
        _length_instruction(user_query, constraints),
        "If you are close to the output limit, finish with a short complete closing sentence.",
        "Structured user constraints:",
        _constraints_text(constraints),
    ])
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]


async def _query_member_with_fallback(
    member: Dict[str, Any],
    messages: List[Dict[str, str]],
    max_tokens: Optional[int],
    status_callback: StatusCallback = None,
) -> Dict[str, Any]:
    candidate_models = [member["primary_model"], *member.get("fallback_models", [])]
    attempted_models = []
    last_result = None
    total_attempts = 0

    for index, model in enumerate(candidate_models):
        attempted_models.append(model)
        used_fallback = index > 0

        async def wrapped_callback(status):
            payload = _member_status_payload(
                member,
                model,
                status["status"],
                attempts=status.get("attempts", 0),
                response=status.get("response"),
                error=status.get("error"),
                used_fallback=used_fallback,
                fallback_index=index if used_fallback else None,
                attempted_models=list(attempted_models),
            )
            if status_callback:
                await status_callback(payload)

        result = await query_model_with_retries(
            model,
            messages,
            max_retries=MODEL_MAX_RETRIES,
            backoff_base=MODEL_RETRY_BACKOFF_BASE,
            max_tokens=max_tokens,
            status_callback=wrapped_callback,
        )
        last_result = result
        total_attempts += result.get("attempts", 0)

        if result.get("status") == "completed":
            return _member_status_payload(
                member,
                model,
                "completed",
                attempts=total_attempts,
                response=result.get("response"),
                used_fallback=used_fallback,
                fallback_index=index if used_fallback else None,
                attempted_models=attempted_models,
            )

        if not is_retryable_error(result.get("error")):
            break

    failed_model = attempted_models[-1] if attempted_models else member["primary_model"]
    return _member_status_payload(
        member,
        failed_model,
        "failed",
        attempts=total_attempts,
        error=(last_result or {}).get("error") or {
            "http_status": None,
            "message": "Model request failed without a usable response.",
            "type": "network_error",
            "retryable": True,
        },
        used_fallback=failed_model != member["primary_model"],
        fallback_index=(len(attempted_models) - 1) if len(attempted_models) > 1 else None,
        attempted_models=attempted_models,
    )


def evaluate_language_quality(text: str, constraints: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic lightweight quality gate for Stage 1 outputs."""
    issues = []
    detected = []
    if re.search(r"[\u0400-\u04FF]", text):
        issues.append("contains_cyrillic")
        detected.append("cyrillic")
    if re.search(r"[\u4E00-\u9FFF]", text):
        issues.append("contains_cjk")
        detected.append("cjk")
    if re.search(r"(.)\1{8,}", text):
        issues.append("excessive_character_repetition")
    if re.search(r"\|[^\n]*\n[^\n]*\|[^\n]*$", text) and not text.rstrip().endswith("|"):
        issues.append("possibly_broken_table")

    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", text)
    ascii_words = [word for word in words if re.fullmatch(r"[A-Za-z]+", word)]
    turkish_chars = re.findall(r"[ÇĞİÖŞÜçğıöşü]", text)
    if constraints.get("requested_language") == "Turkish":
        if len(ascii_words) > 35 and len(turkish_chars) < 8:
            issues.append("mostly_non_turkish")
            detected.append("english")
        if len(set(detected)) >= 2:
            issues.append("mixed_scripts")

    status = "failed" if any(
        issue in issues
        for issue in ["contains_cyrillic", "contains_cjk", "mostly_non_turkish", "excessive_character_repetition"]
    ) else "warning" if issues else "passed"
    return {
        "quality_status": status,
        "detected_languages": sorted(set(detected or ["unknown"])),
        "quality_issues": issues,
    }


def evaluate_language_quality(text: str, constraints: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility entry point for the language-aware response quality gate."""
    return evaluate_response_quality(text, constraints)


def _apply_quality_metadata(result: Dict[str, Any], quality: Dict[str, Any]) -> None:
    for key in (
        "expected_language",
        "expected_language_name",
        "detected_primary_language",
        "detected_languages",
        "script_distribution",
        "language_confidence",
        "quality_status",
        "quality_issues",
    ):
        result[key] = quality.get(key)


def _quality_validation_failure(
    result: Dict[str, Any],
    quality: Dict[str, Any],
    repair_error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Prevent unusable Stage 1 content from becoming a council response."""
    _apply_quality_metadata(result, {**quality, "quality_status": "failed"})
    result["status"] = "failed"
    result["error"] = {
        "http_status": (repair_error or {}).get("http_status"),
        "type": "quality_validation_failed",
        "message": "Model hedef dilde kullanilabilir bir yanit uretemedi.",
        "provider_error_code": (repair_error or {}).get("provider_error_code"),
        "provider_error_message": (repair_error or {}).get("provider_error_message"),
        "retryable": False,
        "raw_response_preview": (repair_error or {}).get("raw_response_preview"),
    }
    return result


async def _repair_member_response_if_needed(
    member: Dict[str, Any],
    messages: List[Dict[str, str]],
    constraints: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    if result.get("status") != "completed":
        return result

    response = result.get("response") or {}
    text = response.get("content") or ""
    quality = evaluate_language_quality(text, constraints)
    needs_truncation_repair = response.get("finish_reason") == "length"
    needs_language_repair = quality["quality_status"] == "failed"
    if not needs_truncation_repair and not needs_language_repair:
        _apply_quality_metadata(result, quality)
        return result

    if needs_truncation_repair:
        repair_instruction = (
            "Önceki cevap token sınırında kesildi. Kullanıcının tüm kısıtlarını "
            "koruyarak cevabı baştan, eksiksiz ve belirtilen kelime sınırında "
            "yeniden yaz. Önceki metni devam ettirme."
        )
    else:
        repair_instruction = (
            "Cevabını tamamen düzgün Türkçe ile, bozuk yabancı kelimeler ve tablo "
            "kullanmadan, en fazla 250 kelimeyle baştan yaz."
        )

    repair_reason = "truncation_and_quality" if needs_truncation_repair and needs_language_repair else (
        "truncation" if needs_truncation_repair else "language_quality"
    )
    language_name = constraints.get("requested_language_name") or constraints.get("requested_language") or "English"
    repair_instruction = (
        "The previous answer failed a language or text-quality check. Rewrite the answer from "
        f"scratch while preserving the original question and every constraint. Produce the entire answer in {language_name}. "
        "Do not continue the previous text. Do not use gibberish, mixed-language explanations, broken tables, "
        "or unfinished sentences. "
        + _length_instruction("", constraints)
    )
    repair_messages = [*messages, {"role": "user", "content": repair_instruction}]
    repaired = await query_model_with_retries(
        result.get("actual_model") or result.get("model"),
        repair_messages,
        max_retries=0,
        backoff_base=MODEL_RETRY_BACKOFF_BASE,
        max_tokens=_max_tokens_for_constraints(constraints),
    )
    if repaired.get("status") == "completed":
        repaired_response = repaired.get("response") or {}
        repaired_quality = evaluate_language_quality(repaired_response.get("content") or "", constraints)
        result["response"] = repaired_response
        result["attempts"] = result.get("attempts", 0) + repaired.get("attempts", 0)
        result["was_repaired"] = repaired_response.get("finish_reason") != "length"
        result["truncated"] = repaired_response.get("finish_reason") == "length"
        repaired_quality = {
            **repaired_quality,
            "quality_status": "failed" if result["truncated"] else repaired_quality["quality_status"],
        }
        _apply_quality_metadata(result, repaired_quality)
        result["repair_reason"] = repair_reason
        if repaired_quality["quality_status"] == "failed":
            return _quality_validation_failure(result, repaired_quality)
        return result

    result["was_repaired"] = False
    result["truncated"] = needs_truncation_repair
    failed_quality = {
        **quality,
        "quality_status": "failed",
        "quality_issues": [*quality["quality_issues"], "repair_failed"],
    }
    result["repair_reason"] = repair_reason
    result["attempts"] = result.get("attempts", 0) + repaired.get("attempts", 0)
    return _quality_validation_failure(result, failed_quality, repaired.get("error"))


async def stage1_collect_responses_detailed(
    user_query: str,
    status_callback: StatusCallback = None,
    conversation_user_messages: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Stage 1: Collect individual responses from all council members with status.
    """
    members = get_council_members()
    constraints = extract_user_constraints(user_query, conversation_user_messages)
    config_errors = validate_council_configuration(members)
    if config_errors:
        statuses = [
            _member_status_payload(
                member,
                member["primary_model"],
                "failed",
                error={
                    "http_status": None,
                    "message": " ".join(config_errors),
                    "type": "configuration_error",
                },
            )
            for member in members
        ]
        return {
            "responses": [],
            "statuses": statuses,
            "summary": _stage1_summary(0, len(members)),
            "constraints": constraints,
            "configuration_errors": config_errors,
        }

    import asyncio

    messages = _stage1_messages(user_query, constraints)
    max_tokens = _max_tokens_for_constraints(constraints)
    async def run_member(member):
        result = await _query_member_with_fallback(
            member,
            messages,
            max_tokens=max_tokens,
            status_callback=status_callback,
        )
        final_result = await _repair_member_response_if_needed(member, messages, constraints, result)
        if status_callback:
            await status_callback(final_result)
        return final_result

    tasks = [run_member(member) for member in members]
    results = await asyncio.gather(*tasks)
    member_results = {
        result["member_id"]: result
        for result in results
    }

    responses = unique_by_member_id(_successful_stage1_results(member_results), "stage1 responses")
    statuses = _stage1_statuses(member_results)
    validate_stage_invariants(responses, statuses)
    summary = _stage1_summary(len(responses), len(members))

    return {
        "responses": responses,
        "statuses": statuses,
        "summary": summary,
        "constraints": constraints,
        "configuration_errors": [],
    }


async def stage1_collect_responses(
    user_query: str,
    conversation_user_messages: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Keep the original return shape for callers that only need responses."""
    detailed = await stage1_collect_responses_detailed(user_query, conversation_user_messages=conversation_user_messages)
    return detailed["responses"]


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    constraints: Optional[Dict[str, Any]] = None,
    stage2_event_callback: StatusCallback = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, Any]]:
    """Stage 2: Each successful Stage 1 member ranks anonymized responses."""
    constraints = constraints or extract_user_constraints(user_query)
    stage1_results = unique_by_member_id(stage1_results, "stage2 input responses")
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    label_to_model = {
        f"Response {label}": result["member_id"]
        for label, result in zip(labels, stage1_results)
    }

    responses_text = "\n\n".join([
        "\n".join([
            f"Response {label}:",
            f"evaluated_member_id: {result['member_id']}",
            f"truncated: {str(result.get('truncated', False)).lower()}",
            f"quality_status: {result.get('quality_status', 'passed')}",
            f"quality_issues: {', '.join(result.get('quality_issues', []))}",
            "content:",
            result["response"],
        ])
        for label, result in zip(labels, stage1_results)
    ])

    evaluated_ids_json = json.dumps([result["member_id"] for result in stage1_results], ensure_ascii=False)
    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Structured user constraints:
{_constraints_text(constraints)}

Here are the responses from different models (anonymized):

{responses_text}

{_language_instruction(user_query, constraints)}

Return ONLY valid JSON in this exact shape:
{{
  "evaluator_member_id": "__EVALUATOR_MEMBER_ID__",
  "evaluations": [
    {{
      "evaluated_member_id": "member-id",
      "constraint_compliance": 0,
      "language_quality": 0,
      "completeness": 0,
      "correctness": 0,
      "conciseness": 0,
      "truncation_penalty": 0,
      "quality_warnings": [],
      "strengths": "...",
      "weaknesses": "..."
    }}
  ],
  "ranking": ["member-id-1", "member-id-2"]
}}

Rules:
- Use only these evaluated member IDs: {evaluated_ids_json}
- Use member_id values, not model names.
- ranking must include each evaluated member exactly once, with no duplicates or unknown IDs.
- If truncated=true or quality_status is failed/warning, apply a serious penalty and mention it in quality_warnings.
- {_language_instruction(user_query, constraints)} JSON keys stay in English, but every explanatory value such as strengths, weaknesses, and quality_warnings must use the target language.
- Keep explanations concise."""

    stage2_results = []
    evaluator_statuses = []
    max_tokens = _max_tokens_for_constraints(constraints)

    if stage2_event_callback:
        await stage2_event_callback({"type": "peer_stage_started"})

    async def emit(event):
        if stage2_event_callback:
            await stage2_event_callback(event)

    async def run_evaluator(result):
        started_at = datetime.utcnow().isoformat()
        base_status = {
            "evaluator_member_id": result["member_id"],
            "member_id": result["member_id"],
            "status": "running",
            "attempts": 0,
            "primary_model": result["primary_model"],
            "actual_model": result["actual_model"],
            "started_at": started_at,
            "completed_at": None,
            "http_status": None,
            "error_type": None,
            "error_message": None,
            "raw_output_preview": None,
            "parsed_evaluation": None,
            "fallback_used": result.get("used_fallback", False),
        }
        await emit({"type": "peer_evaluator_started", "data": base_status})
        try:
            messages = [{
                "role": "user",
                "content": ranking_prompt.replace("__EVALUATOR_MEMBER_ID__", result["member_id"]),
            }]
            response = await asyncio.wait_for(
                query_model_with_retries(
                    result["actual_model"],
                    messages,
                    timeout=STAGE2_REQUEST_TIMEOUT_SECONDS,
                    max_retries=MODEL_MAX_RETRIES,
                    backoff_base=MODEL_RETRY_BACKOFF_BASE,
                    max_tokens=max_tokens,
                ),
                timeout=STAGE2_EVALUATOR_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            status = {
                **base_status,
                "status": "failed",
                "completed_at": datetime.utcnow().isoformat(),
                "error_type": "timeout",
                "error_message": "Evaluator timed out",
            }
            await emit({"type": "peer_evaluator_failed", "data": status})
            return None, status
        except Exception as exc:
            status = {
                **base_status,
                "status": "failed",
                "completed_at": datetime.utcnow().isoformat(),
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
            await emit({"type": "peer_evaluator_failed", "data": status})
            return None, status

        if response.get("status") != "completed":
            error = response.get("error") or {}
            status = {
                **base_status,
                "status": "failed",
                "attempts": response.get("attempts", 0),
                "completed_at": datetime.utcnow().isoformat(),
                "http_status": error.get("http_status"),
                "error_type": error.get("type"),
                "error_message": error.get("message"),
            }
            await emit({"type": "peer_evaluator_failed", "data": status})
            return None, status

        full_text = ((response.get("response") or {}).get("content") or "").strip()
        parsed_payload, parse_error = parse_peer_evaluation_payload(
            full_text,
            result["member_id"],
            [stage_result["member_id"] for stage_result in stage1_results],
        )
        if parsed_payload is None:
            repaired_text = await repair_peer_evaluation_json(
                result,
                full_text,
                [stage_result["member_id"] for stage_result in stage1_results],
                constraints,
            )
            parsed_payload, parse_error = parse_peer_evaluation_payload(
                repaired_text or "",
                result["member_id"],
                [stage_result["member_id"] for stage_result in stage1_results],
            )
            full_text = repaired_text or full_text

        if parsed_payload is None:
            status = {
                **base_status,
                "status": "invalid_output",
                "attempts": response.get("attempts", 0),
                "completed_at": datetime.utcnow().isoformat(),
                "raw_output_preview": full_text[:240],
                "error_type": "invalid_json",
                "error_message": parse_error,
            }
            await emit({"type": "peer_evaluator_invalid", "data": status})
            return None, status

        ranking_labels = [
            next(label for label, member_id in label_to_model.items() if member_id == member_id_value)
            for member_id_value in parsed_payload.ranking
        ]
        stage2_result = {
            "member_id": result["member_id"],
            "evaluator_member_id": result["member_id"],
            "primary_model": result["primary_model"],
            "model": result["actual_model"],
            "actual_model": result["actual_model"],
            "provider": result["provider"],
            "model_name": result["model_name"],
            "display_name": result["display_name"],
            "ranking": full_text,
            "parsed_ranking": ranking_labels,
            "parsed_evaluation": parsed_payload.model_dump(),
        }
        status = {
            **base_status,
            "status": "completed",
            "attempts": response.get("attempts", 0),
            "completed_at": datetime.utcnow().isoformat(),
            "raw_output_preview": full_text[:240],
            "parsed_evaluation": parsed_payload.model_dump(),
        }
        await emit({"type": "peer_evaluator_completed", "data": status})
        return stage2_result, status

    gathered = await asyncio.gather(
        *[run_evaluator(result) for result in stage1_results],
        return_exceptions=True,
    )
    for item in gathered:
        if isinstance(item, Exception):
            status = {
                "evaluator_member_id": "unknown",
                "member_id": "unknown",
                "status": "failed",
                "attempts": 0,
                "completed_at": datetime.utcnow().isoformat(),
                "error_type": item.__class__.__name__,
                "error_message": str(item),
            }
            evaluator_statuses.append(status)
            continue
        result_item, status = item
        evaluator_statuses.append(status)
        if result_item:
            stage2_results.append(result_item)

    stage2_results = unique_by_member_id(stage2_results, "stage2 evaluators")
    validate_stage_invariants(stage1_results, [], stage2_results)
    stage_summary_status = "completed" if stage2_results else "blocked"
    stage2_summary = {
        "status": stage_summary_status,
        "completed": len(stage2_results),
        "total": len(stage1_results),
        "terminal": True,
        "low_confidence": 0 < len(stage2_results) < len(stage1_results),
        "requires_user_choice": len(stage2_results) == 0,
    }
    terminal_event = "peer_stage_completed" if stage2_results else "peer_stage_blocked"
    await emit({"type": terminal_event, "data": {"summary": stage2_summary, "statuses": evaluator_statuses}})
    return stage2_results, label_to_model, {
        "peer_evaluator_statuses": evaluator_statuses,
        "peer_stage_summary": stage2_summary,
    }


def _stage3_context(
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
) -> Tuple[str, str]:
    stage1_text = "\n\n".join([
        f"Member: {result['member_id']}\n"
        f"Model: {result['display_name']}\n"
        f"Truncated: {str(result.get('truncated', False)).lower()}\n"
        f"Answer:\n{result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Reviewer member: {result['member_id']}\n"
        f"Reviewer model: {result['display_name']}\n"
        f"Evaluation:\n{result['ranking']}"
        for result in stage2_results
    ])
    return stage1_text, stage2_text


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_peer_evaluation_payload(
    text: str,
    evaluator_member_id: str,
    allowed_member_ids: List[str],
) -> Tuple[Optional[PeerEvaluationPayload], Optional[str]]:
    raw = _extract_json_object(text)
    if raw is None:
        return None, "Output is not valid JSON"
    try:
        payload = PeerEvaluationPayload.model_validate(raw)
    except ValidationError as exc:
        return None, str(exc)
    allowed = set(allowed_member_ids)
    if payload.evaluator_member_id != evaluator_member_id:
        return None, "evaluator_member_id does not match caller"
    evaluation_ids = [item.evaluated_member_id for item in payload.evaluations]
    if set(evaluation_ids) != allowed:
        return None, "evaluations must contain every evaluated member_id exactly once"
    if len(evaluation_ids) != len(set(evaluation_ids)):
        return None, "evaluations contains duplicate member_id values"
    if set(payload.ranking) != allowed:
        return None, "ranking contains unknown or missing member_id values"
    return payload, None


async def repair_peer_evaluation_json(
    evaluator: Dict[str, Any],
    raw_output: str,
    allowed_member_ids: List[str],
    constraints: Dict[str, Any],
) -> Optional[str]:
    repair_prompt = f"""Aşağıdaki değerlendirme çıktısını yeni bilgi eklemeden geçerli JSON olarak yeniden biçimlendir.
Sadece şu member_id değerlerini kullan: {json.dumps(allowed_member_ids, ensure_ascii=False)}
evaluator_member_id şu olmalı: {evaluator['member_id']}
JSON dışında metin yazma.

Bozuk çıktı:
{raw_output[:4000]}"""
    repair_prompt = (
        f"{repair_prompt}\n\n{_language_instruction('', constraints)} "
        "JSON keys remain in English, but explanatory values must use the target language."
    )
    response = await query_model_with_retries(
        evaluator["actual_model"],
        [{"role": "user", "content": repair_prompt}],
        timeout=STAGE2_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
        backoff_base=MODEL_RETRY_BACKOFF_BASE,
        max_tokens=_max_tokens_for_constraints(constraints),
    )
    if response.get("status") != "completed":
        return None
    return ((response.get("response") or {}).get("content") or "").strip()


async def _quality_control_final_response(
    user_query: str,
    response_text: str,
    constraints: Dict[str, Any],
) -> str:
    qc_prompt = f"""Aşağıdaki nihai yanıtı yalnızca dil, anlam tutarlılığı, yazım ve tekrar bakımından düzelt.

Yeni bilgi, sayı, oran, tarih, kaynak, araştırma sonucu veya yorum ekleme.
Başlık yapısını koru.
Kullanıcının açık kısıtlarını koru:
{_constraints_text(constraints)}

Kullanıcının sorusu:
{user_query}

Düzeltilecek yanıt:
{response_text}

Düzeltilmiş nihai yanıt:"""

    response = await query_model(
        CHAIRMAN_MODEL,
        [{"role": "user", "content": qc_prompt}],
        max_tokens=_max_tokens_for_constraints(constraints),
    )
    if response is None:
        return response_text
    return response.get("content", response_text) or response_text


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Stage 3: Chairman synthesizes final response."""
    constraints = constraints or extract_user_constraints(user_query)
    stage1_text, stage2_text = _stage3_context(stage1_results, stage2_results)

    chairman_prompt = f"""You are the Chairman of an LLM Council.

Your job is only to combine and reconcile the supplied successful answers into one final answer to the user's original question.

Original question:
{user_query}

Structured user constraints:
{_constraints_text(constraints)}

Individual successful answers:
{stage1_text}

Peer evaluations for internal context:
{stage2_text}

Instructions:
- Use only the successful answers and their evaluations.
- Do not mention failed models unless the user explicitly asked for process details.
- Do not mention "Response A", "Response B", rankings, juries, votes, peer review, or the council process unless the user explicitly asked for process details.
- Do not invent numbers, percentages, dates, citations, studies, or research findings that are not present in the supplied answers or the user's question.
- Prefer clear wording over jargon. Avoid broken Turkish, unnecessary English terms, and meaningless filler.
- {_language_instruction(user_query, constraints)}
- {_length_instruction(user_query, constraints)}
- If the supplied answers do not support a factual claim, state the uncertainty instead of filling the gap.

Use exactly this format:

## Kısa ortak cevap

## Modellerin ortaklaştığı noktalar

## Önemli görüş ayrılıkları

## Güven düzeyi

Final answer:"""

    response = await query_model(
        CHAIRMAN_MODEL,
        [{"role": "user", "content": chairman_prompt}],
        max_tokens=_max_tokens_for_constraints(constraints),
    )
    identity = _model_identity(CHAIRMAN_MODEL)

    if response is None:
        return {
            "model": CHAIRMAN_MODEL,
            "actual_model": CHAIRMAN_MODEL,
            "provider": identity["provider"],
            "model_name": identity["model_name"],
            "display_name": _display_name(CHAIRMAN_MODEL),
            "response": "Error: Unable to generate final synthesis.",
        }

    response_text = response.get("content", "")
    checked_text = await _quality_control_final_response(user_query, response_text, constraints)

    return {
        "model": CHAIRMAN_MODEL,
        "actual_model": CHAIRMAN_MODEL,
        "provider": identity["provider"],
        "model_name": identity["model_name"],
        "display_name": _display_name(CHAIRMAN_MODEL),
        "response": checked_text,
    }


def _chairman_error_payload(
    stage: str,
    primary_model: str,
    actual_model: str,
    result: Optional[Dict[str, Any]],
    synthesis_text: str = "",
) -> Dict[str, Any]:
    error = (result or {}).get("error") or {}
    identity = _model_identity(actual_model)
    message = error.get("message") or "Unknown chairman error"
    return {
        "status": "failed",
        "stage": stage,
        "model": actual_model,
        "primary_model": primary_model,
        "actual_model": actual_model,
        "provider": identity["provider"],
        "model_name": identity["model_name"],
        "display_name": _display_name(actual_model),
        "attempts": (result or {}).get("attempts", 0),
        "http_status": error.get("http_status"),
        "error_type": error.get("type"),
        "error_message": message,
        "raw_error_preview": message[:240],
        "synthesis_text": synthesis_text,
        "quality_review_status": "not_started" if stage == "synthesis" else "failed",
        "response": synthesis_text or "",
    }


async def _query_chairman_with_fallback(
    messages: List[Dict[str, str]],
    constraints: Dict[str, Any],
    stage: str,
    event_callback: StatusCallback = None,
) -> Dict[str, Any]:
    fallback_models = [
        model for model in CHAIRMAN_FALLBACK_MODELS
        if _normalize_model_route(model) != "openrouter/free"
    ]
    candidate_models = [CHAIRMAN_PRIMARY_MODEL, *fallback_models]
    last_result = None

    for index, model in enumerate(candidate_models):
        if event_callback:
            await event_callback({
                "type": f"chairman_{stage}_started" if index == 0 else f"chairman_{stage}_retrying",
                "stage": stage,
                "actual_model": model,
            })
        result = await query_model_with_retries(
            model,
            messages,
            max_retries=CHAIRMAN_RETRY_COUNT,
            backoff_base=CHAIRMAN_BACKOFF_SECONDS,
            max_tokens=_max_tokens_for_constraints(constraints),
        )
        last_result = result
        if result.get("status") == "completed":
            return {
                **result,
                "primary_model": CHAIRMAN_PRIMARY_MODEL,
                "actual_model": model,
                "used_fallback": index > 0,
            }
        if not is_retryable_error(result.get("error")):
            break

    actual_model = (last_result or {}).get("model") or candidate_models[0]
    return _chairman_error_payload(stage, CHAIRMAN_PRIMARY_MODEL, actual_model, last_result)


def _quality_output_is_usable(text: str) -> bool:
    stripped = (text or "").strip()
    return len(stripped) >= 20 and not stripped.lower().startswith("error:")


def _final_response_format(constraints: Dict[str, Any]) -> str:
    """Keep Chairman section headings in the same language as the answer."""
    language = constraints.get("requested_language_code")
    headings = {
        "tr": [
            "K\u0131sa ortak cevap",
            "Modellerin ortakla\u015ft\u0131\u011f\u0131 noktalar",
            "\u00d6nemli g\u00f6r\u00fc\u015f ayr\u0131l\u0131klar\u0131",
            "G\u00fcven d\u00fczeyi",
        ],
        "en": ["Short common answer", "Points of agreement", "Important disagreements", "Confidence level"],
        "de": ["Kurze gemeinsame Antwort", "Gemeinsame Punkte", "Wichtige Unterschiede", "Vertrauensniveau"],
        "fr": ["Reponse commune courte", "Points d accord", "Desaccords importants", "Niveau de confiance"],
        "es": ["Respuesta comun breve", "Puntos de acuerdo", "Desacuerdos importantes", "Nivel de confianza"],
    }.get(language, ["Short common answer", "Points of agreement", "Important disagreements", "Confidence level"])
    return "\n\n".join(f"## {heading}" for heading in headings)


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    constraints: Optional[Dict[str, Any]] = None,
    event_callback: StatusCallback = None,
) -> Dict[str, Any]:
    """Stage 3: Chairman synthesizes final response with detailed metadata."""
    constraints = constraints or extract_user_constraints(user_query)
    stage1_text, stage2_text = _stage3_context(stage1_results, stage2_results)

    chairman_prompt = f"""You are the Chairman of an LLM Council.

Your job is only to combine and reconcile the supplied successful answers into one final answer to the user's original question.

Original question:
{user_query}

Structured user constraints:
{_constraints_text(constraints)}

Individual successful answers:
{stage1_text}

Peer evaluations for internal context:
{stage2_text}

Instructions:
- User length constraints override model answer length. {_length_instruction(user_query, constraints)}
- Use only the successful answers and their evaluations.
- Do not mention failed models unless the user explicitly asked for process details.
- Do not mention "Response A", "Response B", rankings, juries, votes, peer review, or the council process unless the user explicitly asked for process details.
- Do not invent numbers, percentages, dates, citations, studies, or research findings that are not present in the supplied answers or the user's question.
- {_language_instruction(user_query, constraints)}
- If the supplied answers do not support a factual claim, state the uncertainty instead of filling the gap.

Use exactly this format:

## Kısa ortak cevap

## Modellerin ortaklaştığı noktalar

## Önemli görüş ayrılıkları

## Güven düzeyi

Final answer:"""
    chairman_prompt = re.sub(
        r"Use exactly this format:\n.*?\n\nFinal answer:",
        f"Use exactly this format, with every heading and explanatory sentence in the target language:\n\n"
        f"{_final_response_format(constraints)}\n\nFinal answer:",
        chairman_prompt,
        flags=re.DOTALL,
    )

    synthesis = await _query_chairman_with_fallback(
        [{"role": "user", "content": chairman_prompt}],
        constraints,
        "synthesis",
        event_callback=event_callback,
    )
    if synthesis.get("status") == "failed":
        if event_callback:
            await event_callback({"type": "chairman_failed", "data": synthesis})
        return synthesis

    actual_model = synthesis["actual_model"]
    identity = _model_identity(actual_model)
    synthesis_text = ((synthesis.get("response") or {}).get("content") or "").strip()
    if event_callback:
        await event_callback({"type": "chairman_synthesis_completed", "actual_model": actual_model})

    qc_prompt = f"""Aşağıdaki nihai yanıtı yalnızca dil, anlam tutarlılığı, yazım ve tekrar bakımından düzelt.
Yeni bilgi, sayı, oran, tarih, kaynak, araştırma sonucu veya yorum ekleme.
Kullanıcının açık kısıtlarını koru:
{_constraints_text(constraints)}

Kullanıcının sorusu:
{user_query}

Düzeltilecek yanıt:
{synthesis_text}

Düzeltilmiş nihai yanıt:"""
    qc_prompt = (
        f"{qc_prompt}\n\n{_language_instruction(user_query, constraints)} "
        "Do not change the section structure. Return only the corrected final answer."
    )
    if event_callback:
        await event_callback({"type": "chairman_quality_started", "actual_model": actual_model})
    quality = await _query_chairman_with_fallback(
        [{"role": "user", "content": qc_prompt}],
        constraints,
        "quality",
        event_callback=None,
    )

    quality_status = "completed"
    quality_warning = None
    final_text = synthesis_text
    if quality.get("status") == "completed":
        candidate = ((quality.get("response") or {}).get("content") or "").strip()
        if _quality_output_is_usable(candidate):
            final_text = candidate
        else:
            quality_status = "failed"
            quality_warning = "Dil ve tutarlılık kontrolü tamamlanamadı; ilk sentez gösteriliyor."
    else:
        quality_status = "failed"
        quality_warning = "Dil ve tutarlılık kontrolü tamamlanamadı; ilk sentez gösteriliyor."
        if event_callback:
            await event_callback({"type": "chairman_quality_failed", "data": quality})

    result = {
        "status": "completed",
        "stage": "quality_review" if quality_status == "completed" else "synthesis",
        "model": actual_model,
        "primary_model": CHAIRMAN_PRIMARY_MODEL,
        "actual_model": actual_model,
        "provider": identity["provider"],
        "model_name": identity["model_name"],
        "display_name": _display_name(actual_model),
        "attempts": synthesis.get("attempts", 0),
        "http_status": None,
        "error_type": None,
        "error_message": None,
        "raw_error_preview": None,
        "synthesis_text": synthesis_text,
        "quality_review_status": quality_status,
        "quality_warning": quality_warning,
        "response": final_text,
    }
    if event_callback:
        await event_callback({"type": "chairman_completed", "data": result})
    return result


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """Parse the FINAL RANKING section from the model's response."""
    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            numbered_matches = re.findall(r"\d+\.\s*Response [A-Z]", ranking_section)
            if numbered_matches:
                return [re.search(r"Response [A-Z]", m).group() for m in numbered_matches]

            matches = re.findall(r"Response [A-Z]", ranking_section)
            return matches

    matches = re.findall(r"Response [A-Z]", ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Calculate aggregate rankings across all members."""
    member_positions = defaultdict(list)

    for ranking in stage2_results:
        parsed_ranking = ranking.get("parsed_ranking") or parse_ranking_from_text(
            ranking.get("ranking", "")
        )

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                member_positions[label_to_model[label]].append(position)

    aggregate = []
    for member_id, positions in member_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "member_id": member_id,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions),
            })

    aggregate.sort(key=lambda x: x["average_rank"])
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """Generate a short title for a conversation based on the first message."""
    constraints = extract_user_constraints(user_query)
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""
    title_prompt = (
        f"{title_prompt}\n\n{_language_instruction(user_query, constraints)} "
        "Return only the title in the target language."
    )

    response = await query_model(
        "openrouter/free",
        [{"role": "user", "content": title_prompt}],
        timeout=60.0,
    )

    if response is None:
        return "New Conversation"

    title = response.get("content", "New Conversation").strip().strip('"\'')
    if len(title) > 50:
        title = title[:47] + "..."
    return title


def build_metadata(
    label_to_model: Optional[Dict[str, str]] = None,
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    stage1_statuses: Optional[List[Dict[str, Any]]] = None,
    stage1_summary: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
    requires_continue: bool = False,
    user_override: bool = False,
) -> Dict[str, Any]:
    return {
        "label_to_model": label_to_model or {},
        "aggregate_rankings": aggregate_rankings or [],
        "stage1_statuses": stage1_statuses or [],
        "stage1_summary": stage1_summary or {},
        "constraints": constraints or {},
        "requires_continue": requires_continue,
        "user_override": user_override,
        "chairman_model": CHAIRMAN_MODEL,
        "chairman": {
            "model": CHAIRMAN_MODEL,
            "actual_model": CHAIRMAN_MODEL,
            "display_name": _display_name(CHAIRMAN_MODEL),
            **_model_identity(CHAIRMAN_MODEL),
        },
    }


async def run_remaining_council(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage1_statuses: Optional[List[Dict[str, Any]]] = None,
    user_override: bool = False,
    constraints: Optional[Dict[str, Any]] = None,
    chairman_event_callback: StatusCallback = None,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Dict[str, Any]]:
    constraints = constraints or extract_user_constraints(user_query)
    minimum = CONTINUE_MIN_SUCCESSFUL_RESPONSES if user_override else MIN_SUCCESSFUL_RESPONSES
    if len(stage1_results) < minimum:
        summary = _stage1_summary(
            len(stage1_results),
            len(get_council_members()),
            user_override=False,
        )
        return [], None, build_metadata(
            stage1_statuses=stage1_statuses,
            stage1_summary=summary,
            constraints=constraints,
            requires_continue=summary["requires_continue"],
        )

    stage2_results, label_to_model, stage2_metadata = await stage2_collect_rankings(
        user_query,
        stage1_results,
        constraints=constraints,
    )
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        constraints=constraints,
        event_callback=chairman_event_callback,
    )
    summary = _stage1_summary(
        len(stage1_results),
        len(get_council_members()),
        user_override=user_override,
    )
    metadata = build_metadata(
        label_to_model=label_to_model,
        aggregate_rankings=aggregate_rankings,
        stage1_statuses=stage1_statuses,
        stage1_summary=summary,
        constraints=constraints,
        requires_continue=False,
        user_override=user_override,
    )
    metadata.update(stage2_metadata)
    return stage2_results, stage3_result, metadata


async def run_full_council(user_query: str) -> Tuple[List, List, Optional[Dict], Dict]:
    """Run the complete 3-stage council process when no approval is needed."""
    stage1_details = await stage1_collect_responses_detailed(user_query)
    stage1_results = stage1_details["responses"]
    summary = stage1_details["summary"]
    metadata = build_metadata(
        stage1_statuses=stage1_details["statuses"],
        stage1_summary=summary,
        constraints=stage1_details["constraints"],
        requires_continue=summary["requires_continue"],
    )

    if summary["blocked"]:
        return stage1_results, [], None, metadata

    stage2_results, stage3_result, metadata = await run_remaining_council(
        user_query,
        stage1_results,
        stage1_statuses=stage1_details["statuses"],
        constraints=stage1_details["constraints"],
    )

    return stage1_results, stage2_results, stage3_result, metadata
