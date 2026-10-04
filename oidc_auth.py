"""
oidc_auth.py — Optionale Unternehmensanmeldung über OpenID Connect

Die lokale Benutzerdatenbank bleibt vollständig bestehen; dieses Modul
ergänzt sie um eine Anmeldung über einen zentralen Identitätsanbieter
(getestet gegen Microsoft Entra ID, v2.0-Endpunkt). Damit entfällt die
lokale Kontenpflege für den Alltag: Konten entstehen beim ersten
Anmelden, Rollen werden — so konfiguriert — aus Gruppenzugehörigkeiten
abgeleitet, und das Ausscheiden einer Person (Offboarding) wird zentral
im IdP vollzogen und spätestens hier wirksam: deaktivierte Konten
lehnen die SSO-Anmeldung ab.

Konfiguration (Umgebung)
  ADR_OIDC_ENABLED      1 = SSO aktiv (Standard 0)
  ADR_OIDC_ISSUER       z. B. https://login.microsoftonline.com/<tenant>/v2.0
  ADR_OIDC_CLIENT_ID    Application (client) ID der App-Registrierung
  ADR_OIDC_CLIENT_SECRET Clientsecret („Geheimnisse gehören zur Umgebung,
                        nicht in die Datenbank" — anders als das
                        Postfachpasswort ist dieser Wert nicht austauschbar
                        über die Oberfläche, weil er vor dem ersten Request
                        nötig ist)
  ADR_OIDC_SCOPES       Standard „openid profile email"
  ADR_OIDC_REDIRECT_URI optional, wenn der Reverse Proxy die externe
                        Adresse nicht korrekt durchreicht
  ADR_OIDC_ROLE_CLAIM   Claim mit den Gruppen (Standard „groups")
  ADR_OIDC_ADMIN_GROUP  Objekt-ID der Gruppe, deren Mitglieder
                        Administrator werden (leer = niemand per SSO)
  ADR_OIDC_LOCAL_LOGIN  0 = nur SSO, kein Passwortformular (Standard 1)
  ADR_BOOTSTRAP_ADMIN   siehe auth.py — im SSO-Betrieb üblich: 0

Warum keine Passwörter für SSO-Konten
  SSO-Konten erhalten beim Anlegen einen Zufalls-Hash ohne bekanntes
  Passwort: eine Anmeldung mit Benutzername/Passwort ist für sie nicht
  möglich. Wer ein SSO-Konto zusätzlich lokal betreiben will, setzt
  über `manage.py reset-password` ein reguläres Passwort — der
  erzwungene Wechsel bei der ersten Anmeldung gilt wie für jedes Konto.

Ablösung der Session
  /auth/logout beendet die Session dieser Anwendung. Die Sitzung beim
  Identitätsanbieter (z. B. das Angemeldet-Sein in Office 365) bleibt
  bestehen — eine Single-Logout-Kette ist nicht implementiert und wird
  im Unternehmensbetrieb vom zentralen IdP geregelt (siehe README).
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from typing import Optional

from flask import Blueprint, abort, redirect, render_template, request, \
    session, url_for
from werkzeug.security import generate_password_hash

import audit
from database import get_db

try:
    from authlib.integrations.flask_client import OAuth
    _AUTHLIB_AVAILABLE = True
except ImportError:  # Die SSO-Funktion ist optional — Abhängigkeit nur
    OAuth = None      # nötig, wenn sie auch eingeschaltet wird.
    _AUTHLIB_AVAILABLE = False

ROLE_ADMIN = "admin"
ROLE_USER = "user"

oidc_bp = Blueprint("oidc", __name__)

_oauth: Optional["OAuth"] = None
_configured = False


def oidc_enabled() -> bool:
    """Ob die SSO-Anmeldung eingeschaltet ist."""
    return os.environ.get("ADR_OIDC_ENABLED", "0").strip().lower() \
        in ("1", "true", "yes")


def local_login_enabled() -> bool:
    """Ob das Passwortformular neben dem SSO erreichbar bleibt."""
    return os.environ.get("ADR_OIDC_LOCAL_LOGIN", "1").strip().lower() \
        not in ("0", "false", "no")


def _require_env(name: str) -> str:
    wert = (os.environ.get(name) or "").strip()
    if not wert:
        raise RuntimeError(
            f"ADR_OIDC_ENABLED ist gesetzt, aber {name} fehlt — "
            f"die SSO-Anmeldung kann ohne diese Angabe nicht starten.")
    return wert


def configure_oidc(app) -> None:
    """Registriert den OIDC-Client an der Flask-App (einmalig, beim Start).

    Fehlt authlib oder eine Pflichtangabe, bricht der Start mit einer
    verständlichen Meldung ab — halb konfiguriertes SSO wäre schlimmer
    als keins. Wer SSO nicht nutzt (ADR_OIDC_ENABLED unset), merkt von
    diesem Modul nichts.
    """
    global _oauth, _configured
    if not oidc_enabled() or _configured:
        return
    if not _AUTHLIB_AVAILABLE:
        raise RuntimeError(
            "ADR_OIDC_ENABLED ist gesetzt, aber das Paket authlib fehlt. "
            "Bitte `pip install authlib requests` bzw. requirements.txt "
            "installieren.")
    scopes = (os.environ.get("ADR_OIDC_SCOPES")
              or "openid profile email").strip()
    issuer = _require_env("ADR_OIDC_ISSUER").rstrip("/")
    client_id = _require_env("ADR_OIDC_CLIENT_ID")
    client_secret = _require_env("ADR_OIDC_CLIENT_SECRET")

    _oauth = OAuth()
    _oauth.register(
        name="entra",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": scopes},
    )
    _oauth.init_app(app)
    _configured = True
    print("[oidc] SSO-Anmeldung aktiviert — Issuer:", issuer)


def _redirect_uri() -> str:
    # `_external=True` leitet sich aus dem Host-Header ab. Im
    # Unternehmensbetrieb wird ADR_OIDC_REDIRECT_URI gesetzt (dann greift der
    # obige Wert) — und der Identitätsanbieter akzeptiert ohnehin nur
    # Redirect-URIs, die in der App-Registrierung eingetragen sind;
    # ein manipulierter Host führt zu einer Ablehnung dort, nicht zu
    # einer Umleitung auf Angreiferseite.
    return (os.environ.get("ADR_OIDC_REDIRECT_URI") or "").strip() \
        or url_for("oidc.callback", _external=True)  # nosemgrep: python.flask.security.audit.flask-url-for-external-true.flask-url-for-external-true


@oidc_bp.route("/auth/oidc/login")
def login():
    """Leitet zum Identitätsanbieter weiter (Authorization Code Flow)."""
    if not oidc_enabled() or _oauth is None:
        abort(404)
    return _oauth.entra.authorize_redirect(redirect_uri=_redirect_uri())


@oidc_bp.route("/auth/oidc/callback")
def callback():
    """Nimmt die Antwort des IdP entgegen und meldet den Benutzer an.

    Der Schutz gegen manipulierte Callbacks ist der state-Parameter des
    OIDC-Standards (von authlib gegen die Session geprüft) — deshalb
    ist diese Route vom CSRF-Token ausgenommen (siehe csrf.py).
    """
    if not oidc_enabled() or _oauth is None:
        abort(404)
    try:
        token = _oauth.entra.authorize_access_token()
    except Exception:
        # Ohne Details: technische Fehler im Flow sind Sache der
        # Serverausgabe, nicht der Browserkonsole des Angreifers.
        import sys
        print("[oidc] Anmeldefluss fehlgeschlagen (Token-Austausch).",
              file=sys.stderr)
        return _fehlerseite("Die Anmeldung beim Identitätsanbieter ist "
                            "fehlgeschlagen. Bitte erneut versuchen.")
    if not token:
        return _fehlerseite("Die Anmeldung wurde abgebrochen.")

    claims = token.get("userinfo") or {}
    if not claims:
        try:
            claims = _oauth.entra.userinfo(token=token)
        except Exception:
            claims = {}
    if not claims:
        return _fehlerseite("Der Identitätsanbieter hat keine "
                            "Benutzerdaten geliefert.")

    user = provision_user(claims)
    if user is None:
        return _fehlerseite(
            "Die Anmeldung ist abgelehnt. Das Konto ist in dieser "
            "Anwendung nicht zugelassen — bitte den Administrator "
            "informieren.")
    audit.log(audit.LOGIN, "user", user["id"],
              f"Anmeldung über Entra ID / OIDC ({user['username']})",
              username=user["username"], user_id=user["id"])
    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session.permanent = False
    return redirect(url_for("index"))


def _fehlerseite(meldung: str):
    return render_template("error.html", title="Anmeldung fehlgeschlagen",
                           error_code=403, error_message=meldung), 403


# ─────────────────────────────────────────────────────────────────────
# Konten aus den IdP-Angaben ableiten
# ─────────────────────────────────────────────────────────────────────

def username_from_claims(claims: dict) -> Optional[str]:
    """Bestimmt den lokalen Benutzernamen aus den IdP-Angaben.

    Reihenfolge: preferred_username (bei Entra die UPN), sonst E-Mail,
    sonst die unveränderliche subject-ID. Länger als die Datenbank
    aufnehmen kann (100 Zeichen) ist ein Fehler, kein Stummzuschnitt —
    ein gekürzter Name wäre eine zweite, andere Identität.
    """
    for feld in ("preferred_username", "email", "sub"):
        wert = str(claims.get(feld) or "").strip()
        if wert:
            if len(wert) > 100:
                return None
            return wert
    return None


def role_from_claims(claims: dict) -> str:
    """Rolle aus der Gruppenzugehörigkeit — nur wenn konfiguriert.

    Ohne ADR_OIDC_ADMIN_GROUP gibt es aus dem SSO keine Administratoren:
    das ist der sichere Standard, denn eine falsch notierte Objekt-ID
    soll niemanden versehentlich erhöhen. Ist die Gruppe gesetzt,
    bestimmt sie die Rolle bei jeder Anmeldung neu (zentrales RBAC) —
    lokale Änderungen wären dann bei der nächsten Anmeldung zunichte.
    """
    admin_gruppe = (os.environ.get("ADR_OIDC_ADMIN_GROUP") or "").strip()
    if not admin_gruppe:
        return ROLE_USER
    claim_name = (os.environ.get("ADR_OIDC_ROLE_CLAIM") or "groups").strip()
    gruppen = claims.get(claim_name) or []
    if isinstance(gruppen, str):
        gruppen = [gruppen]
    return ROLE_ADMIN if admin_gruppe in gruppen else ROLE_USER


def provision_user(claims: dict) -> Optional[dict]:
    """Findet das Konto zu den IdP-Angaben oder legt es an.

    Rückgabe: Benutzerzeile (dict) für die Session, oder None, wenn die
    Anmeldung abzulehnen ist (unbekannte Identität ohne Namen, oder
    deaktiviertes Konto — der Offboarding-Fall).
    """
    username = username_from_claims(claims)
    if not username:
        return None
    email = str(claims.get("email") or "").strip() or None
    role = role_from_claims(claims)

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if row is not None and not row["active"]:
            # Deaktiviertes Konto: die zentrale Sperrung wird hier wirksam.
            conn.close()
            audit.log(audit.LOGIN, "user", row["id"],
                      f"SSO-Anmeldung abgelehnt — Konto deaktiviert "
                      f"({username})", username=username)
            return None

        if row is None:
            # Zufalls-Hash ohne bekanntes Passwort: dieses Konto meldet
            # sich ausschließlich über den Identitätsanbieter an.
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, active, "
                "must_change_password, created_at, created_by, email) "
                "VALUES (?, ?, ?, 1, 0, ?, 'OIDC-Anmeldung', ?)",
                (username,
                 generate_password_hash(secrets.token_urlsafe(32)),
                 role, datetime.now().isoformat(timespec="seconds"), email),
            )
            new_id = cur.lastrowid
            conn.commit()
            audit.log(audit.CREATE, "user", new_id,
                      f"Konto „{username}” durch SSO-Anmeldung angelegt "
                      f"(Rolle {role}{', ' + email if email else ''})",
                      username=username)
        else:
            new_id = row["id"]
            geaendert = []
            if (os.environ.get("ADR_OIDC_ADMIN_GROUP") or "").strip() \
                    and row["role"] != role:
                # Ist die Rolle zentral gemappt, gilt der IdP — auch nach
                # unten (z. B. Austritt aus der Admin-Gruppe). Ohne Mapping
                # wird die lokale Rolle nie angetastet.
                conn.execute("UPDATE users SET role = ? WHERE id = ?",
                             (role, new_id))
                geaendert.append(f"Rolle {row['role']} → {role}")
            if email and email != (row["email"] if "email" in row.keys()
                                   else None):
                conn.execute("UPDATE users SET email = ? WHERE id = ?",
                             (email, new_id))
                geaendert.append("E-Mail aktualisiert")
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                         (datetime.now().isoformat(timespec="seconds"),
                          new_id))
            conn.commit()
            if geaendert:
                audit.log(audit.UPDATE, "user", new_id,
                          f"SSO-Anmeldung: {', '.join(geaendert)} "
                          f"({username})", username=username)

        zeile = conn.execute("SELECT * FROM users WHERE id = ?",
                             (new_id,)).fetchone()
        return dict(zeile) if zeile else None
    finally:
        try:
            conn.close()
        except Exception:
            pass
