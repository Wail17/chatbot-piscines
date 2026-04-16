# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os, json, re, unicodedata, time
import logging
from math import sqrt
from threading import Lock
from difflib import SequenceMatcher, get_close_matches

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_openai import OpenAIEmbeddings
from openai import OpenAI

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,
    extract_found_gens,
    detect_language_code,
    translate_answer,
    translate_for_matching,
    get_top_suggestions,
    polish_faq_answer,
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback, vectorstore_status
from .admin_routes import admin_router
from .config import (
    CORRECTION_THRESHOLD, STORE_DIR, DATA_DIR, EMBEDDINGS_MODEL, LLM_MODEL,
    # optionnel si tu veux un /health bavard:
    # CHROMA_DIR, EMBEDDINGS_MODEL, FEEDBACK_FILE, CORRECTIONS_COLLECTION
)

# ── New feature modules (all optional – graceful degradation) ────────────────
try:
    from .response_cache import init_cache, cache_get, cache_set, cache_stats, normalize_for_cache
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False
    def cache_get(q): return None
    def cache_set(q, r): pass
    def cache_stats(): return {"enabled": False}
    def normalize_for_cache(q): return q.lower().strip()

try:
    from .query_preprocessor import preprocess_query, QueryIntent
    _PREPROCESSOR_AVAILABLE = True
except ImportError:
    _PREPROCESSOR_AVAILABLE = False
    preprocess_query = None

try:
    from .analytics import (
        init_analytics, track_question, track_no_answer,
        track_cache_hit, track_error, get_analytics_report, get_faq_gaps,
    )
    _ANALYTICS_AVAILABLE = True
except ImportError:
    _ANALYTICS_AVAILABLE = False
    def track_question(*a, **kw): pass
    def track_no_answer(*a, **kw): pass
    def track_cache_hit(*a, **kw): pass
    def track_error(*a, **kw): pass
    def get_analytics_report(*a, **kw): return {}
    def get_faq_gaps(*a, **kw): return []

try:
    from .direct_answer import get_direct_answer_with_suggestions
    _DIRECT_ANSWER_AVAILABLE = True
except ImportError:
    _DIRECT_ANSWER_AVAILABLE = False
    get_direct_answer_with_suggestions = None

try:
    from .synonyms import SYNONYM_GROUPS as _IMPORTED_SYNONYM_GROUPS
    _SYNONYMS_MODULE_AVAILABLE = True
except ImportError:
    _SYNONYMS_MODULE_AVAILABLE = False
    _IMPORTED_SYNONYM_GROUPS = []

logger = logging.getLogger(__name__)

app = FastAPI(title="Chatbot Piscines API")

# Include admin router
app.include_router(admin_router)

# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    "https://chatbot-piscines.onrender.com",
    "http://localhost:8080",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "ngrok-skip-browser-warning",
        "Authorization",
        "Accept",
        "Origin",
    ],
    expose_headers=["Content-Type"],
)

# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None
    top_k: int = 1  # Nombre de suggestions à retourner (1 = mode traditionnel, >1 = suggestions multiples)
    min_similarity: float = 0.3  # Score de similarité minimum (0-1)

class IngestRequest(BaseModel):
    path: str
    source_type: str = "mixed"

class CorrectionIn(BaseModel):
    question: str
    answer: str
    # important: pas de liste mutable par défaut
    tags: List[str] = Field(default_factory=list)

class FeedbackIn(BaseModel):
    question: str
    answer: str
    good: bool
    corrected_answer: Optional[str] = None
    notes: Optional[str] = None
    user: Optional[str] = None

# ---------------------------------------------------------------------
# Tips / Const
# ---------------------------------------------------------------------
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Bij ‘toestellen zoeken’: Gen 1 toont meestal meerdere modules; Gen 2 meestal maar 1 module.",
    "3) Gen 1 heeft vaak een USB 5V stekker; Gen 2 heeft 220V of 12V stekker.",
]

# ---------------------------------------------------------------------
# Load FAQ
# ---------------------------------------------------------------------
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
_FAQ_FALLBACK_JSONL = os.path.join(DATA_DIR, "all", "faq", "FAQAI.jsonl")
_FAQ: List[dict] = []
_EMBED_UNSET = object()
_FAQ_EMBED_LOCK: Lock = Lock()
_FAQ_EMBEDDER: Optional[OpenAIEmbeddings] = None
_FAQ_EMBED_DISABLED = False

# Initialize OpenAI client for GPT fallback
_openai_client: Optional[OpenAI] = None
_api_key = os.environ.get("OPENAI_API_KEY")
if _api_key:
    try:
        _openai_client = OpenAI(api_key=_api_key)
    except Exception as e:
        logger.warning(f"Failed to initialize OpenAI client: {e}")
        _openai_client = None
else:
    logger.warning("Missing OPENAI_API_KEY - GPT fallback will be disabled")

logger.debug(f"FAQ file path: {_FAQ_FALLBACK_JSONL}, exists: {os.path.exists(_FAQ_FALLBACK_JSONL)}")


def _load_faq_from_store() -> List[dict]:
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _coerce_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _load_faq_from_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        logger.warning(f"FAQ JSONL not found: {path}")
        return []

    rows: List[dict] = []
    line_count = 0
    skipped_empty_q = 0
    skipped_parse_error = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line_count += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception as e:
                    skipped_parse_error += 1
                    continue

                question = _coerce_str(
                    obj.get("vraag")
                    or obj.get("Vraag")
                    or obj.get("question")
                    or obj.get("Question")
                )
                if not question:
                    skipped_empty_q += 1
                    continue

                category = _coerce_str(
                    obj.get("categorie")
                    or obj.get("Categorie")
                    or obj.get("category")
                    or obj.get("Category")
                )
                answer = _coerce_str(
                    obj.get("antwoord")
                    or obj.get("Antwoord")
                    or obj.get("answer")
                    or obj.get("Answer")
                )
                follow_raw = (
                    obj.get("follow_up")
                    or obj.get("Follow_up")
                    or obj.get("followUp")
                    or obj.get("FollowUp")
                )
                if isinstance(follow_raw, str):
                    follow_up = follow_raw.strip().lower() in {"1", "true", "yes", "ja"}
                else:
                    follow_up = bool(follow_raw)
                followup_q = _coerce_str(
                    obj.get("follow_up_question")
                    or obj.get("followup_q")
                    or obj.get("clarify_question")
                )

                options_raw = (
                    obj.get("opties")
                    or obj.get("Opties")
                    or obj.get("options")
                    or obj.get("Options")
                    or {}
                )
                options: Dict[str, Any] = {}
                if isinstance(options_raw, dict):
                    for label, payload in options_raw.items():
                        key = _coerce_str(label) or str(label)
                        options[key] = payload

                row: Dict[str, Any] = {
                    "category": category,
                    "question": question,
                    "answer": answer if not follow_up else (answer or ""),
                    "follow_up": follow_up,
                    "followup_q": followup_q if (follow_up and followup_q) else None,
                    "options": options,
                    "source": path,
                }

                video = (
                    obj.get("video_url")
                    or obj.get("video")
                    or obj.get("Video")
                    or obj.get("Filmpje")
                    or obj.get("filmpje")
                )
                media = obj.get("media") if isinstance(obj.get("media"), dict) else None
                if not video and media:
                    video = media.get("video") or media.get("url")
                video_str = _coerce_str(video)
                if video_str:
                    row["video_url"] = video_str

                image_path = _coerce_str(
                    obj.get("image_path")
                    or obj.get("image_url")
                    or obj.get("image")
                    or obj.get("Foto")
                )
                if image_path:
                    row["image_path"] = image_path

                alt_raw = obj.get("alt_questions") or obj.get("alternatieve_vragen") or []
                if isinstance(alt_raw, list):
                    alt_list = [str(a).strip() for a in alt_raw if str(a).strip()]
                    if alt_list:
                        row["alt_questions"] = alt_list

                tags = obj.get("tags")
                if isinstance(tags, list):
                    row["tags"] = [str(t).strip() for t in tags if str(t).strip()]

                for src_key, dst_key in [
                    ("ENQuestion", "ENQuestion"), ("EN_Question", "ENQuestion"),
                    ("ENAnswer", "ENAnswer"), ("EN_Answer", "ENAnswer"),
                    ("FRQuestion", "FRQuestion"), ("FR_Question", "FRQuestion"),
                    ("FRReponse", "FRReponse"), ("FR_Reponse", "FRReponse"),
                    ("DEFrage", "DEFrage"), ("DE_Frage", "DEFrage"),
                    ("DEAntwort", "DEAntwort"), ("DE_Antwort", "DEAntwort"),
                ]:
                    val = _coerce_str(obj.get(src_key))
                    if val:
                        row[dst_key] = val

                excel_row_raw = obj.get("excel_row")
                if isinstance(excel_row_raw, int):
                    row["excel_row"] = excel_row_raw
                elif isinstance(excel_row_raw, str) and excel_row_raw.strip().isdigit():
                    row["excel_row"] = int(excel_row_raw.strip())

                rows.append(row)
    except Exception as e:
        logger.error(f"ERROR loading FAQ JSONL: {type(e).__name__}: {e}")
        return []

    logger.info(f"FAQ loaded: {len(rows)} items ({skipped_parse_error} parse errors, {skipped_empty_q} empty questions)")
    return rows


def _reset_faq_embeddings() -> None:
    global _FAQ_EMBEDDER, _FAQ_EMBED_DISABLED
    with _FAQ_EMBED_LOCK:
        _FAQ_EMBEDDER = None
        _FAQ_EMBED_DISABLED = False


def _reload_faq() -> Tuple[int, List[dict]]:
    data: List[dict] = _load_faq_from_store()
    if not data:
        data = _load_faq_from_jsonl(_FAQ_FALLBACK_JSONL)
    global _FAQ
    _FAQ = data
    if not _FAQ:
        logger.error("FAQ is empty after reload!")
    else:
        logger.info(f"FAQ reloaded: {len(_FAQ)} items")
    _reset_faq_embeddings()
    for row in _FAQ:
        row["_embedding"] = _EMBED_UNSET
    return (len(_FAQ), _FAQ)

_reload_faq()

# ── Initialize feature modules ──────────────────────────────────────────────
if _CACHE_AVAILABLE:
    try:
        init_cache(store_dir=STORE_DIR)
    except Exception as _e:
        logger.warning(f"Cache init failed: {_e}")

if _ANALYTICS_AVAILABLE:
    try:
        init_analytics(store_dir=STORE_DIR)
    except Exception as _e:
        logger.warning(f"Analytics init failed: {_e}")

# ─────────────────────────────────────────────────────────────────────────────
# Simple in-memory rate limiter (per IP, no external deps)
# ─────────────────────────────────────────────────────────────────────────────
_RATE_LIMIT_WINDOW = 60      # seconds
_RATE_LIMIT_MAX = 30         # requests per window per IP
_rate_store: Dict[str, List[float]] = {}
_rate_lock = Lock()


