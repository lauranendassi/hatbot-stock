# -*- coding: utf-8 -*-
"""Bot Messenger WeloobeAI — Version production PostgreSQL."""
import os
import json
import datetime
import unicodedata
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify

# ====================================================================
# CONFIG
# ====================================================================
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "weloobe_verify_2026_secure")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

app = Flask(__name__)


# ====================================================================
# UTILITAIRES
# ====================================================================
def normaliser(s):
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for ch in "—–-_,.;:!?()[]'\"/\\":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def contient(low, *mots):
    return any(m in low for m in mots)


def f(n):
    return "{:,}".format(int(n)).replace(",", " ") + " FCFA"


def db():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    return conn


# ====================================================================
# ENVOI DE MESSAGES VERS MESSENGER
# ====================================================================
def envoyer_message(psid, texte):
    url = "https://graph.facebook.com/v20.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    payload = {"recipient": {"id": psid}, "message": {"text": texte}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=10)
        print("  Envoi :", r.status_code, texte[:60])
        return r.status_code == 200
    except Exception as e:
        print("  Erreur envoi :", e)
        return False


# ====================================================================
# LOGIQUE DU BOT
# ====================================================================
def detecter_intention(low):
    if contient(low, "bonjour", "bonsoir", "salut", "hello", "coucou", "hi"):
        return "salutation"
    if contient(low, "aide", "help", "que peux", "options", "commande"):
        return "aide"
    if contient(low, "acheter", "achete", "cherche", "je veux", "besoin", "interesse"):
        return "achat"
    if contient(low, "stock", "dispo", "reste", "combien"):
        return "stock"
    if contient(low, "prix", "cout", "tarif", "coute"):
        return "prix"
    if contient(low, "ordinateur", "pc", "portable", "laptop", "ecran", "ssd", "ram",
                "chargeur", "batterie", "souris", "clavier"):
        return "produit"
    if contient(low, "livraison", "livrer", "delai"):
        return "livraison"
    if contient(low, "merci", "thanks", "parfait"):
        return "merci"
    if contient(low, "au revoir", "bye", "a plus"):
        return "aurevoir"
    return "inconnu"


def trouver_produits(message, limite=5):
    low = normaliser(message)
    tokens = [t for t in low.split() if len(t) >= 3]
    if not tokens:
        return []
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produits")
    produits = cur.fetchall()
    cur.close()
    conn.close()
    scores = []
    for p in produits:
        hay = normaliser(p["nom"] + " " + (p["categorie"] or "") + " " + p["sku"])
        score = sum(len(t) for t in tokens if t in hay)
        if score > 0:
            scores.append((score, dict(p)))
    scores.sort(key=lambda x: -x[0])
    return [p for _, p in scores[:limite]]


def stock_actuel(sku):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produits WHERE sku = %s", (sku,))
    p = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(quantite), 0) AS q FROM ventes WHERE sku = %s", (sku,))
    vendu = cur.fetchone()
    cur.close()
    conn.close()
    if not p:
        return 0
    return (p["stock_initial"] or 0) - (vendu["q"] or 0)


def enregistrer_message(psid, direction, contenu):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (psid, direction, contenu, timestamp) VALUES (%s, %s, %s, %s)",
            (psid, direction, contenu, datetime.datetime.now().isoformat())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Erreur enregistrement message :", e)


