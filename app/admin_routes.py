# app/admin_routes.py
"""
Admin routes for FAQ management.
Provides CRUD endpoints for managing FAQ entries without manual JSONL editing.
"""
from typing import List, Optional, Dict, Any
import os
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------
admin_router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------
class FAQItemCreate(BaseModel):
    category: str = Field(..., description="FAQ category (e.g., 'Wifipool', 'Algemeen')")
    question: str = Field(..., description="The question text")
    answer: str = Field(..., description="The answer text")
    video_url: Optional[str] = Field(None, description="Optional video URL")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")


class FAQItemUpdate(BaseModel):
    category: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    video_url: Optional[str] = None
    tags: Optional[List[str]] = None


class FAQItemResponse(BaseModel):
    id: str
    category: str
    question: str
    answer: str
    video_url: Optional[str] = None
    tags: List[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------
# File Path Configuration
# ---------------------------------------------------------------------
def get_faq_jsonl_path() -> str:
    """Get the path to the FAQAI.jsonl file."""
    from .config import DATA_DIR
    return os.path.join(DATA_DIR, "all", "faq", "FAQAI.jsonl")


# ---------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------
def load_faq_jsonl() -> List[dict]:
    """
    Load FAQ entries from FAQAI.jsonl file.
    Returns a list of dictionaries.
    """
    faq_path = get_faq_jsonl_path()

    if not os.path.exists(faq_path):
        # Create the directory if it doesn't exist
        os.makedirs(os.path.dirname(faq_path), exist_ok=True)
        return []

    items: List[dict] = []
    try:
        with open(faq_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # Normalize the item
                    normalized = normalize_faq_item(obj)
                    items.append(normalized)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading FAQ file: {str(e)}")

    return items


def normalize_faq_item(item: dict) -> dict:
    """
    Normalize FAQ item to ensure consistent field names.
    Converts Dutch field names to English equivalents and ensures all required fields exist.
    """
    # Extract ID (generate if missing)
    item_id = item.get("id") or item.get("ID") or str(uuid.uuid4())

    # Extract category
    category = (
        item.get("category") or
        item.get("Category") or
        item.get("categorie") or
        item.get("Categorie") or
        ""
    )

    # Extract question
    question = (
        item.get("question") or
        item.get("Question") or
        item.get("vraag") or
        item.get("Vraag") or
        ""
    )

    # Extract answer
    answer = (
        item.get("answer") or
        item.get("Answer") or
        item.get("antwoord") or
        item.get("Antwoord") or
        ""
    )

    # Extract video URL
    video_url = (
        item.get("video_url") or
        item.get("Filmpje") or
        item.get("filmpje") or
        None
    )

    # Extract tags
    tags = item.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    # Extract timestamps
    created_at = item.get("created_at") or item.get("created") or None
    updated_at = item.get("updated_at") or item.get("updated") or None

    # Extract follow_up and options (preserve if they exist)
    follow_up = item.get("follow_up", False)
    options = item.get("options", {})

    return {
        "id": item_id,
        "Categorie": category,
        "Vraag": question,
        "Antwoord": answer,
        "Filmpje": video_url or "",
        "tags": tags,
        "follow_up": follow_up,
        "options": options,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def create_jsonl_entry(
    category: str,
    question: str,
    answer: str,
    video_url: Optional[str] = None,
    tags: Optional[List[str]] = None,
    item_id: Optional[str] = None
) -> dict:
    """
    Create a new JSONL entry with the correct format.
    """
    now = datetime.now().isoformat()

    return {
        "id": item_id or str(uuid.uuid4()),
        "Categorie": category,
        "Vraag": question,
        "Antwoord": answer,
        "Filmpje": video_url or "",
        "tags": tags or [],
        "follow_up": False,
        "options": {},
        "created_at": now,
        "updated_at": now,
    }


def save_faq_jsonl(items: List[dict]) -> None:
    """
    Save FAQ entries to FAQAI.jsonl file.
    """
    faq_path = get_faq_jsonl_path()

    # Ensure directory exists
    os.makedirs(os.path.dirname(faq_path), exist_ok=True)

    try:
        with open(faq_path, "w", encoding="utf-8") as f:
            for item in items:
                # Write each item as a single JSON line
                json_line = json.dumps(item, ensure_ascii=False)
                f.write(json_line + "\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing FAQ file: {str(e)}")


def reload_faq_index() -> tuple:
    """
    Reload the FAQ index in memory by calling _reload_faq from main.py.
    """
    try:
        from .main import _reload_faq
        return _reload_faq()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading FAQ: {str(e)}")


# ---------------------------------------------------------------------
# CRUD Endpoints
# ---------------------------------------------------------------------

@admin_router.get("/faq", response_model=List[FAQItemResponse])
def list_faq_items():
    """
    Get all FAQ items.
    """
    items = load_faq_jsonl()

    # Convert to response format
    response_items = []
    for item in items:
        response_items.append(FAQItemResponse(
            id=item.get("id", ""),
            category=item.get("Categorie", ""),
            question=item.get("Vraag", ""),
            answer=item.get("Antwoord", ""),
            video_url=item.get("Filmpje") or None,
            tags=item.get("tags", []),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
        ))

    return response_items


@admin_router.get("/faq/{item_id}", response_model=FAQItemResponse)
def get_faq_item(item_id: str):
    """
    Get a specific FAQ item by ID.
    """
    items = load_faq_jsonl()

    for item in items:
        if item.get("id") == item_id:
            return FAQItemResponse(
                id=item.get("id", ""),
                category=item.get("Categorie", ""),
                question=item.get("Vraag", ""),
                answer=item.get("Antwoord", ""),
                video_url=item.get("Filmpje") or None,
                tags=item.get("tags", []),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )

    raise HTTPException(status_code=404, detail=f"FAQ item with ID {item_id} not found")


@admin_router.post("/faq")
def create_faq_item(faq: FAQItemCreate):
    """
    Create a new FAQ item.
    """
    items = load_faq_jsonl()

    # Create new entry
    new_item = create_jsonl_entry(
        category=faq.category,
        question=faq.question,
        answer=faq.answer,
        video_url=faq.video_url,
        tags=faq.tags,
    )

    # Add to items
    items.append(new_item)

    # Save to file
    save_faq_jsonl(items)

    # Reload FAQ index
    count, _ = reload_faq_index()

    return {
        "success": True,
        "message": "FAQ item created successfully",
        "id": new_item["id"],
        "faq": FAQItemResponse(
            id=new_item["id"],
            category=new_item["Categorie"],
            question=new_item["Vraag"],
            answer=new_item["Antwoord"],
            video_url=new_item.get("Filmpje") or None,
            tags=new_item.get("tags", []),
            created_at=new_item.get("created_at"),
            updated_at=new_item.get("updated_at"),
        ),
        "total_items": count,
    }


@admin_router.put("/faq/{item_id}")
def update_faq_item(item_id: str, faq: FAQItemUpdate):
    """
    Update an existing FAQ item.
    """
    items = load_faq_jsonl()

    # Find the item to update
    item_found = False
    for item in items:
        if item.get("id") == item_id:
            item_found = True

            # Update fields if provided
            if faq.category is not None:
                item["Categorie"] = faq.category
            if faq.question is not None:
                item["Vraag"] = faq.question
            if faq.answer is not None:
                item["Antwoord"] = faq.answer
            if faq.video_url is not None:
                item["Filmpje"] = faq.video_url
            if faq.tags is not None:
                item["tags"] = faq.tags

            # Update timestamp
            item["updated_at"] = datetime.now().isoformat()

            break

    if not item_found:
        raise HTTPException(status_code=404, detail=f"FAQ item with ID {item_id} not found")

    # Save to file
    save_faq_jsonl(items)

    # Reload FAQ index
    count, _ = reload_faq_index()

    # Get updated item
    updated_item = next((item for item in items if item.get("id") == item_id), None)

    return {
        "success": True,
        "message": "FAQ item updated successfully",
        "id": item_id,
        "faq": FAQItemResponse(
            id=updated_item["id"],
            category=updated_item["Categorie"],
            question=updated_item["Vraag"],
            answer=updated_item["Antwoord"],
            video_url=updated_item.get("Filmpje") or None,
            tags=updated_item.get("tags", []),
            created_at=updated_item.get("created_at"),
            updated_at=updated_item.get("updated_at"),
        ),
        "total_items": count,
    }


@admin_router.delete("/faq/{item_id}")
def delete_faq_item(item_id: str):
    """
    Delete a FAQ item.
    """
    items = load_faq_jsonl()

    # Find and remove the item
    original_count = len(items)
    items = [item for item in items if item.get("id") != item_id]

    if len(items) == original_count:
        raise HTTPException(status_code=404, detail=f"FAQ item with ID {item_id} not found")

    # Save to file
    save_faq_jsonl(items)

    # Reload FAQ index
    count, _ = reload_faq_index()

    return {
        "success": True,
        "message": "FAQ item deleted successfully",
        "id": item_id,
        "total_items": count,
    }


@admin_router.post("/faq/reload")
def force_reload_faq():
    """
    Force reload of the FAQ index.
    """
    count, _ = reload_faq_index()

    return {
        "success": True,
        "message": "FAQ index reloaded successfully",
        "total_items": count,
    }
