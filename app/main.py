# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os, json, re, unicodedata
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
from .ingest import ingest_path
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
    extra: Optional[Dict[str, Any]] = None  # pour 'gen' ou 'choice'

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

# ---------------------- FAQ index (JSON) ----------------------
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
def _load_faq() -> List[dict]:
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

_FAQ: List[dict] = _load_faq()

# ---------------------- lookup helpers ----------------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*")

def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s = _PUNCT_RE.sub("", s)
    return s.strip()

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _best_faq_match(user_q: str, min_score: float = 0.80) -> dict | None:
    """1) exact/inclusion sur 'question'
       2) fuzzy sur 'question'
       3) inclusion/fuzzy sur 'answer' (si réponse directe)"""
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # 1) exact / inclusion sur question
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if q and (uq == q or uq in q or q in uq):
            return row

    # 2) fuzzy sur question
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        sc = _ratio(uq, q)
        if sc > best[0]:
            best = (sc, row)
    if best[0] >= min_score:
        return best[1]

    # 3) inclusion/fuzzy sur answer (si direct)
    best = (0.0, None)
    for row in _FAQ:
        a = _normalize(row.get("answer", ""))
        if not a:
            continue
        if uq and (uq in a or a in uq):
            return row
        sc = _ratio(uq, a)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= (min_score - 0.08) else None

# --- petite recherche mots-clés (facultatif) ---
KW_MAP = {
    "tlf": ["tlf"],
    "thermometer": ["thermometer", "thermometers", "vloeistof thermometer", "vloeistofthermometer"],
    "temperatuur": ["temperatuur", "temperatuursmeting", "temperatuurmeting", "temperatuursmetingen", "sensors", "sensor"],
    "aansluiten": ["aansluiten", "aangesloten", "ondersteunen", "kan", "heeft"],
    "twee": ["twee", "2", "tweede"],
}
def _expand_keywords(user_q: str) -> List[str]:
    uq = _normalize(user_q)
    toks = set(uq.split())
    out: set[str] = set()
    for base, variants in KW_MAP.items():
        if base in toks or any(v in uq for v in variants):
            out.update(variants + [base])
    if "tlf" in out:
        out.update(KW_MAP["temperatuur"])
    return list(out)

def _faq_keyword_search(user_q: str) -> dict | None:
    if not _FAQ:
        return None
    kws = _expand_keywords(user_q)
    if not kws:
        return None
    best = (0, None)
    for row in _FAQ:
        blob = _normalize((row.get("question") or "") + " " + (row.get("answer") or ""))
        hits = sum(1 for k in kws if k in blob)
        if hits > best[0]:
            best = (hits, row)
    return best[1] if best[0] >= 2 else None

# ---------------------- helpers extra/clarify ----------------------
def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict):
        return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1", "gen 1"}: return "gen1"
    if g in {"gen2", "gen 2"}: return "gen2"
    if g in {"gen3", "gen 3"}: return "gen3"
    return None

def _parse_extra_choice(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict):
        return None
    # tolérer plusieurs clés pour la sélection
    for k in ("choice", "optie", "option", "selectie"):
        if k in extra and str(extra[k]).strip():
            return str(extra[k]).strip()
    return None

def _match_option_label(user_val: str, options: Dict[str, dict]) -> Tuple[str, dict] | None:
    """retourne (label, payload) si on matche la sélection utilisateur sur une des clés d'options"""
    if not user_val or not options:
        return None
    nv = _normalize(user_val)
    best = (0.0, None)
    for label, payload in options.items():
        nl = _normalize(str(label))
        if nv == nl or nv in nl or nl in nv:
            return (label, payload)
        sc = _ratio(nv, nl)
        if sc > best[0]:
            best = (sc, (label, payload))
    # seuil souple
    return best[1] if best[0] >= 0.78 else None

# ---------------------- Routes ----------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    r1 = _best_faq_match(q)
    r2 = None if r1 else _faq_keyword_search(q)
    return {"q": q, "best_match": bool(r1 or r2), "via": "best" if r1 else ("keywords" if r2 else None), "row": (r1 or r2)}

@app.post("/ingest")
def ingest(req: IngestRequest):
    res = ingest_path(req.path, req.source_type)
    # recharger l'index si régénéré
    global _FAQ
    _FAQ[:] = _load_faq()
    return res

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

# ---------------------- Chat ----------------------
@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) corrections admin (prioritaire)
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) lookup FAQ (robuste)
    row = _best_faq_match(q) or _faq_keyword_search(q)
    if row:
        citations = [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}]

        follow_up = bool(row.get("follow_up"))
        options: Dict[str, dict] = row.get("options") or {}

        # --- cas "ask_gen" (compat) ---
        ask_gen_flag = bool(row.get("ask_gen"))
        if ask_gen_flag:
            gen_from_req = _parse_extra_gen(req.extra) or (detect_gen(q) if callable(detect_gen) else None)

            if gen_from_req == "gen1" and row.get("answer_gen1"):
                return {"answer": row["answer_gen1"], "citations": citations}
            if gen_from_req == "gen2" and row.get("answer_gen2"):
                return {"answer": row["answer_gen2"], "citations": citations}
            if gen_from_req == "gen3" and row.get("answer_gen3"):
                return {"answer": row["answer_gen3"], "citations": citations}

            # pas de GEN encore fournie -> clarification GEN
            return {
                "answer": row.get("followup_q") or "Hebt u een Gen 1 of een Gen 2 apparaat?",
                "clarify": {"param": "gen", "options": ["gen1", "gen2", "gen3"], "tips": GEN_TIPS_NL},
                "citations": citations,
            }

        # --- cas "options" génériques ---
        if follow_up and options:
            choice = _parse_extra_choice(req.extra)
            match = _match_option_label(choice, options) if choice else None
            if match:
                label, payload = match
                main = str(payload.get("answer") or payload.get("antwoord") or "").strip()
                rec = str(payload.get("recommendation") or payload.get("aanbeveling") or "").strip()
                answer_text = main + (("\n\n" + rec) if rec else "")
                return {"answer": answer_text, "citations": citations}

            # pas de choix encore -> proposer les options
            opt_labels = list(options.keys())
            return {
                "answer": row.get("followup_q") or "Kies een optie:",
                "clarify": {"param": "choice", "options": opt_labels},
                "citations": citations,
            }

        # --- réponse directe ---
        direct = row.get("answer")
        if isinstance(direct, str) and direct.strip():
            return {"answer": direct, "citations": citations}

        # garde-fou: si rien d'utilisable
        return {
            "answer": "Het lijkt erop dat deze vraag extra informatie vereist. Kunt u preciseren wat u precies wilt weten?",
            "citations": citations,
        }

    # 3) Fallback RAG
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
