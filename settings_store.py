"""
settings_store.py — Einstellungen in der Datenbank

Warum in der Datenbank und nicht in Umgebungsvariablen: Die Zugangsdaten
eines Mailservers sind Betriebsdaten des Kunden, nicht Teil der
Auslieferung. Steckten sie im Deployment, müsste für jede Änderung des
Postfachpassworts der Container neu erzeugt werden — und der Wert stünde in
`docker inspect`, also in jeder Prozess- und Konfigurationsauflistung.

Deshalb liegen sie in der Tabelle `settings` und werden von einem
Administrator in der Anwendung unter „Einstellungen" gepflegt. Daraus folgt
zweierlei:

  * `get_mail_settings()` liest die Werte bei jedem Zugriff neu — eine
    Änderung wirkt sofort, ohne Neustart.
  * `mail_public_settings()` gibt das Passwort **nie** zurück, sondern nur
    die Auskunft, ob eines hinterlegt ist.

Verschlüsselung ruhender Daten (seit v4.3)
──────────────────────────────────────────
Das Postfachpasswort liegt **nicht im Klartext** in der Datenbank, sondern
AES-verschlüsselt (Fernet, Authentifizierungs-Encryption). Der Schlüssel
wird aus SECRET_KEY abgeleitet (PBKDF-artige SHA-256-Ableitung) und liegt
somit ebenfalls außerhalb der Datenbank: Datenbankdatei und
Konfigurationsspeicher müssen beide erobert werden, um das Passwort zu
lesen. Konsequenzen:

  * Ohne gesetzten SECRET_KEY erzeugt die Anwendung beim Start einen
    Zufallsschlüssel — Sessions und Verschlüsselung überleben dann einen
    Neustart nicht. Darauf weist der Startwarnhinweis in app.py hin; im
    Produktivbetrieb gehört SECRET_KEY in die Umgebung (siehe README).
  * Bestehende Klartext-Einträge bleiben beim Lesen funktionsfähig
    (Migration ohne Ausfall) und werden bei der nächsten Speicherung oder
    durch `manage.py migrate-smtp-password` verschlüsselt.
  * Ein unverwendbarer Eintrag (falscher Schlüssel) liest als leer — der
    Mailversand ist dann „nicht eingerichtet", statt mit Fehlern zu
    laufen. Das Passwort wird neu hinterlegt, es gibt es nirgends wieder
    herzustellen.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken

import audit
from database import get_db

# Namen der Einstellungen für den Mailversand. Die Schlüssel sind bewusst
# stabil und englisch gehalten (Datenbank), die Beschriftung liefert die
# Oberfläche.
MAIL_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_starttls",
    "smtp_user",
    "smtp_password",
    "mail_from",
    "mail_app_name",
    "mail_app_url",
)

MAIL_DEFAULTS = {
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_starttls": "1",
    "smtp_user": "",
    "smtp_password": "",
    "mail_from": "",
    "mail_app_name": "ADR 1000-Punkte-Rechner",
    "mail_app_url": "",
}

# Werte, die nie ausgelesen werden dürfen.
SECRET_KEYS = {"smtp_password"}

# Präfix verschlüsselter Einträge. Fernet-Tokens beginnen mit gAAAA…; der
# eigene Präfix macht das Format auch bei künftigen Alterswechseln
# eindeutig (enc1 = Format 1).
_ENC_PREFIX = "enc1:"


# Ohne SECRET_KEY wird je Prozess ein Zufallsschlüssel erzeugt und im
# Speicher gehalten: innerhalb des laufenden Prozesses bleibt ein
# gespeichertes Passwort lesbar, nach einem Neustart nicht mehr — der
# Warnhinweis bei der Speicherung nennt genau das.
_FLUECHTIGER_SCHLUESSEL = ""


def _fernet() -> Fernet:
    """Fernet-Instanz aus SECRET_KEY ableiten (Schlüssel liegt nicht in der DB)."""
    global _FLUECHTIGER_SCHLUESSEL
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if not _FLUECHTIGER_SCHLUESSEL:
            _FLUECHTIGER_SCHLUESSEL = os.urandom(32).hex()
            print(
                "[warn] SMTP-Passwörter werden mit einem flüchtigen "
                "Schlüssel verschlüsselt — nach dem Neustart (ohne "
                "SECRET_KEY) sind sie nicht mehr lesbar. Bitte SECRET_KEY "
                "in der Umgebung setzen.",
                file=sys.stderr,
            )
        secret = _FLUECHTIGER_SCHLUESSEL
    ableitung = hashlib.sha256(
        ("adr-settings-v1:" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(ableitung))


def encrypt_secret(klartext: str) -> str:
    """Verschlüsselt ein Geheimnis für die Ablage in der Tabelle settings."""
    if not klartext:
        return ""
    return _ENC_PREFIX + _fernet().encrypt(
        klartext.encode("utf-8")).decode("ascii")


def decrypt_secret(gespeichert: str) -> str:
    """Liest ein verschlüsseltes Geheimnis zurück.

    Werte ohne Präfix stammen aus einer Zeit vor v4.3 — sie bleiben lesbar
    (Betrieb ohne Unterbrechung) und werden bei der nächsten Speicherung
    verschlüsselt. Ein nicht entschlüsselbarer Eintrag liest als leer:
    der Mailversand ist dann schlicht nicht eingerichtet.
    """
    if not gespeichert:
        return ""
    if not gespeichert.startswith(_ENC_PREFIX):
        print("[warn] Ein Passwort in der Einstelltabelle liegt unverschlüsselt "
              "vor (Bestand vor v4.3). Es wird bei der nächsten Speicherung "
              "verschlüsselt; sofort geht das mit "
              "`manage.py migrate-smtp-password`.",
              file=sys.stderr)
        return gespeichert
    try:
        return _fernet().decrypt(
            gespeichert[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, ValueError):
        print("[warn] Ein verschlüsseltes Passwort konnte nicht gelesen "
              "werden (anderer SECRET_KEY?). Der Eintrag wird als leer "
              "behandelt — bitte das Passwort neu hinterlegen.",
              file=sys.stderr)
        return ""


def smtp_password_is_encrypted() -> bool:
    """Bestandsprüfung für `manage.py migrate-smtp-password`."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'smtp_password'"
        ).fetchone()
    finally:
        conn.close()
    return bool(row and row["value"] and row["value"].startswith(_ENC_PREFIX))


