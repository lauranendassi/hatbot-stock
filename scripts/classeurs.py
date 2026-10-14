# -*- coding: utf-8 -*-
"""
Construction des classeurs Excel (compatibles Excel 2016 : SUMIF, VLOOKUP,
IFERROR, MATCH uniquement — aucune fonction 365, aucune macro).
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from common import (F_LISTES, normaliser, F_CATALOGUE, F_PARAMS, F_PRODUITS_ACHATS, F_SAISIE_ACHATS, F_SAISIE_VENTES,
                    JOURS_FR, LIGNE_DEBUT_CATALOGUE, LIGNE_DEBUT_SAISIE, MOIS_FR, Achat, Anomalie,
                    Config, EtatStock, Produit, Vente)

# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------
BLEU = "1F3864"
BLEU_CLAIR = "D9E2F3"
GRIS = "F2F2F2"
VERT = "C6EFCE"
ORANGE = "FFEB9C"
ROUGE = "FFC7CE"
JAUNE_SAISIE = "FFF9E5"

MOIS_COURTS = ["Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]
FMT_FCFA = '#,##0" FCFA"'
FMT_NB = "#,##0"
FMT_PCT = "0.0%"
FMT_DATE = "dd/mm/yyyy"

fin = Side(style="thin", color="BFBFBF")
BORD = Border(left=fin, right=fin, top=fin, bottom=fin)


def titre(ws, cellule, texte, sous_titre=None, largeur=8):
    ws[cellule] = texte
    ws[cellule].font = Font(size=16, bold=True, color=BLEU)
    if sous_titre:
        c = ws.cell(row=ws[cellule].row + 1, column=ws[cellule].column, value=sous_titre)
        c.font = Font(italic=True, color="595959", size=10)


def entetes(ws, ligne, valeurs, col0=1, couleur=BLEU):
    for i, v in enumerate(valeurs):
        c = ws.cell(row=ligne, column=col0 + i, value=v)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=couleur)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORD
    ws.row_dimensions[ligne].height = 30


def largeurs(ws, tailles):
    for i, t in enumerate(tailles, start=1):
        ws.column_dimensions[get_column_letter(i)].width = t


def cellule(ws, r, c, v, fmt=None, gras=False, fond=None, align=None):
    x = ws.cell(row=r, column=c, value=v)
    x.border = BORD
    if fmt:
        x.number_format = fmt
    if gras:
        x.font = Font(bold=True)
    if fond:
        x.fill = PatternFill("solid", fgColor=fond)
    if align:
        x.alignment = Alignment(horizontal=align, vertical="center")
    return x


def proteger(ws, mdp):
    ws.protection.sheet = True
    ws.protection.password = mdp
    ws.protection.formatCells = False
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.protection.autoFilter = False
    ws.protection.sort = False


def definir_nom(wb, nom, ref):
    if nom in wb.defined_names:
        del wb.defined_names[nom]
    wb.defined_names[nom] = DefinedName(nom, attr_text=ref)


def regles_statut(ws, plage):
    ws.conditional_formatting.add(plage, CellIsRule(operator="equal", formula=['"Rupture"'],
                                                    fill=PatternFill("solid", fgColor=ROUGE), font=Font(color="9C0006", bold=True)))
    ws.conditional_formatting.add(plage, CellIsRule(operator="equal", formula=['"Stock faible"'],
                                                    fill=PatternFill("solid", fgColor=ORANGE), font=Font(color="9C5700")))
    ws.conditional_formatting.add(plage, CellIsRule(operator="equal", formula=['"Disponible"'],
                                                    fill=PatternFill("solid", fgColor=VERT), font=Font(color="006100")))


# ==========================================================================
# 1. Stocks_vendeur.xlsx — catalogue + saisie des ventes (fichier partagé)
# ==========================================================================
def ecrire_catalogue_vendeur(wb, cfg: Config, produits: list[Produit], etats: dict[str, EtatStock], maj: str):
    """(Ré)écrit la feuille Catalogue et la feuille Paramètres. La saisie n'est jamais touchée."""
    fin_saisie = LIGNE_DEBUT_SAISIE + cfg.lignes_ventes - 1
    if F_CATALOGUE in wb.sheetnames:
        ws = wb[F_CATALOGUE]
        if ws.max_row >= LIGNE_DEBUT_CATALOGUE:
            ws.delete_rows(LIGNE_DEBUT_CATALOGUE, ws.max_row)
        ws.conditional_formatting = type(ws.conditional_formatting)()
    else:
        ws = wb.create_sheet(F_CATALOGUE, 0)
        titre(ws, "A1", f"Catalogue — {cfg.nom_magasin}")
        entetes(ws, 4, ["Code", "Désignation", "Catégorie", "Marque", "Prix de vente",
                        "Stock initial", "Entrées (achats)", "Vendus (saisie)", "Stock disponible",
                        "Seuil d'alerte", "Statut"])
        largeurs(ws, [12, 40, 18, 14, 16, 11, 12, 12, 13, 10, 14])
        ws.freeze_panes = "C5"
    ws["A2"] = (f"Stock mis à jour automatiquement. Achats intégrés le {maj}. "
                "Les ventes saisies sont déduites instantanément.")
    ws["A2"].font = Font(italic=True, color="595959", size=10)

    r = LIGNE_DEBUT_CATALOGUE
    for p in produits:
        e = etats[p.code]
        cellule(ws, r, 1, p.code, gras=True)
        cellule(ws, r, 2, p.designation)
        cellule(ws, r, 3, p.categorie)
        cellule(ws, r, 4, p.marque)
        cellule(ws, r, 5, p.prix_vente, FMT_FCFA)
        cellule(ws, r, 6, p.stock_initial, FMT_NB)
        cellule(ws, r, 7, e.entrees, FMT_NB)
        cellule(ws, r, 8, f"=SUMIF({F_SAISIE_VENTES}!$D${LIGNE_DEBUT_SAISIE}:$D${fin_saisie},$A{r},"
                          f"{F_SAISIE_VENTES}!$E${LIGNE_DEBUT_SAISIE}:$E${fin_saisie})", FMT_NB)
        cellule(ws, r, 9, f"=F{r}+G{r}-H{r}", FMT_NB, gras=True, align="center")
        cellule(ws, r, 10, p.seuil, FMT_NB)
        cellule(ws, r, 11, f'=IF(I{r}<=0,"Rupture",IF(I{r}<=J{r},"Stock faible","Disponible"))', align="center")
        r += 1
    derniere = max(r - 1, LIGNE_DEBUT_CATALOGUE)
    regles_statut(ws, f"K{LIGNE_DEBUT_CATALOGUE}:K{derniere}")
    ws.auto_filter.ref = f"A4:K{derniere}"
    proteger(ws, cfg.mdp_feuilles)

    # Paramètres (masquée) : liste des vendeurs
    if F_PARAMS in wb.sheetnames:
        wp = wb[F_PARAMS]
        wp.delete_rows(1, wp.max_row + 1)
    else:
        wp = wb.create_sheet(F_PARAMS)
    wp["A1"] = "Vendeurs"
    for i, v in enumerate(cfg.vendeurs, start=2):
        wp.cell(row=i, column=1, value=v)
    wp.sheet_state = "hidden"
    proteger(wp, cfg.mdp_feuilles)

    # Liste triée des désignations pour la liste déroulante « filtrante » de la saisie
    if F_LISTES in wb.sheetnames:
        wl = wb[F_LISTES]
        wl.delete_rows(1, wl.max_row + 1)
    else:
        wl = wb.create_sheet(F_LISTES)
    wl["A1"], wl["B1"] = "Désignation (triée)", "Code"
    tries = sorted(produits, key=lambda p: normaliser(p.designation))
    for i, p in enumerate(tries, start=2):
        wl.cell(row=i, column=1, value=p.designation)
        wl.cell(row=i, column=2, value=p.code)
    wl.column_dimensions["A"].width = 70
    wl.sheet_state = "hidden"
    proteger(wl, cfg.mdp_feuilles)
    n_des = max(len(tries), 1) + 1
    definir_nom(wb, "Liste_Des", f"{F_LISTES}!$A$2:$A${n_des}")
    definir_nom(wb, "Liste_Des_Codes", f"{F_LISTES}!$B$2:$B${n_des}")
    definir_nom(wb, "Liste_Des_Debut", f"{F_LISTES}!$A$2")

    definir_nom(wb, "Cat_Codes", f"{F_CATALOGUE}!$A${LIGNE_DEBUT_CATALOGUE}:$A${derniere}")
    definir_nom(wb, "Cat_Table", f"{F_CATALOGUE}!$A${LIGNE_DEBUT_CATALOGUE}:$K${derniere}")
    definir_nom(wb, "Liste_Vendeurs", f"{F_PARAMS}!$A$2:$A${len(cfg.vendeurs) + 1}")


