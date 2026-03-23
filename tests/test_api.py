"""Tests des endpoints API."""

import pytest


@pytest.mark.asyncio
async def test_root(client):
    """Test de la route racine."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "Tendo"
    assert "version" in data


@pytest.mark.asyncio
async def test_health(client):
    """Test du healthcheck."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert "services" in data


@pytest.mark.asyncio
async def test_publications_search(client, sample_publication):
    """Test de recherche de publications."""
    response = await client.get("/publications/search?q=routes")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_publications_stats(client, sample_publication):
    """Test des statistiques de publications."""
    response = await client.get("/publications/stats/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data


@pytest.mark.asyncio
async def test_subscriptions_plans(client):
    """Test de la liste des plans."""
    response = await client.get("/subscriptions/plans")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "essentiel" in data
    assert "premium" in data


@pytest.mark.asyncio
async def test_webhook_verify(client):
    """Test de la vérification du webhook."""
    response = await client.get("/webhook/whatsapp")
    assert response.status_code in (200, 403)
