#!/usr/bin/env python
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ingest import ingest_jsonl

FAQ_JSONL = "app/data/all/faq/FAQAI.jsonl"

def main():
    print(f"Re-ingesting FAQ from: {FAQ_JSONL}")
    result = ingest_jsonl(FAQ_JSONL, source_type="faq")
    print(f"\nIngestion result:")
    print(f"  - Files indexed: {result.get('indexed_files', 0)}")
    print(f"  - Chunks indexed: {result.get('indexed_chunks', 0)}")
    if result.get('wrote_index'):
        print(f"  - Index written to: {result['wrote_index']}")
    if result.get('error'):
        print(f"  - Error: {result['error']}")
    print("\nEmbeddings updated successfully!")

if __name__ == "__main__":
    main()
