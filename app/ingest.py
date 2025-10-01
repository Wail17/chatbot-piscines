# app/ingest.py
import os
import json
import unicodedata
from typing import List, Tuple

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
    STORE_DIR,  # pour écrire faq_index.json
)

# -------------------- Loaders texte classiques --------------------
def _load_pdf_pages(path: str) -> List[Tuple[str, int]]:
    """Retourne [(texte_page, index_page), ...] à partir d'un PDF."""
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


# -------------------- Splitter --------------------
splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_TOKENS,
    chunk_overlap=CHUNK_OVERLAP_TOKENS,
    separators=["\n\n", "\n", ". ", " "],
)


# -------------------- Vector store --------------------
def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )


# -------------------- Ingestion dossier (txt/pdf/docx/html) --------------------
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


# -------------------- Helpers --------------------
def _norm(s: str) -> str:
    """
    Normalise un en-tête: minuscules, accents supprimés, non-alphanum -> '_'.
    Permet de matcher 'Gen 1', 'Gen1', 'FR Réponse', 'DE Frange/Frage', etc.
    """
    if not isinstance(s, str):
        s = str(s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() else "_")
    return "_".join("".join(out).split())


def _boolish(v) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui", "ok"}


# -------------------- Ingestion Excel (FAQ) --------------------
def ingest_excel(path: str, source_type: str = "faq"):
    """
    Lit un Excel de FAQ (multi-langue par ligne) et envoie chaque paire Q/A dans Chroma.
    - Couvre les entêtes tolérantes (accents/espaces/variantes).
    - Détecte GEN1/GEN2/GEN3, pose ask_gen=True s'il y a au moins une croix GEN.
    - Récupère des tags produits (wifipool, display, vloeibare chloor, etc.)
    - Prend en compte photo / video si fournis.
    - Ecrit aussi un index JSON 'store/faq_index.json' pour un lookup rapide.
    """
    if not os.path.exists(path):
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "file not found"}

    df = pd.read_excel(path, engine="openpyxl")
    # mapping nom_normalise -> nom_reel
    norm2real = { _norm(c): c for c in df.columns }

    def get_col(possible_norm_keys: set[str]) -> str | None:
        for key in possible_norm_keys:
            if key in norm2real:
                return norm2real[key]
        return None

    # Colonnes par langue (tolère variantes)
    LANG_COLS = {
        "nl": {"q": {"vraag"}, "a": {"antwoord"}},
        "fr": {"q": {"fr_question"}, "a": {"fr_reponse"}},
        "en": {"q": {"en_question"}, "a": {"en_answer"}},
        "de": {"q": {"de_frage", "de_frange", "de_vraag", "de_question"}, "a": {"de_antwort"}},
    }

    # Au moins une paire Q/A doit exister
    has_any_lang = False
    for lang, qa in LANG_COLS.items():
        cq = get_col(qa["q"])
        ca = get_col(qa["a"])
        if cq and ca:
            has_any_lang = True
            break
    if not has_any_lang:
        return {
            "indexed_files": 0,
            "indexed_chunks": 0,
            "error": "Kolommen 'Vraag'/'Antwoord' ontbreken (of FR/EN/DE Q/A)"
        }

    # catégories / médias
    col_categorie = get_col({"categorie", "category"})
    col_foto = get_col({"foto", "photo", "image"})
    col_video = get_col({"filmpje", "video", "video_url", "video_url_", "youtube"})

    # GEN et produits
    GEN_KEYS = {
        "gen1": {"gen_1", "gen1"},
        "gen2": {"gen_2", "gen2"},
        "gen3": {"gen_3", "gen3"},
    }
    PRODUCT_KEYS = {
        "wifipool": {"wifipool"},
        "display": {"display"},
        "vloeibare_chloor": {"vloeibare_chloor"},
        "zoutelektrolyse": {"zoutelektrolyse"},
        "epdm": {"epdm"},
        "aut_kranen": {"aut_kranen"},
        "frequentieregelaar": {"frequentieregelaar"},
    }

    # résout les colonnes réelles pour GEN et produits
    GEN_COLS_REAL = {g: get_col(keys) for g, keys in GEN_KEYS.items()}
    PROD_COLS_REAL = {p: get_col(keys) for p, keys in PRODUCT_KEYS.items()}

    texts, metas = [], []
    index_rows = []

    # itère sur les lignes
    for _, row in df.iterrows():
        # construit la liste des GEN cochés
        gens = []
        for g, col in GEN_COLS_REAL.items():
            if not col:
                continue
            if _boolish(row.get(col)):
                gens.append(g)

        # produits cochés
        products = []
        for p, col in PROD_COLS_REAL.items():
            if not col:
                continue
            if _boolish(row.get(col)):
                products.append(p)

        categorie = (str(row.get(col_categorie)).strip() if col_categorie else None) or None
        foto = (str(row.get(col_foto)).strip() if col_foto else None) or None
        video = (str(row.get(col_video)).strip() if col_video else None) or None

        # ask_gen : si au moins une colonne GEN est cochée -> on doit demander
        ask_gen = bool(gens)

        # pour chaque langue dispo sur la ligne, créer un chunk
        for lang, qa in LANG_COLS.items():
            cq = get_col(qa["q"])
            ca = get_col(qa["a"])
            if not (cq and ca):
                continue

            q = str(row.get(cq) or "").strip()
            a = str(row.get(ca) or "").strip()
            if not q or not a:
                continue

            # texte indexé (simple et robuste)
            text = f"Vraag: {q}\nAntwoord: {a}"
            title = (q[:80] + "…") if len(q) > 80 else q

            meta = {
                "source": path,
                "title": title,
                "source_type": source_type,  # 'faq'
                "categorie": categorie,
                "gens": gens or None,
                "products": products or None,
                "ask_gen": ask_gen,
                "foto": foto,
                "video": video,
                "lang": lang,
            }
            texts.append(text)
            metas.append(meta)

            # pour le lookup direct JSON
            index_rows.append({
                "question": q,
                "answer": a,
                "lang": lang,
                "category": categorie,
                "gens": gens,
                "products": products,
                "ask_gen": ask_gen,
                "foto": foto,
                "video": video,
                "source": path,
            })

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows with Q/A"}

    # écrit un petit index JSON (utile pour debug/lookup direct)
    os.makedirs(STORE_DIR, exist_ok=True)
    index_path = os.path.join(STORE_DIR, "faq_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_rows, f, ensure_ascii=False, indent=2)

    # push dans Chroma
    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

    return {
        "indexed_files": 1,
        "indexed_chunks": len(texts),
        "wrote_index": index_path,
    }


# -------------------- Ingestion universelle (fichier ou dossier) --------------------
def ingest_path(path: str, source_type: str = "mixed"):
    """
    Routeur : dossier -> ingest_folder ; .xlsx/.xls -> ingest_excel ; sinon charge un fichier texte.
    """
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
            metas.extend(
                [
                    {
                        "source": path,
                        "title": title,
                        "source_type": source_type,
                        "page": page_no,
                    }
                ]
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
