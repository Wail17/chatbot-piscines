from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

from .rag import retrieve, generate_answer
from .ingest import ingest_folder
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD

app = FastAPI(title="Chatbot Piscines API")



ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu", 
    "https://15d118fa1e6e.ngrok-free.app"  # au cas où WP force le www
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,  # OK car on n'utilise pas "*"
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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest):
    return ingest_folder(req.path, req.source_type)

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

@app.post("/chat")
def chat(req: ChatRequest):
    # 1) CHERCHE d'abord une correction admin très proche
    ans, cite, score = search_correction(req.query, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        return {"answer": ans, "citations": [cite], "used_chunks": [] if not req.debug else [{"meta":{"source_type":"correction","score":score},"text":""}]}

    # 2) Sinon, pipeline RAG classique
    docs = retrieve(req.query)
    answer, citations = generate_answer(req.query, docs)
    used = [{"text": d.page_content, "meta": d.metadata} for d in docs] if req.debug else None
    return {"answer": answer, "citations": citations, "used_chunks": used}
