# -*- coding: utf-8 -*-
"""Moteur de conversation : machine à états + règles commerciales appliquées PAR LE CODE.

L'IA ne décide pas des actions importantes. Le code :
  - détecte l'intention (intentions.py) ;
  - applique la règle « qualifier avant de proposer / de donner un prix » (skill qualifier_besoin),
    en retirant les prix des informations transmises à l'IA tant que le client n'est pas qualifié ;
  - pilote la commande étape par étape (COMMANDE_* ci-dessous) ;
  - transfère à un humain et prévient l'équipe par email ;
  - limite les informations selon le rôle (client / vendeur / admin / boss).
L'IA (Groq) formule la réponse à partir du fichier instructions.md, des fiches RAG
et des résultats des skills. Si elle échoue, des réponses préparées prennent le relais.

États :
  CONVERSATION -> COMMANDE_NOM -> COMMANDE_TELEPHONE -> COMMANDE_LIVRAISON
               -> (COMMANDE_QUARTIER) -> COMMANDE_CONFIRMATION -> CONVERSATION
  CONVERSATION -> SAV_DETAILS -> CONVERSATION
  n'importe quel état -> HUMAIN (le bot se tait pendant HANDOFF_HEURES)
"""
from __future__ import annotations

import datetime as dt
import logging
import re

import catalogue
import config
import db
import ia
import intentions
import rag
from catalogue import sans_accents
from skills import executer

log = logging.getLogger("bot.conversation")

ETATS_COMMANDE = {"COMMANDE_NOM", "COMMANDE_TELEPHONE", "COMMANDE_LIVRAISON", "COMMANDE_QUARTIER",
                  "COMMANDE_CONFIRMATION"}
INTENTIONS_INFO = {"LIVRAISON", "PAIEMENT", "GARANTIE", "INFOS_MAGASIN"}


def fcfa(v) -> str:
    return f"{float(v):,.0f}".replace(",", " ") + " FCFA"


def charger_instructions() -> str:
    try:
        return config.INSTRUCTIONS_FICHIER.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.error("instructions.md introuvable : consignes minimales utilisées")
        return "Tu es le conseiller chaleureux d'un magasin de matériel informatique à Yaoundé. Réponds en français."


INSTRUCTIONS = charger_instructions()


def recharger_instructions():
    global INSTRUCTIONS
    INSTRUCTIONS = charger_instructions()


# ==========================================================================
# Point d'entrée
# ==========================================================================
def traiter(psid: str, texte: str) -> list[str]:
    """Traite un message client et renvoie la liste des messages texte à envoyer."""
    base = db.base()
    conv = base.conversation(psid)
    role = executer("verifier_permissions", {"action": "voir_catalogue", "psid": psid}).get("role", "client")
    texte = (texte or "").strip()[:2000]
    base.ajouter_message(psid, "user", texte)
    profil = conv["profil"]
    profil.setdefault("prenom", None)
    log.info("[%s] rôle=%s état=%s ← %s", psid[-6:], role, conv["etat"], texte[:120])

    reponses = _traiter(conv, role, texte)

    for r in reponses:
        base.ajouter_message(psid, "assistant", r)
    profil["derniere_interaction"] = dt.datetime.now().isoformat(timespec="seconds")
    base.enregistrer_conversation(conv)
    log.info("[%s] → état=%s | %s", psid[-6:], conv["etat"], " / ".join(r[:100] for r in reponses))
    return reponses


def _traiter(conv: dict, role: str, texte: str) -> list[str]:
    psid, profil = conv["psid"], conv["profil"]

    if texte.startswith("/"):
        return commande_equipe(conv, role, texte)

    # --- Un conseiller humain a pris la main : le bot se tait
    if conv["etat"] == "HUMAIN":
        fin = conv.get("handoff_jusqua")
        fin = dt.datetime.fromisoformat(fin) if isinstance(fin, str) else fin
        maintenant = dt.datetime.now(fin.tzinfo) if fin and fin.tzinfo else dt.datetime.now()
        if fin and maintenant < fin and not re.search(r"\b(assistant|robot|bot)\b", sans_accents(texte)):
            log.info("[%s] conversation suivie par un humain : pas de réponse automatique", psid[-6:])
            return []
        conv["etat"], conv["handoff_jusqua"] = "CONVERSATION", None

    a = intentions.analyser(texte, attend_budget=profil.get("champ_attendu") == "budget",
                            attend_quantite=conv["etat"] == "COMMANDE_CONFIRMATION")
    mettre_a_jour_profil(profil, a, texte)

    # Produit / catégorie évoqués
    rech = executer("rechercher_produit", {"texte": texte, "limite": 3, "disponibles_seulement": False})
    if rech.get("produit_cite"):
        profil["produit_focus"] = rech["produit_cite"]
        profil["categorie_focus"] = rech.get("categorie")
        vus = profil.setdefault("produits_vus", [])
        if rech["produit_cite"] not in vus:
            vus.append(rech["produit_cite"])
            del vus[:-10]
    elif rech.get("categorie") and conv["etat"] == "CONVERSATION":
        if profil.get("categorie_focus") != rech["categorie"]:
            profil["produit_focus"] = None
        profil["categorie_focus"] = rech["categorie"]

    # --- Priorités absolues
    if a.principale in ("HUMAIN", "PLAINTE"):
        return transferer_humain(conv, a.principale, texte)
    if a.principale == "ANNULER" and conv["etat"] in ETATS_COMMANDE:
        conv["etat"], conv["panier"] = "CONVERSATION", {}
        return ["Pas de souci, j'ai annulé la commande en cours. Je reste là si vous avez une autre question 🙂"]

    if conv["etat"] in ETATS_COMMANDE:
        return etape_commande(conv, role, a, texte)

    # Montant seul juste après une contre-offre (« bon 270000 ») = nouvelle offre
    if (profil.get("attente_oui") or {}).get("action") == "accepter_offre" and a.principale in ("AUTRE", "NON", "PRIX"):
        offres = intentions.montants(sans_accents(texte), True)
        if offres:
            a.offre, a.principale = max(offres), "NEGOCIATION"
            profil["produit_focus"] = profil["attente_oui"]["code"]
            profil.pop("attente_oui")
            return negocier(conv, role, a, texte)

    # Réponse à une question fermée posée juste avant par le bot
    attente = profil.pop("attente_oui", None)
    if attente and (a.principale == "OUI" or (a.principale in ("COMMANDE", "AUTRE") and "OUI" in a.toutes)):
        if attente["action"] == "accepter_offre":
            nego = profil.setdefault("negociation", {}).setdefault(attente["code"], {})
            nego["prix_accorde"] = attente["prix"]
        profil["produit_focus"] = attente["code"]
        return demarrer_commande(conv, a, intro=("Marché conclu à " + fcfa(attente["prix"]) + " ! ")
                                 if attente["action"] == "accepter_offre" else None)
    if profil.pop("attente_photo", False) and rech.get("produit_cite"):
        return photo(conv)
    if profil.pop("attente_commande", False) and rech.get("produit_cite"):
        return demarrer_commande(conv, a)
    if conv["etat"] == "SAV_DETAILS":
        return enregistrer_sav(conv, texte)

    if role != "client":
        return mode_equipe(conv, role, texte, a)

    if a.principale == "SAV":
        if len(texte) >= 60:
            return enregistrer_sav(conv, texte)
        conv["etat"] = "SAV_DETAILS"
        return ["Je suis désolé pour ce souci. Pour que notre technicien vous aide vite, décrivez-moi le problème : "
                "quel appareil (modèle) et ce qui se passe exactement ?"]
    if a.principale == "COMMANDE":
        return demarrer_commande(conv, a)
    if a.principale == "PHOTO":
        return photo(conv)
    if a.principale == "NEGOCIATION" or (a.principale == "PRIX" and a.offre and profil.get("produit_focus")):
        return negocier(conv, role, a, texte)
    return conseiller(conv, role, texte, a)


