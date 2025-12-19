# app/config.py
import os
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Logging Configuration ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# --- Collection Name ---
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "piscines")

# --- OpenAI Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
EMBEDDINGS_MODEL = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-large")

# Validate API key presence
if not OPENAI_API_KEY:
    logging.warning("⚠️  OPENAI_API_KEY not set - AI features will be limited")

# --- Directory Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "store")
CHROMA_DIR = os.getenv("CHROMA_DIR", os.path.join(STORE_DIR, "chroma"))
RAW_DOCS_DIR = os.path.join(STORE_DIR, "raw_docs")
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- RAG Configuration ---
MAX_CHUNK_TOKENS = int(os.getenv("MAX_CHUNK_TOKENS", "500"))
CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", "80"))
TOP_K = int(os.getenv("TOP_K", "8"))
RESPONSE_LANGUAGE = os.getenv("RESPONSE_LANGUAGE", "auto")

# --- Caching Configuration ---
ENABLE_TRANSLATION_CACHE = os.getenv("ENABLE_TRANSLATION_CACHE", "true").lower() == "true"
ENABLE_EMBEDDING_CACHE = os.getenv("ENABLE_EMBEDDING_CACHE", "true").lower() == "true"
TRANSLATION_CACHE_SIZE = int(os.getenv("TRANSLATION_CACHE_SIZE", "512"))
LANGUAGE_DETECTION_CACHE_SIZE = int(os.getenv("LANGUAGE_DETECTION_CACHE_SIZE", "256"))

# --- Feedback & Corrections ---
FEEDBACK_FILE = os.getenv("FEEDBACK_FILE", os.path.join(STORE_DIR, "feedback", "feedback.jsonl"))
CORRECTIONS_COLLECTION = os.getenv("CORRECTIONS_COLLECTION", "corrections")
CORRECTION_THRESHOLD = float(os.getenv("CORRECTION_THRESHOLD", "0.20"))

# --- Language Support ---
SUPPORTED_LANGUAGES = {
    "nl": "Dutch",
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
    "ro": "Romanian",
    "da": "Danish",
    "sv": "Swedish",
    "fi": "Finnish",
    "cs": "Czech",
    "sk": "Slovak",
    "hu": "Hungarian",
    "tr": "Turkish",
    "el": "Greek",
    "et": "Estonian",
    "lv": "Latvian",
    "lt": "Lithuanian",
    "sl": "Slovenian",
}

DEFAULT_LANGUAGE = "nl"

# --- Suggestion Configuration ---
DEFAULT_SUGGESTIONS_COUNT = 4
MIN_SUGGESTIONS_COUNT = 3
MAX_SUGGESTIONS_COUNT = 6
MIN_SIMILARITY_THRESHOLD = 0.3
HIGH_CONFIDENCE_THRESHOLD = 0.85

# Create necessary directories
os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(RAW_DOCS_DIR, exist_ok=True)

logging.info(f"✅ Configuration loaded - Language support: {len(SUPPORTED_LANGUAGES)} languages")
