#!/usr/bin/env python3
"""
Test chatbot response accuracy and quality
"""
import requests
import json
import time
from typing import List, Dict, Tuple

# API Configuration
API_URL = "https://chatbot-piscines.onrender.com/chat"
TIMEOUT = 30

# Test questions with expected keywords in responses
TEST_CASES = [
    {
        "question": "Comment régler le pH de ma piscine?",
        "expected_keywords": ["pH", "7", "régler", "ajuster"],
        "category": "Entretien pH"
    },
    {
        "question": "Qu'est-ce qu'un skimmer?",
        "expected_keywords": ["skimmer", "surface", "eau", "filtr"],
        "category": "Équipement"
    },
    {
        "question": "Problème de pompe, que faire?",
        "expected_keywords": ["pompe", "vérif", "filtr", "débit"],
        "category": "Dépannage"
    },
    {
        "question": "Comment entretenir ma piscine?",
        "expected_keywords": ["entretien", "pH", "chlore", "filtr"],
        "category": "Entretien général"
    },
    {
        "question": "Qu'est-ce que le RX redox?",
        "expected_keywords": ["RX", "redox", "chlor", "oxyd", "mV"],
        "category": "Chimie"
    },
    {
        "question": "Ma piscine a une fuite, que faire?",
        "expected_keywords": ["fuite", "eau", "perte", "niveau"],
        "category": "Problème"
    },
    {
        "question": "Comment installer le WiFiPool?",
        "expected_keywords": ["install", "wifi", "configur", "appareil"],
        "category": "Installation"
    },
    {
        "question": "Problème de chlore trop bas",
        "expected_keywords": ["chlore", "bas", "augment", "dosage"],
        "category": "Chimie"
    },
    {
        "question": "Comment nettoyer le filtre?",
        "expected_keywords": ["filtre", "nettoy", "rinç", "contre-lav"],
        "category": "Entretien"
    },
    {
        "question": "Eau verte dans la piscine",
        "expected_keywords": ["vert", "algue", "chlor", "traitement"],
        "category": "Problème eau"
    }
]

def test_question(question: str, expected_keywords: List[str], category: str) -> Dict:
    """Test a single question and return results"""
    print(f"\n{'='*60}")
    print(f"🧪 Test: {category}")
    print(f"❓ Question: {question}")

    start_time = time.time()

    try:
        response = requests.post(
            API_URL,
            json={"query": question},
            timeout=TIMEOUT,
            headers={"Content-Type": "application/json"}
        )

        elapsed = time.time() - start_time

        if response.status_code != 200:
            print(f"❌ Erreur HTTP {response.status_code}")
            return {
                "success": False,
                "question": question,
                "category": category,
                "error": f"HTTP {response.status_code}",
                "elapsed": elapsed
            }

        data = response.json()
        answer = data.get("answer", "")

        if not answer:
            print(f"❌ Pas de réponse")
            return {
                "success": False,
                "question": question,
                "category": category,
                "error": "No answer",
                "elapsed": elapsed
            }

        # Check for expected keywords
        answer_lower = answer.lower()
        matched_keywords = [kw for kw in expected_keywords if kw.lower() in answer_lower]
        match_rate = len(matched_keywords) / len(expected_keywords) * 100

        print(f"✅ Réponse reçue en {elapsed:.2f}s")
        print(f"📝 Réponse: {answer[:200]}{'...' if len(answer) > 200 else ''}")
        print(f"🎯 Mots-clés trouvés: {len(matched_keywords)}/{len(expected_keywords)} ({match_rate:.0f}%)")
        print(f"   Trouvés: {matched_keywords}")
        if len(matched_keywords) < len(expected_keywords):
            missing = [kw for kw in expected_keywords if kw not in matched_keywords]
            print(f"   Manquants: {missing}")

        # Suggestions
        suggestions = data.get("suggestions", [])
        if suggestions:
            print(f"💡 Suggestions: {suggestions[:3]}")

        # Quality score
        quality_score = match_rate
        if elapsed < 2:
            quality_score += 10  # Bonus for fast response
        if len(answer) > 50:
            quality_score += 5   # Bonus for detailed answer
        if suggestions:
            quality_score += 5   # Bonus for suggestions

        quality_score = min(100, quality_score)

        if quality_score >= 80:
            print(f"⭐ Score qualité: {quality_score:.0f}% - EXCELLENT")
        elif quality_score >= 60:
            print(f"⭐ Score qualité: {quality_score:.0f}% - BON")
        elif quality_score >= 40:
            print(f"⚠️  Score qualité: {quality_score:.0f}% - MOYEN")
        else:
            print(f"❌ Score qualité: {quality_score:.0f}% - FAIBLE")

        return {
            "success": True,
            "question": question,
            "category": category,
            "answer": answer,
            "elapsed": elapsed,
            "match_rate": match_rate,
            "quality_score": quality_score,
            "suggestions": suggestions
        }

    except requests.Timeout:
        print(f"⏱️  Timeout après {TIMEOUT}s")
        return {
            "success": False,
            "question": question,
            "category": category,
            "error": "Timeout",
            "elapsed": TIMEOUT
        }
    except Exception as e:
        print(f"❌ Erreur: {e}")
        return {
            "success": False,
            "question": question,
            "category": category,
            "error": str(e),
            "elapsed": time.time() - start_time
        }

