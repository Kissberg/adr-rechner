# ADR 1000-Punkte-Rechner — Dockerfile
#
# HINWEIS FÜR DEN PRODUKTIVBETRIEB:
# Das Basis-Image sollte zusätzlich per Digest gepinnt werden, z. B.
#   FROM python:3.11-slim@sha256:<digest>
# Den Digest ermitteln mit:
#   docker buildx imagetools inspect python:3.11-slim --format '{{.Manifest.Digest}}'
FROM python:3.11-slim

WORKDIR /app

# System dependencies for PDF generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Unprivilegierter Benutzer: der Container darf NICHT als root laufen ──
RUN groupadd --gid 10001 adr \
 && useradd --uid 10001 --gid adr --no-create-home --shell /usr/sbin/nologin adr

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Application
COPY --chown=adr:adr . .

# Daten- und Exportverzeichnis (persistent über Volumes)
RUN mkdir -p /app/data /app/exports \
 && chown -R adr:adr /app/data /app/exports

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ADR_HOST=0.0.0.0 \
    ADR_PORT=5050

USER adr

# Production WSGI server
EXPOSE 5050

# Healthcheck — ohne Login erreichbar, prüft App und Datenbank
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5050/healthz || exit 1

# Worker/Thread-Zahl reduziert: SQLite erlaubt nur einen Schreiber gleichzeitig.
# Mehr Worker erhöhen hier nicht den Durchsatz, sondern die Gefahr von
# "database is locked". Für höhere Last auf PostgreSQL umstellen.
CMD ["gunicorn", "--bind", "0.0.0.0:5050", \
     "--workers", "2", \
     "--threads", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
