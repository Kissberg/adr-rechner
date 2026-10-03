# Freigabe-Verfahren und Testprotokoll — Berechnungslogik ADR 1.1.3.6

Dieses Verfahren beschreibt, wie die Berechnungslogik des ADR
1000-Punkte-Rechners von der zuständigen Fachperson (betrieblicher
Gefahrgutbeauftragter) geprüft und formell freigegeben wird. Es begleitet
die Forderung aus dem IT-Review: *„Lassen Sie die Berechnungslogik formal
vom zuständigen Gefahrgutbeauftragten testen und freigeben."*

Ziel ist eine nachvollziehbare, wiederholbare Freigabe je Version und je
ADR-Datenstand — nicht eine einmalige Zusage.

Stand: v4.3.0 · Regelbasis: ADR 2025 (BAM, Datenbank GEFAHRGUT)

---

## 1. Rechtsrahmen und Geltungsbereich

* **Gegenstand der Freigabe:** die Freistellungsprüfung nach
  **ADR 1.1.3.6** (1000-Punkte-Regel) für lose bzw. verpackte Gefahrgüter,
  einschließlich der vier kumulativen Voraussetzungen nach 1.1.3.6.1
  (Punktzahl, Klassenbegrenzungen, Höchstmengen je Beförderungseinheit,
  Beförderungsart) und der Punkteermittlung nach **1.1.3.6.2** (Faktoren
  je Beförderungskategorie 0–4).
* **Nicht Gegenstand:** andere Freistellungsregime (z. B. 1.1.3.7
  Sondervorschrift 188 für Flüssigkeiten, begrenzte Mengen nach 3.4).
  Das Werkzeug ersetzt diese Prüfebenen **nicht** — die Freigabe sagt
  ausdrücklich nur über 1.1.3.6 etwas aus.
