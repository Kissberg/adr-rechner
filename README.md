# ADR 1000-Punkte-Rechner

Gefahrgut-Transportberechnung nach ADR 1.1.3.6 — die **1000-Punkte-Regel**.
Berechnen Sie, ob Ihr Gefahrguttransport unter die Freistellung fällt, und erstellen Sie
rechtskonforme Beförderungspapiere (ADR Transport Document).

> **English:** Dangerous goods transport calculation under ADR 1.1.3.6 (1000-point rule).
> Check exemption eligibility and generate compliant transport documents.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform: arm64/amd64](https://img.shields.io/badge/platform-arm64%20%7C%20amd64-lightgrey)
![Docker Pulls](https://img.shields.io/docker/pulls/kissberg/adr-rechner)

---

## Funktionen / Features

- 🔢 **1000-Punkte-Berechnung** — Automatische Ermittlung der Gesamtpunktzahl nach
  ADR 1.1.3.6: ∑(Menge × Faktor) pro Transportkategorie
- 📋 **UN-Nummern-Datenbank** — Über 3000 UN-Nummern mit Stoffbezeichnungen,
  Gefahrklassen, Verpackungsgruppen, Tunnelcodes und Sondervorschriften
- 📄 **Beförderungspapier (PDF)** — ADR-konformes Transportdokument mit allen
  Pflichtangaben (Absender, Empfänger, UN-Nr., Menge, Punkte, Tunnelcode)
- 🏢 **Kundenverwaltung** — Kunden und Versandadressen CRUD, Excel-Import/Export
- 📥 **ADR-Datenimport** — Automatisches Parsen aktueller ADR-PDFs (PyMuPDF)
  zur Aktualisierung der UN-Datenbank
- 🌐 **Deutsche Oberfläche** — Vollständig deutschsprachiges Web-Interface
  (Bootstrap 5, PC-optimiert)

---

## Schnellstart / Quick Start

### Docker (empfohlen / recommended)

```bash
docker pull kissberg/adr-rechner:latest
docker run -d \
  --name adr-rechner \
  -p 5050:5050 \
  -v adr_data:/app/data \
  -v adr_exports:/app/exports \
  kissberg/adr-rechner:latest
```

Öffnen Sie dann **http://localhost:5050** im Browser.

### Docker Compose

```yaml
version: '3.8'
services:
  adr-rechner:
    image: kissberg/adr-rechner:latest
    container_name: adr-rechner
    restart: unless-stopped
    ports:
      - "5050:5050"
    volumes:
      - adr_data:/app/data
      - adr_exports:/app/exports

volumes:
  adr_data:
  adr_exports:
```

```bash
docker compose up -d
```

### Manuelle Installation / Manual

- **Python 3.11+**
- **System:** `libfreetype6` (für PDF-Generierung mit ReportLab)

```bash
git clone git@github.com:Kissberg/adr-rechner.git
cd adr-rechner
pip install -r requirements.txt
python app.py
```

---

## Architektur / Architecture

```
adr-rechner/
├── app.py                      # Flask-App (Haupteinstieg / main entry point)
├── database.py                 # SQLite-Datenbank & CRUD-Operationen
├── befoerderungspapier.py      # PDF-Generierung (ReportLab)
├── adr_import.py               # ADR-PDF-Parsing (PyMuPDF)
├── data/
│   └── adr_2025_seed.json      # Seed-Daten (~3000 UN-Nummern)
├── static/
│   ├── css/                    # Bootstrap 5 Styles
│   └── js/
│       └── calculator.js       # Frontend-Berechnungslogik
├── templates/
│   └── index.html              # Single-Page Application (Jinja2)
├── Dockerfile                  # Multi-Arch (arm64/amd64)
├── docker-compose.yml
└── requirements.txt
```

| Schicht / Layer | Technologie |
|---|---|
| Backend | Python 3.11, Flask, Gunicorn |
| Datenbank | SQLite (WAL-Modus) |
| PDF | ReportLab |
| ADR-Parsing | PyMuPDF (fitz) |
| Frontend | Bootstrap 5, Vanilla JS |
| Deployment | Docker (arm64/amd64), Portainer |

---

## ADR 1.1.3.6 — Die 1000-Punkte-Regel

Nach ADR Unterabschnitt 1.1.3.6 sind Transporte von Gefahrgütern **freigestellt**,
wenn die Gesamtpunktzahl die Grenzwerte nicht überschreitet.

**Formel:** ∑(Menge × Faktor) für alle Gefahrgüter einer Sendung

| Transportkategorie / Category | Faktor / Factor | Höchstmenge / Max Qty |
|---|---|---|
| 0 | 0 | 0 |
| 1 | 50 | 20 |
| 2 | 3 | 333 |
| 3 | 1 | 1000 |
| 4 | 0 | unbegrenzt / unlimited |

Ergebnis ≤ 1000 Punkte → **Freistellung / Exemption**

---

## Lizenz / License

MIT License — siehe [LICENSE](LICENSE).

**⚠️ Wichtiger Haftungsausschluss:** Die enthaltenen ADR-Daten dienen
**ausschließlich Referenzzwecken**. Vor rechtsverbindlicher Nutzung sind
alle Daten zwingend mit den amtlichen ADR-Vorschriften (ECE/TRANS/300)
abzugleichen. Der Autor übernimmt keinerlei Gewähr. Siehe LICENSE für
den vollständigen Disclaimer (Deutsch / English).

---

## Autor / Author

**Yun Zhu** — [GitHub: Kissberg](https://github.com/Kissberg)

---

## Docker Hub

Docker Image: [`kissberg/adr-rechner`](https://hub.docker.com/r/kissberg/adr-rechner)

```bash
docker pull kissberg/adr-rechner:latest
```
