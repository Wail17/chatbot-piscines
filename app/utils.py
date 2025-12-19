# app/utils.py
"""Utility functions for text normalization, language handling, and common operations."""

import re
import unicodedata
import logging
from typing import Optional, Set
from functools import lru_cache

logger = logging.getLogger(__name__)

# Text normalization patterns
_PUNCT_RE = re.compile(r"\s*[?!.:,;()\-\[\]«»""\"'`]\s*")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """
    Normalize text for better matching and comparison.

    - Removes accents
    - Converts to lowercase
    - Removes punctuation
    - Normalizes whitespace
    - Removes non-breaking spaces

    Args:
        text: Input text to normalize

    Returns:
        Normalized text string
    """
    if not text:
        return ""

    # Normalize unicode (remove accents)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))

    # Replace non-breaking spaces
    text = text.replace("\u00a0", " ")

    # Lowercase
    text = text.lower()

    # Remove punctuation
    text = _PUNCT_RE.sub(" ", text)

    # Normalize whitespace
    text = _WHITESPACE_RE.sub(" ", text)

    return text.strip()


@lru_cache(maxsize=128)
def normalize_language_code(code: str | None) -> str:
    """
    Normalize a language code to ISO 639-1 format (2 letters).

    Args:
        code: Language code (e.g., "en", "EN", "eng", "en-US")

    Returns:
        Normalized 2-letter lowercase language code
    """
    if not code:
        return ""

    code = code.strip().lower()
    if not code:
        return ""

    # Extract first 2 characters for ISO 639-1
    return code[:2]


def extract_tokens(text: str) -> Set[str]:
    """
    Extract unique tokens from normalized text.

    Args:
        text: Input text

    Returns:
        Set of unique tokens
    """
    if not text:
        return set()

    normalized = normalize_text(text)
    return {tok for tok in normalized.split() if tok and len(tok) > 1}


def text_contains(haystack: str, needle: str) -> bool:
    """
    Check if needle text is contained in haystack using normalized comparison.

    Args:
        haystack: Text to search in
        needle: Text to search for

    Returns:
        True if needle is found in haystack
    """
    if not haystack or not needle:
        return False

    haystack_norm = normalize_text(haystack)
    needle_norm = normalize_text(needle)

    # Direct substring match
    if needle_norm in haystack_norm or haystack_norm in needle_norm:
        return True

    # Token-based matching
    hay_tokens = extract_tokens(haystack)
    needle_tokens = extract_tokens(needle)

    if not hay_tokens or not needle_tokens:
        return False

    # Check if all needle tokens are in haystack
    return needle_tokens.issubset(hay_tokens)


def safe_get_nested(obj: dict, *keys, default=None):
    """
    Safely get a nested dictionary value.

    Args:
        obj: Dictionary to traverse
        *keys: Sequence of keys to navigate
        default: Default value if key path doesn't exist

    Returns:
        Value at the key path or default
    """
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def coerce_string(value) -> str:
    """
    Safely convert any value to a string.

    Args:
        value: Any value

    Returns:
        String representation, empty string if None
    """
    if value is None:
        return ""
    return str(value).strip()


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate text to a maximum length.

    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add when truncating

    Returns:
        Truncated text
    """
    if not text or len(text) <= max_length:
        return text

    return text[:max_length - len(suffix)] + suffix


class RateLimiter:
    """Simple in-memory rate limiter for API calls."""

    def __init__(self, max_calls: int = 100, window_seconds: int = 60):
        """
        Initialize rate limiter.

        Args:
            max_calls: Maximum number of calls allowed in the window
            window_seconds: Time window in seconds
        """
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls = []

    def is_allowed(self) -> bool:
        """
        Check if a new call is allowed within the rate limit.

        Returns:
            True if call is allowed, False otherwise
        """
        import time
        now = time.time()

        # Remove old calls outside the window
        self.calls = [t for t in self.calls if now - t < self.window_seconds]

        # Check if we're under the limit
        if len(self.calls) < self.max_calls:
            self.calls.append(now)
            return True

        return False


def log_error(error: Exception, context: str = "", **kwargs):
    """
    Log an error with context and additional information.

    Args:
        error: Exception object
        context: Context description
        **kwargs: Additional key-value pairs to log
    """
    error_msg = f"{context}: {type(error).__name__}: {str(error)}"

    if kwargs:
        extra_info = ", ".join(f"{k}={v}" for k, v in kwargs.items())
        error_msg += f" [{extra_info}]"

    logger.error(error_msg, exc_info=False)


def validate_json_response(data: dict, required_fields: list) -> bool:
    """
    Validate that a JSON response contains all required fields.

    Args:
        data: Response dictionary
        required_fields: List of required field names

    Returns:
        True if all required fields are present
    """
    if not isinstance(data, dict):
        return False

    return all(field in data for field in required_fields)
