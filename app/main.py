# app/main.py
import os
from typing import List, Optional, Dict, Any, Set

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,          # détecte gen1/gen2/gen3 dans la question
    extract_found_gens,  # retourne les générations vues dans les métadatas des docs
)
from .ingest import ingest_path          # gère dossiers, fichiers ET Excel (.xlsx/.xls)
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")

# --- CORS ---
# Tu peux aussi surcharger via l'env: CORS_ORIGINS="https://ton-site1,https://ton-site2"
DEFAULT_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    "https://15d118fa1e6e.ngrok-free.app",
]
ENV_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = ENV_ORIGINS if ENV_ORIGINS else DEFAULT_ORIGINS

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

# --- Payload models ---
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    # Permet au front de renvoyer un choix (ex: {"gen": "gen1"}) après une clarification.
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

# --- Conseils NL pour reconnaître Gen 1 vs Gen 2 ---
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker."
]

# --- Health ---
@app.get("/health")
def health():
    return {"status": "ok"}

# --- Ingestion (dossier, fichier texte, ou Excel) ---
@app.post("/ingest")
def ingest(req: IngestRequest):
    # utilise ingest_path : supporte les dossiers ET .xlsx/.xls
    return ingest_path(req.path, req.source_type)

# --- Training / feedback ---
@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

# --- Chat avec logique Gen ---
@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) Correction admin prioritaire
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Déterminer la génération (si fournie par le front ou mentionnée dans la question)
    extra_gen = None
    if isinstance(req.extra, dict):
        extra_gen = req.extra.get("gen")
    gen = extra_gen or detect_gen(q)  # "gen1" / "gen2" / "gen3" ou None

    # 3) Récupération avec filtre éventuel sur la génération
    #    (la fonction retrieve sait filtrer selon metadata['gens'] si gen_filter est fourni)
    docs = retrieve(q, gen_filter=gen)

    # 4) Si les docs trouvés indiquent des générations possibles ET que l'utilisateur
    #    ne les a pas précisées, on lui demande de choisir (clarification)
    found: Set[str] = extract_found_gens(docs)  # ex: {"gen1", "gen2"}
    if not gen and found:
        options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen2"]
        return {
            "answer": "Hebt u een Gen 1 of een Gen 2 apparaat? (Kies hieronder om verder te gaan.)",
            "clarify": {
                "param": "gen",
                "options": options,
                "tips": GEN_TIPS_NL
            },
            "citations": [],
            "used_chunks": [] if req.debug else None
        }

    # 5) Réponse normale basée sur les extraits (Excel + autres)
    answer, citations = generate_answer(q, docs)
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
