"""Fixtures partagées pour les tests."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models import User, Subscription, Publication, Notification, EmailTracking
from app.utils.db import get_db
from app.main import app

# Base de données SQLite en mémoire pour les tests
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture
async def db_session():
    """Crée les tables et fournit une session DB de test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db_session):
    """Client HTTP de test avec la DB mockée."""

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sample_user(db_session):
    """Crée un utilisateur de test."""
    user = User(
        phone_number="+22961000001",
        name="Test User",
        company="Test Corp",
        sectors=["BTP", "Services"],
        regions=["Cotonou"],
        preferred_sources=["gouv.bj"],
        subscription_status="trial",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_publication(db_session):
    """Crée une publication de test."""
    pub = Publication(
        source="gouv.bj",
        reference="AO-GOUV-12345678",
        title="Construction de routes à Cotonou",
        summary="Appel d'offres pour la construction de 10km de routes",
        category="marché",
        sectors=["BTP"],
        regions=["Cotonou"],
        authority_email="test@gouv.bj",
        authority_name="Ministère des Travaux Publics",
    )
    db_session.add(pub)
    await db_session.commit()
    await db_session.refresh(pub)
    return pub
