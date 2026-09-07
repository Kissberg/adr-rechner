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


def _client_ip() -> str:
    """Ermittelt die Client-IP (ohne Vertrauen in X-Forwarded-For)."""
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
    """Erzeugt eine lesbare Änderungsbeschreibung ('feld: alt → neu')."""
    parts = []
    for f in fields:
        old = before.get(f)
        new = after.get(f)
        if str(old) != str(new):
            parts.append(f"{f}: {old!r} → {new!r}")
    return "; ".join(parts)


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