# Liste déroulante « filtrante » (Excel 2016, sans macro) : si la cellule contient le début d'un nom,
# la liste ne montre que les produits qui commencent par ce texte ; vide = tous les produits.
FORMULE_LISTE_FILTREE = ('OFFSET(Liste_Des_Debut,IF(COUNTIF(Liste_Des,$C{r}&"*")=0,0,MATCH($C{r}&"*",Liste_Des,0)-1),0,'
                         'IF(COUNTIF(Liste_Des,$C{r}&"*")=0,ROWS(Liste_Des),COUNTIF(Liste_Des,$C{r}&"*")),1)')


def creer_saisie_ventes(wb, cfg: Config):
    ws = wb.create_sheet(F_SAISIE_VENTES)
    fin_saisie = LIGNE_DEBUT_SAISIE + cfg.lignes_ventes - 1
    titre(ws, "A1", "Saisie des ventes",
          "Cellules jaunes : Date, Produit (liste déroulante), Quantité, Prix pratiqué si remise, Vendeur, Client. "
          "Le code SKU et les prix se remplissent seuls. Recherche : tapez le début du nom, Entrée, puis ouvrez la liste.")
    entetes(ws, 4, ["N°", "Date", "Produit (choisir dans la liste)", "Code SKU (auto)", "Quantité", "Prix catalogue",
                    "Prix pratiqué (si remise)", "Total", "Vendeur", "Client (facultatif)", "Contrôle"])
    largeurs(ws, [6, 12, 58, 13, 9, 15, 15, 16, 14, 22, 17])
    ws.freeze_panes = "B5"

    jaune = PatternFill("solid", fgColor=JAUNE_SAISIE)
    gris = PatternFill("solid", fgColor=GRIS)
    ouvert = Protection(locked=False)
    for r in range(LIGNE_DEBUT_SAISIE, fin_saisie + 1):
        ws.cell(row=r, column=1, value=f'=IF(C{r}="","",ROW()-{LIGNE_DEBUT_SAISIE - 1})')
        x = ws.cell(row=r, column=4, value=f'=IF(C{r}="","",IFERROR(INDEX(Liste_Des_Codes,MATCH(C{r},Liste_Des,0)),"?"))')
        x.fill = gris
        x.font = Font(bold=True, color=BLEU)
        ws.cell(row=r, column=6, value=f'=IF(OR(D{r}="",D{r}="?"),"",IFERROR(VLOOKUP(D{r},Cat_Table,5,FALSE),""))').number_format = FMT_FCFA
        ws.cell(row=r, column=8, value=f'=IF(OR(F{r}="",E{r}=""),"",E{r}*IF(G{r}<>"",G{r},N(F{r})))').number_format = FMT_FCFA
        ws.cell(row=r, column=11, value=(
            f'=IF(C{r}="","",IF(D{r}="?","Choisir dans la liste",IF(B{r}="","Date manquante",IF(E{r}="","Quantité manquante",'
            f'IF(I{r}="","Vendeur manquant",IF(VLOOKUP(D{r},Cat_Table,9,FALSE)<0,"Stock insuffisant","OK"))))))'))
        for c in (2, 3, 5, 7, 9, 10):
            x = ws.cell(row=r, column=c)
            x.fill = jaune
            x.protection = ouvert
        ws.cell(row=r, column=2).number_format = FMT_DATE
        ws.cell(row=r, column=7).number_format = FMT_FCFA

    plage = lambda col: f"{col}{LIGNE_DEBUT_SAISIE}:{col}{fin_saisie}"
    # Pas de message d'erreur bloquant sur le produit : il faut pouvoir taper le début du nom pour filtrer.
    # La colonne Contrôle signale « Choisir dans la liste » tant qu'un vrai produit n'est pas choisi.
    dv_prod = DataValidation(type="list", formula1=FORMULE_LISTE_FILTREE.format(r=LIGNE_DEBUT_SAISIE),
                             allow_blank=True, showErrorMessage=False, showInputMessage=True,
                             promptTitle="Produit",
                             prompt="Ouvrez la liste, ou tapez le début du nom (ex. Dell), Entrée, puis rouvrez la liste.")
    dv_vend = DataValidation(type="list", formula1="Liste_Vendeurs", allow_blank=True, showErrorMessage=True,
                             errorTitle="Vendeur", error="Choisissez votre nom dans la liste.")
    dv_date = DataValidation(type="date", operator="between", formula1="DATE(2020,1,1)", formula2="DATE(2035,12,31)",
                             allow_blank=True, showErrorMessage=True, errorTitle="Date",
                             error="Tapez une date au format jj/mm/aaaa (raccourci : Ctrl+; pour aujourd'hui).")
    dv_qte = DataValidation(type="whole", operator="greaterThan", formula1="0", allow_blank=True,
                            showErrorMessage=True, errorTitle="Quantité", error="La quantité doit être un nombre entier positif.")
    dv_prix = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True,
                             showErrorMessage=True, errorTitle="Prix", error="Montant en FCFA, sans texte.")
    for dv, col in ((dv_prod, "C"), (dv_vend, "I"), (dv_date, "B"), (dv_qte, "E"), (dv_prix, "G")):
        ws.add_data_validation(dv)
        dv.add(plage(col))

    ws.conditional_formatting.add(plage("K"), CellIsRule(operator="equal", formula=['"OK"'],
                                  fill=PatternFill("solid", fgColor=VERT), font=Font(color="006100")))
    ws.conditional_formatting.add(plage("K"), FormulaRule(formula=[f'AND(K{LIGNE_DEBUT_SAISIE}<>"",K{LIGNE_DEBUT_SAISIE}<>"OK")'],
                                  fill=PatternFill("solid", fgColor=ROUGE), font=Font(color="9C0006", bold=True)))
    ws.conditional_formatting.add(plage("C"), FormulaRule(formula=[f'$D{LIGNE_DEBUT_SAISIE}="?"'],
                                  fill=PatternFill("solid", fgColor=ORANGE)))
    proteger(ws, cfg.mdp_feuilles)
    return ws


