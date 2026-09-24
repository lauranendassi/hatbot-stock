# -*- coding: utf-8 -*-
"""Bot Messenger WeloobeAI — IA Groq robuste, naturelle, anti-hallucination.
   - Force l'appel d'outils sur les questions produits
   - Filtre categorie robuste (accents, casse)
   - Reponses honnetes et naturelles
"""
import os
import json
import datetime
import unicodedata
import traceback
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

MODELES_GROQ = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
    "llama-3.1-70b-versatile",
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


# ====================================================================
# OUTILS POUR L'IA
# ====================================================================
def chercher_produits(requete="", budget_max=0, categorie=""):
    """Cherche des produits. Filtre robuste par categorie, mots-cles et budget."""
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

        # Etape 1 : filtrer par categorie
        candidats = []
        for p in tous:
            cat_prod_norm = normaliser(p.get("categorie") or "")
            if cat_norm:
                if cat_norm not in cat_prod_norm and cat_prod_norm not in cat_norm:
                    continue
            candidats.append(p)

        # Etape 2 : filtrer par mots-cles
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

        # Etape 3 : filtrer par budget
        if budget_max:
            candidats = [p for p in candidats if (p.get("prix_vente") or 0) <= budget_max]

        # Etape 4 : trier par prix croissant
        candidats.sort(key=lambda p: p.get("prix_vente") or 0)

        # Resultats trouves
        if candidats:
            return [{
                "sku": p.get("sku"),
                "nom": p.get("nom"),
                "categorie": p.get("categorie"),
                "prix": p.get("prix_vente")
            } for p in candidats[:5]]

        # Aucun resultat : message honnete avec le VRAI prix minimum de la categorie
        if cat_norm:
            prix_cat = []
            for p in tous:
                cat_p_norm = normaliser(p.get("categorie") or "")
                if cat_norm in cat_p_norm or cat_p_norm in cat_norm:
                    prix = p.get("prix_vente") or 0
                    if prix > 0:
                        prix_cat.append(prix)
            prix_min = min(prix_cat) if prix_cat else 0

            return [{
                "aucun_resultat": True,
                "categorie_demandee": categorie,
                "budget_demande": budget_max,
                "prix_minimum_cette_categorie": prix_min,
                "instruction": "Aucun produit de cette categorie ne rentre dans le budget. Cite le prix REEL minimum et propose UNE alternative REELLE du catalogue. N'invente AUCUN produit ni marque."
            }]

        # Aucune categorie : donner les vraies categories du catalogue
        categories_reelles = {}
        for p in tous:
            cat = p.get("categorie") or "Autre"
            prix = p.get("prix_vente") or 0
            if prix > 0:
                if cat not in categories_reelles:
                    categories_reelles[cat] = prix
                else:
                    categories_reelles[cat] = min(categories_reelles[cat], prix)

        return [{
            "aucun_resultat": True,
            "budget_demande": budget_max,
            "categories_disponibles": [
                {"nom": cat, "prix_min": prix}
                for cat, prix in sorted(categories_reelles.items(), key=lambda x: x[1])
            ],
            "instruction": "Aucun produit ne correspond. Cite UNIQUEMENT les categories REELLES avec leur prix minimum REEL. N'invente RIEN."
        }]

    except Exception as e:
        log("ERR", "chercher_produits traitement: " + str(e))
        return [{"erreur": "Erreur lors de la recherche"}]


def verifier_stock(sku):
    """Verifie le stock d'un produit."""
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
            return {"erreur": "Produit inconnu dans le catalogue"}
        stock = (p.get("stock_initial") or 0) - (vendu.get("q") or 0)
        return {
            "sku": sku,
            "nom": p.get("nom"),
            "stock": stock,
            "prix": p.get("prix_vente"),
            "disponible": stock > 0
        }
    except Exception as e:
        log("ERR", "verifier_stock: " + str(e))
        return {"erreur": "Impossible de verifier le stock"}


def lister_catalogue():
    """Liste tout le catalogue."""
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM produits ORDER BY categorie, nom")
        produits = cur.fetchall()
        cur.close()
        conn.close()
        if not produits:
            return [{"info": "Catalogue vide"}]
        return [{
            "sku": p.get("sku"),
            "nom": p.get("nom"),
            "categorie": p.get("categorie"),
            "prix": p.get("prix_vente")
        } for p in produits]
    except Exception as e:
        log("ERR", "lister_catalogue: " + str(e))
        return [{"erreur": "Catalogue indisponible"}]


