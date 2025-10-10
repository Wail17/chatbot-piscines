# app/training.py
import os, json, unicodedata, re
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

_WS = re.compile(r"\s+")
def _normalize(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("\u00a0", " ").lower()
    t = _WS.sub(" ", t).strip()
    return t

def _ensure_dirs():
    if CHROMA_DIR:
        os.makedirs(CHROMA_DIR, exist_ok=True)
    fb_dir = os.path.dirname(FEEDBACK_FILE or "")
    if fb_dir:
        os.makedirs(fb_dir, exist_ok=True)

def _corr_vs() -> Chroma:
    """Retourne la collection Chroma dédiée aux corrections admin."""
    _ensure_dirs()
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=CORRECTIONS_COLLECTION,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

def add_correction(question: str, answer: str, tags: Optional[List[str]] = None) -> dict:
    """Enregistre une correction (Q→A) prioritaire."""
    vs = _corr_vs()
    q_norm = _normalize(question)
    vs.add_texts(
        [q_norm],
        metadatas=[{"type": "correction", "question": question, "question_norm": q_norm, "answer": answer, "tags": tags or []}],
    )
    vs.persist()
    return {"status": "ok", "added": 1}

def search_correction(query: str, k: int = 1, threshold: float = 0.20):
    """
    Cherche une correction proche (score = distance cosine, plus petit = plus proche).
    NB: on normalise l'entrée pour matcher l'ajout.
    """
    vs = _corr_vs()
    pairs = vs.similarity_search_with_score(_normalize(query), k=k)
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
    _ensure_dirs()
    payload = dict(payload)
    payload["ts"] = datetime.utcnow().isoformat() + "Z"
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"saved": True}
