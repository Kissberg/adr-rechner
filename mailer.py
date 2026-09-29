"""
mailer.py — E-Mail-Versand für Anfangspasswörter

Zweck: Beim Anlegen eines Kontos wird das Anfangspasswort der betroffenen
Person per E-Mail zugestellt, statt es am Bildschirm des Administrators
auszugeben. Das Passwort ist ein Übergangswert: es muss bei der ersten
Anmeldung ersetzt werden (must_change_password), damit der Administrator
kein dauerhaft gültiges fremdes Passwort kennt.

────────────────────────────────────────────────────────────────────────
WICHTIG — das Passwort darf nicht ins Log
────────────────────────────────────────────────────────────────────────
Logs sind breiter lesbar und länger verfügbar als die Anwendung selbst
(`docker logs`, json-Dateien, zentrale Logsammler). Ein Anfangspasswort im
Log wäre ein für alle Logleser sichtbarer Zugang. Diese Modul schreibt das
Passwort deshalb niemals in eine Ausgabe und gibt bei Fehlern nur den
technischen Grund zurück — nie den Inhalt der Nachricht.

────────────────────────────────────────────────────────────────────────
Konfiguration (Umgebungsvariablen)
────────────────────────────────────────────────────────────────────────
  ADR_SMTP_HOST      Standard: smtp.ionos.de
  ADR_SMTP_PORT      Standard: 587 (STARTTLS)
  ADR_SMTP_USER      Benutzername des Postfachs — ohne ihn gilt der
                     Versand als NICHT konfiguriert
  ADR_SMTP_PASSWORD  Postfachpasswort
  ADR_MAIL_FROM      Absenderadresse, Standard: ADR_SMTP_USER
  ADR_MAIL_APP_NAME  Name in Betreff und Signatur
  ADR_MAIL_APP_URL   Adresse der Anwendung für den Anmeldelink (optional)
  ADR_SMTP_STARTTLS  Standard 1; auf 0 setzen für implizites TLS (Port 465)

Ist der Versand nicht konfiguriert, fällt die Anwendung auf das bisherige
Verhalten zurück: das erzeugte Passwort wird einmalig im Bildschirm
angezeigt, damit ein Konto auch ohne Postfach angelegt werden kann.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

DEFAULT_SMTP_HOST = "smtp.ionos.de"
DEFAULT_SMTP_PORT = 587
DEFAULT_APP_NAME = "ADR 1000-Punkte-Rechner"

# Zeitlimit je Verbindungsaufbau: ein hängender Mailserver darf die
# Weboberfläche nicht blockieren.
SMTP_TIMEOUT_SECONDS = 15


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def smtp_config() -> dict:
    """Liest die Versandkonfiguration aus der Umgebung."""
    starttls = _env("ADR_SMTP_STARTTLS", "1").lower() not in ("0", "false", "no")
    return {
        "host": _env("ADR_SMTP_HOST", DEFAULT_SMTP_HOST),
        "port": int(_env("ADR_SMTP_PORT", str(DEFAULT_SMTP_PORT)) or DEFAULT_SMTP_PORT),
        "user": _env("ADR_SMTP_USER"),
        "password": os.environ.get("ADR_SMTP_PASSWORD") or "",
        "mail_from": _env("ADR_MAIL_FROM") or _env("ADR_SMTP_USER"),
        "app_name": _env("ADR_MAIL_APP_NAME", DEFAULT_APP_NAME),
        "app_url": _env("ADR_MAIL_APP_URL"),
        "starttls": starttls,
    }


def smtp_configured() -> bool:
    """Wahr, wenn Benutzer und Passwort für den Versand gesetzt sind."""
    cfg = smtp_config()
    return bool(cfg["user"] and cfg["password"] and cfg["mail_from"])


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
        return False, "E-Mail-Versand ist nicht konfiguriert."

    cfg = smtp_config()
    msg = EmailMessage()
    msg["Subject"] = f"Ihr Zugang zum {cfg['app_name']}"
    msg["From"] = cfg["mail_from"]
    msg["To"] = to_email
    msg.set_content(_body(cfg, username, password, role, reason, actor))

    try:
        if cfg["starttls"]:
            with smtplib.SMTP(cfg["host"], cfg["port"],
                              timeout=SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(cfg["user"], cfg["password"])
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                                  timeout=SMTP_TIMEOUT_SECONDS,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(cfg["user"], cfg["password"])
                smtp.send_message(msg)
    except smtplib.SMTPRecipientsRefused:
        return False, f"Die Adresse {to_email} wurde vom Mailserver abgelehnt."
    except smtplib.SMTPAuthenticationError:
        return False, ("Anmeldung am Mailserver fehlgeschlagen — bitte "
                       "ADR_SMTP_USER und ADR_SMTP_PASSWORD prüfen.")
    except (smtplib.SMTPException, OSError) as exc:
        # Nur die technische Ursache nennen, nie den Nachrichteninhalt.
        return False, f"Versand fehlgeschlagen: {exc.__class__.__name__}"
    return True, None
