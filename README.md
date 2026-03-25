# Tendo

**Assistant IA expert en marchés publics via WhatsApp**

Tendo est un service de veille intelligente sur les marchés publics au Bénin et en Afrique de l'Ouest. Il permet aux entreprises de recevoir des alertes personnalisées, de consulter des appels d'offres et d'obtenir des conseils — le tout via WhatsApp.

Développé par **SHIFT UP** — Cotonou, Bénin.

---

## Fonctionnalités

### Pour les utilisateurs
- **Alertes automatiques** — Recevez les appels d'offres correspondant à vos secteurs et régions
- **Recherche intelligente** — Posez des questions sur les marchés publics en langage naturel
- **Résumés IA** — Chaque publication est résumée de manière claire et concise
- **Demande de dossiers** — Demandez un DAO directement depuis WhatsApp
- **Gestion de profil** — Inscrivez-vous et configurez vos préférences en quelques messages
- **Historique** — Consultez vos dernières alertes à tout moment

### Technique
- **Double IA** — Google Gemini (gratuit, par défaut) + Anthropic Claude (premium, pour abonnés)
- **Détection d'intention locale** — Les commandes menu/inscription/paiement sont traitées sans appel API (gratuit et instantané)
- **Mode commercial** — Pour les nouveaux utilisateurs, le bot adopte un ton accueillant d'agent commercial
- **Mode expert** — Pour les abonnés premium, le bot devient un conseiller spécialisé en marchés publics
- **Scraping planifié** — 5 sources béninoises scrapées automatiquement (marches-publics.bj, ARMP, gouv.bj, ABE, ADPME)
- **Paiement Mobile Money** — Via FedaPay (MTN Mobile Money, Moov Money)
- **WhatsApp gratuit** — Meta Cloud API (1000 conversations/mois gratuites)

---

## Architecture

```
tendo-backend/
├── app/
│   ├── main.py                 # Point d'entrée FastAPI + lifespan
│   ├── config.py               # Configuration (pydantic-settings)
│   ├── scheduler.py            # Tâches planifiées (APScheduler)
│   ├── models/                 # Modèles SQLAlchemy
│   │   ├── user.py             # Utilisateurs + abonnements
│   │   ├── subscription.py     # Historique des paiements
│   │   ├── publication.py      # Appels d'offres scrapés
│   │   ├── notification.py     # Alertes envoyées
│   │   └── email_tracking.py   # Suivi des demandes de dossiers
│   ├── routers/                # Endpoints API
│   │   ├── webhook.py          # Webhook WhatsApp (Meta + Twilio)
│   │   ├── payments.py         # Webhook + callback FedaPay
│   │   ├── admin.py            # Administration (stats, triggers)
│   │   ├── users.py            # CRUD utilisateurs
│   │   ├── publications.py     # CRUD publications
│   │   └── subscriptions.py    # Gestion abonnements
│   ├── services/               # Logique métier
│   │   ├── claude.py           # IA conversationnelle (Gemini + Claude)
│   │   ├── whatsapp.py         # Envoi de messages WhatsApp
│   │   ├── payment.py          # Intégration FedaPay
│   │   ├── notifications.py    # Matching + envoi d'alertes
│   │   ├── email_manager.py    # Emails SMTP/IMAP
│   │   └── scraping/           # Scrapers par source
│   │       ├── base.py         # Classe de base + registre
│   │       ├── generic.py      # Scraper générique
│   │       ├── marches_publics_bj.py
│   │       ├── armp.py
│   │       ├── gouv_bj.py
│   │       ├── abe.py
│   │       └── adpme.py
│   ├── schemas/                # Schémas Pydantic (validation)
│   └── utils/                  # Utilitaires (DB, logger, sécurité)
├── static/
│   └── privacy.html            # Politique de confidentialité
├── deploy/                     # Scripts de déploiement VPS
├── tests/                      # 57 tests unitaires
├── alembic/                    # Migrations de base de données
├── docker-compose.yml          # Déploiement Docker (API + PostgreSQL)
├── Dockerfile                  # Image Docker multi-stage
└── requirements.txt            # Dépendances Python
```

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| **Framework** | Python 3.11 + FastAPI (async) |
| **Base de données** | SQLite (dev) / PostgreSQL (prod) via SQLAlchemy 2.0 async |
| **Migrations** | Alembic |
| **IA conversationnelle** | Google Gemini Flash (gratuit) + Anthropic Claude (premium) |
| **WhatsApp** | Meta Cloud API (gratuit) ou Twilio (payant) |
| **Paiement** | FedaPay (Mobile Money MTN/Moov, XOF) |
| **Scraping** | BeautifulSoup4 + requests |
| **Planification** | APScheduler (scraping 6h, alertes 2h) |
| **Authentification** | JWT (python-jose) |
| **Email** | aiosmtplib + aioimaplib |
| **Conteneurisation** | Docker multi-stage + docker-compose |

---

## Plans tarifaires

| Plan | Prix | Fonctionnalités |
|------|------|-----------------|
| **Essai gratuit** | 0 FCFA (7 jours) | Menu, inscription, alertes basiques, IA Gemini |
| **Essentiel** | 5 000 FCFA/mois | Alertes personnalisées, historique, IA Gemini, support |
| **Premium** | 15 000 FCFA/mois | Tout Essentiel + IA Claude expert, demande de dossiers, email monitoring |

