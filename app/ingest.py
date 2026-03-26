# app/ingest.py
import os
import json
import unicodedata
from typing import List, Tuple, Any, Dict

# Note: pandas removed - Excel ingestion deprecated in favor of JSONL
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

# ==============================================================
#                  Helpers généraux (chargement)
# ==============================================================

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

# ==============================================================
#                    Splitter & Vector store
# ==============================================================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_TOKENS,
    chunk_overlap=CHUNK_OVERLAP_TOKENS,
    separators=["\n\n", "\n", ". ", " "],
)

def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

# ==============================================================
#                          Utils
# ==============================================================

def _norm_name(s: Any) -> str:
    """Normalise un nom de colonne/texte: lower, trim, retire accents & NBSP, compresse espaces."""
    s = str(s).replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    return s.strip().lower()

def _boolish(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "x", "✓", "yes", "ja", "oui"}

def _ensure_store_dir() -> None:
    os.makedirs(STORE_DIR, exist_ok=True)

def _index_to_vectorstore(texts: List[str], metas: List[dict]) -> None:
    if not texts:
        return
    vs = _get_vs()
    vs.add_texts(texts=texts, metadatas=metas)
    vs.persist()

# ==============================================================
#                      Ingestion: dossier files
# ==============================================================

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

    _index_to_vectorstore(texts, metas)

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}
    return {"indexed_files": file_count, "indexed_chunks": len(texts)}

# ==============================================================
#                  Ingestion: Excel (DEPRECATED - Use JSONL)
# ==============================================================
#
# Excel ingestion has been removed in favor of JSONL format.
# For FAQ management, use:
#   - app/faq_jsonl.py for JSONL-based FAQ management
#   - migrate_to_jsonl.py to convert existing Excel/data to JSONL
#   - update_faq.py to add/update/delete FAQ entries
#
# The FAQ system now uses a single JSONL file: app/data/faq.jsonl
# ==============================================================

# ==============================================================
#               Ingestion: JSONL (nouveau format)
# ==============================================================

_GEN_ALIASES = {
    "gen1": {"gen1", "gen 1", "generation 1", "g1"},
    "gen2": {"gen2", "gen 2", "generation 2", "g2"},
    "gen3": {"gen3", "gen 3", "generation 3", "g3"},
}

def _is_gen_label(label: str) -> str | None:
    l = _norm_name(label)
    for k, al in _GEN_ALIASES.items():
        if l in al:
            return k
    return None

def ingest_jsonl(path: str, source_type: str = "faq"):
    """
    JSON Lines :
      {"categorie": "...","vraag":"...","follow_up":true|false,
       "follow_up_question":"...", "options" / "opties": { label: { "antwoord": "...", "aanbeveling": "..." } } }
    """
    index_rows: List[dict] = []
    texts, metas = [], []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)

            category = str(obj.get("categorie") or obj.get("category") or "").strip()
            vraag = str(obj.get("vraag") or obj.get("question") or "").strip()
            follow_up = bool(obj.get("follow_up"))
            followup_q = str(obj.get("follow_up_question") or obj.get("followup_q") or "").strip()

            # options key tolerant: options / opties
            opt_map: Dict[str, dict] = obj.get("options") or obj.get("opties") or {}

            row_out: dict = {
                "category": category,
                "question": vraag,
                "answer": None,
                "follow_up": follow_up,
                "followup_q": followup_q if follow_up else None,
                "options": {},
                "source": path,
            }

            # compat GEN si labels ressemblent à gen1/gen2/gen3
            ask_gen = False
            gen_answers: Dict[str, str] = {}

            if follow_up and isinstance(opt_map, dict) and opt_map:
                for label, payload in opt_map.items():
                    label_str = str(label)
                    ans = str((payload or {}).get("antwoord") or (payload or {}).get("answer") or "").strip()
                    rec = str((payload or {}).get("aanbeveling") or (payload or {}).get("recommendation") or "").strip()
                    row_out["options"][label_str] = {"answer": ans, "recommendation": rec}

                    g = _is_gen_label(label_str)
                    if g:
                        ask_gen = True
                        gen_answers[g] = ans

                row_out["ask_gen"] = ask_gen
                if ask_gen:
                    # pour compat avec un main qui attend answer_gen1/2/3
                    if "gen1" in gen_answers:
                        row_out["answer_gen1"] = gen_answers["gen1"]
                    if "gen2" in gen_answers:
                        row_out["answer_gen2"] = gen_answers["gen2"]
                    if "gen3" in gen_answers:
                        row_out["answer_gen3"] = gen_answers["gen3"]

                # pas de chunks texte si pas de réponse directe ; on ajoute quand même un petit signal
                texts.append(f"Vraag: {vraag}\nOpvolgvraag: {followup_q}")
                metas.append({
                    "source": path,
                    "title": (vraag[:80] + "…") if len(vraag) > 80 else vraag,
                    "source_type": source_type,
                    "categorie": category or "",
                    "follow_up": True,
                })

            else:
                # Réponse directe attendue (clé 'antwoord' ou 'answer' à la racine)
                direct_answer = str(obj.get("antwoord") or obj.get("answer") or "").strip()
                row_out["answer"] = direct_answer

                texts.append(f"Vraag: {vraag}\nAntwoord: {direct_answer}")
                metas.append({
                    "source": path,
                    "title": (vraag[:80] + "…") if len(vraag) > 80 else vraag,
                    "source_type": source_type,
                    "categorie": category or "",
                })

            index_rows.append(row_out)

    if not index_rows:
        return {"indexed_files": 0, "indexed_chunks": 0, "error": "no rows"}

    _ensure_store_dir()
    index_path = os.path.join(STORE_DIR, "faq_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_rows, f, ensure_ascii=False, indent=2)

    _index_to_vectorstore(texts, metas)

    return {
        "indexed_files": 1,
        "indexed_chunks": len(texts),
        "wrote_index": index_path,
    }

# ==============================================================
#               Ingestion universelle (fichier / dossier)
# ==============================================================

def ingest_path(path: str, source_type: str = "mixed"):
    """
    Route unique appelée par /ingest.
    - dossier -> ingest_folder
    - .jsonl / .json -> ingest_jsonl
    - .xlsx / .xls   -> DEPRECATED (use JSONL format instead)
    - pdf/docx/html/txt -> envoi RAG uniquement

    Note: Excel ingestion is deprecated. Use JSONL format for FAQ data.
    """
    if not path:
        return {"indexed_files": 0, "indexed_chunks": 0}

    if os.path.isdir(path):
        return ingest_folder(path, source_type)

    ext = path.lower().rsplit(".", 1)[-1]

    if ext in {"jsonl", "json"}:
        return ingest_jsonl(path, source_type="faq")

    if ext in {"xlsx", "xls"}:
        # Excel ingestion is deprecated - use JSONL format
        return {
            "indexed_files": 0,
            "indexed_chunks": 0,
            "error": "Excel ingestion is deprecated. Please convert to JSONL format using migrate_to_jsonl.py"
        }

    # Fichiers texte pour RAG pur (pas d'index de FAQ)
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

    _index_to_vectorstore(texts, metas)

    if not texts:
        return {"indexed_files": 0, "indexed_chunks": 0}
    return {"indexed_files": 1, "indexed_chunks": len(texts)}
