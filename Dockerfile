# ── Build stage ──
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt requirements.prod.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.prod.txt

# ── Runtime stage ──
FROM python:3.11-slim

WORKDIR /app

# Dépendances système pour PostgreSQL (production)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Copier les paquets Python depuis le builder
COPY --from=builder /install /usr/local

# Copier le code applicatif
COPY . .

# Créer un utilisateur non-root
RUN useradd --create-home tendo && chown -R tendo:tendo /app
USER tendo

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/health'); assert r.status_code == 200"

# Démarrage
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--forwarded-allow-ips", "*", "--proxy-headers"]
