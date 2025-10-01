# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os
import json
import re
import unicodedata
import shutil
from difflib import SequenceMatcher

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import (
    retrieve,
    generate_answer,
    detect_gen,
    extract_found_gens,
)
from .training import add_correction, search_correction, save_feedback
from .config import CORRECTION_THRESHOLD, STORE_DIR

# ---------------------------------------------------------------------
# App & CORS
# ---------------------------------------------------------------------
app = FastAPI(title="Chatbot Piscines API")

ALLOWED_ORIGINS = [
    "https://beniferro.eu",
    "https://www.beniferro.eu",
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
# Pydantic models
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None

class IngestRequest(BaseModel):
    path: str              # chemin du fichier .jsonl côté disque
    source_type: str = "faq"

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

# ---------------------------------------------------------------------
# Constantes / tips
# ---------------------------------------------------------------------
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

JSONL_TARGET = os.path.join(STORE_DIR, "faq_index.jsonl")

# ---------------------------------------------------------------------
# Utils normalisation & matching
# ---------------------------------------------------------------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*")

def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("\u00a0", " ")
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _load_jsonl(path: str) -> List[dict]:
    data: List[dict] = []
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                data.append(obj)
            except Exception:
                # on ignore les lignes invalides
                continue
    return data

def _row_get(d: dict, *keys: str, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default

def _get_options_map(row: dict) -> Dict[str, dict]:
    # support both 'opties' and 'options'
    opts = _row_get(row, "opties", "options", default={})
    if not isinstance(opts, dict):
        return {}
    return opts

# quelques synonymes utiles pour reconnaître les choix
OPTION_SYNONYMS = {
    "gen1": {"gen1", "gen 1", "type 1", "wifi gen1", "gen1 apparaat"},
    "gen2": {"gen2", "gen 2", "type 2", "wifi gen2", "gen2 apparaat"},
    "gen3": {"gen3", "gen 3", "type 3", "wifi gen3", "gen3 apparaat"},
    "wifipool": {"wifipool", "wifi", "wifi apparaat", "wifi-apparaat", "wi-fi", "wifi device"},
    "benisol": {"benisol", "zonder wifi", "no wifi", "standalone"},
    "display": {"display", "display apparaat", "display-apparaat", "scherm"},
}

def _normalize_choice(s: str) -> str:
    return _normalize(s)

def _choice_key_for_user_input(row: dict, user_choice: str) -> Optional[str]:
    """
    Essaie de faire correspondre une réponse utilisateur ('gen1', 'wifipool', 'benisol', etc.)
    à l'une des clés d'options dans le JSONL.
    """
    if not user_choice:
        return None

    opts = _get_options_map(row)
    if not opts:
        return None

    user = _normalize_choice(user_choice)

    # 1) exact / inclusion sur labels d'options
    for k in opts.keys():
        nk = _normalize(k)
        if user == nk or user in nk or nk in user:
            return k

    # 2) mapping via synonymes
    # on regarde chaque clé d'option et on essaie de l'associer à une catégorie
    for canonical, variants in OPTION_SYNONYMS.items():
        if user in variants:
            # trouve la 1ère option dont le label matche 'canonical'
            for k in opts.keys():
                nk = _normalize(k)
                if canonical in nk:
                    return k
            # si pas trouvé via label, mais l'option existe en label exact canonical
            if canonical in opts:
                return canonical

    # 3) fuzzy
    best = (0.0, None)
    for k in opts.keys():
        nk = _normalize(k)
        sc = _ratio(user, nk)
        if sc > best[0]:
            best = (sc, k)
    return best[1] if best[0] >= 0.70 else None

def _best_jsonl_row(user_q: str, items: List[dict], min_score: float = 0.80) -> Optional[dict]:
    """Recherche robuste sur la clé 'vraag' (ou variantes)."""
    if not items:
        return None
    uq = _normalize(user_q)

    # 1) exact / inclusion
    for row in items:
        q = _row_get(row, "vraag", "Vraag", "question", "Question", default="")
        qn = _normalize(q)
        if qn and (uq == qn or uq in qn or qn in uq):
            return row

    # 2) fuzzy
    best = (0.0, None)
    for row in items:
        q = _row_get(row, "vraag", "Vraag", "question", "Question", default="")
        qn = _normalize(q)
        if not qn:
            continue
        sc = _ratio(uq, qn)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= min_score else None

def _parse_extra_choice(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict):
        return None
    for key in ("choice", "option", "optie", "keuze", "gen"):
        v = extra.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    """Toujours supporté pour les cas historiques."""
    if not isinstance(extra, dict):
        return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1", "gen 1"}: return "gen1"
    if g in {"gen2", "gen 2"}: return "gen2"
    if g in {"gen3", "gen 3"}: return "gen3"
    return None

def _answer_from_option_block(block: dict) -> str:
    ans = _row_get(block, "antwoord", "answer", "Antwoord", default="")
    add = _row_get(block, "aanbeveling", "recommandation", "recommendation", default="")
    if add:
        return f"{ans}\n\n{add}"
    return ans

# ---------------------------------------------------------------------
# Chargement initial du JSONL
# ---------------------------------------------------------------------
os.makedirs(STORE_DIR, exist_ok=True)
_FAQ: List[dict] = _load_jsonl(JSONL_TARGET)

# ---------------------------------------------------------------------
# Endpoints utilitaires
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    row = _best_jsonl_row(q, _FAQ)
    return {"q": q, "found": bool(row), "row": row}

# ---------------------------------------------------------------------
# Ingest JSONL (copie + reload)
# ---------------------------------------------------------------------
@app.post("/ingest")
def ingest(req: IngestRequest):
    global _FAQ  # IMPORTANT: avant toute lecture/écriture
    path = req.path

    if not os.path.exists(path):
        return {"reloaded": False, "error": "file not found", "faq_rows": len(_FAQ)}

    try:
        shutil.copyfile(path, JSONL_TARGET)
        _FAQ = _load_jsonl(JSONL_TARGET)
        return {"reloaded": True, "faq_rows": len(_FAQ)}
    except Exception as e:
        return {"reloaded": False, "error": str(e), "faq_rows": len(_FAQ)}

# ---------------------------------------------------------------------
# Training / Feedback
# ---------------------------------------------------------------------
@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 0) Corrections admin prioritaires
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 1) Lookup JSONL
    row = _best_jsonl_row(q, _FAQ)
    if row:
        follow_up = bool(_row_get(row, "follow_up", "followup", default=False))
        # options dict (opties / options)
        options_map = _get_options_map(row)

        if follow_up and options_map:
            # l'utilisateur a-t-il déjà donné son choix ?
            choice_text = _parse_extra_choice(req.extra) or _parse_extra_gen(req.extra)
            if choice_text:
                chosen = _choice_key_for_user_input(row, choice_text)
                if chosen and chosen in options_map:
                    answer_text = _answer_from_option_block(options_map[chosen])
                    citations = [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                                  "source": "jsonl", "page": None}]
                    return {"answer": answer_text, "citations": citations}

            # Sinon : poser la clarification
            followup_q = _row_get(row, "follow_up_question", "followup_question", default="Maak eerst een keuze:")
            opts_labels = list(options_map.keys())
            return {
                "answer": followup_q,
                "clarify": {"param": "choice", "options": opts_labels, "tips": GEN_TIPS_NL},
                "citations": [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                               "source": "jsonl", "page": None}],
            }

        # Pas de follow-up : réponse directe
        direct_answer = _row_get(row, "antwoord", "answer", "Antwoord", default=None)
        if isinstance(direct_answer, str) and direct_answer.strip():
            citations = [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                          "source": "jsonl", "page": None}]
            return {"answer": direct_answer, "citations": citations}

        # Si pas d'answer direct et pas d'options => vide
        # On tente le fallback RAG
        # Note: ça ne devrait pas arriver si le JSONL est bien formé
        # mais on garde la robustesse.
        # (continue vers fallback)

    # 2) Fallback RAG si aucune ligne JSONL trouvée
    extra_gen = _parse_extra_gen(req.extra)
    try:
        gen = extra_gen or detect_gen(q)
    except Exception:
        gen = extra_gen

    try:
        docs = retrieve(q, gen_filter=gen)
    except TypeError:
        docs = retrieve(q)

    try:
        found: Set[str] = extract_found_gens(docs)
    except Exception:
        found = set()

    if not gen and found:
        options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen2"]
        return {
            "answer": "Hebt u een Gen 1 of een Gen 2 apparaat?",
            "clarify": {"param": "gen", "options": options, "tips": GEN_TIPS_NL},
            "citations": [],
        }

    if not docs:
        return {
            "answer": "Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?",
            "citations": [],
        }

    answer, citations = generate_answer(q, docs)
    return {"answer": answer, "citations": citations}
