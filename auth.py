"""
auth.py — Authentifizierung und Autorisierung

Einfaches, sessionsbasiertes Rollenmodell. Die Anmeldung ist standardmäßig
aktiviert (AUTH_ENABLED=1). Für lokale Entwicklung kann sie über
AUTH_ENABLED=0 abgeschaltet werden — im Produktivbetrieb ist das NICHT
zulässig (siehe README, Abschnitt Sicherheit).

Rollen
  admin  — volle Rechte, inkl. UN-Datenbank, ADR-Import, Benutzerverwaltung
  user   — Berechnung, Beförderungspapiere, Kunden- und Adressverwaltung

Der erste Administrator wird beim Start angelegt, sofern kein Benutzer
existiert: Benutzername `admin`, Passwort `admin` (oder ADR_ADMIN_USER /
ADR_ADMIN_PASSWORD, wenn gesetzt). Dieses Standardpasswort ist bewusst fest
und öffentlich dokumentiert — eine Erstinstallation braucht damit keine
vorherige Zugangsdatenverteilung.

────────────────────────────────────────────────────────────────────────
WICHTIG — warum `admin`/`admin` vertretbar ist und wo die Grenze liegt
────────────────────────────────────────────────────────────────────────
Ein festes Standardpasswort ist nur deshalb kein Sicherheitsloch, weil es
nur bis zur ersten Anmeldung gilt: das Konto wird mit
`must_change_password = 1` angelegt, und die Anwendung erzwingt den Wechsel
beim ersten Anmelden (siehe app.py, `_require_login`) — vorher ist kein
anderer Endpunkt erreichbar, auch keine API. Der Zustand „Passwortwechsel
offen" ist in der Benutzerverwaltung sichtbar.

Voraussetzung dafür ist, dass der Standard NICHT dauerhaft gültig bleibt:
Die Anwendung darf die Erstinstallation nie ohne erzwungenen Wechsel
ausliefern. Auf einem Netz, das nicht ausschließlich aus vertrauenswürdigen
Rechnern besteht, gehört die Instanz zusätzlich hinter TLS (siehe README,
Abschnitt Sicherheit) — sonst liest der erste Zugriff im Netz das
Anfangspasswort mit.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from typing import Optional

from flask import Blueprint, flash, redirect, render_template, request, \
    session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# `database` als Modul importieren, nicht nur DB_DIR als Wert: der Pfad des
# Datenverzeichnisses kann zur Laufzeit abweichen (Tests, anderer Mount),
# und eine beim Import kopierte Konstante würde das nicht mitbekommen.
import database
import audit
import mailer
import settings_store
from database import get_db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
VALID_ROLES = (ROLE_ADMIN, ROLE_USER)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Benutzerverwaltung liegt bewusst auf einem eigenen Blueprint ohne Präfix:
# die JSON-Schnittstelle gehört unter /api/users, nicht unter /auth/api/users.
users_bp = Blueprint("users", __name__)

# ─────────────────────────────────────────────────────────────────────
# Erstzugang
# ─────────────────────────────────────────────────────────────────────
# Standardwerte für den ersten Administrator. Sie sind bewusst fest und
# stehen im Handbuch; das Konto entsteht mit erzwungenem Passwortwechsel
# und ist bis dahin auf die Passwortseite beschränkt.
BOOTSTRAP_ADMIN_USER = "admin"
BOOTSTRAP_ADMIN_PASSWORD = "admin"

# ─────────────────────────────────────────────────────────────────────
# Passwortrichtlinie
# ─────────────────────────────────────────────────────────────────────
# Bewusst längenorientiert statt komplexitätsorientiert: BSI (TR-02102-1)
# und NIST (SP 800-63B) empfehlen beide Länge als wirksames Kriterium und
# raten von erzwungenen Zeichenklassen ab — sie führen nachweislich zu
# „Passwort1!"-Mustern und Zetteln am Monitor. Was tatsächlich hilft, ist
# eine Mindestlänge, der Ausschluss des Benutzernamens und eine Sperrliste
# der häufigsten Passwörter.
PASSWORD_MIN_LENGTH = int(os.environ.get("ADR_PASSWORD_MIN_LENGTH", "12"))

# Nur für den unwahrscheinlichen Fall, dass ein Betrieb kürzere Passwörter
# zulassen will — dann aber bewusst und dokumentiert.
if PASSWORD_MIN_LENGTH < 8:
    PASSWORD_MIN_LENGTH = 8

# Häufigste Passwörter (Auszug gängiger Leak-Listen). Reine Längenprüfung
# würde „passwort1234" durchlassen.
_WEAK_PASSWORDS = {
    "passwort", "password", "passwort123", "passwort1234", "password123",
    "passwort2024", "passwort2025", "passwort2026", "adr2025", "adr2026",
    "administrator", "admin123", "admin1234", "willkommen", "willkommen1",
    "qwertzuiop", "qwerty123456", "1234567890", "12345678901", "123456789012",
    "geheim123456", "iloveyou123", "sommer2026", "Winter2026!", "letmein123",
}


def validate_password(password: str, username: str = "") -> Optional[str]:
    """Prüft ein neues Passwort. Gibt eine Fehlermeldung zurück oder None.

    Wird an allen Stellen verwendet, an denen ein Passwort gesetzt wird —
    auch beim Zurücksetzen durch einen Administrator und beim Anlegen des
    ersten Kontos. Ein ungeprüfter Pfad wäre eine Hintertür an der
    Richtlinie vorbei.
    """
    if not password:
        return "Das Passwort darf nicht leer sein."
    if len(password) < PASSWORD_MIN_LENGTH:
        return (f"Das Passwort muss mindestens {PASSWORD_MIN_LENGTH} Zeichen "
                f"lang sein.")
    if password.lower() in _WEAK_PASSWORDS:
        return ("Dieses Passwort steht in gängigen Leak-Listen. Bitte ein "
                "anderes wählen.")
    if username and username.lower() in password.lower():
        return "Das Passwort darf den Benutzernamen nicht enthalten."
    if len(set(password)) < 5:
        return ("Das Passwort ist zu gleichförmig. Bitte mehr unterschiedliche "
                "Zeichen verwenden.")
    return None


# ─────────────────────────────────────────────────────────────────────
# Schutz gegen Passwortraten
# ─────────────────────────────────────────────────────────────────────
MAX_LOGIN_ATTEMPTS = int(os.environ.get("ADR_MAX_LOGIN_ATTEMPTS", "10"))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("ADR_LOGIN_LOCKOUT_MINUTES", "15"))


def count_recent_failures(username: str, ip: Optional[str] = None) -> int:
    """Fehlversuche für diesen Benutzernamen im Sperrfenster.

    Gezählt wird über den Benutzernamen (nicht die IP), damit ein Angreifer
    das Limit nicht durch Wechsel der Quell-IP umgeht. Die IP wird zusätzlich
    gespeichert, um den Vorfall später auswerten zu können.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM login_attempts "
            "WHERE username = ? AND attempted_at >= datetime('now', ?)",
            (username.strip().lower(), f"-{LOGIN_LOCKOUT_MINUTES} minutes"),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def is_locked_out(username: str) -> bool:
    return count_recent_failures(username) >= MAX_LOGIN_ATTEMPTS


