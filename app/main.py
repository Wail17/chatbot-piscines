# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
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
    allow_headers=["Content-Type", "Authorization", "Accept", "Origin", "ngrok-skip-browser-warning"],
    expose_headers=["Content-Type"],
)

# ---------------------- Schemas ----------------------
class ChatRequest(BaseModel):
    query: str
    audience: str = "client"
    debug: bool = False
    extra: Optional[Dict[str, Any]] = None  # ex: {"gen":"gen1"} ou {"choice":"wifipool"}

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
    "2) Bij toestel zoeken: Gen 1 toont meestal meerdere modules; Gen 2 toont 1 module.",
    "3) Gen 1 wordt vaak met USB 5V geleverd; Gen 2 met 220V of 12V stekker.",
]

# ---------------------- FAQ (JSON/JSONL) ----------------------
_FAQ_JSON = os.path.join(STORE_DIR, "faq_index.json")     # si tu as un JSON tableau
_FAQ_JSONL = os.path.join(STORE_DIR, "faq_index.jsonl")   # si tu as du JSONL (1 objet par ligne)

def _boolish(v: Any) -> bool:
    if v is None: return False
    s = str(v).strip().lower()
    return s in {"x", "1", "true", "yes", "ja", "oui"}

def _load_faq() -> List[dict]:
    recs: List[dict] = []
    if os.path.exists(_FAQ_JSONL):
        with open(_FAQ_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                recs.append(json.loads(line))
    elif os.path.exists(_FAQ_JSON):
        with open(_FAQ_JSON, "r", encoding="utf-8") as f:
            recs = json.load(f)
    else:
        return []

    # normalisation minimale -> structure commune
    out: List[dict] = []
    for r in recs:
        out.append({
            "category": r.get("Categorie", "") or r.get("Category", ""),
            "question": r.get("Vraag", "") or r.get("Question", ""),
            "answer": r.get("Antwoord", "") or r.get("Answer", ""),
            "photo": r.get("Foto", "") or r.get("Photo", ""),
            "video_url": r.get("Filmpje", "") or r.get("Video", "") or r.get("Video_URL", ""),
            # tags = toutes colonnes cochées 'x'
            "tags": [k for k, v in r.items() if k not in {"Categorie","Vraag","Antwoord","Foto","Filmpje","Video","Video_URL"} and _boolish(v)],
        })
    return out

_FAQ: List[dict] = _load_faq()

# ---------------------- Matching helpers ----------------------
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
    if not _FAQ:
        return None
    uq = _normalize(user_q)

    # exact/inclusion sur question
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if q and (uq == q or uq in q or q in uq):
            return row

    # fuzzy question
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q: continue
        sc = _ratio(uq, q)
        if sc > best[0]: best = (sc, row)
    if best[0] >= min_score:
        return best[1]

    # inclusion/fuzzy sur answer
    best = (0.0, None)
    for row in _FAQ:
        a = _normalize(row.get("answer", ""))
        if not a: continue
        if uq and (uq in a or a in uq):
            return row
        sc = _ratio(uq, a)
        if sc > best[0]: best = (sc, row)
    return best[1] if best[0] >= (min_score - 0.08) else None

# ---------------------- Découpage des sous-sections ----------------------
# On supporte deux familles de sections dans 'Antwoord':
#  - GEN: "Gen 1 : ...", "Gen 2 : ...", "Gen 3 : ..."
#  - TYPE: "Wifipool : ...", "Benisol : ...", "Display : ..."
_SECTION_PATTERNS = {
    "gen": [
        ("gen1", r"^\s*gen\s*1\s*:\s*", "Gen 1"),
        ("gen2", r"^\s*gen\s*2\s*:\s*", "Gen 2"),
        ("gen3", r"^\s*gen\s*3\s*:\s*", "Gen 3"),
    ],
    "type": [
        ("wifipool", r"^\s*wifipool\s*:\s*", "Wifipool"),
        ("benisol", r"^\s*benisol\s*:\s*", "Benisol"),
        ("display", r"^\s*display\s*:\s*", "Display"),
    ],
}

def _split_sections(text: str, family: str) -> Dict[str, str]:
    """
    Découpe 'text' selon la famille ('gen' ou 'type') en utilisant les en-têtes "X :".
    Retour: dict { key -> bloc_texte }
    """
    if not text or family not in _SECTION_PATTERNS:
        return {}
    pats = _SECTION_PATTERNS[family]

    # Construire un regex qui capture les en-têtes et le contenu jusqu'au prochain en-tête
    # Exemple: ^(Gen 1\s*:)(.*?)(?=^Gen 2\s*:|^Gen 3\s*:|$)
    headers = [p[1] for p in pats]
    header_union = "|".join(f"({h})" for h in headers)
    # repère toutes les occurrences d'en-têtes
    header_re = re.compile(header_union, re.IGNORECASE | re.MULTILINE)

    # trouver positions des en-têtes
    matches = list(header_re.finditer(text))
    if not matches:
        return {}

    sections: Dict[str, str] = {}
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()

        # identifier quel header correspond
        hdr = m.group(0)
        key = None
        label = None
        for k, pat, lbl in pats:
            if re.match(pat, hdr, flags=re.IGNORECASE | re.MULTILINE):
                key, label = k, lbl
                break
        if not key:
            continue

        # enlever l'en-tête "X :" au début du bloc
        block = re.sub(r"^\s*[^:]+:\s*", "", block, flags=re.IGNORECASE | re.MULTILINE).strip()
        if block:
            sections[key] = block
    return sections

def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict): return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1", "gen 1"}: return "gen1"
    if g in {"gen2", "gen 2"}: return "gen2"
    if g in {"gen3", "gen 3"}: return "gen3"
    return None

