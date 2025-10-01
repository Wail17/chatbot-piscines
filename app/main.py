# app/main.py
from typing import List, Optional, Dict, Any, Set

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,          # détecte "gen1"/"gen2"/"gen3" dans la question (ou None)
    extract_found_gens,  # récupère {"gen1","gen2"} depuis les métadonnées des docs
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")

# ---------------------------------------------------------------------
# CORS : ajoute tes domaines (WP + éventuel domaine Railway si tu appelles direct)
# ---------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    # "https://web-production-xxxx.up.railway.app",
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
# Payloads
# ---------------------------------------------------------------------
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

# Aide NL pour reconnaître Gen1/Gen2 (affichable côté front)
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, "
    "en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, "
    "en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _normalize_gen(x: Optional[str]) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().lower()
    if s in {"gen1", "gen 1", "1"}:
        return "gen1"
    if s in {"gen2", "gen 2", "2"}:
        return "gen2"
    if s in {"gen3", "gen 3", "3"}:
        return "gen3"
    return None

def _is_truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "ja", "oui"}

def _first_followup(docs) -> str:
    """Retourne la première sous-question (followup_q) rencontrée dans les métadonnées."""
    for d in docs or []:
        md = d.metadata or {}
        fu = (md.get("followup_q") or "").strip()
        if fu:
            return fu
    return ""

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest):
    return ingest_path(req.path, req.source_type)

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

    # 2) GEN fournie/détectée
    extra_gen = None
    if isinstance(req.extra, dict) and "gen" in req.extra:
        extra_gen = _normalize_gen(req.extra.get("gen"))

    try:
        detected = detect_gen(q)  # peut renvoyer None
    except NameError:
        detected = None

    gen = extra_gen or _normalize_gen(detected)

    # 3) Si pas de GEN -> on sonde sans filtre et on regarde ask_gen dans les métadonnées
    if not gen:
        probe_docs = retrieve(q, gen_filter=None)

        should_ask = False
        for d in probe_docs or []:
            md = d.metadata or {}
            if _is_truthy(md.get("ask_gen")):
                should_ask = True
                break

        if should_ask:
            # Options = Gens présentes; défaut ["gen1","gen2"] si rien n'est tagué
            try:
                found: Set[str] = extract_found_gens(probe_docs)
            except NameError:
                found = set()
            options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen2"]

            clarify_prompt = _first_followup(probe_docs) or "Hebt u een Gen 1 of een Gen 2 apparaat?"

            return {
                "answer": clarify_prompt,
                "clarify": {
                    "param": "gen",
                    "options": options,
                    "tips": GEN_TIPS_NL,
                },
                "citations": [],
                "used_chunks": [] if req.debug else None,
            }

        # Sinon on répond directement avec ces docs non filtrés
        answer, citations = generate_answer(q, probe_docs)
        used = [{"text": d.page_content, "meta": d.metadata} for d in probe_docs] if req.debug else None
        return {"answer": answer, "citations": citations, "used_chunks": used}

    # 4) GEN connue -> on filtre la recherche
    docs = retrieve(q, gen_filter=gen)
    answer, citations = generate_answer(q, docs)
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
