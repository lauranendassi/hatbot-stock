# -*- coding: utf-8 -*-
"""Registre des skills. Chaque skill est un module autonome qui expose :

    def executer(params: dict) -> dict

Le résultat contient toujours la clé "ok" (bool). Une exception dans un skill
ne fait jamais tomber le bot : elle est journalisée et renvoyée comme {"ok": False}.

Utilisation :
    from skills import executer
    executer("verifier_stock", {"code": "LAP-001", "quantite": 2})
"""
from __future__ import annotations

import importlib
import logging
import time

log = logging.getLogger("bot.skills")

NOMS = [
    "rechercher_produit",
    "qualifier_besoin",
    "verifier_stock",
    "proposer_alternatives",
    "enregistrer_commande",
    "notifier_email",
    "envoyer_photo",
    "verifier_permissions",
    "negocier_prix",
]


def executer(nom: str, params: dict | None = None) -> dict:
    if nom not in NOMS:
        return {"ok": False, "erreur": f"skill inconnu : {nom}"}
    debut = time.perf_counter()
    try:
        module = importlib.import_module(f"skills.{nom}")
        resultat = module.executer(dict(params or {}))
    except Exception as e:                                   # un skill ne doit jamais faire tomber le bot
        log.exception("Skill %s en erreur", nom)
        resultat = {"ok": False, "erreur": str(e)[:300]}
    log.debug("skill %s (%.0f ms) -> ok=%s", nom, (time.perf_counter() - debut) * 1000, resultat.get("ok"))
    return resultat
