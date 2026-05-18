# app/rag.py
"""
RAG (Retrieval-Augmented Generation) module with multilingual support.

Features:
- Automatic language detection
- Multilingual translation for queries and answers
- Improved similarity search with normalization
- Caching for translations and embeddings
- Better error handling and fallback mechanisms
"""

from typing import List, Tuple, Optional, Set, Dict, Any
import logging
import re
import os
from functools import lru_cache

from openai import OpenAI
from anthropic import Anthropic
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    LLM_MODEL,
    TOP_K,
    RESPONSE_LANGUAGE,
    COLLECTION_NAME,
    SUPPORTED_LANGUAGES,
    DEFAULT_LANGUAGE,
    TRANSLATION_CACHE_SIZE,
    LANGUAGE_DETECTION_CACHE_SIZE,
    ENABLE_TRANSLATION_CACHE,
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
)
from .utils import normalize_text, normalize_language_code, log_error

# Import JSONL FAQ manager
try:
    from .faq_jsonl import get_faq_manager, DEFAULT_FAQ_JSONL
    JSONL_AVAILABLE = True
except ImportError:
    JSONL_AVAILABLE = False
    DEFAULT_FAQ_JSONL = os.path.join(os.path.dirname(__file__), "data", "faq.jsonl")

# Import synonym system (always available, no API needed)
try:
    from .synonyms import expand_with_synonyms, expand_with_synonyms_fuzzy, normalize_with_synonyms
    from .keyword_search import keyword_search_as_documents, build_keyword_index
    SYNONYMS_AVAILABLE = True
except ImportError:
    SYNONYMS_AVAILABLE = False

    def expand_with_synonyms(text: str, **kwargs) -> str:  # type: ignore
        return text

    def expand_with_synonyms_fuzzy(text: str, **kwargs) -> str:  # type: ignore
        return text

    def normalize_with_synonyms(text: str) -> str:  # type: ignore
        return text

logger = logging.getLogger(__name__)

# Anthropic (Claude) client initialization for language detection & translation
anthropic_client: Optional[Anthropic] = None
if ANTHROPIC_API_KEY:
    try:
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        logger.info("✅ Anthropic (Claude) client initialized successfully")
    except Exception as e:
        logger.warning(f"⚠️  Failed to initialize Anthropic client: {e}")
        anthropic_client = None
else:
    logger.warning("⚠️  ANTHROPIC_API_KEY missing - Language detection and translation will be limited")
    anthropic_client = None

# OpenAI client initialization (for embeddings only)
openai_client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("✅ OpenAI client initialized successfully (embeddings only)")
    except Exception as e:
        logger.warning(f"⚠️  Failed to initialize OpenAI client: {e}")
        openai_client = None
else:
    logger.warning("⚠️  OPENAI_API_KEY missing - Embeddings will be disabled")
    openai_client = None


# GEN pattern detection (for pool equipment generations)
_GEN_PATTERNS = {
    "gen1": re.compile(r"\bgen[\s\-]?1\b", re.IGNORECASE),
    "gen2": re.compile(r"\bgen[\s\-]?2\b", re.IGNORECASE),
    "gen3": re.compile(r"\bgen[\s\-]?3\b", re.IGNORECASE),
}
_GEN_HEADER_RE = re.compile(r"(?im)^\s*gen\s*([123])\s*[:\-–\.]\s*")


# ============================================================================
# LANGUAGE DETECTION & TRANSLATION
# ============================================================================

@lru_cache(maxsize=LANGUAGE_DETECTION_CACHE_SIZE if ENABLE_TRANSLATION_CACHE else 0)
def _simple_language_detect(text: str) -> str:
    """
    Simple keyword-based language detection as fallback.
    Returns 'fr', 'nl', 'en', 'de', or '' if unsure.
    """
    text_lower = text.lower()

    # French indicators
    french_words = ['comment', 'pourquoi', 'qu\'est-ce', 'quelle', 'quel', 'piscine',
                   'problème', 'faire', 'régler', 'entretien', 'chlore', 'où']
    fr_count = sum(1 for word in french_words if word in text_lower)

    # Dutch indicators
    dutch_words = ['hoe', 'waarom', 'wat', 'welke', 'zwembad', 'probleem',
                  'doen', 'instellen', 'onderhoud', 'chloor', 'waar', 'kan', 'moet']
    nl_count = sum(1 for word in dutch_words if word in text_lower)

    # English indicators
    english_words = ['how', 'why', 'what', 'which', 'pool', 'problem',
                    'do', 'set', 'maintenance', 'chlorine', 'where', 'can', 'should']
    en_count = sum(1 for word in english_words if word in text_lower)

    # German indicators
    german_words = ['wie', 'warum', 'was', 'welche', 'schwimmbad', 'problem',
                   'machen', 'einstellen', 'wartung', 'chlor', 'wo', 'kann', 'muss']
    de_count = sum(1 for word in german_words if word in text_lower)

    # Pick language with most matches
    scores = {'fr': fr_count, 'nl': nl_count, 'en': en_count, 'de': de_count}
    max_lang = max(scores, key=scores.get)

    return max_lang if scores[max_lang] > 0 else ""


def detect_language_code(text: str) -> str:
    """
    Detect the language code of the given text using Claude.

    Cached for performance. Returns empty string if detection fails.

    Args:
        text: Text to analyze

    Returns:
        ISO 639-1 language code (e.g., "en", "nl", "fr") or empty string
    """
    snippet = (text or "").strip()
    if not snippet:
        return ""

    # Try simple keyword detection first (fast, no API call)
    simple_lang = _simple_language_detect(snippet)
    if simple_lang:
        logger.debug(f"Simple language detection: {simple_lang}")
        return simple_lang

    # Limit snippet length for API efficiency
    snippet = snippet[:400]

    if anthropic_client is None:
        logger.debug("Anthropic client not available for language detection")
        return ""

    try:
        prompt = (
            "Identify the dominant ISO 639-1 language code for the user's text below. "
            "Reply with ONLY the two-letter code (e.g., 'fr', 'nl', 'en', 'de'). "
            "If unsure, guess the most likely language.\n\n"
            f"Text:\n{snippet}"
        )

        resp = anthropic_client.messages.create(
            model=LLM_MODEL,
            max_tokens=10,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = resp.content[0].text.strip().lower()

        # Try to extract 2-letter code
        if re.fullmatch(r"[a-z]{2}", raw):
            logger.debug(f"Claude detected language: {raw}")
            return raw

        match = re.search(r"([a-z]{2})", raw)
        if match:
            logger.debug(f"Claude detected language (extracted): {match.group(1)}")
            return match.group(1)

    except Exception as e:
        log_error(e, "Language detection failed", text_preview=snippet[:50])

    return ""


@lru_cache(maxsize=TRANSLATION_CACHE_SIZE if ENABLE_TRANSLATION_CACHE else 0)
def _cached_translation(prompt: str) -> str:
    """
    Internal cached translation function using Claude.

    Args:
        prompt: Translation prompt for Claude

    Returns:
        Translated text or empty string if failed
    """
    if anthropic_client is None:
        logger.debug("Anthropic client not available for translation")
        return ""

    try:
        resp = anthropic_client.messages.create(
            model=LLM_MODEL,
            max_tokens=2048,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}]
        )
        result = resp.content[0].text.strip()
        logger.debug(f"Claude translation complete: {len(result)} chars")
        return result
    except Exception as e:
        log_error(e, "Translation failed", prompt_preview=prompt[:100])
        return ""


