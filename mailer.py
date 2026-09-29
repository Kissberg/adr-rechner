"""
mailer.py — Versand von Anfangspasswörtern (optional)

Zweck: Wird beim Anlegen eines Kontos kein Startpasswort vorgegeben, erzeugt
die Anwendung eines und kann es der betroffenen Person per E-Mail zustellen.
Das ist ein **optionaler Zusatzweg**: ohne eingerichteten Versand wird das
Passwort einmalig im Bildschirm angezeigt.

────────────────────────────────────────────────────────────────────────
Woher die Zugangsdaten kommen
────────────────────────────────────────────────────────────────────────
Nicht aus Umgebungsvariablen, sondern aus der Tabelle `settings` —
eingetragen von einem Administrator unter „Einstellungen" (siehe
`settings_store.py`). Vorteile: kein Postfachpasswort in `docker inspect`
oder in der Prozessliste, Änderungen wirken ohne Neustart, und eine
Auslieferung bringt keine fremden Zugangsdaten mit.

Ist nichts hinterlegt (`mail_settings_configured()` ist falsch), gilt der
Versand als nicht eingerichtet. Dann wird **gar nicht** versucht zuzustellen
und es entsteht auch keine Fehlermeldung: der Aufrufer zeigt das Passwort
einfach an.

────────────────────────────────────────────────────────────────────────
WICHTIG — das Passwort darf nicht ins Log
────────────────────────────────────────────────────────────────────────
Logs sind breiter lesbar und länger verfügbar als die Anwendung selbst
(`docker logs`, json-Dateien, zentrale Logsammler). Diese Modul schreibt
weder Passwort noch Nachrichteninhalt in eine Ausgabe und gibt bei Fehlern
nur den technischen Grund zurück.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from settings_store import get_mail_settings, mail_settings_configured

# Zeitlimit je Verbindungsaufbau: ein hängender Mailserver darf die
# Weboberfläche nicht blockieren.
SMTP_TIMEOUT_SECONDS = 15


def smtp_config() -> dict:
    """Aktuelle Versandkonfiguration aus der Datenbank."""
    cfg = get_mail_settings()
    try:
        port = int(cfg.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    starttls = (cfg.get("smtp_starttls") or "1").strip().lower() \
        not in ("0", "false", "no")
    return {
        "host": (cfg.get("smtp_host") or "").strip(),
        "port": port,
        "user": (cfg.get("smtp_user") or "").strip(),
        "password": cfg.get("smtp_password") or "",
        "mail_from": (cfg.get("mail_from") or "").strip()
        or (cfg.get("smtp_user") or "").strip(),
        "app_name": (cfg.get("mail_app_name") or "").strip()
        or "ADR 1000-Punkte-Rechner",
        "app_url": (cfg.get("mail_app_url") or "").strip(),
        "starttls": starttls,
    }


def smtp_configured() -> bool:
    """Wahr, wenn Server, Benutzer, Passwort und Absender hinterlegt sind."""
    return mail_settings_configured()


def _connect(cfg: dict):
    """Verbindet und meldet sich an. Der Aufrufer schließt die Verbindung."""
    if cfg["starttls"]:
        smtp = smtplib.SMTP(cfg["host"], cfg["port"],
                            timeout=SMTP_TIMEOUT_SECONDS)
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
    else:
        smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                                timeout=SMTP_TIMEOUT_SECONDS,
                                context=ssl.create_default_context())
    smtp.login(cfg["user"], cfg["password"])
    return smtp


def test_connection() -> tuple:
    """Prüft die Zugangsdaten, **ohne** eine Nachricht zu senden.

    Rückgabe (ok, Meldung). Die Meldung ist für die Oberfläche gedacht und
    enthält niemals Zugangsdaten.
    """
    cfg = smtp_config()
    if not smtp_configured():
        return False, ("Es sind nicht alle Angaben hinterlegt (Server, "
                       "Port, Benutzer, Passwort, Absender).")
    try:
        smtp = _connect(cfg)
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            pass
    except smtplib.SMTPAuthenticationError:
        return False, ("Der Mailserver hat Benutzer oder Passwort abgelehnt. "
                       "Bitte die Zugangsdaten prüfen.")
    except (smtplib.SMTPException, OSError) as exc:
        return False, (f"Keine Verbindung zu {cfg['host']}:"
                       f"{cfg['port']} — {exc.__class__.__name__}")
    return True, f"Verbindung zu {cfg['host']}:{cfg['port']} erfolgreich."


def _body(cfg: dict, username: str, password: str, role: str,
          reason: str, actor: str) -> str:
    rolle = "Administrator" if role == "admin" else "Benutzer"
    einleitung = (
        "für Sie wurde ein Zugang zum " + cfg["app_name"] + " angelegt."
        if reason == "neu" else
        "Ihr Passwort für den " + cfg["app_name"] + " wurde zurückgesetzt."
    )
    zeilen = [
        "Guten Tag,",
        "",
        einleitung,
        "",
        f"Benutzername    : {username}",
        f"Anfangspasswort : {password}",
        f"Rolle           : {rolle}",
    ]
    if cfg["app_url"]:
        zeilen.append(f"Anmeldung       : {cfg['app_url']}")
    zeilen += [
        "",
        "Das Anfangspasswort ist nur ein Übergangswert. Bei der ersten",
        "Anmeldung müssen Sie ein eigenes Passwort vergeben (mindestens 12",
        "Zeichen, es darf den Benutzernamen nicht enthalten und darf nicht",
        "in gängigen Passwortlisten stehen). Bis dahin ist die Nutzung",
        "nicht personenbezogen.",
        "",
        "Bitte löschen Sie diese E-Mail, sobald Sie sich angemeldet haben —",
        "sie enthält ein Zugangsgeheimnis.",
        "",
        "Mit freundlichen Grüßen",
    ]
    if actor:
        zeilen.append(f"{actor} — {cfg['app_name']}")
    else:
        zeilen.append(cfg["app_name"])
    return "\n".join(zeilen)


def send_initial_password(to_email: str, username: str, password: str,
                          role: str = "user", reason: str = "neu",
                          actor: str = "") -> tuple:
    """Sendet das Anfangspasswort an `to_email`.

    Gibt (gesendet: bool, grund: str|None) zurück. Es wird niemals eine
    Ausnahme nach außen gegeben und niemals der Nachrichteninhalt
    protokolliert: ein Fehlschlag darf die Kontoeinrichtung nicht
    verhindern — der Administrator erhält stattdessen das Passwort
    einmalig im Bildschirm angezeigt.
    """
    if not to_email:
        return False, "Keine E-Mail-Adresse hinterlegt."
    if not smtp_configured():
        return False, "E-Mail-Versand ist nicht eingerichtet."

    cfg = smtp_config()
    msg = EmailMessage()
    msg["Subject"] = f"Ihr Zugang zum {cfg['app_name']}"
    msg["From"] = cfg["mail_from"]
    msg["To"] = to_email
    msg.set_content(_body(cfg, username, password, role, reason, actor))

    try:
        smtp = _connect(cfg)
        try:
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass
    except smtplib.SMTPRecipientsRefused:
        return False, f"Die Adresse {to_email} wurde vom Mailserver abgelehnt."
    except smtplib.SMTPAuthenticationError:
        return False, ("Anmeldung am Mailserver fehlgeschlagen — bitte die "
                       "Zugangsdaten unter Einstellungen prüfen.")
    except (smtplib.SMTPException, OSError) as exc:
        # Nur die technische Ursache nennen, nie den Nachrichteninhalt.
        return False, f"Versand fehlgeschlagen: {exc.__class__.__name__}"
    return True, None
