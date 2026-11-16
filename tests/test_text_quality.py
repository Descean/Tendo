"""Tests du controle qualite des alertes (cas reels de nov. 2026)."""

from datetime import datetime, timezone

from app.utils.text_quality import (
    clean_authority,
    deadline_from_text,
    is_generic_title,
    outdated_reason,
)

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def test_authority_menu_noise_rejected():
    assert clean_authority(
        "S D'Adoption En Savoir Plus Offres D'Emploi Cdd Avis D'Appel À Candidature"
    ) is None
    assert clean_authority(
        ", Invite Les Cabinets Intéressés À Manifester Leur Intérêt Pour Les Prestations"
    ) is None
    assert clean_authority("contact@mairie.bj") is None


def test_authority_valid_kept():
    assert clean_authority("ARMP Bénin") == "ARMP Bénin"
    assert clean_authority("CNHU-HKM") == "CNHU-HKM"
    assert clean_authority("Mairie De La Commune D'Abomey-Calavi") == "Mairie de la Commune d'Abomey-Calavi"


def test_generic_titles():
    assert is_generic_title("Marchés publics")
    assert is_generic_title("Appels d'offres")
    assert not is_generic_title("Constitution d'un répertoire de prestataires audiovisuels (SOPA)")


def test_deadline_in_text_past():
    txt = "Clôture : 18 juil 2024 à 22 h00 (heure locale)."
    assert deadline_from_text(txt).year == 2024
    assert outdated_reason(deadline=None, published_date=None, texts=[txt], now=NOW)


def test_deadline_in_text_future():
    txt = "Dépôt des offres au plus tard le 30/11/2026 à 10h00"
    assert outdated_reason(deadline=None, published_date=None, texts=[txt], now=NOW) is None


def test_old_year_only():
    assert outdated_reason(deadline=None, published_date=None, texts=["Plan de passation gestion 2025"], now=NOW)
    assert outdated_reason(deadline=None, published_date=None, texts=["Plan de passation 2026"], now=NOW) is None


def test_explicit_deadline_field():
    past = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert outdated_reason(deadline=past, published_date=None, texts=[], now=NOW)
