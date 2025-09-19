# app/rag.py
<<<<<<< HEAD
from typing import List, Tuple
import logging
=======
from typing import List, Tuple, Optional, Set
import logging
import re
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))

from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
<<<<<<< HEAD
=======
# (optionnel pour supprimer l’avertissement de dépréciation)
# from langchain_chroma import Chroma
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))

from .config import (
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    LLM_MODEL,
    TOP_K,
    RESPONSE_LANGUAGE,
    COLLECTION_NAME,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

client = OpenAI()

<<<<<<< HEAD
=======
# --------- Générations (détection dans la question) ---------
GEN_ALIASES = {
    "gen1": ["gen1", "gen 1", "generation 1", "v1"],
    "gen2": ["gen2", "gen 2", "generation 2", "v2"],
    "gen3": ["gen3", "gen 3", "generation 3", "v3"],
}

def detect_gen(s: str) -> Optional[str]:
    """Détecte gen1/gen2/gen3 dans une chaîne libre (question utilisateur)."""
    s = (s or "").lower()
    for key, aliases in GEN_ALIASES.items():
        for a in aliases:
            if re.search(rf"\b{re.escape(a)}\b", s):
                return key
    return None

>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))

def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

<<<<<<< HEAD
def _expand_queries_with_llm(question: str, n: int = 3) -> list:
    """
    Génère n reformulations courtes de la question (FR/NL/EN...).
    On utilise le même LLM pour rester simple.
=======

def _expand_queries_with_llm(question: str, n: int = 3) -> list:
    """
    Génère n reformulations courtes de la question (FR/NL/EN...).
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
    """
    try:
        prompt = (
            "Reformule la question ci-dessous en variantes de recherche courtes et précises. "
            f"Donne {n} lignes, sans numéros, sans guillemets.\n\nQuestion:\n{question}"
        )
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        text = resp.choices[0].message.content.strip()
        lines = [l.strip("•- \t") for l in text.splitlines() if l.strip()]
        return lines[:n] if lines else []
    except Exception:
        return []


def _uniq_add(acc: list, seen: set, docs) -> bool:
    """
    Ajoute des docs en évitant les doublons.
    Retourne True si on a atteint TOP_K.
    """
    for d in docs or []:
        key = (d.metadata.get("source"), d.metadata.get("page"), hash(d.page_content))
        if key in seen:
            continue
        acc.append(d)
        seen.add(key)
        if len(acc) >= TOP_K:
            return True
    return False


def _expand_neighbors(vs: Chroma, doc, k: int = 3):
<<<<<<< HEAD
    """Bring a few neighboring chunks from the same source to capture steps around the hit."""
=======
    """
    Ramène quelques 'voisins' du même fichier pour capter les étapes
    qui peuvent être dans des chunks adjacents.
    """
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
    try:
        src = (doc.metadata or {}).get("source")
        if not src:
            return []
<<<<<<< HEAD
=======
        # On cherche des passages TRÈS similaires mais dans la même source
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
        return vs.similarity_search(doc.page_content, k=k, filter={"source": src})
    except Exception:
        return []


<<<<<<< HEAD
def retrieve(question: str):
    """
    - Expand question with a few variants.
    - Prefer sources tagged as 'faq' or 'mixed'.
    - Use MMR; if it fails, fall back to similarity.
    - For each hit, also add a few neighbors from the same file
      (so we capture numbered steps that are split across chunks).
=======
def retrieve(question: str, gen_filter: Optional[str] = None):
    """
    - Étend la question avec quelques variantes (LLM).
    - Privilégie les sources taggées 'faq' ou 'mixed'.
    - MMR puis fallback similarity.
    - Pour chaque hit, ajoute 2–3 voisins du même fichier.
    - Si gen_filter est fourni (ex: 'gen1'), filtre sur metadata['gens'].
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
    """
    vs = _get_vs()
    results, seen = [], set()
    queries = [question] + _expand_queries_with_llm(question, n=3)

    def mmr(q, k, fetch_k, flt=None):
        try:
            return vs.max_marginal_relevance_search(q, k=k, fetch_k=fetch_k, filter=flt)
        except Exception:
            try:
                return vs.similarity_search(q, k=k, filter=flt)
            except Exception:
                return []

    def add_with_neighbors(docs):
<<<<<<< HEAD
        # add doc, then 2–3 neighbors from the same source
=======
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
        for d in docs or []:
            if _uniq_add(results, seen, [d]):
                return True
            neigh = _expand_neighbors(vs, d, k=3)
            if _uniq_add(results, seen, neigh):
                return True
        return False

