# -*- coding: utf-8 -*-
"""
SURVEILLANCE EN CONTINU (invisible) — lancée automatiquement à l'ouverture de Windows.

Toutes les 10 secondes, regarde si un fichier de saisie a été enregistré
(Gestion_stock_corrige.xlsx, Stocks_vendeur.xlsx, Achats_AAAA.xlsx, connaissances\\*.md).
Si oui, attend que l'enregistrement soit terminé puis lance la synchronisation :
la vente apparaît partout environ 10 à 20 secondes après Ctrl + S.

Toutes les 5 minutes, synchronise aussi « par sécurité » (changement de jour, fichier admin
fermé entre-temps, nouvel essai vers Neon après une coupure internet).

Une seule surveillance tourne à la fois (si on la lance deux fois, la 2e s'arrête seule).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DOSSIER_CONNAISSANCES, DOSSIER_DATA, RACINE, charger_config, init_logs  # noqa: E402

INTERVALLE = 10          # secondes entre deux vérifications
STABILITE = 3            # secondes sans changement avant de synchroniser (enregistrement terminé)
SECURITE = 300           # synchronisation de sécurité toutes les 5 minutes


def fichiers_surveilles(cfg) -> list[Path]:
    liste = [cfg.f_source, cfg.f_vendeur, cfg.f_achats, RACINE / "config.ini"]
    if DOSSIER_CONNAISSANCES.exists():
        liste += sorted(DOSSIER_CONNAISSANCES.glob("*.md"))
    return liste


def verrous_lecture(cfg) -> list[Path]:
    """Fichiers « ~$ » d'Excel des classeurs générés : leur disparition = classeur refermé,
    on peut alors y écrire la mise à jour qui attendait."""
    return [f.with_name("~$" + f.name) for f in (cfg.f_admin, cfg.f_ventes)]


def empreinte(fichiers, verrous=()) -> tuple:
    res = [(v.name, v.exists()) for v in verrous]
    for f in fichiers:
        try:
            st = f.stat()
            res.append((f.name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            res.append((f.name, None, None))
    return tuple(res)


def instance_unique():
    """Empêche deux surveillances en parallèle (fichier verrou tenu ouvert tant que le programme tourne)."""
    DOSSIER_DATA.mkdir(exist_ok=True)
    chemin = DOSSIER_DATA / "surveillance.lock"
    try:
        if os.name == "nt":
            import msvcrt
            fh = open(chemin, "a+")
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fh = open(chemin, "a+")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        return None


def lancer_sync(log, motif):
    import sync
    try:
        log.info("Synchronisation (%s)", motif)
        sync.synchroniser()
    except RuntimeError as e:                 # « déjà en cours » (tâche planifiée en même temps)
        log.info(str(e))
    except Exception:
        log.exception("Erreur pendant la synchronisation")


def main():
    log = init_logs()
    verrou = instance_unique()
    if verrou is None:
        log.info("Surveillance déjà active : cette instance s'arrête.")
        return
    cfg = charger_config()
    log.info("Surveillance démarrée : mise à jour automatique après chaque enregistrement.")
    precedente = empreinte(fichiers_surveilles(cfg), verrous_lecture(cfg))
    derniere_sync = 0.0
    lancer_sync(log, "démarrage")
    derniere_sync = time.time()
    while True:
        time.sleep(INTERVALLE)
        actuelle = empreinte(fichiers_surveilles(cfg), verrous_lecture(cfg))
        if actuelle != precedente:
            time.sleep(STABILITE)                         # laisser Excel finir d'écrire
            actuelle = empreinte(fichiers_surveilles(cfg), verrous_lecture(cfg))
            changes = [a[0] for a, b in zip(actuelle, precedente) if a != b] or ["fichiers"]
            precedente = actuelle
            cfg = charger_config()
            lancer_sync(log, "enregistrement de " + ", ".join(changes))
            derniere_sync = time.time()
        elif time.time() - derniere_sync >= SECURITE:
            lancer_sync(log, "contrôle régulier")
            derniere_sync = time.time()


if __name__ == "__main__":
    main()
