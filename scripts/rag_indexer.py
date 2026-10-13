# -*- coding: utf-8 -*-
"""
Publication vers Neon (PostgreSQL) + construction de la base de connaissances RAG.

Le cœur du chatbot est la table stock_rag_documents : chaque produit, catégorie,
journée, mois, vendeur, alerte… y est décrit EN FRANÇAIS NATUREL, avec un
niveau d'accès :
    0 = public (clients, vendeurs, admin)
    1 = vendeurs + admin   (alertes de stock, procédures internes)
    2 = admin uniquement   (prix d'achat, marges, chiffre d'affaires, fournisseurs)

Le bot ne voit JAMAIS un document au-dessus du niveau de la personne qui parle :
la séparation vendeurs / admin est garantie par les données, pas par le prompt.

Recherche hybride côté bot : plein texte français (sans accents) + trigrammes
(fautes de frappe) + vecteurs sémantiques (si un fournisseur d'embeddings est configuré).
Les textes des fichiers connaissances/*.md (horaires, garantie, paiement…) sont
découpés par titre et indexés de la même façon.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import defaultdict

import psycopg

import embeddings
from common import DOSSIER_CONNAISSANCES, MOIS_FR, JOURS_FR, fcfa, prix_plancher

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$ SELECT public.unaccent('public.unaccent', $1) $$;

CREATE TABLE IF NOT EXISTS stock_produits (
  code text PRIMARY KEY, designation text NOT NULL, categorie text, marque text,
  prix_vente numeric, prix_achat numeric, stock numeric, seuil numeric, statut text,
  quantite_vendue numeric, ca numeric, marge numeric, maj timestamptz DEFAULT now());

ALTER TABLE stock_produits ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE stock_produits ADD COLUMN IF NOT EXISTS usages text;
ALTER TABLE stock_produits ADD COLUMN IF NOT EXISTS photo_url text;
ALTER TABLE stock_produits ADD COLUMN IF NOT EXISTS prix_min numeric;

CREATE TABLE IF NOT EXISTS stock_ventes (
  id text PRIMARY KEY, date date NOT NULL, code text, designation text, quantite numeric,
  prix_unitaire numeric, total numeric, cout numeric, marge numeric, vendeur text, client text);
CREATE INDEX IF NOT EXISTS stock_ventes_date_idx ON stock_ventes(date);

CREATE TABLE IF NOT EXISTS stock_achats (
  id text PRIMARY KEY, date date NOT NULL, code text, quantite numeric, prix_unitaire numeric,
  total numeric, fournisseur text, facture text);

CREATE TABLE IF NOT EXISTS stock_rag_documents (
  id text PRIMARY KEY,
  niveau smallint NOT NULL DEFAULT 2,
  type text NOT NULL,
  titre text NOT NULL,
  contenu text NOT NULL,
  metadata jsonb DEFAULT '{}'::jsonb,
  date_ref date,
  hash text,
  tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('french', f_unaccent(coalesce(titre, ''))), 'A') ||
    setweight(to_tsvector('french', f_unaccent(contenu)), 'B')) STORED,
  maj timestamptz DEFAULT now());
CREATE INDEX IF NOT EXISTS stock_rag_tsv_idx ON stock_rag_documents USING gin(tsv);
CREATE INDEX IF NOT EXISTS stock_rag_titre_trgm_idx ON stock_rag_documents USING gin (lower(f_unaccent(titre)) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS stock_rag_type_date_idx ON stock_rag_documents(type, date_ref);

CREATE TABLE IF NOT EXISTS stock_sync_etat (cle text PRIMARY KEY, valeur text, maj timestamptz DEFAULT now());
"""


# --------------------------------------------------------------------------
# Rédaction des documents en français naturel
# --------------------------------------------------------------------------
def date_longue(d: dt.date) -> str:
    jour = "1er" if d.day == 1 else str(d.day)
    return f"{JOURS_FR[d.weekday()]} {jour} {MOIS_FR[d.month - 1].lower()} {d.year}"


def qte(n: float) -> str:
    return f"{n:g}"


def phrase_dispo(e) -> str:
    s = e.stock
    if s <= 0:
        return "actuellement en rupture de stock"
    if s <= e.produit.seuil:
        return f"disponible, mais il n'en reste que {qte(s)} (stock faible)"
    return f"disponible en stock ({qte(s)} unités)"


