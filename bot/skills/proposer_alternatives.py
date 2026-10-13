# -*- coding: utf-8 -*-
"""Skill : proposer_alternatives — équivalents disponibles quand un produit est en rupture,
hors budget, ou pour offrir un choix au client.

params :
    code        (str)   produit de référence (facultatif)
    categorie   (str)   sinon, catégorie visée
    budget_max  (float) facultatif
    usages      (list)  facultatif
    exclure     (list)  codes à ne pas proposer
    limite      (int, défaut 2)
retour : {"ok", "produits": [...], "reference": code|None}
"""
from __future__ import annotations

import catalogue
from catalogue import sans_accents


def executer(params: dict) -> dict:
    ref = catalogue.par_code(params.get("code", "")) if params.get("code") else None
    categorie = (ref or {}).get("categorie") or params.get("categorie")
    prix_ref = float(ref["prix_vente"]) if ref else None
    budget = params.get("budget_max")
    usages = params.get("usages") or []
    exclure = set(params.get("exclure") or []) | ({ref["code"]} if ref else set())

    candidats = []
    for p in catalogue.produits():
        if p["code"] in exclure or not catalogue.disponible(p):
            continue
        if categorie and p["categorie"] != categorie:
            continue
        prix = float(p["prix_vente"])
        if budget and prix > budget * 1.05:
            continue
        cible = prix_ref or budget or prix
        score = -abs(prix - cible) / max(cible, 1)
        texte = sans_accents(p.get("usages", ""))
        score += 0.3 * sum(1 for u in usages if u[:5] in texte)
        candidats.append((score, p))
    candidats.sort(key=lambda x: -x[0])
    limite = int(params.get("limite", 2))
    return {"ok": True, "reference": ref["code"] if ref else None,
            "produits": [{k: p.get(k) for k in ("code", "designation", "categorie", "prix_vente", "stock", "usages")}
                         for _, p in candidats[:limite]]}
