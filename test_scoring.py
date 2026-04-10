"""Chatbot scoring test suite."""
import sys, os, json, time, inspect
sys.path.insert(0, '.')
os.environ.setdefault('OPENAI_API_KEY', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')

from app.main import _FAQ, _match_row_with_clarify, _normalize, _gpt_fallback_answer
from app.rag import _simple_language_detect, _cached_translation
from app.synonyms import normalize_with_synonyms, expand_with_synonyms_fuzzy
from app.keyword_search import keyword_search

print(f"FAQ loaded: {len(_FAQ)} entries\n")

# === 1. SYNONYMS ===
print("=== 1. SYNONYM EXPANSION (10 tests) ===")
syn_tests = [
    ("acidity", "ph"), ("chlorine", "chloor"), ("password", "wachtwoord"),
    ("calibrate", "kalibreren"), ("wifi signal", "signaal"),
    ("water temperature", "temperatuur"), ("salt level", "zout"),
    ("pump", "pomp"), ("filter", "filter"), ("reset", "resetten"),
]
syn_pass = 0
for query, expected in syn_tests:
    expanded = normalize_with_synonyms(query)
    fuzzy = expand_with_synonyms_fuzzy(query)
    has_match = expected in expanded.lower() or expected in fuzzy.lower()
    if has_match:
        syn_pass += 1
    status = "PASS" if has_match else "FAIL"
    print(f'  {status} | "{query}" -> "{expected}"')
print(f"  Score: {syn_pass}/{len(syn_tests)}\n")

# === 2. LANGUAGE DETECTION ===
print("=== 2. LANGUAGE DETECTION (10 tests) ===")
lang_tests = [
    ("How do I calibrate the pH sensor?", "en"),
    ("Comment calibrer le capteur pH?", "fr"),
    ("Hoe kalibreer ik de pH-sensor?", "nl"),
    ("Wie kalibriere ich den pH-Sensor?", "de"),
    ("What is the wifi password?", "en"),
    ("Quel est le mot de passe wifi?", "fr"),
    ("Wat is het wifi wachtwoord?", "nl"),
    ("My pool is green", "en"),
    ("Ma piscine est verte", "fr"),
    ("Mijn zwembad is groen", "nl"),
]
lang_pass = 0
for query, expected in lang_tests:
    detected = _simple_language_detect(query)
    match = detected == expected
    if match:
        lang_pass += 1
    status = "PASS" if match else "FAIL"
    print(f'  {status} | "{query}" -> "{detected}" (expect "{expected}")')
print(f"  Score: {lang_pass}/{len(lang_tests)}\n")

# === 3. FAQ MATCHING ===
print("=== 3. FAQ MATCHING - Dutch (10 tests) ===")
faq_tests = [
    ("Hoe weet ik of mijn wifipool Gen 1 of Gen 2 is?", ["gen 1", "gen 2"]),
    ("Waar vind ik handleidingen?", ["handleiding"]),
    ("condensatie vermijden", ["condensatie"]),
    ("timer toevoegen", ["timer"]),
    ("Hoe voeg ik een timer toe?", ["timer", "scheduler"]),
    ("wifi werkt niet", ["wifi"]),
    ("gen 1 of gen 2", ["gen 1", "gen 2"]),
    ("spa", ["spa"]),
    ("pH kalibreren", ["ph", "kalibr"]),
    ("zout te laag", ["zout"]),
]
faq_pass = 0
for query, expected_kws in faq_tests:
    matched, clarify = _match_row_with_clarify(query)
    if matched:
        q = (matched.get("question") or "").lower()
        a = (matched.get("answer") or "")[:100].lower()
        found_any = any(kw in q or kw in a for kw in expected_kws)
        if found_any:
            faq_pass += 1
        status = "PASS" if found_any else "FAIL"
        print(f'  {status} | "{query}" -> "{q[:60]}"')
    elif clarify:
        faq_pass += 1
        print(f'  PASS | "{query}" -> CLARIFY ({len(clarify)} options)')
    else:
        print(f'  FAIL | "{query}" -> NO MATCH')
print(f"  Score: {faq_pass}/{len(faq_tests)}\n")

# === 4. KEYWORD SEARCH ===
print("=== 4. KEYWORD SEARCH (10 tests) ===")
kw_tests = [
    ("condensatie", ["condensatie"]),
    ("handleidingen", ["handleiding"]),
    ("spa", ["spa"]),
    ("wifi wachtwoord", ["wifi", "wachtwoord", "paswoord"]),
    ("pH kalibreren sonde", ["ph", "kalibr", "sonde"]),
    ("pomp", ["pomp"]),
    ("zout toevoegen", ["zout"]),
    ("timer automatisatie", ["timer"]),
    ("gen 1 gen 2", ["gen 1", "gen 2"]),
    ("temperatuur instellen", ["temperatuur", "instel"]),
]
kw_pass = 0
for query, expected_kws in kw_tests:
    results = keyword_search(query, top_k=3)
    if results:
        top_entry, top_score = results[0]
        top_q = top_entry.get("question", "").lower()
        top_a = top_entry.get("answer", "")[:150].lower()
        combined = top_q + " " + top_a
        found_any = any(kw in combined for kw in expected_kws)
        if found_any:
            kw_pass += 1
        status = "PASS" if found_any else "FAIL"
        print(f'  {status} [{top_score:.1f}] | "{query}" -> "{top_q[:55]}"')
    else:
        print(f'  FAIL | "{query}" -> NO RESULTS')
print(f"  Score: {kw_pass}/{len(kw_tests)}\n")

# === 5. SPEED ===
print("=== 5. SPEED (10 queries) ===")
speed_qs = [
    "pH kalibreren", "wifi wachtwoord", "zout te laag", "condensatie",
    "pomp werkt niet", "gen 1 of gen 2", "timer toevoegen",
    "handleidingen", "spa", "wifi",
]
times = []
for q in speed_qs:
    t0 = time.time()
    _match_row_with_clarify(q)
    dt = (time.time() - t0) * 1000
    times.append(dt)
    print(f'  {dt:.0f}ms | "{q}"')
avg = sum(times) / len(times)
speed_pass = 1 if avg < 200 else 0
status = "PASS" if speed_pass else "FAIL"
print(f"  Average: {avg:.0f}ms (target <200ms) - {status}\n")

# === 6. GPT FALLBACK LANGUAGE ===
print("=== 6. GPT FALLBACK LANGUAGE (2 tests) ===")
src = inspect.getsource(_gpt_fallback_answer)
t1 = "_LANG_NAMES" in src
t2 = "lang_name}" in src
print(f'  {"PASS" if t1 else "FAIL"} | Dynamic language mapping')
print(f'  {"PASS" if t2 else "FAIL"} | Uses detected language in prompt')
gpt_pass = (1 if t1 else 0) + (1 if t2 else 0)
print(f"  Score: {gpt_pass}/2\n")

# === 7. TRANSLATION TOKENS ===
print("=== 7. TRANSLATION max_tokens (2 tests) ===")
src2 = inspect.getsource(_cached_translation)
has_2048 = "2048" in src2
print(f'  {"PASS" if has_2048 else "FAIL"} | Translation max_tokens >= 2048')
src3 = inspect.getsource(_gpt_fallback_answer)
has_1024 = "1024" in src3
print(f'  {"PASS" if has_1024 else "FAIL"} | GPT fallback max_tokens >= 1024')
tok_pass = (1 if has_2048 else 0) + (1 if has_1024 else 0)
print(f"  Score: {tok_pass}/2\n")

# === FINAL ===
print("=" * 60)
total_pass = syn_pass + lang_pass + faq_pass + kw_pass + speed_pass + gpt_pass + tok_pass
total_tests = len(syn_tests) + len(lang_tests) + len(faq_tests) + len(kw_tests) + 1 + 2 + 2
pct = total_pass * 100 // total_tests
grade = "A" if pct >= 90 else "B" if pct >= 80 else "C" if pct >= 70 else "D"
print(f"TOTAL: {total_pass}/{total_tests} ({pct}%) - Grade: {grade}")
print()
print("Breakdown:")
print(f"  1. Synonyms:       {syn_pass}/{len(syn_tests)}")
print(f"  2. Lang detect:    {lang_pass}/{len(lang_tests)}")
print(f"  3. FAQ matching:   {faq_pass}/{len(faq_tests)}")
print(f"  4. Keyword search: {kw_pass}/{len(kw_tests)}")
print(f"  5. Speed:          {speed_pass}/1")
print(f"  6. GPT fallback:   {gpt_pass}/2")
print(f"  7. Token limits:   {tok_pass}/2")
