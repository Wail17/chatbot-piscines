# app/rag.py
from typing import List, Tuple, Optional, Set
import logging
import re

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


# ---------- Helpers GEN ----------
_GEN_PATTERNS = {
    "gen1": re.compile(r"\bgen[\s\-]?1\b", re.IGNORECASE),
    "gen2": re.compile(r"\bgen[\s\-]?2\b", re.IGNORECASE),
    "gen3": re.compile(r"\bgen[\s\-]?3\b", re.IGNORECASE),
}

def detect_gen(text: str) -> Optional[str]:
    """Renvoie 'gen1' / 'gen2' / 'gen3' si détecté dans la question, sinon None."""
    t = (text or "")
    for key, pat in _GEN_PATTERNS.items():
        if pat.search(t):
            return key
    return None

def extract_found_gens(docs) -> Set[str]:
    """
    Collecte les générations présentes dans les métadonnées des chunks.
    Compatible avec :
      - liste: ["gen1", "gen2"]
      - CSV string: "gen1,gen2"
      - champ 'gen': "gen1"
      - nom de fichier: ".../gen1/..."
    """
    found: Set[str] = set()
    for d in docs or []:
        md = d.metadata or {}
        gens = md.get("gens")

        # liste => ajoute les valeurs valides
        if isinstance(gens, list):
            for g in gens:
                if isinstance(g, str) and g.lower() in {"gen1", "gen2", "gen3"}:
                    found.add(g.lower())

        # chaîne => peut être "gen1" ou "gen1,gen2"
        elif isinstance(gens, str):
            for g in re.split(r"[,\s]+", gens):
                g = g.strip().lower()
                if g in {"gen1", "gen2", "gen3"}:
                    found.add(g)

        # certains loaders stockent 'gen'
        g1 = md.get("gen")
        if isinstance(g1, str) and g1.lower() in {"gen1", "gen2", "gen3"}:
            found.add(g1.lower())

        # ultime secours : nom de fichier
        src = (md.get("source") or "")
        for g in ("gen1", "gen2", "gen3"):
            if g in src.lower():
                found.add(g)

    return found


# ---------- Query expansion ----------
def _expand_queries_with_llm(question: str, n: int = 3) -> list:
    """Génère n reformulations courtes de la question."""
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
    """Ajoute des docs en évitant les doublons ; True si on atteint TOP_K."""
    for d in docs or []:
        key = ((d.metadata or {}).get("source"),
               (d.metadata or {}).get("page"),
               hash(d.page_content))
        if key in seen:
            continue
        acc.append(d)
        seen.add(key)
        if len(acc) >= TOP_K:
            return True
    return False


def _expand_neighbors(vs: Chroma, doc, k: int = 3):
    """Ramène quelques chunks voisins du même fichier pour capturer des étapes contiguës."""
    try:
        src = (doc.metadata or {}).get("source")
        if not src:
            return []
        return vs.similarity_search(doc.page_content, k=k, filter={"source": src})
    except Exception:
        return []