<<<<<<< HEAD
    # 1) Prefer FAQ (+ mixed)
    for q in queries:
        docs = mmr(
            q,
            k=TOP_K,                        # take more
            fetch_k=max(40, TOP_K * 8),     # widen candidate pool
            flt={"source_type": {"$in": ["faq", "mixed"]}}
=======
    def make_filter(base=None):
        flt = dict(base or {})
        if gen_filter:
            # nécessite que l’ingest Excel ait mis metadata["gens"] = ["gen1", ...]
            flt["gens"] = {"$contains": gen_filter}
        return flt

    # 1) Préférence FAQ (+ mixed)
    for q in queries:
        docs = mmr(
            q,
            k=TOP_K,                         # prendre plus de candidats
            fetch_k=max(40, TOP_K * 8),      # élargir la "pool"
            flt=make_filter({"source_type": {"$in": ["faq", "mixed"]}})
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
        )
        if add_with_neighbors(docs):
            return results

    # 2) Global fallback
    for q in queries:
<<<<<<< HEAD
        docs = mmr(q, k=TOP_K, fetch_k=max(40, TOP_K * 8))
=======
        docs = mmr(q, k=TOP_K, fetch_k=max(40, TOP_K * 8), flt=make_filter())
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
        if add_with_neighbors(docs):
            return results

    return results


<<<<<<< HEAD

=======
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
def retrieve_with_scores(question: str) -> List[Tuple]:
    """
    Retourne [(doc, score)] ; score plus petit = plus proche (distance Chroma).
    """
    try:
        vs = _get_vs()
        pairs = vs.similarity_search_with_score(question, k=TOP_K)
        logger.info("retrieve_with_scores() -> %d pairs", len(pairs))
        return pairs
    except Exception as e:
        logger.exception("retrieve_with_scores() failed: %s", e)
        return []


def chat_with_confidence(question: str):
    """
    Utile pour remonter un score au front.
    Retourne (docs, scores, best_score) ; best_score plus petit = mieux.
    """
    pairs = retrieve_with_scores(question)
    docs = [p[0] for p in pairs]
    scores = [p[1] for p in pairs]
    if not docs:
        return None, [], 1e9
    best = min(scores) if scores else 1e9
    return docs, scores, best


# ---------- Prompt / génération ----------
def _lang_instruction(_: str) -> str:
    """
    Règle de langue :
      - RESPONSE_LANGUAGE == 'auto' -> répondre dans la langue de l'utilisateur
      - sinon -> forcer la langue donnée (fr, en, es, ...).
    """
    if (RESPONSE_LANGUAGE or "").strip().lower() == "auto":
        return (
            "Detect the user's language from the query and ALWAYS answer in that language. "
            "Do not mention that you detected the language. Keep product/brand names and URLs as-is."
        )
    return f"Answer in {RESPONSE_LANGUAGE}. Keep product/brand names and URLs as-is."


def _build_context(docs) -> str:
    """
    Concatène les extraits utiles en préfixant chaque chunk par un titre/source.
    """
    if not docs:
        return "(no context found)"
    parts = []
    for d in docs:
        title = d.metadata.get("title") or d.metadata.get("source") or "Document"
        page = d.metadata.get("page")
        head = f"[{title}{f' — p.{page}' if page is not None else ''}]"
        parts.append(f"{head} {d.page_content}")
    return "\n\n".join(parts)


def _build_messages(question: str, docs) -> list:
    """
    Messages pour Chat Completions :
      - system : rôle, langue, consignes
      - user   : question + contexte
    """
    lang_rule = _lang_instruction(question)
    context = _build_context(docs)

    system = (
        "You are 'Assistant Piscines', a helpful support agent for a pool e-commerce site.\n"
        f"{lang_rule}\n"
        "Use ONLY the provided context to answer. If the context is insufficient, say it and ask a short, "
        "clear clarifying question instead of hallucinating.\n"
        "Keep answers concise and helpful.\n"
        "Whenever the context contains a procedure or numbered steps (e.g., Step 1/2/3), reproduce them clearly as an ordered list using the exact terms found in the context (e.g., Snippet, Scheduler). Do not generalize; stick closely to the provided instructions.\n"
<<<<<<< HEAD
=======
        "Always adapt the answer to the user's specific model generation (Gen 1 / Gen 2 / Gen 3) if provided in the query or metadata. If the generation is unclear and multiple generations could apply, ask which one they have before giving detailed steps.\n"
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
    )

    user = f"QUESTION:\n{question}\n\nCONTEXT (excerpts):\n{context}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _collect_citations(docs) -> list:
    """
    Construit des citations uniques : [{title, url, source, page}].
    """
    citations, seen = [], set()
    for d in docs or []:
        src = d.metadata.get("source")
        url = d.metadata.get("url") or None
        title = d.metadata.get("title") or src or "Document"
        page = d.metadata.get("page")
        key = (title, url, src, page)
        if key in seen:
            continue
        citations.append({"title": title, "url": url, "source": src, "page": page})
        seen.add(key)
    return citations


def generate_answer(question: str, docs) -> Tuple[str, list]:
    """
    Génère une réponse en s'appuyant sur les docs fournis.
    Retourne (answer, citations)
    """
    messages = _build_messages(question, docs)

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.2,
    )
    answer = resp.choices[0].message.content
    citations = _collect_citations(docs)

<<<<<<< HEAD
    # --- FIX: pas d'f-string imbriquée ---
=======
    # --- logging lisible des citations ---
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
    formatted = []
    for c in citations:
        title = c.get("title") or c.get("source") or "Document"
        page = c.get("page")
        page_str = f" p.{page}" if page is not None else ""
        formatted.append(f"{title}{page_str}")

    logger.info(
        "generate_answer: used %d chunks | citations=%s",
        len(docs or []),
        formatted,
    )

    return answer, citations
<<<<<<< HEAD
=======


# --------- Utilitaire optionnel pour l’endpoint ----------
def extract_found_gens(docs) -> Set[str]:
    """
    Retourne l'ensemble des générations détectées dans les métadonnées des docs.
    Utile côté endpoint pour décider de poser la question 'Gen 1 ou Gen 2 ?'
    avant de donner la réponse.
    """
    found: Set[str] = set()
    for d in docs or []:
        for g in (d.metadata or {}).get("gens", []):
            if g in ("gen1", "gen2", "gen3"):
                found.add(g)
    return found
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
