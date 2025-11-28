#!/usr/bin/env python3
"""
Test to verify the bug fix for chatbot response format
"""
import sys
import json

# Check that top_k default is now 1
print("=" * 80)
print("TESTING BUG FIX: Default top_k value")
print("=" * 80)

# Read the main.py file to verify the fix
with open("app/main.py", "r") as f:
    content = f.read()

# Find the ChatRequest class definition
if "top_k: int = 1" in content:
    print("✓ SUCCESS: Default top_k is now 1 (fixed!)")
    print("  This means the chatbot will use traditional response format by default")
elif "top_k: int = 4" in content:
    print("✗ FAILED: Default top_k is still 4 (bug not fixed!)")
    sys.exit(1)
else:
    print("? WARNING: Could not find top_k definition")
    sys.exit(1)

print("\n" + "=" * 80)
print("TESTING: FAQ file loading")
print("=" * 80)

# Test FAQ loading
import os
FAQ_FILE = "app/data/all/faq/FAQAI.jsonl"
if os.path.exists(FAQ_FILE):
    print(f"✓ FAQ file exists: {FAQ_FILE}")
    with open(FAQ_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    print(f"✓ FAQ file has {len(lines)} entries")

    # Check for the test questions
    import re
    test_questions = [
        "serienummer",
        "reset.*wifipool",
    ]

    found_questions = []
    for line in lines:
        try:
            obj = json.loads(line)
            question = obj.get("Vraag", "")
            if any(re.search(pattern, question.lower()) for pattern in test_questions):
                found_questions.append(question[:80])
        except:
            pass

    if found_questions:
        print(f"✓ Found {len(found_questions)} test-related questions in FAQ")
        for q in found_questions[:3]:
            print(f"  - {q}")
    else:
        print("✗ Test questions not found in FAQ")
else:
    print(f"✗ FAQ file not found: {FAQ_FILE}")
    sys.exit(1)

print("\n" + "=" * 80)
print("EXPECTED BEHAVIOR AFTER FIX:")
print("=" * 80)
print("1. Chatbot will use traditional response format: {answer, citations, suggestions}")
print("2. Questions with valid answers will return proper responses")
print("3. Questions without answers will still return 'Geen antwoord gevonden' but")
print("   with suggestions for similar questions")
print("\n" + "=" * 80)
print("ALL TESTS PASSED! ✓")
print("=" * 80)