def creer_mode_emploi(wb, titre_feuille, lignes):
    ws = wb.create_sheet("Mode d'emploi")
    ws.column_dimensions["A"].width = 110
    ws["A1"] = titre_feuille
    ws["A1"].font = Font(size=16, bold=True, color=BLEU)
    for i, l in enumerate(lignes, start=3):
        c = ws.cell(row=i, column=1, value=l)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if l and l[0].isdigit():
            c.font = Font(bold=True)
    return ws


def creer_fichier_vendeur(cfg, produits, etats, maj):
    wb = Workbook()
    wb.remove(wb.active)
    ecrire_catalogue_vendeur(wb, cfg, produits, etats, maj)
    creer_saisie_ventes(wb, cfg)
    creer_mode_emploi(wb, "Mode d'emploi — vendeurs", [
        "1. Après chaque vente, allez dans la feuille « Saisie_ventes », sur la première ligne vide.",
        "2. Date : tapez Ctrl + ; (point-virgule) pour la date du jour.",
        "3. Produit : cliquez la flèche et choisissez le produit. Pour chercher : tapez le début du nom "
        "(ex. « Dell » ou « Écran »), appuyez sur Entrée, puis rouvrez la flèche : seuls les produits qui "
        "commencent ainsi s'affichent. Le code SKU et le prix se remplissent seuls.",
        "4. Quantité : nombre d'articles vendus.",
        "5. Prix pratiqué : seulement si vous avez accordé une remise. Sinon laissez vide.",
        "6. Vendeur : choisissez votre nom. Client : facultatif (nom ou téléphone).",
        "7. Vérifiez la colonne « Contrôle » : elle doit afficher OK. Puis Ctrl + S.",
        "",
        "Le Catalogue montre le stock disponible en temps réel : vos ventes sont déduites dès la saisie.",
        "Les arrivages (achats) sont ajoutés automatiquement par le système quelques minutes après "
        "la fermeture du fichier. Fermez le fichier en fin de journée.",
        "Ne supprimez jamais une ligne : en cas d'erreur, corrigez-la ou mettez la quantité à 0.",
    ])
    wb.active = wb.sheetnames.index(F_SAISIE_VENTES)
    return wb


