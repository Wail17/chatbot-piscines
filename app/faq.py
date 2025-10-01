# app/faq.py
import json
import os
import re
from typing import Dict, Any, Optional, Tuple, List
try:
    from rapidfuzz import fuzz, process  # plus rapide/robuste
    HAVE_RAPID = True
except Exception:
    HAVE_RAPID = False

from .config import STORE_DIR

FAQ_INDEX_PATH = os.path.join(STORE_DIR, "faq_index.json")

_FAQ: Dict[str, Dict[str, Any]] = {}    # question_norm -> payload
_ALL_QUESTIONS: List[str] = []          # pour fuzzy

_NORM_RE = re.compile(r"\s+")

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = _NORM_RE.sub(" ", s)
    return s

def load_faq_index() -> int:
    """Charge l'index JSON en mémoire. Retourne le nombre d’entrées."""
    global _FAQ, _ALL_QUESTIONS
    _FAQ, _ALL_QUESTIONS = {}, []
    if not os.path.exists(FAQ_INDEX_PATH):
        return 0
    with open(FAQ_INDEX_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for item in data:
        q = _norm(item.get("question") or "")
        if not q:
            continue
        _FAQ[q] = item
        _ALL_QUESTIONS.append(q)
    return len(_FAQ)

def faq_lookup(question: str, threshold: int = 86) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """
    Cherche la question dans l'index: exact puis fuzzy.
    Retourne (payload, matched_question, score)
    payload contient: question, answer, category, gens, ask_gen, video_url, photo, source
    """
    qn = _norm(question)
    if not qn or not _FAQ:
        return None, None, 0

    # exact
    if qn in _FAQ:
        return _FAQ[qn], qn, 100

    # fuzzy
    if HAVE_RAPID and _ALL_QUESTIONS:
        best = process.extractOne(qn, _ALL_QUESTIONS, scorer=fuzz.WRatio)
        if best and best[1] >= threshold:
            mq = best[0]
            return _FAQ.get(mq), mq, int(best[1])

    return None, None, 0
