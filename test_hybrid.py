#!/usr/bin/env python3
"""
HYBRID SEARCH - Combine keyword + vector search for maximum precision
=====================================================================

Uses BOTH:
1. Vector similarity search (semantic understanding)
2. Keyword search (exact term matching)
3. Combines scores to get best of both worlds
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def hybrid_search(query: str, top_k: int = 5, keyword_weight: float = 0.3):
    """
    Hybrid search combining vector similarity + keyword matching.

    Args:
        query: User question
        top_k: Number of results
        keyword_weight: Weight for keyword score (0-1, rest is vector weight)

    Returns:
        List of (entry, combined_score) tuples
    """
    from app.keyword_search import keyword_search
    from app.rag import get_suggestion_with_reasoning

    # Get keyword results
    keyword_results = keyword_search(query, top_k=top_k * 2)

    # Get vector results
    try:
        vector_result = get_suggestion_with_reasoning(query)
        if vector_result and "suggestions" in vector_result:
            vector_suggestions = vector_result["suggestions"]
        else:
            vector_suggestions = []
    except Exception as e:
        print(f"Vector search failed: {e}")
        # Fallback to keyword only
        return keyword_results[:top_k]

    # Combine scores
    # Build a map of question → scores
    combined = {}

    # Add keyword scores (normalized)
    max_keyword_score = max((s for _, s in keyword_results), default=1.0)
    for entry, score in keyword_results:
        question = entry.get("question", "")
        normalized_score = score / max_keyword_score if max_keyword_score > 0 else 0
        combined[question] = {
            "entry": entry,
            "keyword_score": normalized_score,
            "vector_score": 0.0,
        }

    # Add vector scores
    for suggestion in vector_suggestions:
        question = suggestion.get("question", "")
        similarity = suggestion.get("similarity_score", 0) / 100.0  # Convert from %

        if question in combined:
            combined[question]["vector_score"] = similarity
        else:
            combined[question] = {
                "entry": {
                    "question": question,
                    "answer": suggestion.get("answer", ""),
                    "category": suggestion.get("category", ""),
                },
                "keyword_score": 0.0,
                "vector_score": similarity,
            }

    # Calculate combined scores
    results = []
    for question, data in combined.items():
        kw_score = data["keyword_score"]
        vec_score = data["vector_score"]

        # Weighted combination
        combined_score = (keyword_weight * kw_score) + ((1 - keyword_weight) * vec_score)

        results.append((data["entry"], combined_score))

    # Sort by combined score
    results.sort(key=lambda x: x[1], reverse=True)

    return results[:top_k]


def test_hybrid_precision():
    """Test precision with hybrid search."""
    print("\n" + "=" * 80)
    print("🎯 HYBRID SEARCH PRECISION TEST")
    print("=" * 80)
    print("Combining vector similarity + keyword matching")
    print()

    from app.keyword_search import build_keyword_index, _load_faq_direct

    # Build index
    entries = _load_faq_direct()
    if not entries:
        print("❌ No FAQ entries found")
        return False

    build_keyword_index(entries)
    print(f"✓ Indexes built with {len(entries)} FAQ entries\n")

    # Simple test cases
    test_cases = [
        ("wifi wachtwoord fout", ["wachtwoord", "password"], "WiFi password"),
        ("wifi verbinding probleem signaal", ["signaal", "verbinding", "bereik"], "WiFi signal"),
        ("sensor kalibreren", ["kalibreren", "calibr"], "Calibration"),
        ("sensor vervangen", ["vervangen", "nieuw"], "Replacement"),
        ("pomp is lek", ["lek", "lekkage"], "Pump leak"),
        ("pomp werkt niet", ["werkt niet"], "Pump not working"),
    ]

    print(f"Running {len(test_cases)} hybrid search tests...\n")
    print("-" * 80)

    passed = 0
    for i, (query, expected_kw, desc) in enumerate(test_cases, 1):
        results = hybrid_search(query, top_k=3, keyword_weight=0.4)

        if not results:
            print(f"❌ [{i}/{len(test_cases)}] {desc}: NO RESULTS")
            continue

        top_entry, top_score = results[0]
        top_question = top_entry.get('question', '')
        top_text = (top_question + " " + top_entry.get('answer', '')).lower()

        # Check if expected keywords present
        found = any(kw.lower() in top_text for kw in expected_kw)

        status = "✅" if found else "❌"
        if found:
            passed += 1

        print(f"{status} [{i}/{len(test_cases)}] {desc}")
        print(f"    Query: '{query}'")
        print(f"    Expected: {expected_kw}")
        print(f"    Top: {top_question[:70]}...")
        print(f"    Score: {top_score:.3f}")
        print()

    accuracy = (passed / len(test_cases)) * 100
    print("=" * 80)
    print(f"Hybrid Search Accuracy: {passed}/{len(test_cases)} ({accuracy:.1f}%)")
    print("=" * 80)

    return accuracy >= 80


if __name__ == "__main__":
    try:
        success = test_hybrid_precision()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
