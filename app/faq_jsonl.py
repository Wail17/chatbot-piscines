# app/faq_jsonl.py
"""
JSONL-based FAQ knowledge management system.

This module replaces Excel-based FAQ with a pure JSONL knowledge base.
All FAQ entries are stored in faq.jsonl and loaded into memory with embeddings.

Features:
- Fast JSONL loading with caching
- Automatic embedding generation
- FAQ update and persistence
- Memory-efficient storage
- Thread-safe operations
"""

import json
import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from threading import Lock
import time

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

from .config import (
    DATA_DIR,
    STORE_DIR,
    CHROMA_DIR,
    EMBEDDINGS_MODEL,
    COLLECTION_NAME,
)
from .utils import normalize_text, log_error, coerce_string

logger = logging.getLogger(__name__)

# Default FAQ JSONL file path
DEFAULT_FAQ_JSONL = os.path.join(DATA_DIR, "faq.jsonl")

# In-memory FAQ cache
_FAQ_CACHE: List[Dict[str, Any]] = []
_FAQ_CACHE_LOCK = Lock()
_FAQ_CACHE_TIMESTAMP = 0.0

# Embedding cache
_EMBEDDINGS_CACHE: Optional[OpenAIEmbeddings] = None


class FAQJSONLManager:
    """Manager for JSONL-based FAQ knowledge base."""

    def __init__(self, jsonl_path: str = DEFAULT_FAQ_JSONL):
        """
        Initialize FAQ JSONL manager.

        Args:
            jsonl_path: Path to FAQ JSONL file
        """
        self.jsonl_path = jsonl_path
        self.embeddings = None
        self.vectorstore = None
        self.faq_entries: List[Dict[str, Any]] = []

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.jsonl_path), exist_ok=True)

        logger.info(f"FAQ JSONL Manager initialized: {self.jsonl_path}")

    def load_faq(self) -> List[Dict[str, Any]]:
        """
        Load FAQ entries from JSONL file.

        Returns:
            List of FAQ entry dictionaries

        Each entry contains:
        - question: str
        - answer: str
        - metadata: dict (optional)
        """
        if not os.path.exists(self.jsonl_path):
            logger.warning(f"FAQ JSONL file not found: {self.jsonl_path}")
            # Create empty file
            with open(self.jsonl_path, 'w', encoding='utf-8') as f:
                pass
            return []

        entries = []
        line_number = 0

        try:
            with open(self.jsonl_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line_number += 1
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        entry = json.loads(line)

                        # Validate required fields
                        if not isinstance(entry, dict):
                            logger.warning(f"Line {line_number}: Entry is not a dict, skipping")
                            continue

                        question = coerce_string(entry.get("question", ""))
                        answer = coerce_string(entry.get("answer", ""))

                        if not question or not answer:
                            logger.warning(f"Line {line_number}: Missing question or answer, skipping")
                            continue

                        # Normalize entry
                        normalized_entry = {
                            "question": question,
                            "answer": answer,
                            "metadata": entry.get("metadata", {}),
                            "line_number": line_number
                        }

                        # Add optional fields
                        if "category" in entry:
                            normalized_entry["category"] = coerce_string(entry["category"])

                        if "tags" in entry and isinstance(entry["tags"], list):
                            normalized_entry["tags"] = [coerce_string(t) for t in entry["tags"]]

                        entries.append(normalized_entry)

                    except json.JSONDecodeError as e:
                        logger.error(f"Line {line_number}: JSON decode error: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Line {line_number}: Unexpected error: {e}")
                        continue

            self.faq_entries = entries
            logger.info(f"Loaded {len(entries)} FAQ entries from JSONL")

            # Auto-build keyword index with synonym support (no API needed)
            try:
                from .keyword_search import build_keyword_index
                build_keyword_index(entries)
            except Exception:
                pass  # Non-critical

            return entries

        except Exception as e:
            log_error(e, "Failed to load FAQ JSONL", path=self.jsonl_path)
            return []

    def save_faq(self, entries: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Save FAQ entries to JSONL file.

        Args:
            entries: List of FAQ entries (uses self.faq_entries if None)

        Returns:
            True if successful, False otherwise
        """
        if entries is None:
            entries = self.faq_entries

        if not entries:
            logger.warning("No entries to save")
            return False

        try:
            # Write to temporary file first
            temp_path = self.jsonl_path + ".tmp"

            with open(temp_path, 'w', encoding='utf-8') as f:
                for entry in entries:
                    # Remove line_number before saving
                    save_entry = {k: v for k, v in entry.items() if k != "line_number"}
                    f.write(json.dumps(save_entry, ensure_ascii=False) + '\n')

            # Atomic replace
            os.replace(temp_path, self.jsonl_path)

            logger.info(f"Saved {len(entries)} FAQ entries to JSONL")
            return True

        except Exception as e:
            log_error(e, "Failed to save FAQ JSONL", path=self.jsonl_path)
            return False

    def update_faq_entry(self, question: str, new_answer: str, create_if_missing: bool = True) -> bool:
        """
        Update an FAQ entry by question text.

        Args:
            question: Question text to match
            new_answer: New answer text
            create_if_missing: Create new entry if question not found

        Returns:
            True if updated/created, False otherwise
        """
        question_norm = normalize_text(question)

        # Find matching entry
        found_index = None
        for i, entry in enumerate(self.faq_entries):
            entry_q_norm = normalize_text(entry.get("question", ""))
            if entry_q_norm == question_norm:
                found_index = i
                break

        if found_index is not None:
            # Update existing entry
            self.faq_entries[found_index]["answer"] = new_answer
            logger.info(f"Updated FAQ entry: {question[:50]}...")
        elif create_if_missing:
            # Create new entry
            new_entry = {
                "question": question,
                "answer": new_answer,
                "metadata": {}
            }
            self.faq_entries.append(new_entry)
            logger.info(f"Created new FAQ entry: {question[:50]}...")
        else:
            logger.warning(f"FAQ entry not found and create_if_missing=False: {question[:50]}...")
            return False

        # Save to file
        return self.save_faq()

    def delete_faq_entry(self, question: str) -> bool:
        """
        Delete an FAQ entry by question text.

        Args:
            question: Question text to match

        Returns:
            True if deleted, False if not found
        """
        question_norm = normalize_text(question)

        # Find and remove matching entry
        for i, entry in enumerate(self.faq_entries):
            entry_q_norm = normalize_text(entry.get("question", ""))
            if entry_q_norm == question_norm:
                deleted = self.faq_entries.pop(i)
                logger.info(f"Deleted FAQ entry: {question[:50]}...")
                return self.save_faq()

        logger.warning(f"FAQ entry not found for deletion: {question[:50]}...")
        return False

    def find_faq_entry(self, question: str) -> Optional[Dict[str, Any]]:
        """
        Find an FAQ entry by question text.

        Args:
            question: Question text to match

        Returns:
            FAQ entry dict or None if not found
        """
        question_norm = normalize_text(question)

        for entry in self.faq_entries:
            entry_q_norm = normalize_text(entry.get("question", ""))
            if entry_q_norm == question_norm:
                return entry

        return None

    def build_embeddings(self, force_rebuild: bool = False) -> bool:
        """
        Build vector embeddings for all FAQ entries.

        Args:
            force_rebuild: Force rebuild even if embeddings exist

        Returns:
            True if successful, False otherwise
        """
        if not self.faq_entries:
            logger.warning("No FAQ entries to embed")
            return False

        try:
            # Initialize embeddings
            if self.embeddings is None:
                self.embeddings = OpenAIEmbeddings(model=EMBEDDINGS_MODEL)

            # Create documents for embedding
            documents = []
            for entry in self.faq_entries:
                question = entry.get("question", "")
                answer = entry.get("answer", "")
                category = entry.get("category", "")
                tags = entry.get("tags", [])

                # Combine question and answer for better context
                content = f"Question: {question}\nAnswer: {answer}"

                # Build metadata
                metadata = {
                    "question": question,
                    "answer": answer,
                    "title": question[:100],  # For display
                    "source": self.jsonl_path,
                    "source_type": "faq",
                    "category": category,
                }

                if tags:
                    metadata["tags"] = tags

                # Add custom metadata
                if "metadata" in entry and isinstance(entry["metadata"], dict):
                    metadata.update(entry["metadata"])

                doc = Document(page_content=content, metadata=metadata)
                documents.append(doc)

            # Create or update vector store
            if force_rebuild or self.vectorstore is None:
                logger.info(f"Building embeddings for {len(documents)} FAQ entries...")

                # Delete old collection if rebuilding
                if force_rebuild and os.path.exists(CHROMA_DIR):
                    try:
                        # Create new vectorstore (will replace old one)
                        self.vectorstore = Chroma.from_documents(
                            documents=documents,
                            embedding=self.embeddings,
                            collection_name=COLLECTION_NAME,
                            persist_directory=CHROMA_DIR
                        )
                    except Exception as e:
                        logger.warning(f"Error during force rebuild: {e}")
                        # Try without force
                        self.vectorstore = Chroma.from_documents(
                            documents=documents,
                            embedding=self.embeddings,
                            collection_name=COLLECTION_NAME,
                            persist_directory=CHROMA_DIR
                        )
                else:
                    self.vectorstore = Chroma.from_documents(
                        documents=documents,
                        embedding=self.embeddings,
                        collection_name=COLLECTION_NAME,
                        persist_directory=CHROMA_DIR
                    )

                logger.info(f"✅ Successfully built embeddings for {len(documents)} entries")
                return True

            else:
                # Add to existing vectorstore
                self.vectorstore.add_documents(documents)
                logger.info(f"Added {len(documents)} documents to existing vectorstore")
                return True

        except Exception as e:
            log_error(e, "Failed to build embeddings", entry_count=len(self.faq_entries))
            return False

    def get_vectorstore(self):
        """
        Get the Chroma vectorstore instance.

        Returns:
            Chroma vectorstore or None
        """
        if self.vectorstore is None:
            try:
                self.embeddings = OpenAIEmbeddings(model=EMBEDDINGS_MODEL)
                self.vectorstore = Chroma(
                    collection_name=COLLECTION_NAME,
                    persist_directory=CHROMA_DIR,
                    embedding_function=self.embeddings
                )
            except Exception as e:
                log_error(e, "Failed to load vectorstore")
                return None

        return self.vectorstore

    def reload(self, rebuild_embeddings: bool = True) -> int:
        """
        Reload FAQ from file and optionally rebuild embeddings.

        Args:
            rebuild_embeddings: Whether to rebuild embeddings after reload

        Returns:
            Number of FAQ entries loaded
        """
        entries = self.load_faq()

        if entries and rebuild_embeddings:
            self.build_embeddings(force_rebuild=True)

        return len(entries)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get FAQ statistics.

        Returns:
            Dictionary with stats
        """
        return {
            "total_entries": len(self.faq_entries),
            "jsonl_path": self.jsonl_path,
            "file_exists": os.path.exists(self.jsonl_path),
            "vectorstore_ready": self.vectorstore is not None,
            "categories": len(set(e.get("category", "") for e in self.faq_entries if e.get("category"))),
        }


# Global FAQ manager instance
_faq_manager: Optional[FAQJSONLManager] = None
_faq_manager_lock = Lock()


def get_faq_manager(jsonl_path: str = DEFAULT_FAQ_JSONL) -> FAQJSONLManager:
    """
    Get or create the global FAQ manager instance.

    Args:
        jsonl_path: Path to FAQ JSONL file

    Returns:
        FAQJSONLManager instance
    """
    global _faq_manager

    with _faq_manager_lock:
        if _faq_manager is None:
            _faq_manager = FAQJSONLManager(jsonl_path)
            _faq_manager.load_faq()

        return _faq_manager


def load_faq_jsonl(jsonl_path: str = DEFAULT_FAQ_JSONL, force_reload: bool = False) -> List[Dict[str, Any]]:
    """
    Load FAQ entries from JSONL file (cached).

    Args:
        jsonl_path: Path to FAQ JSONL file
        force_reload: Force reload from disk

    Returns:
        List of FAQ entries
    """
    global _FAQ_CACHE, _FAQ_CACHE_TIMESTAMP

    # Check cache
    if not force_reload and _FAQ_CACHE:
        file_mtime = os.path.getmtime(jsonl_path) if os.path.exists(jsonl_path) else 0
        if file_mtime <= _FAQ_CACHE_TIMESTAMP:
            logger.debug("Using cached FAQ entries")
            return _FAQ_CACHE

    # Load from file
    with _FAQ_CACHE_LOCK:
        manager = get_faq_manager(jsonl_path)
        entries = manager.load_faq()

        _FAQ_CACHE = entries
        _FAQ_CACHE_TIMESTAMP = time.time()

        return entries


def update_faq_jsonl(question: str, new_answer: str, jsonl_path: str = DEFAULT_FAQ_JSONL) -> bool:
    """
    Update FAQ entry and rebuild embeddings.

    Args:
        question: Question to update
        new_answer: New answer
        jsonl_path: Path to FAQ JSONL file

    Returns:
        True if successful
    """
    manager = get_faq_manager(jsonl_path)

    # Update entry
    success = manager.update_faq_entry(question, new_answer)

    if success:
        # Rebuild embeddings
        manager.build_embeddings(force_rebuild=True)

        # Clear cache
        global _FAQ_CACHE, _FAQ_CACHE_TIMESTAMP
        with _FAQ_CACHE_LOCK:
            _FAQ_CACHE = []
            _FAQ_CACHE_TIMESTAMP = 0.0

    return success
