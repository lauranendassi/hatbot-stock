# -*- coding: utf-8 -*-
"""Bot Messenger WeloobeAI v3
   - Gestion d'etat explicite : recherche / commande / contexte
   - Memoire du dernier produit propose
   - Machine a etats pour la commande
   - Fallback robuste si l'IA echoue
"""
import os
import re
import json
import smtplib
import datetime
import unicodedata
import traceback
import requests
import psycopg2
import psycopg2.extras
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

# ====================================================================
# CONFIG
# ====================================================================
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "weloobe_verify_2026_secure")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
NOTIF_EMAIL = os.environ.get("NOTIF_EMAIL", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://hatbot-stock.onrender.com")

MODELES_GROQ = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]

app = Flask(__name__)

client_ia = None
if GROQ_API_KEY:
    try:
        client_ia = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY
        )
    except Exception as e:
        print("Erreur init Groq :", e)


# ====================================================================
# UTILITAIRES
# ====================================================================
def normaliser(s):
    try:
        s = (s or "").lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        for ch in "—–-_,.;:!?()[]'\"/\\":
            s = s.replace(ch, " ")
        return " ".join(s.split())
    except Exception:
        return ""


def f(n):
    try:
        return "{:,}".format(int(n)).replace(",", " ") + " FCFA"
    except Exception:
        return "0 FCFA"


def db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL non configuree")
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
        connect_timeout=10
    )


def log(prefixe, message):
    try:
        print("[{}] {}".format(prefixe, str(message)[:500]))
    except Exception:
        pass


def formater_reponse(texte):
    if not texte:
        return ""
    try:
        texte = re.sub(r"\*\*(.+?)\*\*", r"\1", texte)
        texte = re.sub(r"\*(.+?)\*", r"\1", texte)
        texte = re.sub(r"__(.+?)__", r"\1", texte)
        texte = re.sub(r"_(.+?)_", r"\1", texte)
        texte = re.sub(r"`(.+?)`", r"\1", texte)
        texte = re.sub(r"^#+\s*", "", texte, flags=re.MULTILINE)
        texte = re.sub(r"^\s*[-*]\s+", "• ", texte, flags=re.MULTILINE)
        texte = re.sub(r"\n{3,}", "\n\n", texte)
        texte = re.sub(r"[ \t]+", " ", texte)
        texte = texte.strip()
        if len(texte) > 1800:
            texte = texte[:1797] + "..."
        return texte
    except Exception as e:
        log("WARN", "formater_reponse: " + str(e))
        return texte[:1800] if texte else ""


# ====================================================================
# PROFIL CLIENT
# ====================================================================
def charger_profil(psid):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM clients WHERE psid = %s", (psid,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return {
                "psid": psid, "nom": None, "telephone": None,
                "commande_en_cours": None, "derniers_produits": None
            }
        profil = dict(row)
        for champ in ["commande_en_cours", "derniers_produits"]:
            if profil.get(champ):
                try:
                    profil[champ] = json.loads(profil[champ])
                except Exception:
                    profil[champ] = None
        return profil
    except Exception as e:
        log("WARN", "charger_profil: " + str(e))
        return {"psid": psid}