def _check_rate_limit(client_ip: str) -> bool:
    """Returns True if request is allowed, False if rate limit exceeded."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    with _rate_lock:
        timestamps = _rate_store.get(client_ip, [])
        # Drop old timestamps
        timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            _rate_store[client_ip] = timestamps
            return False
        timestamps.append(now)
        _rate_store[client_ip] = timestamps
        return True

# ---------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»""\"'`]\s*")
# Lowered thresholds to improve matching (was 0.68, 0.78, 0.63, 0.84)
_MATCH_THRESHOLD = 0.55  # Was 0.68 - too high
_SEMANTIC_MATCH_THRESHOLD = 0.65  # Was 0.78 - too high
_SEMANTIC_TRIGGER = 0.50  # Was 0.63 - too high
_CERTAINTY_THRESHOLD = 0.75  # Was 0.84 - too high
_CERTAINTY_GAP = 0.1
_AMBIGUITY_GAP = 0.06
_AMBIGUITY_PEER_THRESHOLD = 0.55
_AMBIGUITY_STOPWORDS = {
    "hoe",
    "ik",
    "kan",
    "een",
    "de",
    "het",
    "in",
    "op",
    "wat",
    "je",
    "met",
    "voor",
    "en",
    "of",
    "is",
    "moet",
    "mijn",
    "van",
    "aan",
    "te",
    "heb",
    "hebt",
    "wil",
    "wifipool",
    "apparaat",
}


_ORDINAL_ALIASES: Dict[str, int] = {
    "eerste": 1,
    "tweede": 2,
    "derde": 3,
    "vierde": 4,
    "vijfde": 5,
    "zesde": 6,
    "seventh": 7,
    "zevende": 7,
    "achtste": 8,
    "negende": 9,
    "tiende": 10,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _tokens(s: str) -> Set[str]:
    return {tok for tok in s.split(" ") if tok}


def _text_contains(haystack_norm: str, needle_norm: str) -> bool:
    if not haystack_norm or not needle_norm:
        return False
    if needle_norm in haystack_norm or haystack_norm in needle_norm:
        return True
    hay_tokens = _tokens(haystack_norm)
    needle_tokens = _tokens(needle_norm)
    if not hay_tokens or not needle_tokens:
        return False
    return needle_tokens <= hay_tokens

def _normalize(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\u00a0", " ").lower()
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


_SYNONYM_GROUPS: Dict[str, Set[str]] = {
    "reset": {
        "reset",
        "hard reset",
        "harde reset",
        "hardreset",
        "harde herstart",
        "herstart",
        "herstarten",
        "restart",
        "opnieuw starten",
        "factory reset",
        "resetten",
        "hard-reset",
        "reboot",
        "rebooten",
        "herinitialiseren",
        "reinitialiseren",
        "harder reset",
        "harder herstart",
        "hard resetten",
        "reset complet",
        "reinitialisation",
        "réinitialisation",
        "réinitialiser",
        "reinitialiser",
        "resetear",
        "reiniciar",
        "hard reset durchführen",
        "hard reset ausführen",
        "hard-reset durchführen",
    },
    "temperatuur": {
        "temperatuur",
        "temperatuursensor",
        "temperatuurmeting",
        "temperature",
        "temp",
        "thermosonde",
        "thermometer",
        "warmte",
        "sonde",
    },
    "apparaat": {
        "apparaat",
        "toestel",
        "device",
        "appareil",
        "gerät",
        "equipment",
    },
    "doen": {
        "doen",
        "faire",
        "machen",
        "do",
        "does",
        "doit",
        "uitvoeren",
        "uit te voeren",
        "uitvoering",
        "uitvoeren van",
        "voer",
        "voeren",
        "uitvoer",
        "perform",
        "performer",
    },
    "niveau": {
        "niveau",
        "niveauregelaar",
        "niveau meting",
        "niveau regeling",
        "waterniveau",
        "vlotter",
        "float",
        "level",
    },
    "flow": {
        "flow",
        "debiet",
        "debietmeter",
        "flowswitch",
        "flowmeting",
        "flow sensor",
        "flowmeter",
    },
    "pomp": {
        "pomp",
        "pompe",
        "pump",
        "circulatiepomp",
        "circulation pump",
    },
    "sensor": {
        "sensor",
        "sonde",
        "probe",
        "meter",
        "meting",
    },
    "koppelen": {
        "koppelen",
        "connect",
        "connecter",
        "verbinden",
        "anschließen",
        "linken",
    },
    "kalibratie": {
        "kalibreren",
        "kalibratie",
        "calibratie",
        "calibrate",
        "calibration",
    },
    "wifi": {
        "wifi",
        "wi-fi",
        "wlan",
        "wireless",
    },
    "wifipool": {
        "wifipool",
        "wifi pool",
        "wifi-pool",
    },
    "benisol": {
        "benisol",
        "zonder wifi",
    },
    "zout": {
        "zout",
        "zoutelektrolyse",
        "electrolyse",
        "elektrolyse",
        "chlorinator",
        "salt",
        "saltelektrolyse",
        "salzelektrolyse",
        "salzelectrolyse",
        "zoutelektrolysetoestel",
        "elektrolysetoestel",
        "zout elektrolyse",
        "salt electrolysis",
        "electrolysis",
        "electrolyser",
        "electrolyzer",
        "electrolyseur",
        "électrolyseur",
        "saltwater chlorinator",
        "salt chlorinator",
        "salzelektrolysegerät",
        "salzwasser chlorinator",
        "chlorelektrolyse",
        "électrolyse",
        "electrólisis",
    },
    "start": {
        "start",
        "starten",
        "start niet",
        "start niet op",
        "gaat niet aan",
        "gaat niet aanzetten",
        "wil niet starten",
        "wil niet opstarten",
        "springt niet aan",
        "aangaan",
        "aanzetten",
        "opstarten",
        "inschakelen",
        "startet nicht",
        "geht nicht an",
        "einschalten",
        "anschalten",
        "start nicht",
        "lässt sich nicht einschalten",
        "ne démarre pas",
        "ne demarre pas",
        "demarrer",
        "démarrer",
        "allumer",
        "s'allumer",
        "s allumer",
        "s'allume",
        "s allume",
        "ne s'allume pas",
        "ne s allume pas",
        "mettre en marche",
        "mettre en route",
        "se met en route",
        "ne se met pas en route",
        "turn on",
        "turning on",
        "switch on",
        "power on",
        "not on",
        "won't turn on",
        "wont turn on",
        "not starting",
        "won't start",
        "wont start",
        "doesn't start",
        "does not start",
        "no arranca",
    },
    "gen1": {
        "gen1",
        "gen 1",
        "generation 1",
        "g1",
    },
    "gen2": {
        "gen2",
        "gen 2",
        "generation 2",
        "g2",
    },
    "gen3": {
        "gen3",
        "gen 3",
        "generation 3",
        "g3",
    },
    "zwembad": {
        "zwembad",
        "pool",
        "piscine",
    },
    "afwijken": {
        "afwijken",
        "wijkt af",
        "wijken af",
        "afwijkt",
        "afwijking",
        "afwijkingen",
        "varieert",
        "varieren",
        "fluctueert",
        "fluctueren",
        "instabiel",
        "onstabiel",
        "schommelt",
        "schommelen",
        "deviate",
        "deviates",
        "deviating",
        "deviation",
        "fluctuate",
        "fluctuates",
        "varier",
        "varie",
        "fluctuer",
        "fluctue",
        "d\u00e9vier",
        "devier",
        "d\u00e9vie",
        "devie",
    },
    "manueel": {
        "manueel",
        "manueel aan",
        "manueel aanschakelen",
        "manueel zetten",
        "op manueel zetten",
        "op manueel",
        "manuel",
        "handmatig",
        "manually",
        "manual mode",
        "manuell",
        "handbetrieb",
        "mode manuel",
    },
    "chloorautomaat": {
        "chloorautomaat",
        "chloordoseertoestel",
        "doseertoestel vloeibare chloor",
        "vloeibare chloor dosering",
        "chloor automaat",
        "chlorine dispenser",
        "chlorinator",
    },
}


def _build_synonym_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for canonical, variants in _SYNONYM_GROUPS.items():
        canonical_norm = _normalize(canonical)
        if not canonical_norm:
            continue
        for term in variants:
            alias_norm = _normalize(term)
            if not alias_norm or alias_norm == canonical_norm:
                continue
            mapping[alias_norm] = canonical_norm
        mapping.setdefault(canonical_norm, canonical_norm)
    return mapping


_SYNONYM_MAP: Dict[str, str] = _build_synonym_map()
_CANONICAL_DISPLAY: Dict[str, str] = {}
for canonical in _SYNONYM_GROUPS:
    canon_norm = _normalize(canonical)
    if canon_norm:
        _CANONICAL_DISPLAY[canon_norm] = canonical
_SYNONYM_CANONICALS: Set[str] = set(_CANONICAL_DISPLAY.keys())


def _apply_synonyms(norm_text: str) -> str:
    if not norm_text:
        return norm_text
    tokens = [tok for tok in norm_text.split(" ") if tok]
    if not tokens:
        return norm_text
    padded = f" {norm_text} "
    extras: List[str] = []
    for alias, canonical in _SYNONYM_MAP.items():
        if alias in tokens or f" {alias} " in padded:
            extras.append(canonical)
    if not extras:
        return norm_text
    for item in extras:
        if item not in tokens:
            tokens.append(item)
    return " ".join(tokens)


def _normalize_query(text: str) -> Tuple[str, str]:
    base = _normalize(text)
    return base, _apply_synonyms(base)


def _canonical_tokens(text: str) -> Set[str]:
    if not text:
        return set()
    return {tok for tok in text.split(" ") if tok in _SYNONYM_CANONICALS}


def _row_norms(row: dict) -> Tuple[str, str]:
    cached = row.get("_norm_pair")
    if cached:
        return cached
    base = _normalize(row.get("question") or "")
    parts: List[str] = [base]
    options = row.get("options") or {}
    if isinstance(options, dict):
        for label in options.keys():
            label_norm = _normalize(str(label))
            if label_norm:
                parts.append(label_norm)
    # Include alternative phrasings + multilingual questions so they participate in matching
    for alt in (row.get("alt_questions") or []):
        alt_norm = _normalize(str(alt))
        if alt_norm:
            parts.append(alt_norm)
    for lang_field in ("ENQuestion", "FRQuestion", "DEFrage"):
        alt_q = row.get(lang_field)
        if alt_q:
            alt_norm = _normalize(str(alt_q))
            if alt_norm:
                parts.append(alt_norm)
    augmented = _apply_synonyms(" ".join(p for p in parts if p))
    row["_norm_pair"] = (base, augmented)
    return row["_norm_pair"]


def _semantic_query_text(user_q: str) -> str:
    base = (user_q or "").strip()
    _, augmented = _normalize_query(user_q)
    extras: List[str] = []
    seen: Set[str] = set()
    if augmented:
        for token in augmented.split(" "):
            if token in _SYNONYM_CANONICALS and token not in seen:
                display = _CANONICAL_DISPLAY.get(token, token)
                if display and display.lower() not in base.lower():
                    extras.append(display)
                seen.add(token)
    if extras:
        base = (base + " " + " ".join(extras)).strip()
    return base


def _row_semantic_text(row: dict) -> str:
    cached = row.get("_semantic_text")
    if cached is not None:
        return cached
    parts: List[str] = [str(row.get("question") or "").strip()]
    tags = row.get("tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags if str(t).strip())
    options = row.get("options") or {}
    if isinstance(options, dict):
        parts.extend(str(label) for label in options.keys() if str(label).strip())
    for alt in (row.get("alt_questions") or []):
        if str(alt).strip():
            parts.append(str(alt).strip())
    for lang_field in ("ENQuestion", "FRQuestion", "DEFrage"):
        alt_q = row.get(lang_field)
        if alt_q and str(alt_q).strip():
            parts.append(str(alt_q).strip())
    _, augmented = _row_norms(row)
    if augmented:
        for token in augmented.split(" "):
            if token in _SYNONYM_CANONICALS:
                display = _CANONICAL_DISPLAY.get(token, token)
                if display:
                    parts.append(display)
    text = " ".join(part for part in parts if part).strip()
    row["_semantic_text"] = text
    return text


_LANG_BY_REF: Dict[str, str] = {}


def _ref_key(ref: str | None) -> str:
    return _normalize(ref or "")


def _remember_language(ref: str | None, lang: str | None) -> None:
    lang = (lang or "").strip().lower()
    key = _ref_key(ref)
    if not key or not lang:
        return
    _LANG_BY_REF[key] = lang


def _language_for_ref(ref: str | None) -> str | None:
    key = _ref_key(ref)
    if not key:
        return None
    return _LANG_BY_REF.get(key)


def _ensure_language(text: str, lang_code: str | None) -> str:
    if not text:
        return text
    return translate_answer(text, lang_code or "")


def _gpt_fallback_answer(question: str, lang_code: str = "nl") -> Optional[str]:
    """
    Generate an intelligent answer using GPT when FAQ doesn't have the answer.
    Returns None if GPT is unavailable or fails.
    """
    if not _openai_client:
        return None

    # Map lang code to language name for the prompt
    _LANG_NAMES = {
        "nl": "Dutch", "en": "English", "fr": "French", "de": "German",
        "es": "Spanish", "it": "Italian", "pt": "Portuguese", "pl": "Polish",
        "ro": "Romanian", "da": "Danish", "sv": "Swedish", "fi": "Finnish",
        "tr": "Turkish", "el": "Greek",
    }
    lang_name = _LANG_NAMES.get(lang_code, "Dutch")

    try:
        prompt = f"""You are a pool equipment support assistant for Beniferro.

