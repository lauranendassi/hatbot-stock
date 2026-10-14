# Système de stock et chatbot WeloobeAI — Guide complet

## 1. Le principe en une phrase

Les vendeurs et l'admin **saisissent** dans 3 fichiers Excel ; un petit programme invisible sur le PC **met à jour tout le reste** une dizaine de secondes après chaque enregistrement (fichiers de suivi, tableau de bord chiffré, base Neon) ; le **chatbot Messenger** lit Neon et répond aux clients avec les stocks et les prix réels.

```
 SAISIE (humains)                       MISE À JOUR AUTO (PC)          CONSULTATION
 ───────────────                        ─────────────────────          ────────────
 Gestion_stock_corrige.xlsx  ─┐                                   ┌─► Ventes_2026_Mois.xlsx
 Stocks_vendeur.xlsx         ─┼─► Ctrl+S ─► surveiller.py ─► sync ┼─► Stocks_admin.xlsx 🔒
 Achats_2026.xlsx            ─┘            (~10 secondes)         └─► Neon (base en ligne)
                                                                          │
                                       Clients Messenger ◄── chatbot (Render) ◄┘
```

Règle d'or : **chaque fichier a un seul « auteur »**. Les humains écrivent dans les fichiers de saisie ; le programme écrit dans les fichiers de consultation. Ne tapez jamais rien dans `Ventes_2026_Mois.xlsx` ni dans `Stocks_admin.xlsx` : ils sont régénérés automatiquement et vos modifications seraient effacées.

## 2. Les fichiers

