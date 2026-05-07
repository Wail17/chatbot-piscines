"""
Lexicon storage for untranslatable / brand-protected terms.

Used by the bulk-save translation pipeline so Sonnet 4.6 keeps brand names
(Wifipool, Beniferro, GEN 1, GEN 2, …) verbatim across languages.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

_LEXICON_PATH = os.path.join(os.path.dirname(__file__), "data", "lexicon.json")


def load_lexicon() -> List[Dict[str, str]]:
    if not os.path.exists(_LEXICON_PATH):
        return []
    try:
        with open(_LEXICON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        terms = data.get("terms") or []
    elif isinstance(data, list):
        terms = data
    else:
        return []
    cleaned: List[Dict[str, str]] = []
    for item in terms:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        if not term:
            continue
        note = str(item.get("note", "")).strip()
        cleaned.append({"term": term, "note": note})
    return cleaned


def save_lexicon(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    cleaned: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        note = str(item.get("note", "")).strip()
        cleaned.append({"term": term, "note": note})
    os.makedirs(os.path.dirname(_LEXICON_PATH), exist_ok=True)
    with open(_LEXICON_PATH, "w", encoding="utf-8") as f:
        json.dump({"terms": cleaned}, f, ensure_ascii=False, indent=2)
    return cleaned


def lexicon_prompt_block() -> str:
    """Return a system-prompt fragment listing protected terms for translation."""
    items = load_lexicon()
    if not items:
        return ""
    lines = ["Keep these terms VERBATIM in every language (do not translate, do not adapt casing):"]
    for it in items:
        if it.get("note"):
            lines.append(f"- {it['term']} — {it['note']}")
        else:
            lines.append(f"- {it['term']}")
    return "\n".join(lines)
