#!/usr/bin/env python3
"""
Test ALL FAQ questions to get accurate precision statistics.

For each FAQ question, we:
1. Use it as a search query
2. Check if the system returns the SAME FAQ as top result
3. Calculate accuracy percentage

This gives us REAL accuracy on the entire dataset.
"""

import sys
import json
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.keyword_search import keyword_search_as_documents
from app.rag import retrieve as retrieve_current
from app.rag_optimized import retrieve_keyword_first
from app.rag_pure import retrieve_pure_semantic


def load_all_faqs() -> List[Dict[str, Any]]:
    """Load all FAQ questions from JSONL."""
    faqs = []
    faq_file = "app/data/faq.jsonl"

    with open(faq_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                faq = json.loads(line)
                question = faq.get('question', '').strip()
                if question:
                    faqs.append(faq)
            except json.JSONDecodeError:
                continue

    return faqs


def normalize_question(q: str) -> str:
    """Normalize question for comparison."""
    return q.lower().strip().replace('\n', ' ').replace('  ', ' ')


def test_method(method_name: str, retrieve_func, faqs: List[Dict], sample_size: int = None):
    """
    Test a retrieval method on all FAQs.

    Args:
        method_name: Name of the method
        retrieve_func: Retrieval function
        faqs: List of FAQ entries
        sample_size: If set, only test first N questions (for speed)

    Returns:
        Dict with results
    """
    print(f"\n{'='*80}")
    print(f"Testing: {method_name}")
    print(f"{'='*80}")

    test_faqs = faqs[:sample_size] if sample_size else faqs
    total = len(test_faqs)

    results = {
        "method": method_name,
        "total": total,
        "exact_match": 0,       # Top result is exact same question
        "partial_match": 0,     # Top result is very similar
        "wrong": 0,             # Top result is different
        "no_results": 0,        # No results found
        "errors": 0,            # Error during search
        "details": []
    }

    print(f"Testing {total} FAQ questions...")
    print()

    for i, faq in enumerate(test_faqs, 1):
        original_question = faq['question']
        normalized_original = normalize_question(original_question)

        # Progress indicator
        if i % 10 == 0:
            print(f"  Progress: {i}/{total} ({i/total*100:.0f}%)...", end='\r')

        try:
            # Search using this FAQ question as query
            docs = retrieve_func(original_question)

            if not docs:
                results["no_results"] += 1
                results["details"].append({
                    "index": i,
                    "question": original_question[:60],
                    "status": "no_results"
                })
                continue

            # Check top result
            top_result_question = docs[0].metadata.get('question', '')
            normalized_top = normalize_question(top_result_question)

            # Exact match
            if normalized_top == normalized_original:
                results["exact_match"] += 1
                status = "exact"
            # Partial match (at least 70% of words match)
            elif len(set(normalized_top.split()) & set(normalized_original.split())) / max(len(normalized_original.split()), 1) > 0.7:
                results["partial_match"] += 1
                status = "partial"
            else:
                results["wrong"] += 1
                status = "wrong"
                # Store wrong matches for analysis
                results["details"].append({
                    "index": i,
                    "question": original_question[:60],
                    "got": top_result_question[:60],
                    "status": status
                })

        except Exception as e:
            results["errors"] += 1
            results["details"].append({
                "index": i,
                "question": original_question[:60],
                "error": str(e),
                "status": "error"
            })

    print(f"  Progress: {total}/{total} (100%) - DONE!          ")

    # Calculate accuracy
    accuracy = ((results["exact_match"] + results["partial_match"]) / total * 100) if total > 0 else 0
    exact_only = (results["exact_match"] / total * 100) if total > 0 else 0

    results["accuracy"] = accuracy
    results["exact_accuracy"] = exact_only

    return results


def print_results(results: Dict):
    """Print results in a nice format."""
    method = results["method"]
    total = results["total"]

    print(f"\n{'─'*80}")
    print(f"📊 RESULTS: {method}")
    print(f"{'─'*80}")
    print(f"Total questions tested: {total}")
    print()
    print(f"✅ Exact match (top 1):  {results['exact_match']:>4} ({results['exact_accuracy']:.1f}%)")
    print(f"⚠️  Partial match:        {results['partial_match']:>4} ({results['partial_match']/total*100:.1f}%)")
    print(f"❌ Wrong result:         {results['wrong']:>4} ({results['wrong']/total*100:.1f}%)")
    print(f"⚪ No results:           {results['no_results']:>4} ({results['no_results']/total*100:.1f}%)")
    print(f"💥 Errors:               {results['errors']:>4} ({results['errors']/total*100:.1f}%)")
    print()
    print(f"🎯 OVERALL ACCURACY: {results['accuracy']:.1f}% (exact + partial)")
    print(f"{'─'*80}")


def compare_methods():
    """Compare all methods on all FAQs."""

    print("\n" + "="*80)
    print("🔬 COMPREHENSIVE FAQ ACCURACY TEST")
    print("="*80)
    print("\nTesting ALL FAQ questions to measure real-world accuracy...")
    print()

    # Load FAQs
    faqs = load_all_faqs()
    print(f"✅ Loaded {len(faqs)} FAQ questions")

    # Methods to test
    methods = [
        ("KEYWORD SEARCH (current)", keyword_search_as_documents),
        ("CURRENT SYSTEM (hybrid)", retrieve_current),
        ("KEYWORD-FIRST", retrieve_keyword_first),
        ("PURE EMBEDDINGS (0.6)", lambda q: retrieve_pure_semantic(q, score_threshold=0.6)),
    ]

    # Test each method
    all_results = []

    for method_name, retrieve_func in methods:
        # Wrap the function to match expected signature
        if method_name == "KEYWORD SEARCH (current)":
            func = lambda q: retrieve_func(q, top_k=5)
        else:
            func = retrieve_func

        results = test_method(method_name, func, faqs)
        print_results(results)
        all_results.append(results)

    # Comparison summary
    print("\n\n" + "="*80)
    print("📊 FINAL COMPARISON")
    print("="*80)
    print()
    print(f"{'Method':<30} {'Exact':<12} {'Total Acc':<12} {'Wrong':<10} {'Verdict'}")
    print("─"*80)

    for res in all_results:
        method = res['method']
        exact = f"{res['exact_accuracy']:.1f}%"
        total = f"{res['accuracy']:.1f}%"
        wrong = f"{res['wrong']}/{res['total']}"

        # Verdict
        if res['accuracy'] >= 95:
            verdict = "🏆 EXCELLENT"
        elif res['accuracy'] >= 90:
            verdict = "✅ VERY GOOD"
        elif res['accuracy'] >= 85:
            verdict = "✅ GOOD"
        elif res['accuracy'] >= 75:
            verdict = "⚠️  OK"
        else:
            verdict = "❌ POOR"

        print(f"{method:<30} {exact:<12} {total:<12} {wrong:<10} {verdict}")

    print("="*80)

    # Best method
    best = max(all_results, key=lambda x: x['accuracy'])
    print(f"\n🏆 BEST METHOD: {best['method']}")
    print(f"   Accuracy: {best['accuracy']:.1f}%")
    print(f"   Exact matches: {best['exact_match']}/{best['total']} ({best['exact_accuracy']:.1f}%)")

    # Show some wrong matches for best method
    if best['details']:
        print(f"\n❌ Examples of wrong matches:")
        for detail in best['details'][:5]:  # Show first 5
            if detail.get('status') == 'wrong':
                print(f"   Query: {detail['question']}...")
                print(f"   Got:   {detail.get('got', 'N/A')}...")
                print()

    print("="*80)

    return all_results


if __name__ == "__main__":
    compare_methods()
