# app/ingest.py
import os
from typing import List, Tuple, Dict, Any

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
)

# ---------- Helpers loaders ----------
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

# ---------- Excel (FAQ) ----------
def _normalize_yes(x: Any) -> bool:
    if x is None:
        return False
    s = str(x).strip().lower()
    return s in {"x", "✓", "true", "1", "yes", "oui"}

def _ingest_excel(path: str, source_type: str = "faq") -> Dict[str, int]:
    """
    Lis un Excel avec colonnes du type:
      - Categorie / Category (facultatif)
      - Vraag ou Question
      - Antwoord ou Answer
      - Gen 1 / Gen1 / Gen 2 / Gen2 / Gen 3 (cases cochées)
    Chaque ligne devient un document, et on encode dans metadata['gens'] = ['gen1', 'gen2', ...]
    """
    df = pd.read_excel(path)

    # mapping colonnes possibles
    col_q = None
    for k in ["Vraag", "Question", "vraag", "question"]:
        if k in df.columns: col_q = k; break
    col_a = None
    for k in ["Antwoord", "Answer", "antwoord", "answer"]:
        if k in df.columns: col_a = k; break

    if not col_q or not col_a:
        return {"indexed_files": 0, "indexed_chunks": 0}

    col_cat = None
    for k in ["Categorie", "Category", "categorie", "category"]:
        if k in df.columns: col_cat = k; break

    gen_cols = {
        "gen1": [c for c in df.columns if c.strip().lower() in {"gen 1", "gen1"}],
        "gen2": [c for c in df.columns if c.strip().lower() in {"gen 2", "gen2"}],
        "gen3": [c for c in df.columns if c.strip().lower() in {"gen 3", "gen3"}],
    }

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
        separators=["\n\n", "\n", ". ", " "],
    )

    texts, metas = [], []
    file_count = 0

    for _, row in df.iterrows():
        q = str(row.get(col_q) or "").strip()
        a = str(row.get(col_a) or "").strip()
        if not q or not a:
            continue

        gens = []
        for g, cols in gen_cols.items():
            for c in cols:
                if _normalize_yes(row.get(c)):
                    gens.append(g); break

        title = (q[:70] + "…") if len(q) > 70 else q
        content = f"Q: {q}\n\nA: {a}"

        chunks = [c for c in splitter.split_text(content) if c.strip()]
        texts.extend(chunks)
        metas.extend(
            [{
                "source": path,
                "title": title,
                "source_type": source_type,
                "category": (row.get(col_cat) or None),
                "gens": gens,   # ex: ["gen1","gen2"]
            }] * len(chunks)
        )
        file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Ingestion fichiers/dossiers ----------
def ingest_folder(root: str, source_type: str = "mixed") -> Dict[str, int]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
        separators=["\n\n", "\n", ". ", " "],
    )

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
                        [{
                            "source": path,
                            "title": title,
                            "source_type": source_type,
                            "page": page_no,
                        }] * len(chunks)
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
                [{
                    "source": path,
                    "title": title,
                    "source_type": source_type,
                }] * len(chunks)
            )
            file_count += 1

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}

    vs = Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ---------- Dispatcher : chemin unique ----------
def ingest_path(path: str, source_type: str = "mixed") -> Dict[str, int]:
    if os.path.isdir(path):
        return ingest_folder(path, source_type)

    ext = path.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xls"):
        return _ingest_excel(path, source_type="faq")

    # Pour 1 fichier texte/PDF/docx unique : on réutilise ingest_folder sur son dossier
    parent = os.path.dirname(path) or "."
    return ingest_folder(parent, source_type)
