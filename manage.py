#!/usr/bin/env python3
"""
manage.py — Verwaltungswerkzeug für den Betrieb

Die Anwendung selbst bietet bewusst keinen Weg, ein vergessenes
Administratorkonto zurückzusetzen: ein solcher Weg über das Web wäre ein
Angriffsziel. Ohne Ersatz wäre ein vergessener Administrator allerdings
eine dauerhafte Sperre — deshalb dieses Werkzeug. Es läuft ausschließlich
auf dem Server bzw. im Container und setzt Dateizugriff auf die
Datenbank voraus.

Aufruf (Docker):

    docker exec -it adr-rechner python manage.py list-users
    docker exec -it adr-rechner python manage.py reset-password admin
    docker exec -it adr-rechner python manage.py create-user muenchen01 --role user
    docker exec -it adr-rechner python manage.py unlock admin
    docker exec -it adr-rechner python manage.py purge-audit --days 3650

Ein zurückgesetztes Passwort muss bei der nächsten Anmeldung geändert
werden. Das Passwort wird nie ins Log geschrieben.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

import database
from database import get_db
import auth
import audit


def _fail(message: str, code: int = 1):
    print(f"Fehler: {message}", file=sys.stderr)
    sys.exit(code)


def _find_user(username: str):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE username = ?",
                            (username,)).fetchone()
    finally:
        conn.close()


def _read_new_password(username: str, vorgegeben: str = None) -> str:
    """Fragt das neue Passwort ab und prüft es gegen die Richtlinie."""
    if vorgegeben:
        problem = auth.validate_password(vorgegeben, username)
        if problem:
            _fail(problem)
        return vorgegeben

    while True:
        pw = getpass.getpass("Neues Passwort: ")
        problem = auth.validate_password(pw, username)
        if problem:
            print(f"  {problem}")
            continue
        wiederholung = getpass.getpass("Wiederholen: ")
        if pw != wiederholung:
            print("  Die Eingaben stimmen nicht überein.")
            continue
        return pw


def cmd_list_users(_args) -> int:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, username, role, active, must_change_password, "
            "last_login, created_at, created_by FROM users "
            "ORDER BY active DESC, username COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("Keine Benutzer vorhanden.")
        return 0

    print(f"{'ID':>4}  {'Benutzername':<24} {'Rolle':<8} {'Status':<22} "
          f"{'Letzte Anmeldung':<20}")
    print("-" * 84)
    for r in rows:
        if not r["active"]:
            status = "deaktiviert"
        elif r["must_change_password"]:
            status = "Passwortwechsel offen"
        else:
            status = "aktiv"
        print(f"{r['id']:>4}  {r['username']:<24} {r['role']:<8} {status:<22} "
              f"{(r['last_login'] or '—'):<20}")
    print(f"\n{len(rows)} Konto/Konten.")
    return 0


def cmd_reset_password(args) -> int:
    user = _find_user(args.username)
    if user is None:
        _fail(f"Benutzer „{args.username}” existiert nicht.")

    pw = args.password or secrets.token_urlsafe(12)
    if args.password:
        problem = auth.validate_password(args.password, args.username)
        if problem:
            _fail(problem)
        generated = False
    else:
        generated = True

    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, "
            "password_changed_at = ? WHERE id = ?",
            (generate_password_hash(pw),
             datetime.now().isoformat(timespec="seconds"), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    audit.log(audit.UPDATE, "user", user["id"],
              f"Passwort von „{args.username}” per manage.py zurückgesetzt",
              username="manage.py", user_id=0)

    print(f"Passwort für „{args.username}” wurde zurückgesetzt.")
    if generated:
        print(f"  Neues Passwort: {pw}")
        print("  Es wird nur jetzt angezeigt — bitte sofort weitergeben.")
    print("  Bei der nächsten Anmeldung muss ein eigenes Passwort gesetzt werden.")
    return 0


def cmd_create_user(args) -> int:
    if _find_user(args.username) is not None:
        _fail(f"Benutzer „{args.username}” existiert bereits.")

    pw = args.password or secrets.token_urlsafe(12)
    generated = args.password is None
    if args.password:
        problem = auth.validate_password(args.password, args.username)
        if problem:
            _fail(problem)

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, active, "
            "must_change_password, created_at, created_by) "
            "VALUES (?, ?, ?, 1, 1, ?, 'manage.py')",
            (args.username, generate_password_hash(pw), args.role,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Benutzer „{args.username}” mit Rolle {args.role} angelegt.")
    if generated:
        print(f"  Passwort: {pw}")
        print("  Es wird nur jetzt angezeigt.")
    print("  Bei der ersten Anmeldung muss ein eigenes Passwort gesetzt werden.")
    return 0


def cmd_unlock(args) -> int:
    """Hebt eine Sperre nach zu vielen Fehlversuchen auf."""
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM login_attempts WHERE username = ?",
                           (args.username.strip().lower(),))
        conn.commit()
        entfernt = cur.rowcount or 0
    finally:
        conn.close()

    if entfernt == 0:
        print(f"Für „{args.username}” liegen keine Fehlversuche vor.")
    else:
        print(f"{entfernt} Fehlversuch(e) für „{args.username}” entfernt — "
              f"die Anmeldung ist wieder möglich.")
    return 0


def cmd_purge_audit(args) -> int:
    """Löscht Audit-Einträge, die älter als die Aufbewahrungsfrist sind.

    Das Audit-Log enthält Benutzernamen und IP-Adressen. Ohne Frist wächst
    es unbegrenzt — das verstößt gegen den Grundsatz der
    Speicherbegrenzung (Art. 5 Abs. 1 lit. e DSGVO).
    """
    tage = args.days
    if tage <= 0:
        _fail("Die Aufbewahrungsfrist muss größer als 0 Tage sein.")

    conn = get_db()
    try:
        vorher = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()

    entfernt = audit.purge_old_entries(tage)

    conn = get_db()
    try:
        nachher = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()

    print(f"Aufbewahrungsfrist: {tage} Tage")
    print(f"  Einträge vorher : {vorher}")
    print(f"  gelöscht        : {entfernt}")
    print(f"  Einträge nachher: {nachher}")

    if entfernt:
        # Über die Löschung selbst wird nicht protokolliert: der Eintrag
        # würde sonst sofort wieder eine Zeile anlegen und bei einem
        # automatisierten Lauf den Bestand künstlich am Leben halten.
        pass
    return 0


def cmd_hash_password(args) -> int:
    """Gibt einen Hash aus — für Sonderfälle, keine Regelverwendung."""
    problem = auth.validate_password(args.password, "")
    if problem:
        _fail(problem)
    print(generate_password_hash(args.password))
    return 0


def cmd_check(_args) -> int:
    """Prüft, ob Zugang zur Datenbank besteht und wie der Bestand aussieht."""
    print(f"Datenverzeichnis: {database.DB_DIR}")
    print(f"Datenbankdatei  : {database.DB_PATH}")

    conn = get_db()
    try:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        aktive = conn.execute(
            "SELECT COUNT(*) FROM users WHERE active = 1").fetchone()[0]
        admins = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
        ).fetchone()[0]
        audit_rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        alt = conn.execute(
            "SELECT MIN(created_at) FROM audit_log").fetchone()[0]
    finally:
        conn.close()

    print(f"Benutzer gesamt : {users}")
    print(f"davon aktiv     : {aktive}")
    print(f"aktive Admins   : {admins}")
    print(f"Audit-Einträge  : {audit_rows}" + (f"  (ältester: {alt})" if alt else ""))

    if admins == 0:
        print("\nWARNUNG: Es gibt keinen aktiven Administrator. Die "
              "Verwaltung ist gesperrt.")
        print("         Behebung: python manage.py create-user <name> --role admin")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verwaltung des ADR 1000-Punkte-Rechners",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list-users", help="Konten auflisten")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("create-user", help="Konto anlegen")
    p.add_argument("username")
    p.add_argument("--role", choices=list(auth.VALID_ROLES), default="user")
    p.add_argument("--password", help="sonst wird eines erzeugt")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("reset-password",
                       help="Passwort setzen (auch für vergessene Zugänge)")
    p.add_argument("username")
    p.add_argument("--password", help="sonst wird eines erzeugt")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("unlock", help="Sperre nach Fehlversuchen aufheben")
    p.add_argument("username")
    p.set_defaults(func=cmd_unlock)

    p = sub.add_parser("purge-audit", help="alte Audit-Einträge löschen")
    p.add_argument("--days", type=int, default=3650,
                   help="Aufbewahrungsfrist in Tagen (Standard 3650 = 10 Jahre)")
    p.set_defaults(func=cmd_purge_audit)

    p = sub.add_parser("hash-password", help="Hash für Sonderfälle erzeugen")
    p.add_argument("password")
    p.set_defaults(func=cmd_hash_password)

    p = sub.add_parser("check", help="Zugang und Datenbestand prüfen")
    p.set_defaults(func=cmd_check)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    database.init_db()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
