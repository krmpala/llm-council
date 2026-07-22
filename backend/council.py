"""3-stage LLM Council orchestration."""

import re
from collections import defaultdict
from typing import Awaitable, Callable, List, Dict, Any, Optional, Tuple

from .openrouter import (
    build_model_status,
    query_model,
    query_models_parallel_with_retries,
)
from .config import (
    CHAIRMAN_MODEL,
    COUNCIL_MODELS,
    MIN_SUCCESSFUL_RESPONSES,
    MODEL_MAX_RETRIES,
    MODEL_RETRY_BACKOFF_BASE,
)


StatusCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


def _model_identity(model: str) -> Dict[str, str]:
    provider, _, model_name = model.partition("/")
    return {
        "provider": provider or "unknown",
        "model_name": model_name or model,
    }


def _successful_stage1_results(
    model_results: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    results = []
    for model in COUNCIL_MODELS:
        model_result = model_results.get(model)
        if not model_result or model_result.get("status") != "completed":
            continue

        response = model_result.get("response") or {}
        identity = _model_identity(model)
        results.append({
            "model": model,
            "provider": identity["provider"],
            "model_name": identity["model_name"],
            "response": response.get("content", ""),
        })
    return results


def _stage1_statuses(model_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    statuses = []
    for model in COUNCIL_MODELS:
        statuses.append(model_results.get(model) or build_model_status(model, "pending"))
    return statuses


def _stage1_summary(successful_count: int, total_count: int) -> Dict[str, Any]:
    failed_count = total_count - successful_count
    blocked = successful_count < MIN_SUCCESSFUL_RESPONSES
    requires_continue = failed_count > 0 and not blocked

    if blocked:
        message = (
            f"At least {MIN_SUCCESSFUL_RESPONSES} successful independent "
            f"responses are required before Stage 2 and Stage 3 can start."
        )
    elif requires_continue:
        message = "Some models failed after retries. The council can continue with the remaining members."
    else:
        message = "All required council responses were collected."

    return {
        "successful": successful_count,
        "failed": failed_count,
        "total": total_count,
        "minimum_required": MIN_SUCCESSFUL_RESPONSES,
        "blocked": blocked,
        "can_continue": successful_count >= MIN_SUCCESSFUL_RESPONSES,
        "requires_continue": requires_continue,
        "message": message,
    }


def _has_turkish_signal(text: str) -> bool:
    lowered = text.lower()
    turkish_chars = set("çğıöşüİ")
    common_words = {
        "bir", "ve", "ile", "için", "nedir", "nasıl", "neden", "hangi",
        "mı", "mi", "mu", "mü", "değil", "lütfen", "cevap", "kısa",
    }
    words = set(re.findall(r"\b[\wçğıöşüİ]+\b", lowered, re.IGNORECASE))
    return any(char in text for char in turkish_chars) or len(words & common_words) >= 2


def _language_instruction(user_query: str) -> str:
    if _has_turkish_signal(user_query):
        return (
            "Kullanıcının dili Türkçe görünüyor. Yanıtını doğal, akıcı ve "
            "bozuk İngilizce karışımı kullanmadan Türkçe ver."
        )
    return "Answer in the same language as the user unless they explicitly request another language."


def _length_instruction(user_query: str) -> str:
    lowered = user_query.lower()
    explicit_length_markers = [
        "kelime", "paragraf", "sayfa", "madde", "uzun", "detaylı",
        "kapsamlı", "short", "brief", "words", "paragraphs", "detailed",
    ]
    if any(marker in lowered for marker in explicit_length_markers):
        return (
            "Kullanıcının istediği uzunluk ve ayrıntı düzeyine uy. Açık bir "
            "uzunluk isteği varsa onu önceliklendir."
        )
    return "Basit sorularda nihai cevap 500 kelimeyi geçmesin."


def validate_council_configuration(models: Optional[List[str]] = None) -> List[str]:
    """Return configuration errors that would make the council ambiguous."""
    models = models or COUNCIL_MODELS
    errors = []
    normalized_routes = []
    normalized_model_names = []

    for model in models:
        normalized = model.strip().lower()
        if normalized == "openrouter/free":
            errors.append("openrouter/free cannot be used as a council member.")

        provider, _, model_name = normalized.partition("/")
        model_core = model_name.split(":")[0]
        normalized_routes.append(normalized)
        normalized_model_names.append(model_core or provider)

    duplicate_routes = sorted({
        route for route in normalized_routes if normalized_routes.count(route) > 1
    })
    duplicate_models = sorted({
        name for name in normalized_model_names if normalized_model_names.count(name) > 1
    })

    if duplicate_routes:
        errors.append(f"Duplicate council model route(s): {', '.join(duplicate_routes)}.")
    if duplicate_models:
        errors.append(f"Duplicate council model name(s): {', '.join(duplicate_models)}.")

    return errors


def _stage1_messages(user_query: str) -> List[Dict[str, str]]:
    system_prompt = "\n".join([
        "You are an independent council member. Answer the user's question directly.",
        "Do not mention the council process or other models.",
        _language_instruction(user_query),
    ])
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]


async def stage1_collect_responses_detailed(
    user_query: str,
    status_callback: StatusCallback = None,
) -> Dict[str, Any]:
    """
    Stage 1: Collect individual responses from all council models with status.
    """
    config_errors = validate_council_configuration()
    if config_errors:
        statuses = []
        for model in COUNCIL_MODELS:
            statuses.append(build_model_status(
                model,
                "failed",
                error={
                    "http_status": None,
                    "message": " ".join(config_errors),
                    "type": "configuration_error",
                },
            ))
        return {
            "responses": [],
            "statuses": statuses,
            "summary": _stage1_summary(0, len(COUNCIL_MODELS)),
            "configuration_errors": config_errors,
        }

    model_results = await query_models_parallel_with_retries(
        COUNCIL_MODELS,
        _stage1_messages(user_query),
        max_retries=MODEL_MAX_RETRIES,
        backoff_base=MODEL_RETRY_BACKOFF_BASE,
        status_callback=status_callback,
    )

    responses = _successful_stage1_results(model_results)
    statuses = _stage1_statuses(model_results)
    summary = _stage1_summary(len(responses), len(COUNCIL_MODELS))

    return {
        "responses": responses,
        "statuses": statuses,
        "summary": summary,
        "configuration_errors": [],
    }


async def stage1_collect_responses(user_query: str) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.

    This keeps the original return shape for callers that only need successful
    responses.
    """
    detailed = await stage1_collect_responses_detailed(user_query)
    return detailed["responses"]


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each successful Stage 1 model ranks the anonymized responses.
    """
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    label_to_model = {
        f"Response {label}": result["model"]
        for label, result in zip(labels, stage1_results)
    }

    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

{_language_instruction(user_query)}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]
    reviewer_models = [result["model"] for result in stage1_results]
    responses = await query_models_parallel_with_retries(
        reviewer_models,
        messages,
        max_retries=MODEL_MAX_RETRIES,
        backoff_base=MODEL_RETRY_BACKOFF_BASE,
    )

    stage2_results = []
    for model, model_result in responses.items():
        if model_result.get("status") == "completed":
            response = model_result.get("response") or {}
            full_text = response.get("content", "")
            parsed = parse_ranking_from_text(full_text)
            identity = _model_identity(model)
            stage2_results.append({
                "model": model,
                "provider": identity["provider"],
                "model_name": identity["model_name"],
                "ranking": full_text,
                "parsed_ranking": parsed,
            })

    return stage2_results, label_to_model


