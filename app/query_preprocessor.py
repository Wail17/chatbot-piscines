# app/query_preprocessor.py
"""
Query preprocessor for the chatbot.

Analyzes incoming queries BEFORE they go to the RAG pipeline to:
1. Detect and respond to greetings
2. Detect empty/too-short/spam queries
3. Detect out-of-scope questions (not about pools/Wifipool)
4. Detect follow-up questions and annotate them
5. Clean and normalize query text
6. Detect the user's intent category (question, greeting, feedback, etc.)

This runs instantly with NO API calls.
"""

import re
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ─── INTENT TYPES ─────────────────────────────────────────────────────────────

class QueryIntent(str, Enum):
    QUESTION = "question"          # Normal pool-related question
    GREETING = "greeting"          # "Hello", "Bonjour", "Hoi", etc.
    THANKS = "thanks"              # "Thank you", "Merci", "Bedankt"
    GOODBYE = "goodbye"            # "Bye", "Au revoir", "Tot ziens"
    HELP = "help"                  # "Help", "Aide", "Hulp"
    FOLLOWUP = "followup"          # "Why?", "How?", "And then?", etc.
    OUT_OF_SCOPE = "out_of_scope"  # Not about pools at all
    SPAM = "spam"                  # Too short, garbage, repeated chars
    EMPTY = "empty"                # Empty or whitespace only


@dataclass
class ProcessedQuery:
    """Result of query preprocessing."""
    original: str
    cleaned: str
    intent: QueryIntent
    language_hint: Optional[str] = None        # Detected language (best effort)
    is_pool_related: bool = True
    is_followup: bool = False
    immediate_response: Optional[Dict[str, Any]] = None  # If we can answer without RAG
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── PATTERN DICTIONARIES ─────────────────────────────────────────────────────

_GREETINGS = {
    "nl": ["hoi", "hallo", "goedemorgen", "goedemiddag", "goedenavond",
           "dag", "salut", "goededag", "hé", "hey"],
    "fr": ["bonjour", "bonsoir", "salut", "coucou", "allô", "allo",
           "bonne journée"],
    "en": ["hello", "hi", "hey", "good morning", "good afternoon",
           "good evening", "howdy", "greetings"],
    "de": ["hallo", "guten morgen", "guten tag", "guten abend", "servus", "moin"],
    "es": ["hola", "buenos días", "buenas tardes", "buenas noches"],
    "it": ["ciao", "buongiorno", "buonasera", "salve"],
}

_THANKS = {
    "nl": ["bedankt", "dank je", "dank u", "dank u wel", "merci", "thanks",
           "super bedankt", "heel erg bedankt", "top"],
    "fr": ["merci", "merci beaucoup", "je vous remercie", "c'est parfait",
           "super merci"],
    "en": ["thank you", "thanks", "thank you so much", "great thanks",
           "many thanks", "cheers", "awesome"],
    "de": ["danke", "danke schön", "vielen dank", "danke sehr"],
    "es": ["gracias", "muchas gracias"],
    "it": ["grazie", "molte grazie"],
}

_GOODBYES = {
    "nl": ["tot ziens", "doei", "dag", "ajuus", "tot later", "bye", "ciao",
           "tot morgen"],
    "fr": ["au revoir", "à bientôt", "bonne journée", "bonne soirée",
           "ciao", "adieu"],
    "en": ["goodbye", "bye", "see you", "take care", "see you later",
           "good night", "later"],
    "de": ["auf wiedersehen", "tschüss", "tschau", "bis später"],
}

_HELP_REQUESTS = {
    "nl": ["help", "hulp", "ik weet het niet", "i don't know", "hoe werkt dit"],
    "fr": ["aide", "j'ai besoin d'aide", "help"],
    "en": ["help", "i need help", "can you help", "how does this work"],
}

