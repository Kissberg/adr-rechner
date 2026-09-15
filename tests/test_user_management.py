"""
Tests der Benutzerverwaltung, Passwortrichtlinie und Sperre gegen Raten.

Hintergrund: Bis v3 gab es keine Möglichkeit, weitere Konten anzulegen —
in einem Betrieb mit mehreren Niederlassungen war das der blocker dafür,
die Anwendung überhaupt auszuliefern. Ebenso fehlte jeder Schutz gegen
automatisiertes Durchprobieren von Passwörtern.
"""

import sqlite3
import tempfile

import pytest

import database
import audit
import auth


# Darf den Benutzernamen nicht enthalten — „Test-Admin-...“ würde von der
# eigenen Richtlinie abgelehnt (das prüft test_passwort_mit_benutzernamen).
ADMIN_PW = "Streng-Vertraulich-2026"
USER_PW = "Muenchen-Sachbearbeiter-2026"


@pytest.fixture()
def client(monkeypatch):
    """Frische Datenbank plus angemeldeter Administrator."""
    tmp = tempfile.mkdtemp()
    db_path = f"{tmp}\\adr-test.db" if "\\" in str(tmp) else f"{tmp}/adr-test.db"
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.delenv("ADR_REQUIRE_ADMIN_PASSWORD", raising=False)

    database.init_db()
    auth.ensure_default_admin()

    import app as app_module
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    return c


def _rows(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _db_path():
    return database.DB_PATH


# ─────────────────────────────────────────────────────────────────────
# Passwortrichtlinie
# ─────────────────────────────────────────────────────────────────────

def test_zu_kurzes_passwort_wird_abgelehnt():
    assert auth.validate_password("kurz", "admin")


def test_passwort_aus_leakliste_wird_abgelehnt():
    assert auth.validate_password("passwort1234", "admin")


def test_passwort_mit_benutzernamen_wird_abgelehnt():
    assert auth.validate_password("muenchen01-ist-lang", "muenchen01")


def test_gleichfoermiges_passwort_wird_abgelehnt():
    """„aaaaaaaaaaaa“ erfüllt die Länge, ist aber trivial zu raten."""
    assert auth.validate_password("aaaaaaaaaaaaaa", "admin")


def test_langes_passwort_wird_akzeptiert():
    assert auth.validate_password("Kurier faehrt um acht", "admin") is None


# ─────────────────────────────────────────────────────────────────────
# Konten anlegen
# ─────────────────────────────────────────────────────────────────────

def test_admin_legt_benutzer_an_und_erhaelt_einmalpasswort(client):
    res = client.post("/api/users", json={"username": "muenchen01",
                                          "role": "user"})
    assert res.status_code == 201
    data = res.get_json()
    assert data["username"] == "muenchen01"
    # Erzeugtes Passwort wird genau einmal zurückgegeben …
    assert data["generated_password"]
    assert auth.validate_password(data["generated_password"], "muenchen01") is None

    # … und niemals geloggt.
    eintraege = _rows(_db_path(), "SELECT detail FROM audit_log")
    for e in eintraege:
        assert data["generated_password"] not in (e["detail"] or "")


def test_angelegter_benutzer_ist_aktiv_und_muss_passwort_aendern(client):
    client.post("/api/users", json={"username": "hamburg02", "role": "user"})
    rows = _rows(_db_path(), "SELECT * FROM users WHERE username = 'hamburg02'")
    assert len(rows) == 1
    assert rows[0]["active"] == 1
    assert rows[0]["must_change_password"] == 1


def test_benutzername_mit_ungueltigen_zeichen_wird_abgelehnt(client):
    res = client.post("/api/users", json={"username": "max mustermann"})
    assert res.status_code == 400


def test_doppelter_benutzername_wird_abgelehnt(client):
    client.post("/api/users", json={"username": "koeln03"})
    res = client.post("/api/users", json={"username": "koeln03"})
    assert res.status_code == 409


def test_nicht_admin_darf_keine_benutzer_anlegen(client):
    client.post("/api/users", json={"username": "muenchen01", "role": "user"})
    # Passwort setzen, solange die Administrator-Sitzung noch besteht.
    pw = _password_of(client, "muenchen01", USER_PW)

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})
    # Konto kommt mit fremd gesetztem Passwort → erst wechseln.
    client.post("/auth/password", json={"old_password": pw,
                                        "new_password": "Frisches-Wort-2026"})

    res = client.post("/api/users", json={"username": "boese"})
    assert res.status_code == 403


def _password_of(client, username, password=None):
    """Setzt das Passwort eines Kontos und gibt es zurück (Testhilfe).

    Wird kein Passwort vorgegeben, erzeugt die Anwendung eines — dann ist
    es in der Antwort enthalten, aber nirgends im Log.
    """
    res = client.post(f"/api/users/{_id_of(username)}/password",
                      json={"password": password})
    assert res.status_code == 200, res.get_json()
    return res.get_json().get("generated_password") or password


def _id_of(username):
    rows = _rows(_db_path(), "SELECT id FROM users WHERE username = ?", (username,))
    return rows[0]["id"]


