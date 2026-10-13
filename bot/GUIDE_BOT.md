# Chatbot Messenger WeloobeAI v4 — Installation, déploiement et tests

## 0. Comment les pièces s'emboîtent

```
PC du magasin (Excel)                      Render (gratuit)                       Meta
─────────────────────                      ────────────────                       ────
Gestion_stock_corrige.xlsx ─┐              bot.py (Flask + gunicorn)
Stocks_vendeur.xlsx ────────┼─ sync.py ─►  Neon PostgreSQL ◄──── lit ────  conversation.py ◄── webhook ── Messenger
Achats_2026.xlsx ───────────┘  (5 min)     • stock_produits (catalogue)        ├─ intentions.py (règles en dur)
                                           • stock_rag_documents (RAG)      ├─ skills/ (8 modules)
                                           • bot_* (conversations, profils,  ├─ rag.py (recherche hybride)
                                             mémoire, commandes, rôles)      ├─ ia.py (Groq 120b → 20b → secours)
                                                                             └─ instructions.md (règles du patron)
```

**Pourquoi le bot ne lit pas directement les fichiers Excel** : il tourne sur un serveur Render, qui ne voit pas le disque de votre PC. La synchronisation automatique du PC (déjà installée avec `installer.bat`) recopie les Excel dans Neon toutes les 5 minutes : le bot lit donc **les données des Excel, via Neon**. Si Neon ne répond pas, il utilise `catalogue_secours.json`, une copie du catalogue public écrite par la même synchronisation.

## 1. Ce que fait chaque fichier

| Fichier | Rôle |
|---|---|
| `bot.py` | Serveur Flask : webhook Messenger (vérification, signature, anti-doublon, traitement en arrière-plan), `/sante`, `/test`. |
| `conversation.py` | Moteur : machine à états, règle « qualifier avant de proposer », commande, transfert humain, SAV, mode équipe. |
| `intentions.py` | Détection d'intention **en dur** (prix, commande, humain, SAV, plainte…) + extraction budget, usages, téléphone, quantité. |
| `rag.py` | Recherche des fiches dans Neon, filtrées par rôle ; masque les prix tant que le client n'est pas qualifié. |
| `ia.py` | Groq : `openai/gpt-oss-120b`, puis `openai/gpt-oss-20b` si limite atteinte, puis réponses préparées. Retire le Markdown. |
| `messenger.py` | Envoi Messenger, découpage à 1 900 caractères, photos, indicateur « en train d'écrire ». |
| `catalogue.py` | Catalogue public (Neon, sinon secours). Jamais de prix d'achat. |
| `db.py` | Tables du bot (créées automatiquement) : conversations + profil, 30 derniers messages, rôles, commandes. |
| `instructions.md` | **Règles métier** — modifiable sans toucher au code. |
| `skills/` | 9 capacités indépendantes, interface `executer(params) -> dict`. |
| `tests/` | 35 tests automatiques (`pytest -q`). |
| `simuler.py` | Discuter avec le bot dans le terminal. |

### Les skills

| Skill | Entrée principale | Sortie |
|---|---|---|
| `rechercher_produit` | texte, catégorie, usages, budget | produits classés, produit nommé par le client (même avec une faute : « elitbok ») |
| `qualifier_besoin` | profil, catégorie | qualifié ? sinon LA question à poser (usage → budget ; modèle du PC pour une pièce) |
| `verifier_stock` | code, quantité | disponible, suffisant, phrase prête |
| `proposer_alternatives` | code ou catégorie, budget | équivalents disponibles au prix le plus proche |
| `enregistrer_commande` | nom, téléphone, retrait/livraison, lignes | référence `CMD-AAAAMMJJ-0001` |
| `notifier_email` | type, sujet, contenu | email à l'équipe, **Martial en copie** |
| `envoyer_photo` | psid, code | photo envoyée dans Messenger |
| `verifier_permissions` | rôle, action | autorisé ? (client < vendeur < admin < boss) |
| `negocier_prix` | code, offre du client, état | décision (accepte, contre-offre, dernier prix, prix ferme) et LE prix à annoncer |

### La négociation

Le bot négocie comme un vendeur, en 3 paliers : il fait d'abord un petit geste, puis un plus grand, puis donne son dernier prix (le **prix plancher**), et accepte toute offre qui atteint le palier en cours.
- **Prix plancher** : colonne `Prix minimum (FCFA)` de `Gestion_stock_corrige.xlsx`. Si elle est vide, il est calculé sur le PC selon `config.ini` → `[negociation]` : prix de vente − 7 %, sans jamais descendre sous prix d'achat + 15 %.
- Ce plancher va dans Neon (`stock_produits.prix_min`) mais n'est **jamais** transmis à l'IA : c'est le code qui décide du prix, et une réponse de l'IA qui annonce un autre montant est remplacée.
- Le prix accordé est repris dans la réservation, contrôlé une dernière fois (jamais sous le plancher), et signalé « prix NÉGOCIÉ » dans l'email à l'équipe.
- Si Neon est injoignable (catalogue de secours, sans plancher), le prix est ferme.

