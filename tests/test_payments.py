"""Tests du système de paiement FedaPay."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from app.services.payment import PLANS, create_payment_link, verify_transaction
from app.models.user import User, SubscriptionStatus
from app.models.subscription import Subscription, PaymentStatus


class TestFedaPayPlans:
    """Tests des plans de paiement."""

    def test_plans_exist(self):
        assert "essentiel" in PLANS
        assert "premium" in PLANS

    def test_essentiel_plan_details(self):
        plan = PLANS["essentiel"]
        assert plan["amount"] == 5000
        assert plan["currency"] == "XOF"
        assert plan["duration_days"] == 30
        assert "name" in plan
        assert "description" in plan

    def test_premium_plan_details(self):
        plan = PLANS["premium"]
        assert plan["amount"] == 15000
        assert plan["currency"] == "XOF"
        assert plan["duration_days"] == 30

    def test_premium_costs_more(self):
        assert PLANS["premium"]["amount"] > PLANS["essentiel"]["amount"]


class TestCreatePaymentLink:
    """Tests de création de lien de paiement."""

    @pytest.mark.asyncio
    async def test_invalid_plan_raises(self):
        with pytest.raises(ValueError, match="Plan inconnu"):
            await create_payment_link("+22961000001", "inexistant")

    @pytest.mark.asyncio
    async def test_create_link_success(self):
        """Test avec mock de l'API FedaPay."""
        mock_response_create = MagicMock()
        mock_response_create.status_code = 201
        mock_response_create.json.return_value = {
            "v1/transaction": {"id": 12345}
        }

        mock_response_token = MagicMock()
        mock_response_token.status_code = 200
        mock_response_token.json.return_value = {"token": "test_token_abc"}

        with patch("app.services.payment.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            # Premier appel: créer la transaction, deuxième: obtenir le token
            mock_client.post = AsyncMock(
                side_effect=[mock_response_create, mock_response_token]
            )
            mock_client_cls.return_value = mock_client

            result = await create_payment_link(
                user_phone="+22961000001",
                plan="essentiel",
                user_name="Jean Test",
            )

            assert "payment_link" in result
            assert "process.fedapay.com" in result["payment_link"]
            assert result["amount"] == 5000
            assert result["currency"] == "XOF"
            assert result["tx_ref"].startswith("tendo-essentiel-")


class TestFedaPayWebhook:
    """Tests du webhook FedaPay."""

    @pytest.mark.asyncio
    async def test_webhook_approved(self, client, db_session):
        """Test du webhook avec transaction approuvée."""
        # Créer un utilisateur de test
        user = User(
            phone_number="+22961000001",
            name="Test User",
            subscription_status=SubscriptionStatus.TRIAL.value,
        )
        db_session.add(user)
        await db_session.commit()

        webhook_data = {
            "name": "transaction.approved",
            "entity": {
                "id": 12345,
                "amount": 5000,
                "status": "approved",
                "metadata": {
                    "phone_number": "+22961000001",
                    "plan": "essentiel",
                    "tx_ref": "tendo-essentiel-+22961000001-abc12345",
                },
            },
        }

        with patch("app.routers.payments.verify_webhook_signature", return_value=True):
            with patch("app.routers.payments.send_message", new_callable=AsyncMock):
                response = await client.post(
                    "/payments/webhook/fedapay",
                    json=webhook_data,
                )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_webhook_declined(self, client):
        """Test du webhook avec transaction refusée."""
        webhook_data = {
            "name": "transaction.declined",
            "entity": {
                "id": 99999,
                "amount": 5000,
                "status": "declined",
                "metadata": {},
            },
        }

        with patch("app.routers.payments.verify_webhook_signature", return_value=True):
            response = await client.post(
                "/payments/webhook/fedapay",
                json=webhook_data,
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_callback_approved(self, client, db_session):
        """Test du callback après paiement."""
        # Réponse mock pour verify_transaction
        with patch("app.routers.payments.verify_transaction", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = {
                "transaction_id": "12345",
                "tx_ref": "tendo-essentiel-+22961000001-abc",
                "status": "approved",
                "amount": 5000,
                "currency": "XOF",
                "metadata": {
                    "phone_number": "+22961000001",
                    "plan": "essentiel",
                    "tx_ref": "tendo-essentiel-+22961000001-abc",
                },
            }

            with patch("app.routers.payments.send_message", new_callable=AsyncMock):
                response = await client.get(
                    "/payments/callback?id=12345&status=approved"
                )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_payment_initiate_requires_auth(self, client):
        """Test que l'initiation de paiement requiert l'authentification."""
        response = await client.post(
            "/payments/initiate",
            json={"plan": "essentiel"},
        )
        assert response.status_code in (401, 403)


class TestSubscriptionActivation:
    """Tests de l'activation d'abonnement après paiement."""

    @pytest.mark.asyncio
    async def test_subscription_created_after_payment(self, db_session):
        """Vérifie qu'un abonnement est créé après paiement."""
        from sqlalchemy import select

        user = User(
            phone_number="+22961555555",
            name="Paiement Test",
            subscription_status=SubscriptionStatus.TRIAL.value,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Simuler l'activation
        sub = Subscription(
            user_id=user.id,
            plan="premium",
            start_date=datetime.now(timezone.utc),
            end_date=datetime.now(timezone.utc) + timedelta(days=30),
            payment_id="fedapay_12345",
            amount=15000.0,
            status=PaymentStatus.PAID.value,
        )
        db_session.add(sub)
        user.subscription_status = SubscriptionStatus.ACTIVE.value
        await db_session.commit()

        # Vérifier
        result = await db_session.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        subs = result.scalars().all()
        assert len(subs) == 1
        assert subs[0].plan == "premium"
        assert subs[0].amount == 15000.0

        await db_session.refresh(user)
        assert user.subscription_status == SubscriptionStatus.ACTIVE.value
