"""
Tests der Verschlüsselung ruhender Zugangsdaten (settings_store).

Kernzusicherungen:
  * Das Postfachpasswort liegt in der Datenbank nie im Klartext.
  * Anwendungsintern liest es sich unverändert zurück (Versand funktioniert).
  * Falscher Schlüssel → leerer Wert, kein Fehler, kein Klartext.
  * Bestand aus Klartext (vor v4.3) bleibt lesbar und lässt sich migrieren.
"""

import sqlite3
import tempfile

import pytest

import database
import auth
import settings_store

ADMIN_PW = "Streng-Vertraulich-2026a"
PASSWORT = "Postfach-Geheim-2026"


@pytest.fixture()
def db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    monkeypatch.setenv("SECRET_KEY", "tester-schluessel")
    database.init_db()
    return tmp


def _roh_wert() -> str:
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        zeile = conn.execute(
            "SELECT value FROM settings WHERE key = 'smtp_password'"
        ).fetchone()
        return zeile["value"] if zeile else ""
    finally:
        conn.close()


def test_rundlauf_verschluesselt(db):
    gespeichert = settings_store.encrypt_secret(PASSWORT)
    assert gespeichert.startswith("enc1:")
    assert PASSWORT not in gespeichert
    assert settings_store.decrypt_secret(gespeichert) == PASSWORT


def test_datenbank_enthaelt_keinen_klartext(db):
    settings_store.set_mail_settings(
        {"smtp_password": PASSWORT}, actor="test")
    roh = _roh_wert()
    assert roh.startswith("enc1:")
    assert PASSWORT not in roh
    assert settings_store.get_mail_settings()["smtp_password"] == PASSWORT


def test_falscher_schluessel_liest_leer(db, monkeypatch):
    settings_store.set_mail_settings(
        {"smtp_password": PASSWORT}, actor="test")
    # Neuer Schlüssel (z. B. rotiertes SECRET_KEY nach Upgrade):
    monkeypatch.setenv("SECRET_KEY", "ein-ganz-anderer-schluessel")
    assert settings_store.get_mail_settings()["smtp_password"] == ""
    assert not settings_store.mail_settings_configured()


def test_bestand_klartext_bleibt_lesbar(db):
    """Zeilen aus der Zeit vor v4.3 ohne Präfix lesen als Klartext."""
    conn = sqlite3.connect(database.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('smtp_password', ?)",
            (PASSWORT,))
        conn.commit()
    finally:
        conn.close()
    assert settings_store.get_mail_settings()["smtp_password"] == PASSWORT
    assert not settings_store.smtp_password_is_encrypted()


def test_migrate_verschluesselt_bestand(db):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('smtp_password', ?)",
            (PASSWORT,))
        conn.commit()
    finally:
        conn.close()

    assert settings_store.smtp_password_is_encrypted() is False
    verschluesselt = settings_store.encrypt_secret(PASSWORT)
    conn = sqlite3.connect(database.DB_PATH)
    try:
        conn.execute("UPDATE settings SET value = ? "
                     "WHERE key = 'smtp_password'", (verschluesselt,))
        conn.commit()
    finally:
        conn.close()
    assert settings_store.smtp_password_is_encrypted() is True
    # Funktion bleibt erhalten: der Versand liest denselben Klartext.
    assert settings_store.get_mail_settings()["smtp_password"] == PASSWORT


def test_leerer_wert_bleibt_leer(db):
    assert settings_store.encrypt_secret("") == ""
    assert settings_store.decrypt_secret("") == ""
