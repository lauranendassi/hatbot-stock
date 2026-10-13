# -*- coding: utf-8 -*-
"""Environnement de test : aucune base, aucun appel réseau (IA et Messenger simulés)."""
import os
import sys
from pathlib import Path

os.environ.update({"DATABASE_URL": "", "MESSENGER_DRY_RUN": "1", "GROQ_API_KEY": "", "BREVO_API_KEY": "",
                   "SMTP_HOTE": "", "CODE_VENDEUR": "v123", "CODE_ADMIN": "a123", "CODE_BOSS": "b123",
                   "EMAIL_DESTINATAIRES": "patron@exemple.cm", "EMAIL_COPIE": "martial@exemple.cm"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import catalogue  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

# Catalogue figé pour les tests : les prix réels peuvent changer sans casser les tests
config.CATALOGUE_SECOURS = Path(__file__).resolve().parent / "catalogue_test.json"


@pytest.fixture(autouse=True)
def base_neuve():
    db.remplacer_base(db.BaseMemoire())
    catalogue.produits(force=True)
    yield


@pytest.fixture
def ia_factice(monkeypatch):
    """Remplace Groq : renvoie un texte qui montre ce que l'IA a reçu."""
    import ia
    appels = []

    def generer(systeme, messages, max_tokens=700):
        appels.append({"systeme": systeme, "messages": messages})
        return appels_reponse[0] if appels_reponse else "Réponse IA de test."
    appels_reponse = []
    monkeypatch.setattr(ia, "generer", generer)
    return {"appels": appels, "reponses": appels_reponse}


@pytest.fixture
def emails(monkeypatch):
    envoyes = []
    import skills.notifier_email as ne
    original = ne.executer
    monkeypatch.setattr(ne, "executer", lambda p: (envoyes.append(p), original(p))[1])
    return envoyes
