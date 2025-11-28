#!/usr/bin/env python3
"""
Direct test of chat functionality to see actual errors
"""
import sys
import os
import traceback

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 80)
print("TESTING: Direct chat endpoint call")
print("=" * 80)

# Import the main module to trigger FAQ loading
try:
    print("\n[1] Importing app.main...")
    from app.main import _FAQ, _match_row_with_clarify, _respond_for_row
    print(f"✓ Imported successfully. FAQ has {len(_FAQ)} items")
except Exception as e:
    print(f"✗ Import failed: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test the matching function
test_questions = [
    "Waar vind ik het serienummer?",
    "Hoe reset ik mijn wifipool?",
]

for question in test_questions:
    print("\n" + "=" * 80)
    print(f"TESTING: {question}")
    print("=" * 80)

    try:
        print("\n[2] Calling _match_row_with_clarify...")
        matched_row, clarify_rows = _match_row_with_clarify(question)

        if matched_row:
            print(f"✓ Matched row found!")
            print(f"  Question: {matched_row.get('question', 'N/A')[:80]}")
            print(f"  Answer: {matched_row.get('answer', 'N/A')[:80]}")

            # Try to generate response
            print("\n[3] Calling _respond_for_row...")
            response = _respond_for_row(matched_row, "nl", "test_client", question)

            print(f"✓ Response generated!")
            print(f"  Answer field: {response.get('answer', 'N/A')[:150]}")
            if 'suggestions' in response:
                print(f"  Suggestions: {len(response.get('suggestions', []))} items")

        elif clarify_rows:
            print(f"✓ Found {len(clarify_rows)} clarification options")
            for i, row in enumerate(clarify_rows[:3], 1):
                print(f"  {i}. {row.get('question', 'N/A')[:80]}")
        else:
            print("✗ No match found")

    except Exception as e:
        print(f"✗ EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
        print("\nThis is likely the error causing the chatbot to fail!")
        sys.exit(1)

print("\n" + "=" * 80)
print("ALL TESTS PASSED! ✓")
print("=" * 80)
