#!/usr/bin/env python3
"""
Test chatbot accuracy with Dutch questions
"""
import requests
import time

API_URL = "http://localhost:8000/chat"

# Test cases with Dutch questions
TEST_CASES = [
    {
        "question": "Hoe kan ik de pH instellen?",
        "expected_keywords": ["pH", "instellen", "waarde"],
        "category": "pH Management"
    },
    {
        "question": "Probleem met de pomp",
        "expected_keywords": ["pomp", "probleem", "controle"],
        "category": "Pomp Issues"
    },
    {
        "question": "Hoe onderhoud ik mijn zwembad?",
        "expected_keywords": ["onderhoud", "zwembad", "pH", "filter"],
        "category": "Maintenance"
    },
    {
        "question": "Wat is RX redox?",
        "expected_keywords": ["RX", "redox", "chloor", "mV"],
        "category": "Chemistry"
    },
    {
        "question": "Mijn zwembad lekt",
        "expected_keywords": ["lek", "water", "niveau"],
        "category": "Leak Problem"
    },
]

def test_chatbot():
    print("🧪 Testing Chatbot Accuracy...")
    print("="*60)

    results = []

    for test in TEST_CASES:
        print(f"\n📝 Test: {test['category']}")
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

            # Check keywords
            answer_lower = answer.lower()
            matched = sum(1 for kw in test['expected_keywords'] if kw.lower() in answer_lower)
            total = len(test['expected_keywords'])
            accuracy = (matched / total * 100) if total > 0 else 0

            source = data.get('source', 'unknown')
            print(f"✅ Response in {elapsed:.2f}s (source: {source})")
            print(f"📊 Keywords: {matched}/{total} ({accuracy:.0f}%)")
            print(f"📝 Full Answer:\n{answer}\n")

            results.append({
                "success": True,
                "category": test['category'],
                "accuracy": accuracy,
                "elapsed": elapsed
            })

        except Exception as e:
            print(f"❌ Error: {e}")
            results.append({"success": False, "category": test['category']})

    # Summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)

    successful = [r for r in results if r.get("success")]
    if successful:
        avg_accuracy = sum(r["accuracy"] for r in successful) / len(successful)
        avg_time = sum(r["elapsed"] for r in successful) / len(successful)

        print(f"✅ Success rate: {len(successful)}/{len(results)}")
        print(f"📊 Average accuracy: {avg_accuracy:.1f}%")
        print(f"⚡ Average response time: {avg_time:.2f}s")

        if avg_accuracy >= 90:
            print("\n🎉 EXCELLENT! 90%+ accuracy achieved!")
        elif avg_accuracy >= 75:
            print("\n✅ GOOD! Above 75% accuracy")
        else:
            print("\n⚠️  Needs improvement")
    else:
        print("❌ All tests failed")

if __name__ == "__main__":
    test_chatbot()
