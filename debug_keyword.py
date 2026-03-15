#!/usr/bin/env python3
"""Debug keyword search for pH kalibratie."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.keyword_search import keyword_search_as_documents


def debug_keyword():
    """Debug keyword search."""

    query = "pH kalibratie"
    print(f"\n🔍 Debugging keyword search for: '{query}'")
    print("="*80)

    results = keyword_search_as_documents(query, top_k=10)

    print(f"\nFound {len(results)} results:")
    print("-"*80)

    for i, doc in enumerate(results, 1):
        q = doc.metadata.get('question', '')
        score = doc.metadata.get('score', 0)
        print(f"\n{i}. [score={score:.3f}] {q}")

        # Check if it contains calibration keywords
        text = (q + " " + doc.metadata.get('answer', '')).lower()
        has_kalibreer = "kalibreer" in text
        has_meting = "meting" in text or "meet" in text

        print(f"   Has 'kalibreer': {has_kalibreer}")
        print(f"   Has 'meting': {has_meting}")

    print("\n" + "="*80)


if __name__ == "__main__":
    debug_keyword()