def _rows_to_dicts(rows) -> dict:
    return {r["key"]: (r["value"] or "") for r in rows}


def get_settings() -> dict:
    """Alle Einstellungen als Dict (fehlende Werte = Standard).

    Geheimnisse kommen entschlüsselt zurück — die Aufrufer dieses Moduls
    brauchen das Passwort für den Versand, können aber nie sehen, wie es
    in der Datenbank liegt.
    """
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    werte = dict(MAIL_DEFAULTS)
    roh = _rows_to_dicts(rows)
    for key, wert in roh.items():
        if key in SECRET_KEYS:
            werte[key] = decrypt_secret(wert)
        else:
            werte[key] = wert
    return werte


def get_mail_settings() -> dict:
    """Einstellungen für den Mailversand (mit Standardwerten)."""
    alle = get_settings()
    return {k: alle.get(k, "") for k in MAIL_KEYS}


def mail_settings_configured() -> bool:
    """Wahr, wenn Server, Benutzer, Passwort und Absender gesetzt sind."""
    cfg = get_mail_settings()
    return bool(cfg["smtp_host"] and cfg["smtp_user"]
                and cfg["smtp_password"] and cfg["mail_from"])


def mail_public_settings() -> dict:
    """Einstellungen für die Oberfläche — ohne das Passwort.

    Stattdessen `password_set`: die Oberfläche zeigt „hinterlegt" bzw. den
    Hinweis, dass ein neues Passwort eingetragen werden kann.
    """
    cfg = get_mail_settings()
    cfg["password_set"] = bool(cfg.get("smtp_password"))
    cfg.pop("smtp_password", None)
    return cfg


def set_mail_settings(values: dict, actor: str = "",
                      clear_password: bool = False) -> dict:
    """Speichert die übergebenen Werte.

    Regeln:
      * Nur bekannte Schlüssel werden übernommen (kein Durchschreiben
        beliebiger Einträge).
      * Ein leer übergebenes Passwort lässt das gespeicherte stehen — die
        Oberfläche zeigt es nie an, es muss also erhalten bleiben können.
        Zum Entfernen gibt es `clear_password`.
      * Der Vorgang wird protokolliert, aber ohne Werte: das Audit-Log darf
        keine Zugangsdaten enthalten.
    """
    conn = get_db()
    try:
        jetzt = datetime.now().isoformat(timespec="seconds")
        geaendert = []

        # Ausdrückliches Löschen zuerst: die Oberfläche zeigt das Passwort
        # nie an, sie kann es also nur über diesen Weg entfernen. Der
        # Schlüssel ist dabei möglicherweise gar nicht im übergebenen Dict.
        if clear_password:
            for key in SECRET_KEYS:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at, updated_by) "
                    "VALUES (?, '', ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = '', updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by",
                    (key, jetzt, actor))
                geaendert.append(key)

        for key, wert in values.items():
            if key not in MAIL_KEYS:
                continue
            if key in SECRET_KEYS:
                if clear_password:
                    # Oben bereits geleert — ein späterer Wert darf das
                    # nicht wieder überschreiben.
                    continue
                if wert:
                    conn.execute(
                        "INSERT INTO settings (key, value, updated_at, updated_by) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                        "value = excluded.value, "
                        "updated_at = excluded.updated_at, "
                        "updated_by = excluded.updated_by",
                        (key, encrypt_secret(str(wert)), jetzt, actor))
                    geaendert.append(key)
                continue

            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (key, (wert or "").strip(), jetzt, actor))
            geaendert.append(key)
        conn.commit()
    finally:
        conn.close()

    if geaendert:
        # Nur die Feldnamen — niemals Werte, und das Passwort schon gar nicht.
        audit.log(audit.UPDATE, "settings", None,
                  "Einstellungen für den Mailversand geändert: "
                  + ", ".join(sorted(geaendert)))
    return get_mail_settings()
