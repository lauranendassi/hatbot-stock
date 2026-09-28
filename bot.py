# -*- coding: utf-8 -*-
"""Bot Messenger WeloobeAI v2
   - Memoire conversationnelle longue + profil client
   - Post-traitement des reponses (formatage Messenger)
   - Machine a etats pour la prise de commande
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


# ====================================================================
# POST-TRAITEMENT DES REPONSES
# ====================================================================
def formater_reponse(texte):
    """Nettoie le texte pour un affichage Messenger optimal."""
    if not texte:
        return ""
    try:
        # Retirer le Markdown qui s'affiche mal sur Messenger
        texte = re.sub(r"\*\*(.+?)\*\*", r"\1", texte)  # **gras**
        texte = re.sub(r"\*(.+?)\*", r"\1", texte)       # *italique*
        texte = re.sub(r"__(.+?)__", r"\1", texte)
        texte = re.sub(r"_(.+?)_", r"\1", texte)
        texte = re.sub(r"`(.+?)`", r"\1", texte)
        texte = re.sub(r"^#+\s*", "", texte, flags=re.MULTILINE)

        # Remplacer les puces Markdown par des puces propres
        texte = re.sub(r"^\s*[-*]\s+", "• ", texte, flags=re.MULTILINE)

        # Retirer les tableaux Markdown (| col | col |)
        lignes = []
        for ligne in texte.split("\n"):
            if ligne.strip().startswith("|") and ligne.strip().endswith("|"):
                if set(ligne.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
                    continue  # ligne de separation
                cells = [c.strip() for c in ligne.strip("|").split("|")]
                ligne = " • ".join(c for c in cells if c)
            lignes.append(ligne)
        texte = "\n".join(lignes)

        # Nettoyer les espaces multiples et lignes vides consecutives
        texte = re.sub(r"\n{3,}", "\n\n", texte)
        texte = re.sub(r"[ \t]+", " ", texte)
        texte = texte.strip()

        # Limiter la longueur
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
    """Charge le profil enrichi du client."""
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM clients WHERE psid = %s", (psid,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return {
                "psid": psid,
                "nom": None,
                "telephone": None,
                "budget": None,
                "derniers_produits_vus": [],
                "commande_en_cours": None
            }
        profil = dict(row)
        # Parse JSON des derniers produits vus
        for champ in ["derniers_produits_vus", "commande_en_cours"]:
            if profil.get(champ):
                try:
                    profil[champ] = json.loads(profil[champ])
                except Exception:
                    profil[champ] = [] if champ == "derniers_produits_vus" else None
            else:
                profil[champ] = [] if champ == "derniers_produits_vus" else None
        return profil
    except Exception as e:
        log("WARN", "charger_profil: " + str(e))
        return {"psid": psid}


def maj_profil(psid, **kwargs):
    """Met a jour le profil client."""
    try:
        conn = db()
        cur = conn.cursor()

        # S'assurer que le client existe
        cur.execute("SELECT psid FROM clients WHERE psid = %s", (psid,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO clients (psid, derniere_interaction) VALUES (%s, %s)",
                (psid, datetime.datetime.now().isoformat())
            )

        # Mise a jour des champs
        champs = []
        valeurs = []
        for cle, val in kwargs.items():
            if val is not None:
                champs.append("{} = %s".format(cle))
                if isinstance(val, (list, dict)):
                    valeurs.append(json.dumps(val))
                else:
                    valeurs.append(val)

        if champs:
            valeurs.append(psid)
            cur.execute(
                "UPDATE clients SET {}, derniere_interaction = %s WHERE psid = %s".format(
                    ", ".join(champs),
                    "%s"
                ),
                valeurs[:-1] + [datetime.datetime.now().isoformat(), psid]
            )

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log("WARN", "maj_profil: " + str(e))


# ====================================================================
# OUTILS POUR L'IA
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
            if cat_norm:
                if cat_norm not in cat_prod_norm and cat_prod_norm not in cat_norm:
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
                "instruction": "Aucun produit de cette categorie ne rentre dans le budget. Cite le prix REEL minimum et propose UNE alternative REELLE."
            }]

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
            "instruction": "Aucun produit ne correspond. Cite UNIQUEMENT les categories REELLES avec prix minimum REEL."
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


def demarrer_commande(psid, sku, quantite=1):
    """Demarre une commande (brouillon)."""
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
            "etape": "nom",
            "nom_client": None,
            "telephone": None
        }
        maj_profil(psid, commande_en_cours=commande)
        return {"succes": True, "commande": commande}
    except Exception as e:
        log("ERR", "demarrer_commande: " + str(e))
        return {"erreur": "Impossible de demarrer la commande"}


def valider_telephone(tel):
    """Valide un numero camerounais. Formats acceptes : 6XXXXXXXX, +2376XXXXXXXX, 002376XXXXXXXX"""
    if not tel:
        return False
    tel_clean = re.sub(r"[^\d+]", "", str(tel))
    # Enlever prefixe +237 ou 00237
    tel_clean = re.sub(r"^(\+237|00237)", "", tel_clean)
    # Doit commencer par 6 et faire 9 chiffres
    return bool(re.match(r"^6\d{8}$", tel_clean))


def finaliser_commande(psid, nom_client, telephone, notes=""):
    """Enregistre la commande finale."""
    try:
        profil = charger_profil(psid)
        commande = profil.get("commande_en_cours")
        if not commande:
            return {"erreur": "Aucune commande en cours"}

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

        # Vider le brouillon
        maj_profil(psid, commande_en_cours=None, nom=nom_client, telephone=telephone)

        # Notifier le commercial
        envoyer_notification_commande(
            commande_id, nom_client, telephone, commande.get("nom_produit"),
            quantite, prix_total, notes
        )

        return {
            "succes": True,
            "commande_id": commande_id,
            "produit": commande.get("nom_produit"),
            "quantite": quantite,
            "prix_total": prix_total
        }
    except Exception as e:
        log("ERR", "finaliser_commande: " + str(e))
        return {"erreur": "Impossible d'enregistrer la commande"}


def envoyer_notification_commande(commande_id, nom, telephone, produit,
                                    quantite, prix_total, notes=""):
    if not SMTP_USER or not SMTP_PASSWORD or not NOTIF_EMAIL:
        log("WARN", "Email non configure")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = NOTIF_EMAIL
        msg["Subject"] = "Nouvelle commande #{} - WeloobeAI".format(commande_id)
        corps = """Nouvelle commande recue via Messenger.

