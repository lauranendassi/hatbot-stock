# -*- coding: utf-8 -*-
"""Skill : qualifier_besoin — règle du patron : comprendre le besoin AVANT de proposer
ou de donner un prix. Une seule question à la fois.

params :
    profil        (dict) profil client (usages, budget_max, modele_pc, nb_demandes_prix, derniere_question…)
    categorie     (str|None) catégorie concernée (PC portable, Écran, Composant…)
    produit_cite  (str|None) code d'un produit nommé précisément par le client
retour :
    {"ok": True, "qualifie": bool, "champ": "usage"|"budget"|"modele_pc"|None,
     "question": str|None, "manque": [...], "raison": str}

Règles :
  - Ordinateurs et écrans : il faut l'USAGE, puis le BUDGET.
    Exception : usage connu + modèle précis cité -> qualifié (on peut donner son prix).
  - Composants / accessoires : il faut le MODÈLE DE L'ORDINATEUR (compatibilité) ; pas de budget.
  - Si le client redemande le prix une 2e fois malgré la question, on le lui donne
    (insister davantage serait désagréable) : raison = "insistance".
"""
from __future__ import annotations

import random

from catalogue import sans_accents

QUESTIONS = {
    "usage": [
        "Avec plaisir ! Pour bien vous conseiller, vous allez surtout l'utiliser pour quoi "
        "(bureautique, études, gestion, graphisme, programmation…) ?",
        "Bien sûr ! Pour vous orienter vers le bon modèle, à quoi va-t-il servir au quotidien ?",
        "Volontiers. C'est pour quel usage principalement : travail de bureau, études, création, autre chose ?",
    ],
    "usage_ecran": [
        "Avec plaisir ! L'écran, c'est plutôt pour le bureau, pour un deuxième écran, ou pour la maison ?",
    ],
    "budget": [
        "Très bien, je vois le profil. Vous avez une idée du budget que vous souhaitez y mettre ?",
        "Parfait. Et côté budget, vous pensez à environ combien, pour que je vous propose ce qui convient vraiment ?",
        "D'accord ! Quel budget maximum vous êtes-vous fixé ? Ça m'aide à choisir le meilleur rapport qualité-prix.",
    ],
    "modele_pc": [
        "Bien sûr ! C'est pour quel modèle d'ordinateur ? Je vérifie que la pièce est compatible.",
        "Pour être sûr de la compatibilité, quel est le modèle exact de votre ordinateur (il est souvent écrit sous la machine) ?",
    ],
}
CATEGORIES_PIECES = {"composant", "accessoire"}
CATEGORIES_ECRAN = {"ecran"}


def executer(params: dict) -> dict:
    profil = params.get("profil") or {}
    categorie = sans_accents(params.get("categorie") or "")
    produit_cite = params.get("produit_cite")
    insistance = int(profil.get("nb_demandes_prix", 0)) >= 2

    if categorie in CATEGORIES_PIECES:
        manque = [] if profil.get("modele_pc") else ["modele_pc"]
    else:
        manque = []
        if not profil.get("usages"):
            manque.append("usage")
        budget_connu = profil.get("budget_max") or profil.get("budget_ouvert")
        if not budget_connu and not (produit_cite and profil.get("usages")):
            manque.append("budget")

    if not manque:
        return {"ok": True, "qualifie": True, "champ": None, "question": None, "manque": [], "raison": "complet"}
    if insistance:
        return {"ok": True, "qualifie": True, "champ": None, "question": None, "manque": manque, "raison": "insistance"}

    champ = manque[0]
    cle = "usage_ecran" if champ == "usage" and categorie in CATEGORIES_ECRAN else champ
    choix = [q for q in QUESTIONS[cle] if q != profil.get("derniere_question")] or QUESTIONS[cle]
    return {"ok": True, "qualifie": False, "champ": champ, "question": random.choice(choix), "manque": manque,
            "raison": "a_qualifier"}
