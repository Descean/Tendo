# Guide de Developpement - Tendo by SHIFT UP

> Assistant IA pour les marches publics au Benin via WhatsApp
> Derniere mise a jour : 31 mars 2026

---

## 1. Architecture Generale

```
Client (WhatsApp)  <-->  Meta Cloud API  <-->  FastAPI Backend  <-->  PostgreSQL
                                                    |
Client (Web)  <-->  Nginx + SSL  <-->  Landing Page (static/index.html)
                                            |
                                    /api/v1/register  -->  FedaPay (paiement)
                                                              |
                                                      callback --> WhatsApp
```

### Stack Technique
- **Backend** : Python 3.11, FastAPI, SQLAlchemy 2.0 (async), asyncpg
- **Base de donnees** : PostgreSQL 15 (Docker)
- **Deploiement** : Docker Compose sur VPS Contabo (207.180.221.113)
- **Reverse proxy** : Nginx + Let's Encrypt SSL
- **Domaine** : tendo.shiftup.bj
- **WhatsApp** : Meta Cloud API (gratuit, 1000 conv/mois)
- **Paiement** : FedaPay (Mobile Money MTN/Moov, mode LIVE)
- **IA** : Groq (Llama 3.3 70B) > Gemini Flash > Claude (cascade)
- **Scraping** : 18 sources d'appels d'offres
- **PDF** : pypdf 4.3.1 (extraction texte), Tesseract OCR (fallback), Gemini Vision (fallback)

### Acces VPS
```bash
ssh root@207.180.221.113
# mot de passe : (voir gestionnaire de mots de passe)
```

### Structure du projet
```
/opt/tendo/                              (sur le VPS)
  ├── app/
  │   ├── main.py                        # Point d'entree FastAPI
  │   ├── config.py                      # Settings (pydantic-settings, lit .env)
  │   ├── admin_panel.py                 # sqladmin (CRUD admin basique)
  │   ├── scheduler.py                   # APScheduler (9 jobs planifies)
  │   │
  │   ├── models/
  │   │   ├── __init__.py
  │   │   ├── base.py                    # Base declarative SQLAlchemy
  │   │   ├── user.py                    # User (phone, email, subscription_status, etc.)
  │   │   ├── publication.py             # Publication (AO, AMI, etc.) + champs PDF
  │   │   ├── subscription.py            # Subscription (plan, start/end_date, payment_id)
  │   │   ├── notification.py            # Notification (tracking envois)
  │   │   ├── email_tracking.py          # Suivi des demandes email
  │   │   ├── document_analysis.py       # Analyse de documents PDF
  │   │   └── knowledge_base.py          # Base de connaissances IA
  │   │
  │   ├── routers/
  │   │   ├── webhook.py                 # Webhook WhatsApp (Meta Cloud API)
  │   │   ├── publications.py            # API publications (sources masquees)
  │   │   ├── payments.py                # Webhooks FedaPay + callback
  │   │   ├── admin.py                   # Dashboard admin complet (SPA HTML)
  │   │   ├── users.py                   # API utilisateurs
  │   │   └── subscriptions.py           # API abonnements
  │   │
  │   ├── services/
  │   │   ├── claude.py                  # IA conversationnelle (cascade Groq > Gemini > Claude)
  │   │   ├── whatsapp.py                # Envoi messages WhatsApp (Meta Cloud API)
  │   │   ├── payment.py                 # FedaPay integration
  │   │   ├── document_analyzer.py       # Extraction PDF (pypdf > OCR > Gemini Vision)
  │   │   ├── document_classifier.py     # Classification lexicale + IA (11 types, 10 secteurs)
  │   │   ├── jnmp_analyzer.py           # Segmentation journaux JNMP en AO individuels
  │   │   ├── email_manager.py           # Envoi email (demande de dossier)
  │   │   ├── notifications.py           # Envoi notifications WhatsApp
  │   │   ├── monitoring.py              # Monitoring systeme
  │   │   ├── knowledge_service.py       # Base de connaissances IA
  │   │   └── scraping/                  # 18 scrapers (voir section 3.2)
  │   │       ├── base.py                # BaseScraper (classe mere)
  │   │       ├── generic.py             # GenericScraper
  │   │       ├── armp.py                # ARMP Benin
  │   │       ├── marches_publics_bj.py  # marches-publics.bj
  │   │       ├── gouv_bj.py             # gouv.bj
  │   │       ├── adpme.py               # ADPME
  │   │       ├── abe.py                 # ABE
  │   │       ├── jnmp.py                # JNMP (journaux PDF)
  │   │       ├── bad.py                 # Banque Africaine de Dev
  │   │       ├── afd.py                 # Agence Francaise de Dev
  │   │       ├── banque_mondiale.py     # Banque Mondiale
  │   │       ├── ungm.py                # UN Global Marketplace
  │   │       ├── unfpa.py               # UNFPA
  │   │       ├── tns.py                 # TNS
  │   │       ├── uemoa.py               # UEMOA
  │   │       ├── j360.py                # Journal 360
  │   │       ├── care_benin.py          # CARE Benin
  │   │       └── ong_benin.py           # ASIN, GIZ, ENABEL
  │   │
  │   ├── schemas/                       # Schemas Pydantic (validation API)
  │   │   ├── publication.py
  │   │   ├── user.py
  │   │   ├── subscription.py
  │   │   ├── payment.py
  │   │   └── notification.py
  │   │
  │   ├── utils/
  │   │   ├── db.py                      # AsyncSessionLocal, get_db
  │   │   ├── logger.py                  # Logger configure
  │   │   ├── security.py               # Fonctions de securite
  │   │   └── redis_client.py            # Client Redis (optionnel)
  │   │
  │   └── workers/                       # Celery (non utilise, remplace par APScheduler)
  │       ├── celery_app.py
  │       └── tasks.py
  │
  ├── alembic/                           # Migrations DB
  ├── static/
  │   ├── index.html                     # Landing page
  │   └── images/
  ├── .env                               # Variables d'environnement
  ├── docker-compose.yml
  ├── Dockerfile
  └── requirements.txt
```

