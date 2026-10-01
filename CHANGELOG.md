# Änderungsverlauf

Alle nennenswerten Änderungen dieses Projekts. Die Versionierung folgt
`MAJOR.MINOR.PATCH`; jeder Eintrag nennt die Beweggründe, nicht nur die
Änderung.

- **Aktuelle Version:** 4.2.3 (Datenstand ADR 2025)
- **Datenquelle:** Datenbank GEFAHRGUT der BAM (`dl-de/by-2-0`)

---

## 4.2.3 — Render-Instanz mit Anmeldung und Administratorzugang

**Anlass:** Die öffentliche Render-Instanz lief bisher mit `AUTH_ENABLED=0`.
Dadurch sah jeder Besucher direkt die Rechneransicht; die Anmeldeseite und die
Administrationsbereiche waren auf dieser Instanz nicht erreichbar. Die bereits
vorbereitete Blueprint-Änderung wurde als eigener Patch-Release ausgeliefert,
damit Image, Versionsanzeige und Dokumentation eindeutig zusammenpassen.

### Geändert

- `render.yaml`: `AUTH_ENABLED=1`, `ADR_ADMIN_USER=admin` und
  `PREFER_SECURE_COOKIE=1`; `ADR_ADMIN_PASSWORD` bleibt bewusst ungesetzt.
  Auf einem leeren Datenbestand gilt dadurch der ursprüngliche Erstzugang
  `admin`/`admin`, anschließend erzwingt die Anwendung den Passwortwechsel.
- `APP_VERSION` = `4.2.3`; README, Anleitung, Healthcheck-Beispiel und
  Dokumentationsdatei auf 4.2.3 nachgezogen.
- Neue Deployment-Vorlage `~/adr-deploy-v4.2.3.sh`: Sicherung, Vorbedingungen,
  Vorflug auf leerem Volume, Healthcheck, automatischer Rückbau auf 4.2.2 und
  Prüfung von Version, Startbefehl, Datenbestand und Benutzern.

### Geprüft

- Die exakte Render-Umgebung wurde lokal mit einem frischen Datenvolume
  ausgeführt: `/` antwortet 302 auf `/auth/login`, `/api/kunden` antwortet
  401, `admin`/`admin` führt auf den erzwungenen Passwortwechsel, und
  `/healthz` meldet 3.374 UN-Varianten.
- Die vollständige Testsuite bleibt bei **160 Tests**.
- Die Bedienungsanleitung wurde aus einer frischen 4.2.3-Instanz neu erzeugt,
  als PDF textlich geprüft und unter `docs/` abgelegt.

---

## 4.2.2 — Erststart auf leerem Datenbestand repariert

**Anlass:** Beim ersten Start mit einem **leeren Datenverzeichnis** — also bei
jeder Neuinstallation, jeder frisch aufgesetzten Testinstanz und jedem neuen
Server — konnte der Container sofort wieder aussteigen. Beide gunicorn-Worker
führten gleichzeitig `init_db()` aus; einer verlor das Rennen um die Schreibsperre
der SQLite-Datei und beendete sich mit

```
sqlite3.OperationalError: database is locked
Worker (pid:7) exited with code 3.
Reason: Worker failed to boot.
```

gunicorn stoppte daraufhin den Master, der Container endete mit Exit-Code 3. Auf
einem bereits befüllten Volume trat das nie auf (dort ist `init_db()` schnell),
sodass der Fehler lange unentdeckt blieb — er traf aber **jede** frisch
aufgesetzte Instanz, auch jede Prüf- und Hosting-Umgebung.

### Geändert

- `Dockerfile`: Der Startbefehl lädt die Anwendung mit **`--preload`**, bevor
  gunicorn die Worker abspaltet. `init_db()` läuft damit **einmal** im
  Master-Prozess; das Wettrennen um die SQLite-Sperre entfällt. Die Anzahl der
  Worker (2) und das Einlesen der 3.374 BAM-Varianten bleiben unverändert.
  Wer den Container mit eigenem Startbefehl betreibt, muss `--preload` mitgeben
  oder mit `--workers 1` fahren.
