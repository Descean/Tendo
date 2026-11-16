"""Tests : tri par profil, récapitulatif du matin, modèle hors fenêtre de 24 h."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import notifications as n
from app.services.matching import correspond_profil

NOW = datetime.now(timezone.utc)


def pub(i, title, deadline_days=None, **kw):
    d = dict(id=i, title=title, summary="", technical_summary=None, pdf_content=None, authority_name="Mairie de Parakou",
             document_type="AAO", deadline=NOW + timedelta(days=deadline_days) if deadline_days is not None else None,
             published_date=None, pdf_url="https://x/a.pdf", lots_detail=None, budget=None, delivery_delay=None,
             financing_source=None, source="gouv.bj", reference=f"r{i}", sectors=[], regions=[], country="Bénin")
    d.update(kw)
    return SimpleNamespace(**d)


def user(**kw):
    d = dict(id=1, phone_number="+22900000000", subscription_status="trial", subscription_plan=None,
             sectors=[], regions=[], preferred_sources=[], name="Test", company=None)
    d.update(kw)
    return SimpleNamespace(**d)


def test_profil_secteur():
    u = user(sectors=["BTP"])
    assert correspond_profil(u, pub(1, "Travaux de construction d'un marché couvert"))
    assert not correspond_profil(u, pub(2, "Acquisition de matériel informatique et logiciel", sectors=["TIC"]))


def test_profil_region():
    u = user(regions=["Cotonou"])
    assert correspond_profil(u, pub(1, "Travaux à Cotonou"))
    assert not correspond_profil(u, pub(2, "Réhabilitation d'écoles à Natitingou"))
    assert correspond_profil(u, pub(3, "Fourniture de mobilier"))  # lieu non précisé : envoyé


def test_ligne_recap_limites():
    ligne = n._ligne_recap(pub(7, "Construction d'un centre de santé avec clôture et forage", 3))
    assert ligne["id"] == "voir_7"
    assert len(ligne["title"]) <= 24 and len(ligne["description"]) <= 72
    assert ligne["description"].startswith("J-")


class FauxWhatsApp:
    def __init__(self, fenetre_fermee=False):
        self.appels, self.fermee = [], fenetre_fermee

    async def send_interactive_buttons(self, *a, **k):
        if self.fermee:
            raise Exception("Meta API error: {'code': 131047, 'message': 'Re-engagement message'}")
        self.appels.append(("boutons", a, k))

    async def send_interactive_list(self, *a, **k):
        if self.fermee:
            raise Exception("Meta API error: {'code': 131047}")
        self.appels.append(("liste", a, k))

    async def send_template_message(self, *a, **k):
        self.appels.append(("modele", a, k))


def _run(fw, pubs):
    ancien = n.whatsapp
    n.whatsapp = fw
    try:
        return asyncio.run(n._envoyer_a_utilisateur(user(), pubs))
    finally:
        n.whatsapp = ancien


def test_un_avis_boutons():
    fw = FauxWhatsApp()
    _run(fw, [pub(1, "Construction d'un dalot à Parakou", 5)])
    assert fw.appels[0][0] == "boutons"
    titres = [b["title"] for b in fw.appels[0][1][2]]
    assert "Confier à SHIFT UP" in titres and all(len(t) <= 20 for t in titres)


def test_plusieurs_avis_liste():
    fw = FauxWhatsApp()
    envoyes = _run(fw, [pub(1, "Construction d'un dalot", 5), pub(2, "Fourniture de tables-bancs", 2)])
    assert fw.appels[0][0] == "liste" and len(envoyes) == 2


def test_hors_fenetre_modele():
    fw = FauxWhatsApp(fenetre_fermee=True)
    _run(fw, [pub(1, "Construction d'un dalot", 5), pub(2, "Fourniture de tables-bancs", 2)])
    genre, a, k = fw.appels[0]
    assert genre == "modele" and a[1] == "tendo_alerte_avis"
    params = k["components"][0]["parameters"]
    assert params[0]["text"] == "2"
