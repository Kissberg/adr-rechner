# Datenschutz-Dokumentation (DSGVO)

Arbeitsgrundlage für den Betrieb des ADR 1000-Punkte-Rechners in einem
Unternehmen mit mehreren Niederlassungen.

> **Kein Rechtsrat.** Dieses Dokument beschreibt, welche technischen
> Vorkehrungen die Software trifft und welche Angaben der Betreiber noch
> ergänzen muss. Die abschließende Bewertung gehört zum Datenschutzbeauftragten
> bzw. zur Rechtsabteilung.

---

## 1. Rollen

| Rolle | Wer |
|-------|-----|
| **Verantwortlicher** (Art. 4 Nr. 7) | Das betreibende Unternehmen |
| **Auftragsverarbeiter** | Keiner — die Anwendung läuft auf eigener Hardware bzw. in einer eigenen Containerumgebung |

Wird die Anwendung bei einem Hoster betrieben, entsteht ein
Auftragsverarbeitungsverhältnis: dann ist ein **AVV nach Art. 28** nötig.
Für die Auswahl des Hosters gilt Art. 28 Abs. 2 — insbesondere Standort
der Verarbeitung. Da die Anwendung selbst keine Verbindung zu
Drittanbietern aufbaut (keine Telemetrie, keine externen APIs), entsteht
**kein Drittlandtransfer** (Art. 44 ff.).

> Achtung bei der Weitergabe von Zugangsdaten an einen Dienstleister für
> Wartung — das begründet ein eigenes AV-Verhältnis.

---

## 2. Verzeichnis von Verarbeitungstätigkeiten (Art. 30)

| | |
|---|---|
| **Bezeichnung** | Gefahrgut-Transportberechnung und Beförderungspapiererstellung |
| **Zweck** | Berechnung der Freigrenze nach ADR 1.1.3.6, Erstellung und Archivierung von Beförderungspapieren |
| **Betroffene** | Ansprechpartner bei Kunden (Empfänger), Absenderadressen |
| **Datenkategorien** | Name bzw. Firma, Anschrift, Ansprechpartner, Telefon, E-Mail; bei Beschäftigten zusätzlich Benutzername und Anmeldedaten |
| **Empfänger** | Keine Weitergabe an Dritte |
| **Löschfristen** | siehe Abschnitt 5 |
| **TOM** | siehe Abschnitt 6 |

---

## 3. Rechtsgrundlagen (Art. 6)

| Verarbeitung | Grundlage |
|---|---|
| Transportberechnung, Beförderungspapier | Art. 6 Abs. 1 lit. b (Vertrag) bzw. lit. c i. V. m. Gefahrgutrecht |
| Aufbewahrung der Beförderungspapiere | Art. 6 Abs. 1 lit. c i. V. m. § 257 HGB, § 147 AO |
| Audit-Log (Nachweisbarkeit) | Art. 6 Abs. 1 lit. f (berechtigtes Interesse an Nachvollziehbarkeit) bzw. lit. c |
| Anmeldedaten der Beschäftigten | Art. 6 Abs. 1 lit. b; § 26 BDSG |
| IP-Adresse im Zugriffsprotokoll | Art. 6 Abs. 1 lit. f — **nur wenn tatsächlich erforderlich**; sonst abschalten (siehe Abschnitt 6) |

---

## 4. Wo personenbezogene Daten liegen

| Ort | Inhalt | Löschbar? |
|---|---|---|
| `customers`, `shipping_addresses` (SQLite) | Kundenstammdaten | Ja — über die Oberfläche (Administrator) |
| `shipments`, `shipment_items` | Sendungen und Positionen | Ja — über die Oberfläche (Administrator) |
| `audit_log` (SQLite) | Wer wann was geändert hat | Nur über die Aufbewahrungsfrist |
| `exports/*.pdf` (Volume) | Beförderungspapiere mit Empfängeranschrift | Ja — beim Löschen der Sendung automatisch |
| `users` (SQLite) | Benutzername, Passwort-Hash, Anmeldezeitpunkte | Deaktivieren statt löschen |
| Backup / Volume-Snapshot | Kopie aller oben genannten Daten | Über die Backup-Aufbewahrung |

**Passwörter** werden ausschließlich als scrypt-Hash gespeichert
(`N=32768, r=8, p=1`); Klartextpasswörter existieren nur im Moment der
Eingabe. Ein erzeugtes Anfangspasswort wird in eine nur für den Besitzer
lesbare Datei geschrieben und beim ersten Passwortwechsel gelöscht.