---

## 2. Credentials et Configuration

### WhatsApp Business API
| Cle | Valeur |
|-----|--------|
| Numero Tendo | +229 41 88 99 21 |
| ID App | 769282702624304 |
| ID Numero Telephone | 1004130242791000 |
| ID Compte Business | 1668093884540939 |
| App Secret | (voir .env sur VPS) |
| Verify Token | (voir .env sur VPS) |

### FedaPay (LIVE)
| Cle | Valeur |
|-----|--------|
| Secret Key | (voir .env sur VPS) |
| Public Key | (voir .env sur VPS) |
| Webhook Secret | (voir .env sur VPS) |

### Tarifs
| Plan | Prix | Duree |
|------|------|-------|
| Decouverte | Gratuit | 7 jours |
| Essentiel | 2 990 FCFA/mois | 30 jours |
| Premium | 9 990 FCFA/mois | 30 jours |

### IA (cascade par cout)
1. **Groq** (gratuit) : Llama 3.3 70B - 30 req/min
2. **Gemini** (gratuit) : Flash - 15 req/min
3. **Claude** (payant) : Pour premium uniquement
4. **Fallback local** : Reponse generique si tout echoue

### Admin Dashboard
- **URL** : https://tendo.shiftup.bj/admin/?key=Tendo@ShiftUp2024!
- **Auth** : Parametre `?key=` avec la valeur de `settings.secret_key`

---

## 3. Ce qui est fait (COMPLETE)

