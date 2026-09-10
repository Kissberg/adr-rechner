# ADR 1000-Punkte-Rechner

Gefahrgut-Transportberechnung nach **ADR 1.1.3.6** — der 1000-Punkte-Regel.
Berechnen Sie, ob Ihr Gefahrguttransport unter die Freistellung fällt, und
erstellen Sie rechtskonforme Beförderungspapiere (ADR Transport Document).

> **English:** Dangerous goods transport calculation under ADR 1.1.3.6 (1000-point rule).
> Check exemption eligibility and generate compliant transport documents.

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg">
  <img alt="Version 2.0" src="https://img.shields.io/badge/version-2.0-orange">
  <img alt="Platform: x86-64" src="https://img.shields.io/badge/platform-x86--64-lightgrey">
  <img alt="Docker Pulls" src="https://img.shields.io/docker/pulls/kissberg/adr-rechner">
</p>

---

## ⚠️ Wichtiges Update — Version 2.0

Version 2.0 schließt **rechtlich und sicherheitstechnisch kritische Lücken**
der Version 1.x. **Wer 1.x produktiv einsetzt, sollte umgehend aktualisieren.**

| # | Problem in 1.x | Konsequenz | Status in 2.0 |
|---|----------------|------------|---------------|
| 1 | Freistellung nur nach `Punkte ≤ 1000` | Güter der Beförderungskategorie 0 (u. a. bestimmte Stoffe der Klassen 1, 6.2 und 7) wurden fälschlich freigestellt | ✅ Vollständige 1.1.3.6-Prüfung über die Beförderungskategorie |
| 2 | Feld `max_quantity_per_transport` vorhanden, aber nie geprüft | Höchstmenge je Beförderungseinheit (1.1.3.6.3) wurde ignoriert | ✅ wird geprüft und blockiert |
| 3 | Keine Unterscheidung Stückgut / Tank / Schüttgut | Tanktransport konnte fälschlich freigestellt werden | ✅ Beförderungsart ist Pflichtfeld |
| 4 | `DEBUG=True`, Bindung an `0.0.0.0`, hartcodierter Secret Key | Werkzeug-Debugger von außen erreichbar (RCE-Risiko) | ✅ behoben, Konfiguration über Umgebungsvariablen |
| 5 | Keine Anmeldung | Jeder im Netz konnte Stamm- und Personendaten ändern/löschen | ✅ Anmeldepflicht mit Rollen `admin` / `user` |
| 6 | Kein Änderungsprotokoll | Änderungen an Kategorie/Punktfaktor nicht nachvollziehbar | ✅ Audit-Log (append-only) |
| 7 | `escapeHtml()` escapete keine Anführungszeichen | XSS in Attributkontexten möglich | ✅ behoben |
| 8 | Keine Tests | Regressionen blieben unbemerkt | ✅ 30 Unit-Tests für die Regelengine |
| 9 | Jede Berechnung erzeugte eine Sendung | Datenbestand wurde mit Testrechnungen aufgebläht | ✅ Trennung Vorschau / Speichern |
| 10 | UN-Daten enthielten Dubletten, fehlende Kategorie wurde als Kat. 3 angenommen | Falsche Zuordnung möglich | ✅ Dubletten entfernt, Fail-Safe statt Standardwert |

> **Hinweis für Bestandsnutzer:** In 1.x gespeicherte Sendungen besitzen kein
> gespeichertes Prüfergebnis. Für diese Altdaten wird beim PDF-Aufruf
> konservativ **„nicht freigestellt“** angenommen.

---

## Funktionen

- 🔢 **1000-Punkte-Berechnung** — Gesamtpunktzahl nach ADR 1.1.3.6: ∑(Menge × Faktor) pro Transportkategorie
- 📋 **UN-Nummern-Datenbank** — rund 2.900 UN-Nummern mit Stoffbezeichnungen, Gefahrklassen, Verpackungsgruppen und Tunnelcodes
- 📄 **Beförderungspapier (PDF)** — ADR-konformes Transportdokument mit allen Pflichtangaben (Absender, Empfänger, UN-Nr., Menge, Punkte, Tunnelcode)
- 🏢 **Kundenverwaltung** — Kunden und Versandadressen (CRUD), Excel-Import/Export
- 📥 **ADR-Datenimport** — automatisches Parsen aktueller ADR-PDFs (PyMuPDF) zur Aktualisierung der UN-Datenbank
- 🌐 **Deutsche Oberfläche** — vollständig deutschsprachiges Web-Interface (Bootstrap 5)

---

