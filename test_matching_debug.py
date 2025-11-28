#!/usr/bin/env python3
"""
Direct test of FAQ matching without running the full server.
This script tests the _match_row_with_clarify function directly.
"""

import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(__file__))

# Import the main module (this will trigger FAQ loading)
from app.main import _match_row_with_clarify, _FAQ, _normalize_query, _row_norms, _similarity

def main():
    test_questions = [
        "Hoe reset ik mijn wifipool?",
        "Wat is de watertemperatuur?",
        "Hoe kalibreer ik de pH sensor?",
        "Reset wifipool",
        "Generieke vraag zonder match"
    ]

    print("=" * 80)
    print("FAQ MATCHING DEBUG TEST")
    print("=" * 80)
    print(f"\nTotal FAQ items loaded: {len(_FAQ)}")

    if _FAQ:
        print(f"\nFirst 3 FAQ questions:")
        for i, row in enumerate(_FAQ[:3], 1):
            q = row.get("question", "NO_QUESTION")
            print(f"  {i}. {q[:100]}")
    else:
        print("\nERROR: FAQ is empty!")
        return

    print("\n" + "=" * 80)
    print("TESTING INDIVIDUAL QUESTIONS")
    print("=" * 80)

    for question in test_questions:
        print(f"\n{'='*80}")
        print(f"TESTING: '{question}'")
        print(f"{'='*80}")

        try:
            matched_row, clarify_rows = _match_row_with_clarify(question)

            print(f"\n--- RESULT ---")
            if matched_row:
                print(f"✓ MATCHED!")
                print(f"  Question: {matched_row.get('question', 'N/A')[:150]}")
                print(f"  Answer: {matched_row.get('answer', 'N/A')[:150]}")
                print(f"  Category: {matched_row.get('category', 'N/A')}")
            elif clarify_rows:
                print(f"? NEEDS CLARIFICATION ({len(clarify_rows)} options)")
                for i, row in enumerate(clarify_rows[:3], 1):
                    print(f"  {i}. {row.get('question', 'N/A')[:100]}")
            else:
                print(f"✗ NO MATCH FOUND")
            print(f"--- END RESULT ---\n")

        except Exception as e:
            print(f"\n!!! ERROR during matching: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    main()
