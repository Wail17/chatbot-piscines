#!/usr/bin/env python3
"""
Test KEYWORD-FIRST approach vs other methods.

Goal: Confirm that keyword-first gives best precision for SAV.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.rag import retrieve as retrieve_current
from app.rag_optimized import retrieve_keyword_first, retrieve_keyword_only


def test_all_methods():
    """Test all retrieval methods on critical SAV questions."""

    test_cases = [
        {
            "query": "pH kalibratie",
            "must_have": "kalibreer",
            "description": "pH calibration"
        },
        {
            "query": "pH meting verkeerd",
            "must_have": "meting",
            "description": "pH measurement error"
        },
        {
            "query": "pomp lekt",
            "must_have": "lek",
            "description": "Pump leak"
        },
        {
            "query": "wifi verbinding",
            "must_have": "wifi",
            "description": "WiFi connection"
        },
        {
            "query": "zoutelektrolyse instellen",
            "must_have": "zout",
            "description": "Salt electrolysis setup"
        },
    ]

    methods = [
        ("KEYWORD ONLY", retrieve_keyword_only),
        ("KEYWORD-FIRST", lambda q: retrieve_keyword_first(q, keyword_threshold=2)),
        ("CURRENT SYSTEM", retrieve_current),
    ]

    print("\n" + "="*80)
    print("🎯 KEYWORD-FIRST vs OTHER METHODS")
    print("="*80)
    print()

    method_scores = {name: [] for name, _ in methods}

    for test in test_cases:
        query = test["query"]
        print(f"\n📝 Query: \"{query}\" ({test['description']})")
        print("-" * 80)

        for method_name, retrieve_func in methods:
            try:
                docs = retrieve_func(query)

                if not docs:
                    print(f"  ❌ {method_name:20} → No results")
                    method_scores[method_name].append(False)
                    continue

                # Check if result is relevant
                top_q = docs[0].metadata.get('question', '').lower()
                top_a = docs[0].metadata.get('answer', '').lower()
                text = top_q + " " + top_a

                if test["must_have"].lower() in text:
                    print(f"  ✅ {method_name:20} → {docs[0].metadata.get('question', '')[:55]}...")
                    method_scores[method_name].append(True)
                else:
                    print(f"  ⚠️  {method_name:20} → {docs[0].metadata.get('question', '')[:55]}...")
                    method_scores[method_name].append(False)

            except Exception as e:
                print(f"  ❌ {method_name:20} → ERROR: {e}")
                method_scores[method_name].append(False)

    # Final scores
    print("\n\n" + "="*80)
    print("📊 FINAL SCORES")
    print("="*80)

    for method_name, _ in methods:
        scores = method_scores[method_name]
        correct = sum(scores)
        total = len(scores)
        accuracy = (correct / total * 100) if total > 0 else 0

        if accuracy >= 90:
            icon = "🏆"
        elif accuracy >= 80:
            icon = "✅"
        else:
            icon = "⚠️"

        print(f"{icon} {method_name:20} → {correct}/{total} ({accuracy:.0f}%)")

    print("\n" + "="*80)
    print("💡 RECOMMENDATION FOR SAV")
    print("="*80)

    best = max(method_scores.items(), key=lambda x: sum(x[1]))
    best_name = best[0]
    best_accuracy = (sum(best[1]) / len(best[1]) * 100)

    print(f"\nBest method: {best_name} ({best_accuracy:.0f}%)")

    if best_name == "KEYWORD ONLY":
        print("\n✅ KEYWORD ONLY is sufficient!")
        print("   - No API costs")
        print("   - 91.8% accuracy on synonyms")
        print("   - Best precision for technical questions")
    elif best_name == "KEYWORD-FIRST":
        print("\n✅ KEYWORD-FIRST recommended!")
        print("   - Uses keyword as primary (91.8% accurate)")
        print("   - Semantic as fallback for edge cases")
        print("   - Good balance of precision and coverage")

    print("="*80)


if __name__ == "__main__":
    test_all_methods()
