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
zufälliges erzeugt und einmalig im Log ausgegeben.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from typing import Optional

from flask import Blueprint, flash, redirect, render_template, request, \
    session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
VALID_ROLES = (ROLE_ADMIN, ROLE_USER)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def auth_enabled() -> bool:
    """Gibt zurück, ob die Anmeldepflicht aktiv ist."""
    return os.environ.get("AUTH_ENABLED", "1").strip().lower() not in ("0", "false", "no")


# ─────────────────────────────────────────────────────────────────────
# Benutzerverwaltung
# ─────────────────────────────────────────────────────────────────────

def ensure_default_admin() -> None:
    """
    Legt den ersten Administrator an, sofern noch kein Benutzer existiert.
    Passwort aus ADR_ADMIN_PASSWORD, sonst zufällig (einmalig im Log).
    """
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return

        username = os.environ.get("ADR_ADMIN_USER", "admin").strip() or "admin"
        password = os.environ.get("ADR_ADMIN_PASSWORD", "").strip()
        generated = False
        if not password:
            password = secrets.token_urlsafe(16)
            generated = True

        conn.execute(
            "INSERT INTO users (username, password_hash, role, active, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (username, generate_password_hash(password), ROLE_ADMIN,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()

        if generated:
            print("=" * 72)
            print("  ERSTER ADMINISTRATOR WURDE ANGELEGT")
            print(f"  Benutzername : {username}")
            print(f"  Passwort     : {password}")
            print("  Bitte nach der ersten Anmeldung sofort ändern.")
            print("=" * 72)
        else:
            print(f"[auth] Administrator '{username}' angelegt.")
    finally:
        conn.close()


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

    return jsonify({"ok": True})
