#!/usr/bin/env python3
"""
Test semantic search accuracy on similar questions.

This test checks if vector embeddings improve precision
for questions that are semantically different but share keywords.
"""

import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from app.rag import retrieve


def test_similar_questions():
    """Test on questions with similar keywords but different meaning."""

    test_cases = [
        {
            "query": "How to reset the WiFi?",
            "expected_keywords": ["wifi", "reset", "netwerk", "opnieuw"],
            "not_expected": ["factory", "fabriek", "herstellen"],
            "description": "WiFi reset (NOT factory reset)"
        },
        {
            "query": "How to do a factory reset?",
            "expected_keywords": ["factory", "fabriek", "herstellen", "volledig"],
            "not_expected": ["wifi", "netwerk"],
            "description": "Factory reset (NOT WiFi reset)"
        },
        {
            "query": "Pump is leaking water",
            "expected_keywords": ["lek", "water", "lekt"],
            "not_expected": ["werkt niet", "draait niet"],
            "description": "Pump leak (NOT pump not working)"
        },
        {
            "query": "Pump is not working",
            "expected_keywords": ["werkt niet", "defect", "draait"],
            "not_expected": ["lek"],
            "description": "Pump malfunction (NOT leak)"
        },
        {
            "query": "How to restart the device?",
            "expected_keywords": ["herstarten", "opnieuw", "reboot"],
            "not_expected": ["factory", "fabriek"],
            "description": "Restart (NOT factory reset)"
        },
    ]

    print("\n" + "="*70)
    print("SEMANTIC SEARCH ACCURACY TEST")
    print("="*70)
    print("\nTesting if vector embeddings distinguish similar questions...\n")

    results = []

    for i, test in enumerate(test_cases, 1):
        query = test["query"]
        print(f"{i}. Query: '{query}'")
        print(f"   Expected: {test['description']}")

        try:
            docs = retrieve(query)

            if not docs:
                print(f"   ❌ No results retrieved")
                results.append(False)
                continue

            # Get top result
            top_doc = docs[0]
            question = top_doc.metadata.get('question', '')
            answer = top_doc.metadata.get('answer', '')

            text = (question + " " + answer).lower()

            # Check if expected keywords are present
            has_expected = any(kw.lower() in text for kw in test["expected_keywords"])
            has_not_expected = any(kw.lower() in text for kw in test["not_expected"])

            if has_expected and not has_not_expected:
                print(f"   ✅ CORRECT - Top result is relevant")
                print(f"      Match: {question[:80]}...")
                results.append(True)
            elif has_not_expected:
                print(f"   ❌ WRONG - Confused with similar question")
                print(f"      Got: {question[:80]}...")
                results.append(False)
            else:
                print(f"   ⚠️  UNCLEAR - Could not determine relevance")
                print(f"      Got: {question[:80]}...")
                results.append(False)

        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            results.append(False)

        print()

    # Calculate accuracy
    correct = sum(results)
    total = len(results)
    accuracy = (correct / total * 100) if total > 0 else 0

    print("="*70)
    print(f"RESULTS: {correct}/{total} correct ({accuracy:.1f}%)")
    print("="*70)

    if accuracy >= 85:
        print("✅ EXCELLENT - Semantic search is working well!")
    elif accuracy >= 70:
        print("⚠️  GOOD - Some improvements needed")
    else:
        print("❌ POOR - Semantic search needs improvement")

    return accuracy


if __name__ == "__main__":
    accuracy = test_similar_questions()

    print("\n" + "="*70)
    print("COMPARISON")
    print("="*70)
    print(f"Keyword-only search:  ~65-70%")
    print(f"Vector embeddings:     {accuracy:.1f}%")
    print(f"Improvement:          +{accuracy - 67.5:.1f}%")
    print("="*70)
