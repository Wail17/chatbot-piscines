# Admin FAQ Management System

Ce document décrit le nouveau système d'administration pour gérer le FAQ sans modifier manuellement le fichier JSONL.

## Endpoints disponibles

### 1. Liste toutes les questions FAQ
```bash
GET /admin/faq
```

**Réponse:**
```json
[
  {
    "id": "uuid-1234",
    "category": "Wifipool",
    "question": "Hoe reset ik mijn wifipool?",
    "answer": "Om je wifipool te resetten, volg deze stappen...",
    "video_url": "https://youtube.com/watch?v=abc",
    "tags": ["reset", "wifipool"],
    "created_at": "2025-01-15T10:30:00",
    "updated_at": "2025-01-15T10:30:00"
  }
]
```

### 2. Récupère une question spécifique
```bash
GET /admin/faq/{id}
```

**Exemple:**
```bash
curl -X GET "http://localhost:8000/admin/faq/uuid-1234"
```

### 3. Crée une nouvelle question
```bash
POST /admin/faq
```

**Body:**
```json
{
  "category": "Wifipool",
  "question": "Hoe reset ik mijn wifipool?",
  "answer": "Om je wifipool te resetten, volg deze stappen...",
  "video_url": "https://youtube.com/watch?v=abc",
  "tags": ["reset", "wifipool"]
}
```

**Exemple curl:**
```bash
curl -X POST "http://localhost:8000/admin/faq" \
  -H "Content-Type: application/json" \
  -d '{
    "category": "Wifipool",
    "question": "Hoe reset ik mijn wifipool?",
    "answer": "Om je wifipool te resetten, volg deze stappen...",
    "video_url": "https://youtube.com/watch?v=abc",
    "tags": ["reset", "wifipool"]
  }'
```

**Réponse:**
```json
{
  "success": true,
  "message": "FAQ item created successfully",
  "id": "generated-uuid",
  "faq": {
    "id": "generated-uuid",
    "category": "Wifipool",
    "question": "Hoe reset ik mijn wifipool?",
    "answer": "Om je wifipool te resetten...",
    "video_url": "https://youtube.com/...",
    "tags": ["reset", "wifipool"],
    "created_at": "2025-01-15T10:30:00",
    "updated_at": "2025-01-15T10:30:00"
  },
  "total_items": 167
}
```

### 4. Met à jour une question existante
```bash
PUT /admin/faq/{id}
```

**Body (tous les champs sont optionnels):**
```json
{
  "category": "Wifipool Algemeen",
  "question": "Hoe reset ik mijn wifipool apparaat?",
  "answer": "Nouvelle réponse...",
  "video_url": "https://youtube.com/watch?v=new",
  "tags": ["reset", "wifipool", "apparaat"]
}
```

**Exemple curl:**
```bash
curl -X PUT "http://localhost:8000/admin/faq/uuid-1234" \
  -H "Content-Type: application/json" \
  -d '{
    "answer": "Nouvelle réponse mise à jour..."
  }'
```

### 5. Supprime une question
```bash
DELETE /admin/faq/{id}
```

**Exemple:**
```bash
curl -X DELETE "http://localhost:8000/admin/faq/uuid-1234"
```

**Réponse:**
```json
{
  "success": true,
  "message": "FAQ item deleted successfully",
  "id": "uuid-1234",
  "total_items": 165
}
```

### 6. Force le rechargement de l'index FAQ
```bash
POST /admin/faq/reload
```

**Exemple:**
```bash
curl -X POST "http://localhost:8000/admin/faq/reload"
```

**Réponse:**
```json
{
  "success": true,
  "message": "FAQ index reloaded successfully",
  "total_items": 166
}
```

## Fonctionnement technique

### Synchronisation automatique
Chaque opération de modification (POST, PUT, DELETE) déclenche automatiquement:
1. La sauvegarde du fichier `FAQAI.jsonl`
2. Le rechargement de l'index FAQ en mémoire via `_reload_faq()`