def maj_client(psid, intention):
    try:
        conn = db()
        cur = conn.cursor()
        maintenant = datetime.datetime.now().isoformat()
        cur.execute("SELECT * FROM clients WHERE psid = %s", (psid,))
        existant = cur.fetchone()
        if existant:
            cur.execute(
                "UPDATE clients SET derniere_interaction = %s, derniere_intention = %s WHERE psid = %s",
                (maintenant, intention, psid)
            )
        else:
            cur.execute(
                "INSERT INTO clients (psid, derniere_interaction, derniere_intention) VALUES (%s, %s, %s)",
                (psid, maintenant, intention)
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Erreur maj client :", e)


def enregistrer_proposition(psid, sku):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO propositions (psid, sku, timestamp, statut) VALUES (%s, %s, %s, %s)",
            (psid, sku, datetime.datetime.now().isoformat(), "envoyee")
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Erreur enregistrement proposition :", e)


# ====================================================================
# REPONSES
# ====================================================================
def repondre(psid, message):
    low = normaliser(message)
    intention = detecter_intention(low)
    maj_client(psid, intention)

    if intention == "salutation":
        return ("Bonjour 👋 Bienvenue chez WeloobeAI !\n\n"
                "Je suis votre assistant commercial. Je peux vous aider à trouver "
                "un ordinateur, un écran ou un accessoire.\n\n"
                "Dites-moi ce que vous cherchez (ex : « je cherche un PC portable »).")

    if intention == "aide":
        return ("Voici ce que je peux faire :\n\n"
                "🔍 Rechercher un produit — « je cherche un PC portable »\n"
                "💰 Voir un prix — « prix Latitude 5420 »\n"
                "📦 Vérifier la disponibilité\n"
                "🚚 Informations de livraison\n\n"
                "Posez votre question, je m'occupe du reste.")

    if intention in ("achat", "produit"):
        produits = trouver_produits(message, limite=3)
        if not produits:
            return ("Je n'ai pas trouvé de produit correspondant à votre demande 🤔\n\n"
                    "Pouvez-vous préciser le type de matériel recherché ?\n"
                    "Ex : « un PC portable », « un écran 24 pouces », « un SSD »")
        reponse = "Voici ce que je peux vous proposer :\n\n"
        for p in produits:
            stock = stock_actuel(p["sku"])
            dispo = "✅ En stock" if stock > 0 else "⏳ Sur commande"
            reponse += "• {} — {}\n   {} ({})\n\n".format(
                p["nom"].split(" — ")[0], f(p["prix_vente"]), dispo, p["categorie"] or ""
            )
            enregistrer_proposition(psid, p["sku"])
        reponse += "Un produit vous intéresse ? Répondez par son nom."
        return reponse

    if intention == "stock":
        produits = trouver_produits(message)
        if not produits:
            return "De quel produit souhaitez-vous vérifier la disponibilité ?"
        p = produits[0]
        stock = stock_actuel(p["sku"])
        if stock > 0:
            return "📦 {} est disponible. Stock actuel : {} unité(s).\nPrix : {}".format(
                p["nom"].split(" — ")[0], stock, f(p["prix_vente"]))
        return "📦 {} est momentanément en rupture. Voulez-vous être prévenu au réapprovisionnement ?".format(
            p["nom"].split(" — ")[0])

    if intention == "prix":
        produits = trouver_produits(message)
        if not produits:
            return "Quel produit vous intéresse ? Je vous donnerai son prix."
        p = produits[0]
        return "💰 {} est proposé à {}.\n\nSouhaitez-vous que je vous en dise plus sur ses caractéristiques ?".format(
            p["nom"].split(" — ")[0], f(p["prix_vente"]))

    if intention == "livraison":
        return ("🚚 Nous livrons sur Yaoundé et ses environs.\n\n"
                "• Livraison sous 24-48h\n"
                "• Retrait possible en boutique\n\n"
                "Souhaitez-vous commander ?")

    if intention == "merci":
        return "Avec plaisir 😊 N'hésitez pas si vous avez d'autres questions."

    if intention == "aurevoir":
        return "Merci de votre visite ! À bientôt chez WeloobeAI 👋"

    return ("Je n'ai pas bien compris 🤔\n\n"
            "Vous cherchez un ordinateur, un écran ou un accessoire ?\n"
            "Dites-moi simplement ce dont vous avez besoin.\n\n"
            "Tapez « aide » pour voir tout ce que je peux faire.")


# ====================================================================
# ROUTES
# ====================================================================
@app.route("/", methods=["GET"])
def accueil():
    return jsonify({
        "status": "ok",
        "bot": "WeloobeAI Chatbot",
        "timestamp": datetime.datetime.now().isoformat()
    })


@app.route("/webhook", methods=["GET"])
def webhook_verification():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    print("Vérification webhook : mode={}, token={}".format(mode, token))
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("  ✅ Token valide")
        return challenge, 200
    print("  ❌ Token invalide")
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_reception():
    data = request.get_json()
    if data.get("object") != "page":
        return "Not a page event", 404

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            psid = event.get("sender", {}).get("id")
            message = event.get("message", {})
            texte = message.get("text", "")
            if not psid or not texte:
                continue
            print("Message de {} : {}".format(psid, texte))
            enregistrer_message(psid, "in", texte)
            try:
                reponse = repondre(psid, texte)
                enregistrer_message(psid, "out", reponse)
                envoyer_message(psid, reponse)
            except Exception as e:
                print("Erreur traitement :", e)

    return "EVENT_RECEIVED", 200


# ====================================================================
# INITIALISATION DE LA BASE (au premier démarrage)
# ====================================================================
def init_database():
    """Crée les tables si elles n'existent pas, et importe les produits depuis Excel."""
    try:
        conn = db()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS produits (
                sku TEXT PRIMARY KEY,
                nom TEXT NOT NULL,
                categorie TEXT,
                prix_achat INTEGER,
                prix_vente INTEGER,
                stock_initial INTEGER,
                seuil INTEGER
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ventes (
                id SERIAL PRIMARY KEY,
                date TEXT,
                sku TEXT,
                quantite INTEGER,
                client TEXT,
                canal TEXT DEFAULT 'messenger'
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                psid TEXT PRIMARY KEY,
                prenom TEXT,
                nom TEXT,
                derniere_interaction TEXT,
                derniere_intention TEXT,
                contexte TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                psid TEXT,
                direction TEXT,
                contenu TEXT,
                timestamp TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS propositions (
                id SERIAL PRIMARY KEY,
                psid TEXT,
                sku TEXT,
                timestamp TEXT,
                statut TEXT
            )
        """)

        conn.commit()

        # Importer les produits si la table est vide
        cur.execute("SELECT COUNT(*) AS n FROM produits")
        nb = cur.fetchone()["n"]
        if nb == 0:
            try:
                from openpyxl import load_workbook
                wb = load_workbook("Gestion_stock_corrige.xlsx", data_only=False)
                ws = wb["Produits"]
                for r in range(5, 105):
                    sku = ws.cell(r, 1).value
                    nom = ws.cell(r, 2).value
                    if not sku or not nom:
                        continue
                    cur.execute(
                        "INSERT INTO produits (sku, nom, categorie, prix_achat, prix_vente, stock_initial, seuil) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (str(sku), str(nom), ws.cell(r, 11).value or "",
                         int(ws.cell(r, 3).value or 0), int(ws.cell(r, 4).value or 0),
                         int(ws.cell(r, 5).value or 0), int(ws.cell(r, 10).value or 2))
                    )
                conn.commit()
                print("Produits importés depuis Excel.")
            except Exception as e:
                print("Impossible d'importer les produits :", e)

        cur.close()
        conn.close()
        print("Base de données initialisée.")
    except Exception as e:
        print("Erreur init base :", e)


# Initialisation au démarrage
init_database()


# ====================================================================
# LANCEMENT (local)
# ====================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Bot Messenger WeloobeAI")
    print("  Webhook : http://localhost:5000/webhook")
    print("  Token configuré : {}".format("oui" if PAGE_ACCESS_TOKEN else "NON"))
    print("  DB configurée : {}".format("oui" if DATABASE_URL else "NON"))
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)