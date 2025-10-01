# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os, json, re, unicodedata, hashlib
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

# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
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
# Models
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# Load FAQ index (écrit par /ingest)
# ---------------------------------------------------------------------
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
try:
    with open(_FAQ_PATH, "r", encoding="utf-8") as f:
        _FAQ: List[dict] = json.load(f)
except Exception:
    _FAQ = []

# ---------------------------------------------------------------------
# Helpers (normalisation / fuzzy / ids / composition)
# ---------------------------------------------------------------------
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

def _row_get(d: dict, *keys, default=None):
    for k in keys:
        if k in d and isinstance(d[k], str) and d[k].strip():
            return d[k]
    return default

def _make_row_ref(row: dict) -> str:
    # ID stable si présent, sinon hash (question+source)
    if "id" in row and row["id"]:
        return str(row["id"])
    base = (row.get("question","") or "") + "|" + (row.get("source","") or "")
    return hashlib.md5(base.encode("utf-8")).hexdigest()

# synonymes pour matcher les choix utilisateur
OPTION_SYNONYMS: Dict[str, Set[str]] = {
    "gen1": {"gen1","gen 1","g1","type 1","gen1 apparaat","gen 1 apparaat","ik heb gen 1","ik heb een gen 1"},
    "gen2": {"gen2","gen 2","g2","type 2","gen2 apparaat","gen 2 apparaat","ik heb gen 2","ik heb een gen 2"},
    "gen3": {"gen3","gen 3","g3","type 3","gen3 apparaat","gen 3 apparaat","ik heb gen 3","ik heb een gen 3"},
    "wifipool": {"wifipool","wifi","wifi apparaat","wifi-apparaat","wifi apparaten","wi-fi","wifi device","wifipool apparaat"},
    "benisol": {"benisol","zonder wifi","no wifi","standalone","benisol apparaat","benisol (zonder wifi)"},
    "display": {"display","display apparaat","display-apparaat","scherm","display apparaten"},
}

def _best_label_for_choice(options: Dict[str, dict], user_text: str) -> Optional[str]:
    """Retourne la clé d'option la plus probable selon la saisie utilisateur."""
    if not options:
        return None
    ut = _normalize(user_text)
    labels = list(options.keys())
    norm_map = {lab: _normalize(str(lab)) for lab in labels}

    # 1) heuristique GEN/Wifipool/Benisol/Display
    def label_contains(token: str) -> Optional[str]:
        for lab, nl in norm_map.items():
            if token in nl:
                return lab
        return None

    # GEN
    if any(t in ut for t in OPTION_SYNONYMS["gen1"]):
        found = label_contains("gen1") or label_contains("gen 1")
        if found: return found
    if any(t in ut for t in OPTION_SYNONYMS["gen2"]):
        found = label_contains("gen2") or label_contains("gen 2")
        if found: return found
    if any(t in ut for t in OPTION_SYNONYMS["gen3"]):
        found = label_contains("gen3") or label_contains("gen 3")
        if found: return found

    # Wifipool / Benisol / Display
    if any(t in ut for t in OPTION_SYNONYMS["wifipool"]):
        found = label_contains("wifipool") or label_contains("wifi")
        if found: return found
    if any(t in ut for t in OPTION_SYNONYMS["benisol"]):
        found = label_contains("benisol") or label_contains("zonder wifi")
        if found: return found
    if any(t in ut for t in OPTION_SYNONYMS["display"]):
        found = label_contains("display")
        if found: return found

    # 2) fuzzy sur les libellés
    best = (0.0, None)
    for lab, nl in norm_map.items():
        sc = _ratio(ut, nl)
        if sc > best[0]:
            best = (sc, lab)
    if best[0] >= 0.70:
        return best[1]

    return None

