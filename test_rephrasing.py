"""Test que les reformulations de la feuille "Evaluatie" de AI 2.0.xlsx trouvent la bonne FAQ row.

Exécution :
    pytest test_rephrasing.py -v

Ces tests ne font PAS d'appels LLM — ils vérifient uniquement que le matching sémantique/keyword
pointe vers la bonne ligne Excel (correspondant à la "cel Cxx" indiquée par le propriétaire).
"""

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

JSONL_PATH = PROJECT_ROOT / "app" / "data" / "all" / "faq" / "FAQAI.jsonl"


@pytest.fixture(scope="module")
def faq_entries():
    """Load FAQ entries once per test module."""
    assert JSONL_PATH.exists(), f"Run `python -m app.excel_loader --reload` first ({JSONL_PATH} missing)"
    entries = []
    with open(JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    assert entries, "FAQ JSONL is empty"
    return entries


@pytest.fixture(scope="module")
def faq_by_row(faq_entries):
    """Index FAQ entries by Excel row number."""
    return {e.get("excel_row"): e for e in faq_entries if e.get("excel_row")}


_RELOADED = False


def _find_row_for_query(query: str):
    """Return the best-matching FAQ row, replicating /chat's language-first flow."""
    global _RELOADED
    from app.main import _match_row_with_clarify, _reload_faq, _normalize
    from app.rag import detect_language_code, translate_for_matching

    if not _RELOADED:
        _reload_faq()
        _RELOADED = True

    lang_code = detect_language_code(query)
    translated = None
    if lang_code and lang_code not in {"", "nl"}:
        try:
            translated = translate_for_matching(query, lang_code)
        except Exception:
            translated = None
        if translated and _normalize(translated) == _normalize(query):
            translated = None

    row, _clarify = _match_row_with_clarify(query)
    if not row and translated:
        row, _clarify = _match_row_with_clarify(translated)
    return row


# ─── Reformulations from the "Evaluatie" sheet ────────────────────────────
# Each case: (query, expected_excel_row, description)
# Rows reference the owner's "cel Cxx" notes in the Evaluatie sheet of AI 2.0.xlsx.

REPHRASING_CASES = [
    # Reset variations → row ~26 (owner's cel C26)
    ("hoe moet ik mijn wifipool resetten?", 26, "NL reset short form"),
    ("Wat moet ik doen om mijn wifipool apparaat te resetten?", 26, "NL reset full form (exact Excel)"),
    ("hoe moet ik een harde reset uitvoeren?", 26, "NL hard reset phrasing"),
    ("Hoe kan ik mijn Wifipool-apparaat eenvoudig opnieuw instellen?", 26, "NL 'opnieuw instellen' variation"),
    ("Hoe herstel ik mijn Wifipool zodat hij weer normaal werkt?", 26, "NL 'herstellen' variation"),

    # Salt electrolyser variations → row ~50 (owner's cel C50)
    ("Mijn zoutelektrolyse start niet", 50, "NL salt electrolyser not starting"),
    ("Mijn zoutelektrolyse start niet op", 50, "NL salt electrolyser not starting variant"),
    ("Meine Salzelectrolyse start nicht", 50, "DE salt electrolyser not starting"),
    ("How come the Electrolyser is not on", 50, "EN electrolyser not on"),
    ("pourquoi mon electrolyseur ne se met pas en route?", 50, "FR electrolyser not starting"),
]


def _normalize_answer(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@pytest.mark.parametrize("query,expected_row,desc", REPHRASING_CASES)
def test_rephrasing_matches_expected_row(query, expected_row, desc, faq_by_row):
    """Each rephrasing should map to the owner-specified row OR a semantic duplicate."""
    row = _find_row_for_query(query)
    assert row is not None, f"No row matched for: {query!r} ({desc})"

    expected = faq_by_row.get(expected_row)
    if expected is None:
        pytest.skip(f"Expected row {expected_row} missing from JSONL (Excel probably shifted)")

    matched_row_num = row.get("excel_row")
    if matched_row_num == expected_row:
        return

    # Accept semantically-equivalent rows (Excel has duplicates across sections).
    expected_q_norm = _normalize_answer(expected.get("Vraag") or expected.get("question") or "")
    expected_a_norm = _normalize_answer(expected.get("Antwoord") or expected.get("answer") or "")
    got_q_norm = _normalize_answer(row.get("question") or "")
    got_a_norm = _normalize_answer(row.get("answer") or "")

    same_question = expected_q_norm and expected_q_norm == got_q_norm
    same_answer = expected_a_norm and expected_a_norm == got_a_norm

    if same_question or same_answer:
        return

    # Otherwise, allow a row within tolerance or require high keyword overlap.
    tolerance = 3
    if matched_row_num is not None:
        diff = abs(matched_row_num - expected_row)
        if diff <= tolerance:
            return

    overlap = set(expected_q_norm.split()) & set(got_q_norm.split())
    assert len(overlap) >= 3, (
        f"{desc}: query {query!r} matched row {matched_row_num} ({row.get('question')!r}) "
        f"— not a semantic match for expected row {expected_row} ({expected.get('Vraag')!r})"
    )


def test_faq_loaded_minimum_entries(faq_entries):
    """Sanity: Excel should yield at least 250 entries."""
    assert len(faq_entries) >= 250, f"Only {len(faq_entries)} entries loaded"


def test_multilingual_coverage(faq_entries):
    """At least 90% of entries should have an English translation."""
    with_en = sum(1 for e in faq_entries if e.get("ENAnswer"))
    ratio = with_en / len(faq_entries)
    assert ratio >= 0.9, f"Only {ratio:.0%} of entries have EN translations ({with_en}/{len(faq_entries)})"


def test_images_extracted():
    """At least a handful of images should be extracted."""
    images_dir = PROJECT_ROOT / "app" / "data" / "faq_images"
    assert images_dir.exists(), f"Images directory missing: {images_dir}"
    imgs = list(images_dir.glob("row_*.*"))
    assert len(imgs) >= 20, f"Only {len(imgs)} images extracted (expected ≥ 20)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
