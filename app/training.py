# app/training.py
import os, json, unicodedata, re, uuid, logging
from datetime import datetime
from typing import List, Optional, Tuple, Any, Dict

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    FEEDBACK_FILE,
    CORRECTIONS_COLLECTION,
)

log = logging.getLogger("training")

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

def _validate_env():
    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not EMBEDDINGS_MODEL:
        missing.append("EMBEDDINGS_MODEL")
    if not CHROMA_DIR:
        missing.append("CHROMA_DIR")
    if not CORRECTIONS_COLLECTION:
        missing.append("CORRECTIONS_COLLECTION")
    if missing:
        raise RuntimeError(f"Env misconfigured: missing {', '.join(missing)}")

def _emb() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBEDDINGS_MODEL)

def _corr_vs() -> Chroma:
    _validate_env()
    _ensure_dirs()
    try:
        return Chroma(
            persist_directory=CHROMA_DIR,
            collection_name=CORRECTIONS_COLLECTION,
            embedding_function=_emb(),
        )
    except Exception as e:
        log.exception("Chroma init failed")
        raise RuntimeError(f"Chroma init failed: {e}")

def add_correction(question: str, answer: str, tags: Optional[List[str]] = None) -> dict:
    vs = _corr_vs()
    q_norm = _normalize(question)
    corr_id = str(uuid.uuid4())
    meta = {
        "type": "correction",
        "id": corr_id,
        "question": question,
        "question_norm": q_norm,
        "answer": answer,
        "tags": tags or [],
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    vs.add_texts([q_norm], metadatas=[meta], ids=[corr_id])
    vs.persist()
    return {"status": "ok", "id": corr_id}

def search_correction(query: str, k: int = 1, threshold: float = 0.20):
    vs = _corr_vs()
    pairs = vs.similarity_search_with_score(_normalize(query), k=k)
    if not pairs:
        return None, None, None
    doc, score = pairs[0]  # score = distance cosine (plus petit = mieux)
    if score <= threshold:
        cite = {"title": "Correction admin", "source": "corrections", "id": (doc.metadata or {}).get("id")}
        ans = (doc.metadata or {}).get("answer", "")
        return ans, cite, {"distance": score}
    return None, None, {"distance": score}

def save_feedback(payload: dict) -> dict:
    _ensure_dirs()
    payload = dict(payload)
    payload["ts"] = datetime.utcnow().isoformat() + "Z"
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"saved": True}
