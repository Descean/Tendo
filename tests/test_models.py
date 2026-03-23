"""Tests des modèles de données."""

import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.models.user import User, SubscriptionStatus, SubscriptionPlan
from app.models.subscription import Subscription, PaymentStatus
from app.models.publication import Publication
from app.models.notification import Notification


@pytest.mark.asyncio
async def test_create_user(db_session):
    """Test de création d'un utilisateur."""
    user = User(
        phone_number="+22961999999",
        name="Jean Dupont",
        company="Dupont SARL",
        sectors=["BTP"],
        regions=["Cotonou"],
        subscription_status=SubscriptionStatus.TRIAL.value,
    )
    db_session.add(user)
    await db_session.commit()

    result = await db_session.execute(
        select(User).where(User.phone_number == "+22961999999")
    )
    found = result.scalar_one()
    assert found.name == "Jean Dupont"
    assert found.company == "Dupont SARL"
    assert found.sectors == ["BTP"]
    assert found.subscription_status == "trial"


@pytest.mark.asyncio
async def test_create_publication(db_session):
    """Test de création d'une publication."""
    pub = Publication(
        source="gouv.bj",
        reference="AO-TEST-00000001",
        title="Fourniture de matériel informatique",
        summary="Achat de 100 ordinateurs pour le ministère",
        category="fourniture",
        sectors=["TIC"],
        regions=["Bénin"],
        budget=50000000.0,
    )
    db_session.add(pub)
    await db_session.commit()

    result = await db_session.execute(
        select(Publication).where(Publication.reference == "AO-TEST-00000001")
    )
    found = result.scalar_one()
    assert found.title == "Fourniture de matériel informatique"
    assert found.budget == 50000000.0
    assert found.is_processed is False


@pytest.mark.asyncio
async def test_user_subscription_relationship(db_session, sample_user):
    """Test de la relation user -> subscriptions."""
    sub = Subscription(
        user_id=sample_user.id,
        plan="essentiel",
        end_date=datetime.now(timezone.utc) + timedelta(days=30),
        amount=5000.0,
        status=PaymentStatus.PAID.value,
    )
    db_session.add(sub)
    await db_session.commit()

    # Vérifier que la subscription existe
    result = await db_session.execute(
        select(Subscription).where(Subscription.user_id == sample_user.id)
    )
    subs = result.scalars().all()
    assert len(subs) == 1
    assert subs[0].plan == "essentiel"


@pytest.mark.asyncio
async def test_notification_creation(db_session, sample_user, sample_publication):
    """Test de création d'une notification."""
    notif = Notification(
        user_id=sample_user.id,
        publication_id=sample_publication.id,
    )
    db_session.add(notif)
    await db_session.commit()

    result = await db_session.execute(
        select(Notification).where(Notification.user_id == sample_user.id)
    )
    found = result.scalar_one()
    assert found.publication_id == sample_publication.id
    assert found.opened is False
