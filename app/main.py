# app/main.py
from typing import List, Optional, Dict, Any, Set
import os, json, re, unicodedata, shutil, hashlib
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

app = FastAPI(title="Chatbot Piscines API")

# ---------------------- CORS ----------------------
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

# ---------------------- Models ----------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None

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

GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Als je apparaat nog niet gekoppeld is aan je telefoon, en je drukt op het + teken bij stekkers en meetsensoren, en vervolgens op “toestellen zoeken”, dan krijg je bij een Gen 1 toestel meestal meerdere modules te zien, en bij een Gen 2 toestel maar 1 module.",
    "3) Een Gen 1 apparaat wordt meestal met een USB 5V stekker geleverd. Een Gen 2 apparaat heeft alleen een 220V stekker of een 12V stekker.",
]

# ---------------------- JSONL index ----------------------
JSONL_TARGET = os.path.join(STORE_DIR, "faq_index.jsonl")

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

def _row_id_from_parts(category: str, question: str) -> str:
    base = _normalize((category or "") + "||" + (question or ""))
    return hashlib.md5(base.encode("utf-8")).hexdigest()

def _add_row_ids(items: List[dict]) -> List[dict]:
    out = []
    for d in items:
        if "_row_id" not in d:
            cat = d.get("categorie") or d.get("category") or ""
            q = d.get("vraag") or d.get("Vraag") or d.get("question") or d.get("Question") or ""
            d["_row_id"] = _row_id_from_parts(str(cat), str(q))
        out.append(d)
    return out

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
                data.append(json.loads(ln))
            except Exception:
                continue
    return _add_row_ids(data)

