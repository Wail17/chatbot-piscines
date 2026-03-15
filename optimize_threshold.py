#!/usr/bin/env python3
"""
Find optimal similarity threshold for embeddings.

Tests different thresholds to find sweet spot between:
- Precision (avoid wrong answers)
- Recall (don't miss good answers)

For SAV: Precision > Recall (better no answer than wrong answer!)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.rag_pure import retrieve_pure_semantic


def test_thresholds():
    """Test critical questions with different similarity thresholds."""

    # Critical test cases
    test_cases = [
        {
            "query": "pH kalibratie",
            "must_have": "kalibreer",
            "must_not": "meting wijkt",
        },
        {
            "query": "pH calibration",
            "must_have": "kalibreer",
            "must_not": "meting wijkt",
        },
        {
            "query": "pomp lekt water",
            "must_have": "lek",
            "must_not": "werkt niet",
        },
        {
            "query": "pump leaking",
            "must_have": "lek",
            "must_not": "werkt niet",
        },
        {
            "query": "wifi verbinding",
            "must_have": "wifi",
            "must_not": None,
        },
    ]

    # Test thresholds from strict to lenient
    thresholds = [0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5]

    print("\n" + "="*80)
    print("🔍 THRESHOLD OPTIMIZATION")
    print("="*80)
    print("\nTesting thresholds from strict (0.8) to lenient (0.5)...")
    print()

    results_by_threshold = {}

    for threshold in thresholds:
        print(f"\n{'='*80}")
        print(f"THRESHOLD: {threshold}")
        print(f"{'='*80}")

        correct = 0
        total = 0
        no_results = 0

        for test in test_cases:
            query = test["query"]
            docs = retrieve_pure_semantic(query, k=3, score_threshold=threshold)

            if not docs:
                print(f"  ⚪ '{query[:40]}...' → No results")
                no_results += 1
                total += 1
                continue

            # Check first result
            top_doc = docs[0]
            question = top_doc.metadata.get('question', '').lower()
            answer = top_doc.metadata.get('answer', '').lower()
            text = question + " " + answer

            has_must = test["must_have"].lower() in text if test["must_have"] else True
            has_must_not = test["must_not"] and test["must_not"].lower() in text

            if has_must and not has_must_not:
                print(f"  ✅ '{query[:40]}...' → Correct")
                correct += 1
            elif has_must_not:
                print(f"  ❌ '{query[:40]}...' → Wrong (confused)")
            else:
                print(f"  ⚠️  '{query[:40]}...' → Unclear")

            total += 1

        # Calculate metrics
        accuracy = (correct / total * 100) if total > 0 else 0
        no_result_rate = (no_results / total * 100) if total > 0 else 0

        results_by_threshold[threshold] = {
            "accuracy": accuracy,
            "correct": correct,
            "total": total,
            "no_results": no_results,
            "no_result_rate": no_result_rate
        }

        print(f"\n  Accuracy: {correct}/{total} ({accuracy:.1f}%)")
        print(f"  No results: {no_results}/{total} ({no_result_rate:.1f}%)")

    # Summary
    print("\n\n" + "="*80)
    print("📊 SUMMARY")
    print("="*80)
    print(f"\n{'Threshold':<12} {'Accuracy':<15} {'No Results':<15} {'Verdict':<20}")
    print("-" * 80)

    best_threshold = None
    best_score = 0

    for threshold in thresholds:
        r = results_by_threshold[threshold]
        acc = r["accuracy"]
        no_res = r["no_result_rate"]

        # Score: prefer accuracy but penalize too many no-results
        # For SAV: accuracy is more important than coverage
        score = acc - (no_res * 0.3)  # Small penalty for no results

        # Verdict
        if acc >= 85 and no_res < 30:
            verdict = "✅ EXCELLENT"
        elif acc >= 75 and no_res < 40:
            verdict = "⚠️  GOOD"
        elif acc >= 60:
            verdict = "⚠️  OK"
        else:
            verdict = "❌ POOR"

        print(f"{threshold:<12.2f} {acc:>6.1f}%  ({r['correct']}/{r['total']:<2}) {no_res:>6.1f}%  ({r['no_results']}/{r['total']:<2}) {verdict}")

        if score > best_score:
            best_score = score
            best_threshold = threshold

    print("\n" + "="*80)
    print("🎯 RECOMMENDATION")
    print("="*80)
    print(f"\nOptimal threshold: {best_threshold}")
    print(f"Accuracy: {results_by_threshold[best_threshold]['accuracy']:.1f}%")
    print(f"No results rate: {results_by_threshold[best_threshold]['no_result_rate']:.1f}%")
    print("\nFor SAV (customer support):")
    print("- Use threshold 0.65-0.70 for good balance")
    print("- Higher threshold (0.75+) = very strict, fewer matches")
    print("- Lower threshold (0.55-) = more matches but risk confusion")
    print("="*80)


if __name__ == "__main__":
    test_thresholds()
