# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os, json, re, unicodedata, shutil
from difflib import SequenceMatcher

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Faculatif / déjà existant dans ton app
from .rag import retrieve, generate_answer, detect_gen, extract_found_gens  # type: ignore
from .training import add_correction, search_correction, save_feedback       # type: ignore
from .config import CORRECTION_THRESHOLD, STORE_DIR

# -----------------------------------------------------------------------------
# FastAPI + CORS
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Schémas d’E/S
# -----------------------------------------------------------------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None  # contiendra p.ex. {"pick":"Wifipool"}

class IngestRequest(BaseModel):
    path: str                  # chemin lisible par le serveur (Railway)
    source_type: str = "faq"   # ignoré ici, pour compat descendante

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

# -----------------------------------------------------------------------------
# Constantes et utilitaires
# -----------------------------------------------------------------------------
JSONL_TARGET = os.path.join(STORE_DIR, "faq.jsonl")
os.makedirs(STORE_DIR, exist_ok=True)

# Normalisation texte pour matching souple
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

def _truthy(v: Any) -> bool:
    if v is None: return False
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "ja", "oui", "x"}

def _norm_label(s: str) -> str:
    """Normalise un libellé d’option ('WiFi', 'wifipool', 'GEN 1', …)."""
    return _normalize(s)

# -----------------------------------------------------------------------------
# Chargement JSONL en mémoire
# -----------------------------------------------------------------------------
_FAQ: List[dict] = []

def _row_unify_keys(d: dict) -> dict:
    """
    Harmonise les variantes de clés possibles.
    Supporte:
      - 'categorie' / 'Categorie'
      - 'vraag' / 'Vraag'
      - 'follow_up' / 'followup'
      - 'follow_up_question' / 'followup_question'
      - 'options' / 'opties'
      - 'antwoord' / 'Antwoord'
    """
    out = {}

    # copie brutale pour conserver tout:
    out.update(d)

    # alias majeurs
    if "categorie" not in out and "Categorie" in out:
        out["categorie"] = out["Categorie"]
    if "vraag" not in out and "Vraag" in out:
        out["vraag"] = out["Vraag"]
    if "antwoord" not in out and "Antwoord" in out:
        out["antwoord"] = out["Antwoord"]

    # follow_up
    if "follow_up" not in out:
        if "followup" in out:
            out["follow_up"] = out["followup"]
    # follow_up_question
    if "follow_up_question" not in out:
        if "followup_question" in out:
            out["follow_up_question"] = out["followup_question"]
    # options
    if "options" not in out and "opties" in out:
        out["options"] = out["opties"]

    # booléans clean
    if "follow_up" in out:
        out["follow_up"] = _truthy(out["follow_up"])

    return out

def _load_jsonl(path: str) -> List[dict]:
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                d = json.loads(s)
                rows.append(_row_unify_keys(d))
            except Exception:
                # on ignore la ligne invalide
                pass
    return rows

# Charger au boot si un fichier existe déjà
if os.path.exists(JSONL_TARGET):
    _FAQ = _load_jsonl(JSONL_TARGET)

# -----------------------------------------------------------------------------
# Recherche dans le JSONL
# -----------------------------------------------------------------------------
def _best_faq_match(user_q: str, min_score: float = 0.80) -> Optional[dict]:
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # exact / inclusion sur la question
    for row in _FAQ:
        q = _normalize(str(row.get("vraag", "")))
        if q and (uq == q or uq in q or q in uq):
            return row

    # fuzzy sur la question
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(str(row.get("vraag", "")))
        if not q:
            continue
        sc = _ratio(uq, q)
        if sc > best[0]:
            best = (sc, row)
    if best[0] >= min_score:
        return best[1]

    # fallback: inclusion/fuzzy sur une éventuelle réponse 'antwoord'
    best = (0.0, None)
    for row in _FAQ:
        a = _normalize(str(row.get("antwoord", "")))
        if not a:
            continue
        if uq and (uq in a or a in uq):
            return row
        sc = _ratio(uq, a)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= (min_score - 0.08) else None

def _row_options(row: dict) -> Dict[str, dict]:
    """
    Retourne un dict {label_original -> data_option}, et un
    dict normalisé {label_norm -> label_original} pour matching.
    """
    opts = row.get("options") or {}
    if not isinstance(opts, dict):
        return {}
    return opts

