# app/ingest.py
import os
import json
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
                        [
                            {
                                "source": path,
                                "title": title,
                                "source_type": source_type,
                                "page": page_no,
                            }
                        ] * len(chunks)
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
                [
                    {
                        "source": path,
                        "title": title,
                        "source_type": source_type,
                    }
                ] * len(chunks)
            )
            file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Helpers ----------
def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
    """Retourne le nom de colonne réel (case-sensitive) à partir d'aliases insensibles à la casse."""
    low = {c.lower(): c for c in df.columns}
    for a in aliases:
        if a and a.lower() in low:
            return low[a.lower()]
    return ""

# ---------- Ingestion Excel (FAQ) ----------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Lit un Excel de FAQ et :
      - écrit un index JSON (store/faq_index.json) pour un lookup direct
      - envoie chaque ligne (question/réponse) dans Chroma avec métadonnées *scalaires*
    Colonnes tolérées (insensibles à la casse) :
      * 'Categorie'
      * 'Vraag' (obligatoire)
      * 'Antwoord' (obligatoire)
      * 'Foto' (optionnel)
      * 'Filmpje' / 'Video' / 'Video_URL' (optionnel)
      * Colonnes Gen : 'Gen 1' / 'Gen1' ; 'Gen 2'/ 'Gen2' ; 'Gen 3'/ 'Gen3'
      * Toute autre colonne cochée 'x' sera ajoutée dans 'tags'
    """
    if not os.path.exists(path):
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "file not found"}

    df = pd.read_excel(path, engine="openpyxl").fillna("")

    c_q = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a = _col_lookup(df, "Antwoord", "Answer")
    if not c_q or not c_a:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "kolommen 'Vraag'/'Antwoord' ontbreken"}

    c_cat   = _col_lookup(df, "Categorie", "Category")
    c_photo = _col_lookup(df, "Foto", "Photo")
    c_video = _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    gen_cols_real = [c for c in df.columns if "gen" in c.lower()]

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
                l = gc.strip().lower()
                if "gen 1" in l or "gen1" in l:
                    gens_list.append("gen1")
                elif "gen 2" in l or "gen2" in l:
                    gens_list.append("gen2")
                elif "gen 3" in l or "gen3" in l:
                    gens_list.append("gen3")

        # Tags (liste) = toutes les colonnes 'x' hors bases & hors gen*
        base_cols = {c_q, c_a, c_cat, c_photo, c_video}
        tags_list: List[str] = []
        for col in df.columns:
            cname = str(col)
            if cname in base_cols:
                continue
            if "gen" in cname.lower():
                continue
            v = row.get(col)
            if _boolish(v):
                tags_list.append(cname)

        # --- Texte pour la base vectorielle
        text = f"Vraag: {vraag}\nAntwoord: {antw}"

        # --- METADATA POUR CHROMA (scalaires uniquement)
        gens_csv = ",".join(gens_list) if gens_list else None
        tags_csv = ",".join(tags_list) if tags_list else None

        title = (vraag[:80] + "…") if len(vraag) > 80 else vraag
        meta = {
            "source": path,
            "title": title,
            "source_type": source_type,
            "categorie": category or None,
            "gens": gens_csv,            # <- string (CSV) pour Chroma
            "video_url": video or None,
            "photo": photo or None,
            "tags": tags_csv,            # <- string (CSV) pour Chroma
        }
        texts.append(text)
        metas.append(meta)

        # --- LIGNE D’INDEX JSON (on garde les LISTES ici)
        index_rows.append({
            "question": vraag,
            "answer": antw,
            "category": category,
            "gens": gens_list,          # <- liste complète pour /chat
            "video_url": video or None,
            "photo": photo or None,
            "tags": tags_list,          # <- liste complète pour /chat
            "source": path,
        })

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows"}

    # Écrire l'index JSON pour le lookup direct
    os.makedirs(STORE_DIR, exist_ok=True)
    index_path = os.path.join(STORE_DIR, "faq_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_rows, f, ensure_ascii=False, indent=2)

    # Ajouter dans Chroma
    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {
        "indexed_files": 1,
        "indexed_chunks": len(texts),
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

    # Fichier texte isolé
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