def _stage3_context(
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
) -> Tuple[str, str]:
    stage1_text = "\n\n".join([
        f"Model: {result['provider']} / {result['model_name']}\n"
        f"Answer:\n{result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Reviewer: {result['provider']} / {result['model_name']}\n"
        f"Evaluation:\n{result['ranking']}"
        for result in stage2_results
    ])
    return stage1_text, stage2_text


async def _quality_control_final_response(
    user_query: str,
    response_text: str,
) -> str:
    qc_prompt = f"""Aşağıdaki nihai yanıtı yalnızca dil, anlam tutarlılığı, yazım ve tekrar bakımından düzelt.

Yeni bilgi, sayı, oran, tarih, kaynak, araştırma sonucu veya yorum ekleme.
Başlık yapısını koru.
Kullanıcının dili Türkçeyse doğal Türkçe kullan; gereksiz İngilizce terimleri temizle.

Kullanıcının sorusu:
{user_query}

Düzeltilecek yanıt:
{response_text}

Düzeltilmiş nihai yanıt:"""

    response = await query_model(CHAIRMAN_MODEL, [{"role": "user", "content": qc_prompt}])
    if response is None:
        return response_text
    return response.get("content", response_text) or response_text


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.
    """
    stage1_text, stage2_text = _stage3_context(stage1_results, stage2_results)

    chairman_prompt = f"""You are the Chairman of an LLM Council.

Your job is only to combine and reconcile the supplied answers into one final answer to the user's original question.

Original question:
{user_query}