- `render.yaml` (neu): Blueprint für eine **öffentliche Instanz** bei Render —
  kostenloser Instanztyp (512 MB), Startbefehl mit `$PORT` und `--preload`,
  Healthcheck `/healthz`. Damit lässt sich die Anwendung benutzen, ohne eine
  Instanz im eigenen Netz freizugeben. **Nachtrag (Konfiguration, kein
  Anwendungscode):** die Blueprint liefert die Anwendung mit **eingeschalteter
  Anmeldung** aus — `AUTH_ENABLED=1`, `ADR_ADMIN_USER=admin`,
  `PREFER_SECURE_COOKIE=1`, **kein** `ADR_ADMIN_PASSWORD`, damit auf dem leeren
  Volume der dokumentierte Erstzugang `admin`/`admin` mit erzwungenem Wechsel
  entsteht. Die öffentliche Adresse zeigt dadurch die vollständige Anwendung
  einschließlich Benutzerverwaltung, Einstellungen und ADR-Import. Ein Passwort
  gehört nicht in eine öffentliche Blueprint; der Erstzugang ist unmittelbar
  nach dem Deployment zu beanspruchen. README und Anleitung beschreiben den Weg,
  den Erstzugang und die Grenzen (Ruhezustand nach 15 Minuten, flüchtiges
  Dateisystem).
- `APP_VERSION` = `4.2.2`; Versionsangaben in `README.md`, `CHANGELOG.md` und der
  Installations- und Bedienungsanleitung nachgezogen, Screenshots aus einer
  frischen 4.2.2-Instanz neu erzeugt.

### Geprüft

- **Erststart auf leerem Volume mit dem Standard-Startbefehl**: Container läuft,
  `/healthz` meldet `{"status":"ok","un_numbers":3374,"version":"4.2.2"}`.
- **Vorgänger-Startbefehl ohne `--preload`** bricht auf demselben leeren Volume
  weiterhin mit Exit 3 ab — die Ursache ist damit belegt und nicht nur vermutet.
- **160 Tests grün** (`pytest -q`, 76 s). Keine Testanpassung nötig: geändert
  wurde der Startparameter, kein Anwendungscode.
- Laufzeitbedarf ca. 80 MB Arbeitsspeicher (Grundlage der Free-Instanz).

---

## 4.2.1 — Veralteter Hinweis in der Oberfläche entfernt

**Anlass:** Die Seite *Passwort ändern* wies darauf hin, dass nach der Änderung
„eine eventuell vorhandene Datei mit dem erzeugten Anfangspasswort automatisch
gelöscht“ werde. Eine solche Datei gibt es seit 4.1 nicht mehr — der Hinweis
beschrieb einen Ablauf, den die Anwendung nicht mehr kennt, und war damit
geeignet, falsche Erwartungen an den Passwortwechsel zu wecken.

### Geändert

- `templates/password_change.html`: Der Hinweis benennt jetzt die tatsächliche
  Wirkung — nach dem Speichern gilt das bisherige Passwort nicht mehr, ein
  Anfangspasswort ist verbraucht und sollte nicht weitergegeben werden.
- Keine Verhaltensänderung, keine Änderung an Datenbank oder Schnittstellen.

---

## 4.2.0 — Mailserver-Zugangsdaten in der Anwendung

**Anlass:** Die Zugangsdaten des Mailservers lagen in Umgebungsvariablen und
damit in der Container-Konfiguration. Für einen Postfachwechsel war ein neuer
Container nötig, und eine Weitergabe des Images hätte fremde Zugangsdaten
mitgeführt.

### Neu

- Tabelle `settings` (`key`, `value`, `updated_at`, `updated_by`); wird über
  `CREATE TABLE IF NOT EXISTS` angelegt — kein Migrationsschritt nötig.