# ==========================================================================
# Profil client
# ==========================================================================
def mettre_a_jour_profil(profil: dict, a: intentions.Analyse, texte: str):
    attendu = profil.get("champ_attendu")
    if a.usages:
        profil["usages"] = sorted(set(profil.get("usages") or []) | set(a.usages))
    elif attendu == "usage" and a.principale in ("AUTRE", "OUI", "NON") and len(texte) >= 3:
        profil["usages"] = ["autre"]
        profil["usage_libre"] = texte[:150]           # « un peu de tout », réponse libre : on la garde telle quelle
    if a.budget:
        profil["budget_max"] = a.budget
        profil["budget_ouvert"] = False
        if a.budget_min:
            profil["budget_min"] = a.budget_min
    elif attendu == "budget" and re.search(r"sais pas|aucune idee|pas de budget|peu importe|ca depend|"
                                           r"proposez|a vous de voir|libre", sans_accents(texte)):
        profil["budget_ouvert"] = True
    if a.petit_budget:
        profil["petit_budget"] = True
    if attendu == "modele_pc" and re.search(r"[a-z].*\d|\d.*[a-z]|dell|hp|lenovo|asus|acer|toshiba|apple|mac",
                                            sans_accents(texte)):
        profil["modele_pc"] = texte[:80]
    t = sans_accents(texte)
    if re.search(r"pas (un |de |d'|d )?(pc |ordinateur |ordi )?(fixe|de bureau|desktop)", t):
        profil["categorie_refusee"] = "PC fixe"
        profil["categorie_focus"] = "PC portable"
        profil["produit_focus"] = None
    elif re.search(r"pas (un |de |d'|d )?(pc |ordinateur |ordi )?(portable|laptop)", t):
        profil["categorie_refusee"] = "PC portable"
        profil["categorie_focus"] = "PC fixe"
        profil["produit_focus"] = None
    if a.pour_qui:
        profil["pour_qui"] = a.pour_qui
    if a.telephone:
        profil["telephone"] = a.telephone
    if "PRIX" in a.toutes:
        profil["nb_demandes_prix"] = int(profil.get("nb_demandes_prix", 0)) + 1


def resume_profil(profil: dict) -> str:
    morceaux = []
    if profil.get("prenom"):
        morceaux.append(f"prénom {profil['prenom']}")
    if profil.get("usages"):
        u = ", ".join(profil["usages"])
        morceaux.append(f"usage : {u}" + (f" (« {profil['usage_libre']} »)" if profil.get("usage_libre") else ""))
    if profil.get("budget_max"):
        morceaux.append(f"budget max {fcfa(profil['budget_max'])}")
    elif profil.get("budget_ouvert"):
        morceaux.append("budget non fixé (ouvert aux conseils)")
    if profil.get("petit_budget"):
        morceaux.append("cherche à économiser")
    if profil.get("pour_qui"):
        morceaux.append(f"pour {profil['pour_qui']}")
    if profil.get("modele_pc"):
        morceaux.append(f"son ordinateur : {profil['modele_pc']}")
    if profil.get("produit_focus"):
        p = catalogue.par_code(profil["produit_focus"])
        if p:
            morceaux.append(f"s'intéresse à : {p['designation']} ({p['code']})")
    if profil.get("commandes"):
        morceaux.append(f"commandes passées : {', '.join(profil['commandes'][-3:])}")
    return " ; ".join(morceaux) or "aucune information pour l'instant"


