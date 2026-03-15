# app/keyword_search.py
"""
Keyword-based FAQ search using TF-IDF and synonym expansion.

This module provides OFFLINE search that works WITHOUT the OpenAI API.
It's used as:
1. Primary search when embeddings are not yet built
2. Fallback when vector similarity confidence is low
3. Cross-validation to catch domain mismatches

Algorithm:
    1. Normalize query with synonym expansion
    2. Score each FAQ entry by term overlap (TF-IDF style)
    3. Apply bonus for exact phrase matches
    4. Apply bonus for same category/domain
    5. Return top-k with scores

Key advantage over pure vector search:
    - "zuurtegraad" and "ph" and "acidité" all match the same FAQ entries
    - Works instantly with zero API calls
    - Immune to embedding model quota issues
"""

import os
import re
import math
import logging
from typing import List, Dict, Tuple, Optional, Any
from functools import lru_cache
from threading import Lock

from .synonyms import (
    normalize_with_synonyms,
    expand_with_synonyms,
    expand_with_synonyms_fuzzy,
    get_related_terms,
    detect_domains_in_text,
)
from .utils import normalize_text

logger = logging.getLogger(__name__)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

# Minimum score threshold to consider a result relevant
MIN_SCORE_THRESHOLD = 0.05

# Weight for question match vs answer match
QUESTION_WEIGHT = 3.0  # Increased from 2.0 - questions are more important
ANSWER_WEIGHT = 1.0

# Bonus for exact phrase match
EXACT_PHRASE_BONUS = 2.0  # Increased from 1.5 - exact phrases should dominate

# Bonus for multi-word canonical match
CANONICAL_PHRASE_BONUS = 2.5  # When normalized phrases match closely

# Bonus when domains match
DOMAIN_MATCH_BONUS = 0.5  # Increased from 0.3

# Bonus for high term coverage (80%+ query terms matched)
HIGH_COVERAGE_BONUS = 1.5

# Stop words to ignore (NL + FR + EN)
STOP_WORDS = {
    # NL
    "de", "het", "een", "van", "in", "is", "op", "te", "en", "dat", "die",
    "zijn", "bij", "met", "wat", "voor", "om", "dan", "naar", "hoe", "mijn",
    "mij", "kan", "ik", "er", "niet", "wel", "ook", "als", "maar", "dit",
    "nog", "ze", "hij", "we", "je", "zo", "uit", "worden", "heeft",
    "wordt", "was", "worden", "door", "meer", "moet", "geen", "gaat",
    # FR
    "le", "la", "les", "un", "une", "des", "et", "en", "je", "que",
    "qui", "est", "pas", "par", "sur", "au", "du", "pour", "il", "elle",
    "nous", "vous", "ils", "elles", "se", "ne", "mon", "son", "ma", "sa",
    "ce", "ou", "mais", "donc", "car", "si", "tout", "plus", "aussi",
    # EN
    "the", "a", "an", "of", "in", "is", "on", "to", "and", "that", "are",
    "this", "for", "with", "how", "my", "can", "i", "not", "do", "does",
    "it", "at", "be", "by", "from", "or", "what", "when", "why", "where",
}


# ─── TF-IDF INDEX ─────────────────────────────────────────────────────────────

