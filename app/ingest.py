# app/ingest.py
import os
import json
import re
import unicodedata
from typing import List, Tuple, Any, Dict

import pandas as pd
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from docx import Document as DocxDocument
from pypdf import PdfReader

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    MAX_CHUNK_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    COLLECTION_NAME,
    STORE_DIR,
)

# ---------- Loaders texte classiques ----------
def _load_pdf_pages(path: str) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    try:
        reader = PdfReader(path)
        for i, p in enumerate(reader.pages, start=1):
            out.append(((p.extract_text() or "").strip(), i))
    except Exception:
        pass
    return out

def _load_docx(path: str) -> str:
    try:
        doc = DocxDocument(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return ""

def _load_html(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return md(str(soup))

def _load_txt(path: str) -> str:
    return open(path, "r", encoding="utf-8", errors="ignore").read()

# ---------- Splitter ----------
splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_TOKENS,
    chunk_overlap=CHUNK_OVERLAP_TOKENS,
    separators=["\n\n", "\n", ". ", " "],
)

# ---------- Vector store ----------
def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

# ---------- Helpers colonnes ----------
def _norm_name(s: Any) -> str:
    """Normalise un nom de colonne: lower, trim, retire accents & NBSP, compresse espaces."""
    s = str(s).replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    return s.strip().lower()

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
    """Retourne le nom de colonne réel (case-sensitive) à partir d'aliases tolérants."""
    low = {_norm_name(c): c for c in df.columns}
    for a in aliases:
        key = _norm_name(a)
        if key in low:
            return low[key]
    return ""

def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}

# ---------- Détection de la sous-question (ligne jaune) ----------
def _extract_followup_question(answer_text: str) -> str:
    """
    Renvoie la première ligne courte qui finit par '?'
    (typiquement la sous-question en tête de 'Antwoord').
    """
    if not answer_text:
        return ""
    for line in answer_text.splitlines():
        s = line.strip()
        if s.endswith("?") and 3 <= len(s) <= 220:
            return s
        # on s'arrête très vite : la question jaune est tout en haut
        if s and len(s) > 220:
            break
    return ""

# ---------- Découpe des branches ----------
# Variantes "gen 1" / "gen2" / "voor gen 1 apparaten" …
_GEN1_MARK = re.compile(r"(^|\n)\s*(voor\s*)?gen[\s\-]*1[^:\n]*[:\-]?\s*", re.IGNORECASE)
_GEN2_MARK = re.compile(r"(^|\n)\s*(voor\s*)?gen[\s\-]*2[^:\n]*[:\-]?\s*", re.IGNORECASE)
_GEN3_MARK = re.compile(r"(^|\n)\s*(voor\s*)?gen[\s\-]*3[^:\n]*[:\-]?\s*", re.IGNORECASE)

# Variantes "Wifipool :" / "Benisol :" (on tolère les espaces et tirets)
_WIFIPOOL_MARK = re.compile(r"(^|\n)\s*wifipool\s*[:\-]\s*", re.IGNORECASE)
_BENISOL_MARK  = re.compile(r"(^|\n)\s*benisol\s*[:\-]\s*",  re.IGNORECASE)

def _slice_by_markers(text: str, markers: List[Tuple[str, re.Pattern]]) -> Dict[str, str]:
    """
    Découpe `text` selon une liste de marqueurs [(clé, regex), ...].
    Retourne {clé: bloc}.
    """
    spans = []
    for key, rgx in markers:
        m = list(rgx.finditer(text))
        if m:
            # on prend le 1er match pour chaque clé
            spans.append((key, m[0].start(), m[0].end()))
    if not spans:
        return {}

    # ordonner par position
    spans.sort(key=lambda t: t[1])

    out: Dict[str, str] = {}
    for i, (key, s, e) in enumerate(spans):
        end = spans[i + 1][1] if i + 1 < len(spans) else len(text)
        out[key] = text[e:end].strip()
    return out