def _answer_from_option_block(block: dict, row: Optional[dict] = None) -> str:
    """Compose une réponse à partir de l’option choisie, même sans champ 'antwoord'."""
    parts: List[str] = []

    # Préfixe: réponse générale éventuelle au niveau de la ligne
    if row:
        base = _row_get(row, "antwoord", "answer", "Antwoord", default="")
        if isinstance(base, str) and base.strip():
            parts.append(base.strip())

    # Réponse courte au niveau de l’option
    ans = _row_get(block, "antwoord", "answer", "Antwoord", default="")
    rec = _row_get(block, "aanbeveling", "recommendation", "recommandation", default="")
    if isinstance(ans, str) and ans.strip():
        parts.append(ans.strip())
    if isinstance(rec, str) and rec.strip():
        parts.append(rec.strip())

    # Textes simples
    simple_keys = [
        ("uitleg", "Uitleg"),
        ("beschrijving", "Beschrijving"),
        ("capaciteit", "Capaciteit"),
        ("bekabeling", "Bekabeling"),
        ("aantal_sondes", "Aantal sondes"),
        ("met_higo", "Met Higo"),
        ("zonder_higo", "Zonder Higo"),
        ("type", "Type"),
        ("opmerking", "Opmerking"),
        ("tip_app", "Tip"),
        ("opmerking_veiligheid", "Veiligheid"),
        ("tip_ethernet", "Tip Ethernet"),
    ]
    for k, label in simple_keys:
        val = block.get(k)
        if isinstance(val, str) and val.strip():
            parts.append(f"{label}: {val.strip()}")

    # Listes -> puces
    list_keys = [
        ("te_bestellen", "Te bestellen"),
        ("benodigd", "Benodigd"),
        ("installatie", "Installatie"),
        ("stappen", "Stappen"),
        ("stappen_met_higo", "Stappen (met Higo)"),
        ("stappen_zonder_higo", "Stappen (zonder Higo)"),
        ("varianten", "Varianten"),
        ("stappen_algemeen", "Stappen (algemeen)"),
        ("tips", "Tips"),
        ("punten", "Punten"),
        ("links", "Links"),
        ("verlengstukken", "Verlengstukken"),
    ]
    for k, label in list_keys:
        lst = block.get(k)
        if isinstance(lst, list) and lst:
            bullets = "\n".join(f"- {str(x).strip()}" for x in lst if str(x).strip())
            if bullets:
                parts.append(f"{label}:\n{bullets}")

    # Dictionnaires
    dict_keys = [("specificaties", "Specificaties")]
    for k, label in dict_keys:
        dic = block.get(k)
        if isinstance(dic, dict) and dic:
            bullets = "\n".join(f"- {str(kk).strip()}: {str(vv).strip()}" for kk, vv in dic.items())
            if bullets:
                parts.append(f"{label}:\n{bullets}")

    text = "\n\n".join(p for p in parts if p)
    if not text.strip():
        text = "(geen details beschikbaar voor deze keuze)"
    return text

# ---------------- Lookup helpers (robustes) ----------------
def _best_faq_match(user_q: str, min_score: float = 0.80) -> dict | None:
    """1) match exact/inclusion sur question normalisée
       2) fuzzy sur question
       3) inclusion/fuzzy sur réponse si besoin."""
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # exact / inclusion sur question
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        if uq == q or uq in q or q in uq:
            return row

    # fuzzy sur question
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

    # fuzzy sur réponse
    best = (0.0, None)
    for row in _FAQ:
        a = _normalize(row.get("answer", "") or "")
        if a:
            if uq and (uq in a or a in uq):
                return row
            sc = _ratio(uq, a)
            if sc > best[0]:
                best = (sc, row)
    return best[1] if best[0] >= (min_score - 0.08) else None

# --- recherche par mots-clés (synonymes utiles) ------------
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

