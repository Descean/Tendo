"""Tests des scrapers (structure et imports)."""

import pytest
from unittest.mock import patch, MagicMock

from app.services.scraping import ALL_SCRAPERS
from app.services.scraping.base import BaseScraper
from app.services.scraping.generic import GenericScraper


class TestScraperRegistry:
    """Tests du registre des scrapers."""

    def test_all_scrapers_registered(self):
        assert len(ALL_SCRAPERS) == 5

    def test_expected_sources(self):
        expected = {"marches-publics.bj", "ARMP", "gouv.bj", "ADPME", "ABE"}
        assert set(ALL_SCRAPERS.keys()) == expected

    def test_all_inherit_base(self):
        for name, cls in ALL_SCRAPERS.items():
            assert issubclass(cls, BaseScraper), f"{name} n'hérite pas de BaseScraper"

    def test_scrapers_have_name_and_url(self):
        for source_name, cls in ALL_SCRAPERS.items():
            scraper = cls()
            assert scraper.name, f"{source_name} n'a pas de name"
            assert scraper.base_url, f"{source_name} n'a pas de base_url"


class TestBaseScraper:
    """Tests de la classe de base."""

    def test_generate_reference(self):
        scraper = list(ALL_SCRAPERS.values())[0]()
        ref = scraper.generate_reference("GOUV", "Test Title", "https://example.com")
        assert ref.startswith("AO-GOUV-")
        assert len(ref) > 10

    def test_generate_reference_deterministic(self):
        scraper = list(ALL_SCRAPERS.values())[0]()
        ref1 = scraper.generate_reference("TEST", "Title", "url")
        ref2 = scraper.generate_reference("TEST", "Title", "url")
        assert ref1 == ref2

    def test_clean_text(self):
        assert BaseScraper.clean_text("  hello   world  ") == "hello world"
        assert BaseScraper.clean_text(None) == ""
        assert BaseScraper.clean_text("") == ""

    def test_now_utc(self):
        dt = BaseScraper.now_utc()
        assert dt.tzinfo is not None


class TestGenericScraper:
    """Tests du scraper générique."""

    def test_initialization(self):
        scraper = GenericScraper(
            name="test",
            base_url="https://example.com",
            source="test-source",
            list_selector="article",
            title_selector="h2",
            link_selector="a",
        )
        assert scraper.name == "test"
        assert scraper.source == "test-source"


class TestScrapersRunSafely:
    """Tests que chaque scraper gère les erreurs correctement."""

    def test_all_scrapers_handle_network_errors(self):
        """Vérifie que les scrapers retournent [] en cas d'erreur réseau."""
        for source_name, cls in ALL_SCRAPERS.items():
            scraper = cls()
            # Mock la session HTTP pour simuler une erreur
            with patch.object(scraper.session, 'get', side_effect=Exception("Network error")):
                result = scraper.run()
                assert result == [], f"{source_name} ne gère pas l'erreur réseau"