def main():
    """Run all tests and generate report"""
    print("🚀 Démarrage des tests d'exactitude du chatbot")
    print(f"📍 API: {API_URL}")
    print(f"🧪 Nombre de tests: {len(TEST_CASES)}")

    # Check if API is available
    print("\n🔍 Vérification de disponibilité de l'API...")
    try:
        response = requests.get(API_URL.replace('/chat', '/health'), timeout=5)
        print(f"✅ API disponible (status: {response.status_code})")
    except Exception as e:
        print(f"⚠️  API pourrait être indisponible: {e}")
        print("   Les tests vont continuer, mais pourraient échouer...")

    # Run tests
    results = []
    for test_case in TEST_CASES:
        result = test_question(
            test_case["question"],
            test_case["expected_keywords"],
            test_case["category"]
        )
        results.append(result)
        time.sleep(0.5)  # Pause between requests

    # Generate report
    print("\n" + "="*60)
    print("📊 RAPPORT FINAL")
    print("="*60)

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n✅ Tests réussis: {len(successful)}/{len(results)} ({len(successful)/len(results)*100:.0f}%)")
    print(f"❌ Tests échoués: {len(failed)}/{len(results)}")

    if successful:
        avg_quality = sum(r["quality_score"] for r in successful) / len(successful)
        avg_time = sum(r["elapsed"] for r in successful) / len(successful)
        avg_match = sum(r["match_rate"] for r in successful) / len(successful)

        print(f"\n📈 Moyennes:")
        print(f"   Score qualité: {avg_quality:.1f}%")
        print(f"   Temps de réponse: {avg_time:.2f}s")
        print(f"   Taux de correspondance mots-clés: {avg_match:.1f}%")

    if failed:
        print(f"\n❌ Échecs détaillés:")
        for r in failed:
            print(f"   - {r['category']}: {r.get('error', 'Unknown')}")

    # Overall assessment
    print(f"\n🎯 ÉVALUATION GLOBALE:")
    if len(successful) == len(results) and avg_quality >= 80:
        print("   ⭐⭐⭐⭐⭐ EXCELLENT - Chatbot prêt pour production!")
    elif len(successful) >= len(results) * 0.8 and avg_quality >= 70:
        print("   ⭐⭐⭐⭐ TRÈS BON - Quelques ajustements recommandés")
    elif len(successful) >= len(results) * 0.6:
        print("   ⭐⭐⭐ BON - Améliorations nécessaires")
    elif len(successful) >= len(results) * 0.4:
        print("   ⭐⭐ MOYEN - Corrections importantes requises")
    else:
        print("   ⭐ FAIBLE - Révision majeure nécessaire")

    # Save results
    with open("/home/user/chatbot-piscines/test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Résultats sauvegardés: test_results.json")

if __name__ == "__main__":
    main()
