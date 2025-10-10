# app/training.py
import os, json, unicodedata, re, uuid, logging
from datetime import datetime
from typing import List, Optional, Tuple, Any, Dict

# Fallback SQLite (certains containers n'ont pas sqlite3 natif correct)
try:
    import sqlite3  # noqa
except Exception:  # pragma: no cover - fallback path
    import pysqlite3 as sqlite3  # noqa

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain.schema import Document

from rapidfuzz import fuzz

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    FEEDBACK_FILE,
    CORRECTIONS_COLLECTION,
)

log = logging.getLogger("training")


class _JsonCorrectionStore:
    """Fallback persistence when chromadb is unavailable."""

    def __init__(self) -> None:
        base_dir = CHROMA_DIR or "."
        os.makedirs(base_dir, exist_ok=True)
        self.path = os.path.join(base_dir, "corrections_fallback.jsonl")

    def add_texts(self, texts: List[str], metadatas: List[Dict[str, Any]], ids: List[str]):
        records = []
        for text, meta, corr_id in zip(texts, metadatas, ids):
            meta = dict(meta or {})
            meta.setdefault("id", corr_id)
            meta.setdefault("question_norm", text)
            meta.setdefault("ts", datetime.utcnow().isoformat() + "Z")
            records.append(meta)
        with open(self.path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def persist(self):  # pragma: no cover - noop compatibility hook
        return None

    def _entries(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        rows: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows

    def similarity_search_with_score(self, query: str, k: int = 1):
        query_norm = _normalize(query)
        results: List[Tuple[Document, float]] = []
        for row in self._entries():
            qn = row.get("question_norm") or _normalize(row.get("question") or "")
            if not qn:
                continue
            similarity = fuzz.ratio(query_norm, qn) / 100.0
            distance = 1.0 - similarity
            doc = Document(page_content=qn, metadata=row)
            results.append((doc, distance))
        results.sort(key=lambda item: item[1])
        return results[:k]


_fallback_store: Optional[_JsonCorrectionStore] = None
_fallback_reason: Optional[str] = None

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

def _validate_env(require_openai: bool = True):
    missing = []
    if require_openai and not os.getenv("OPENAI_API_KEY"):
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

def _corr_vs():
    global _fallback_store, _fallback_reason
    force_fallback = os.getenv("TRAINING_FORCE_FALLBACK") == "1"
    if force_fallback:
        if not _fallback_store:
            _fallback_store = _JsonCorrectionStore()
            _fallback_reason = "TRAINING_FORCE_FALLBACK=1"
        return _fallback_store

    _validate_env(require_openai=True)
    _ensure_dirs()
    try:
        return Chroma(
            persist_directory=CHROMA_DIR,
            collection_name=CORRECTIONS_COLLECTION,
            embedding_function=_emb(),
        )
    except Exception as e:
        log.exception("Chroma init failed")
        if not _fallback_store:
            _fallback_store = _JsonCorrectionStore()
        _fallback_reason = str(e)
        return _fallback_store

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


def vectorstore_status() -> Dict[str, Any]:
    backend = "chroma"
    reason = None
    forced = os.getenv("TRAINING_FORCE_FALLBACK") == "1"
    if _fallback_store is not None or forced:
        backend = "jsonl-fallback"
        reason = _fallback_reason or ("TRAINING_FORCE_FALLBACK=1" if forced else None)
    return {
        "backend": backend,
        "fallback_reason": reason,
        "persist_path": getattr(_fallback_store, "path", CHROMA_DIR),
        "forced": forced,
    }
