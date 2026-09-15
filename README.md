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

## ⚠️ Wichtiges Update — Version 3.0: Datenquelle und Variantenlogik

Version 3.0 ersetzt die Datenbasis und korrigiert einen Fehler, der zu
**falschen Punktzahlen** führen konnte.

### Warum: das PDF ist als Datenquelle ungeeignet

Tabelle A (Kapitel 3.2) ist eine 20-spaltige Tabelle, die über zwei
gegenüberliegende Seiten läuft. Wird sie als Text gelesen, gehen die
Spaltengrenzen verloren. Die Beförderungskategorie musste deshalb geschätzt
werden — und wenn nichts gefunden wurde, wurde ersatzweise **Kategorie 3**
angenommen. Bei einer Freistellungsentscheidung nach ADR 1.1.3.6 ist das
ein untragbares Risiko.

### Was sich ändert

| | bis 2.x | ab 3.0 |
|---|---|---|
| Datenquelle | ADR-PDF, heuristisch geparst | **amtliche BAM-Datei** (Datenbank GEFAHRGUT), strukturiert |
| Beförderungskategorie | geschätzt, Default Kat. 3 | eigenes Feld der BAM — **nichts wird geraten** |
| Punktfaktor | aus der Kategorie abgeleitet | von der BAM mitgeliefert (`N_MULTIPLIKATOR`) |
| Varianten | `UPDATE … WHERE un_number = ?` | Schlüssel ist **(UN-Nummer, Variante)** |
| ADR-PDF | Datenquelle | **nur Verifikation und Änderungsaufsicht** |
| Zusätzliche Felder | — | LQ, EQ, Kemler-Zahl, Klassifizierungscode, Tankcode, Gefahrzettel |

### Der Variantenfehler

Eine UN-Nummer hat in Tabelle A mehrere Varianten, und die
Beförderungskategorie hängt an der Verpackungsgruppe:

```
UN 1133 KLEBSTOFFE    PG I → Kat 1 (Faktor 50)
                      PG II → Kat 2 (Faktor 3)
                      PG III → Kat 3 (Faktor 1)
```

Insgesamt betrifft das **354 UN-Nummern**. Ein Update nur auf die UN-Nummer
überschreibt diese Werte gegenseitig — es würde beispielsweise für alle drei
Verpackungsgruppen Kategorie 3 stehen. Seit 3.0 ist `(un_number, variant)`
der natürliche Schlüssel, abgesichert durch einen eindeutigen Index.

### Qualität der Datenbasis

Abgleich der neuen BAM-Daten gegen den bisherigen Bestand (ADR 2025):

- **223 Abweichungen** behoben (Gefahrklasse 89, Verpackungsgruppe 86,
  Beförderungskategorie 42, Tunnelcode 5, ein Datensatz ohne UN-Nummer)
- Gegenprobe durch einen unabhängigen Parse des ADR-PDF:
  **2.346 von 2.347 UN-Nummern stimmen überein (99,96 %)** — die einzige
  Abweichung ist UN 3316, deren Spalte (15) «siehe SV 671 (E)» lautet und
  nicht maschinell auflösbar ist.

### Datenquelle und Lizenzpflicht

Die Daten stammen aus der **Datenbank GEFAHRGUT (DGG)** der
Bundesanstalt für Materialforschung und -prüfung (BAM) und stehen unter der
*Datenlizenz Deutschland – Namensnennung – Version 2.0* (`dl-de/by-2-0`).
Sie sind seit dem 23.07.2025 kostenfrei und dürfen auch kommerziell
genutzt werden. **Die Quellenangabe ist Lizenzpflicht** und wird in der
Anwendung ausgegeben:

```
Source: Bundesanstalt für Materialforschung und -prüfung (BAM) –
Datenbank GEFAHRGUT – URL: tes.bam.de/TES/Navigation/EN/DGG-Database/
dgg-database.html — Data licence Germany – attribution – Version 2.0
```

> ⚠️ **Zwei Einschränkungen der BAM-Lizenz:**
> 1. Die BAM untersagt gemäß § 44b (3) UrhG die Nutzung der Daten für
>    **Text- und Data-Mining**. Die Verwendung als Nachschlagetabelle in
>    dieser Anwendung ist zulässig; das Training von Modellen mit diesen
>    Daten benötigt die schriftliche Zustimmung der BAM.
> 2. Die BAM übernimmt **keine Gewähr** für Richtigkeit und Vollständigkeit.
>    Eine Freistellungsentscheidung ist daher stets fachlich zu prüfen.