def record_failed_attempt(username: str, ip: Optional[str] = None) -> int:
    """Vermerkt einen Fehlversuch und räumt alte Einträge gleich mit auf."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO login_attempts (username, ip_address) VALUES (?, ?)",
            (username.strip().lower(), ip or ""),
        )
        # Alles außerhalb des Sperrfensters ist für die Zählung irrelevant
        # und wird entfernt, damit die Tabelle nicht unbegrenzt wächst.
        conn.execute(
            "DELETE FROM login_attempts WHERE attempted_at < datetime('now', ?)",
            (f"-{max(LOGIN_LOCKOUT_MINUTES, 1) * 24} minutes",),
        )
        conn.commit()
    finally:
        conn.close()
    return count_recent_failures(username)


def clear_failed_attempts(username: str) -> None:
    """Nach erfolgreicher Anmeldung den Zähler zurücksetzen."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM login_attempts WHERE username = ?",
                     (username.strip().lower(),))
        conn.commit()
    finally:
        conn.close()


def auth_enabled() -> bool:
    """Gibt zurück, ob die Anmeldepflicht aktiv ist."""
    return os.environ.get("AUTH_ENABLED", "1").strip().lower() not in ("0", "false", "no")


def bootstrap_admin_enabled() -> bool:
    """Ob der dokumentierte Erstzugang automatisch angelegt werden darf.

    Im Konzernbetrieb wird er üblicherweise abgeschaltet
    (ADR_BOOTSTRAP_ADMIN=0): dort stellt der Identitätsanbieter (SSO)
    oder `manage.py bootstrap-admin` den ersten Zugang — ein Konto mit
    einem öffentlich dokumentierten Passwort wäre ein Prüfungsbefund,
    selbst wenn der Wechsel erzwungen würde.
    """
    return os.environ.get("ADR_BOOTSTRAP_ADMIN", "1").strip().lower() \
        not in ("0", "false", "no")


