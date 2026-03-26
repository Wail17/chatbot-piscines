# 🔑 Comment obtenir ta clé API Render

## Méthode 1: Interface Web (2 minutes)

1. **Ouvre Render dans ton navigateur:**
   👉 https://dashboard.render.com/account/api-keys

2. **Connecte-toi** (si pas déjà connecté)

3. **Crée une nouvelle clé:**
   - Clique sur le bouton **"Create API Key"** ou **"New API Key"**
   - Donne un nom: `chatbot-deployment`
   - Clique **"Create"**

4. **COPIE LA CLÉ** (elle ressemble à: `rnd_xxxxxxxxxxxxxxxxxxxxx`)
   ⚠️ **IMPORTANT:** Tu ne pourras la voir qu'une seule fois!

5. **Colle la clé ici dans le chat Claude**

---

## Méthode 2: Déjà connecté sur Brave?

Si tu es déjà connecté à Render sur Brave, copie simplement la clé depuis:
👉 https://dashboard.render.com/account/api-keys

---

## Après avoir obtenu la clé

Envoie-moi la clé dans le chat et je lance:

```bash
./deploy-render.sh rnd_xxxxxxxxxxxxxxxxxxxxx
```

Et ton chatbot sera en ligne en **2 minutes**! 🚀

---

## Pas de compte Render?

Pas de problème! Alternatives ultra-rapides:

### **Netlify Drop** (30 secondes, ZERO config):
1. Va sur: https://app.netlify.com/drop
2. Drag & drop le fichier `index.html`
3. URL publique instantanée!

### **Tiiny.host** (30 secondes):
1. Va sur: https://tiiny.host
2. Upload `index.html`
3. URL publique instantanée!

---

## ⏱️ Comparaison

| Méthode | Temps | Nécessite compte | Permanent |
|---------|-------|------------------|-----------|
| Render | 2 min | Oui (gratuit) | ✅ Oui |
| Netlify Drop | 30 sec | Non | ✅ Oui |
| Tiiny.host | 30 sec | Non | ⏰ 7 jours |

---

## 🎯 Prêt?

Dis-moi quelle option tu préfères et je te guide! 🚀
