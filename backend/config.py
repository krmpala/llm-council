"""Configuration for the LLM Council."""

import json
import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
]

# Optional fallback model mapping. Keep placeholders here and override with
# COUNCIL_FALLBACK_MODELS_JSON for real deployments, for example:
# {"member-3": ["provider/model-a", "provider/model-b"]}
COUNCIL_MODEL_FALLBACKS = {}


def _fallbacks_from_env():
    raw_value = os.getenv("COUNCIL_FALLBACK_MODELS_JSON")
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _build_council_members():
    env_fallbacks = _fallbacks_from_env()
    members = []
    for index, model in enumerate(COUNCIL_MODELS, start=1):
        member_id = f"member-{index}"
        fallback_models = env_fallbacks.get(
            member_id,
            COUNCIL_MODEL_FALLBACKS.get(member_id, []),
        )
        members.append({
            "member_id": member_id,
            "primary_model": model,
            "fallback_models": fallback_models,
        })
    return members


COUNCIL_MEMBERS = _build_council_members()

# Chairman model - synthesizes final response. CHAIRMAN_MODEL remains as a
# backward-compatible alias for the primary model.
CHAIRMAN_PRIMARY_MODEL = os.getenv(
    "CHAIRMAN_PRIMARY_MODEL",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)
CHAIRMAN_MODEL = CHAIRMAN_PRIMARY_MODEL
try:
    CHAIRMAN_FALLBACK_MODELS = json.loads(os.getenv("CHAIRMAN_FALLBACK_MODELS_JSON", "[]"))
    if not isinstance(CHAIRMAN_FALLBACK_MODELS, list):
        CHAIRMAN_FALLBACK_MODELS = []
except json.JSONDecodeError:
    CHAIRMAN_FALLBACK_MODELS = []
CHAIRMAN_RETRY_COUNT = int(os.getenv("CHAIRMAN_RETRY_COUNT", "2"))
CHAIRMAN_BACKOFF_SECONDS = float(os.getenv("CHAIRMAN_BACKOFF_SECONDS", "1.0"))

# Council execution settings
MIN_SUCCESSFUL_RESPONSES = 3
CONTINUE_MIN_SUCCESSFUL_RESPONSES = 2
MODEL_MAX_RETRIES = 2
MODEL_RETRY_BACKOFF_BASE = 1.0
STAGE2_REQUEST_TIMEOUT_SECONDS = float(os.getenv("STAGE2_REQUEST_TIMEOUT_SECONDS", "90"))
STAGE2_EVALUATOR_TIMEOUT_SECONDS = float(os.getenv("STAGE2_EVALUATOR_TIMEOUT_SECONDS", "240"))

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