| Fichier | Qui l'utilise | Rôle |
|---|---|---|
| `Gestion_stock_corrige.xlsx` | Admin | **Référentiel produits** : code, désignation, catégorie, marque, prix d'achat, prix de vente, stock initial, seuil d'alerte, usages, description, photo, prix minimum de négociation. Toute modification de prix ou nouveau produit se fait ici. |
| `Stocks_vendeur.xlsx` | Les 6 vendeurs | Feuille **Saisie_ventes** (une ligne par vente) et feuille **Catalogue** (prix de vente et stock disponible en temps réel). Aucun prix d'achat visible. |
| `Achats_2026.xlsx` | Admin | Saisie des **arrivages** (date, code, quantité, prix d'achat, fournisseur, facture). |
| `Ventes_2026_Mois.xlsx` | Admin, direction | **Généré** : une feuille par mois + récapitulatif par produit et par vendeur. |
| `Stocks_admin.xlsx` 🔒 | Direction | **Généré et chiffré** (mot de passe `weloobe_admin`) : tableau de bord, calendrier des ventes, stock valorisé, marges, journal des ventes et achats, performance des vendeurs, alertes. |

Dossiers et fichiers techniques :

| Élément | Rôle |
|---|---|
| `config.ini` | Réglages : année, noms des vendeurs, mots de passe, connexion Neon, marge de négociation du bot. |
| `connaissances\magasin.md` | Ce que le bot répond sur l'adresse, les horaires, le paiement, la garantie, la livraison. Remplacez les `[crochets]`. |
| `scripts\` | Le programme de mise à jour (Python). |
| `bot\` | Le chatbot Messenger (déployé sur Render via GitHub). Guide détaillé : `bot\GUIDE_BOT.md`. |
| `modeles\` | Modèles utilisés **seulement** si `config.ini` ou `Gestion_stock_corrige.xlsx` n'existent pas encore. |
| `logs\sync.log` | Journal : chaque mise à jour, chaque fichier reporté, chaque erreur. |
| `_sauvegardes\` | Copies automatiques avant toute recréation de fichier. |

## 3. Installation (une seule fois, ou après une mise à jour du dossier)

1. Fermez Excel.
2. Placez le dossier dans `C:\Users\DM-TECH\Desktop\Projet_Stock\`.
   *Si vous mettez à jour un dossier existant* : copiez le nouveau contenu **par-dessus** l'ancien. Vos fichiers Excel, `config.ini`, `venv` et `bot\.env` ne sont pas dans le ZIP : ils restent intacts.
3. *Première installation seulement* : ouvrez `config.ini` (Bloc-notes), collez la chaîne Neon après `database_url =` et mettez les vrais prénoms des vendeurs après `noms =`. (Si `config.ini` n'existe pas encore, `installer.bat` le crée à l'étape 4 : complétez-le puis relancez `installer.bat`.)
4. Double-cliquez sur **`installer.bat`**. Il :
   - prépare Python et ses bibliothèques ;
   - crée les fichiers Excel manquants, et **convertit** un ancien fichier vendeur au nouveau format en **reprenant les ventes déjà saisies** ;
   - fait une première mise à jour (fichiers + Neon) ;
   - active la **mise à jour automatique** (au démarrage de Windows, sans droits administrateur).
5. Vérifiez la fin de la fenêtre : `✔ Neon : 15 produits…` puis `Mise a jour automatique ACTIVE`.

## 4. Au quotidien

### Vendeur — enregistrer une vente
1. `Stocks_vendeur.xlsx` → feuille **Saisie_ventes** → première ligne vide.
2. **Date** : `Ctrl + ;` (date du jour).
3. **Produit** : flèche de la liste déroulante. Pour chercher : tapez le début du nom (`Dell`, `HP`, `Écran`…), **Entrée**, puis rouvrez la flèche : seuls les produits qui commencent ainsi apparaissent.
   → le **code SKU**, le **prix** et le **total** se remplissent tout seuls.
4. **Quantité**, **Prix pratiqué** (seulement en cas de remise), **Vendeur**, **Client** (facultatif).
5. La colonne **Contrôle** doit afficher **OK** (sinon elle dit quoi corriger).
6. **Ctrl + S**. C'est tout : environ 10 secondes plus tard, les ventes du mois, le tableau de bord et le stock du bot sont à jour.

Ne supprimez jamais une ligne : en cas d'erreur, corrigez-la (ou mettez la quantité à 0).

### Admin — arrivage de marchandise
`Achats_2026.xlsx` → une ligne par arrivage → Ctrl + S. Le stock remonte partout automatiquement.

### Admin — nouveau produit ou changement de prix
Uniquement dans `Gestion_stock_corrige.xlsx` → Ctrl + S. La liste déroulante des vendeurs, le catalogue et le bot se mettent à jour. (Le prix d'une vente déjà saisie ne change pas.)

### Direction — consulter
Ouvrez `Stocks_admin.xlsx` (mot de passe). Un fichier **ouvert** ne peut pas être modifié par le programme : pour voir les dernières ventes, **fermez-le puis rouvrez-le** une quinzaine de secondes plus tard. Chaque soir, regardez la feuille **Alertes** (ruptures, stocks faibles, saisies incomplètes).

## 5. Le chatbot Messenger

Il répond aux clients sur la page Facebook, 24 h/24, en s'appuyant sur les stocks et prix réels (copiés dans Neon à chaque mise à jour).

- **Il comprend d'abord le besoin** (usage, puis budget) avant de proposer un produit et d'en donner le prix — le code l'empêche de donner un prix trop tôt. Si le client insiste, il le donne.
- **Il conseille 1 ou 2 produits** adaptés, avec des arguments liés à l'usage du client ; jamais le catalogue complet.
- **Il négocie** en 3 paliers (petit geste, plus grand geste, dernier prix) sans jamais descendre sous le **prix minimum** : colonne `Prix minimum (FCFA)` du fichier produits, ou à défaut prix de vente − 7 % sans passer sous prix d'achat + 15 % (réglable dans `config.ini`, section `[negociation]`). C'est le code qui décide du prix, pas l'IA.
- **Il prend les réservations** (nom, téléphone camerounais vérifié, retrait ou livraison) et prévient l'équipe par email, Martial en copie.
- **Il passe la main** à un humain si le client le demande, est mécontent ou a une panne (SAV) ; il se tait ensuite 12 h sur cette conversation, et aussi dès qu'un conseiller répond lui-même depuis la page.
- **Équipe** : dans Messenger, `/connexion CODE` donne accès aux commandes `/stock`, `/commandes`, et aux questions sur les ventes (« combien on a vendu hier ? ») selon le rôle (vendeur, admin, boss).
- Son comportement se règle dans `bot\instructions.md`, sans toucher au code.

Mise à jour du bot : modifiez les fichiers du dossier `bot\`, puis dans Git Bash :
```
cd ~/Desktop/Projet_Stock
git add -A
git commit -m "description de la modification"
git push origin main
```
Render redéploie tout seul en 1 à 2 minutes. Santé du bot : `https://hatbot-stock.onrender.com/sante`.

## 6. En cas de problème

| Symptôme | Solution |
|---|---|
| Le tableau de bord n'affiche pas une vente | Le fichier vendeur a-t-il été enregistré (Ctrl+S) ? `Stocks_admin.xlsx` était-il ouvert ? Fermez-le, attendez 15 s, rouvrez-le. |
| Rien ne se met à jour | Double-cliquez `sync_stock.bat` (mise à jour immédiate) et lisez le message. Puis relancez `installer_tache.bat` pour réactiver l'automatique. |
| `⏸ … ouvert dans Excel` dans le journal | Normal : le fichier sera mis à jour dès sa fermeture. |
| `✘ … n'a pas pu être mis à jour` | Copiez les lignes du journal `logs\sync.log` à votre support technique. |
| Contrôle = « Choisir dans la liste » | Le produit tapé n'est pas un nom exact : choisissez-le dans la flèche. |
| Le bot donne un stock ancien | Le PC est-il allumé et connecté à internet ? La mise à jour vers Neon reprend toute seule au retour de la connexion. |
| Le bot ne répond plus | Ouvrez `/sante` (lien ci-dessus). Si un client a demandé un conseiller, le bot se tait 12 h sur cette conversation : c'est voulu. |
| Arrêter la mise à jour automatique | `desinstaller_tache.bat`, puis redémarrer le PC. |

## 7. Sécurité

- `config.ini`, les fichiers Excel et `bot\.env` contiennent des mots de passe, des clés et les prix d'achat : ils ne sont **jamais** envoyés sur GitHub (fichier `.gitignore`) et ne doivent pas être partagés avec les vendeurs.
- Les vendeurs n'ont besoin que de `Stocks_vendeur.xlsx`.
- Le mot de passe du fichier admin se transmet de vive voix ou par SMS, jamais par email.
