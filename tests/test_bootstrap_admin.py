"""
Tests des Bootstrap-Schalters für den Erstzugang (auth.py).

Im Konzernbetrieb ist der automatische Erstzugang (admin/admin) üblich-
weise unerwünscht: ADR_BOOTSTRAP_ADMIN=0 legt kein Konto an — der erste
Zugang kommt dann aus dem Identitätsanbieter (SSO) oder aus
`manage.py bootstrap-admin`.
"""

import tempfile

import pytest

import database
import auth
from database import get_db

ADMIN_PW = "Streng-Vertraulich-2026a"


def _benutzer_zahlen() -> int:
    conn = get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def leere_db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    monkeypatch.setenv("SECRET_KEY", "test-key")
    database.init_db()
    return tmp


def test_standard_legt_erstzugang_an(leere_db, monkeypatch):
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.delenv("ADR_BOOTSTRAP_ADMIN", raising=False)
    auth.ensure_default_admin()
    assert _benutzer_zahlen() == 1


def test_deaktivierter_bootstrap_legt_nichts_an(leere_db, monkeypatch):
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("ADR_BOOTSTRAP_ADMIN", "0")
    auth.ensure_default_admin()
    assert _benutzer_zahlen() == 0


def test_mehrfacher_aufruf_mit_schalter_legt_nur_nichts_an(leere_db, monkeypatch):
    """Auch wiederholte Starts mit gesetztem Schalter erzeugen kein Konto.
    Der Schalter gilt pro Start: steht er bei einem späteren Start nicht
    mehr in der Umgebung, verhält sich die Anwendung wie bisher (leere
    Benutzertabelle → Erstzugang). Das gehört so ins Betriebsdokument."""
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("ADR_BOOTSTRAP_ADMIN", "0")
    auth.ensure_default_admin()
    auth.ensure_default_admin()
    assert _benutzer_zahlen() == 0