# ─────────────────────────────────────────────────────────────────────
# Benutzerverwaltung
# ─────────────────────────────────────────────────────────────────────
#
# Hinweis zur Ablösung: bis v4.0 erzeugte die Anwendung bei fehlendem
# ADR_ADMIN_PASSWORD ein Zufallspasswort und legte es in einer Datei
# `.admin_password` (0600) im Datenverzeichnis ab. Seit v4.1 gibt es
# stattdessen einen festen, dokumentierten Erstzugang (admin/admin) mit
# erzwungenem Wechsel — damit entfällt die Passwortdatei samt
# Rechte-Absicherung (icacls/chmod) ersatzlos. Wer den Erstzugang
# vorbelegen will, setzt ADR_ADMIN_PASSWORD.

def ensure_default_admin() -> None:
    """
    Legt den ersten Administrator an, sofern noch kein Benutzer existiert.

    Benutzername/Passwort: ADR_ADMIN_USER / ADR_ADMIN_PASSWORD, sonst
    „admin" / „admin" (BOOTSTRAP_ADMIN_*). Das Konto entsteht in jedem Fall
    mit must_change_password = 1 — der Wechsel wird bei der ersten
    Anmeldung erzwungen (siehe app.py, `_require_login`), bis dahin ist
    kein anderer Endpunkt erreichbar. Die Passwortrichtlinie greift hier
    bewusst nicht: „admin" wäre nach ihr zu kurz, und genau dafür ist der
    erzwungene Wechsel der Ausgleich.

    Wichtig bei mehreren Gunicorn-Workern: Alle Worker führen diesen Code
    beim Import aus. Ein normales "erst prüfen, dann einfügen" kann daher
    eine Race-Condition haben (beide Worker sehen eine leere Tabelle, der
    zweite INSERT schlägt fehl und der Worker stürzt ab). INSERT OR IGNORE
    nutzt den UNIQUE-Constraint als Schutz — der verlierende Worker erkennt
    an rowcount == 0, dass ein anderer Worker den Benutzer angelegt hat.

    Ein bereits vorhandener Benutzer wird niemals überschrieben.

    Mit ADR_BOOTSTRAP_ADMIN=0 entfällt dieser Automatismus vollständig —
    für den Konzernbetrieb mit SSO (siehe oidc_auth.py) oder wenn der
    erste Zugang bewusst über `manage.py bootstrap-admin` erfolgen soll.
    """
    if not bootstrap_admin_enabled():
        print("[bootstrap] ADR_BOOTSTRAP_ADMIN=0 — kein automatischer "
              "Erstzugang. Ersten Zugang über SSO oder "
              "`manage.py bootstrap-admin` anlegen.")
        return

    username = (os.environ.get("ADR_ADMIN_USER") or "").strip() \
        or BOOTSTRAP_ADMIN_USER
    password = (os.environ.get("ADR_ADMIN_PASSWORD") or "").strip()
    from_environment = bool(password)
    if not password:
        password = BOOTSTRAP_ADMIN_PASSWORD

    conn = get_db()
    try:
        # Nur eine leere Benutzertabelle bekommt den Erstzugang. Ein
        # „admin”, der nach einer Umbenennung oder Löschung bei jedem Start
        # wieder auftaucht, wäre ein dauerhaftes Einfallstor — für diesen
        # Fall gibt es `manage.py bootstrap-admin`.
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, active, "
            "must_change_password, created_at, created_by) "
            "VALUES (?, ?, ?, 1, 1, ?, ?)",
            (username, generate_password_hash(password), ROLE_ADMIN,
             datetime.now().isoformat(timespec="seconds"),
             "Umgebungsvariable" if from_environment else "Standard"),
        )
        conn.commit()
        created = (cur.rowcount or 0) > 0
    finally:
        conn.close()

    if not created:
        # Paralleler Worker war schneller.
        return

    print("=" * 72)
    print("  ERSTER ADMINISTRATOR WURDE ANGELEGT")
    print(f"  Benutzername : {username}")
    if from_environment:
        # Wert aus der Umgebung — ein Geheimnis, das nicht ins Log gehört.
        print("  Passwort     : aus ADR_ADMIN_PASSWORD gesetzt (nicht im Log)")
    else:
        # Dokumentierter Erstzugang; gilt nur bis zur ersten Anmeldung.
        print(f"  Passwort     : {password}")
    print("  ACHTUNG      : Der Passwortwechsel wird bei der ersten Anmeldung "
          "erzwungen.")
    print("=" * 72)