### Format JSONL
Les entrées sont sauvegardées au format JSONL avec les champs suivants:
```json
{
  "id": "uuid-generated",
  "Categorie": "Wifipool",
  "Vraag": "Question en néerlandais",
  "Antwoord": "Réponse en néerlandais",
  "Filmpje": "URL de la vidéo",
  "tags": ["tag1", "tag2"],
  "follow_up": false,
  "options": {},
  "created_at": "2025-01-15T10:30:00",
  "updated_at": "2025-01-15T10:30:00"
}
```

### Gestion des erreurs
- **404**: ID non trouvé
- **500**: Erreur de lecture/écriture du fichier

### Compatibilité
Le système est rétrocompatible avec l'ancien format de chargement du FAQ. Les questions existantes continuent de fonctionner normalement.

## Interface web (à venir)

Pour faciliter l'utilisation par le CEO, vous pouvez créer une interface web simple qui utilise ces endpoints. Voici un exemple de structure HTML/JavaScript:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Admin FAQ</title>
</head>
<body>
    <h1>Gestion FAQ</h1>

    <h2>Ajouter une question</h2>
    <form id="addFaqForm">
        <input type="text" name="category" placeholder="Catégorie" required>
        <textarea name="question" placeholder="Question" required></textarea>
        <textarea name="answer" placeholder="Réponse" required></textarea>
        <input type="url" name="video_url" placeholder="URL vidéo (optionnel)">
        <input type="text" name="tags" placeholder="Tags (séparés par des virgules)">
        <button type="submit">Ajouter</button>
    </form>

    <h2>Liste des questions</h2>
    <div id="faqList"></div>

    <script>
        // Charger les questions
        async function loadFaqs() {
            const response = await fetch('/admin/faq');
            const faqs = await response.json();
            const listDiv = document.getElementById('faqList');
            listDiv.innerHTML = faqs.map(faq => `
                <div>
                    <h3>${faq.question}</h3>
                    <p>${faq.answer}</p>
                    <button onclick="deleteFaq('${faq.id}')">Supprimer</button>
                </div>
            `).join('');
        }

        // Ajouter une question
        document.getElementById('addFaqForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const data = {
                category: formData.get('category'),
                question: formData.get('question'),
                answer: formData.get('answer'),
                video_url: formData.get('video_url') || null,
                tags: formData.get('tags').split(',').map(t => t.trim()).filter(t => t)
            };

            await fetch('/admin/faq', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            loadFaqs();
            e.target.reset();
        });

        // Supprimer une question
        async function deleteFaq(id) {
            if (confirm('Êtes-vous sûr de vouloir supprimer cette question?')) {
                await fetch(`/admin/faq/${id}`, { method: 'DELETE' });
                loadFaqs();
            }
        }

        // Charger au démarrage
        loadFaqs();
    </script>
</body>
</html>
```

## Tests

Pour tester le système:

1. **Démarrer le serveur:**
   ```bash
   uvicorn app.main:app --reload
   ```

2. **Tester la création:**
   ```bash
   curl -X POST "http://localhost:8000/admin/faq" \
     -H "Content-Type: application/json" \
     -d '{
       "category": "Test",
       "question": "Question test?",
       "answer": "Réponse test",
       "tags": ["test"]
     }'
   ```

3. **Vérifier la liste:**
   ```bash
   curl -X GET "http://localhost:8000/admin/faq"
   ```

4. **Vérifier le fichier JSONL:**
   ```bash
   tail -1 app/data/all/faq/FAQAI.jsonl
   ```

5. **Tester la modification:**
   ```bash
   curl -X PUT "http://localhost:8000/admin/faq/{id}" \
     -H "Content-Type: application/json" \
     -d '{"answer": "Nouvelle réponse"}'
   ```

6. **Tester la suppression:**
   ```bash
   curl -X DELETE "http://localhost:8000/admin/faq/{id}"
   ```
