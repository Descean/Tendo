"""Client Redis – initialisation lazy pour eviter les crashs si Redis est indisponible."""

from typing import Optional

import redis.asyncio as aioredis

from app.config import settings
from app.utils.logger import logger

# Client initialise au premier appel, pas a l'import
_redis_async: Optional[aioredis.Redis] = None


async def get_redis() -> Optional[aioredis.Redis]:
    """Retourne le client Redis async (lazy init).

    Retourne None si Redis est indisponible, pour ne pas bloquer l'application.
    """
    global _redis_async

    if _redis_async is None:
        try:
            _redis_async = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # Test de connexion
            await _redis_async.ping()
            logger.info("[Redis] Connexion etablie")
        except Exception as e:
            logger.warning(f"[Redis] Indisponible ({e}), cache desactive")
            _redis_async = None
            return None

    return _redis_async


async def close_redis():
    """Ferme proprement la connexion Redis."""
    global _redis_async
    if _redis_async is not None:
        try:
            await _redis_async.close()
        except Exception:
            pass
        _redis_async = None
