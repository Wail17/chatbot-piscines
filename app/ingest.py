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

# ---------- Helpers ----------
def _norm_name(s: Any) -> str:
    """lower + retire accents + NBSP + compresse espaces"""
    s = str(s).replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    return s.strip().lower()

def _col_lookup(df: pd.DataFrame, *aliases) -> str:
    low = {_norm_name(c): c for c in df.columns}
    for a in aliases:
        k = _norm_name(a)
        if k in low:
            return low[k]
    return ""

def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}

# --- Parsing des branches dans le texte d'Antwoord (après regroupement) ---
BRANCH_PATS = [
    (re.compile(r"^\s*(wifipool|wifi\s*pool)\s*:\s*(.*)$", re.IGNORECASE), "wifipool"),
    (re.compile(r"^\s*(benisol)\s*:\s*(.*)$", re.IGNORECASE), "benisol"),
    (re.compile(r"^\s*gen\s*1\s*:\s*(.*)$", re.IGNORECASE), "gen1"),
    (re.compile(r"^\s*gen\s*2\s*:\s*(.*)$", re.IGNORECASE), "gen2"),
    (re.compile(r"^\s*gen\s*3\s*:\s*(.*)$", re.IGNORECASE), "gen3"),
]

def _extract_branches(full_answer: str) -> Dict[str, str]:
    """
    Cherche des en-têtes de type 'Wifipool: ...', 'Benisol: ...', 'Gen 1: ...'
    et renvoie { 'wifipool': '...', 'benisol': '...' } ou { 'gen1': '...', 'gen2': '...' }.
    """
    lines = [l for l in (full_answer or "").splitlines()]
    branches: Dict[str, List[str]] = {}
    current_key = None

    def flush():
        nonlocal current_key
        if current_key is not None:
            txt = "\n".join(branches[current_key]).strip()
            branches[current_key] = [txt] if txt else []
        current_key = None

    for ln in lines:
        matched = False
        for pat, key in BRANCH_PATS:
            m = pat.match(ln)
            if m:
                # nouveau header -> flush précédent
                if current_key is not None:
                    flush()
                current_key = key
                branches.setdefault(current_key, [])
                # premier bout de la ligne après "Label:"
                tail = m.group(1) if key in ("wifipool", "benisol") else m.group(0)
                # Dans nos regex wifipool/benisol, group(2) contient le début de contenu
                if key in ("wifipool", "benisol"):
                    branches[current_key].append(m.group(2).strip())
                else:
                    # pour genX, on a capturé tout après 'Gen X:' via la regex correspondante
                    branches[current_key].append(m.group(1).strip())
                matched = True
                break
        if not matched:
            if current_key is not None:
                branches[current_key].append(ln)

    if current_key is not None:
        flush()

    # Nettoyage final
    out = {k: "\n".join(v).strip() for k, v in branches.items() if v and "\n".join(v).strip()}
    return out

def _first_line_question(full_answer: str) -> str:
    """
    Si la première ligne du bloc Answer finit par '?', on la prend
    comme follow-up (question à poser), et on la retirera du contenu indexé.
    """
    if not full_answer:
        return ""
    lines = [l.strip() for l in full_answer.splitlines() if l.strip()]
    if not lines:
        return ""
    first = lines[0]
    return first if first.endswith("?") else ""

def _strip_first_line(full_answer: str) -> str:
    lines = [l for l in (full_answer or "").splitlines()]
    if not lines:
        return ""
    if lines[0].strip().endswith("?"):
        return "\n".join(lines[1:]).strip()
    return full_answer.strip()

