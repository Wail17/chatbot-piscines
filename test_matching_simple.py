#!/usr/bin/env python3
"""
Simple standalone test of matching logic without full app imports
"""
import os
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Dict, Any, Tuple, Set

# Copy the necessary normalization functions
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»""\"'`]\s*")

def _normalize(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\u00a0", " ").lower()
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _partial_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return 1.0
    matcher = SequenceMatcher(None, longer, shorter)
    best = 0.0
    for block in matcher.get_matching_blocks():
        start = max(block.a - block.b, 0)
        substring = longer[start:start + len(shorter)]
        if not substring:
            continue
        best = max(best, SequenceMatcher(None, substring, shorter).ratio())
        if best >= 0.999:
            return 1.0
    return best

def _token_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a = {tok for tok in a.split(" ") if tok}
    tokens_b = {tok for tok in b.split(" ") if tok}
    if not tokens_a or not tokens_b:
        return 0.0
    shared = len(tokens_a & tokens_b)
    return shared / float(max(len(tokens_a), len(tokens_b)))

def _fuzzy_token_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a = [tok for tok in a.split(" ") if tok]
    tokens_b = [tok for tok in b.split(" ") if tok]
    if not tokens_a or not tokens_b:
        return 0.0

    used: Set[str] = set()
    hits = 0
    for tok in tokens_a:
        best = 0.0
        best_token = None
        for candidate in tokens_b:
            if candidate in used:
                continue
            score = SequenceMatcher(None, tok, candidate).ratio()
            if score > best:
                best = score
                best_token = candidate
        if best_token is not None and best >= 0.74:
            used.add(best_token)
            hits += 1

    return hits / float(max(len(tokens_a), len(tokens_b)))

def _similarity(a: str, b: str) -> float:
    base = _ratio(a, b)
    partial = _partial_ratio(a, b)
    overlap = _token_overlap(a, b)
    fuzzy_overlap = _fuzzy_token_overlap(a, b)
    blend_one = min(1.0, base + overlap * 0.5)
    blend_two = min(1.0, base + fuzzy_overlap * 0.65)
    return max(base, partial, overlap, fuzzy_overlap, blend_one, blend_two)

def load_faq():
    """Load FAQ from JSONL file"""
    BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
    DATA_DIR = os.path.join(BASE_DIR, "data")
    FAQ_PATH = os.path.join(DATA_DIR, "all", "faq", "FAQAI.jsonl")

    rows = []
    with open(FAQ_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            question = str(obj.get("Vraag") or obj.get("question") or "").strip()
            answer = str(obj.get("Antwoord") or obj.get("answer") or "").strip()
            if question:
                rows.append({
                    "question": question,
                    "answer": answer,
                    "category": obj.get("Categorie", "")
                })
    return rows

def main():
    print("="*80)
    print("SIMPLE MATCHING TEST")
    print("="*80)

    # Load FAQ
    faq = load_faq()
    print(f"\nLoaded {len(faq)} FAQ items")

    # Test questions
    test_questions = [
        "Hoe reset ik mijn wifipool?",
        "Reset wifipool",
        "Hoe kan ik condensatie vermijden?",
        "Watertemperatuur",
        "pH sensor kalibreren"
    ]

    for test_q in test_questions:
        print(f"\n{'='*80}")
        print(f"Testing: '{test_q}'")
        print(f"{'='*80}")

        # Normalize the test question
        norm_test = _normalize(test_q)
        print(f"Normalized: '{norm_test}'")

        # Find best matches
        scores = []
        for row in faq:
            q = row["question"]
            norm_q = _normalize(q)

            # Check for exact match or substring match
            if norm_test == norm_q or norm_test in norm_q or norm_q in norm_test:
                scores.append((q, 1.0, "EXACT/SUBSTRING"))
                continue

            # Calculate similarity
            score = _similarity(norm_test, norm_q)
            scores.append((q, score, "SIMILARITY"))

        # Sort by score
        scores.sort(key=lambda x: x[1], reverse=True)

        # Show top 5
        print(f"\nTop 5 matches:")
        for i, (q, score, match_type) in enumerate(scores[:5], 1):
            q_display = q[:80] + "..." if len(q) > 80 else q
            print(f"  {i}. [{score:.4f}] ({match_type}) {q_display}")

        # Check if top match exceeds threshold
        threshold = 0.68
        if scores[0][1] >= threshold:
            print(f"\n✓ MATCH FOUND (score {scores[0][1]:.4f} >= threshold {threshold})")
        else:
            print(f"\n✗ NO MATCH (top score {scores[0][1]:.4f} < threshold {threshold})")

    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)

if __name__ == "__main__":
    main()
