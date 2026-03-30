# app/rag_optimized.py
"""
OPTIMIZED retrieval for SAV/customer support.

Strategy: KEYWORD-FIRST with embeddings fallback

Why keyword-first?
- Keyword + synonyms: 91.8% accuracy ✅
- Pure embeddings: 41.7% accuracy ❌
- Smart hybrid: 87.5% accuracy (works because of keyword fallback!)

For SAV, we need PRECISION - wrong answer = angry customer!
Keyword search is more precise for technical domain-specific questions.

When to use embeddings:
- Only when keyword search finds < 2 results
- As a backup for truly novel questions
- Never as primary method for this use case
"""

from typing import List, Optional
import logging
from langchain_core.documents import Document

from .keyword_search import keyword_search_as_documents
from .rag_pure import retrieve_pure_semantic
from .config import TOP_K

logger = logging.getLogger(__name__)


def retrieve_keyword_first(
    question: str,
    k: int = TOP_K,
    keyword_threshold: int = 2,
    semantic_threshold: float = 0.6,
    gen_filter: Optional[str] = None
) -> List[Document]:
    """
    Keyword-first hybrid search optimized for SAV.

    Strategy:
    1. Try keyword search with synonyms (91.8% accurate!)
    2. If >=keyword_threshold results: return keyword results ✅
    3. Otherwise: fallback to semantic search
    4. If semantic finds nothing: return whatever keyword found

    This ensures we prioritize the most accurate method (keyword)
    while still having semantic as backup for edge cases.

    Args:
        question: User question
        k: Number of results
        keyword_threshold: Min results from keyword before skipping semantic
        semantic_threshold: Similarity threshold for semantic search
        gen_filter: Optional generation filter

    Returns:
        List of relevant documents
    """
    logger.info(f"Keyword-first search: '{question[:50]}...'")

    # ═══════════════════════════════════════════════════════════
    # STEP 1: Try keyword search (PRIMARY METHOD)
    # ═══════════════════════════════════════════════════════════
    try:
        keyword_results = keyword_search_as_documents(question, top_k=k)

        if len(keyword_results) >= keyword_threshold:
            logger.info(f"✅ Keyword search found {len(keyword_results)} results - SUFFICIENT")
            return keyword_results

        logger.info(f"⚠️  Keyword search found only {len(keyword_results)} results - trying semantic...")

    except Exception as e:
        logger.warning(f"Keyword search failed: {e}")
        keyword_results = []

    # ═══════════════════════════════════════════════════════════
    # STEP 2: Keyword insufficient - try semantic (FALLBACK)
    # ═══════════════════════════════════════════════════════════
    try:
        semantic_results = retrieve_pure_semantic(
            question,
            k=k,
            score_threshold=semantic_threshold,
            gen_filter=gen_filter
        )

        if semantic_results:
            logger.info(f"✅ Semantic search found {len(semantic_results)} additional results")

            # Combine keyword + semantic, deduplicate
            combined = keyword_results + semantic_results
            seen = set()
            unique = []

            for doc in combined:
                q = doc.metadata.get('question', '')
                if q not in seen:
                    seen.add(q)
                    unique.append(doc)

            logger.info(f"📊 Final: {len(unique)} unique results (keyword + semantic)")
            return unique[:k]

    except Exception as e:
        logger.warning(f"Semantic search failed: {e}")

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Return whatever we have (prefer something over nothing)
    # ═══════════════════════════════════════════════════════════
    if keyword_results:
        logger.info(f"📊 Returning {len(keyword_results)} keyword results (semantic failed)")
        return keyword_results

    logger.warning("❌ No results from keyword or semantic search")
    return []


def retrieve_keyword_only(
    question: str,
    k: int = TOP_K
) -> List[Document]:
    """
    Keyword-only search (no embeddings, no API costs).

    Use this for:
    - Production if 91.8% accuracy is sufficient
    - Avoiding API costs
    - Maximum precision on technical questions

    Args:
        question: User question
        k: Number of results

    Returns:
        List of relevant documents
    """
    logger.info(f"Keyword-only search: '{question[:50]}...'")

    try:
        results = keyword_search_as_documents(question, top_k=k)
        logger.info(f"✅ Found {len(results)} results")
        return results
    except Exception as e:
        logger.error(f"Keyword search failed: {e}")
        return []


def get_search_stats(question: str) -> dict:
    """
    Get statistics about search methods for debugging.

    Returns:
        Dict with counts for each method
    """
    stats = {
        "question": question[:100],
        "methods": {}
    }

    # Keyword
    try:
        kw_results = keyword_search_as_documents(question, top_k=5)
        stats["methods"]["keyword"] = {
            "count": len(kw_results),
            "top": kw_results[0].metadata.get('question', '') if kw_results else None
        }
    except Exception as e:
        stats["methods"]["keyword"] = {"error": str(e)}

    # Semantic
    try:
        sem_results = retrieve_pure_semantic(question, k=5, score_threshold=0.6)
        stats["methods"]["semantic"] = {
            "count": len(sem_results),
            "top": sem_results[0].metadata.get('question', '') if sem_results else None
        }
    except Exception as e:
        stats["methods"]["semantic"] = {"error": str(e)}

    return stats
