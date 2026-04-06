# Tendo

**Assistant IA expert en marches publics via WhatsApp — Benin & Afrique de l'Ouest**

Tendo est un service de veille intelligente sur les marches publics. Il scrape 18 sources, analyse les documents par IA, et distribue des alertes personnalisees aux entreprises via WhatsApp.

Developpe par **SHIFT UP** — Cotonou, Benin.

---

## Fonctionnalites

### Pour les utilisateurs
- **Alertes WhatsApp automatiques** — Appels d'offres correspondant a vos secteurs et regions, distribues en continu de 9h a 18h GMT
- **Messages structures** — Boutons interactifs WhatsApp (Voir le dossier, En savoir plus, Demander dossier)
- **IA conversationnelle** — Posez des questions sur les marches publics en langage naturel
- **Resumes IA** — Chaque publication est analysee et resumee par DeepSeek/Groq
- **Demande de dossiers** — Demandez un DAO directement depuis WhatsApp (premium)
- **Discussions proactives** — Tendo initie des conversations sur les opportunites de marches
- **Gestion de profil** — Inscription et preferences en quelques messages

### Pour l'administrateur
- **Dashboard strategique** — 7 onglets : Tableau de Bord, Finance, Marche, Utilisateurs, Operations, Connaissances, Centre de Code
- **KPIs temps reel** — MRR, ARR, ARPU, taux de conversion, funnel utilisateurs
- **9 declencheurs manuels** — Scraping, Notifications, JNMP, Pipeline PDF, Nettoyage, Enrichissement IA, Discussion proactive, Seed Knowledge, Auto-apprentissage
- **Base de connaissances** — CRUD complet pour enrichir l'IA avec des connaissances metier
- **Centre de Code IA** — Editeur de code integre avec generation par langage naturel (Groq/Gemini/DeepSeek)
- **Auto-apprentissage** — Tendo analyse automatiquement les PV d'attribution pour construire de l'intelligence marche
- **Rapport quotidien** — Envoye a 19h GMT par WhatsApp a l'administrateur

---

## Architecture

```
tendo/
├── app/
│   ├── main.py                  # Point d'entree FastAPI + lifespan
│   ├── config.py                # Configuration (pydantic-settings)
│   ├── scheduler.py             # 12 taches planifiees (APScheduler)
│   ├── models/
│   │   ├── user.py              # Utilisateurs + abonnements
│   │   ├── subscription.py      # Historique des paiements
│   │   ├── publication.py       # Appels d'offres scrapes
│   │   ├── notification.py      # Alertes envoyees (per-user tracking)
│   │   ├── knowledge_base.py    # Base de connaissances metier
│   │   ├── document_analysis.py # Analyse de documents
│   │   └── email_tracking.py    # Suivi des demandes de dossiers
│   ├── routers/
│   │   ├── webhook.py           # Webhook WhatsApp (Meta Cloud API)
│   │   ├── payments.py          # Webhook + callback FedaPay
│   │   ├── admin.py             # Dashboard admin SPA + APIs CRUD + Centre de Code
│   │   ├── users.py             # CRUD utilisateurs
│   │   ├── publications.py      # CRUD publications
│   │   └── subscriptions.py     # Gestion abonnements
│   ├── services/
│   │   ├── claude.py            # IA conversationnelle (cascade 4 LLMs)
│   │   ├── whatsapp.py          # Meta Cloud API (messages + boutons interactifs)
│   │   ├── payment.py           # Integration FedaPay (Mobile Money)
│   │   ├── notifications.py     # Matching preferences + envoi alertes
│   │   ├── knowledge_service.py # Recherche + enrichissement base de connaissances
│   │   ├── deepseek_reader.py   # Lecture et analyse PDF par DeepSeek
│   │   ├── jnmp_analyzer.py     # Segmentation journaux JNMP en documents
│   │   ├── monitoring.py        # Alertes admin + rapports
│   │   ├── email_manager.py     # Emails SMTP/IMAP
│   │   └── scraping/            # 18 scrapers
│   │       ├── base.py          # Classe de base + registre ALL_SCRAPERS
│   │       ├── armp.py          # ARMP Benin (decisions, recours)
│   │       ├── jnmp.py          # Journal National des Marches Publics
│   │       ├── marches_publics_bj.py  # Portail national
│   │       ├── gouv_bj.py       # gouv.bj/opportunites
│   │       ├── abe.py           # Agence Beninoise de l'Environnement
│   │       ├── adpme.py         # Agence pour les PME
│   │       ├── bad.py           # Banque Africaine de Developpement
│   │       └── ...              # + 10 autres scrapers
│   ├── schemas/                 # Schemas Pydantic (validation)
│   └── utils/                   # Utilitaires (DB, logger, securite)
├── alembic/                     # Migrations de base de donnees
├── presentation_tendo.html      # Page de presentation investisseurs
├── docker-compose.yml           # Deploiement Docker (API + PostgreSQL)
├── Dockerfile                   # Image Docker multi-stage
└── requirements.txt             # Dependances Python
```

