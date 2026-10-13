# -*- coding: utf-8 -*-
"""
SYNCHRONISATION AUTOMATIQUE — lancée par le Planificateur de tâches toutes les
5 minutes (aucune action humaine). Silencieuse et sans risque :

  1. LIT (même si les fichiers sont ouverts dans Excel) :
       Gestion_stock_corrige.xlsx, Stocks_vendeur.xlsx (saisie), Achats_AAAA.xlsx
  2. CALCULE le stock, les ventes par mois, les marges, le calendrier, les alertes.
  3. ÉCRIT uniquement ce qui a changé, et uniquement dans les fichiers FERMÉS :
       - Ventes_AAAA_Mois.xlsx  (12 mois + récap)
       - Stocks_admin.xlsx      (rechiffré avec le mot de passe admin)
       - Stocks_vendeur.xlsx    (catalogue : prix, arrivages — la saisie n'est jamais touchée)
       - Achats_AAAA.xlsx       (liste des produits pour la liste déroulante)
     Un fichier ouvert est simplement reporté au passage suivant (aucune PermissionError).
  4. PUBLIE les données et la base de connaissances RAG dans Neon pour le chatbot.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys

from common import (DOSSIER_DATA, FichierVerrouille, VerrouExecution, calculer_stocks, charger_config,
                    charger_produits, ecrire_json, enregistrer_classeur, init_logs, lire_achats, lire_json,
                    lire_ventes, ouvrir_classeur)
import classeurs

ETAT = DOSSIER_DATA / "etat_sync.json"


def empreinte(*objets) -> str:
    brut = json.dumps(objets, default=lambda o: getattr(o, "__dict__", str(o)), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(brut.encode()).hexdigest()[:16]


def exporter_catalogue_secours(produits, etats, maj, log):
    """Copie PUBLIQUE du catalogue (sans prix d'achat) pour le bot, utilisée si Neon est injoignable.
    Fichier : bot/catalogue_secours.json (à pousser sur GitHub de temps en temps)."""
    from common import RACINE, ecrire_json
    dossier = RACINE / "bot"
    if not dossier.exists():
        return
    ecrire_json(dossier / "catalogue_secours.json", {
        "mis_a_jour": maj,
        "produits": [{"code": p.code, "designation": p.designation, "categorie": p.categorie, "marque": p.marque,
                      "prix_vente": p.prix_vente, "stock": etats[p.code].stock, "seuil": p.seuil,
                      "statut": etats[p.code].statut, "description": p.description, "usages": p.usages,
                      "photo_url": p.photo_url} for p in produits]})


def synchroniser(forcer: bool = False) -> int:
    log = init_logs()
    cfg = charger_config()
    etat = lire_json(ETAT, {})
    maj = dt.datetime.now().strftime("%d/%m/%Y à %H:%M")
    aujourd_hui = dt.date.today().isoformat()

    with VerrouExecution():
        produits = charger_produits(cfg)
        index = {p.code: p for p in produits}
        anomalies = []
        achats = lire_achats(cfg, index, anomalies)
        ventes = lire_ventes(cfg, index, anomalies)
        etats = calculer_stocks(produits, achats, ventes)

        h_produits = empreinte([p.__dict__ for p in produits])
        h_achats = empreinte([(a.date, a.code, a.quantite, a.prix_unitaire, a.fournisseur, a.facture) for a in achats])
        h_ventes = empreinte([(v.ligne, v.date, v.code, v.quantite, v.prix_unitaire, v.vendeur, v.client) for v in ventes])
        h_anom = empreinte([a.__dict__ for a in anomalies])
        cibles = {
            "vendeur": empreinte(h_produits, h_achats, cfg.vendeurs),
            "achats": h_produits,
            "ventes": empreinte(h_produits, h_ventes),
            "admin": empreinte(h_produits, h_achats, h_ventes, h_anom, aujourd_hui),
            "neon": empreinte(h_produits, h_achats, h_ventes, aujourd_hui, bool(cfg.database_url), cfg.embed_provider,
                             cfg.remise_max_pct, cfg.marge_min_pct),
        }
        a_faire = {k for k, h in cibles.items() if forcer or etat.get(k) != h}
        if not a_faire:
            log.info("Rien de nouveau (%d ventes, %d achats).", len(ventes), len(achats))
            return 0
        log.info("Changements détectés → %s", ", ".join(sorted(a_faire)))

        def ecrire(cle, fichier, fabrique, mdp=None):
            if cle not in a_faire:
                return
            try:
                enregistrer_classeur(fabrique(), fichier, mdp)
                etat[cle] = cibles[cle]
                log.info("✔ %s mis à jour", fichier.name)
            except FichierVerrouille:
                log.info("⏸ %s est ouvert dans Excel : mise à jour reportée au prochain passage", fichier.name)

        ecrire("ventes", cfg.f_ventes, lambda: classeurs.creer_fichier_ventes(cfg, produits, ventes, maj))
        ecrire("admin", cfg.f_admin,
               lambda: classeurs.creer_fichier_admin(cfg, produits, etats, achats, ventes, anomalies, maj),
               mdp=cfg.mdp_admin)

        def maj_vendeur():
            wb = ouvrir_classeur(cfg.f_vendeur)
            classeurs.ecrire_catalogue_vendeur(wb, cfg, produits, etats, maj)
            return wb
        ecrire("vendeur", cfg.f_vendeur, maj_vendeur)

        if cfg.f_achats.exists():
            def maj_achats():
                wb = ouvrir_classeur(cfg.f_achats, cfg.mdp_admin)
                classeurs.ecrire_produits_achats(wb, cfg, produits)
                return wb
            from common import est_chiffre
            ecrire("achats", cfg.f_achats, maj_achats, mdp=cfg.mdp_admin if est_chiffre(cfg.f_achats) else None)

        if "neon" in a_faire:
            if not cfg.database_url:
                etat["neon"] = cibles["neon"]
            else:
                try:
                    import rag_indexer
                    rag_indexer.publier(cfg, produits, etats, achats, ventes, anomalies, log)
                    etat["neon"] = cibles["neon"]
                except Exception as e:                       # coupure internet : on réessaiera
                    log.warning("Neon/RAG non mis à jour (%s) — nouvel essai au prochain passage", e)

        exporter_catalogue_secours(produits, etats, maj, log)
        etat["derniere_synchro"] = maj
        ecrire_json(ETAT, etat)
        if anomalies:
            log.info("%d anomalie(s) de saisie (voir feuille Alertes du fichier admin)", len(anomalies))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(synchroniser(forcer="--forcer" in sys.argv))
    except RuntimeError as e:
        init_logs().info(str(e))
    except Exception:
        init_logs().exception("Erreur de synchronisation")
        sys.exit(1)