# ==========================================================================
# 2. Achats_AAAA.xlsx — saisie des entrées (admin)
# ==========================================================================
def ecrire_produits_achats(wb, cfg, produits):
    if F_PRODUITS_ACHATS in wb.sheetnames:
        ws = wb[F_PRODUITS_ACHATS]
        ws.delete_rows(2, ws.max_row + 1)
    else:
        ws = wb.create_sheet(F_PRODUITS_ACHATS)
        entetes(ws, 1, ["Code", "Désignation", "Prix d'achat de référence"])
        largeurs(ws, [12, 42, 20])
    for i, p in enumerate(produits, start=2):
        cellule(ws, i, 1, p.code, gras=True)
        cellule(ws, i, 2, p.designation)
        cellule(ws, i, 3, p.prix_achat, FMT_FCFA)
    n = len(produits) + 1
    definir_nom(wb, "Prod_Codes", f"{F_PRODUITS_ACHATS}!$A$2:$A${n}")
    definir_nom(wb, "Prod_Table", f"{F_PRODUITS_ACHATS}!$A$2:$C${n}")
    proteger(ws, cfg.mdp_feuilles)


def creer_fichier_achats(cfg, produits):
    wb = Workbook()
    ws = wb.active
    ws.title = F_SAISIE_ACHATS
    fin = LIGNE_DEBUT_SAISIE + cfg.lignes_achats - 1
    titre(ws, "A1", f"Saisie des achats {cfg.annee}",
          "Une ligne par arrivage. Cellules jaunes à remplir. Prix vide = prix d'achat de référence.")
    entetes(ws, 4, ["Date", "Code produit", "Désignation", "Quantité", "Prix d'achat unitaire",
                    "Fournisseur", "N° facture", "Total"])
    largeurs(ws, [12, 14, 40, 10, 18, 22, 14, 18])
    ws.freeze_panes = "A5"
    jaune = PatternFill("solid", fgColor=JAUNE_SAISIE)
    for r in range(LIGNE_DEBUT_SAISIE, fin + 1):
        ws.cell(row=r, column=3, value=f'=IF(B{r}="","",IFERROR(VLOOKUP(B{r},Prod_Table,2,FALSE),"Code inconnu"))')
        ws.cell(row=r, column=8, value=(f'=IF(OR(B{r}="",D{r}=""),"",D{r}*IF(E{r}<>"",E{r},'
                                        f'IFERROR(VLOOKUP(B{r},Prod_Table,3,FALSE),0)))')).number_format = FMT_FCFA
        for c in (1, 2, 4, 5, 6, 7):
            x = ws.cell(row=r, column=c)
            x.fill = jaune
            x.protection = Protection(locked=False)
        ws.cell(row=r, column=1).number_format = FMT_DATE
        ws.cell(row=r, column=5).number_format = FMT_FCFA
    dv = DataValidation(type="list", formula1="Prod_Codes", allow_blank=True, showErrorMessage=True,
                        error="Choisissez un code dans la liste.")
    dvd = DataValidation(type="date", operator="between", formula1="DATE(2020,1,1)", formula2="DATE(2035,12,31)",
                         allow_blank=True, showErrorMessage=True, error="Date au format jj/mm/aaaa.")
    dvq = DataValidation(type="whole", operator="greaterThan", formula1="0", allow_blank=True, showErrorMessage=True,
                         error="Nombre entier positif.")
    for d, col in ((dv, "B"), (dvd, "A"), (dvq, "D")):
        ws.add_data_validation(d)
        d.add(f"{col}{LIGNE_DEBUT_SAISIE}:{col}{fin}")
    proteger(ws, cfg.mdp_feuilles)
    ecrire_produits_achats(wb, cfg, produits)
    return wb