Answer this customer question in {lang_name}:
{question}

Provide helpful, accurate information about pool equipment (Wifipool, salt electrolysis, sensors, pumps, etc).
If you're not sure, suggest contacting support at support@beniferro.eu.
Keep the answer clear and concise."""

        response = _openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024
        )
        answer = response.choices[0].message.content
        return answer
    except Exception as e:
        logger.error(f"GPT fallback failed: {e}")
        return None


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _partial_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return 1.0
    matcher = SequenceMatcher(None, longer, shorter)
    best = 0.0
    for block in matcher.get_matching_blocks():
        start = max(block.a - block.b, 0)
        substring = longer[start:start + len(shorter)]
        if not substring:
            continue
        best = max(best, SequenceMatcher(None, substring, shorter).ratio())
        if best >= 0.999:
            return 1.0
    return best


def _token_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a = {tok for tok in a.split(" ") if tok}
    tokens_b = {tok for tok in b.split(" ") if tok}
    if not tokens_a or not tokens_b:
        return 0.0
    shared = len(tokens_a & tokens_b)
    return shared / float(max(len(tokens_a), len(tokens_b)))


def _fuzzy_token_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a = [tok for tok in a.split(" ") if tok]
    tokens_b = [tok for tok in b.split(" ") if tok]
    if not tokens_a or not tokens_b:
        return 0.0

    used: Set[str] = set()
    hits = 0
    for tok in tokens_a:
        best = 0.0
        best_token: Optional[str] = None
        for candidate in tokens_b:
            if candidate in used:
                continue
            score = SequenceMatcher(None, tok, candidate).ratio()
            if score > best:
                best = score
                best_token = candidate
        if best_token is not None and best >= 0.74:
            used.add(best_token)
            hits += 1

    return hits / float(max(len(tokens_a), len(tokens_b)))


def _similarity(a: str, b: str) -> float:
    base = _ratio(a, b)
    partial = _partial_ratio(a, b)
    overlap = _token_overlap(a, b)
    fuzzy_overlap = _fuzzy_token_overlap(a, b)
    blend_one = min(1.0, base + overlap * 0.5)
    blend_two = min(1.0, base + fuzzy_overlap * 0.65)
    return max(base, partial, overlap, fuzzy_overlap, blend_one, blend_two)


def _disable_faq_embeddings() -> None:
    global _FAQ_EMBEDDER, _FAQ_EMBED_DISABLED
    if _FAQ_EMBED_LOCK.locked():
        _FAQ_EMBEDDER = None
        _FAQ_EMBED_DISABLED = True
    else:
        with _FAQ_EMBED_LOCK:
            _FAQ_EMBEDDER = None
            _FAQ_EMBED_DISABLED = True


def _faq_embedder() -> OpenAIEmbeddings:
    global _FAQ_EMBEDDER, _FAQ_EMBED_DISABLED
    if _FAQ_EMBED_DISABLED:
        raise RuntimeError("FAQ embeddings disabled")
    with _FAQ_EMBED_LOCK:
        if _FAQ_EMBEDDER is None:
            try:
                _FAQ_EMBEDDER = OpenAIEmbeddings(model=EMBEDDINGS_MODEL)
            except Exception as exc:
                _FAQ_EMBEDDER = None
                _FAQ_EMBED_DISABLED = True
                raise exc
    return _FAQ_EMBEDDER


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(x * y for x, y in zip(vec_a, vec_b))
    denom = sqrt(sum(x * x for x in vec_a)) * sqrt(sum(y * y for y in vec_b))
    if denom == 0.0:
        return 0.0
    return dot / denom


def _ensure_row_embedding(row: dict, embedder: OpenAIEmbeddings) -> Optional[List[float]]:
    cached = row.get("_embedding", _EMBED_UNSET)
    if cached is not _EMBED_UNSET:
        return cached
    question = _row_semantic_text(row)
    if not question:
        row["_embedding"] = None
        return None
    with _FAQ_EMBED_LOCK:
        cached = row.get("_embedding", _EMBED_UNSET)
        if cached is not _EMBED_UNSET:
            return cached
        try:
            vector = embedder.embed_query(question)
        except Exception as exc:
            row["_embedding"] = None
            raise exc
        row["_embedding"] = vector
        return vector


_SEMANTIC_CACHE: Dict[str, Tuple[float, Dict[int, Tuple[dict, float]]]] = {}
_SEMANTIC_CACHE_TTL = 30  # seconds – covers a single request cycle


def _semantic_scores(user_q: str) -> Dict[int, Tuple[dict, float]]:
    global _FAQ_EMBED_DISABLED
    results: Dict[int, Tuple[dict, float]] = {}
    if not _FAQ or not user_q or _FAQ_EMBED_DISABLED:
        return results

    # Per-query short-lived cache to avoid recomputing within the same request
    cache_key = user_q.strip().lower()
    cached = _SEMANTIC_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _SEMANTIC_CACHE_TTL:
        return cached[1]

    try:
        embedder = _faq_embedder()
    except Exception:
        return results
    try:
        query_vec = embedder.embed_query(_semantic_query_text(user_q))
    except Exception:
        _disable_faq_embeddings()
        return results

    for row in _FAQ:
        try:
            vec = _ensure_row_embedding(row, embedder)
        except Exception:
            _disable_faq_embeddings()
            return {}
        if not vec:
            continue
        score = _cosine_similarity(query_vec, vec)
        if score <= 0.0:
            continue
        results[id(row)] = (row, score)

    # Cache results and evict old entries
    now = time.time()
    _SEMANTIC_CACHE[cache_key] = (now, results)
    if len(_SEMANTIC_CACHE) > 50:
        stale = [k for k, (t, _) in _SEMANTIC_CACHE.items() if now - t > _SEMANTIC_CACHE_TTL]
        for k in stale:
            _SEMANTIC_CACHE.pop(k, None)

    return results


def _semantic_match_row(user_q: str) -> Optional[dict]:
    scores = _semantic_scores(user_q)
    best_score = 0.0
    best_row: Optional[dict] = None
    for row, score in (scores.values() if scores else []):
        if score > best_score:
            best_score = score
            best_row = row
    if best_score >= _SEMANTIC_MATCH_THRESHOLD:
        return best_row
    return None

# ---------------------------------------------------------------------
# Question suggestions based on semantic similarity
# ---------------------------------------------------------------------
def _add_suggestions_to_response(
    response: Dict[str, Any],
    user_q: str,
    lang_code: str,
    current_row: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Helper to add suggestions to any response if they don't exist yet.
    This ensures ALL responses include suggestions to reduce fault rate.

    Args:
        response: The response dict to add suggestions to
        user_q: The user's question
        lang_code: Language code for suggestions
        current_row: The current FAQ row (to exclude from suggestions)

    Returns:
        The response dict with suggestions added (if possible)
    """
    if not user_q or "suggestions" in response:
        return response

    try:
        suggestions = _get_similar_questions(current_row, user_q, lang_code)
        if suggestions:
            response["suggestions"] = suggestions
    except Exception:
        pass

    return response


def _get_question_in_language(row: dict, lang_code: str) -> str:
    """Extract question text in the appropriate language."""
    if not row:
        return ""

    # Map language codes to field names
    lang_fields = {
        "fr": "FRQuestion",
        "de": "DEFrage",
        "en": "ENQuestion",
        "nl": "Vraag"
    }

    # Try to get question in requested language
    field = lang_fields.get(lang_code, "Vraag")
    question = (row.get(field) or "").strip()

    # Fall back to main question field or "Vraag"
    if not question:
        question = (row.get("question") or row.get("Vraag") or "").strip()

    return question


def _get_answer_in_language(row: dict, lang_code: str) -> Optional[str]:
    """Return pre-translated answer from Excel if available for the target language."""
    if not row or not lang_code:
        return None
    lang_fields = {
        "fr": "FRReponse",
        "de": "DEAntwort",
        "en": "ENAnswer",
    }
    field = lang_fields.get(lang_code)
    if not field:
        return None
    val = (row.get(field) or "").strip()
    return val or None


def _enrich_response_with_media(response: Dict[str, Any], row: Optional[dict]) -> Dict[str, Any]:
    """Add image_url and video_url fields (when available) to a response dict."""
    if not isinstance(row, dict) or not isinstance(response, dict):
        return response
    image_path = (row.get("image_path") or "").strip()
    if image_path and "image_url" not in response:
        response["image_url"] = image_path
    video_url = (row.get("video_url") or "").strip()
    if video_url and "video_url" not in response:
        response["video_url"] = video_url
    return response


