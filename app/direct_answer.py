# app/direct_answer.py
"""
Direct FAQ answer extractor.

When the keyword search finds a very high-confidence match,
this module extracts the answer DIRECTLY from the FAQ without
going through the LLM. This:

1. Eliminates OpenAI API costs for well-known questions
2. Returns answers faster (no network round-trip)
3. Returns more accurate answers (exact FAQ text, not LLM paraphrase)
4. Works 100% OFFLINE

Strategy:
- Score > HIGH_CONFIDENCE: Return FAQ answer directly
- Score < HIGH_CONFIDENCE: Fall through to RAG/LLM pipeline

The confidence threshold for direct extraction is higher than
for keyword search alone, since we want to be SURE the match
is correct before bypassing the LLM validation.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple

from .keyword_search import keyword_search

logger = logging.getLogger(__name__)

# ─── THRESHOLDS ───────────────────────────────────────────────────────────────

# Minimum score to return a direct FAQ answer (bypasses LLM)
# Higher = more conservative, fewer direct answers but more accurate
DIRECT_ANSWER_THRESHOLD = 6.0   # TF-IDF score (empirically tuned)

# If second result is within this fraction of top result, it's ambiguous
AMBIGUITY_GAP_RATIO = 0.85  # Top 2 scores within 85% = ambiguous


# ─── DIRECT EXTRACTION ────────────────────────────────────────────────────────

def try_direct_answer(
    query: str,
    threshold: float = DIRECT_ANSWER_THRESHOLD,
    top_k: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Try to answer directly from FAQ without LLM.

    Returns None if confidence is too low or result is ambiguous,
    in which case the caller should fall through to the full RAG pipeline.

    Args:
        query: User query (in any language)
        threshold: Minimum score to return direct answer
        top_k: Number of candidates to consider

    Returns:
        Direct answer dict or None if LLM should handle it
    """
    results = keyword_search(query, top_k=top_k, min_score=0.01)

    if not results:
        return None

    top_entry, top_score = results[0]

    # Too low confidence
    if top_score < threshold:
        logger.debug(f"Direct answer: score {top_score:.2f} < threshold {threshold} → use LLM")
        return None

    # Check for ambiguity: if second result is close to first, use LLM
    if len(results) >= 2:
        second_entry, second_score = results[1]
        if second_score >= top_score * AMBIGUITY_GAP_RATIO:
            logger.debug(
                f"Direct answer: ambiguous (top={top_score:.2f}, second={second_score:.2f}) → use LLM"
            )
            return None

    # High confidence, unambiguous → return direct
    question = top_entry.get("question", "")
    answer = top_entry.get("answer", "")
    category = top_entry.get("category", "")

    if not answer:
        return None

    logger.info(
        f"Direct FAQ answer (score={top_score:.2f}): '{question[:60]}...'"
    )

    return {
        "answer": answer,
        "question": question,
        "category": category,
        "confidence": min(top_score / 10.0, 1.0),  # Normalize to 0-1
        "score": top_score,
        "source": "direct_faq",
        "citations": [{"title": question, "source": "faq", "page": None}],
    }


def get_direct_answer_with_suggestions(
    query: str,
    threshold: float = DIRECT_ANSWER_THRESHOLD,
    suggestion_count: int = 4,
) -> Optional[Dict[str, Any]]:
    """
    Get direct answer plus related FAQ suggestions.

    This is the main entry point for the direct answer path.

    Args:
        query: User query
        threshold: Confidence threshold
        suggestion_count: Number of suggestions to include

    Returns:
        Response dict with answer + suggestions, or None to use LLM
    """
    result = try_direct_answer(query, threshold=threshold)

    if result is None:
        return None

    # Get suggestions (other related questions)
    suggestions = _get_related_suggestions(
        query=query,
        exclude_question=result["question"],
        count=suggestion_count,
    )

    return {
        **result,
        "suggestions": suggestions,
    }


def _get_related_suggestions(
    query: str,
    exclude_question: str,
    count: int = 4,
) -> List[str]:
    """Get related FAQ questions as suggestions."""
    try:
        results = keyword_search(query, top_k=count + 2, min_score=0.5)
        suggestions = []
        for entry, score in results:
            q = entry.get("question", "")
            if q and q != exclude_question and q not in suggestions:
                suggestions.append(q)
            if len(suggestions) >= count:
                break
        return suggestions
    except Exception as e:
        logger.debug(f"Suggestion generation failed: {e}")
        return []


# ─── BATCH EVALUATION ─────────────────────────────────────────────────────────

def evaluate_direct_threshold(
    test_queries: List[Tuple[str, str]],
    threshold: float = DIRECT_ANSWER_THRESHOLD,
) -> Dict[str, Any]:
    """
    Evaluate how many queries can be answered directly at a given threshold.

    Useful for tuning the threshold.

    Args:
        test_queries: List of (query, expected_answer_contains) tuples
        threshold: Threshold to test

    Returns:
        Evaluation results
    """
    total = len(test_queries)
    direct_count = 0
    correct = 0

    for query, expected_substring in test_queries:
        result = try_direct_answer(query, threshold=threshold)
        if result:
            direct_count += 1
            if expected_substring.lower() in result["answer"].lower():
                correct += 1

    return {
        "threshold": threshold,
        "total_queries": total,
        "direct_answers": direct_count,
        "direct_rate_pct": round(direct_count / total * 100, 1) if total > 0 else 0,
        "correct_direct": correct,
        "accuracy_pct": round(correct / direct_count * 100, 1) if direct_count > 0 else 0,
        "llm_fallback": total - direct_count,
    }