# ==========================================================================
# 3. Ventes_AAAA_Mois.xlsx — 12 feuilles mensuelles + récap (généré)
# ==========================================================================
def creer_fichier_ventes(cfg, produits, ventes: list[Vente], maj):
    wb = Workbook()
    rec = wb.active
    rec.title = "Récap"
    par_mois = defaultdict(list)
    for v in ventes:
        if v.date.year == cfg.annee:
            par_mois[v.date.month].append(v)

    for m in range(1, 13):
        ws = wb.create_sheet(MOIS_FR[m - 1])
        titre(ws, "A1", f"Ventes — {MOIS_FR[m - 1]} {cfg.annee}", f"Généré automatiquement le {maj}. Ne pas modifier.")
        entetes(ws, 4, ["Date", "N° saisie", "Vendeur", "Code", "Désignation", "Quantité",
                        "Prix unitaire", "Total", "Client"])
        largeurs(ws, [12, 10, 14, 12, 40, 10, 15, 16, 22])
        ws.freeze_panes = "A5"
        lignes = sorted(par_mois[m], key=lambda v: (v.date, v.ligne))
        r = 5
        for v in lignes:
            for c, val, fmt in ((1, v.date, FMT_DATE), (2, v.ligne - LIGNE_DEBUT_SAISIE + 1, None), (3, v.vendeur, None),
                                (4, v.code, None), (5, v.designation, None), (6, v.quantite, FMT_NB),
                                (7, v.prix_unitaire, FMT_FCFA), (8, v.total, FMT_FCFA), (9, v.client, None)):
                cellule(ws, r, c, val, fmt, fond=ORANGE if (c == 1 and v.date_estimee) else None)
            r += 1
        cellule(ws, r, 5, "TOTAL DU MOIS", gras=True, fond=BLEU_CLAIR)
        cellule(ws, r, 6, sum(v.quantite for v in lignes), FMT_NB, gras=True, fond=BLEU_CLAIR)
        cellule(ws, r, 8, sum(v.total for v in lignes), FMT_FCFA, gras=True, fond=BLEU_CLAIR)
        if lignes:
            ws.auto_filter.ref = f"A4:I{r - 1}"
        proteger(ws, cfg.mdp_feuilles)

    # ---- Récap
    titre(rec, "A1", f"Récapitulatif des ventes {cfg.annee}", f"Mis à jour automatiquement le {maj}.")
    entetes(rec, 4, ["Mois", "Nombre de ventes", "Articles vendus", "Chiffre d'affaires"])
    largeurs(rec, [14, 16, 16, 20] + [12] * 13)
    for m in range(1, 13):
        L = par_mois[m]
        r = 4 + m
        cellule(rec, r, 1, MOIS_FR[m - 1], gras=True)
        cellule(rec, r, 2, len(L), FMT_NB)
        cellule(rec, r, 3, sum(v.quantite for v in L), FMT_NB)
        cellule(rec, r, 4, sum(v.total for v in L), FMT_FCFA)
    cellule(rec, 17, 1, "TOTAL", gras=True, fond=BLEU_CLAIR)
    for c, col in ((2, "B"), (3, "C"), (4, "D")):
        cellule(rec, 17, c, f"=SUM({col}5:{col}16)", FMT_FCFA if c == 4 else FMT_NB, gras=True, fond=BLEU_CLAIR)

    # Quantités par produit et par mois
    r0 = 20
    rec.cell(row=r0 - 1, column=1, value="Articles vendus par produit et par mois").font = Font(bold=True, size=12, color=BLEU)
    entetes(rec, r0, ["Code", "Désignation"] + MOIS_COURTS + ["Total"])
    rec.column_dimensions["B"].width = 36
    qte = defaultdict(float)
    for v in ventes:
        if v.date.year == cfg.annee:
            qte[(v.code, v.date.month)] += v.quantite
    for i, p in enumerate(produits, start=r0 + 1):
        cellule(rec, i, 1, p.code, gras=True)
        cellule(rec, i, 2, p.designation)
        for m in range(1, 13):
            cellule(rec, i, 2 + m, qte.get((p.code, m), 0) or None, FMT_NB)
        cellule(rec, i, 15, f"=SUM(C{i}:N{i})", FMT_NB, gras=True)

    # Par vendeur
    r1 = r0 + len(produits) + 3
    rec.cell(row=r1 - 1, column=1, value="Chiffre d'affaires par vendeur et par mois").font = Font(bold=True, size=12, color=BLEU)
    entetes(rec, r1, ["Vendeur", ""] + MOIS_COURTS + ["Total"])
    ca_v = defaultdict(float)
    for v in ventes:
        if v.date.year == cfg.annee:
            ca_v[(v.vendeur, v.date.month)] += v.total
    noms = list(dict.fromkeys(cfg.vendeurs + sorted({v.vendeur for v in ventes})))
    for i, nom in enumerate(noms, start=r1 + 1):
        cellule(rec, i, 1, nom, gras=True)
        cellule(rec, i, 2, "")
        for m in range(1, 13):
            cellule(rec, i, 2 + m, ca_v.get((nom, m), 0) or None, FMT_NB)
        cellule(rec, i, 15, f"=SUM(C{i}:N{i})", FMT_NB, gras=True)
    rec.freeze_panes = "A5"
    proteger(rec, cfg.mdp_feuilles)
    return wb


