# app/admin_routes.py
"""
Admin routes for FAQ management
"""

from typing import List, Optional, Dict, Any
import os
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ==================== MODELS ====================
class FAQItemCreate(BaseModel):
    category: str
    question: str
    answer: str
    video_url: Optional[str] = None
    tags: List[str] = []

class FAQItemUpdate(BaseModel):
    category: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    video_url: Optional[str] = None
    tags: Optional[List[str]] = None

# ==================== ROUTER ====================
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# Path to JSONL file
FAQ_FILE = os.path.join(os.path.dirname(__file__), "../data/all/faq/FAQAI.jsonl")

# ==================== HELPERS ====================
def load_faq_jsonl() -> List[Dict[str, Any]]:
    """Load all entries from JSONL file"""
    if not os.path.exists(FAQ_FILE):
        return []

    items = []
    with open(FAQ_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                # Add ID if missing
                if 'id' not in item:
                    item['id'] = str(uuid.uuid4())
                items.append(item)
            except json.JSONDecodeError:
                continue

    return items

def save_faq_jsonl(items: List[Dict[str, Any]]) -> None:
    """Save all entries to JSONL file"""
    os.makedirs(os.path.dirname(FAQ_FILE), exist_ok=True)

    with open(FAQ_FILE, 'w', encoding='utf-8') as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

def normalize_faq_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize FAQ item for display"""
    return {
        'id': item.get('id', str(uuid.uuid4())),
        'category': item.get('Categorie') or item.get('category', ''),
        'question': item.get('Vraag') or item.get('question', ''),
        'answer': item.get('Antwoord') or item.get('answer', ''),
        'video_url': item.get('Filmpje') or item.get('video_url') or None,
        'tags': item.get('tags', []),
        'created_at': item.get('created_at', datetime.now().isoformat())
    }

def create_jsonl_entry(data: FAQItemCreate, item_id: str) -> Dict[str, Any]:
    """Create JSONL entry in expected format"""
    return {
        'id': item_id,
        'Categorie': data.category,
        'Vraag': data.question,
        'Antwoord': data.answer,
        'Filmpje': data.video_url or '',
        'tags': data.tags,
        'follow_up': False,
        'options': {},
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }

# ==================== ROUTES ====================

@admin_router.get("/faq")
async def get_all_faq():
    """Get all FAQ items"""
    try:
        items = load_faq_jsonl()
        normalized = [normalize_faq_item(item) for item in items]

        return {
            "success": True,
            "count": len(normalized),
            "faq": normalized
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading FAQ: {str(e)}")

@admin_router.get("/faq/{faq_id}")
async def get_faq_by_id(faq_id: str):
    """Get specific FAQ item"""
    try:
        items = load_faq_jsonl()
        item = next((i for i in items if i.get('id') == faq_id), None)

        if not item:
            raise HTTPException(status_code=404, detail="FAQ not found")

        return {
            "success": True,
            "faq": normalize_faq_item(item)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading FAQ: {str(e)}")

@admin_router.post("/faq")
async def create_faq(data: FAQItemCreate):
    """Create new FAQ item"""
    try:
        items = load_faq_jsonl()

        # Generate new ID
        new_id = str(uuid.uuid4())

        # Create entry
        new_item = create_jsonl_entry(data, new_id)

        # Add to list
        items.append(new_item)

        # Save
        save_faq_jsonl(items)

        # Reload FAQ index in memory
        from .main import _reload_faq
        _reload_faq()

        return {
            "success": True,
            "message": "FAQ created successfully",
            "id": new_id,
            "faq": normalize_faq_item(new_item)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating FAQ: {str(e)}")

@admin_router.put("/faq/{faq_id}")
async def update_faq(faq_id: str, data: FAQItemUpdate):
    """Update existing FAQ item"""
    try:
        items = load_faq_jsonl()

        # Find item
        item_index = next((i for i, item in enumerate(items) if item.get('id') == faq_id), None)

        if item_index is None:
            raise HTTPException(status_code=404, detail="FAQ not found")

        # Update fields
        item = items[item_index]

        if data.category is not None:
            item['Categorie'] = data.category
        if data.question is not None:
            item['Vraag'] = data.question
        if data.answer is not None:
            item['Antwoord'] = data.answer
        if data.video_url is not None:
            item['Filmpje'] = data.video_url
        if data.tags is not None:
            item['tags'] = data.tags

        item['updated_at'] = datetime.now().isoformat()

        # Save
        save_faq_jsonl(items)

        # Reload FAQ index
        from .main import _reload_faq
        _reload_faq()

        return {
            "success": True,
            "message": "FAQ updated successfully",
            "faq": normalize_faq_item(item)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating FAQ: {str(e)}")

@admin_router.delete("/faq/{faq_id}")
async def delete_faq(faq_id: str):
    """Delete FAQ item"""
    try:
        items = load_faq_jsonl()

        # Filter out item to delete
        original_count = len(items)
        items = [item for item in items if item.get('id') != faq_id]

        if len(items) == original_count:
            raise HTTPException(status_code=404, detail="FAQ not found")

        # Save
        save_faq_jsonl(items)

        # Reload FAQ index
        from .main import _reload_faq
        _reload_faq()

        return {
            "success": True,
            "message": "FAQ deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting FAQ: {str(e)}")

@admin_router.post("/faq/reload")
async def reload_faq_index():
    """Force reload of FAQ index in memory"""
    try:
        from .main import _reload_faq
        count, _ = _reload_faq()

        return {
            "success": True,
            "message": "FAQ index reloaded",
            "count": count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading FAQ: {str(e)}")