- Modul `settings_store.py` als einzige Lesestelle; `mailer.py` liest die
  Konfiguration **ausschließlich** von dort.
- Seite **Einstellungen** (`/einstellungen`, nur Administratoren) mit den
  Feldern Mailserver, Port, STARTTLS, Benutzer/Postfach, Passwort,
  Absenderadresse, Adresse der Anwendung, Name in Betreff und Signatur.
- Schnittstellen `GET|PUT /api/settings/mail` und
  `POST /api/settings/mail/test` (Anmeldung am Mailserver **ohne** Versand
  einer Nachricht).

### Geändert

- Die Umgebungsvariablen `ADR_SMTP_*` und `ADR_MAIL_*` **entfallen ersatzlos**.
- Änderungen wirken sofort, ohne Neustart des Containers.

### Sicherheit

- `mail_public_settings()` liefert das Postfachpasswort **nie** — nur
  `password_set: true/false`. Ein leer übergebenes Passwort lässt das
  gespeicherte bestehen, `clear_password: true` leert es.
- Eingabeprüfung: Port ganzzahlig 1–65535, `mail_from` gegen ein
  Adressmuster, `mail_app_url` muss mit `http(s)://` beginnen.
- Ohne vollständige Konfiguration oder ohne Empfängeradresse wird **gar nicht**
  versucht zuzustellen; die Oberfläche zeigt das Passwort dann einmalig an.

### Tests

- 160 Tests (vorher 136): `tests/test_settings.py` (Zugriffsschutz,
  Passwort verlässt die Anwendung nicht, Korrektur und Leeren,
  Eingabeprüfung) und `tests/test_mailer.py` auf Datenbank-Konfiguration
  umgestellt.

---

## 4.1.0 — Erstzugang, Anfangspasswort per E-Mail, Löschen

**Anlass:** Bei der Auslieferung an einen Betrieb mit mehreren Standorten war
unklar, wie der erste Administrator sein Startpasswort erhält, ohne dass ein
Passwort vorab verteilt werden muss. Außerdem fehlte eine Möglichkeit, Konten
wirklich zu löschen (Fehlanlagen, Art. 17 DSGVO).

### Neu

- **Erstzugang `admin` / `admin`** (bzw. `ADR_ADMIN_USER`/`ADR_ADMIN_PASSWORD`).
  Das Konto entsteht immer mit `must_change_password = 1`.
- Anmeldung führt direkt auf `/auth/passwort-aendern`; **jede** andere Route —
  auch jede Schnittstelle — antwortet `403 {"must_change_password": true}`.
- Spalte `users.email` (optional, Migration in `V4_USER_COLUMNS`).
- Modul `mailer.py` (nur Standardbibliothek): ein **erzeugtes**
  Anfangspasswort geht per E-Mail an die hinterlegte Adresse.
- `manage.py bootstrap-admin` (Erstzugang setzen, einziger Weg an der
  Passwortrichtlinie vorbei) und `manage.py delete-user`.
- Startpasswort beim Anlegen optional **vorgeben** (richtliniengeprüft) oder
  leer lassen und **erzeugen** lassen — in beiden Fällen ist der Wechsel beim
  ersten Anmelden erzwungen.

### Geändert

- **Deaktivieren ≠ Löschen:** `PUT /api/users/<id> {"active": false}` sperrt
  umkehrbar, `DELETE /api/users/<id>` löscht die Zeile **hart** (samt
  Fehlversuchen, damit der Name wieder frei ist). Geschützt bleiben das eigene
  Konto und der letzte aktive Administrator.
- E-Mail-Adresse ist nachträglich korrigierbar **und leerbar**.
- Entfallen: Passwortdatei `.admin_password`, `ADR_REQUIRE_ADMIN_PASSWORD`,
  `ADR_ADMIN_PASSWORD_FILE`.

### Tests