## 2. Règles du patron : où elles sont appliquées

| Règle | Instructions | Verrou dans le code |
|---|---|---|
| Comprendre le besoin avant de proposer | § 3 | `qualifier_besoin` + `rag.masquer_prix` : l'IA **ne reçoit pas les prix** tant que l'usage (et le budget, ou un modèle précis) n'est pas connu. Si elle invente quand même un prix, la réponse est remplacée par la question. |
| Une seule question à la fois | § 2 | La consigne envoyée à l'IA contient UNE question choisie par le code. |
| Pas de catalogue brut | § 4 | Le système ne transmet que 1 à 3 produits filtrés. |
| Ton chaleureux, conseil | § 1, 2, 6 | — |
| Transmettre à un humain | § 7 | Intention `HUMAIN`/`PLAINTE` → email + bot en pause 12 h. Pause aussi dès qu'un conseiller répond depuis la Page. |
| Rôles vendeur / admin / boss / client | § 8 | `verifier_permissions` + niveau des fiches RAG. |
| Martial en copie | — | `EMAIL_COPIE` sur chaque notification. |

Choix à faire valider par le patron : si un client **redemande** un prix après la première question, le bot le lui donne (insister davantage ferait fuir le client). Pour changer ça, modifiez la ligne `insistance = ... >= 2` dans `skills/qualifier_besoin.py` (mettez 99 pour ne jamais céder).

## 3. Tester sur votre PC (avant de déployer)

Dans Git Bash :

```bash
cd /c/Users/DM-TECH/Desktop/Projet_Stock/bot
python -m venv venv && source venv/Scripts/activate
pip install -r requirements.txt
pytest -q                       # doit afficher : 35 passed
cp .env.example .env            # puis remplissez GROQ_API_KEY (et DATABASE_URL pour tester avec Neon)
python simuler.py               # discutez avec le bot dans le terminal
```

Scénario de test conseillé dans `simuler.py` :
1. `bonsoir c'est combien le HP EliteBook ?` → il doit demander l'usage, **sans prix**.
2. `pour la comptabilité` → il propose l'EliteBook avec son prix.
3. `je le prends` → nom, téléphone, boutique ou livraison, récapitulatif, `oui` → référence CMD-….
4. `je veux parler à quelqu'un` → transfert ; le message suivant ne reçoit plus de réponse.
5. `/connexion VOTRE_CODE_ADMIN` puis `/aide`, `/commandes`, `combien on a vendu hier ?`.

## 4. Déployer sur Render (votre service `hatbot-stock` existant)

1. Mettez le contenu du dossier `bot/` dans le dépôt GitHub relié à votre service Render (remplace l'ancien code ; gardez une copie de l'ancien sur une branche).
   Ne mettez **jamais** `.env` sur GitHub (déjà exclu par `.gitignore`).