Commande #{cid}
-------------------------------------
Client      : {nom}
Telephone   : {tel}
Produit     : {prod}
Quantite    : {qte}
Prix total  : {prix}
Notes       : {notes}
-------------------------------------""".format(
            cid=commande_id, nom=nom, tel=telephone,
            prod=produit, qte=quantite, prix=prix_total,
            notes=notes or "aucune"
        )
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
    {
        "type": "function",
        "function": {
            "name": "chercher_produits",
            "description": "Cherche des produits dans le catalogue",
            "parameters": {
                "type": "object",
                "properties": {
                    "requete": {"type": "string"},
                    "budget_max": {"type": "number"},
                    "categorie": {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verifier_stock",
            "description": "Verifie la disponibilite d'un produit par SKU",
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
            "description": "Liste tous les produits du catalogue",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "demarrer_commande",
            "description": "Demarre une commande. Utilise UNIQUEMENT quand le client a confirme le produit et la quantite.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string"},
                    "quantite": {"type": "integer"}
                },
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finaliser_commande",
            "description": "Finalise la commande. Utilise UNIQUEMENT quand tu as nom ET telephone valides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nom_client": {"type": "string"},
                    "telephone": {"type": "string"},
                    "notes": {"type": "string"}
                },
                "required": ["nom_client", "telephone"]
            }
        }
    }
]


# ====================================================================
# HISTORIQUE
# ====================================================================
def charger_historique(psid, limite=30):
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
1. Tu ne proposes QUE les produits retournes par les outils.
2. INTERDICTION de mentionner : Chromebook, tablette, iPad, reconditionne, occasion, partenaire externe, pack etudiant, carte graphique, sac, casque, cable HDMI, ou tout produit absent du catalogue.
3. Categories reelles : PC portable, PC fixe, Ecran, Composant, Accessoire.
4. N'invente JAMAIS de prix.

=== FORMAT DES REPONSES ===
- Pas d'asterisques, pas de **, pas de #, pas de tableaux Markdown
- Utilise des puces simples avec le caractere •
- Reponses courtes : 1 a 3 phrases + liste si necessaire
- Un seul emoji maximum
- Pas de "je suis la pour vous aider" repetitif

=== COMPORTEMENT ===

Client qui cherche un produit :
- Utilise chercher_produits
- Presente 2-3 produits max avec LEUR PRIX REEL
- Format : "• [nom] - [prix]"

Client qui demande un produit absent :
- Dis simplement "Nous n'avons pas ce produit en catalogue."
- Ne propose PAS d'autres categories differentes
- Si possible, propose un produit REEL de la MEME categorie

Client qui veut commander :
- Etape 1 : Confirme le produit et la quantite
  Exemple : "Tres bon choix ! Samsung S24R350 a 120 000 FCFA. Combien d'unites ?"
- Etape 2 : Appelle demarrer_commande(sku, quantite)
- Etape 3 : Demande le nom complet
- Etape 4 : Demande le telephone
- Etape 5 : Verifie le format du telephone (doit commencer par 6 et faire 9 chiffres)
- Etape 6 : Appelle finaliser_commande(nom_client, telephone)
- Etape 7 : Confirme au client : "Commande enregistree. Un conseiller vous rappellera."

IMPORTANT pour la commande :
- UNE SEULE question a la fois
- Ne demande pas nom + telephone dans le meme message
- Si le client donne un telephone invalide, redemande poliment
- Si le client hesite, rassure-le

Client qui salue :
- Reponse courte (1-2 phrases)
- Demande ce qu'il cherche

=== STYLE ===
- Naturel, direct, chaleureux
- Une question a la fois
- Reponds en francais"""


