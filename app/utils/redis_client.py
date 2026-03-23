"""Client Redis pour cache et file d'attente."""

import redis.asyncio as aioredis
import redis as sync_redis

from app.config import settings

# Client async (pour FastAPI)
redis_async = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)

# Client sync (pour Celery)
redis_sync = sync_redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis():
    """Dépendance FastAPI pour obtenir le client Redis."""
    return redis_async