_FOLLOWUP_PATTERNS = [
    r"^(waarom|pourquoi|why|warum|perché|por qué)\??$",
    r"^(hoe|comment|how|wie|come|cómo)\??$",
    r"^(en dan|and then|et ensuite|und dann)\??$",
    r"^(wat bedoel je|what do you mean|que voulez-vous dire)\??$",
    r"^(meer info|plus d'info|more info|meer details|plus de détails)\??$",
    r"^(leg uit|explain|explique|erklär)\??$",
    r"^(kan je dat herhalen|repeat|répète|nochmal)\??$",
    r"^(ok|okay|oke|oke\?|oké)\?*$",
    r"^(ja|oui|yes|sí|ja\?|yes\?)\?*$",
    r"^(nee|non|no|nein)\?*$",
    r"^(verder|next|suite|weiter)\?*$",
]

# Pool-related keywords (if a query has NONE of these, it's likely out of scope)
_POOL_KEYWORDS = [
    # NL
    "zwembad", "piscine", "pool", "wifipool", "wifi", "ph", "chloor",
    "orp", "zout", "sensor", "sonde", "pomp", "filter", "calibr",
    "doseer", "elektrolyse", "wifipool", "gen1", "gen2", "gen3",
    "beniferro", "app", "installat", "timer", "temperatuur",
    "meetwaarde", "instellingen", "reset", "verbinding", "netwerk",
    "kalibreer", "kalibreer", "alkaliniteit", "hardheid",
    # FR
    "piscine", "chlore", "capteur", "pompe", "filtre", "mesure",
    "électrolyse", "sel", "réseau", "connexion", "paramètre",
    "calibrat", "réinitialis",
    # EN
    "pool", "sensor", "probe", "pump", "filter", "calibrat",
    "electrolysis", "salt", "water", "chlorine", "measurement",
    "reset", "connection", "network", "temperature",
]

# Topics that are clearly NOT pool related
_OUT_OF_SCOPE_PATTERNS = [
    r"\b(pizza|restaurant|weather|météo|weer|sport|football|voetbal)\b",
    r"\b(president|politics|guerre|war|coronavirus|covid)\b",
    r"\b(recipe|recept|recette|cuisine|cooking)\b",
    r"\b(amazon|netflix|facebook|instagram|twitter|tiktok)\b",
]


# ─── RESPONSE TEMPLATES ───────────────────────────────────────────────────────

_GREETING_RESPONSES = {
    "nl": "Hallo! Ik ben de Wifipool assistent. Hoe kan ik je helpen met je zwembad of apparaat?",
    "fr": "Bonjour ! Je suis l'assistant Wifipool. Comment puis-je vous aider avec votre piscine ou appareil ?",
    "en": "Hello! I'm the Wifipool assistant. How can I help you with your pool or device?",
    "de": "Hallo! Ich bin der Wifipool-Assistent. Wie kann ich Ihnen mit Ihrem Pool oder Gerät helfen?",
    "es": "¡Hola! Soy el asistente Wifipool. ¿Cómo puedo ayudarle con su piscina o dispositivo?",
    "it": "Ciao! Sono l'assistente Wifipool. Come posso aiutarti con la tua piscina o dispositivo?",
}

_THANKS_RESPONSES = {
    "nl": "Graag gedaan! Als je nog vragen hebt over je Wifipool of zwembad, stel ze gerust.",
    "fr": "Avec plaisir ! N'hésitez pas si vous avez d'autres questions sur votre Wifipool ou piscine.",
    "en": "You're welcome! Feel free to ask if you have any more questions about your Wifipool or pool.",
    "de": "Gern geschehen! Wenn Sie weitere Fragen zu Ihrem Wifipool oder Pool haben, fragen Sie gerne.",
    "es": "¡De nada! Si tiene más preguntas sobre su Wifipool o piscina, no dude en preguntar.",
    "it": "Prego! Se hai altre domande sul tuo Wifipool o piscina, non esitare a chiedere.",
}