* **Datenbasis:** amtliche Datenbank GEFAHRGUT der Bundesanstalt für
  Materialforschung und -prüfung (BAM), Lizenz `dl-de/by-2-0`, je Import
  vermerkt und im Werkzeug ausgewiesen (Fußzeile, Ergebnis, Beförderungs-
  papier: „Regelbasis …, importiert am …").
* **Rechtserhebliche Änderung** an Regelengine (`adr_rules.py`),
  Datenimport oder Vorlagen des Beförderungspapiers setzt die Freigabe
  aus und erfordert eine erneute Prüfung nach diesem Protokoll.

## 2. Automatische Vorprüfung (als Nachweis beizubringen)

Vor der manuellen Prüfung bringt der Hersteller folgende Belege mit:

| Nachweis | Ort | Erwartung |
|---|---|---|
| Unit-Tests der Regelengine (inkl. Ausschlussklassen, Faktoren, Fail-safe) | `tests/test_adr_rules.py`, CI | ohne Fehler |
| Strukturprüfung des BAM-Imports | `bam_import.py`, ADR-Import-Seite | Prüfung bestanden, sonst Import abgelehnt |
| Verifikation des Datenbestands gegen das amtliche ADR-PDF (Tabelle A) | ADR-Import → Verifikation | keine ungeklärten Abweichungen |
| Prüfsummen der Vorschriftentexte 1.1.3.6 und 5.4.1.1 (Änderungsaufsicht) | `adr_section_scans` | keine Änderung seit letzter Freigabe |
| Sicherheits-CI (CodeQL, Semgrep, pip-audit) | GitHub Actions | grün |

## 3. Manuelle Prüffälle

Die erwarteten Ergebnisse stammen aus dem ADR-Text bzw. der BAM-Datei;
das Protokoll nennt die Stelle, unter der die Erwartung verifiziert wird.
Abweichungen sind vor der Freigabe zu klären.

| Nr. | Prüffall | Eingabe | Erwartung (Stelle) | Ergebnis / Datum |
|---|---|---|---|---|
| M-01 | Punktefaktoren je Kategorie | je eine UN der Kategorien 1–4 berechnen | Faktor 1 / 3 / 6; Kategorie 0 zählt 0, Kategorie 4 zählt nicht (ADR 1.1.3.6.2) | ☐ |
| M-02 | Grenze 1000 Punkte | Gesamtpunktzahl exakt 1000 und 1001 je Beförderungseinheit | 1000 → freigestellt, 1001 → nicht freigestellt (ADR 1.1.3.6.1) | ☐ |
| M-03 | Kumulation der Voraussetzungen | Punktzahl ≤ 1000, aber Höchstmenge je Beförderungseinheit überschritten | nicht freigestellt — Punktzahl allein genügt nicht (ADR 1.1.3.6.1) | ☐ |
| M-04 | Ausgeschlossene Klassen | Position der Klassen 1, 6.2 oder 7 | keine Freistellung nach 1.1.3.6, klare Meldung (ADR 1.1.3.6 Geltungsbereich) | ☐ |
| M-05 | Beförderungsart | identische Positionen als Paket und als Tank/Großcontainer | Freistellung nur für verpackte Beförderung (ADR 1.1.3.6) | ☐ |
| M-06 | Mehrdeutige Varianten | UN-Nummer mit mehreren Verpackungsgruppen ohne Auswahl | **keine** rechtsverbindliche Prüfung, Auswahl wird erzwungen (Fail-safe) | ☐ |
| M-07 | Höchstmengen je Beförderungseinheit | je Klasse die in der BAM-Datei geführte Höchstmenge | Grenzwert aus Tabelle/Fußnoten wird vollständig berücksichtigt (ADR 1.1.3.6.1 / BAM GEFAHRGUT) | ☐ |
| M-08 | Umweltgefährdende Güter | UN 3077 / UN 3082 | korrekte Punkteermittlung, Vermerk „UMWELTGEFÄHRDEND" auf dem Papier (ADR 5.4.1) | ☐ |
| M-09 | Beförderungspapier | gespeicherte Sendung als PDF erzeugen | Pflichtangaben nach **5.4.1.1** (a)–(i) vollständig, Tunnelcode in Klammern, Regelbasis ausgewiesen | ☐ |
| M-10 | Datenstand-Nachvollziehbarkeit | Berechnung vor und nach einem BAM-Import vergleichen | Ergebnis/Papier nennt Version und Importdatum; gespeicherte Sendungen behalten ihren Stand | ☐ |
| M-11 | Sprach-/Anwenderfehler | leere Mengen, Buchstaben als Menge, unbekannte UN-Nummer | verständliche Fehlermeldung, keine Berechnung mit stillschweigenden Annahmen | ☐ |

## 4. Bewertung und Freigabe

* **Freigabe** gilt für die geprüfte Werkzeugversion (APP_VERSION) und den
  geprüften Datenstand. Beide sind im Freigabe-Schreiben anzugeben; das
  Werkzeug zeigt beide an (Fußzeile, `/healthz`, Beförderungspapier).
* **Bedingte Freigabe** mit Auflagen ist zulässig; Auflagen werden hier
  mit Frist festgehalten.
* Nach jedem **BAM-Import eines neuen ADR-Datenstands** wird mindestens
  M-01 bis M-05 und M-10 wiederholt (Kurzfreigabe); nach Änderungen an
  `adr_rules.py` oder der PDF-Vorlage immer das volle Protokoll.
* Das Werkzeug ist ein **Rechen- und Prüfwerkzeug** und ersetzt nicht die
  Prüfpflichten des Absenders; die Freigabe dokumentiert die
  Vertrauenswürdigkeit der Berechnung, nicht ein Haftungsinstrument
  (Haftungsausschluss der MIT-Lizenz bleibt unberührt — die
  Konzernseite trägt die organisatorische Verantwortung durch dieses
  Verfahren).

## 5. Prüfungsvermerk

```
Werkzeugversion   : ____________________   (APP_VERSION, z. B. 4.3.0)
Regelbasis        : ____________________   (z. B. ADR 2025, importiert am …)
Geprüfte Fälle    : ☐ M-01 … M-11 vollständig   Anzahl Abweichungen: ____
Ergebnis          : ☐ freigegeben    ☐ bedingt freigegeben    ☐ abgelehnt
Auflagen/Fristen  : ______________________________________________

Ort, Datum        : ____________________
Name              : ____________________
Funktion          : Gefahrgutbeauftragte(r)
Unterschrift      : ____________________
```