## Schnellstart

### Docker (empfohlen)

```bash
docker run -d \
  --name adr-rechner \
  -p 5050:5050 \
  -v adr_data:/app/data \
  -v adr_exports:/app/exports \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e ADR_ADMIN_PASSWORD="<starkes-passwort>" \
  kissberg/adr-rechner:latest
```

Danach **http://localhost:5050** im Browser öffnen.

### Docker Compose

```yaml
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
    environment:
      - SECRET_KEY=${SECRET_KEY:?SECRET_KEY muss gesetzt werden}
      - ADR_ADMIN_USER=${ADR_ADMIN_USER:-admin}
      - ADR_ADMIN_PASSWORD=${ADR_ADMIN_PASSWORD:-}

volumes:
  adr_data:
  adr_exports:
```

```bash
docker compose up -d
```

### Manuelle Installation

Voraussetzungen: **Python 3.11+** und `libfreetype6` (für die PDF-Generierung).

```bash
git clone https://github.com/Kissberg/adr-rechner.git
cd adr-rechner
pip install -r requirements.txt
python app.py
```

---

## Architektur

```
adr-rechner/
├── app.py                  # Flask-App (Haupteinstieg)
├── adr_rules.py            # ADR-1.1.3.6-Regelengine (testbar, ohne Flask)
├── database.py             # SQLite-Datenbank & CRUD-Operationen
├── befoerderungspapier.py  # PDF-Generierung (ReportLab)
├── adr_import.py           # ADR-PDF-Parsing (PyMuPDF)
├── auth.py                 # Anmeldung, Rollen (admin/user)
├── audit.py                # Änderungsprotokoll (append-only)
├── tests/                  # Unit-Tests der Regelengine
├── data/
│   └── adr_2025_seed.json  # Seed-Daten (rund 2.900 UN-Nummern)
├── static/                 # Bootstrap 5 Styles + JavaScript
├── templates/              # Jinja2-Templates
├── Dockerfile              # amd64 (x86-64)
├── docker-compose.yml
└── requirements.txt
```

| Schicht | Technologie |
|---------|-------------|
| Backend | Python 3.11, Flask, Gunicorn |
| Datenbank | SQLite (WAL-Modus) |
| PDF | ReportLab |
| ADR-Parsing | PyMuPDF (fitz) |
| Frontend | Bootstrap 5, Vanilla JS |
| Deployment | Docker (**amd64 / x86-64**) |

> **Hinweis zur Plattform:** Das offizielle Docker-Image wird für **amd64
> (x86-64)** gebaut. Die frühere arm64-Variante (Raspberry Pi) wird nicht
> mehr gepflegt — ein Self-Build ist über `docker build -t adr-rechner .`
> auf der jeweiligen Architektur weiterhin möglich.

---

## Sicherheit & Konfiguration

Ab Version 2.0 ist die Anwendung **anmeldepflichtig**. Die Konfiguration
erfolgt ausschließlich über Umgebungsvariablen — es gibt keine
hartcodierten Geheimnisse mehr im Quellcode.

| Variable | Pflicht | Standard | Bedeutung |
|----------|---------|----------|-----------|
| `SECRET_KEY` | **ja** | zufällig | Sitzungsschlüssel. Ohne Wert werden nach jedem Neustart alle Anmeldungen ungültig. |
| `AUTH_ENABLED` | nein | `1` | `0` schaltet die Anmeldung ab — **nur für lokale Entwicklung**. |
| `ADR_ADMIN_USER` | nein | `admin` | Benutzername des ersten Administrators. |
| `ADR_ADMIN_PASSWORD` | nein | zufällig | Passwort des ersten Administrators. Ohne Wert wird ein zufälliges erzeugt und **einmalig im Log ausgegeben**. |
| `PREFER_SECURE_COOKIE` | nein | `0` | `1` setzt `Secure` am Session-Cookie — **bei HTTPS/Betrieb hinter Reverse-Proxy setzen**. |
| `MAX_UPLOAD_MB` | nein | `50` | Obergrenze für PDF-/Excel-Uploads (Schutz vor Ressourcenerschöpfung). |
| `ADR_HOST` / `ADR_PORT` | nein | `127.0.0.1` / `5050` | Nur für `python app.py`. `ADR_HOST=0.0.0.0` ist ohne Reverse-Proxy nicht zulässig. |

### Rollen

| Rolle | Rechte |
|-------|--------|
| `admin` | Alles, inkl. UN-Datenbank, ADR-Import, Audit-Log, Löschen von Sendungen |
| `user` | Berechnung, Beförderungspapiere, Kunden- und Adressverwaltung |