# ==========================================================================
# 4. Stocks_admin.xlsx — vue complète, chiffrée (générée)
# ==========================================================================
def creer_fichier_admin(cfg, produits, etats, achats: list[Achat], ventes: list[Vente],
                        anomalies: list[Anomalie], maj):
    wb = Workbook()
    aujourd_hui = dt.date.today()
    v_an = [v for v in ventes if v.date.year == cfg.annee]

    # ---------------- Tableau de bord
    tb = wb.active
    tb.title = "Tableau de bord"
    titre(tb, "A1", f"Tableau de bord — {cfg.nom_magasin}", f"Données à jour au {maj}")
    largeurs(tb, [32, 20, 4, 44, 12, 18, 4, 10, 16])
    ca_an = sum(v.total for v in v_an)
    marge_an = sum(v.marge for v in v_an)
    ca_mois = sum(v.total for v in v_an if v.date.month == aujourd_hui.month)
    ca_jour = sum(v.total for v in ventes if v.date == aujourd_hui)
    val_cout = sum(max(e.stock, 0) * e.produit.prix_achat for e in etats.values())
    val_vente = sum(max(e.stock, 0) * e.produit.prix_vente for e in etats.values())
    kpis = [
        ("Chiffre d'affaires aujourd'hui", ca_jour, FMT_FCFA),
        (f"Chiffre d'affaires {MOIS_FR[aujourd_hui.month - 1].lower()}", ca_mois, FMT_FCFA),
        (f"Chiffre d'affaires {cfg.annee}", ca_an, FMT_FCFA),
        (f"Marge brute {cfg.annee}", marge_an, FMT_FCFA),
        ("Taux de marge", (marge_an / ca_an) if ca_an else 0, FMT_PCT),
        ("Valeur du stock (prix d'achat)", val_cout, FMT_FCFA),
        ("Valeur du stock (prix de vente)", val_vente, FMT_FCFA),
        ("Produits en rupture", sum(1 for e in etats.values() if e.statut == "Rupture"), FMT_NB),
        ("Produits en stock faible", sum(1 for e in etats.values() if e.statut == "Stock faible"), FMT_NB),
        ("Anomalies de saisie", len(anomalies), FMT_NB),
    ]
    for i, (lib, val, fmt) in enumerate(kpis, start=4):
        cellule(tb, i, 1, lib, gras=True, fond=BLEU_CLAIR)
        cellule(tb, i, 2, val, fmt, gras=True, align="right")

    tb["D3"] = "Top 5 produits (CA de l'année)"
    tb["D3"].font = Font(bold=True, color=BLEU)
    entetes(tb, 4, ["Produit", "Qté vendue", "CA"], col0=4)
    top = sorted(etats.values(), key=lambda e: e.ca, reverse=True)[:5]
    for i, e in enumerate(top, start=5):
        cellule(tb, i, 4, e.produit.designation)
        cellule(tb, i, 5, e.sorties, FMT_NB)
        cellule(tb, i, 6, e.ca, FMT_FCFA)

    tb["H3"] = "CA par mois"
    tb["H3"].font = Font(bold=True, color=BLEU)
    entetes(tb, 4, ["Mois", "CA"], col0=8)
    for m in range(1, 13):
        cellule(tb, 4 + m, 8, MOIS_COURTS[m - 1])
        cellule(tb, 4 + m, 9, sum(v.total for v in v_an if v.date.month == m), FMT_NB)
    ch = BarChart()
    ch.title = f"Chiffre d'affaires mensuel {cfg.annee}"
    ch.y_axis.title = "FCFA"
    ch.legend = None
    ch.add_data(Reference(tb, min_col=9, min_row=4, max_row=16), titles_from_data=True)
    ch.set_categories(Reference(tb, min_col=8, min_row=5, max_row=16))
    ch.height, ch.width = 8, 18
    tb.add_chart(ch, "A16")

    # ---------------- Stock complet
    ws = wb.create_sheet("Stock")
    titre(ws, "A1", "État du stock (vue administrateur)", f"Données à jour au {maj}")
    entetes(ws, 4, ["Code", "Désignation", "Catégorie", "Prix d'achat", "Prix de vente", "Marge unitaire",
                    "Taux de marge", "Stock initial", "Entrées", "Sorties", "Stock actuel", "Seuil",
                    "Statut", "Valeur au coût", "Valeur à la vente", "Dernier achat", "Dernière vente"])
    largeurs(ws, [11, 36, 16, 14, 14, 14, 10, 10, 9, 9, 10, 8, 13, 16, 16, 13, 13])
    for r, p in enumerate(produits, start=5):
        e = etats[p.code]
        vals = [p.code, p.designation, p.categorie, p.prix_achat, p.prix_vente, f"=E{r}-D{r}",
                f"=IF(E{r}=0,0,F{r}/E{r})", p.stock_initial, e.entrees, e.sorties, f"=H{r}+I{r}-J{r}", p.seuil,
                e.statut, f"=MAX(K{r},0)*D{r}", f"=MAX(K{r},0)*E{r}",
                e.dernier_achat.date if e.dernier_achat else None,
                e.derniere_vente.date if e.derniere_vente else None]
        fmts = [None, None, None, FMT_FCFA, FMT_FCFA, FMT_FCFA, FMT_PCT, FMT_NB, FMT_NB, FMT_NB, FMT_NB, FMT_NB,
                None, FMT_FCFA, FMT_FCFA, FMT_DATE, FMT_DATE]
        for c, (val, fmt) in enumerate(zip(vals, fmts), start=1):
            cellule(ws, r, c, val, fmt, gras=(c in (1, 11)))
    n = 4 + len(produits)
    cellule(ws, n + 1, 2, "TOTAL", gras=True, fond=BLEU_CLAIR)
    for col in "HIJK":
        cellule(ws, n + 1, ord(col) - 64, f"=SUM({col}5:{col}{n})", FMT_NB, gras=True, fond=BLEU_CLAIR)
    for col in "NO":
        cellule(ws, n + 1, ord(col) - 64, f"=SUM({col}5:{col}{n})", FMT_FCFA, gras=True, fond=BLEU_CLAIR)
    regles_statut(ws, f"M5:M{n}")
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:Q{n}"

    # ---------------- Calendrier (12 mois, CA par jour)
    creer_calendrier(wb, cfg, ventes, maj)

    # ---------------- Journal des ventes avec marges
    wv = wb.create_sheet("Ventes")
    titre(wv, "A1", f"Journal des ventes {cfg.annee}", "Coût et marge calculés au prix d'achat de référence.")
    entetes(wv, 4, ["Date", "Ligne saisie", "Vendeur", "Code", "Désignation", "Qté", "Prix unitaire",
                    "Total", "Coût", "Marge", "Client", "Remarque"])
    largeurs(wv, [12, 10, 14, 11, 36, 7, 14, 15, 15, 14, 18, 22])
    for r, v in enumerate(sorted(v_an, key=lambda x: (x.date, x.ligne)), start=5):
        vals = [v.date, v.ligne, v.vendeur, v.code, v.designation, v.quantite, v.prix_unitaire, v.total,
                v.quantite * v.cout_unitaire, v.marge, v.client, "Date estimée" if v.date_estimee else ""]
        fmts = [FMT_DATE, None, None, None, None, FMT_NB, FMT_FCFA, FMT_FCFA, FMT_FCFA, FMT_FCFA, None, None]
        for c, (val, fmt) in enumerate(zip(vals, fmts), start=1):
            cellule(wv, r, c, val, fmt)
    wv.freeze_panes = "A5"
    wv.auto_filter.ref = f"A4:L{max(5, 4 + len(v_an))}"

    # ---------------- Achats
    wa = wb.create_sheet("Achats")
    titre(wa, "A1", f"Journal des achats {cfg.annee}")
    entetes(wa, 4, ["Date", "Code", "Désignation", "Quantité", "Prix unitaire", "Total", "Fournisseur", "N° facture"])
    largeurs(wa, [12, 11, 36, 10, 15, 16, 22, 14])
    noms = {p.code: p.designation for p in produits}
    for r, a in enumerate(sorted(achats, key=lambda x: x.date), start=5):
        for c, (val, fmt) in enumerate(zip(
                [a.date, a.code, noms[a.code], a.quantite, a.prix_unitaire, a.total, a.fournisseur, a.facture],
                [FMT_DATE, None, None, FMT_NB, FMT_FCFA, FMT_FCFA, None, None]), start=1):
            cellule(wa, r, c, val, fmt)

    # ---------------- Vendeurs
    wvd = wb.create_sheet("Vendeurs")
    titre(wvd, "A1", f"Performance des vendeurs {cfg.annee}")
    entetes(wvd, 4, ["Vendeur", "Nb ventes", "Articles", "CA", "Marge", "Panier moyen", "Remises accordées"])
    largeurs(wvd, [18, 11, 10, 17, 17, 15, 17])
    noms_v = list(dict.fromkeys(cfg.vendeurs + sorted({v.vendeur for v in v_an})))
    for r, nom in enumerate(noms_v, start=5):
        L = [v for v in v_an if v.vendeur == nom]
        ca = sum(v.total for v in L)
        remise = sum(max(0, (etats[v.code].produit.prix_vente - v.prix_unitaire)) * v.quantite for v in L)
        for c, (val, fmt) in enumerate(zip(
                [nom, len(L), sum(v.quantite for v in L), ca, sum(v.marge for v in L), (ca / len(L)) if L else 0, remise],
                [None, FMT_NB, FMT_NB, FMT_FCFA, FMT_FCFA, FMT_FCFA, FMT_FCFA]), start=1):
            cellule(wvd, r, c, val, fmt, gras=(c == 1))

    # ---------------- Alertes
    wal = wb.create_sheet("Alertes")
    titre(wal, "A1", "Alertes", f"Contrôle automatique du {maj}")
    entetes(wal, 4, ["Type", "Fichier / produit", "Ligne", "Détail"])
    largeurs(wal, [18, 34, 8, 70])
    r = 5
    for e in sorted(etats.values(), key=lambda x: x.stock):
        if e.statut != "Disponible":
            cellule(wal, r, 1, e.statut, fond=ROUGE if e.statut == "Rupture" else ORANGE, gras=True)
            cellule(wal, r, 2, f"{e.produit.code} — {e.produit.designation}")
            cellule(wal, r, 3, "")
            txt = f"Stock actuel : {e.stock:g} (seuil {e.produit.seuil:g})."
            if e.stock < 0:
                txt += " Stock NÉGATIF : vente saisie sans stock ou achat non enregistré."
            cellule(wal, r, 4, txt)
            r += 1
    for a in anomalies:
        cellule(wal, r, 1, "Saisie", fond=GRIS, gras=True)
        cellule(wal, r, 2, a.fichier)
        cellule(wal, r, 3, a.ligne)
        cellule(wal, r, 4, a.message)
        r += 1
    if r == 5:
        cellule(wal, 5, 1, "Aucune alerte", fond=VERT, gras=True)
    return wb


