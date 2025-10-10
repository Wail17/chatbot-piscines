# app/main.py
from typing import List, Optional, Dict, Any, Set, Tuple
import os, json, re, unicodedata, time
from difflib import SequenceMatcher, get_close_matches

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .rag import retrieve, generate_answer, detect_gen, extract_found_gens
from .ingest import ingest_path
from .training import add_correction, search_correction, save_feedback, vectorstore_status
from .config import (
    CORRECTION_THRESHOLD, STORE_DIR, DATA_DIR,
    # optionnel si tu veux un /health bavard:
    # CHROMA_DIR, EMBEDDINGS_MODEL, FEEDBACK_FILE, CORRECTIONS_COLLECTION
)

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
    # important: pas de liste mutable par défaut
    tags: List[str] = Field(default_factory=list)

class FeedbackIn(BaseModel):
    question: str
    answer: str
    good: bool
    corrected_answer: Optional[str] = None
    notes: Optional[str] = None
    user: Optional[str] = None

# ---------------------------------------------------------------------
# Tips / Const
# ---------------------------------------------------------------------
GEN_TIPS_NL = [
    "1) Een Gen 2 apparaat heeft een ethernet (internetkabel) aansluiting.",
    "2) Bij ‘toestellen zoeken’: Gen 1 toont meestal meerdere modules; Gen 2 meestal maar 1 module.",
    "3) Gen 1 heeft vaak een USB 5V stekker; Gen 2 heeft 220V of 12V stekker.",
]

# ---------------------------------------------------------------------
# Load FAQ
# ---------------------------------------------------------------------
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
_FAQ_FALLBACK_JSONL = os.path.join(DATA_DIR, "all", "faq", "FAQAI.jsonl")
_FAQ: List[dict] = []


def _load_faq_from_store() -> List[dict]:
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _coerce_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _load_faq_from_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []

    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                question = _coerce_str(
                    obj.get("vraag")
                    or obj.get("Vraag")
                    or obj.get("question")
                    or obj.get("Question")
                )
                if not question:
                    continue

                category = _coerce_str(
                    obj.get("categorie")
                    or obj.get("Categorie")
                    or obj.get("category")
                    or obj.get("Category")
                )
                answer = _coerce_str(
                    obj.get("antwoord")
                    or obj.get("Antwoord")
                    or obj.get("answer")
                    or obj.get("Answer")
                )
                follow_raw = (
                    obj.get("follow_up")
                    or obj.get("Follow_up")
                    or obj.get("followUp")
                    or obj.get("FollowUp")
                )
                if isinstance(follow_raw, str):
                    follow_up = follow_raw.strip().lower() in {"1", "true", "yes", "ja"}
                else:
                    follow_up = bool(follow_raw)
                followup_q = _coerce_str(
                    obj.get("follow_up_question")
                    or obj.get("followup_q")
                    or obj.get("clarify_question")
                )

                options_raw = (
                    obj.get("opties")
                    or obj.get("Opties")
                    or obj.get("options")
                    or obj.get("Options")
                    or {}
                )
                options: Dict[str, Any] = {}
                if isinstance(options_raw, dict):
                    for label, payload in options_raw.items():
                        key = _coerce_str(label) or str(label)
                        options[key] = payload

                row: Dict[str, Any] = {
                    "category": category,
                    "question": question,
                    "answer": answer if not follow_up else (answer or ""),
                    "follow_up": follow_up,
                    "followup_q": followup_q if (follow_up and followup_q) else None,
                    "options": options,
                    "source": path,
                }

                video = (
                    obj.get("video_url")
                    or obj.get("video")
                    or obj.get("Video")
                    or obj.get("Filmpje")
                    or obj.get("filmpje")
                )
                media = obj.get("media") if isinstance(obj.get("media"), dict) else None
                if not video and media:
                    video = media.get("video") or media.get("url")
                video_str = _coerce_str(video)
                if video_str:
                    row["video_url"] = video_str

                tags = obj.get("tags")
                if isinstance(tags, list):
                    row["tags"] = [str(t).strip() for t in tags if str(t).strip()]

                rows.append(row)
    except Exception:
        return []

    return rows


