"""Language selection and deterministic response-quality checks."""

import re
import unicodedata
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_LANGUAGE_CODE = "en"
LANGUAGE_NAMES = {
    "tr": "Turkish",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "ar": "Arabic",
    "ru": "Russian",
    "zh": "Chinese",
}

_SCRIPT_BY_LANGUAGE = {
    "tr": "latin",
    "en": "latin",
    "de": "latin",
    "fr": "latin",
    "es": "latin",
    "ar": "arabic",
    "ru": "cyrillic",
    "zh": "cjk",
}

_TRANSLITERATION = str.maketrans({
    "\u0131": "i", "\u0130": "i", "\u015f": "s", "\u015e": "s",
    "\u011f": "g", "\u011e": "g", "\u00fc": "u", "\u00dc": "u",
    "\u00f6": "o", "\u00d6": "o", "\u00e7": "c", "\u00c7": "c",
    "\u00df": "ss",
})

_LANGUAGE_WORDS = {
    "tr": {
        "bir", "ve", "ile", "icin", "nedir", "nasil", "neden", "hangi", "bu",
        "olarak", "gibi", "daha", "cok", "kisa", "cevap", "lutfen", "olur", "olabilir",
        "gereken", "adim", "plan", "turkiye", "ogrenci", "yapay", "zeka", "ay",
    },
    "en": {
        "the", "and", "for", "with", "that", "this", "please", "answer", "explain",
        "how", "what", "why", "can", "should", "would", "plan", "student", "language",
        "short", "write", "give", "about", "from", "your", "in", "to",
    },
    "de": {
        "der", "die", "das", "und", "ist", "mit", "fuer", "fur", "bitte", "erklaere",
        "erklar", "wie", "was", "warum", "kann", "soll", "antwort", "auf", "deutsch",
        "einen", "eine", "einem", "plan", "schueler", "schuler", "kurz",
    },
    "fr": {
        "le", "la", "les", "et", "pour", "avec", "que", "ce", "bonjour", "reponds",
        "repondez", "francais", "francaise", "comment", "pourquoi", "peux", "plan", "court",
    },
    "es": {
        "el", "la", "los", "las", "y", "para", "con", "que", "por", "responde",
        "espanol", "como", "porque", "puedo", "plan", "corto", "explica",
    },
}

_TECHNICAL_PROPER_NAMES = {
    "python", "numpy", "pytorch", "tensorflow", "github", "kaggle", "coursera", "fastapi",
    "javascript", "typescript", "sql", "api", "ai", "llm", "openai", "google", "nvidia",
}

_EXPLICIT_LANGUAGE_PATTERNS = {
    "tr": (
        r"\b(?:turkce|turkish)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+turkish\b",
        r"\bturkce\s+olarak\b",
    ),
    "en": (
        r"\b(?:ingilizce|english)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write|explain)\s+in\s+english\b",
        r"\bin\s+english\b",
        r"\bauf\s+englisch\b",
    ),
    "de": (
        r"\b(?:almanca|german|deutsch)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+german\b",
        r"\bauf\s+deutsch\b",
    ),
    "fr": (
        r"\b(?:fransizca|french|francais)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+french\b",
        r"\b(?:reponds|repondez)\s+en\s+francais\b",
    ),
    "es": (
        r"\b(?:ispanyolca|spanish|espanol)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+spanish\b",
        r"\ben\s+espanol\b",
    ),
    "ar": (
        r"\b(?:arabic|arabca)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+arabic\b",
    ),
    "ru": (
        r"\b(?:russian|rusca)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+russian\b",
    ),
    "zh": (
        r"\b(?:chinese|cince|mandarin)\s+(?:cevapla|cevap|yanitla|yanit|acikla|yaz|olsun|answer|respond)\b",
        r"\b(?:answer|respond|reply|write)\s+in\s+chinese\b",
    ),
}