### Import und Verifikation in der Anwendung

Unter **„Daten & Verifikation“** werden beide Dateien hochgeladen:

1. **BAM-Datei** (`ADR25.xlsx` oder `ADR25_csv.txt`) — wird importiert und
   bildet den Datenbestand. Vor dem Import prüft eine Strukturprüfung die
   Datei; bei Auffälligkeiten wird abgebrochen.
2. **ADR-PDF** — schreibt **nicht** in die Datenbank. Es liefert
   - einen Abgleich des eigenen PDF-Parse gegen den Datenbestand
   - den Wortlaut von **1.1.3.6** (1000-Punkte-Regel) und **5.4.1.1**
     (Beförderungspapier) samt Prüfsumme, damit Textänderungen
     zwischen zwei ADR-Ausgaben auffallen.

> **Hinweis:** ADR wird zweibändig veröffentlicht. Band 1 enthält die Teile
> 1–3 (dort liegt 1.1.3.6), Band 2 die Teile 4–9 (dort liegt 5.4.1.1).
> Fehlt ein Abschnitt in der hochgeladenen Datei, wird das ausdrücklich
> gemeldet — es wird kein „nichts gefunden“ vorgetäuscht.

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
| `ADR_ADMIN_PASSWORD` | nein | zufällig | Passwort des ersten Administrators. Ohne Wert wird ein zufälliges erzeugt und in `.admin_password` im Datenverzeichnis abgelegt (**nicht** im Log). |
| `ADR_REQUIRE_ADMIN_PASSWORD` | nein | `0` | `1` verweigert den Start, wenn `ADR_ADMIN_PASSWORD` fehlt — **im Produktivbetrieb empfohlen**. |
| `ADR_ADMIN_PASSWORD_FILE` | nein | `<Datenverz>/.admin_password` | Alternativer Ort für die Passwortdatei. |
| `ADR_PASSWORD_MIN_LENGTH` | nein | `12` | Mindestlänge neuer Passwörter (kleiner als 8 wird nicht akzeptiert). |
| `ADR_MAX_LOGIN_ATTEMPTS` | nein | `10` | Fehlversuche, nach denen ein Konto gesperrt wird. |
| `ADR_LOGIN_LOCKOUT_MINUTES` | nein | `15` | Dauer der Sperre. |
| `ADR_AUDIT_RETENTION_DAYS` | nein | `0` (aus) | Tage, nach denen Audit-Einträge beim Start gelöscht werden. Siehe `DSGVO.md`. |
| `ADR_AUDIT_LOG_IP` | nein | `1` | `0` schreibt keine IP-Adressen ins Audit-Log (Datenminimierung). |
| `ADR_DB_DIR` / `ADR_DB_PATH` | nein | `<App>/data` | Datenverzeichnis bzw. -datei — für **mehrere Instanzen auf einem Server**. |
| `PREFER_SECURE_COOKIE` | nein | `0` | `1` setzt `Secure` am Session-Cookie — **bei HTTPS/Betrieb hinter Reverse-Proxy setzen**. |
| `MAX_UPLOAD_MB` | nein | `50` | Obergrenze für PDF-/Excel-Uploads (Schutz vor Ressourcenerschöpfung). |
| `ADR_HOST` / `ADR_PORT` | nein | `127.0.0.1` / `5050` | Nur für `python app.py`. `ADR_HOST=0.0.0.0` ist ohne Reverse-Proxy nicht zulässig. |

### Passwortrichtlinie

Bewusst **längenorientiert** statt komplexitätsorientiert: BSI (TR-02102-1)
und NIST (SP 800-63B) empfehlen beide Länge als wirksames Kriterium und
raten von erzwungenen Zeichenklassen ab — sie führen nachweislich zu
Mustern wie `Sommer2026!`. Geprüft wird:

* Mindestlänge 12 Zeichen
* nicht in einer Sperrliste gängiger Leak-Passwörter
* enthält nicht den Benutzernamen
* mindestens 5 verschiedene Zeichen

