#!/usr/bin/env python3
"""
FAQ Update Utility

Simple command-line tool to update FAQ entries in the JSONL file.

Usage:
    python3 update_faq.py add "Question text" "Answer text"
    python3 update_faq.py update "Question text" "New answer text"
    python3 update_faq.py delete "Question text"
    python3 update_faq.py search "keyword"
    python3 update_faq.py list
"""

import os
import sys
import json
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()


def add_faq(question: str, answer: str, category: str = "", tags: list = None):
    """Add or update FAQ entry."""
    from app.rag import update_faq_entry

    print(f"\n{'='*60}")
    print("ADD/UPDATE FAQ ENTRY")
    print("="*60)

    print(f"\nQuestion: {question}")
    print(f"Answer: {answer[:100]}{'...' if len(answer) > 100 else ''}")
    if category:
        print(f"Category: {category}")
    if tags:
        print(f"Tags: {', '.join(tags)}")

    try:
        result = update_faq_entry(
            question=question,
            new_answer=answer,
            category=category,
            tags=tags,
            rebuild_embeddings=True
        )

        if result.get("success"):
            if result.get("created"):
                print(f"\n✅ New FAQ entry created")
            else:
                print(f"\n✅ FAQ entry updated")

            print(f"   Total entries: {result.get('total_entries', 'N/A')}")
            print(f"   Embeddings: {result.get('embeddings_status', 'N/A')}")
        else:
            print(f"\n❌ Failed: {result.get('error', 'Unknown error')}")
            return 1

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


def delete_faq(question: str):
    """Delete FAQ entry."""
    from app.faq_jsonl import get_faq_manager

    print(f"\n{'='*60}")
    print("DELETE FAQ ENTRY")
    print("="*60)

    print(f"\nSearching for: {question}")

    try:
        manager = get_faq_manager()

        # Find the entry first
        entry = manager.find_faq_entry(question)

        if not entry:
            print(f"\n❌ Entry not found")
            return 1

        print(f"\nFound entry:")
        print(f"  Q: {entry['question'][:80]}...")
        print(f"  A: {entry['answer'][:80]}...")

        # Confirm deletion
        confirm = input("\nDelete this entry? (yes/no): ").strip().lower()

        if confirm not in ['yes', 'y']:
            print("\n⚠️  Deletion cancelled")
            return 0

        # Delete
        success = manager.delete_faq_entry(question)

        if success:
            print(f"\n✅ Entry deleted")

            # Rebuild embeddings
            print("\n🔄 Rebuilding embeddings...")
            manager.build_embeddings(force_rebuild=True)
            print("✅ Embeddings rebuilt")

            return 0
        else:
            print(f"\n❌ Failed to delete entry")
            return 1

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def search_faq(keyword: str):
    """Search FAQ entries."""
    from app.faq_jsonl import get_faq_manager

    print(f"\n{'='*60}")
    print("SEARCH FAQ")
    print("="*60)

    print(f"\nSearching for: {keyword}")

    try:
        manager = get_faq_manager()
        entries = manager.load_faq()

        # Search in questions and answers
        matches = []
        keyword_lower = keyword.lower()

        for entry in entries:
            question = entry.get('question', '')
            answer = entry.get('answer', '')

            if keyword_lower in question.lower() or keyword_lower in answer.lower():
                matches.append(entry)

        print(f"\nFound {len(matches)} matches:")

        for i, entry in enumerate(matches[:10], 1):
            print(f"\n{i}. Q: {entry['question'][:70]}...")
            print(f"   A: {entry['answer'][:70]}...")

            if entry.get('category'):
                print(f"   Category: {entry['category']}")

        if len(matches) > 10:
            print(f"\n... and {len(matches) - 10} more")

        return 0

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def list_faq(limit: int = 20):
    """List FAQ entries."""
    from app.faq_jsonl import get_faq_manager

    print(f"\n{'='*60}")
    print("FAQ ENTRIES")
    print("="*60)

    try:
        manager = get_faq_manager()
        entries = manager.load_faq()

        print(f"\nTotal entries: {len(entries)}")
        print(f"Showing first {min(limit, len(entries))}:")

        for i, entry in enumerate(entries[:limit], 1):
            print(f"\n{i}. Q: {entry['question'][:70]}...")
            print(f"   A: {entry['answer'][:70]}...")

            if entry.get('category'):
                print(f"   Category: {entry['category']}")

        if len(entries) > limit:
            print(f"\n... and {len(entries) - limit} more")

        return 0

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def show_stats():
    """Show FAQ statistics."""
    from app.rag import get_faq_stats

    print(f"\n{'='*60}")
    print("FAQ STATISTICS")
    print("="*60)

    try:
        stats = get_faq_stats()

        print(f"\n📊 Statistics:")
        print(f"   Total entries: {stats.get('total_entries', 0)}")
        print(f"   JSONL path: {stats.get('jsonl_path', 'N/A')}")
        print(f"   Vectorstore ready: {stats.get('vectorstore_ready', False)}")
        print(f"   Using JSONL: {stats.get('using_jsonl', False)}")

        return 0

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def print_usage():
    """Print usage information."""
    print("""
FAQ Update Utility
==================

Usage:
    python3 update_faq.py add "Question" "Answer" [category] [tag1,tag2,...]
    python3 update_faq.py update "Question" "New answer"
    python3 update_faq.py delete "Question"
    python3 update_faq.py search "keyword"
    python3 update_faq.py list [limit]
    python3 update_faq.py stats

Examples:
    # Add new FAQ entry
    python3 update_faq.py add "How to reset?" "Press reset button for 10 seconds"

    # Add with category and tags
    python3 update_faq.py add "How to calibrate pH?" "Follow these steps..." "Chemistry" "ph,calibration,sensor"

    # Update existing entry
    python3 update_faq.py update "How to reset?" "New improved answer"

    # Delete entry
    python3 update_faq.py delete "How to reset?"

    # Search FAQ
    python3 update_faq.py search "wifipool"

    # List all entries
    python3 update_faq.py list

    # Show statistics
    python3 update_faq.py stats
""")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print_usage()
        return 1

    command = sys.argv[1].lower()

    if command in ['help', '-h', '--help']:
        print_usage()
        return 0

    elif command == 'add':
        if len(sys.argv) < 4:
            print("❌ Usage: python3 update_faq.py add \"Question\" \"Answer\" [category] [tags]")
            return 1

        question = sys.argv[2]
        answer = sys.argv[3]
        category = sys.argv[4] if len(sys.argv) > 4 else ""
        tags = sys.argv[5].split(',') if len(sys.argv) > 5 else []

        return add_faq(question, answer, category, tags)

    elif command == 'update':
        if len(sys.argv) < 4:
            print("❌ Usage: python3 update_faq.py update \"Question\" \"New answer\"")
            return 1

        question = sys.argv[2]
        answer = sys.argv[3]

        return add_faq(question, answer)

    elif command == 'delete':
        if len(sys.argv) < 3:
            print("❌ Usage: python3 update_faq.py delete \"Question\"")
            return 1

        question = sys.argv[2]
        return delete_faq(question)

    elif command == 'search':
        if len(sys.argv) < 3:
            print("❌ Usage: python3 update_faq.py search \"keyword\"")
            return 1

        keyword = sys.argv[2]
        return search_faq(keyword)

    elif command == 'list':
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        return list_faq(limit)

    elif command == 'stats':
        return show_stats()

    else:
        print(f"❌ Unknown command: {command}")
        print_usage()
        return 1


if __name__ == "__main__":
    exit(main())