def _parse_extra_choice(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict): return None
    for k in ("choice", "optie", "option", "selectie"):
        v = extra.get(k)
        if v: return str(v)
    return None

def _best_label_match(value: str, labels: List[str]) -> Optional[str]:
    nv = _normalize(value)
    best = (0.0, None)
    for lab in labels:
        nl = _normalize(lab)
        if nv == nl or nv in nl or nl in nv:
            return lab
        sc = _ratio(nv, nl)
        if sc > best[0]: best = (sc, lab)
    return best[1] if best[0] >= 0.78 else None

# ---------------------- Routes ----------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}


@app.post("/ingest")
def ingest(req: IngestRequest):
    """
    Charge le JSON/JSONL fourni, le copie dans STORE_DIR sous le nom standard,
    puis recharge l'index en mémoire (_FAQ).
    """
    path = (req.path or "").strip()
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"file not found: {path}")

    os.makedirs(STORE_DIR, exist_ok=True)

    ext = os.path.splitext(path)[1].lower()
    if ext not in {".jsonl", ".json"}:
        raise HTTPException(status_code=400, detail="expected .jsonl or .json")

    target = os.path.join(
        STORE_DIR,
        "faq_index.jsonl" if ext == ".jsonl" else "faq_index.json"
    )

    # copie dans le STORE_DIR
    shutil.copyfile(path, target)

    # recharge en mémoire
    global _FAQ
    _FAQ[:] = _load_faq()

    return {"reloaded": True, "faq_rows": len(_FAQ), "target": target}

@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    return add_correction(req.question, req.answer, req.tags)

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())

@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.query or "").strip()

    # 1) Corrections admin
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    # 2) Lookup FAQ
    row = _best_faq_match(q)
    if row:
        question = row.get("question") or "FAQ"
        answer = row.get("answer") or ""
        citations = [{"title": question, "source": row.get("category") or "FAQ", "page": None}]

        # a) Sections GEN ?
        gen_sections = _split_sections(answer, "gen")
        if len(gen_sections) >= 2:
            chosen = _parse_extra_gen(req.extra)
            if chosen and chosen in gen_sections:
                return {"answer": gen_sections[chosen], "citations": citations}
            # pas de choix -> demander GEN
            return {
                "answer": "Hebt u een Gen 1 of een Gen 2 apparaat?",
                "clarify": {"param": "gen", "options": [ "gen1", "gen2" ] + (["gen3"] if "gen3" in gen_sections else []), "tips": GEN_TIPS_NL},
                "citations": citations,
            }

        # b) Sections TYPE (Wifipool / Benisol / Display) ?
        type_sections = _split_sections(answer, "type")
        if len(type_sections) >= 2:
            labels_pretty = {
                "wifipool": "Wifipool",
                "benisol": "Benisol",
                "display": "Display",
            }
            # Si l'utilisateur a déjà choisi une option (extra.choice)
            choice = _parse_extra_choice(req.extra)
            if choice:
                label = _best_label_match(choice, [labels_pretty[k] for k in type_sections.keys()])
                if label:
                    # remap pretty -> key
                    key = [k for k,v in labels_pretty.items() if v == label][0]
                    return {"answer": type_sections[key], "citations": citations}

            # Demander le choix
            present_pretty = [labels_pretty[k] for k in type_sections.keys()]
            return {
                "answer": "Welk type apparaat heb je?",
                "clarify": {"param": "choice", "options": present_pretty},
                "citations": citations,
            }

        # c) Sinon : réponse directe
        if answer.strip():
            # ajoute vidéo si dispo
            if row.get("video_url"):
                answer += "\n\nBekijk video: " + str(row["video_url"])
            return {"answer": answer, "citations": citations}

        # garde-fou si pas de contenu exploitable
        return {"answer": "Er is geen specifiek antwoord gevonden voor deze vraag.", "citations": citations}

    # 3) Fallback RAG
    try:
        gen = _parse_extra_gen(req.extra) or (detect_gen(q) if callable(detect_gen) else None)
    except Exception:
        gen = _parse_extra_gen(req.extra)

    try:
        docs = retrieve(q, gen_filter=gen)
    except TypeError:
        docs = retrieve(q)

    try:
        found: Set[str] = extract_found_gens(docs)
    except Exception:
        found = set()

    if not gen and found:
        options = sorted(list(found & {"gen1","gen2","gen3"})) or ["gen1","gen2"]
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
