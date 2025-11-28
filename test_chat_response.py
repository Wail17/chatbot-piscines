#!/usr/bin/env python3
"""Test script to debug the chatbot response format"""
import json
import sys
import os

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

from app.main import chat, ChatRequest
from unittest.mock import MagicMock

# Create a fake request object
fake_request = MagicMock()
fake_request.client.host = "127.0.0.1"

# Test with different top_k values
print("=" * 80)
print("TEST 1: Default top_k=4 (current behavior)")
print("=" * 80)
req1 = ChatRequest(query="Hoe reset ik mijn wifipool?", top_k=4)
try:
    response1 = chat(req1, fake_request)
    print(json.dumps(response1, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("TEST 2: top_k=1 (single answer mode)")
print("=" * 80)
req2 = ChatRequest(query="Hoe reset ik mijn wifipool?", top_k=1)
try:
    response2 = chat(req2, fake_request)
    print(json.dumps(response2, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("TEST 3: Another question with top_k=4")
print("=" * 80)
req3 = ChatRequest(query="Waar vind ik het serienummer?", top_k=4)
try:
    response3 = chat(req3, fake_request)
    print(json.dumps(response3, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
