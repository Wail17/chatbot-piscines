# app/main.py
from typing import List, Optional, Dict, Any, Set
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

# ---------------------- FAQ index ----------------------
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
try:
    with open(_FAQ_PATH, "r", encoding="utf-8") as f:
        _FAQ: List[dict] = json.load(f)
except Exception:
    _FAQ = []

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

def _best_faq_match(user_q: str, min_score: float = 0.74) -> dict | None:
    """
    Appariement robuste :
    1) exact / inclusion sur question
    2) fuzzy question
    3) inclusion / fuzzy sur réponse
    Seuils assouplis pour mieux couvrir les variantes.
    """
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # 1) exact / inclusion sur question
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        if uq == q or uq in q or q in uq:
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

    # 3) inclusion/fuzzy sur réponse
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
    # léger rabais sur le seuil pour la réponse
    return best[1] if best[0] >= (min_score - 0.06) else None

# --- recherche par mots-clés (quelques synonymes utiles) ---
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

# ---------------------- helpers: extra params ----------------------
def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict):
        return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1", "gen 1"}: return "gen1"
    if g in {"gen2", "gen 2"}: return "gen2"
    if g in {"gen3", "gen 3"}: return "gen3"
    return None

def _parse_extra_choice(extra: Optional[Dict[str, Any]], param: str) -> Optional[str]:
    """Récupère la valeur d’un paramètre arbitraire (ex: 'device' -> 'wifipool'/'benisol')."""
    if not isinstance(extra, dict) or not param:
        return None
    val = str(extra.get(param, "")).strip().lower()
    return val or None

def _detect_choice_in_text(text: str, options: List[str]) -> Optional[str]:
    """Tentative simple: si une des options apparaît telle quelle dans la requête."""
    t = " " + _normalize(text) + " "
    for opt in options or []:
        o = " " + _normalize(opt) + " "
        if o in t:
            return opt
    return None

# ---------------------- Routes ----------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    r1 = _best_faq_match(q, min_score=0.74)
    r2 = None if r1 else _faq_keyword_search(q)
    return {"q": q, "best_match": bool(r1 or r2), "via": "best" if r1 else ("keywords" if r2 else None), "row": (r1 or r2)}

@app.post("/ingest")
def ingest(req: IngestRequest):
    res = ingest_path(req.path, req.source_type)
    # recharger l'index si régénéré
    global _FAQ
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            _FAQ = json.load(f)
    except Exception:
        pass
    return res

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) Correction admin prioritaire
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Lookup FAQ (question -> éventuellement clarification)
    row = _best_faq_match(q, min_score=0.74) or _faq_keyword_search(q)
    if row:
        citations = [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}]
        video_line = ("\n\nBekijk video: " + str(row["video_url"])) if row.get("video_url") else ""

        # 2.a) Cas spécial GEN (issu d'une cellule jaune)
        if bool(row.get("ask_gen")):
            # si déjà fourni (via extra) ou déductible de la question → répondre directement
            gen_from_req = _parse_extra_gen(req.extra)
            try:
                gen_from_text = detect_gen(q)
            except Exception:
                gen_from_text = None
            final_gen = gen_from_req or gen_from_text

            if final_gen == "gen1" and row.get("answer_gen1"):
                return {"answer": row["answer_gen1"] + video_line, "citations": citations}
            if final_gen == "gen2" and row.get("answer_gen2"):
                return {"answer": row["answer_gen2"] + video_line, "citations": citations}
            if final_gen == "gen3" and row.get("answer_gen3"):
                return {"answer": row["answer_gen3"] + video_line, "citations": citations}

            # sinon → demander GEN
            opts = [o for o in ["gen1", "gen2", "gen3"] if row.get(f"answer_{o}")]
            if not opts:
                opts = ["gen1", "gen2"]
            return {
                "answer": row.get("followup_q") or "Hebt u een Gen 1 of een Gen 2 apparaat?",
                "clarify": {"param": "gen", "options": opts, "tips": GEN_TIPS_NL},
                "citations": citations,
            }

        # 2.b) Clarification GENERIQUE (ex: Wifipool vs Benisol)
        if bool(row.get("ask_choice")):
            param = (row.get("choice_param") or "choice").strip()
            options: List[str] = row.get("choice_options") or []

            # Essayer de déduire depuis extra ou texte
            picked = _parse_extra_choice(req.extra, param) or _detect_choice_in_text(q, options)
            answers_map: Dict[str, str] = row.get("answers") or {}

            if picked and picked in answers_map and answers_map[picked]:
                return {"answer": str(answers_map[picked]) + video_line, "citations": citations}

            # sinon → demander explicitement
            if options:
                return {
                    "answer": row.get("followup_q") or (param.capitalize() + "?"),
                    "clarify": {"param": param, "options": options},
                    "citations": citations,
                }

        # 2.c) Réponse directe (pas de clarification demandée)
        if row.get("answer"):
            return {"answer": row["answer"] + video_line, "citations": citations}

        # garde-fou
        return {"answer": "(geen antwoord gevonden)", "citations": citations}

    # 3) Fallback RAG si pas de ligne FAQ trouvée
    try:
        extra_gen = _parse_extra_gen(req.extra)
    except Exception:
        extra_gen = None

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
