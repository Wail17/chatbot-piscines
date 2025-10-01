# app/main.py
from typing import List, Optional, Dict, Any, Set
import os, json, re, unicodedata
from difflib import SequenceMatcher

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag import retrieve, generate_answer, detect_gen, extract_found_gens
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
    extra: Optional[Dict[str, Any]] = None  # e.g. {"choice":"Gen 1"}

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
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
def _load_faq():
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            rows = json.load(f)
            # tolère 'opties' -> 'options'
            for r in rows:
                if "opties" in r and "options" not in r:
                    r["options"] = r["opties"]
            return rows
    except Exception:
        return []

_FAQ: List[dict] = _load_faq()

# ---------------------- lookup helpers ----------------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*")

def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s = _PUNCT_RE.sub("", s)
    return s.strip()

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _best_faq_match(user_q: str, min_score: float = 0.80) -> dict | None:
    """Matching robuste sur 'vraag' puis, si besoin, sur les contenus d'options/antwoord."""
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # 1) exact / inclusion sur vraag
    for row in _FAQ:
        q = _normalize(row.get("vraag", "") or row.get("question", ""))
        if q and (uq == q or uq in q or q in uq):
            return row

    # 2) fuzzy sur vraag
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(row.get("vraag", "") or row.get("question", ""))
        if not q:
            continue
        sc = _ratio(uq, q)
        if sc > best[0]:
            best = (sc, row)
    if best[0] >= min_score:
        return best[1]

    # 3) fallback: inclusion/fuzzy dans les réponses (top-level 'antwoord' ou options.*.antwoord)
    best = (0.0, None)
    for row in _FAQ:
        blob = []
        if row.get("antwoord"):
            blob.append(str(row["antwoord"]))
        opts = row.get("options") or {}
        if isinstance(opts, dict):
            for k, v in opts.items():
                if isinstance(v, dict):
                    if v.get("antwoord"):
                        blob.append(str(v["antwoord"]))
        text = _normalize(" ".join(blob))
        if not text:
            continue
        if uq and (uq in text or text in uq):
            return row
        sc = _ratio(uq, text)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= (min_score - 0.08) else None

# ---- choix / options helpers ----
def _norm_label(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())

# petits synonymes utiles
_SYNO = {
    "gen1": "gen 1",
    "gen2": "gen 2",
    "gen3": "gen 3",
    "wifipool": "wifi apparaten",
    "wifi": "wifi apparaten",
    "display": "display apparaten",
    "benisol": "benisol",
}

def _row_options(row: dict) -> Dict[str, dict]:
    opts = row.get("options")
    return opts if isinstance(opts, dict) else {}

def _match_option(choice_raw: str, opts: Dict[str, dict]) -> Optional[str]:
    """Retourne la clé d'option la plus plausible (exact norm, synonymes, puis fuzzy)."""
    if not choice_raw or not opts:
        return None
    labels = list(opts.keys())
    if not labels:
        return None

    c = _norm_label(choice_raw)
    # 1) exact normalisé
    for k in labels:
        if _norm_label(k) == c:
            return k
    # 2) synonymes
    if c in _SYNO:
        target = _norm_label(_SYNO[c])
        for k in labels:
            if _norm_label(k) == target:
                return k
    # 3) fuzzy
    best = (0.0, None)
    for k in labels:
        sc = _ratio(c, _norm_label(k))
        if sc > best[0]:
            best = (sc, k)
    return best[1] if best[0] >= 0.70 else None

def _answer_from_option(row: dict, key: str) -> Optional[str]:
    opts = _row_options(row)
    if key not in opts or not isinstance(opts[key], dict):
        return None
    ans = str(opts[key].get("antwoord") or "").strip()
    rec = str(opts[key].get("aanbeveling") or "").strip()
    if ans and rec:
        return f"{ans}\n\nAanbeveling: {rec}"
    return ans or None

def _parse_choice(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict):
        return None
    v = extra.get("choice")
    if isinstance(v, str) and v.strip():
        return v.strip()
    # compat: si le front t’envoie encore "gen"
    g = extra.get("gen")
    return g.strip() if isinstance(g, str) and g.strip() else None

# ---------------------- Routes ----------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    row = _best_faq_match(q)
    return {"q": q, "found": bool(row), "row": row}

@app.post("/ingest")
def ingest(req: IngestRequest):
    res = ingest_path(req.path, req.source_type)
    # recharge l'index si un JSONL a été traité
    global _FAQ
    _FAQ[:] = _load_faq()
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

    # 1) corrections admin
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) JSONL lookup
    row = _best_faq_match(q)
    if row:
        follow = bool(row.get("follow_up"))
        vraag = row.get("vraag") or row.get("question") or "FAQ"
        src = row.get("source") or "jsonl"

        # Normalise les options
        if "opties" in row and "options" not in row:
            row["options"] = row["opties"]
        options_dict = _row_options(row)
        options_list = list(options_dict.keys())

        # follow_up : pose la question OU répond si choix fourni
        if follow:
            choice_raw = _parse_choice(req.extra)
            if choice_raw:
                matched = _match_option(choice_raw, options_dict)
                if matched:
                    a = _answer_from_option(row, matched)
                    if a:
                        return {
                            "answer": a,
                            "citations": [{"title": vraag, "source": src, "page": None}],
                        }
                    # si pas d’answer trouvée pour cette option, on retombe en clarify
            # -> demander le choix
            prompt = row.get("follow_up_question") or "Kies een optie om verder te gaan:"
            return {
                "answer": prompt,
                "clarify": {"param": "choice", "options": options_list},
                "citations": [{"title": vraag, "source": src, "page": None}],
            }

        # pas de follow_up → réponse directe
        direct = str(row.get("antwoord") or "").strip()
        if not direct and options_list and len(options_list) == 1:
            # tolérance: s’il n’y a qu’une option, on prend son antwoord
            direct = _answer_from_option(row, options_list[0]) or ""
        if direct:
            return {
                "answer": direct,
                "citations": [{"title": vraag, "source": src, "page": None}],
            }
        # sinon, pas d’antwoord → continuer en RAG fallback

    # 3) RAG fallback
    # (on garde ta logique GEN si tu veux)
    extra_gen = None
    try:
        if isinstance(req.extra, dict):
            g = str(req.extra.get("gen", "")).strip().lower()
            extra_gen = "gen1" if g in {"gen1", "gen 1"} else "gen2" if g in {"gen2", "gen 2"} else "gen3" if g in {"gen3", "gen 3"} else None
    except Exception:
        pass

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
