"""
Tests der SSO-Anmeldung über OpenID Connect (oidc_auth.py).

Getestet wird die lokale Seite des Standards: Ableitung von Benutzername
und Rolle aus den IdP-Angaben, Anlage und Sperrung von Konten, Sichtbar-
keit der Anmeldeoption und der Callback mit gemocktem Token-Austausch
(ohne Netz, ohne Entra ID).
"""

import tempfile
from types import SimpleNamespace

import pytest

import database
import auth
import oidc_auth
from database import get_db

ADMIN_PW = "Streng-Vertraulich-2026a"

CLAIMS_MUSTERMANN = {
    "sub": "a1b2c3d4e5f6",
    "preferred_username": "max.mustermann@firma.de",
    "email": "max.mustermann@firma.de",
    "name": "Max Mustermann",
    "groups": ["g-1", "g-2"],
}


@pytest.fixture()
def db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    monkeypatch.setenv("SECRET_KEY", "test-key")
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    database.init_db()
    auth.ensure_default_admin()
    return tmp


# ── Rollenableitung ────────────────────────────────────────────────────

def test_ohne_admin_gruppe_gibt_es_keine_sso_admins(db, monkeypatch):
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    assert oidc_auth.role_from_claims(
        CLAIMS_MUSTERMANN) == oidc_auth.ROLE_USER


def test_admin_gruppe_erhoeht_die_rolle(db, monkeypatch):
    monkeypatch.setenv("ADR_OIDC_ADMIN_GROUP", "g-2")
    assert oidc_auth.role_from_claims(CLAIMS_MUSTERMANN) == oidc_auth.ROLE_ADMIN
    monkeypatch.setenv("ADR_OIDC_ADMIN_GROUP", "andere-gruppe")
    assert oidc_auth.role_from_claims(CLAIMS_MUSTERMANN) == oidc_auth.ROLE_USER


def test_anderer_claim_name_wird_gelesen(db, monkeypatch):
    monkeypatch.setenv("ADR_OIDC_ADMIN_GROUP", "sso-admins")
    monkeypatch.setenv("ADR_OIDC_ROLE_CLAIM", "rollen")
    assert oidc_auth.role_from_claims(
        {"rollen": ["sso-admins"]}) == oidc_auth.ROLE_ADMIN


# ── Benutzernamen-Ableitung ────────────────────────────────────────────

def test_benutzername_aus_preferred_username(db, monkeypatch):
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    user = oidc_auth.provision_user(CLAIMS_MUSTERMANN)
    assert user["username"] == "max.mustermann@firma.de"
    assert user["email"] == "max.mustermann@firma.de"


def test_ohne_angaben_wird_abgelehnt(db, monkeypatch):
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    assert oidc_auth.provision_user({}) is None


def test_subject_id_als_letzter_fallback(db, monkeypatch):
    """Ohne UPN und E-Mail bleibt die unveränderliche subject-ID —
    eindeutig, aber ohne praktische Bedeutung für die Anzeige."""
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    user = oidc_auth.provision_user({"sub": "x1y2z3"})
    assert user["username"] == "x1y2z3"


def test_neues_konto_hat_kein_nutzbares_passwort(db, monkeypatch):
    from werkzeug.security import check_password_hash
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    user = oidc_auth.provision_user(CLAIMS_MUSTERMANN)
    assert user["must_change_password"] == 0
    # Ein Passwort, das niemand kennt, kann auch niemand erraten:
    assert not check_password_hash(user["password_hash"], "admin")
    assert not check_password_hash(user["password_hash"], "")


def test_deaktiviertes_konto_bleibt_gesperrt(db, monkeypatch):
    """Offboarding-Fall: deaktivierte Konten lehnen die SSO-Anmeldung ab."""
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    erster = oidc_auth.provision_user(CLAIMS_MUSTERMANN)

    conn = get_db()
    try:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?",
                     (erster["id"],))
        conn.commit()
    finally:
        conn.close()

    assert oidc_auth.provision_user(CLAIMS_MUSTERMANN) is None


def test_rolle_wird_bei_anmeldung_nachgezogen(db, monkeypatch):
    monkeypatch.setenv("ADR_OIDC_ADMIN_GROUP", "g-2")
    erster = oidc_auth.provision_user(CLAIMS_MUSTERMANN)
    assert erster["role"] == oidc_auth.ROLE_ADMIN

    monkeypatch.setenv("ADR_OIDC_ADMIN_GROUP", "andere-gruppe")
    zweiter = oidc_auth.provision_user(CLAIMS_MUSTERMANN)
    # Die Gruppe ist nicht mehr gesetzt → zentral gesteuert zurück auf
    # Benutzer (Austritt aus der Admin-Gruppe wirkt sofort). Ohne
    # konfigurierte Gruppe würde die lokale Rolle nie angetastet.
    assert zweiter["role"] == oidc_auth.ROLE_USER


# ── Anmeldeseite und Callback ──────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch, db):
    import app as app_module
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    yield c, app_module


def test_ohne_sso_kein_button(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("ADR_OIDC_ENABLED", "0")
    assert "Entra" not in c.get("/auth/login").get_data(as_text=True)


def test_mit_sso_button_und_ohne_passwortformular(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("ADR_OIDC_ENABLED", "1")
    monkeypatch.setenv("ADR_OIDC_LOCAL_LOGIN", "0")
    seite = c.get("/auth/login").get_data(as_text=True)
    assert "Entra" in seite
    assert 'name="password"' not in seite


OIDC_ENV = {
    "ADR_OIDC_ENABLED": "1",
    "ADR_OIDC_ISSUER": "https://login.microsoftonline.com/tenant/v2.0",
    "ADR_OIDC_CLIENT_ID": "test-client",
    "ADR_OIDC_CLIENT_SECRET": "test-secret",
}


def test_callback_legt_konto_an_und_meldet_an(client, monkeypatch):
    c, app_module = client
    for schluessel, wert in OIDC_ENV.items():
        monkeypatch.setenv(schluessel, wert)
    monkeypatch.delenv("ADR_OIDC_ADMIN_GROUP", raising=False)
    oidc_auth.configure_oidc(app_module.app)

    # Token-Austausch wird gemockt — geprüft wird, was die Anwendung
    # daraus macht: Konto anlegen, Session aufbauen, zum Rechner leiten.
    def _fake_token():
        return {"userinfo": dict(CLAIMS_MUSTERMANN),
                "access_token": "nicht-geprueft"}
    monkeypatch.setattr(
        oidc_auth._oauth.entra, "authorize_access_token", _fake_token)

    res = c.get("/auth/oidc/callback")
    assert res.status_code == 302

    with c.session_transaction() as sess:
        assert sess["username"] == "max.mustermann@firma.de"
        assert sess["role"] == oidc_auth.ROLE_USER

    # Der angemeldete SSO-Benutzer erreicht den geschützten Bereich.
    assert c.get("/").status_code == 200


def test_callback_lehnt_deaktiviertes_konto_ab(client, monkeypatch):
    c, app_module = client
    for schluessel, wert in OIDC_ENV.items():
        monkeypatch.setenv(schluessel, wert)
    oidc_auth.configure_oidc(app_module.app)
    erstes = oidc_auth.provision_user(CLAIMS_MUSTERMANN)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?",
                     (erstes["id"],))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        oidc_auth._oauth.entra, "authorize_access_token",
        lambda: {"userinfo": dict(CLAIMS_MUSTERMANN)})

    res = c.get("/auth/oidc/callback")
    assert res.status_code == 403
