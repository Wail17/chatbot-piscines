# app/analytics.py
"""
Analytics tracker for the chatbot.

Tracks:
1. Question frequency (most asked questions)
2. No-answer questions (FAQ gaps to fill)
3. Low-confidence answers (areas to improve)
4. Language distribution
5. Cache hit rate

Storage: JSONL file (appended, never deleted)
Report: In-memory aggregation for /analytics endpoint

No external dependencies. No API calls.
"""

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from threading import Lock
from collections import defaultdict, Counter
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

ANALYTICS_FILE = "analytics.jsonl"
MAX_IN_MEMORY = 10_000   # Keep last N events in memory for fast aggregation
FLUSH_EVERY = 10         # Write to disk every N events


# ─── EVENT TYPES ──────────────────────────────────────────────────────────────

class EventType:
    QUESTION = "question"         # User asked a question
    NO_ANSWER = "no_answer"       # No FAQ match found
    LOW_CONFIDENCE = "low_conf"   # Answer returned but confidence was low
    CACHE_HIT = "cache_hit"       # Response served from cache
    ERROR = "error"               # Internal error occurred
    FEEDBACK_GOOD = "feedback_good"    # User marked response as good
    FEEDBACK_BAD = "feedback_bad"      # User marked response as bad


# ─── ANALYTICS STORE ──────────────────────────────────────────────────────────

class AnalyticsStore:
    """
    In-memory + disk analytics store.

    Thread-safe. Flushes to JSONL periodically.
    """

    def __init__(self, store_dir: str):
        self.store_dir = store_dir
        self.file_path = os.path.join(store_dir, ANALYTICS_FILE)
        self._events: List[Dict] = []
        self._lock = Lock()
        self._unflushed = 0

        # In-memory counters for fast reporting
        self._question_counter: Counter = Counter()
        self._no_answer_counter: Counter = Counter()
        self._language_counter: Counter = Counter()
        self._daily_counter: Counter = Counter()

        logger.info(f"Analytics store initialized: {self.file_path}")

    def track(self, event_type: str, **kwargs) -> None:
        """
        Record an analytics event.

        Args:
            event_type: Event type string
            **kwargs: Event-specific data (query, language, confidence, etc.)
        """
        event = {
            "type": event_type,
            "ts": time.time(),
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            **kwargs,
        }

        with self._lock:
            self._events.append(event)

            # Update in-memory counters
            if event_type == EventType.QUESTION:
                q = kwargs.get("query", "")[:200]
                lang = kwargs.get("language", "unknown")
                if q:
                    self._question_counter[q] += 1
                if lang:
                    self._language_counter[lang] += 1

            elif event_type == EventType.NO_ANSWER:
                q = kwargs.get("query", "")[:200]
                if q:
                    self._no_answer_counter[q] += 1

            day = event["date"]
            self._daily_counter[day] += 1

            # Keep memory bounded
            if len(self._events) > MAX_IN_MEMORY:
                self._events = self._events[-MAX_IN_MEMORY:]

            self._unflushed += 1

            # Periodic flush to disk
            if self._unflushed >= FLUSH_EVERY:
                self._flush_unsafe()

    def _flush_unsafe(self) -> None:
        """Flush pending events to disk. Must be called with lock held."""
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                for event in self._events[-self._unflushed:]:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._unflushed = 0
        except Exception as e:
            logger.warning(f"Analytics flush failed: {e}")

    def flush(self) -> None:
        """Flush pending events to disk."""
        with self._lock:
            self._flush_unsafe()

    def get_report(self, days: int = 7) -> Dict[str, Any]:
        """
        Generate an analytics report.

        Args:
            days: Look back N days (default 7)

        Returns:
            Report dictionary
        """
        with self._lock:
            total_questions = sum(self._question_counter.values())
            total_no_answers = sum(self._no_answer_counter.values())

            # Most asked questions
            top_questions = [
                {"question": q, "count": c}
                for q, c in self._question_counter.most_common(20)
            ]

            # FAQ gaps (questions that never got answered)
            faq_gaps = [
                {"question": q, "count": c}
                for q, c in self._no_answer_counter.most_common(20)
            ]

            # Language distribution
            lang_dist = dict(self._language_counter.most_common())

            # Daily breakdown (last N days)
            from datetime import datetime, timedelta
            cutoff = datetime.utcnow() - timedelta(days=days)
            daily = {
                date: count
                for date, count in self._daily_counter.items()
                if date >= cutoff.strftime("%Y-%m-%d")
            }

            answer_rate = (
                round((total_questions - total_no_answers) / total_questions * 100, 1)
                if total_questions > 0 else 0.0
            )

            return {
                "summary": {
                    "total_questions": total_questions,
                    "total_no_answers": total_no_answers,
                    "answer_rate_pct": answer_rate,
                    "unique_questions": len(self._question_counter),
                },
                "top_questions": top_questions,
                "faq_gaps": faq_gaps,
                "language_distribution": lang_dist,
                "daily_activity": daily,
                "report_period_days": days,
                "generated_at": datetime.utcnow().isoformat(),
            }

    def get_faq_gaps(self, min_count: int = 1, top_k: int = 50) -> List[Dict]:
        """
        Get questions that were asked but never found a good answer.

        These are your FAQ gaps - questions to add to the FAQ.

        Args:
            min_count: Minimum times asked to be considered a gap
            top_k: Maximum results to return

        Returns:
            List of gap entries sorted by frequency
        """
        with self._lock:
            return [
                {"question": q, "times_asked": c, "priority": "high" if c >= 3 else "medium"}
                for q, c in self._no_answer_counter.most_common(top_k)
                if c >= min_count
            ]


