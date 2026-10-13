# -*- coding: utf-8 -*-
"""Scénarios complets : règles du patron vérifiées de bout en bout (IA simulée)."""
import conversation
import db


def dire(texte, psid="client1"):
    return conversation.traiter(psid, texte)


def test_pas_de_prix_avant_qualification(ia_factice):
    r = dire("bonsoir c'est combien le HP EliteBook ?")
    systeme = ia_factice["appels"][-1]["systeme"]
    assert "330 000" not in systeme and "NE DONNES AUCUN PRIX" in systeme
    assert db.base().conversation("client1")["profil"]["champ_attendu"] == "usage"


def test_prix_donne_apres_qualification(ia_factice):
    dire("c'est combien le HP EliteBook ?")
    dire("pour la comptabilité, beaucoup d'excel")
    systeme = ia_factice["appels"][-1]["systeme"]
    assert "330 000 FCFA" in systeme and "recommande 1 ou 2 produits" in systeme


def test_ia_qui_donne_un_prix_trop_tot_est_corrigee(ia_factice):
    ia_factice["reponses"].append("Il coûte 330 000 FCFA !")
    r = dire("prix du elitebook ?")
    assert "FCFA" not in r[0] and "?" in r[0]


def test_parcours_besoin_budget_proposition(ia_factice):
    dire("je cherche un ordinateur pour mon fils")
    p = db.base().conversation("client1")["profil"]
    assert p["champ_attendu"] == "usage" and p["pour_qui"] == "mon fils"
    dire("il entre à l'université")
    assert db.base().conversation("client1")["profil"]["champ_attendu"] == "budget"
    dire("pas plus de 300 000")
    systeme = ia_factice["appels"][-1]["systeme"]
    assert "Produit proposé par le système" in systeme and "Latitude 5420" in systeme
    assert "MacBook" not in systeme.split("RÉSULTATS DU SYSTÈME")[1].split("FICHES")[0]


def test_insistance_sur_le_prix(ia_factice):
    dire("prix du dell latitude 5420 ?")
    dire("donnez moi juste le prix svp")
    assert "plusieurs fois" in ia_factice["appels"][-1]["systeme"]


def test_commande_complete(ia_factice, emails):
    dire("le samsung S24R350 pour la maison", "c2")
    r = dire("je le prends", "c2")
    assert "nom" in r[0]
    dire("Jean Mballa", "c2")
    r = dire("677 12 34 56", "c2")
    assert "boutique" in r[0]
    r = dire("livraison à Bastos", "c2")
    assert "récapitule" in r[0] and "Bastos" in r[0] and "82 000 FCFA" in r[0]
    r = dire("2", "c2")
    assert "164 000 FCFA" in r[0]
    r = dire("oui", "c2")
    assert "CMD-" in r[0]
    conv = db.base().conversation("c2")
    assert conv["etat"] == "CONVERSATION" and conv["profil"]["nom"] == "Jean Mballa"
    assert db.base().dernieres_commandes()[0]["total"] == 164000
    assert emails and emails[-1]["type"] == "commande"


def test_question_pendant_commande_puis_reprise(ia_factice):
    dire("le dell p2422h pour le bureau", "c3")
    dire("je veux commander", "c3")
    r = dire("il y a une garantie ?", "c3")
    assert len(r) == 2 and r[1].startswith("Pour finaliser") and "nom" in r[1]


def test_annulation_commande(ia_factice):
    dire("le dell p2422h pour le bureau", "c4")
    dire("je le prends", "c4")
    r = dire("laisse tomber", "c4")
    assert "annulé" in r[0] and db.base().conversation("c4")["etat"] == "CONVERSATION"


def test_transfert_humain_puis_silence(ia_factice, emails):
    r = dire("je veux parler à un conseiller", "c5")
    assert "conseiller" in r[0] and emails[-1]["type"] == "humain"
    assert dire("allo ?", "c5") == []
    assert db.base().conversation("c5")["etat"] == "HUMAIN"


def test_plainte(ia_factice, emails):
    r = dire("vous êtes des arnaqueurs, mon pc est déjà en panne", "c6")
    assert "désolé" in r[0] and emails[-1]["type"] == "plainte"


def test_sav(ia_factice, emails):
    r = dire("mon ordinateur ne s'allume plus", "c7")
    assert db.base().conversation("c7")["etat"] == "SAV_DETAILS"
    dire("c'est le latitude 5420 acheté le mois dernier, écran noir au démarrage", "c7")
    assert emails[-1]["type"] == "sav"


def test_roles_equipe(ia_factice):
    assert "incorrect" in dire("/connexion mauvais", "staff")[0]
    assert "admin" in dire("/connexion a123", "staff")[0]
    assert "réservations" in dire("/commandes", "staff")[0] or "Aucune" in dire("/commandes", "staff")[0]
    dire("combien on a vendu hier ?", "staff")
    assert "membre de l'équipe (admin)" in ia_factice["appels"][-1]["systeme"]
    dire("/connexion v123", "vendeur1")
    assert "réservée" in dire("/commandes", "vendeur1")[0]
    assert "Stock" in dire("/stock", "vendeur1")[0] or "Aucune" in dire("/stock", "vendeur1")[0]


def test_ia_en_panne_reponse_preparee(monkeypatch):
    import ia
    monkeypatch.setattr(ia, "generer", lambda *a, **k: None)
    r = dire("je cherche un pc portable", "c8")
    assert r[0].endswith("?") and "FCFA" not in r[0]


def test_memoire_30_messages(ia_factice):
    for i in range(20):
        dire(f"message {i}", "c9")
    assert len(ia_factice["appels"][-1]["messages"]) <= 30


def test_negociation_pendant_recapitulatif(ia_factice):
    dire("le dell latitude 5420 pour mes études", "n1")
    dire("je le prends", "n1")
    dire("Laurane", "n1")
    r = dire("676354365533333", "n1")
    assert "pas valide" in r[0]
    dire("677123456", "n1")
    r = dire("je passe en boutique", "n1")
    assert "récapitule" in r[0]
    r = dire("vous pouvez me laisser à 280000 ?", "n1")         # contre-offre (285 000)
    assert db.base().conversation("n1")["panier"].get("offre_en_attente") == 285000
    r = dire("oui", "n1")
    assert "285 000 FCFA" in r[-1] and "au lieu de 295 000 FCFA" in r[-1]
    r = dire("oui", "n1")
    assert "CMD-" in r[0] and "285 000 FCFA" in r[0]
    assert db.base().dernieres_commandes()[0]["lignes"][0]["prix_unitaire"] == 285000


def test_ia_ne_peut_pas_changer_le_prix_negocie(ia_factice):
    ia_factice["reponses"].append("Allez, je vous le fais à 250 000 FCFA !")
    dire("le dell latitude 5420 pour mes études", "n2")
    r = dire("laissez-le moi à 280000 svp", "n2")
    assert "250 000" not in r[0] and "285 000 FCFA" in r[0]


def test_photo_demandee_puis_modele(ia_factice):
    r = dire("vous pouvez m'envoyer une photo ?", "n3")
    assert "quel modèle" in r[0]
    r = dire("le hp probook", "n3")
    assert "photo" in r[0] and "ProBook" in r[0]
