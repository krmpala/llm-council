"""Configuration for the LLM Council."""

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

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

# Council execution settings
MIN_SUCCESSFUL_RESPONSES = 3
MODEL_MAX_RETRIES = 2
MODEL_RETRY_BACKOFF_BASE = 1.0

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
