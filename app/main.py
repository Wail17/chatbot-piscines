# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import json
import os
import re
from difflib import SequenceMatcher

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,          # -> "gen1" / "gen2" / "gen3" ou None
    extract_found_gens,  # -> set({"gen1","gen2"}) trouvé dans les metadata
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD, STORE_DIR

# ------------------------------------------------------------------------------------
# App
# ------------------------------------------------------------------------------------
app = FastAPI(title="Chatbot Piscines API")

# ------------------------------------------------------------------------------------
# CORS  (mets ici tes domaines de prod et de la page WP où s’intègre le widget)
# ------------------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    # "https://web-production-XXXX.up.railway.app",  # si tu appelles direct l’API
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

# ------------------------------------------------------------------------------------
# Chargement de l’index FAQ (écrit par /ingest)
# ------------------------------------------------------------------------------------
FAQ_INDEX_PATH = os.path.join(STORE_DIR, "faq_index.json")
_FAQ: List[Dict[str, Any]] = []

def _normalize(s: str) -> str:
    # lower + remove accents
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()

    # normalize spaces (NBSP -> space, collapse)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)

    # remove punctuation and any spaces around it (handles "module ?" vs "module?")
    s = re.sub(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*", "", s)

    return s.strip()

def _load_faq_index():
    global _FAQ
    try:
        with open(FAQ_INDEX_PATH, "r", encoding="utf-8") as f:
            _FAQ = json.load(f)
    except Exception:
        _FAQ = []

_load_faq_index()

def _best_faq_match(user_q: str, min_score: float = 0.80) -> dict | None:
    """
    Exact/inclusion match after normalization, else fuzzy (difflib) with a slightly
    lower threshold to survive tiny typos/spaces differences from Excel.
    """
    if not _FAQ:
        return None
    uq = _normalize(user_q)
    best: tuple[float, dict | None] = (0.0, None)

    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue

        # exact or inclusion -> immediate hit
        if uq == q or uq in q or q in uq:
            return row

        # fuzzy
        score = SequenceMatcher(None, uq, q).ratio()
        if score > best[0]:
            best = (score, row)

    return best[1] if best[0] >= min_score else None

# ------------------------------------------------------------------------------------
# Modèles
# ------------------------------------------------------------------------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None  # ex: {"gen":"gen1"}

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

# Conseils NL pour reconnaître Gen1/Gen2 (affichable côté front si besoin)
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ------------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.post("/ingest")
def ingest(req: IngestRequest):
    """
    Supporte dossiers, fichiers texte ET Excel (.xlsx/.xls).
    Après ingestion, recharge l’index FAQ en mémoire.
    """
    out = ingest_path(req.path, req.source_type)
    _load_faq_index()
    out["faq_rows"] = len(_FAQ)
    return out

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

# ---- (optionnel) debug pour tester le lookup direct
@app.get("/debug/faq_lookup")
def debug_faq_lookup(q: str):
    row = _best_faq_match(q)
    return {"query": q, "found": bool(row), "row": row}

@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 0) Correction admin prioritaire
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 1) Lookup direct dans l’Excel (faq_index.json)
    direct_row = _best_faq_match(q)
    if direct_row:
        # Si la ligne exige une clarification GEN ET que l’utilisateur n’a pas encore choisi
        extra_gen = (req.extra or {}).get("gen") if isinstance(req.extra, dict) else None
        if direct_row.get("ask_gen") and not extra_gen:
            options = direct_row.get("gens") or ["gen1", "gen2"]
            # formater en minuscules propres (["gen1","gen2",...])
            opts = [str(x).lower() for x in options if str(x).strip()]
            prompt = direct_row.get("followup_q") or "Hebt u een Gen 1 of een Gen 2 apparaat?"

            return {
                "answer": prompt,
                "clarify": {
                    "param": "gen",
                    "options": opts,
                    "tips": GEN_TIPS_NL,
                },
                "citations": [{
                    "title": direct_row.get("question") or "FAQ",
                    "url": None,
                    "source": direct_row.get("source"),
                    "page": None,
                }],
                "used_chunks": None if not req.debug else [{
                    "text": f"FAQ direct hit: {direct_row.get('question')}",
                    "meta": {"source": direct_row.get("source"), "sheet": direct_row.get("sheet")}
                }]
            }

        # Sinon, renvoyer la réponse de la ligne Excel directement
        answer = direct_row.get("answer") or direct_row.get("Antwoord") or "(geen antwoord)"
        return {
            "answer": answer,
            "citations": [{
                "title": direct_row.get("question") or "FAQ",
                "url": None,
                "source": direct_row.get("source"),
                "page": None,
            }],
            "used_chunks": None if not req.debug else [{
                "text": f"FAQ direct hit: {direct_row.get('question')}",
                "meta": {"source": direct_row.get("source"), "sheet": direct_row.get("sheet")}
            }]
        }

    # 2) Sinon, on passe au RAG
    #    Déterminer la génération si fournie ou détectée dans la question
    extra_gen = None
    if isinstance(req.extra, dict):
        g = str(req.extra.get("gen", "")).strip().lower()
        if g in {"gen1", "gen 1"}:
            extra_gen = "gen1"
        elif g in {"gen2", "gen 2"}:
            extra_gen = "gen2"
        elif g in {"gen3", "gen 3"}:
            extra_gen = "gen3"
    try:
        gen = extra_gen or detect_gen(q)   # peut renvoyer None
    except NameError:
        gen = extra_gen

    # Récupération vectorielle (avec éventuel filtre gen)
    try:
        docs = retrieve(q, gen_filter=gen)
    except TypeError:
        docs = retrieve(q)

    # Si l’Excel marque des générations et que l’utilisateur n’a pas précisé -> demander GEN
    try:
        found: Set[str] = extract_found_gens(docs)
    except NameError:
        found = set()

    if not gen and found:
        options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen2"]
        return {
            "answer": "Hebt u een Gen 1 of een Gen 2 apparaat?",
            "clarify": {
                "param": "gen",
                "options": options,
                "tips": GEN_TIPS_NL,
            },
            "citations": [],
            "used_chunks": [] if req.debug else None,
        }

    # 3) Générer la réponse RAG
    if not docs:
        # message gentil si aucun context
        return {
            "answer": (
                "Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. "
                "Kun je meer details geven over wat je precies wilt weten?"
            ),
            "citations": [],
            "used_chunks": [] if req.debug else None,
        }

    answer, citations = generate_answer(q, docs)
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
