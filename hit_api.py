# hit_api.py
import httpx

INGEST_PAYLOAD = {
    "path": r"C:\Users\wailk\OneDrive\Documents\chatbot-piscines\app\data\all",
    "source_type": "mixed"
}
CHAT_PAYLOAD = {
    "query": "Quelle est la garantie de la piscine X ?",
    "audience": "client"
}

print("→ POST /ingest ...")
r1 = httpx.post("http://127.0.0.1:8000/ingest", json=INGEST_PAYLOAD, timeout=60.0)
print(r1.status_code, r1.json())

print("→ POST /chat ...")
r2 = httpx.post("http://127.0.0.1:8000/chat", json=CHAT_PAYLOAD, timeout=60.0)
print(r2.status_code, r2.json())