2. Render → votre service → **Settings** :
   - Build Command : `pip install -r requirements.txt`
   - Start Command : `gunicorn bot:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`
     (identique au `Procfile` ; gardez **1 worker**, le bot garde l'ordre des messages par client en mémoire)
   - Python 3.13 : le fichier `.python-version` suffit ; sinon variable `PYTHON_VERSION=3.13.5`.
   - Health Check Path : `/sante`
3. **Environment** : ajoutez les variables du tableau ci-dessous.
4. Déployez, puis ouvrez `https://hatbot-stock.onrender.com/sante` : vous devez voir `"base": "neon"`, `"catalogue": {"produits": 15, "source": "neon"}` et l'heure de la dernière synchro Excel.

### Variables d'environnement

| Variable | Valeur | Obligatoire |
|---|---|---|
| `DATABASE_URL` | chaîne Neon (la même que dans `config.ini` du PC) | oui |
| `PAGE_ACCESS_TOKEN` | jeton de la Page Facebook | oui |
| `VERIFY_TOKEN` | mot secret de votre choix (le même dans Meta) | oui |
| `APP_SECRET` | « Clé secrète » de l'app Meta (Paramètres > Général) | fortement conseillé |
| `GROQ_API_KEY` | clé Groq (console.groq.com) | oui |
| `MODELE_PRINCIPAL` / `MODELE_SECOURS` | `openai/gpt-oss-120b` / `openai/gpt-oss-20b` | défaut |
| `CODE_VENDEUR`, `CODE_ADMIN`, `CODE_BOSS` | codes secrets différents, à taper `/connexion CODE` | oui |
| `BREVO_API_KEY` | clé API Brevo | pour les emails |
| `EMAIL_EXPEDITEUR` | adresse validée dans Brevo | pour les emails |
| `EMAIL_DESTINATAIRES` | patron (plusieurs : séparés par des virgules) | pour les emails |
| `EMAIL_COPIE` | l'adresse de Martial | pour les emails |
| `GRAPH_VERSION` | `v26.0` | défaut |
| `HANDOFF_HEURES` | 12 (durée de pause du bot après transfert) | défaut |
| `TEST_TOKEN` | mot secret pour la route `/test` | facultatif |
| `NOM_MAGASIN` | WeloobeAI | défaut |

**Pourquoi Brevo et pas Gmail ?** Depuis septembre 2025, Render bloque les ports SMTP (25, 465, 587) sur les services gratuits : un envoi Gmail/SMTP échouerait. Brevo envoie par API web (gratuit, 300 emails/jour) : créez un compte, validez l'adresse expéditrice (*Senders*), puis *SMTP & API → API Keys*. Sans configuration, les emails sont écrits dans les logs Render (mode simulation).

**Mise en veille de Render gratuit** : après 15 minutes sans visite, le service s'endort et le premier message met ~50 s à recevoir une réponse. Créez un ping gratuit (cron-job.org ou UptimeRobot) sur `https://hatbot-stock.onrender.com/sante` toutes les 10 minutes. Les messages en double que Meta peut renvoyer pendant le réveil sont ignorés automatiquement.

## 5. Configurer le webhook Meta

Dans developers.facebook.com → votre app → **Messenger → Paramètres de l'API** :
1. **Webhook** : URL de rappel `https://hatbot-stock.onrender.com/webhook`, jeton de vérification = `VERIFY_TOKEN` → *Vérifier et enregistrer*.
2. Abonnez la Page aux champs : **messages**, **messaging_postbacks**, **message_echoes** (indispensable pour que le bot se taise quand un conseiller répond depuis la Page).
3. Générez le **jeton d'accès de la Page** → `PAGE_ACCESS_TOKEN`.
4. *(Recommandé)* Paramètres de la Page → Messenger : bouton « Démarrer » ; le bot le traite comme un « Bonjour ».
5. Tant que l'app est en mode Développement, seuls les comptes ayant un rôle sur l'app (administrateur, testeur) reçoivent les réponses. Pour le public : passez l'app en mode **Live** (l'autorisation `pages_messaging` doit être approuvée par Meta).

## 6. Tests après déploiement

1. `GET /sante` → `statut: ok`, `base: neon`, `ia: true`.
2. Depuis un compte testeur, écrivez à la Page : le scénario du § 3.
3. Route `/test` sans Messenger (Git Bash) :
   ```bash
   curl -X POST https://hatbot-stock.onrender.com/test -H "X-Test-Token: VOTRE_TEST_TOKEN" \
        -H "Content-Type: application/json" -d '{"psid":"essai1","texte":"c est combien le macbook ?"}'
   ```
4. Logs Render : chaque échange affiche `[xxxxxx] rôle=client état=… ← message` puis `→ réponse`, les skills, le modèle utilisé et sa durée.
5. Neon → *SQL Editor* :
   ```sql
   SELECT reference, nom, telephone, total, cree_le FROM bot_commandes ORDER BY id DESC;
   SELECT psid, etat, profil FROM bot_conversations ORDER BY maj DESC LIMIT 10;
   ```

## 7. Au quotidien

- **Modifier le comportement** : éditez `instructions.md`, poussez sur GitHub (Render redéploie). Ou, compte boss : `/instructions` dans Messenger pour recharger sans redéployer.
- **Photos produits** : ajoutez une colonne `Photo (URL)` dans `Gestion_stock_corrige.xlsx` avec un lien **https** public vers l'image. Elle arrive dans Neon à la synchronisation suivante.
- **Usages des produits** : la colonne `Usages` (ex. « bureautique, études, gestion ») aide beaucoup le bot à bien conseiller. Remplissez-la pour chaque produit.
- **Infos pratiques** (adresse, horaires, paiement, garantie, livraison) : `connaissances\magasin.md` sur le PC — remplacez les [crochets].
- **Rôles** : chaque membre de l'équipe écrit `/connexion CODE` à la Page une seule fois. `/deconnexion` pour redevenir client.
- **Commandes** : ce sont des réservations. Le conseiller rappelle le client ; la vente est ensuite saisie normalement dans `Stocks_vendeur.xlsx`.
- **Reprendre la main sur une conversation** : répondez simplement depuis la boîte de réception de la Page ; le bot se tait 12 h pour ce client (ou jusqu'à ce que le client écrive « bot »).
