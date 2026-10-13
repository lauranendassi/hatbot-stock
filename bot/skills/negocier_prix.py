# -*- coding: utf-8 -*-
"""Skill : negocier_prix — négociation encadrée, décidée PAR LE CODE (jamais par l'IA).

Le prix plancher (colonne « Prix minimum » ou calcul automatique sur le PC) arrive dans
Neon (stock_produits.prix_min). Il n'est JAMAIS transmis à l'IA ni au client.

Stratégie (3 paliers, comme un bon vendeur) :
    palier 1 = prix - 40 % de la marge de négociation
    palier 2 = prix - 70 % de la marge de négociation
    palier 3 = prix plancher (dernier mot)
  - Offre du client >= palier en cours  -> on accepte SON offre (arrondie).
  - Sinon -> contre-offre au palier en cours, palier suivant au prochain tour.
  - Au palier 3, si l'offre reste en dessous -> prix final, on ne bouge plus.
  - Sans montant (« faites un effort ») -> on propose le palier en cours.
  - Pas de plancher connu (Neon indisponible) -> prix ferme.

params :
    code    (str)
    offre   (float|None)  montant proposé par le client
    etat    (dict)        état de négociation précédent pour ce produit (renvoyé par ce skill)
retour :
    {"ok", "decision": "accepte" | "contre_offre" | "dernier_prix" | "prix_ferme" | "deja_au_prix" | "deja_moins_cher",
     "prix": prix à annoncer, "prix_catalogue", "remise", "etat": nouvel état, "offre_trop_basse": bool}
"""
from __future__ import annotations

import math

import catalogue


def _arrondi_bas(v: float, pas: int) -> float:
    return math.floor(v / pas) * pas


def executer(params: dict) -> dict:
    p = catalogue.par_code(params.get("code", ""))
    if not p:
        return {"ok": False, "erreur": "produit inconnu"}
    prix = float(p["prix_vente"])
    plancher = p.get("prix_min")
    plancher = float(plancher) if plancher not in (None, "") else prix
    plancher = min(plancher, prix)
    etat = dict(params.get("etat") or {})
    tour = int(etat.get("tour", 0))
    offre = params.get("offre")
    offre = float(offre) if offre else None

    base = {"ok": True, "prix_catalogue": prix, "designation": p["designation"], "code": p["code"]}

    if etat.get("prix_accorde"):                                  # déjà conclu : on ne rouvre pas
        accorde = float(etat["prix_accorde"])
        return {**base, "decision": "deja_au_prix", "prix": accorde, "remise": prix - accorde, "etat": etat,
                "offre_trop_basse": bool(offre and offre < accorde)}
    if offre and offre >= prix:
        return {**base, "decision": "deja_moins_cher" if offre > prix else "accepte", "prix": prix, "remise": 0,
                "etat": {**etat, "prix_accorde": prix}}

    dernier = etat.get("dernier_propose")
    if offre and dernier and offre >= float(dernier):            # le client accepte (ou dépasse) notre proposition
        return {**base, "decision": "accepte", "prix": float(dernier), "remise": prix - float(dernier),
                "etat": {**etat, "tour": tour + 1, "prix_accorde": float(dernier)}}

    marge = prix - plancher
    if marge <= 0:
        return {**base, "decision": "prix_ferme", "prix": prix, "remise": 0, "etat": etat,
                "offre_trop_basse": bool(offre)}

    pas = 5000 if prix >= 100000 else 1000
    paliers = []
    for frac in (0.4, 0.7):
        v = max(_arrondi_bas(prix - frac * marge, pas), plancher)
        if not paliers or v < paliers[-1]:
            paliers.append(v)
    if not paliers or paliers[-1] > plancher:
        paliers.append(plancher)
    palier = paliers[min(tour, len(paliers) - 1)]
    trop_basse = bool(offre and offre < plancher * 0.9)

    if offre and offre >= palier:
        accorde = max(_arrondi_bas(offre, 500), plancher)
        return {**base, "decision": "accepte", "prix": accorde, "remise": prix - accorde,
                "etat": {**etat, "tour": tour + 1, "prix_accorde": accorde}}
    if tour >= len(paliers) - 1 or palier == plancher and tour >= len(paliers) - 1:
        return {**base, "decision": "dernier_prix", "prix": plancher, "remise": prix - plancher,
                "etat": {**etat, "tour": tour + 1, "dernier_propose": plancher}, "offre_trop_basse": trop_basse}
    decision = "dernier_prix" if palier == plancher else "contre_offre"
    return {**base, "decision": decision, "prix": palier, "remise": prix - palier,
            "etat": {**etat, "tour": tour + 1, "dernier_propose": palier}, "offre_trop_basse": trop_basse}
