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
"""

from __future__ import annotations

from datetime import datetime

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


def _rows_to_dicts(rows) -> dict:
    return {r["key"]: (r["value"] or "") for r in rows}


def get_settings() -> dict:
    """Alle Einstellungen als Dict (fehlende Werte = Standard)."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    werte = dict(MAIL_DEFAULTS)
    werte.update(_rows_to_dicts(rows))
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
                        (key, wert, jetzt, actor))
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
