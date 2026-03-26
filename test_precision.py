#!/usr/bin/env python3
"""
TEST DE PRÉCISION - Vérifier que le système trouve LA BONNE question
====================================================================

Ce test vérifie que le système ne confond pas les questions similaires.
Par exemple:
- "Comment kalibreren pH?" → doit trouver FAQ sur CALIBRATION pH
- "Comment mesurer pH?" → doit trouver FAQ sur MESURE pH
- PAS l'inverse!

On teste des paires de questions proches mais DIFFÉRENTES pour voir
si le système les distingue correctement.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def test_precision():
    """
    Test de précision: le système doit distinguer des questions similaires
    et retourner LA BONNE réponse, pas une réponse au hasard.
    """
    print("\n" + "=" * 80)
    print("🎯 TEST DE PRÉCISION - Éviter les Confusions")
    print("=" * 80)
    print("Objectif: Vérifier que le système trouve LA BONNE question FAQ")
    print()

    from app.keyword_search import keyword_search, build_keyword_index, _load_faq_direct

    # Build index
    entries = _load_faq_direct()
    if not entries:
        print("❌ No FAQ entries found")
        return False

    build_keyword_index(entries)
    print(f"✓ Index built with {len(entries)} FAQ entries\n")

    # Create a mapping of query → expected keywords in the correct answer
    # These are questions that are SIMILAR but should get DIFFERENT answers
    precision_tests = [
        # (query, expected_keywords_in_answer, NOT_expected_keywords, description)

        # pH CALIBRATION vs pH MEASUREMENT
        (
            "Comment kalibreren pH sonde?",
            ["kalibreren", "calibr", "buffer", "ijken", "aflezen"],
            ["meten", "meting", "afwijking", "verschil"],
            "Calibration pH (NOT measurement)"
        ),
        (
            "pH meting wijkt af",
            ["meting", "afwijking", "verschil", "afwijken"],
            ["kalibreren", "calibreren", "buffer"],
            "pH measurement problem (NOT calibration)"
        ),

        # WIFI CONNECTION vs WIFI PASSWORD
        (
            "wifi wachtwoord fout",
            ["wachtwoord", "password", "verkeerd", "authenticat"],
            ["verbinding", "bereik", "signaal", "router"],
            "WiFi password error (NOT connection)"
        ),
        (
            "wifi verbinding probleem geen signaal",
            ["signaal", "bereik", "verbinding", "afstand"],
            ["wachtwoord", "password", "verkeerd wachtwoord"],
            "WiFi signal problem (NOT password)"
        ),

        # PUMP LEAK vs PUMP NOT WORKING
        (
            "pomp is lek slangetje",
            ["lek", "slangetje", "lekkage", "tube"],
            ["werkt niet", "draait niet", "start niet"],
            "Pump leak (NOT pump not working)"
        ),
        (
            "pomp werkt niet start niet",
            ["werkt niet", "start", "draait niet"],
            ["lek", "lekkage", "slangetje"],
            "Pump not working (NOT leak)"
        ),

        # LEVEL SENSOR vs LEVEL TOO LOW
        (
            "niveausensor werkt niet vlotter",
            ["sensor", "vlotter", "schakelaar", "switch"],
            ["te laag", "bijvullen", "water niveau laag"],
            "Level sensor problem (NOT water too low)"
        ),
        (
            "waterniveau te laag bijvullen",
            ["laag", "bijvullen", "peil"],
            ["sensor defect", "vlotter kapot"],
            "Water level too low (NOT sensor problem)"
        ),

        # RESET vs RESTART
        (
            "factory reset fabrieksinstellingen",
            ["factory", "fabrieksinstellingen", "wissen", "reset"],
            ["herstarten", "reboot", "opnieuw opstarten"],
            "Factory reset (NOT restart)"
        ),
        (
            "apparaat herstarten reboot",
            ["herstarten", "reboot", "opnieuw"],
            ["factory", "fabrieksinstellingen", "wissen"],
            "Restart/reboot (NOT factory reset)"
        ),

        # FLOW SENSOR vs FLOW TOO LOW
        (
            "flow sensor werkt niet",
            ["sensor", "flowswitch", "defect", "kapot"],
            ["debiet laag", "flow te laag", "pomp"],
            "Flow sensor problem (NOT flow too low)"
        ),
        (
            "debiet te laag flow laag",
            ["debiet", "flow", "laag", "circulatie"],
            ["sensor defect", "flowswitch kapot"],
            "Flow too low (NOT sensor problem)"
        ),

        # ORP/RX HIGH vs ORP/RX LOW
        (
            "RX waarde te hoog",
            ["hoog", "te hoog", "overdosering"],
            ["laag", "te laag", "onvoldoende"],
            "ORP too high (NOT too low)"
        ),
        (
            "RX waarde te laag",
            ["laag", "te laag", "onvoldoende"],
            ["hoog", "te hoog", "overdosering"],
            "ORP too low (NOT too high)"
        ),

        # CALIBRATION vs REPLACEMENT
        (
            "sensor kalibreren afstellen",
            ["kalibreren", "afstellen", "ijken", "calibr"],
            ["vervangen", "nieuw", "replacement"],
            "Calibrate sensor (NOT replace)"
        ),
        (
            "sensor vervangen nieuwe",
            ["vervangen", "nieuw", "replacement", "vervanging"],
            ["kalibreren", "ijken", "afstellen"],
            "Replace sensor (NOT calibrate)"
        ),

        # CLEANING vs REPLACING
        (
            "sonde reinigen schoonmaken",
            ["reinigen", "schoonmaken", "clean"],
            ["vervangen", "nieuw", "replacement"],
            "Clean probe (NOT replace)"
        ),
        (
            "sonde vervangen nieuwe kopen",
            ["vervangen", "nieuwe", "kopen"],
            ["reinigen", "schoonmaken", "clean"],
            "Replace probe (NOT clean)"
        ),

        # TIMER vs FAILSAFE
        (
            "timer instellen programmeren",
            ["timer", "programmeren", "tijdschakelaar", "schedule"],
            ["failsafe", "beveiliging", "noodstop"],
            "Timer setup (NOT failsafe)"
        ),
        (
            "failsafe instellen beveiliging",
            ["failsafe", "beveiliging", "noodstop"],
            ["timer", "tijdschakelaar", "programmeren"],
            "Failsafe setup (NOT timer)"
        ),

        # APP vs DEVICE
        (
            "app werkt niet telefoon",
            ["app", "telefoon", "smartphone", "application"],
            ["apparaat offline", "device offline"],
            "App problem (NOT device offline)"
        ),
        (
            "apparaat offline rood bolletje",
            ["offline", "apparaat", "bolletje", "device"],
            ["app werkt niet", "smartphone"],
            "Device offline (NOT app problem)"
        ),
    ]

    print(f"Running {len(precision_tests)} precision tests...\n")
    print("-" * 80)

    passed = 0
    failed = 0
    wrong_answer = 0

    for i, (query, expected_kw, not_expected_kw, desc) in enumerate(precision_tests, 1):
        results = keyword_search(query, top_k=3)

        if not results:
            print(f"❌ [{i:2d}/{len(precision_tests)}] {desc}")
            print(f"    Query: '{query}'")
            print(f"    ❌ NO RESULTS FOUND")
            failed += 1
            print()
            continue

        # Check top result
        top_entry, top_score = results[0]
        top_question = top_entry.get('question', '')
        top_answer = top_entry.get('answer', '')
        combined = (top_question + " " + top_answer).lower()

        # Check if expected keywords are present
        expected_found = any(kw.lower() in combined for kw in expected_kw)

        # Check if NOT-expected keywords are present (BAD!)
        wrong_found = any(kw.lower() in combined for kw in not_expected_kw)

        if expected_found and not wrong_found:
            status = "✅"
            passed += 1
        elif wrong_found:
            status = "❌ WRONG"
            wrong_answer += 1
            failed += 1
        else:
            status = "🟡"
            failed += 1

        print(f"{status} [{i:2d}/{len(precision_tests)}] {desc}")
        print(f"    Query: '{query}'")
        print(f"    Expected: {expected_kw[:3]}")
        print(f"    NOT expected: {not_expected_kw[:3]}")
        print(f"    Top result: {top_question[:70]}...")
        print(f"    Score: {top_score:.3f}")

        if wrong_found:
            print(f"    ⚠️  CONFUSION: Found wrong FAQ (contains NOT-expected keywords)")
        elif not expected_found:
            print(f"    ⚠️  IRRELEVANT: Found FAQ doesn't contain expected keywords")

        print()

    # Summary
    total = len(precision_tests)
    accuracy = (passed / total) * 100

    print("=" * 80)
    print("PRECISION TEST SUMMARY")
    print("=" * 80)
    print(f"\n✅ Correct answers: {passed}/{total} ({accuracy:.1f}%)")
    print(f"❌ Wrong/Confused: {wrong_answer}/{total}")
    print(f"🟡 Irrelevant: {failed - wrong_answer}/{total}")
    print()

    if wrong_answer > 0:
        print(f"⚠️  DANGER: {wrong_answer} questions got WRONG FAQ (confusion)")
        print("   Le système confond des questions similaires!")
        print()

    if accuracy >= 90:
        print(f"🎉 EXCELLENT! {accuracy:.1f}% precision")
        print("Le système trouve LA BONNE question!")
        return True
    elif accuracy >= 80:
        print(f"✅ GOOD: {accuracy:.1f}% precision")
        print(f"Need improvement: {int((0.9 * total) - passed)} more correct")
        return True
    else:
        print(f"❌ NEEDS WORK: {accuracy:.1f}% precision")
        print(f"Need improvement: {int((0.9 * total) - passed)} more correct")
        print("Le système confond trop de questions!")
        return False


if __name__ == "__main__":
    try:
        success = test_precision()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
