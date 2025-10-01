# app/main.py
from typing import List, Optional, Dict, Any, Set

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import de base obligatoires
from .rag import retrieve as _raw_retrieve, generate_answer

# Imports optionnels (si absents on met des stubs)
try:
    from .rag import detect_gen as _detect_gen
except Exception:
    _detect_gen = None  # type: ignore

try:
    from .rag import extract_found_gens as _extract_found_gens
except Exception:
    _extract_found_gens = None  # type: ignore

from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")

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

# ---------- Models ----------
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

GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ---------- Small helpers ----------
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
    return str(v).strip().lower() in {"1", "true", "yes", "ja", "oui"}

def _first_followup(docs) -> str:
    for d in docs or []:
        fu = (d.metadata or {}).get("followup_q") or ""
        if str(fu).strip():
            return str(fu).strip()
    return ""

def _safe_detect_gen(text: str) -> Optional[str]:
    try:
        if _detect_gen is None:
            return None
        return _detect_gen(text)  # may return None
    except Exception:
        return None

def _safe_extract_found_gens(docs) -> Set[str]:
    try:
        if _extract_found_gens is None:
            return set()
        return set(_extract_found_gens(docs) or [])
    except Exception:
        return set()

def _safe_retrieve(question: str, gen_filter: Optional[str]):
    """
    Supporte retrieve(question) ET retrieve(question, gen_filter=...).
    """
    try:
        if gen_filter:
            return _raw_retrieve(question, gen_filter=gen_filter)
        return _raw_retrieve(question)
    except TypeError:
        # Ancienne signature sans gen_filter
        return _raw_retrieve(question)
    except Exception:
        # Dernier filet
        return []

# ---------- Routes ----------
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
    try:
        q = (req.query or "").strip()

        # 1) Corrections admin
        ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
        if ans:
            used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
            return {"answer": ans, "citations": [cite], "used_chunks": used}

        # 2) GEN fournie/détectée
        extra_gen = None
        if isinstance(req.extra, dict) and "gen" in req.extra:
            extra_gen = _normalize_gen(req.extra.get("gen"))

        detected = _safe_detect_gen(q)
        gen = extra_gen or _normalize_gen(detected)

        # 3) Pas de GEN : sondage sans filtre pour voir si ask_gen=True
        if not gen:
            probe_docs = _safe_retrieve(q, gen_filter=None)

            should_ask = any(_is_truthy((d.metadata or {}).get("ask_gen")) for d in (probe_docs or []))
            if should_ask:
                options = sorted(list(_safe_extract_found_gens(probe_docs) & {"gen1", "gen2", "gen3"})) or ["gen1", "gen2"]
                clarify_prompt = _first_followup(probe_docs) or "Hebt u een Gen 1 of een Gen 2 apparaat?"

                return {
                    "answer": clarify_prompt,
                    "clarify": {"param": "gen", "options": options, "tips": GEN_TIPS_NL},
                    "citations": [],
                    "used_chunks": [] if req.debug else None,
                }

            answer, citations = generate_answer(q, probe_docs)
            used = [{"text": d.page_content, "meta": d.metadata} for d in (probe_docs or [])] if req.debug else None
            return {"answer": answer, "citations": citations, "used_chunks": used}

        # 4) GEN connue -> recherche filtrée
        docs = _safe_retrieve(q, gen_filter=gen)
        answer, citations = generate_answer(q, docs)
        used = [{"text": d.page_content, "meta": d.metadata} for d in (docs or [])] if req.debug else None
        return {"answer": answer, "citations": citations, "used_chunks": used}

    except Exception as e:
        # Filet de sécurité pour éviter les 500 silencieux
        return {
            "answer": "Er is een fout opgetreden. Probeer het opnieuw of neem contact op met de support.",
            "error": str(e),
            "citations": [],
            "used_chunks": None,
        }
