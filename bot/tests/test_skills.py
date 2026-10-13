# -*- coding: utf-8 -*-
"""Tests unitaires : chaque skill testé indépendamment.  Lancer :  pytest -q"""
import intentions
import messenger
from skills import executer


# ---------- intentions & extraction
def test_intentions_principales():
    assert intentions.analyser("c'est combien le latitude ?").principale == "PRIX"
    assert intentions.analyser("je veux parler à un humain").principale == "HUMAIN"
    assert intentions.analyser("mon pc ne s'allume plus").principale == "SAV"
    assert intentions.analyser("vous êtes des arnaqueurs").principale == "PLAINTE"
    assert intentions.analyser("je le prends").principale == "COMMANDE"
    assert intentions.analyser("oui c'est bon").principale == "OUI"
    assert intentions.analyser("Bonsoir").principale == "SALUTATION"


def test_extraction_budget_usages_telephone():
    a = intentions.analyser("pas plus de 300 000 pour mon fils étudiant")
    assert a.budget == 300000 and "etudes" in a.usages and a.pour_qui == "mon fils"
    assert intentions.analyser("budget 250k").budget == 250000
    a = intentions.analyser("entre 200 et 300 mille")
    assert (a.budget_min, a.budget) == (200000, 300000)
    assert intentions.analyser("300", attend_budget=True).budget == 300000
    assert intentions.analyser("mon numéro : +237 6 77 12 34 56").telephone == "677123456"
    assert "graphisme" in intentions.analyser("je fais du montage vidéo").usages


# ---------- rechercher_produit
def test_recherche_modele_et_fautes():
    assert executer("rechercher_produit", {"texte": "le dell latitude 5420"})["produit_cite"] == "LAP-001"
    assert executer("rechercher_produit", {"texte": "vous avez l'elitbok ?"})["produit_cite"] == "LAP-003"
    assert executer("rechercher_produit", {"texte": "un tinkpad"})["produit_cite"] == "LAP-005"
    assert executer("rechercher_produit", {"texte": "macbook air"})["produit_cite"] == "LAP-006"


def test_recherche_categorie_et_budget():
    r = executer("rechercher_produit", {"texte": "un ecran 24 pouces"})
    assert r["categorie"] == "Écran" and all(p["categorie"] == "Écran" for p in r["produits"])
    r = executer("rechercher_produit", {"texte": "ordinateur portable", "budget_max": 300000, "usages": ["etudes"]})
    assert r["produits"] and all(p["prix_vente"] <= 315000 for p in r["produits"])
    assert r["produits"][0]["code"] in ("LAP-001", "LAP-004")
    assert executer("rechercher_produit", {"texte": "dell"})["produit_cite"] is None   # trop vague


# ---------- qualifier_besoin
def test_qualification():
    q = executer("qualifier_besoin", {"profil": {}, "categorie": "PC portable"})
    assert not q["qualifie"] and q["champ"] == "usage" and q["question"].count("?") == 1
    q = executer("qualifier_besoin", {"profil": {"usages": ["etudes"]}, "categorie": "PC portable"})
    assert q["champ"] == "budget"
    assert executer("qualifier_besoin", {"profil": {"usages": ["etudes"], "budget_max": 300000}})["qualifie"]
    assert executer("qualifier_besoin", {"profil": {"usages": ["gestion"]}, "produit_cite": "LAP-003"})["qualifie"]
    q = executer("qualifier_besoin", {"profil": {}, "categorie": "Composant"})
    assert q["champ"] == "modele_pc"
    q = executer("qualifier_besoin", {"profil": {"nb_demandes_prix": 2}, "categorie": "PC portable"})
    assert q["qualifie"] and q["raison"] == "insistance"


# ---------- stock, alternatives
def test_stock_et_alternatives():
    s = executer("verifier_stock", {"code": "LAP-006", "quantite": 5})
    assert s["disponible"] and not s["suffisant"]
    alt = executer("proposer_alternatives", {"code": "LAP-001", "limite": 2})
    assert len(alt["produits"]) == 2 and all(p["categorie"] == "PC portable" for p in alt["produits"])
    assert "LAP-001" not in [p["code"] for p in alt["produits"]]


