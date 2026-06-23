# ADR 1000-Punkte-Rechner — Dockerfile
FROM python:3.11-slim

WORKDIR /app

# System dependencies for PDF generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY . .

# Create data directory for SQLite (persisted via volume)
RUN mkdir -p /app/data /app/exports

# Production WSGI server
EXPOSE 5050

CMD ["gunicorn", "--bind", "0.0.0.0:5050", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
