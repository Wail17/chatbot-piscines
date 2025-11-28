# Fix: Chatbot ne trouve aucune réponse dans le FAQ

## Problème
Le chatbot retournait "Geen antwoord beschikbaar" pour TOUTES les questions, même celles qui existent dans FAQAI.jsonl.

## Cause racine identifiée
Le chemin du fichier FAQ dans `app/admin_routes.py` était **INCORRECT** :

```python
# AVANT (INCORRECT)
FAQ_FILE = os.path.join(os.path.dirname(__file__), "../data/all/faq/FAQAI.jsonl")
# Pointait vers: /home/user/chatbot-piscines/data/all/faq/FAQAI.jsonl (n'existe PAS)

# APRÈS (CORRECT)
FAQ_FILE = os.path.join(os.path.dirname(__file__), "data/all/faq/FAQAI.jsonl")
# Pointe vers: /home/user/chatbot-piscines/app/data/all/faq/FAQAI.jsonl (existe ✓)
```

## Changements effectués

### 1. **Fix principal : Correction du chemin dans `app/admin_routes.py`** (ligne 34)
   - Suppression du `../` incorrect
   - Le fichier FAQ est maintenant correctement localisé

### 2. **Ajout de logs de debug dans `app/main.py`**
   - Logs au démarrage pour vérifier le chemin du fichier FAQ
   - Logs dans `_reload_faq()` pour tracer le chargement
   - Logs dans `_load_faq_from_jsonl()` pour compter les lignes lues/skippées
   - Logs dans `_match_row_with_clarify()` pour tracer le matching

### 3. **Nouveaux endpoints de debug**
   - `GET /debug/faq` : Affiche l'état actuel du FAQ (count, paths, sample)
   - `GET /debug/reload-faq` : Force le rechargement du FAQ depuis le fichier
   - Amélioration de `GET /health` pour inclure plus d'infos sur le FAQ

## Vérification
Le fichier FAQAI.jsonl contient **166 entrées valides** et charge correctement maintenant.

```bash
python test_faq_load.py
# Output: Successfully loaded 166 FAQ items
```

## Utilisation des nouveaux endpoints

### Vérifier l'état du FAQ
```bash
curl http://localhost:8000/health
curl http://localhost:8000/debug/faq
```

### Forcer un reload du FAQ
```bash
curl http://localhost:8000/debug/reload-faq
```

## Instructions pour déploiement

1. Redémarrer le serveur pour appliquer les changements
2. Vérifier que le FAQ charge correctement avec `GET /health`
3. Si nécessaire, forcer un reload avec `GET /debug/reload-faq`
4. Tester avec une question du FAQ pour confirmer

## Tests à effectuer après déploiement

1. `GET /health` → devrait retourner `faq_rows: 166`
2. `GET /debug/faq` → devrait afficher 5 questions d'exemple
3. `POST /chat` avec question "Hoe weet ik of mijn wifipool apparaat een Gen 1 of een Gen 2 is?" → devrait retourner une réponse valide