# ==========================================================================
# Conseil (RAG + IA) avec règle de qualification
# ==========================================================================
def conseiller(conv: dict, role: str, texte: str, a: intentions.Analyse) -> list[str]:
    profil = conv["profil"]
    sujet_produit = bool(profil.get("produit_focus") or profil.get("categorie_focus") or a.usages or a.budget
                         or {"PRIX", "DISPONIBILITE"} & set(a.toutes) or profil.get("champ_attendu"))
    question_info = a.principale in INTENTIONS_INFO
    directive, donnees, qualifie, items = [], [], True, []

    q = executer("qualifier_besoin", {"profil": profil, "categorie": profil.get("categorie_focus"),
                                      "produit_cite": profil.get("produit_focus")})
    if sujet_produit and not question_info:
        qualifie = q["qualifie"]

    fiches = rag.rechercher(texte, niveau=0, contexte=" ".join(filter(None, [
        profil.get("categorie_focus"), (catalogue.par_code(profil.get("produit_focus") or "") or {}).get("designation")])))

    if not sujet_produit or question_info:
        profil["champ_attendu"] = None
        if a.principale == "SALUTATION" and not sujet_produit:
            directive.append("Le client salue. Salue-le chaleureusement (avec son prénom s'il est connu), "
                             "présente-toi en une phrase et demande ce que tu peux faire pour lui. Ne cite aucun produit.")
        elif question_info:
            directive.append("Le client pose une question pratique. Réponds-y avec les fiches « connaissance ». "
                             "Si l'information manque ou est entre crochets, propose de passer le relais à un conseiller.")
            if sujet_produit and not q["qualifie"]:
                directive.append(f"Termine en posant, naturellement, cette question de découverte : « {q['question']} »")
                profil["champ_attendu"], profil["derniere_question"] = q["champ"], q["question"]
        else:
            directive.append("Réponds simplement et chaleureusement, puis propose ton aide pour trouver le bon matériel.")
    elif not qualifie:
        directive.append(
            "RÈGLE DU MAGASIN : le besoin n'est pas encore assez clair, tu NE DONNES AUCUN PRIX et tu NE PROPOSES "
            "PAS ENCORE de modèle précis. Accueille la demande en une phrase (montre que tu as compris), "
            f"puis pose UNE SEULE question, celle-ci, reformulée naturellement : « {q['question']} »")
        if a.principale in ("PRIX", "DISPONIBILITE") and profil.get("produit_focus"):
            dispo = executer("verifier_stock", {"code": profil["produit_focus"]})
            if dispo.get("ok"):
                directive.append(f"Tu peux dire si ce modèle est disponible ({dispo['phrase']}), mais pas son prix : "
                                 "explique gentiment que tu veux d'abord être sûr qu'il correspond à son besoin.")
        fiches = [rag.Fiche(f.id, f.titre, rag.masquer_prix(f.contenu), f.type, f.score) for f in fiches]
        profil["champ_attendu"], profil["derniere_question"] = q["champ"], q["question"]
    else:
        profil["champ_attendu"] = None
        lignes, items = propositions(profil, texte)
        donnees += lignes
        types = {i["type"] for i in items}
        if {"voisin", "hors_budget_cat"} <= types:            # deux options différentes : « je le prends » = lequel ?
            profil["produit_focus"] = None
        elif items:                                           # produit dont on parle : pour « je le prends »,
            profil["produit_focus"] = items[0]["code"]        # la photo, la négociation
        directive.append(
            "Le besoin est clair : recommande 1 ou 2 produits MAXIMUM parmi les résultats du système. "
            "Convaincs avec honnêteté : 2 bénéfices concrets pour SON usage (pas une liste de caractéristiques), "
            "donne le prix et la disponibilité, rassure (matériel pro testé, garanti). "
            "Termine par UNE question qui fait avancer (réserver, voir une photo, passer en boutique).")
        if q.get("raison") == "insistance":
            directive.append("Le client a demandé le prix plusieurs fois : donne-le directement, sans reposer de question avant.")

    secours = {"items": items, "fiches": fiches, "salutation": a.principale == "SALUTATION" and not sujet_produit,
               "info": question_info}
    return [generer_reponse(conv, role, directive, fiches, donnees, qualifie=qualifie,
                            question_secours=q.get("question") if not q["qualifie"] else None, secours=secours)]


def propositions(profil: dict, texte: str) -> tuple[list[str], list[dict]]:
    """Résultats des skills : lignes pour l'IA + liste structurée pour les réponses de secours."""
    lignes, items = [], []
    budget = profil.get("budget_max")
    cible = executer("rechercher_produit", {"texte": texte, "categorie": profil.get("categorie_focus"),
                                            "usages": profil.get("usages"), "budget_max": budget,
                                            "budget_min": profil.get("budget_min"), "limite": 3})
    produits = cible.get("produits", [])
    focus = profil.get("produit_focus")
    if focus:
        st = executer("verifier_stock", {"code": focus})
        p = catalogue.par_code(focus)
        if p:
            hors_budget = budget and float(p["prix_vente"]) > budget * 1.05
            lignes.append(f"Produit demandé : {p['designation']} ({p['code']}) — {fcfa(p['prix_vente'])} — "
                          f"{st.get('phrase')}" + (" — AU-DESSUS du budget du client" if hors_budget else "")
                          + f" — idéal pour : {p.get('usages')}")
            items.append({"type": "demande", "code": p["code"], "nom": p["designation"], "prix": p["prix_vente"],
                          "phrase": st.get("phrase", ""), "hors_budget": bool(hors_budget),
                          "disponible": bool(st.get("disponible"))})
            if not st.get("disponible") or hors_budget:
                alt = executer("proposer_alternatives", {"code": focus, "budget_max": budget,
                                                         "usages": profil.get("usages")})
                for x in alt.get("produits", []):
                    lignes.append(f"Alternative : {x['designation']} ({x['code']}) — {fcfa(x['prix_vente'])} — "
                                  f"{x['stock']:g} en stock — idéal pour : {x.get('usages')}")
                    items.append({"type": "alternative", "code": x["code"], "nom": x["designation"], "prix": x["prix_vente"]})
    for x in produits:
        if x["code"] != focus:
            lignes.append(f"Produit proposé par le système : {x['designation']} ({x['code']}) — "
                          f"{fcfa(x['prix_vente'])} — {'disponible' if float(x['stock'] or 0) > 0 else 'rupture'} "
                          f"— idéal pour : {x.get('usages')}")
            items.append({"type": "propose", "code": x["code"], "nom": x["designation"], "prix": x["prix_vente"]})
    if (not produits and budget and profil.get("categorie_focus") in ("PC portable", "PC fixe")
            and profil.get("categorie_refusee") not in ("PC fixe", "PC portable")):
        autre = "PC fixe" if profil["categorie_focus"] == "PC portable" else "PC portable"
        voisin = executer("rechercher_produit", {"texte": "", "categorie": autre, "usages": profil.get("usages"),
                                                 "budget_max": budget, "limite": 1}).get("produits", [])
        for x in voisin:
            moins_cher_cat = sorted((p for p in catalogue.produits() if catalogue.disponible(p)
                                     and p["categorie"] == profil["categorie_focus"]), key=lambda p: float(p["prix_vente"]))[:1]
            for m in moins_cher_cat:
                lignes.append(f"AUCUN {profil['categorie_focus'].upper()} dans ce budget : le moins cher est {m['designation']} "
                              f"({m['code']}) à {fcfa(m['prix_vente'])}. Dis-le d'abord honnêtement, c'est ce que le client a demandé.")
                items.append({"type": "hors_budget_cat", "code": m["code"], "nom": m["designation"],
                              "prix": m["prix_vente"], "categorie": profil["categorie_focus"]})
            lignes.append(f"Alternative dans le budget, autre type d'ordinateur ({autre}) : {x['designation']} "
                          f"({x['code']}) — {fcfa(x['prix_vente'])} — idéal pour : {x.get('usages')}. "
                          "Présente-la ENSUITE comme une option, en expliquant la différence (fixe = écran à prévoir, "
                          "non transportable), puis demande ce qu'il préfère.")
            items.append({"type": "voisin", "code": x["code"], "nom": x["designation"], "prix": x["prix_vente"],
                          "categorie": autre})
        produits = voisin
    if not produits and budget:
        moins_cher = sorted((p for p in catalogue.produits() if catalogue.disponible(p)
                             and (not profil.get("categorie_focus") or p["categorie"] == profil["categorie_focus"])),
                            key=lambda p: float(p["prix_vente"]))[:1]
        for p in moins_cher:
            lignes.append(f"BUDGET TROP BAS : rien dans ce budget. Le moins cher disponible est {p['designation']} "
                          f"({p['code']}) à {fcfa(p['prix_vente'])}. Sois honnête et propose une piste "
                          "(ce modèle, ou améliorer un PC existant avec un SSD ou de la RAM).")
            items.append({"type": "moins_cher", "code": p["code"], "nom": p["designation"], "prix": p["prix_vente"],
                          "categorie": p["categorie"], "budget": budget})
    if "gaming" in (profil.get("usages") or []):
        lignes.append("ATTENTION : aucun de nos ordinateurs n'est conçu pour les jeux récents exigeants. "
                      "Dis-le honnêtement et propose le plus proche pour un usage léger.")
    return lignes, items