def maj_profil(psid, **kwargs):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT psid FROM clients WHERE psid = %s", (psid,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO clients (psid, derniere_interaction) VALUES (%s, %s)",
                (psid, datetime.datetime.now().isoformat())
            )
        champs = []
        valeurs = []
        for cle, val in kwargs.items():
            champs.append("{} = %s".format(cle))
            if isinstance(val, (list, dict)):
                valeurs.append(json.dumps(val))
            else:
                valeurs.append(val)
        if champs:
            valeurs.extend([datetime.datetime.now().isoformat(), psid])
            cur.execute(
                "UPDATE clients SET {}, derniere_interaction = %s WHERE psid = %s".format(
                    ", ".join(champs)),
                valeurs
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log("WARN", "maj_profil: " + str(e))


# ====================================================================
# DETECTION D'INTENTION
# ====================================================================
def detecter_intention(message):
    low = normaliser(message)

    if any(m in low for m in ["bonjour", "bonsoir", "salut", "hello", "coucou", "hi"]):
        return "salutation"

    if any(m in low for m in ["annule", "annuler", "laisse tomber", "oublie", "stop"]):
        return "annulation"

    if low.strip() in ["oui", "ok", "d'accord", "daccord", "confirme", "confirmation"]:
        return "confirmation"

    if any(m in low for m in [
        "commander", "commande", "passe ma", "je veux commander",
        "je prends", "je vais prendre", "je veux acheter", "acheter",
        "passe commande", "valide"
    ]):
        return "commande"

    tel_clean = re.sub(r"[^\d]", "", low)
    tel_clean = re.sub(r"^(237|00237)", "", tel_clean)
    if re.match(r"^6\d{8}$", tel_clean):
        return "fournir_tel"

    if any(m in low for m in ["celui", "celle", "celui-ci", "celui la"]):
        return "reference_produit"

    if any(m in low for m in [
        "cherche", "veux", "besoin", "montre", "affiche", "liste",
        "catalogue", "stock", "prix", "combien", "dispo", "pc",
        "ordinateur", "portable", "ecran", "moniteur", "ssd", "ram",
        "accessoire", "composant", "fixe", "tour"
    ]):
        return "recherche"

    return "autre"


def identifier_produit_par_nom(message, derniers_produits):
    if not derniers_produits:
        return None
    low = normaliser(message)
    for p in derniers_produits:
        nom_norm = normaliser(p.get("nom") or "")
        mots = [m for m in nom_norm.split() if len(m) >= 3]
        for mot in mots:
            if mot in low:
                return p
    return None


# ====================================================================
# OUTILS
# ====================================================================
def chercher_produits(requete="", budget_max=0, categorie=""):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM produits")
        tous = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        log("ERR", "chercher_produits DB: " + str(e))
        return [{"erreur": "Base momentanement indisponible"}]

    try:
        tokens = [t for t in normaliser(requete).split() if len(t) >= 3]
        try:
            budget_max = float(budget_max) if budget_max else 0
        except Exception:
            budget_max = 0
        cat_norm = normaliser(categorie) if categorie else ""

        candidats = []
        for p in tous:
            cat_prod_norm = normaliser(p.get("categorie") or "")
            if cat_norm and cat_norm not in cat_prod_norm and cat_prod_norm not in cat_norm:
                continue
            candidats.append(p)

        if tokens:
            filtres = []
            for p in candidats:
                hay = normaliser((p.get("nom") or "") + " " + (p.get("categorie") or ""))
                score = sum(len(t) for t in tokens if t in hay)
                if score > 0:
                    filtres.append((score, p))
            if filtres:
                filtres.sort(key=lambda x: -x[0])
                candidats = [p for _, p in filtres]

        if budget_max:
            candidats = [p for p in candidats if (p.get("prix_vente") or 0) <= budget_max]

        candidats.sort(key=lambda p: p.get("prix_vente") or 0)

        if candidats:
            return [{
                "sku": p.get("sku"),
                "nom": p.get("nom"),
                "categorie": p.get("categorie"),
                "prix": p.get("prix_vente")
            } for p in candidats[:3]]

        return [{
            "aucun_resultat": True,
            "categorie_demandee": categorie,
            "budget_demande": budget_max,
            "message": "Aucun produit ne correspond."
        }]
    except Exception as e:
        log("ERR", "chercher_produits traitement: " + str(e))
        return [{"erreur": "Erreur lors de la recherche"}]


def verifier_stock(sku):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM produits WHERE sku = %s", (sku,))
        p = cur.fetchone()
        cur.execute("SELECT COALESCE(SUM(quantite), 0) AS q FROM ventes WHERE sku = %s", (sku,))
        vendu = cur.fetchone()
        cur.close()
        conn.close()
        if not p:
            return {"erreur": "Produit inconnu"}
        stock = (p.get("stock_initial") or 0) - (vendu.get("q") or 0)
        return {"sku": sku, "nom": p.get("nom"), "stock": stock,
                "prix": p.get("prix_vente"), "disponible": stock > 0}
    except Exception as e:
        log("ERR", "verifier_stock: " + str(e))
        return {"erreur": "Impossible de verifier le stock"}


def lister_catalogue():
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM produits ORDER BY categorie, nom")
        produits = cur.fetchall()
        cur.close()
        conn.close()
        return [{"sku": p.get("sku"), "nom": p.get("nom"),
                 "categorie": p.get("categorie"), "prix": p.get("prix_vente")}
                for p in produits]
    except Exception as e:
        log("ERR", "lister_catalogue: " + str(e))
        return []


def demarrer_commande(psid, sku, quantite=1):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT nom, prix_vente FROM produits WHERE sku = %s", (sku,))
        p = cur.fetchone()
        cur.close()
        conn.close()
        if not p:
            return {"erreur": "Produit inconnu"}

        commande = {
            "sku": sku,
            "nom_produit": p.get("nom"),
            "prix_unitaire": p.get("prix_vente"),
            "quantite": quantite,
            "etape": "nom"
        }
        maj_profil(psid, commande_en_cours=commande)
        return {"succes": True, "commande": commande}
    except Exception as e:
        log("ERR", "demarrer_commande: " + str(e))
        return {"erreur": "Impossible de demarrer"}


def valider_telephone(tel):
    if not tel:
        return False
    tel_clean = re.sub(r"[^\d]", "", str(tel))
    tel_clean = re.sub(r"^(237|00237)", "", tel_clean)
    return bool(re.match(r"^6\d{8}$", tel_clean))


def finaliser_commande(psid, nom_client, telephone, notes=""):
    try:
        profil = charger_profil(psid)
        commande = profil.get("commande_en_cours")
        if not commande:
            return {"erreur": "Aucune commande en cours"}

        if not valider_telephone(telephone):
            return {"erreur": "telephone_invalide",
                    "message": "Le numero n'est pas valide. Format attendu : 6XX XXX XXX (9 chiffres commencant par 6)."}

        sku = commande.get("sku")
        quantite = int(commande.get("quantite") or 1)
        prix_unitaire = commande.get("prix_unitaire") or 0
        prix_total = prix_unitaire * quantite

        conn = db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO commandes
            (psid, nom_client, telephone, sku, nom_produit, quantite,
             prix_unitaire, prix_total, statut, notes, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (psid, nom_client, telephone, sku, commande.get("nom_produit"),
              quantite, prix_unitaire, prix_total, "nouvelle", notes,
              datetime.datetime.now().isoformat()))
        commande_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
        conn.close()

        maj_profil(psid, commande_en_cours=None, nom=nom_client, telephone=telephone)
        envoyer_notification_commande(commande_id, nom_client, telephone,
                                       commande.get("nom_produit"), quantite,
                                       prix_total, notes)

        return {"succes": True, "commande_id": commande_id,
                "produit": commande.get("nom_produit"),
                "quantite": quantite, "prix_total": prix_total}
    except Exception as e:
        log("ERR", "finaliser_commande: " + str(e))
        return {"erreur": "Impossible d'enregistrer"}


def envoyer_notification_commande(commande_id, nom, telephone, produit,
                                    quantite, prix_total, notes=""):
    if not SMTP_USER or not SMTP_PASSWORD or not NOTIF_EMAIL:
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = NOTIF_EMAIL
        msg["Subject"] = "Nouvelle commande #{} - WeloobeAI".format(commande_id)
        corps = """Nouvelle commande recue via Messenger.

Commande #{cid}
Client      : {nom}
Telephone   : {tel}
Produit     : {prod}
Quantite    : {qte}
Prix total  : {prix}
Notes       : {notes}""".format(
            cid=commande_id, nom=nom, tel=telephone, prod=produit,
            qte=quantite, prix=prix_total, notes=notes or "aucune")
        msg.attach(MIMEText(corps, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log("EMAIL", "Notification envoyee")
        return True
    except Exception as e:
        log("ERR", "envoyer_notification: " + str(e))
        return False


OUTILS = [
    {"type": "function", "function": {
        "name": "chercher_produits",
        "description": "Cherche des produits dans le catalogue",
        "parameters": {"type": "object", "properties": {
            "requete": {"type": "string"},
            "budget_max": {"type": "number"},
            "categorie": {"type": "string"}
        }}}},
    {"type": "function", "function": {
        "name": "verifier_stock",
        "description": "Verifie la disponibilite d'un produit",
        "parameters": {"type": "object", "properties": {"sku": {"type": "string"}},
                       "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "lister_catalogue",
        "description": "Liste tout le catalogue",
        "parameters": {"type": "object", "properties": {}}}},
]


# ====================================================================
# HISTORIQUE
# ====================================================================
def charger_historique(psid, limite=20):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT role, contenu FROM messages WHERE psid = %s "
            "ORDER BY timestamp DESC LIMIT %s",
            (psid, limite))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        rows = list(reversed(rows))
        return [{"role": r.get("role") or "user", "content": r.get("contenu") or ""}
                for r in rows]
    except Exception as e:
        log("WARN", "charger_historique: " + str(e))
        return []


def enregistrer_message(psid, role, contenu):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (psid, role, contenu, timestamp) VALUES (%s, %s, %s, %s)",
            (psid, role, contenu or "", datetime.datetime.now().isoformat()))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log("WARN", "enregistrer_message: " + str(e))


# ====================================================================
# SYSTEM PROMPT
# ====================================================================
SYSTEM_PROMPT = """Tu es l'assistant commercial de WeloobeAI, magasin de materiel informatique a Yaounde.

REGLES :
1. Tu ne proposes QUE les produits retournes par les outils.
2. INTERDICTION de mentionner : Chromebook, tablette, iPad, reconditionne, occasion.
3. Categories reelles : PC portable, PC fixe, Ecran, Composant, Accessoire.
4. N'invente JAMAIS de prix.

FORMAT :
- Pas d'asterisques, pas de **, pas de #
- Puces simples avec le caractere •
- Reponses courtes (1-3 phrases + liste si necessaire)
- Un emoji maximum

COMPORTEMENT :
- Client cherche un produit : utilise chercher_produits, presente 2-3 produits avec leur prix REEL
- Client dit "je veux commander" : NE RELANCE PAS la recherche de produits
- Reponds toujours en francais"""


# ====================================================================
# APPEL IA
# ====================================================================
def appeler_ia(messages, avec_outils=True, force_outil=False):
    if not client_ia:
        return None
    for modele in MODELES_GROQ:
        try:
            kwargs = {
                "model": modele, "messages": messages,
                "temperature": 0.4, "max_tokens": 500
            }
            if avec_outils:
                kwargs["tools"] = OUTILS
                kwargs["tool_choice"] = "required" if force_outil else "auto"
            return client_ia.chat.completions.create(**kwargs)
        except Exception as e:
            log("WARN", "Modele {} echoue : {}".format(modele, str(e)[:200]))
            continue
    return None


# ====================================================================
# LOGIQUE PRINCIPALE
# ====================================================================
def repondre_avec_ia(psid, message_client):
    if not client_ia:
        return "Bonjour ! Le service est en cours de configuration."

    try:
        profil = charger_profil(psid)
        intention = detecter_intention(message_client)
        derniers_produits = profil.get("derniers_produits") or []
        commande = profil.get("commande_en_cours")

        log("INTENTION", "{} -> {}".format(message_client[:40], intention))

        # ============================================================
        # CAS 1 : COMMANDE EN COURS
        # ============================================================
        if commande:
            etape = commande.get("etape")

            if intention == "annulation":
                maj_profil(psid, commande_en_cours=None)
                return "Pas de probleme, j'ai annule la commande."

            if etape == "nom":
                if intention == "fournir_tel":
                    return "J'ai d'abord besoin de votre nom complet. Quel est-il ?"
                if intention == "commande":
                    return "Votre commande est deja en cours. Quel est votre nom complet ?"
                nom = message_client.strip()
                if len(nom) < 2:
                    return "Pouvez-vous me donner votre nom complet ?"
                commande["etape"] = "telephone"
                commande["nom_client"] = nom
                maj_profil(psid, commande_en_cours=commande, nom=nom)
                return "Merci {} ! Quel est votre numero de telephone (format : 6XX XXX XXX) ?".format(nom)

            if etape == "telephone":
                if intention == "commande":
                    return "Quel est votre numero de telephone ?"

                tel_clean = re.sub(r"[^\d]", "", message_client)
                tel_clean = re.sub(r"^(237|00237)", "", tel_clean)

                if re.match(r"^6\d{8}$", tel_clean):
                    resultat = finaliser_commande(
                        psid, commande.get("nom_client"), tel_clean
                    )
                    if resultat.get("succes"):
                        return ("Commande #{} enregistree !\n\n"
                                "Produit : {}\n"
                                "Quantite : {}\n"
                                "Total : {}\n\n"
                                "Un conseiller vous rappellera au {} dans les plus brefs delais.").format(
                            resultat["commande_id"], resultat["produit"],
                            resultat["quantite"], f(resultat["prix_total"]),
                            tel_clean
                        )
                    return "Erreur lors de l'enregistrement. Reformulez svp."

                return "Le numero n'est pas valide. Format attendu : 6XX XXX XXX (9 chiffres commencant par 6)."

        # ============================================================
        # CAS 2 : INTENTION DE COMMANDE
        # ============================================================
        if intention == "commande":
            produit_cible = identifier_produit_par_nom(message_client, derniers_produits)
            if not produit_cible and len(derniers_produits) == 1:
                produit_cible = derniers_produits[0]

            if not produit_cible:
                if derniers_produits:
                    liste = "\n".join("• {} - {}".format(p["nom"], f(p["prix"]))
                                     for p in derniers_produits)
                    return "Quel produit souhaitez-vous commander ?\n\n" + liste
                return "Quel produit souhaitez-vous commander ?"

            resultat = demarrer_commande(psid, produit_cible["sku"], 1)
            if resultat.get("succes"):
                return "Tres bon choix !\n\n{} a {}.\n\nQuel est votre nom complet ?".format(
                    produit_cible["nom"], f(produit_cible["prix"]))
            return "Je n'arrive pas a demarrer la commande. Reformulez svp."

        # ============================================================
        # CAS 3 : REFERENCE A UN PRODUIT
        # ============================================================
        if intention == "reference_produit" and derniers_produits:
            produit = identifier_produit_par_nom(message_client, derniers_produits)
            if produit:
                return "{} a {}.\n\nSouhaitez-vous le commander ?".format(
                    produit["nom"], f(produit["prix"]))

        # ============================================================
        # CAS 4 : RECHERCHE DE PRODUITS
        # ============================================================
        historique = charger_historique(psid, limite=10)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *historique,
            {"role": "user", "content": message_client}
        ]

        forcer = intention == "recherche"
        response = appeler_ia(messages, avec_outils=True, force_outil=forcer)
        if not response:
            return "Je n'ai pas bien compris. Pouvez-vous reformuler ?"

        try:
            msg = response.choices[0].message
        except Exception:
            return "Je n'ai pas bien compris."

        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name,
                                 "arguments": tc.function.arguments}
                } for tc in tool_calls]
            })

            produits_trouves = None

            for tool_call in tool_calls:
                try:
                    nom_outil = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments or "{}")
                    except Exception:
                        args = {}
                    log("OUTIL", "{} ({})".format(nom_outil, args))

                    if nom_outil == "chercher_produits":
                        resultat = chercher_produits(
                            args.get("requete", ""),
                            args.get("budget_max", 0),
                            args.get("categorie", "")
                        )
                        if isinstance(resultat, list) and resultat and not resultat[0].get("aucun_resultat"):
                            produits_trouves = resultat
                    elif nom_outil == "verifier_stock":
                        resultat = verifier_stock(args.get("sku", ""))
                    elif nom_outil == "lister_catalogue":
                        resultat = lister_catalogue()
                    else:
                        resultat = {"erreur": "outil inconnu"}

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(resultat, ensure_ascii=False, default=str)
                    })
                except Exception as e:
                    log("ERR", "traitement tool: " + str(e))

            if produits_trouves:
                maj_profil(psid, derniers_produits=produits_trouves)

            try:
                response2 = client_ia.chat.completions.create(
                    model=MODELES_GROQ[0],
                    messages=messages,
                    temperature=0.4,
                    max_tokens=500
                )
                reponse = response2.choices[0].message.content
            except Exception:
                if produits_trouves:
                    reponse = "Voici ce que je vous propose :\n\n"
                    for p in produits_trouves[:3]:
                        reponse += "• {} - {}\n".format(p["nom"], f(p["prix"]))
                    reponse += "\nLequel vous interesse ?"
                else:
                    reponse = "Je n'ai pas trouve de produit correspondant."

            return formater_reponse(reponse)

        return formater_reponse(msg.content or "Pouvez-vous reformuler ?")

    except Exception as e:
        log("ERR", "repondre_avec_ia: " + str(e))
        log("ERR", traceback.format_exc()[:500])
        return "Je rencontre un souci technique. Reformulez svp."


