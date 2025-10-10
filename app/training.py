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

# ---------------- Normalisation ----------------
_WS = re.compile(r"\s+")
def _normalize(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("\u00a0", " ").lower()
    t = _WS.sub(" ", t).strip()
    return t

# ---------------- Dossiers & ENV ----------------
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
    # Si EMBEDDINGS_MODEL est invalide, l’exception sera remontée proprement
    return OpenAIEmbeddings(model=EMBEDDINGS_MODEL)

def _corr_vs() -> Chroma:
    """Collection Chroma persistante pour corrections admin."""
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

# ---------------- CRUD Corrections ----------------
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

def update_correction(corr_id: str, *, question: Optional[str] = None,
                      answer: Optional[str] = None, tags: Optional[List[str]] = None) -> dict:
    vs = _corr_vs()
    try:
        vs.delete(ids=[corr_id])
    except Exception:
        pass
    q_norm = _normalize(question) if question else corr_id
    meta = {
        "type": "correction",
        "id": corr_id,
        "question": question,
        "question_norm": _normalize(question) if question else None,
        "answer": answer,
        "tags": tags or [],
        "ts": datetime.utcnow().isoformat() + "Z",
        "updated": True,
    }
    vs.add_texts([q_norm], metadatas=[meta], ids=[corr_id])
    vs.persist()
    return {"status": "ok", "id": corr_id, "updated": True}

def delete_correction(corr_id: str) -> dict:
    vs = _corr_vs()
    vs.delete(ids=[corr_id])
    vs.persist()
    return {"status": "ok", "deleted": corr_id}

def list_corrections(limit: int = 100) -> List[Dict[str, Any]]:
    vs = _corr_vs()
    pairs = vs.similarity_search_with_score("", k=limit)
    out: List[Dict[str, Any]] = []
    for doc, _ in pairs:
        m = doc.metadata or {}
        out.append({
            "id": m.get("id"),
            "question": m.get("question"),
            "answer": m.get("answer"),
            "tags": m.get("tags", []),
            "ts": m.get("ts"),
        })
    return out

# ---------------- Recherche ----------------
def _best_pair(pairs: List[Tuple[Any, float]]):
    if not pairs:
        return None
    doc, raw = pairs[0]
    if isinstance(raw, (int, float)) and 0.0 <= raw <= 2.0:
        distance = float(raw)
        similarity = max(0.0, 1.0 - distance)
    else:
        similarity = float(raw)
        distance = max(0.0, 1.0 - similarity)
    return doc, distance, similarity

def search_correction(query: str, k: int = 1, distance_threshold: float = 0.20, similarity_threshold: float = 0.80):
    vs = _corr_vs()
    pairs = vs.similarity_search_with_score(_normalize(query), k=k)
    if not pairs:
        return None, None, None
    doc, distance, similarity = _best_pair(pairs)
    meta = doc.metadata or {}
    ok = (distance is not None and distance <= distance_threshold) or (similarity is not None and similarity >= similarity_threshold)
    debug = {"distance": distance, "similarity": similarity}
    if ok:
        cite = {"title": "Correction admin", "source": "corrections", "id": meta.get("id")}
        ans = meta.get("answer", "")
        return ans, cite, debug
    return None, None, debug

# ---------------- Feedback ----------------
def save_feedback(payload: dict) -> dict:
    _ensure_dirs()
    payload = dict(payload)
    payload["ts"] = datetime.utcnow().isoformat() + "Z"
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"saved": True}
