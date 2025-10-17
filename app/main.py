# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os, json, re, unicodedata, time
from math import sqrt
from threading import Lock
from difflib import SequenceMatcher, get_close_matches

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langchain_openai import OpenAIEmbeddings

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,
    extract_found_gens,
    detect_language_code,
    translate_answer,
    translate_for_matching,
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback, vectorstore_status
from .config import (
    CORRECTION_THRESHOLD, STORE_DIR, DATA_DIR, EMBEDDINGS_MODEL,
    # optionnel si tu veux un /health bavard:
    # CHROMA_DIR, EMBEDDINGS_MODEL, FEEDBACK_FILE, CORRECTIONS_COLLECTION
)

app = FastAPI(title="Chatbot Piscines API")

# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
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
        return []

    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                question = _coerce_str(
                    obj.get("vraag")
                    or obj.get("Vraag")
                    or obj.get("question")
                    or obj.get("Question")
                )
                if not question:
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

                tags = obj.get("tags")
                if isinstance(tags, list):
                    row["tags"] = [str(t).strip() for t in tags if str(t).strip()]

                rows.append(row)
    except Exception:
        return []

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
    _reset_faq_embeddings()
    for row in _FAQ:
        row["_embedding"] = _EMBED_UNSET
    return (len(_FAQ), _FAQ)

_reload_faq()

# ---------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*")
_MATCH_THRESHOLD = 0.68
_SEMANTIC_MATCH_THRESHOLD = 0.78
_SEMANTIC_TRIGGER = 0.63
_CERTAINTY_THRESHOLD = 0.84
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
        "chlorinator",
        "salt",
        "saltelektrolyse",
        "salzelektrolyse",
        "salzelectrolyse",
        "zoutelektrolysetoestel",
        "zout elektrolyse",
        "salt electrolysis",
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
        "startet nicht",
        "geht nicht an",
        "no arranca",
        "ne démarre pas",
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


def _semantic_scores(user_q: str) -> Dict[int, Tuple[dict, float]]:
    global _FAQ_EMBED_DISABLED
    results: Dict[int, Tuple[dict, float]] = {}
    if not _FAQ or not user_q or _FAQ_EMBED_DISABLED:
        return results
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
                score *= 0.55
            else:
                overlap = len(query_canon & row_canon)
                coverage = overlap / float(len(query_canon))
                if coverage < 0.4:
                    score *= 0.55
                elif coverage < 0.65:
                    score *= 0.75
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


def _respond_for_row(row: dict, lang_code: str, client_id: str) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {"answer": _ensure_language("Geen antwoord gevonden.", lang_code), "citations": []}
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
        return {
            "answer": _ensure_language(answer_text, lang_code),
            "clarify": {"ref": row.get("question"), "options": labels, "tips": tips},
            "citations": _citations_for_row(row),
        }
    direct = (row.get("answer") or row.get("antwoord") or "").strip()
    if direct:
        extra_line = "\n\nBekijk video: " + str(row["video_url"]) if row.get("video_url") else ""
        return {
            "answer": _ensure_language(direct + extra_line, lang_code),
            "citations": _citations_for_row(row),
        }
    return {"answer": _ensure_language("Geen antwoord gevonden.", lang_code), "citations": []}


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

def _render_option_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    ans = (payload.get("answer") or payload.get("antwoord") or "").strip()
    rec = (payload.get("recommendation") or payload.get("aanbeveling") or "").strip()
    if ans:
        return ans + (("\n\nAanbeveling: " + rec) if rec else "")
    lines: List[str] = []
    for k, v in payload.items():
        if v in (None, "", []): continue
        key = str(k).replace("_", " ").strip().capitalize()
        if isinstance(v, list):
            items = "\n".join([f"  • {str(x)}" for x in v if str(x).strip()])
            lines.append(f"{key}:\n{items}")
        elif isinstance(v, dict):
            parts = [f"{kk}: {vv}" for kk, vv in v.items() if vv not in (None, "", [])]
            if parts:
                lines.append(f"{key}: " + "; ".join(parts))
        else:
            lines.append(f"{key}: {v}")
    return "\n".join(lines).strip()

def _build_answer_for_option(row: dict, option_label: str) -> str:
    payload = (row.get("options") or {}).get(option_label)
    if payload is None:
        return "Ik heb geen details gevonden voor deze keuze."
    intro = (row.get("answer") or row.get("antwoord") or "").strip()
    body = _render_option_payload(payload)
    return (intro + "\n\n" + body).strip() if intro else body

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
    return {"status": "ok", "faq_rows": len(_FAQ)}

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

# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    q = (req.query or "").strip()
    extra = req.extra or {}
    clarify_ref = (extra.get("clarify_ref") or extra.get("context_question") or "").strip()
    client_id = _client_id_from_request(request)

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

    # Corrections admin
    ans = cite = None
    score = 0.0
    try:
        ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    except Exception:
        ans = None
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": _ensure_language(ans, lang_code), "citations": [cite], "used_chunks": used}

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
                return _respond_for_row(selected, lang_code, client_id)
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
            return {
                "answer": _ensure_language(message, lang_code),
                "clarify": {"ref": pend.get("q") or clarify_ref, "options": options, "tips": []},
                "citations": [],
            }

        base_row = _find_row_by_ref(clarify_ref)
        if not base_row:
            return {"answer": _ensure_language("Ik kan je keuze niet aan de juiste vraag koppelen.", lang_code), "citations": []}

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
                return {"answer": _ensure_language(chosen, lang_code), "citations": _citations_for_row(base_row)}

        labels = _labels(base_row)
        if not labels:
            direct = (base_row.get("answer") or base_row.get("antwoord") or "").strip()
            if direct:
                _PENDING_BY_CLIENT.pop(client_id, None)
                return {"answer": _ensure_language(direct, lang_code), "citations": _citations_for_row(base_row)}
            return {"answer": _ensure_language("Geen antwoord gevonden.", lang_code), "citations": []}

        label = _map_choice_to_key(q, labels)
        if not label:
            return {
                "answer": _ensure_language("Ik herken deze keuze niet. Kies één van: " + ", ".join(labels), lang_code),
                "citations": _citations_for_row(base_row)
            }
        answer = _build_answer_for_option(base_row, label)
        _PENDING_BY_CLIENT.pop(client_id, None)
        return {"answer": _ensure_language(answer, lang_code), "citations": _citations_for_row(base_row)}

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
                    return _respond_for_row(selected, lang_code, client_id)
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
                return {
                    "answer": _ensure_language(message, lang_code),
                    "clarify": {"ref": pend.get("q") or q, "options": options, "tips": []},
                    "citations": [],
                }

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
                        return {"answer": _ensure_language(chosen, lang_code), "citations": _citations_for_row(base_row)}

                if labels:
                    label = _map_choice_to_key(q, labels)
                    if not label:
                        matches = get_close_matches(q, labels, n=1, cutoff=0.45)
                        label = matches[0] if matches else None
                    if label:
                        _PENDING_BY_CLIENT.pop(client_id, None)
                        answer = _build_answer_for_option(base_row, label)
                        return {"answer": _ensure_language(answer, lang_code), "citations": _citations_for_row(base_row)}

            return {
                "answer": _ensure_language("Ik heb nog even de context nodig: bij welke vraag hoort deze keuze? Kies opnieuw bij de vorige vraag, of stuur je keuze met de contextvraag mee.", lang_code),
                "need_ref": True,
                "citations": [],
            }

    # ----- 2) lookup direct dans l'index
    matched_row, clarify_rows = _match_row_with_clarify(q)
    translated_query = None
    if not matched_row and not clarify_rows and lang_code not in {"", "nl"}:
        try:
            translated_query = translate_for_matching(q, lang_code)
        except Exception:
            translated_query = None
        if translated_query:
            if _normalize(translated_query) != _normalize(q):
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
        return {
            "answer": _ensure_language(message, lang_code),
            "clarify": {"ref": q, "options": options, "tips": []},
            "citations": [],
        }
    if matched_row:
        return _respond_for_row(matched_row, lang_code, client_id)

    # ----- 3) RAG fallback
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
        return {
            "answer": _ensure_language("Hebt u een Gen 1 of een Gen 2 apparaat?", lang_code),
            "clarify": {"ref": q, "options": options, "tips": GEN_TIPS_NL},
            "citations": [],
        }

    if not docs:
        return {
            "answer": _ensure_language("Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?", lang_code),
            "citations": [],
        }

    try:
        answer, citations = generate_answer(q, docs)
    except Exception:
        return {
            "answer": _ensure_language("Ik kan momenteel geen automatisch antwoord genereren. Kun je je vraag op een andere manier formuleren of meer details geven?", lang_code),
            "citations": [],
        }
    return {"answer": answer, "citations": citations}
