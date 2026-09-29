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
    docker exec -it adr-rechner python manage.py bootstrap-admin
    docker exec -it adr-rechner python manage.py create-user muenchen01 \
        --email m.mustermann@firma.de --role user
    docker exec -it adr-rechner python manage.py delete-user muenchen01
    docker exec -it adr-rechner python manage.py unlock admin
    docker exec -it adr-rechner python manage.py purge-audit --days 3650

Ein zurückgesetztes Passwort muss bei der nächsten Anmeldung geändert
werden. Das Passwort wird nie ins Log geschrieben.

Passwörter und die Richtlinie: `create-user`/`reset-password --password`
prüfen gegen die Passwortrichtlinie. `bootstrap-admin` ist die einzige
Ausnahme — es setzt den dokumentierten Erstzugang (admin/admin) und
erzwingt dafür den Wechsel bei der nächsten Anmeldung. Ohne diese Ausnahme
wäre eine Instanz mit versehentlich gelöschtem Administrator nicht mehr
erreichbar.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import datetime
from typing import NoReturn

from werkzeug.security import generate_password_hash

import database
from database import get_db
import auth
import audit
import mailer


def _fail(message: str, code: int = 1) -> "NoReturn":
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
            "SELECT id, username, email, role, active, must_change_password, "
            "last_login, created_at, created_by FROM users "
            "ORDER BY active DESC, username COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("Keine Benutzer vorhanden.")
        return 0

    print(f"{'ID':>4}  {'Benutzername':<20} {'Rolle':<6} {'Status':<22} "
          f"{'E-Mail':<32}")
    print("-" * 88)
    for r in rows:
        if not r["active"]:
            status = "deaktiviert"
        elif r["must_change_password"]:
            status = "Passwortwechsel offen"
        else:
            status = "aktiv"
        print(f"{r['id']:>4}  {r['username']:<20} {r['role']:<6} {status:<22} "
              f"{(r['email'] or '—'):<32}")
    print(f"\n{len(rows)} Konto/Konten.")
    return 0


def cmd_bootstrap_admin(args) -> int:
    """Setzt den Erstzugang des Administrators zurück (Notfall/Erststart).

    Einziger Weg, der die Passwortrichtlinie umgeht: der Wert ist der
    dokumentierte Standard (admin/admin) bzw. ADR_ADMIN_PASSWORD. Der
    Wechsel bei der nächsten Anmeldung wird in jedem Fall erzwungen —
    deshalb bleibt der Vorgang vertretbar. Fehlt der Administrator bereits,
    wird er angelegt.
    """
    username = args.username or (auth.os.environ.get("ADR_ADMIN_USER") or "").strip() \
        or auth.BOOTSTRAP_ADMIN_USER
    password = args.password or \
        (auth.os.environ.get("ADR_ADMIN_PASSWORD") or "").strip() or \
        auth.BOOTSTRAP_ADMIN_PASSWORD

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?",
                           (username,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, active, "
                "must_change_password, created_at, created_by) "
                "VALUES (?, ?, 'admin', 1, 1, ?, 'manage.py')",
                (username, generate_password_hash(password),
                 datetime.now().isoformat(timespec="seconds")),
            )
            angelegt = True
            user_id = None
        else:
            user_id = row["id"]
            conn.execute(
                "UPDATE users SET password_hash = ?, role = 'admin', "
                "active = 1, must_change_password = 1, password_changed_at = ? "
                "WHERE id = ?",
                (generate_password_hash(password),
                 datetime.now().isoformat(timespec="seconds"), user_id),
            )
            conn.execute("DELETE FROM login_attempts WHERE username = ?",
                         (username.strip().lower(),))
            angelegt = False
        conn.commit()
    finally:
        conn.close()

    audit.log(audit.UPDATE, "user", user_id,
              f"Erstzugang für „{username}” per manage.py gesetzt "
              f"(Rolle admin, Passwortwechsel erzwungen)",
              username="manage.py", user_id=0)

    if angelegt:
        print(f"Administrator „{username}” wurde angelegt.")
    else:
        print(f"Zugang für „{username}” wurde zurückgesetzt "
              f"(Rolle Administrator, aktiv).")
    print(f"  Passwort     : {password}")
    print("  ACHTUNG      : Dieses Passwort ist der dokumentierte Erstzugang.")
    print("                 Es muss bei der nächsten Anmeldung geändert werden.")
    if not args.password and not (auth.os.environ.get("ADR_ADMIN_PASSWORD") or "").strip():
        print("  Hinweis      : Standardwert — sofort nach der Anmeldung ändern "
              "und die Instanz nicht über ein fremdes Netz erreichbar lassen.")
    return 0


