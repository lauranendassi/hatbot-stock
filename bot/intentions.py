# -*- coding: utf-8 -*-
"""Détection d'intention EN DUR (expressions régulières), sans l'IA.

Elle décide des actions sensibles (transfert à un humain, commande, SAV, annulation)
de façon fiable et prévisible ; l'IA ne sert qu'à formuler les réponses.
Extrait aussi les informations utiles : budget, usages, quantité, téléphone, destinataire.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from catalogue import sans_accents

# Ordre = priorité (la première intention trouvée devient l'intention principale)
MOTIFS: list[tuple[str, str]] = [
    ("HUMAIN", r"\b(parler|discuter|echanger) (a|avec) (un|une|le|la|quelqu'?un)|\bhumain\b|vraie? personne|"
               r"\bconseiller\b|un vendeur|\bun agent\b|service client|\brappel(ez|er|le)?[- ]?moi\b|"
               r"appel(ez|er)[- ]moi|votre (numero|contact)|un responsable"),
    ("PLAINTE", r"arnaqu|escroc|\bvoleurs?\b|pas content|mecontent|inadmissible|\bhonte\b|scandal|en colere|"
                r"\bdecu|rembours|plainte|nul a chier|pas serieux"),
    ("SAV", r"\bpanne\b|ne s'?allume|s'?allume (pas|plus)|marche (pas|plus)|fonctionne (pas|plus)|probleme (avec|sur)|"
            r"ecran noir|ecran bleu|\bbug|repar|\bsav\b|chauffe|redemarre tout seul|batterie (ne|tient)|"
            r"ne charge (pas|plus)|casse"),
    ("ANNULER", r"\bannul|laisse tomber|\boublie[zr]?\b|\bplus besoin\b|je ne veux plus|finalement non|"
                r"je change d'?avis|^stop\b"),
    ("PHOTO", r"\bphotos?\b|\bimages?\b|\bvoir (le|la|les|l'|a quoi)|montre[rz]?[- ]?(moi)?\b|a quoi (il|elle|ca) ressemble"),
    ("NEGOCIATION", r"laisse[rz]?[- ](moi|nous)?|laisse[- ]le|\bdernier prix\b|\bfai(re|tes|s|t) un (petit )?(effort|geste)|\bfai(s|tes)[- ]moi (un|une|le|la)\b|\b(me|nous) (le |la )?fai(re|tes|s) (a |au )?\d|\ba \d+ ?(k|mille|000)? ?(c'?est bon|ok)\b|"
                    r"\bremise\b|\breduction\b|\bnegoci|\brabais\b|\bdiscount\b|moins cher pour moi|"
                    r"\bbaisse[rz]?\b|\bdiminue[rz]?\b|\b\d+\s*(k|mille|000)?\s*ca (passe|va)\b|\bca passe a\b|\bje propose\b|\bje vous propose\b|"
                    r"\bmon prix\b|\bdernier mot\b|\bc'?est trop cher\b|\btrop cher\b|\bje paye?\b \d|"
                    r"\bje donne\b"),
    ("COMMANDE", r"\bje (le |la |les |l')(prends|veux|commande|achete|retiens|valide)\b|"
                 r"\bje (prends|commande|achete|reserve)\b|\bcommander\b|passer (une |la )?commande|"
                 r"\breserv(er|ez|e[- ]le|e[- ]la)\b|je suis interesse|on (le )?prend|je confirme l'?achat|"
                 r"\bje vais (le |la )?prendre\b"),
    ("PRIX", r"\bprix\b|\bcombien\b|\bcout|\btarif|\bcoute|c'?est a combien|\bmontant\b|\bcher\b"),
    ("DISPONIBILITE", r"\bdispo|en stock|vous avez|il (en )?reste|avez[- ]vous|y a[- ]t[- ]il|\bvous vendez\b|"
                      r"est[- ]ce que vous avez|\btu as\b"),
    ("LIVRAISON", r"\blivr|\bexpedi|\btransport|\bagence de voyage|\bcolis\b"),
    ("PAIEMENT", r"\bpay|paiement|orange money|\bom\b|\bmtn\b|\bmomo\b|mobile money|\bcash\b|\bespece|virement|"
                 r"\bacompte|\btranche|\ba credit\b|\bfacilite"),
    ("GARANTIE", r"\bgarant"),
    ("INFOS_MAGASIN", r"\bhoraire|\bouvert|\bferme|\badresse|\bou (etes|se trouve|vous trouver|est situe)|"
                      r"\blocalisation|\bsitue|\bboutique\b|\bmagasin\b|\bquartier\b"),
    ("SALUTATION", r"^(bonjour|bonsoir|salut|hello|hi|cc|coucou|bjr|bsr|slt|bonne journee)\b"),
    ("REMERCIEMENT", r"\bmerci\b|\bthanks?\b|\bthank you\b"),
    ("AU_REVOIR", r"\bau revoir\b|\ba (plus|bientot|demain)\b|\bbye\b|bonne (soiree|nuit)"),
    ("OUI", r"^(oui|ok|okay|d'?accord|dac|ouais|yes|yep|c'?est bon|parfait|valide|je confirme|exact|ca marche|"
            r"vas[- ]y|allons[- ]y|tout a fait|c'?est ca|correct)\b"),
    ("NON", r"^(non|nan|pas vraiment|nope|pas du tout|non merci)\b"),
]
_COMPILES = [(nom, re.compile(m)) for nom, m in MOTIFS]

USAGES: dict[str, str] = {
    "bureautique": r"bureautique|\bword\b|\bexcel\b|secretari|saisie|internet|\bmails?\b|navigation|facebook|zoom|"
                   r"\bteams\b|reunion",
    "etudes": r"etudiant|etude|universit|\bfac\b|\becole\b|\bcours\b|\bmemoire\b|\beleve\b|lycee|college|\bbts\b|master",
    "gestion": r"compta|gestion|\bsage\b|\bcaisse\b|factur|stock|\bbanque\b|\bfinance",
    "developpement": r"programm|\bdev\b|developpe|\bcode\b|\bcoder\b|logiciel|informaticien|\bpython\b|\bjava\b|"
                     r"\bweb\b|\breseau\b|virtualis",
    "graphisme": r"graphi|photoshop|illustrator|\bdesign|montage|\bvideo|premiere pro|creation|youtube|"
                 r"\bphoto(graphie)?s? (retouche|edit)|retouche|canva|architect|autocad",
    "gaming": r"\bjeux?\b|gaming|gamer|\bfifa\b|\bgta\b|\bpes\b|jouer",
    "entreprise": r"entreprise|\bsociete\b|\bcabinet\b|\bparc\b|employes|\bpme\b|\bong\b|administration|\bservice\b",
    "maison": r"famille|enfants?|maison|\bdomicile\b|regarder des films|netflix",
    "deplacement": r"voyage|mission|\bterrain\b|\bleger\b|deplacement|autonomie",
}
_USAGES = {k: re.compile(v) for k, v in USAGES.items()}

RE_TELEPHONE = re.compile(r"(?<![\d+])(?:\+?237[\s.-]*)?(6(?:[\s.-]?\d){8})(?![\s.-]?\d)")
RE_SUITE_CHIFFRES = re.compile(r"\+?\d[\d\s.-]{6,}\d")
RE_MONTANT = re.compile(r"(\d{1,3}(?:[\s.]\d{3})+|\d+(?:[.,]\d+)?)\s*(k\b|mille|milles|m\b|millions?|fcfa|f\b|frs?|francs?|xaf)?")
MOTS_BUDGET = re.compile(r"budget|\bmax|pas plus|maximum|moins de|en dessous|jusqu|environ|autour|entre|"
                         r"\bj'?ai\b|\bdispose|\bprevu|\bpas cher|\bmoyens\b|\benveloppe")


@dataclass
class Analyse:
    principale: str
    toutes: list[str] = field(default_factory=list)
    budget: float | None = None
    budget_min: float | None = None
    usages: list[str] = field(default_factory=list)
    quantite: int | None = None
    telephone: str | None = None
    telephone_invalide: bool = False
    offre: float | None = None
    pour_qui: str | None = None
    petit_budget: bool = False


def montants(texte_norm: str, contexte_budget: bool = False) -> list[float]:
    vals = []
    for m in RE_MONTANT.finditer(texte_norm):
        brut, unite = m.group(1), (m.group(2) or "").strip()
        try:
            v = float(re.sub(r"[\s.]", "", brut) if re.search(r"[\s.]\d{3}$", brut) else brut.replace(",", "."))
        except ValueError:
            continue
        if unite in ("k", "mille", "milles"):
            v *= 1000
        elif unite.startswith("m"):
            v *= 1_000_000
        elif not unite and contexte_budget and 30 <= v < 1000:
            v *= 1000                               # « 300 » dans une réponse de budget = 300 000
        if v >= 5000:
            vals.append(v)
    return vals


def analyser(texte: str, attend_budget: bool = False, attend_quantite: bool = False) -> Analyse:
    t = sans_accents(texte)
    trouvees = [nom for nom, rx in _COMPILES if rx.search(t)]
    a = Analyse(principale=trouvees[0] if trouvees else "AUTRE", toutes=trouvees)

    contexte_budget = attend_budget or bool(MOTS_BUDGET.search(t))
    tel = RE_TELEPHONE.search(texte.replace(" ", " "))
    texte_sans_tel = t.replace(tel.group(0), " ") if tel else t
    if tel:
        a.telephone = re.sub(r"\D", "", tel.group(1))
    elif RE_SUITE_CHIFFRES.search(texte):
        a.telephone_invalide = True             # des chiffres, mais pas un numéro camerounais valide
    vals = montants(texte_sans_tel, contexte_budget)
    if vals and contexte_budget:
        a.budget = max(vals)
        if len(vals) >= 2 and "entre" in t:
            a.budget_min = min(vals)
    if "NEGOCIATION" in trouvees or "PRIX" in trouvees:
        offres = montants(texte_sans_tel, True)
        if offres:
            a.offre = max(offres) if len(offres) == 1 else min(offres)
    if re.search(r"pas (trop )?cher|petit budget|moins cher|economique|budget (serre|limite|reduit)", t):
        a.petit_budget = True

    a.usages = [u for u, rx in _USAGES.items() if rx.search(t)]

    q = re.search(r"\b(\d{1,3})\s*(pieces?|unites?|exemplaires?|machines?|ordinateurs?|pc|postes?|ecrans?|barrettes?|"
                  r"chargeurs?|batteries?|ssd)\b", t) or re.search(r"\bx\s?(\d{1,3})\b", t)
    if q:
        a.quantite = int(q.group(1))
    elif attend_quantite:
        m = re.fullmatch(r"\s*(\d{1,3})\s*", t) or re.search(r"\b(un|une|deux|trois|quatre|cinq|six|dix)\b", t)
        if m:
            mots = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "dix": 10}
            a.quantite = int(m.group(1)) if m.group(1).isdigit() else mots[m.group(1)]

    p = re.search(r"pour (mon|ma|mes) (fils|fille|enfants?|frere|soeur|epouse|femme|mari|pere|mere|patron|"
                  r"entreprise|boutique|bureau|personnel|employes|ecole|eglise)", t)
    if p:
        a.pour_qui = f"{p.group(1)} {p.group(2)}"
    elif re.search(r"\bpour moi\b|\bpersonnel\b", t):
        a.pour_qui = "moi"
    return a
