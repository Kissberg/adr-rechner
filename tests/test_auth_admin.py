"""
Tests für das Anlegen des ersten Administrators.

Hintergrund: In Version 2.0 kam es beim Start mit mehreren
Gunicorn-Workern zu einer Race-Condition — jeder Worker prüfte, ob die
Tabelle `users` leer ist, und legte dann denselben Administrator an. Der
zweite INSERT scheiterte am UNIQUE-Constraint und der Worker stürzte beim
Boot ab (in CI gefunden). `INSERT OR IGNORE` behebt das.
"""

import os
import sqlite3
import subprocess
import tempfile

import pytest

import database
import auth
from auth import ensure_default_admin
from werkzeug.security import check_password_hash


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


# ─────────────────────────────────────────────────────────────────────
# Erzeugtes Passwort darf nicht ins Log
# ─────────────────────────────────────────────────────────────────────
# Hintergrund: Logs sind breiter lesbar und länger verfügbar als die
# Anwendung selbst — `docker logs`, json-file-Treiber, ELK/Loki. Ein
# erzeugtes Admin-Passwort im Log ist faktisch ein dauerhaft gültiger
# Admin-Zugang für alle, die Logs lesen dürfen.

def _password_from_file(path):
    """Liest das erzeugte Passwort aus der Passwortdatei."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("Passwort"):
                return line.split(":", 1)[1].strip()
    raise AssertionError(f"Kein Passwort in {path} gefunden")


def test_erzeugtes_passwort_steht_nicht_im_log(fresh_db, capsys):
    """Kernforderung: das Passwort darf nicht nach stdout gelangen."""
    ensure_default_admin()
    out = capsys.readouterr().out

    path = auth.admin_password_path()
    password = _password_from_file(path)

    assert "ERSTER ADMINISTRATOR WURDE ANGELEGT" in out
    assert password not in out, "Passwort wurde ins Log geschrieben"
    assert password not in capsys.readouterr().err


def test_erzeugtes_passwort_liegt_in_datei(fresh_db, monkeypatch):
    ensure_default_admin()
    path = auth.admin_password_path()
    assert os.path.exists(path)

    password = _password_from_file(path)
    assert len(password) >= 16, "erzeugtes Passwort ist zu kurz"

    # Das gespeicherte Passwort muss tatsächlich zum Konto passen.
    conn = sqlite3.connect(fresh_db)
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    conn.close()
    assert check_password_hash(stored, password)


def _andere_konten_duerfen_lesen(path):
    """Kontoname eines fremden ACE mit Zugriff, sonst None (nur Windows)."""
    # icacls schreibt in der Systemcodeseite (unter Windows cp850/cp1252),
    # nicht in UTF-8. Die relevanten Teile (SID, Rechte) sind ASCII —
    # Ersetzungszeichen im deutschen Resttext sind hier unerheblich.
    out = subprocess.run(["icacls", path], capture_output=True)
    text = out.stdout.decode("utf-8", errors="replace")

    sid = auth._current_user_sid()
    fremd = []
    for line in text.splitlines()[1:]:          # Zeile 0 ist der Dateiname
        line = line.strip()
        if not line or not line.startswith(("(", "*", "VORDEFINIERT",
                                            "JEDER", "BUILTIN", "NT-",
                                            "Everyone")) and not sid:
            continue
        if sid and sid in line:
            continue                            # eigener Zugriffseintrag
        if line.startswith("(") or ":" not in line:
            continue
        fremd.append(line)
    return fremd or None


def test_passwortdatei_ist_nur_fuer_den_besitzer_lesbar(fresh_db):
    ensure_default_admin()
    path = auth.admin_password_path()

    if os.name == "nt":
        assert auth.restrict_to_owner(path), "Rechte konnten nicht gesetzt werden"
        fremd = _andere_konten_duerfen_lesen(path)
        assert fremd is None, f"Fremde Zugriffseinträge: {fremd}"
        return

    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600, f"Passwortdatei hat Modus {oct(mode)}, erwartet 0o600"


def test_passwortdatei_pfad_ist_konfigurierbar(fresh_db, monkeypatch, tmp_path):
    custom = tmp_path / "anderer-ort.txt"
    monkeypatch.setenv("ADR_ADMIN_PASSWORD_FILE", str(custom))
    ensure_default_admin()
    assert custom.exists()
    assert _password_from_file(str(custom))


# ─────────────────────────────────────────────────────────────────────
# Strenger Start für den Produktivbetrieb
# ─────────────────────────────────────────────────────────────────────

def test_strenger_start_ohne_passwort_verweigert(fresh_db, monkeypatch, capsys):
    """ADR_REQUIRE_ADMIN_PASSWORD=1 ohne Passwort: Start verweigern."""
    monkeypatch.delenv("ADR_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ADR_REQUIRE_ADMIN_PASSWORD", "1")

    with pytest.raises(RuntimeError, match="ADR_ADMIN_PASSWORD"):
        ensure_default_admin()

    # Es darf kein Konto und keine Passwortdatei entstanden sein
    assert _user_count(fresh_db) == 0
    assert not os.path.exists(auth.admin_password_path())
    assert capsys.readouterr().out == ""


def test_strenger_start_mit_passwort_erlaubt(fresh_db, monkeypatch):
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", "sicheres-start-passwort")
    monkeypatch.setenv("ADR_REQUIRE_ADMIN_PASSWORD", "1")
    ensure_default_admin()

    conn = sqlite3.connect(fresh_db)
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    conn.close()
    assert check_password_hash(stored, "sicheres-start-passwort")
    # Bei gesetztem Passwort wird keine Datei geschrieben
    assert not os.path.exists(auth.admin_password_path())


def test_ohne_strengen_start_wird_passwort_erzeugt(fresh_db, monkeypatch):
    """Ohne ADR_REQUIRE_ADMIN_PASSWORD bleibt das bisherige Verhalten."""
    monkeypatch.delenv("ADR_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADR_REQUIRE_ADMIN_PASSWORD", raising=False)
    ensure_default_admin()
    assert _user_count(fresh_db) == 1
    assert os.path.exists(auth.admin_password_path())


# ─────────────────────────────────────────────────────────────────────
# Passwortdatei wird nach dem Ändern entfernt
# ─────────────────────────────────────────────────────────────────────

def test_passwortdatei_wird_nach_aenderung_geloescht(fresh_db, monkeypatch):
    ensure_default_admin()
    path = auth.admin_password_path()
    assert os.path.exists(path)
    password = _password_from_file(path)

    conn = sqlite3.connect(fresh_db)
    uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
    conn.close()

    from flask import Flask, session
    app = Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/auth/password", method="POST",
                                  json={"old_password": password,
                                        "new_password": "Neues-Passwort-2026"}):
        session["user_id"] = uid
        response = auth.change_password()

    assert response.status_code == 200, response.get_data(as_text=True)
    assert not os.path.exists(path), "Passwortdatei wurde nicht gelöscht"

    conn = sqlite3.connect(fresh_db)
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    conn.close()
    assert check_password_hash(stored, "Neues-Passwort-2026")
