"""Script de démarrage rapide pour Tendo.

Usage:
    python run.py              — Démarrer le serveur
    python run.py --test       — Lancer les tests
    python run.py --check      — Vérifier la configuration
    python run.py --migrate    — Appliquer les migrations Alembic
"""

import sys
import os
import subprocess


def check_config():
    """Vérifie que la configuration est correcte."""
    print("\n=== Tendo — Vérification de la configuration ===\n")

    from app.config import settings

    checks = []

    # Database
    db_ok = bool(settings.database_url)
    checks.append(("Database URL", db_ok, settings.database_url[:40] + "..."))

    # WhatsApp
    if settings.whatsapp_provider == "meta":
        meta_ok = bool(settings.meta_phone_number_id and settings.meta_access_token
                       and not settings.meta_phone_number_id.startswith("your_"))
        checks.append(("Meta WhatsApp", meta_ok,
                       f"Phone ID: {settings.meta_phone_number_id[:10]}..." if meta_ok else "NON CONFIGURÉ"))
    else:
        twilio_ok = bool(settings.twilio_account_sid and not settings.twilio_account_sid.startswith("AC" + "x"*10))
        checks.append(("Twilio", twilio_ok,
                       f"SID: {settings.twilio_account_sid[:10]}..." if twilio_ok else "NON CONFIGURÉ"))

    # Claude AI
    claude_ok = bool(settings.claude_api_key and not settings.claude_api_key.startswith("sk-ant-xxx"))
    checks.append(("Claude AI (Anthropic)", claude_ok,
                   "Configuré" if claude_ok else "NON CONFIGURÉ (mode fallback actif)"))

    # FedaPay
    fedapay_ok = bool(settings.fedapay_secret_key and not settings.fedapay_secret_key.startswith("sk_sandbox_xxx"))
    checks.append(("FedaPay", fedapay_ok,
                   f"{'Sandbox' if 'sandbox' in settings.fedapay_secret_key else 'Live'}" if fedapay_ok else "NON CONFIGURÉ"))

    # Email
    email_ok = bool(settings.smtp_user and settings.smtp_password
                    and not settings.smtp_user.startswith("tendo@example"))
    checks.append(("Email SMTP", email_ok,
                   settings.smtp_user if email_ok else "NON CONFIGURÉ"))

    # Affichage
    for name, ok, detail in checks:
        status = "OK" if ok else "!!"
        icon = "+" if ok else "-"
        print(f"  [{icon}] {name}: {detail}")

    ok_count = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"\n  Résultat: {ok_count}/{total} services configurés")

    if ok_count < total:
        print("\n  Pour configurer les services manquants, éditez le fichier .env")
        print("  Le serveur fonctionne quand même (avec des fonctionnalités limitées)\n")
    else:
        print("\n  Tout est configuré ! Le système est prêt.\n")

    return ok_count == total


def run_server():
    """Démarre le serveur FastAPI."""
    print("\n=== Tendo — Démarrage du serveur ===\n")
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--reload",
        "--host", "0.0.0.0",
        "--port", "8000",
    ])


def run_tests():
    """Lance la suite de tests."""
    print("\n=== Tendo — Tests ===\n")
    result = subprocess.run([
        sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"
    ])
    sys.exit(result.returncode)


def run_migrate():
    """Applique les migrations Alembic."""
    print("\n=== Tendo — Migrations ===\n")
    subprocess.run([
        sys.executable, "-m", "alembic", "upgrade", "head"
    ])


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--test" in args:
        run_tests()
    elif "--check" in args:
        check_config()
    elif "--migrate" in args:
        run_migrate()
    else:
        check_config()
        run_server()