# ---------- Retrieval ----------
def retrieve(question: str, gen_filter: Optional[str] = None):
    """
    - MMR + fallback similarity
    - Pas de filtre Chroma sur 'gens' (string CSV) ; on filtre en mémoire si demandé.
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
        for d in docs or []:
            if _uniq_add(results, seen, [d]):
                return True
            neigh = _expand_neighbors(vs, d, k=3)
            if _uniq_add(results, seen, neigh):
                return True
        return False

    # 1) On cible les sources "faq" & "mixed" (pas de filtre sur 'gens' côté Chroma)
    for q in queries:
        flt = {"source_type": {"$in": ["faq", "mixed"]}}
        docs = mmr(q, k=TOP_K, fetch_k=max(40, TOP_K * 8), flt=flt)
        if add_with_neighbors(docs):
            break

    # 2) Fallback global si pas assez d’éléments
    if len(results) < TOP_K:
        for q in queries:
            docs = mmr(q, k=TOP_K, fetch_k=max(40, TOP_K * 8), flt=None)
            if add_with_neighbors(docs):
                break

    # 3) Filtre en mémoire par génération si demandé
    if gen_filter:
        want = gen_filter.strip().lower()

        def md_has_gen(md) -> bool:
            if not md:
                return False

            # CSV string ou simple string
            gens = md.get("gens")
            if isinstance(gens, str):
                for g in re.split(r"[,\s]+", gens):
                    if g.strip().lower() == want:
                        return True

            # liste éventuelle
            if isinstance(gens, list):
                if any(isinstance(x, str) and x.strip().lower() == want for x in gens):
                    return True

            # champ 'gen'
            g1 = md.get("gen")
            if isinstance(g1, str) and g1.strip().lower() == want:
                return True

            # secours: nom de fichier
            src = (md.get("source") or "").lower()
            if want in src:
                return True

            return False

        filtered = [d for d in results if md_has_gen(d.metadata)]
        if filtered:
            results = filtered

    return results


def retrieve_with_scores(question: str) -> List[Tuple]:
    """Retourne [(doc, score)] ; plus petit = plus proche."""
    try:
        vs = _get_vs()
        pairs = vs.similarity_search_with_score(question, k=TOP_K)
        logger.info("retrieve_with_scores() -> %d pairs", len(pairs))
        return pairs
    except Exception as e:
        logger.exception("retrieve_with_scores() failed: %s", e)
        return []


def chat_with_confidence(question: str):
    """Renvoie (docs, scores, best_score) ; best_score plus petit = mieux."""
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
    - 'auto' -> répondre dans la langue de l'utilisateur
    - sinon -> forcer la langue
    """
    if (RESPONSE_LANGUAGE or "").strip().lower() == "auto":
        return (
            "Detect the user's language from the query and ALWAYS answer in that language. "
            "Do not mention that you detected the language. Keep product/brand names and URLs as-is."
        )
    return f"Answer in {RESPONSE_LANGUAGE}. Keep product/brand names and URLs as-is."


def _build_context(docs) -> str:
    """Concatène les extraits utiles en préfixant par [title — p.X]."""
    if not docs:
        return "(no context found)"
    parts = []
    for d in docs:
        md = d.metadata or {}
        title = md.get("title") or md.get("source") or "Document"
        page = md.get("page")
        head = f"[{title}{f' — p.{page}' if page is not None else ''}]"
        parts.append(f"{head} {d.page_content}")
    return "\n\n".join(parts)


def _build_messages(question: str, docs) -> list:
    lang_rule = _lang_instruction(question)
    context = _build_context(docs)

    system = (
        "You are 'Assistant Piscines', a helpful support agent for a pool e-commerce site.\n"
        f"{lang_rule}\n"
        "Use ONLY the provided context to answer. If the context is insufficient, say it and ask a short, "
        "clear clarifying question instead of hallucinating.\n"
        "Keep answers concise and helpful.\n"
        "Whenever the context contains a procedure or numbered steps (e.g., Step 1/2/3), reproduce them clearly as an ordered list using the exact terms found in the context (e.g., Snippet, Scheduler). Do not generalize; stick closely to the provided instructions.\n"
    )

    user = f"QUESTION:\n{question}\n\nCONTEXT (excerpts):\n{context}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _collect_citations(docs) -> list:
    """Citations uniques : [{title, url, source, page}]."""
    citations, seen = [], set()
    for d in docs or []:
        md = d.metadata or {}
        src = md.get("source")
        url = md.get("url") or None
        title = md.get("title") or src or "Document"
        page = md.get("page")
        key = (title, url, src, page)
        if key in seen:
            continue
        citations.append({"title": title, "url": url, "source": src, "page": page})
        seen.add(key)
    return citations


def generate_answer(question: str, docs) -> Tuple[str, list]:
    """Génère une réponse appuyée sur les docs fournis. Retourne (answer, citations)."""
    messages = _build_messages(question, docs)
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.2,
    )
    answer = resp.choices[0].message.content
    citations = _collect_citations(docs)

    # Log utile
    formatted = []
    for c in citations:
        title = c.get("title") or c.get("source") or "Document"
        page = c.get("page")
        page_str = f" p.{page}" if page is not None else ""
        formatted.append(f"{title}{page_str}")
    logger.info("generate_answer: used %d chunks | citations=%s", len(docs or []), formatted)

    return answer, citations
