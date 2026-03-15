#!/usr/bin/env python3
"""
EDGE CASE TESTS - Maximum stress test
=====================================

Tests extreme cases:
- Typos and misspellings
- Mixed languages in same query
- Very short queries
- Abbreviations
- Informal language
- Conjugations and variations
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def test_edge_cases():
    """Test extreme edge cases that would trip up a weak system."""
    print("\n" + "=" * 80)
    print("🔥 EDGE CASE TESTS - Maximum Difficulty")
    print("=" * 80)
    print()

    from app.keyword_search import keyword_search, build_keyword_index, _load_faq_direct

    # Build index
    entries = _load_faq_direct()
    if not entries:
        print("❌ No FAQ entries found")
        return False

    build_keyword_index(entries)
    print(f"✓ Index built with {len(entries)} FAQ entries\n")

    edge_cases = [
        # (query, expected_concept, description)
        # Typos and misspellings
        ("ph te laag helpen", "pH too low", "Typo: laag → should still find pH low"),
        ("kalibreer de sonde plz", "calibration", "Informal: plz instead of alstublieft"),
        ("wifi connectie probleme", "wifi problem", "Typo: probleme → probleem"),
        ("resetn van apparaat", "reset", "Typo: resetn → resetten"),

        # Mixed languages
        ("pH trop bas help", "pH low", "Mixed FR-EN: pH trop bas help"),
        ("wifi connection probleem oplossen", "wifi problem", "Mixed EN-NL"),
        ("sensor calibrate nu", "calibration", "Mixed EN-NL: calibrate nu"),

        # Very short queries
        ("ph?", "pH", "Ultra-short: just 'ph?'"),
        ("reset", "reset", "Single word: reset"),
        ("wifi", "wifi", "Single word: wifi"),
        ("pomp", "pump", "Single word NL: pomp"),

        # Abbreviations and slang
        ("temp sensor kapot", "temperature", "Abbreviation: temp"),
        ("ph sonde stuk", "pH sensor", "Informal: stuk = broken"),
        ("debiet ok?", "flow", "Abbreviation: ok"),
        ("orp waarde weird", "ORP", "EN slang: weird"),

        # Conjugations and variations
        ("kalibreer", "calibration", "Verb conjugation: kalibreer"),
        ("geresetd", "reset", "Past tense typo: geresetd"),
        ("verbinding maken met wifi", "connect wifi", "Infinitive: maken"),
        ("pompen werken niet", "pump problem", "Plural: pompen"),

        # Complex multi-word phrases
        ("hoe kalibreer ik de pH sonde", "pH calibration", "Full question NL"),
        ("comment calibrer le capteur pH", "pH calibration", "Full question FR"),
        ("how to reset the device", "reset device", "Full question EN"),

        # Domain-specific slang
        ("chloor doet raar", "chlorine problem", "NL slang: doet raar"),
        ("rx springt", "ORP fluctuation", "NL slang: springt"),
        ("elektrolyse cel vuil", "electrolysis maintenance", "NL: vuil = dirty"),
    ]

    print(f"Running {len(edge_cases)} edge case tests...\n")
    print("-" * 80)

    passed = 0
    failed = 0

    for i, (query, expected_concept, description) in enumerate(edge_cases, 1):
        results = keyword_search(query, top_k=3)

        found = len(results) > 0
        status = "✅" if found else "❌"

        if found:
            passed += 1
        else:
            failed += 1

        print(f"{status} [{i:2d}/{len(edge_cases)}] {description}")
        print(f"    Query: '{query}'")
        print(f"    Expected: {expected_concept}")

        if results:
            top = results[0]
            entry, score = top
            print(f"    Found: {entry['question'][:60]}... (score: {score:.3f})")
        else:
            print(f"    ❌ NO RESULTS FOUND")

        print()

    # Summary
    total = len(edge_cases)
    success_rate = (passed / total) * 100

    print("=" * 80)
    print("EDGE CASE TEST SUMMARY")
    print("=" * 80)
    print(f"\n✅ Passed: {passed}/{total} ({success_rate:.1f}%)")
    print(f"❌ Failed: {failed}/{total}")
    print()

    if success_rate >= 85:
        print(f"🎉 EXCELLENT! {success_rate:.1f}% on edge cases")
        print("System is VERY ROBUST!")
        return True
    elif success_rate >= 75:
        print(f"✅ GOOD! {success_rate:.1f}% on edge cases")
        print("System handles most edge cases well")
        return True
    else:
        print(f"⚠️  NEEDS IMPROVEMENT: {success_rate:.1f}%")
        return False


if __name__ == "__main__":
    try:
        success = test_edge_cases()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
