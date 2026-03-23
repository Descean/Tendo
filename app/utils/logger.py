"""Configuration du logging."""

import logging
import sys

from app.config import settings


def setup_logger(name: str = "tendo") -> logging.Logger:
    """Crée et configure un logger."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        level = logging.DEBUG if settings.app_debug else logging.INFO
        logger.setLevel(level)

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = setup_logger()
