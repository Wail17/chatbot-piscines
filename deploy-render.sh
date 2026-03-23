#!/bin/bash

# Script de déploiement automatique sur Render
# Usage: ./deploy-render.sh <RENDER_API_KEY>

set -e

RENDER_API_KEY="$1"

if [ -z "$RENDER_API_KEY" ]; then
    echo "❌ Erreur: Clé API Render manquante"
    echo ""
    echo "Usage: ./deploy-render.sh <RENDER_API_KEY>"
    echo ""
    echo "Pour obtenir ta clé API:"
    echo "1. Va sur https://dashboard.render.com/account/api-keys"
    echo "2. Clique 'New API Key'"
    echo "3. Copie la clé et exécute:"
    echo "   ./deploy-render.sh <ta-clé-api>"
    exit 1
fi

echo "🚀 Déploiement du chatbot sur Render..."
echo ""

# Informations du projet
REPO_URL="https://github.com/Wail17/chatbot-piscines"
BRANCH="claude/upgrade-pool-chatbot-HjLmf"
SERVICE_NAME="chatbot-piscines"

echo "📦 Configuration:"
echo "  - Repo: $REPO_URL"
echo "  - Branch: $BRANCH"
echo "  - Service: $SERVICE_NAME"
echo ""

# Créer le service Static Site
echo "🔨 Création du service Static Site..."

RESPONSE=$(curl -s -X POST https://api.render.com/v1/services \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "static_site",
    "name": "'"$SERVICE_NAME"'",
    "repo": "'"$REPO_URL"'",
    "branch": "'"$BRANCH"'",
    "buildCommand": "echo \"No build needed\"",
    "publishPath": ".",
    "envVars": [],
    "autoDeploy": "yes"
  }')

# Vérifier si la création a réussi
if echo "$RESPONSE" | grep -q '"id"'; then
    SERVICE_ID=$(echo "$RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    SERVICE_URL=$(echo "$RESPONSE" | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4)

    echo "✅ Service créé avec succès!"
    echo ""
    echo "📊 Détails:"
    echo "  - ID: $SERVICE_ID"
    echo "  - URL: $SERVICE_URL"
    echo ""
    echo "⏳ Le déploiement est en cours..."
    echo "   Tu peux suivre la progression sur: https://dashboard.render.com"
    echo ""
    echo "🎉 Ton chatbot sera disponible dans 2-3 minutes sur:"
    echo "   👉 $SERVICE_URL"
    echo ""

    # Attendre le déploiement (optionnel)
    echo "⏱️  Attente du déploiement..."
    sleep 10

    # Vérifier le statut
    for i in {1..12}; do
        STATUS=$(curl -s -X GET "https://api.render.com/v1/services/$SERVICE_ID" \
          -H "Authorization: Bearer $RENDER_API_KEY" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

        echo "   Status: $STATUS"

        if [ "$STATUS" = "live" ]; then
            echo ""
            echo "🎉 DÉPLOIEMENT RÉUSSI!"
            echo "🌐 Ton chatbot est en ligne: $SERVICE_URL"
            echo ""
            echo "✅ Teste-le maintenant:"
            echo "   curl $SERVICE_URL"
            exit 0
        fi

        sleep 10
    done

    echo ""
    echo "⏳ Le déploiement prend plus de temps que prévu."
    echo "📊 Vérifie le statut sur: https://dashboard.render.com"

elif echo "$RESPONSE" | grep -q '"message"'; then
    ERROR_MSG=$(echo "$RESPONSE" | grep -o '"message":"[^"]*"' | cut -d'"' -f4)
    echo "❌ Erreur lors de la création du service:"
    echo "   $ERROR_MSG"
    echo ""
    echo "💡 Solutions possibles:"
    echo "   1. Vérifie que ta clé API est valide"
    echo "   2. Vérifie que tu as les permissions nécessaires"
    echo "   3. Un service avec ce nom existe peut-être déjà"
    echo ""
    echo "🔍 Réponse complète de l'API:"
    echo "$RESPONSE"
    exit 1
else
    echo "❌ Erreur inconnue"
    echo "🔍 Réponse de l'API:"
    echo "$RESPONSE"
    exit 1
fi
