# -*- coding: utf-8 -*-
"""Accès au catalogue public (jamais de prix d'achat ici).

Source : table `stock_produits` de Neon, alimentée toutes les 5 min depuis les fichiers
Excel du magasin par scripts/sync.py. Si Neon est injoignable : catalogue_secours.json
(copie écrite par la même synchronisation)."""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata

import config
import db

log = logging.getLogger("bot.catalogue")
_cache = {"t": 0.0, "produits": [], "source": ""}
DUREE_CACHE = 60


def sans_accents(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def _secours():
    try:
        data = json.loads(config.CATALOGUE_SECOURS.read_text(encoding="utf-8"))
        return data.get("produits", [])
    except Exception as e:
        log.error("Catalogue de secours illisible : %s", e)
        return []


def produits(force: bool = False) -> list[dict]:
    if not force and _cache["produits"] and time.time() - _cache["t"] < DUREE_CACHE:
        return _cache["produits"]
    liste, source = None, "neon"
    try:
        liste = db.base().produits()
    except Exception as e:
        log.warning("Neon indisponible pour le catalogue (%s) : bascule sur le catalogue de secours", e)
    if not liste:
        liste, source = _secours(), "secours"
    for p in liste:
        p["_texte"] = sans_accents(" ".join(str(p.get(k, "")) for k in
                                            ("code", "designation", "categorie", "marque", "usages", "description")))
    _cache.update(t=time.time(), produits=liste, source=source)
    return liste


def source() -> str:
    produits()
    return _cache["source"]


def par_code(code: str) -> dict | None:
    code = (code or "").upper().strip()
    return next((p for p in produits() if p["code"].upper() == code), None)


def categories() -> list[str]:
    return sorted({p["categorie"] for p in produits()})


def disponible(p: dict) -> bool:
    return float(p.get("stock") or 0) > 0
