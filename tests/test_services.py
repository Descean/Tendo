"""Tests des services (sans appels API externes)."""

import pytest

from app.services.claude import _simple_intent_detection
from app.services.notifications import matches_user_preferences
from app.services.payment import PLANS
from app.models.user import User
from app.models.publication import Publication


class TestIntentDetection:
    """Tests de la détection d'intention par mots-clés."""

    def test_menu(self):
        assert _simple_intent_detection("menu") == "MENU"
        assert _simple_intent_detection("aide") == "MENU"
        assert _simple_intent_detection("help") == "MENU"

    def test_inscription(self):
        assert _simple_intent_detection("inscription") == "INSCRIPTION"
        assert _simple_intent_detection("inscrire") == "INSCRIPTION"

    def test_abonnement(self):
        assert _simple_intent_detection("abonnement") == "ABONNEMENT"
        assert _simple_intent_detection("plans") == "ABONNEMENT"
        assert _simple_intent_detection("tarifs") == "ABONNEMENT"

    def test_historique(self):
        assert _simple_intent_detection("historique") == "HISTORIQUE"
        assert _simple_intent_detection("alertes") == "HISTORIQUE"

    def test_paiement(self):
        assert _simple_intent_detection("paiement") == "PAIEMENT"
        assert _simple_intent_detection("payer") == "PAIEMENT"

    def test_support(self):
        assert _simple_intent_detection("support") == "SUPPORT"

    def test_demande_dossier(self):
        assert _simple_intent_detection("/demander_dossier AO-123") == "DEMANDE_DOSSIER"

    def test_question_default(self):
        assert _simple_intent_detection("Comment répondre à un appel d'offres ?") == "QUESTION"
        assert _simple_intent_detection("Quelle est la procédure pour soumissionner ?") == "QUESTION"

    def test_greeting_is_menu(self):
        """Les salutations renvoient au menu (UX WhatsApp)."""
        assert _simple_intent_detection("bonjour") == "MENU"
        assert _simple_intent_detection("salut") == "MENU"
        assert _simple_intent_detection("hello") == "MENU"

    def test_numeric_shortcuts(self):
        """Les raccourcis numériques du menu fonctionnent."""
        assert _simple_intent_detection("1") == "INSCRIPTION"
        assert _simple_intent_detection("2") == "ABONNEMENT"
        assert _simple_intent_detection("3") == "HISTORIQUE"
        assert _simple_intent_detection("4") == "PAIEMENT"
        assert _simple_intent_detection("5") == "SUPPORT"


class TestNotificationMatching:
    """Tests du matching des préférences utilisateur."""

    def _make_user(self, sectors=None, regions=None, sources=None):
        """Crée un objet simple qui imite User pour le matching."""
        class FakeUser:
            pass
        user = FakeUser()
        user.sectors = sectors or []
        user.regions = regions or []
        user.preferred_sources = sources or []
        return user

    def _make_pub(self, sectors=None, regions=None, source="gouv.bj"):
        """Crée un objet simple qui imite Publication pour le matching."""
        class FakePub:
            pass
        pub = FakePub()
        pub.sectors = sectors or []
        pub.regions = regions or []
        pub.source = source
        return pub

    def test_no_preferences_matches_all(self):
        user = self._make_user()
        pub = self._make_pub(sectors=["BTP"], regions=["Cotonou"])
        assert matches_user_preferences(user, pub) is True

    def test_sector_match(self):
        user = self._make_user(sectors=["BTP", "TIC"])
        pub = self._make_pub(sectors=["BTP"])
        assert matches_user_preferences(user, pub) is True

    def test_sector_no_match(self):
        user = self._make_user(sectors=["Santé"])
        pub = self._make_pub(sectors=["BTP"])
        assert matches_user_preferences(user, pub) is False

    def test_region_match(self):
        user = self._make_user(regions=["Cotonou"])
        pub = self._make_pub(regions=["Cotonou", "Parakou"])
        assert matches_user_preferences(user, pub) is True

    def test_source_match(self):
        user = self._make_user(sources=["gouv.bj"])
        pub = self._make_pub(source="gouv.bj")
        assert matches_user_preferences(user, pub) is True

    def test_source_no_match(self):
        user = self._make_user(sources=["ARMP"])
        pub = self._make_pub(source="gouv.bj")
        assert matches_user_preferences(user, pub) is False


class TestPaymentPlans:
    """Tests des plans de paiement."""

    def test_plans_exist(self):
        assert "essentiel" in PLANS
        assert "premium" in PLANS

    def test_essentiel_plan(self):
        plan = PLANS["essentiel"]
        assert plan["amount"] == 5000
        assert plan["currency"] == "XOF"
        assert plan["duration_days"] == 30

    def test_premium_plan(self):
        plan = PLANS["premium"]
        assert plan["amount"] == 15000
        assert plan["currency"] == "XOF"
