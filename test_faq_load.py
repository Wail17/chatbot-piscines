#!/usr/bin/env python3
"""
Script de test simplifié pour vérifier le chargement de FAQ
"""
import os
import json
from typing import List, Dict, Any

# Configuration
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
DATA_DIR = os.path.join(BASE_DIR, "data")
STORE_DIR = os.path.join(BASE_DIR, "store")
_FAQ_PATH = os.path.join(STORE_DIR, "faq_index.json")
_FAQ_FALLBACK_JSONL = os.path.join(DATA_DIR, "all", "faq", "FAQAI.jsonl")

def _coerce_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()

def _load_faq_from_store() -> List[dict]:
    try:
        with open(_FAQ_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []

def _load_faq_from_jsonl(path: str) -> List[dict]:
    print(f"[DEBUG] _load_faq_from_jsonl called with path: {path}")
    if not os.path.exists(path):
        print(f"[DEBUG] ERROR: Path does not exist: {path}")
        return []
    print(f"[DEBUG] Path exists, starting to read...")

    rows: List[dict] = []
    line_count = 0
    skipped_empty_q = 0
    skipped_parse_error = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line_count += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception as e:
                    skipped_parse_error += 1
                    print(f"[DEBUG] Parse error on line {line_count}: {e}")
                    continue

                question = _coerce_str(
                    obj.get("vraag")
                    or obj.get("Vraag")
                    or obj.get("question")
                    or obj.get("Question")
                )
                if not question:
                    skipped_empty_q += 1
                    continue

                category = _coerce_str(
                    obj.get("categorie")
                    or obj.get("Categorie")
                    or obj.get("category")
                    or obj.get("Category")
                )
                answer = _coerce_str(
                    obj.get("antwoord")
                    or obj.get("Antwoord")
                    or obj.get("answer")
                    or obj.get("Answer")
                )

                row: Dict[str, Any] = {
                    "category": category,
                    "question": question,
                    "answer": answer,
                    "source": path,
                }
                rows.append(row)
    except Exception as e:
        print(f"[DEBUG] ERROR loading FAQ JSONL: {type(e).__name__}: {e}")
        return []

    print(f"[DEBUG] JSONL parsing complete:")
    print(f"[DEBUG]   - Total lines read: {line_count}")
    print(f"[DEBUG]   - Parse errors: {skipped_parse_error}")
    print(f"[DEBUG]   - Empty questions: {skipped_empty_q}")
    print(f"[DEBUG]   - Valid FAQ items: {len(rows)}")
    return rows

def test_load():
    print(f"[DEBUG] FAQ file path: {_FAQ_FALLBACK_JSONL}")
    print(f"[DEBUG] FAQ file exists: {os.path.exists(_FAQ_FALLBACK_JSONL)}")
    if os.path.exists(_FAQ_FALLBACK_JSONL):
        file_size = os.path.getsize(_FAQ_FALLBACK_JSONL)
        print(f"[DEBUG] FAQ file size: {file_size} bytes")

    print("\n[DEBUG] Testing FAQ load from store...")
    data = _load_faq_from_store()
    print(f"[DEBUG] Loaded from store: {len(data)} items")

    if not data:
        print(f"\n[DEBUG] Store empty, loading from JSONL: {_FAQ_FALLBACK_JSONL}")
        data = _load_faq_from_jsonl(_FAQ_FALLBACK_JSONL)
        print(f"[DEBUG] Loaded from JSONL: {len(data)} items")

    print(f"\n[DEBUG] FAQ reloaded: {len(data)} items")
    if data:
        print(f"[DEBUG] First FAQ item: {data[0]}")
        print(f"\n[DEBUG] Sample of FAQ items (first 5):")
        for i, item in enumerate(data[:5], 1):
            print(f"  {i}. Q: {item['question'][:60]}...")
            print(f"     A: {item['answer'][:60] if item['answer'] else '(empty)'}...")
    else:
        print(f"[DEBUG] ERROR: FAQ is empty!")

    return data

if __name__ == "__main__":
    faq = test_load()
    print(f"\n{'='*60}")
    print(f"FINAL RESULT: Successfully loaded {len(faq)} FAQ items")
    print(f"{'='*60}")