- 136 Tests (vorher 103): `tests/test_mailer.py` (Fake-SMTP),
  `test_auth_admin.py` neu geschrieben, `test_user_management.py` erweitert.

---

## 4.0.0 — Benutzerverwaltung, Passwortrichtlinie, DSGVO-Lücken

**Anlass:** Vor der Auslieferung an ein Unternehmen mit mehreren Niederlassungen
ergab eine Codeprüfung drei Blocker: es gab keine Möglichkeit, weitere Konten
anzulegen; ein vergessenes Administratorkonto war nicht zurücksetzbar; und das
Audit-Log schrieb bei Kundenänderungen die vollständigen Werte mit, wodurch eine
Löschung nach Art. 17 DSGVO ins Leere lief.

### Neu

- Seite **Benutzerverwaltung** und Schnittstellen `/api/users` (anlegen, Rolle
  ändern, deaktivieren, Passwort zurücksetzen) — nur Administratoren.
- **Passwortrichtlinie**: Mindestlänge 12, Sperrliste, kein Benutzername im
  Passwort, mindestens 5 verschiedene Zeichen; Speicherung als scrypt-Hash.
- **Kontosperre** nach 10 Fehlversuchen für 15 Minuten, gezählt über den
  Benutzernamen (nicht über die IP).
- **Audit-Log** (append-only) mit Aufbewahrungsfrist
  (`ADR_AUDIT_RETENTION_DAYS`) und abschaltbarer IP-Protokollierung
  (`ADR_AUDIT_LOG_IP`).
- **Datenauskunft nach Art. 15/20 DSGVO** als JSON je Kunde.
- Dokumentation `DSGVO.md` (Verzeichnis nach Art. 30, Löschkonzept, TOM).

### Behoben

- Audit-Log speichert bei Kunden- und Adressänderungen nur noch die
  **Feldnamen**, nicht die Werte.
- Beim Löschen einer Sendung wird die zugehörige Beförderungspapier-PDF
  mitgelöscht.

### Tests

- 103 Tests (vorher 63): `test_auth_admin.py`, `test_user_management.py`,
  `test_dsgvo.py`.

---

## 3.0.0 — Amtliche BAM-Daten statt PDF-Parsing

**Anlass:** Tabelle A ist eine 20-spaltige Tabelle über zwei gegenüberliegende
Seiten. Beim Auslesen als Text gehen die Spaltengrenzen verloren; die
Beförderungskategorie wurde geschätzt und im Zweifel **Kategorie 3**
angenommen. Bei einer Freistellungsentscheidung nach 1.1.3.6 ist das ein
untragbares Risiko.

### Geändert

- Datenquelle ist die amtliche **Datenbank GEFAHRGUT** der BAM
  (`data/bam/ADR25_csv.txt`, tab-separiert, cp1252) — 3.374 Varianten zu
  2.347 UN-Nummern. Beförderungskategorie (`N_KATEGORIE`) und Punktfaktor
  (`N_MULTIPLIKATOR`) kommen aus der Datei; es wird nichts mehr geschätzt.
- Natürlicher Schlüssel der UN-Daten ist **(UN-Nummer, Variante)**, abgesichert
  durch den eindeutigen Index `idx_un_variant`.
- Das ADR-PDF ist **nur noch Verifikation**: Abgleich des PDF-Parses gegen den
  Datenbestand sowie Prüfsummen der Abschnitte 1.1.3.6 und 5.4.1.1 zur
  Änderungsaufsicht. Es schreibt nicht mehr in die Datenbank.
- Zusätzliche Felder je Variante: LQ, EQ, Kemler-Zahl, Klassifizierungscode,
  Tankcode, Gefahrzettel.

### Behoben

- **Variantenfehler:** Ein Update allein über die UN-Nummer überschrieb die
  Varianten gegenseitig (z. B. UN 1133: VG I/II/III hätten alle Kategorie 3
  erhalten). 578 der 2.347 UN-Nummern sind mehrvariantig.