# ====================================================================
# MESSENGER
# ====================================================================
def envoyer_message(psid, texte):
    if not PAGE_ACCESS_TOKEN:
        return False
    try:
        url = "https://graph.facebook.com/v20.0/me/messages"
        params = {"access_token": PAGE_ACCESS_TOKEN}
        texte = (texte or "")[:1900]
        payload = {"recipient": {"id": psid}, "message": {"text": texte}}
        r = requests.post(url, params=params, json=payload, timeout=10)
        log("ENVOI", "{} - {}".format(r.status_code, texte[:60]))
        return r.status_code == 200
    except Exception as e:
        log("ERR", "envoyer_message: " + str(e))
        return False


# ====================================================================
# ROUTES
# ====================================================================
@app.route("/", methods=["GET"])
def accueil():
    return jsonify({
        "status": "ok", "version": "v3",
        "bot": "WeloobeAI Chatbot",
        "ia": "connectee" if client_ia else "non configuree",
        "db": "connectee" if DATABASE_URL else "non configuree",
        "email": "configure" if SMTP_USER else "non configure"
    })


@app.route("/webhook", methods=["GET"])
def webhook_verification():
    try:
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge or "", 200
        return "Forbidden", 403
    except Exception as e:
        log("ERR", "webhook_verification: " + str(e))
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_reception():
    try:
        data = request.get_json(silent=True) or {}
        if data.get("object") != "page":
            return "Not a page event", 404

        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                try:
                    psid = event.get("sender", {}).get("id")
                    texte = event.get("message", {}).get("text", "")
                    if not psid or not texte:
                        continue
                    log("RECU", "{} : {}".format(psid, texte))
                    enregistrer_message(psid, "user", texte)
                    reponse = repondre_avec_ia(psid, texte)
                    if not reponse:
                        reponse = "Pouvez-vous reformuler ?"
                    enregistrer_message(psid, "assistant", reponse)
                    envoyer_message(psid, reponse)
                except Exception as e:
                    log("ERR", "traitement event: " + str(e))
                    continue
        return "EVENT_RECEIVED", 200
    except Exception as e:
        log("ERR", "webhook_reception: " + str(e))
        return "EVENT_RECEIVED", 200


