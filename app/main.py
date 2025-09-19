# app/main.py
from typing import List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import CORRECTION_THRESHOLD
from .ingest import ingest_folder
from .rag import retrieve, generate_answer
from .training import add_correction, search_correction, save_feedback

app = FastAPI(title="Chatbot Piscines API")

ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
    "https://web-production-e8b3b.up.railway.app",  # Railway public URL
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

# --------- Models ----------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False

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

# --------- Routes ----------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest):
    """(Re)build index from a folder of docs."""
    return ingest_folder(req.path, req.source_type)

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

@app.post("/chat")
def chat(req: ChatRequest):
    # 1) Try admin correction first
    ans, cite, score = search_correction(req.query, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Else RAG pipeline
    docs = retrieve(req.query)
    answer, citations = generate_answer(req.query, docs)
    used = [{"text": d.page_content, "meta": d.metadata}] if req.debug else None
    if req.debug:
        used = [{"text": d.page_content, "meta": d.metadata} for d in docs]
    return {"answer": answer, "citations": citations, "used_chunks": used}
