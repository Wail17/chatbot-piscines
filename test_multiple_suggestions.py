#!/usr/bin/env python3
"""
Script de test pour le système de suggestions multiples
"""
import sys
import os

# Ajouter le répertoire parent au PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from app.main import _get_faq_suggestions_with_scores, _build_response_from_suggestions, _reload_faq

def test_multiple_suggestions():
    """Teste le système de suggestions multiples"""

    print("=" * 80)
    print("TEST: Système de suggestions multiples FAQ")
    print("=" * 80)

    # Recharger la FAQ
    count, _ = _reload_faq()
    print(f"\n✓ FAQ chargée: {count} entrées\n")

    # Test 1: Question sur la condensation (devrait avoir un bon match)
    print("\n" + "-" * 80)
    print("TEST 1: Question avec haute similarité")
    print("-" * 80)
    question1 = "Comment éviter la condensation sur mon appareil?"
    print(f"Question: {question1}\n")

    suggestions1 = _get_faq_suggestions_with_scores(
        user_q=question1,
        top_k=4,
        min_similarity=0.3,
        lang_code="nl"
    )

    print(f"Nombre de suggestions: {len(suggestions1)}")
    for i, sugg in enumerate(suggestions1, 1):
        print(f"\n  {i}. Score: {sugg['similarity_score']}%")
        print(f"     Question: {sugg['question'][:100]}...")
        print(f"     Catégorie: {sugg['category']}")

    if suggestions1:
        response1 = _build_response_from_suggestions(
            suggestions=suggestions1,
            user_q=question1,
            lang_code="nl"
        )
        print(f"\n  Type de réponse: {response1['response']['type']}")
        print(f"  Message: {response1['response']['message'][:100]}...")

    # Test 2: Question générale (devrait avoir plusieurs suggestions moyennes)
    print("\n" + "-" * 80)
    print("TEST 2: Question générale")
    print("-" * 80)
    question2 = "Comment utiliser le wifipool?"
    print(f"Question: {question2}\n")

    suggestions2 = _get_faq_suggestions_with_scores(
        user_q=question2,
        top_k=4,
        min_similarity=0.3,
        lang_code="nl"
    )

    print(f"Nombre de suggestions: {len(suggestions2)}")
    for i, sugg in enumerate(suggestions2, 1):
        print(f"\n  {i}. Score: {sugg['similarity_score']}%")
        print(f"     Question: {sugg['question'][:100]}...")
        print(f"     Catégorie: {sugg['category']}")

    if suggestions2:
        response2 = _build_response_from_suggestions(
            suggestions=suggestions2,
            user_q=question2,
            lang_code="nl"
        )
        print(f"\n  Type de réponse: {response2['response']['type']}")

    # Test 3: Question sans match évident
    print("\n" + "-" * 80)
    print("TEST 3: Question sans match évident")
    print("-" * 80)
    question3 = "Quelle est la météo aujourd'hui?"
    print(f"Question: {question3}\n")

    suggestions3 = _get_faq_suggestions_with_scores(
        user_q=question3,
        top_k=4,
        min_similarity=0.3,
        lang_code="nl"
    )

    print(f"Nombre de suggestions: {len(suggestions3)}")
    if suggestions3:
        for i, sugg in enumerate(suggestions3, 1):
            print(f"\n  {i}. Score: {sugg['similarity_score']}%")
            print(f"     Question: {sugg['question'][:80]}...")

        response3 = _build_response_from_suggestions(
            suggestions=suggestions3,
            user_q=question3,
            lang_code="nl"
        )
        print(f"\n  Type de réponse: {response3['response']['type']}")
    else:
        print("  Aucune suggestion trouvée")

    print("\n" + "=" * 80)
    print("TESTS TERMINÉS")
    print("=" * 80)

if __name__ == "__main__":
    try:
        test_multiple_suggestions()
    except Exception as e:
        print(f"\n❌ ERREUR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