# ====================================================================
# APPEL IA
# ====================================================================
def appeler_ia(messages, avec_outils=True, force_outil=False):
    if not client_ia:
        return None
    for modele in MODELES_GROQ:
        try:
            kwargs = {
                "model": modele,
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": 600
            }
            if avec_outils:
                kwargs["tools"] = OUTILS
                kwargs["tool_choice"] = "required" if force_outil else "auto"
            response = client_ia.chat.completions.create(**kwargs)
            log("IA", "Modele : " + modele + (" [force]" if force_outil else ""))
            return response
        except Exception as e:
            log("WARN", "Modele {} echoue : {}".format(modele, str(e)[:200]))
            continue
    return None


def executer_outil(nom, args, psid):
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
        elif nom == "demarrer_commande":
            return demarrer_commande(
                psid,
                args.get("sku", ""),
                int(args.get("quantite", 1))
            )
        elif nom == "finaliser_commande":
            return finaliser_commande(
                psid,
                args.get("nom_client", ""),
                args.get("telephone", ""),
                args.get("notes", "")
            )
        return {"erreur": "outil inconnu"}
    except Exception as e:
        log("ERR", "executer_outil: " + str(e))
        return {"erreur": "Erreur execution outil"}


def repondre_avec_ia(psid, message_client):
    if not client_ia:
        return "Bonjour ! Le service est en cours de configuration."

    try:
        # Charger profil client (memoire longue)
        profil = charger_profil(psid)

        # Construire le contexte client
        contexte_client = ""
        if profil.get("nom"):
            contexte_client += "\nNom du client (deja connu) : {}".format(profil["nom"])
        if profil.get("telephone"):
            contexte_client += "\nTelephone (deja connu) : {}".format(profil["telephone"])
        if profil.get("budget"):
            contexte_client += "\nBudget evoque : {}".format(profil["budget"])
        if profil.get("commande_en_cours"):
            cmd = profil["commande_en_cours"]
            contexte_client += "\nCommande en cours : {} x {} (etape: {})".format(
                cmd.get("quantite", 1), cmd.get("nom_produit"), cmd.get("etape")
            )

        system_complet = SYSTEM_PROMPT
        if contexte_client:
            system_complet += "\n\n=== CONTEXTE CLIENT ===\n" + contexte_client
            system_complet += "\n\nUtilise ces informations : ne redemande PAS ce que le client a deja donne."

        # Historique long (30 messages)
        historique = charger_historique(psid, limite=30)

        messages = [{"role": "system", "content": system_complet}]
        messages.extend(historique)
        messages.append({"role": "user", "content": message_client})

        low = normaliser(message_client)
        mots_produits = ["pc", "ordinateur", "portable", "ecran", "moniteur", "ssd", "ram",
                         "batterie", "chargeur", "accessoire", "composant", "fixe", "tour",
                         "montre", "affiche", "liste", "catalogue", "stock", "prix", "cherche",
                         "veux", "besoin", "dispo", "combien", "commande", "acheter",
                         "commander", "prends", "prend"]
        forcer = any(m in low for m in mots_produits)

        response = appeler_ia(messages, avec_outils=True, force_outil=forcer)
        if not response:
            return "Je rencontre un souci technique. Pouvez-vous reformuler ?"

        try:
            msg = response.choices[0].message
        except Exception:
            return "Je n'ai pas bien compris."

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

            produit_photo = None

            for tool_call in tool_calls:
                try:
                    nom_outil = tool_call.function.name
                    args_str = tool_call.function.arguments or "{}"
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}

                    log("OUTIL", "{} ({})".format(nom_outil, args))
                    resultat = executer_outil(nom_outil, args, psid)

                    if nom_outil == "chercher_produits" and isinstance(resultat, list):
                        if len(resultat) == 1 and resultat[0].get("sku"):
                            produit_photo = resultat[0]

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(resultat, ensure_ascii=False, default=str)
                    })
                except Exception as e:
                    log("ERR", "traitement tool_call: " + str(e))
                    continue

            response2 = appeler_ia(messages, avec_outils=False)
            reponse = None
            if response2:
                try:
                    reponse = response2.choices[0].message.content
                except Exception:
                    pass
            if not reponse:
                reponse = "J'ai trouve des produits mais je n'arrive pas a formuler la reponse."

            # Post-traitement
            reponse = formater_reponse(reponse)

            if produit_photo:
                envoyer_photo(psid, produit_photo["sku"])

            return reponse

        return formater_reponse(msg.content or "Je n'ai pas bien compris.")

    except Exception as e:
        log("ERR", "repondre_avec_ia: " + str(e))
        log("ERR", traceback.format_exc()[:500])
        return "Je rencontre un souci technique."


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


