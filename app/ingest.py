# app/ingest.py
import os
import re
import json
import unicodedata
from typing import List, Tuple, Any, Dict

import pandas as pd
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from docx import Document as DocxDocument
from pypdf import PdfReader

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

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

# ---------- Utils ----------
def _norm(s: Any) -> str:
    """Normalise (lower, sans accents, espaces compressés, NBSP=>espace)."""
    s = str(s or "").replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
    low = {_norm(c): c for c in df.columns}
    for a in aliases:
        na = _norm(a)
        if na in low:
            return low[na]
    return ""

def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = _norm(v)
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}

# -- détection surlignage (Antwoord)
def _highlight_mask_for_answer(path: str, sheet_name: str | None, answer_col_letter: str) -> set[int]:
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name] if (sheet_name and sheet_name in wb.sheetnames) else wb.active
    hi = set()
    col_idx = ord(answer_col_letter.upper()) - ord("A")  # 0-based
    for i, row in enumerate(ws.iter_rows(min_row=2), start=0):  # 0-based côté pandas (en excluant header)
        cell = row[col_idx]
        fill = cell.fill
        rgb = getattr(getattr(fill, "start_color", None), "rgb", None)
        if fill and fill.fill_type and rgb and rgb not in ("00000000", "FFFFFFFF"):
            hi.add(i)
    return hi

# -- parsing des options "Gen 1:", "Gen 2:", "Wifipool:", "Benisol:" etc.
_LABEL_RX = re.compile(r"^\s*(gen\s*[123]|wifi\s*pool|wifipool|benisol)\s*[:\-]\s*(.*)$", re.IGNORECASE)

def _norm_label(label: str) -> str | None:
    t = _norm(label)
    if t.startswith("gen 1") or t.startswith("gen1"):
        return "gen1"
    if t.startswith("gen 2") or t.startswith("gen2"):
        return "gen2"
    if t.startswith("gen 3") or t.startswith("gen3"):
        return "gen3"
    if t.startswith("wifipool") or t.startswith("wifi pool"):
        return "wifipool"
    if t.startswith("benisol"):
        return "benisol"
    return None

def _split_options_block(s: str) -> tuple[str | None, str]:
    """
    Si s commence par 'Gen 1:' / 'Wifipool:' etc., renvoie (clé normalisée, contenu_sans_entête).
    Sinon (None, s).
    """
    if not s:
        return None, s
    m = _LABEL_RX.match(s)
    if not m:
        return None, s
    key = _norm_label(m.group(1))
    rest = m.group(2).strip()
    return key, rest