# ============================================================================
# EXPERT MODE — Claude Haiku as Wifipool/Beniferro expert
# Reads the full FAQ as cached context and generates human-like answers
# ============================================================================

_EXPERT_FAQ_CONTEXT: Optional[str] = None
_EXPERT_FAQ_CACHE_SIG: Optional[tuple] = None
_EXPERT_MODEL = "claude-haiku-4-5-20251001"

_EXPERT_SYSTEM_BASE = (
    "You ARE the senior Wifipool/Beniferro pool expert assistant. Beniferro is a "
    "Belgian company that makes smart pool controllers (Wifipool Gen 1/Gen 2/Gen 3), "
    "salt electrolysers, pH/ORP dosing systems, frequency regulators, and pool "
    "accessories. Zwembad.eu (https://www.zwembad.eu) is the sister webshop selling "
    "the equipment.\n\n"
    "You will receive TWO blocks of context below: (1) COMPANY KNOWLEDGE — curated "
    "extracts from beniferro.eu and zwembad.eu, and (2) FAQ KNOWLEDGE BASE — 335 "
    "human-curated Q/A entries from the master Excel. You know every single line of "
    "both blocks by heart — you do not 'search', you simply KNOW. Respond like a "
    "human expert who has lived this product for years.\n\n"
    "PRIMARY DIRECTIVE: When the user asks a real technical or product question, "
    "ANSWER IT directly. Synthesize across multiple FAQ rows when needed. Do NOT "
    "punt to a 'pick a topic' menu unless the question is genuinely unanswerable "
    "from the FAQ + company knowledge. The user's default expectation is a useful "
    "answer, not a menu.\n\n"
    "MULTI-PART QUESTIONS: If the user packs several questions into one message "
    "(e.g. 'How do I adjust the pH AND where do I find my serial number?'), "
    "answer EACH part in the same reply, drawing from the relevant FAQ rows for "
    "each part. Combine them naturally — do not refuse multi-part questions. List "
    "EVERY contributing FAQ row in the 'sources' array so the UI can show all the "
    "related images/videos.\n\n"
    "STRICT RULES:\n"
    "- Use ONLY information from the COMPANY KNOWLEDGE and FAQ KNOWLEDGE BASE "
    "below. Never invent procedures, error codes, part numbers, serial numbers, "
    "URLs, or contact details.\n"
    "- ANTI-SUBSTITUTION RULE: Before answering, identify the EXACT topic of the "
    "user's question. Only answer if a FAQ row or company knowledge entry covers "
    "THAT exact topic. NEVER substitute a related-but-different row (e.g. user "
    "asks 'how to raise my pH' — do NOT answer with 'pH alarm troubleshooting' "
    "just because both mention pH). If the exact topic is not in the knowledge, "
    "say so honestly in the user's language and set primary_source=null.\n"
    "- If the question is outside the pool / spa / water-treatment / Wifipool / "
    "Beniferro / Zwembad.eu domain (weather, general coding, politics, capital of "
    "Belgium, jokes, recipes, …), politely refuse in ONE short sentence in the "
    "user's language, redirect to your domain, and set out_of_scope=true. Do NOT "
    "answer 'Correct' or any other one-word non-answer.\n"
    "- If the user's question is on-topic but genuinely not covered by the FAQ or "
    "company knowledge, say so clearly instead of guessing: e.g. 'I don't have "
    "that information in my knowledge base — please contact the SAV team.'\n"
    "- Reserve the 'vague question' path ONLY for actually empty/meaningless openers "
    "like 'hi', 'tell me about Beniferro', 'what can you teach me'. A real technical "
    "question — even if short — must get a real answer. When in doubt, ANSWER. "
    "Populate 'choices' with 3-4 short example topics only in this rare vague case, "
    "and set primary_source=null and confidence below 0.4.\n"
    "- Use the prior conversation turns as context. If the user gives a short reply "
    "(e.g. 'gen 2', 'oui', 'the first one', '1'), interpret it as an answer to YOUR "
    "previous question and continue the same topic — do NOT treat it as a new "
    "independent query.\n"
    "- Preserve URLs, product names (Wifipool, Beniferro, Pool Twin, Pool Duo, Gen 1, "
    "Gen 2, Gen 3, Shelly), and technical terms exactly as they appear in the FAQ.\n"
    "- Cite every contributing FAQ row in 'sources' (array of integers, in the order "
    "they appear in your answer). Pick the most relevant one as 'primary_source'.\n\n"
    "INTERACTIVE CHOICES:\n"
    "- If your answer asks the user to pick between a small set of options "
    "(e.g. 'Is it a Gen 1 or Gen 2 device?', 'Do you have Pool Twin or Pool Duo?', "
    "'Yes or no?'), list those exact options in the 'choices' field so the UI can "
    "render them as clickable buttons. Keep each choice short (1-4 words). "
    "Use the same language as your answer.\n"
    "- If you are not asking a choice question, leave 'choices' as an empty list.\n\n"
    "OUTPUT — strict JSON, no preamble, no markdown code fence:\n"
    "{\n"
    '  "answer": "your natural answer in the user language",\n'
    '  "sources": [row_int, ...],\n'
    '  "primary_source": row_int or null,\n'
    '  "confidence": 0.0 to 1.0,\n'
    '  "out_of_scope": true|false,\n'
    '  "ambiguous": true|false,\n'
    '  "alternative_questions": ["..", ".."] (only when ambiguous),\n'
    '  "choices": ["Option 1", "Option 2"] (only when you ask the user to pick)\n'
    "}"
)

_LANG_NAMES = {"nl": "Dutch", "fr": "French", "en": "English", "de": "German"}


