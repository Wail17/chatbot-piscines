"""
Simple test script to verify the admin FAQ system can load existing data.
Run with: python test_admin_faq.py
"""
import sys
import os
import json

# Add app directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_load_faq():
    """Test loading the FAQ JSONL file."""
    from app.admin_routes import load_faq_jsonl, normalize_faq_item

    print("Testing FAQ loading...")
    try:
        items = load_faq_jsonl()
        print(f"✓ Successfully loaded {len(items)} FAQ items")

        if items:
            # Show first item as example
            first_item = items[0]
            print(f"\nFirst item structure:")
            print(f"  ID: {first_item.get('id', 'N/A')}")
            print(f"  Category: {first_item.get('Categorie', 'N/A')}")
            print(f"  Question: {first_item.get('Vraag', 'N/A')[:50]}...")
            print(f"  Answer: {first_item.get('Antwoord', 'N/A')[:50]}...")
            print(f"  Video: {first_item.get('Filmpje', 'N/A')}")
            print(f"  Tags: {first_item.get('tags', [])}")

        return True
    except Exception as e:
        print(f"✗ Error loading FAQ: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_normalize():
    """Test normalization of FAQ items."""
    from app.admin_routes import normalize_faq_item

    print("\n\nTesting normalization...")

    # Test with Dutch field names
    test_item = {
        "Categorie": "Test",
        "Vraag": "Test question?",
        "Antwoord": "Test answer",
        "Filmpje": "https://youtube.com/test",
        "tags": ["test", "demo"]
    }

    normalized = normalize_faq_item(test_item)
    print("✓ Normalization successful")
    print(f"  Generated ID: {normalized.get('id', 'N/A')}")
    print(f"  Category: {normalized.get('Categorie', 'N/A')}")
    print(f"  Question: {normalized.get('Vraag', 'N/A')}")

    return True


def test_create_entry():
    """Test creating a new FAQ entry."""
    from app.admin_routes import create_jsonl_entry

    print("\n\nTesting entry creation...")

    entry = create_jsonl_entry(
        category="Test Category",
        question="How to test?",
        answer="Just run the tests!",
        video_url="https://youtube.com/test",
        tags=["test", "demo"]
    )

    print("✓ Entry creation successful")
    print(f"  ID: {entry['id']}")
    print(f"  Category: {entry['Categorie']}")
    print(f"  Question: {entry['Vraag']}")
    print(f"  Created at: {entry['created_at']}")
    print(f"  Updated at: {entry['updated_at']}")

    # Verify it can be serialized to JSON
    json_str = json.dumps(entry, ensure_ascii=False)
    print(f"  JSON length: {len(json_str)} chars")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Admin FAQ System Tests")
    print("=" * 60)

    all_passed = True

    if not test_load_faq():
        all_passed = False

    if not test_normalize():
        all_passed = False

    if not test_create_entry():
        all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed")
        sys.exit(1)
    print("=" * 60)
