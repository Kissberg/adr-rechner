# Code-Audit — Vorgehensweise und Befunde

Dieses Dokument beschreibt, wie die Sicherheit des ADR
1000-Punkte-Rechners geprüft wird (SAST, DAST, Abhängigkeiten) und ordnet
die technischen Maßnahmen den OWASP Top 10 (2021) zu. Es ist Teil der
Antwort auf die Anforderung „Code-Audits (SAST/DAST) und die
Vorgehensweise vorlegen".

Stand: v4.3.0

---

## 1. Grundsätze

1. **Jede Änderung wird gescannt.** Die GitHub-Workflows (ci.yml,
   security.yml) laufen bei jedem Push und jedem Pull-Request auf `main`
   sowie wöchentlich automatisch — auch ohne Änderungen, damit neue
   Erkennungsregeln greifen.
2. **Findings werden nicht versteckt.** Statische Befunde (CodeQL,
   Semgrep) sind als Build-Fehler sichtbar; die Dependency-Prüfung
   (pip-audit) blockiert den Build hart. Der dynamische Basisscan (ZAP)
   liefert einen Report-Artefakt, dessen Warnungen einzeln bewertet
   werden (siehe Abschnitt 5).
3. **Reproduzierbarkeit.** Alle Prüfungen lassen sich lokal mit denselben
   Befehlen ausführen (Abschnitt 6) — ein externer Prüfer kann jeden
   Befund nachvollziehen.
4. **Abhängigkeiten exakt gepinnt.** `requirements.txt` fest jede Version
   mit `==` ein; Dependabot schlägt Updates als Pull-Request vor, der
   Upgrade wird reviewt und gescannt.

## 2. Statische Analyse (SAST)

| Werkzeug | Rolle | Regelbasis | Gate |
|---|---|---|---|
| CodeQL (GitHub) | Datenflussanalyse | `security-extended` | Build-Fehler bei Befunden (Security-Tab) |
| Semgrep | Musterregelprüfung | `p/python`, `p/owasp-top-ten`, `p/secrets`; ausgewertet werden Warnung und Fehler (Warnstufe INFO dient als Report, nicht als Blocker), `tests/` ausgenommen | `--error` bricht den Build ab |
| pip-audit | bekannte CVEs in Abhängigkeiten | PyPI-Advisory-DB | hartes Tor in ci.yml, wöchentlich |

Anlaufstellen: `.github/workflows/security.yml` (CodeQL, Semgrep, ZAP),
`.github/workflows/ci.yml` (Tests + pip-audit),
`.github/dependabot.yml` (Update-Vorschläge wöchentlich).

## 3. Dynamische Analyse (DAST)

ZAP-Basisscan gegen die in CI gebaute Container-Instanz
(`security.yml`, Job `zap-baseline`):

* Die Anwendung läuft im Produktivimage — gescannt wird, was
  ausgeliefert wird, nicht der Entwicklungsstand.
* Der Scan läuft **ohne Anmeldung** (`AUTH_ENABLED=0`), damit alle
  Seiten und APIs erreicht werden. Die Zugriffskontrolle selbst ist
  Gegenstand der Unit-Tests (401/403 auf allen geschützten Routen,
  Rollentrennung, Kontosperrung) und der statischen Analyse.
* Der Report wird als CI-Artefakt abgelegt (`zap-baseline-report`);
  Warnungen werden je Release bewertet und — wenn sie zutreffen — als
  Issue aufgenommen. Ein authentifizierter Scan (ZAP-Authentifizierung
  über den Anmelde-Flow) ist vorgesehen und im Security-Workflow
  nachziehbar, sobald der Konzern-Zielbetrieb feststeht.

## 4. Zuordnung zu den OWASP Top 10 (2021)