---

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| **Framework** | Python 3.11 + FastAPI (async) |
| **Base de donnees** | PostgreSQL 16 via SQLAlchemy 2.0 async |
| **Migrations** | Alembic |
| **IA conversationnelle** | Groq (Llama 3.3 70B) > Gemini Flash > DeepSeek > Claude (cascade) |
| **Analyse de documents** | DeepSeek Reader (extraction 16 champs structures depuis PDF) |
| **WhatsApp** | Meta Cloud API v21 (gratuit, 1000 conv/mois) |
| **Paiement** | FedaPay (Mobile Money MTN/Moov, XOF) |
| **Scraping** | BeautifulSoup4 + requests (18 scrapers) |
| **Planification** | APScheduler (12 jobs : scraping, notifications, enrichissement, rapport...) |
| **Dashboard admin** | SPA Alpine.js + Tailwind CSS + Chart.js |
| **Centre de Code** | Editeur integre + generation IA (Groq/Gemini/DeepSeek) |
| **Base de connaissances** | SQLAlchemy model avec auto-apprentissage |
| **Conteneurisation** | Docker multi-stage + docker-compose |
| **Deploiement** | VPS Contabo via SFTP + docker compose build |

---

## Pipeline de donnees

```
Scraping (6h GMT)
    │
    ├── 18 scrapers → nouvelles publications en base
    │
    ├── Segmentation JNMP → journaux PDF decomposes en documents individuels
    │
    ├── Enrichissement DeepSeek → analyse PDF, extraction 16 champs structures
    │   (titre, autorite, budget, deadline, garantie, financement, secteurs...)
    │
    └── Phase 2 : enrichissement des publications texte (sans PDF)

Notifications (9h-18h GMT, toutes les 30 min)
    │
    ├── Matching per-user (secteurs, regions, sources)
    ├── Max 2 notifications par utilisateur par cycle
    ├── Messages WhatsApp structures avec boutons interactifs
    └── Tracking via table notifications (user_id + publication_id)

Auto-apprentissage (dimanche 3h)
    │
    ├── Analyse PV d'attribution → fourchettes de prix par secteur
    ├── Statistiques sources actives (30 jours)
    ├── Repartition types de documents
    └── Top autorites contractantes

Rapport quotidien (19h GMT) → WhatsApp admin
```

---

## Dashboard administrateur

Accessible via : `https://[serveur]:8000/admin/?key=[SECRET_KEY]`

### 7 onglets

| Onglet | Fonctionnalites |
|--------|-----------------|
| **Tableau de Bord** | KPIs, graphiques inscriptions/sources, alertes systeme, funnel conversion |
| **Finance** | MRR, ARR, ARPU, revenus, historique paiements, projections |
| **Marche** | Publications par source/type, couverture sectorielle, gaps offre/demande |
| **Utilisateurs** | Liste, filtres, toggle actif/inactif, profil, expiration trial |
| **Operations** | Scheduler, 9 declencheurs manuels, config systeme, logs temps reel |
| **Connaissances** | CRUD base de connaissances, peuplement initial, auto-apprentissage |
| **Centre de Code** | Editeur de fichiers, chat IA, generation de code, terminal shell |

### Centre de Code IA

Le Centre de Code permet de modifier le projet directement depuis le navigateur :
- **Navigateur de fichiers** — Arborescence complete du projet
- **Editeur de code** — Edition avec numeros de ligne, detection de langage
- **Chat IA** — Decrivez en francais, l'IA genere du code adapte au projet
- **Terminal shell** — Execution de commandes (avec protection anti-destructive)
- **Sauvegarde** — Backup automatique (.bak) avant chaque modification
- **Moteur IA** : Groq (Llama 3.3 70B) > Gemini Flash > DeepSeek (cascade gratuite)

---

## Plans tarifaires

| Plan | Prix | Fonctionnalites |
|------|------|-----------------|
| **Essai gratuit** | 0 FCFA (7 jours) | Alertes basiques, IA Groq/Gemini, menu WhatsApp |
| **Essentiel** | 5 000 FCFA/mois | Alertes personnalisees, historique, IA enrichie, support |
| **Premium** | 15 000 FCFA/mois | Tout Essentiel + IA Claude expert, demande de dossiers, email monitoring |

---

## Installation

