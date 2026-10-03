"""
Tests der Einstellungsseite und der Schnittstelle dafür.

Hintergrund: Der Mailversand war zuvor über Umgebungsvariablen
konfigurierbar. Das hatte zwei Nachteile: ein ausgeliefertes Image brachte
fremde Zugangsdaten mit, und jedes geänderte Postfachpasswort verlangte
einen neuen Container. Seit v4.2 pflegt ein Administrator die Angaben in der
Anwendung; sie liegen in der Tabelle `settings`.

Geprüft wird hier vor allem die Zugriffskontrolle und dass das
Postfachpasswort die Anwendung nicht verlässt.
"""

import sqlite3
import tempfile

import pytest

import database
import auth
import settings_store

ADMIN_PW = "Streng-Vertraulich-2026a"
ADMIN_PW_NEU = "Streng-Vertraulich-2026b"

GUELTIG = {
    "smtp_host": "smtp.example.de",
    "smtp_port": "587",
    "smtp_starttls": True,
    "smtp_user": "versand@example.de",
    "smtp_password": "Postfach-Geheim-2026",
    "mail_from": "versand@example.de",
    "mail_app_name": "ADR 1000-Punkte-Rechner",
    "mail_app_url": "http://192.168.178.144:5050",
}


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    # Seit v4.3 wird das Postfachpasswort mit einem aus SECRET_KEY
    # abgeleiteten Schlüssel verschlüsselt — Speichern und Lesen müssen
    # denselben Schlüssel verwenden.
    monkeypatch.setenv("SECRET_KEY", "test-key")
    database.init_db()
    auth.ensure_default_admin()

    import app as app_module
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    c.post("/auth/password", json={"old_password": ADMIN_PW,
                                   "new_password": ADMIN_PW_NEU,
                                   "confirm_password": ADMIN_PW_NEU})
    return c


def _rows(sql, params=()):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _als_benutzer_anmelden(client):
    """Legt einen Benutzer an und meldet sich damit an.

    Das Startpasswort darf den Benutzernamen nicht enthalten (Richtlinie) —
    „sachbearbeiter" darf also nicht im Passwort vorkommen.
    """
    start = "Startwort-Fuer-Kollegen"
    res = client.post("/api/users", json={"username": "sachbearbeiter",
                                          "role": "user", "password": start})
    assert res.status_code == 201, res.get_json()
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "sachbearbeiter",
                                     "password": start})
    res = client.post("/auth/password", json={
        "old_password": start,
        "new_password": "Eigenes-Wort-2026-x",
        "confirm_password": "Eigenes-Wort-2026-x"})
    assert res.status_code == 200, res.get_json()


# ─────────────────────────────────────────────────────────────────────
# Seite und Zugriff
# ─────────────────────────────────────────────────────────────────────

def test_seite_ist_fuer_administratoren_erreichbar(client):
    res = client.get("/einstellungen")
    assert res.status_code == 200
    assert "Mailserver" in res.get_data(as_text=True)


def test_seite_ist_fuer_benutzer_gesperrt(client):
    _als_benutzer_anmelden(client)
    assert client.get("/einstellungen").status_code == 403


def test_schnittstelle_ist_fuer_benutzer_gesperrt(client):
    _als_benutzer_anmelden(client)
    assert client.get("/api/settings/mail").status_code == 403
    assert client.put("/api/settings/mail", json=GUELTIG).status_code == 403


# ─────────────────────────────────────────────────────────────────────
# Speichern und Auslesen
# ─────────────────────────────────────────────────────────────────────

def test_leere_antwort_enthaelt_kein_passwort(client):
    res = client.get("/api/settings/mail")
    assert res.status_code == 200
    daten = res.get_json()
    assert "smtp_password" not in daten
    assert daten["password_set"] is False


def test_speichern_und_wieder_auslesen_ohne_passwort(client):
    res = client.put("/api/settings/mail", json=GUELTIG)
    assert res.status_code == 200
    daten = res.get_json()
    assert daten["smtp_host"] == "smtp.example.de"
    assert daten["password_set"] is True
    assert "Postfach-Geheim-2026" not in res.get_data(as_text=True)

    gespeichert = _rows("SELECT value FROM settings WHERE key = 'smtp_password'")
    # Seit v4.3 liegt das Passwort verschlüsselt in der Datenbank —
    # der Klartext taucht in keinem Feld auf.
    assert gespeichert[0]["value"].startswith("enc1:")
    assert "Postfach-Geheim-2026" not in gespeichert[0]["value"]


def test_passwort_bleibt_bei_leerem_feld_erhalten(client):
    client.put("/api/settings/mail", json=GUELTIG)
    ohne = dict(GUELTIG)
    ohne["smtp_password"] = ""
    ohne["smtp_host"] = "neu.example.de"
    res = client.put("/api/settings/mail", json=ohne)
    assert res.status_code == 200
    assert res.get_json()["password_set"] is True

    cfg = settings_store.get_mail_settings()
    assert cfg["smtp_host"] == "neu.example.de"
    assert cfg["smtp_password"] == "Postfach-Geheim-2026"


def test_passwort_kann_geloescht_werden(client):
    client.put("/api/settings/mail", json=GUELTIG)
    ohne = dict(GUELTIG)
    ohne["smtp_password"] = ""
    ohne["clear_password"] = True
    res = client.put("/api/settings/mail", json=ohne)
    assert res.status_code == 200
    assert res.get_json()["password_set"] is False
    assert settings_store.get_mail_settings()["smtp_password"] == ""


def test_audit_log_nennt_nur_feldnamen(client):
    client.put("/api/settings/mail", json=GUELTIG)
    zeilen = _rows("SELECT detail FROM audit_log WHERE entity = 'settings'")
    assert zeilen
    zusammen = " ".join(z["detail"] or "" for z in zeilen)
    assert "smtp_password" in zusammen
    assert "Postfach-Geheim-2026" not in zusammen


# ─────────────────────────────────────────────────────────────────────
# Eingabeprüfung
# ─────────────────────────────────────────────────────────────────────

def test_ungueltiger_port_wird_abgelehnt(client):
    kaputt = dict(GUELTIG, smtp_port="siebenhundert")
    res = client.put("/api/settings/mail", json=kaputt)
    assert res.status_code == 400

    kaputt = dict(GUELTIG, smtp_port="70000")
    assert client.put("/api/settings/mail", json=kaputt).status_code == 400


def test_ungueltiger_absender_wird_abgelehnt(client):
    kaputt = dict(GUELTIG, mail_from="keine-adresse")
    res = client.put("/api/settings/mail", json=kaputt)
    assert res.status_code == 400


def test_ungueltige_app_adresse_wird_abgelehnt(client):
    kaputt = dict(GUELTIG, mail_app_url="192.168.178.144:5050")
    assert client.put("/api/settings/mail", json=kaputt).status_code == 400


def test_starttls_kann_abgeschaltet_werden(client):
    werte = dict(GUELTIG, smtp_starttls=False)
    assert client.put("/api/settings/mail", json=werte).status_code == 200
    assert settings_store.get_mail_settings()["smtp_starttls"] == "0"


# ─────────────────────────────────────────────────────────────────────
# Verbindungsprüfung ohne Zugangsdaten
# ─────────────────────────────────────────────────────────────────────

def test_verbindungspruefung_ohne_angaben_meldet_klar(client):
    res = client.post("/api/settings/mail/test")
    assert res.status_code == 200
    daten = res.get_json()
    assert daten["ok"] is False
    assert "hinterlegt" in daten["message"]
