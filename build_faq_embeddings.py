#!/usr/bin/env python3
"""
Build embeddings from JSONL FAQ file.

This script initializes the JSONL-based FAQ system and builds
the Chroma vectorstore embeddings for semantic search.
"""

import os
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()


def main():
    """Build FAQ embeddings from JSONL."""
    print("\n" + "="*60)
    print("FAQ EMBEDDING BUILDER")
    print("="*60)

    # Check for OpenAI API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("\n❌ ERROR: OPENAI_API_KEY environment variable not set")
        print("   Embeddings require OpenAI API access")
        print("\nPlease set your API key:")
        print("   export OPENAI_API_KEY='your-key-here'")
        return 1

    print(f"\n✅ OpenAI API key found")

    # Import after environment check
    try:
        from app.rag import initialize_faq_jsonl, get_faq_stats
        from app.faq_jsonl import DEFAULT_FAQ_JSONL
    except ImportError as e:
        print(f"\n❌ Import error: {e}")
        print("   Make sure all dependencies are installed")
        return 1

    # Check if JSONL file exists
    if not os.path.exists(DEFAULT_FAQ_JSONL):
        print(f"\n❌ FAQ file not found: {DEFAULT_FAQ_JSONL}")
        print("   Run migrate_to_jsonl.py first to create the FAQ file")
        return 1

    print(f"\n📁 FAQ file: {DEFAULT_FAQ_JSONL}")

    # Count entries in JSONL
    import json
    entry_count = 0
    with open(DEFAULT_FAQ_JSONL, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                entry_count += 1

    print(f"📊 FAQ entries: {entry_count}")

    # Initialize FAQ and build embeddings
    print("\n" + "-"*60)
    print("BUILDING EMBEDDINGS...")
    print("-"*60)

    try:
        result = initialize_faq_jsonl(
            jsonl_path=DEFAULT_FAQ_JSONL,
            rebuild_embeddings=True
        )

        if result.get("success"):
            print(f"\n✅ Embeddings built successfully!")
            print(f"   Entries loaded: {result.get('entries_loaded', 0)}")
            print(f"   Embeddings: {result.get('embeddings_built', 0)}")
            print(f"   Chroma directory: {result.get('chroma_dir', 'N/A')}")

            # Get stats
            stats = get_faq_stats()
            print(f"\n📊 FAQ Statistics:")
            print(f"   Total entries: {stats.get('total_entries', 0)}")
            print(f"   Vectorstore ready: {stats.get('vectorstore_ready', False)}")

            # Test a simple query
            print("\n" + "-"*60)
            print("TESTING RETRIEVAL...")
            print("-"*60)

            from app.rag import retrieve

            test_queries = [
                "How do I reset my Wifipool?",
                "pH sensor calibration",
                "WiFi connection problems"
            ]

            for query in test_queries:
                print(f"\nTest query: '{query}'")
                try:
                    docs = retrieve(query)
                    if docs:
                        print(f"✅ Retrieved {len(docs)} documents")
                        # Show top result
                        if hasattr(docs[0], 'metadata'):
                            meta = docs[0].metadata
                            question = meta.get('question', 'N/A')
                            print(f"   Top match: {question[:80]}...")
                    else:
                        print("⚠️  No documents retrieved")
                except Exception as e:
                    print(f"❌ Retrieval error: {e}")

            print("\n" + "="*60)
            print("✅ FAQ EMBEDDINGS BUILD COMPLETE")
            print("="*60)
            print("\nNext steps:")
            print("1. Run test_jsonl.py to test the full system")
            print("2. Start the chatbot and verify JSONL-based responses")
            print("3. Update FAQ entries using update_faq_entry() function")
            print("="*60)

            return 0

        else:
            print(f"\n❌ Failed to build embeddings")
            print(f"   Error: {result.get('error', 'Unknown error')}")
            return 1

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