### Prerequis
- Python 3.11+
- Docker et Docker Compose
- Un compte Meta Developer (WhatsApp Business)
- Un compte FedaPay (paiements Mobile Money)
- Cles API gratuites : Groq, Gemini, DeepSeek (optionnel : Claude)

### Installation locale

```bash
# 1. Cloner le projet
git clone git@github.com:Descean/Tendo.git
cd Tendo

# 2. Creer l'environnement virtuel
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows

# 3. Installer les dependances
pip install -r requirements.txt

# 4. Configurer l'environnement
cp .env.example .env
# Editer .env avec vos cles API

# 5. Lancer
python run.py
```

### Docker (production)

```bash
# Demarrer API + PostgreSQL
docker compose up -d --build

# Verifier
curl http://localhost:8000/health
```

---

## Configuration (.env)

| Variable | Description | Obligatoire |
|----------|-------------|-------------|
| `META_PHONE_NUMBER_ID` | ID du numero WhatsApp Business | Oui |
| `META_ACCESS_TOKEN` | Token d'acces Meta | Oui |
| `META_VERIFY_TOKEN` | Token de verification webhook | Oui |
| `META_APP_SECRET` | Secret de l'app Facebook | Oui |
| `GROQ_API_KEY` | Cle API Groq (gratuit, 30 req/min) | Oui |
| `GEMINI_API_KEY` | Cle API Google Gemini (gratuit) | Recommande |
| `DEEPSEEK_API_KEY` | Cle API DeepSeek (lecture PDF) | Recommande |
| `CLAUDE_API_KEY` | Cle API Anthropic Claude (premium) | Non |
| `FEDAPAY_SECRET_KEY` | Cle secrete FedaPay | Oui |
| `FEDAPAY_PUBLIC_KEY` | Cle publique FedaPay | Oui |
| `SECRET_KEY` | Cle admin dashboard + JWT | Oui |
| `DATABASE_URL` | URL PostgreSQL (auto en Docker) | Non |
| `SMTP_USER` / `SMTP_PASSWORD` | Email SMTP (demande de dossiers) | Non |

---

## Deploiement VPS

Deploiement automatise via SFTP + Docker :

```bash
# Depuis la machine locale (via paramiko/SFTP)
# 1. Upload des fichiers modifies vers /opt/tendo/
# 2. docker compose up -d --build
# 3. Verification : docker logs tendo-api --tail 20
```

Le serveur tourne sur un VPS Contabo avec :
- Docker Compose (tendo-api + tendo-db)
- PostgreSQL 16 (volume persistant)
- Uvicorn (port 8000)

---

## Scheduler (12 taches)

| Job | Horaire | Description |
|-----|---------|-------------|
| Scraping | 6h GMT | 18 scrapers marches publics |
| Notifications | 9h-18h /30min | Distribution alertes WhatsApp |
| Verification abonnements | 0h | Expiration trial/abonnements |
| Rappels expiration | 9h | Rappel J-3, J-1, J0 |
| Enrichissement IA | 7h, 15h, 23h | DeepSeek lecture PDF + enrichissement texte |
| Nettoyage AO expires | 1h | Suppression publications perimees |
| Discussions proactives | Mar+Ven 10h | Tendo engage les utilisateurs |
| Rapport quotidien | 19h GMT | Rapport WhatsApp a l'admin |
| Verification email | 10h, 16h | Inbox demandes de dossiers |
| Segmentation JNMP | 6h30, 18h30 | Decoupe journaux en documents |
| Auto-apprentissage | Dim 3h | Analyse PV, intelligence marche |
| Seed Knowledge | Demarrage | Peuplement initial base de connaissances |

---

## Historique des versions

| Version | Description |
|---------|-------------|
| **V7** (avril 2026) | Dashboard admin 7 onglets, Centre de Code IA, base de connaissances CRUD, auto-apprentissage, 9 triggers manuels |
| **V6** (mars 2026) | Messages structures WhatsApp, DeepSeek reader, auto-expiration AO, pipeline chaine scraping>JNMP>enrichissement |
| **V5** (mars 2026) | Dashboard admin pro, 18 scrapers, JNMP analyzer, cleanup, discussions proactives |
| **V4** | Notifications per-user, scheduler single worker, JNMP filter |
| **V3** | Messages structures, DeepSeek, expiration auto |
| **V2** | CI/CD, backup, monitoring, admin panel, BAD/AFD scrapers |
| **V1** | MVP : WhatsApp bot, 5 scrapers, Gemini, FedaPay |

---

## Licence

Projet proprietaire — SHIFT UP, Cotonou, Benin.

---

## Contact

- **Email** : contact@shiftup.bj
- **WhatsApp** : +229 01 40 80 91 08
- **Site** : https://shiftup.bj
