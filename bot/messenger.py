# -*- coding: utf-8 -*-
"""Envoi vers Facebook Messenger (Send API, Graph API).
Découpe automatiquement les messages à 1900 caractères maximum."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re

import requests

import config

log = logging.getLogger("bot.messenger")
_session = requests.Session()
METADATA_BOT = "weloobe-bot"     # permet de distinguer nos messages de ceux d'un conseiller humain (échos)


def _url():
    return f"https://graph.facebook.com/{config.GRAPH_VERSION}/me/messages"


def signature_valide(corps: bytes, entete: str | None) -> bool:
    """Vérifie que l'appel vient bien de Meta (en-tête X-Hub-Signature-256)."""
    if not config.APP_SECRET:
        return True                      # non configuré : accepté (à éviter en production)
    if not entete or not entete.startswith("sha256="):
        return False
    attendu = hmac.new(config.APP_SECRET.encode(), corps, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, entete[7:])


def decouper(texte: str, maxi: int = config.LONGUEUR_MAX_MESSAGE) -> list[str]:
    """Coupe aux paragraphes, puis aux phrases, puis aux mots, sans jamais dépasser maxi."""
    texte = texte.strip()
    if len(texte) <= maxi:
        return [texte] if texte else []
    unites: list[tuple[str, str]] = []                 # (texte, séparateur à placer avant)
    for para in re.split(r"\n\s*\n", texte):
        for j, phrase in enumerate(re.split(r"(?<=[.!?])\s+", para.strip())):
            if phrase:
                unites.append((phrase, "\n\n" if j == 0 else " "))
    morceaux, courant = [], ""
    for bloc, sep in unites:
        while len(bloc) > maxi:                         # phrase géante : coupe aux mots
            coupe = bloc.rfind(" ", 0, maxi)
            coupe = coupe if coupe > 0 else maxi
            if courant:
                morceaux.append(courant)
                courant = ""
            morceaux.append(bloc[:coupe].strip())
            bloc = bloc[coupe:].strip()
        if courant and len(courant) + len(sep) + len(bloc) <= maxi:
            courant += sep + bloc
        else:
            if courant:
                morceaux.append(courant)
            courant = bloc
    if courant:
        morceaux.append(courant)
    return morceaux


def _envoyer(psid: str, charge: dict) -> bool:
    corps = {"recipient": {"id": psid}, "messaging_type": "RESPONSE", **charge}
    if config.MESSENGER_DRY_RUN or not config.PAGE_ACCESS_TOKEN:
        log.info("[SIMULATION Messenger → %s] %s", psid[-6:], str(charge)[:500])
        return True
    try:
        r = _session.post(_url(), params={"access_token": config.PAGE_ACCESS_TOKEN}, json=corps, timeout=15)
        if r.status_code != 200:
            log.error("Messenger %s : %s", r.status_code, r.text[:300])
            return False
        return True
    except requests.RequestException as e:
        log.error("Messenger injoignable : %s", e)
        return False


def envoyer_texte(psid: str, texte: str) -> bool:
    ok = True
    for morceau in decouper(texte):
        ok = _envoyer(psid, {"message": {"text": morceau, "metadata": METADATA_BOT}}) and ok
    return ok


def envoyer_image(psid: str, url: str) -> bool:
    return _envoyer(psid, {"message": {"attachment": {"type": "image", "payload": {"url": url, "is_reusable": True}}}})


def indicateur_frappe(psid: str, actif: bool = True):
    if config.MESSENGER_DRY_RUN or not config.PAGE_ACCESS_TOKEN:
        return
    try:
        _session.post(_url(), params={"access_token": config.PAGE_ACCESS_TOKEN}, timeout=5,
                      json={"recipient": {"id": psid}, "sender_action": "typing_on" if actif else "typing_off"})
    except requests.RequestException:
        pass


def prenom(psid: str) -> str | None:
    """Prénom du client (si la page a l'autorisation). Silencieux en cas d'échec."""
    if config.MESSENGER_DRY_RUN or not config.PAGE_ACCESS_TOKEN:
        return None
    try:
        r = _session.get(f"https://graph.facebook.com/{config.GRAPH_VERSION}/{psid}",
                         params={"fields": "first_name", "access_token": config.PAGE_ACCESS_TOKEN}, timeout=5)
        return r.json().get("first_name") if r.status_code == 200 else None
    except Exception:
        return None