---

## 5. Löschkonzept

Zwei Pflichten stehen sich gegenüber und müssen **getrennt** behandelt
werden:

* **DSGVO** verlangt Löschung, sobald der Zweck entfällt (Art. 5 Abs. 1
  lit. e, Art. 17).
* **§ 257 HGB / § 147 AO** verlangen die Aufbewahrung bestimmter Unterlagen
  (6 bzw. 10 Jahre). Eine Löschung darf insoweit **nicht** erfolgen
  (Art. 17 Abs. 3 lit. b DSGVO).

Daraus folgt die Aufteilung:

| Datenkategorie | Frist | Begründung |
|---|---|---|
| Kundenstammdaten ohne Geschäftsvorgang | Löschung nach Wegfall des Zwecks | Art. 17 |
| Beförderungspapiere und Sendungsdaten | **10 Jahre** ab Ende des Kalenderjahres | § 257 HGB (Beginn 2025), § 147 AO |
| Audit-Log | Siehe unten — betreiberspezifisch | Nachweispflicht |
| Fehlversuche beim Anmelden | automatisch nach dem Sperrfenster | Art. 5 Abs. 1 lit. e |
| Backup-Kopien | Rotationsfrist des Backups | Art. 5 Abs. 1 lit. e |

### Aufbewahrungsfrist des Audit-Logs

Das Log enthält Benutzernamen und (falls aktiviert) IP-Adressen. Ohne
Frist wächst es unbegrenzt. Zwei Wege:

```bash
# Beim Start aufräumen (Frist in Tagen):
ADR_AUDIT_RETENTION_DAYS=3650

# Oder gezielt / per Cron:
docker exec adr-rechner python manage.py purge-audit --days 3650
```

**Wichtig:** Die Aufbewahrungsfrist wird gerade automatisch gelöscht — vor
dem Produktivbetrieb mit dem Datenschutzbeauftragten festlegen und in
diesem Dokument eintragen. Die Frist ist bewusst nicht im Code
festverdrahtet, weil sie von den Aufbewahrungspflichten des Betriebs
abhängt.

### Löschung in der Anwendung

* **Kunde löschen** — Administrator, über die Kundenliste. Nur möglich,
  wenn keine Sendungen mehr existieren.
* **Sendung löschen** — Administrator. Entfernt Positionen **und** die
  zugehörige PDF-Datei aus `exports/`.
* **Benutzer** — wird deaktiviert, nicht gelöscht: das Audit-Log verweist
  über den Benutzernamen auf das Konto, eine Zeilenlöschung würde diese
  Zuordnung zerstören. Deaktivieren sperrt den Zugang sofort.

> **Backups nicht vergessen.** Eine Löschung in der laufenden Datenbank
> lässt den Datensatz in älteren Sicherungen bestehen. Ohne dokumentierte
> Backup-Rotation ist die Löschung unvollständig.

---

## 6. Technische und organisatorische Maßnahmen (Art. 32)

### In der Anwendung umgesetzt

| Maßnahme | Umsetzung |
|---|---|
| Zugangskontrolle | Anmeldepflicht für alle Seiten; nur Anmeldung, Statik und `/healthz` sind offen |
| Passwortschutz | scrypt; Mindestlänge 12 Zeichen; Ausschluss gängiger Leak-Passwörter und des Benutzernamens |
| Erzwingung eigener Passwörter | Vom Administrator vergebene oder zurückgesetzte Passwörter müssen bei der ersten Anmeldung ersetzt werden |
| Schutz gegen Raten | Sperre nach 10 Fehlversuchen für 15 Minuten, gezählt über den Benutzernamen (IP-Wechsel hilft nicht) |
| Sitzungsschutz | HttpOnly, SameSite=Lax, `Secure` bei HTTPS, Sitzung endet mit dem Browser |
| Zugriffstrennung | Administratorrechte nur für Stammdatenpflege, Audit-Log und Löschvorgänge |
| Mandantentrennung | **Eine Instanz je Niederlassung** (siehe README) — keine gemeinsame Datenhaltung |
| Nachvollziehbarkeit | Audit-Log für Anmeldungen und Änderungen |
| Datenminimierung | Im Audit-Log werden bei Kundenänderungen nur die **Feldnamen** protokolliert, nicht die Werte |
| Löschung | Kunden, Sendungen und zugehörige PDF-Dateien sind löschbar |
| Betroffenenrechte | Datenauskunft als JSON je Kunde herunterladbar |
| Fehlkonfiguration | Ohne `ADR_ADMIN_PASSWORD` bei gesetztem `ADR_REQUIRE_ADMIN_PASSWORD=1` startet die Anwendung nicht |

