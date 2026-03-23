"""Tendo – Point d'entrée principal FastAPI."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.utils.logger import logger
from app.utils.db import async_engine
from app.models.base import Base
from app.scheduler import setup_scheduler, shutdown_scheduler

# Import des routers
from app.routers import webhook, users, subscriptions, publications, payments, admin


# ── Rate Limiting ──
limiter = Limiter(key_func=get_remote_address)


# ── Lifecycle ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup et shutdown de l'application."""
    logger.info(f"Démarrage de {settings.app_name} ({settings.app_env})")

    # Créer les tables si elles n'existent pas
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Base de données initialisée")

    # Démarrer le scheduler de tâches planifiées
    setup_scheduler()

    yield

    # Cleanup
    shutdown_scheduler()
    await async_engine.dispose()
    logger.info(f"{settings.app_name} arrêté")


# ── App ──
app = FastAPI(
    title="Tendo API",
    description=(
        "Assistant IA intelligent de veille sur les marchés publics via WhatsApp. "
        "API backend pour le Bénin et l'Afrique de l'Ouest."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS – ouvert en dev, restreint en production
if settings.app_env == "production":
    allowed_origins = [
        "https://shiftup.bj",
        "https://www.shiftup.bj",
        "https://tendo.shiftup.bj",
        settings.base_url,
    ]
else:
    allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Static files ──
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── Routers ──
app.include_router(webhook.router)
app.include_router(users.router)
app.include_router(subscriptions.router)
app.include_router(publications.router)
app.include_router(payments.router)
app.include_router(admin.router)


# ── Routes de base ──
@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "version": "1.0.0",
        "description": "Tendo — Assistant IA Marchés Publics via WhatsApp",
        "docs": "/docs",
    }


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    """Page de politique de confidentialité (requise par Meta)."""
    privacy_file = Path(__file__).parent.parent / "static" / "privacy.html"
    return HTMLResponse(content=privacy_file.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    """Health check détaillé pour le monitoring."""
    from app.utils.db import AsyncSessionLocal
    from sqlalchemy import text

    db_ok = False
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    from app.scheduler import scheduler
    scheduler_ok = scheduler.running

    status = "healthy" if (db_ok and scheduler_ok) else "degraded"
    return {
        "status": status,
        "env": settings.app_env,
        "services": {
            "database": "ok" if db_ok else "error",
            "scheduler": "running" if scheduler_ok else "stopped",
        },
    }


# ── Error handler global ──
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erreur non gérée: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
    )