Individual answers:
{stage1_text}

Peer evaluations for internal context:
{stage2_text}

Instructions:
- Do not mention "Response A", "Response B", rankings, juries, votes, peer review, or the council process unless the user explicitly asked for process details.
- Do not invent numbers, percentages, dates, citations, studies, or research findings that are not present in the supplied answers or the user's question.
- Prefer clear wording over jargon. Avoid broken Turkish, unnecessary English terms, and meaningless filler.
- {_language_instruction(user_query)}
- {_length_instruction(user_query)}
- If the supplied answers do not support a factual claim, state the uncertainty instead of filling the gap.

Use exactly this format:

## Kısa ortak cevap

## Modellerin ortaklaştığı noktalar

## Önemli görüş ayrılıkları

## Güven düzeyi

Final answer:"""

    messages = [{"role": "user", "content": chairman_prompt}]
    response = await query_model(CHAIRMAN_MODEL, messages)
    identity = _model_identity(CHAIRMAN_MODEL)

    if response is None:
        return {
            "model": CHAIRMAN_MODEL,
            "provider": identity["provider"],
            "model_name": identity["model_name"],
            "response": "Error: Unable to generate final synthesis.",
        }

    response_text = response.get("content", "")
    checked_text = await _quality_control_final_response(user_query, response_text)

    return {
        "model": CHAIRMAN_MODEL,
        "provider": identity["provider"],
        "model_name": identity["model_name"],
        "response": checked_text,
    }


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.
    """
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
    """
    Calculate aggregate rankings across all models.
    """
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        parsed_ranking = ranking.get("parsed_ranking") or parse_ranking_from_text(
            ranking.get("ranking", "")
        )

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            identity = _model_identity(model)
            aggregate.append({
                "model": model,
                "provider": identity["provider"],
                "model_name": identity["model_name"],
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions),
            })

    aggregate.sort(key=lambda x: x["average_rank"])
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]
    response = await query_model("openrouter/free", messages, timeout=60.0)

    if response is None:
        return "New Conversation"

    title = response.get("content", "New Conversation").strip()
    title = title.strip('"\'')

    if len(title) > 50:
        title = title[:47] + "..."

    return title


def build_metadata(
    label_to_model: Optional[Dict[str, str]] = None,
    aggregate_rankings: Optional[List[Dict[str, Any]]] = None,
    stage1_statuses: Optional[List[Dict[str, Any]]] = None,
    stage1_summary: Optional[Dict[str, Any]] = None,
    requires_continue: bool = False,
) -> Dict[str, Any]:
    return {
        "label_to_model": label_to_model or {},
        "aggregate_rankings": aggregate_rankings or [],
        "stage1_statuses": stage1_statuses or [],
        "stage1_summary": stage1_summary or {},
        "requires_continue": requires_continue,
        "chairman_model": CHAIRMAN_MODEL,
        "chairman": {
            "model": CHAIRMAN_MODEL,
            **_model_identity(CHAIRMAN_MODEL),
        },
    }


async def run_remaining_council(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage1_statuses: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    if len(stage1_results) < MIN_SUCCESSFUL_RESPONSES:
        summary = _stage1_summary(len(stage1_results), len(COUNCIL_MODELS))
        return [], {
            "model": "error",
            "provider": "system",
            "model_name": "minimum-participant-check",
            "response": summary["message"],
        }, build_metadata(
            stage1_statuses=stage1_statuses,
            stage1_summary=summary,
            requires_continue=False,
        )

    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results)
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
    )
    summary = _stage1_summary(len(stage1_results), len(COUNCIL_MODELS))
    metadata = build_metadata(
        label_to_model=label_to_model,
        aggregate_rankings=aggregate_rankings,
        stage1_statuses=stage1_statuses,
        stage1_summary=summary,
        requires_continue=False,
    )
    return stage2_results, stage3_result, metadata


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage council process.
    """
    stage1_details = await stage1_collect_responses_detailed(user_query)
    stage1_results = stage1_details["responses"]
    summary = stage1_details["summary"]

    if summary["blocked"]:
        metadata = build_metadata(
            stage1_statuses=stage1_details["statuses"],
            stage1_summary=summary,
        )
        return stage1_results, [], {
            "model": "error",
            "provider": "system",
            "model_name": "minimum-participant-check",
            "response": summary["message"],
        }, metadata

    stage2_results, stage3_result, metadata = await run_remaining_council(
        user_query,
        stage1_results,
        stage1_statuses=stage1_details["statuses"],
    )

    return stage1_results, stage2_results, stage3_result, metadata