### Empfohlener Produktivbetrieb

```bash
docker run -d \
  --name adr-rechner \
  -p 127.0.0.1:5050:5050 \
  -v adr_data:/app/data \
  -v adr_exports:/app/exports \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e ADR_ADMIN_PASSWORD="<starkes-passwort>" \
  -e PREFER_SECURE_COOKIE=1 \
  kissberg/adr-rechner:latest
```

Danach einen Reverse-Proxy (nginx, Traefik, Caddy) mit TLS vorschalten und
den Container **nicht** direkt exponieren. Der Healthcheck ist unter
`/healthz` ohne Anmeldung erreichbar.

### Datenschutz / Nachweispflicht

- Alle Änderungen an Stammdaten, Sendungen und ADR-Importen werden im
  **Audit-Log** protokolliert (Benutzer, Zeit, Aktion, Änderung, IP).
  Das Log ist append-only und nur für Administratoren unter
  `/api/audit-log` abrufbar.
- Kundendaten sind personenbezogene Daten im Sinne der DSGVO. Der
  Zugriffsschutz ist daher keine Komfortfunktion, sondern eine Anforderung
  aus Art. 32 DSGVO.
- Für eine GoBD-konforme Archivierung der Beförderungspapiere ist zusätzlich
  ein WORM-Speicher bzw. eine Signatur/Timestamping-Lösung erforderlich
  (siehe `PLAN.md`).

---

## ADR 1.1.3.6 — Die 1000-Punkte-Regel

Nach ADR Unterabschnitt 1.1.3.6 sind Transporte von Gefahrgütern
**freigestellt**, wenn **alle** Voraussetzungen erfüllt sind. Die Punktzahl
ist nur *eine* davon.

**Formel:** ∑(Menge × Faktor) für alle Gefahrgüter einer Sendung

### Die vier kumulativen Voraussetzungen

| Nr. | Voraussetzung | Rechtsgrundlage |
|-----|---------------|-----------------|
| 1 | Beförderung als **Stückgut** (in Versandstücken). Tank und Schüttgut sind nie freigestellt. | 1.1.3.6.2 |
| 2 | Kein Gut hat die **Beförderungskategorie 0** — diese ist niemals freigestellt. | 1.1.3.6.3 |
| 3 | Je Gut wird die **Höchstmenge je Beförderungseinheit** eingehalten. | 1.1.3.6.3 |
| 4 | Die **Gesamtpunktzahl** überschreitet 1000 nicht. | 1.1.3.6.4 |

> **Fail-Safe-Prinzip:** Lässt sich eine Voraussetzung nicht zweifelsfrei
> prüfen (unbekannte Beförderungskategorie, nicht gewählte Verpackungsgruppe),
> wird **nicht** freigestellt. Eine zu Unrecht erteilte Freistellung ist ein
> Rechtsverstoß; eine zu Unrecht verweigerte führt lediglich zur (legalen)
> Vollanwendung des ADR.

### Beförderungskategorien

| Kategorie | Faktor | Höchstmenge je Beförderungseinheit |
|-----------|--------|-------------------------------------|
| 0 | 0 | 0 (niemals freigestellt) |
| 1 | 50 | 20 kg / L |
| 2 | 3 | 333 kg / L |
| 3 | 1 | 1000 kg / L |
| 4 | 0 | unbegrenzt |

> **Fussnote a) zu 1.1.3.6.3:** Für die UN-Nummern 0081, 0082, 0084, 0241,
> 0331, 0332, 0482, 1005 und 1017 gilt abweichend: Faktor **20**, Höchstmenge
> **50 kg**.

Ergebnis ≤ 1000 Punkte **und** keine andere Voraussetzung verletzt
→ **Freistellung**.

---

## Lizenz

MIT License — siehe [LICENSE](LICENSE).

**⚠️ Wichtiger Haftungsausschluss:** Die enthaltenen ADR-Daten dienen
**ausschließlich Referenzzwecken**. Vor rechtsverbindlicher Nutzung sind
alle Daten zwingend mit den amtlichen ADR-Vorschriften (ECE/TRANS/300)
abzugleichen. Der Autor übernimmt keinerlei Gewähr.

---

## Autor

**Yun Zhu** — [GitHub: Kissberg](https://github.com/Kissberg)

---

## Docker Hub

Docker Image: [`kissberg/adr-rechner`](https://hub.docker.com/r/kissberg/adr-rechner)

```bash
docker pull kissberg/adr-rechner:latest
```