# ---------- permissions
def test_permissions():
    assert executer("verifier_permissions", {"action": "voir_marges", "role": "client"})["autorise"] is False
    assert executer("verifier_permissions", {"action": "voir_marges", "role": "admin"})["autorise"] is True
    assert executer("verifier_permissions", {"action": "gerer_roles", "role": "admin"})["autorise"] is False
    assert executer("verifier_permissions", {"action": "role_depuis_code", "code": "a123"})["role"] == "admin"
    assert executer("verifier_permissions", {"action": "role_depuis_code", "code": "faux"})["ok"] is False


# ---------- commande, email, photo
def test_commande():
    ok = executer("enregistrer_commande", {"psid": "x", "nom": "Paul", "telephone": "+237 677 12 34 56",
                                          "mode_livraison": "retrait", "lignes": [{"code": "ECR-003", "quantite": 2}]})
    assert ok["ok"] and ok["total"] == 164000 and ok["reference"].startswith("CMD-")
    ko = executer("enregistrer_commande", {"psid": "x", "nom": "Paul", "telephone": "1234",
                                          "lignes": [{"code": "LAP-006", "quantite": 9}]})
    assert not ko["ok"] and len(ko["erreurs"]) == 2


def test_email_simulation_avec_copie():
    r = executer("notifier_email", {"type": "commande", "sujet": "test", "contenu": "Bonjour"})
    assert r["canal"] == "simulation" and "martial@exemple.cm" in r["destinataires"]


def test_photo_absente():
    r = executer("envoyer_photo", {"psid": "x", "code": "LAP-001"})
    assert r["raison"] == "pas_de_photo"


def test_skill_inconnu_ne_plante_pas():
    assert executer("nexiste_pas", {})["ok"] is False


# ---------- Messenger : 1900 caractères maximum
def test_decoupage_messenger():
    texte = ("Une phrase assez longue pour tester le découpage. " * 120).strip()
    morceaux = messenger.decouper(texte)
    assert len(morceaux) >= 3 and all(len(m) <= 1900 for m in morceaux)
    assert " ".join(morceaux).replace("  ", " ") == texte


# ---------- négociation (catalogue de test : LAP-001 à 295 000, plancher 275 000)
def test_negociation_paliers_et_plancher():
    etat = None
    prix_vus = []
    for offre in (200000, 200000, 200000, 200000):          # client qui ne bouge pas
        r = executer("negocier_prix", {"code": "LAP-001", "offre": offre, "etat": etat})
        etat = r["etat"]
        prix_vus.append(r["prix"])
    assert prix_vus[0] < 295000 and prix_vus == sorted(prix_vus, reverse=True)
    assert min(prix_vus) == 275000 and r["decision"] == "dernier_prix" and r["offre_trop_basse"]


def test_negociation_accepte_offre_raisonnable():
    r1 = executer("negocier_prix", {"code": "LAP-001", "offre": 280000})
    assert r1["decision"] == "contre_offre" and r1["prix"] == 285000
    r2 = executer("negocier_prix", {"code": "LAP-001", "offre": 285000, "etat": r1["etat"]})
    assert r2["decision"] == "accepte" and r2["prix"] == 285000
    r3 = executer("negocier_prix", {"code": "LAP-001", "offre": 250000, "etat": r2["etat"]})
    assert r3["decision"] == "deja_au_prix" and r3["prix"] == 285000


def test_negociation_sans_plancher_prix_ferme(monkeypatch):
    import catalogue
    p = dict(catalogue.par_code("LAP-001"))
    p["prix_min"] = None
    monkeypatch.setattr(catalogue, "par_code", lambda c: p)
    assert executer("negocier_prix", {"code": "LAP-001", "offre": 280000})["decision"] == "prix_ferme"


def test_commande_refuse_prix_sous_plancher():
    r = executer("enregistrer_commande", {"psid": "x", "nom": "Paul", "telephone": "677123456",
                                         "lignes": [{"code": "LAP-001", "quantite": 1, "prix_unitaire": 200000}]})
    assert not r["ok"] and "prix négocié invalide" in r["erreurs"][0]


def test_telephone_trop_long_refuse():
    a = intentions.analyser("676354365533333")
    assert a.telephone is None and a.telephone_invalide


def test_je_veux_image_nest_pas_une_commande():
    assert intentions.analyser("je veux l'image d'abord").principale == "PHOTO"
    assert intentions.analyser("je veux un laptop").principale != "COMMANDE"
