# How we work — Wifipool Chatbot

A practical guide for the team. Three sections:

1. **How we work** — day-to-day responsibilities and rhythm
2. **How we do it** — concrete workflows for the most common tasks
3. **How we set it up** — what's behind the scenes (infra, repo, deploy)

> The chatbot is built around one master file (`AI 2.0.xlsx`) which holds every
> question, every translation, and every embedded photo. Everything else
> (JSONL, embeddings, dashboard, chat UI) is regenerated from that file.

---

## 1. How we work

### Roles

| Role            | Owns                                                        |
|-----------------|-------------------------------------------------------------|
| Content owner   | Writes new FAQ entries, decides categories, validates tone  |
| Technical lead  | Deploys, monitors errors, manages dependencies              |
| Support / first-line | Watches "Unanswered questions" daily, flags gaps       |

### Cadence

- **Daily** — quick glance at *Unanswered questions* in the dashboard. If a real
  question keeps showing up, add it via *Quick Add*.
- **Weekly** — review *Top questions* and *Language usage*. Look for missing
  translations or unclear answers.
- **Monthly** — bulk-edit Excel: clean phrasing, fix typos, add product links,
  attach images. Upload via the dashboard. The chatbot reloads automatically.
- **Quarterly** — review categories (split / merge / rename), revisit synonyms.

### Don'ts

- Don't edit the JSONL (`app/data/all/faq/FAQAI.jsonl`) by hand. It's regenerated
  from `AI 2.0.xlsx` on every reload.
- Don't commit `AI 2.0.xlsx` to a feature branch — it's the source of truth and
  belongs on `main`.
- Don't deploy a new model version on a Friday afternoon.

---

## 2. How we do it

### Add a new Q&A

**Option A — Quick add (1 to 5 questions)**

1. Open the dashboard → *Quick Add*.
2. Enter the question (NL is fine — it propagates to the other languages).
3. Enter the answer.
4. Optionally drop one or more images, paste YouTube links (one per line),
   add product links (`Title | URL` per line).
5. Save. The chatbot answers it within ~2 seconds.

**Option B — Bulk edit via Excel**

1. Dashboard → *Download Excel*.
2. Edit `AI 2.0.xlsx` locally. Keep the *Alle vragen* sheet structure intact.
3. Upload via *Upload Excel*. Backup is created automatically (last 3 are kept
   in `excel_backups/`).
4. Watch for the success toast: it shows how many entries / images were loaded.

**Option C — Browse per category**

1. Dashboard → *FAQ per categorie*.
2. Click a tab. Search inside the category. Edit via the row icons.
3. Or download a *FAQ per categorie.xlsx* for an at-a-glance categorized view.

### Add multiple photos to one answer

Embed several images in the same row inside `AI 2.0.xlsx` (anchor them to the
*Foto* column or anywhere on that row). All of them are extracted on reload and
shown side by side in the chat.

Via *Quick Add*: pick multiple files in the file picker — they all get attached
to the new entry.

### Add multiple videos

In the *Filmpje* column, put one URL per line (or separated by `;`). Each URL is
detected, the chat shows YouTube thumbnails for each one.

### Add product / webshop links

Add a column named **Producten** (or *Verkooplinks* / *ProductLinks* / *Webshop*)
to the *Alle vragen* sheet. One entry per line in that cell:

```
Wifipool Gen 2 | https://beniferro.eu/product/wifipool-gen2
Doseerpomp     | https://beniferro.eu/product/doseerpomp
```

Markdown links also work: `[Title](https://...)`. Plain URLs without title get
a slug-based label.

### Promote / fix a category

Categories are detected from the *Categorie* column in `AI 2.0.xlsx`. To rename
a category, find/replace in Excel and re-upload. Existing tabs in the dashboard
update automatically.

### Investigate an unanswered question

1. Dashboard → *Unanswered questions*.
2. Click an entry → it copies the question to the clipboard.
3. Paste it into *Quick Add* and write the answer.
4. Save. The next person who asks gets the new answer.