def _get_faq_suggestions_with_scores(
    user_q: str,
    top_k: int = 4,
    min_similarity: float = 0.3,
    lang_code: str = "nl"
) -> List[dict]:
    """
    Récupère les top K suggestions FAQ avec leurs scores de similarité et métadonnées complètes.

    Args:
        user_q: La question de l'utilisateur
        top_k: Nombre de suggestions à retourner (défaut: 4)
        min_similarity: Score de similarité minimum (0-1, défaut: 0.3)
        lang_code: Code de langue pour les traductions

    Returns:
        Liste de suggestions avec format:
        [{
            "question": str,
            "answer": str,
            "category": str,
            "similarity_score": float (0-100),
            "follow_up": dict (optionnel),
            "media": dict (optionnel)
        }]
    """
    try:
        # Obtenir les scores sémantiques pour toutes les FAQ
        scores = _semantic_scores(user_q)
        if not scores:
            return []

        # Convertir en liste triée par score décroissant
        candidates = sorted(
            [(row, score) for row_id, (row, score) in scores.items()],
            key=lambda x: x[1],
            reverse=True
        )

        suggestions = []
        for row, score in candidates:
            # Filtrer par score minimum
            if score < min_similarity:
                continue

            # Extraire les informations de la FAQ
            question = _get_question_in_language(row, lang_code)
            answer = (row.get("answer") or row.get("antwoord") or "").strip()
            category = (row.get("category") or row.get("categorie") or "").strip()

            suggestion = {
                "question": question,
                "answer": _ensure_language(answer, lang_code),
                "category": category,
                "similarity_score": round(score * 100, 1)  # Convertir en pourcentage
            }

            # Ajouter les informations de follow-up si disponibles
            if row.get("follow_up"):
                followup_q = row.get("followup_q") or row.get("follow_up_question") or ""
                options = row.get("options") or {}
                suggestion["follow_up"] = {
                    "question": followup_q,
                    "options": list(options.keys()) if options else []
                }

            # Ajouter les médias si disponibles
            media = _extract_media_from_payload(row)
            if media:
                suggestion["media"] = media

            # Ajouter la vidéo si disponible
            video_url = row.get("video_url")
            if video_url:
                if "media" not in suggestion:
                    suggestion["media"] = {}
                suggestion["media"]["video"] = video_url

            suggestions.append(suggestion)

            # Arrêter si on a assez de suggestions
            if len(suggestions) >= top_k:
                break

        return suggestions

    except Exception as exc:
        import logging
        logging.warning(f"Error generating FAQ suggestions with scores: {exc}")
        return []


def _get_similar_questions(
    current_row: dict,
    user_q: str,
    lang_code: str,
    min_count: int = 3,
    max_count: int = 6,
    min_similarity: float = 0.50
) -> List[str]:
    """
    Generate 3-6 similar question suggestions based on semantic similarity.
    ALWAYS returns between min_count and max_count suggestions (unless FAQ has fewer entries).

    Args:
        current_row: The matched FAQ row
        user_q: The original user question
        lang_code: Language code for translation
        min_count: Minimum number of suggestions (default 3)
        max_count: Maximum number of suggestions (default 6)
        min_similarity: Minimum similarity score threshold (default 0.50)

    Returns:
        List of similar questions in the appropriate language (3-6 items)
    """
    try:
        # Get semantic scores for all FAQ entries
        scores = _semantic_scores(user_q)

        # Fallback: if no scores (e.g., OpenAI unavailable), pick random FAQ questions
        if not scores:
            import random
            suggestions = []
            seen_questions = set()

            # Get current question to exclude it
            if current_row:
                current_question = _get_question_in_language(current_row, lang_code)
                if current_question:
                    seen_questions.add(current_question.strip().lower())

            # Pick random questions from FAQ
            available_rows = [row for row in _FAQ if id(row) != (id(current_row) if current_row else None)]
            random.shuffle(available_rows)

            for row in available_rows:
                question = _get_question_in_language(row, lang_code)
                if question:
                    question_normalized = question.strip().lower()
                    if question_normalized not in seen_questions:
                        suggestions.append(question)
                        seen_questions.add(question_normalized)
                        if len(suggestions) >= max_count:
                            break

            return suggestions[:max_count]

        # Get current row ID to exclude it
        current_id = id(current_row) if current_row else None

        # Try with progressively lower thresholds to ensure we get enough suggestions
        thresholds = [min_similarity, 0.40, 0.30, 0.20, 0.10]
        candidates = []

        for threshold in thresholds:
            # Filter and sort by score, excluding current question
            candidates = [
                (row, score)
                for row_id, (row, score) in scores.items()
                if row_id != current_id and score >= threshold
            ]

            # Sort by score descending
            candidates.sort(key=lambda x: x[1], reverse=True)

            # If we have enough candidates, stop lowering threshold
            if len(candidates) >= min_count:
                break

        # Extract questions in the right language
        suggestions = []
        seen_questions = set()

        # Get current question to exclude it from suggestions
        if current_row:
            current_question = _get_question_in_language(current_row, lang_code)
            if current_question:
                seen_questions.add(current_question.strip().lower())

        for row, score in candidates[:max_count * 2]:  # Check more to handle duplicates
            question = _get_question_in_language(row, lang_code)
            if question:
                question_normalized = question.strip().lower()
                if question_normalized not in seen_questions:
                    suggestions.append(question)
                    seen_questions.add(question_normalized)

                    # Stop once we have enough
                    if len(suggestions) >= max_count:
                        break

        # Ensure we have at least min_count suggestions (if possible)
        # If we have fewer, try getting from different categories
        if len(suggestions) < min_count and current_row:
            category = current_row.get("category", "")
            # Get suggestions from same category
            for row_id, (row, score) in scores.items():
                if len(suggestions) >= max_count:
                    break
                if row_id == current_id:
                    continue
                if category and row.get("category") == category:
                    question = _get_question_in_language(row, lang_code)
                    if question:
                        question_normalized = question.strip().lower()
                        if question_normalized not in seen_questions:
                            suggestions.append(question)
                            seen_questions.add(question_normalized)

        # Final fallback: if still not enough suggestions, add random ones
        if len(suggestions) < min_count:
            import random
            available_rows = [row for row_id, (row, _) in scores.items() if row_id != current_id]
            if not available_rows:
                available_rows = [row for row in _FAQ if id(row) != current_id]
            random.shuffle(available_rows)

            for row in available_rows:
                if len(suggestions) >= max_count:
                    break
                question = _get_question_in_language(row, lang_code)
                if question:
                    question_normalized = question.strip().lower()
                    if question_normalized not in seen_questions:
                        suggestions.append(question)
                        seen_questions.add(question_normalized)

        return suggestions[:max_count]

    except Exception as exc:
        # Log error but don't fail the whole response
        import logging
        logging.warning(f"Error generating question suggestions: {exc}")
        # Final fallback: return random questions
        try:
            import random
            suggestions = []
            seen_questions = set()

            if current_row:
                current_question = _get_question_in_language(current_row, lang_code)
                if current_question:
                    seen_questions.add(current_question.strip().lower())

            available_rows = [row for row in _FAQ if id(row) != (id(current_row) if current_row else None)]
            random.shuffle(available_rows)

            for row in available_rows[:max_count]:
                question = _get_question_in_language(row, lang_code)
                if question:
                    question_normalized = question.strip().lower()
                    if question_normalized not in seen_questions:
                        suggestions.append(question)
                        seen_questions.add(question_normalized)

            return suggestions[:max_count]
        except:
            return []

# ---------------------------------------------------------------------
# Follow-up memory (fallback sans clarify_ref)
# ---------------------------------------------------------------------
# { client_id: {"q": base_question, "labels": [...], "ts": epoch, "row": row_dict} }
_PENDING_BY_CLIENT: Dict[str, Dict[str, Any]] = {}
_PENDING_TTL = 180.0  # seconds

def _client_id_from_request(req: Request) -> str:
    xf = req.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip()
    return (getattr(req.client, "host", None) or "unknown").strip()

