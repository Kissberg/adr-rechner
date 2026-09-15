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
from database import get_db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
VALID_ROLES = (ROLE_ADMIN, ROLE_USER)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Datei für ein erzeugtes Anfangspasswort. Bewusst im Datenverzeichnis,
# damit sie im Docker-Volume liegt und nicht im Image landet.
ADMIN_PASSWORD_FILENAME = ".admin_password"


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
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, active, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (username, generate_password_hash(password), ROLE_ADMIN,
             datetime.now().isoformat(timespec="seconds")),
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
        user = verify_credentials(username, password)
        if user is None:
            error = "Benutzername oder Passwort ungültig."
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session.permanent = False
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


@auth_bp.route("/password", methods=["POST"])
@login_required
def change_password():
    """Passwort des angemeldeten Benutzers ändern."""
    from flask import jsonify
    user = current_user()
    data = request.get_json(force=True, silent=True) or {}
    old = data.get("old_password", "")
    new = data.get("new_password", "")

    if len(new) < 10:
        return jsonify({"error": "Das neue Passwort muss mindestens 10 Zeichen lang sein."}), 400

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None:
            return jsonify({"error": "Benutzer nicht gefunden"}), 404
        if not check_password_hash(row["password_hash"], old):
            return jsonify({"error": "Aktuelles Passwort ist falsch."}), 403
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(new), user["id"]))
        conn.commit()
    finally:
        conn.close()

    # Eine Datei mit dem erzeugten Anfangspasswort ist damit wertlos
    # geworden — sie wird entfernt, damit sie nicht liegen bleibt.
    delete_admin_password_file()

    return jsonify({"ok": True})