Gespeichert wird ausschließlich ein **scrypt-Hash** (`N=32768, r=8, p=1`).

Vom Administrator vergebene oder zurückgesetzte Passwörter müssen bei der
nächsten Anmeldung ersetzt werden — sonst kennt die Verwaltung dauerhaft
ein fremdes Passwort und die Nutzung ist nicht personenbezogen.

Bei 10 Fehlversuchen wird das Konto 15 Minuten gesperrt. Gezählt wird über
den Benutzernamen, nicht die IP-Adresse — sonst würde ein Wechsel der
Quell-IP das Limit umgehen.

### Warum das erzeugte Passwort nicht im Log steht

Wird kein `ADR_ADMIN_PASSWORD` gesetzt, erzeugt die Anwendung ein
Zufallspasswort und schreibt es in `.admin_password` im Datenverzeichnis.
Das Log nennt nur den Pfad, niemals das Passwort selbst.

Der Grund: Logs sind grundsätzlich **breiter lesbar und länger verfügbar**
als die Anwendung. `docker logs` zeigt sie jedem mit Docker-Zugang, der
`json-file`-Treiber hält sie in drei Rotationen à 10 MB vor, und in
Betrieben mit ELK/Loki/Grafana sind sie wochenlang durchsuchbar — für
deutlich mehr Personen als die Datenbank. Ein einmalig erzeugtes
Administratorpasswort im Log wäre faktisch ein dauerhaft gültiger
Admin-Zugang für alle, die Logs lesen dürfen.

Die Datei wird mit Rechten nur für den Besitzer angelegt (Unix `0600`,
unter Windows per `icacls` auf das eigene Konto beschränkt) und nach dem
ersten Passwortwechsel automatisch gelöscht. Kann sie nicht geschrieben
werden, **verweigert die Anwendung den Start** — sie fällt nicht darauf
zurück, das Passwort doch ins Log zu schreiben.

Für den Produktivbetrieb `ADR_REQUIRE_ADMIN_PASSWORD=1` setzen: Dann
verweigert die Anwendung den Start, solange kein Passwort gesetzt ist.

### Rollen

| Funktion | `admin` | `user` |
|---|:---:|:---:|
| Berechnung, Beförderungspapiere | ✓ | ✓ |
| Kunden und Adressen anlegen und ändern | ✓ | ✓ |
| **Kunden und Adressen löschen** | ✓ | — |
| **Datenauskunft nach Art. 15 DSGVO** | ✓ | — |
| Benutzerverwaltung | ✓ | — |
| Audit-Log einsehen | ✓ | — |
| UN-Datenbank bearbeiten, ADR-Import, Verifikation | ✓ | — |
| Sendungen löschen | ✓ | — |

Löschvorgänge und die Auskunft nach Art. 15 sind auf Administratoren
beschränkt: beides betrifft personenbezogene Daten unmittelbar und ist
nicht umkehrbar.

### Benutzerverwaltung

Administratoren legen unter **Benutzerverwaltung** (Menü oben rechts) die
Konten an. Ein Kennwort kann vorgegeben oder erzeugt werden; ein erzeugtes
wird **genau einmal** angezeigt und erscheint niemals im Log. Jedes so
vergebene Passwort muss bei der ersten Anmeldung ersetzt werden.

Konten werden **deaktiviert, nicht gelöscht** — das Audit-Log verweist über
den Benutzernamen auf das Konto, eine Zeilenlöschung würde diese Zuordnung
zerstören. Der letzte aktive Administrator kann sich weder selbst
herabstufen noch deaktivieren.

Ein vergessenes Administratorkonto lässt sich nur auf dem Server zurücksetzen:

```bash
docker exec -it adr-rechner python manage.py list-users
docker exec -it adr-rechner python manage.py reset-password admin
docker exec -it adr-rechner python manage.py unlock admin     # Sperre aufheben
docker exec -it adr-rechner python manage.py check            # Bestand prüfen
```

### Eine Instanz je Niederlassung

Die Anwendung hat **keine Mandantentrennung**: alle angemeldeten Konten
sehen alle Kunden. Für einen Betrieb mit mehreren Niederlassungen ist
deshalb **je Standort eine eigene Instanz** vorgesehen — getrennte
Datenbestände, getrennte Zugänge, kein gegenseitiger Einblick.