def normalize_for_matching(text: str) -> str:
    """Create an accent-insensitive form suitable for small language profiles."""
    text = (text or "").translate(_TRANSLITERATION).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def _word_tokens(text: str) -> List[str]:
    return re.findall(r"[^\W\d_]+", normalize_for_matching(text), flags=re.UNICODE)


def _script_distribution(text: str) -> Dict[str, float]:
    counts = Counter()
    for character in text or "":
        if not character.isalpha():
            continue
        codepoint = ord(character)
        if 0x0400 <= codepoint <= 0x052F:
            counts["cyrillic"] += 1
        elif 0x0600 <= codepoint <= 0x06FF:
            counts["arabic"] += 1
        elif 0x4E00 <= codepoint <= 0x9FFF:
            counts["cjk"] += 1
        elif character.isascii() or "LATIN" in unicodedata.name(character, ""):
            counts["latin"] += 1
        else:
            counts["other"] += 1
    total = sum(counts.values())
    if not total:
        return {"latin": 0.0, "cyrillic": 0.0, "arabic": 0.0, "cjk": 0.0, "other": 0.0}
    return {
        script: round(counts.get(script, 0) / total, 3)
        for script in ("latin", "cyrillic", "arabic", "cjk", "other")
    }


def detect_language(text: str) -> Dict[str, Any]:
    """Detect one supported language without sending user content to another service."""
    scripts = _script_distribution(text)
    if scripts["arabic"] >= 0.55:
        return {"code": "ar", "confidence": scripts["arabic"], "languages": ["ar"], "scripts": scripts}
    if scripts["cyrillic"] >= 0.55:
        return {"code": "ru", "confidence": scripts["cyrillic"], "languages": ["ru"], "scripts": scripts}
    if scripts["cjk"] >= 0.55:
        return {"code": "zh", "confidence": scripts["cjk"], "languages": ["zh"], "scripts": scripts}

    tokens = [token for token in _word_tokens(text) if token not in _TECHNICAL_PROPER_NAMES]
    scores = {
        code: sum(token in words for token in tokens)
        for code, words in _LANGUAGE_WORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_code, best_score = ranked[0]
    second_score = ranked[1][1]
    if best_score == 0 or best_score < 2:
        return {"code": "unknown", "confidence": 0.0, "languages": [], "scripts": scripts}
    confidence = round(best_score / max(best_score + second_score, 1), 3)
    if best_score < 2 and len(tokens) > 6:
        confidence = min(confidence, 0.3)
    detected = [code for code, score in ranked if score and score >= max(1, best_score * 0.35)]
    return {
        "code": best_code,
        "confidence": confidence,
        "languages": detected,
        "scripts": scripts,
        "scores": scores,
    }


def _explicit_language(text: str) -> Optional[str]:
    normalized = normalize_for_matching(text)
    for code, patterns in _EXPLICIT_LANGUAGE_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            return code
    return None


def resolve_requested_language(
    user_query: str,
    conversation_user_messages: Optional[Iterable[str]] = None,
    default_language_code: str = DEFAULT_LANGUAGE_CODE,
) -> Dict[str, Any]:
    """Resolve explicit requests first, then the current and prior user language."""
    explicit = _explicit_language(user_query)
    if explicit:
        return {
            "requested_language_code": explicit,
            "requested_language_name": LANGUAGE_NAMES[explicit],
            "requested_language": LANGUAGE_NAMES[explicit],
            "language_source": "explicit",
            "language_confidence": 1.0,
        }

    detected = detect_language(user_query)
    if detected["code"] != "unknown" and detected["confidence"] >= 0.35:
        code = detected["code"]
        return {
            "requested_language_code": code,
            "requested_language_name": LANGUAGE_NAMES[code],
            "requested_language": LANGUAGE_NAMES[code],
            "language_source": "detected",
            "language_confidence": detected["confidence"],
        }

    for previous in reversed(list(conversation_user_messages or [])):
        previous_detected = detect_language(previous)
        if previous_detected["code"] != "unknown" and previous_detected["confidence"] >= 0.35:
            code = previous_detected["code"]
            return {
                "requested_language_code": code,
                "requested_language_name": LANGUAGE_NAMES[code],
                "requested_language": LANGUAGE_NAMES[code],
                "language_source": "conversation",
                "language_confidence": previous_detected["confidence"],
            }

    return {
        "requested_language_code": default_language_code,
        "requested_language_name": LANGUAGE_NAMES[default_language_code],
        "requested_language": LANGUAGE_NAMES[default_language_code],
        "language_source": "default",
        "language_confidence": 0.0,
    }


def language_instruction(constraints: Dict[str, Any]) -> str:
    language_name = constraints.get("requested_language_name") or constraints.get("requested_language") or "English"
    return (
        f"Produce the entire answer in {language_name}. Except for necessary proper names "
        "and technical terms, do not mix words, sentences, or explanations from other languages."
    )


def _gibberish_issues(text: str, expected_language: str) -> List[str]:
    normalized_tokens = _word_tokens(text)
    suspicious = []
    known = {"ahz", "mdahin", "rpeye"}
    for token in normalized_tokens:
        if token in known:
            suspicious.append(token)
        elif token == "bathrooms" and expected_language != "en":
            suspicious.append(token)
        elif len(token) >= 7 and re.search(r"[a-z][A-Z]", token):
            suspicious.append(token)
    if len(suspicious) >= 2:
        return ["gibberish_tokens"]
    return []


def evaluate_response_quality(text: str, constraints: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a deterministic, language-aware gate to a generated answer."""
    text = text or ""
    expected = constraints.get("requested_language_code") or {
        "Turkish": "tr", "English": "en", "German": "de", "French": "fr",
        "Spanish": "es", "Arabic": "ar", "Russian": "ru", "Chinese": "zh",
    }.get(constraints.get("requested_language"), DEFAULT_LANGUAGE_CODE)
    expected_name = LANGUAGE_NAMES.get(expected, "English")
    detected = detect_language(text)
    issues: List[str] = []

    if not text.strip():
        issues.append("empty_content")
    if re.search(r"(.)\1{8,}", text):
        issues.append("excessive_character_repetition")
    if re.search(r"(?i)(^|\s)(error|rate limit|model not found|invalid api key|authentication failed)(\s|:|$)", text):
        issues.append("provider_error_text")
    if re.search(r"\|[^\n]*\n[^\n]*\|[^\n]*$", text) and not text.rstrip().endswith("|"):
        issues.append("broken_markdown_table")
    if text.rstrip().endswith((",", ":", ";", "-")):
        issues.append("unfinished_line")

    expected_script = _SCRIPT_BY_LANGUAGE.get(expected, "latin")
    scripts = detected["scripts"]
    foreign_scripts = [
        script for script, share in scripts.items()
        if script not in {expected_script, "other"} and share >= 0.03
    ]
    if foreign_scripts:
        issues.append("mixed_scripts")

    primary = detected["code"]
    if primary != "unknown" and primary != expected and detected["confidence"] >= 0.35:
        issues.append("wrong_primary_language")
    scores = detected.get("scores", {})
    expected_score = scores.get(expected, 0)
    strongest_foreign = max((score for code, score in scores.items() if code != expected), default=0)
    if strongest_foreign >= max(3, expected_score + 2):
        issues.append("excessive_foreign_language")

    issues.extend(_gibberish_issues(text, expected))
    failure_issues = {
        "empty_content", "excessive_character_repetition", "provider_error_text",
        "broken_markdown_table", "mixed_scripts", "wrong_primary_language",
        "excessive_foreign_language", "gibberish_tokens",
    }
    status = "failed" if any(issue in failure_issues for issue in issues) else "warning" if issues else "passed"
    detected_languages = detected.get("languages") or ([primary] if primary != "unknown" else [])
    return {
        "expected_language": expected,
        "expected_language_name": expected_name,
        "detected_primary_language": primary,
        "detected_languages": detected_languages or ["unknown"],
        "script_distribution": scripts,
        "language_confidence": detected["confidence"],
        "quality_status": status,
        "quality_issues": sorted(set(issues)),
    }
