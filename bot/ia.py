# -*- coding: utf-8 -*-
"""Appel au modèle Groq (gratuit), avec chaîne de secours :
gpt-oss-120b  ->  gpt-oss-20b  ->  None (le bot passe alors en réponses préparées)."""
from __future__ import annotations

import logging
import re
import time

import requests

import config

log = logging.getLogger("bot.ia")
_session = requests.Session()
_pause_jusqua: dict[str, float] = {}       # modèle -> heure de fin de pause (limite de débit atteinte)
DERNIERE_ERREUR: str | None = None


def _appel(modele: str, systeme: str, messages: list[dict], max_tokens: int, avec_effort: bool) -> str | None:
    corps = {"model": modele, "temperature": 0.5, "max_completion_tokens": max_tokens,
             "messages": [{"role": "system", "content": systeme}] + messages}
    if avec_effort:
        corps["reasoning_effort"] = "low"          # gpt-oss : réfléchit peu, répond vite, consomme moins
    r = _session.post(config.GROQ_URL, json=corps, timeout=config.IA_TIMEOUT,
                      headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
    if r.status_code == 400 and avec_effort and "reasoning" in r.text:
        return _appel(modele, systeme, messages, max_tokens, avec_effort=False)
    if r.status_code == 429:
        attente = float(r.headers.get("retry-after", "20") or 20)
        _pause_jusqua[modele] = time.time() + min(attente, 120)
        raise RuntimeError(f"limite de débit Groq ({modele}), pause {attente:.0f}s")
    if r.status_code >= 400:
        raise RuntimeError(f"Groq {r.status_code} : {r.text[:200]}")
    contenu = (r.json()["choices"][0]["message"].get("content") or "").strip()
    return nettoyer(contenu) or None


def nettoyer(texte: str) -> str:
    """Messenger n'affiche pas le Markdown : on retire **gras**, titres #, tableaux."""
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.S)
    texte = re.sub(r"\*\*(.+?)\*\*", r"\1", texte)
    texte = re.sub(r"(?m)^#{1,6}\s*", "", texte)
    texte = re.sub(r"(?m)^\s*\|.*\|\s*$", "", texte)
    texte = re.sub(r"(?m)^\s*[-*]\s+", "• ", texte)
    return re.sub(r"\n{3,}", "\n\n", texte).strip()


def generer(systeme: str, messages: list[dict], max_tokens: int = 700) -> str | None:
    global DERNIERE_ERREUR
    if not config.GROQ_API_KEY:
        DERNIERE_ERREUR = "GROQ_API_KEY absente"
        log.warning("GROQ_API_KEY absente : réponses préparées uniquement")
        return None
    for modele in (config.MODELE_PRINCIPAL, config.MODELE_SECOURS):
        if _pause_jusqua.get(modele, 0) > time.time():
            continue
        debut = time.perf_counter()
        try:
            texte = _appel(modele, systeme, messages, max_tokens, avec_effort=True)
            if texte:
                log.info("IA %s : %.1fs", modele, time.perf_counter() - debut)
                DERNIERE_ERREUR = None
                return texte
            log.warning("IA %s : réponse vide", modele)
        except (requests.RequestException, RuntimeError, KeyError, ValueError) as e:
            log.warning("IA %s en échec : %s", modele, e)
            DERNIERE_ERREUR = f"{modele} : {e}"[:300]
    return None
