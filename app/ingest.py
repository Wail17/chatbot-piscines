# app/ingest.py
import os
<<<<<<< HEAD
from typing import List, Tuple

=======
import re
from pathlib import Path
from typing import List, Tuple

import pandas as pd
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from docx import Document as DocxDocument
from pypdf import PdfReader

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
<<<<<<< HEAD
=======
# (si tu veux éviter l'avertissement de dépréciation, remplace la ligne au-dessus par:)
# from langchain_chroma import Chroma
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    MAX_CHUNK_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    COLLECTION_NAME,
)

<<<<<<< HEAD
# ---------- Loaders ----------
=======
# ---------- Utils ----------
def _norm_question(s: str) -> str:
    """Normalise une question pour correspondance exacte (q_key)."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
        separators=["\n\n", "\n", ". ", " "],
    )

def _new_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

# ---------- Loaders (PDF/DOCX/HTML/TXT) ----------
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
def _load_pdf_pages(path: str) -> List[Tuple[str, int]]:
    """Retourne [(texte_page, index_page), ...]"""
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

<<<<<<< HEAD
# ---------- Ingestion ----------
def ingest_folder(root: str, source_type: str = "mixed"):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
        separators=["\n\n", "\n", ". ", " "],
    )

=======
# ---------- Ingestion dossiers/fichiers texte ----------
def ingest_folder(root: str, source_type: str = "mixed"):
    splitter = _splitter()
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
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

<<<<<<< HEAD
    vs = Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {"indexed_files": file_count, "indexed_chunks": len(texts)}
=======
    vs = _new_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Ingestion Excel (FAQ + générations) ----------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Excel attendu (noms de colonnes tolérants) :
      - 'Categorie' | 'Vraag' | 'Antwoord' | ... | 'Gen 1' | 'Gen 2' | 'Gen 3'
    Les colonnes 'Gen' contiennent une croix : x, ✓, 1, true, yes...
    On crée des chunks depuis la réponse et on ajoute des métadonnées :
      - gens: ['gen1', 'gen2', ...]
      - q_key: question normalisée (pour match exact côté backend)
    """
    df = pd.read_excel(Path(path))

    def norm_col(name): return str(name).strip().lower()
    cols = {norm_col(c): c for c in df.columns}

    col_cat = cols.get("categorie") or cols.get("category") or list(df.columns)[0]
    col_q   = cols.get("vraag")     or cols.get("question") or list(df.columns)[1]
    col_a   = cols.get("antwoord")  or cols.get("answer")   or list(df.columns)[2]

    # colonnes génération
    gen_cols = [c for c in df.columns if norm_col(c).startswith("gen")]
    if not gen_cols:
        gen_cols = [c for c in df.columns if "gen" in norm_col(c)]

    splitter = _splitter()
    texts, metas = [], []
    file_count = 0

    for _, row in df.iterrows():
        q = str(row.get(col_q, "") or "").strip()
        a = str(row.get(col_a, "") or "").strip()
        if not a:
            continue

        gens = []
        for gc in gen_cols:
            val = str(row.get(gc, "") or "").strip().lower()
            if val in ("x", "✓", "1", "true", "yes"):
                gens.append(norm_col(gc).replace(" ", ""))  # "gen 1" -> "gen1"

        cat = str(row.get(col_cat, "") or "").strip() or "FAQ"
        title = q or cat or "FAQ"
        q_key = _norm_question(q)

        # On embedd uniquement la réponse (on garde la question en metadata/q_key)
        chunks = [c for c in splitter.split_text(a) if c.strip()]
        if not chunks:
            continue

        texts.extend(chunks)
        metas.extend(
            [
                {
                    "source": str(Path(path)),
                    "title": title,
                    "category": cat,
                    "source_type": source_type,
                    "gens": gens,       # << important pour le filtre Gen
                    "q_key": q_key,     # << pour match exact si besoin
                }
            ] * len(chunks)
        )
        file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = _new_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Entrée unique pratique ----------
def ingest_path(path: str, source_type: str = "mixed"):
    """
    Appelle la bonne ingestion selon le type :
      - dossier -> ingest_folder
      - .xlsx/.xls -> ingest_excel (source_type='faq' par défaut)
      - fichier texte -> ingère comme unitaire via ingest_folder sur son dossier
    """
    p = Path(path)
    if p.is_dir():
        return ingest_folder(str(p), source_type=source_type)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls"):
        # Par défaut, on tague l'Excel comme 'faq' (adaptable)
        return ingest_excel(str(p), source_type=(source_type or "faq"))
    # fallback: traite le fichier seul comme un petit dossier
    return ingest_folder(str(p.parent), source_type=source_type)
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
