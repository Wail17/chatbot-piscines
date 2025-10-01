# app/ingest.py
import os
import json
import re
import unicodedata
from typing import List, Tuple, Any

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

# ------------------------- load helpers -------------------------
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

# ------------------------- text split --------------------------
splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_TOKENS,
    chunk_overlap=CHUNK_OVERLAP_TOKENS,
    separators=["\n\n", "\n", ". ", " "],
)

# ------------------------- vector store ------------------------
def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

# ------------------------- utils -------------------------------
def _norm_name(s: Any) -> str:
    """lower + retire accents + compresse espaces + remplace NBSP."""
    s = str(s).replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    return s.strip().lower()

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
    low = {_norm_name(c): c for c in df.columns}
    for a in aliases:
        n = _norm_name(a)
        if n in low:
            return low[n]
    return ""

def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}

# question de tri ?
_GEN_PAT = re.compile(r"\bgen\s*[12]\b", re.IGNORECASE)
def _looks_like_gen_question(text: str) -> bool:
    t = _norm_name(text)
    if "?" not in text:
        # on tolère quand même (parfois pas de ?)
        pass
    if _GEN_PAT.search(t):
        return True
    # autres variantes : “gen1”, “gen 1 toestel”, …
    if "gen 1" in t or "gen1" in t or "gen 2" in t or "gen2" in t:
        return True
    return False

def _looks_like_type_question(text: str) -> bool:
    """Wifipool vs Benisol tri"""
    t = _norm_name(text)
    return ("benisol" in t) and ("wifi" in t or "wifipool" in t)

def _is_colored_fill(cell) -> bool:
    fill = getattr(cell, "fill", None)
    if not fill or not fill.fill_type:
        return False
    color = getattr(fill, "start_color", None)
    rgb = getattr(color, "rgb", None)
    # considère surligné tout ce qui n'est ni transparent, ni blanc
    return bool(rgb and rgb not in ("00000000", "FFFFFFFF"))

# ------------------------- core ingestion -----------------------
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
                    metas.extend([{
                        "source": path, "title": title, "source_type": source_type, "page": page_no
                    }] * len(chunks))
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
            metas.extend([{
                "source": path, "title": title, "source_type": source_type
            }] * len(chunks))
            file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

def ingest_excel(path: str, source_type: str = "faq"):
    if not os.path.exists(path):
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "file not found"}

    xls = pd.ExcelFile(path, engine="openpyxl")
    chosen_df, chosen_sheet = None, None
    for sheet in xls.sheet_names:
        df_try = xls.parse(sheet).fillna("")
        if _col_lookup(df_try, "Vraag", "Question", "Vragen") and _col_lookup(df_try, "Antwoord", "Answer"):
            chosen_df, chosen_sheet = df_try, sheet
            break
    if chosen_df is None:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "kolommen 'Vraag'/'Antwoord' ontbreken"}

    df = chosen_df
    c_q    = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a    = _col_lookup(df, "Antwoord", "Answer")
    c_cat  = _col_lookup(df, "Categorie", "Category")
    c_photo= _col_lookup(df, "Foto", "Photo")
    c_video= _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    # ---- masque de surlignage pour la colonne Antwoord ----
    highlighted_rows: set[int] = set()
    try:
        wb = load_workbook(path, data_only=True)
        ws = wb[chosen_sheet] if chosen_sheet in wb.sheetnames else wb.active
        a_pos = list(df.columns).index(c_a)  # 0-based
        a_letter = get_column_letter(a_pos + 1)
        col_idx0 = ord(a_letter.upper()) - ord("A")  # 0-based
        for i, row in enumerate(ws.iter_rows(min_row=2), start=0):
            cell = row[col_idx0]
            if _is_colored_fill(cell):
                highlighted_rows.add(i)
    except Exception:
        highlighted_rows = set()

    texts, metas, index_rows = [], [], []

    n = len(df)
    ridx = 0
    while ridx < n:
        vraag = str(df.iloc[ridx][c_q]).strip()
        antw  = str(df.iloc[ridx][c_a]).strip()
        if not vraag:
            ridx += 1
            continue

        category = str(df.iloc[ridx][c_cat]).strip() if c_cat else ""
        photo    = str(df.iloc[ridx][c_photo]).strip() if c_photo else ""
        video    = str(df.iloc[ridx][c_video]).strip() if c_video else ""

        is_hl = ridx in highlighted_rows
        tri_gen   = is_hl and _looks_like_gen_question(antw)
        tri_type  = is_hl and _looks_like_type_question(antw)

        if tri_gen or tri_type:
            # --- collecter les réponses enfants jusqu'à la prochaine question ---
            children: List[str] = []
            j = ridx + 1
            while j < n and not str(df.iloc[j][c_q]).strip():
                sub = str(df.iloc[j][c_a]).strip()
                if sub:
                    children.append(sub)
                j += 1

            # mapper enfants -> options
            d: dict[str, str] = {}
            for t in children:
                tl = _norm_name(t)
                if "gen 1" in tl or "gen1" in tl:
                    d["gen1"] = t
                elif "gen 2" in tl or "gen2" in tl:
                    d["gen2"] = t
                elif "benisol" in tl:
                    d["benisol"] = t
                elif "wifi" in tl or "wifipool" in tl:
                    d["wifipool"] = t

            # pousser dans Chroma des variantes trouvées
            for key, ans in d.items():
                texts.append(f"Vraag: {vraag}\nAntwoord: {ans}")
                metas.append({
                    "source": path,
                    "title": (vraag[:80] + "…") if len(vraag) > 80 else vraag,
                    "source_type": source_type,
                    "categorie": category or "",
                    "gens": key,                 # clé de filtrage (gen1/gen2/benisol/wifipool)
                    "video_url": video or "",
                    "photo": photo or "",
                    "tags": "",
                    "sheet": chosen_sheet or "",
                    "ask_gen": False,
                })

            # index: on garde la question de tri + drapeau ask_gen
            index_rows.append({
                "question": vraag,
                "answer": "",                   # pas de réponse directe
                "category": category,
                "gens": list(d.keys()),         # options disponibles
                "video_url": video or None,
                "photo": photo or None,
                "tags": [],
                "ask_gen": True,                # => /chat posera la question
                "followup_q": antw,             # texte jaune
                "source": path,
                "sheet": chosen_sheet,
            })

            # sauter aux lignes suivantes (déjà consommées par children)
            ridx = j
            continue

        # --- cas normal : réponse directe ---
        text = f"Vraag: {vraag}\nAntwoord: {antw}"
        title = (vraag[:80] + "…") if len(vraag) > 80 else vraag
        texts.append(text)
        metas.append({
            "source": path,
            "title": title,
            "source_type": source_type,
            "categorie": category or "",
            "gens": "",                       # pas de filtre
            "video_url": video or "",
            "photo": photo or "",
            "tags": "",
            "sheet": chosen_sheet or "",
            "ask_gen": False,
        })
        index_rows.append({
            "question": vraag,
            "answer": antw,
            "category": category,
            "gens": [],
            "video_url": video or None,
            "photo": photo or None,
            "tags": [],
            "ask_gen": False,
            "followup_q": None,
            "source": path,
            "sheet": chosen_sheet,
        })

        ridx += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows"}

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

# ------------------------- universal entry ----------------------
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
            metas.extend([{
                "source": path, "title": title, "source_type": source_type, "page": page_no
            }] * len(chunks))
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
