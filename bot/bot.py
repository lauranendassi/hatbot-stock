# -*- coding: utf-8 -*-
"""
Chatbot Messenger WeloobeAI — v4 (RAG + skills + machine à états)

Routes :
  GET  /webhook   vérification par Meta (hub.challenge)
  POST /webhook   messages Messenger (réponse 200 immédiate, traitement en arrière-plan)
  GET  /sante     état du service (pour Render et un « ping » anti-veille)
  POST /test      essai sans Messenger : {"psid": "...", "texte": "..."} -> réponses
                  (protégé par l'en-tête X-Test-Token = TEST_TOKEN)

Démarrage local :  python bot.py        Production (Render) :  gunicorn bot:app (voir Procfile)
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request

import config

logging.basicConfig(level=getattr(logging, config.LOG_NIVEAU.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("bot")

import catalogue  # noqa: E402  (après la configuration des logs)
import conversation  # noqa: E402
import db  # noqa: E402
import messenger  # noqa: E402

app = Flask(__name__)
executeur = ThreadPoolExecutor(max_workers=4)
_verrous: dict[str, threading.Lock] = {}
_verrous_global = threading.Lock()

try:
    db.base().initialiser()
except Exception:
    log.exception("Initialisation de la base impossible au démarrage (nouvel essai au premier message)")
log.info("Bot démarré | modèles %s / %s | catalogue : %d produits (%s) | instructions : %d caractères",
         config.MODELE_PRINCIPAL, config.MODELE_SECOURS, len(catalogue.produits()), catalogue.source(),
         len(conversation.INSTRUCTIONS))


def verrou(psid: str) -> threading.Lock:
    """Un message à la fois par client, dans l'ordre d'arrivée."""
    with _verrous_global:
        return _verrous.setdefault(psid, threading.Lock())


def traiter_evenement(ev: dict):
    psid = ev.get("sender", {}).get("id")
    if not psid:
        return
    message = ev.get("message") or {}

    # Écho d'un message envoyé par la Page : s'il ne vient pas du bot, un conseiller a pris la main
    if message.get("is_echo"):
        if message.get("metadata") != messenger.METADATA_BOT and message.get("text"):
            client = ev.get("recipient", {}).get("id")
            if client:
                with verrou(client):
                    conv = db.base().conversation(client)
                    conv["etat"] = "HUMAIN"
                    conv["handoff_jusqua"] = (dt.datetime.now(dt.timezone.utc)
                                              + dt.timedelta(hours=config.HANDOFF_HEURES)).isoformat()
                    db.base().enregistrer_conversation(conv)
                    db.base().ajouter_message(client, "assistant", f"[Conseiller humain] {message['text']}")
                log.info("[%s] un conseiller humain a répondu : bot en pause", client[-6:])
        return

    mid = message.get("mid") or (ev.get("postback") or {}).get("mid")
    if mid and not db.base().marquer_traite(mid):
        log.info("Message %s déjà traité (renvoi de Meta) : ignoré", mid[-8:])
        return

    if "postback" in ev:                                     # bouton « Démarrer » ou bouton de menu
        texte = ev["postback"].get("title") or ev["postback"].get("payload") or "Bonjour"
    elif message.get("quick_reply"):
        texte = message.get("text") or message["quick_reply"].get("payload", "")
    elif message.get("text"):
        texte = message["text"]
    elif message.get("attachments"):
        types = {p.get("type") for p in message["attachments"]}
        texte = "[Le client a envoyé une image]" if "image" in types else "[Le client a envoyé une pièce jointe]"
    else:
        return

    with verrou(psid):
        messenger.indicateur_frappe(psid, True)
        try:
            conv = db.base().conversation(psid)
            if not conv["profil"].get("prenom") and not conv["profil"].get("prenom_cherche"):
                conv["profil"]["prenom"] = messenger.prenom(psid)
                conv["profil"]["prenom_cherche"] = True
                db.base().enregistrer_conversation(conv)
            reponses = conversation.traiter(psid, texte)
        except Exception:
            log.exception("[%s] erreur de traitement", psid[-6:])
            reponses = ["Oups, j'ai eu un petit souci technique. Pouvez-vous répéter votre message ? "
                        "Si ça persiste, écrivez « conseiller » et une personne de l'équipe vous répondra."]
        for r in reponses:
            messenger.envoyer_texte(psid, r)
        messenger.indicateur_frappe(psid, False)


@app.get("/webhook")
def verification():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == config.VERIFY_TOKEN:
        log.info("Webhook vérifié par Meta")
        return request.args.get("hub.challenge", ""), 200
    return "Jeton de vérification incorrect", 403


@app.post("/webhook")
def webhook():
    corps = request.get_data()
    if not messenger.signature_valide(corps, request.headers.get("X-Hub-Signature-256")):
        log.warning("Signature Meta invalide : appel refusé")
        return "signature invalide", 403
    data = request.get_json(silent=True) or {}
    if data.get("object") != "page":
        return "ignoré", 200
    for entree in data.get("entry", []):
        for ev in entree.get("messaging", []):
            executeur.submit(traiter_evenement, ev)
    return "EVENT_RECEIVED", 200                                 # Meta exige une réponse rapide


@app.get("/sante")
def sante():
    base = db.base()
    return jsonify({"statut": "ok", "heure": dt.datetime.now().isoformat(timespec="seconds"),
                    "catalogue": {"produits": len(catalogue.produits()), "source": catalogue.source()},
                    "derniere_synchro_excel": getattr(base, "derniere_synchro", lambda: None)(),
                    "base": "neon" if config.DATABASE_URL else "memoire",
                    "ia": bool(config.GROQ_API_KEY)})


@app.get("/")
def accueil():
    return "Chatbot WeloobeAI v4 en ligne. Santé : /sante", 200


@app.post("/test")
def test():
    jeton = os.environ.get("TEST_TOKEN", "")
    if not jeton or request.headers.get("X-Test-Token") != jeton:
        return "interdit", 403
    data = request.get_json(force=True)
    psid = str(data.get("psid", "test-utilisateur"))
    with verrou(psid):
        reponses = conversation.traiter(psid, data.get("texte", ""))
    conv = db.base().conversation(psid)
    return jsonify({"reponses": reponses, "etat": conv["etat"], "profil": conv["profil"]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