def doc(id_, niveau, type_, titre, contenu, date_ref=None, **meta):
    return {"id": id_, "niveau": niveau, "type": type_, "titre": titre, "contenu": contenu.strip(),
            "date_ref": date_ref, "metadata": meta}


def construire_documents(cfg, produits, etats, achats, ventes, anomalies) -> list[dict]:
    docs: list[dict] = []
    aujourd_hui = dt.date.today()
    v_an = [v for v in ventes if v.date.year == cfg.annee]

    # ---- Produits (public) + fiche admin
    for p in produits:
        e = etats[p.code]
        texte = (f"{p.designation} (référence {p.code})"
                 + (f", de marque {p.marque}" if p.marque else "")
                 + f", rayon {p.categorie}. Prix de vente : {fcfa(p.prix_vente)}. "
                 f"Ce produit est {phrase_dispo(e)}.")
        if p.usages:
            texte += f" Idéal pour : {p.usages}."
        if p.description:
            texte += f" Caractéristiques : {p.description}."
        docs.append(doc(f"produit:{p.code}", 0, "produit", f"{p.designation} ({p.code})", texte,
                        code=p.code, categorie=p.categorie, prix=p.prix_vente, stock=e.stock, statut=e.statut))

        mu = p.prix_vente - p.prix_achat
        t = (f"Fiche administrateur de {p.designation} ({p.code}). Prix d'achat de référence {fcfa(p.prix_achat)}, "
             f"prix de vente {fcfa(p.prix_vente)}, prix plancher de négociation {fcfa(prix_plancher(p, cfg))}, "
             f"soit une marge unitaire de {fcfa(mu)}"
             f"{f' ({mu / p.prix_vente:.0%})' if p.prix_vente else ''}. "
             f"Stock : {qte(p.stock_initial)} au départ, {qte(e.entrees)} entrés, {qte(e.sorties)} vendus, "
             f"{qte(e.stock)} aujourd'hui (seuil d'alerte {qte(p.seuil)}), "
             f"valeur au coût {fcfa(max(e.stock, 0) * p.prix_achat)}. "
             f"Depuis le début de l'année : {fcfa(e.ca)} de chiffre d'affaires et {fcfa(e.marge)} de marge.")
        if e.dernier_achat:
            a = e.dernier_achat
            t += (f" Dernier arrivage le {date_longue(a.date)} : {qte(a.quantite)} unités à {fcfa(a.prix_unitaire)}"
                  + (f" chez {a.fournisseur}" if a.fournisseur else "") + ".")
        if e.derniere_vente:
            t += f" Dernière vente le {date_longue(e.derniere_vente.date)} par {e.derniere_vente.vendeur}."
        docs.append(doc(f"produit_admin:{p.code}", 2, "produit_admin", f"{p.designation} ({p.code}) — fiche admin", t,
                        code=p.code))

    # ---- Catégories + vue d'ensemble du catalogue (public)
    par_cat = defaultdict(list)
    for p in produits:
        par_cat[p.categorie].append(p)
    resume = []
    for cat, L in sorted(par_cat.items()):
        lignes = [f"{p.designation} à {fcfa(p.prix_vente)} ({etats[p.code].statut.lower()})" for p in L]
        prix = [p.prix_vente for p in L]
        docs.append(doc(f"categorie:{cat}", 0, "categorie", f"Rayon {cat}",
                        f"Dans le rayon {cat}, nous proposons {len(L)} article(s), de {fcfa(min(prix))} à "
                        f"{fcfa(max(prix))} : " + " ; ".join(lignes) + ".", categorie=cat))
        resume.append(f"{cat} ({len(L)} article(s), de {fcfa(min(prix))} à {fcfa(max(prix))})")
    dispo = sum(1 for e in etats.values() if e.stock > 0)
    docs.append(doc("catalogue", 0, "catalogue", f"Catalogue de {cfg.nom_magasin}",
                    f"{cfg.nom_magasin} vend du matériel informatique. Le catalogue compte {len(produits)} "
                    f"références, dont {dispo} disponibles en ce moment. Rayons : " + " ; ".join(resume) + "."))

    # ---- Alertes de stock (vendeurs + admin)
    faibles = [e for e in etats.values() if e.statut != "Disponible"]
    if faibles:
        txt = "Produits à surveiller : " + " ; ".join(
            f"{e.produit.designation} ({e.produit.code}) — {e.statut.lower()}, {qte(max(e.stock, 0))} en stock"
            for e in sorted(faibles, key=lambda x: x.stock)) + "."
    else:
        txt = "Aucun produit en rupture ni en stock faible pour le moment."
    docs.append(doc("alertes_stock", 1, "alertes", "Ruptures et stocks faibles", txt, date_ref=aujourd_hui))

    # ---- Journées de vente (admin)
    par_jour = defaultdict(list)
    for v in v_an:
        par_jour[v.date].append(v)
    for d, L in par_jour.items():
        ca = sum(v.total for v in L)
        par_vendeur = defaultdict(float)
        for v in L:
            par_vendeur[v.vendeur] += v.total
        detail = " ; ".join(f"{qte(v.quantite)} × {v.designation} à {fcfa(v.prix_unitaire)} par {v.vendeur}"
                            + (f" (client {v.client})" if v.client else "") for v in L[:40])
        docs.append(doc(f"jour:{d.isoformat()}", 2, "jour", f"Ventes du {date_longue(d)}",
                        f"Le {date_longue(d)}, le magasin a réalisé {len(L)} vente(s) pour "
                        f"{qte(sum(v.quantite for v in L))} article(s), soit {fcfa(ca)} de chiffre d'affaires et "
                        f"{fcfa(sum(v.marge for v in L))} de marge. Par vendeur : "
                        + ", ".join(f"{k} {fcfa(x)}" for k, x in sorted(par_vendeur.items(), key=lambda i: -i[1]))
                        + f". Détail : {detail}.", date_ref=d, ca=ca))

    # ---- Mois (admin)
    for m in range(1, 13):
        L = [v for v in v_an if v.date.month == m]
        A = [a for a in achats if a.date.year == cfg.annee and a.date.month == m]
        if not L and not A:
            continue
        ca = sum(v.total for v in L)
        en_cours = (m == aujourd_hui.month and aujourd_hui.year == cfg.annee)
        if en_cours:   # mois incomplet : comparaison à la même date du mois précédent
            prec = sum(v.total for v in v_an if v.date.month == m - 1 and v.date.day <= aujourd_hui.day) if m > 1 else 0
            evol = (f" ({(ca - prec) / prec:+.0%} par rapport au {aujourd_hui.day} {MOIS_FR[m - 2].lower()})"
                    if prec else "")
        else:
            prec = sum(v.total for v in v_an if v.date.month == m - 1) if m > 1 else 0
            evol = f" ({(ca - prec) / prec:+.0%} par rapport au mois précédent)" if prec else ""
        par_vend = defaultdict(lambda: [0, 0.0])
        for v in L:
            par_vend[v.vendeur][0] += 1
            par_vend[v.vendeur][1] += v.total
        top = defaultdict(float)
        for v in L:
            top[v.designation] += v.quantite
        meilleurs = sorted(top.items(), key=lambda i: -i[1])[:5]
        meilleur_jour = max(((d, sum(x.total for x in vs)) for d, vs in par_jour.items() if d.month == m),
                            key=lambda i: i[1], default=None)
        t = (f"En {MOIS_FR[m - 1].lower()} {cfg.annee}{' (mois en cours, au ' + str(aujourd_hui.day) + ')' if en_cours else ''} : "
             f"{len(L)} vente(s), {fcfa(ca)} de chiffre d'affaires{evol}, {fcfa(sum(v.marge for v in L))} de marge.")
        if par_vend:
            t += " Classement des vendeurs du mois : " + ", ".join(
                f"{i}. {n} ({fcfa(x[1])}, {x[0]} vente(s))"
                for i, (n, x) in enumerate(sorted(par_vend.items(), key=lambda i: -i[1][1]), start=1)) + "."
        if meilleurs:
            t += " Produits les plus vendus : " + ", ".join(f"{n} ({qte(q)})" for n, q in meilleurs) + "."
        if meilleur_jour:
            t += f" Meilleure journée : {date_longue(meilleur_jour[0])} avec {fcfa(meilleur_jour[1])}."
        if A:
            t += (f" Achats du mois : {len(A)} arrivage(s) pour {fcfa(sum(a.total for a in A))} — "
                  + " ; ".join(f"{qte(a.quantite)} × {etats[a.code].produit.designation}"
                               + (f" ({a.fournisseur})" if a.fournisseur else "") for a in A[:25]) + ".")
        docs.append(doc(f"mois:{cfg.annee}-{m:02d}", 2, "mois", f"Bilan de {MOIS_FR[m - 1].lower()} {cfg.annee}", t,
                        date_ref=dt.date(cfg.annee, m, 1), ca=ca))

    # ---- Vendeurs (admin)
    for nom in sorted({v.vendeur for v in v_an}):
        L = [v for v in v_an if v.vendeur == nom]
        Lm = [v for v in L if v.date.month == aujourd_hui.month]
        docs.append(doc(f"vendeur:{nom}", 2, "vendeur", f"Performance de {nom}",
                        f"{nom} a enregistré {len(L)} vente(s) en {cfg.annee} pour {fcfa(sum(v.total for v in L))} "
                        f"(marge {fcfa(sum(v.marge for v in L))}), dont {len(Lm)} vente(s) et "
                        f"{fcfa(sum(v.total for v in Lm))} ce mois-ci. Dernière vente le {date_longue(max(v.date for v in L))}.",
                        vendeur=nom))

    # ---- Synthèse du jour (admin) — toujours jointe au contexte admin
    ca_j = sum(v.total for v in ventes if v.date == aujourd_hui)
    ca_m = sum(v.total for v in v_an if v.date.month == aujourd_hui.month)
    val = sum(max(e.stock, 0) * e.produit.prix_achat for e in etats.values())
    docs.append(doc("synthese", 2, "synthese", "Situation générale du magasin",
                    f"Aujourd'hui {date_longue(aujourd_hui)} : {fcfa(ca_j)} de ventes. Depuis le début du mois : "
                    f"{fcfa(ca_m)}. Depuis le début de l'année : {fcfa(sum(v.total for v in v_an))} de chiffre "
                    f"d'affaires et {fcfa(sum(v.marge for v in v_an))} de marge. Valeur du stock au prix d'achat : "
                    f"{fcfa(val)}. {sum(1 for e in etats.values() if e.statut == 'Rupture')} produit(s) en rupture, "
                    f"{sum(1 for e in etats.values() if e.statut == 'Stock faible')} en stock faible. "
                    f"{len(anomalies)} anomalie(s) de saisie à vérifier.", date_ref=aujourd_hui))
    if anomalies:
        docs.append(doc("anomalies", 2, "anomalies", "Anomalies de saisie à corriger",
                        " ; ".join(f"{a.fichier} ligne {a.ligne} : {a.message}" for a in anomalies[:60]) + "."))

    docs.extend(documents_connaissances())
    for d_ in docs:
        d_["hash"] = hashlib.sha1((d_["titre"] + "\n" + d_["contenu"] + str(d_["niveau"])).encode()).hexdigest()
    return docs