def _row_get(d: dict, *keys: str, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default

def _get_options_map(row: dict) -> Dict[str, dict]:
    return _row_get(row, "opties", "options", default={}) or {}

OPTION_SYNONYMS = {
    "gen1": {"gen1", "gen 1", "g1", "type 1", "gen1 apparaat", "gen 1 apparaat", "ik heb gen 1", "ik heb een gen 1"},
    "gen2": {"gen2", "gen 2", "g2", "type 2", "gen2 apparaat", "gen 2 apparaat", "ik heb gen 2", "ik heb een gen 2"},
    "gen3": {"gen3", "gen 3", "g3", "type 3", "gen3 apparaat", "gen 3 apparaat", "ik heb gen 3", "ik heb een gen 3"},
    "wifipool": {"wifipool", "wifi", "wifi apparaat", "wifi-apparaat", "wi-fi", "wifi device", "wifipool apparaat"},
    "benisol": {"benisol", "zonder wifi", "no wifi", "standalone", "benisol apparaat"},
    "display": {"display", "display apparaat", "display-apparaat", "scherm"},
}

def _choice_key_for_user_input(row: dict, user_choice: str) -> Optional[str]:
    if not user_choice:
        return None
    opts = _get_options_map(row)
    if not opts:
        return None

    user = _normalize(user_choice)

    # 1) exact/inclusion
    for k in opts.keys():
        nk = _normalize(k)
        if user == nk or user in nk or nk in user:
            return k

    # 2) synonymes/canoniques
    for canonical, variants in OPTION_SYNONYMS.items():
        if user in variants or any(v in user for v in variants):
            # si l'un des libellés ressemble au canonique
            for k in opts.keys():
                nk = _normalize(k)
                if canonical in nk or nk in canonical:
                    return k
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
    if not items:
        return None
    uq = _normalize(user_q)

    # exact/inclusion sur la question
    for row in items:
        q = _row_get(row, "vraag", "Vraag", "question", "Question", default="")
        qn = _normalize(q)
        if qn and (uq == qn or uq in qn or qn in uq):
            return row

    # fuzzy
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

# ---------------------- Load FAQ at startup ----------------------
os.makedirs(STORE_DIR, exist_ok=True)
_FAQ: List[dict] = _load_jsonl(JSONL_TARGET)

# ---------------------- Routes ----------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    row = _best_jsonl_row(q, _FAQ)
    return {"q": q, "found": bool(row), "row": row}

@app.post("/ingest")
def ingest(req: IngestRequest):
    global _FAQ  # <- IMPORTANT: tout en haut de la fonction
    path = req.path
    if not os.path.exists(path):
        return {"reloaded": False, "error": "file not found", "faq_rows": len(_FAQ)}
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        shutil.copyfile(path, JSONL_TARGET)
        _FAQ = _load_jsonl(JSONL_TARGET)
        return {"reloaded": True, "faq_rows": len(_FAQ)}
    except Exception as e:
        return {"reloaded": False, "error": str(e), "faq_rows": len(_FAQ)}

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

# ---------------------- Chat ----------------------
def _best_faq_match(user_q: str) -> Optional[dict]:
    return _best_jsonl_row(user_q, _FAQ)

def _row_by_id(row_id: str) -> Optional[dict]:
    if not row_id:
        return None
    for r in _FAQ:
        if r.get("_row_id") == row_id:
            return r
    return None

def _row_by_context_question(qtext: str) -> Optional[dict]:
    if not qtext:
        return None
    return _best_jsonl_row(qtext, _FAQ, min_score=0.75)

@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) Corrections admin
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 1.b) Si on nous envoie une réponse à un follow-up (clarify_ref)
    clarify_ref = None
    if isinstance(req.extra, dict):
        clarify_ref = req.extra.get("clarify_ref")

    if clarify_ref:
        row = _row_by_id(str(clarify_ref))
        if row:
            options_map = _get_options_map(row)
            if options_map:
                chosen_key = _choice_key_for_user_input(row, q) or _choice_key_for_user_input(row, _parse_extra_choice(req.extra) or "")
                if chosen_key and chosen_key in options_map:
                    answer_text = _answer_from_option_block(options_map[chosen_key])
                    citations = [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                                  "source": "jsonl", "page": None}]
                    return {"answer": answer_text, "citations": citations}

                # pas trouvé -> renvoie la même demande avec options
                followup_q = _row_get(row, "follow_up_question", "followup_question", default="Maak eerst een keuze:")
                return {
                    "answer": followup_q,
                    "clarify": {
                        "param": "choice",
                        "options": list(options_map.keys()),
                        "tips": GEN_TIPS_NL,
                        "clarify_ref": row.get("_row_id"),
                    },
                    "citations": [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                                   "source": "jsonl", "page": None}],
                }

    # 2) Lookup JSONL classique
    row = _best_faq_match(q)
    if not row and isinstance(req.extra, dict):
        # Si l'utilisateur a juste tapé "Gen 1", on essaie d'utiliser une question de contexte
        ctx_q = req.extra.get("context_question")
        row = _row_by_context_question(str(ctx_q)) if ctx_q else None

    if row:
        options_map = _get_options_map(row)
        follow_up = bool(_row_get(row, "follow_up", "followup", default=False))

        # Si follow_up, tenter un choix déjà fourni dans extra (ex: boutons)
        if follow_up and options_map:
            choice_text = _parse_extra_choice(req.extra) if isinstance(req.extra, dict) else None
            if choice_text:
                chosen_key = _choice_key_for_user_input(row, choice_text)
                if chosen_key and chosen_key in options_map:
                    answer_text = _answer_from_option_block(options_map[chosen_key])
                    citations = [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                                  "source": "jsonl", "page": None}]
                    return {"answer": answer_text, "citations": citations}

            # poser la clarification avec un identifiant stable
            followup_q = _row_get(row, "follow_up_question", "followup_question", default="Maak eerst een keuze:")
            opts_labels = list(options_map.keys())
            return {
                "answer": followup_q,
                "clarify": {
                    "param": "choice",
                    "options": opts_labels,
                    "tips": GEN_TIPS_NL,
                    "clarify_ref": row.get("_row_id"),
                },
                "citations": [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                               "source": "jsonl", "page": None}],
            }

        # réponse directe
        direct_answer = _row_get(row, "antwoord", "answer", "Antwoord", default=None)
        if isinstance(direct_answer, str) and direct_answer.strip():
            citations = [{"title": _row_get(row, "vraag", "Vraag", "question", "Question", default="FAQ"),
                          "source": "jsonl", "page": None}]
            return {"answer": direct_answer, "citations": citations}

    # 3) Fallback RAG
    extra_gen = _parse_extra_gen(req.extra if isinstance(req.extra, dict) else None)
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
