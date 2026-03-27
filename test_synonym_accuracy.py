#!/usr/bin/env python3
"""
Test chatbot accuracy with synonym/rephrased questions.
Tests actual FAQ questions with variations to verify synonym matching.
"""
import requests
import time

API_URL = "http://localhost:8000/chat"

# Test cases with rephrased versions of actual FAQ questions
# Each test has the rephrased question and keywords from the actual FAQ answer
TEST_CASES = [
    {
        "question": "Hoe kalibreer ik mijn Wifipool?",  # Rephrasing of "Hoe moet ik een Wifipool kalibreren?"
        "expected_keywords": ["kalibreren", "kalibratie", "ijken"],
        "category": "Calibration",
        "description": "Synonym test: kalibreren"
    },
    {
        "question": "Geen flow detectie terwijl pomp draait",  # Rephrasing of "Er komt geen flow op mijn scherm, terwijl de filterpomp draait"
        "expected_keywords": ["flow", "sensor", "pomp"],
        "category": "Flow Detection",
        "description": "Synonym test: flow + pomp"
    },
    {
        "question": "Waar stel ik de werkingscyclus in?",  # Exact match to FAQ
        "expected_keywords": ["werkingscyclus", "bedrijfscyclus", "zwembadinstellingen", "automatisatie"],
        "category": "Settings",
        "description": "Direct match test"
    },
    {
        "question": "pH dosering vertragen tegen overdosering",  # Rephrasing of "Kan ik de pH- en RX-dosering vertragen, zodat er minder overdosering is?"
        "expected_keywords": ["dosering", "vertragen", "werkingscyclus", "pH"],
        "category": "Dosing Control",
        "description": "Synonym test: dosering + vertragen"
    },
    {
        "question": "Apparaat manueel aan en uit zetten",  # Rephrasing of "Hoe kan ik een toestel manueel starten of stoppen?"
        "expected_keywords": ["manueel", "starten", "stoppen", "app"],
        "category": "Manual Control",
        "description": "Synonym test: starten/stoppen"
    },
    {
        "question": "pH waarde klopt niet met teststrips",  # Rephrasing of "De pH waarde van mijn apparaat komt niet overeen met de kleurmeting"
        "expected_keywords": ["pH", "kleurmeting", "kalibreren", "alkaliniteit"],
        "category": "pH Measurement",
        "description": "Synonym test: kleurmeting + teststrips"
    },
    {
        "question": "Benodigde watersnelheid voor zoutelektrolyse",  # Rephrasing of "Wat is de benodigde stroomsnelheid voor zoutelektrolyse?"
        "expected_keywords": ["stroomsnelheid", "zoutelektrolyse", "m3/h", "flow"],
        "category": "Salt Chlorination",
        "description": "Synonym test: watersnelheid = stroomsnelheid"
    },
    {
        "question": "Wat zijn voordelen en nadelen van low-salt?",  # Rephrasing of "Wat zijn de pro's en contra's van low-salt elektrolyse versus hydrolyse?"
        "expected_keywords": ["low-salt", "hydrolyse", "voordeel", "nadeel"],
        "category": "Technology Comparison",
        "description": "Synonym test: voordelen/nadelen = pro's/contra's"
    },
]

def test_chatbot():
    print("🧪 Testing Chatbot Synonym & FAQ Accuracy...")
    print("="*80)

    results = []

    for test in TEST_CASES:
        print(f"\n📝 Test: {test['category']}")
        print(f"🔄 {test['description']}")
        print(f"❓ Question: {test['question']}")

        try:
            start = time.time()
            response = requests.post(
                API_URL,
                json={"query": test['question']},
                timeout=30
            )
            elapsed = time.time() - start

            if response.status_code != 200:
                print(f"❌ Error {response.status_code}")
                results.append({"success": False, "category": test['category']})
                continue

            data = response.json()
            answer = data.get('answer', '')
            source = data.get('source', 'unknown')

            # Check keywords
            answer_lower = answer.lower()
            matched = sum(1 for kw in test['expected_keywords'] if kw.lower() in answer_lower)
            total = len(test['expected_keywords'])
            accuracy = (matched / total * 100) if total > 0 else 0

            print(f"✅ Response in {elapsed:.2f}s (source: {source})")
            print(f"📊 Keywords matched: {matched}/{total} ({accuracy:.0f}%)")

            # Show which keywords matched
            matched_kw = [kw for kw in test['expected_keywords'] if kw.lower() in answer_lower]
            missing_kw = [kw for kw in test['expected_keywords'] if kw.lower() not in answer_lower]
            if matched_kw:
                print(f"   ✓ Matched: {', '.join(matched_kw)}")
            if missing_kw:
                print(f"   ✗ Missing: {', '.join(missing_kw)}")

            print(f"📝 Answer preview: {answer[:150]}...")

            results.append({
                "success": True,
                "category": test['category'],
                "accuracy": accuracy,
                "elapsed": elapsed,
                "source": source
            })

        except Exception as e:
            print(f"❌ Error: {e}")
            results.append({"success": False, "category": test['category']})

    # Summary
    print("\n" + "="*80)
    print("📊 SUMMARY")
    print("="*80)

    successful = [r for r in results if r.get("success")]
    if successful:
        avg_accuracy = sum(r["accuracy"] for r in successful) / len(successful)
        avg_time = sum(r["elapsed"] for r in successful) / len(successful)

        # Count by source
        sources = {}
        for r in successful:
            src = r.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1

        print(f"✅ Success rate: {len(successful)}/{len(results)}")
        print(f"📊 Average keyword accuracy: {avg_accuracy:.1f}%")
        print(f"⚡ Average response time: {avg_time:.2f}s")
        print(f"📚 Sources used: {sources}")

        if avg_accuracy >= 90:
            print("\n🎉 EXCELLENT! 90%+ accuracy achieved!")
            print("✅ Synonym matching is working well!")
        elif avg_accuracy >= 75:
            print("\n✅ GOOD! Above 75% accuracy")
            print("💡 Some synonyms may need adjustment")
        elif avg_accuracy >= 50:
            print("\n⚠️  FAIR - needs improvement")
            print("💡 Check synonym definitions and FAQ matching logic")
        else:
            print("\n❌ POOR - significant issues")
            print("💡 FAQ matching or synonym system may not be working")
    else:
        print("❌ All tests failed - check if server is running")

if __name__ == "__main__":
    test_chatbot()
