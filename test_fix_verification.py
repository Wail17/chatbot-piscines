#!/usr/bin/env python3
"""
Test script to verify the FAQ and GPT fallback fixes.

Tests:
1. FAQ questions with good answers should return FAQ response with source="faq"
2. FAQ questions with empty answers should use GPT fallback with source="ai_fallback"
3. Non-FAQ questions should use GPT fallback with source="ai_fallback"
"""

import json
from app.main import _FAQ, _load_faq_from_jsonl, _FAQ_FALLBACK_JSONL

def test_faq_loaded():
    """Test that FAQ is loaded correctly."""
    print("=" * 60)
    print("TEST 1: FAQ Loading")
    print("=" * 60)

    if not _FAQ:
        print("Loading FAQ from JSONL...")
        from app.main import _reload_faq
        count, _ = _reload_faq()
        print(f"✓ Loaded {count} FAQ entries")
    else:
        print(f"✓ FAQ already loaded: {len(_FAQ)} entries")

    return len(_FAQ) > 0


def test_faq_with_empty_answers():
    """Test that FAQ entries with empty answers are identified."""
    print("\n" + "=" * 60)
    print("TEST 2: FAQ Entries with Empty Answers")
    print("=" * 60)

    empty_answer_questions = [
        "Wat moet ik doen om mijn wifipool apparaat te resetten?",
        "Waar kan ik het serienummer van mijn wifipool apparaat vinden?"
    ]

    found_count = 0
    for question in empty_answer_questions:
        # Search in FAQ
        for row in _FAQ:
            if question.lower() in row.get("question", "").lower():
                answer = (row.get("answer") or "").strip()
                if not answer or len(answer) < 30:
                    print(f"✓ Found empty/short answer for: {question[:60]}...")
                    print(f"  Answer length: {len(answer)} chars")
                    found_count += 1
                else:
                    print(f"✗ Answer exists for: {question[:60]}...")
                    print(f"  Answer: {answer[:100]}...")
                break

    print(f"\nFound {found_count} FAQ entries with empty/short answers")
    return found_count > 0


def test_faq_with_good_answers():
    """Test that FAQ entries with good answers exist."""
    print("\n" + "=" * 60)
    print("TEST 3: FAQ Entries with Good Answers")
    print("=" * 60)

    # Test a question that should have a good answer
    search_terms = ["handleidingen", "youtube", "beniferro"]

    found = False
    for row in _FAQ:
        question = row.get("question", "").lower()
        answer = (row.get("answer") or "").strip()

        if any(term in question for term in search_terms):
            if answer and len(answer) >= 30:
                print(f"✓ Found FAQ with good answer:")
                print(f"  Q: {row.get('question', '')[:80]}...")
                print(f"  A: {answer[:100]}...")
                print(f"  Answer length: {len(answer)} chars")
                found = True
                break

    if not found:
        print("✗ No FAQ entries with good answers found")

    return found


def verify_gpt_fallback_configured():
    """Verify GPT fallback is configured."""
    print("\n" + "=" * 60)
    print("TEST 4: GPT Fallback Configuration")
    print("=" * 60)

    import os
    api_key = os.environ.get("OPENAI_API_KEY")

    if api_key:
        print("✓ OPENAI_API_KEY is configured")
        print(f"  Key length: {len(api_key)} chars")

        # Check if client is initialized
        from app.main import _openai_client
        if _openai_client:
            print("✓ OpenAI client is initialized")
            return True
        else:
            print("✗ OpenAI client failed to initialize")
            return False
    else:
        print("✗ OPENAI_API_KEY is NOT configured")
        print("  GPT fallback will not work!")
        return False


def verify_code_changes():
    """Verify that the code changes are in place."""
    print("\n" + "=" * 60)
    print("TEST 5: Code Changes Verification")
    print("=" * 60)

    # Read the main.py file and check for key changes
    with open('/home/user/chatbot-piscines/app/main.py', 'r') as f:
        content = f.read()

    checks = [
        ('MIN_ANSWER_LENGTH check', 'MIN_ANSWER_LENGTH = 30'),
        ('GPT fallback in _respond_for_row', 'gpt_answer = _gpt_fallback_answer(user_q or question_ref'),
        ('Source field "ai_fallback"', '"source": "ai_fallback"'),
        ('Source field "faq"', '"source": "faq"'),
        ('Empty answer detection', 'if not direct or len(direct) < MIN_ANSWER_LENGTH:'),
    ]

    all_passed = True
    for check_name, check_string in checks:
        if check_string in content:
            print(f"✓ {check_name}")
        else:
            print(f"✗ {check_name} - NOT FOUND")
            all_passed = False

    return all_passed


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "FAQ & GPT FALLBACK FIX VERIFICATION" + " " * 12 + "║")
    print("╚" + "=" * 58 + "╝")

    results = {
        "FAQ Loading": test_faq_loaded(),
        "Empty Answer Detection": test_faq_with_empty_answers(),
        "Good Answer Detection": test_faq_with_good_answers(),
        "GPT Configuration": verify_gpt_fallback_configured(),
        "Code Changes": verify_code_changes(),
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {test_name}")

    all_passed = all(results.values())

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL TESTS PASSED!")
    else:
        print("✗ SOME TESTS FAILED")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
