# app/ingest.py
import os
import json
import unicodedata
from typing import List, Tuple, Any

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
            metas.extend(
                [{"source": path, "title": title, "source_type": source_type}] * len(chunks)
            )
            file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Helpers ----------
def _norm_name(s: Any) -> str:
    """Normalise un nom de colonne: lower, trim, retire accents & espaces insécables, compresse espaces."""
    s = str(s).replace("\u00a0", " ")  # NBSP -> espace normal
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())  # compresse espaces successifs
    return s.strip().lower()

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
    """Retourne le nom de colonne réel (case-sensitive) à partir d'aliases insensibles à la casse/espaces/accents."""
    low = {_norm_name(c): c for c in df.columns}
    for a in aliases:
        key = _norm_name(a)
        if key in low:
            return low[key]
    return ""

# ---------- Ingestion Excel (FAQ) ----------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Lis l'Excel et :
      - trouve la bonne feuille automatiquement,
      - écrit store/faq_index.json (lookup direct),
      - ajoute les lignes dans Chroma (métadonnées scalaires).
    Colonnes attendues (variantes acceptées) :
      * 'Categorie'
      * 'Vraag' / 'Question'
      * 'Antwoord' / 'Answer'
      * 'Foto' (optionnel)
      * 'Filmpje' / 'Video' / 'Video_URL' (optionnel)
      * 'Gen 1' / 'Gen1' ; 'Gen 2' / 'Gen2' ; 'Gen 3' / 'Gen3'
      * autres colonnes cochées 'x' => tags
    """
    if not os.path.exists(path):
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "file not found"}

    # Ouvre le classeur et essaie chaque feuille jusqu’à trouver Q/A
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
        # aucune feuille n’a les colonnes Q/A détectables
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "kolommen 'Vraag'/'Antwoord' ontbreken"}

    df = chosen_df

    c_q    = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a    = _col_lookup(df, "Antwoord", "Answer")
    c_cat  = _col_lookup(df, "Categorie", "Category")
    c_photo= _col_lookup(df, "Foto", "Photo")
    c_video= _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    # Colonnes Gen (toutes celles dont le nom normalisé contient 'gen')
    gen_cols_real = [c for c in df.columns if "gen" in _norm_name(c)]

    texts, metas = [], []
    index_rows = []

    for _, row in df.iterrows():
        vraag = str(row.get(c_q, "")).strip()
        antw  = str(row.get(c_a, "")).strip()
        if not vraag or not antw:
            continue

        category = str(row.get(c_cat, "")).strip() if c_cat else ""
        photo    = str(row.get(c_photo, "")).strip() if c_photo else ""
        video    = str(row.get(c_video, "")).strip() if c_video else ""

        # Gens (liste)
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

        # Tags (liste) = colonnes cochées 'x' hors bases & hors gen*
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

        # ---- Texte pour Chroma
        text = f"Vraag: {vraag}\nAntwoord: {antw}"

        # ---- Métadonnées scalaires (CSV) pour Chroma
        gens_csv = ",".join(gens_list) if gens_list else None
        tags_csv = ",".join(tags_list) if tags_list else None

        title = (vraag[:80] + "…") if len(vraag) > 80 else vraag
        metas.append({
            "source": path,
            "title": title,
            "source_type": source_type,
            "categorie": category or None,
            "gens": gens_csv,           # CSV
            "video_url": video or None,
            "photo": photo or None,
            "tags": tags_csv,           # CSV
            "sheet": chosen_sheet,
        })
        texts.append(text)

        # ---- Index JSON (on garde les LISTES)
        index_rows.append({
            "question": vraag,
            "answer": antw,
            "category": category,
            "gens": gens_list,
            "video_url": video or None,
            "photo": photo or None,
            "tags": tags_list,
            "source": path,
            "sheet": chosen_sheet,
        })

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows"}

    os.makedirs(STORE_DIR, exist_ok=True)
    index_path = os.path.join(STORE_DIR, "faq_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_rows, f, ensure_ascii=False, indent=2)

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {"indexed_files": 1, "indexed_chunks": len(texts), "sheet_used": chosen_sheet, "wrote_index": index_path}

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

def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}