# ====================================================================
# INIT BASE
# ====================================================================
def init_database():
    if not DATABASE_URL:
        return
    try:
        conn = db()
        cur = conn.cursor()

        cur.execute("""CREATE TABLE IF NOT EXISTS produits (
            sku TEXT PRIMARY KEY, nom TEXT NOT NULL, categorie TEXT,
            prix_achat INTEGER, prix_vente INTEGER,
            stock_initial INTEGER, seuil INTEGER)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS clients (
            psid TEXT PRIMARY KEY, nom TEXT, telephone TEXT, budget INTEGER,
            derniers_produits TEXT, commande_en_cours TEXT,
            derniere_interaction TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY, psid TEXT, role TEXT,
            contenu TEXT, timestamp TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS ventes (
            id SERIAL PRIMARY KEY, date TEXT, sku TEXT,
            quantite INTEGER, client TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS commandes (
            id SERIAL PRIMARY KEY, psid TEXT, nom_client TEXT, telephone TEXT,
            sku TEXT, nom_produit TEXT, quantite INTEGER,
            prix_unitaire INTEGER, prix_total INTEGER,
            statut TEXT DEFAULT 'nouvelle', notes TEXT, timestamp TEXT)""")

        for alter in [
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS nom TEXT",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS telephone TEXT",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS derniers_produits TEXT",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS commande_en_cours TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS role TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS timestamp TEXT",
        ]:
            try:
                cur.execute(alter)
            except Exception:
                pass

        conn.commit()

        cur.execute("SELECT COUNT(*) AS n FROM produits")
        if (cur.fetchone().get("n") or 0) == 0:
            try:
                from openpyxl import load_workbook
                wb = load_workbook("Gestion_stock_corrige.xlsx", data_only=False)
                ws = wb["Produits"]
                importes = 0
                for r in range(5, 105):
                    try:
                        sku = ws.cell(r, 1).value
                        nom = ws.cell(r, 2).value
                        if not sku or not nom:
                            continue
                        cur.execute(
                            "INSERT INTO produits (sku, nom, categorie, prix_achat, prix_vente, stock_initial, seuil) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (sku) DO NOTHING",
                            (str(sku), str(nom), ws.cell(r, 11).value or "",
                             int(ws.cell(r, 3).value or 0),
                             int(ws.cell(r, 4).value or 0),
                             int(ws.cell(r, 5).value or 0),
                             int(ws.cell(r, 10).value or 2)))
                        importes += 1
                    except Exception:
                        continue
                conn.commit()
                log("DB", "{} produits importes".format(importes))
            except Exception as e:
                log("WARN", "Import Excel: " + str(e))

        cur.close()
        conn.close()
        log("DB", "Base v3 initialisee")
    except Exception as e:
        log("ERR", "init_database: " + str(e))


try:
    init_database()
except Exception as e:
    log("ERR", "init_database global: " + str(e))


# ====================================================================
# LANCEMENT
# ====================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Bot Messenger WeloobeAI v3")
    print("  IA    : {}".format("connectee" if client_ia else "NON"))
    print("  DB    : {}".format("connectee" if DATABASE_URL else "NON"))
    print("  Email : {}".format("configure" if SMTP_USER else "NON"))
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)