_GOODBYE_RESPONSES = {
    "nl": "Tot ziens! Nog veel plezier met je zwembad. 🏊",
    "fr": "Au revoir ! Profitez bien de votre piscine. 🏊",
    "en": "Goodbye! Enjoy your pool. 🏊",
    "de": "Auf Wiedersehen! Viel Spaß mit Ihrem Pool. 🏊",
    "es": "¡Adiós! Que disfrute de su piscina. 🏊",
    "it": "Arrivederci! Goditevi la piscina. 🏊",
}

_OUT_OF_SCOPE_RESPONSES = {
    "nl": "Ik ben gespecialiseerd in zwembad- en Wifipool-ondersteuning. Kan ik je helpen met je zwembad, pH, chloor, sensoren of apparaat?",
    "fr": "Je suis spécialisé dans le support piscine et Wifipool. Puis-je vous aider avec votre piscine, pH, chlore, capteurs ou appareil ?",
    "en": "I specialize in pool and Wifipool support. Can I help you with your pool, pH, chlorine, sensors or device?",
    "de": "Ich bin auf Pool- und Wifipool-Support spezialisiert. Kann ich Ihnen mit Ihrem Pool, pH, Chlor, Sensoren oder Gerät helfen?",
}

_EMPTY_QUERY_RESPONSES = {
    "nl": "Je hebt een lege vraag gestuurd. Stel gerust je vraag over je Wifipool of zwembad.",
    "fr": "Vous avez envoyé une question vide. N'hésitez pas à poser votre question sur votre Wifipool ou piscine.",
    "en": "You sent an empty query. Feel free to ask your question about your Wifipool or pool.",
}

_SUGGESTIONS_DEFAULT = {
    "nl": [
        "Hoe kalibreer ik mijn pH sonde?",
        "Hoe reset ik mijn Wifipool apparaat?",
        "Waarom is mijn WiFi verbinding verbroken?",
        "Hoe pas ik de chloor dosering aan?",
    ],
    "fr": [
        "Comment calibrer ma sonde pH?",
        "Comment réinitialiser mon appareil Wifipool?",
        "Pourquoi ma connexion WiFi est-elle perdue?",
        "Comment ajuster le dosage de chlore?",
    ],
    "en": [
        "How do I calibrate my pH sensor?",
        "How do I reset my Wifipool device?",
        "Why is my WiFi connection lost?",
        "How do I adjust chlorine dosage?",
    ],
}


# ─── LANGUAGE DETECTION (NO API) ──────────────────────────────────────────────

def _quick_detect_language(text: str) -> Optional[str]:
    """
    Quick rule-based language detection for common words.
    No API needed. Returns None if uncertain.
    """
    text_lower = text.lower()
    words = set(re.findall(r'\b\w+\b', text_lower))

    # Strong indicators per language
    nl_words = {"de", "het", "een", "van", "ik", "hoe", "wat", "mijn", "kan", "wel",
                "niet", "zijn", "hebben", "gaat", "wil", "maar"}
    fr_words = {"le", "la", "les", "je", "un", "une", "des", "est", "pas", "mon",
                "ma", "comment", "pourquoi", "que", "qui", "pour"}
    en_words = {"the", "is", "are", "how", "why", "what", "when", "my", "can",
                "does", "not", "this", "that", "with"}
    de_words = {"der", "die", "das", "ein", "eine", "ich", "ist", "nicht", "haben",
                "warum", "wie", "mein", "bitte"}

    scores = {
        "nl": len(words & nl_words),
        "fr": len(words & fr_words),
        "en": len(words & en_words),
        "de": len(words & de_words),
    }

    best_lang, best_score = max(scores.items(), key=lambda x: x[1])
    return best_lang if best_score >= 2 else None


# ─── MAIN PREPROCESSOR ────────────────────────────────────────────────────────

