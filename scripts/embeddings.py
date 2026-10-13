# -*- coding: utf-8 -*-
"""Embeddings pour la recherche sémantique (même fichier côté PC et côté bot).
provider : voyage | openai | none. Le fournisseur, le modèle et la dimension
doivent être IDENTIQUES à l'indexation (PC) et à la recherche (bot)."""
from __future__ import annotations

import time

import requests

MODELES_PAR_DEFAUT = {"voyage": "voyage-3.5", "openai": "text-embedding-3-small"}


def actif(provider: str, cle: str) -> bool:
    return provider in MODELES_PAR_DEFAUT and bool(cle)


def vectoriser(textes: list[str], provider: str, cle: str, modele: str = "", dimension: int = 1024,
               type_entree: str = "document") -> list[list[float]]:
    """type_entree : 'document' à l'indexation, 'query' pour une question."""
    if not textes:
        return []
    modele = modele or MODELES_PAR_DEFAUT[provider]
    resultats: list[list[float]] = []
    for i in range(0, len(textes), 64):
        lot = [t[:8000] for t in textes[i:i + 64]]
        if provider == "voyage":
            url = "https://api.voyageai.com/v1/embeddings"
            corps = {"input": lot, "model": modele, "input_type": type_entree, "output_dimension": dimension}
        else:
            url = "https://api.openai.com/v1/embeddings"
            corps = {"input": lot, "model": modele, "dimensions": dimension}
        for essai in range(4):
            rep = requests.post(url, json=corps, headers={"Authorization": f"Bearer {cle}"}, timeout=60)
            if rep.status_code == 429 or rep.status_code >= 500:
                time.sleep(2 ** essai * 2)
                continue
            rep.raise_for_status()
            donnees = sorted(rep.json()["data"], key=lambda d: d["index"])
            resultats.extend(d["embedding"] for d in donnees)
            break
        else:
            rep.raise_for_status()
    return resultats


def en_litteral_pgvector(vecteur: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vecteur) + "]"
