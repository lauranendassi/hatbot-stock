# -*- coding: utf-8 -*-
"""Skill : verifier_stock — disponibilité d'un produit pour une quantité donnée.

params : code (str), quantite (int, défaut 1)
retour : {"ok", "code", "designation", "stock", "disponible", "suffisant", "statut", "phrase"}
"""
from __future__ import annotations

import catalogue


def executer(params: dict) -> dict:
    p = catalogue.par_code(params.get("code", ""))
    if not p:
        return {"ok": False, "erreur": "produit introuvable"}
    quantite = max(1, int(params.get("quantite") or 1))
    stock = float(p.get("stock") or 0)
    seuil = float(p.get("seuil") or 0)
    if stock <= 0:
        phrase = "en rupture pour le moment"
    elif stock < quantite:
        phrase = f"il n'en reste que {stock:g}, pas assez pour {quantite}"
    elif stock <= seuil:
        phrase = f"disponible, mais il n'en reste plus que {stock:g}"
    else:
        phrase = "disponible en boutique"
    return {"ok": True, "code": p["code"], "designation": p["designation"], "stock": stock,
            "disponible": stock > 0, "suffisant": stock >= quantite,
            "statut": "Rupture" if stock <= 0 else ("Stock faible" if stock <= seuil else "Disponible"),
            "phrase": phrase}
