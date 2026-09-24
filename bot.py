# -*- coding: utf-8 -*-
"""Bot Messenger WeloobeAI — Version IA générative (Groq)"""
import os
import datetime
import unicodedata
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify
from openai import OpenAI

# ====================================================================
# CONFIG
# ====================================================================
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "weloobe_verify_2026_secure")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

app = Flask(__name__)

# Client Groq (compatible OpenAI)
client_ia = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY
)

MODELE = "openai/gpt-oss-120b"


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


def f(n):
    return "{:,}".format(int(n)).replace(",", " ") + " FCFA"


def db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


# ====================================================================
# OUTILS POUR L'IA (function calling)
# ====================================================================
def chercher_produits(requete="", budget_max=0, categorie=""):
    """Cherche des produits dans la base selon des critères."""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produits")
    tous = cur.fetchall()
    cur.close()
    conn.close()

    resultats = []
    tokens = normaliser(requete).split()

    for p in tous:
        hay = normaliser(p["nom"] + " " + (p["categorie"] or ""))
        # Score de correspondance
        score = sum(len(t) for t in tokens if t in hay)
        if categorie and categorie.lower() not in (p["categorie"] or "").lower():
            continue
        if budget_max and p["prix_vente"] > budget_max:
            continue
        if not tokens or score > 0:
            resultats.append({**p, "score": score})

    resultats.sort(key=lambda x: -x.get("score", 0))
    return resultats[:5]


def verifier_stock(sku):
    """Vérifie le stock d'un produit."""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produits WHERE sku = %s", (sku,))
    p = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(quantite), 0) AS q FROM ventes WHERE sku = %s", (sku,))
    vendu = cur.fetchone()
    cur.close()
    conn.close()
    if not p:
        return None
    stock = (p["stock_initial"] or 0) - (vendu["q"] or 0)
    return {"sku": sku, "nom": p["nom"], "stock": stock, "prix": p["prix_vente"]}


def lister_catalogue():
    """Liste tous les produits disponibles."""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produits ORDER BY categorie, nom")
    produits = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(p) for p in produits]