def generer_reponse(conv, role, directive, fiches, donnees, qualifie=True, question_secours=None,
                    secours=None, prix_impose=None) -> str:
    profil = conv["profil"]
    directive = directive + ["N'affirme jamais avoir envoyé une photo, transmis une demande, réservé, appelé ou "
                             "prévenu quelqu'un : seul le système fait ces actions, et il te le dit quand c'est fait. "
                             "N'invente aucun numéro, aucune adresse, aucun délai."]
    contexte = [
        f"Date : {dt.date.today():%d/%m/%Y}. Magasin : {config.NOM_MAGASIN}.",
        f"Interlocuteur : {role}. Profil connu : {resume_profil(profil)}.",
        "CONSIGNES POUR CE MESSAGE :\n- " + "\n- ".join(directive),
    ]
    if donnees:
        contexte.append("RÉSULTATS DU SYSTÈME (fiables, à utiliser tels quels) :\n" + "\n".join(donnees))
    if fiches:
        contexte.append("FICHES DU MAGASIN (seule source d'information autorisée) :\n" +
                        "\n\n".join(f"[{f.titre}]\n{f.contenu}" for f in fiches))
    systeme = INSTRUCTIONS + "\n\n=== CONTEXTE DU MESSAGE (généré par le système) ===\n" + "\n\n".join(contexte)

    historique = db.base().historique(conv["psid"], config.MEMOIRE_MESSAGES)
    messages = [{"role": m["role"], "content": m["contenu"]} for m in historique if m["role"] in ("user", "assistant")]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    reponse = ia.generer(systeme, messages)
    if reponse and not qualifie and rag.RE_PRIX.search(reponse):
        log.warning("L'IA a donné un prix avant qualification : réponse remplacée")
        reponse = None
    if reponse and prix_impose is not None and not prix_conforme(reponse, prix_impose):
        log.warning("L'IA n'a pas respecté le prix négocié (%s) : réponse remplacée", prix_impose)
        reponse = None
    if not reponse:
        reponse = reponse_preparee(conv, qualifie, question_secours, secours or {"fiches": fiches})
    return reponse


def reponse_preparee(conv, qualifie, question, secours) -> str:
    """Secours quand l'IA ne répond pas : utile, naturel, conforme aux règles, sans détail interne."""
    profil = conv["profil"]
    prenom = f" {profil['prenom']}" if profil.get("prenom") else ""
    items = secours.get("items") or []
    if secours.get("negociation"):
        return message_negociation(secours["negociation"])
    if not qualifie and question:
        return question
    if secours.get("salutation"):
        return (f"Bonjour{prenom} ! Bienvenue chez {config.NOM_MAGASIN}. Vous cherchez un ordinateur, un écran ou "
                "une pièce ? Dites-moi ce dont vous avez besoin, je vous conseille avec plaisir.")
    if secours.get("info"):
        connaissances = [f for f in secours.get("fiches") or [] if f.type == "connaissance" and "[" not in f.contenu]
        if connaissances:
            return connaissances[0].contenu + "\n\nPuis-je vous aider pour autre chose ?"
        return ("Pour vous répondre précisément, je préfère passer le relais à un conseiller : "
                "écrivez « conseiller » et une personne de l'équipe vous répondra ici.")
    moins_cher = [i for i in items if i["type"] == "moins_cher"]
    if moins_cher and not any(i["type"] in ("propose", "alternative") for i in items):
        m = moins_cher[0]
        return (f"Je vais être honnête avec vous : avec {fcfa(m['budget'])}, je n'ai pas de modèle qui convienne "
                f"pour le moment. Le plus abordable est le {m['nom']} à {fcfa(m['prix'])}. "
                "Une autre piste économique : améliorer un ordinateur existant avec un SSD ou de la mémoire. "
                "Laquelle des deux vous intéresse ?")
    voisin = next((i for i in items if i["type"] == "voisin"), None)
    hors = next((i for i in items if i["type"] == "hors_budget_cat"), None)
    if voisin and hors:
        return (f"Je vais être franc : aucun {hors['categorie'].lower()} n'entre dans votre budget pour le moment, "
                f"le plus abordable est le {hors['nom'].split(' (')[0]} à {fcfa(hors['prix'])}. Dans votre budget, je peux "
                f"en revanche vous proposer un ordinateur fixe, le {voisin['nom'].split(' (')[0]} à {fcfa(voisin['prix'])} "
                "(écran non inclus). Qu'est-ce qui vous conviendrait le mieux ?")
    lignes = []
    for i in items:
        if i["type"] == "demande":
            note = ""
            if i.get("hors_budget"):
                note = (" (un peu au-dessus de votre budget)" if float(i["prix"]) <= float(profil.get("budget_max") or 0) * 1.15
                        else " (au-dessus de votre budget)")
            etat = "" if i.get("disponible") else " — en rupture pour le moment"
            lignes.append(f"• {i['nom']} : {fcfa(i['prix'])}{note}{etat}")
        elif i["type"] in ("propose", "alternative"):
            lignes.append(f"• {i['nom']} : {fcfa(i['prix'])}")
    for i in items:
        if i["type"] == "voisin":
            lignes.append(f"• Dans votre budget, en ordinateur {'fixe' if i['categorie'] == 'PC fixe' else 'portable'} : "
                          f"{i['nom']} : {fcfa(i['prix'])}" + (" (écran non inclus)" if i["categorie"] == "PC fixe" else ""))
    if lignes:
        return ("Voici ce que je peux vous proposer :\n" + "\n".join(lignes[:3])
                + ("\n\nVoulez-vous voir une photo ou réserver l'un d'eux ?" if len(lignes) > 1
                   else "\n\nVoulez-vous voir une photo ou le réserver ?"))
    return ("Merci pour votre message ! Je rencontre un petit souci pour vous répondre en détail. "
            "Pouvez-vous reformuler, ou écrire « conseiller » pour qu'une personne de l'équipe vous réponde ?")