def _reload_faq() -> Tuple[int, List[dict]]:
    data: List[dict] = _load_faq_from_store()
    if not data:
        data = _load_faq_from_jsonl(_FAQ_FALLBACK_JSONL)
    global _FAQ
    _FAQ = data
    return (len(_FAQ), _FAQ)

_reload_faq()

# ---------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»“”\"'`]\s*")

def _normalize(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\u00a0", " ").lower()
    s = re.sub(r"\s+", " ", s)
    s = _PUNCT_RE.sub("", s)
    return s.strip()

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

# ---------------------------------------------------------------------
# Follow-up memory (fallback sans clarify_ref)
# ---------------------------------------------------------------------
# { client_id: {"q": base_question, "labels": [...], "ts": epoch, "row": row_dict} }
_PENDING_BY_CLIENT: Dict[str, Dict[str, Any]] = {}
_PENDING_TTL = 180.0  # seconds

def _client_id_from_request(req: Request) -> str:
    xf = req.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip()
    return (getattr(req.client, "host", None) or "unknown").strip()

def _set_pending(client_id: str, row: dict, labels: List[str]) -> None:
    _PENDING_BY_CLIENT[client_id] = {
        "q": row.get("question") or "",
        "labels": labels,
        "ts": time.time(),
        "row": row,
    }

def _pop_valid_pending(client_id: str) -> dict | None:
    item = _PENDING_BY_CLIENT.get(client_id)
    if not item:
        return None
    if time.time() - float(item.get("ts", 0.0)) > _PENDING_TTL:
        _PENDING_BY_CLIENT.pop(client_id, None)
        return None
    return item

# ---------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------
def _find_row_by_question(user_q: str) -> dict | None:
    if not _FAQ:
        return None
    uq = _normalize(user_q)
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if q and (uq == q or uq in q or q in uq):
            return row
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        sc = _ratio(uq, q)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= 0.80 else None

def _find_row_by_ref(ref_q: str) -> dict | None:
    if not ref_q:
        return None
    uq = _normalize(ref_q)
    best = (0.0, None)
    for row in _FAQ:
        q = _normalize(row.get("question", ""))
        if not q:
            continue
        if uq == q or uq in q or q in uq:
            return row
        sc = _ratio(uq, q)
        if sc > best[0]:
            best = (sc, row)
    return best[1] if best[0] >= 0.75 else None

# options / gen
_GEN_ALIASES: Dict[str, set] = {
    "gen1": {"gen1", "gen 1", "g1", "generation 1"},
    "gen2": {"gen2", "gen 2", "g2", "generation 2"},
    "gen3": {"gen3", "gen 3", "g3", "generation 3"},
}
_DEVICE_ALIASES: Dict[str, set] = {
    "wifipool": {"wifipool", "wifi", "wifi apparaat", "wifi-apparaat"},
    "benisol": {"benisol", "zonder wifi"},
    "display": {"display", "display apparaat", "display-apparaat"},
}

def _labels(row: dict) -> List[str]:
    opts = row.get("options") or {}
    return list(opts.keys())

def _map_choice_to_key(choice: str, option_labels: List[str]) -> str | None:
    t = _normalize(choice)
    for key, al in _DEVICE_ALIASES.items():
        if t in al:
            cands = [lbl for lbl in option_labels if key in _normalize(lbl)]
            if cands:
                return cands[0]
    for key, al in _GEN_ALIASES.items():
        if t in al:
            cands = [lbl for lbl in option_labels if "gen" in _normalize(lbl)]
            if len(cands) == 1:
                return cands[0]
    matches = get_close_matches(choice, option_labels, n=1, cutoff=0.6)
    if matches:
        return matches[0]
    for lbl in option_labels:
        if _normalize(lbl) in t or t in _normalize(lbl):
            return lbl
    return None

def _looks_like_followup_choice(text: str) -> bool:
    t = _normalize(text)
    bank = {
        "gen1","gen 1","g1","gen2","gen 2","g2","gen3","gen 3","g3",
        "wifipool","wifi","wifi apparaat","wifi-apparaat",
        "benisol","zonder wifi","display","display apparaat","display-apparaat"
    }
    return t in bank

def _map_choice_to_genkey(choice: str) -> str | None:
    t = _normalize(choice)
    for key, al in _GEN_ALIASES.items():
        if t in al:
            return key
    return None

def _choose_gen_answer(row: dict, gen_key: str) -> str | None:
    if gen_key == "gen1" and row.get("answer_gen1"):
        return str(row["answer_gen1"]).strip()
    if gen_key == "gen2" and row.get("answer_gen2"):
        return str(row["answer_gen2"]).strip()
    if gen_key == "gen3" and row.get("answer_gen3"):
        return str(row["answer_gen3"]).strip()
    return None

def _render_option_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    ans = (payload.get("answer") or payload.get("antwoord") or "").strip()
    rec = (payload.get("recommendation") or payload.get("aanbeveling") or "").strip()
    if ans:
        return ans + (("\n\nAanbeveling: " + rec) if rec else "")
    lines: List[str] = []
    for k, v in payload.items():
        if v in (None, "", []): continue
        key = str(k).replace("_", " ").strip().capitalize()
        if isinstance(v, list):
            items = "\n".join([f"  • {str(x)}" for x in v if str(x).strip()])
            lines.append(f"{key}:\n{items}")
        elif isinstance(v, dict):
            parts = [f"{kk}: {vv}" for kk, vv in v.items() if vv not in (None, "", [])]
            if parts:
                lines.append(f"{key}: " + "; ".join(parts))
        else:
            lines.append(f"{key}: {v}")
    return "\n".join(lines).strip()

def _build_answer_for_option(row: dict, option_label: str) -> str:
    payload = (row.get("options") or {}).get(option_label)
    if payload is None:
        return "Ik heb geen details gevonden voor deze keuze."
    intro = (row.get("answer") or row.get("antwoord") or "").strip()
    body = _render_option_payload(payload)
    return (intro + "\n\n" + body).strip() if intro else body

def _citations_for_row(row: dict) -> List[dict]:
    return [{"title": row.get("question") or "FAQ", "source": row.get("source") or "", "page": None}]

def _parse_extra_gen(extra: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(extra, dict): return None
    g = str(extra.get("gen", "")).strip().lower()
    if g in {"gen1","gen 1"}: return "gen1"
    if g in {"gen2","gen 2"}: return "gen2"
    if g in {"gen3","gen 3"}: return "gen3"
    return None

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "faq_rows": len(_FAQ)}

@app.post("/ingest")
def ingest(req: IngestRequest):
    res = ingest_path(req.path, req.source_type)
    _reload_faq()
    return {"reloaded": True, "faq_rows": len(_FAQ), **res}

# >>> UNIQUE définition de /train/correction (avec try/except)
@app.post("/train/correction")
def train_correction(req: CorrectionIn):
    try:
        return add_correction(req.question, req.answer, req.tags)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/train/correction error: {type(e).__name__}: {e}")

@app.post("/feedback")
def feedback(req: FeedbackIn):
    return save_feedback(req.dict())


@app.get("/debug/imports")
def debug_imports():
    out = {}
    try:
        import importlib.metadata as im
        out["chromadb"] = im.version("chromadb")
    except Exception as e:
        out["chromadb_error"] = str(e)

    try:
        import sqlite3
        out["sqlite3_version"] = getattr(sqlite3, "sqlite_version", "unknown")
    except Exception as e:
        out["sqlite3_error"] = str(e)

    try:
        import sys
        out["python"] = sys.version
        out["sys_path_sample"] = sys.path[:5]
    except Exception:  # pragma: no cover - diagnostics only
        pass
    try:
        out["vectorstore"] = vectorstore_status()
    except Exception as e:  # pragma: no cover - diagnostics only
        out["vectorstore_error"] = str(e)
    return out

# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    q = (req.query or "").strip()
    extra = req.extra or {}
    clarify_ref = (extra.get("clarify_ref") or extra.get("context_question") or "").strip()

    # Corrections admin
    ans, cite, score = search_correction(q, k=1, threshold=CORRECTION_THRESHOLD)
    if ans:
        used = [] if not req.debug else [{"meta": {"source_type": "correction", "score": score}, "text": ""}]
        return {"answer": ans, "citations": [cite], "used_chunks": used}

    client_id = _client_id_from_request(request)

    # ----- 1) follow-up avec clarify_ref
    if clarify_ref:
        base_row = _find_row_by_ref(clarify_ref)
        if not base_row:
            return {"answer": "Ik kan je keuze niet aan de juiste vraag koppelen.", "citations": []}

        gen_key = _map_choice_to_genkey(q)
        if gen_key:
            chosen = _choose_gen_answer(base_row, gen_key)
            if chosen:
                return {"answer": chosen, "citations": _citations_for_row(base_row)}

        labels = _labels(base_row)
        if not labels:
            direct = (base_row.get("answer") or base_row.get("antwoord") or "").strip()
            if direct:
                return {"answer": direct, "citations": _citations_for_row(base_row)}
            return {"answer": "Geen antwoord gevonden.", "citations": []}

        label = _map_choice_to_key(q, labels)
        if not label:
            return {
                "answer": "Ik herken deze keuze niet. Kies één van: " + ", ".join(labels),
                "citations": _citations_for_row(base_row)
            }
        answer = _build_answer_for_option(base_row, label)
        return {"answer": answer, "citations": _citations_for_row(base_row)}

    # ----- 1b) follow-up SANS clarify_ref -> fallback via pending memory
    if _looks_like_followup_choice(q):
        pend = _pop_valid_pending(client_id)
        if pend:
            base_row = pend["row"]
            labels = pend["labels"]

            gen_key = _map_choice_to_genkey(q)
            if gen_key:
                chosen = _choose_gen_answer(base_row, gen_key)
                if chosen:
                    return {"answer": chosen, "citations": _citations_for_row(base_row)}

            if labels:
                label = _map_choice_to_key(q, labels) or get_close_matches(q, labels, n=1, cutoff=0.4)[0] if get_close_matches(q, labels, n=1, cutoff=0.4) else None
                if label:
                    answer = _build_answer_for_option(base_row, label)
                    return {"answer": answer, "citations": _citations_for_row(base_row)}

        return {
            "answer": "Ik heb nog even de context nodig: bij welke vraag hoort deze keuze? Kies opnieuw bij de vorige vraag, of stuur je keuze met de contextvraag mee.",
            "need_ref": True,
            "citations": []
        }

    # ----- 2) lookup direct dans l'index
    row = _find_row_by_question(q)
    if row:
        if row.get("follow_up"):
            labels = _labels(row)
            tips: List[str] = GEN_TIPS_NL if any("gen" in _normalize(k) for k in labels) else []
            _set_pending(_client_id_from_request(request), row, labels)
            return {
                "answer": row.get("followup_q") or row.get("follow_up_question") or "Kunt u een keuze maken?",
                "clarify": {"ref": row.get("question"), "options": labels, "tips": tips},
                "citations": _citations_for_row(row)
            }

        direct = (row.get("answer") or row.get("antwoord") or "").strip()
        if direct:
            extra_line = "\n\nBekijk video: " + str(row["video_url"]) if row.get("video_url") else ""
            return {"answer": direct + extra_line, "citations": _citations_for_row(row)}

    # ----- 3) RAG fallback
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
        options = sorted(list(found & {"gen1", "gen2", "gen3"})) or ["gen1", "gen 2"]
        fake_row = {"question": q, "options": {o: {} for o in options}}
        _set_pending(client_id, fake_row, options)
        return {
            "answer": "Hebt u een Gen 1 of een Gen 2 apparaat?",
            "clarify": {"ref": q, "options": options, "tips": GEN_TIPS_NL},
            "citations": [],
        }

    if not docs:
        return {
            "answer": "Het lijkt erop dat er geen specifieke context beschikbaar is om je vraag te beantwoorden. Kun je meer details geven over wat je precies wilt weten?",
            "citations": [],
        }

    answer, citations = generate_answer(q, docs)
    return {"answer": answer, "citations": citations}