def verify_credentials(username: str, password: str) -> Optional[sqlite3.Row]:
    """Prüft Benutzername und Passwort, gibt den Datensatz oder None zurück."""
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()

    if user is None:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None

    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    return user


# ─────────────────────────────────────────────────────────────────────
# Decorator
# ─────────────────────────────────────────────────────────────────────

def current_user() -> Optional[dict]:
    """Gibt den angemeldeten Benutzer als Dict zurück (oder None)."""
    if not auth_enabled():
        return {"id": 0, "username": "local", "role": ROLE_ADMIN}
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1",
                           (uid,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def login_required(func):
    """Erzwingt eine gültige Anmeldung."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            if request.accept_mimetypes.best == "application/json" or \
                    request.path.startswith("/api/"):
                from flask import jsonify
                return jsonify({"error": "Nicht angemeldet"}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return func(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """Erzwingt eine der angegebenen Rollen."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                from flask import jsonify
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Nicht angemeldet"}), 401
                return redirect(url_for("auth.login", next=request.full_path))
            if user.get("role") not in roles:
                from flask import jsonify, abort
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Keine Berechtigung"}), 403
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────
# Routen
# ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Anmeldeseite."""
    if not auth_enabled():
        session["user_id"] = 0
        session["username"] = "local"
        return redirect(url_for("index"))

    from oidc_auth import local_login_enabled
    if request.method == "POST" and not local_login_enabled():
        # SSO-only-Betrieb: das Passwortformular ist gar nicht erst
        # sichtbar — ein direkt adressierter POST ist dann kein
        # Anmeldeweg mehr und wird wie eine nicht vorhandene Seite
        # behandelt.
        from flask import abort
        abort(404)

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        # Vor der Prüfung sperren: sonst wären die letzten Versuche bis zum
        # Limit noch verwertbar.
        if username and is_locked_out(username):
            error = ("Zu viele Fehlversuche. Das Konto ist für "
                     f"{LOGIN_LOCKOUT_MINUTES} Minuten gesperrt.")
        else:
            user = verify_credentials(username, password)
            if user is None:
                # Die Meldung nennt bewusst nicht, ob der Benutzername
                # existiert — sonst lassen sich gültige Konten aufzählen.
                error = "Benutzername oder Passwort ungültig."
                if username:
                    record_failed_attempt(username, request.remote_addr)
            else:
                clear_failed_attempts(username)
                session.clear()
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["role"] = user["role"]
                session.permanent = False
                # Von einem Administrator vergebenes Passwort: erst ändern,
                # dann weiterarbeiten.
                if user["must_change_password"]:
                    return redirect(url_for("auth.password_page", next="1"))
                nxt = request.args.get("next") or url_for("index")
                # Offene Redirects verhindern
                if not nxt.startswith("/"):
                    nxt = url_for("index")
                return redirect(nxt)

    return render_template("login.html", title="Anmeldung", error=error)


@auth_bp.route("/logout")
def logout():
    """Abmeldung."""
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/passwort-aendern")
@login_required
def password_page():
    """Seite zum Ändern des eigenen Passworts."""
    user = current_user()
    return render_template(
        "password_change.html",
        title="Passwort ändern",
        forced=bool(user and user["must_change_password"]),
    )


@auth_bp.route("/password", methods=["POST"])
@login_required
def change_password():
    """Passwort des angemeldeten Benutzers ändern."""
    from flask import jsonify
    user = current_user()
    data = request.get_json(force=True, silent=True) or {}
    old = data.get("old_password", "")
    new = data.get("new_password", "")
    confirm = data.get("confirm_password", "")

    problem = validate_password(new, user["username"])
    if problem:
        return jsonify({"error": problem}), 400
    if confirm and new != confirm:
        return jsonify({"error": "Die Passwortwiederholung stimmt nicht überein."}), 400

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404
        if not check_password_hash(row["password_hash"], old):
            return jsonify({"error": "Aktuelles Passwort ist falsch."}), 403
        if check_password_hash(row["password_hash"], new):
            return jsonify({"error": "Das neue Passwort entspricht dem "
                                     "bisherigen. Bitte ein anderes wählen."}), 400
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, "
            "password_changed_at = ? WHERE id = ?",
            (generate_password_hash(new),
             datetime.now().isoformat(timespec="seconds"), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    session["must_change_password"] = False

    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────
# Benutzerverwaltung (nur Administratoren)
# ─────────────────────────────────────────────────────────────────────
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")

# Bewusst grob: die endgültige Gültigkeit einer Adresse kann nur der
# Mailserver feststellen. Geprüft wird, was ohne DNS-Zugriff entscheidbar
# ist — genau ein @, keine Leerzeichen, ein Punkt in der Domain.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _validate_email(email: str) -> Optional[str]:
    """Gibt eine Fehlermeldung zurück oder None."""
    if not email:
        return ("Bitte eine E-Mail-Adresse angeben — dorthin wird das "
                "Anfangspasswort gesendet.")
    if len(email) > 200:
        return "Die E-Mail-Adresse ist zu lang (höchstens 200 Zeichen)."
    if not EMAIL_PATTERN.match(email):
        return f"„{email}” ist keine gültige E-Mail-Adresse."
    return None


def _user_public_dict(row) -> dict:
    """Benutzerdatensatz ohne Passwort-Hash für die Ausgabe."""
    return {
        "id": row["id"],
        "username": row["username"],
        # row["email"] fehlt in alten Datenbanken bis zum ersten Start nach
        # dem Upgrade — sqlite3.Row wirft dann IndexError statt None.
        "email": (row["email"] if "email" in row.keys() else None),
        "role": row["role"],
        "active": bool(row["active"]),
        "must_change_password": bool(row["must_change_password"]),
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "last_login": row["last_login"],
        "password_changed_at": row["password_changed_at"],
        "is_self": False,
    }


def _active_admin_count(exclude_id: Optional[int] = None) -> int:
    conn = get_db()
    try:
        sql = ("SELECT COUNT(*) FROM users WHERE role = ? AND active = 1")
        params = [ROLE_ADMIN]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


@users_bp.route("/benutzer")
@login_required
@role_required(ROLE_ADMIN)
def users_page():
    """Benutzerverwaltung."""
    return render_template("benutzer.html", title="Benutzerverwaltung",
                           mail_configured=mailer.smtp_configured())


# ─────────────────────────────────────────────────────────────────────
# Einstellungen (nur Administratoren)
# ─────────────────────────────────────────────────────────────────────
#
# Der Mailversand ist optional und wird hier eingerichtet — nicht über
# Umgebungsvariablen. Damit bringt eine Auslieferung keine fremden
# Zugangsdaten mit, und der Betrieb kann ein Postfach wechseln, ohne den
# Container neu zu erzeugen.

def _mail_settings_input(data: dict) -> tuple:
    """Liest und prüft die Eingaben. Rückgabe (werte, fehler)."""
    werte = {
        "smtp_host": (data.get("smtp_host") or "").strip(),
        "smtp_port": (str(data.get("smtp_port") or "587")).strip(),
        "smtp_starttls": "1" if data.get("smtp_starttls", True) else "0",
        "smtp_user": (data.get("smtp_user") or "").strip(),
        "mail_from": (data.get("mail_from") or "").strip(),
        "mail_app_name": (data.get("mail_app_name") or "").strip(),
        "mail_app_url": (data.get("mail_app_url") or "").strip(),
    }
    if data.get("smtp_password"):
        werte["smtp_password"] = data["smtp_password"]

    # Port: eine unbrauchbare Angabe darf nicht gespeichert werden — sonst
    # schlägt jeder Versand mit einem kryptischen Fehler fehl.
    try:
        port = int(werte["smtp_port"])
    except ValueError:
        return werte, "Der Port muss eine Zahl sein."
    if not 1 <= port <= 65535:
        return werte, "Der Port muss zwischen 1 und 65535 liegen."
    if werte["mail_from"] and not EMAIL_PATTERN.match(werte["mail_from"]):
        return werte, f"„{werte['mail_from']}” ist keine gültige Absenderadresse."
    if werte["mail_app_url"] and not werte["mail_app_url"].startswith(("http://", "https://")):
        return werte, "Die Adresse der Anwendung muss mit http:// oder https:// beginnen."
    return werte, None


@users_bp.route("/einstellungen")
@login_required
@role_required(ROLE_ADMIN)
def settings_page():
    """Seite für die Betriebseinstellungen."""
    return render_template("einstellungen.html", title="Einstellungen",
                           werte=settings_store.mail_public_settings(),
                           mail_configured=mailer.smtp_configured())


@users_bp.route("/api/settings/mail", methods=["GET"])
@login_required
@role_required(ROLE_ADMIN)
def api_mail_settings_get():
    from flask import jsonify
    return jsonify(settings_store.mail_public_settings())


@users_bp.route("/api/settings/mail", methods=["PUT"])
@login_required
@role_required(ROLE_ADMIN)
def api_mail_settings_put():
    """Speichert die Versandeinstellungen.

    Das Passwort wird nie zurückgegeben. Ein leer gelassenes Passwort lässt
    das gespeicherte stehen (die Oberfläche zeigt es nicht an);
    `clear_password` entfernt es ausdrücklich.
    """
    from flask import jsonify
    data = request.get_json(force=True, silent=True) or {}
    werte, fehler = _mail_settings_input(data)
    if fehler:
        return jsonify({"error": fehler}), 400

    me = current_user()
    settings_store.set_mail_settings(
        werte, actor=me["username"],
        clear_password=bool(data.get("clear_password")))
    return jsonify(settings_store.mail_public_settings())


@users_bp.route("/api/settings/mail/test", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def api_mail_settings_test():
    """Prüft die Zugangsdaten — ohne eine Nachricht zu senden."""
    from flask import jsonify
    ok, meldung = mailer.test_connection()
    return jsonify({"ok": ok, "message": meldung})


@users_bp.route("/api/users", methods=["GET"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_list():
    from flask import jsonify
    me = current_user()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY active DESC, username COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = _user_public_dict(r)
        d["is_self"] = (r["id"] == me["id"])
        out.append(d)
    return jsonify({"users": out, "min_password_length": PASSWORD_MIN_LENGTH})


@users_bp.route("/api/users", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_create():
    """Legt ein Benutzerkonto an.

    Pflicht sind Benutzername und Rolle. Das Startpasswort kann der
    Administrator vorgeben; bleibt das Feld leer, erzeugt die Anwendung
    eines und zeigt es **einmalig in der Antwort** (nicht im Log).

    Die E-Mail-Adresse ist optional: ist ein Versand eingerichtet
    (mailer.smtp_configured()), geht ein erzeugtes Passwort an diese
    Adresse — sonst wird es am Bildschirm übergeben. Ein Konto lässt sich
    also ohne Mailserver anlegen.

    In jedem Fall entsteht das Konto mit must_change_password = 1: die
    Person muss das Startpasswort bei der ersten Anmeldung ersetzen, sonst
    kennt der Administrator dauerhaft ein fremdes Passwort.
    """
    from flask import jsonify
    me = current_user()
    data = request.get_json(force=True, silent=True) or {}

    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    role = (data.get("role") or ROLE_USER).strip()
    password = data.get("password") or ""
    generated = False

    if not USERNAME_PATTERN.match(username):
        return jsonify({"error": "Der Benutzername muss 3–64 Zeichen lang sein "
                                 "und darf nur Buchstaben, Ziffern, Punkt, "
                                 "Bindestrich und Unterstrich enthalten."}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": f"Unbekannte Rolle: {role}"}), 400
    if email:
        problem = _validate_email(email)
        if problem:
            return jsonify({"error": problem}), 400

    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    else:
        problem = validate_password(password, username)
        if problem:
            return jsonify({"error": problem}), 400

    conn = get_db()
    try:
        existing = conn.execute("SELECT id, active FROM users WHERE username = ?",
                                (username,)).fetchone()
        if existing:
            if existing["active"]:
                return jsonify({"error": f"Der Benutzer „{username}” existiert "
                                         f"bereits."}), 409
            return jsonify({"error": f"Der Benutzer „{username}” existiert "
                                     f"bereits, ist aber deaktiviert. Bitte "
                                     f"stattdessen wieder aktivieren."}), 409
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, active, "
            "must_change_password, created_at, created_by, email) "
            "VALUES (?, ?, ?, 1, 1, ?, ?, ?)",
            (username, generate_password_hash(password), role,
             datetime.now().isoformat(timespec="seconds"), me["username"],
             email or None),
        )
        new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # Versand erst nach dem Anlegen: ein Zustellfehler darf das Konto nicht
    # wieder verschwinden lassen. Ohne Adresse oder ohne Versandkonfiguration
    # wird nicht versucht zuzustellen — das ist der Regelfall.
    sent, reason = False, None
    if generated and email:
        sent, reason = mailer.send_initial_password(
            email, username, password, role, reason="neu",
            actor=me["username"])

    if sent:
        zustellung = f"Das Anfangspasswort wurde an {email} gesendet."
    elif generated:
        zustellung = ("Das Anfangspasswort wird jetzt einmalig angezeigt — "
                      "bitte sofort weitergeben.")
    else:
        zustellung = ("Das vorgegebene Passwort bitte weitergeben; es muss bei "
                      "der ersten Anmeldung geändert werden.")

    audit.log(audit.CREATE, "user", new_id,
              f"Benutzer „{username}”"
              + (f" ({email})" if email else "")
              + f" mit Rolle {role} angelegt; "
              + ("Anfangspasswort per E-Mail zugestellt" if sent
                 else ("Anfangspasswort vom Administrator vergeben" if not generated
                       else "Anfangspasswort einmalig angezeigt")))
    return jsonify({
        "id": new_id,
        "username": username,
        "email": email or None,
        "role": role,
        "email_sent": sent,
        "email_error": reason,
        "generated_password": password if (generated and not sent) else None,
        "message": f"Benutzer „{username}” wurde angelegt. {zustellung}",
    }), 201


@users_bp.route("/api/users/<int:user_id>", methods=["PUT"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_update(user_id: int):
    """Ändert Rolle, Aktivstatus und E-Mail-Adresse eines Kontos."""
    from flask import jsonify
    me = current_user()
    data = request.get_json(force=True, silent=True) or {}

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404

        new_role = data.get("role", row["role"])
        new_active = data.get("active", bool(row["active"]))
        old_email = row["email"] if "email" in row.keys() else None
        new_email = (data.get("email") if data.get("email") is not None
                     else old_email)
        new_email = (new_email or "").strip() or None

        if new_role not in VALID_ROLES:
            return jsonify({"error": f"Unbekannte Rolle: {new_role}"}), 400
        if "email" in data:
            # Eine falsch geschriebene Adresse würde den Versand von
            # Anfangspasswörtern dauerhaft ins Leere laufen lassen — die
            # Korrektur muss also möglich sein, aber geprüft. Ein leerer Wert
            # nimmt die Adresse wieder heraus und ist zulässig.
            roh = (data.get("email") or "").strip()
            if roh:
                problem = _validate_email(roh)
                if problem:
                    return jsonify({"error": problem}), 400
            new_email = roh or None

        # Aussperr-Schutz: sonst kann sich der letzte Administrator selbst
        # die Rechte entziehen und niemand kommt mehr an die Verwaltung.
        if user_id == me["id"] and (new_role != ROLE_ADMIN or not new_active):
            return jsonify({"error": "Das eigene Konto kann nicht "
                                     "herabgestuft oder deaktiviert werden."}), 400
        losing_admin = (row["role"] == ROLE_ADMIN and row["active"]
                        and (new_role != ROLE_ADMIN or not new_active))
        if losing_admin and _active_admin_count(exclude_id=user_id) == 0:
            return jsonify({"error": "Der letzte aktive Administrator kann "
                                     "nicht herabgestuft oder deaktiviert "
                                     "werden."}), 400

        conn.execute("UPDATE users SET role = ?, active = ?, email = ? WHERE id = ?",
                     (new_role, 1 if new_active else 0, new_email, user_id))
        conn.commit()
    finally:
        conn.close()

    changes = audit.diff_text({"role": row["role"], "active": bool(row["active"]),
                               "email": old_email},
                              {"role": new_role, "active": new_active,
                               "email": new_email},
                              ("role", "active", "email"))
    if changes:
        audit.log(audit.UPDATE, "user", user_id,
                  f"Benutzer „{row['username']}”: {changes}")
    return jsonify({"ok": True, "id": user_id, "email": new_email})


@users_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_delete(user_id: int):
    """Löscht ein Konto endgültig (Zeile wird entfernt).

    Zwei getrennte Vorgänge, bewusst nicht vermischt:
      * Deaktivieren (PUT mit active=false) sperrt den Zugang sofort und
        lässt das Konto samt Zuordnung im Audit-Log bestehen — der Regelfall
        bei Austritt, Krankheit, Umzug.
      * Löschen (diese Route) entfernt den Datensatz. Das ist der Weg für
        Fehlanlagen, Testkonten und für das Recht auf Löschung
        (Art. 17 DSGVO). Die Zuordnung im Audit-Log bleibt über den
        Benutzernamen als Text erhalten; ein Eintrag hält fest, wer wann
        welches Konto gelöscht hat.

    Ausgesperrt bleibt nur, was das System handlungsunfähig machen würde:
    das eigene Konto und der letzte aktive Administrator.
    """
    from flask import jsonify
    me = current_user()
    if user_id == me["id"]:
        return jsonify({"error": "Das eigene Konto kann nicht gelöscht "
                                 "werden. Bitte einen anderen Administrator "
                                 "darum bitten."}), 400

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404
        if row["role"] == ROLE_ADMIN and row["active"] \
                and _active_admin_count(exclude_id=user_id) == 0:
            return jsonify({"error": "Der letzte aktive Administrator kann "
                                     "nicht gelöscht werden."}), 400

        username = row["username"]
        email = row["email"] if "email" in row.keys() else None
        role = row["role"]
        was_active = bool(row["active"])

        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        # Fehlversuche gehören zum Konto: ohne diese Zeile würde ein neues
        # Konto gleichen Namens mit fremden Fehlversuchen starten.
        conn.execute("DELETE FROM login_attempts WHERE username = ?",
                     (username.strip().lower(),))
        conn.commit()
    finally:
        conn.close()

    audit.log(audit.DELETE, "user", user_id,
              f"Benutzer „{username}” (Rolle {role}"
              + (f", {email}" if email else "")
              + ") endgültig gelöscht"
              + ("" if was_active else " — Konto war bereits deaktiviert"))
    return jsonify({"ok": True, "id": user_id, "username": username,
                    "deleted": True})


@users_bp.route("/api/users/<int:user_id>/password", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_reset_password(user_id: int):
    """Setzt das Passwort eines Kontos zurück.

    Das Konto muss das Passwort bei der nächsten Anmeldung ändern — sonst
    kennt der Administrator dauerhaft ein fremdes Passwort. Bei erzeugtem
    Passwort geht es per E-Mail an die hinterlegte Adresse; nur wenn kein
    Versand eingerichtet ist, kommt es einmalig in der Antwort zurück.
    """
    from flask import jsonify
    me = current_user()
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404

        email = row["email"] if "email" in row.keys() else None
        generated = False
        if not password:
            password = secrets.token_urlsafe(12)
            generated = True
        else:
            problem = validate_password(password, row["username"])
            if problem:
                return jsonify({"error": problem}), 400

        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, "
            "password_changed_at = ? WHERE id = ?",
            (generate_password_hash(password),
             datetime.now().isoformat(timespec="seconds"), user_id),
        )
        conn.commit()
        username, role = row["username"], row["role"]
    finally:
        conn.close()

    sent, reason = False, None
    if generated and email:
        sent, reason = mailer.send_initial_password(
            email, username, password, role, reason="zuruecksetzung",
            actor=me["username"])

    audit.log(audit.UPDATE, "user", user_id,
              f"Passwort von „{username}” zurückgesetzt "
              f"(Änderung bei nächster Anmeldung erzwungen; "
              + ("per E-Mail zugestellt)" if sent
                 else "einmalig angezeigt)"))
    return jsonify({
        "ok": True,
        "username": username,
        "email": email,
        "email_sent": sent,
        "email_error": reason,
        "generated_password": password if (generated and not sent) else None,
    })