# Définition des outils pour Groq
OUTILS = [
    {
        "type": "function",
        "function": {
            "name": "chercher_produits",
            "description": "Cherche des produits dans le catalogue selon le besoin du client",
            "parameters": {
                "type": "object",
                "properties": {
                    "requete": {"type": "string", "description": "Mots-clés de recherche (ex: 'pc portable', 'écran')"},
                    "budget_max": {"type": "number", "description": "Budget maximum du client en FCFA"},
                    "categorie": {"type": "string", "description": "Catégorie : PC portable, PC fixe, Écran, Composant, Accessoire"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verifier_stock",
            "description": "Vérifie la disponibilité d'un produit par son SKU",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "Code SKU du produit"}
                },
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lister_catalogue",
            "description": "Liste tous les produits du catalogue",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


# ====================================================================
# HISTORIQUE DE CONVERSATION
# ====================================================================
def charger_historique(psid, limite=10):
    """Charge les derniers messages échangés avec ce client."""
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT role, contenu FROM messages WHERE psid = %s "
            "ORDER BY timestamp DESC LIMIT %s",
            (psid, limite)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        # Remettre dans l'ordre chronologique
        rows = list(reversed(rows))
        return [{"role": r["role"], "content": r["contenu"]} for r in rows]
    except Exception as e:
        print("Erreur chargement historique :", e)
        return []


def enregistrer_message(psid, role, contenu):
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (psid, role, contenu, timestamp) VALUES (%s, %s, %s, %s)",
            (psid, role, contenu, datetime.datetime.now().isoformat())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Erreur enregistrement :", e)


# ====================================================================
# LOGIQUE PRINCIPALE
# ====================================================================
def repondre_avec_ia(psid, message_client):
    """Génère une réponse intelligente via Groq."""

    # Système : personnalité du bot
    system_prompt = """Tu es l'assistant commercial de WeloobeAI, magasin de matériel informatique à Yaoundé (Cameroun).

Ton rôle :
- Accueillir chaleureusement les clients
- Comprendre leur besoin en posant des questions si nécessaire
- Leur proposer les produits adaptés de notre catalogue
- Donner les prix en FCFA
- Vérifier la disponibilité

Règles :
- Sois naturel, amical et professionnel
- Utilise des emojis avec modération
- Si le client exprime un besoin, utilise l'outil chercher_produits
- Si le client demande un produit précis, utilise verifier_stock
- Si le client veut voir tout le catalogue, utilise lister_catalogue
- Ne invente JAMAIS de produits ou de prix : utilise toujours les outils
- Réponds en français

Produits disponibles : ordinateurs portables, PC fixes, écrans, composants, accessoires."""

    # Historique
    historique = charger_historique(psid, limite=10)

    messages = [
        {"role": "system", "content": system_prompt},
        *historique,
        {"role": "user", "content": message_client}
    ]

    try:
        # Premier appel : l'IA décide si elle doit utiliser un outil
        response = client_ia.chat.completions.create(
            model=MODELE,
            messages=messages,
            tools=OUTILS,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=500
        )

        msg = response.choices[0].message

        # Si l'IA veut appeler un outil
        if msg.tool_calls:
            for tool_call in msg.tool_calls:
                nom_outil = tool_call.function.name
                args = eval(tool_call.function.arguments) if tool_call.function.arguments else {}

                print("  Outil appelé : {} ({})".format(nom_outil, args))

                # Exécuter l'outil
                if nom_outil == "chercher_produits":
                    resultat = chercher_produits(
                        args.get("requete", ""),
                        args.get("budget_max", 0),
                        args.get("categorie", "")
                    )
                elif nom_outil == "verifier_stock":
                    resultat = verifier_stock(args.get("sku", ""))
                elif nom_outil == "lister_catalogue":
                    resultat = lister_catalogue()
                else:
                    resultat = {"erreur": "outil inconnu"}

                # Renvoyer le résultat à l'IA
                messages.append(msg)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(resultat)
                })

            # Deuxième appel : l'IA formule la réponse finale
            response2 = client_ia.chat.completions.create(
                model=MODELE,
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            return response2.choices[0].message.content

        return msg.content

    except Exception as e:
        print("Erreur IA :", e)
        return "Désolé, je rencontre un petit souci technique. Pouvez-vous reformuler votre demande ?"


def envoyer_message(psid, texte):
    url = "https://graph.facebook.com/v20.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    payload = {"recipient": {"id": psid}, "message": {"text": texte}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=10)
        print("  Envoi :", r.status_code)
        return r.status_code == 200
    except Exception as e:
        print("  Erreur envoi :", e)
        return False


# ====================================================================
# WEBHOOK
# ====================================================================
@app.route("/", methods=["GET"])
def accueil():
    return jsonify({"status": "ok", "bot": "WeloobeAI Chatbot IA"})


@app.route("/webhook", methods=["GET"])
def webhook_verification():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_reception():
    data = request.get_json()
    if data.get("object") != "page":
        return "Not a page event", 404

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            psid = event.get("sender", {}).get("id")
            texte = event.get("message", {}).get("text", "")
            if not psid or not texte:
                continue

            print("Message de {} : {}".format(psid, texte))
            enregistrer_message(psid, "user", texte)

            try:
                reponse = repondre_avec_ia(psid, texte)
                enregistrer_message(psid, "assistant", reponse)
                envoyer_message(psid, reponse)
            except Exception as e:
                print("Erreur traitement :", e)

    return "EVENT_RECEIVED", 200


# ====================================================================
# INIT BASE
# ====================================================================
def init_database():
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
            CREATE TABLE IF NOT EXISTS clients (
                psid TEXT PRIMARY KEY,
                derniere_interaction TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                psid TEXT,
                role TEXT,
                contenu TEXT,
                timestamp TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ventes (
                id SERIAL PRIMARY KEY,
                date TEXT,
                sku TEXT,
                quantite INTEGER,
                client TEXT
            )
        """)

        conn.commit()

        cur.execute("SELECT COUNT(*) AS n FROM produits")
        if cur.fetchone()["n"] == 0:
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
                print("Produits importés.")
            except Exception as e:
                print("Erreur import Excel :", e)

        cur.close()
        conn.close()
    except Exception as e:
        print("Erreur init :", e)


init_database()


if __name__ == "__main__":
    print("=" * 60)
    print("  Bot Messenger WeloobeAI — IA Groq")
    print("  Modèle : " + MODELE)
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)