- 223 Abweichungen gegenüber dem vorherigen Bestand behoben (Gefahrklasse 89,
  Verpackungsgruppe 86, Beförderungskategorie 42, Tunnelcode 5).
  Gegenprobe gegen einen unabhängigen PDF-Parse: 2.346 von 2.347 UN-Nummern
  stimmen überein (99,96 %).

### Tests und Betrieb

- 63 Tests (vorher 30).
- Container läuft als unprivilegierter Benutzer, mit `HEALTHCHECK`,
  Upload-Obergrenze und behobenem XSS im Attributkontext.

---

## 2.0.0 — Rechtlich kritische Lücken geschlossen

**Anlass:** Version 1.x stellte eine Beförderung allein anhand der Punktzahl
fest. Das ist rechtlich falsch: Die Freistellung nach 1.1.3.6 hat **vier
kumulative Voraussetzungen**.

### Geändert

| # | Problem in 1.x | Konsequenz | Umsetzung in 2.0 |
|---|---|---|---|
| 1 | Freistellung nur nach `Punkte ≤ 1000` | Güter der Beförderungskategorie 0 wurden fälschlich freigestellt | Vollständige 1.1.3.6-Prüfung über die Beförderungskategorie |
| 2 | `max_quantity_per_transport` vorhanden, aber nie geprüft | Höchstmenge je Beförderungseinheit ignoriert | wird geprüft und blockiert |
| 3 | Keine Unterscheidung Stückgut / Tank / Schüttgut | Tanktransport konnte fälschlich freigestellt werden | Beförderungsart ist Pflichtfeld |
| 4 | `DEBUG=True`, Bindung an `0.0.0.0`, hartcodierter Secret Key | Debugger von außen erreichbar (RCE-Risiko) | behoben, Konfiguration über Umgebungsvariablen |
| 5 | Keine Anmeldung | jeder im Netz konnte Stamm- und Personendaten ändern | Anmeldepflicht mit Rollen `admin`/`user` |
| 6 | Kein Änderungsprotokoll | Änderungen an Kategorie/Punktfaktor nicht nachvollziehbar | Audit-Log (append-only) |
| 7 | `escapeHtml()` escapete keine Anführungszeichen | XSS in Attributkontexten möglich | behoben |
| 8 | Keine Tests | Regressionen blieben unbemerkt | 30 Unit-Tests für die Regelengine |
| 9 | Jede Berechnung erzeugte eine Sendung | Datenbestand mit Testrechnungen aufgebläht | Trennung Vorschau / Speichern |
| 10 | Dubletten, fehlende Kategorie als „Kat. 3“ angenommen | falsche Zuordnung möglich | Dubletten entfernt, Fail-Safe statt Standardwert |

### Weiteres

- Punkteformel korrekt: **Menge je Verpackung × Anzahl Verpackungen × Faktor**
  (frontend und backend); die Anzahl der Verpackungen löst eine Neuberechnung aus.
- Beförderungspapier bindet die **exakte** UN-Variante über `un_db_id`
  (vorher: falsche Verpackungsgruppe bei mehrvariantigen UN-Nummern).
- Gesamtmenge auf dem Beförderungspapier = Summe aus Menge × Anzahl Verpackungen.
- Eigene Auswahlliste für UN-Varianten (Verpackungsgruppe, Kategorie, Faktor in
  einer Zeile) statt `datalist`.

> **Hinweis für Bestandsnutzer:** In 1.x gespeicherte Sendungen besitzen kein
> gespeichertes Prüfergebnis. Für diese Altdaten wird beim PDF-Aufruf
> konservativ „nicht freigestellt“ angenommen.

---

## 1.0.0 — Erste Fassung

- Flask-Anwendung mit 1000-Punkte-Rechner, UN-Datenbank aus dem ADR-PDF,
  Kunden- und Adressverwaltung, Beförderungspapier als PDF (ReportLab).
- Oberfläche vollständig deutsch (Bootstrap).
