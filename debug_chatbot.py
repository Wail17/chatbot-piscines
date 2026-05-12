"""Diagnostic en 1 commande - teste pourquoi les réponses sont pourries.

Usage:
    python debug_chatbot.py
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

print("=" * 70)
print("DIAGNOSTIC CHATBOT-PISCINES")
print("=" * 70)

# --- 1. Cles API ---------------------------------------------------------
oa_key = os.getenv("OPENAI_API_KEY", "")
an_key = os.getenv("ANTHROPIC_API_KEY", "")
print(f"\n[1] OPENAI_API_KEY    set: {bool(oa_key)}  len: {len(oa_key)}  starts: {oa_key[:12]}...")
print(f"[1] ANTHROPIC_API_KEY set: {bool(an_key)}  len: {len(an_key)}  starts: {an_key[:12]}...")

if not an_key:
    print("\n[X] ANTHROPIC_API_KEY manquante - cree un .env avec ANTHROPIC_API_KEY=sk-ant-...")
    sys.exit(1)

# --- 2. Test direct API Anthropic ----------------------------------------
print("\n[2] Test brut API Anthropic (claude-haiku-4-5-20251001)...")
try:
    from anthropic import Anthropic
    c = Anthropic(api_key=an_key)
    r = c.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with the word OK only."}],
    )
    print(f"    OK -> {r.content[0].text.strip()!r}")
except Exception as e:
    print(f"    ECHEC -> {type(e).__name__}: {e}")
    print("    >>> C'est ICI le bug. Verifie ta cle Anthropic / quota / billing.")
    sys.exit(1)

# --- 3. Charge la FAQ ----------------------------------------------------
print("\n[3] Chargement FAQ JSONL...")
try:
    from app.faq_jsonl import get_faq_manager
    mgr = get_faq_manager()
    entries = mgr.load_faq()
    print(f"    OK -> {len(entries)} entries chargees depuis {mgr.jsonl_path}")
    if entries:
        e0 = entries[0]
        print(f"    Sample row 0: Q={ (e0.get('question') or '')[:60]!r}")
except Exception as e:
    print(f"    ECHEC -> {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

# --- 4. Appel expert_answer comme le fait /chat --------------------------
print("\n[4] Appel expert_answer (le path principal de /chat)...")
try:
    from app.rag import expert_answer
    q = "Hoe reset ik mijn Wifipool Gen 2?"
    out = expert_answer(q, "nl", entries, history=None)
    print(f"    question: {q}")
    print(f"    error?  : {out.get('error')!r}")
    print(f"    confidence: {out.get('confidence')}")
    print(f"    primary_source: {out.get('primary_source')}")
    ans = (out.get('answer') or '').strip()
    print(f"    answer ({len(ans)} chars):")
    print("    " + (ans[:400] or "(VIDE - c'est le bug!)").replace("\n", "\n    "))
    if out.get('error'):
        print("\n    >>> expert_answer renvoie une ERREUR - le fallback s'active = mauvaise qualite.")
    elif not ans:
        print("\n    >>> answer vide - Claude renvoie du JSON mal forme.")
        print(f"    raw: {out.get('raw', '')[:300]!r}")
    else:
        print("\n    >>> Si tu vois une vraie reponse ici, le backend marche.")
        print("    >>> Le bug est cote front, cote deploiement, ou cote env vars Render.")
except Exception as e:
    print(f"    ECHEC -> {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n" + "=" * 70)
print("FIN DIAGNOSTIC")
print("=" * 70)
