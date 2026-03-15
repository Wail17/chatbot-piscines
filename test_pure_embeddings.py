#!/usr/bin/env python3
"""
Test PURE embeddings vs HYBRID system.

Compares:
- Current hybrid system (keyword + synonyms + embeddings + expansions)
- Pure semantic search (embeddings only, no noise)

Goal: Find optimal configuration for SAV (customer support).
For SAV, precision is CRITICAL - wrong answer = angry customer!
"""

import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from app.rag import retrieve as retrieve_hybrid
from app.rag_pure import retrieve_pure_semantic, retrieve_hybrid_smart


def test_critical_sav_questions():
    """
    Test critical SAV questions where precision is essential.

    These are real customer support scenarios where wrong answer = problem:
    - "pump leaking" vs "pump not working" → Different solutions!
    - "pH calibration" vs "pH measurement wrong" → Different actions!
    - "WiFi reset" vs "factory reset" → Don't want to lose all data!
    """

    # Critical test cases - MUST NOT confuse these!
    test_cases = [
        {
            "id": 1,
            "query_nl": "pH kalibratie",
            "query_en": "pH calibration",
            "must_contain": ["kalibreer"],  # Must mention calibration
            "must_not_contain": ["meting wijkt"],  # Must NOT be about measurement errors
            "description": "pH CALIBRATION (not measurement error)",
            "critical": "🔴 CRITICAL - Wrong answer = customer calibrates when sensor broken"
        },
        {
            "id": 2,
            "query_nl": "pH meting is verkeerd",
            "query_en": "pH measurement wrong",
            "must_contain": ["meting", "wijkt"],
            "must_not_contain": ["kalibreer buffer"],  # Not pure calibration procedure
            "description": "pH MEASUREMENT ERROR (not calibration)",
            "critical": "🔴 CRITICAL - Wrong answer = customer wastes time"
        },
        {
            "id": 3,
            "query_nl": "pomp lekt water",
            "query_en": "pump leaking",
            "must_contain": ["lek"],
            "must_not_contain": ["werkt niet", "draait niet"],
            "description": "PUMP LEAK (not malfunction)",
            "critical": "🔴 CRITICAL - Leak needs immediate action, different from not working"
        },
        {
            "id": 4,
            "query_nl": "wifi verbinding probleem",
            "query_en": "wifi connection issue",
            "must_contain": ["wifi", "koppel"],
            "must_not_contain": [],  # General WiFi issues
            "description": "WIFI CONNECTION",
            "critical": "🟡 IMPORTANT - Common support issue"
        },
    ]

    print("\n" + "="*80)
    print("🎯 SAV PRECISION TEST - CRITICAL CUSTOMER SUPPORT QUESTIONS")
    print("="*80)
    print("\nTesting 3 methods:")
    print("1. HYBRID (current) - keyword + synonyms + embeddings + expansions")
    print("2. PURE SEMANTIC - embeddings only, high threshold (0.75)")
    print("3. SMART HYBRID - semantic first, keyword fallback")
    print()

    methods = [
        ("HYBRID (current)", retrieve_hybrid),
        ("PURE (0.75)", lambda q: retrieve_pure_semantic(q, score_threshold=0.75)),
        ("SMART HYBRID", retrieve_hybrid_smart),
    ]

    # Results tracking
    method_results = {name: [] for name, _ in methods}

    for test in test_cases:
        print("\n" + "="*80)
        print(f"TEST {test['id']}: {test['description']}")
        print("="*80)
        print(f"{test['critical']}")
        print()

        # Test both languages
        for lang, query in [("NL", test["query_nl"]), ("EN", test["query_en"])]:
            print(f"\n[{lang}] Query: \"{query}\"")
            print("-" * 80)

            for method_name, retrieve_func in methods:
                try:
                    docs = retrieve_func(query)

                    if not docs:
                        print(f"  ❌ {method_name:20} → No results")
                        method_results[method_name].append(False)
                        continue

                    # Check top result
                    top_doc = docs[0]
                    question = top_doc.metadata.get('question', '').lower()
                    answer = top_doc.metadata.get('answer', '').lower()
                    text = question + " " + answer

                    # Validate result
                    has_must = any(kw.lower() in text for kw in test["must_contain"]) if test["must_contain"] else True
                    has_must_not = any(kw.lower() in text for kw in test["must_not_contain"]) if test["must_not_contain"] else False

                    if has_must and not has_must_not:
                        icon = "✅"
                        verdict = "CORRECT"
                        method_results[method_name].append(True)
                    elif has_must_not:
                        icon = "❌"
                        verdict = "WRONG - Confused with different issue!"
                        method_results[method_name].append(False)
                    else:
                        icon = "⚠️"
                        verdict = "UNCLEAR"
                        method_results[method_name].append(False)

                    # Show result
                    result_preview = top_doc.metadata.get('question', '')[:60]
                    print(f"  {icon} {method_name:20} → {verdict}")
                    if icon != "✅":
                        print(f"     Got: {result_preview}...")

                except Exception as e:
                    print(f"  ❌ {method_name:20} → ERROR: {e}")
                    method_results[method_name].append(False)

    # Final scores
    print("\n\n" + "="*80)
    print("📊 FINAL RESULTS")
    print("="*80)

    for method_name, _ in methods:
        results = method_results[method_name]
        correct = sum(results)
        total = len(results)
        accuracy = (correct / total * 100) if total > 0 else 0

        if accuracy >= 90:
            icon = "🏆"
            verdict = "EXCELLENT"
        elif accuracy >= 80:
            icon = "✅"
            verdict = "GOOD"
        elif accuracy >= 70:
            icon = "⚠️"
            verdict = "OK"
        else:
            icon = "❌"
            verdict = "POOR"

        print(f"{icon} {method_name:20} → {correct}/{total} correct ({accuracy:.1f}%) - {verdict}")

    print("\n" + "="*80)
    print("🎯 RECOMMENDATION FOR SAV")
    print("="*80)

    # Find best method
    best_method = max(method_results.items(), key=lambda x: sum(x[1]))
    best_name, best_results = best_method
    best_accuracy = (sum(best_results) / len(best_results) * 100) if best_results else 0

    print(f"Best method: {best_name} ({best_accuracy:.1f}%)")

    if best_accuracy >= 85:
        print("✅ Ready for production SAV!")
    elif best_accuracy >= 75:
        print("⚠️  Acceptable but needs monitoring")
    else:
        print("❌ NOT ready for SAV - needs more optimization")

    print("="*80)


if __name__ == "__main__":
    test_critical_sav_questions()