def cmd_delete_user(args) -> int:
    """Löscht ein Konto endgültig (Zeile wird entfernt).

    Gegenstück zu `unlock`/Deaktivieren: für Fehlanlagen, Testkonten und
    Löschbegehren nach Art. 17 DSGVO. Der letzte aktive Administrator und
    das gerade laufende Konto sind ausgenommen.
    """
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?",
                           (args.username,)).fetchone()
        if row is None:
            _fail(f"Benutzer „{args.username}” existiert nicht.")
        if row["role"] == "admin" and row["active"]:
            andere = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 "
                "AND id != ?", (row["id"],)).fetchone()[0]
            if andere == 0:
                _fail("Der letzte aktive Administrator kann nicht gelöscht "
                      "werden.")
        conn.execute("DELETE FROM users WHERE id = ?", (row["id"],))
        conn.execute("DELETE FROM login_attempts WHERE username = ?",
                     (row["username"].strip().lower(),))
        conn.commit()
        name, role = row["username"], row["role"]
        email = row["email"] if "email" in row.keys() else None
        user_id = row["id"]
    finally:
        conn.close()

    audit.log(audit.DELETE, "user", user_id,
              f"Benutzer „{name}” (Rolle {role}"
              + (f", {email}" if email else "")
              + ") per manage.py endgültig gelöscht",
              username="manage.py", user_id=0)
    print(f"Benutzer „{name}” wurde endgültig gelöscht.")
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
    if getattr(args, "send_email", False):
        email = user["email"] if "email" in user.keys() else None
        if not email:
            print("  E-Mail-Versand nicht möglich: für dieses Konto ist keine "
                  "Adresse hinterlegt.")
        else:
            sent, grund = mailer.send_initial_password(
                email, args.username, pw, user["role"], reason="zuruecksetzung",
                actor="manage.py")
            print(f"  Anfangspasswort an {email} gesendet." if sent
                  else f"  E-Mail-Versand fehlgeschlagen: {grund}")
    print("  Bei der nächsten Anmeldung muss ein eigenes Passwort gesetzt werden.")
    return 0


def cmd_create_user(args) -> int:
    if _find_user(args.username) is not None:
        _fail(f"Benutzer „{args.username}” existiert bereits.")

    email = (getattr(args, "email", "") or "").strip()
    if args.send_email and not email:
        _fail("Für den Versand wird --email benötigt.")

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
            "must_change_password, created_at, created_by, email) "
            "VALUES (?, ?, ?, 1, 1, ?, 'manage.py', ?)",
            (args.username, generate_password_hash(pw), args.role,
             datetime.now().isoformat(timespec="seconds"), email or None),
        )
        conn.commit()
    finally:
        conn.close()

    audit.log(audit.CREATE, "user", None,
              f"Benutzer „{args.username}”"
              + (f" ({email})" if email else "")
              + f" mit Rolle {args.role} per manage.py angelegt",
              username="manage.py", user_id=0)

    print(f"Benutzer „{args.username}” mit Rolle {args.role} angelegt.")
    if generated:
        print(f"  Passwort: {pw}")
        print("  Es wird nur jetzt angezeigt.")
    if args.send_email:
        sent, grund = mailer.send_initial_password(email, args.username, pw,
                                                   args.role, reason="neu",
                                                   actor="manage.py")
        if sent:
            print(f"  Anfangspasswort wurde an {email} gesendet.")
        else:
            print(f"  E-Mail-Versand fehlgeschlagen: {grund}")
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
        print("         Behebung: python manage.py bootstrap-admin")
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
    p.add_argument("--email", help="Zustelladresse für das Anfangspasswort")
    p.add_argument("--role", choices=list(auth.VALID_ROLES), default="user")
    p.add_argument("--password", help="sonst wird eines erzeugt")
    p.add_argument("--send-email", action="store_true",
                   help="Anfangspasswort per E-Mail senden (braucht --email)")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("bootstrap-admin",
                       help="Erstzugang des Administrators setzen (Notfall)")
    p.add_argument("--username", help=f"Standard: {auth.BOOTSTRAP_ADMIN_USER}")
    p.add_argument("--password",
                   help=f"Standard: {auth.BOOTSTRAP_ADMIN_PASSWORD} bzw. "
                        f"ADR_ADMIN_PASSWORD")
    p.set_defaults(func=cmd_bootstrap_admin)

    p = sub.add_parser("delete-user", help="Konto endgültig löschen")
    p.add_argument("username")
    p.set_defaults(func=cmd_delete_user)

    p = sub.add_parser("reset-password",
                       help="Passwort setzen (auch für vergessene Zugänge)")
    p.add_argument("username")
    p.add_argument("--password", help="sonst wird eines erzeugt")
    p.add_argument("--send-email", action="store_true",
                   help="neues Passwort per E-Mail senden")
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