def _set_pending(
    client_id: str,
    row: Optional[dict],
    labels: List[str],
    language: str | None = None,
    *,
    mode: str = "followup",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    question_ref = ""
    if isinstance(row, dict):
        question_ref = row.get("question") or ""
    elif extra:
        question_ref = extra.get("question", "")
    if question_ref and language:
        _remember_language(question_ref, language)
    entry: Dict[str, Any] = {
        "q": question_ref,
        "labels": labels,
        "ts": time.time(),
        "row": row if isinstance(row, dict) else None,
        "language": (language or "").strip().lower(),
        "mode": mode,
    }
    if extra:
        entry.update(extra)
    _PENDING_BY_CLIENT[client_id] = entry

def _get_valid_pending(client_id: str) -> dict | None:
    item = _PENDING_BY_CLIENT.get(client_id)
    if not item:
        return None
    if time.time() - float(item.get("ts", 0.0)) > _PENDING_TTL:
        _PENDING_BY_CLIENT.pop(client_id, None)
        return None
    return item

def _pop_valid_pending(client_id: str) -> dict | None:
    item = _get_valid_pending(client_id)
    if item:
        _PENDING_BY_CLIENT.pop(client_id, None)
    return item

# ---------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------
def _match_row_with_clarify(user_q: str) -> Tuple[Optional[dict], List[dict]]:
    if not _FAQ:
        return (None, [])

    uq_base, uq_aug = _normalize_query(user_q)
    if not uq_base:
        return (None, [])

    direct_matches: List[dict] = []
    query_canon = _canonical_tokens(uq_aug)
    row_scores: Dict[int, Tuple[dict, float]] = {}

    for row in _FAQ:
        base_norm, aug_norm = _row_norms(row)

        if not base_norm and not aug_norm:
            continue

        if base_norm and (
            uq_base == base_norm or (uq_base and (uq_base in base_norm or base_norm in uq_base))
        ):
            direct_matches.append(row)
            continue

        score = _similarity(uq_aug, aug_norm)

        if score > 0.0 and query_canon:
            row_canon = _canonical_tokens(aug_norm)
            if not row_canon:
                score *= 0.85
            else:
                overlap = len(query_canon & row_canon)
                coverage = overlap / float(len(query_canon))
                if coverage < 0.4:
                    score *= 0.85
                elif coverage < 0.65:
                    score *= 0.90
                else:
                    score = min(1.0, score + min(1.0, coverage) * 0.25)

        if score > 0.0:
            key = id(row)
            stored = row_scores.get(key)
            if not stored or score > stored[1]:
                row_scores[key] = (row, score)

    if direct_matches:
        if len(direct_matches) == 1:
            return (direct_matches[0], [])
        return (None, direct_matches[:4])

    need_semantic = not row_scores
    if not need_semantic:
        prelim = sorted(row_scores.values(), key=lambda item: item[1], reverse=True)
        if not prelim or prelim[0][1] < _SEMANTIC_TRIGGER:
            need_semantic = True

    if need_semantic:
        semantic_scores = _semantic_scores(user_q)
        for key, (row, score) in semantic_scores.items():
            stored = row_scores.get(key)
            if stored:
                if score > stored[1]:
                    row_scores[key] = (row, score)
            else:
                row_scores[key] = (row, score)

    if not row_scores:
        return (None, [])

    candidates = sorted(row_scores.values(), key=lambda item: item[1], reverse=True)
    candidates = [item for item in candidates if item[1] > 0.0]

    if not candidates:
        # FALLBACK: simple token overlap
        fallback_matches = []
        query_tokens = set(uq_base.split())
        for row in _FAQ:
            base_norm, _ = _row_norms(row)
            if not base_norm:
                continue
            row_tokens = set(base_norm.split())
            common_tokens = query_tokens & row_tokens
            if common_tokens:
                overlap_ratio = len(common_tokens) / max(len(query_tokens), len(row_tokens))
                if overlap_ratio > 0.3:
                    fallback_matches.append((row, overlap_ratio))
        if fallback_matches:
            fallback_matches.sort(key=lambda x: x[1], reverse=True)
            if fallback_matches[0][1] >= 0.4:
                return (fallback_matches[0][0], [])
        return (None, [])

    top_row, top_score = candidates[0]
    second_score = candidates[1][1] if len(candidates) > 1 else 0.0

    if top_score >= _CERTAINTY_THRESHOLD or (
        top_score >= _MATCH_THRESHOLD and (top_score - second_score) >= _CERTAINTY_GAP
    ):
        return (top_row, [])

    if top_score < _MATCH_THRESHOLD:
        return (None, [])

    if len(candidates) == 1:
        return (top_row, [])

    top_base, top_norm = _row_norms(top_row)
    top_tokens = {
        tok
        for tok in (top_norm or top_base or "").split(" ")
        if tok and tok not in _AMBIGUITY_STOPWORDS
    }
    top_canon = _canonical_tokens(top_norm)
    ambiguous: List[Tuple[dict, float]] = []
    for row, score in candidates:
        if len(ambiguous) >= 4:
            break
        if score < _MATCH_THRESHOLD:
            continue
        if (top_score - score) > _AMBIGUITY_GAP:
            continue
        _, row_norm = _row_norms(row)
        peer_sim = _similarity(top_norm, row_norm) if top_norm and row_norm else 0.0
        peer_overlap = _token_overlap(top_norm or "", row_norm or "")
        row_tokens = {
            tok for tok in (row_norm or "").split(" ") if tok and tok not in _AMBIGUITY_STOPWORDS
        }
        if query_canon:
            row_canon = _canonical_tokens(row_norm)
            coverage = len(query_canon & row_canon) / float(len(query_canon)) if row_canon else 0.0
            if coverage < 0.5:
                continue
        if top_canon:
            row_canon = _canonical_tokens(row_norm)
            if row_canon and not (top_canon & row_canon):
                continue
        if top_tokens and row_tokens and not (top_tokens & row_tokens):
            continue
        if peer_sim >= _AMBIGUITY_PEER_THRESHOLD or peer_overlap >= _AMBIGUITY_PEER_THRESHOLD:
            ambiguous.append((row, score))

    if len(ambiguous) >= 2:
        return (None, [row for row, _ in ambiguous])

    return (top_row, [])

def _build_ambiguity_message(rows: List[dict]) -> str:
    if not rows:
        return "Kun je aangeven welke vraag je precies bedoelt?"
    parts: List[str] = []
    for idx, row in enumerate(rows, start=1):
        question = (row.get("question") or "").strip() or "Vraag"
        parts.append(f"{idx}) '{question}'")
    range_text = "1" if len(parts) == 1 else f"1–{len(parts)}"
    joined = " ".join(parts)
    return ("Bedoel je met je vraag: " + joined + f"? Kies het nummer ({range_text}) of antwoord: 'ja, ik bedoel vraag X'.")

def _parse_choice_index(choice: str, count: int) -> Optional[int]:
    if count <= 0:
        return None
    t = _normalize(choice)
    if not t:
        return None
    match = re.search(r"\b(\d{1,2})\b", t)
    if match:
        idx = int(match.group(1))
        if 1 <= idx <= count:
            return idx
    for word, value in _ORDINAL_ALIASES.items():
        if word in t and 1 <= value <= count:
            return value
    return None


def _resolve_ambiguity_selection(pending: Dict[str, Any], choice_text: str) -> Optional[dict]:
    candidates: List[dict] = pending.get("candidates") or []
    if not candidates:
        return None
    idx = _parse_choice_index(choice_text, len(candidates))
    if idx:
        return candidates[idx - 1]
    _, norm_choice = _normalize_query(choice_text)
    if not norm_choice:
        return None
    best: Tuple[float, Optional[dict]] = (0.0, None)
    for candidate in candidates:
        base_norm, aug_norm = _row_norms(candidate)
        target_norm = aug_norm or base_norm
        if not target_norm:
            continue
        if _text_contains(norm_choice, target_norm) or _text_contains(target_norm, norm_choice):
            return candidate
        score = _similarity(norm_choice, target_norm)
        if score > best[0]:
            best = (score, candidate)
    if best[0] >= 0.55:
        return best[1]
    return None


def _respond_for_row(row: dict, lang_code: str, client_id: str, user_q: str = "") -> Dict[str, Any]:
    if not isinstance(row, dict):
        # Even for errors, provide suggestions based on semantic similarity
        response = {"answer": _ensure_language("Geen antwoord gevonden.", lang_code), "citations": []}
        if user_q:
            suggestions = _get_similar_questions(None, user_q, lang_code)
            if suggestions:
                response["suggestions"] = suggestions
        return response

    question_ref = row.get("question")
    if question_ref:
        _remember_language(question_ref, lang_code)

    if row.get("follow_up"):
        labels = _labels(row)
        tips: List[str] = GEN_TIPS_NL if any("gen" in _normalize(k) for k in labels) else []
        _set_pending(client_id, row, labels, lang_code, mode="followup")
        intro = (row.get("answer") or row.get("antwoord") or "").strip()
        follow_q = row.get("followup_q") or row.get("follow_up_question") or "Kunt u een keuze maken?"
        parts = [intro] if intro else []
        if follow_q:
            parts.append(follow_q.strip())
        if labels:
            bullet_list = "\n".join(f"- {label}" for label in labels)
            parts.append("Opties:\n" + bullet_list)
        answer_text = "\n\n".join([p for p in parts if p]).strip() or follow_q
        response = {
            "answer": _ensure_language(answer_text, lang_code),
            "clarify": {"ref": row.get("question"), "options": labels, "tips": tips},
            "citations": _citations_for_row(row),
            "source": "faq"
        }
        # Add suggestions even for follow-up questions
        if user_q:
            suggestions = _get_similar_questions(row, user_q, lang_code)
            if suggestions:
                response["suggestions"] = suggestions
        return response

    # Language-first: if Excel has pre-translated answer in user's language, use it directly
    # (skip the LLM translation roundtrip entirely)
    pretrans = _get_answer_in_language(row, lang_code)
    if pretrans:
        direct = polish_faq_answer(pretrans, lang_code)
        _skip_translation = True
    else:
        raw = (row.get("answer") or row.get("antwoord") or "").strip()
        direct = polish_faq_answer(raw, "nl")
        _skip_translation = False

    # Check if FAQ answer is empty or too short/unhelpful - use GPT fallback
    MIN_ANSWER_LENGTH = 30  # Minimum meaningful answer length
    if not direct or len(direct) < MIN_ANSWER_LENGTH:
        logger.debug(f"FAQ answer too short ({len(direct) if direct else 0} chars), trying GPT fallback")
        # Try GPT fallback for empty/short FAQ answers
        gpt_answer = _gpt_fallback_answer(user_q or question_ref or "", lang_code)
        if gpt_answer:
            _AI_NOTES_INNER = {
                "nl": "\n\n*Deze informatie is gegenereerd door AI. Voor specifieke vragen kunt u contact opnemen met support@beniferro.eu.*",
                "fr": "\n\n*Ces informations sont générées par IA. Pour des questions spécifiques, veuillez contacter support@beniferro.eu.*",
                "en": "\n\n*This information is AI-generated. For specific questions, please contact support@beniferro.eu.*",
                "de": "\n\n*Diese Informationen wurden von KI generiert. Bei spezifischen Fragen wenden Sie sich bitte an support@beniferro.eu.*",
            }
            ai_note = _AI_NOTES_INNER.get(lang_code, _AI_NOTES_INNER["nl"])
            response = {
                "answer": gpt_answer + ai_note,
                "citations": _citations_for_row(row),
                "source": "ai_fallback"
            }
            if user_q:
                suggestions = _get_similar_questions(row, user_q, lang_code)
                if suggestions:
                    response["suggestions"] = suggestions
            return response

        # GPT failed, provide friendly message
        question_matched = row.get("question", "").strip()
        if question_matched:
            friendly_msg = (
                f"Ik heb je vraag '{question_matched[:100]}' gevonden, "
                f"maar het antwoord is nog niet beschikbaar. "
                f"Neem contact op met support@beniferro.eu voor meer informatie."
            )
            response = {
                "answer": _ensure_language(friendly_msg, lang_code),
                "citations": _citations_for_row(row),
                "source": "faq"
            }
        else:
            response = {
                "answer": _ensure_language("Geen antwoord gevonden.", lang_code),
                "citations": [],
                "source": "faq"
            }

        if user_q:
            suggestions = _get_similar_questions(row, user_q, lang_code)
            if suggestions:
                response["suggestions"] = suggestions
        return response

    # FAQ has a good answer - return it
    if direct:
        # Only append video line when no dedicated image/video will be rendered
        has_media = bool(row.get("image_path")) or bool(row.get("video_url"))
        extra_line = ""
        if row.get("video_url") and not has_media:
            extra_line = "\n\nBekijk video: " + str(row["video_url"])

        answer_text = direct + extra_line if not _skip_translation else direct + extra_line
        final_answer = direct + extra_line if _skip_translation else _ensure_language(answer_text, lang_code)

        response = {
            "answer": final_answer,
            "citations": _citations_for_row(row),
            "source": "faq"
        }
        media = _extract_media_from_payload(row)
        if media:
            response["media"] = media
        _enrich_response_with_media(response, row)

        # ALWAYS generate similar question suggestions (never return without them)
        # This helps reduce fault rate by guiding users to related questions
        if user_q:
            suggestions = _get_similar_questions(row, user_q, lang_code)
            if suggestions:
                response["suggestions"] = suggestions

        return response

    # Fallback (should not reach here)
    response = {
        "answer": _ensure_language("Geen antwoord gevonden.", lang_code),
        "citations": [],
        "source": "faq"
    }
    if user_q:
        suggestions = _get_similar_questions(row, user_q, lang_code)
        if suggestions:
            response["suggestions"] = suggestions
    return response


def _find_row_by_ref(ref_q: str) -> dict | None:
    if not ref_q:
        return None
    uq_base, uq_aug = _normalize_query(ref_q)
    best = (0.0, None)
    for row in _FAQ:
        base_norm, aug_norm = _row_norms(row)
        target_norm = aug_norm or base_norm
        if not target_norm:
            continue
        if base_norm and (uq_base == base_norm or uq_base in base_norm or base_norm in uq_base):
            return row
        sc = _similarity(uq_aug, target_norm)
        if sc > best[0]:
            best = (sc, row)
    if best[0] >= (_MATCH_THRESHOLD - 0.05):
        return best[1]
    return _semantic_match_row(ref_q)

# options / gen
_GEN_ALIASES: Dict[str, set] = {
    "gen1": {"gen1", "gen 1", "g1", "generation 1"},
    "gen2": {"gen2", "gen 2", "g2", "generation 2"},
    "gen3": {"gen3", "gen 3", "g3", "generation 3"},
}
_DEVICE_ALIASES: Dict[str, set] = {
    "wifipool": {"wifipool", "wifi", "wifi apparaat", "wifi-apparaat"},
    "benisol": {"benisol", "zonder wifi"},
    "display": {"display", "display apparaat", "display-apparaat"},
}

def _labels(row: dict) -> List[str]:
    opts = row.get("options") or {}
    return list(opts.keys())

def _map_choice_to_key(choice: str, option_labels: List[str]) -> str | None:
    t = _normalize(choice)
    if not t:
        return None
    idx = _parse_choice_index(choice, len(option_labels))
    if idx:
        return option_labels[idx - 1]
    label_info = [(lbl, _normalize(lbl)) for lbl in option_labels]

    for key, aliases in _DEVICE_ALIASES.items():
        key_norm = _normalize(key)
        for alias in aliases:
            alias_norm = _normalize(alias)
            if _text_contains(t, alias_norm):
                for lbl, lbl_norm in label_info:
                    if _text_contains(lbl_norm, key_norm) or _text_contains(lbl_norm, alias_norm):
                        return lbl

    for key, aliases in _GEN_ALIASES.items():
        key_norm = _normalize(key)
        for alias in aliases:
            alias_norm = _normalize(alias)
            if _text_contains(t, alias_norm):
                candidates = [
                    lbl for lbl, lbl_norm in label_info if _text_contains(lbl_norm, key_norm)
                ]
                if len(candidates) == 1:
                    return candidates[0]
                if candidates:
                    return candidates[0]

    for lbl, lbl_norm in label_info:
        if _text_contains(t, lbl_norm):
            return lbl

    matches = get_close_matches(choice, option_labels, n=1, cutoff=0.55)
    if matches:
        return matches[0]
    return None

def _looks_like_followup_choice(text: str) -> bool:
    raw = (text or "").strip()
    t = _normalize(text)
    if not t:
        return False
    if "?" in raw:
        return False
    words = [tok for tok in t.split(" ") if tok]
    if len(words) > 8:
        return False
    question_words = {
        "hoe",
        "wat",
        "waarom",
        "waar",
        "wanneer",
        "welke",
        "how",
        "what",
        "why",
        "where",
        "when",
        "quel",
        "quels",
        "quelle",
        "quand",
        "comment",
    }
    if any(word in question_words for word in words):
        return False
    if any(ch.isdigit() for ch in t):
        return True
    for word in _ORDINAL_ALIASES.keys():
        if word in t:
            return True
    for aliases in list(_GEN_ALIASES.values()) + list(_DEVICE_ALIASES.values()):
        for alias in aliases:
            if _text_contains(t, _normalize(alias)):
                return True
    return False

def _map_choice_to_genkey(choice: str) -> str | None:
    t = _normalize(choice)
    if not t:
        return None
    for key, aliases in _GEN_ALIASES.items():
        key_norm = _normalize(key)
        for alias in aliases:
            if _text_contains(t, _normalize(alias)) or _text_contains(t, key_norm):
                return key
    return None

def _choose_gen_answer(row: dict, gen_key: str) -> str | None:
    if gen_key == "gen1" and row.get("answer_gen1"):
        return str(row["answer_gen1"]).strip()
    if gen_key == "gen2" and row.get("answer_gen2"):
        return str(row["answer_gen2"]).strip()
    if gen_key == "gen3" and row.get("answer_gen3"):
        return str(row["answer_gen3"]).strip()
    return None

_IMAGE_KEY_ALIASES: Set[str] = {
    "foto",
    "fotos",
    "photo",
    "photos",
    "image",
    "images",
    "afbeelding",
    "afbeeldingen",
    "beeld",
    "beelden",
    "illustratie",
    "illustraties",
    "picture",
    "pictures",
}

_URL_KEY_ALIASES: Set[str] = {"url", "href", "src", "link"}


def _media_key_norm(key: Any) -> str:
    norm = _normalize(str(key))
    return norm.replace(" ", "")


def _is_image_key(key: Any) -> bool:
    norm = _media_key_norm(key)
    if not norm:
        return False
    return any(alias in norm for alias in _IMAGE_KEY_ALIASES)


def _collect_image_urls(value: Any) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()

    def _walk(val: Any) -> None:
        if isinstance(val, str):
            url = val.strip()
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
            return
        if isinstance(val, dict):
            for sub_key, sub_val in val.items():
                sub_norm = _media_key_norm(sub_key)
                if sub_norm in _URL_KEY_ALIASES or _is_image_key(sub_key):
                    _walk(sub_val)
            return
        if isinstance(val, (list, tuple, set)):
            for item in val:
                _walk(item)

    _walk(value)
    return urls


def _extract_media_from_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    images: List[Dict[str, str]] = []
    seen: Set[str] = set()

    def _add_from_value(val: Any) -> None:
        for url in _collect_image_urls(val):
            if url not in seen:
                seen.add(url)
                images.append({"type": "image", "url": url})

    for key, value in payload.items():
        if _is_image_key(key):
            _add_from_value(value)
            continue
        key_norm = _media_key_norm(key)
        if key_norm == "media" and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                if _is_image_key(sub_key):
                    _add_from_value(sub_val)
    return {"images": images} if images else {}


def _render_option_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    ans = (payload.get("answer") or payload.get("antwoord") or "").strip()
    rec = (payload.get("recommendation") or payload.get("aanbeveling") or "").strip()
    if ans:
        return ans + (("\n\nAanbeveling: " + rec) if rec else "")
    lines: List[str] = []
    for k, v in payload.items():
        if v in (None, "", []):
            continue
        if _is_image_key(k):
            image_urls = _collect_image_urls(v)
            if image_urls:
                figures = "\n".join(
                    f"<img src='{url}' alt='Afbeelding' style='max-width:100%;height:auto;' />"
                    for url in image_urls
                )
                lines.append(f"Afbeeldingen:\n{figures}")
            continue
        key = str(k).replace("_", " ").strip().capitalize()
        if isinstance(v, list):
            items = "\n".join([f"  • {str(x)}" for x in v if str(x).strip()])
            if items:
                lines.append(f"{key}:\n{items}")
        elif isinstance(v, dict):
            parts = [f"{kk}: {vv}" for kk, vv in v.items() if vv not in (None, "", [])]
            if parts:
                lines.append(f"{key}: " + "; ".join(parts))
        else:
            lines.append(f"{key}: {v}")
    return "\n".join(lines).strip()


def _build_answer_for_option(row: dict, option_label: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    payload = (row.get("options") or {}).get(option_label)
    if payload is None:
        return "Ik heb geen details gevonden voor deze keuze.", None
    intro = (row.get("answer") or row.get("antwoord") or "").strip()
    body = _render_option_payload(payload)
    media = _extract_media_from_payload(payload)
    text = (intro + "\n\n" + body).strip() if intro else body
    return text, (media or None)

def _citations_for_row(row: dict) -> List[dict]:
    return [{"title": row.get("question") or "FAQ", "source": row.get("source") or "", "page": None}]

def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict): return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1","gen 1"}: return "gen1"
    if g in {"gen2","gen 2"}: return "gen2"
    if g in {"gen3","gen 3"}: return "gen3"
    return None

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    info: Dict[str, Any] = {
        "status": "ok",
        "faq_rows": len(_FAQ),
        "faq_file_exists": os.path.exists(_FAQ_FALLBACK_JSONL),
        "cache": cache_stats(),
        "features": {
            "cache": _CACHE_AVAILABLE,
            "analytics": _ANALYTICS_AVAILABLE,
            "query_preprocessor": _PREPROCESSOR_AVAILABLE,
            "direct_answer": _DIRECT_ANSWER_AVAILABLE,
        },
    }
    if _ANALYTICS_AVAILABLE:
        try:
            report = get_analytics_report(days=1)
            info["analytics_today"] = report.get("summary", {})
        except Exception:
            pass
    return info

@app.get("/analytics")
def analytics(days: int = 7):
    """Return usage analytics report."""
    if not _ANALYTICS_AVAILABLE:
        return {"error": "Analytics not available"}
    return get_analytics_report(days=days)


@app.get("/faq/gaps")
def faq_gaps(min_count: int = 1):
    """Return questions that were asked but never got a good FAQ answer."""
    if not _ANALYTICS_AVAILABLE:
        return {"gaps": [], "note": "Analytics not available"}
    gaps = get_faq_gaps(min_count=min_count)
    return {"gaps": gaps, "total": len(gaps)}


@app.get("/debug/faq")
def debug_faq():
    """Debug endpoint to check FAQ state"""
    sample = _FAQ[:5] if _FAQ else []
    return {
        "faq_count": len(_FAQ),
        "faq_path": _FAQ_FALLBACK_JSONL,
        "faq_exists": os.path.exists(_FAQ_FALLBACK_JSONL),
        "store_path": _FAQ_PATH,
        "store_exists": os.path.exists(_FAQ_PATH),
        "sample_questions": [
            {
                "category": item.get("category", ""),
                "question": item.get("question", "")[:100],
                "has_answer": bool(item.get("answer", "").strip())
            }
            for item in sample
        ]
    }

@app.get("/debug/reload-faq")
def force_reload_faq():
    """Force reload FAQ from file"""
    count, _ = _reload_faq()
    return {
        "success": True,
        "message": "FAQ reloaded",
        "count": count,
        "source": "JSONL fallback" if not os.path.exists(_FAQ_PATH) else "Store"
    }

@app.get("/debug/test-match")
def debug_test_match(q: str = "Hoe reset ik mijn wifipool?"):
    """
    Debug endpoint to test FAQ matching with detailed scoring.
    Shows why a question matches or doesn't match.

    Args:
        q: Question to test (default: "Hoe reset ik mijn wifipool?")

    Returns detailed matching information including:
    - Normalized query
    - All FAQ scores (top 20)
    - Matched row (if any)
    - Clarification options (if any)
    - Threshold comparisons
    """
    if not _FAQ:
        return {
            "error": "FAQ is empty",
            "faq_count": 0,
            "query": q
        }

    # Normalize the query
    uq_base, uq_aug = _normalize_query(q)
    query_canon = _canonical_tokens(uq_aug)

    # Calculate all scores manually for debugging
    all_scores = []
    for row in _FAQ:
        base_norm, aug_norm = _row_norms(row)
        question_text = row.get("question", "NO_QUESTION")

        if not base_norm and not aug_norm:
            all_scores.append({
                "question": question_text[:150],
                "score": 0.0,
                "reason": "Empty normalized text"
            })
            continue

        # Check for direct match
        if base_norm and (
            uq_base == base_norm or (uq_base and (uq_base in base_norm or base_norm in uq_base))
        ):
            all_scores.append({
                "question": question_text[:150],
                "score": 1.0,
                "reason": "Direct match",
                "base_norm": base_norm[:100],
                "aug_norm": aug_norm[:100] if aug_norm else ""
            })
            continue

        # Calculate similarity
        score = _similarity(uq_aug, aug_norm)
        reason = f"Base similarity: {score:.4f}"

        # Apply canonical token penalties/boosts
        if score > 0.0 and query_canon:
            row_canon = _canonical_tokens(aug_norm)
            if not row_canon:
                original_score = score
                score *= 0.55
                reason += f" → {score:.4f} (no canonical tokens)"
            else:
                overlap = len(query_canon & row_canon)
                coverage = overlap / float(len(query_canon))
                original_score = score

                if coverage < 0.4:
                    score *= 0.55
                    reason += f" → {score:.4f} (low coverage: {coverage:.2f})"
                elif coverage < 0.65:
                    score *= 0.75
                    reason += f" → {score:.4f} (medium coverage: {coverage:.2f})"
                else:
                    score = min(1.0, score + min(1.0, coverage) * 0.25)
                    reason += f" → {score:.4f} (high coverage: {coverage:.2f})"

        all_scores.append({
            "question": question_text[:150],
            "score": score,
            "reason": reason,
            "base_norm": base_norm[:100] if base_norm else "",
            "aug_norm": aug_norm[:100] if aug_norm else ""
        })

    # Sort by score
    all_scores.sort(key=lambda x: x["score"], reverse=True)

    # Now call the actual matching function
    matched_row, clarify_rows = _match_row_with_clarify(q)

    result = {
        "query": q,
        "normalized": {
            "base": uq_base,
            "augmented": uq_aug,
            "canonical_tokens": list(query_canon)
        },
        "faq_count": len(_FAQ),
        "thresholds": {
            "MATCH_THRESHOLD": _MATCH_THRESHOLD,
            "SEMANTIC_MATCH_THRESHOLD": _SEMANTIC_MATCH_THRESHOLD,
            "SEMANTIC_TRIGGER": _SEMANTIC_TRIGGER,
            "CERTAINTY_THRESHOLD": _CERTAINTY_THRESHOLD
        },
        "top_20_scores": all_scores[:20],
        "matched": None,
        "clarify_options": [],
        "matching_result": "NO_MATCH"
    }

    if matched_row:
        result["matched"] = {
            "question": matched_row.get("question", "")[:200],
            "answer": matched_row.get("answer", "")[:200],
            "category": matched_row.get("category", ""),
            "has_follow_up": matched_row.get("follow_up", False)
        }
        result["matching_result"] = "MATCHED"
    elif clarify_rows:
        result["clarify_options"] = [
            {
                "question": row.get("question", "")[:200],
                "category": row.get("category", "")
            }
            for row in clarify_rows
        ]
        result["matching_result"] = "NEEDS_CLARIFICATION"

    return result

@app.post("/ingest")
def ingest(req: IngestRequest):
    res = ingest_path(req.path, req.source_type)
    _reload_faq()
    return {"reloaded": True, "faq_rows": len(_FAQ), **res}

# >>> UNIQUE définition de /train/correction (avec try/except)
@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    try:
        return add_correction(req.question, req.answer, req.tags)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/train/correction error: {type(e).__name__}: {e}")

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())


