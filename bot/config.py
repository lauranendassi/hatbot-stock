# -*- coding: utf-8 -*-
"""Configuration du bot : tout vient des variables d'environnement (Render > Environment).
En local, un fichier .env à côté de bot.py est lu automatiquement."""
from __future__ import annotations

import os
from pathlib import Path

ICI = Path(__file__).resolve().parent


def _charger_env_local():
    f = ICI / ".env"
    if not f.exists():
        return
    for ligne in f.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#") and "=" in ligne:
            cle, _, val = ligne.partition("=")
            os.environ.setdefault(cle.strip(), val.strip().strip('"').strip("'"))


_charger_env_local()


def env(cle: str, defaut: str = "") -> str:
    return os.environ.get(cle, defaut).strip()


# --- Magasin
NOM_MAGASIN = env("NOM_MAGASIN", "WeloobeAI")
NOM_BOT = env("NOM_BOT", "l'assistant WeloobeAI")

# --- Base Neon (vide = mode mémoire, pour les tests locaux)
DATABASE_URL = env("DATABASE_URL")

# --- Messenger
PAGE_ACCESS_TOKEN = env("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = env("VERIFY_TOKEN", "weloobe_verify")
APP_SECRET = env("APP_SECRET")                       # vérifie la signature des appels Meta
GRAPH_VERSION = env("GRAPH_VERSION", "v26.0")
MESSENGER_DRY_RUN = env("MESSENGER_DRY_RUN", "0") == "1"   # 1 = n'envoie rien, écrit dans les logs
LONGUEUR_MAX_MESSAGE = 1900

# --- IA (Groq, gratuit)
GROQ_API_KEY = env("GROQ_API_KEY")
GROQ_URL = env("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
MODELE_PRINCIPAL = env("MODELE_PRINCIPAL", "openai/gpt-oss-120b")
MODELE_SECOURS = env("MODELE_SECOURS", "openai/gpt-oss-20b")
IA_TIMEOUT = int(env("IA_TIMEOUT", "25"))

# --- Mémoire et conversation
MEMOIRE_MESSAGES = int(env("MEMOIRE_MESSAGES", "30"))
HANDOFF_HEURES = float(env("HANDOFF_HEURES", "12"))   # durée de silence du bot après transfert à un humain

# --- Rôles : codes à taper dans Messenger « /connexion CODE »
CODE_VENDEUR = env("CODE_VENDEUR")
CODE_ADMIN = env("CODE_ADMIN")
CODE_BOSS = env("CODE_BOSS")

# --- Emails (Brevo : API HTTP gratuite, fonctionne sur Render gratuit qui bloque le SMTP)
BREVO_API_KEY = env("BREVO_API_KEY")
EMAIL_EXPEDITEUR = env("EMAIL_EXPEDITEUR")           # adresse validée dans Brevo
EMAIL_DESTINATAIRES = [e.strip() for e in env("EMAIL_DESTINATAIRES").split(",") if e.strip()]
EMAIL_COPIE = [e.strip() for e in env("EMAIL_COPIE").split(",") if e.strip()]   # Martial
# SMTP (uniquement en local ou sur une offre Render payante)
SMTP_HOTE = env("SMTP_HOTE")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_UTILISATEUR = env("SMTP_UTILISATEUR")
SMTP_MOT_DE_PASSE = env("SMTP_MOT_DE_PASSE")

# --- Divers
LOG_NIVEAU = env("LOG_NIVEAU", "INFO")
INSTRUCTIONS_FICHIER = ICI / env("INSTRUCTIONS_FICHIER", "instructions.md")
CATALOGUE_SECOURS = ICI / "catalogue_secours.json"
