#!/usr/bin/env python3
"""
Test script to verify the FAQ matching fixes.

This tests the fixes for the "No answers" problem:
1. Lowered matching thresholds
2. Reduced canonical token penalties
3. Added fallback substring matching
4. Better handling of empty answers
"""

import sys
import os

# Minimal test without heavy dependencies
def test_thresholds():
    """Test that thresholds were lowered"""
    print("="*80)
    print("TEST 1: Verify thresholds were lowered")
    print("="*80)

    # Read the main.py file to check threshold values
    main_file = os.path.join(os.path.dirname(__file__), "app", "main.py")
    with open(main_file, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = {
        "_MATCH_THRESHOLD = 0.55": "Match threshold",
        "_SEMANTIC_MATCH_THRESHOLD = 0.65": "Semantic match threshold",
        "_SEMANTIC_TRIGGER = 0.50": "Semantic trigger",
        "_CERTAINTY_THRESHOLD = 0.75": "Certainty threshold",
        "score *= 0.85  # Was 0.55": "Canonical penalty reduction 1",
        "score *= 0.90  # Was 0.75": "Canonical penalty reduction 2"
    }

    all_passed = True
    for pattern, description in checks.items():
        if pattern in content:
            print(f"✓ {description}: FOUND")
        else:
            print(f"✗ {description}: NOT FOUND (FAIL)")
            all_passed = False

    if all_passed:
        print("\n✓ All threshold checks PASSED!")
    else:
        print("\n✗ Some threshold checks FAILED!")

    return all_passed

def test_fallback():
    """Test that fallback matching was added"""
    print("\n" + "="*80)
    print("TEST 2: Verify fallback matching was added")
    print("="*80)

    main_file = os.path.join(os.path.dirname(__file__), "app", "main.py")
    with open(main_file, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = {
        "FALLBACK: Simple substring/token matching": "Fallback comment",
        "fallback_matches = []": "Fallback variable",
        "query_tokens = set(uq_base.split())": "Query tokens",
        "common_tokens = query_tokens & row_tokens": "Common tokens check",
        "if top_fallback[1] >= 0.4:": "Fallback threshold"
    }

    all_passed = True
    for pattern, description in checks.items():
        if pattern in content:
            print(f"✓ {description}: FOUND")
        else:
            print(f"✗ {description}: NOT FOUND (FAIL)")
            all_passed = False

    if all_passed:
        print("\n✓ Fallback matching checks PASSED!")
    else:
        print("\n✗ Some fallback checks FAILED!")

    return all_passed

def test_empty_answer_handling():
    """Test that empty answer handling was improved"""
    print("\n" + "="*80)
    print("TEST 3: Verify empty answer handling")
    print("="*80)

    main_file = os.path.join(os.path.dirname(__file__), "app", "main.py")
    with open(main_file, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = {
        "This handles FAQ entries with empty answers": "Empty answer comment",
        "question_matched = row.get(\"question\", \"\").strip()": "Question extraction",
        "maar het antwoord is nog niet beschikbaar": "User-friendly message",
        "support@beniferro.eu": "Contact info"
    }

    all_passed = True
    for pattern, description in checks.items():
        if pattern in content:
            print(f"✓ {description}: FOUND")
        else:
            print(f"✗ {description}: NOT FOUND (FAIL)")
            all_passed = False

    if all_passed:
        print("\n✓ Empty answer handling checks PASSED!")
    else:
        print("\n✗ Some empty answer checks FAILED!")

    return all_passed

def test_debug_endpoint():
    """Test that debug endpoint was added"""
    print("\n" + "="*80)
    print("TEST 4: Verify debug endpoint exists")
    print("="*80)

    main_file = os.path.join(os.path.dirname(__file__), "app", "main.py")
    with open(main_file, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = {
        "@app.get(\"/debug/test-match\")": "Debug endpoint decorator",
        "def debug_test_match": "Debug endpoint function",
        "all_scores = []": "Score collection",
        "\"top_20_scores\":": "Top scores output"
    }

    all_passed = True
    for pattern, description in checks.items():
        if pattern in content:
            print(f"✓ {description}: FOUND")
        else:
            print(f"✗ {description}: NOT FOUND (FAIL)")
            all_passed = False

    if all_passed:
        print("\n✓ Debug endpoint checks PASSED!")
    else:
        print("\n✗ Some debug endpoint checks FAILED!")

    return all_passed

def main():
    print("\n" + "="*80)
    print("FAQ MATCHING FIXES - VERIFICATION TEST")
    print("="*80 + "\n")

    results = []
    results.append(("Thresholds", test_thresholds()))
    results.append(("Fallback", test_fallback()))
    results.append(("Empty Answers", test_empty_answer_handling()))
    results.append(("Debug Endpoint", test_debug_endpoint()))

    print("\n" + "="*80)
    print("FINAL RESULTS")
    print("="*80)

    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{test_name:20s}: {status}")

    all_passed = all(r[1] for r in results)

    print("\n" + "="*80)
    if all_passed:
        print("✓✓✓ ALL TESTS PASSED! ✓✓✓")
        print("The fixes have been successfully implemented.")
    else:
        print("✗✗✗ SOME TESTS FAILED ✗✗✗")
        print("Please review the failed checks above.")
    print("="*80 + "\n")

    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
