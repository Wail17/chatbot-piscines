# API reference

> Complete endpoint reference for the Wifipool AI Assistant. All examples use `curl`; replace `https://chatbot-piscines.onrender.com` with `http://localhost:8000` for local development.

The interactive Swagger UI is available at **[/docs](https://chatbot-piscines.onrender.com/docs)** once the server is running. The OpenAPI 3.0 specification is at **[/openapi.json](https://chatbot-piscines.onrender.com/openapi.json)**.

---

## Authentication

| Surface | Auth |
|---|---|
| `POST /chat`, `GET /`, `GET /dashboard.html`, `GET /faq_images/*` | None (public) |
| `POST /admin/login` | None (issues a token) |
| Everything else under `/admin/*`, `/analytics`, `/faq/gaps` | `X-Admin-Token: <password>` header |

Failed admin auth returns:
```
HTTP 401 Unauthorized
{ "detail": "Invalid admin token" }
```

---

## 1. Public — chat & content

### `POST /chat`
Answer a customer question.

**Request:**
```json
{
  "query": "How do I adjust the pH of my pool?",
  "language": "en",
  "extra": {}
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | ✅ | The user's question |
| `language` | `"nl"`\|`"en"`\|`"fr"`\|`"de"` | optional | Force a language; otherwise auto-detected |
| `extra.clarify_ref` | string | optional | Reference to a previous question (for follow-ups) |
| `top_k` | int | optional | Override retrieval depth |

**Response (200):**
```json
{
  "answer": "To adjust the pH of your pool, use the dosing settings...",
  "citations": [
    { "id": "faq:11", "title": "How can I manually start a device?", "source": "faq" }
  ],
  "source": "expert",
  "confidence": 0.92,
  "image_url": "/faq_images/row_11.png",
  "image_urls": ["/faq_images/row_11.png", "/faq_images/row_11_2.png"],
  "video_url": "https://youtube.com/watch?v=...",
  "choices": ["Gen 1", "Gen 2"]
}
```

| Field | When present | Meaning |
|---|---|---|
| `answer` | always | Natural-language response in the resolved language |
| `source` | always | `"expert"` (Claude), `"faq"` (RAG), `"expert_out_of_scope"`, `"api_error"` |
| `image_urls` | when row has images | Carousel-ready URL list |
| `image_url` | when row has images | Compat alias = first of `image_urls` |
| `video_url` | when row has video | YouTube or external link |
| `choices` | when answer needs clarification | Inline button labels (e.g. `["Gen 1", "Gen 2"]`) |
| `suggestions` | sometimes | Related questions the user might also ask |

**Example:**
```bash
curl -X POST https://chatbot-piscines.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Comment regler le pH ?", "language": "fr"}'
```

**Error responses:**
- `429 Too Many Requests` — exceeded 60 requests/min/IP
- `500 Internal Server Error` — fallback when all answer paths fail

---

### `GET /health`
Lightweight health probe used by Render.

```bash
curl https://chatbot-piscines.onrender.com/health
```

**Response (200):**
```json
{
  "status": "ok",
  "faq_rows": 335,
  "faq_file_exists": true,
  "features": {
    "anthropic_ready": true,
    "anthropic_key_present": true,
    "expert_model": "claude-haiku-4-5-20251001"
  }
}
```

---

### `GET /faq_images/<filename>`
Static mount serving extracted FAQ images. URL pattern: `/faq_images/row_<excel_row>.png` (or `row_<excel_row>_<i>.png` for multi-image rows).

---

## 2. Admin — authentication

### `POST /admin/login`
Validate password → return token.

**Request:**
```json
{ "password": "your-admin-password" }
```

**Response (200):**
```json
{ "ok": true, "token": "your-admin-password" }
```

**Response (401):**
```json
{ "detail": "Invalid password" }
```

> The token IS the password (no per-session rotation — single shared admin). Frontend stores it in `sessionStorage` and replays it in `X-Admin-Token` on every subsequent admin request.

---

## 3. Admin — knowledge base (CRUD)

All routes require `X-Admin-Token` header.

### `GET /admin/faq`
List all FAQ entries (normalized: only NL + metadata).

```bash
curl https://chatbot-piscines.onrender.com/admin/faq \
  -H "X-Admin-Token: admin"
```

**Response:**
```json
{ "success": true, "count": 335, "faq": [{ "id": "...", "category": "...", "question": "...", "answer": "..." }] }
```

### `GET /admin/faq/{faq_id}`
Get a single FAQ entry by ID.

### `POST /admin/faq`
Create a new FAQ entry (manual fields).

**Request:**
```json
{
  "category": "Wifipool Algemeen",
  "question": "How do I…?",
  "answer": "To do this…",
  "video_url": "https://youtube.com/...",
  "tags": ["Gen2", "Wifipool"]
}
```

### `PUT /admin/faq/{faq_id}`
Update an existing FAQ entry. Partial updates supported (only send the fields you change).

### `DELETE /admin/faq/{faq_id}`
Delete a FAQ entry. Triggers in-memory re-index.

### `POST /admin/faq/reload`
Force a re-index from the current JSONL file.

**Response:**
```json
{ "success": true, "message": "FAQ index reloaded", "count": 335 }
```

---

## 4. Admin — Excel round-trip

### `GET /admin/excel/download`
Download the master `AI 2.0.xlsx` file.

```bash
curl -OJ https://chatbot-piscines.onrender.com/admin/excel/download \
  -H "X-Admin-Token: admin"
```

**Response:** binary `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` with `Content-Disposition: attachment; filename="AI 2.0.xlsx"`.

### `POST /admin/excel/upload`
Replace the master Excel with an edited version. Last 3 versions are backed up under `excel_backups/`.

```bash
curl -X POST https://chatbot-piscines.onrender.com/admin/excel/upload \
  -H "X-Admin-Token: admin" \
  -F "file=@edited.xlsx"
```

**Response:**
```json
{ "ok": true, "entries": 335, "images": 115, "faq_loaded": 335 }
```

### `POST /admin/faq/quick-add`
Single-form Q&A creation (multipart/form-data). The fastest way for the owner to add a question without touching Excel.

```bash
curl -X POST https://chatbot-piscines.onrender.com/admin/faq/quick-add \
  -H "X-Admin-Token: admin" \
  -F "question=How do I reset my Gen 3 device?" \
  -F "answer=Press and hold the button for 10 seconds." \
  -F "video_url=https://youtube.com/..." \
  -F "image=@photo.jpg"
```

**Response:**
```json
{ "ok": true, "faq_loaded": 336, "image": "/faq_images/manual_1715800000.png" }
```

---

## 5. Admin — analytics

### `GET /analytics?days=N`
Aggregated metrics over the last N days (default 30).

```bash
curl "https://chatbot-piscines.onrender.com/analytics?days=7" \
  -H "X-Admin-Token: admin"
```

**Response:**
```json
{
  "summary": {
    "total_questions": 142,
    "total_no_answers": 8,
    "answer_rate_pct": 94.4,
    "unique_questions": 89
  },
  "top_questions": [
    { "question": "How do I reset…?", "count": 12 }
  ],
  "language_distribution": { "nl": 80, "en": 32, "fr": 20, "de": 10 },
  "daily_activity": [
    { "date": "2026-05-09", "count": 18 }
  ],
  "faq_gaps": [
    { "question": "How do I clean the salt cell?", "count": 3, "priority": "HIGH" }
  ]
}
```

### `GET /faq/gaps`
Just the unanswered-questions list (used by the dashboard's "Unanswered" tab).

---

## 6. Status codes

| Code | When |
|---|---|
| **200** | OK |
| **400** | Malformed request body (missing required fields, invalid JSON) |
| **401** | Missing or wrong `X-Admin-Token` |
| **404** | FAQ ID not found |
| **413** | File upload too large |
| **429** | Rate limit (60 req/min/IP on `/chat`) |
| **500** | Internal error (logged on server) |

---

## 7. Rate limits

- **`POST /chat`** — 60 requests / 60 seconds / source IP.
- **`/admin/*`** — no application-level limit (admin is trusted), but Render's platform limits still apply.

Exceeding the chat limit returns:
```
HTTP 429
{ "detail": "Te veel verzoeken. Wacht even en probeer opnieuw." }
```

---

## 8. Versioning

The API is currently **unversioned** (single production deployment, one client). Breaking changes are communicated via commit messages on `main`. Once external integrators exist, the API will be prefixed with `/v1/` and a deprecation policy added.

---

*See [docs/ARCHITECTURE.md](ARCHITECTURE.md) for system-level design and [docs/SECURITY.md](SECURITY.md) for auth + threat-model details.*