class FAQKeywordIndex:
    """
    In-memory keyword index for FAQ entries.

    Builds a TF-IDF-like index from FAQ entries and supports
    fast synonym-aware search without any API calls.
    """

    def __init__(self):
        self._entries: List[Dict] = []      # Raw FAQ entries
        self._index: List[Dict] = []        # Processed entries with tokens
        self._idf: Dict[str, float] = {}    # IDF weights per term
        self._lock = Lock()
        self._built = False

    def build(self, faq_entries: List[Dict]) -> None:
        """
        Build keyword index from FAQ entries.

        Args:
            faq_entries: List of dicts with 'question' and 'answer' keys
        """
        with self._lock:
            self._entries = faq_entries
            self._index = []

            # Build per-entry token sets
            doc_freq: Dict[str, int] = {}

            for entry in faq_entries:
                question = entry.get("question", "")
                answer = entry.get("answer", "")
                category = entry.get("category", "")

                # Process question (with synonym normalization)
                q_tokens = self._tokenize(question)
                a_tokens = self._tokenize(answer)
                all_tokens = q_tokens | a_tokens

                # Track document frequency for IDF
                for tok in all_tokens:
                    doc_freq[tok] = doc_freq.get(tok, 0) + 1

                self._index.append({
                    "question": question,
                    "answer": answer,
                    "category": category,
                    "q_tokens": q_tokens,
                    "a_tokens": a_tokens,
                    "all_tokens": all_tokens,
                    "domains": detect_domains_in_text(question + " " + answer),
                })

            # Calculate IDF
            n = len(faq_entries)
            self._idf = {
                tok: math.log((n + 1) / (freq + 1)) + 1
                for tok, freq in doc_freq.items()
            }

            self._built = True
            logger.info(f"Keyword index built: {len(self._index)} entries, {len(self._idf)} unique terms")

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = MIN_SCORE_THRESHOLD,
    ) -> List[Tuple[Dict, float]]:
        """
        Search FAQ entries by keyword similarity.

        Returns:
            List of (entry, score) tuples, sorted by score descending
        """
        if not self._built or not self._index:
            return []

        # Normalize and expand query with synonyms (including fuzzy for typos)
        query_expanded = expand_with_synonyms_fuzzy(query)
        query_tokens = self._tokenize(query_expanded)
        query_domains = detect_domains_in_text(query + " " + query_expanded)

        if not query_tokens:
            return []

        # Also normalize original query for exact phrase matching
        query_norm = normalize_text(normalize_with_synonyms(query))

        scores: List[Tuple[int, float]] = []

        for i, doc in enumerate(self._index):
            score = self._score(
                query_tokens=query_tokens,
                query_domains=query_domains,
                query_norm=query_norm,
                doc=doc,
            )
            if score >= min_score:
                scores.append((i, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        # Return top-k with entry data
        results = []
        for i, score in scores[:top_k]:
            entry = self._entries[i].copy()
            results.append((entry, score))

        return results

    def _score(
        self,
        query_tokens: set,
        query_domains: set,
        query_norm: str,
        doc: Dict,
    ) -> float:
        """
        Score a document against the query.

        Scoring factors:
        1. TF-IDF weighted token overlap (question weighted higher)
        2. Exact phrase match bonus
        3. Canonical phrase similarity bonus
        4. High coverage bonus (80%+ terms matched)
        5. Domain match bonus
        """
        score = 0.0

        if not query_tokens:
            return 0.0

        # ── TF-IDF overlap ────────────────────────────────────────────────────

        # Question tokens match (weighted higher)
        q_overlap = query_tokens & doc["q_tokens"]
        for tok in q_overlap:
            idf = self._idf.get(tok, 1.0)
            score += QUESTION_WEIGHT * idf

        # Answer tokens match
        a_overlap = query_tokens & doc["a_tokens"]
        for tok in a_overlap:
            idf = self._idf.get(tok, 1.0)
            score += ANSWER_WEIGHT * idf

        if score == 0.0:
            return 0.0

        # ── Coverage bonus ────────────────────────────────────────────────────
        # If query is short and most terms match, boost heavily
        coverage = len(q_overlap) / max(len(query_tokens), 1)
        if coverage >= 0.8:
            score *= HIGH_COVERAGE_BONUS

        # Normalize by query length
        score = score / (len(query_tokens) + 1)

        # ── Exact phrase bonus ────────────────────────────────────────────────

        doc_q_norm = normalize_text(normalize_with_synonyms(doc["question"]))
        doc_a_norm = normalize_text(normalize_with_synonyms(doc["answer"]))

        # Check if query phrase appears verbatim in question/answer
        if query_norm and len(query_norm) > 3:
            if query_norm in doc_q_norm:
                score *= EXACT_PHRASE_BONUS
            elif query_norm in doc_a_norm:
                score *= (EXACT_PHRASE_BONUS * 0.7)

        # ── Canonical phrase similarity ───────────────────────────────────────
        # NEW: Check if query and doc question have high word-level similarity
        # after synonym normalization (catches "pH te laag" == "acidité trop basse")
        if query_norm and len(query_norm) > 5 and len(doc_q_norm) > 5:
            # Simple overlap check: if 60%+ of words in both match
            q_words = set(query_norm.split())
            doc_q_words = set(doc_q_norm.split())

            if q_words and doc_q_words:
                overlap_ratio = len(q_words & doc_q_words) / min(len(q_words), len(doc_q_words))
                if overlap_ratio >= 0.6:
                    score *= CANONICAL_PHRASE_BONUS

        # ── Domain match bonus ────────────────────────────────────────────────

        if query_domains and doc["domains"]:
            common_domains = query_domains & doc["domains"]
            if common_domains:
                score += DOMAIN_MATCH_BONUS * len(common_domains)

        return score

    def _tokenize(self, text: str) -> set:
        """
        Tokenize text into a set of meaningful terms.

        Steps:
        1. Normalize (lowercase, remove accents)
        2. Apply synonym normalization (so pH = zuurtegraad = acidité)
        3. Split into tokens
        4. Remove stop words and very short tokens
        """
        if not text:
            return set()

        # Normalize with synonyms for consistent matching
        norm = normalize_text(normalize_with_synonyms(text))

        # Tokenize
        tokens = re.findall(r'\b[a-z0-9]{2,}\b', norm)

        # Remove stop words
        return {t for t in tokens if t not in STOP_WORDS}

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def entry_count(self) -> int:
        return len(self._entries)


# ─── GLOBAL INSTANCE ──────────────────────────────────────────────────────────

_index: Optional[FAQKeywordIndex] = None
_index_lock = Lock()


def get_keyword_index() -> FAQKeywordIndex:
    """Get or create the global keyword index."""
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = FAQKeywordIndex()
    return _index


def build_keyword_index(faq_entries: List[Dict]) -> None:
    """
    Build the keyword index from FAQ entries.

    Call this after loading FAQ from JSONL.

    Args:
        faq_entries: List of dicts with 'question' and 'answer' keys
    """
    idx = get_keyword_index()
    idx.build(faq_entries)
    logger.info(f"✅ Keyword index built with {idx.entry_count} entries")


def _load_faq_direct(jsonl_path: Optional[str] = None) -> List[Dict]:
    """
    Load FAQ entries directly from JSONL without langchain dependency.
    Used as fallback when full faq_jsonl module is not available.
    """
    import json as _json

    if jsonl_path is None:
        # Try common paths
        base = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(base, "data", "faq.jsonl"),
            os.path.join(base, "data", "all", "faq", "FAQAI.jsonl"),
        ]
        for p in candidates:
            if os.path.exists(p):
                jsonl_path = p
                break

    if not jsonl_path or not os.path.exists(jsonl_path):
        return []

    entries = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
                # Normalize field names (old format uses Vraag/Antwoord)
                question = (
                    obj.get("question") or obj.get("Vraag") or
                    obj.get("vraag") or ""
                ).strip()
                answer = (
                    obj.get("answer") or obj.get("Antwoord") or
                    obj.get("antwoord") or ""
                ).strip()
                category = (
                    obj.get("category") or obj.get("Categorie") or
                    obj.get("categorie") or ""
                ).strip()

                if question and answer:
                    entries.append({
                        "question": question,
                        "answer": answer,
                        "category": category,
                    })
            except Exception:
                continue

    return entries


def keyword_search(
    query: str,
    top_k: int = 5,
    min_score: float = MIN_SCORE_THRESHOLD,
) -> List[Tuple[Dict, float]]:
    """
    Perform keyword-based FAQ search using synonym expansion.

    Works completely OFFLINE with no API calls.
    Handles synonyms across NL/FR/EN automatically.

    Args:
        query: User question in any supported language
        top_k: Maximum number of results to return
        min_score: Minimum relevance score (0.0 to 1.0+)

    Returns:
        List of (faq_entry, score) tuples, sorted by relevance
    """
    idx = get_keyword_index()

    if not idx.is_built:
        # Try to auto-build: first via full module, then direct JSONL load
        try:
            from .faq_jsonl import get_faq_manager
            manager = get_faq_manager()
            entries = manager.faq_entries or manager.load_faq()
            idx.build(entries)
        except Exception:
            # Fallback: load directly from JSONL (no langchain needed)
            try:
                entries = _load_faq_direct()
                if entries:
                    idx.build(entries)
                    logger.info(f"Keyword index built from direct JSONL load: {len(entries)} entries")
                else:
                    logger.warning("No FAQ entries found for keyword index")
                    return []
            except Exception as e:
                logger.warning(f"Could not build keyword index: {e}")
                return []

    return idx.search(query, top_k=top_k, min_score=min_score)


def keyword_search_as_documents(
    query: str,
    top_k: int = 5,
    min_score: float = MIN_SCORE_THRESHOLD,
) -> List[Any]:
    """
    Perform keyword search and return LangChain Document objects.

    This allows using keyword search as a drop-in replacement
    for vector similarity search in the RAG pipeline.

    Args:
        query: User question
        top_k: Maximum results
        min_score: Minimum relevance score

    Returns:
        List of LangChain Document-like objects
    """
    from langchain_core.documents import Document

    results = keyword_search(query, top_k=top_k, min_score=min_score)

    documents = []
    for entry, score in results:
        question = entry.get("question", "")
        answer = entry.get("answer", "")
        category = entry.get("category", "")

        content = f"Question: {question}\nAnswer: {answer}"

        doc = Document(
            page_content=content,
            metadata={
                "question": question,
                "answer": answer,
                "category": category,
                "source_type": "faq",
                "keyword_score": score,
                "search_method": "keyword",
            },
        )
        documents.append(doc)

    return documents