def creer_calendrier(wb, cfg, ventes: list[Vente], maj):
    ws = wb.create_sheet("Calendrier", 1)
    titre(ws, "A1", f"Calendrier des ventes {cfg.annee}",
          f"Chiffre d'affaires par jour (FCFA). Plus la case est foncée, plus la journée a été forte. Mis à jour le {maj}.")
    ca_jour = defaultdict(float)
    nb_jour = defaultdict(int)
    for v in ventes:
        if v.date.year == cfg.annee:
            ca_jour[v.date] += v.total
            nb_jour[v.date] += 1
    maxi = max(ca_jour.values(), default=0)
    aujourd_hui = dt.date.today()
    nuances = ["EAF1FB", "C9DCF2", "9DC3E6", "5B9BD5", "2E75B6"]
    hauteur = 2 + 6 * 2 + 2

    for c in range(1, 25):
        ws.column_dimensions[get_column_letter(c)].width = 3 if c % 8 == 0 else 10.5
    for m in range(1, 13):
        bloc_l, bloc_c = (m - 1) // 3, (m - 1) % 3
        r0 = 4 + bloc_l * hauteur
        c0 = 1 + bloc_c * 8
        ws.merge_cells(start_row=r0, start_column=c0, end_row=r0, end_column=c0 + 6)
        t = ws.cell(row=r0, column=c0, value=f"{MOIS_FR[m - 1]} {cfg.annee}")
        t.font = Font(bold=True, color="FFFFFF", size=12)
        t.fill = PatternFill("solid", fgColor=BLEU)
        t.alignment = Alignment(horizontal="center")
        for j, nom in enumerate(JOURS_FR):
            x = ws.cell(row=r0 + 1, column=c0 + j, value=nom[:3].capitalize())
            x.font = Font(bold=True, color=BLEU)
            x.alignment = Alignment(horizontal="center")
            x.fill = PatternFill("solid", fgColor=BLEU_CLAIR)
        semaines = calendar.Calendar(firstweekday=0).monthdatescalendar(cfg.annee, m)
        for s, semaine in enumerate(semaines):
            for j, jour in enumerate(semaine):
                rj, rv = r0 + 2 + s * 2, r0 + 3 + s * 2
                cj, cv = ws.cell(row=rj, column=c0 + j), ws.cell(row=rv, column=c0 + j)
                if jour.month != m:
                    continue
                ca = ca_jour.get(jour, 0)
                cj.value = jour.day
                cj.font = Font(bold=True, size=9, color="595959")
                cj.alignment = Alignment(horizontal="left")
                cv.value = ca if ca else None
                cv.number_format = '#,##0'
                cv.alignment = Alignment(horizontal="center")
                if ca and maxi:
                    nuance = nuances[min(4, int(ca / maxi * 4.999))]
                    for x in (cj, cv):
                        x.fill = PatternFill("solid", fgColor=nuance)
                    if nuance in ("5B9BD5", "2E75B6"):
                        cv.font = Font(bold=True, color="FFFFFF")
                        cj.font = Font(bold=True, size=9, color="FFFFFF")
                    cv.comment = None
                bord = Side(style="medium", color="C00000") if jour == aujourd_hui else fin
                cj.border = Border(left=bord, right=bord, top=bord)
                cv.border = Border(left=bord, right=bord, bottom=bord)
        total = sum(ca_jour[d] for d in ca_jour if d.month == m)
        nb = sum(nb_jour[d] for d in nb_jour if d.month == m)
        ws.merge_cells(start_row=r0 + 14, start_column=c0, end_row=r0 + 14, end_column=c0 + 6)
        tt = ws.cell(row=r0 + 14, column=c0, value=f"Total : {total:,.0f} FCFA — {nb} vente(s)".replace(",", " "))
        tt.font = Font(bold=True, color=BLEU)
        tt.alignment = Alignment(horizontal="center")
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 85
