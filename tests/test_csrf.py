"""
Tests des CSRF-Schutzes (csrf.py, before_request in app.py).

Bewusst mit TESTING=False: der Schutz ist unter TESTING abgeschaltet
(damit die Bestandstests die Endpunkte direkt ansprechen können) — hier
läuft exakt der Weg des Produktivbetriebs: fehlender, falscher und
gültiger Token.
"""

import re
import tempfile

import pytest

import database
import auth

ADMIN_PW = "Streng-Vertraulich-2026a"
ADMIN_PW_NEU = "Streng-Vertraulich-2026c"


@pytest.fixture()
def client(monkeypatch):
    """Anmeldetestlauf mit aktivem CSRF-Schutz (TESTING=False)."""
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("SECRET_KEY", "test-key")
    database.init_db()
    auth.ensure_default_admin()

    import app as app_module
    app_module.app.config.update(TESTING=False, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    yield c
    app_module.app.config.update(TESTING=True)


def _csrf_aus_formular(html: str) -> str:
    treffer = re.search(
        r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert treffer, "Anmeldeformular enthält keinen CSRF-Token"
    return treffer.group(1)


def _csrf_aus_meta(html: str) -> str:
    treffer = re.search(
        r'meta name="csrf-token" content="([^"]+)"', html)
    assert treffer, "Seite enthält keinen CSRF-Meta-Token"
    return treffer.group(1)


def test_login_ohne_token_wird_abgewiesen(client):
    res = client.post("/auth/login", data={"username": "admin",
                                           "password": ADMIN_PW})
    assert res.status_code == 400
    assert "CSRF" in res.get_data(as_text=True)


def test_login_mit_token_gelingt(client):
    seite = client.get("/auth/login")
    token = _csrf_aus_formular(seite.get_data(as_text=True))

    res = client.post("/auth/login", data={"username": "admin",
                                           "password": ADMIN_PW,
                                           "csrf_token": token})
    assert res.status_code == 302, "gültiger Token muss durchgelassen werden"


def test_login_mit_falschem_token_wird_abgewiesen(client):
    client.get("/auth/login")
    res = client.post("/auth/login", data={"username": "admin",
                                           "password": ADMIN_PW,
                                           "csrf_token": "nur-gemacht"})
    assert res.status_code == 400


def test_api_ohne_header_wird_abgewiesen(client):
    seite = client.get("/auth/login")
    token = _csrf_aus_formular(seite.get_data(as_text=True))
    client.post("/auth/login", data={"username": "admin",
                                     "password": ADMIN_PW,
                                     "csrf_token": token})

    res = client.post("/auth/password", json={
        "old_password": ADMIN_PW, "new_password": ADMIN_PW_NEU,
        "confirm_password": ADMIN_PW_NEU})
    assert res.status_code == 400
    assert "CSRF" in res.get_json()["error"]


def _csrf_meta_nach_anmeldung(client) -> str:
    """Token aus dem Meta-Tag der nächsten Seite holen.

    Nach der Anmeldung landet man auf der erzwungenen Passwortseite
    (Erstzugang) — sie erbt base.html und trägt den Meta-Token.
    """
    seite = client.get("/auth/passwort-aendern")
    return _csrf_aus_meta(seite.get_data(as_text=True))


def test_api_mit_header_gelingt(client):
    seite = client.get("/auth/login")
    token = _csrf_aus_formular(seite.get_data(as_text=True))
    client.post("/auth/login", data={"username": "admin",
                                     "password": ADMIN_PW,
                                     "csrf_token": token})

    # Der Token wird bei der Anmeldung gedreht (session.clear()) — der
    # neue steht im Meta-Tag der nächsten Seite.
    token = _csrf_meta_nach_anmeldung(client)
    res = client.post("/auth/password", json={
        "old_password": ADMIN_PW, "new_password": ADMIN_PW_NEU,
        "confirm_password": ADMIN_PW_NEU},
        headers={"X-CSRF-Token": token})
    assert res.status_code == 200, res.get_json()


def test_get_routen_brauchen_keinen_token(client):
    seite = client.get("/auth/login")
    assert seite.status_code == 200
    token = _csrf_aus_formular(seite.get_data(as_text=True))
    client.post("/auth/login", data={"username": "admin",
                                     "password": ADMIN_PW,
                                     "csrf_token": token})
    # Erstanmeldung → erzwungene Passwortseite; sie und healthz brauchen
    # keinen CSRF-Token (nur ändernde Methoden werden geprüft).
    assert client.get("/auth/passwort-aendern").status_code == 200
    assert client.get("/healthz").status_code == 200
