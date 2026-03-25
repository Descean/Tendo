"""Configuration centralisée via pydantic-settings."""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database (SQLite par défaut pour le dev local, PostgreSQL en production)
    database_url: str = "sqlite+aiosqlite:///./tendo.db"
    database_url_sync: str = "sqlite:///./tendo.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # ── WhatsApp Provider ──
    # "meta" = Meta Cloud API (GRATUIT, 1000 conv/mois)
    # "twilio" = Twilio (payant)
    whatsapp_provider: str = "meta"

    # Meta WhatsApp Cloud API (GRATUIT)
    meta_phone_number_id: str = ""
    meta_access_token: str = ""
    meta_verify_token: str = "tendo_verify_token_2024"
    meta_app_secret: str = ""
    meta_business_account_id: str = ""
    meta_wehook_url: str = ""  # URL ngrok pour le dev local

    # Twilio (optionnel, payant)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "whatsapp:+14155238886"

    # IA Conversationnelle (cascade : Groq > Gemini > Claude > fallback)
    groq_api_key: str = ""  # Groq (gratuit: 30 req/min, Llama 3.3 70B)
    claude_api_key: str = ""
    gemini_api_key: str = ""  # Google Gemini (gratuit: 15 req/min, 1M tokens/jour)

    # FedaPay (Mobile Money MTN/Moov – Bénin)
    fedapay_secret_key: str = ""
    fedapay_public_key: str = ""
    fedapay_webhook_secret: str = ""
    fedapay_app_id: str = ""
    fedapay_app_secret_key: str = ""
    fedapay_app_url: str = ""

    # Email
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993

    # Security
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # Scraping
    scraping_schedule: str = "0 6 * * *"

    # Admin
    admin_emails: List[str] = Field(default_factory=lambda: ["admin@shiftup.bj"])

    # App
    app_name: str = "Tendo"
    app_env: str = "development"
    app_debug: bool = True
    base_url: str = "https://tendo.shiftup.bj"


settings = Settings()