def documents_connaissances() -> list[dict]:
    """connaissances/*.md : une section '## Titre' = un document.
    Ligne optionnelle en tête de fichier : 'niveau: 1' (0 par défaut)."""
    docs = []
    if not DOSSIER_CONNAISSANCES.exists():
        return docs
    for f in sorted(DOSSIER_CONNAISSANCES.glob("*.md")):
        texte = f.read_text(encoding="utf-8")
        niveau = 0
        m = re.match(r"\s*niveau\s*:\s*(\d)", texte)
        if m:
            niveau = int(m.group(1))
        for i, bloc in enumerate(re.split(r"(?m)^##\s+", texte)[1:]):
            titre, _, corps = bloc.partition("\n")
            corps = re.sub(r"<!--.*?-->", "", corps, flags=re.S).strip()
            if corps:
                docs.append(doc(f"kb:{f.stem}:{i}", niveau, "connaissance", titre.strip(), corps, source=f.name))
    return docs


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------
def publier(cfg, produits, etats, achats, ventes, anomalies, log):
    docs = construire_documents(cfg, produits, etats, achats, ventes, anomalies)
    vec_actif = embeddings.actif(cfg.embed_provider, cfg.embed_key)
    with psycopg.connect(cfg.database_url, connect_timeout=15, autocommit=False) as cx:
        with cx.cursor() as cur:
            cur.execute(SCHEMA)
            pgvector = False
            try:
                cur.execute("SAVEPOINT v; CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(f"ALTER TABLE stock_rag_documents ADD COLUMN IF NOT EXISTS embedding vector({cfg.embed_dim})")
                cur.execute("RELEASE SAVEPOINT v")
                pgvector = True
            except psycopg.Error:
                cur.execute("ROLLBACK TO SAVEPOINT v")
                if vec_actif:
                    log.warning("pgvector indisponible : recherche plein texte uniquement")

            # Données brutes (utiles pour les questions chiffrées précises)
            cur.execute("DELETE FROM stock_produits")
            cur.executemany(
                "INSERT INTO stock_produits (code, designation, categorie, marque, prix_vente, prix_achat, stock, seuil,"
                " statut, quantite_vendue, ca, marge, description, usages, photo_url, prix_min)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(p.code, p.designation, p.categorie, p.marque, p.prix_vente, p.prix_achat, etats[p.code].stock,
                  p.seuil, etats[p.code].statut, etats[p.code].sorties, etats[p.code].ca, etats[p.code].marge,
                  p.description, p.usages, p.photo_url, prix_plancher(p, cfg))
                 for p in produits])
            cur.execute("DELETE FROM stock_ventes")
            cur.executemany(
                "INSERT INTO stock_ventes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(f"{v.date.year}-{v.ligne}", v.date, v.code, v.designation, v.quantite, v.prix_unitaire, v.total,
                  v.quantite * v.cout_unitaire, v.marge, v.vendeur, v.client) for v in ventes])
            cur.execute("DELETE FROM stock_achats")
            cur.executemany(
                "INSERT INTO stock_achats VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(f"{a.date.year}-{a.ligne}", a.date, a.code, a.quantite, a.prix_unitaire, a.total,
                  a.fournisseur, a.facture) for a in achats])

            # Documents RAG : on ne réécrit (et ne revectorise) que ce qui a changé
            cur.execute("SELECT id, hash" + (", embedding IS NOT NULL" if pgvector else ", false")
                        + " FROM stock_rag_documents")
            existants = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            modifies = [d for d in docs if existants.get(d["id"], (None,))[0] != d["hash"]]
            a_vectoriser = [d for d in docs if vec_actif and pgvector and
                            (d in modifies or not existants.get(d["id"], (None, False))[1])]
            for d in modifies:
                cur.execute(
                    "INSERT INTO stock_rag_documents (id, niveau, type, titre, contenu, metadata, date_ref, hash, maj)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (id) DO UPDATE SET niveau=EXCLUDED.niveau,"
                    " type=EXCLUDED.type, titre=EXCLUDED.titre, contenu=EXCLUDED.contenu, metadata=EXCLUDED.metadata,"
                    " date_ref=EXCLUDED.date_ref, hash=EXCLUDED.hash, maj=now()"
                    + (", embedding=NULL" if pgvector else ""),
                    (d["id"], d["niveau"], d["type"], d["titre"], d["contenu"],
                     json.dumps(d["metadata"], ensure_ascii=False, default=str), d["date_ref"], d["hash"]))
            ids = [d["id"] for d in docs]
            cur.execute("DELETE FROM stock_rag_documents WHERE NOT (id = ANY(%s))", (ids,))
            supprimes = cur.rowcount

            if a_vectoriser:
                vecteurs = embeddings.vectoriser([f"{d['titre']}\n{d['contenu']}" for d in a_vectoriser],
                                                 cfg.embed_provider, cfg.embed_key, cfg.embed_model, cfg.embed_dim)
                cur.executemany("UPDATE stock_rag_documents SET embedding = %s::vector WHERE id = %s",
                                [(embeddings.en_litteral_pgvector(v), d["id"]) for v, d in zip(vecteurs, a_vectoriser)])

            cur.execute("INSERT INTO stock_sync_etat (cle, valeur, maj) VALUES ('derniere_synchro', %s, now()) "
                        "ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, maj = now()",
                        (dt.datetime.now().strftime("%d/%m/%Y à %H:%M"),))
            cur.execute("INSERT INTO stock_sync_etat (cle, valeur, maj) VALUES ('magasin', %s, now()) "
                        "ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, maj = now()", (cfg.nom_magasin,))
        cx.commit()
    log.info("✔ Neon : %d produits, %d ventes, %d achats — RAG : %d documents (%d modifiés, %d supprimés, %d vectorisés)",
             len(produits), len(ventes), len(achats), len(docs), len(modifies), supprimes, len(a_vectoriser))
