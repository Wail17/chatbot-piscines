"""Test d'accuracy LIVE contre le serveur /chat.

Tire N questions au hasard dans la FAQ, les pose en NL/EN/FR + reformulations,
verifie si primary_source = excel_row attendu, sort un % + les echecs.

Usage:
    # Demarre d'abord le serveur dans une autre fenetre:
    uvicorn app.main:app --port 8000

    # Puis lance les tests:
    python test_accuracy_live.py
    python test_accuracy_live.py --url https://ton-app.onrender.com --n 50
"""
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

FAQ_PATH = Path(__file__).parent / "app" / "data" / "all" / "faq" / "FAQAI.jsonl"


def load_faq() -> List[dict]:
    rows = []
    with open(FAQ_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def paraphrase_nl(q: str) -> str:
    """Reformulation simple : ajoute des mots de remplissage, vire la ponctuation."""
    q = q.strip().rstrip("?!.")
    swaps = [
        (r"\bHoe kan ik\b", "Hoe doe ik om"),
        (r"\bHoe\b", "Op welke manier"),
        (r"\bWaarom\b", "Om welke reden"),
        (r"\bWaar\b", "Waar ergens"),
        (r"\bWat is\b", "Kun je uitleggen wat"),
        (r"\bKan ik\b", "Is het mogelijk om"),
    ]
    for pat, rep in swaps:
        new = re.sub(pat, rep, q, count=1, flags=re.IGNORECASE)
        if new != q:
            return new + " ?"
    # Fallback: prepend a colloquial opener
    return "Vraag: " + q.lower() + " ?"


VARIANTS = ("nl_original", "nl_paraphrase", "en", "fr")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def build_test_cases(rows: List[dict], n: int, seed: int) -> List[dict]:
    rng = random.Random(seed)
    pool = [r for r in rows if (r.get("Vraag") or "").strip() and r.get("excel_row")]
    sample = rng.sample(pool, min(n, len(pool)))
    cases = []
    for r in sample:
        row = r["excel_row"]
        nl_q = r["Vraag"].strip()
        # Expected texts for fallback matching (when primary_source is missing)
        expected_titles = {_norm(nl_q)}
        if r.get("ENQuestion"):
            expected_titles.add(_norm(r["ENQuestion"]))
        if r.get("FRQuestion"):
            expected_titles.add(_norm(r["FRQuestion"]))
        for at in r.get("alt_questions", []) or []:
            expected_titles.add(_norm(at))
        base = {"row": row, "expected_titles": list(expected_titles)}
        cases.append({**base, "variant": "nl_original", "query": nl_q})
        cases.append({**base, "variant": "nl_paraphrase", "query": paraphrase_nl(nl_q)})
        if r.get("ENQuestion"):
            cases.append({**base, "variant": "en", "query": r["ENQuestion"].strip()})
        if r.get("FRQuestion"):
            cases.append({**base, "variant": "fr", "query": r["FRQuestion"].strip()})
    return cases


def ask(url: str, query: str, lang: str, timeout: int = 30) -> dict:
    try:
        r = requests.post(
            f"{url.rstrip('/')}/chat",
            json={"query": query, "language": lang, "top_k": 1},
            timeout=timeout,
        )
        if r.status_code != 200:
            return {"_http": r.status_code, "_error": r.text[:200]}
        return r.json()
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--n", type=int, default=50, help="Number of FAQ rows to sample")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--slow", type=float, default=0.0, help="Delay between calls (sec)")
    p.add_argument("--target", type=float, default=95.0, help="Pass threshold %%")
    args = p.parse_args()

    print(f"Loading FAQ from {FAQ_PATH} ...")
    rows = load_faq()
    print(f"  {len(rows)} FAQ entries loaded")

    cases = build_test_cases(rows, args.n, args.seed)
    by_variant = {}
    for c in cases:
        by_variant[c["variant"]] = by_variant.get(c["variant"], 0) + 1
    print(f"  {len(cases)} test cases generated: {by_variant}")
    print(f"  target accuracy: {args.target}%")
    print(f"  posting to {args.url}/chat")
    print()

    # Health check
    try:
        h = requests.get(f"{args.url.rstrip('/')}/health", timeout=5)
        print(f"  /health -> {h.status_code}: {h.text[:100]}")
    except Exception as e:
        print(f"  /health unreachable: {e}")
        print(f"  >>> Le serveur n'est pas joignable. Lance d'abord:")
        print(f"      uvicorn app.main:app --port 8000")
        sys.exit(1)
    print()

    lang_map = {"nl_original": "nl", "nl_paraphrase": "nl", "en": "en", "fr": "fr"}

    results = []
    correct = 0
    started = time.time()

    for i, c in enumerate(cases, 1):
        lang = lang_map[c["variant"]]
        resp = ask(args.url, c["query"], lang)

        got_row = resp.get("primary_source") or (resp.get("metadata") or {}).get("excel_row")
        # citations sometimes carry excel_row
        if got_row is None and isinstance(resp.get("citations"), list) and resp["citations"]:
            first_cite = resp["citations"][0] or {}
            got_row = first_cite.get("excel_row") or first_cite.get("row")

        ok = got_row == c["row"]

        # Fallback path doesn't set primary_source; match by citation title
        if not ok:
            cite_title = ""
            if isinstance(resp.get("citations"), list) and resp["citations"]:
                cite_title = (resp["citations"][0] or {}).get("title", "")
            if cite_title and _norm(cite_title) in set(c.get("expected_titles") or []):
                ok = True
                got_row = f"~title-match~ ({cite_title[:40]})"

        if ok:
            correct += 1

        results.append({
            **c,
            "expected_row": c["row"],
            "got_row": got_row,
            "ok": ok,
            "source": resp.get("source"),
            "confidence": resp.get("confidence"),
            "error": resp.get("_error") or resp.get("error"),
            "answer_preview": (resp.get("answer") or "")[:120],
        })

        mark = "OK" if ok else "X "
        running_pct = 100.0 * correct / i
        print(f"  [{i:3d}/{len(cases)}] {mark} {c['variant']:14s} row {c['row']:3d} -> {got_row}  ({running_pct:.1f}%)")
        if args.slow > 0:
            time.sleep(args.slow)

    elapsed = time.time() - started
    pct = 100.0 * correct / len(cases) if cases else 0.0

    print()
    print("=" * 70)
    print(f"  ACCURACY: {correct}/{len(cases)} = {pct:.1f}%  (target {args.target}%)")
    print(f"  elapsed: {elapsed:.1f}s  ({elapsed/len(cases):.2f}s/req)")
    print("=" * 70)

    # Breakdown by variant
    print("\n  Per variant:")
    for v in VARIANTS:
        subset = [r for r in results if r["variant"] == v]
        if subset:
            sub_ok = sum(1 for r in subset if r["ok"])
            print(f"    {v:14s}  {sub_ok:3d}/{len(subset):3d} = {100.0*sub_ok/len(subset):.1f}%")

    # Show failures
    failures = [r for r in results if not r["ok"]]
    if failures:
        print(f"\n  First 20 failures (out of {len(failures)}):")
        for r in failures[:20]:
            print(f"    row {r['expected_row']:3d} ({r['variant']:14s})  got={r['got_row']}  conf={r['confidence']}  src={r['source']}")
            print(f"       Q: {r['query'][:90]!r}")
            if r.get("error"):
                print(f"       err: {r['error']}")
            elif r.get("answer_preview"):
                print(f"       A: {r['answer_preview']!r}")

    # Persist
    out = Path(__file__).parent / "test_results_accuracy.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": {"correct": correct, "total": len(cases), "pct": pct}, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n  Full results saved to {out}")

    sys.exit(0 if pct >= args.target else 1)


if __name__ == "__main__":
    main()