| OWASP-Risiko | Maßnahmen in dieser Anwendung |
|---|---|
| A01 Broken Access Control | Anmeldepflicht für **alle** Routen zentral in `app.py` (`_require_login`); Rollenprüfung per Dekorator (`auth.role_required`) und `deny_unless_admin()` (fail closed); API ohne Anmeldung → 401, ohne Rolle → 403; Kopplung von Berechnung und Speicherung (`POST /calculate mode=save`), damit Prüfergebnis und Dokument nicht auseinanderlaufen |
| A02 Cryptographic Failures | Passwörter als scrypt-Hash (Werkzeug, automatisch salted); Postfachpasswort AES-verschlüsselt in der Datenbank (Fernet, Schlüssel aus `SECRET_KEY` abgeleitet, liegt außerhalb der DB — siehe `settings_store.py`); TLS wird über Reverse Proxy geführt, `SESSION_COOKIE_SECURE` per `PREFER_SECURE_COOKIE=1` |
| A03 Injection | SQL ausschließlich parameterisiert (Platzhalter `?`; die wenigen f-String-SQLs interpolieren nur intern gebildete Klauseln, Werte gebunden); Templates nutzen Jinja2-Autoescaping, `|safe` wird nicht verwendet; **CSRF-Token** auf allen ändernden Requests (Formularfeld + `X-CSRF-Token`, konstanter Zeitvergleich, Rotation bei Anmeldung — siehe `csrf.py`, `static/js/csrf.js`) |
| A04 Insecure Design | Berechnungslogik isoliert und testbar (`adr_rules.py`, 160+ Unit-Tests); Fail-safe: mehrdeutige Varianten führen zu einer **Ablehnung** statt zu einer Schätzung; ADR-Import mit Strukturprüfung und unabhängiger Verifikation gegen das amtliche PDF; Regelbasis wird je Berechnung/Sendung festgehalten und im Dokument ausgewiesen |
| A05 Security Misconfiguration | Container läuft unprivilegiert (uid 10001); Debug aus; Gunicorn hinter Reverse Proxy; Bootstrap-Konto `admin/admin` nur bis zur erzwungenen Erstanmeldung und per `ADR_BOOTSTRAP_ADMIN=0` abschaltbar (Konzernbetrieb mit SSO setzt genau das); kein Secret im Image oder im Audit-Log |
| A06 Vulnerable Components | pip-audit als hartes Tor, Dependabot wöchentlich, exakte Pins |
| A07 Identification & Authentication Failures | Kontosperrung nach Fehlversuchen (benutzerbezogen, umgeht keine IP-Wechsel); Passwortrichtlinie an BSI TR-02102-1 / NIST SP 800-63B angelehnt (Länge statt Zeichenklassen, Sperrliste, kein Benutzername); erzwungener Passwortwechsel bei fremdvergebenen Passwörtern; optionale **SSO-Anbindung (OIDC / Microsoft Entra ID)** mit zentraler Rollenableitung und Offboarding-Wirkung (`oidc_auth.py`) |
| A08 Software & Data Integrity | Uploads nur mit erlaubten Endungen, Größenlimit (50 MB), BAM-Import mit Prüfung vor dem Schreiben; ADR-PDF-Vorschriftenabschnitte per Prüfsumme überwacht (`adr_section_scans`) |
| A09 Logging & Monitoring | Append-only Audit-Log für Anmeldungen, Berechnungen, Stammdatenänderungen, Exporte und Löschungen; keine Zugangsdaten im Log; Aufbewahrungsfrist konfigurierbar (`ADR_AUDIT_RETENTION_DAYS`, `manage.py purge-audit`) |
| A10 SSRF | Keine serverseitigen Abrufe von Anwender-URLs — der einzige externe Abruf ist der OIDC-Discovery-Call gegen den konfigurierten Issuer |

## 5. Bekannte Befunde und ihre Bewertung

* **ZAP-Warnungen auf der Anmeldeseite** (z. B. „Content Security Policy
  Header Not Set"): Bootstrap und eigene Skripte laden von CDN;
  eine CSP erfordert Nonce-/Hash-Pflege in allen Templates. Bewertet als
  akzeptiertes Restrisiko für den Intranetbetrieb hinter Reverse Proxy;
  im Konzernkontext empfiehlt sich die CSP auf Proxy-Ebene.
* **Kein authentifizierter DAST-Scan** in CI (siehe Abschnitt 3) —
  dokumentiert, nicht verschwiegen.
* Fehlende CSRF-Tokens vor v4.3 sind in v4.3 geschlossen; Bestands-
  installationen aktualisieren mit `git pull` + Container-Rebuild.

## 6. Lokale Ausführung (Reproduzierbarkeit)

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Unit-Tests (inkl. Zugriffs- und CSRF-Tests)
python -m pytest tests/ -v

# Bekannte Schwachstellen in Abhängigkeiten
pip-audit --requirement requirements.txt --strict

# Statische Musterprüfung (Regelsets wie in security.yml)
semgrep scan --config p/python --config p/owasp-top-ten \
             --config p/secrets --metrics=off \
             --exclude tests --severity WARNING --severity ERROR

# Dynamischer Basisscan gegen eine lokale Instanz (Linux/macOS;
# --network host, damit der ZAP-Container die Anwendung erreicht)
python app.py &                                   # 127.0.0.1:5050
docker run --rm --network host -v "$PWD:/zap/wrk:rw" \
  ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t http://127.0.0.1:5050 -I -r zap_report.html
```

## 7. Verantwortlichkeiten

* **Autor**: pflegt Scans, reagiert auf Findings (Fix oder dokumentierte
  Bewertung in Abschnitt 5), hält dieses Dokument stand.
* **Betreibende Instanz** (Konzern): bestätigt Umgebung (TLS, Reverse
  Proxy, `SECRET_KEY`, `PREFER_SECURE_COOKIE=1`, `ADR_BOOTSTRAP_ADMIN=0`,
  OIDC-Konfiguration) und nimmt die formale Freigabe der
  Berechnungslogik vor — technisch unterstützt durch die Testsuite
  (`adr_rules.py`) und die Verifikation der Stammdaten gegen das
  amtliche ADR-PDF.