```bash
# Niederlassung München
docker run -d --name adr-muenchen \
  -p 127.0.0.1:5051:5050 \
  -v adr_muenchen_data:/app/data \
  -v adr_muenchen_exports:/app/exports \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e ADR_ADMIN_PASSWORD="<starkes-passwort>" \
  -e ADR_REQUIRE_ADMIN_PASSWORD=1 \
  -e ADR_AUDIT_RETENTION_DAYS=3650 \
  kissberg/adr-rechner:latest

# Niederlassung Hamburg — eigener Port, eigenes Volume, eigenes SECRET_KEY
docker run -d --name adr-hamburg \
  -p 127.0.0.1:5052:5050 \
  -v adr_hamburg_data:/app/data \
  -v adr_hamburg_exports:/app/exports \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e ADR_ADMIN_PASSWORD="<anderes-starkes-passwort>" \
  -e ADR_REQUIRE_ADMIN_PASSWORD=1 \
  kissberg/adr-rechner:latest
```

Jede Instanz hat ihre eigene Datenbank und ihr eigenes `SECRET_KEY` —
nie dasselbe verwenden, sonst ist eine Sitzung in beiden gültig.

Die UN-Stammdaten (Tabelle A) sind in jeder Instanz identisch und werden
aus derselben BAM-Datei befüllt; sie müssen bei einem ADR-Versionswechsel
in jeder Instanz einmal aktualisiert werden.

### Empfohlener Produktivbetrieb

```bash
docker run -d \
  --name adr-rechner \
  -p 127.0.0.1:5050:5050 \
  -v adr_data:/app/data \
  -v adr_exports:/app/exports \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e ADR_ADMIN_PASSWORD="<starkes-passwort>" \
  -e ADR_REQUIRE_ADMIN_PASSWORD=1 \
  -e PREFER_SECURE_COOKIE=1 \
  -e ADR_AUDIT_RETENTION_DAYS=3650 \
  kissberg/adr-rechner:latest
```

Danach einen Reverse-Proxy (nginx, Traefik, Caddy) mit TLS vorschalten und
den Container **nicht** direkt exponieren. Der Healthcheck ist unter
`/healthz` ohne Anmeldung erreichbar.

Das Passwort selbst wird **nicht** in die Kommandozeile geschrieben (sie
erscheint sonst in der Prozessliste und in der Shell-Historie). Stattdessen
eine Env-Datei mit `chmod 600` verwenden oder den Wert beim ersten Start
erzeugen lassen.

### Datenschutz / Nachweispflicht

- Alle Änderungen an Stammdaten, Sendungen und ADR-Importen werden im
  **Audit-Log** protokolliert (Benutzer, Zeit, Aktion, geänderte Felder,
  optional IP). Das Log ist append-only und nur für Administratoren unter
  `/api/audit-log` abrufbar.
- Bei Kunden- und Adressänderungen werden **nur die Feldnamen** festgehalten,
  nicht die Werte. Sonst entstünde im Log eine zweite Kopie der
  Kundenstammdaten, die eine Löschung nach Art. 17 DSGVO überleben würde.
- Kundendaten sind personenbezogene Daten im Sinne der DSGVO. Der
  Zugriffsschutz ist daher keine Komfortfunktion, sondern eine Anforderung
  aus Art. 32 DSGVO.
- Das Log enthält Benutzernamen und — sofern aktiviert — IP-Adressen.
  Ohne Aufbewahrungsfrist wächst es unbegrenzt (Art. 5 Abs. 1 lit. e).
  Über `ADR_AUDIT_RETENTION_DAYS` oder `manage.py purge-audit` aufräumen.
- Zugehörige Beförderungspapier-PDFs werden beim Löschen einer Sendung
  mitgelöscht — sie enthalten die vollständige Empfängeranschrift.
- Für eine GoBD-konforme Archivierung der Beförderungspapiere ist zusätzlich
  ein WORM-Speicher bzw. eine Signatur/Timestamping-Lösung erforderlich
  (siehe `PLAN.md`).

**Die vollständige Datenschutz-Dokumentation** — Verzeichnis nach Art. 30,
Rechtsgrundlagen, Löschkonzept, TOM und die Umsetzung der Betroffenenrechte —
steht in **[`DSGVO.md`](DSGVO.md)**.

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