# ==========================================================================
# Commande (machine à états)
# ==========================================================================
def demarrer_commande(conv, a, intro=None) -> list[str]:
    profil = conv["profil"]
    code = profil.get("produit_focus")
    if not code:
        conv["profil"]["champ_attendu"] = None
        conv["profil"]["attente_commande"] = True
        return ["Avec plaisir ! Quel produit souhaitez-vous commander ? Donnez-moi le modèle "
                "(par exemple « Dell Latitude 5420 »)."]
    quantite = a.quantite or 1
    st = executer("verifier_stock", {"code": code, "quantite": quantite})
    if not st.get("suffisant"):
        alt = executer("proposer_alternatives", {"code": code, "budget_max": profil.get("budget_max")})
        noms = " ou ".join(f"{x['designation']} à {fcfa(x['prix_vente'])}" for x in alt.get("produits", []))
        return [f"Je suis désolé, pour le {st.get('designation', 'produit')} {st.get('phrase', 'le stock ne suffit pas')}. "
                + (f"Je peux vous proposer à la place : {noms}. Ça vous intéresse ?" if noms else
                   "Voulez-vous qu'un conseiller vous prévienne dès qu'il revient ?")]
    conv["panier"] = {"code": code, "quantite": quantite}
    accorde = (profil.get("negociation", {}).get(code) or {}).get("prix_accorde")
    if accorde:
        conv["panier"]["prix_unitaire"] = float(accorde)
    p = catalogue.par_code(code)
    debut = (intro or "Excellent choix ! ") + f"Je prépare la réservation du {nom_court(p['designation'])}."
    return [debut + " " + prochaine_question_commande(conv)]


def prochaine_question_commande(conv) -> str:
    panier, profil = conv["panier"], conv["profil"]
    panier.setdefault("nom", profil.get("nom"))
    panier.setdefault("telephone", profil.get("telephone"))
    if not panier.get("nom"):
        conv["etat"] = "COMMANDE_NOM"
        return "À quel nom dois-je enregistrer la commande ?"
    if not panier.get("telephone"):
        conv["etat"] = "COMMANDE_TELEPHONE"
        return "Quel numéro de téléphone pouvons-nous utiliser pour vous joindre ?"
    if not panier.get("mode_livraison"):
        conv["etat"] = "COMMANDE_LIVRAISON"
        return "Vous préférez passer le récupérer en boutique, ou être livré ?"
    if panier["mode_livraison"] == "livraison" and not panier.get("quartier"):
        conv["etat"] = "COMMANDE_QUARTIER"
        return "Dans quel quartier (et quelle ville) faut-il livrer ?"
    conv["etat"] = "COMMANDE_CONFIRMATION"
    return recapitulatif(panier)


def recapitulatif(panier) -> str:
    p = catalogue.par_code(panier["code"])
    pu = float(panier.get("prix_unitaire") or p["prix_vente"])
    total = pu * panier["quantite"]
    prix_txt = (f"{fcfa(pu)} l'unité (au lieu de {fcfa(p['prix_vente'])})" if pu < float(p["prix_vente"])
                else f"{fcfa(pu)} l'unité")
    livraison = ("retrait en boutique" if panier["mode_livraison"] == "retrait"
                 else f"livraison à {panier['quartier']} (frais confirmés par un conseiller)")
    return (f"Je récapitule :\n• {panier['quantite']} × {p['designation']}\n• Prix : {prix_txt}\n• Total : {fcfa(total)}\n"
            f"• Au nom de {panier['nom']}, tél. {panier['telephone']}\n• {livraison[0].upper() + livraison[1:]}\n\n"
            "Je confirme la réservation ? (oui / non — ou indiquez une autre quantité)")