def _build_expert_messages(question: str, history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """Build the messages list for Anthropic, including up to the last 3 turns of history.

    history format: [{"role": "user"|"assistant", "content": "..."}, ...] in chronological order.
    """
    msgs: List[Dict[str, str]] = []
    if history:
        trimmed = [h for h in history if h.get("role") in ("user", "assistant") and (h.get("content") or "").strip()]
        trimmed = trimmed[-6:]
        for h in trimmed:
            content = h["content"].strip()
            if h["role"] == "assistant" and len(content) > 600:
                content = content[:600] + "..."
            msgs.append({"role": h["role"], "content": content})
    msgs.append({"role": "user", "content": question})
    return msgs


_COMPANY_KNOWLEDGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "app", "data", "company_knowledge.md"
)


def _load_company_knowledge() -> str:
    """Read the curated company knowledge markdown if present. Empty string on miss."""
    try:
        if not os.path.exists(_COMPANY_KNOWLEDGE_PATH):
            return ""
        with open(_COMPANY_KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _company_knowledge_mtime() -> float:
    try:
        return os.path.getmtime(_COMPANY_KNOWLEDGE_PATH)
    except OSError:
        return 0.0


def _build_expert_faq_context(entries: List[Dict[str, Any]]) -> str:
    parts: List[str] = []

    # Prepend the curated company knowledge so Claude treats it as part of its
    # internalized expertise. The whole block is cached server-side via
    # cache_control so the marginal cost per request stays low.
    company = _load_company_knowledge()
    if company:
        parts.append(
            "=== COMPANY KNOWLEDGE (Beniferro & Zwembad.eu) ===\n"
            "The two companies behind this chatbot. Use this content the same "
            "way you use the FAQ — you know it by heart.\n\n"
            + company
        )

    faq_lines: List[str] = []
    for e in entries:
        row = e.get("excel_row")
        q = (e.get("Vraag") or e.get("question") or "").strip()
        a = (e.get("Antwoord") or e.get("answer") or "").strip()
        if not q or not a:
            continue
        row_tag = f"[row {row}]" if row else "[row ?]"
        faq_lines.append(f"{row_tag} Q: {q}\nA: {a}")
    if faq_lines:
        parts.append("=== FAQ KNOWLEDGE BASE (335 curated Q/A entries) ===\n" + "\n\n".join(faq_lines))

    return "\n\n".join(parts)


def _entries_signature(entries: List[Dict[str, Any]]) -> int:
    """Content-aware signature: detects edits anywhere in the FAQ or in the
    company knowledge file. Any edit anywhere invalidates the cache."""
    h = hash((len(entries), _company_knowledge_mtime()))
    for e in entries:
        h ^= hash((
            e.get("excel_row"),
            (e.get("Vraag") or e.get("question") or ""),
            (e.get("Antwoord") or e.get("answer") or ""),
        ))
    return h


def invalidate_expert_faq_context() -> None:
    """Force-clear the cached FAQ context. Called from main._reload_faq after
    any FAQ create/update/delete so the next expert_answer rebuilds the prompt."""
    global _EXPERT_FAQ_CONTEXT, _EXPERT_FAQ_CACHE_SIG
    _EXPERT_FAQ_CONTEXT = None
    _EXPERT_FAQ_CACHE_SIG = None
    logger.info("Expert FAQ context invalidated — will rebuild on next request")


def _get_expert_faq_context(entries: List[Dict[str, Any]]) -> str:
    global _EXPERT_FAQ_CONTEXT, _EXPERT_FAQ_CACHE_SIG
    sig = _entries_signature(entries)
    if _EXPERT_FAQ_CONTEXT is None or _EXPERT_FAQ_CACHE_SIG != sig:
        _EXPERT_FAQ_CONTEXT = _build_expert_faq_context(entries)
        _EXPERT_FAQ_CACHE_SIG = sig
        logger.info(f"Built expert FAQ context: {len(entries)} entries, {len(_EXPERT_FAQ_CONTEXT)} chars")
    return _EXPERT_FAQ_CONTEXT


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    import json as _json
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s).strip()
    try:
        return _json.loads(s)
    except Exception:
        pass
    match = re.search(r"\{.*\}", s, re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(0))
        except Exception:
            return None
    return None


