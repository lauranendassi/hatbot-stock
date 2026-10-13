# Système de stock WeloobeAI — Guide d'installation et d'utilisation

## 1. Diagnostic : ce qui était impossible, et ce qu'on fait à la place

| Contrainte demandée | Réalité technique (Excel 2016, sans VBA) | Solution retenue |
|---|---|---|
| Mise à jour « à l'ouverture du fichier », sans script | Sans macro, Excel n'exécute aucun code à l'ouverture. Les liaisons entre classeurs ne lisent pas un fichier chiffré. | Un script **automatique et invisible** (Planificateur de tâches, toutes les 5 min). Personne ne le lance, personne ne le voit. |
| Admin chiffré **et** alimenté par Power Query | Power Query ne lit pas un classeur chiffré, et un classeur chiffré ne se rafraîchit pas tout seul. | Le fichier admin n'a **plus besoin** de se rafraîchir : Python le **régénère entièrement** puis le **rechiffre** (`weloobe_admin`). Il est toujours à jour quand on l'ouvre. |
| « Temps réel » entre fichiers | Python ne peut pas écrire dans un fichier ouvert (PermissionError). | Le stock vendeur se calcule **dans Excel, en direct** (formules SUMIF). Le reste se met à jour dès que le fichier concerné est fermé (≤ 5 min). Un fichier ouvert est simplement reporté, jamais d'erreur. |
| 6 vendeurs sur le même fichier | Excel 2016 ne permet pas la co-édition simultanée (réservée à Microsoft 365). | Un seul fichier partagé, **utilisé à tour de rôle** (poste de caisse ou dossier réseau). Voir § 8 si les vendeurs doivent saisir en même temps sur plusieurs postes. |
| « MySQL Neon » | Neon est une base **PostgreSQL**, pas MySQL. | Tout est écrit pour PostgreSQL (Neon), avec recherche plein texte française et pgvector. |

## 2. Architecture

```
                         PC du magasin (Windows 11, Excel 2016)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  SAISIES HUMAINES                                                            │
 │  Gestion_stock_corrige.xlsx ── produits, prix d'achat/vente, stock initial  │
 │  Stocks_vendeur.xlsx ───────── 6 vendeurs : saisie des ventes               │
 │       └─ Catalogue : stock disponible calculé EN DIRECT (SUMIF)             │
 │  Achats_2026.xlsx ──────────── admin : saisie des arrivages                 │
 │                                                                              │
 │           │ lecture (même si les fichiers sont ouverts)                      │
 │           ▼                                                                  │
 │  ┌──────────────────────────────────────────────┐   Planificateur de tâches │
 │  │ scripts\sync.py  (pythonw, invisible)        │◄── toutes les 5 min        │
 │  │  calcule stock, ventes, marges, calendrier   │◄── à l'ouverture de session│
 │  │  n'écrit que ce qui a changé, que si fermé   │                            │
 │  └──────────────────────────────────────────────┘                            │
 │           │ écriture atomique                     │                          │
 │           ▼                                       │                          │
 │  Ventes_2026_Mois.xlsx   12 mois + récap          │                          │
 │  Stocks_admin.xlsx       CHIFFRÉ weloobe_admin    │                          │
 │  Stocks_vendeur.xlsx     catalogue (arrivages)    │                          │
 └───────────────────────────────────────────────────┼──────────────────────────┘
                                                     │ HTTPS (si internet)
                                                     ▼
                   Neon PostgreSQL ─ produits, ventes, achats
                                   └ rag_documents : fiches en français naturel
                                     + niveau d'accès (0 client / 1 vendeur / 2 admin)
                                     + plein texte + trigrammes + vecteurs
                                                     ▲
                                                     │ recherche hybride filtrée
                   Render : bot/bot.py ── Messenger ─┘──► Groq (gpt-oss-120b / 20b)
                                         └── page de discussion (téléphone, PC)
```

**Principe clé** : chaque fichier a **un seul écrivain**.
Les humains écrivent dans 3 fichiers de saisie ; le script écrit dans les fichiers de consultation. Il n'y a donc jamais de conflit.

## 3. Les fichiers et leur rôle

