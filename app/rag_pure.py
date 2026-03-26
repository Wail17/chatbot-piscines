# app/rag_pure.py
"""
PURE semantic search using ONLY vector embeddings.

This module provides a clean, simple semantic search without:
- Synonym expansion (interferes with embeddings)
- LLM query reformulation (creates confusion)
- Neighbor expansion (adds irrelevant results)
- Keyword fallback (mixing methods reduces accuracy)
- MMR diversity (favors variety over precision)

For SAV/customer support, PRECISION is critical.
Better to return 1 perfect match than 5 diverse but wrong matches.
"""

from typing import List, Optional, Dict, Any
import logging
from langchain.schema import Document

from .faq_jsonl import get_faq_manager
from .config import TOP_K

logger = logging.getLogger(__name__)


def retrieve_pure_semantic(
    question: str,
    k: int = TOP_K,
    score_threshold: float = 0.7,
    gen_filter: Optional[str] = None
) -> List[Document]:
    """
    Pure semantic search using ONLY vector embeddings.

    No synonym expansion, no LLM reformulation, no keyword fallback.
    Just clean, precise semantic similarity.

    Args:
        question: User question (any language)
        k: Number of results to return (default: TOP_K)
        score_threshold: Minimum similarity score (0-1, default: 0.7)
            Higher = more strict, fewer false positives
        gen_filter: Optional generation filter (gen1, gen2, gen3)

    Returns:
        List of relevant documents, sorted by similarity
    """
    logger.info(f"Pure semantic search: '{question[:50]}...' (threshold={score_threshold})")

    try:
        # Get FAQ manager with vectorstore
        faq_mgr = get_faq_manager()

        # Build embeddings if not already built
        if not faq_mgr.vectorstore:
            logger.info("Vectorstore not found, building embeddings...")
            success = faq_mgr.build_embeddings()
            if not success:
                logger.error("Failed to build vectorstore")
                return []

        # Build filter
        filter_dict = None
        if gen_filter:
            filter_dict = {"generation": gen_filter}

        # Pure similarity search with scores
        # Use similarity_search_with_relevance_scores for threshold filtering
        try:
            results_with_scores = faq_mgr.vectorstore.similarity_search_with_relevance_scores(
                question,
                k=k * 2,  # Fetch more, filter by threshold
                filter=filter_dict
            )

            # Filter by threshold and limit to k
            results = [
                doc for doc, score in results_with_scores
                if score >= score_threshold
            ][:k]

            if results:
                logger.info(f"Found {len(results)} results above threshold {score_threshold}")
                # Log scores for debugging
                for i, (doc, score) in enumerate(results_with_scores[:len(results)], 1):
                    question_preview = doc.metadata.get('question', '')[:60]
                    logger.debug(f"  {i}. [{score:.3f}] {question_preview}...")
            else:
                logger.warning(f"No results above threshold {score_threshold}")

            return results

        except AttributeError:
            # Fallback if similarity_search_with_relevance_scores not available
            logger.warning("Using similarity_search without scores")
            results = faq_mgr.vectorstore.similarity_search(
                question,
                k=k,
                filter=filter_dict
            )
            logger.info(f"Found {len(results)} results (no threshold filtering)")
            return results

    except Exception as e:
        logger.error(f"Pure semantic search failed: {e}", exc_info=True)
        return []


def retrieve_hybrid_smart(
    question: str,
    k: int = TOP_K,
    semantic_threshold: float = 0.75,
    gen_filter: Optional[str] = None
) -> List[Document]:
    """
    Smart hybrid: Try pure semantic first, fallback to keyword if needed.

    Strategy:
    1. Try pure semantic search with high threshold
    2. If <2 results: lower threshold
    3. If still <1 result: fallback to keyword search

    Args:
        question: User question
        k: Number of results
        semantic_threshold: Initial threshold for semantic search
        gen_filter: Optional generation filter

    Returns:
        List of relevant documents
    """
    # Try pure semantic with high threshold first
    results = retrieve_pure_semantic(
        question,
        k=k,
        score_threshold=semantic_threshold,
        gen_filter=gen_filter
    )

    # If insufficient results, try lower threshold
    if len(results) < 2:
        logger.info(f"Only {len(results)} results, trying lower threshold 0.6")
        results = retrieve_pure_semantic(
            question,
            k=k,
            score_threshold=0.6,
            gen_filter=gen_filter
        )

    # If still no results, fallback to keyword
    if len(results) < 1:
        logger.warning("Semantic search failed, falling back to keyword search")
        try:
            from .keyword_search import keyword_search_as_documents
            results = keyword_search_as_documents(question, top_k=k)
            logger.info(f"Keyword fallback returned {len(results)} results")
        except Exception as e:
            logger.error(f"Keyword fallback failed: {e}")
            results = []

    return results


def compare_search_methods(question: str) -> Dict[str, Any]:
    """
    Compare different search methods for debugging.

    Returns performance metrics for:
    - Pure semantic (high threshold)
    - Pure semantic (medium threshold)
    - Pure semantic (low threshold)
    - Keyword search

    Useful for tuning thresholds and understanding which method works best.
    """
    results = {
        "question": question,
        "methods": {}
    }

    # Test different semantic thresholds
    for threshold in [0.8, 0.7, 0.6, 0.5]:
        docs = retrieve_pure_semantic(question, k=3, score_threshold=threshold)
        results["methods"][f"semantic_{threshold}"] = {
            "count": len(docs),
            "top_match": docs[0].metadata.get('question', '') if docs else None
        }

    # Test keyword
    try:
        from .keyword_search import keyword_search_as_documents
        docs = keyword_search_as_documents(question, top_k=3)
        results["methods"]["keyword"] = {
            "count": len(docs),
            "top_match": docs[0].metadata.get('question', '') if docs else None
        }
    except Exception:
        results["methods"]["keyword"] = {"error": "Not available"}

    return results