def _detect_branches(answer_text: str, followup_q: str) -> Dict[str, Any]:
    """
    Détecte le type de branche et renvoie un objet:
      {
        "type": "gen" | "device" | None,
        "options": ["gen1","gen2"] / ["wifipool","benisol"],
        "labels":  {"gen1":"GEN1", ...},
        "answers": {"gen1": "...", "gen2": "..."}
      }
    """
    out = {"type": None, "options": [], "labels": {}, "answers": {}}

    # Heuristique 1 : question qui contient 'gen' -> branche gen
    if re.search(r"\bgen\s*[123]\b", followup_q, re.IGNORECASE):
        blocks = _slice_by_markers(
            answer_text,
            [
                ("gen1", _GEN1_MARK),
                ("gen2", _GEN2_MARK),
                ("gen3", _GEN3_MARK),
            ],
        )
        # on ne garde que ceux présents
        options = [k for k in ["gen1", "gen2", "gen3"] if k in blocks]
        if options:
            out["type"] = "gen"
            out["options"] = options
            out["labels"] = {k: k.upper().replace("GEN", "GEN ") for k in options}  # "gen1" -> "GEN 1"
            out["answers"] = blocks
            return out

    # Heuristique 2 : question qui mentionne wifipool/benisol OU texte structuré en Wifipool: / Benisol:
    if ("wifipool" in followup_q.lower()) or ("benisol" in followup_q.lower()) \
       or (_WIFIPOOL_MARK.search(answer_text) and _BENISOL_MARK.search(answer_text)):
        blocks = _slice_by_markers(
            answer_text,
            [
                ("wifipool", _WIFIPOOL_MARK),
                ("benisol", _BENISOL_MARK),
            ],
        )
        options = [k for k in ["wifipool", "benisol"] if k in blocks]
        if options:
            out["type"] = "device"
            out["options"] = options
            out["labels"] = {"wifipool": "WIFIPOOL", "benisol": "BENISOL"}
            out["answers"] = blocks
            return out

    return out