| Fichier | Qui l'ouvre | Qui écrit dedans | Rôle |
|---|---|---|---|
| `Gestion_stock_corrige.xlsx` | Admin | Admin | **Référentiel produits** : code, désignation, catégorie, marque, prix d'achat, prix de vente, stock initial, seuil. Les colonnes sont reconnues par leur titre, dans n'importe quel ordre. |
| `Stocks_vendeur.xlsx` | 6 vendeurs | Vendeurs (feuille *Saisie_ventes*) + script (feuille *Catalogue*) | Saisie des ventes, catalogue avec **prix de vente uniquement** et stock en direct. Aucun prix d'achat. |
| `Achats_2026.xlsx` | Admin | Admin | Saisie des arrivages (date, code, quantité, prix, fournisseur, facture). Peut être chiffré lui aussi : le script le déchiffre avec le même mot de passe. |
| `Ventes_2026_Mois.xlsx` | Admin, gérant | Script uniquement | 12 feuilles mensuelles + récapitulatif (par mois, par produit, par vendeur). Lecture seule. |
| `Stocks_admin.xlsx` 🔒 | Admin | Script uniquement | Tableau de bord, **calendrier des ventes**, stock valorisé, marges, journal des ventes et achats, performance vendeurs, alertes. Chiffré `weloobe_admin`. |
| `config.ini` | Admin | Admin | Année, noms des vendeurs, mots de passe, connexion Neon. |
| `connaissances\*.md` | Admin | Admin | Ce que le bot sait du magasin (horaires, paiement, garantie, livraison…). |
| `scripts\` | — | — | `common.py`, `classeurs.py`, `init_systeme.py`, `sync.py`, `rag_indexer.py`, `embeddings.py` |
| `bot\` | — | — | Chatbot Messenger RAG à déployer sur Render (voir `bot\GUIDE_BOT.md`) |
| `data\`, `logs\`, `_sauvegardes\` | — | Script | État interne, journal (`logs\sync.log`), copies avant recréation. |

## 4. Installation sur le PC (15 minutes)

1. Copiez le contenu du ZIP dans `C:\Users\DM-TECH\Desktop\Projet_Stock\`.
   Gardez votre `Gestion_stock_corrige.xlsx` dans ce dossier.
2. **Mettez de côté vos anciens fichiers** `Stocks_vendeur.xlsx`, `Ventes_2026_Mois.xlsx`, `Achats_2026.xlsx`, `Stocks_admin.xlsx` (déplacez-les dans un sous-dossier `ancien\`). Les nouveaux ont une structure différente.
   Si des ventes étaient déjà saisies dans l'ancien fichier vendeur, recopiez-les dans la nouvelle feuille *Saisie_ventes* (colonnes Date, Code, Quantité, Vendeur) après l'étape 4.
3. Ouvrez `config.ini` avec le Bloc-notes : vérifiez les **noms des 6 vendeurs** et l'année.
4. Double-cliquez sur **`installer.bat`**. Il :
   - crée `venv\` (ou réutilise le vôtre s'il est dans ce dossier) et installe les bibliothèques ;
   - crée les 4 fichiers ;
   - fait une première synchronisation ;
   - programme la tâche automatique.

   *Votre venv est ailleurs ?* Déplacez-le dans `Projet_Stock\venv\` ou créez-en un nouveau : `py -3.13 -m venv venv`.

5. Vérification : ouvrez `Stocks_admin.xlsx` → Excel demande le mot de passe → `weloobe_admin`.

### Programmation (faite par `installer_tache.bat`, ou à la main)

`installer_tache.bat` crée deux tâches **sans fenêtre** (elles utilisent `pythonw.exe`) :

- `WeloobeAI_Synchro_Stock` : toutes les 5 minutes ;
- `WeloobeAI_Synchro_Stock_Demarrage` : 1 minute après l'ouverture de session.

Réglages appliqués : fonctionne sur batterie, rattrape un passage manqué, durée max 4 min, jamais deux exécutions en même temps.

Pour le faire à la main : *Planificateur de tâches → Créer une tâche*
- **Général** : nom `WeloobeAI_Synchro_Stock`, « Exécuter seulement si l'utilisateur est connecté ».
- **Déclencheurs** : Nouveau → « À l'ouverture de session » → cocher « Répéter la tâche toutes les **5 minutes** pendant **Indéfiniment** ».
- **Actions** : Programme = `C:\Users\DM-TECH\Desktop\Projet_Stock\venv\Scripts\pythonw.exe` ; Arguments = `scripts\sync.py` ; Commencer dans = `C:\Users\DM-TECH\Desktop\Projet_Stock`.
- **Conditions** : décocher « Ne démarrer que si l'ordinateur est sur secteur ».
- **Paramètres** : « Exécuter la tâche dès que possible si un démarrage planifié est manqué » ; « Si la tâche est déjà en cours : ne pas démarrer une nouvelle instance ».

`sync_stock.bat` sert au **test manuel** (double-clic = synchronisation immédiate avec affichage). Ce n'est pas lui qui tourne toutes les 5 minutes, pour éviter une fenêtre noire qui clignote.

Si le dossier est un jour renommé ou déplacé : relancez `installer_tache.bat`.

## 5. Le chatbot Messenger (RAG)

Le bot est dans le dossier `bot\` et se déploie sur Render. Tout est expliqué dans **`bot\GUIDE_BOT.md`**.
Côté PC, il suffit que `config.ini` contienne la chaîne Neon (section `[neon]`) : la synchronisation
publie alors toutes les 5 minutes le catalogue, les stocks et les fiches RAG que le bot utilise.
Colonnes facultatives utiles dans `Gestion_stock_corrige.xlsx` : **Usages** et **Photo (URL)**.

## 6. Utilisation au quotidien

### Vendeurs (Stocks_vendeur.xlsx)
1. Feuille **Saisie_ventes**, première ligne vide, cellules jaunes :
   Date (`Ctrl` + `;`) → Code (liste) → Quantité → Prix pratiqué *seulement en cas de remise* → Vendeur → Client (facultatif).
2. La colonne **Contrôle** doit afficher **OK** (sinon : code inconnu, date ou vendeur manquant, stock insuffisant).
3. `Ctrl + S`. Le **Catalogue** déduit la vente immédiatement.
4. **Fermer le fichier** dès qu'on n'en a plus besoin (au moins en fin de journée) : c'est à ce moment que les arrivages y sont intégrés.
5. Erreur de saisie : corriger la ligne. **Ne jamais supprimer de ligne** (mettre la quantité à 0 si besoin).

### Admin
- **Arrivage** : `Achats_2026.xlsx`, une ligne par arrivage, enregistrer. Le stock vendeur, l'admin et le bot suivent sous 5 minutes (le fichier vendeur dès qu'il est fermé).
- **Nouveau produit ou changement de prix** : uniquement dans `Gestion_stock_corrige.xlsx`. Le prix d'une vente déjà saisie ne change pas rétroactivement (il est figé au premier passage du script).
- **Consulter** : `Stocks_admin.xlsx` (mot de passe). Il est régénéré en arrière-plan ; s'il est ouvert, la mise à jour attend sa fermeture. Pour voir les dernières ventes : fermer puis rouvrir.
- **Contrôler chaque soir** la feuille **Alertes** (ruptures, stocks négatifs, saisies incomplètes).
- **Synchro immédiate** : double-clic sur `sync_stock.bat`.

### Changement d'année
Dans `config.ini`, `annee = 2027`, puis `installer.bat` : création de `Achats_2027.xlsx` et `Ventes_2027_Mois.xlsx`. Pour le nouveau fichier vendeur, archivez l'ancien et mettez à jour le **stock initial** du référentiel avec le stock final 2026 (feuille *Stock* du fichier admin).

## 7. Dépannage

| Symptôme | Cause / solution |
|---|---|
| L'admin n'affiche pas une vente récente | Le fichier vendeur n'a pas été enregistré, ou l'admin était ouvert : fermez-le, attendez 5 min, rouvrez. Voir `logs\sync.log`. |
| `⏸ … ouvert dans Excel : mise à jour reportée` dans le journal | Normal. Se résout seul à la fermeture du fichier. |
| `Mot de passe incorrect` | Le mot de passe de `config.ini` ne correspond plus au fichier admin ou achats. |
| `Aucun produit trouvé` | Le référentiel doit contenir au moins les colonnes *Désignation* et *Prix de vente* (ou *Code*). |
| Le bot donne une donnée ancienne | Internet coupé sur le PC : le journal indique `Neon/RAG non mis à jour`, nouvel essai automatique toutes les 5 min. |
| Les arrivages n'apparaissent pas chez les vendeurs | Le fichier vendeur n'est jamais fermé : le fermer une fois. |
| Tout recréer proprement | `venv\Scripts\python scripts\init_systeme.py --force` (sauvegarde automatique dans `_sauvegardes\`). **Attention** : recrée aussi le fichier vendeur, donc la saisie repart de zéro. |

## 8. Si les 6 vendeurs doivent saisir en même temps

Excel 2016 ne sait pas faire écrire plusieurs personnes dans un même fichier en même temps. Si c'est votre cas (plusieurs postes, même moment), la bonne évolution est un **petit formulaire web de saisie** hébergé sur le même service Render, qui écrit directement dans Neon ; le script synchroniserait alors Neon vers les fichiers Excel. Les fichiers admin, ventes et le bot resteraient identiques.

## 9. Sécurité

- `config.ini` contient le mot de passe admin en clair : gardez le dossier `Projet_Stock` hors du partage vendeurs. Les vendeurs ne doivent avoir accès qu'à `Stocks_vendeur.xlsx`. Alternative : supprimer la ligne du fichier et définir la variable d'environnement Windows `STOCK_MDP_ADMIN`.
- `Gestion_stock_corrige.xlsx` contient les prix d'achat : vous pouvez le chiffrer dans Excel (*Fichier → Informations → Protéger le classeur → Chiffrer avec mot de passe*) avec `weloobe_admin` ; le script continue de le lire.
- Les codes du bot (`CODE_VENDEUR`, `CODE_ADMIN`, `CODE_BOSS`) sont différents de `weloobe_admin` et se changent dans Render.
