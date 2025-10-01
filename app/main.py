# app/main.py
from typing import List, Optional, Dict, Any, Set
import os
import traceback
import unicodedata

import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    # Optionnels : s'ils n'existent pas dans rag.py, commente ces imports et l'usage plus bas
    detect_gen,          # -> "gen1"/"gen2"/"gen3" ou None
    extract_found_gens,  # -> set({"gen1","gen2"}) détecté dans les metadata
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")

# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    # ajoute ton domaine Railway si tu appelles l’API direct depuis WP :
    # "https://web-production-e8b3b.up.railway.app",
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
# Modèles
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None   # ex: {"gen":"gen1"}

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

# Aide NL pour reconnaître Gen1/Gen2 (pour le front si besoin)
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ---------------------------------------------------------------------
# Utils (pour /debug/peek_excel)
# ---------------------------------------------------------------------
def _norm_col(s: Any) -> str:
    """Normalise une étiquette de colonne pour le debug (minuscules, sans accents, alnum)."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.lower() if ch.isalnum())

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---- INGEST avec try/except pour renvoyer l’erreur au client
@app.post("/ingest")
def ingest(req: IngestRequest):
    try:
        result = ingest_path(req.path, req.source_type)
        return result
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(
            status_code=400,
            content={"error": str(e), "trace": tb}
        )

# ---- Petit endpoint de debug pour vérifier l’Excel
@app.get("/debug/peek_excel")
def peek_excel(path: str):
    if not os.path.exists(path):
        return {"exists": False, "path": path}
    try:
        df = pd.read_excel(path, engine="openpyxl")
        cols = list(df.columns)
        return {
            "exists": True,
            "path": path,
            "n_rows": int(len(df)),
            "columns": cols,
            "normalized": [_norm_col(c) for c in cols],
            "head2": df.head(2).fillna("").to_dict(orient="records"),
        }
    except Exception as e:
        tb = traceback.format_exc()
        return {"exists": True, "path": path, "error": str(e), "trace": tb}

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

    # 2) Déterminer la génération (si fournie par le front ou mentionnée)
    extra_gen = None
    if isinstance(req.extra, dict):
        extra_gen = req.extra.get("gen")
        if isinstance(extra_gen, str):
            g = extra_gen.strip().lower()
            if g in {"gen1", "gen 1"}:
                extra_gen = "gen1"
            elif g in {"gen2", "gen 2"}:
                extra_gen = "gen2"
            elif g in {"gen3", "gen 3"}:
                extra_gen = "gen3"
            else:
                extra_gen = None
    try:
        gen = extra_gen or detect_gen(q)  # si detect_gen n'existe pas, commente cette ligne
    except NameError:
        gen = extra_gen

    # 3) Récupération (filtrée par gen si possible)
    try:
        docs = retrieve(q, gen_filter=gen)  # si retrieve() n'a pas gen_filter, on fallback dessous
    except TypeError:
        docs = retrieve(q)

    # 4) Si l’Excel marque des générations et que l’utilisateur n’a pas précisé -> demander GEN
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
            "used_chunks": [] if req.debug else None,
        }

    # 5) Réponse RAG standard
    answer, citations = generate_answer(q, docs)
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
