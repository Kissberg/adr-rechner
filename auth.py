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
existiert. Die Zugangsdaten kommen aus den Umgebungsvariablen
ADR_ADMIN_USER / ADR_ADMIN_PASSWORD. Ist kein Passwort gesetzt, wird ein
zufälliges erzeugt und in eine Datei geschrieben, die nur für den Besitzer
lesbar ist (0600) — NICHT ins Log.

────────────────────────────────────────────────────────────────────────
WICHTIG — warum ein erzeugtes Passwort nicht ins Log darf
────────────────────────────────────────────────────────────────────────
Logs sind grundsätzlich breiter lesbar und länger verfügbar als die
Anwendung selbst: `docker logs` zeigt sie jedem mit Docker-Zugang, in
Containern landen sie in json-Dateien, in Betrieben in ELK/Loki/Grafana —
dort sind sie oft wochenlang durchsuchbar und für deutlich mehr Personen
lesbar als die Datenbank. Ein einmalig erzeugtes Administratorpasswort im
Log ist damit faktisch ein dauerhaft gültiger Admin-Zugang für alle, die
Logs lesen dürfen.

Deshalb wird das erzeugte Passwort in eine Datei mit Modus 0600 im
Datenverzeichnis geschrieben und im Log nur der Pfad genannt. Für den
Produktivbetrieb steht zusätzlich ADR_REQUIRE_ADMIN_PASSWORD=1 bereit:
Dann verweigert die Anwendung den Start, wenn kein Passwort gesetzt ist,
statt eines zu erzeugen.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import subprocess
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
from database import get_db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
VALID_ROLES = (ROLE_ADMIN, ROLE_USER)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Benutzerverwaltung liegt bewusst auf einem eigenen Blueprint ohne Präfix:
# die JSON-Schnittstelle gehört unter /api/users, nicht unter /auth/api/users.
users_bp = Blueprint("users", __name__)

# Datei für ein erzeugtes Anfangspasswort. Bewusst im Datenverzeichnis,
# damit sie im Docker-Volume liegt und nicht im Image landet.
ADMIN_PASSWORD_FILENAME = ".admin_password"

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


def require_configured_password() -> bool:
    """Strenger Start: ohne gesetztes ADR_ADMIN_PASSWORD nicht hochfahren.

    Für den Produktivbetrieb empfohlen. Wird hierauf verzichtet, erzeugt die
    Anwendung ein Zufallspasswort und legt es in einer Datei ab.
    """
    return os.environ.get("ADR_REQUIRE_ADMIN_PASSWORD", "0").strip().lower() \
        in ("1", "true", "yes")


def admin_password_path() -> str:
    """Pfad der Datei mit dem erzeugten Anfangspasswort."""
    configured = os.environ.get("ADR_ADMIN_PASSWORD_FILE", "").strip()
    return configured or os.path.join(database.DB_DIR, ADMIN_PASSWORD_FILENAME)


