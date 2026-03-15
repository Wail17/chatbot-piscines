#!/usr/bin/env python3
"""
TEST FINAL - Évaluation manuelle par un humain
===============================================

Ce test montre les résultats AU MENTOR qui peut juger manuellement
si les réponses sont correctes ou non.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def manual_evaluation_test():
    """
    Affiche les résultats pour évaluation manuelle.
    Le mentor peut voir si les réponses sont pertinentes.
    """
    print("\n" + "=" * 80)
    print("🎓 TEST POUR ÉVALUATION MANUELLE DU MENTOR")
    print("=" * 80)
    print("Voici les questions et leurs réponses top-1.")
    print("Le mentor peut juger si c'est correct ou non.\n")

    from app.keyword_search import keyword_search, build_keyword_index, _load_faq_direct

    # Build index
    entries = _load_faq_direct()
    if not entries:
        print("❌ No FAQ entries found")
        return False

    build_keyword_index(entries)
    print(f"✓ Index construit avec {len(entries)} FAQs\n")

    # Test cases that a mentor would try
    test_queries = [
        # WiFi
        "wifi wachtwoord verkeerd",
        "wifi verbinding probleem",
        "wifi signaal te zwak",
        "wifi offline",

        # Calibration
        "pH sensor kalibreren",
        "hoe kalibreer ik pH",
        "calibration probleem",

        # Pump
        "pomp is lek",
        "pomp werkt niet",
        "circulatiepomp probleem",

        # Reset
        "apparaat resetten",
        "factory reset",
        "herstarten",

        # Flow
        "debiet te laag",
        "flow sensor",
        "geen flow",

        # Level
        "waterniveau te laag",
        "niveausensor",

        # ORP/RX
        "RX te hoog",
        "RX te laag",

        # Others
        "timer instellen",
        "failsafe",
        "app werkt niet",
        "apparaat offline",
    ]

    print(f"Testing {len(test_queries)} questions qu'un mentor pourrait poser...\n")
    print("=" * 80)

    for i, query in enumerate(test_queries, 1):
        results = keyword_search(query, top_k=1)

        print(f"\n[{i:2d}/{len(test_queries)}] QUESTION UTILISATEUR:")
        print(f"    \"{query}\"")
        print()

        if results:
            entry, score = results[0]
            question = entry.get('question', '')
            answer = entry.get('answer', '')

            print(f"    RÉPONSE FAQ (score: {score:.2f}):")
            print(f"    Q: {question}")
            print(f"    A: {answer[:200]}...")
        else:
            print(f"    ❌ AUCUNE RÉPONSE TROUVÉE")

        print()
        print("-" * 80)

    print("\n" + "=" * 80)
    print("Évaluation terminée!")
    print("Le mentor peut maintenant juger manuellement si les réponses sont bonnes.")
    print("=" * 80)

    return True


if __name__ == "__main__":
    try:
        manual_evaluation_test()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
