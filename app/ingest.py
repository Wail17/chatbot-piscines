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

# --------------------------- regex GEN ---------------------------
_GEN_WORD = re.compile(r"\bgen\s*[123]?\b", re.IGNORECASE)
_GEN1_RX  = re.compile(r"^\s*gen\s*1\s*[:\-–]?\s*(.*)$", re.IGNORECASE)
_GEN2_RX  = re.compile(r"^\s*gen\s*2\s*[:\-–]?\s*(.*)$", re.IGNORECASE)

# ---------------------- Loaders texte ----------------------------
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

# ------------------------- Splitter ------------------------------
splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_TOKENS,
    chunk_overlap=CHUNK_OVERLAP_TOKENS,
    separators=["\n\n", "\n", ". ", " "],
)

# ---------------------- Vector store ----------------------------
def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

# ---------------------- Helpers Excel ---------------------------
def _norm_name(s: Any) -> str:
    """normalise noms de colonnes: lower + sans accents + espaces compressés"""
    s = str(s).replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    return s.strip().lower()

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
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

def _extract_followup_question(answer_text: str) -> str:
    """
    retourne la première petite ligne finissant par '?'
    (typiquement 'Is je Wifi apparaat een type Gen 1 of Gen 2?')
    """
    if not answer_text:
        return ""
    for line in answer_text.strip().splitlines()[:3]:
        s = line.strip()
        if s.endswith("?") and 3 <= len(s) <= 200:
            return s
    return ""

# ---------------------- Ingestion dossier -----------------------
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

# ---------------------- Ingestion Excel -------------------------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Lit l'Excel et :
      - choisit la première feuille contenant 'Vraag' & 'Antwoord'
      - écrit store/faq_index.json (lookup direct)
      - envoie les lignes dans Chroma (métadonnées scalaires)
      + extrait les réponses spécifiques Gen 1 / Gen 2 situées juste en dessous
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
    c_q     = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a     = _col_lookup(df, "Antwoord", "Answer")
    c_cat   = _col_lookup(df, "Categorie", "Category")
    c_photo = _col_lookup(df, "Foto", "Photo")
    c_video = _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    # Colonnes GEN cochées
    gen_cols_real = [c for c in df.columns if "gen" in _norm_name(c)]

    texts: List[str] = []
    metas: List[dict] = []
    index_rows: List[dict] = []

    # Boucle par index (permet look-ahead)
    for ridx in range(len(df)):
        row = df.iloc[ridx]

        vraag = str(row.get(c_q, "")).strip()
        antw  = str(row.get(c_a, "")).strip()
        if not vraag or not antw:
            continue

        category = str(row.get(c_cat, "")).strip() if c_cat else ""
        photo    = str(row.get(c_photo, "")).strip() if c_photo else ""
        video    = str(row.get(c_video, "")).strip() if c_video else ""

        # Gens cochés
        gens_list: List[str] = []
        for gc in gen_cols_real:
            if _boolish(row.get(gc)):
                l = _norm_name(gc)
                if "gen 1" in l or l == "gen1":
                    gens_list.append("gen1")
                elif "gen 2" in l or l == "gen2":
                    gens_list.append("gen2")
                elif "gen 3" in l or l == "gen3":
                    gens_list.append("gen3")

        # Sous-question & besoin de GEN
        followup_q = _extract_followup_question(antw)
        mentions_gen = bool(_GEN_WORD.search(followup_q))
        ask_gen = bool(mentions_gen or len(gens_list) > 0)

        # Chercher réponses Gen1/Gen2 dans les lignes qui suivent (même colonne 'Antwoord')
        ans_g1, ans_g2 = "", ""
        if ask_gen:
            max_j = min(ridx + 6, len(df))  # on scanne jusqu'à 5 lignes en dessous
            for j in range(ridx + 1, max_j):
                next_q = str(df.iloc[j].get(c_q, "")).strip() if c_q else ""
                if next_q:  # on stoppe à la prochaine vraie question
                    break
                nxt_a = str(df.iloc[j].get(c_a, "")).strip()
                if not nxt_a:
                    continue
                m1 = _GEN1_RX.match(nxt_a)
                m2 = _GEN2_RX.match(nxt_a)
                if m1 and not ans_g1:
                    ans_g1 = (m1.group(1).strip() or nxt_a)
                elif m2 and not ans_g2:
                    ans_g2 = (m2.group(1).strip() or nxt_a)

        # Texte indexé (on ajoute les variantes si trouvées)
        text = f"Vraag: {vraag}\nAntwoord: {antw}"
        if ans_g1:
            text += f"\n\nAntwoord (Gen1): {ans_g1}"
        if ans_g2:
            text += f"\n\nAntwoord (Gen2): {ans_g2}"

        gens_csv = ",".join(gens_list) if gens_list else ""
        title = (vraag[:80] + "…") if len(vraag) > 80 else vraag

        metas.append({
            "source": path,
            "title": title,
            "source_type": source_type,
            "categorie": category or "",
            "gens": gens_csv,
            "video_url": video or "",
            "photo": photo or "",
            "tags": "",                  # on garde simple côté vecteur
            "sheet": chosen_sheet or "",
            "ask_gen": bool(ask_gen),
            "followup_q": followup_q or "",
        })
        texts.append(text)

        # Index JSON (utilisé par /chat)
        index_rows.append({
            "question": vraag,
            "answer": antw,
            "answer_gen1": ans_g1 or None,
            "answer_gen2": ans_g2 or None,
            "category": category,
            "gens": gens_list,
            "video_url": video or None,
            "photo": photo or None,
            "tags": [],
            "ask_gen": bool(ask_gen),
            "followup_q": followup_q or None,
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

    return {
        "indexed_files": 1,
        "indexed_chunks": len(texts),
        "sheet_used": chosen_sheet,
        "wrote_index": index_path,
    }

# ---------------- Ingestion universelle (fichier) -----------------
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
