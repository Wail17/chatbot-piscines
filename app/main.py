<<<<<<< HEAD
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

from .rag import retrieve, generate_answer
from .ingest import ingest_folder
=======
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
    extract_found_gens,  # retourne les générations vues dans les métadonnées des docs
)
from .ingest import ingest_path          # gère dossiers, fichiers ET Excel (.xlsx/.xls)
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")

<<<<<<< HEAD


ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu", 
    "https://15d118fa1e6e.ngrok-free.app"  # au cas où WP force le www
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,  # OK car on n'utilise pas "*"
=======
# --- CORS ---
ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    "https://15d118fa1e6e.ngrok-free.app",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
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

<<<<<<< HEAD

=======
# --- Payload models ---
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
<<<<<<< HEAD
=======
    extra: Optional[Dict[str, Any]] = None  # ex: {"gen": "gen1"}
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))

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

<<<<<<< HEAD
=======
# --- Tips NL pour reconnaître Gen 1 vs Gen 2 ---
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker."
]

# --- Health ---
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
@app.get("/health")
def health():
    return {"status": "ok"}

<<<<<<< HEAD
@app.post("/ingest")
def ingest(req: IngestRequest):
    return ingest_folder(req.path, req.source_type)

=======
# --- Ingestion (dossier, fichier texte, ou Excel) ---
@app.post("/ingest")
def ingest(req: IngestRequest):
    # utilise ingest_path : supporte les dossiers ET .xlsx/.xls
    return ingest_path(req.path, req.source_type)

# --- Training / feedback (inchangé) ---
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

<<<<<<< HEAD
@app.post("/chat")
def chat(req: ChatRequest):
    # 1) CHERCHE d'abord une correction admin très proche
    ans, cite, score = search_correction(req.query, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        return {"answer": ans, "citations": [cite], "used_chunks": [] if not req.debug else [{"meta":{"source_type":"correction","score":score},"text":""}]}

    # 2) Sinon, pipeline RAG classique
    docs = retrieve(req.query)
    answer, citations = generate_answer(req.query, docs)
=======
# --- Chat avec logique Gen ---
@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) Correction admin prioritaire (inchangé)
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Déterminer la génération (si fournie par le front ou mentionnée dans la question)
    extra_gen = (req.extra or {}).get("gen") if isinstance(req.extra, dict) else None
    gen = extra_gen or detect_gen(q)  # "gen1" / "gen2" / "gen3" ou None

    # 3) Récupération avec filtre éventuel sur la génération
    docs = retrieve(q, gen_filter=gen)

    # 4) Si l’Excel marque cette FAQ pour des générations (metadata['gens']) et que l’utilisateur
    #    n’a PAS encore précisé -> demander d’abord GEN (clarify), puis le front renverra la même question avec extra.gen
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
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
