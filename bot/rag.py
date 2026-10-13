# -*- coding: utf-8 -*-
"""RAG — récupère les fiches utiles dans Neon (table stock_rag_documents, alimentée par
scripts/sync.py depuis les fichiers Excel) et les filtre selon le rôle.

Recherche hybride, sans service payant :
  1. ciblage direct : codes produits, dates (« hier », « en septembre », « 12/10 »)
  2. plein texte français sans accents (OU entre les mots, classé par pertinence)
  3. trigrammes sur les titres (fautes de frappe : « elitbok », « tinkpad »)
  4. fusion par rang réciproque (RRF)
Si Neon est injoignable : fiches produits reconstruites depuis le catalogue de secours.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

import catalogue
import db
from catalogue import sans_accents

log = logging.getLogger("bot.rag")

MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
        "septembre", "octobre", "novembre", "decembre"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
RE_CODE = re.compile(r"\b([A-Za-z]{2,4})[- ]?(\d{2,4})\b")
RE_PRIX = re.compile(r"\d[\d\s  .]*\s*FCFA", re.I)
MOTS_VIDES = {"vous", "avez", "avec", "pour", "dans", "quel", "quelle", "quels", "combien", "encore", "votre",
              "notre", "cette", "bonjour", "bonsoir", "merci", "prix", "coute", "coutent", "faire", "fait", "sont",
              "elle", "elles", "leur", "plus", "moins", "tres", "bien", "comment", "veux", "voudrais", "cherche"}


@dataclass
class Fiche:
    id: str
    titre: str
    contenu: str
    type: str
    score: float = 0.0


def masquer_prix(texte: str) -> str:
    return RE_PRIX.sub("[prix communiqué après qualification]", texte)


def ids_temporels(question: str, aujourd_hui: dt.date) -> list[str]:
    q = sans_accents(question)
    jours: list[dt.date] = []
    if re.search(r"avant[- ]hier", q):
        jours.append(aujourd_hui - dt.timedelta(days=2))
    elif re.search(r"\bhier\b", q):
        jours.append(aujourd_hui - dt.timedelta(days=1))
    if re.search(r"aujourd|ce matin|ce soir|\bce jour\b", q):
        jours.append(aujourd_hui)
    if "semaine derniere" in q:
        debut = aujourd_hui - dt.timedelta(days=aujourd_hui.weekday() + 7)
        jours += [debut + dt.timedelta(days=i) for i in range(7)]
    elif "semaine" in q:
        debut = aujourd_hui - dt.timedelta(days=aujourd_hui.weekday())
        jours += [debut + dt.timedelta(days=i) for i in range((aujourd_hui - debut).days + 1)]
    for j, nom in enumerate(JOURS):
        if re.search(rf"\b{nom}\b", q):
            ecart = (aujourd_hui.weekday() - j) % 7 or (7 if "dernier" in q else 0)
            jours.append(aujourd_hui - dt.timedelta(days=ecart))
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", q):
        try:
            an = int(m.group(3)) if m.group(3) else aujourd_hui.year
            jours.append(dt.date(an + 2000 if an < 100 else an, int(m.group(2)), int(m.group(1))))
        except ValueError:
            pass
    ids = [f"jour:{d.isoformat()}" for d in jours]
    mois = {MOIS.index(m) + 1 for m in MOIS if re.search(rf"\b{m}\b", q)}
    if re.search(r"ce mois|mois en cours", q):
        mois.add(aujourd_hui.month)
    if re.search(r"mois (dernier|passe)", q):
        mois.add((aujourd_hui.month - 2) % 12 + 1)
    return ids + [f"mois:{aujourd_hui.year}-{m:02d}" for m in sorted(mois)]


def rechercher(question: str, niveau: int, contexte: str = "", k: int = 7) -> list[Fiche]:
    try:
        cx_ctx = db.base().connexion()
    except Exception:
        cx_ctx = None
    if cx_ctx is None:
        return _recherche_locale(question + " " + contexte, k)
    try:
        with cx_ctx as cx:
            return _recherche_sql(cx, question, niveau, contexte, k)
    except Exception as e:
        log.warning("RAG Neon indisponible (%s) : recherche locale", e)
        return _recherche_locale(question + " " + contexte, k)


def _recherche_sql(cx, question, niveau, contexte, k) -> list[Fiche]:
    requete = f"{question} {contexte}".strip()
    classements: list[list[str]] = []
    directs = ids_temporels(question, dt.date.today()) if niveau >= 2 else []
    for m in RE_CODE.finditer(requete):
        code = f"{m.group(1).upper()}-{m.group(2)}"
        directs.append(f"produit:{code}")
        if niveau >= 2:
            directs.append(f"produit_admin:{code}")

    rows = cx.execute("""
        WITH q AS (SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tq
                   FROM unnest(to_tsvector('french', f_unaccent(%(q)s))))
        SELECT id FROM stock_rag_documents, q
        WHERE q.tq IS NOT NULL AND niveau <= %(n)s AND tsv @@ q.tq
        ORDER BY ts_rank_cd(tsv, q.tq, 32) DESC LIMIT 25""", {"q": requete, "n": niveau}).fetchall()
    classements.append([r["id"] for r in rows])

    mots = [m for m in re.findall(r"[a-z0-9]{4,}", sans_accents(question)) if m not in MOTS_VIDES]
    if mots:
        rows = cx.execute("""
            SELECT id, max(word_similarity(m, lower(f_unaccent(titre)))) AS s
            FROM stock_rag_documents, unnest(%(mots)s::text[]) AS m WHERE niveau <= %(n)s
            GROUP BY id HAVING max(word_similarity(m, lower(f_unaccent(titre)))) >= 0.45
            ORDER BY s DESC LIMIT 15""", {"mots": mots, "n": niveau}).fetchall()
        classements.append([r["id"] for r in rows])

    scores: dict[str, float] = {}
    for liste in classements:
        for rang, id_ in enumerate(liste):
            scores[id_] = scores.get(id_, 0) + 1 / (60 + rang)
    for id_ in directs:
        scores[id_] = scores.get(id_, 0) + 1.0
    ids = sorted(scores, key=scores.get, reverse=True)[:k]
    if niveau >= 2:
        ids.append("synthese")
    rows = cx.execute("SELECT id, titre, contenu, type FROM stock_rag_documents WHERE id = ANY(%(ids)s) AND niveau <= %(n)s",
                      {"ids": ids, "n": niveau}).fetchall()
    fiches = [Fiche(r["id"], r["titre"], r["contenu"], r["type"], scores.get(r["id"], 0)) for r in rows]
    return sorted(fiches, key=lambda f: -f.score)


def _recherche_locale(requete: str, k: int) -> list[Fiche]:
    """Secours sans base : fiches produits construites depuis le catalogue (niveau public)."""
    mots = set(re.findall(r"[a-z0-9]{3,}", sans_accents(requete))) - MOTS_VIDES
    fiches = []
    for p in catalogue.produits():
        score = sum(1 for m in mots if m in p["_texte"])
        if score:
            stock = float(p.get("stock") or 0)
            dispo = "en rupture" if stock <= 0 else f"disponible ({stock:g} en stock)"
            fiches.append(Fiche(f"produit:{p['code']}", p["designation"],
                                f"{p['designation']} (référence {p['code']}), rayon {p['categorie']}. Prix de vente : "
                                f"{float(p['prix_vente']):,.0f} FCFA. Actuellement {dispo}. Idéal pour : {p.get('usages', '')}."
                                .replace(",", " "), "produit", score))
    return sorted(fiches, key=lambda f: -f.score)[:k]
