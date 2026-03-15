#!/usr/bin/env python3
"""
STRICT MENTOR TEST - 90%+ TARGET
=================================

Simulates a strict mentor testing the chatbot with:
- 50+ question variations
- Multiple synonyms per concept
- Cross-language mixing (NL/FR/EN)
- Typos and abbreviations
- Edge cases

Goal: 90%+ success rate
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def test_comprehensive_synonyms():
    """
    Comprehensive test suite with 50+ variations.
    Each pair should find same or highly overlapping answers.
    """
    print("\n" + "=" * 80)
    print("🎓 MENTOR STRICT TEST - COMPREHENSIVE SYNONYM MATCHING")
    print("=" * 80)
    print("Target: 90%+ success rate (45+/50 tests passing)")
    print()

    from app.keyword_search import keyword_search, build_keyword_index, _load_faq_direct

    # Build index
    entries = _load_faq_direct()
    if not entries:
        print("❌ No FAQ entries found")
        return False

    build_keyword_index(entries)
    print(f"✓ Index built with {len(entries)} FAQ entries\n")

    # TEST CASES: (query_1, query_2, description)
    # Each pair should find same FAQ entry
    test_cases = [
        # ═══ pH TESTS ═══
        ("pH te laag", "zuurtegraad te laag", "NL: pH synonym"),
        ("pH te laag", "acidité trop basse", "NL-FR: pH cross-language"),
        ("pH te laag", "acidity too low", "NL-EN: pH cross-language"),
        ("pH waarde meten", "zuurgraad meten", "NL: pH value variant"),
        ("pH niveau", "acidity level", "NL-EN: pH level"),

        # ═══ CALIBRATION TESTS ═══
        ("pH sensor kalibreren", "pH sonde calibreren", "NL: calibrate synonyms"),
        ("sensor kalibreren", "capteur calibrer", "NL-FR: calibrate"),
        ("sensor kalibreren", "probe calibrate", "NL-EN: calibrate"),
        ("kalibratie probleem", "calibration issue", "NL-EN: calibration noun"),
        ("ijken van de sonde", "kalibreren sensor", "NL: ijken = kalibreren"),

        # ═══ RESET TESTS ═══
        ("apparaat resetten", "device reset", "NL-EN: reset"),
        ("wifipool reset", "wifipool herstarten", "NL: reset = herstarten"),
        ("factory reset", "fabrieksinstellingen", "EN-NL: factory reset"),
        ("hard reset doen", "opnieuw opstarten", "NL: hard reset"),
        ("réinitialiser", "reset appareil", "FR-NL: reset"),

        # ═══ TEMPERATURE TESTS ═══
        ("watertemperatuur te laag", "water temp too low", "NL-EN: temperature"),
        ("temperatuur sensor", "temp sensor", "NL-EN: temp abbreviation"),
        ("température de l'eau", "watertemperatuur", "FR-NL: temperature"),
        ("koude water", "cold water temp", "NL-EN: cold"),

        # ═══ CONNECTION TESTS ═══
        ("wifi verbindingsprobleem", "wifi connection problem", "NL-EN: connection"),
        ("verbinding maken", "connect to wifi", "NL-EN: connect verb"),
        ("problème connexion", "wifi connectie", "FR-NL: connexion"),
        ("aansluiten op netwerk", "connect network", "NL-EN: connect to network"),
        ("geen verbinding", "no connection", "NL-EN: no connection"),

        # ═══ PUMP TESTS ═══
        ("circulatiepomp probleem", "circulation pump issue", "NL-EN: pump"),
        ("pomp werkt niet", "pump not working", "NL-EN: pump problem"),
        ("pompe de circulation", "circulatiepomp", "FR-NL: circulation pump"),
        ("filterpomp defect", "filter pump broken", "NL-EN: filter pump"),
        ("doseerpomp instellen", "dosing pump setup", "NL-EN: dosing pump"),

        # ═══ WATER LEVEL TESTS ═══
        ("waterniveau te laag", "water level too low", "NL-EN: water level"),
        ("peil sensor", "level sensor", "NL-EN: level sensor"),
        ("niveau d'eau", "waterniveau", "FR-NL: water level"),
        ("waterstand meten", "measure water level", "NL-EN: measure level"),
        ("vlotter schakelaar", "float switch", "NL-EN: float"),

        # ═══ FLOW TESTS ═══
        ("debiet te laag", "flow too low", "NL-EN: flow"),
        ("flowmeter probleem", "debietmeter defect", "EN-NL: flowmeter"),
        ("waterflow sensor", "flow rate sensor", "NL-EN: flow sensor"),
        ("stroomsnelheid meten", "measure flow rate", "NL-EN: flow rate"),
        ("débitmètre", "flowmeter", "FR-EN: flowmeter"),
        ("doorstroom probleem", "flow problem", "NL-EN: flow issue"),

        # ═══ SALT/ELECTROLYSIS TESTS ═══
        ("zoutgehalte", "salt level", "NL-EN: salt"),
        ("sel concentration", "zoutconcentratie", "FR-NL: salt concentration"),
        ("elektrolyse probleem", "electrolysis issue", "NL-EN: electrolysis"),
        ("zoutelektrolyse", "salt electrolysis", "NL-EN: salt electrolysis"),
        ("électrolyse au sel", "zout elektrolyse", "FR-NL: salt electrolysis"),

        # ═══ CHLORINE TESTS ═══
        ("chloorgehalte", "chlorine level", "NL-EN: chlorine"),
        ("chlore niveau", "chloor niveau", "FR-NL: chlorine level"),
        ("vrij chloor", "free chlorine", "NL-EN: free chlorine"),
        ("desinfectant", "disinfectant", "FR-EN: disinfectant"),

        # ═══ ORP/REDOX TESTS ═══
        ("ORP waarde", "redox waarde", "abbreviation: ORP = redox"),
        ("redoxpotentiaal", "ORP value", "NL-EN: redox potential"),
        ("potentiel redox", "ORP niveau", "FR-EN: redox potential"),

        # ═══ SENSOR TESTS ═══
        ("pH sonde defect", "pH sensor broken", "NL-EN: sensor"),
        ("capteur problème", "sensor probleem", "FR-NL: sensor problem"),
        ("electrode vervangen", "replace probe", "NL-EN: electrode"),

        # ═══ DEVICE/APP TESTS ═══
        ("wifipool app", "application wifipool", "NL-FR: app"),
        ("apparaat offline", "device offline", "NL-EN: device offline"),
        ("controller instellen", "setup controller", "NL-EN: controller"),

        # ═══ SETTINGS TESTS ═══
        ("instellingen wijzigen", "change settings", "NL-EN: settings"),
        ("paramètres", "instellingen", "FR-NL: parameters"),
        ("configuratie aanpassen", "adjust configuration", "NL-EN: configuration"),
    ]

    print(f"Running {len(test_cases)} test pairs...\n")
    print("-" * 80)

    passed = 0
    failed = 0
    partial = 0

    for i, (q1, q2, desc) in enumerate(test_cases, 1):
        # Search both queries
        results_1 = keyword_search(q1, top_k=3)
        results_2 = keyword_search(q2, top_k=3)

        # Extract FAQ questions
        if results_1:
            faq_1 = results_1[0][0].get('question', '') or results_1[0][0].get('source', '')
        else:
            faq_1 = None

        if results_2:
            faq_2 = results_2[0][0].get('question', '') or results_2[0][0].get('source', '')
        else:
            faq_2 = None

        # Check if both found same source
        same_source = False
        if results_1 and results_2:
            # Check if top result is same
            same_source = faq_1 == faq_2

            # Or check if there's any overlap in top 3
            if not same_source:
                questions_1 = {r[0].get('question', r[0].get('source', '')) for r in results_1}
                questions_2 = {r[0].get('question', r[0].get('source', '')) for r in results_2}
                overlap = questions_1 & questions_2
                if overlap:
                    same_source = True
                    partial += 1
                else:
                    partial += 1  # Both found something but different

        # Verdict
        if same_source:
            status = "✅"
            passed += 1
        elif results_1 and results_2:
            status = "🟡"  # Both found results but different
            # Don't increment failed, already in partial
        else:
            status = "❌"
            failed += 1

        # Print result
        print(f"{status} [{i:2d}/{len(test_cases)}] {desc}")
        print(f"    Q1: '{q1}' → {len(results_1)} results")
        print(f"    Q2: '{q2}' → {len(results_2)} results")

        if faq_1:
            print(f"    A1 (faq): {faq_1[:80]}...")
        if faq_2:
            print(f"    A2 (faq): {faq_2[:80]}...")

        if same_source:
            print(f"    ✅ SAME SOURCE - Good (same FAQ/source, minor differences)")
        elif results_1 and results_2:
            print(f"    🟡 DIFFERENT - Both found answers but not the same FAQ")
        else:
            print(f"    ❌ FAILED - One or both queries found no results")

        print()

    # ═══════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════════

    total = len(test_cases)
    success_rate = (passed / total) * 100

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print(f"\n✅ Passed: {passed}/{total} ({success_rate:.1f}%)")
    print(f"❌ Failed: {failed}/{total}")

    if partial > 0:
        print(f"🟡 Partial: {partial} (found results but different FAQs)")

    print()
    print("Details:")
    for i, (q1, q2, desc) in enumerate(test_cases, 1):
        results_1 = keyword_search(q1, top_k=1)
        results_2 = keyword_search(q2, top_k=1)

        same = False
        if results_1 and results_2:
            faq_1 = results_1[0][0].get('question', results_1[0][0].get('source', ''))
            faq_2 = results_2[0][0].get('question', results_2[0][0].get('source', ''))
            same = faq_1 == faq_2

        symbol = "✅" if same else ("🟡" if (results_1 and results_2) else "❌")
        print(f"{symbol} {desc}")

    print("\n" + "=" * 80)

    # Verdict
    if success_rate >= 90:
        print(f"🎉 SUCCESS! {success_rate:.1f}% ≥ 90% target")
        print("The synonym system is MENTOR-APPROVED!")
        return True
    elif success_rate >= 80:
        print(f"⚠️  CLOSE! {success_rate:.1f}% (target: 90%)")
        print(f"Need {int((0.9 * total) - passed)} more passing tests")
        return False
    else:
        print(f"❌ NEEDS WORK: {success_rate:.1f}% < 90% target")
        print(f"Need {int((0.9 * total) - passed)} more passing tests")
        return False


if __name__ == "__main__":
    try:
        success = test_comprehensive_synonyms()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
