# -*- coding: utf-8 -*-
"""Discuter avec le bot dans le terminal, sans Messenger.

    python simuler.py                 # client
    python simuler.py --psid patron   # autre conversation (tapez /connexion CODE pour changer de rôle)

Utilise .env (Groq, Neon) s'il existe ; sinon mode mémoire + réponses préparées.
"""
import argparse
import os

os.environ.setdefault("MESSENGER_DRY_RUN", "1")
os.environ.setdefault("LOG_NIVEAU", "WARNING")

import config  # noqa: E402
import logging  # noqa: E402

logging.basicConfig(level=getattr(logging, config.LOG_NIVEAU), format="%(levelname)s %(name)s | %(message)s")
import conversation  # noqa: E402
import db  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--psid", default="simulateur-client")
args = ap.parse_args()
try:
    db.base().initialiser()
except Exception as e:
    print("Base :", e)
print("Tapez vos messages (Ctrl+C pour quitter).\n")
while True:
    try:
        texte = input("Vous : ")
    except (KeyboardInterrupt, EOFError):
        break
    for r in conversation.traiter(args.psid, texte):
        print(f"\nBot : {r}\n")
    conv = db.base().conversation(args.psid)
    print(f"   [état={conv['etat']} | profil: usages={conv['profil'].get('usages')} "
          f"budget={conv['profil'].get('budget_max')} focus={conv['profil'].get('produit_focus')}]\n")