@app.get("/debug/imports")
def debug_imports():
    out = {}
    try:
        import importlib.metadata as im
        out["chromadb"] = im.version("chromadb")
    except Exception as e:
        out["chromadb_error"] = str(e)

    try:
        import sqlite3
        out["sqlite3_version"] = getattr(sqlite3, "sqlite_version", "unknown")
    except Exception as e:
        out["sqlite3_error"] = str(e)

    try:
        import sys
        out["python"] = sys.version
        out["sys_path_sample"] = sys.path[:5]
    except Exception:  # pragma: no cover - diagnostics only
        pass
    try:
        out["vectorstore"] = vectorstore_status()
    except Exception as e:  # pragma: no cover - diagnostics only
        out["vectorstore_error"] = str(e)
    return out

def _build_response_from_suggestions(
    suggestions: List[dict],
    user_q: str,
    lang_code: str,
    high_confidence_threshold: float = 85.0,
    min_match_threshold: float = 30.0
) -> Optional[Dict[str, Any]]:
    """
    Construit la réponse appropriée basée sur les suggestions et leurs scores.

    Args:
        suggestions: Liste des suggestions avec scores
        user_q: Question de l'utilisateur
        lang_code: Code de langue
        high_confidence_threshold: Seuil pour haute confiance (défaut: 85%)
        min_match_threshold: Seuil minimum pour match (défaut: 30%)

    Returns:
        Dictionnaire de réponse ou None si aucune suggestion valide
    """
    if not suggestions:
        return {
            "success": True,
            "user_question": user_q,
            "response": {
                "type": "no_match",
                "message": _ensure_language(
                    "Ik heb geen geschikte antwoorden gevonden voor je vraag. Kun je je vraag anders formuleren?",
                    lang_code
                ),
                "suggestions": []
            }
        }

    # Vérifier le score du meilleur match
    best_score = suggestions[0]["similarity_score"]

    if best_score >= high_confidence_threshold:
        # Haute confiance: retourner la réponse directe + alternatives
        best_match = suggestions[0]
        alternatives = suggestions[1:] if len(suggestions) > 1 else []

        return {
            "success": True,
            "user_question": user_q,
            "response": {
                "type": "high_confidence",
                "message": _ensure_language("Voici la réponse la plus pertinente:", lang_code),
                "best_match": best_match,
                "alternatives": alternatives,
                "suggestions": suggestions
            }
        }
    elif best_score >= min_match_threshold:
        # Confiance moyenne: afficher toutes les suggestions
        return {
            "success": True,
            "user_question": user_q,
            "response": {
                "type": "multiple_suggestions",
                "message": _ensure_language("Voici les questions qui correspondent le mieux:", lang_code),
                "suggestions": suggestions
            }
        }
    else:
        # Aucun match suffisant
        return {
            "success": True,
            "user_question": user_q,
            "response": {
                "type": "no_match",
                "message": _ensure_language(
                    "Ik heb geen geschikte antwoorden gevonden voor je vraag. Voici quelques questions similaires:",
                    lang_code
                ),
                "suggestions": suggestions
            }
        }


# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    q = (req.query or "").strip()
    extra = req.extra or {}
    clarify_ref = (extra.get("clarify_ref") or extra.get("context_question") or "").strip()
    client_id = _client_id_from_request(request)

    # ── Rate limiting ────────────────────────────────────────────────────────
    if not _check_rate_limit(client_id):
        raise HTTPException(status_code=429, detail="Te veel verzoeken. Wacht even en probeer opnieuw.")

    # ── Query preprocessor: greetings / thanks / out-of-scope ───────────────
    if _PREPROCESSOR_AVAILABLE and q and not clarify_ref:
        try:
            processed = preprocess_query(q)
            if processed.immediate_response is not None:
                # Greeting, thanks, goodbye, out-of-scope → return immediately (no API cost)
                return processed.immediate_response
        except Exception as _pe:
            logger.debug(f"Preprocessor error: {_pe}")

    lang_code = detect_language_code(q)
    if not lang_code:
        stored_lang = _language_for_ref(q)
        if stored_lang:
            lang_code = stored_lang
    if clarify_ref and not lang_code:
        lang_code = _language_for_ref(clarify_ref)
    if not lang_code:
        pending_lang = (_PENDING_BY_CLIENT.get(client_id) or {}).get("language") if client_id else None
        if pending_lang:
            lang_code = pending_lang
    if not lang_code:
        lang_code = "nl"

    # ── Track this question in analytics ────────────────────────────────────
    if q:
        track_question(q, language=lang_code, source="api")

    # ── Response cache (only for non-clarify requests) ───────────────────────
    if q and not clarify_ref:
        cache_key = normalize_for_cache(q)
        cached = cache_get(cache_key)
        if cached is not None:
            track_cache_hit(q)
            return cached

    # ----- MODE SUGGESTIONS MULTIPLES -----
    # Si top_k > 1, utiliser le nouveau système de suggestions multiples
    use_multiple_suggestions = req.top_k > 1

    if use_multiple_suggestions and not clarify_ref:
        # Obtenir les suggestions FAQ avec scores
        try:
            suggestions = _get_faq_suggestions_with_scores(
                user_q=q,
                top_k=req.top_k,
                min_similarity=req.min_similarity,
                lang_code=lang_code
            )

            # Construire la réponse appropriée selon les scores
            response = _build_response_from_suggestions(
                suggestions=suggestions,
                user_q=q,
                lang_code=lang_code,
                high_confidence_threshold=85.0,
                min_match_threshold=req.min_similarity * 100  # Convertir en pourcentage
            )

            if response:
                return response

        except Exception as e:
            import logging
            logging.warning(f"Error in multiple suggestions mode: {e}")
            # Continue avec la logique traditionnelle en cas d'erreur

    # Corrections admin
    ans = cite = None
    score = 0.0
    try:
        ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    except Exception:
        ans = None
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        response = {"answer": _ensure_language(ans, lang_code), "citations": [cite], "used_chunks": used, "source": "correction"}
        return _add_suggestions_to_response(response, q, lang_code, None)

    # ----- 1) follow-up avec clarify_ref
    if clarify_ref:
        pend = _get_valid_pending(client_id)
        if pend and pend.get("mode") == "ambiguity" and _ref_key(pend.get("q")) == _ref_key(clarify_ref):
            candidates = pend.get("candidates") or []
            pend_lang = (pend.get("language") or "").strip().lower()
            if pend_lang:
                lang_code = pend_lang
            selected = _resolve_ambiguity_selection(pend, q)
            if selected:
                _PENDING_BY_CLIENT.pop(client_id, None)
                original_q = pend.get("q") or q
                return _respond_for_row(selected, lang_code, client_id, original_q)
            _set_pending(
                client_id,
                None,
                [],
                lang_code,
                mode="ambiguity",
                extra={"question": pend.get("q") or clarify_ref, "candidates": candidates},
            )
            message = _build_ambiguity_message(candidates)
            options = [(row.get("question") or "") for row in candidates]
            response = {
                "answer": _ensure_language(message, lang_code),
                "clarify": {"ref": pend.get("q") or clarify_ref, "options": options, "tips": []},
                "citations": [],
                "source": "faq"
            }
            return _add_suggestions_to_response(response, q, lang_code, None)

        base_row = _find_row_by_ref(clarify_ref)
        if not base_row:
            response = {"answer": _ensure_language("Ik kan je keuze niet aan de juiste vraag koppelen.", lang_code), "citations": [], "source": "faq"}
            return _add_suggestions_to_response(response, q, lang_code, None)

        row_lang = _language_for_ref(base_row.get("question"))
        if row_lang:
            lang_code = row_lang
        else:
            _remember_language(base_row.get("question"), lang_code)

        gen_key = _map_choice_to_genkey(q)
        if gen_key:
            chosen = _choose_gen_answer(base_row, gen_key)
            if chosen:
                _PENDING_BY_CLIENT.pop(client_id, None)
                response = {"answer": _ensure_language(chosen, lang_code), "citations": _citations_for_row(base_row), "source": "faq"}
                return _add_suggestions_to_response(response, q, lang_code, base_row)

        labels = _labels(base_row)
        if not labels:
            direct = (base_row.get("answer") or base_row.get("antwoord") or "").strip()
            if direct:
                _PENDING_BY_CLIENT.pop(client_id, None)
                response = {"answer": _ensure_language(direct, lang_code), "citations": _citations_for_row(base_row), "source": "faq"}
                return _add_suggestions_to_response(response, q, lang_code, base_row)
            response = {"answer": _ensure_language("Geen antwoord gevonden.", lang_code), "citations": [], "source": "faq"}
            return _add_suggestions_to_response(response, q, lang_code, base_row)

        label = _map_choice_to_key(q, labels)
        if not label:
            response = {
                "answer": _ensure_language("Ik herken deze keuze niet. Kies één van: " + ", ".join(labels), lang_code),
                "citations": _citations_for_row(base_row),
                "source": "faq"
            }
            return _add_suggestions_to_response(response, q, lang_code, base_row)
        answer, media = _build_answer_for_option(base_row, label)
        _PENDING_BY_CLIENT.pop(client_id, None)
        response = {"answer": _ensure_language(answer, lang_code), "citations": _citations_for_row(base_row), "source": "faq"}
        if media:
            response["media"] = media
        return _add_suggestions_to_response(response, q, lang_code, base_row)

    # ----- follow-up zonder expliciete clarify_ref (pending memory)
    if _looks_like_followup_choice(q):
        pend = _pop_valid_pending(client_id)
        if pend:
            pend_lang = (pend.get("language") or "").strip().lower()
            if pend_lang:
                lang_code = pend_lang
            if pend.get("mode") == "ambiguity":
                candidates = pend.get("candidates") or []
                selected = _resolve_ambiguity_selection(pend, q)
                if selected:
                    original_q = pend.get("q") or q
                    return _respond_for_row(selected, lang_code, client_id, original_q)
                _set_pending(
                    client_id,
                    None,
                    [],
                    lang_code,
                    mode="ambiguity",
                    extra={"question": pend.get("q") or q, "candidates": candidates},
                )
                message = _build_ambiguity_message(candidates)
                options = [(row.get("question") or "") for row in candidates]
                response = {
                    "answer": _ensure_language(message, lang_code),
                    "clarify": {"ref": pend.get("q") or q, "options": options, "tips": []},
                    "citations": [],
                    "source": "faq"
                }
                return _add_suggestions_to_response(response, q, lang_code, None)

            base_row = pend.get("row")
            if isinstance(base_row, dict):
                labels = pend.get("labels") or []
                if not pend_lang:
                    _remember_language(base_row.get("question"), lang_code)

                gen_key = _map_choice_to_genkey(q)
                if gen_key:
                    chosen = _choose_gen_answer(base_row, gen_key)
                    if chosen:
                        _PENDING_BY_CLIENT.pop(client_id, None)
                        response = {"answer": _ensure_language(chosen, lang_code), "citations": _citations_for_row(base_row), "source": "faq"}
                        return _add_suggestions_to_response(response, q, lang_code, base_row)

                if labels:
                    label = _map_choice_to_key(q, labels)
                    if not label:
                        matches = get_close_matches(q, labels, n=1, cutoff=0.45)
                        label = matches[0] if matches else None
                    if label:
                        _PENDING_BY_CLIENT.pop(client_id, None)
                        answer, media = _build_answer_for_option(base_row, label)
                        resp = {"answer": _ensure_language(answer, lang_code), "citations": _citations_for_row(base_row), "source": "faq"}
                        if media:
                            resp["media"] = media
                        return _add_suggestions_to_response(resp, q, lang_code, base_row)

            response = {
                "answer": _ensure_language("Ik heb nog even de context nodig: bij welke vraag hoort deze keuze? Kies opnieuw bij de vorige vraag, of stuur je keuze met de contextvraag mee.", lang_code),
                "need_ref": True,
                "citations": [],
                "source": "faq"
            }
            return _add_suggestions_to_response(response, q, lang_code, None)

    # ----- Pre-translate non-Dutch queries for better matching -----
    translated_query = None
    if lang_code and lang_code not in {"", "nl"}:
        try:
            translated_query = translate_for_matching(q, lang_code)
        except Exception:
            translated_query = None
        if translated_query and _normalize(translated_query) == _normalize(q):
            translated_query = None  # no actual translation happened

    # ----- 1b) Direct FAQ answer (high-confidence keyword match, no LLM) -----
    if _DIRECT_ANSWER_AVAILABLE and q and not clarify_ref:
        try:
            # Try original query first, then translated query
            direct_result = get_direct_answer_with_suggestions(q)
            if direct_result is None and translated_query:
                direct_result = get_direct_answer_with_suggestions(translated_query)
            if direct_result is not None:
                matched_row = direct_result.get("row") or {}
                pre_translated = _get_answer_in_language(matched_row, lang_code)
                if pre_translated:
                    answer_text = polish_faq_answer(pre_translated, lang_code)
                else:
                    polished_nl = polish_faq_answer(direct_result["answer"], "nl")
                    answer_text = _ensure_language(polished_nl, lang_code)
                response = {
                    "answer": answer_text,
                    "citations": direct_result.get("citations", []),
                    "source": "direct_faq",
                    "confidence": direct_result.get("confidence"),
                }
                _enrich_response_with_media(response, matched_row)
                if direct_result.get("suggestions"):
                    response["suggestions"] = direct_result["suggestions"]
                cache_set(normalize_for_cache(q), response)
                return response
        except Exception as _de:
            logger.debug(f"Direct answer error: {_de}")

    # ----- 2) lookup direct dans l'index
    matched_row, clarify_rows = _match_row_with_clarify(q)
    if not matched_row and not clarify_rows and translated_query:
        alt_row, alt_clarify = _match_row_with_clarify(translated_query)
        if alt_clarify:
            clarify_rows = alt_clarify
        elif alt_row:
            matched_row = alt_row
    if clarify_rows:
        _set_pending(
            client_id,
            None,
            [],
            lang_code,
            mode="ambiguity",
            extra={"question": q, "candidates": clarify_rows},
        )
        options = [(row.get("question") or "") for row in clarify_rows]
        message = _build_ambiguity_message(clarify_rows)
        response = {
            "answer": _ensure_language(message, lang_code),
            "clarify": {"ref": q, "options": options, "tips": []},
            "citations": [],
            "source": "faq"
        }
        return _add_suggestions_to_response(response, q, lang_code, None)
    if matched_row:
        result = _respond_for_row(matched_row, lang_code, client_id, q)
        cache_set(normalize_for_cache(q), result)
        return result

    # ----- 3) GPT fallback for intelligent answers
    track_no_answer(q, language=lang_code)
    gpt_answer = _gpt_fallback_answer(q, lang_code)
    if gpt_answer:
        # Add a note that the answer is AI-generated (in user's language)
        _AI_NOTES = {
            "nl": "\n\n*Deze informatie is gegenereerd door AI. Voor specifieke vragen kunt u contact opnemen met support@beniferro.eu.*",
            "fr": "\n\n*Ces informations sont générées par IA. Pour des questions spécifiques, veuillez contacter support@beniferro.eu.*",
            "en": "\n\n*This information is AI-generated. For specific questions, please contact support@beniferro.eu.*",
            "de": "\n\n*Diese Informationen wurden von KI generiert. Bei spezifischen Fragen wenden Sie sich bitte an support@beniferro.eu.*",
        }
        ai_note = _AI_NOTES.get(lang_code, _AI_NOTES["nl"])
        full_answer = gpt_answer + ai_note
        response = {
            "answer": full_answer,
            "citations": [],
            "source": "ai_fallback"
        }
        result = _add_suggestions_to_response(response, q, lang_code, None)
        cache_set(normalize_for_cache(q), result)
        return result

    # ----- 4) RAG fallback
    extra_gen = _parse_extra_gen(req.extra)
    try:
        gen = extra_gen or detect_gen(q)
    except Exception:
        gen = extra_gen

    try:
        docs = retrieve(q, gen_filter=gen)
    except TypeError:
        try:
            docs = retrieve(q)
        except Exception:
            docs = []
    except Exception:
        docs = []

    try:
        found: Set[str] = extract_found_gens(docs)
    except Exception:
        found = set()

    if not gen and found:
        options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen 2"]
        fake_row = {"question": q, "options": {o: {} for o in options}}
        _remember_language(q, lang_code)
        _set_pending(client_id, fake_row, options, lang_code)
        response = {
            "answer": _ensure_language("Hebt u een Gen 1 of een Gen 2 apparaat?", lang_code),
            "clarify": {"ref": q, "options": options, "tips": GEN_TIPS_NL},
            "citations": [],
            "source": "rag"
        }
        return _add_suggestions_to_response(response, q, lang_code, None)

    if not docs:
        response = {
            "answer": _ensure_language("Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?", lang_code),
            "citations": [],
            "source": "rag"
        }
        return _add_suggestions_to_response(response, q, lang_code, None)

    try:
        answer, citations = generate_answer(q, docs)
    except Exception:
        response = {
            "answer": _ensure_language("Ik kan momenteel geen automatisch antwoord genereren. Kun je je vraag op een andere manier formuleren of meer details geven?", lang_code),
            "citations": [],
            "source": "rag"
        }
        return _add_suggestions_to_response(response, q, lang_code, None)

    response = {"answer": answer, "citations": citations, "source": "rag"}
    result = _add_suggestions_to_response(response, q, lang_code, None)
    cache_set(normalize_for_cache(q), result)
    return result


