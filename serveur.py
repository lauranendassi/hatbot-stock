# -*- coding: utf-8 -*-
"""Serveur chatbot v2 — regles enrichies."""
import datetime
import unicodedata
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from openpyxl import load_workbook

BASE = Path(__file__).parent
XLSX = BASE / "Gestion_stock_corrige.xlsx"

app = Flask(__name__, static_folder=str(BASE), static_url_path="")


def lire_excel():
    if not XLSX.exists():
        raise FileNotFoundError("Fichier introuvable : " + str(XLSX))

    wb = load_workbook(str(XLSX), data_only=False)

    # PRODUITS
    ws = wb["Produits"]
    produits = []
    by_nom = {}
    for r in range(5, 105):
        sku = ws.cell(r, 1).value
        nom = ws.cell(r, 2).value
        if not sku or not nom:
            continue
        p = {
            "sku": str(sku), "nom": str(nom),
            "cat": ws.cell(r, 11).value or "",
            "prix_achat": float(ws.cell(r, 3).value or 0),
            "prix": float(ws.cell(r, 4).value or 0),
            "stock_initial": int(ws.cell(r, 5).value or 0),
            "seuil": int(ws.cell(r, 10).value or 2),
            "entrees": 0, "sorties": 0, "vendus": 0, "rang_alerte": None
        }
        produits.append(p)
        by_nom[str(nom)] = p

    # ACHATS
    ws = wb["Achats"]
    nb_achats = 0
    achats_list = []
    for r in range(5, 105):
        nom = ws.cell(r, 2).value
        qte = ws.cell(r, 4).value
        date_a = ws.cell(r, 1).value
        if not nom or qte in (None, ""):
            continue
        nom = str(nom)
        if nom in by_nom:
            by_nom[nom]["entrees"] += int(qte)
            nb_achats += 1
        d = None
        if isinstance(date_a, datetime.datetime):
            d = date_a.date()
        elif isinstance(date_a, datetime.date):
            d = date_a
        achats_list.append({"date": d.isoformat() if d else "", "nom": nom, "qte": int(qte)})

    # VENTES
    ws = wb["Ventes"]
    ventes_list = []
    ca_total = ben_total = 0.0
    ca_mois = ben_mois = 0.0
    aujourd = datetime.date.today()
    ca_par_mois = {}
    ca_par_jour = {}
    ca_par_cat = {}

    for r in range(5, 105):
        date_v = ws.cell(r, 1).value
        nom = ws.cell(r, 2).value
        qte = ws.cell(r, 4).value
        client = ws.cell(r, 5).value or ""
        if not nom or qte in (None, ""):
            continue
        nom = str(nom)
        if nom not in by_nom:
            continue
        p = by_nom[nom]
        qte = int(qte)
        ca = qte * p["prix"]
        ben = qte * (p["prix"] - p["prix_achat"])
        ca_total += ca
        ben_total += ben
        p["sorties"] += qte
        p["vendus"] += qte

        d = None
        if isinstance(date_v, datetime.datetime):
            d = date_v.date()
        elif isinstance(date_v, datetime.date):
            d = date_v

        if d:
            cle_mois = d.strftime("%Y-%m")
            ca_par_mois[cle_mois] = ca_par_mois.get(cle_mois, 0) + ca
            ca_par_jour[d.isoformat()] = ca_par_jour.get(d.isoformat(), 0) + ca
            ca_par_cat[p["cat"]] = ca_par_cat.get(p["cat"], 0) + ca
            if d.year == aujourd.year and d.month == aujourd.month:
                ca_mois += ca
                ben_mois += ben

        ventes_list.append({
            "date": d.isoformat() if d else "",
            "sku": p["sku"], "nom": p["nom"], "qte": qte,
            "client": str(client), "ca": int(ca), "benefice": int(ben),
            "cat": p["cat"]
        })

    # CALCULS
    total_unites = 0
    valeur_stock = 0.0
    rang = 1
    for p in produits:
        p["stock"] = p["stock_initial"] + p["entrees"] - p["sorties"]
        if p["stock"] <= p["seuil"]:
            p["rang_alerte"] = rang
            rang += 1
        total_unites += p["stock"]
        valeur_stock += p["stock"] * p["prix_achat"]
    nb_alertes = rang - 1

    # CLIENTS
    clients_stats = {}
    for v in ventes_list:
        c = v["client"]
        if c not in clients_stats:
            clients_stats[c] = {"nb": 0, "ca": 0, "derniere": "", "produits": []}
        clients_stats[c]["nb"] += 1
        clients_stats[c]["ca"] += v["ca"]
        if v["date"] > clients_stats[c]["derniere"]:
            clients_stats[c]["derniere"] = v["date"]
        clients_stats[c]["produits"].append(v["nom"])

    return {
        "produits": produits,
        "ventes": ventes_list,
        "achats": achats_list,
        "clients": clients_stats,
        "ca_par_mois": ca_par_mois,
        "ca_par_jour": ca_par_jour,
        "ca_par_cat": ca_par_cat,
        "kpi": {
            "ca_total": int(ca_total), "ca_mois": int(ca_mois),
            "benefice_total": int(ben_total), "benefice_mois": int(ben_mois),
            "nb_alertes": nb_alertes, "valeur_stock": int(valeur_stock),
            "nb_ventes_total": len(ventes_list), "nb_achats_total": nb_achats,
            "total_unites": total_unites,
            "mois_courant": aujourd.strftime("%B %Y")
        },
        "genere_le": datetime.datetime.now().isoformat()
    }