---

## Installation

### Prérequis
- Python 3.11+
- Un compte Meta Developer (pour WhatsApp)
- Un compte FedaPay (pour les paiements)
- Une clé Gemini API (gratuite sur https://aistudio.google.com/apikey)

### Installation locale

```bash
# 1. Cloner le projet
git clone git@github.com:Descean/Tendo.git
cd Tendo

# 2. Créer l'environnement virtuel
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer l'environnement
cp .env.example .env
# Éditer .env avec vos clés API

# 5. Vérifier la configuration et lancer
python run.py
```

Le serveur démarre sur `http://localhost:8000`.

### Vérification

```bash
python run.py --check    # Vérifier la configuration
python run.py --test     # Lancer les tests (57 tests)
python run.py --migrate  # Appliquer les migrations
```

---

## Configuration (.env)

Copier `.env.example` vers `.env` et renseigner :

| Variable | Description | Obligatoire |
|----------|-------------|-------------|
| `META_PHONE_NUMBER_ID` | ID du numéro WhatsApp Business | Oui |
| `META_ACCESS_TOKEN` | Token d'accès Meta | Oui |
| `META_VERIFY_TOKEN` | Token de vérification webhook | Oui |
| `META_APP_SECRET` | Secret de l'app Facebook (hex, dans Settings > Basic) | Oui |
| `GEMINI_API_KEY` | Clé API Google Gemini (gratuite) | Oui |
| `CLAUDE_API_KEY` | Clé API Anthropic Claude | Non (premium) |
| `FEDAPAY_SECRET_KEY` | Clé secrète FedaPay | Oui |
| `FEDAPAY_PUBLIC_KEY` | Clé publique FedaPay | Oui |
| `SECRET_KEY` | Clé secrète JWT (générer avec `python -c "import secrets; print(secrets.token_urlsafe(48))"`) | Oui |
| `SMTP_USER` | Email pour l'envoi SMTP | Non |
| `SMTP_PASSWORD` | Mot de passe d'application Gmail | Non |

---

## Endpoints API

### Webhooks
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/webhook/whatsapp` | Vérification webhook Meta |
| `POST` | `/webhook/whatsapp` | Réception des messages WhatsApp |
| `POST` | `/payments/webhook/fedapay` | Webhook paiement FedaPay |
| `GET` | `/payments/callback` | Callback après paiement |

### API publique
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/health` | État de santé du serveur |
| `GET` | `/privacy` | Politique de confidentialité |
| `GET` | `/` | Page d'accueil |

### Administration (JWT requis)
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/admin/stats` | Statistiques globales |
| `GET` | `/admin/users` | Liste des utilisateurs |
| `POST` | `/admin/trigger/scraping` | Lancer le scraping manuellement |
| `POST` | `/admin/trigger/notifications` | Envoyer les notifications |
| `GET` | `/admin/scheduler` | État du scheduler |

---

## Déploiement

### VPS (recommandé)

```bash
# Sur le serveur (Ubuntu 22.04+)
scp -r . user@your-server:/opt/tendo/
ssh user@your-server
cd /opt/tendo
bash deploy/setup_vps.sh
```

### Docker

```bash
# Démarrer l'API + PostgreSQL
docker-compose up -d

# Vérifier
curl http://localhost:8000/health
```

### DNS

Configurer un enregistrement DNS **A** pointant `tendo.shiftup.bj` vers l'IP du VPS.

---

## Tests

```bash
# Lancer tous les tests
python -m pytest tests/ -v

# Résultat attendu : 57 passed
```

Les tests couvrent :
- Webhook WhatsApp (Meta + Twilio)
- Paiements FedaPay
- Détection d'intention IA
- Scrapers
- Authentification
- Modèles et schémas

---

## Flux utilisateur WhatsApp

```
Utilisateur                          Tendo
    |                                  |
    |-- "Bonjour" ------------------>  |
    |                                  |-- Créer compte (trial 7j)
    |  <-- Message de bienvenue ----   |
    |                                  |
    |-- "Menu" --------------------->  |
    |  <-- 1.Rechercher 2.S'inscrire   |
    |       3.Historique 4.Abonnement  |
    |       5.Support                  |
    |                                  |
    |-- "2" (S'inscrire) ---------->   |
    |  <-- "Quel est votre nom ?"      |
    |-- "Jean Dupont" ------------->   |
    |  <-- "Votre entreprise ?"        |
    |-- "BTP Bénin SARL" --------->   |
    |  <-- "Vos secteurs ?" (liste)    |
    |-- "1,3" (BTP, Services) ----->   |
    |  <-- "Vos régions ?"            |
    |-- "1,10" (Cotonou, Tout) ---->   |
    |  <-- "Inscription terminée !"    |
    |                                  |
    |-- "4" (Abonnement) ---------->   |
    |  <-- Plans + lien FedaPay        |
    |                                  |
    |-- [Paie via Mobile Money] ---->  FedaPay
    |                                  |
    |  <-- "Abonnement activé !"       |
```

---

## Licence

Projet propriétaire — SHIFT UP, Cotonou, Bénin.

---

## Contact

- **Email** : contact@shiftup.bj
- **WhatsApp** : Envoyez un message au numéro Tendo
- **Site** : https://shiftup.bj
