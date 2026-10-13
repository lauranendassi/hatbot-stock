# -*- coding: utf-8 -*-
"""
INSTALLATION — à lancer UNE SEULE FOIS (ou avec --force pour tout recréer).

Crée, à partir de Gestion_stock_corrige.xlsx (15 produits) :
  - Stocks_vendeur.xlsx   (catalogue + saisie des ventes, partagé par les 6 vendeurs)
  - Achats_2026.xlsx      (saisie des arrivages, admin)
  - Ventes_2026_Mois.xlsx (12 mois + récap, généré)
  - Stocks_admin.xlsx     (vue complète, chiffrée : weloobe_admin)

Les fichiers existants ne sont JAMAIS écrasés sans --force ; avec --force,
une copie horodatée est faite dans _sauvegardes\\ avant remplacement.

Usage :
    python scripts\\init_systeme.py            # crée ce qui manque
    python scripts\\init_systeme.py --force    # recrée tout (avec sauvegarde)
    python scripts\\init_systeme.py --demo     # crée aussi un fichier source de démonstration
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys

from openpyxl import Workbook

from common import (DOSSIER_DATA, DOSSIER_SAUV, F_SAISIE_ACHATS, F_SAISIE_VENTES, ouvrir_classeur,
                    charger_config, charger_produits, calculer_stocks, enregistrer_classeur)
from classeurs import creer_fichier_achats, creer_fichier_vendeur, entetes, largeurs

# Catalogue réel du magasin (15 produits). PRIX ESTIMÉS pour la démonstration :
# le vrai fichier Gestion_stock_corrige.xlsx fait foi.
PRODUITS_DEMO = [
    # code, désignation, catégorie, marque, PA, PV, stock, seuil, usages
    ("LAP-001", "Dell Latitude 5420 (Core i5 11e gén., 16 Go RAM, SSD 256 Go, 14 pouces)", "PC portable", "Dell", 230000, 295000, 6, 2, "bureautique, études, gestion, télétravail"),
    ("LAP-002", "Dell Latitude 7430 (Core i7 12e gén., 16 Go RAM, SSD 512 Go, 14 pouces)", "PC portable", "Dell", 380000, 485000, 3, 1, "développement, multitâche intensif, cadres, déplacements"),
    ("LAP-003", "HP EliteBook 840 G8 (Core i5 11e gén., 16 Go RAM, SSD 512 Go, 14 pouces)", "PC portable", "HP", 260000, 330000, 5, 2, "bureautique avancée, gestion, études supérieures, entreprise"),
    ("LAP-004", "HP ProBook 450 G8 (Core i5 11e gén., 8 Go RAM, SSD 256 Go, 15,6 pouces)", "PC portable", "HP", 220000, 280000, 5, 2, "bureautique, études, grand écran, petit budget"),
    ("LAP-005", "Lenovo ThinkPad T14 Gen 2 (Core i5 11e gén., 16 Go RAM, SSD 512 Go, 14 pouces)", "PC portable", "Lenovo", 250000, 320000, 4, 1, "développement, gestion, robustesse, entreprise"),
    ("LAP-006", "Apple MacBook Air M1 (8 Go RAM, SSD 256 Go, 13 pouces)", "PC portable", "Apple", 520000, 675000, 2, 1, "graphisme, montage vidéo léger, autonomie, création"),
    ("DES-001", "Dell OptiPlex 7090 SFF (Core i5 10e gén., 8 Go RAM, SSD 256 Go)", "PC fixe", "Dell", 190000, 245000, 4, 1, "bureau, secrétariat, caisse, entreprise"),
    ("DES-002", "HP ProDesk 400 G7 (Core i5 10e gén., 8 Go RAM, SSD 256 Go)", "PC fixe", "HP", 170000, 220000, 4, 1, "bureau, secrétariat, cybercafé, école"),
    ("ECR-001", "Écran Dell P2422H 24 pouces Full HD IPS", "Écran", "Dell", 85000, 110000, 6, 2, "bureau, double écran, confort visuel"),
    ("ECR-002", "Écran HP E24 G4 24 pouces Full HD IPS", "Écran", "HP", 80000, 105000, 5, 2, "bureau, double écran, entreprise"),
    ("ECR-003", "Écran Samsung S24R350 24 pouces Full HD", "Écran", "Samsung", 62000, 82000, 8, 2, "maison, études, petit budget"),
    ("COMP-001", "SSD Kingston 512 Go SATA", "Composant", "Kingston", 22000, 32000, 15, 4, "accélérer un vieux PC, plus de stockage"),
    ("COMP-002", "Barrette RAM Crucial 16 Go DDR4", "Composant", "Crucial", 25000, 35000, 12, 3, "plus de mémoire, PC lent en multitâche"),
    ("COMP-003", "Batterie pour Dell Latitude 5420", "Composant", "Dell", 28000, 40000, 6, 2, "remplacement batterie Latitude 5420"),
    ("ACC-001", "Chargeur Dell USB-C 65 W", "Accessoire", "Dell", 18000, 30000, 10, 3, "chargeur Dell USB-C de remplacement ou de secours"),
]


def creer_source_demo(chemin):
    wb = Workbook()
    ws = wb.active
    ws.title = "Produits"
    entetes(ws, 1, ["Code", "Désignation", "Catégorie", "Marque", "Prix d'achat", "Prix de vente",
                    "Stock initial", "Seuil d'alerte", "Usages", "Photo (URL)"])
    largeurs(ws, [10, 62, 13, 10, 13, 13, 11, 11, 45, 40])
    for i, p in enumerate(PRODUITS_DEMO, start=2):
        for c, v in enumerate(p, start=1):
            ws.cell(row=i, column=c, value=v)
    wb.save(chemin)


def format_actuel(chemin, cfg) -> bool:
    """Vrai si le classeur a déjà la structure de ce système (feuilles de saisie attendues)."""
    attendu = F_SAISIE_VENTES if chemin == cfg.f_vendeur else F_SAISIE_ACHATS
    try:
        wb = ouvrir_classeur(chemin, cfg.mdp_admin, read_only=True)
        ok = attendu in wb.sheetnames
        wb.close()
        return ok
    except Exception:
        return False


def sauvegarder(chemin, suffixe=""):
    if chemin.exists():
        DOSSIER_SAUV.mkdir(exist_ok=True)
        dest = DOSSIER_SAUV / f"{chemin.stem}{'_' + suffixe if suffixe else ''}_{dt.datetime.now():%Y%m%d_%H%M%S}{chemin.suffix}"
        shutil.copy2(chemin, dest)
        print(f"   sauvegarde : {dest.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="recrée les fichiers (avec sauvegarde)")
    ap.add_argument("--demo", action="store_true", help="crée un fichier source de démonstration s'il manque")
    args = ap.parse_args()
    cfg = charger_config()
    DOSSIER_DATA.mkdir(exist_ok=True)

    if not cfg.f_source.exists():
        if args.demo:
            creer_source_demo(cfg.f_source)
            print(f"✔ Fichier source de démonstration créé : {cfg.f_source.name}")
        else:
            sys.exit(
                f"\n✘ Le fichier des produits « {cfg.f_source.name} » n'est pas dans ce dossier :\n"
                f"     {cfg.f_source.parent}\n\n"
                "  Copiez votre Gestion_stock_corrige.xlsx (les 15 produits) dans ce dossier,\n"
                "  puis relancez installer.bat.\n"
                "  (Pour un simple essai avec des produits fictifs : python scripts\\init_systeme.py --demo)")

    produits = charger_produits(cfg)
    print(f"✔ {len(produits)} produits lus dans {cfg.f_source.name}")
    etats = calculer_stocks(produits, [], [])
    maj = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

    for chemin, fabrique in ((cfg.f_vendeur, lambda: creer_fichier_vendeur(cfg, produits, etats, maj)),
                             (cfg.f_achats, lambda: creer_fichier_achats(cfg, produits))):
        if chemin.exists() and not args.force:
            if format_actuel(chemin, cfg):
                print(f"• {chemin.name} existe déjà (inchangé — utilisez --force pour le recréer)")
                continue
            print(f"• {chemin.name} est un ancien fichier (autre structure) : mis de côté puis recréé")
            sauvegarder(chemin, "ancien_format")
            if chemin == cfg.f_vendeur:
                (DOSSIER_DATA / "journal_ventes.json").unlink(missing_ok=True)
        if args.force:
            sauvegarder(chemin)
            if chemin == cfg.f_vendeur:
                (DOSSIER_DATA / "journal_ventes.json").unlink(missing_ok=True)
        enregistrer_classeur(fabrique(), chemin)
        print(f"✔ {chemin.name} créé")

    if not (DOSSIER_DATA / "etat_sync.json").exists():          # 1re installation : on garde les anciens
        sauvegarder(cfg.f_ventes, "avant_installation")
        sauvegarder(cfg.f_admin, "avant_installation")
    elif args.force:
        sauvegarder(cfg.f_ventes)
        sauvegarder(cfg.f_admin)
        (DOSSIER_DATA / "etat_sync.json").unlink(missing_ok=True)

    # Le premier passage de synchronisation génère Ventes + Admin (chiffré)
    import sync
    sync.synchroniser(forcer=True)
    print("✔ Installation terminée. Programmez maintenant la tâche (installer_tache.bat).")


if __name__ == "__main__":
    main()