# ---------- Ingestion Excel (FAQ) ----------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Lis l'Excel et :
      - choisit la feuille contenant 'Vraag'/'Antwoord',
      - écrit store/faq_index.json,
      - envoie les lignes dans Chroma (métadonnées scalaires).
    Colonnes acceptées :
      * 'Categorie'
      * 'Vraag' / 'Question'
      * 'Antwoord' / 'Answer'
      * 'Foto'
      * 'Filmpje' / 'Video' / 'Video_URL'
      * 'Gen 1' / 'Gen1' / 'Gen 2' / 'Gen2' / 'Gen 3' / 'Gen3' (coches facultatives)
      * autres colonnes cochées 'x' => tags
    """
    if not os.path.exists(path):
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "file not found"}

    # 1) Choix de la feuille
    xls = pd.ExcelFile(path, engine="openpyxl")
    chosen_df, chosen_sheet = None, None
    for sheet in xls.sheet_names:
        df_try = xls.parse(sheet).fillna("")
        c_q_try = _col_lookup(df_try, "Vraag", "Question", "Vragen")
        c_a_try = _col_lookup(df_try, "Antwoord", "Answer")
        if c_q_try and c_a_try:
            chosen_df, chosen_sheet = df_try, sheet
            break

    if chosen_df is None:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "kolommen 'Vraag'/'Antwoord' ontbreken"}

    df = chosen_df

    # 2) Résolution colonnes
    c_q     = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a     = _col_lookup(df, "Antwoord", "Answer")
    c_cat   = _col_lookup(df, "Categorie", "Category")
    c_photo = _col_lookup(df, "Foto", "Photo")
    c_video = _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    # colonnes 'gen*' éventuellement cochées, converties en CSV "gen1,gen2"
    gen_cols_real = [c for c in df.columns if "gen" in _norm_name(c)]

    texts, metas, index_rows = [], [], []

    # 3) Itération lignes
    for _, row in df.iterrows():
        vraag = str(row.get(c_q, "")).strip()
        antw  = str(row.get(c_a, "")).strip()
        if not vraag or not antw:
            continue

        category = str(row.get(c_cat, "")).strip() if c_cat else ""
        photo    = str(row.get(c_photo, "")).strip() if c_photo else ""
        video    = str(row.get(c_video, "")).strip() if c_video else ""

        # Gens cochés (optionnel, pour filtrage RAG)
        gens_list: List[str] = []
        for gc in gen_cols_real:
            val = row.get(gc)
            if _boolish(val):
                l = _norm_name(gc)
                if "gen 1" in l or l == "gen1":
                    gens_list.append("gen1")
                elif "gen 2" in l or l == "gen2":
                    gens_list.append("gen2")
                elif "gen 3" in l or l == "gen3":
                    gens_list.append("gen3")

        # ---- Détection de la sous-question (ligne jaune) et des branches
        followup_q = _extract_followup_question(antw)
        # On retire la ligne question jaune du corps de réponse "antw_body"
        antw_body = antw
        if followup_q and antw.startswith(followup_q):
            antw_body = antw[len(followup_q):].lstrip()

        branches = _detect_branches(antw_body, followup_q)
        ask_gen = bool(branches.get("type"))  # True si on a un type ET des options/answers

        # ---- Préparation du JSON d'index
        # base_answer: si pas de branche, on garde la réponse complète
        base_answer = "" if ask_gen else antw_body

        index_row = {
            "question": vraag,
            "answer": base_answer,
            "category": category,
            "gens": gens_list,                 # LISTE dans l'index JSON
            "video_url": video or None,
            "photo": photo or None,
            "tags": [],                        # rempli juste après
            "ask_gen": ask_gen,
            "followup_q": followup_q or None,
            "followup_type": branches.get("type"),
            "branch_options": branches.get("options") or None,
            "branch_labels": branches.get("labels") or None,
            "branch_answers": branches.get("answers") or None,
            "source": path,
            "sheet": chosen_sheet,
        }

        # Tags génériques = colonnes cochées 'x', hors bases & hors gen*
        base_cols = {c_q, c_a, c_cat, c_photo, c_video}
        tags_list: List[str] = []
        for col in df.columns:
            cname = str(col)
            if cname in base_cols:
                continue
            if "gen" in _norm_name(cname):
                continue
            v = row.get(col)
            if _boolish(v):
                tags_list.append(cname)
        index_row["tags"] = tags_list

        index_rows.append(index_row)

        # ---- Données envoyées à Chroma (métadonnées **scalaires** seulement)
        text = f"Vraag: {vraag}\nAntwoord: {antw_body if antw_body else antw}"
        gens_csv = ",".join(gens_list) if gens_list else ""
        tags_csv = ",".join(tags_list) if tags_list else ""
        title = (vraag[:80] + "…") if len(vraag) > 80 else vraag

        metas.append({
            "source": path,
            "title": title,
            "source_type": source_type,
            "categorie": category or "",
            "gens": gens_csv,                     # CSV pour éviter 'complex metadata'
            "video_url": video or "",
            "photo": photo or "",
            "tags": tags_csv,                     # CSV
            "sheet": chosen_sheet or "",
            # Indications utiles pour le /chat (bool + texte court), scalaires :
            "ask_gen": bool(ask_gen),
            "followup_q": (followup_q or ""),
        })
        texts.append(text)

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows"}

    # 4) Sauvegardes
    os.makedirs(STORE_DIR, exist_ok=True)
    index_path = os.path.join(STORE_DIR, "faq_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_rows, f, ensure_ascii=False, indent=2)

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {
        "indexed_files": 1,
        "indexed_chunks": len(texts),
        "sheet_used": chosen_sheet,
        "wrote_index": index_path,
    }

# ---------- Ingestion universelle (fichier ou dossier) ----------
def ingest_path(path: str, source_type: str = "mixed"):
    if not path:
        return {"indexed_files": 0, "indexed_chunks": 0}

    if os.path.isdir(path):
        return ingest_folder(path, source_type)

    ext = path.lower().rsplit(".", 1)[-1]
    if ext in {"xlsx", "xls"}:
        return ingest_excel(path, source_type="faq")

    texts, metas = [], []
    title = os.path.splitext(os.path.basename(path))[0]
    if ext == "pdf":
        for page_text, page_no in _load_pdf_pages(path):
            if not page_text.strip():
                continue
            chunks = [c for c in splitter.split_text(page_text) if c.strip()]
            texts.extend(chunks)
            metas.extend(
                [{"source": path, "title": title, "source_type": source_type, "page": page_no}]
                * len(chunks)
            )
    elif ext == "docx":
        text = _load_docx(path)
        chunks = [c for c in splitter.split_text(text) if c.strip()]
        texts.extend(chunks)
        metas.extend([{"source": path, "title": title, "source_type": source_type}] * len(chunks))
    elif ext in {"html", "htm"}:
        text = _load_html(path)
        chunks = [c for c in splitter.split_text(text) if c.strip()]
        texts.extend(chunks)
        metas.extend([{"source": path, "title": title, "source_type": source_type}] * len(chunks))
    elif ext in {"txt", "md"}:
        text = _load_txt(path)
        chunks = [c for c in splitter.split_text(text) if c.strip()]
        texts.extend(chunks)
        metas.extend([{"source": path, "title": title, "source_type": source_type}] * len(chunks))

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": 1, "indexed_chunks": len(texts)}
