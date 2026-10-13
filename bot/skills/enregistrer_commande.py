# -*- coding: utf-8 -*-
"""Skill : enregistrer_commande — enregistre une commande VALIDÉE par le client.

params :
    psid            (str)
    nom             (str)  nom du client
    telephone       (str)  9 chiffres commençant par 6 (Cameroun), +237 accepté
    mode_livraison  (str)  "retrait" ou "livraison"
    quartier        (str)  obligatoire si livraison
    lignes          (list) [{"code": "LAP-001", "quantite": 1, "prix_unitaire": 280000 (si négocié)}, ...]
retour :
    {"ok": True, "reference", "total", "lignes": [...]}  ou  {"ok": False, "erreurs": [...]}

La commande est une RÉSERVATION : le stock Excel ne baisse qu'au moment où le
vendeur saisit la vente dans Stocks_vendeur.xlsx (paiement / remise du produit).
"""
from __future__ import annotations

import re

import catalogue
import db


def normaliser_telephone(tel: str) -> str | None:
    chiffres = re.sub(r"\D", "", tel or "")
    if chiffres.startswith("237") and len(chiffres) == 12:
        chiffres = chiffres[3:]
    return chiffres if re.fullmatch(r"6\d{8}", chiffres) else None


def executer(params: dict) -> dict:
    erreurs = []
    nom = (params.get("nom") or "").strip()
    tel = normaliser_telephone(params.get("telephone", ""))
    mode = params.get("mode_livraison") or "retrait"
    quartier = (params.get("quartier") or "").strip()
    if len(nom) < 2:
        erreurs.append("nom manquant")
    if not tel:
        erreurs.append("téléphone invalide (9 chiffres commençant par 6)")
    if mode not in ("retrait", "livraison"):
        erreurs.append("mode de livraison inconnu")
    if mode == "livraison" and not quartier:
        erreurs.append("quartier de livraison manquant")

    lignes = []
    for l in params.get("lignes") or []:
        p = catalogue.par_code(l.get("code", ""))
        q = int(l.get("quantite") or 0)
        if not p:
            erreurs.append(f"produit inconnu {l.get('code')}")
            continue
        if q <= 0:
            erreurs.append(f"quantité invalide pour {p['code']}")
            continue
        if float(p.get("stock") or 0) < q:
            erreurs.append(f"stock insuffisant pour {p['designation']} ({float(p.get('stock') or 0):g} disponible)")
            continue
        prix = float(p["prix_vente"])
        pu = float(l.get("prix_unitaire") or prix)
        plancher = float(p["prix_min"]) if p.get("prix_min") not in (None, "") else prix
        if pu < min(plancher, prix):                     # sécurité : jamais sous le prix plancher
            erreurs.append(f"prix négocié invalide pour {p['designation']}")
            continue
        lignes.append({"code": p["code"], "designation": p["designation"], "quantite": q, "prix_unitaire": pu,
                       "prix_catalogue": prix, "total": q * pu})
    if not lignes and not erreurs:
        erreurs.append("aucun produit")
    if erreurs:
        return {"ok": False, "erreurs": erreurs}

    total = sum(l["total"] for l in lignes)
    ref = db.base().inserer_commande({"psid": params.get("psid", ""), "nom": nom, "telephone": tel,
                                      "mode_livraison": mode, "quartier": quartier, "lignes": lignes, "total": total})
    return {"ok": True, "reference": ref, "total": total, "lignes": lignes, "telephone": tel}
