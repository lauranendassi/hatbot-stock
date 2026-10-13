# -*- coding: utf-8 -*-
"""Skill : verifier_permissions — qui a le droit de faire quoi.

Rôles : client < vendeur < admin < boss
  client  : catalogue, prix de vente (après qualification), commande, SAV
  vendeur : + alertes de stock, statut du stock exact
  admin   : + ventes, chiffre d'affaires, marges, commandes reçues
  boss    : + gestion des rôles, rapports

params :
    action (str)  une clé de ACTIONS, ou "role_depuis_code"
    psid   (str)  pour lire le rôle enregistré
    role   (str)  alternative à psid
    code   (str)  pour action="role_depuis_code" : code tapé après /connexion
retour : {"ok", "autorise", "role", "niveau_rag"}  ou  {"ok", "role"} pour role_depuis_code
"""
from __future__ import annotations

import hmac

import config
import db

ORDRE = ["client", "vendeur", "admin", "boss"]
ACTIONS = {
    "voir_catalogue": "client",
    "commander": "client",
    "voir_alertes_stock": "vendeur",
    "voir_stock_exact": "vendeur",
    "voir_ventes": "admin",
    "voir_marges": "admin",
    "voir_prix_achat": "admin",
    "voir_commandes": "admin",
    "gerer_roles": "boss",
}
NIVEAU_RAG = {"client": 0, "vendeur": 1, "admin": 2, "boss": 2}   # niveaux des fiches de stock_rag_documents


def role_de(psid: str) -> str:
    try:
        r = db.base().role(psid)
    except Exception:
        r = "client"
    return r if r in ORDRE else "client"


def executer(params: dict) -> dict:
    action = params.get("action", "")
    if action == "role_depuis_code":
        code = (params.get("code") or "").strip()
        for role, attendu in (("boss", config.CODE_BOSS), ("admin", config.CODE_ADMIN), ("vendeur", config.CODE_VENDEUR)):
            if attendu and code and hmac.compare_digest(code, attendu):
                return {"ok": True, "role": role}
        return {"ok": False, "role": None}

    role = params.get("role") or (role_de(params["psid"]) if params.get("psid") else "client")
    if action not in ACTIONS:
        return {"ok": False, "autorise": False, "role": role, "erreur": f"action inconnue : {action}"}
    autorise = ORDRE.index(role) >= ORDRE.index(ACTIONS[action])
    return {"ok": True, "autorise": autorise, "role": role, "niveau_rag": NIVEAU_RAG[role]}
