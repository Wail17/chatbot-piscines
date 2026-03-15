#!/bin/bash

# Script pour déployer la démo sur différentes plateformes

echo "🚀 Options de déploiement pour la démo du chatbot"
echo ""
echo "Option 1: Serveur local + ngrok (tunnel public)"
echo "Option 2: GitHub Gist"
echo "Option 3: Copier le code pour CodePen/JSFiddle"
echo ""

# Vérifier si le fichier existe
if [ ! -f "chatbot-demo.html" ]; then
    echo "❌ Erreur: chatbot-demo.html introuvable"
    exit 1
fi

echo "✅ Fichier HTML trouvé"
echo ""
echo "📋 Pour déployer manuellement:"
echo ""
echo "1️⃣  Via CodePen (le plus simple):"
echo "   • Aller sur https://codepen.io/pen/"
echo "   • Copier le contenu de chatbot-demo.html"
echo "   • Coller dans l'éditeur"
echo "   • Cliquer sur 'Save' puis partager le lien"
echo ""
echo "2️⃣  Via Netlify Drop:"
echo "   • Aller sur https://app.netlify.com/drop"
echo "   • Glisser-déposer le fichier chatbot-demo.html"
echo "   • Récupérer le lien généré"
echo ""
echo "3️⃣  Via GitHub Gist:"
echo "   • Aller sur https://gist.github.com/"
echo "   • Créer un nouveau gist avec chatbot-demo.html"
echo "   • Utiliser le bouton 'Raw' pour obtenir l'URL"
echo ""

read -p "Voulez-vous afficher le contenu du fichier pour le copier? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    cat chatbot-demo.html
fi
