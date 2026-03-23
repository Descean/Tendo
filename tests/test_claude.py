"""Tests du service Claude AI (mode fallback)."""

import pytest

from app.services.claude import (
    _simple_intent_detection,
    _fallback_chat,
    chat,
    detect_intent,
    summarize_publication,
)


class TestFallbackChat:
    """Tests du mode dégradé (sans API Claude)."""

    def test_ao_keywords(self):
        reply = _fallback_chat("Comment répondre à un appel d'offres ?")
        assert "Marchés Publics" in reply or "marchés" in reply.lower()

    def test_dossier_keywords(self):
        reply = _fallback_chat("Comment obtenir un dossier ?")
        assert "DAO" in reply or "dossier" in reply.lower()

    def test_generic_reply(self):
        reply = _fallback_chat("bonjour")
        assert "Tendo" in reply
        assert "Menu" in reply


class TestIntentDetectionExtended:
    """Tests étendus de la détection d'intention."""

    def test_case_insensitive(self):
        assert _simple_intent_detection("MENU") == "MENU"
        assert _simple_intent_detection("Menu") == "MENU"
        assert _simple_intent_detection("menu") == "MENU"

    def test_demande_dossier_variations(self):
        assert _simple_intent_detection("/demander_dossier AO-MARC-12345") == "DEMANDE_DOSSIER"
        assert _simple_intent_detection("je voudrais demander le dossier AO-123") == "DEMANDE_DOSSIER"

    def test_unknown_defaults_to_question(self):
        assert _simple_intent_detection("quelle est la capitale du Bénin?") == "QUESTION"

    @pytest.mark.asyncio
    async def test_detect_intent_fallback(self):
        """detect_intent doit fonctionner même sans API Claude."""
        result = await detect_intent("menu")
        assert result["intent"] == "MENU"
        assert result["raw_message"] == "menu"

    @pytest.mark.asyncio
    async def test_chat_fallback(self):
        """chat() doit répondre même sans API Claude."""
        reply = await chat("bonjour")
        assert isinstance(reply, str)
        assert len(reply) > 0

    @pytest.mark.asyncio
    async def test_summarize_fallback(self):
        """summarize_publication doit fonctionner sans API Claude."""
        result = await summarize_publication(
            "Construction de routes",
            "Un appel d'offres pour la construction de routes à Cotonou",
        )
        assert isinstance(result, str)
        assert len(result) > 0