def _current_user_sid() -> Optional[str]:
    """SID des aktuellen Windows-Kontos, für icacls. None unter Unix."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[System.Security.Principal.WindowsIdentity]::GetCurrent()"
             ".User.Value"],
            capture_output=True, text=True, timeout=20,
        )
        sid = out.stdout.strip()
        return sid or None
    except (OSError, subprocess.SubprocessError):
        return None


def restrict_to_owner(path: str) -> bool:
    """Beschränkt die Datei auf den Besitzer. Wirkt unter Unix und Windows.

    Unter Unix genügt chmod 0600. Unter Windows ignoriert os.open() den
    Modus — ohne diesen Schritt wäre die Datei über die vom übergeordneten
    Ordner geerbten Rechte für alle Konten des Rechners lesbar, und die
    Ausgabe „nur für den Besitzer lesbar" wäre eine falsche Zusicherung.
    """
    if os.name == "nt":
        sid = _current_user_sid()
        if not sid:
            return False
        try:
            # /inheritance:r entfernt geerbte Rechte; danach wird nur dem
            # eigenen Konto Lese- und Schreibrecht eingeräumt.
            subprocess.run(["icacls", path, "/inheritance:r", "/Q"],
                           capture_output=True, timeout=20, check=False)
            grant = subprocess.run(
                ["icacls", path, "/grant:r", f"*{sid}:(R,W)", "/Q"],
                capture_output=True, timeout=20, check=False)
            return grant.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    try:
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False


def write_admin_password(username: str, password: str) -> Optional[tuple]:
    """Schreibt das erzeugte Passwort in eine eigentümer-geschützte Datei.

    Gibt (Pfad, rechte_gesetzt) zurück oder None, wenn die Datei nicht
    geschrieben werden konnte. Das Passwort wird unter keinen Umständen ins
    Log geschrieben — auch nicht, wenn das Schreiben fehlschlägt.
    """
    path = admin_password_path()
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # 0600 direkt beim Anlegen: so existiert die Datei nie kurzzeitig
        # mit weitergehenden Rechten.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"Benutzername : {username}\n")
            fh.write(f"Passwort     : {password}\n")
            fh.write("\nDiese Datei enthält ein erzeugtes Anfangspasswort.\n")
            fh.write("Bitte nach der ersten Anmeldung das Passwort ändern\n")
            fh.write("und diese Datei löschen.\n")
        return path, restrict_to_owner(path)
    except OSError:
        return None


def delete_admin_password_file() -> bool:
    """Entfernt die Passwortdatei, nachdem das Passwort geändert wurde."""
    path = admin_password_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────
# Benutzerverwaltung
# ─────────────────────────────────────────────────────────────────────

def ensure_default_admin() -> None:
    """
    Legt den ersten Administrator an, sofern noch kein Benutzer existiert.
    Passwort aus ADR_ADMIN_PASSWORD, sonst zufällig — dann wird es in eine
    Datei mit Modus 0600 geschrieben, NICHT ausgegeben (siehe Modulkopf).

    Wichtig bei mehreren Gunicorn-Workern: Alle Worker führen diesen Code
    beim Import aus. Ein normales "erst prüfen, dann einfügen" kann daher
    eine Race-Condition haben (beide Worker sehen eine leere Tabelle, der
    zweite INSERT schlägt fehl und der Worker stürzt ab). INSERT OR IGNORE
    nutzt den UNIQUE-Constraint als Schutz — der verlierende Worker erkennt
    an rowcount == 0, dass ein anderer Worker den Benutzer angelegt hat.

    Ein bereits vorhandener Benutzer wird niemals überschrieben.

    Raises:
        RuntimeError: Wenn ein Passwort erzeugt werden müsste, aber weder
            ADR_ADMIN_PASSWORD gesetzt ist (bei ADR_REQUIRE_ADMIN_PASSWORD=1)
            noch die Passwortdatei geschrieben werden kann. Ein Administrator
            mit unbekanntem Passwort wäre eine dauerhafte Sperre — deshalb
            wird hier abgebrochen statt weiterzulaufen.
    """
    username = os.environ.get("ADR_ADMIN_USER", "admin").strip() or "admin"
    password = os.environ.get("ADR_ADMIN_PASSWORD", "").strip()
    generated = False

    if not password:
        if require_configured_password():
            raise RuntimeError(
                "ADR_REQUIRE_ADMIN_PASSWORD ist gesetzt, aber "
                "ADR_ADMIN_PASSWORD fehlt. Start verweigert — bitte "
                "ADR_ADMIN_PASSWORD setzen oder ADR_REQUIRE_ADMIN_PASSWORD "
                "zurücknehmen, damit ein Zufallspasswort erzeugt wird."
            )
        password = secrets.token_urlsafe(16)
        generated = True

    # Ein vorgegebenes Passwort wird nur geprüft, wenn wirklich ein Konto
    # entsteht. Bei bestehender Datenbank legt die Funktion nichts an — eine
    # Prüfung könnte dort ein Upgrade blockieren, obwohl der Wert gar nicht
    # verwendet wird.
    if not generated:
        conn = get_db()
        try:
            vorhanden = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        if not vorhanden:
            problem = validate_password(password, username)
            if problem:
                raise RuntimeError(
                    "ADR_ADMIN_PASSWORD erfüllt die Passwortrichtlinie nicht "
                    f"({problem}) Start verweigert. Bitte ein längeres, "
                    "einmaliges Passwort setzen — oder ADR_ADMIN_PASSWORD "
                    "weglassen, dann wird eines erzeugt."
                )

    # Passwort zuerst sichern: kann die Datei nicht geschrieben werden,
    # darf der Benutzer nicht angelegt werden — sonst entstünde ein Konto,
    # dessen Passwort niemand kennt.
    password_file = None
    restricted = False
    if generated:
        written = write_admin_password(username, password)
        if written is not None:
            password_file, restricted = written
        if password_file is None:
            raise RuntimeError(
                "Ein Administratorpasswort müsste erzeugt werden, konnte aber "
                "nicht in die Datei " + admin_password_path() + " geschrieben "
                "werden. Start verweigert. Bitte ADR_ADMIN_PASSWORD setzen "
                "oder Schreibrechte im Datenverzeichnis prüfen."
            )

    conn = get_db()
    try:
        # Ein erzeugtes Passwort liegt bis zur ersten Änderung in einer Datei
        # auf der Platte. Deshalb muss es beim ersten Anmelden ersetzt
        # werden — danach ist die Datei wertlos und wird gelöscht.
        # Ein vom Betreiber gesetztes Passwort kennt nur er; dort ist keine
        # erzwungene Änderung nötig.
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, active, "
            "must_change_password, created_at, created_by) "
            "VALUES (?, ?, ?, 1, ?, ?, ?)",
            (username, generate_password_hash(password), ROLE_ADMIN,
             1 if generated else 0,
             datetime.now().isoformat(timespec="seconds"),
             "Umgebungsvariable" if not generated else "System"),
        )
        conn.commit()
        created = (cur.rowcount or 0) > 0
    finally:
        conn.close()

    if not created:
        # Bereits vorhanden (vorheriger Start oder paralleler Worker).
        # Das Passwort wird bewusst NICHT zurückgesetzt, damit niemand durch
        # bloßes Setzen einer Umgebungsvariable Zugriff auf fremde Konten
        # übernehmen kann.
        return

    if generated:
        # Nur der Pfad — niemals das Passwort selbst.
        print("=" * 72)
        print("  ERSTER ADMINISTRATOR WURDE ANGELEGT")
        print(f"  Benutzername : {username}")
        print("  Passwort     : NICHT im Log — siehe Datei")
        if restricted:
            print(f"  Datei        : {password_file}  (nur für den Besitzer lesbar)")
        else:
            # Keine falsche Zusicherung: wenn die Rechte nicht gesetzt werden
            # konnten, muss das hier stehen.
            print(f"  Datei        : {password_file}")
            print("  ACHTUNG      : Die Rechte konnten NICHT auf den Besitzer "
                  "beschränkt werden.")
            print("                Datei nach der ersten Anmeldung unbedingt "
                  "löschen.")
        print("  Bitte nach der ersten Anmeldung ändern und die Datei löschen.")
        print("=" * 72)
    else:
        print(f"[auth] Administrator '{username}' angelegt.")


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

    # Eine Datei mit dem erzeugten Anfangspasswort ist damit wertlos
    # geworden — sie wird entfernt, damit sie nicht liegen bleibt.
    delete_admin_password_file()

    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────
# Benutzerverwaltung (nur Administratoren)
# ─────────────────────────────────────────────────────────────────────
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


def _user_public_dict(row) -> dict:
    """Benutzerdatensatz ohne Passwort-Hash für die Ausgabe."""
    return {
        "id": row["id"],
        "username": row["username"],
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
    return render_template("benutzer.html", title="Benutzerverwaltung")


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

    Ohne Passwort im Request wird eines erzeugt und **einmalig in der
    Antwort** zurückgegeben — nicht im Log. Der Administrator übergibt es
    der Person, die es beim ersten Anmelden ohnehin ersetzen muss.
    """
    from flask import jsonify
    me = current_user()
    data = request.get_json(force=True, silent=True) or {}

    username = (data.get("username") or "").strip()
    role = (data.get("role") or ROLE_USER).strip()
    password = data.get("password") or ""
    generated = False

    if not USERNAME_PATTERN.match(username):
        return jsonify({"error": "Der Benutzername muss 3–64 Zeichen lang sein "
                                 "und darf nur Buchstaben, Ziffern, Punkt, "
                                 "Bindestrich und Unterstrich enthalten."}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": f"Unbekannte Rolle: {role}"}), 400

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
            "must_change_password, created_at, created_by) "
            "VALUES (?, ?, ?, 1, 1, ?, ?)",
            (username, generate_password_hash(password), role,
             datetime.now().isoformat(timespec="seconds"), me["username"]),
        )
        new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    audit.log(audit.CREATE, "user", new_id,
              f"Benutzer „{username}” mit Rolle {role} angelegt")
    return jsonify({
        "id": new_id,
        "username": username,
        "role": role,
        "generated_password": password if generated else None,
        "message": f"Benutzer „{username}” wurde angelegt.",
    }), 201


