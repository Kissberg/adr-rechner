"""
Tests des Erstzugangs (Administrator beim ersten Start).

Hintergrund: Seit v4.1 entsteht der erste Administrator mit dem
dokumentierten Standard admin/admin und dem erzwungenen Passwortwechsel.
Vorher erzeugte die Anwendung ein Zufallspasswort und legte es in einer
Datei `.admin_password` ab — das war ein zusätzlicher Betriebsschritt
(Datei lesen, Rechte prüfen, Datei löschen) und eine Fehlerquelle, wenn
die Datei nicht geschrieben werden konnte.

Ein fester Standard ist nur zulässig, solange er ausschließlich bis zur
ersten Anmeldung gilt. Genau das wird hier geprüft.

Wichtig bei mehreren Gunicorn-Workern: Alle Worker führen diesen Code beim
Import aus. Ein normales "erst prüfen, dann einfügen" hat eine
Race-Condition (beide sehen eine leere Tabelle, der zweite INSERT scheitert
am UNIQUE-Constraint und der Worker stürzt beim Boot ab). INSERT OR IGNORE
behebt das.
"""

import sqlite3
import tempfile

import pytest
from werkzeug.security import check_password_hash

import database
import auth
from auth import ensure_default_admin


@pytest.fixture()
def fresh_db(monkeypatch):
    """Leitet die SQLite-Datei für die Dauer eines Tests in ein Temp-Verzeichnis."""
    tmp = tempfile.mkdtemp()
    db_path = f"{tmp}\\adr-test.db" if "\\" in str(tmp) else f"{tmp}/adr-test.db"
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.delenv("ADR_ADMIN_USER", raising=False)
    monkeypatch.delenv("ADR_ADMIN_PASSWORD", raising=False)
    database.init_db()
    yield db_path


def _user_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def _row(db_path, username="admin"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users WHERE username = ?",
                            (username,)).fetchone()
    finally:
        conn.close()


def test_admin_wird_einmal_angelegt(fresh_db, capsys):
    ensure_default_admin()
    assert _user_count(fresh_db) == 1


def test_wiederholter_aufruf_legt_kein_duplikat_an(fresh_db, capsys):
    """Zweiter Aufruf darf weder einen Fehler werfen noch einen Duplikat erzeugen."""
    ensure_default_admin()
    ensure_default_admin()
    ensure_default_admin()
    assert _user_count(fresh_db) == 1


def test_banner_wird_nur_beim_ersten_anlegen_ausgegeben(fresh_db, capsys):
    ensure_default_admin()
    first = capsys.readouterr().out
    ensure_default_admin()
    second = capsys.readouterr().out

    assert "ERSTER ADMINISTRATOR WURDE ANGELEGT" in first
    assert second == ""


# ─────────────────────────────────────────────────────────────────────
# Der dokumentierte Erstzugang
# ─────────────────────────────────────────────────────────────────────

def test_ohne_umgebungsvariablen_gilt_admin_admin(fresh_db, capsys):
    ensure_default_admin()

    row = _row(fresh_db)
    assert row is not None
    assert row["username"] == "admin"
    assert check_password_hash(row["password_hash"], "admin")

    out = capsys.readouterr().out
    assert "Benutzername : admin" in out
    assert "Passwort     : admin" in out


def test_erstzugang_erzwingt_den_wechsel(fresh_db):
    """Kernbedingung: der Standard darf nur bis zur ersten Anmeldung gelten."""
    ensure_default_admin()
    row = _row(fresh_db)
    assert row["must_change_password"] == 1
    assert row["role"] == "admin"
    assert row["active"] == 1


def test_standardpasswort_ist_nicht_das_endgueltige(fresh_db):
    """„admin" erfüllt die Richtlinie nicht — es kann also kein Endzustand sein."""
    assert auth.validate_password("admin", "admin") is not None


def test_umgebungsvariable_hat_vorrang(fresh_db, monkeypatch, capsys):
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", "Eigenes-Startwort-2026")
    ensure_default_admin()

    row = _row(fresh_db)
    assert check_password_hash(row["password_hash"], "Eigenes-Startwort-2026")
    # Auch hier gilt: erst wechseln.
    assert row["must_change_password"] == 1

    # Aus der Umgebung gesetzte Werte sind Geheimnisse — nicht ins Log.
    out = capsys.readouterr().out
    assert "Eigenes-Startwort-2026" not in out


def test_benutzername_ist_konfigurierbar(fresh_db, monkeypatch):
    monkeypatch.setenv("ADR_ADMIN_USER", "oberadmin")
    ensure_default_admin()

    assert _row(fresh_db, "oberadmin") is not None
    assert _row(fresh_db, "admin") is None


def test_vorhandener_admin_wird_nicht_ueberschrieben(fresh_db, monkeypatch):
    """
    Existiert der Administrator bereits, darf ein gesetztes
    ADR_ADMIN_PASSWORD das Passwort nicht zurücksetzen — sonst könnte
    jeder mit Zugriff auf die Umgebungsvariable ein fremdes Konto
    übernehmen.
    """
    ensure_default_admin()
    original = _row(fresh_db)["password_hash"]

    monkeypatch.setenv("ADR_ADMIN_PASSWORD", "ganz-anderes-passwort")
    ensure_default_admin()

    now = _row(fresh_db)["password_hash"]
    assert now == original
    assert check_password_hash(now, "ganz-anderes-passwort") is False


def test_admin_wird_nicht_angelegt_wenn_andere_konten_existieren(fresh_db):
    """Der Erstzugang gilt nur für die leere Datenbank."""
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO users (username, password_hash, role, active, "
        "must_change_password) VALUES ('muenchen01', 'x', 'user', 1, 0)")
    conn.commit()
    conn.close()

    ensure_default_admin()
    assert _user_count(fresh_db) == 1
    assert _row(fresh_db, "admin") is None


# ─────────────────────────────────────────────────────────────────────
# Der erzwungene Wechsel greift wirklich für jede Route
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture()
def anmeldung(fresh_db, monkeypatch):
    """Angemeldeter Administrator im Zustand „Passwortwechsel offen"."""
    # Das Modul `app` legt beim Import nur beim allerersten Import an —
    # in der Testsitzung ist das längst passiert.
    ensure_default_admin()
    import app as app_module
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": "admin"})
    return c


def test_erster_login_fuehrt_zur_passwortseite(anmeldung):
    res = anmeldung.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/passwort-aendern" in res.headers["Location"]


def test_api_ist_vor_dem_wechsel_gesperrt(anmeldung):
    res = anmeldung.get("/api/kunden")
    assert res.status_code == 403
    assert res.get_json().get("must_change_password") is True


def test_wechsel_auf_das_standardpasswort_ist_verboten(anmeldung):
    """Sonst könnte „admin" dauerhaft bestehen bleiben."""
    res = anmeldung.post("/auth/password", json={
        "old_password": "admin", "new_password": "admin",
        "confirm_password": "admin"})
    assert res.status_code == 400

    row = _row(database.DB_PATH)
    assert row["must_change_password"] == 1


def test_nach_dem_wechsel_ist_die_anwendung_frei(anmeldung):
    neues = "Erstes-Eigenes-Wort-2026"
    res = anmeldung.post("/auth/password", json={
        "old_password": "admin", "new_password": neues,
        "confirm_password": neues})
    assert res.status_code == 200, res.get_json()

    assert anmeldung.get("/api/kunden").status_code == 200
    row = _row(database.DB_PATH)
    assert row["must_change_password"] == 0
    assert check_password_hash(row["password_hash"], neues)
