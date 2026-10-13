# -*- coding: utf-8 -*-
"""Skill : envoyer_photo — envoie la photo d'un produit dans Messenger.

params : psid (str), code (str)
retour : {"ok", "envoye": bool, "raison"?: "pas_de_photo" | "produit_inconnu" | "echec_envoi"}

La photo vient de la colonne « Photo (URL) » du fichier Gestion_stock_corrige.xlsx
(lien public https vers une image : hébergement d'images, page Facebook, Google Drive public…).
"""
from __future__ import annotations

import catalogue
import messenger


def executer(params: dict) -> dict:
    p = catalogue.par_code(params.get("code", ""))
    if not p:
        return {"ok": False, "envoye": False, "raison": "produit_inconnu"}
    url = (p.get("photo_url") or "").strip()
    if not url.startswith("https://"):
        return {"ok": False, "envoye": False, "raison": "pas_de_photo", "designation": p["designation"]}
    if not messenger.envoyer_image(params.get("psid", ""), url):
        return {"ok": False, "envoye": False, "raison": "echec_envoi", "designation": p["designation"]}
    return {"ok": True, "envoye": True, "designation": p["designation"]}