def expert_answer(
    question: str,
    lang_code: str,
    faq_entries: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Generate a natural expert answer using Claude Haiku with the FAQ as cached context.

    Returns a dict with: answer, sources, primary_source, confidence, out_of_scope,
    ambiguous, alternative_questions, raw (debug), error (if failure).
    """
    empty = {
        "answer": "",
        "sources": [],
        "primary_source": None,
        "confidence": 0.0,
        "out_of_scope": False,
        "ambiguous": False,
        "alternative_questions": [],
    }
    if anthropic_client is None:
        return {**empty, "error": "anthropic_client_unavailable"}
    if not question or not question.strip():
        return {**empty, "error": "empty_question"}

    code = (lang_code or "nl").lower()
    target_lang = _LANG_NAMES.get(code, "Dutch")
    context = _get_expert_faq_context(faq_entries)
    if not context:
        return {**empty, "error": "empty_faq_context"}

    # When the user explicitly picks a language in the UI, the backend passes
    # that lang_code here and we hard-lock Claude to it. The language lock is
    # absolute — even if the user types in another language, we respond in the
    # UI-selected language.
    system_prompt = _EXPERT_SYSTEM_BASE + (
        f"\n\nABSOLUTE LANGUAGE LOCK: You MUST respond ONLY in {target_lang}. "
        f"This is non-negotiable.\n"
        f"- If the user writes in Dutch but the UI language is {target_lang}, "
        f"respond in {target_lang}.\n"
        f"- If the user writes in French but the UI language is {target_lang}, "
        f"respond in {target_lang}.\n"
        f"- If the user mixes languages, respond in {target_lang} only.\n"
        f"- NEVER mix languages in a single response.\n"
        f"- NEVER ask the user which language they prefer — the UI already chose "
        f"{target_lang}.\n"
        f"- The FAQ knowledge base is mostly in Dutch; translate the relevant "
        f"facts into {target_lang} naturally before answering.\n"
        f"Producing a response in any language other than {target_lang} is a "
        f"critical failure."
    )

    try:
        resp = anthropic_client.messages.create(
            model=_EXPERT_MODEL,
            max_tokens=1500,
            temperature=0.2,
            system=[
                {"type": "text", "text": system_prompt},
                {
                    "type": "text",
                    "text": "FAQ KNOWLEDGE BASE (Dutch master, translate answer to user language):\n\n" + context,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=_build_expert_messages(question, history),
        )
        raw = resp.content[0].text if resp.content else ""
        logger.info(
            f"expert_answer ok | model={_EXPERT_MODEL} | tokens in/out="
            f"{getattr(resp.usage, 'input_tokens', '?')}/{getattr(resp.usage, 'output_tokens', '?')}"
            f" | cache_read={getattr(resp.usage, 'cache_read_input_tokens', '?')}"
        )
    except Exception as e:
        logger.warning(
            f"expert_answer Anthropic API FAILED — {type(e).__name__}: {e} "
            f"| question[:80]={question[:80]!r} | model={_EXPERT_MODEL}"
        )
        log_error(e, "expert_answer API call failed", question=question[:80])
        return {**empty, "error": f"api_error: {type(e).__name__}: {e}"}

    data = _extract_json(raw)
    if not data:
        logger.warning(
            f"expert_answer JSON parse failed — Claude returned malformed JSON. "
            f"raw[:300]={raw[:300]!r}"
        )
        return {**empty, "error": "json_parse_failed", "raw": raw[:400]}

    return {
        "answer": (data.get("answer") or "").strip(),
        "sources": data.get("sources") or [],
        "primary_source": data.get("primary_source"),
        "confidence": float(data.get("confidence") or 0.0),
        "out_of_scope": bool(data.get("out_of_scope")),
        "ambiguous": bool(data.get("ambiguous")),
        "alternative_questions": data.get("alternative_questions") or [],
    }


@lru_cache(maxsize=1024)
def polish_faq_answer(text: str, lang_code: str = "nl") -> str:
    """Light LLM pass to fix obvious typos / missing letters in a pre-written FAQ answer.

    The FAQ source Excel sometimes has minor defects (e.g. 'oer' instead of 'Voer').
    This function fixes those defects WITHOUT rewriting or expanding the content.
    Cached per (text, lang) so each FAQ row is polished once.
    """
    if not text:
        return text
    stripped = text.strip()
    if not stripped:
        return text

    first_char = stripped[0]
    needs_polish = first_char.islower() or first_char in ",;:.)"
    if not needs_polish:
        # Also polish if we detect a word that's likely truncated (e.g. "oer" at start).
        first_word = stripped.split(None, 1)[0].rstrip(",.;:!?")
        if len(first_word) >= 2 and first_word[0].islower():
            needs_polish = True

    if not needs_polish:
        return text

    lang_name = SUPPORTED_LANGUAGES.get(normalize_language_code(lang_code) or "nl", "Dutch")
    prompt = (
        f"The following {lang_name} FAQ answer may have minor typos or a missing first "
        f"letter at the very start (e.g. 'oer' instead of 'Voer'). Fix ONLY obvious "
        f"typos, missing letters, or capitalization. Do NOT rewrite, rephrase, translate, "
        f"shorten, or expand the content. Keep line breaks, product names, URLs, and "
        f"technical terms exactly as they appear. Reply with the corrected text only.\n\n"
        f"Answer:\n{text}"
    )

    if anthropic_client is not None:
        try:
            fixed = _cached_translation(prompt)
            if fixed and len(fixed) >= max(10, int(len(text) * 0.5)):
                return fixed
        except Exception as e:
            log_error(e, "Answer polish (Anthropic) failed", text_preview=text[:50])

    if openai_client is not None:
        try:
            resp = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.0,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            fixed = (resp.choices[0].message.content or "").strip()
            if fixed and len(fixed) >= max(10, int(len(text) * 0.5)):
                return fixed
        except Exception as e:
            log_error(e, "Answer polish (OpenAI) failed", text_preview=text[:50])

    return text


def translate_answer(text: str, target_code: str) -> str:
    """
    Translate answer text to the target language.

    Preserves formatting, URLs, product names, and technical terms.

    Args:
        text: Text to translate
        target_code: Target language code (ISO 639-1)

    Returns:
        Translated text or original if translation fails
    """
    if not text:
        return text

    code = normalize_language_code(target_code)

    # No translation needed for Dutch (source language) or empty code
    if not code or code == DEFAULT_LANGUAGE:
        return text

    # Get language name
    lang_name = SUPPORTED_LANGUAGES.get(code, code)

    try:
        prompt = (
            f"Translate the following support reply to {lang_name}. "
            "Preserve the formatting, bullet lists, numbering, and "
            "keep product names, option labels, and URLs exactly as they appear. "
            "Respond with the translation only.\n\n"
            f"Reply:\n{text}"
        )
        translated = _cached_translation(prompt)
        return translated or text
    except Exception as e:
        log_error(e, "Answer translation failed", target_lang=code)
        return text


def translate_for_matching(text: str, source_code: str, target_code: str = DEFAULT_LANGUAGE) -> str:
    """
    Translate user query for better FAQ matching.

    Optimized for matching against Dutch FAQ entries.

    Args:
        text: Query text to translate
        source_code: Source language code
        target_code: Target language code (default: Dutch)

    Returns:
        Translated query or original text if translation fails
    """
    if not text:
        return text

    src = normalize_language_code(source_code)
    tgt = normalize_language_code(target_code)

    # No translation needed if same language
    if not src or not tgt or src == tgt:
        return text

    try:
        prompt = (
            "Vertaal de volgende klantenvraag naar het Nederlands zodat een FAQ-zoekmachine "
            "met Nederlandse trefwoorden hem kan begrijpen. "
            "Behoud eigennamen en merknamen. Geef alleen de vertaling.\n\n"
            f"Vraag:\n{text}"
        )
        translated = _cached_translation(prompt)
        return translated or text
    except Exception as e:
        log_error(e, "Query translation failed", source_lang=src, target_lang=tgt)
        return text


def translate_list(items: List[str], target_code: str) -> List[str]:
    """
    Translate a list of strings (e.g., suggestions).

    Args:
        items: List of strings to translate
        target_code: Target language code

    Returns:
        List of translated strings
    """
    if not items:
        return items

    code = normalize_language_code(target_code)
    if not code or code == DEFAULT_LANGUAGE:
        return items

    lang_name = SUPPORTED_LANGUAGES.get(code, code)

    try:
        # Combine all items for efficient batch translation
        combined = "\n###\n".join(items)
        prompt = (
            f"Translate the following items to {lang_name}. "
            "Each item is separated by '###'. "
            "Preserve the separator and return translations in the same order.\n\n"
            f"{combined}"
        )

        translated = _cached_translation(prompt)
        if translated:
            return [s.strip() for s in translated.split("###")]

    except Exception as e:
        log_error(e, "List translation failed", item_count=len(items), target_lang=code)

    return items


# ============================================================================
# GEN DETECTION & EXTRACTION (Pool Equipment Generations)
# ============================================================================

def detect_gen(text: str) -> Optional[str]:
    """
    Detect generation mention in text (gen1, gen2, gen3).

    Args:
        text: Text to analyze

    Returns:
        "gen1", "gen2", "gen3" or None
    """
    t = (text or "")
    for key, pat in _GEN_PATTERNS.items():
        if pat.search(t):
            return key
    return None


def extract_found_gens(docs: List[Any]) -> Set[str]:
    """
    Extract all generation mentions from document metadata.

    Compatible with various metadata formats:
    - List: ["gen1", "gen2"]
    - CSV string: "gen1,gen2"
    - Single value: "gen1"
    - Filename: ".../gen1/..."

    Args:
        docs: List of documents with metadata

    Returns:
        Set of generation identifiers
    """
    found: Set[str] = set()

    for d in docs or []:
        md = d.metadata or {}
        gens = md.get("gens")

        # Handle list format
        if isinstance(gens, list):
            for g in gens:
                if isinstance(g, str) and g.lower() in {"gen1", "gen2", "gen3"}:
                    found.add(g.lower())

        # Handle string format (CSV or single value)
        elif isinstance(gens, str):
            for g in re.split(r"[,\s]+", gens):
                g = g.strip().lower()
                if g in {"gen1", "gen2", "gen3"}:
                    found.add(g)

        # Handle single 'gen' field
        g1 = md.get("gen")
        if isinstance(g1, str) and g1.lower() in {"gen1", "gen2", "gen3"}:
            found.add(g1.lower())

        # Fallback: check filename
        src = (md.get("source") or "")
        for g in ("gen1", "gen2", "gen3"):
            if g in src.lower():
                found.add(g)

    return found


def _only_answer_part(text: str) -> str:
    """Extract only the answer part after 'Antwoord:' marker."""
    if not text:
        return ""
    parts = re.split(r"(?i)\bantwoord\s*:\s*", text, maxsplit=1)
    return parts[1] if len(parts) == 2 else text


def _extract_gen_block(raw_text: str, gen: str) -> str:
    """
    Extract specific GEN block from structured answer.

    Handles answers formatted like:
    Gen 1:
    Answer for gen 1...
    Gen 2:
    Answer for gen 2...

    Args:
        raw_text: Full answer text
        gen: Target generation (e.g., "gen1")

    Returns:
        Extracted block or full text if no structure found
    """
    if not raw_text or not gen:
        return raw_text or ""

    body = _only_answer_part(raw_text)
    matches = list(_GEN_HEADER_RE.finditer(body))

    if not matches:
        return body.strip()

    # Map gen format variations
    want = {"gen1": "1", "gen 1": "1", "gen2": "2", "gen 2": "2", "gen3": "3", "gen 3": "3"}.get(gen.lower())
    if not want:
        return body.strip()

    # Find the matching block
    for i, m in enumerate(matches):
        current = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)

        if current == want:
            return body[start:end].strip()

    return body.strip()


# ============================================================================
# QUERY EXPANSION & REFORMULATION
# ============================================================================

def _expand_queries_with_llm(question: str, n: int = 3) -> List[str]:
    """
    Generate query reformulations using LLM for better retrieval.

    Args:
        question: Original question
        n: Number of reformulations to generate

    Returns:
        List of reformulated queries
    """
    if openai_client is None:
        logger.debug("OpenAI client not available for query expansion")
        return []

    try:
        prompt = (
            "Reformule la question ci-dessous en variantes de recherche courtes et précises. "
            f"Donne {n} lignes, sans numéros, sans guillemets.\n\nQuestion:\n{question}"
        )

        resp = openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )

        text = resp.choices[0].message.content.strip()
        lines = [l.strip("•- \t") for l in text.splitlines() if l.strip()]
        return lines[:n] if lines else []

    except Exception as e:
        log_error(e, "Query expansion failed", question_preview=question[:50])
        return []


# ============================================================================
# VECTOR STORE & RETRIEVAL
# ============================================================================

def _get_vs() -> Chroma:
    """Get or create Chroma vector store instance."""
    try:
        # Try to use JSONL FAQ manager first
        if JSONL_AVAILABLE:
            try:
                manager = get_faq_manager()
                vs = manager.get_vectorstore()
                if vs is not None:
                    logger.debug("Using JSONL FAQ vectorstore")
                    return vs
            except Exception as e:
                logger.warning(f"JSONL vectorstore not available: {e}")

        # Fallback to direct Chroma connection
        return Chroma(
            persist_directory=CHROMA_DIR,
            collection_name=COLLECTION_NAME,
            embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
        )
    except Exception as e:
        log_error(e, "Failed to initialize vector store")
        raise


def _uniq_add(acc: List[Any], seen: Set[Any], docs: List[Any]) -> bool:
    """
    Add unique documents to accumulator, avoiding duplicates.

    Args:
        acc: Accumulator list
        seen: Set of seen document keys
        docs: Documents to add

    Returns:
        True if TOP_K limit reached
    """
    for d in docs or []:
        key = (
            (d.metadata or {}).get("source"),
            (d.metadata or {}).get("page"),
            hash(d.page_content)
        )

        if key in seen:
            continue

        acc.append(d)
        seen.add(key)

        if len(acc) >= TOP_K:
            return True

    return False


def _expand_neighbors(vs: Chroma, doc: Any, k: int = 3) -> List[Any]:
    """
    Retrieve neighboring chunks from the same source document.

    Helps capture sequential context from the same file.

    Args:
        vs: Vector store instance
        doc: Source document
        k: Number of neighbors to retrieve

    Returns:
        List of neighboring documents
    """
    try:
        src = (doc.metadata or {}).get("source")
        if not src:
            return []
        return vs.similarity_search(doc.page_content, k=k, filter={"source": src})
    except Exception as e:
        log_error(e, "Neighbor expansion failed")
        return []


def retrieve(question: str, gen_filter: Optional[str] = None) -> List[Any]:
    """
    Retrieve relevant documents using MMR with smart fallback.

    Features:
    - Synonym expansion (NL/FR/EN) so "zuurtegraad" == "pH" == "acidité"
    - Maximal Marginal Relevance for diversity
    - Query expansion for better recall
    - Neighbor expansion for context
    - GEN-based filtering
    - Keyword search fallback when vector store unavailable

    Args:
        question: User question (any language/phrasing)
        gen_filter: Optional generation filter ("gen1", "gen2", "gen3")

    Returns:
        List of relevant documents
    """
    # ── Synonym expansion (with fuzzy matching for typos) ─────────────────────
    # Enrich the query with synonyms so different phrasings match the same FAQ.
    # Also handles typos: "kaliibrate" still matches "kalibreren"
    # Example: "acidity" → "acidity ph zuurtegraad acidite acid level ..."
    if SYNONYMS_AVAILABLE:
        question_expanded = expand_with_synonyms_fuzzy(question)
        if question_expanded != question:
            logger.debug(f"Synonym expansion: '{question[:50]}' → '{question_expanded[:80]}'")
    else:
        question_expanded = question

    # ── Try vector store ──────────────────────────────────────────────────────
    try:
        vs = _get_vs()
    except Exception as e:
        logger.warning(f"Vector store unavailable, falling back to keyword search: {e}")
        # Keyword fallback: works with NO API, uses synonyms internally
        if SYNONYMS_AVAILABLE:
            return keyword_search_as_documents(question, top_k=TOP_K)
        return []

    results: List[Any] = []
    seen: Set[Any] = set()

    # Build query list: original + synonym-expanded + LLM reformulations
    queries = [question_expanded]
    if question_expanded != question:
        queries.append(question)  # also try original
    queries += _expand_queries_with_llm(question, n=2)

    def mmr(q: str, k: int, fetch_k: int, flt: Optional[Dict] = None) -> List[Any]:
        """Run MMR search with fallback to similarity search."""
        try:
            return vs.max_marginal_relevance_search(q, k=k, fetch_k=fetch_k, filter=flt)
        except Exception:
            try:
                return vs.similarity_search(q, k=k, filter=flt)
            except Exception as e:
                log_error(e, "Search failed", query_preview=q[:50])
                return []

    def add_with_neighbors(docs: List[Any]) -> bool:
        """Add documents with their neighbors."""
        for d in docs or []:
            if _uniq_add(results, seen, [d]):
                return True

            neigh = _expand_neighbors(vs, d, k=3)
            if _uniq_add(results, seen, neigh):
                return True
        return False

    # 1) Target FAQ and mixed sources
    for q in queries:
        flt = {"source_type": {"$in": ["faq", "mixed"]}}
        docs = mmr(q, k=TOP_K, fetch_k=max(40, TOP_K * 8), flt=flt)

        if add_with_neighbors(docs):
            break

    # 2) Fallback to all sources if insufficient results
    if len(results) < TOP_K:
        for q in queries:
            docs = mmr(q, k=TOP_K, fetch_k=max(40, TOP_K * 8), flt=None)
            if add_with_neighbors(docs):
                break

    # 3) Keyword search reinforcement using synonyms
    # When vector results are insufficient, add keyword matches to fill gaps.
    # This catches synonym mismatches that embeddings might miss.
    if SYNONYMS_AVAILABLE and len(results) < max(2, TOP_K // 2):
        try:
            kw_docs = keyword_search_as_documents(question, top_k=TOP_K)
            _uniq_add(results, seen, kw_docs)
            if kw_docs:
                logger.debug(f"Keyword search added {len(kw_docs)} docs as reinforcement")
        except Exception as e:
            logger.debug(f"Keyword search reinforcement failed: {e}")

    # 4) Apply GEN filter if specified
    if gen_filter:
        want = gen_filter.strip().lower()

        def md_has_gen(md: Dict) -> bool:
            if not md:
                return False

            # Check 'gens' field (CSV or list)
            gens = md.get("gens")
            if isinstance(gens, str):
                for g in re.split(r"[,\s]+", gens):
                    if g.strip().lower() == want:
                        return True

            if isinstance(gens, list):
                if any(isinstance(x, str) and x.strip().lower() == want for x in gens):
                    return True

            # Check 'gen' field
            g1 = md.get("gen")
            if isinstance(g1, str) and g1.strip().lower() == want:
                return True

            # Check filename
            src = (md.get("source") or "").lower()
            if want in src:
                return True

            return False

        filtered = [d for d in results if md_has_gen(d.metadata)]
        if filtered:
            results = filtered

    logger.info(f"Retrieved {len(results)} documents for query: {question[:50]}...")
    return results


def retrieve_with_scores(question: str) -> List[Tuple[Any, float]]:
    """
    Retrieve documents with similarity scores.

    Args:
        question: User question

    Returns:
        List of (document, score) tuples; lower score = higher similarity
    """
    try:
        vs = _get_vs()
        pairs = vs.similarity_search_with_score(question, k=TOP_K)
        logger.info(f"Retrieved {len(pairs)} documents with scores")
        return pairs
    except Exception as e:
        log_error(e, "Scored retrieval failed", question_preview=question[:50])
        return []


def chat_with_confidence(question: str) -> Tuple[Optional[List[Any]], List[float], float]:
    """
    Retrieve documents with confidence scoring.

    Args:
        question: User question

    Returns:
        Tuple of (documents, scores, best_score)
        best_score: Lower is better
    """
    pairs = retrieve_with_scores(question)
    docs = [p[0] for p in pairs]
    scores = [p[1] for p in pairs]

    if not docs:
        return None, [], 1e9

    best = min(scores) if scores else 1e9
    return docs, scores, best


# ============================================================================
# CONTEXT BUILDING & PROMPT GENERATION
# ============================================================================

def _lang_instruction(question: str) -> str:
    """
    Generate language instruction for the LLM based on configuration.

    Args:
        question: User question (for context)

    Returns:
        Language instruction string
    """
    if (RESPONSE_LANGUAGE or "").strip().lower() == "auto":
        return (
            "Detect the user's language from the query and ALWAYS answer in that language. "
            "Do not mention that you detected the language. Keep product/brand names and URLs as-is."
        )
    return f"Answer in {RESPONSE_LANGUAGE}. Keep product/brand names and URLs as-is."


def _build_context(docs: List[Any]) -> str:
    """
    Build context string from retrieved documents.

    Args:
        docs: List of documents

    Returns:
        Formatted context string
    """
    if not docs:
        return "(no context found)"

    parts = []
    for d in docs:
        md = d.metadata or {}
        title = md.get("title") or md.get("source") or "Document"
        page = md.get("page")

        head = f"[{title}{f' — p.{page}' if page is not None else ''}]"
        parts.append(f"{head} {d.page_content}")

    return "\n\n".join(parts)


def _build_messages(question: str, docs: List[Any]) -> List[Dict[str, str]]:
    """
    Build messages for OpenAI chat completion.

    Args:
        question: User question
        docs: Context documents

    Returns:
        List of message dictionaries
    """
    lang_rule = _lang_instruction(question)
    context = _build_context(docs)

    system = (
        "You are 'Assistant Piscines', a helpful support agent for a pool e-commerce site.\n"
        f"{lang_rule}\n"
        "Use ONLY the provided context to answer. If the context is insufficient, say it and ask a short, "
        "clear clarifying question instead of hallucinating.\n"
        "Keep answers concise and helpful.\n"
        "Whenever the context contains a procedure or numbered steps (e.g., Step 1/2/3), reproduce them clearly "
        "as an ordered list using the exact terms found in the context. Do not generalize; stick closely to the "
        "provided instructions.\n"
    )

    user = f"QUESTION:\n{question}\n\nCONTEXT (excerpts):\n{context}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _collect_citations(docs: List[Any]) -> List[Dict[str, Any]]:
    """
    Collect unique citations from documents.

    Args:
        docs: List of documents

    Returns:
        List of citation dictionaries
    """
    citations: List[Dict[str, Any]] = []
    seen: Set[Tuple] = set()

    for d in docs or []:
        md = d.metadata or {}
        src = md.get("source")
        url = md.get("url") or None
        title = md.get("title") or src or "Document"
        page = md.get("page")

        key = (title, url, src, page)
        if key in seen:
            continue

        citations.append({
            "title": title,
            "url": url,
            "source": src,
            "page": page
        })
        seen.add(key)

    return citations


# ============================================================================
# ANSWER GENERATION
# ============================================================================

def generate_answer(question: str, docs: List[Any], chosen_gen: Optional[str] = None) -> Tuple[str, List[Dict]]:
    """
    Generate answer using LLM or extract GEN-specific blocks.

    Args:
        question: User question
        docs: Context documents
        chosen_gen: Optional GEN filter ("gen1", "gen2", "gen3")

    Returns:
        Tuple of (answer, citations)
    """
    # 1) Deterministic GEN extraction mode
    if chosen_gen:
        pieces = []
        for d in docs or []:
            block = _extract_gen_block(d.page_content or "", chosen_gen)
            # Only include if we actually extracted a sub-block
            if block and block != (d.page_content or "").strip():
                pieces.append(block)

        if pieces:
            answer = "\n\n".join(pieces).strip()
            citations = _collect_citations(docs)
            logger.info(f"Returned GEN-specific answer (gen={chosen_gen})")
            return answer, citations

    # 2) LLM generation fallback
    if openai_client is None:
        logger.warning("OpenAI client not available for answer generation")
        error_msg = (
            "Ik kan momenteel geen automatisch antwoord genereren vanwege een configuratieprobleem. "
            "Neem contact op met de ondersteuning."
        )
        return error_msg, _collect_citations(docs)

    try:
        messages = _build_messages(question, docs)

        resp = openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=800,
        )

        answer = resp.choices[0].message.content
        citations = _collect_citations(docs)

        logger.info("Generated LLM answer successfully")
        return answer, citations

    except Exception as e:
        log_error(e, "Answer generation failed", question_preview=question[:50])
        error_msg = (
            "Ik kan momenteel geen automatisch antwoord genereren. "
            "Probeer het opnieuw of neem contact op met de ondersteuning."
        )
        return error_msg, _collect_citations(docs)


# ============================================================================
# SUGGESTION GENERATION
# ============================================================================

def get_top_suggestions(
    question: str,
    top_k: int = 4,
    min_similarity: float = 0.3
) -> List[Dict[str, Any]]:
    """
    Get top K FAQ suggestions with similarity scores.

    Args:
        question: User question
        top_k: Number of suggestions to return
        min_similarity: Minimum similarity threshold (0-1)

    Returns:
        List of suggestion dictionaries with scores
    """
    try:
        # Retrieve documents with distance scores
        pairs = retrieve_with_scores(question)

        if not pairs:
            logger.info("No suggestions found")
            return []

        suggestions = []

        for doc, distance_score in pairs[:top_k * 2]:  # Fetch more to filter
            # Convert distance to similarity (lower distance = higher similarity)
            # For L2 distance: similarity = 1 / (1 + distance)
            similarity = 1.0 / (1.0 + distance_score)

            # Filter by minimum threshold
            if similarity < min_similarity:
                continue

            # Extract metadata
            metadata = doc.metadata or {}

            suggestion = {
                "question": metadata.get("title", ""),
                "answer": doc.page_content or "",
                "similarity_score": round(similarity * 100, 1),  # Convert to percentage
                "category": metadata.get("category", ""),
                "metadata": metadata
            }

            suggestions.append(suggestion)

            # Stop if we have enough
            if len(suggestions) >= top_k:
                break

        # Sort by score (descending)
        suggestions.sort(key=lambda x: x["similarity_score"], reverse=True)

        logger.info(f"Returned {len(suggestions)} suggestions")
        return suggestions[:top_k]

    except Exception as e:
        log_error(e, "Suggestion generation failed", question_preview=question[:50])
        return []


# ============================================================================
# INTELLIGENT RETRIEVAL WITH REASONING VALIDATION
# ============================================================================

def retrieve_with_reasoning(
    question: str,
    gen_filter: Optional[str] = None,
    use_reasoning: bool = True,
    min_confidence: float = 0.6
) -> Tuple[Optional[List[Any]], Optional[Any], float]:
    """
    Retrieve documents with intelligent reasoning-based validation.

    This function combines:
    1. Vector similarity search (RAG)
    2. Intent classification
    3. Domain matching
    4. Reasoning-based validation
    5. Confidence scoring

    Args:
        question: User's question
        gen_filter: Optional generation filter
        use_reasoning: Whether to use reasoning validation (requires API key)
        min_confidence: Minimum confidence threshold

    Returns:
        Tuple of (documents, validation_result, confidence_score)
    """
    # Import reasoning module here to avoid circular imports
    try:
        from .reasoning import (
            classify_intent,
            validate_match,
            calculate_overall_confidence,
            should_answer_with_confidence
        )
    except ImportError as e:
        logger.warning(f"Reasoning module not available: {e}")
        use_reasoning = False

    # Step 1: Retrieve candidate documents using embeddings
    try:
        docs = retrieve(question, gen_filter=gen_filter)
    except Exception as e:
        log_error(e, "Document retrieval failed")
        return None, None, 0.0

    if not docs:
        logger.info("No documents retrieved")
        return None, None, 0.0

    # Step 2: Get similarity scores
    try:
        scored_docs = retrieve_with_scores(question)
        if not scored_docs:
            return docs, None, 0.3
    except Exception as e:
        log_error(e, "Scored retrieval failed")
        return docs, None, 0.3

    # If reasoning is disabled, return basic results
    if not use_reasoning or not openai_client:
        best_score = scored_docs[0][1] if scored_docs else 1e9
        # Convert distance to similarity
        similarity = 1.0 / (1.0 + best_score)
        return docs, None, similarity

    # Step 3: Classify user intent
    try:
        user_intent = classify_intent(question)
        logger.info(
            f"Intent: {user_intent.primary_intent}, "
            f"Domain: {user_intent.domain}, "
            f"Confidence: {user_intent.confidence:.2f}"
        )
    except Exception as e:
        log_error(e, "Intent classification failed")
        return docs, None, 0.4

    # Step 4: Validate best match using reasoning
    best_doc, best_distance = scored_docs[0]
    best_similarity = 1.0 / (1.0 + best_distance)

    try:
        # Extract FAQ question from metadata
        faq_question = (best_doc.metadata or {}).get("title", "")
        faq_answer = best_doc.page_content

        if not faq_question or not faq_answer:
            logger.warning("Missing FAQ question or answer in metadata")
            return docs, None, best_similarity

        # Validate the match
        validation = validate_match(
            user_question=question,
            faq_question=faq_question,
            faq_answer=faq_answer,
            user_intent=user_intent
        )

        # Calculate overall confidence
        overall_confidence = calculate_overall_confidence(
            similarity_score=best_similarity,
            validation=validation,
            user_intent=user_intent
        )

        logger.info(
            f"Validation: valid={validation.is_valid}, "
            f"confidence={overall_confidence:.2f}, "
            f"recommendation={validation.recommendation}"
        )

        # Check if we should answer
        should_answer, reason = should_answer_with_confidence(
            overall_confidence,
            threshold=min_confidence
        )

        if not should_answer:
            logger.info(f"Confidence too low ({overall_confidence:.2f}): {reason}")
            return docs, validation, overall_confidence

        if not validation.is_valid:
            logger.warning(f"Match validation failed: {validation.reasoning}")
            return docs, validation, overall_confidence

        return docs, validation, overall_confidence

    except Exception as e:
        log_error(e, "Reasoning validation failed")
        return docs, None, best_similarity


def get_intelligent_suggestions(
    question: str,
    top_k: int = 4,
    min_similarity: float = 0.3,
    use_reasoning: bool = True
) -> List[Dict[str, Any]]:
    """
    Get suggestions with reasoning-based validation and filtering.

    Each suggestion is validated to ensure relevance before being returned.

    Args:
        question: User's question
        top_k: Number of suggestions to return
        min_similarity: Minimum similarity threshold
        use_reasoning: Whether to use reasoning validation

    Returns:
        List of validated suggestion dictionaries
    """
    # Get basic suggestions first
    suggestions = get_top_suggestions(question, top_k=top_k * 2, min_similarity=min_similarity)

    if not suggestions or not use_reasoning:
        return suggestions[:top_k]

    # Import reasoning module
    try:
        from .reasoning import classify_intent, validate_match, classify_domain
    except ImportError:
        return suggestions[:top_k]

    # Classify user intent once
    try:
        user_intent = classify_intent(question)
        user_domain = user_intent.domain
    except Exception as e:
        log_error(e, "Intent classification for suggestions failed")
        return suggestions[:top_k]

    # Validate each suggestion
    validated = []

    for sugg in suggestions:
        faq_q = sugg.get("question", "")
        faq_a = sugg.get("answer", "")

        if not faq_q or not faq_a:
            continue

        try:
            # Quick domain check
            sugg_domain = classify_domain(faq_q)

            # Only validate if domains match or are related
            if user_domain != "general" and sugg_domain != "general":
                if user_domain != sugg_domain:
                    # Skip if domains are completely unrelated
                    continue

            # Add domain info to suggestion
            sugg["domain"] = sugg_domain
            sugg["validated"] = True

            validated.append(sugg)

            if len(validated) >= top_k:
                break

        except Exception as e:
            log_error(e, "Suggestion validation failed")
            # Include anyway if validation fails
            validated.append(sugg)

    logger.info(f"Validated {len(validated)}/{len(suggestions)} suggestions")

    return validated[:top_k]


# ============================================================================
# JSONL FAQ INITIALIZATION
# ============================================================================

def initialize_faq_jsonl(jsonl_path: str = DEFAULT_FAQ_JSONL, rebuild_embeddings: bool = False) -> Dict[str, Any]:
    """
    Initialize FAQ from JSONL file and build embeddings.

    This is the main initialization function for the JSONL-based FAQ system.
    Call this at startup or when FAQ is updated.

    Args:
        jsonl_path: Path to FAQ JSONL file
        rebuild_embeddings: Force rebuild embeddings

    Returns:
        Dictionary with initialization status and stats
    """
    if not JSONL_AVAILABLE:
        return {
            "success": False,
            "error": "JSONL FAQ module not available"
        }

    try:
        logger.info("Initializing JSONL FAQ system...")

        # Get FAQ manager
        manager = get_faq_manager(jsonl_path)

        # Load FAQ entries
        entries = manager.load_faq()

        if not entries:
            logger.warning("No FAQ entries loaded")
            return {
                "success": False,
                "error": "No FAQ entries found",
                "entries_count": 0
            }

        logger.info(f"Loaded {len(entries)} FAQ entries")

        # Build embeddings
        if rebuild_embeddings or manager.vectorstore is None:
            logger.info("Building embeddings...")
            success = manager.build_embeddings(force_rebuild=rebuild_embeddings)

            if not success:
                return {
                    "success": False,
                    "error": "Failed to build embeddings",
                    "entries_count": len(entries)
                }

        # Get stats
        stats = manager.get_stats()

        # Build keyword index (always built, works without API)
        if SYNONYMS_AVAILABLE:
            try:
                build_keyword_index(entries)
                logger.info(f"✅ Keyword index built with synonym support ({len(entries)} entries)")
            except Exception as ke:
                logger.warning(f"Keyword index build failed (non-critical): {ke}")

        logger.info(f"✅ FAQ JSONL system initialized: {stats['total_entries']} entries")

        return {
            "success": True,
            "entries_count": stats['total_entries'],
            "keyword_index": SYNONYMS_AVAILABLE,
            "stats": stats,
            "message": "FAQ JSONL system ready"
        }

    except Exception as e:
        log_error(e, "Failed to initialize FAQ JSONL")
        return {
            "success": False,
            "error": str(e)
        }


def update_faq_entry(question: str, new_answer: str) -> Dict[str, Any]:
    """
    Update an FAQ entry and rebuild embeddings.

    Args:
        question: Question text to update
        new_answer: New answer text

    Returns:
        Dictionary with update status
    """
    if not JSONL_AVAILABLE:
        return {
            "success": False,
            "error": "JSONL FAQ module not available"
        }

    try:
        from .faq_jsonl import update_faq_jsonl

        logger.info(f"Updating FAQ entry: {question[:50]}...")

        success = update_faq_jsonl(question, new_answer)

        if success:
            logger.info("✅ FAQ entry updated and embeddings rebuilt")
            return {
                "success": True,
                "message": "FAQ entry updated successfully"
            }
        else:
            return {
                "success": False,
                "error": "Failed to update FAQ entry"
            }

    except Exception as e:
        log_error(e, "Failed to update FAQ entry", question=question[:50])
        return {
            "success": False,
            "error": str(e)
        }


def get_faq_stats() -> Dict[str, Any]:
    """
    Get FAQ statistics.

    Returns:
        Dictionary with FAQ stats
    """
    if not JSONL_AVAILABLE:
        return {
            "system": "legacy",
            "available": False
        }

    try:
        manager = get_faq_manager()
        stats = manager.get_stats()
        stats["system"] = "jsonl"
        stats["available"] = True
        return stats

    except Exception as e:
        log_error(e, "Failed to get FAQ stats")
        return {
            "system": "jsonl",
            "available": False,
            "error": str(e)
        }
