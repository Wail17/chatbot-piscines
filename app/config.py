# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# --- Nom de la collection (doit être identique pour ingest ET lecture) ---
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "piscines")

# --- OpenAI / modèles ---
# IMPORTANT: l'environnement doit contenir OPENAI_API_KEY (pas OPENAI_xAPI_KEY)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
EMBEDDINGS_MODEL = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-large")

# --- Dossiers ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "store")
CHROMA_DIR = os.path.join(STORE_DIR, "chroma")
RAW_DOCS_DIR = os.path.join(STORE_DIR, "raw_docs")
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- RAG ---
MAX_CHUNK_TOKENS = int(os.getenv("MAX_CHUNK_TOKENS", "500"))
CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", "80"))
<<<<<<< HEAD
TOP_K = int(os.getenv("TOP_K", "8"))
=======
TOP_K = int(os.getenv("TOP_K", "5"))
>>>>>>> 9ac38f6 (push from clean folder (no OneDrive, remove NUL))
RESPONSE_LANGUAGE = os.getenv("RESPONSE_LANGUAGE", "auto")

# --- Feedback / corrections ---
FEEDBACK_FILE = os.getenv(
    "FEEDBACK_FILE",
    os.path.join(STORE_DIR, "feedback", "feedback.jsonl")
)
CORRECTIONS_COLLECTION = os.getenv("CORRECTIONS_COLLECTION", "corrections")
CORRECTION_THRESHOLD = float(os.getenv("CORRECTION_THRESHOLD", "0.20"))

# Crée les dossiers nécessaires
os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(RAW_DOCS_DIR, exist_ok=True)