def _find_row_from_ref_or_question(ref: Optional[str], context_q: Optional[str]) -> Optional[dict]:
    """Essaye d'identifier la ligne follow-up via un ref (id/question) ou la question d'origine."""
    if not _FAQ:
        return None
    if ref:
        # on compare soit id exact, soit question exacte
        for row in _FAQ:
            if str(row.get("id") or "") == ref or (row.get("question") == ref):
                return row
        # fallback: hash
        for row in _FAQ:
            if _make_row_ref(row) == ref:
                return row
    if context_q:
        cand = _best_faq_match(context_q)
        if cand:
            return cand
    return None

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    r1 = _best_faq_match(q)
    r2 = None if r1 else _faq_keyword_search(q)
    return {
        "q": q,
        "best_match": bool(r1 or r2),
        "via": "best" if r1 else ("keywords" if r2 else None),
        "row": (r1 or r2),
    }

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
    return {"reloaded": True, "faq_rows": len(_FAQ)}

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict):
        return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1", "gen 1"}: return "gen1"
    if g in {"gen2", "gen 2"}: return "gen2"
    if g in {"gen3", "gen 3"}: return "gen3"
    return None

# --------------------------- core /chat ---------------------------
@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 0) follow-up direct si on reçoit clarify_ref / context_question
    extra = req.extra or {}
    clarify_ref = str(extra.get("clarify_ref") or "").strip() or None
    context_q = str(extra.get("context_question") or "").strip() or None
    if clarify_ref or context_q:
        row = _find_row_from_ref_or_question(clarify_ref, context_q)
        if row and row.get("follow_up") and (row.get("options") or row.get("opties")):
            options_map: Dict[str, dict] = row.get("options") or row.get("opties") or {}
            chosen = _best_label_for_choice(options_map, q)
            if chosen and chosen in options_map:
                answer_text = _answer_from_option_block(options_map[chosen], row=row)
                citations = [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}]
                return {"answer": answer_text, "citations": citations}
            else:
                # on redemande proprement le choix
                labels = list(options_map.keys())
                return {
                    "answer": row.get("followup_q") or row.get("follow_up_question") or "Kun je je keuze bevestigen?",
                    "clarify": {
                        "ref": row.get("question") or _make_row_ref(row),
                        "options": labels
                    },
                    "citations": [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}],
                }

    # 1) Correction admin prioritaire
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Lookup FAQ (ultra-robuste)
    row = _best_faq_match(q) or _faq_keyword_search(q)
    if row:
        # follow-up ?
        if bool(row.get("follow_up")) and (row.get("options") or row.get("opties")):
            labels = list((row.get("options") or row.get("opties")).keys())
            return {
                "answer": row.get("followup_q") or row.get("follow_up_question") or "Kun je je keuze bevestigen?",
                "clarify": {
                    # on renvoie la question comme ref (facile à stocker côté front)
                    "ref": row.get("question") or _make_row_ref(row),
                    "options": labels,
                    # tips utiles si c'est un cas GEN
                    "tips": GEN_TIPS_NL if any("gen" in _normalize(l) for l in labels) else []
                },
                "citations": [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}],
            }

        # réponse directe
        extra_line = ""
        if row.get("video_url"):
            extra_line = "\n\nBekijk video: " + str(row["video_url"])
        citations = [{"title": row.get("question") or "FAQ", "source": row.get("source"), "page": None}]
        answer_text = row.get("answer", "") or row.get("Antwoord", "") or ""
        return {"answer": (answer_text + extra_line).strip(), "citations": citations}

    # 3) Déterminer génération éventuelle (RAG)
    extra_gen = _parse_extra_gen(req.extra)
    try:
        gen = extra_gen or detect_gen(q)
    except Exception:
        gen = extra_gen

    # 4) Récupération RAG (filtrée si gen détectée)
    try:
        docs = retrieve(q, gen_filter=gen)
    except TypeError:
        docs = retrieve(q)

    # 5) Si l’Excel/JSON marque des générations et que l’utilisateur n’a pas précisé -> demander GEN
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

    # 6) Réponse RAG standard (ou fallback si rien)
    if not docs:
        return {
            "answer": "Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?",
            "citations": [],
        }
    answer, citations = generate_answer(q, docs)
    return {"answer": answer, "citations": citations}