# ─── GLOBAL INSTANCE ──────────────────────────────────────────────────────────

_store: Optional[AnalyticsStore] = None


def init_analytics(store_dir: str) -> AnalyticsStore:
    """Initialize global analytics store."""
    global _store
    _store = AnalyticsStore(store_dir)
    return _store


def get_analytics() -> Optional[AnalyticsStore]:
    return _store


# ─── CONVENIENCE FUNCTIONS ────────────────────────────────────────────────────

def track_question(query: str, language: str = "nl", source: str = "api") -> None:
    """Track a user question."""
    if _store:
        _store.track(EventType.QUESTION, query=query, language=language, source=source)


def track_no_answer(query: str, language: str = "nl") -> None:
    """Track a question that got no good answer (FAQ gap)."""
    if _store:
        _store.track(EventType.NO_ANSWER, query=query, language=language)


def track_low_confidence(query: str, confidence: float, language: str = "nl") -> None:
    """Track a low-confidence answer."""
    if _store:
        _store.track(
            EventType.LOW_CONFIDENCE,
            query=query,
            confidence=round(confidence, 3),
            language=language,
        )


def track_cache_hit(query: str) -> None:
    """Track a cache hit."""
    if _store:
        _store.track(EventType.CACHE_HIT, query=query[:100])


def track_error(query: str, error: str) -> None:
    """Track an error."""
    if _store:
        _store.track(EventType.ERROR, query=query[:100], error=error[:200])


def track_feedback(query: str, good: bool, language: str = "nl") -> None:
    """Track user feedback on a response."""
    if _store:
        event_type = EventType.FEEDBACK_GOOD if good else EventType.FEEDBACK_BAD
        _store.track(event_type, query=query[:200], language=language)


def get_analytics_report(days: int = 7) -> Dict[str, Any]:
    """Get analytics report."""
    if not _store:
        return {"error": "Analytics not initialized"}
    return _store.get_report(days=days)


def get_faq_gaps(min_count: int = 1) -> List[Dict]:
    """Get FAQ gap suggestions."""
    if not _store:
        return []
    return _store.get_faq_gaps(min_count=min_count)