### Vom Betreiber sicherzustellen

Diese Punkte liegen außerhalb der Anwendung:

| Maßnahme | Hinweis |
|---|---|
| **Transportverschlüsselung** | Reverse-Proxy mit TLS; `PREFER_SECURE_COOKIE=1` setzen |
| **Verschlüsselung ruhender Daten** | Die SQLite-Datei und die PDFs sind unverschlüsselt. Datenträger- bzw. Volume-Verschlüsselung (LUKS, BitLocker) oder verschlüsseltes Storage verwenden |
| **Backup** | Regelmäßig, verschlüsselt, mit dokumentierter Rotationsfrist; **Wiederherstellung testen** |
| **Netzwerkzugang** | Container nicht direkt exponieren (`127.0.0.1`), Zugriff über Reverse-Proxy |
| **Berechtigungen** | Eine Person, ein Konto. Keine geteilten Zugänge — sonst ist das Audit-Log nicht aussagekräftig |
| **Austritt** | Konto beim Ausscheiden deaktivieren (`manage.py` oder Benutzerverwaltung) |
| **Schwachstellen** | Regelmäßige Aktualisierung des Basisimages und der Abhängigkeiten |
| **Verzeichnis** | Abschnitt 2 in die Unterlagen des Betriebs übernehmen |
| **IP-Adresse** | Falls nicht erforderlich: `ADR_AUDIT_LOG_IP=0` |

---

## 7. Betroffenenrechte

| Recht | Umsetzung |
|---|---|
| **Art. 15 Auskunft** | Kundenliste → Symbol „Datenauskunft“ → JSON mit allen gespeicherten Daten. Der Vorgang wird protokolliert |
| **Art. 16 Berichtigung** | Kunde bearbeiten |
| **Art. 17 Löschung** | Sendungen löschen, dann Kunden löschen (Administrator). PDF-Dateien werden mitentfernt. **Backups beachten** |
| **Art. 18 Einschränkung** | Es gibt keine Sperrfunktion. Ersatzweise: Kunde nicht mehr verwenden und Löschung dokumentiert aufschieben |
| **Art. 20 Übertragbarkeit** | Dieselbe JSON-Ausgabe wie bei Art. 15 — maschinenlesbar |
| **Art. 21 Widerspruch** | Keine automatisierte Einzelentscheidung; Widerspruch betrifft nur die Verarbeitung auf Grundlage von lit. f |

Fehlende Sperrfunktion nach Art. 18 ist eine bewusste Auslassung:
in der Praxis wird in dieser Fallkonstellation meist ohnehin gelöscht oder
aufbewahrt. Wird eine echte Sperre gebraucht, ist sie nachzurüsten.

---

## 8. Verletzung des Schutzes (Art. 33/34)

Die Anwendung meldet keine Vorfälle. Auffälligkeiten sind manuell zu
prüfen:

```bash
docker exec adr-rechner python manage.py check
docker exec adr-rechner python manage.py list-users        # unerwartete Konten?
```

Im Betreiberbetrieb außerdem: `docker logs`, Zugriff auf das
Volume, Backup-Zugriffe, unerwartete Abgänge in `exports/`.

Meldung an die Aufsichtsbehörde innerhalb von 72 Stunden, wenn ein Risiko
für die Rechte der Betroffenen besteht. Frist beginnt mit der
Kenntnisnahme, nicht mit der Aufklärung.

---

## 9. Offene Punkte vor dem Produktivbetrieb

- [ ] Aufbewahrungsfrist für das Audit-Log festlegen und in Abschnitt 5 eintragen
- [ ] Backup-Konzept mit Fristen aufschreiben und Wiederherstellung testen
- [ ] Volume-Verschlüsselung einrichten
- [ ] TLS-Zugang einrichten und `PREFER_SECURE_COOKIE=1` setzen
- [ ] Entscheidung zu `ADR_AUDIT_LOG_IP`
- [ ] Konten je Niederlassung anlegen (eine Instanz je Standort)
- [ ] Verzeichnis nach Art. 30 in die Unterlagen des Betriebs übernehmen
- [ ] Datenschutzbeauftragten bzw. Rechtsabteilung einbeziehen
