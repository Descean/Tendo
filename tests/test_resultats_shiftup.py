"""Tests de la reconnaissance des résultats des dossiers SHIFT UP."""

from types import SimpleNamespace

from app.services.resultats_shiftup import (
    _meme_entreprise,
    correspond_au_marche,
    entreprise_designee,
)

SOU = {
    "reference": "F_DAF_122310",
    "autorite": "Agence des Infrastructures du Secteur de l'Énergie (ACISE)",
    "objet": "Acquisition de fournitures de bureau, matériels et outillages",
    "lots": None,
    "entrepriseNom": "ETS BKK ET FILS",
    "entrepriseSigle": "BKK",
}


def pub(texte, title="PV d'attribution provisoire"):
    return SimpleNamespace(title=title, summary=None, technical_summary=None, pdf_content=texte)


def test_reference_match():
    assert correspond_au_marche(SOU, pub("Marché F_DAF_122310 ...")) == 1.0


def test_objet_autorite_match():
    p = pub("ACISE Agence des Infrastructures du Secteur de l'Energie : fournitures de bureau matériels outillages")
    assert correspond_au_marche(SOU, p) >= 0.6


def test_autre_marche():
    assert correspond_au_marche(SOU, pub("Construction d'un marché couvert à Parakou")) < 0.6


def test_entreprise_attributaire():
    t = "Soumissionnaires : SOCIETE X, ETS BKK ET FILS. Attributaire provisoire : ETS BKK ET FILS pour 4 500 000 FCFA"
    assert entreprise_designee(SOU, t)


def test_entreprise_simple_soumissionnaire():
    t = "Liste des soumissionnaires : ETS BKK ET FILS, SOCIETE X ........................................................................................................................................................................................................ Attributaire : SOCIETE X SARL"
    assert not entreprise_designee(SOU, t)


def test_meme_entreprise():
    assert _meme_entreprise(SOU, "Ets BKK & Fils SARL")
    assert _meme_entreprise(SOU, "BKK")
    assert not _meme_entreprise(SOU, "Société X")
