# Résumé des modifications - Système de suggestions multiples

## 📋 Modifications effectuées

### 1. **Fichier `app/rag.py`**

#### Nouvelle fonction `get_top_suggestions()`
- **Localisation**: Ligne 491-551
- **Fonctionnalité**: Retourne les top K suggestions de documents similaires avec scores de similarité
- **Paramètres**:
  - `question: str` - La question de l'utilisateur
  - `top_k: int = 4` - Nombre de suggestions (défaut: 4)
  - `min_similarity: float = 0.3` - Score minimum (0-1, défaut: 0.3)
- **Retour**: Liste de dictionnaires avec:
  ```json
  {
    "question": "...",
    "answer": "...",
    "similarity_score": 85.5,
    "category": "...",
    "metadata": {...}
  }
  ```
- **Caractéristiques**:
  - ✅ Convertit les scores de distance en pourcentages de similarité (0-100%)
  - ✅ Filtre les résultats avec score minimum
  - ✅ Trie par score décroissant
  - ✅ Gestion d'erreurs robuste

### 2. **Fichier `app/main.py`**

#### A. Modèle `ChatRequest` (ligne 57-63)
Ajout de deux nouveaux paramètres optionnels:
```python
top_k: int = 4                    # Nombre de suggestions à retourner
min_similarity: float = 0.3       # Score de similarité minimum (0-1)
```

#### B. Fonction `_get_faq_suggestions_with_scores()` (ligne 900-989)
- **Fonctionnalité**: Récupère les suggestions FAQ avec métadonnées complètes et scores
- **Paramètres**:
  - `user_q: str` - Question utilisateur
  - `top_k: int = 4` - Nombre de suggestions
  - `min_similarity: float = 0.3` - Score minimum
  - `lang_code: str = "nl"` - Code de langue
- **Retour**: Liste de suggestions avec:
  ```json
  {
    "question": "...",
    "answer": "...",
    "category": "...",
    "similarity_score": 75.3,
    "follow_up": {                 // Optionnel
      "question": "...",
      "options": [...]
    },
    "media": {                     // Optionnel
      "images": [...],
      "video": "..."
    }
  }
  ```
- **Caractéristiques**:
  - ✅ Utilise `_semantic_scores()` pour calculer les similarités cosine
  - ✅ Convertit scores en pourcentages (0-100%)
  - ✅ Inclut tous les champs existants (Foto, Filmpje, follow_up, etc.)
  - ✅ Support multilingue via `_ensure_language()`

#### C. Fonction `_build_response_from_suggestions()` (ligne 1775-1852)
- **Fonctionnalité**: Construit la réponse appropriée selon les scores de similarité
- **Logique de réponse**:
  1. **Score ≥ 85%** → Type: `"high_confidence"`
     - Retourne la meilleure réponse + alternatives
     - Format: `best_match` + `alternatives` + `suggestions`

  2. **Score 30-85%** → Type: `"multiple_suggestions"`
     - Affiche toutes les suggestions
     - Format: `suggestions` uniquement

  3. **Score < 30%** → Type: `"no_match"`
     - Message d'erreur + suggestions possibles
     - Format: message + `suggestions`

#### D. Endpoint `/chat` modifié (ligne 1879-1908)
- **Nouvelle logique** (prioritaire si `top_k > 1`):
  ```python
  # Si top_k > 1, utiliser le nouveau système
  if req.top_k > 1 and not clarify_ref:
      suggestions = _get_faq_suggestions_with_scores(...)
      response = _build_response_from_suggestions(...)
      return response
  ```
- **Comportement**:
  - Activé automatiquement quand `top_k > 1` dans la requête
  - Ne s'active pas pour les follow-ups (clarify_ref)
  - Fallback vers la logique traditionnelle en cas d'erreur
  - Compatible avec l'ancien système

## 📊 Format de réponse API

### Exemple de requête
```json
POST /chat
{
  "query": "Hoe kan ik condensatie vermijden?",
  "top_k": 4,
  "min_similarity": 0.3
}
```

### Exemple de réponse (high_confidence)
```json
{
  "success": true,
  "user_question": "Hoe kan ik condensatie vermijden?",
  "response": {
    "type": "high_confidence",
    "message": "Voici la réponse la plus pertinente:",
    "best_match": {
      "question": "Hoe kan ik condensatie op mijn apparatuur vermijden?",
      "answer": "Telkens de temperatuur...",
      "category": "Algemeen",
      "similarity_score": 92.5,
      "media": {
        "video": "https://..."
      }
    },
    "alternatives": [
      {
        "question": "Wat moet ik doen in de winter?",
        "answer": "...",
        "similarity_score": 78.3,
        "category": "Algemeen"
      }
    ],
    "suggestions": [...]
  }
}
```

### Exemple de réponse (multiple_suggestions)
```json
{
  "success": true,
  "user_question": "Comment utiliser wifipool?",
  "response": {
    "type": "multiple_suggestions",
    "message": "Voici les questions qui correspondent le mieux:",
    "suggestions": [
      {
        "question": "Waarom is wifipool geschikt voor spa's?",
        "answer": "...",
        "category": "Wifipool Algemeen",
        "similarity_score": 68.7
      },
      {
        "question": "Hoe installeer ik wifipool?",
        "answer": "...",
        "category": "Wifipool Algemeen",
        "similarity_score": 65.2
      }
    ]
  }
}
```

### Exemple de réponse (no_match)
```json
{
  "success": true,
  "user_question": "Quelle est la météo?",
  "response": {
    "type": "no_match",
    "message": "Ik heb geen geschikte antwoorden gevonden...",
    "suggestions": []
  }
}
```

## ✅ Contraintes respectées

- ✅ **Compatibilité JSONL**: Aucune modification des données existantes
- ✅ **Frontend intact**: Pas de modifications frontend
- ✅ **Cosine similarity**: Utilise `_cosine_similarity()` existante
- ✅ **Tri décroissant**: Suggestions triées par score
- ✅ **Tous les champs**: Conserve Foto, Filmpje, follow_up, etc.
- ✅ **Score minimum**: Filtre avec `min_similarity` (défaut: 0.3 = 30%)
- ✅ **Top K suggestions**: Paramètre `top_k` (défaut: 4)

## 🔧 Utilisation

### Mode ancien (par défaut, top_k = 1)
```json
POST /chat
{
  "query": "Ma question",
  "top_k": 1  // Comportement classique
}
```

### Mode nouveau (suggestions multiples)
```json
POST /chat
{
  "query": "Ma question",
  "top_k": 4,           // Retourne 4 suggestions
  "min_similarity": 0.3  // Score minimum 30%
}
```

## 📝 Tests

Pour tester les modifications:

```bash
# Démarrer le serveur
uvicorn app.main:app --reload

# Tester avec curl
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Hoe kan ik condensatie vermijden?",
    "top_k": 4,
    "min_similarity": 0.3
  }'
```

## 🎯 Prochaines étapes

1. **Tests d'intégration**: Tester avec différentes questions en néerlandais
2. **Ajustement des seuils**: Affiner les seuils 85% et 30% selon les résultats
3. **Frontend**: Adapter l'interface WordPress pour afficher les suggestions multiples
4. **Métriques**: Suivre le taux d'erreur et l'amélioration avec ce système

## 📌 Notes importantes

- Le système utilise `_semantic_scores()` qui nécessite OpenAI API key
- Fallback vers random questions si embeddings indisponibles
- Compatible avec l'ancien système (top_k=1 par défaut)
- Les follow-ups (clarify_ref) utilisent toujours l'ancien système