def envoyer_photo(psid, sku):
    try:
        dossier = Path("photos")
        if not dossier.exists():
            return False
        fichier = None
        for ext in [".jpg", ".jpeg", ".png", ".JPG", ".PNG"]:
            candidate = dossier / (sku + ext)
            if candidate.exists():
                fichier = candidate
                break
        if not fichier:
            return False

        url_image = "{}/photos/{}".format(PUBLIC_URL, fichier.name)
        url = "https://graph.facebook.com/v20.0/me/messages"
        params = {"access_token": PAGE_ACCESS_TOKEN}
        payload = {
            "recipient": {"id": psid},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {"url": url_image, "is_reusable": True}
                }
            }
        }
        r = requests.post(url, params=params, json=payload, timeout=10)
        log("PHOTO", "{} - {}".format(r.status_code, url_image))
        return r.status_code == 200
    except Exception as e:
        log("ERR", "envoyer_photo: " + str(e))
        return False


# ====================================================================
# ROUTES
# ====================================================================
@app.route("/", methods=["GET"])
def accueil():
    return jsonify({
        "status": "ok",
        "version": "v2",
        "bot": "WeloobeAI Chatbot",
        "ia": "connectee" if client_ia else "non configuree",
        "db": "connectee" if DATABASE_URL else "non configuree",
        "email": "configure" if SMTP_USER else "non configure"
    })


@app.route("/photos/<path:filename>")
def servir_photo(filename):
    try:
        return send_from_directory("photos", filename)
    except Exception:
        return "Not found", 404


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
                        reponse = "Je n'ai pas de reponse pour le moment."

                    enregistrer_message(psid, "assistant", reponse)
                    envoyer_message(psid, reponse)

                except Exception as e:
                    log("ERR", "traitement event: " + str(e))
                    continue

        return "EVENT_RECEIVED", 200

    except Exception as e:
        log("ERR", "webhook_reception global: " + str(e))
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

        # Table clients enrichie (memoire longue)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                psid TEXT PRIMARY KEY,
                nom TEXT,
                telephone TEXT,
                budget INTEGER,
                derniers_produits_vus TEXT,
                commande_en_cours TEXT,
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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS commandes (
                id SERIAL PRIMARY KEY,
                psid TEXT,
                nom_client TEXT,
                telephone TEXT,
                sku TEXT,
                nom_produit TEXT,
                quantite INTEGER,
                prix_unitaire INTEGER,
                prix_total INTEGER,
                statut TEXT DEFAULT 'nouvelle',
                notes TEXT,
                timestamp TEXT
            )
        """)

        # Ajouts de colonnes si la table existait deja
        for alter in [
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS nom TEXT",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS telephone TEXT",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS budget INTEGER",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS derniers_produits_vus TEXT",
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS commande_en_cours TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS role TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS timestamp TEXT",
        ]:
            try:
                cur.execute(alter)
            except Exception:
                pass

        conn.commit()

        # Import produits
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
        log("DB", "Base v2 initialisee")
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
    print("  Bot Messenger WeloobeAI v2")
    print("  IA    : {}".format("connectee" if client_ia else "NON"))
    print("  DB    : {}".format("connectee" if DATABASE_URL else "NON"))
    print("  Email : {}".format("configure" if SMTP_USER else "NON"))
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)