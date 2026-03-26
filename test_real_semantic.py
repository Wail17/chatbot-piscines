#!/usr/bin/env python3
"""
Test semantic search with REAL FAQ questions that are similar.

Tests if the system can distinguish between questions that share keywords
but have different meanings.
"""

import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from app.rag import retrieve


def test_real_similar_questions():
    """
    Test with actual similar questions from FAQ.

    These test pairs have similar keywords but different topics:
    - pH calibration vs pH measurement errors
    - WiFi connection vs WiFi offline issues
    - Pump leak vs pump malfunction
    """

    test_cases = [
        {
            "query_nl": "pH kalibratie",
            "query_en": "pH calibration",
            "query_fr": "calibration pH",
            "expected_in_top_result": "kalibreer",
            "not_expected": "meting wijkt",
            "description": "pH calibration (NOT pH measurement errors)"
        },
        {
            "query_nl": "pH meting is verkeerd",
            "query_en": "pH measurement is wrong",
            "query_fr": "mesure pH incorrecte",
            "expected_in_top_result": "meting wijkt",
            "not_expected": "kalibreer",
            "description": "pH measurement error (NOT calibration)"
        },
        {
            "query_nl": "pomp lekt water",
            "query_en": "pump is leaking",
            "query_fr": "pompe fuit",
            "expected_in_top_result": "lek",
            "not_expected": "werkt niet",
            "description": "Pump leak (NOT malfunction)"
        },
        {
            "query_nl": "wifi verbinding probleem",
            "query_en": "wifi connection problem",
            "query_fr": "problème connexion wifi",
            "expected_in_top_result": "wifi",
            "not_expected": None,  # Too general
            "description": "WiFi connection issues"
        },
    ]

    print("\n" + "="*70)
    print("SEMANTIC SEARCH - REAL FAQ QUESTIONS TEST")
    print("="*70)
    print("\nTesting multilingual semantic search with similar questions...\n")

    all_results = []

    for i, test in enumerate(test_cases, 1):
        print(f"\n{'='*70}")
        print(f"TEST {i}: {test['description']}")
        print(f"{'='*70}")

        # Test in all 3 languages
        queries = [
            ("NL", test["query_nl"]),
            ("EN", test["query_en"]),
            ("FR", test["query_fr"]),
        ]

        test_results = []

        for lang, query in queries:
            print(f"\n[{lang}] Query: '{query}'")

            try:
                docs = retrieve(query)

                if not docs:
                    print(f"     ❌ No results")
                    test_results.append(False)
                    continue

                # Check top result
                top_doc = docs[0]
                question = top_doc.metadata.get('question', '').lower()
                answer = top_doc.metadata.get('answer', '').lower()
                text = question + " " + answer

                # Determine correctness
                has_expected = test["expected_in_top_result"].lower() in text if test["expected_in_top_result"] else True
                has_not_expected = test["not_expected"] and test["not_expected"].lower() in text

                if has_expected and not has_not_expected:
                    print(f"     ✅ CORRECT")
                    print(f"        → {top_doc.metadata.get('question', '')[:70]}...")
                    test_results.append(True)
                elif has_not_expected:
                    print(f"     ❌ WRONG - Confused with different topic")
                    print(f"        → {top_doc.metadata.get('question', '')[:70]}...")
                    test_results.append(False)
                else:
                    print(f"     ⚠️  UNCLEAR")
                    print(f"        → {top_doc.metadata.get('question', '')[:70]}...")
                    test_results.append(False)

            except Exception as e:
                print(f"     ❌ ERROR: {e}")
                test_results.append(False)

        # Test case accuracy
        correct = sum(test_results)
        total = len(test_results)
        accuracy = (correct / total * 100) if total > 0 else 0

        print(f"\n  Test {i} accuracy: {correct}/{total} ({accuracy:.0f}%)")
        all_results.extend(test_results)

    # Overall accuracy
    correct = sum(all_results)
    total = len(all_results)
    accuracy = (correct / total * 100) if total > 0 else 0

    print("\n" + "="*70)
    print(f"OVERALL RESULTS: {correct}/{total} correct ({accuracy:.1f}%)")
    print("="*70)

    if accuracy >= 85:
        print("✅ EXCELLENT - Semantic search working great!")
    elif accuracy >= 70:
        print("✅ GOOD - Decent performance")
    elif accuracy >= 60:
        print("⚠️  OK - Room for improvement")
    else:
        print("❌ POOR - Needs work")

    print("\n" + "="*70)
    print("COMPARISON TO KEYWORD-ONLY SEARCH")
    print("="*70)
    print(f"Expected keyword-only:  ~65-70%")
    print(f"With vector embeddings: {accuracy:.1f}%")

    if accuracy > 70:
        print(f"✅ Improvement: +{accuracy - 67.5:.1f}%")
    else:
        print(f"⚠️  Change: {accuracy - 67.5:+.1f}%")

    print("="*70)

    return accuracy


if __name__ == "__main__":
    test_real_similar_questions()