def etape_commande(conv, role, a, texte) -> list[str]:
    etat, panier, profil = conv["etat"], conv["panier"], conv["profil"]
    t = sans_accents(texte)

    # Négociation pendant la commande : le prix de la réservation est mis à jour
    if a.principale == "NEGOCIATION" or (a.principale == "PRIX" and a.offre):
        reponse = negocier(conv, role, a, texte, code=panier["code"])
        accorde = (profil.get("negociation", {}).get(panier["code"]) or {}).get("prix_accorde")
        if accorde:
            panier["prix_unitaire"] = float(accorde)
            profil.pop("attente_oui", None)
            return [reponse[0], prochaine_question_commande(conv)]
        attente = profil.pop("attente_oui", None)
        if attente:
            panier["offre_en_attente"] = attente["prix"]
        return reponse
    if etat == "COMMANDE_CONFIRMATION" and panier.get("offre_en_attente") and a.principale == "OUI":
        prix = panier.pop("offre_en_attente")
        panier["prix_unitaire"] = prix
        profil.setdefault("negociation", {}).setdefault(panier["code"], {})["prix_accorde"] = prix
        return [f"Marché conclu à {fcfa(prix)} 🤝", recapitulatif(panier)]

    # Question annexe en pleine commande (garantie, paiement…) : on répond puis on reprend
    reponse_attendue = (etat == "COMMANDE_LIVRAISON" and re.search(
        r"boutique|retir|recuper|passer|venir|sur place|magasin|livr|domicile|chez moi", t)) or \
        (etat == "COMMANDE_TELEPHONE" and (a.telephone or a.telephone_invalide))
    if a.principale in INTENTIONS_INFO | {"PHOTO"} and not reponse_attendue:
        if a.principale == "PHOTO":
            debut = photo(conv)[0]
        else:
            fiches = rag.rechercher(texte, niveau=0)
            debut = generer_reponse(conv, role, ["Réponds brièvement à cette question pratique avec les fiches. "
                                                 "Ne pose pas de question à la fin."], fiches, [])
        return [debut, "Pour finaliser votre commande : " + prochaine_question_commande(conv)]

    if etat == "COMMANDE_NOM":
        nom = re.sub(r"(?i)^(je m'?appelle|moi c'?est|c'?est|mon nom est|au nom de)\s+", "", texte).strip(" .!")
        if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", nom)) < 2 or len(nom) > 60:
            return ["Je n'ai pas bien saisi. Quel nom dois-je mettre sur la commande ?"]
        panier["nom"] = profil["nom"] = nom.title()
    elif etat == "COMMANDE_TELEPHONE":
        if a.telephone_invalide and not a.telephone:
            panier["essais_tel"] = panier.get("essais_tel", 0) + 1
            if panier["essais_tel"] >= 3:
                return transferer_humain(conv, "HUMAIN", "Le client n'arrive pas à donner un numéro valide.")
            return ["Ce numéro ne semble pas valide : un numéro camerounais a 9 chiffres et commence par 6 "
                    "(exemple : 6 77 12 34 56, avec ou sans +237). Pouvez-vous le vérifier ?"]
        if not a.telephone:
            panier["essais_tel"] = panier.get("essais_tel", 0) + 1
            if panier["essais_tel"] >= 3:
                return transferer_humain(conv, "HUMAIN", "Le client n'arrive pas à donner un numéro valide.")
            return ["Il me faut un numéro camerounais à 9 chiffres commençant par 6 (exemple : 6 77 12 34 56). "
                    "Vous pouvez me le réécrire ?"]
        panier["telephone"] = profil["telephone"] = a.telephone
    elif etat == "COMMANDE_LIVRAISON":
        if re.search(r"boutique|retir|recuper|passer|venir|sur place|magasin|moi[- ]meme", t):
            panier["mode_livraison"] = "retrait"
        elif re.search(r"livr|envoy|domicile|chez moi|expedi|bureau", t):
            panier["mode_livraison"] = "livraison"
            m = re.search(r"(?:^|\s)(?:à|a|au|aux|vers|quartier)\s+([A-Za-zÀ-ÿ' -]{3,40})$", texte.strip(" .!"), re.I)
            if m and not re.search(r"livr|domicile|maison", sans_accents(m.group(1))):
                panier["quartier"] = m.group(1).strip().title()
        else:
            return ["Dites-moi simplement « boutique » si vous passez le chercher, ou « livraison » si on vous l'apporte."]
    elif etat == "COMMANDE_QUARTIER":
        if len(texte) < 3:
            return ["Quel quartier exactement ? (par exemple : Bastos, Essos, Mvog-Mbi…)"]
        panier["quartier"] = re.sub(r"(?i)^(je suis |c'?est |plutot |plutôt )?(à|a|au|aux|vers|sur|quartier)\s+", "",
                                    texte.strip(" .!"))[:80]
    elif etat == "COMMANDE_CONFIRMATION":
        if a.quantite and a.quantite != panier["quantite"]:
            st = executer("verifier_stock", {"code": panier["code"], "quantite": a.quantite})
            if not st.get("suffisant"):
                return [f"Pour {a.quantite} exemplaires, {st.get('phrase')}. Je garde {panier['quantite']} ? (oui / non)"]
            panier["quantite"] = a.quantite
            return [recapitulatif(panier)]
        if a.principale == "OUI" or "OUI" in a.toutes:
            return valider_commande(conv)
        if a.principale == "NON" or "NON" in a.toutes:
            conv["etat"], conv["panier"] = "CONVERSATION", {}
            return ["D'accord, je n'enregistre rien. Dites-moi ce que vous souhaitez changer, je reprends avec vous."]
        return ["Je confirme la réservation ? Répondez simplement « oui » ou « non »."]
    return [prochaine_question_commande(conv)]


def valider_commande(conv) -> list[str]:
    panier, profil = conv["panier"], conv["profil"]
    res = executer("enregistrer_commande", {"psid": conv["psid"], "nom": panier.get("nom"),
                                            "telephone": panier.get("telephone"),
                                            "mode_livraison": panier.get("mode_livraison"),
                                            "quartier": panier.get("quartier"),
                                            "lignes": [{"code": panier["code"], "quantite": panier["quantite"],
                                                        "prix_unitaire": panier.get("prix_unitaire")}]})
    if not res.get("ok"):
        log.warning("Commande refusée : %s", res.get("erreurs"))
        conv["panier"] = {}
        transferer_humain(conv, "HUMAIN", "Commande en échec : " + "; ".join(res.get("erreurs", [])))
        return ["Je n'ai pas pu enregistrer la commande (" + "; ".join(res.get("erreurs", ["erreur"])) + "). "
                "Je préviens un conseiller, il vous répond ici pour finaliser avec vous."]
    l = res["lignes"][0]
    executer("notifier_email", {"type": "commande", "sujet": f"{res['reference']} — {fcfa(res['total'])}",
                                "contenu": (f"Référence : {res['reference']}\nClient : {panier['nom']} — {res['telephone']}\n"
                                            f"Produit : {l['quantite']} × {l['designation']} ({l['code']})\n"
                                            + (f"Prix NÉGOCIÉ : {fcfa(l['prix_unitaire'])} l'unité (catalogue "
                                               f"{fcfa(l['prix_catalogue'])})\n" if l['prix_unitaire'] < l['prix_catalogue'] else "")
                                            + f"Total : {fcfa(res['total'])}\n"
                                            f"Mode : {panier['mode_livraison']}"
                                            + (f" — {panier.get('quartier')}" if panier.get("quartier") else "")
                                            + f"\nProfil : {resume_profil(profil)}\n\n"
                                            "À faire : rappeler le client pour confirmer et organiser le paiement.")})
    profil.setdefault("commandes", []).append(res["reference"])
    conv["etat"], conv["panier"] = "CONVERSATION", {}
    return [f"C'est noté ✅ Votre réservation {res['reference']} est enregistrée ({fcfa(res['total'])}). "
            "Un conseiller vous appelle très vite pour confirmer et organiser le paiement. Merci pour votre confiance !"]



