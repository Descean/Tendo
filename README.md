# ChatUp - Assistant IA Marchés Publics via WhatsApp

Backend Python FastAPI pour un assistant IA expert en marchés publics, accessible via WhatsApp. Le service cible le Bénin et l'Afrique de l'Ouest.

## Architecture

```
chatup-backend/
├── app/
│   ├── main.py                    # FastAPI app, routers
│   ├── config.py                  # Configuration (pydantic-settings)
│   ├── models/                    # SQLAlchemy models
│   │   ├── user.py                # Utilisateurs WhatsApp
│   │   ├── subscription.py        # Abonnements
│   │   ├── publication.py         # Appels d'offres
│   │   ├── notification.py        # Alertes envoyées
│   │   └── email_tracking.py      # Suivi demandes dossiers
│   ├── schemas/                   # Pydantic validation
│   ├── routers/                   # Endpoints API
│   │   ├── webhook.py             # WhatsApp webhook (Meta Cloud API / Twilio)
│   │   ├── users.py               # API utilisateurs (web)
│   │   ├── subscriptions.py       # Gestion abonnements
│   │   ├── publications.py        # Recherche AO
│   │   └── payments.py            # Paiements FedaPay (Mobile Money)
│   ├── services/                  # Logique métier
│   │   ├── whatsapp.py            # Envoi messages (Meta Cloud API / Twilio)
│   │   ├── claude.py              # IA Claude (Anthropic)
│   │   ├── payment.py             # FedaPay (Mobile Money MTN/Moov)
│   │   ├── email_manager.py       # SMTP/IMAP
│   │   ├── notifications.py       # Matching & alertes
│   │   └── scraping/              # Scrapers par source
│   ├── utils/                     # db, redis, logger, security
│   └── workers/                   # Celery tasks
├── functions/                     # Azure Functions (scraping)
├── requirements.txt
├── Dockerfile
└── .env.example
```

## Installation

### Prérequis

- Python 3.10+
- SQLite (dev local, inclus) ou PostgreSQL 14+ (production)
- Redis 7+ (optionnel pour le dev local)

### Setup local

```bash
# 1. Cloner et créer l'environnement
cd ChatUp_app
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos clés API

# 4. Lancer le serveur (SQLite par défaut, pas besoin de créer de DB)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Le serveur démarre sur `http://localhost:8000`. Documentation API sur `/docs`.

### Lancer les workers Celery (optionnel)

```bash
# Worker
celery -A app.workers.celery_app worker --loglevel=info

# Beat (tâches planifiées)
celery -A app.workers.celery_app beat --loglevel=info
```

## Configuration WhatsApp

### Meta Cloud API (GRATUIT — recommandé)
1. Créer une app sur `developers.facebook.com`
2. Ajouter le produit "WhatsApp"
3. Obtenir le Phone Number ID et Access Token
4. Renseigner `META_*` dans `.env`
5. 1000 conversations/mois gratuites

### Twilio (payant — optionnel)
1. Créer un compte Twilio et activer WhatsApp Sandbox
2. Configurer le webhook URL : `https://votre-domaine.com/webhook/whatsapp`
3. Renseigner `TWILIO_*` dans `.env` et mettre `WHATSAPP_PROVIDER=twilio`

## Configuration FedaPay (Paiements)

1. Créer un compte sur `fedapay.com`
2. Utiliser le mode **sandbox** pour les tests (clés `sk_sandbox_...`)
3. Renseigner `FEDAPAY_SECRET_KEY` et `FEDAPAY_PUBLIC_KEY` dans `.env`
4. Supporte Mobile Money MTN et Moov (Bénin, Togo, Côte d'Ivoire)

## Déploiement Azure

### App Service (Backend API)

```bash
# Build et push Docker
docker build -t chatup-api .
docker tag chatup-api votre-registry.azurecr.io/chatup-api:latest
docker push votre-registry.azurecr.io/chatup-api:latest

# Déployer sur Azure App Service
az webapp create --resource-group chatup-rg \
  --plan chatup-plan \
  --name chatup-api \
  --deployment-container-image-name votre-registry.azurecr.io/chatup-api:latest
```

### Azure Functions (Scraping)

```bash
cd functions
func azure functionapp publish chatup-scraping --python
```

## Services externes

| Service | Usage | Variable d'env | Coût |
|---------|-------|---------------|------|
| Meta Cloud API | WhatsApp (GRATUIT) | `META_PHONE_NUMBER_ID`, `META_ACCESS_TOKEN` | Gratuit (1000 conv/mois) |
| Anthropic | Claude AI (conversations) | `CLAUDE_API_KEY` | Pay-per-use |
| FedaPay | Paiements Mobile Money MTN/Moov | `FEDAPAY_SECRET_KEY` | 1.5% par transaction |
| SQLite/PostgreSQL | Base de données | `DATABASE_URL` | Gratuit (SQLite) |
| Redis | Cache & file de tâches | `REDIS_URL` | Optionnel en dev |

## Sources de scraping

| Source | URL | Fréquence |
|--------|-----|-----------|
| marches-publics.bj | https://www.marches-publics.bj | Quotidien 6h00 |
| ARMP | https://armp.bj | Quotidien 6h15 |
| gouv.bj | https://www.gouv.bj/opportunites/ | Quotidien 6h30 |
| ADPME | https://epme.adpme.bj | Quotidien 6h45 |
| ABE | https://www.abe.bj | Quotidien 7h00 |

Pour ajouter une source : copier `functions/scrape_generic/` et configurer le `GenericScraper`.

## API Endpoints

### Webhook
- `POST /webhook/whatsapp` – Réception messages WhatsApp (Meta / Twilio)
- `GET /webhook/whatsapp` – Vérification webhook Meta

### Utilisateurs
- `POST /users/auth/token` – Génération JWT
- `GET /users/me` – Profil utilisateur
- `PUT /users/me` – Mise à jour profil
- `DELETE /users/me` – Suppression compte (RGPD)

### Publications
- `GET /publications/search` – Recherche avancée
- `GET /publications/{id}` – Détails publication
- `GET /publications/sources/list` – Sources disponibles

### Abonnements
- `GET /subscriptions/plans` – Plans disponibles
- `GET /subscriptions/me` – Mes abonnements

### Paiements
- `POST /payments/initiate` – Créer lien de paiement FedaPay
- `POST /payments/webhook/fedapay` – Webhook FedaPay
- `GET /payments/callback` – Callback retour paiement

## Licence

Propriétaire - SHIFT UP