def _pick_option(row: dict, user_choice: str) -> Optional[Tuple[str, str]]:
    """
    Match le choix utilisateur contre les labels d’options.
    Retourne (answer, label_matched) ou None.
    """
    opts = _row_options(row)
    if not opts:
        return None

    # index normalisé
    norm_to_label = { _norm_label(lbl): lbl for lbl in opts.keys() }
    uc_norm = _norm_label(user_choice)

    # match direct normalisé
    if uc_norm in norm_to_label:
        real = norm_to_label[uc_norm]
        data = opts.get(real, {})
        ans = str(data.get("antwoord", "")).strip()
        tip = str(data.get("aanbeveling", "")).strip()
        full = ans if not tip else (ans + "\n\n" + tip)
        return (full, real)

    # fuzzy léger si pas trouvé
    best = (0.0, None)
    for nlabel, real in norm_to_label.items():
        sc = _ratio(uc_norm, nlabel)
        if sc > best[0]:
            best = (sc, real)
    if best[0] >= 0.75:
        data = opts.get(best[1], {})
        ans = str(data.get("antwoord", "")).strip()
        tip = str(data.get("aanbeveling", "")).strip()
        full = ans if not tip else (ans + "\n\n" + tip)
        return (full, best[1])

    return None

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.get("/debug/faq_lookup")
def dbg_lookup(q: str):
    row = _best_faq_match(q)
    if not row:
        return {"q": q, "matched": False}
    return {
        "q": q,
        "matched": True,
        "vraag": row.get("vraag"),
        "follow_up": row.get("follow_up", False),
        "follow_up_question": row.get("follow_up_question"),
        "options": list((_row_options(row) or {}).keys()),
    }

@app.post("/ingest")
def ingest(req: IngestRequest):
    """
    Charge un fichier JSONL (1 JSON par ligne) depuis `req.path` et le copie
    vers STORE_DIR/faq.jsonl puis recharge en mémoire.
    """
    src = req.path
    if not os.path.exists(src):
        return {"reloaded": False, "error": "file not found", "faq_rows": len(_FAQ)}
    # copie atomique-ish
    shutil.copyfile(src, JSONL_TARGET)

    global _FAQ
    _FAQ = _load_jsonl(JSONL_TARGET)
    return {"reloaded": True, "faq_rows": len(_FAQ)}

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

# Paramètre générique pour la clarification côté UI
FOLLOWUP_PARAM = "pick"

@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) corrections admin prioritaire
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) JSONL direct
    row = _best_faq_match(q)
    if row:
        # a) cas avec follow_up -> poser la question si on n’a pas encore le choix
        follow = bool(row.get("follow_up", False))
        if follow:
            # si le choix a déjà été donné (ex. clic bouton)
            choice = None
            if isinstance(req.extra, dict):
                choice = req.extra.get(FOLLOWUP_PARAM)

            if choice:
                picked = _pick_option(row, str(choice))
                if picked:
                    answer_text, matched_label = picked
                    citations = [{
                        "title": row.get("vraag") or "FAQ",
                        "source": JSONL_TARGET,
                        "page": None
                    }]
                    return {"answer": answer_text, "citations": citations}

                # si le choix n'a pas matché, on repose la question
            # poser la clarification
            opts = list((_row_options(row) or {}).keys())
            return {
                "answer": row.get("follow_up_question") or "Kunt u een keuze maken?",
                "clarify": {"param": FOLLOWUP_PARAM, "options": opts},
                "citations": [{"title": row.get("vraag") or "FAQ", "source": JSONL_TARGET, "page": None}],
            }

        # b) sinon : réponse directe 'antwoord'
        direct = str(row.get("antwoord", "")).strip()
        if direct:
            citations = [{"title": row.get("vraag") or "FAQ", "source": JSONL_TARGET, "page": None}]
            return {"answer": direct, "citations": citations}

    # 3) Fallback RAG (si dispo)
    #    Détection éventuelle de gen (compatibilité avec ton ancien front)
    extra_gen = None
    if isinstance(req.extra, dict):
        g = str(req.extra.get("gen", "")).strip().lower()
        if g in {"gen1", "gen 1"}: extra_gen = "gen1"
        elif g in {"gen2", "gen 2"}: extra_gen = "gen2"
        elif g in {"gen3", "gen 3"}: extra_gen = "gen3"

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
            "clarify": {"param": "gen", "options": options},
            "citations": [],
        }

    if not docs:
        return {
            "answer": "Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?",
            "citations": [],
        }

    answer, citations = generate_answer(q, docs)
    return {"answer": answer, "citations": citations}
