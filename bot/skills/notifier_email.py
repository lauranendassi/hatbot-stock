# -*- coding: utf-8 -*-
"""Skill : notifier_email — prévient l'équipe par email (Martial toujours en copie).

params :
    type     (str)  commande | humain | sav | plainte | rapport | autre
    sujet    (str)
    contenu  (str)  texte brut (converti en HTML simple)
retour : {"ok", "canal": "brevo" | "smtp" | "simulation", "destinataires": [...]}

Canaux, dans l'ordre :
  1. Brevo (API HTTP, gratuite 300 emails/jour) — fonctionne sur Render gratuit
  2. SMTP — uniquement en local ou sur une offre Render payante (Render gratuit bloque
     les ports SMTP 25/465/587 depuis septembre 2025)
  3. simulation — rien n'est configuré : l'email est écrit dans les logs
"""
from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

import requests

import config

log = logging.getLogger("bot.email")
PREFIXES = {"commande": "🛒 Nouvelle commande", "humain": "🙋 Client à rappeler", "sav": "🔧 Demande SAV",
            "plainte": "⚠️ Client mécontent", "rapport": "📊 Rapport", "autre": "ℹ️ Information"}


def executer(params: dict) -> dict:
    type_ = params.get("type", "autre")
    sujet = f"[{config.NOM_MAGASIN}] {PREFIXES.get(type_, PREFIXES['autre'])} — {params.get('sujet', '')}".strip(" —")
    contenu = params.get("contenu", "")
    corps_html = ("<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.5'>"
                  + html.escape(contenu).replace("\n", "<br>")
                  + "<p style='color:#888;font-size:12px'>Message automatique du chatbot Messenger.</p></div>")
    dest, copie = config.EMAIL_DESTINATAIRES, [c for c in config.EMAIL_COPIE if c not in config.EMAIL_DESTINATAIRES]
    if not dest and copie:
        dest, copie = copie, []

    if dest and config.BREVO_API_KEY and config.EMAIL_EXPEDITEUR:
        try:
            r = requests.post("https://api.brevo.com/v3/smtp/email", timeout=15,
                              headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"},
                              json={"sender": {"email": config.EMAIL_EXPEDITEUR, "name": config.NOM_MAGASIN},
                                    "to": [{"email": e} for e in dest],
                                    **({"cc": [{"email": e} for e in copie]} if copie else {}),
                                    "subject": sujet, "htmlContent": corps_html, "textContent": contenu})
            if r.status_code in (200, 201, 202):
                log.info("Email envoyé (Brevo) : %s", sujet)
                return {"ok": True, "canal": "brevo", "destinataires": dest + copie}
            log.error("Brevo %s : %s", r.status_code, r.text[:300])
        except requests.RequestException as e:
            log.error("Brevo injoignable : %s", e)

    if dest and config.SMTP_HOTE and config.EMAIL_EXPEDITEUR:
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = sujet, config.EMAIL_EXPEDITEUR, ", ".join(dest)
            if copie:
                msg["Cc"] = ", ".join(copie)
            msg.set_content(contenu)
            msg.add_alternative(corps_html, subtype="html")
            with smtplib.SMTP(config.SMTP_HOTE, config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                if config.SMTP_UTILISATEUR:
                    s.login(config.SMTP_UTILISATEUR, config.SMTP_MOT_DE_PASSE)
                s.send_message(msg)
            log.info("Email envoyé (SMTP) : %s", sujet)
            return {"ok": True, "canal": "smtp", "destinataires": dest + copie}
        except Exception as e:
            log.error("SMTP en échec : %s", e)

    log.warning("[SIMULATION EMAIL] à %s cc %s | %s\n%s", dest or "(aucun)", copie or "-", sujet, contenu)
    return {"ok": not (config.BREVO_API_KEY or config.SMTP_HOTE), "canal": "simulation", "destinataires": dest + copie}
