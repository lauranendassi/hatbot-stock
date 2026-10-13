# -*- coding: utf-8 -*-
"""Skill : rechercher_produit — trouve les produits qui correspondent à une demande.

params :
    texte              (str)   message du client (« un dell latitude », « ecran 24 pouces »…)
    code               (str)   code exact (prioritaire)
    categorie          (str)   catégorie imposée
    usages             (list)  usages détectés (bureautique, etudes, graphisme…)
    budget_max         (float) budget maximum en FCFA
    budget_min         (float)
    disponibles_seulement (bool, défaut True)
    limite             (int, défaut 3)
retour :
    {"ok", "produits": [...], "produit_cite": code|None, "categorie": str|None}
    produit_cite = produit nommé précisément par le client (modèle reconnu)
"""
from __future__ import annotations

import difflib
import re

import catalogue
from catalogue import sans_accents

CATEGORIES_MOTS = {
    "pc portable": r"portable|laptop|notebook|macbook|ultrabook|\bpc\b(?! fixe| de bureau)|ordinateur(?! fixe| de bureau)",
    "pc fixe": r"\bfixe\b|de bureau|unite centrale|\bdesktop\b|\btour\b|\boptiplex\b|\bprodesk\b",
    "ecran": r"ecran|moniteur|\bmonitor\b|\b2[2-7] ?pouces\b",
    "composant": r"\bssd\b|\bram\b|memoire|barrette|batterie|disque|stockage|\bpiece",
    "accessoire": r"chargeur|souris|clavier|cable|accessoire|sacoche|adaptateur",
}
MOTS_VIDES = {"pour", "de", "du", "la", "le", "les", "des", "avec", "et", "en", "au", "aux", "un", "une"}
MOTS_PIECES = ("batterie", "chargeur", "barrette", "ssd", "ecran")
MOTS_GENERIQUES = {"core", "gen", "ram", "ssd", "pouces", "pouce", "full", "ips", "go", "sff", "ecran", "pour",
                   "barrette", "sata", "ddr4", "batterie", "chargeur", "apple", "gene", "dell", "hp", "lenovo",
                   "samsung", "kingston", "crucial", "ordinateur"}


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", sans_accents(s)) if len(t) >= 2]


def detecter_categorie(texte: str) -> str | None:
    t = sans_accents(texte)
    noms = {sans_accents(c): c for c in catalogue.categories()}
    for cle, motif in CATEGORIES_MOTS.items():
        if re.search(motif, t):
            for norm, vrai in noms.items():
                if cle == norm or cle in norm or norm in cle:
                    return vrai
    return None


def _score_nom(p: dict, mots_client: list[str], frequence: dict[str, int]) -> float:
    """Correspondance avec le nom du modèle. Les mots rares (latitude, 5420, macbook…) comptent beaucoup."""
    score = 0.0
    for tok in set(_tokens(p["designation"] + " " + p.get("marque", ""))):
        if tok in MOTS_VIDES:
            continue
        if tok in MOTS_GENERIQUES and not tok.isdigit():
            poids = 0.3
        else:
            poids = 2.0 if frequence.get(tok, 0) <= 2 else 0.6
        if tok in mots_client:
            score += poids
        elif len(tok) >= 5 and not tok.isdigit() and difflib.get_close_matches(tok, mots_client, n=1, cutoff=0.8):
            score += poids * 0.8                            # fautes de frappe : « latitud », « elitbook »
    return score


def _score_usage(p: dict, usages: list[str]) -> float:
    if not usages:
        return 0.0
    texte = sans_accents(p.get("usages", "") + " " + p["designation"])
    equivalences = {
        "bureautique": ["bureautique", "bureau", "secretariat"], "etudes": ["etudes", "etudiant", "ecole"],
        "gestion": ["gestion", "caisse", "entreprise"], "developpement": ["developpement", "multitache", "16 go"],
        "graphisme": ["graphisme", "montage", "creation", "macbook"], "entreprise": ["entreprise", "bureau", "cadres"],
        "maison": ["maison", "budget", "etudes"], "deplacement": ["deplacement", "autonomie", "leger", "14 pouces"],
        "gaming": ["gaming"],
    }
    return sum(1.0 for u in usages for mot in equivalences.get(u, [u]) if mot in texte)


def executer(params: dict) -> dict:
    produits = catalogue.produits()
    if not produits:
        return {"ok": False, "erreur": "catalogue vide", "produits": []}
    texte = params.get("texte", "") or ""
    limite = int(params.get("limite", 3))
    dispo_seul = params.get("disponibles_seulement", True)

    if params.get("code"):
        p = catalogue.par_code(params["code"])
        return {"ok": bool(p), "produits": [_public(p)] if p else [], "produit_cite": p["code"] if p else None,
                "categorie": p["categorie"] if p else None}

    mots = _tokens(texte)
    frequence: dict[str, int] = {}
    for p in produits:
        for tok in set(_tokens(p["designation"] + " " + p.get("marque", ""))):
            frequence[tok] = frequence.get(tok, 0) + 1

    categorie = params.get("categorie") or detecter_categorie(texte)
    budget_max = params.get("budget_max")
    budget_min = params.get("budget_min")
    usages = params.get("usages") or []

    notes = []
    for p in produits:
        nom = _score_nom(p, mots, frequence) if mots else 0.0
        notes.append((p, nom))
    meilleur_nom = max((n for _, n in notes), default=0)
    cite = None
    if meilleur_nom >= 1.6:          # un mot rare du modèle, même avec une faute de frappe
        candidats = [p for p, n in notes if n == meilleur_nom]
        if len(candidats) > 1:      # « latitude 5420 » : le PC plutôt que sa batterie, sauf si le client dit « batterie »
            texte_norm = sans_accents(texte)
            sans_piece = [p for p in candidats                       # le 1er mot de la désignation = type de pièce
                          if not any(sans_accents(p["designation"]).startswith(m) and m not in texte_norm
                                     for m in MOTS_PIECES)]
            candidats = sans_piece or candidats
        if len(candidats) == 1:
            cite = candidats[0]["code"]
            categorie = categorie or candidats[0]["categorie"]

    resultats = []
    for p, nom in notes:
        if categorie and p["categorie"] != categorie and p["code"] != cite:
            continue
        if dispo_seul and not catalogue.disponible(p) and p["code"] != cite:
            continue
        prix = float(p["prix_vente"])
        if budget_max and prix > budget_max * 1.05 and p["code"] != cite:
            continue
        score = nom * 3 + _score_usage(p, usages)
        if budget_max:
            score += 1 - abs(budget_max - prix) / budget_max     # au plus près du budget, sans le dépasser
        if budget_min and prix < budget_min * 0.9:
            score -= 1
        if catalogue.disponible(p):
            score += 0.5
        resultats.append((score, p))
    resultats.sort(key=lambda x: -x[0])
    return {"ok": True, "produits": [_public(p) for _, p in resultats[:limite]], "produit_cite": cite,
            "categorie": categorie}


def _public(p: dict) -> dict:
    return {k: p.get(k) for k in ("code", "designation", "categorie", "marque", "prix_vente", "stock", "statut",
                                  "usages", "description", "photo_url")}