### Roll back a bad upload

The last 3 Excel uploads are kept in `excel_backups/AI 2.0.<timestamp>.xlsx`.
Copy the right one back to `AI 2.0.xlsx` (or upload it via the dashboard) and
the chatbot reloads.

---

## 3. How we set it up

### Repo layout

```
chatbot-piscines/
├── app/                        # FastAPI backend
│   ├── main.py                 # API: /chat, /admin/*, FAQ in-memory index
│   ├── admin_routes.py         # CRUD admin endpoints (/admin/faq)
│   ├── excel_loader.py         # AI 2.0.xlsx → JSONL + extracted images
│   ├── rag.py                  # Embeddings + answer generation
│   ├── data/
│   │   ├── all/faq/FAQAI.jsonl # Generated knowledge base
│   │   └── faq_images/         # Extracted images, served at /faq_images
│   └── …
├── AI 2.0.xlsx                 # Source of truth — DO NOT EDIT BY HAND
├── index.html                  # Public chatbot UI
├── dashboard.html              # Admin dashboard (auth-gated)
├── requirements.txt
├── render.yaml / Procfile      # Deploy config
└── excel_backups/              # Auto-rotated backups (last 3)
```

### Local development

```bash
# 1. Python 3.11+
python -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Set the API keys (see GET-API-KEY.md)
export ANTHROPIC_API_KEY=...        # Linux / macOS
# setx ANTHROPIC_API_KEY ...        # Windows
export OPENAI_API_KEY=...
export ADMIN_PASSWORD=...           # for the dashboard

# 3. Run
uvicorn app.main:app --reload --port 8000

# 4. Open http://localhost:8000  (chat) and /dashboard (admin)
```

### Reload from Excel after a manual change

```bash
python -c "from app.excel_loader import reload_from_excel; print(reload_from_excel())"
```

(Or upload via the dashboard, which calls the same code.)

### Deployment (Render)

The repo includes `render.yaml` and `Procfile`. On push to `main`:

1. Render rebuilds the container from `requirements.txt`.
2. The previous deployment stays live until the new one passes health check.
3. `AI 2.0.xlsx` is shipped with the code — but operations should prefer
   updating it via the dashboard so backups are written.

Health checks: `/health` returns `{"status": "ok"}`.

### Authentication

- The dashboard sits behind `/admin/login`. The password lives in the
  `ADMIN_PASSWORD` env var.
- The chat (`/chat`) is rate-limited per IP (60 req/min) and CORS-restricted to
  the configured origins (`beniferro.eu`, the Render URL, `localhost`).

### What gets cached

- **Answer cache** — popular questions skip the LLM round-trip entirely.
  Cleared on every Excel reload.
- **Embeddings** — recomputed lazily on first request after a reload. First
  call after an upload is slower; subsequent calls are instant.

### Monitoring

- Top questions, language split, and gap list are visible in the dashboard
  *Overview* and *Unanswered questions* sections.
- Backend logs (Render dashboard) show every request, the matched FAQ row,
  and any error from the LLM.

### Adding a new language

Add four columns to `AI 2.0.xlsx`: e.g. `ES Question`, `ES Answer`. Update
`app/main.py` `_lang_field()` mapping and the language detector. The frontend
already sends `language` on every `/chat` call.

---

## Cheat sheet

| Need to…                              | Where                                |
|---------------------------------------|--------------------------------------|
| Add 1–5 questions                     | Dashboard → Quick Add                |
| Bulk edit all questions               | Dashboard → Download/Upload Excel    |
| Browse by topic                       | Dashboard → FAQ per categorie        |
| See what users are stuck on           | Dashboard → Unanswered questions     |
| Roll back yesterday's mistake         | `excel_backups/` → re-upload         |
| Force a knowledge reload              | Dashboard refresh, or `POST /admin/excel/upload` |
| Restart the server                    | Render dashboard → manual deploy     |

When in doubt: the master Excel is the truth. Reload it, and everything else
falls in line.
