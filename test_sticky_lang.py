"""Internal test for the per-client sticky language fix.

Tests the new _LANG_BY_CLIENT cache without booting the server / hitting LLMs.
"""
import sys
import time
sys.path.insert(0, ".")

from app import main as M

# ── Test 1: store + recall in a session ──────────────────────────────
M._LANG_BY_CLIENT.clear()
client = "client-A"
M._remember_client_lang(client, "nl")
assert M._get_client_lang(client) == "nl", "should remember nl"
print("[OK] T1 — sticky stores nl and recalls it")

# ── Test 2: only nl/fr/en/de are accepted ────────────────────────────
M._LANG_BY_CLIENT.clear()
M._remember_client_lang(client, "es")
assert M._get_client_lang(client) is None, "should reject 'es'"
M._remember_client_lang(client, "")
assert M._get_client_lang(client) is None, "should reject empty"
M._remember_client_lang(client, None)
assert M._get_client_lang(client) is None, "should reject None"
print("[OK] T2 — only the 4 supported languages get stored")

# ── Test 3: empty client_id is a no-op ──────────────────────────────
M._LANG_BY_CLIENT.clear()
M._remember_client_lang("", "nl")
M._remember_client_lang(None, "nl")
assert len(M._LANG_BY_CLIENT) == 0, "should ignore empty client"
print("[OK] T3 — empty client_id is a no-op")

# ── Test 4: TTL expiry ──────────────────────────────────────────────
M._LANG_BY_CLIENT.clear()
M._LANG_CLIENT_TTL = 0.05  # 50 ms for test
M._remember_client_lang(client, "fr")
assert M._get_client_lang(client) == "fr"
time.sleep(0.1)
assert M._get_client_lang(client) is None, "should expire after TTL"
M._LANG_CLIENT_TTL = 1800.0  # restore
print("[OK] T4 — entries expire after TTL")

# ── Test 5: overwrite (lang switch within session) ──────────────────
M._LANG_BY_CLIENT.clear()
M._remember_client_lang(client, "nl")
M._remember_client_lang(client, "fr")
assert M._get_client_lang(client) == "fr", "newer lang should win"
print("[OK] T5 — newer language overwrites the older one")

# ── Test 6: independent clients ─────────────────────────────────────
M._LANG_BY_CLIENT.clear()
M._remember_client_lang("a", "nl")
M._remember_client_lang("b", "fr")
M._remember_client_lang("c", "de")
assert M._get_client_lang("a") == "nl"
assert M._get_client_lang("b") == "fr"
assert M._get_client_lang("c") == "de"
assert M._get_client_lang("d") is None
print("[OK] T6 — sessions are isolated per client_id")

# ── Test 7: simulate the /chat cascade ──────────────────────────────
# Replicate the resolution logic from the /chat handler so we can verify
# the order: explicit > detect > stored_ref > clarify_ref > pending > sticky > en
def resolve_lang(*, explicit, detected, stored_ref, clarify_lang, pending,
                 sticky):
    if explicit in {"nl", "fr", "en", "de"}:
        return explicit
    code = detected
    if not code and stored_ref:
        code = stored_ref
    if not code and clarify_lang:
        code = clarify_lang
    if not code and pending:
        code = pending
    if not code and sticky:
        code = sticky
    return code or "en"

# Scenario A: explicit always wins
assert resolve_lang(explicit="nl", detected="fr", stored_ref=None,
                    clarify_lang=None, pending=None, sticky="de") == "nl"
print("[OK] T7a — explicit beats every other source")

# Scenario B: short follow-up, no detection, sticky saves us
# Before the fix this would have returned "en" (the bug we are killing).
got = resolve_lang(explicit="", detected="", stored_ref=None,
                   clarify_lang=None, pending=None, sticky="nl")
assert got == "nl", f"expected nl, got {got}"
print("[OK] T7b — sticky language rescues short follow-ups (the bug fix)")

# Scenario C: no sticky either → falls back to en (acceptable default)
got = resolve_lang(explicit="", detected="", stored_ref=None,
                   clarify_lang=None, pending=None, sticky=None)
assert got == "en"
print("[OK] T7c — fallback to en only when no sticky exists")

# Scenario D: brand-new conversation in French — first message detects fr,
# second short message keeps fr because sticky picked it up.
M._LANG_BY_CLIENT.clear()
turn1 = resolve_lang(explicit="", detected="fr", stored_ref=None,
                     clarify_lang=None, pending=None, sticky=None)
M._remember_client_lang(client, turn1)
assert turn1 == "fr"
turn2 = resolve_lang(explicit="", detected="", stored_ref=None,
                     clarify_lang=None, pending=None,
                     sticky=M._get_client_lang(client))
assert turn2 == "fr", f"turn 2 should still be fr, got {turn2}"
print("[OK] T7d — full multi-turn FR scenario stays in FR")

# Scenario E: the BAD scenario from the bug report —
# T1 NL, T2 short follow-up. Without sticky → 'en'. With sticky → 'nl'.
M._LANG_BY_CLIENT.clear()
t1 = resolve_lang(explicit="", detected="nl", stored_ref=None,
                  clarify_lang=None, pending=None, sticky=None)
M._remember_client_lang(client, t1)
assert t1 == "nl"
t2 = resolve_lang(explicit="", detected="", stored_ref=None,
                  clarify_lang=None, pending=None,
                  sticky=M._get_client_lang(client))
assert t2 == "nl", f"BUG: short follow-up after NL turn went to {t2}"
print("[OK] T7e — Dutch follow-up stays Dutch (was 'en' before the fix)")

print("\n=== All 7 sticky-language tests passed ===")