def preprocess_query(
    query: str,
    detected_language: Optional[str] = None,
) -> ProcessedQuery:
    """
    Preprocess a user query before passing to RAG.

    Detects intent, cleans query, and returns immediate response
    if the query doesn't need RAG (greetings, thanks, out-of-scope, etc.)

    Args:
        query: Raw user query
        detected_language: Language code if already detected

    Returns:
        ProcessedQuery with intent, cleaned text, and optional immediate response
    """
    original = query or ""
    cleaned = _clean_query(original)

    # ── Empty / too short ─────────────────────────────────────────────────────
    if not cleaned or len(cleaned.strip()) < 2:
        lang = detected_language or "nl"
        return ProcessedQuery(
            original=original,
            cleaned=cleaned,
            intent=QueryIntent.EMPTY,
            language_hint=lang,
            is_pool_related=False,
            immediate_response={
                "answer": _EMPTY_QUERY_RESPONSES.get(lang, _EMPTY_QUERY_RESPONSES["nl"]),
                "suggestions": _SUGGESTIONS_DEFAULT.get(lang, _SUGGESTIONS_DEFAULT["nl"]),
                "language": lang,
            },
        )

    # ── Spam detection ────────────────────────────────────────────────────────
    if _is_spam(cleaned):
        lang = detected_language or "nl"
        return ProcessedQuery(
            original=original,
            cleaned=cleaned,
            intent=QueryIntent.SPAM,
            language_hint=lang,
            is_pool_related=False,
            immediate_response={
                "answer": _EMPTY_QUERY_RESPONSES.get(lang, _EMPTY_QUERY_RESPONSES["nl"]),
                "suggestions": _SUGGESTIONS_DEFAULT.get(lang, _SUGGESTIONS_DEFAULT["nl"]),
                "language": lang,
            },
        )

    # ── Language detection ────────────────────────────────────────────────────
    lang = detected_language or _quick_detect_language(cleaned) or "nl"

    # ── Greeting detection ────────────────────────────────────────────────────
    greeting_lang = _detect_greeting(cleaned)
    if greeting_lang:
        response_lang = greeting_lang or lang
        return ProcessedQuery(
            original=original,
            cleaned=cleaned,
            intent=QueryIntent.GREETING,
            language_hint=response_lang,
            is_pool_related=False,
            immediate_response={
                "answer": _GREETING_RESPONSES.get(response_lang, _GREETING_RESPONSES["en"]),
                "suggestions": _SUGGESTIONS_DEFAULT.get(response_lang, _SUGGESTIONS_DEFAULT["nl"]),
                "language": response_lang,
            },
        )

    # ── Thanks detection ──────────────────────────────────────────────────────
    thanks_lang = _detect_thanks(cleaned)
    if thanks_lang:
        response_lang = thanks_lang or lang
        return ProcessedQuery(
            original=original,
            cleaned=cleaned,
            intent=QueryIntent.THANKS,
            language_hint=response_lang,
            is_pool_related=False,
            immediate_response={
                "answer": _THANKS_RESPONSES.get(response_lang, _THANKS_RESPONSES["en"]),
                "suggestions": _SUGGESTIONS_DEFAULT.get(response_lang, _SUGGESTIONS_DEFAULT["nl"]),
                "language": response_lang,
            },
        )

    # ── Goodbye detection ─────────────────────────────────────────────────────
    goodbye_lang = _detect_goodbye(cleaned)
    if goodbye_lang:
        response_lang = goodbye_lang or lang
        return ProcessedQuery(
            original=original,
            cleaned=cleaned,
            intent=QueryIntent.GOODBYE,
            language_hint=response_lang,
            is_pool_related=False,
            immediate_response={
                "answer": _GOODBYE_RESPONSES.get(response_lang, _GOODBYE_RESPONSES["en"]),
                "suggestions": [],
                "language": response_lang,
            },
        )

    # ── Follow-up detection ───────────────────────────────────────────────────
    is_followup = _detect_followup(cleaned)

    # ── Out-of-scope detection ────────────────────────────────────────────────
    if not is_followup and _is_out_of_scope(cleaned):
        response_lang = lang
        return ProcessedQuery(
            original=original,
            cleaned=cleaned,
            intent=QueryIntent.OUT_OF_SCOPE,
            language_hint=response_lang,
            is_pool_related=False,
            immediate_response={
                "answer": _OUT_OF_SCOPE_RESPONSES.get(response_lang, _OUT_OF_SCOPE_RESPONSES["en"]),
                "suggestions": _SUGGESTIONS_DEFAULT.get(response_lang, _SUGGESTIONS_DEFAULT["nl"]),
                "language": response_lang,
            },
        )

    # ── Normal question ───────────────────────────────────────────────────────
    return ProcessedQuery(
        original=original,
        cleaned=cleaned,
        intent=QueryIntent.QUESTION if not is_followup else QueryIntent.FOLLOWUP,
        language_hint=lang,
        is_pool_related=True,
        is_followup=is_followup,
        metadata={
            "word_count": len(cleaned.split()),
            "has_question_mark": "?" in cleaned,
        },
    )


# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────

def _clean_query(text: str) -> str:
    """Clean and normalize query text."""
    if not text:
        return ""

    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text.strip())

    # Remove very common noise characters
    text = text.strip('.,!;: ')

    return text


def _is_spam(text: str) -> bool:
    """Detect spam/garbage queries."""
    if not text or len(text.strip()) < 2:
        return True

    # Repeated characters: "aaaaaaa", "???????????"
    if re.match(r'^(.)\1{5,}$', text.strip()):
        return True

    # All numbers
    if re.match(r'^\d+$', text.strip()):
        return True

    # Too short to be meaningful
    if len(text.strip()) < 3:
        return True

    return False


def _detect_greeting(text: str) -> Optional[str]:
    """Detect greetings. Returns language code or None."""
    text_lower = text.lower().strip().rstrip('!?.,:')

    for lang, phrases in _GREETINGS.items():
        for phrase in phrases:
            if text_lower == phrase or text_lower.startswith(phrase + " ") or text_lower.startswith(phrase + "!"):
                return lang
            # Also check: "bonjour, j'ai une question" etc.
            if text_lower.startswith(phrase) and len(text_lower) <= len(phrase) + 15:
                return lang

    return None


def _detect_thanks(text: str) -> Optional[str]:
    """Detect thank-you messages. Returns language code or None."""
    text_lower = text.lower().strip().rstrip('!?.,:')

    for lang, phrases in _THANKS.items():
        for phrase in phrases:
            if text_lower == phrase or text_lower.startswith(phrase):
                return lang

    return None


def _detect_goodbye(text: str) -> Optional[str]:
    """Detect goodbye messages. Returns language code or None."""
    text_lower = text.lower().strip().rstrip('!?.,:')

    for lang, phrases in _GOODBYES.items():
        for phrase in phrases:
            if text_lower == phrase or text_lower.startswith(phrase):
                return lang

    return None


def _detect_followup(text: str) -> bool:
    """Detect if this is a follow-up to a previous question."""
    text_lower = text.lower().strip()

    for pattern in _FOLLOWUP_PATTERNS:
        if re.match(pattern, text_lower, re.IGNORECASE):
            return True

    # Very short queries (1-2 words) are often follow-ups
    words = text_lower.split()
    if len(words) <= 2 and not any(kw in text_lower for kw in _POOL_KEYWORDS):
        return True

    return False


def _is_out_of_scope(text: str) -> bool:
    """
    Detect if query is clearly not about pools/Wifipool.

    Only flags as out-of-scope if:
    1. Contains out-of-scope indicators AND
    2. Contains NO pool-related keywords
    """
    text_lower = text.lower()

    # Check if any pool keyword is present
    has_pool_keyword = any(kw in text_lower for kw in _POOL_KEYWORDS)
    if has_pool_keyword:
        return False

    # Check for out-of-scope patterns
    for pattern in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, text_lower):
            return True

    return False
