# app/training.py
import os, json
from datetime import datetime
from typing import List, Optional, Tuple

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    FEEDBACK_FILE,
    CORRECTIONS_COLLECTION,
)

def _corr_vs() -> Chroma:
    """Retourne la collection Chroma dédiée aux corrections admin."""
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=CORRECTIONS_COLLECTION,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

def add_correction(question: str, answer: str, tags: Optional[List[str]] = None) -> dict:
    """Enregistre une correction (Q→A) prioritaire."""
    vs = _corr_vs()
    vs.add_texts([question], metadatas=[{"type": "correction", "answer": answer, "tags": tags or []}])
    vs.persist()
    return {"status": "ok", "added": 1}

def search_correction(query: str, k: int = 1, threshold: float = 0.20):
    """Cherche une correction proche (score/ distance Chroma : plus petit = plus proche)."""
    vs = _corr_vs()
    pairs = vs.similarity_search_with_score(query, k=k)
    if not pairs:
        return None, None, None
    doc, score = pairs[0]
    if score <= threshold:
        cite = {"title": "Correction admin", "source": "corrections"}
        ans = (doc.metadata or {}).get("answer", "")
        return ans, cite, score
    return None, None, score

def save_feedback(payload: dict) -> dict:
    """Sauvegarde un feedback JSONL (👍/👎, correction proposée, etc.)."""
    os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
    payload = dict(payload)
    payload["ts"] = datetime.utcnow().isoformat() + "Z"
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"saved": True}
