# app/rag.py
from typing import List, Tuple
import logging

from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

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


def _get_vs() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDINGS_MODEL),
    )

def _expand_queries_with_llm(question: str, n: int = 3) -> list:
    """
    Génère n reformulations courtes de la question (FR/NL/EN...).
    On utilise le même LLM pour rester simple.
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


def retrieve(question: str):
    """
    Stratégie robuste :
      1) Génère 2–3 reformulations de la question.
      2) Pour chaque requête, cherche d'abord dans source_type='faq' (si présent),
         puis globalement (tous types).
      3) MMR pour la diversité, fallback similarity si besoin.
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

    # 1) Préférence FAQ
    for q in queries:
        docs = mmr(q, k=min(4, TOP_K), fetch_k=max(30, TOP_K * 6), flt={"source_type": "faq"})
        if _uniq_add(results, seen, docs):
            return results

    # 2) Global
    for q in queries:
        docs = mmr(q, k=TOP_K, fetch_k=max(30, TOP_K * 6))
        if _uniq_add(results, seen, docs):
            return results

    return results




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
        "At the end of the answer, add a short 'Sources :' section listing the titles/filenames used. "
        "If none were used, say 'Aucune source utilisée.'"
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

    # --- FIX: pas d'f-string imbriquée ---
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