# ---------- Ingestion dossier (txt/pdf/docx/html) ----------
def ingest_folder(root: str, source_type: str = "mixed"):
    texts, metas = [], []
    file_count = 0

    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            path = os.path.join(dirpath, fname)
            ext = fname.lower().rsplit(".", 1)[-1]
            title = os.path.splitext(fname)[0]

            if ext == "pdf":
                pages = _load_pdf_pages(path)
                any_page = False
                for page_text, page_no in pages:
                    if not page_text.strip():
                        continue
                    any_page = True
                    chunks = [c for c in splitter.split_text(page_text) if c.strip()]
                    texts.extend(chunks)
                    metas.extend(
                        [{"source": path, "title": title, "source_type": source_type, "page": page_no}]
                        * len(chunks)
                    )
                if any_page:
                    file_count += 1
                continue

            if ext == "docx":
                text = _load_docx(path)
            elif ext in ("html", "htm"):
                text = _load_html(path)
            elif ext in ("txt", "md"):
                text = _load_txt(path)
            else:
                continue

            if not text.strip():
                continue

            chunks = [c for c in splitter.split_text(text) if c.strip()]
            texts.extend(chunks)
            metas.extend([{"source": path, "title": title, "source_type": source_type}] * len(chunks))
            file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Ingestion Excel (FAQ) ----------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Lit un Excel de FAQ. Pose une sous-question seulement quand la cellule Antwoord
    de la ligne est surlignée (jaune) ET/OU quand on détecte des options
    (Gen1/Gen2, Wifipool/Benisol…) dans les lignes suivantes.
    Les options suivantes (où la colonne 'Vraag' est vide) sont rattachées à la
    question jaune.
    """
    if not os.path.exists(path):
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "file not found"}

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

    # Colonnes
    c_q    = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a    = _col_lookup(df, "Antwoord", "Answer")
    c_cat  = _col_lookup(df, "Categorie", "Category")
    c_photo= _col_lookup(df, "Foto", "Photo")
    c_video= _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    # masque surlignage pour 'Antwoord'
    try:
        a_pos = list(df.columns).index(c_a)
        a_letter = get_column_letter(a_pos + 1)
    except Exception:
        a_letter = "C"
    highlighted = _highlight_mask_for_answer(path, chosen_sheet, a_letter)

    texts: List[str] = []
    metas: List[Dict[str, Any]] = []
    index_rows: List[Dict[str, Any]] = []

    i = 0
    n = len(df)
    while i < n:
        row = df.iloc[i]
        vraag = str(row.get(c_q, "")).strip()
        antw  = str(row.get(c_a, "")).strip()

        if not vraag and not antw:
            i += 1
            continue

        category = str(row.get(c_cat, "")).strip() if c_cat else ""
        photo    = str(row.get(c_photo, "")).strip() if c_photo else ""
        video    = str(row.get(c_video, "")).strip() if c_video else ""

        # Détection d'un bloc "question jaune" + collecte des options sur les lignes suivantes
        is_highlight = (i in highlighted)
        branches: Dict[str, str] = {}
        select_param = None  # 'gen' | 'device' | 'choice'
        options_list: List[str] = []

        if vraag and antw:
            # si la réponse (cellule Antwoord) est une *question* jaune => on regarde les lignes suivantes
            if is_highlight:
                j = i + 1
                while j < n:
                    row_next = df.iloc[j]
                    # stop dès qu'une nouvelle 'Vraag' réapparait
                    if str(row_next.get(c_q, "")).strip():
                        break
                    ans_next = str(row_next.get(c_a, "")).strip()
                    if not ans_next:
                        j += 1
                        continue
                    key, content = _split_options_block(ans_next)
                    if key:
                        branches[key] = content
                    j += 1

                if branches:
                    keys = set(branches.keys())
                    if any(k.startswith("gen") for k in keys):
                        select_param = "gen"
                    elif keys <= {"wifipool", "benisol"} or "wifipool" in keys or "benisol" in keys:
                        select_param = "device"
                    else:
                        select_param = "choice"
                    options_list = sorted(list(keys))

        # texte pour chroma (on envoie la paire Q/A brute; si c’est une sous-question,
        # le LLM ne s’en sert pas directement — côté /chat on utilise l’index JSON)
        if vraag and antw:
            text = f"Vraag: {vraag}\nAntwoord: {antw}"
            title = (vraag[:80] + "…") if len(vraag) > 80 else vraag
            metas.append({
                "source": path,
                "title": title,
                "source_type": source_type,
                "categorie": category or "",
                "video_url": video or "",
                "photo": photo or "",
                "sheet": chosen_sheet or "",
            })
            texts.append(text)

            # index JSON enrichi
            index_row = {
                "question": vraag,
                "answer": antw,                # utile si pas de sous-question
                "category": category,
                "video_url": video or None,
                "photo": photo or None,
                "source": path,
                "sheet": chosen_sheet,
                # champs pour sous-question
                "ask_select": bool(is_highlight and (branches or antw)),  # jaune => on considère sous-question
                "followup_q": antw if is_highlight else None,
                "select_param": select_param,
                "options": options_list,
                "branches": branches,          # { key -> texte }
            }
            index_rows.append(index_row)

        i += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows"}

    # Sauvegardes
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
            metas.extend([{"source": path, "title": title, "source_type": source_type, "page": page_no}] * len(chunks))
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
