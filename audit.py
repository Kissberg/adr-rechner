"""
audit.py — Änderungsprotokoll (Audit-Log)

Protokolliert alle sicherheits- und compliance-relevanten Operationen:
An- und Abmeldung, Änderungen an Stammdaten (UN-Datenbank, Kunden,
Adressen), ADR-Importe sowie das Erstellen und Löschen von Sendungen.

Damit werden die Anforderungen aus DSGVO Art. 30 (Verzeichnis von
Verarbeitungstätigkeiten) und die innerbetriebliche Nachweispflicht
erfüllt: Es ist jederzeit rekonstruierbar, wer wann welche Daten geändert hat.

Hinweis (GoBD): Das Audit-Log ist append-only. Es gibt bewusst keine
API zum Löschen einzelner Einträge; bereinigt wird ausschließlich über
die Aufbewahrungsfrist in `purge_old_entries()`.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from flask import request
from database import get_db

# Aktionen
LOGIN = "login"
LOGOUT = "logout"
CREATE = "create"
UPDATE = "update"
DELETE = "delete"
IMPORT = "import"
CALCULATE = "calculate"
EXPORT = "export"

ACTION_LABELS = {
    LOGIN: "Anmeldung",
    LOGOUT: "Abmeldung",
    CREATE: "Angelegt",
    UPDATE: "Geändert",
    DELETE: "Gelöscht",
    IMPORT: "Import",
    CALCULATE: "Berechnung",
    EXPORT: "Export",
}


def _log_ip_enabled() -> bool:
    """Ob die Client-IP mitgeschrieben wird.

    Die IP-Adresse ist personenbezogen (Art. 4 Nr. 1 DSGVO). Für die
    Nachvollziehbarkeit von Änderungen ist sie nicht erforderlich — sie
    hilft nur bei der Aufklärung von Missbrauch. Wer darauf verzichten
    kann, setzt ADR_AUDIT_LOG_IP=0 und reduziert damit den gespeicherten
    Personenbezug auf das Nötige (Art. 5 Abs. 1 lit. c).
    """
    return os.environ.get("ADR_AUDIT_LOG_IP", "1").strip().lower() \
        not in ("0", "false", "no")


def _client_ip() -> str:
    """Ermittelt die Client-IP (ohne Vertrauen in X-Forwarded-For)."""
    if not _log_ip_enabled():
        return ""
    return request.remote_addr or "" if request else ""


def log(
    action: str,
    entity: str,
    entity_id: Optional[int] = None,
    detail: str = "",
    username: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """
    Schreibt einen Eintrag in das Audit-Log. Fehler beim Schreiben werden
    unterdrückt — ein fehlgeschlagenes Logging darf die Fachoperation
    nicht blockieren, wird aber auf stderr gemeldet.
    """
    from flask import has_request_context, session

    if username is None and has_request_context():
        username = session.get("username")
    if user_id is None and has_request_context():
        user_id = session.get("user_id")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO audit_log (created_at, user_id, username, action, "
            "entity, entity_id, detail, ip_address) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                user_id,
                username,
                action,
                entity,
                entity_id,
                (detail or "")[:1000],
                _client_ip(),
            ),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover
        import sys
        print(f"[audit] Schreiben fehlgeschlagen: {exc}", file=sys.stderr)
    finally:
        conn.close()


def diff_text(before: dict, after: dict, fields: tuple) -> str:
    """Erzeugt eine lesbare Änderungsbeschreibung ('feld: alt → neu').

    Nur für Felder ohne Personenbezug verwenden (z. B. Rolle, Aktivstatus).
    Für personenbezogene Stammdaten gehört `changed_fields` hierher — sonst
    entsteht im Audit-Log eine zweite, dauerhafte Kopie der Daten.
    """
    parts = []
    for f in fields:
        old = before.get(f)
        new = after.get(f)
        if str(old) != str(new):
            parts.append(f"{f}: {old!r} → {new!r}")
    return "; ".join(parts)


def changed_fields(before: dict, after: dict, fields: tuple) -> str:
    """Nennt nur die geänderten Feldnamen — ohne die Werte selbst.

    Das Audit-Log soll belegen, *dass* und *wer* etwas geändert hat, nicht
    die Daten selbst ein zweites Mal speichern. Ein Mitschreiben der Werte
    hätte zwei Folgen:

      1. Das Log wird zur Kopie der Kundenstammdaten und unterläuft damit
         die Datenminimierung (Art. 5 Abs. 1 lit. c DSGVO).
      2. Eine Löschung nach Art. 17 DSGVO bliebe wirkungslos — Name,
         Ansprechpartner, Telefon und E-Mail stünden weiterhin im Log.

    Beispiel: „geändert: contact, phone, email“.
    """
    geaendert = [f for f in fields if str(before.get(f)) != str(after.get(f))]
    return ", ".join(geaendert)


def purge_old_entries(days: int = 3650) -> int:
    """Löscht Einträge älter als `days` Tage (Standard: 10 Jahre)."""
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM audit_log "
            "WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()