# ─────────────────────────────────────────────────────────────────────
# Erzwungener Passwortwechsel
# ─────────────────────────────────────────────────────────────────────

def test_erzwungener_wechsel_sperrt_alles_andere(client):
    """Ein Konto mit fremd vergebenem Passwort darf nichts anderes tun."""
    client.post("/api/users", json={"username": "muenchen01", "role": "user"})
    pw = _password_of(client, "muenchen01")
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})

    # Fachliche Seiten sind gesperrt …
    res = client.get("/api/kunden")
    assert res.status_code == 403
    assert res.get_json().get("must_change_password") is True

    # … die Seite zum Ändern ist erreichbar.
    assert client.get("/auth/passwort-aendern").status_code == 200


def test_nach_dem_wechsel_ist_der_zugriff_frei(client):
    client.post("/api/users", json={"username": "muenchen01", "role": "user"})
    pw = _password_of(client, "muenchen01")
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})

    res = client.post("/auth/password", json={
        "old_password": pw, "new_password": USER_PW, "confirm_password": USER_PW})
    assert res.status_code == 200

    assert client.get("/api/kunden").status_code == 200
    rows = _rows(_db_path(),
                 "SELECT must_change_password FROM users WHERE username = 'muenchen01'")
    assert rows[0]["must_change_password"] == 0


def test_wechsel_auf_zu_kurzes_passwort_scheitert(client):
    client.post("/api/users", json={"username": "muenchen01", "role": "user"})
    pw = _password_of(client, "muenchen01")
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})

    res = client.post("/auth/password", json={"old_password": pw,
                                              "new_password": "kurz"})
    assert res.status_code == 400


# ─────────────────────────────────────────────────────────────────────
# Sperre gegen Passwortraten
# ─────────────────────────────────────────────────────────────────────

def test_zu_viele_fehlversuche_sperren_das_konto(client, monkeypatch):
    client.get("/auth/logout")
    monkeypatch.setattr(auth, "MAX_LOGIN_ATTEMPTS", 3)

    for _ in range(3):
        client.post("/auth/login", data={"username": "admin", "password": "falsch"})

    # Auch mit richtigem Passwort ist jetzt gesperrt.
    res = client.post("/auth/login", data={"username": "admin", "password": ADMIN_PW},
                      follow_redirects=True)
    assert "Fehlversuche" in res.get_data(as_text=True)


def test_erfolgreiche_anmeldung_setzt_zaehler_zurueck(client, monkeypatch):
    client.get("/auth/logout")
    monkeypatch.setattr(auth, "MAX_LOGIN_ATTEMPTS", 3)

    for _ in range(2):
        client.post("/auth/login", data={"username": "admin", "password": "falsch"})
    assert auth.count_recent_failures("admin") == 2

    client.post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    assert auth.count_recent_failures("admin") == 0


# ─────────────────────────────────────────────────────────────────────
# Schutz vor dem Aussperren
# ─────────────────────────────────────────────────────────────────────

def test_letzter_admin_kann_sich_nicht_selbst_herabstufen(client):
    admin_id = _id_of("admin")
    res = client.put(f"/api/users/{admin_id}", json={"role": "user"})
    assert res.status_code == 400
    assert client.get("/api/users").status_code == 200


def test_letzter_admin_kann_sich_nicht_selbst_deaktivieren(client):
    admin_id = _id_of("admin")
    res = client.delete(f"/api/users/{admin_id}")
    assert res.status_code == 400


def test_zweiter_admin_erlaubt_herabstufung_des_ersten(client):
    """Ein zweiter Administrator darf den ersten herabstufen — aber nicht
    sich selbst, sobald er der letzte ist."""
    res = client.post("/api/users", json={
        "username": "admin2", "role": "admin",
        "password": "Zweiter-Zugang-2026"})
    assert res.status_code == 201

    # Zum Herabstufen muss man als der andere Administrator angemeldet sein —
    # das eigene Konto ist immer geschützt.
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "admin2",
                                     "password": "Zweiter-Zugang-2026"})
    client.post("/auth/password", json={"old_password": "Zweiter-Zugang-2026",
                                        "new_password": "Zweiter-Zugang-2027"})

    admin_id = _id_of("admin")
    assert client.put(f"/api/users/{admin_id}",
                      json={"role": "user"}).status_code == 200

    # admin2 ist nun der letzte Administrator und schützt sich selbst.
    assert client.put(f"/api/users/{_id_of('admin2')}",
                      json={"role": "user"}).status_code == 400


# ─────────────────────────────────────────────────────────────────────
# Deaktivieren statt löschen
# ─────────────────────────────────────────────────────────────────────

def test_deaktivieren_sperrt_die_anmeldung(client):
    client.post("/api/users", json={"username": "muenchen01", "role": "user"})
    pw = _password_of(client, "muenchen01", USER_PW)

    res = client.delete(f"/api/users/{_id_of('muenchen01')}")
    assert res.status_code == 200

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})
    assert client.get("/api/kunden").status_code == 401