OUTILS = [
    {
        "type": "function",
        "function": {
            "name": "chercher_produits",
            "description": "Cherche des produits dans le catalogue WeloobeAI. Filtre par categorie, mots-cles et budget. Retourne UNIQUEMENT les produits existants.",
            "parameters": {
                "type": "object",
                "properties": {
                    "requete": {"type": "string", "description": "Mots-cles de recherche"},
                    "budget_max": {"type": "number", "description": "Budget maximum en FCFA (0 si non specifie)"},
                    "categorie": {"type": "string", "description": "Categorie : PC portable, PC fixe, Ecran, Composant ou Accessoire"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verifier_stock",
            "description": "Verifie la disponibilite d'un produit par son SKU",
            "parameters": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lister_catalogue",
            "description": "Liste TOUS les produits du catalogue WeloobeAI",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


# ====================================================================
# HISTORIQUE
# ====================================================================
def charger_historique(psid, limite=10):
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
            (psid, role, contenu or "", datetime.datetime.now().isoformat())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log("WARN", "enregistrer_message: " + str(e))


# ====================================================================
# SYSTEM PROMPT
# ====================================================================
SYSTEM_PROMPT = """Tu es l'assistant commercial de WeloobeAI, magasin de materiel informatique a Yaounde, Cameroun.

=== REGLES ABSOLUES ===
1. Tu ne proposes QUE les produits retournes par les outils. Tu n'inventes JAMAIS un produit, marque, modele ou prix.
2. INTERDICTION FORMELLE de mentionner : Chromebook, tablette, iPad, reconditionne, occasion, partenaire externe, pack etudiant, carte graphique, sac, casque, support ecran.
3. Tu ne mentionnes QUE les categories reelles : PC portable, PC fixe, Ecran, Composant, Accessoire.
4. JAMAIS de produits d'occasion ou reconditionnes.

=== COMPORTEMENT NATUREL ===

Quand un client cherche un produit SANS budget :
- Utilise chercher_produits avec la categorie appropriee
- Presente 2-3 modeles avec LEUR PRIX REEL
- Ne demande pas de budget d'abord : montre ce que tu as

Quand un client demande un produit ABSENT du catalogue (cable HDMI, souris, sac...) :
- Dis simplement : "Nous n'avons pas ce produit en catalogue."
- NE MENTIONNE PAS de prix, meme pour dire "0 FCFA"
- Propose ce que tu as REELLEMENT dans la MEME categorie
  - Exemple : client demande cable HDMI -> propose nos accessoires reels
  - Exemple : client demande sac -> dis que tu n'en as pas et propose autre chose
- Ne saute PAS d'une categorie a l'autre (pas de "on a des PC portables" quand on parle d'accessoires)

Quand un client donne un budget TROP BAS :
- Cite le prix REEL du produit le moins cher de la categorie
- Propose UNE seule alternative REELLE
- Exemple : "Nos PC portables demarrent a 465 000 FCFA. Preferez-vous voir nos ecrans a partir de 120 000 FCFA ?"

Quand un client decrit un USAGE precis :
- Cherche les produits adaptes
- Explique POURQUOI ce modele convient
- Ne pose pas 3 questions d'un coup

Quand un client dit juste "bonjour" :
- Reponse courte et chaleureuse (1-2 phrases)
- Demande ce qu'il cherche

Quand un client confirme un achat :
- Felicite, recapitule le produit et le prix
- Propose de passer commande : "Souhaitez-vous commander ? Un conseiller vous contactera."

Quand un client pose une question vague :
- Demande une precision, mais UNE seule question a la fois
- Exemple : "Vous cherchez pour quel usage ?" (pas 3 questions d'un coup)

=== STYLE ===
- Naturel, direct, chaleureux
- Phrases courtes, pas de listes a rallonge
- Ne repete PAS "je suis la pour vous aider" ou "je suis a votre disposition"
- Un seul emoji maximum par reponse
- Reponds toujours en francais

=== EXEMPLES DE REPONSES NATURELLES ===

Client : "montre-moi les ecrans"
Toi : [appelle chercher_produits(categorie="Ecran")]
      "Voici nos ecrans disponibles : [liste avec prix reels]. Lequel vous interesse ?"

Client : "je cherche un PC pour ma fille etudiante"
Toi : [appelle chercher_produits(categorie="PC portable")]
      "Voici ce que nous avons : [liste avec prix reels]. Quel budget avez-vous ?"

=== CATEGORIES REELLES ===
PC portable, PC fixe, Ecran, Composant, Accessoire."""


# ====================================================================
# APPEL IA
# ====================================================================
def appeler_ia(messages, avec_outils=True, force_outil=False):
    """Appelle Groq avec fallback. Si force_outil, exige l'appel d'un outil."""
    if not client_ia:
        return None

    for modele in MODELES_GROQ:
        try:
            kwargs = {
                "model": modele,
                "messages": messages,
                "temperature": 0.5,
                "max_tokens": 500
            }
            if avec_outils:
                kwargs["tools"] = OUTILS
                kwargs["tool_choice"] = "required" if force_outil else "auto"

            response = client_ia.chat.completions.create(**kwargs)
            log("IA", "Modele utilise : " + modele + (" [outil force]" if force_outil else ""))
            return response
        except Exception as e:
            log("WARN", "Modele {} echoue : {}".format(modele, str(e)[:200]))
            continue
    return None


def executer_outil(nom, args):
    """Execute un outil."""
    try:
        if nom == "chercher_produits":
            return chercher_produits(
                args.get("requete", ""),
                args.get("budget_max", 0),
                args.get("categorie", "")
            )
        elif nom == "verifier_stock":
            return verifier_stock(args.get("sku", ""))
        elif nom == "lister_catalogue":
            return lister_catalogue()
        return {"erreur": "outil inconnu"}
    except Exception as e:
        log("ERR", "executer_outil: " + str(e))
        return {"erreur": "Erreur execution outil"}


def repondre_avec_ia(psid, message_client):
    """Genere une reponse. Ne plante jamais."""
    if not client_ia:
        return "Bonjour ! Je suis l'assistant WeloobeAI. Le service est en cours de configuration."

    try:
        historique = charger_historique(psid, limite=10)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(historique)
        messages.append({"role": "user", "content": message_client})

        # Detecter si la question porte sur un produit -> forcer l'appel d'outil
        low = normaliser(message_client)
        mots_produits = ["pc", "ordinateur", "portable", "ecran", "moniteur", "ssd", "ram",
                         "batterie", "chargeur", "accessoire", "composant", "fixe", "tour",
                         "montre", "affiche", "liste", "catalogue", "stock", "prix", "cherche",
                         "veux", "besoin", "dispo", "combien"]
        forcer = any(m in low for m in mots_produits)

        response = appeler_ia(messages, avec_outils=True, force_outil=forcer)
        if not response:
            return "Je rencontre un souci technique. Pouvez-vous reformuler ?"

        try:
            msg = response.choices[0].message
        except Exception:
            return "Je n'ai pas bien compris. Pouvez-vous reformuler ?"

        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in tool_calls
                ]
            })

            for tool_call in tool_calls:
                try:
                    nom_outil = tool_call.function.name
                    args_str = tool_call.function.arguments or "{}"
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}

                    log("OUTIL", "{} ({})".format(nom_outil, args))
                    resultat = executer_outil(nom_outil, args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(resultat, ensure_ascii=False, default=str)
                    })
                except Exception as e:
                    log("ERR", "traitement tool_call: " + str(e))
                    continue

            # Deuxieme appel (sans forcer, sans outils) pour formuler la reponse
            response2 = appeler_ia(messages, avec_outils=False)
            if response2:
                try:
                    contenu = response2.choices[0].message.content
                    if contenu:
                        return contenu
                except Exception:
                    pass

            return "J'ai trouve des produits mais je n'arrive pas a formuler la reponse. Reformulez svp."

        return msg.content or "Je n'ai pas bien compris."

    except Exception as e:
        log("ERR", "repondre_avec_ia: " + str(e))
        log("ERR", traceback.format_exc()[:500])
        return "Je rencontre un souci technique. Pouvez-vous reformuler votre demande ?"


def envoyer_message(psid, texte):
    """Envoie un message a Messenger."""
    if not PAGE_ACCESS_TOKEN:
        log("ERR", "PAGE_ACCESS_TOKEN manquant")
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
# WEBHOOK
# ====================================================================
@app.route("/", methods=["GET"])
def accueil():
    return jsonify({
        "status": "ok",
        "bot": "WeloobeAI Chatbot IA",
        "ia": "connectee" if client_ia else "non configuree",
        "db": "connectee" if DATABASE_URL else "non configuree"
    })


@app.route("/webhook", methods=["GET"])
def webhook_verification():
    try:
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            log("WEBHOOK", "Verification OK")
            return challenge or "", 200
        log("WEBHOOK", "Verification echouee")
        return "Forbidden", 403
    except Exception as e:
        log("ERR", "webhook_verification: " + str(e))
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_reception():
    """Recoit les evenements. Ne plante JAMAIS."""
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
                        reponse = "Je n'ai pas de reponse pour le moment."

                    enregistrer_message(psid, "assistant", reponse)
                    envoyer_message(psid, reponse)

                except Exception as e:
                    log("ERR", "traitement event: " + str(e))
                    log("ERR", traceback.format_exc()[:500])
                    continue

        return "EVENT_RECEIVED", 200

    except Exception as e:
        log("ERR", "webhook_reception global: " + str(e))
        return "EVENT_RECEIVED", 200


# ====================================================================
# INIT BASE
# ====================================================================
def init_database():
    """Cree les tables. Ne plante jamais."""
    if not DATABASE_URL:
        log("WARN", "Pas de DATABASE_URL, base non initialisee")
        return

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

        for alter in [
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS role TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS timestamp TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS psid TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS contenu TEXT",
        ]:
            try:
                cur.execute(alter)
            except Exception as e:
                log("WARN", "ALTER: " + str(e))

        conn.commit()

        # Import des produits si table vide
        cur.execute("SELECT COUNT(*) AS n FROM produits")
        row = cur.fetchone()
        if (row.get("n") or 0) == 0:
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
                             int(ws.cell(r, 10).value or 2))
                        )
                        importes += 1
                    except Exception:
                        continue
                conn.commit()
                log("DB", "{} produits importes".format(importes))
            except Exception as e:
                log("WARN", "Import Excel: " + str(e))

        cur.close()
        conn.close()
        log("DB", "Base initialisee")
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
    print("  Bot Messenger WeloobeAI — IA Groq robuste")
    print("  IA : {}".format("connectee" if client_ia else "NON"))
    print("  DB : {}".format("connectee" if DATABASE_URL else "NON"))
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)