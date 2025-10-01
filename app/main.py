# app/main.py
from typing import List, Optional, Dict, Any, Set
import os, json, re, unicodedata
from difflib import SequenceMatcher

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,
    extract_found_gens,
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD, STORE_DIR

app = FastAPI(title="Chatbot Piscines API")

# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    # "https://web-production-XXXX.up.railway.app",
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
    tags: List[str] = []

class FeedbackIn(BaseModel):
    question: str
    answer: str
    good: bool
    corrected_answer: Optional[str] = None
    notes: Optional[str] = None
    user: Optional[str] = None

GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ---------------------------------------------------------------------
# Load FAQ index (écrit par /ingest)
# ---------------------------------------------------------------------
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
try:
    with open(_FAQ_PATH, "r", encoding="utf-8") as f:
        _FAQ: List[dict] = json.load(f)
except Exception:
    _FAQ = []

# ---------------- Lookup helpers (robustes) ----------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*")

def _normalize(s: str) -> str:
    # lower + retire accents
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()

    # NBSP -> espace + compresser
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)

    # retirer ponctuation (et espaces autour) pour tolérer "module ?" vs "module?"
    s = _PUNCT_RE.sub("", s)
    return s.strip()

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _best_faq_match(user_q: str, min_score: float = 0.80) -> dict | None:
    """1) match exact/inclusion sur question normalisée
       2) fuzzy sur question
       3) inclusion/fuzzy sur réponse si besoin (utile si l'info est surtout dans 'answer')."""
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # 1) exact / inclusion sur question
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        if uq == q or uq in q or q in uq:
            return row

    # 2) fuzzy sur question
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        sc = _ratio(uq, q)
        if sc > best[0]:
            best = (sc, row)
    if best[0] >= min_score:
        return best[1]

    # 3) Inclusion/fuzzy sur réponse
    best = (0.0, None)
    for row in _FAQ:
        a = _normalize(row.get("answer", ""))
        if not a:
            continue
        if uq and (uq in a or a in uq):
            return row
        sc = _ratio(uq, a)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= (min_score - 0.08) else None  # un poil plus tolérant

# --- recherche par mots-clés (synonymes utiles) ------------
KW_MAP = {
    # clés -> listes de variantes
    "tlf": ["tlf"],
    "thermometer": ["thermometer", "thermometers", "vloeistof thermometer", "vloeistofthermometer"],
    "temperatuur": ["temperatuur", "temperatuursmeting", "temperatuurmeting", "temperatuursmetingen", "sensors", "sensor"],
    "aansluiten": ["aansluiten", "aangesloten", "ondersteunen", "kan", "heeft"],
    "twee": ["twee", "2", "tweede"],
}

def _expand_keywords(user_q: str) -> List[str]:
    uq = _normalize(user_q)
    toks = set(uq.split())
    out: set[str] = set()
    for base, variants in KW_MAP.items():
        if base in toks or any(v in uq for v in variants):
            out.update(variants + [base])
    # cas TLF + thermo → ajoute 'temperatuur' car Excel parle de temperatuurmetingen
    if ("tlf" in out) and ({"thermometer"} & set(KW_MAP.keys())):
        out.update(KW_MAP["temperatuur"])
    return list(out)

def _faq_keyword_search(user_q: str) -> dict | None:
    """Scanne question+réponse normalisées et score par nb de mots-clés trouvés."""
    if not _FAQ:
        return None
    kws = _expand_keywords(user_q)
    if not kws:
        return None

    best = (0, None)
    for row in _FAQ:
        blob = _normalize((row.get("question") or "") + " " + (row.get("answer") or ""))
        hits = sum(1 for k in kws if k in blob)
        if hits > best[0]:
            best = (hits, row)

    # exiger min 2 hits pour éviter faux positifs
    return best[1] if best[0] >= 2 else None

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    r1 = _best_faq_match(q)
    r2 = None if r1 else _faq_keyword_search(q)
    return {
        "q": q,
        "best_match": bool(r1 or r2),
        "via": "best" if r1 else ("keywords" if r2 else None),
        "row": (r1 or r2),
    }

@app.post("/ingest")
def ingest(req: IngestRequest):
    res = ingest_path(req.path, req.source_type)
    # recharger l'index si régénéré
    global _FAQ
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            _FAQ = json.load(f)
    except Exception:
        pass
    return res

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) Correction admin prioritaire
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Lookup FAQ ultra-robuste (question puis mots-clés)
    row = _best_faq_match(q) or _faq_keyword_search(q)
    if row:
        # clarification GEN seulement si la ligne le demande
        ask_gen_flag = bool(row.get("ask_gen"))
        if ask_gen_flag:
            opts = ["gen1", "gen2"]
            return {
                "answer": row.get("followup_q") or "Hebt u een Gen 1 of een Gen 2 apparaat?",
                "clarify": {"param": "gen", "options": opts, "tips": GEN_TIPS_NL},
                "citations": [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}],
            }

        # réponse directe de l’index
        extra_line = ""
        if row.get("video_url"):
            extra_line = "\n\nBekijk video: " + str(row["video_url"])
        citations = [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}]
        return {"answer": row.get("answer", "") + extra_line, "citations": citations}

    # 3) Déterminer génération éventuelle
    extra_gen = None
    if isinstance(req.extra, dict):
        g = str(req.extra.get("gen", "")).strip().lower()
        extra_gen = "gen1" if g in {"gen1", "gen 1"} else "gen2" if g in {"gen2", "gen 2"} else "gen3" if g in {"gen3", "gen 3"} else None
    try:
        gen = extra_gen or detect_gen(q)
    except NameError:
        gen = extra_gen

    # 4) Récupération RAG (filtrée si gen détectée)
    try:
        docs = retrieve(q, gen_filter=gen)
    except TypeError:
        docs = retrieve(q)

    # 5) Si l’Excel marque des générations et que l’utilisateur n’a pas précisé -> demander GEN
    try:
        found: Set[str] = extract_found_gens(docs)
    except NameError:
        found = set()

    if not gen and found:
        options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen2"]
        return {
            "answer": "Hebt u een Gen 1 of een Gen 2 apparaat?",
            "clarify": {"param": "gen", "options": options, "tips": GEN_TIPS_NL},
            "citations": [],
        }

    # 6) Réponse RAG standard (ou fallback si rien)
    if not docs:
        return {
            "answer": "Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?",
            "citations": [],
        }
    answer, citations = generate_answer(q, docs)
    return {"answer": answer, "citations": citations}