# ---------- Ingestion Excel (FAQ) ----------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    - Regroupe les lignes 'Antwoord' qui suivent une 'Vraag' (jusqu'à la prochaine Vraag)
    - Extrait followup_q (si la 1ère ligne de réponse finit par '?')
    - Extrait des branches (Wifipool/Benisol ou Gen1/Gen2/Gen3) si présentes
    - Indexe tout (et écrit aussi store/faq_index.json pour lookup direct)
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
    c_q    = _col_lookup(df, "Vraag", "Question", "Vragen")
    c_a    = _col_lookup(df, "Antwoord", "Answer")
    c_cat  = _col_lookup(df, "Categorie", "Category")
    c_foto = _col_lookup(df, "Foto", "Photo")
    c_vid  = _col_lookup(df, "Filmpje", "Video", "Video_URL", "Video Url")

    n = len(df)
    texts, metas, index_rows = [], [], []

    i = 0
    while i < n:
        vraag = str(df.at[i, c_q]).strip() if c_q else ""
        if not vraag:
            i += 1
            continue

        category = str(df.at[i, c_cat]).strip() if c_cat else ""
        photo    = str(df.at[i, c_foto]).strip() if c_foto else ""
        video    = str(df.at[i, c_vid]).strip() if c_vid else ""

        # Regrouper Antwoord de cette ligne + suivantes tant que la colonne Vraag est vide
        parts = []
        a0 = str(df.at[i, c_a]).strip() if c_a else ""
        if a0:
            parts.append(a0)
        j = i + 1
        while j < n:
            next_q = str(df.at[j, c_q]).strip() if c_q else ""
            if next_q:  # nouvelle question -> on s'arrête
                break
            a_next = str(df.at[j, c_a]).strip() if c_a else ""
            if a_next:
                parts.append(a_next)
            j += 1
        full_answer = "\n".join(parts).strip()

        # follow-up éventuel (ligne 1 finissant par '?')
        followup_q = _first_line_question(full_answer)
        content_for_index = _strip_first_line(full_answer)

        # extraire les branches si le texte les contient
        branches = _extract_branches(content_for_index)
        branch_param = ""
        if branches:
            keys = set(branches.keys())
            if keys.issubset({"gen1", "gen2", "gen3"}):
                branch_param = "gen"
            else:
                branch_param = "branch"

        # Gens CSV pour filtrage RAG (si on trouve des clés genX dans branches)
        gens_list = sorted([k for k in branches.keys() if k.startswith("gen")])
        gens_csv  = ",".join(gens_list)

        # Tags CSV (pour l’instant on ne passe pas de tags Excel -> simple)
        tags_csv = ""

        # Texte pour Chroma (toujours Q + contenu complet SANS la 1ère ligne si c’était un ?)
        text = f"Vraag: {vraag}\nAntwoord: {content_for_index or full_answer}"
        title = (vraag[:80] + "…") if len(vraag) > 80 else vraag

        metas.append({
            "source": path,
            "title": title,
            "source_type": source_type,
            "categorie": category or "",
            "gens": gens_csv,                 # CSV
            "video_url": video or "",
            "photo": photo or "",
            "tags": tags_csv,                 # CSV
            "sheet": chosen_sheet or "",
            "followup_q": followup_q or "",
            "branch_param": branch_param,     # "gen" ou "branch" ou ""
            "branch_keys": ",".join(branches.keys()) if branches else "",
        })
        texts.append(text)

        # Index JSON (pour /chat lookup direct)
        row_idx = {
            "question": vraag,
            "answer": content_for_index or full_answer,
            "category": category,
            "video_url": video or None,
            "photo": photo or None,
            "tags": [],  # pas de tags ligne par ligne ici
            "source": path,
            "sheet": chosen_sheet,
            "followup_q": followup_q or None,
            "branches": branches or None,     # { "wifipool": "...", "benisol": "..." } ou { "gen1": "...", ... }
            "branch_param": branch_param or None,
        }
        index_rows.append(row_idx)

        i = j  # on saute les lignes d’antwoord déjà consommées

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

    return {"indexed_files": 1, "indexed_chunks": len(texts), "sheet_used": chosen_sheet, "wrote_index": index_path}

# ---------- Ingestion universelle (fichier ou dossier) ----------
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
                        [{"source": path, "title": title, "source_type": source_type, "page": page_no}] * len(chunks)
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