### 3.1 Backend Core
- [x] FastAPI avec async + SQLAlchemy 2.0
- [x] Modeles : User, Publication, Subscription, Notification, DocumentAnalysis, KnowledgeBase, EmailTracking
- [x] Webhook WhatsApp (Meta Cloud API) avec verification signature
- [x] Service IA conversationnelle avec cascade Groq > Gemini > Claude
- [x] Detection d'intent (MENU, INSCRIPTION, QUESTION, ABONNEMENT, etc.)
- [x] Historique de conversation par utilisateur
- [x] Admin panel sqladmin avec CRUD complet
- [x] Docker Compose (app + PostgreSQL)
- [x] Nginx + SSL (Let's Encrypt)

### 3.2 Scraping et Publications (18 sources)
Sources actives dans `ALL_SCRAPERS` :

| Source | Scraper | Frequence |
|--------|---------|-----------|
| marches-publics.bj | MarchesPublicsBJScraper | Quotidien |
| ARMP | ARMPScraper | Quotidien |
| gouv.bj | GouvBJScraper | Quotidien |
| ADPME | ADPMEScraper | Quotidien |
| ABE | ABEScraper | Quotidien |
| JNMP | JNMPScraper | Quotidien |
| BAD | BADScraper | Quotidien/Hebdo |
| AFD | AFDScraper | Quotidien/Hebdo |
| Banque Mondiale | BanqueMondialesScraper | Quotidien/Hebdo |
| UNGM | UNGMScraper | Quotidien/Hebdo |
| UNFPA | UNFPAScraper | Quotidien/Hebdo |
| TNS | TNSScraper | Quotidien/Hebdo |
| UEMOA | UEMOAScraper | 2-3x/semaine |
| J360 | J360Scraper | 2-3x/semaine |
| CARE Benin | CAREBeninScraper | Hebdo |
| ASIN | ASINScraper (ong_benin.py) | Hebdo |
| GIZ | GIZBeninScraper (ong_benin.py) | Hebdo |
| ENABEL | ENABELBeninScraper (ong_benin.py) | Hebdo |

### 3.3 PDF Pipeline
- [x] Extraction texte : pypdf 4.3.1 > Tesseract OCR > Gemini Vision (cascade)
- [x] Resume technique IA (5-8 points cles)
- [x] Extraction structuree : pieces requises, criteres de qualification
- [x] Stockage en base : pdf_content, technical_summary, required_documents, qualification_criteria
- [x] Traitement par batch (scheduler a 6h30 et 18h30 UTC)

### 3.4 Segmentation JNMP (NOUVEAU - 31 mars 2026)
- [x] Module `app/services/jnmp_analyzer.py`
- [x] Decoupe les journaux JNMP PDF (recueils de 10-20+ documents) en AO individuels
- [x] Detection de 25+ types de rubriques (AAO, PV_OUVERTURE, PV_ATTRIBUTION, ADDITIF, AMI, etc.)
- [x] Extraction metadata par regex : autorite, reference, objet, montant, deadline, financement
- [x] Creation automatique de publications individuelles dans la base
- [x] Scheduler : 6h30 et 18h30 UTC

### 3.5 WhatsApp Bot
- [x] Accueil nouveau contact avec envoi des AO actifs
- [x] Commande DETAIL pour analyse complete d'une publication
- [x] Commande /demander_dossier pour demande par email
- [x] Notifications 2x/jour
- [x] Inscription supprimee du bot -> redirige vers le site web
- [x] Menu personnalise selon le tier (trial/essentiel/premium)
- [x] Recherche d'AO par mots-cles (commande Rechercher)
- [x] IA contextuelle : reconnait le plan de l'utilisateur dans le prompt
- [x] Fonctions Premium visibles uniquement pour les abonnes Premium

### 3.6 Landing Page (tendo.shiftup.bj)
- [x] Design responsive (Inter font, theme violet/sombre)
- [x] Font Awesome (remplace tous les emojis)
- [x] Images de marque (banner + logo)
- [x] Sections : Probleme/Solution, Services, Processus, Tarifs, Inscription, A propos
- [x] Formulaire d'inscription securise (honeypot, device fingerprint, anti-injection)
- [x] Email obligatoire, selection multiple de secteurs
- [x] Boutons pricing qui pre-selectionnent le plan

### 3.7 Paiement FedaPay
- [x] Integration FedaPay (mode LIVE)
- [x] Transaction avec infos client pre-remplies (nom, email, telephone)
- [x] Lien de paiement genere via API token
- [x] Apres paiement : redirection vers WhatsApp Tendo
- [x] Webhook FedaPay robuste (plusieurs formats de payload)
- [x] Endpoint /payments/verify/{id} pour verification manuelle

### 3.8 Dashboard Admin (REFAIT - 31 mars 2026)
- [x] SPA complete dans `app/routers/admin.py` (Alpine.js + Tailwind CSS + Chart.js)
- [x] Design professionnel : sidebar fixe 240px, theme sombre (#0B1121)
- [x] Font Awesome 6.5.1 pour toutes les icones
- [x] Inter (Google Fonts) pour la typographie
- [x] 5 sections : Tableau de Bord, Finance, Marche, Utilisateurs, Operations
- [x] KPI cards avec gradients et badges icones
- [x] Login par cle d'acces (`?key=Tendo@ShiftUp2024!`)
- [x] API endpoints admin :
  - `GET /admin/api/stats` — Statistiques globales
  - `GET /admin/api/users` — Liste utilisateurs
  - `GET /admin/api/publications` — Liste publications
  - `GET /admin/api/system` — Infos systeme
  - `GET /admin/api/logs` — Logs recents
  - `POST /admin/api/users/{id}/toggle` — Activer/desactiver utilisateur
  - `DELETE /admin/api/publications/{id}` — Supprimer publication
  - `POST /admin/trigger/scraping` — Lancer scraping
  - `POST /admin/trigger/notifications` — Lancer notifications
  - `POST /admin/trigger/pdf-processing` — Lancer pipeline PDF
  - `POST /admin/trigger/jnmp` — Lancer segmentation JNMP

### 3.9 API Publications
- [x] Sources masquees des reponses client
- [x] Endpoint admin avec sources visibles
- [x] Recherche avancee : texte, type, secteur, region, budget, deadline
- [x] Stats sans exposer les sources

### 3.10 Pages legales
- [x] `/privacy` - Politique de confidentialite
- [x] `/terms` - Conditions d'utilisation
- [x] `/delete-data` - Formulaire de suppression de donnees

---

## 4. Scheduler (taches planifiees)

9 jobs configures dans `app/scheduler.py` :

| Job | Cron (UTC) | Cotonou | Description |
|-----|------------|---------|-------------|
| `scraping` | Configurable (.env) / defaut `*/6h` | — | Scraping des 18 sources |
| `notifications` | `*/2h :05` | — | Envoi notifications WhatsApp |
| `check_subscriptions` | `00:00` | 01:00 | Verification abonnements expires |
| `expiration_reminders` | `09:00` | 10:00 | Rappels 3j avant expiration |
| `daily_report` | `20:00` | 21:00 | Rapport quotidien admin |
| `email_check` | `10:00, 16:00` | 11:00, 17:00 | Scan inbox email (reponses DAO) |
| `jnmp_processing` | `06:30, 18:30` | 07:30, 19:30 | Segmentation journaux JNMP |
| `seed_knowledge` | Au demarrage | — | Peuplement base de connaissances (1 fois) |

---

## 5. Methodologie de deploiement

### 5.1 Deploiement depuis Windows (methode actuelle)

On deploie fichier par fichier via SSH/SFTP avec paramiko, puis `docker cp` dans le container :

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('207.180.221.113', username='root', password='VOTRE_MOT_DE_PASSE')

# 1. Upload via SFTP vers /tmp
sftp = ssh.open_sftp()
sftp.put('app/routers/admin.py', '/tmp/admin.py')
sftp.close()

# 2. Copier dans le container
ssh.exec_command('docker cp /tmp/admin.py tendo-api:/app/app/routers/admin.py')

# 3. Commit + restart
ssh.exec_command('docker commit tendo-api tendo-api-img')
ssh.exec_command('cd /opt/tendo && docker compose restart api')
# OU docker restart tendo-api

ssh.close()
```

### 5.2 Commandes utiles sur le VPS

```bash
# Logs en temps reel
cd /opt/tendo && docker compose logs -f --tail=50

# Rebuild complet
cd /opt/tendo && docker compose up -d --build --force-recreate

# Sante API
curl http://localhost:8000/health

# PostgreSQL
docker exec -it tendo-db psql -U tendo -d tendo

# Scraping manuel
curl -X POST "http://localhost:8000/admin/trigger/scraping?key=Tendo@ShiftUp2024!"

# Pipeline PDF manuel
curl -X POST "http://localhost:8000/admin/trigger/pdf-processing?key=Tendo@ShiftUp2024!"

# Segmentation JNMP manuelle
curl -X POST "http://localhost:8000/admin/trigger/jnmp?key=Tendo@ShiftUp2024!"

# Publications en base
docker exec -it tendo-db psql -U tendo -d tendo -c \
  "SELECT id, reference, title, source FROM publications ORDER BY id DESC LIMIT 10;"

# Utilisateurs
docker exec -it tendo-db psql -U tendo -d tendo -c \
  "SELECT id, name, phone_number, subscription_status, subscription_plan FROM users ORDER BY id DESC;"
```

---

## 6. Modeles de donnees importants

### Subscription
Colonnes : `id, user_id, plan, start_date, end_date, payment_id, amount, status`
- **PAS de** `created_at` (utiliser `start_date`)
- **PAS de** `transaction_id` (utiliser `payment_id`)

### Publication
Champs PDF ajoutes : `pdf_url, pdf_content, technical_summary, required_documents, qualification_criteria, document_type, sectors, regions`

---

## 7. Flux utilisateur complet

### 7.1 Inscription via le site
```
1. Utilisateur arrive sur tendo.shiftup.bj
2. Remplit le formulaire : nom, tel, email, entreprise, secteurs, region, plan
3. POST /api/v1/register
   - Cree l'utilisateur en base (trial 7 jours)
   - Si plan payant : genere lien FedaPay avec infos pre-remplies
4. Redirect vers FedaPay (nom, email, telephone pre-remplis)
5. Utilisateur paie par Mobile Money
6. FedaPay redirige vers WhatsApp Tendo
7. Webhook FedaPay active l'abonnement + envoie message WhatsApp de confirmation
```

### 7.2 Utilisation quotidienne WhatsApp
```
1. Tendo envoie les nouveaux AO chaque matin et apres-midi
2. DETAIL AO-XXXX : analyse complete
3. /demander_dossier : recevoir un DAO par email
4. MENU : voir les options
5. Rechercher [mot-cle] : trouver des AO
6. Questions libres : IA (Groq/Gemini/Claude)
```

---

## 8. Variables d'environnement (.env)

```env
# Database
DATABASE_URL=postgresql+asyncpg://tendo:tendo2024@db:5432/tendo
DATABASE_URL_SYNC=postgresql://tendo:tendo2024@db:5432/tendo

# WhatsApp
WHATSAPP_PROVIDER=meta
META_PHONE_NUMBER_ID=1004130242791000
META_ACCESS_TOKEN=...  (voir .env sur VPS)
META_VERIFY_TOKEN=...  (voir .env sur VPS)
META_APP_SECRET=...  (voir .env sur VPS)
META_BUSINESS_ACCOUNT_ID=1668093884540939

# IA — voir .env sur VPS
GROQ_API_KEY=...
GEMINI_API_KEY=...
CLAUDE_API_KEY=...

# FedaPay (LIVE) — voir .env sur VPS
FEDAPAY_SECRET_KEY=...
FEDAPAY_PUBLIC_KEY=...
FEDAPAY_WEBHOOK_SECRET=...

# Email — voir .env sur VPS
SMTP_USER=...
SMTP_PASSWORD=...

# App
APP_ENV=production
BASE_URL=https://tendo.shiftup.bj
SECRET_KEY=...  (voir .env sur VPS)
```

---

## 9. Erreurs connues et solutions

| Erreur | Cause | Solution |
|--------|-------|----------|
| `Subscription.created_at` AttributeError | Subscription n'a pas `created_at` | Utiliser `Subscription.start_date` |
| `Subscription.transaction_id` AttributeError | Subscription n'a pas `transaction_id` | Utiliser `Subscription.payment_id` |
| `ImportError: setup_scheduler` | main.py attend setup_scheduler | Verifier que scheduler.py exporte setup_scheduler/shutdown_scheduler |
| `429 Rate Limit (Groq/Gemini)` | Trop de requetes IA | Augmenter les delais (5s entre pubs, 3s entre appels IA) |
| `proxies error (anthropic)` | anthropic < 0.40.0 | Mettre `anthropic>=0.40.0` dans requirements.txt |
| `UnicodeEncodeError (Windows)` | Console Windows cp1252 | Utiliser `errors='replace'` dans decode() |
| Formulaire inscription bloque | Anti-bot timing check casse | Fix: extraire timestamp sans les 8 chars random |
| Dashboard admin ecrase | Deploiement partiel ecrase le SPA complet | Toujours deployer le fichier admin.py complet |

---

## 10. Historique des versions

### V1 (mars 2026) - MVP
- Backend FastAPI, 9 scrapers, webhook WhatsApp, landing page, inscription FedaPay

### V2 (27 mars 2026) - Corrections
- Fix formulaire inscription, fix callback URL, fix paiement

### V3 (28 mars 2026) - Enrichissement
- Sources masquees, email DAO, images marque, multi-secteurs

### V4 (30 mars 2026) - Mise a jour majeure
- Webhook FedaPay robuste, menu enrichi, IA tier-aware, IMAP monitoring
- CI/CD basique, admin panel sqladmin, scrapers BAD/AFD
- Pipeline PDF + classification documents

### V5 (31 mars 2026) - Dashboard + JNMP
- Dashboard admin professionnel (SPA avec sidebar, Font Awesome, Chart.js)
- 18 scrapers (ajout: Banque Mondiale, UNGM, UNFPA, TNS, UEMOA, CARE, ASIN, GIZ, ENABEL)
- Module segmentation JNMP (decoupe journaux PDF en AO individuels)
- Nettoyage fichiers temporaires (archives dans `_archive/`)

---

## 11. Ce qui reste a faire (TODO)

### Priorite 1 : Dashboard utilisateur
Interface web ou l'utilisateur voit ses alertes, son historique, son abonnement.

### Priorite 2 : Renouvellement automatique
Generer un lien de paiement de renouvellement dans le rappel d'expiration.

### Priorite 3 : J360 PDF paywall
Gerer l'acces aux PDF proteges de journal360.bj.

### Comment reprendre avec Claude
Fournir ce guide en contexte et dire :
> "Je travaille sur Tendo. Voici le guide de dev. La prochaine tache est [X]. Le projet est deploye sur un VPS Contabo accessible en SSH. On deploie avec des scripts Python Paramiko depuis Windows."