# ==========================================================================
# Négociation (le prix est décidé par le skill negocier_prix, l'IA ne fait que le dire)
# ==========================================================================
def negocier(conv, role, a, texte, code=None) -> list[str]:
    profil = conv["profil"]
    code = code or profil.get("produit_focus")
    if not code:
        return ["Je comprends, on peut en discuter ! Pour quel modèle exactement ?"]
    etats = profil.setdefault("negociation", {})
    r = executer("negocier_prix", {"code": code, "offre": a.offre, "etat": etats.get(code)})
    if not r.get("ok"):
        return ["Pour quel modèle exactement souhaitez-vous un prix ?"]
    etats[code] = r["etat"]
    p = catalogue.par_code(code)
    prix, catalogue_prix, decision = r["prix"], r["prix_catalogue"], r["decision"]
    stock = executer("verifier_stock", {"code": code})

    alternatives = []
    if decision in ("dernier_prix", "prix_ferme") or r.get("offre_trop_basse"):
        alt = executer("proposer_alternatives", {"code": code, "budget_max": a.offre or prix * 0.95,
                                                 "usages": profil.get("usages"), "limite": 1})
        alternatives = [x for x in alt.get("produits", []) if float(x["prix_vente"]) < prix]

    consignes = {
        "deja_moins_cher": f"Bonne nouvelle : le prix est déjà de {fcfa(prix)}, en dessous de ce que le client "
                           "proposait. Dis-le avec le sourire et propose de le réserver.",
        "accepte": f"Le système ACCEPTE : prix accordé {fcfa(prix)} (au lieu de {fcfa(catalogue_prix)}). "
                   "Montre-toi content de lui faire plaisir, félicite-le pour la bonne affaire, "
                   "et demande s'il veut que tu le lui réserves à ce prix.",
        "contre_offre": f"Le système fait une CONTRE-OFFRE : {fcfa(prix)} (au lieu de {fcfa(catalogue_prix)}). "
                        "Accueille sa demande avec chaleur, ne dis pas non sèchement : explique en 1 ou 2 arguments "
                        "concrets pourquoi le produit vaut ce prix pour SON usage, annonce la contre-offre comme un "
                        "geste, puis demande si on part là-dessus.",
        "dernier_prix": f"Le système donne son DERNIER PRIX : {fcfa(prix)}. Dis clairement, gentiment, que c'est le "
                        "maximum possible, rappelle la valeur du produit, et demande s'il souhaite le réserver."
                        + (" S'il trouve encore ça trop cher, propose l'alternative moins chère indiquée." if alternatives else ""),
        "prix_ferme": f"Le prix est FERME : {fcfa(prix)}. Explique avec tact qu'il est déjà au plus juste, "
                      "valorise le produit" + (", et propose l'alternative moins chère indiquée." if alternatives else "."),
        "deja_au_prix": f"Un prix de {fcfa(prix)} a déjà été accordé : c'est le meilleur possible, on ne descend plus. "
                        "Rappelle-le gentiment et propose de réserver.",
    }
    directive = [consignes[decision],
                 f"Montant à annoncer EXACTEMENT : {fcfa(prix)}. N'annonce aucun autre prix pour ce produit, "
                 "n'invente aucune autre remise, cadeau ou condition.",
                 "Reste chaleureux et convaincant, jamais sur la défensive. 3 à 4 phrases maximum."]
    if r.get("offre_trop_basse"):
        directive.append("Son offre est très loin du prix : dis-le avec humour et respect, sans le vexer.")
    donnees = [f"Produit négocié : {p['designation']} ({code}) — prix catalogue {fcfa(catalogue_prix)} — "
               f"{stock.get('phrase')} — idéal pour : {p.get('usages')}" +
               (f" — caractéristiques : {p.get('description')}" if p.get("description") else "")]
    if stock.get("statut") == "Stock faible":
        donnees.append("Stock faible : tu peux dire honnêtement qu'il en reste peu.")
    for x in alternatives:
        donnees.append(f"Alternative moins chère : {x['designation']} ({x['code']}) — {fcfa(x['prix_vente'])}")
    if decision in ("contre_offre", "dernier_prix"):
        profil["attente_oui"] = {"action": "accepter_offre", "code": code, "prix": prix}
    elif decision in ("accepte", "deja_au_prix", "deja_moins_cher"):
        profil["attente_oui"] = {"action": "reserver", "code": code, "prix": prix}

    secours = {"negociation": {**r, "nom": p["designation"], "usages_client": profil.get("usages"),
                               "alternative": alternatives[0] if alternatives else None}}
    reponse = generer_reponse(conv, role, directive, [], donnees, qualifie=True, secours=secours,
                              prix_impose=prix)
    return [reponse]


def prix_conforme(reponse: str, prix_impose: float) -> bool:
    """L'IA doit annoncer le prix décidé et ne jamais citer de montant plus bas (sauf prix d'un autre produit)."""
    montants = [float(re.sub(r"\D", "", m)) for m in rag.RE_PRIX.findall(reponse) if re.sub(r"\D", "", m)]
    if prix_impose not in montants:
        return False
    autres = {float(p["prix_vente"]) for p in catalogue.produits()}
    return all(m >= prix_impose or m in autres for m in montants)


USAGES_TEXTE = {"bureautique": "la bureautique", "etudes": "vos études", "gestion": "la gestion",
                "developpement": "la programmation", "graphisme": "le graphisme", "entreprise": "votre entreprise",
                "maison": "la maison", "deplacement": "vos déplacements", "gaming": "le jeu"}


def nom_court(designation: str) -> str:
    return designation.split(" (")[0]


def message_negociation(n: dict) -> str:
    prix, nom = fcfa(n["prix"]), nom_court(n["nom"])
    usages = [USAGES_TEXTE[u] for u in n.get("usages_client") or [] if u in USAGES_TEXTE]
    arg = (f" Pour {' et '.join(usages[:2])}, c'est vraiment un excellent choix." if usages else
           " C'est une machine fiable, vous ne serez pas déçu.")
    alt = n.get("alternative")
    alt_txt = (f" Si c'est encore trop, le {nom_court(alt['designation'])} est à {fcfa(alt['prix_vente'])}." if alt else "")
    return {
        "deja_moins_cher": f"Bonne nouvelle : le {nom} est déjà à {prix}, c'est même moins que ce que vous proposiez ! "
                           "Je vous le réserve ?",
        "accepte": f"Allez, c'est d'accord pour {prix} ! Vous faites une très bonne affaire sur le {nom}. "
                   "Je vous le réserve à ce prix ?",
        "contre_offre": f"Je comprends, et j'ai envie de vous faire plaisir.{arg} Ce que je peux faire pour vous, "
                        f"c'est {prix} au lieu de {fcfa(n['prix_catalogue'])}. On part là-dessus ?",
        "dernier_prix": f"J'ai vraiment fait le maximum : {prix}, c'est mon dernier prix pour le {nom}.{alt_txt} "
                        "Je vous le réserve ?",
        "prix_ferme": f"Le {nom} est déjà à son prix le plus juste, {prix}, je ne peux pas descendre davantage.{alt_txt} "
                      "Voulez-vous le réserver ?",
        "deja_au_prix": f"Nous sommes déjà d'accord sur {prix}, c'est le meilleur prix possible pour le {nom}. "
                        "Je vous le réserve ?",
    }[n["decision"]]