@app.route("/api/data")
def api_data():
    try:
        return jsonify(lire_excel())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message vide"}), 400
    try:
        stock_data = lire_excel()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(traiter_question(message, stock_data))


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "chatbot.html")


# ====================================================================
# NORMALISATION
# ====================================================================
def normaliser(s):
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for ch in "—–-_,.;:!?()[]'\"/\\":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def contient(low, *mots):
    """Vrai si un des mots est present dans le message."""
    return any(m in low for m in mots)


def trouver_produit(message, produits):
    q = normaliser(message)
    tokens = [t for t in q.split() if len(t) >= 3]
    if not tokens:
        return None
    best, best_score = None, 0
    for p in produits:
        hay = normaliser(p["nom"] + " " + p["cat"] + " " + p["sku"])
        score = sum(len(t) for t in tokens if t in hay)
        if score > best_score:
            best_score, best = score, p
    # Seuil plus eleve pour eviter les faux positifs
    return best if best_score >= 8 else None


def f(n):
    return "{:,}".format(int(n)).replace(",", " ") + " FCFA"


# ====================================================================
# TRAITEMENT DES QUESTIONS
# ====================================================================
def traiter_question(message, data):
    low = normaliser(message)
    kpi = data["kpi"]
    produits = data["produits"]
    clients = data["clients"]

    # ---------- SALUTATIONS CONVERSATIONNELLES ----------
    if contient(low, "comment tu vas", "comment vas tu", "ca va", "comment ca va",
                "tu vas bien", "comment va tu"):
        return {"text": "Je vais très bien, merci 😊 Prêt à vous aider sur le stock. Que voulez-vous savoir ?"}

    if low.startswith(("bonjour", "bonsoir", "salut", "coucou", "hello", "hi", "hey", "yo")):
        return {"text": "Bonjour 👋 Posez-moi une question sur les produits, le stock, les prix, les alertes, les clients ou le chiffre d'affaires."}

    if contient(low, "merci", "thanks", "super", "parfait", "nickel"):
        return {"text": "Avec plaisir 😊 Autre chose ?"}

    if contient(low, "au revoir", "bye", "a plus", "bonne journee"):
        return {"text": "Bonne journée ! À bientôt 👋"}

    if contient(low, "qui es tu", "qui est tu", "tu es qui", "presente toi"):
        return {"text": "Je suis l'assistant stock WeloobeAI. Je lis votre fichier Excel en temps réel et réponds à vos questions sur les produits, ventes, achats, alertes et chiffre d'affaires."}

    # ---------- AIDE ----------
    if contient(low, "aide", "help", "que peux tu", "que sais tu", "capacite", "commande"):
        return {"text": (
            "Voici ce que je peux faire :\n\n"
            "📦 Stock : « stock macbook », « combien reste latitude 5420 »\n"
            "💰 Prix : « prix elitebook », « marge latitude »\n"
            "⚠️ Alertes : « alertes », « à commander »\n"
            "📊 CA : « chiffre d'affaires », « ca du mois »\n"
            "💵 Bénéfice : « bénéfice », « marge totale »\n"
            "🏆 Top ventes : « top ventes », « meilleur produit »\n"
            "👥 Clients : « liste clients », « ca par client »\n"
            "📅 Périodes : « meilleur mois », « meilleur jour »\n"
            "📋 Catalogue : « tous les produits », « liste des produits »\n"
            "🏷️ Catégories : « montre les écrans », « pc portables »"
        )}

    # ---------- CATALOGUE ----------
    if contient(low, "tous les produits", "liste produits", "liste des produits",
                "catalogue", "inventaire", "quels produits", "montre moi les produits"):
        lignes = []
        for p in produits:
            dispo = "{} u.".format(p["stock"]) if p["stock"] > 0 else "RUPTURE"
            lignes.append("• {} — {} — {}".format(
                p["nom"].split(" — ")[0], f(p["prix"]), dispo))
        return {"text": "📋 Catalogue ({} produits) :\n\n{}".format(
            len(produits), "\n".join(lignes))}

    # ---------- CATÉGORIE ----------
    categories = ["PC portable", "PC fixe", "Écran", "Composant", "Accessoire"]
    for cat in categories:
        cat_low = normaliser(cat)
        variantes = {
            "PC portable": ["pc portable", "portable", "laptop", "portables"],
            "PC fixe": ["pc fixe", "fixe", "tour", "desktop", "unite centrale"],
            "Écran": ["ecran", "ecrans", "moniteur", "moniteurs"],
            "Composant": ["composant", "composants", "ssd", "ram", "batterie"],
            "Accessoire": ["accessoire", "accessoires", "chargeur", "chargeurs"]
        }
        if cat in variantes and contient(low, *variantes[cat]):
            cat_produits = [p for p in produits if p["cat"] == cat]
            if cat_produits:
                lignes = ["• {} — {} — {} u.".format(
                    p["nom"].split(" — ")[0], f(p["prix"]), p["stock"])
                    for p in cat_produits]
                return {"text": "🏷️ {} ({} produits) :\n\n{}".format(
                    cat, len(cat_produits), "\n".join(lignes))}

    # ---------- MEILLEUR MOIS ----------
    if contient(low, "meilleur mois", "mois productif", "mois le plus",
                "plus gros mois", "meilleure periode"):
        ca_par_mois = data["ca_par_mois"]
        if not ca_par_mois:
            return {"text": "Aucune vente enregistrée."}
        meilleur = max(ca_par_mois.items(), key=lambda x: x[1])
        mois_fr = {"01": "Janvier", "02": "Février", "03": "Mars", "04": "Avril",
                   "05": "Mai", "06": "Juin", "07": "Juillet", "08": "Août",
                   "09": "Septembre", "10": "Octobre", "11": "Novembre", "12": "Décembre"}
        annee, mois_num = meilleur[0].split("-")
        nom_mois = mois_fr.get(mois_num, mois_num)
        classement = sorted(ca_par_mois.items(), key=lambda x: -x[1])[:5]
        lignes = []
        for cle, ca in classement:
            a, m = cle.split("-")
            lignes.append("• {} {} : {}".format(mois_fr.get(m, m), a, f(ca)))
        return {"text": "📅 Meilleur mois : {} {} avec {}\n\nClassement :\n\n{}".format(
            nom_mois, annee, f(meilleur[1]), "\n".join(lignes))}

    # ---------- MEILLEUR JOUR ----------
    if contient(low, "meilleur jour", "jour productif", "jour le plus",
                "plus gros jour"):
        ca_par_jour = data["ca_par_jour"]
        if not ca_par_jour:
            return {"text": "Aucune vente enregistrée."}
        meilleur = max(ca_par_jour.items(), key=lambda x: x[1])
        return {"text": "📅 Meilleur jour : {} avec {}".format(
            meilleur[0], f(meilleur[1]))}

    # ---------- MEILLEURE CATÉGORIE ----------
    if contient(low, "meilleur categorie", "meilleure categorie",
                "categorie qui rapporte", "categorie la plus"):
        ca_par_cat = data["ca_par_cat"]
        if not ca_par_cat:
            return {"text": "Aucune vente enregistrée."}
        classement = sorted(ca_par_cat.items(), key=lambda x: -x[1])
        lignes = ["• {} : {}".format(cat, f(ca)) for cat, ca in classement]
        return {"text": "🏷️ CA par catégorie :\n\n" + "\n".join(lignes)}

    # ---------- ALERTES ----------
    if contient(low, "alerte", "commander", "reappro", "rupture", "seuil", "manque"):
        alertes = sorted([p for p in produits if p["rang_alerte"]],
                         key=lambda x: x["rang_alerte"])
        if not alertes:
            return {"text": "✅ Aucun produit en alerte."}
        lignes = []
        for p in alertes:
            prio = "URGENT" if p["stock"] == 0 else (
                "HAUT" if p["stock"] <= p["seuil"]/2 else "NORMAL")
            lignes.append("• {} — {} u. (seuil {}) — {}".format(
                p["nom"].split(" — ")[0], p["stock"], p["seuil"], prio))
        return {"text": "⚠️ {} produit(s) à commander :\n\n{}".format(
            len(alertes), "\n".join(lignes))}

    # ---------- BÉNÉFICE ----------
    if contient(low, "benefice", "marge", "profit", "gain", "rentab"):
        return {"text": "💵 Bénéfice brut :\n\n• Total : {}\n• Ce mois : {}\n• Marge moyenne : {} %".format(
            f(kpi["benefice_total"]), f(kpi["benefice_mois"]),
            round(100 * kpi["benefice_total"] / kpi["ca_total"]) if kpi["ca_total"] else 0)}

    # ---------- CA ----------
    if contient(low, "ca du mois", "chiffre d'affaires du mois", "ca mois",
                "chiffre du mois"):
        return {"text": "📊 CA du mois en cours : {}".format(f(kpi["ca_mois"]))}

    if contient(low, "ca total", "chiffre total", "ca global", "chiffre global"):
        return {"text": "📊 CA total : {}".format(f(kpi["ca_total"]))}

    if contient(low, "chiffre", "ca ", " ca", "recette", "vente", "vendu"):
        return {"text": "📊 Chiffre d'affaires :\n\n• Total : {}\n• Ce mois : {}\n• Ventes : {}".format(
            f(kpi["ca_total"]), f(kpi["ca_mois"]), kpi["nb_ventes_total"])}

    # ---------- VALEUR STOCK ----------
    if contient(low, "valeur", "patrimoine", "capital", "combien vaut"):
        return {"text": "💼 Valeur du stock (prix d'achat × quantité) : {}\n\n• Unités : {}".format(
            f(kpi["valeur_stock"]), kpi["total_unites"])}

    # ---------- TOP VENTES ----------
    if contient(low, "top", "meilleur produit", "plus vendu", "best seller",
                "produit qui rapporte"):
        top = sorted([p for p in produits if p["vendus"] > 0],
                     key=lambda x: x["vendus"], reverse=True)[:5]
        if not top:
            return {"text": "Aucune vente enregistrée."}
        lignes = []
        for i, p in enumerate(top):
            lignes.append("{}. {} — {} vendu(s) — {}".format(
                i+1, p["nom"].split(" — ")[0], p["vendus"],
                f(p["vendus"] * p["prix"])))
        return {"text": "🏆 Top ventes :\n\n" + "\n".join(lignes)}

    # ---------- CLIENTS ----------
    if contient(low, "client", "acheteur"):
        if not clients:
            return {"text": "Aucun client enregistré."}
        if contient(low, "meilleur", "top", "plus gros", "fidele"):
            top = sorted(clients.items(), key=lambda x: -x[1]["ca"])[:3]
            lignes = ["{}. {} — {} commande(s) — {}".format(
                i+1, nom, s["nb"], f(s["ca"])) for i, (nom, s) in enumerate(top)]
            return {"text": "🏆 Meilleurs clients :\n\n" + "\n".join(lignes)}
        lignes = []
        for nom, s in sorted(clients.items(), key=lambda x: -x[1]["ca"]):
            lignes.append("• {} — {} commande(s) — {}".format(nom, s["nb"], f(s["ca"])))
        return {"text": "👥 Clients :\n\n" + "\n".join(lignes)}

    # ---------- PRODUIT SPÉCIFIQUE ----------
    p = trouver_produit(message, produits)
    if p:
        if contient(low, "stock", "dispo", "reste", "quantite", "combien"):
            statut = "⚠️ Sous le seuil" if 0 < p["stock"] <= p["seuil"] else (
                "❌ Rupture" if p["stock"] <= 0 else "✅ Disponible")
            return {"text": "📦 {}\n\n• Stock : {} unité(s)\n• Seuil : {}\n• Statut : {}".format(
                p["nom"], p["stock"], p["seuil"], statut)}
        if contient(low, "prix", "cout", "tarif", "coute", "combien"):
            return {"text": "💰 {}\n\n• Prix de vente : {}\n• Prix d'achat : {}\n• Marge : {}".format(
                p["nom"], f(p["prix"]), f(p["prix_achat"]), f(p["prix"] - p["prix_achat"]))}
        return {"text": "📋 {}\n\n• SKU : {}\n• Catégorie : {}\n• Prix : {}\n• Stock : {} u.".format(
            p["nom"], p["sku"], p["cat"], f(p["prix"]), p["stock"])}

    # ---------- FALLBACK ----------
    return {"text": (
        "Je n'ai pas bien compris 🤔\n\n"
        "Essayez :\n"
        "• « stock macbook »\n"
        "• « prix latitude 5420 »\n"
        "• « alertes »\n"
        "• « chiffre d'affaires »\n"
        "• « meilleur mois »\n"
        "• « tous les produits »\n"
        "• « liste clients »\n"
        "• « aide » pour tout voir"
    )}


if __name__ == "__main__":
    print("=" * 60)
    print("  Chatbot Stock v2 — Serveur local enrichi")
    print("  http://localhost:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False)