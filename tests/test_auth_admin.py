"""
Tests für das Anlegen des ersten Administrators.

Hintergrund: In Version 2.0 kam es beim Start mit mehreren
Gunicorn-Workern zu einer Race-Condition — jeder Worker prüfte, ob die
Tabelle `users` leer ist, und legte dann denselben Administrator an. Der
zweite INSERT scheiterte am UNIQUE-Constraint und der Worker stürzte beim
Boot ab (in CI gefunden). `INSERT OR IGNORE` behebt das.
"""

import sqlite3
import tempfile

import pytest

import database
from auth import ensure_default_admin


@pytest.fixture()
def fresh_db(monkeypatch):
    """Leitet die SQLite-Datei für die Dauer eines Tests in ein Temp-Verzeichnis."""
    tmp = tempfile.mkdtemp()
    db_path = f"{tmp}\\adr-test.db" if "\\" in str(tmp) else f"{tmp}/adr-test.db"
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield db_path


def _user_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
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


def test_passwort_wird_nur_beim_ersten_anlegen_ausgegeben(fresh_db, capsys):
    ensure_default_admin()
    first = capsys.readouterr().out
    capsys.readouterr()
    ensure_default_admin()
    second = capsys.readouterr().out

    assert "ERSTER ADMINISTRATOR WURDE ANGELEGT" in first
    # Beim zweiten Aufruf darf das Passwort nicht erneut ausgegeben werden
    assert "ERSTER ADMINISTRATOR" not in second
    assert second == ""


def test_vorhandener_admin_wird_nicht_ueberschrieben(fresh_db, monkeypatch):
    """
    Existiert der Administrator bereits, darf ein gesetztes
    ADR_ADMIN_PASSWORD das Passwort nicht zurücksetzen — sonst könnte
    jeder mit Zugriff auf die Umgebungsvariable ein fremdes Konto
    übernehmen.
    """
    from werkzeug.security import check_password_hash

    ensure_default_admin()

    conn = sqlite3.connect(fresh_db)
    original = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    conn.close()

    monkeypatch.setenv("ADR_ADMIN_PASSWORD", "ganz-anderes-passwort")
    ensure_default_admin()

    conn = sqlite3.connect(fresh_db)
    now = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    conn.close()

    assert now == original
    assert check_password_hash(now, "ganz-anderes-passwort") is False
