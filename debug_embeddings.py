#!/usr/bin/env python3
"""Debug embeddings to understand what's happening."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.faq_jsonl import get_faq_manager


def debug_embeddings():
    """Debug embeddings and similarity search."""

    print("\n" + "="*80)
    print("🔍 DEBUGGING EMBEDDINGS")
    print("="*80)

    # Get FAQ manager
    faq_mgr = get_faq_manager()

    # Build embeddings if needed
    if not faq_mgr.vectorstore:
        print("\nBuilding embeddings...")
        faq_mgr.build_embeddings()

    if not faq_mgr.vectorstore:
        print("❌ ERROR: No vectorstore!")
        return

    print(f"✅ Vectorstore loaded")
    print(f"   Type: {type(faq_mgr.vectorstore)}")

    # Test query
    query = "pH kalibratie"
    print(f"\n📝 Test query: '{query}'")
    print("-" * 80)

    # Try different search methods
    print("\n1. similarity_search (no scores):")
    try:
        results = faq_mgr.vectorstore.similarity_search(query, k=3)
        print(f"   Found {len(results)} results")
        for i, doc in enumerate(results, 1):
            q = doc.metadata.get('question', '')
            print(f"   {i}. {q[:70]}...")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Try with scores
    print("\n2. similarity_search_with_score:")
    try:
        results = faq_mgr.vectorstore.similarity_search_with_score(query, k=5)
        print(f"   Found {len(results)} results")
        for i, (doc, score) in enumerate(results, 1):
            q = doc.metadata.get('question', '')
            print(f"   {i}. [score={score:.4f}] {q[:60]}...")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Try relevance scores
    print("\n3. similarity_search_with_relevance_scores:")
    try:
        results = faq_mgr.vectorstore.similarity_search_with_relevance_scores(query, k=5)
        print(f"   Found {len(results)} results")
        for i, (doc, score) in enumerate(results, 1):
            q = doc.metadata.get('question', '')
            print(f"   {i}. [relevance={score:.4f}] {q[:60]}...")
    except AttributeError:
        print("   ⚠️  Method not available")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Check available methods
    print("\n4. Available search methods:")
    methods = [m for m in dir(faq_mgr.vectorstore) if 'search' in m.lower()]
    for method in methods:
        print(f"   - {method}")

    print("\n" + "="*80)


if __name__ == "__main__":
    debug_embeddings()