# ---------------------------------------------------------------------
# Serve Static Frontend
# ---------------------------------------------------------------------
import os

@app.get("/")
async def serve_frontend():
    """Serve the index.html file at root"""
    index_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "index.html")
    return FileResponse(index_path)

@app.get("/dashboard")
async def serve_dashboard():
    """Serve the admin dashboard"""
    dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.html")
    return FileResponse(dashboard_path)


# ---------------------------------------------------------------------
# Static FAQ images (extracted from Excel)
# ---------------------------------------------------------------------
_FAQ_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "data", "faq_images")
os.makedirs(_FAQ_IMAGES_DIR, exist_ok=True)
app.mount("/faq_images", StaticFiles(directory=_FAQ_IMAGES_DIR), name="faq_images")


# ---------------------------------------------------------------------
# Admin: reload from Excel (AI 2.0.xlsx → JSONL + images)
# ---------------------------------------------------------------------
@app.post("/admin/reload-excel")
async def reload_from_excel_endpoint(polish: bool = False):
    """Rebuild FAQAI.jsonl + faq_images/ from AI 2.0.xlsx and reload the in-memory FAQ.

    Query params:
      - polish=true → run an LLM pass to fix typos/grammar in every answer
        (slower, but produces clean user-facing responses).
    """
    try:
        from .excel_loader import reload_from_excel
        summary = reload_from_excel(polish=polish)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Excel reload failed")
        raise HTTPException(status_code=500, detail=f"Excel reload failed: {e}")

    count, _ = _reload_faq()
    return {
        "ok": True,
        "entries": summary["entries"],
        "images": summary["images"],
        "synonym_groups": summary["synonym_groups"],
        "polished": summary.get("polished", 0),
        "faq_loaded": count,
    }