# ==========================================================================
# Humain, SAV, photo
# ==========================================================================
def transferer_humain(conv, motif, texte) -> list[str]:
    profil = conv["profil"]
    conv["etat"] = "HUMAIN"
    conv["handoff_jusqua"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=config.HANDOFF_HEURES)).isoformat()
    derniers = db.base().historique(conv["psid"], 8)
    executer("notifier_email", {
        "type": "plainte" if motif == "PLAINTE" else "humain",
        "sujet": f"client {profil.get('prenom') or conv['psid'][-6:]}",
        "contenu": (f"Un client demande à parler à une personne (motif : {motif.lower()}).\n"
                    f"Téléphone : {profil.get('telephone') or 'non communiqué'}\nProfil : {resume_profil(profil)}\n\n"
                    "Derniers messages :\n" + "\n".join(f"{'Client' if m['role'] == 'user' else 'Bot'} : {m['contenu']}"
                                                        for m in derniers)
                    + "\n\nRépondez-lui depuis la boîte de réception de la Page Facebook (le bot se met en pause).")})
    if motif == "PLAINTE":
        msg = ("Je suis sincèrement désolé pour cette situation. Je transmets tout de suite votre message à un "
               "responsable, qui va vous répondre personnellement ici.")
    else:
        msg = "Bien sûr ! Je préviens un conseiller de l'équipe, il vous répond ici dès que possible."
    if not profil.get("telephone"):
        msg += " Si vous préférez être appelé, laissez-moi votre numéro."
    return [msg]


def enregistrer_sav(conv, texte) -> list[str]:
    profil = conv["profil"]
    conv["etat"] = "CONVERSATION"
    executer("notifier_email", {"type": "sav", "sujet": f"client {profil.get('prenom') or conv['psid'][-6:]}",
                                "contenu": f"Problème décrit : {texte}\nTéléphone : {profil.get('telephone') or 'non communiqué'}\n"
                                           f"Profil : {resume_profil(profil)}"})
    msg = ("Merci pour ces précisions. J'ai transmis votre demande à notre service technique, qui vous recontacte "
           "rapidement. Pensez à garder votre facture : elle est nécessaire pour la garantie.")
    if not profil.get("telephone"):
        msg += " Pouvez-vous me laisser un numéro pour vous joindre ?"
    return [msg]


def photo(conv) -> list[str]:
    code = conv["profil"].get("produit_focus")
    if not code:
        conv["profil"]["attente_photo"] = True
        return ["Avec plaisir ! De quel modèle voulez-vous voir la photo ?"]
    res = executer("envoyer_photo", {"psid": conv["psid"], "code": code})
    p = catalogue.par_code(code) or {}
    if res.get("envoye"):
        return [f"Voici le {p.get('designation', 'produit')} 📷 Il vous plaît ?"]
    return [f"Je n'ai pas encore de photo du {p.get('designation', 'produit')} à vous envoyer ici. "
            "Vous pouvez venir le voir en boutique, ou je demande à un conseiller de vous en envoyer une."]


# ==========================================================================
# Mode équipe (vendeur / admin / boss)
# ==========================================================================
NIVEAUX = {"vendeur": 1, "admin": 2, "boss": 2}


def mode_equipe(conv, role, texte, a) -> list[str]:
    fiches = rag.rechercher(texte, niveau=NIVEAUX.get(role, 0), k=8)
    directive = [f"Tu parles à un membre de l'équipe ({role}). Pas de discours commercial ni de questions de "
                 "découverte : réponds directement, chiffres précis d'abord, en t'appuyant sur les fiches. "
                 "Si l'information n'est pas dans les fiches, dis-le."]
    return [generer_reponse(conv, role, directive, fiches, [], qualifie=True)]


def commande_equipe(conv, role, texte) -> list[str]:
    morceaux = texte.split()
    cmd = morceaux[0].lower()
    psid = conv["psid"]
    if cmd == "/connexion":
        res = executer("verifier_permissions", {"action": "role_depuis_code",
                                                "code": morceaux[1] if len(morceaux) > 1 else ""})
        if not res.get("ok"):
            return ["Code incorrect."]
        db.base().definir_role(psid, res["role"])
        conv["etat"] = "CONVERSATION"
        return [f"Connecté en tant que {res['role']}. Tapez /aide pour voir ce que vous pouvez faire."]
    if cmd == "/deconnexion":
        db.base().definir_role(psid, "client")
        return ["Vous êtes revenu en mode client."]
    if cmd == "/moi":
        return [f"Rôle : {role}\nIdentifiant Messenger : {psid}"]
    if cmd == "/aide":
        lignes = ["/moi — votre rôle", "/deconnexion — revenir en mode client"]
        if role in ("vendeur", "admin", "boss"):
            lignes += ["/stock — ruptures et stocks faibles", "Question libre — ex. « il reste combien de 5420 ? »"]
        if role in ("admin", "boss"):
            lignes += ["/commandes — 10 dernières réservations", "Question libre — ex. « combien on a vendu hier ? »"]
        if role == "boss":
            lignes += ["/instructions — recharger instructions.md sans redémarrer"]
        return ["Commandes disponibles :\n" + "\n".join(lignes)]
    if cmd == "/stock":
        if not executer("verifier_permissions", {"action": "voir_alertes_stock", "role": role})["autorise"]:
            return ["Commande réservée à l'équipe."]
        alertes = [p for p in catalogue.produits() if float(p.get("stock") or 0) <= float(p.get("seuil") or 0)]
        if not alertes:
            return ["Aucune rupture ni stock faible 👍"]
        return ["Stock à surveiller :\n" + "\n".join(
            f"• {p['designation'][:45]} ({p['code']}) : {float(p['stock']):g}" for p in sorted(alertes, key=lambda x: x["stock"]))]
    if cmd == "/commandes":
        if not executer("verifier_permissions", {"action": "voir_commandes", "role": role})["autorise"]:
            return ["Commande réservée à l'administration."]
        cmds = db.base().dernieres_commandes(10)
        if not cmds:
            return ["Aucune réservation pour le moment."]
        return ["Dernières réservations :\n" + "\n".join(
            f"• {c['reference']} — {c.get('nom')} ({c.get('telephone')}) — {fcfa(c['total'])} — {c.get('statut')}"
            for c in cmds)]
    if cmd == "/instructions":
        if role != "boss":
            return ["Commande réservée au boss."]
        recharger_instructions()
        return ["Instructions rechargées ✅"]
    return ["Commande inconnue. Tapez /aide."]