@users_bp.route("/api/users/<int:user_id>", methods=["PUT"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_update(user_id: int):
    """Ändert Rolle und Aktivstatus eines Kontos."""
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

        if new_role not in VALID_ROLES:
            return jsonify({"error": f"Unbekannte Rolle: {new_role}"}), 400

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

        conn.execute("UPDATE users SET role = ?, active = ? WHERE id = ?",
                     (new_role, 1 if new_active else 0, user_id))
        conn.commit()
    finally:
        conn.close()

    changes = audit.diff_text(dict(row), {"role": new_role, "active": new_active},
                              ("role", "active"))
    if changes:
        audit.log(audit.UPDATE, "user", user_id,
                  f"Benutzer „{row['username']}”: {changes}")
    return jsonify({"ok": True, "id": user_id})


@users_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_delete(user_id: int):
    """Deaktiviert ein Konto.

    Bewusst keine Zeilenlöschung: das Audit-Log verweist über `username`
    auf das Konto, und ein gelöschter Benutzer würde diese Zuordnung
    zerstören. Deaktivieren sperrt den Zugang sofort und bleibt
    nachvollziehbar.
    """
    from flask import jsonify
    me = current_user()
    if user_id == me["id"]:
        return jsonify({"error": "Das eigene Konto kann nicht deaktiviert "
                                 "werden."}), 400

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404
        if row["role"] == ROLE_ADMIN and _active_admin_count(exclude_id=user_id) == 0:
            return jsonify({"error": "Der letzte aktive Administrator kann "
                                     "nicht deaktiviert werden."}), 400
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    audit.log(audit.DELETE, "user", user_id,
              f"Benutzer „{row['username']}” deaktiviert")
    return jsonify({"ok": True, "id": user_id})


@users_bp.route("/api/users/<int:user_id>/password", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def api_users_reset_password(user_id: int):
    """Setzt das Passwort eines Kontos zurück.

    Das Konto muss das Passwort bei der nächsten Anmeldung ändern — sonst
    kennt der Administrator dauerhaft ein fremdes Passwort.
    """
    from flask import jsonify
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404

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
    finally:
        conn.close()

    audit.log(audit.UPDATE, "user", user_id,
              f"Passwort von „{row['username']}” zurückgesetzt "
              f"(Änderung bei nächster Anmeldung erzwungen)")
    return jsonify({
        "ok": True,
        "username": row["username"],
        "generated_password": password if generated else None,
    })
