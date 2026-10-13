# -*- coding: utf-8 -*-
"""Stockage du bot : conversations, mémoire (messages), profils clients, rôles, commandes.

Deux implémentations avec la même interface :
  - BaseNeon    : PostgreSQL Neon (production)
  - BaseMemoire : en mémoire (tests et essais locaux sans base)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from copy import deepcopy

import config

log = logging.getLogger("bot.db")

SCHEMA_BOT = """
CREATE TABLE IF NOT EXISTS bot_conversations (
  psid text PRIMARY KEY,
  etat text NOT NULL DEFAULT 'CONVERSATION',
  panier jsonb NOT NULL DEFAULT '{}'::jsonb,
  profil jsonb NOT NULL DEFAULT '{}'::jsonb,
  handoff_jusqua timestamptz,
  cree_le timestamptz DEFAULT now(),
  maj timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS bot_messages (
  id bigserial PRIMARY KEY,
  psid text NOT NULL,
  role text NOT NULL,
  contenu text NOT NULL,
  cree_le timestamptz DEFAULT now());
CREATE INDEX IF NOT EXISTS bot_messages_psid_idx ON bot_messages(psid, id DESC);

CREATE TABLE IF NOT EXISTS bot_messages_traites (
  mid text PRIMARY KEY,
  cree_le timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS bot_utilisateurs (
  psid text PRIMARY KEY,
  role text NOT NULL DEFAULT 'client',
  maj timestamptz DEFAULT now());

CREATE TABLE IF NOT EXISTS bot_commandes (
  id bigserial PRIMARY KEY,
  reference text UNIQUE NOT NULL,
  psid text NOT NULL,
  nom text, telephone text, mode_livraison text, quartier text,
  lignes jsonb NOT NULL,
  total numeric NOT NULL,
  statut text NOT NULL DEFAULT 'nouvelle',
  cree_le timestamptz DEFAULT now());
"""


def _nouvelle_conversation(psid: str) -> dict:
    return {"psid": psid, "etat": "CONVERSATION", "panier": {}, "profil": {}, "handoff_jusqua": None}


class BaseMemoire:
    """Implémentation en mémoire. Les produits viennent du catalogue de secours."""

    def __init__(self):
        self._verrou = threading.Lock()
        self.conversations: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}
        self.traites: set[str] = set()
        self.roles: dict[str, str] = {}
        self.commandes: list[dict] = []

    def initialiser(self):
        pass

    def conversation(self, psid):
        with self._verrou:
            return deepcopy(self.conversations.get(psid) or _nouvelle_conversation(psid))

    def enregistrer_conversation(self, conv):
        with self._verrou:
            self.conversations[conv["psid"]] = deepcopy(conv)

    def ajouter_message(self, psid, role, contenu):
        with self._verrou:
            self.messages.setdefault(psid, []).append({"role": role, "contenu": contenu})

    def historique(self, psid, n):
        with self._verrou:
            return deepcopy(self.messages.get(psid, [])[-n:])

    def marquer_traite(self, mid) -> bool:
        """Vrai si le message est nouveau (Meta renvoie parfois deux fois le même)."""
        with self._verrou:
            if mid in self.traites:
                return False
            self.traites.add(mid)
            return True

    def role(self, psid):
        return self.roles.get(psid, "client")

    def definir_role(self, psid, role):
        self.roles[psid] = role

    def inserer_commande(self, cmd) -> str:
        with self._verrou:
            ref = f"CMD-{dt.date.today():%Y%m%d}-{len(self.commandes) + 1:04d}"
            self.commandes.append({**cmd, "reference": ref, "statut": "nouvelle",
                                   "cree_le": dt.datetime.now().isoformat(timespec="minutes")})
            return ref

    def dernieres_commandes(self, n=10):
        return deepcopy(self.commandes[-n:][::-1])

    def produits(self):
        return None            # -> le catalogue de secours sera utilisé

    def connexion(self):
        return None            # pas de recherche SQL : rag.py bascule en recherche locale


class BaseNeon:
    def __init__(self, url):
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row
        # Neon met sa base en veille après quelques minutes d'inactivité et coupe les connexions :
        # check_connection teste chaque connexion avant usage et la remplace si elle est morte.
        self.pool = ConnectionPool(url, min_size=0, max_size=4, kwargs={"row_factory": dict_row,
                                   "connect_timeout": 15}, open=True, timeout=20, max_idle=60,
                                   check=ConnectionPool.check_connection, reconnect_timeout=60)

    def connexion(self):
        return self.pool.connection()

    def initialiser(self):
        with self.connexion() as cx:
            cx.execute(SCHEMA_BOT)
        log.info("Tables du bot prêtes")

    def conversation(self, psid):
        with self.connexion() as cx:
            r = cx.execute("SELECT psid, etat, panier, profil, handoff_jusqua FROM bot_conversations WHERE psid=%s",
                           (psid,)).fetchone()
        return dict(r) if r else _nouvelle_conversation(psid)

    def enregistrer_conversation(self, conv):
        with self.connexion() as cx:
            cx.execute("""INSERT INTO bot_conversations (psid, etat, panier, profil, handoff_jusqua, maj)
                          VALUES (%s,%s,%s,%s,%s,now())
                          ON CONFLICT (psid) DO UPDATE SET etat=EXCLUDED.etat, panier=EXCLUDED.panier,
                          profil=EXCLUDED.profil, handoff_jusqua=EXCLUDED.handoff_jusqua, maj=now()""",
                       (conv["psid"], conv["etat"], json.dumps(conv["panier"], ensure_ascii=False),
                        json.dumps(conv["profil"], ensure_ascii=False, default=str), conv.get("handoff_jusqua")))

    def ajouter_message(self, psid, role, contenu):
        with self.connexion() as cx:
            cx.execute("INSERT INTO bot_messages (psid, role, contenu) VALUES (%s,%s,%s)", (psid, role, contenu))

    def historique(self, psid, n):
        with self.connexion() as cx:
            rows = cx.execute("SELECT role, contenu FROM bot_messages WHERE psid=%s ORDER BY id DESC LIMIT %s",
                              (psid, n)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def marquer_traite(self, mid) -> bool:
        with self.connexion() as cx:
            r = cx.execute("INSERT INTO bot_messages_traites (mid) VALUES (%s) ON CONFLICT DO NOTHING RETURNING mid",
                           (mid,)).fetchone()
            cx.execute("DELETE FROM bot_messages_traites WHERE cree_le < now() - interval '3 days'")
        return r is not None

    def role(self, psid):
        with self.connexion() as cx:
            r = cx.execute("SELECT role FROM bot_utilisateurs WHERE psid=%s", (psid,)).fetchone()
        return r["role"] if r else "client"

    def definir_role(self, psid, role):
        with self.connexion() as cx:
            cx.execute("""INSERT INTO bot_utilisateurs (psid, role, maj) VALUES (%s,%s,now())
                          ON CONFLICT (psid) DO UPDATE SET role=EXCLUDED.role, maj=now()""", (psid, role))

    def inserer_commande(self, cmd) -> str:
        with self.connexion() as cx:
            n = cx.execute("SELECT count(*) AS n FROM bot_commandes WHERE cree_le::date = current_date").fetchone()["n"]
            ref = f"CMD-{dt.date.today():%Y%m%d}-{n + 1:04d}"
            cx.execute("""INSERT INTO bot_commandes (reference, psid, nom, telephone, mode_livraison, quartier, lignes, total)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                       (ref, cmd["psid"], cmd.get("nom"), cmd.get("telephone"), cmd.get("mode_livraison"),
                        cmd.get("quartier"), json.dumps(cmd["lignes"], ensure_ascii=False), cmd["total"]))
        return ref

    def dernieres_commandes(self, n=10):
        with self.connexion() as cx:
            rows = cx.execute("""SELECT reference, nom, telephone, mode_livraison, quartier, lignes, total, statut,
                                 to_char(cree_le AT TIME ZONE 'Africa/Douala', 'DD/MM HH24:MI') AS cree_le
                                 FROM bot_commandes ORDER BY id DESC LIMIT %s""", (n,)).fetchall()
        return [dict(r) for r in rows]

    def produits(self):
        """Catalogue public publié par la synchronisation du PC (table stock_produits)."""
        with self.connexion() as cx:
            rows = cx.execute("""SELECT code, designation, categorie, marque, prix_vente::float AS prix_vente,
                                 stock::float AS stock, seuil::float AS seuil, statut,
                                 coalesce(description,'') AS description, coalesce(usages,'') AS usages,
                                 coalesce(photo_url,'') AS photo_url, prix_min::float AS prix_min
                                 FROM stock_produits ORDER BY code""").fetchall()
        return [dict(r) for r in rows]

    def derniere_synchro(self):
        try:
            with self.connexion() as cx:
                r = cx.execute("SELECT valeur FROM stock_sync_etat WHERE cle='derniere_synchro'").fetchone()
            return r["valeur"] if r else None
        except Exception:
            return None


_base = None


def base():
    global _base
    if _base is None:
        if config.DATABASE_URL:
            _base = BaseNeon(config.DATABASE_URL)
        else:
            log.warning("DATABASE_URL vide : stockage EN MÉMOIRE (tests uniquement, tout est perdu au redémarrage)")
            _base = BaseMemoire()
    return _base


def remplacer_base(nouvelle):
    """Pour les tests."""
    global _base
    _base = nouvelle
