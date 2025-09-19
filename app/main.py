# app/main.py
from typing import List, Optional, Dict, Any, Set

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    # Les deux suivants sont optionnels : ne les importe que si tu les as
    # ajoutés dans ton rag.py. Sinon, supprime-les ici et dans /chat plus bas.
    detect_gen,          # -> "gen1" / "gen2" / "gen3" ou None d'après la question
    extract_found_gens,  # -> set({"gen1","gen2"}) trouvé dans les metadata
)
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")

# ---------------------------------------------------------------------
# CORS (mets ici tes domaines de prod)
# ---------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    # ajoute ton domaine Railway si tu appelles directement l'API depuis WP
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
# Modèles
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    # Permet au front d'envoyer la génération choisie (ex: {"gen":"gen1"})
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

# Aide NL pour reconnaître Gen1/Gen2 (affichable côté front si besoin)
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest):
    # Supporte dossiers, fichiers texte ET Excel (.xlsx/.xls)
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

    # 2) Déterminer la génération (si fournie par le front ou mentionnée dans la question)
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
        gen = extra_gen or detect_gen(q)  # si detect_gen n'existe pas, supprime cette ligne et passe gen=None
    except NameError:
        gen = extra_gen

    # 3) Récupération (filtrée par gen si dispo)
    try:
        docs = retrieve(q, gen_filter=gen)  # si ta signature est retrieve(question) uniquement, retire gen_filter
    except TypeError:
        # fallback si retrieve ne prend pas gen_filter
        docs = retrieve(q)

    # 4) Si l’Excel marque des générations et que l’utilisateur n’a pas précisé -> demander d’abord GEN
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

    # 5) Réponse RAG standard
    answer, citations = generate_answer(q, docs)
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
