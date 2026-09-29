"""
Tests der Benutzerverwaltung, Passwortrichtlinie und Sperre gegen Raten.

Hintergrund: Bis v3 gab es keine Möglichkeit, weitere Konten anzulegen —
in einem Betrieb mit mehreren Niederlassungen war das der blocker dafür,
die Anwendung überhaupt auszuliefern. Ebenso fehlte jeder Schutz gegen
automatisiertes Durchprobieren von Passwörtern.

Seit v4.1 gilt beim Anlegen: Benutzername + E-Mail-Adresse, das
Anfangspasswort wird erzeugt und per E-Mail zugestellt. Ist kein Versand
eingerichtet (im Test immer der Fall), kommt es einmalig in der Antwort
zurück — ein Konto muss auch ohne Postfach einrichtbar bleiben.
"""

import sqlite3
import tempfile

import pytest

import database
import audit
import auth


# Darf den Benutzernamen nicht enthalten — „Test-Admin-...“ würde von der
# eigenen Richtlinie abgelehnt (das prüft test_passwort_mit_benutzernamen).
ADMIN_PW = "Streng-Vertraulich-2026a"
ADMIN_PW_NEU = "Streng-Vertraulich-2026b"
USER_PW = "Muenchen-Sachbearbeiter-2026"

MAIL = "sachbearbeiter@musterbetrieb.de"


@pytest.fixture()
def client(monkeypatch):
    """Frische Datenbank plus angemeldeter Administrator."""
    tmp = tempfile.mkdtemp()
    db_path = f"{tmp}\\adr-test.db" if "\\" in str(tmp) else f"{tmp}/adr-test.db"
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    # Kein Versand im Test — das erzeugte Passwort kommt aus der Antwort.
    monkeypatch.delenv("ADR_SMTP_USER", raising=False)
    monkeypatch.delenv("ADR_SMTP_PASSWORD", raising=False)

    database.init_db()
    auth.ensure_default_admin()

    import app as app_module
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    # Seit v4.1 ist der Wechsel beim ersten Anmelden erzwungen.
    c.post("/auth/password", json={"old_password": ADMIN_PW,
                                   "new_password": ADMIN_PW_NEU,
                                   "confirm_password": ADMIN_PW_NEU})
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


def _neu(c, username="muenchen01", email=MAIL, rolle="user", **rest):
    payload = {"username": username, "email": email, "role": rolle}
    payload.update(rest)
    return c.post("/api/users", json=payload)


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
    res = _neu(client)
    assert res.status_code == 201
    data = res.get_json()
    assert data["username"] == "muenchen01"
    assert data["email"] == MAIL
    # Ohne Versandkonfiguration kommt das erzeugte Passwort genau einmal
    # zurück …
    assert data["email_sent"] is False
    assert data["email_error"]
    assert data["generated_password"]
    assert auth.validate_password(data["generated_password"], "muenchen01") is None

    # … und wird niemals geloggt.
    eintraege = _rows(_db_path(), "SELECT detail FROM audit_log")
    for e in eintraege:
        assert data["generated_password"] not in (e["detail"] or "")


def test_email_wird_gespeichert(client):
    _neu(client, email="Zustellung@Musterbetrieb.DE")
    rows = _rows(_db_path(),
                 "SELECT email FROM users WHERE username = 'muenchen01'")
    assert rows[0]["email"] == "Zustellung@Musterbetrieb.DE"


def test_angelegter_benutzer_ist_aktiv_und_muss_passwort_aendern(client):
    _neu(client, username="hamburg02")
    rows = _rows(_db_path(), "SELECT * FROM users WHERE username = 'hamburg02'")
    assert len(rows) == 1
    assert rows[0]["active"] == 1
    assert rows[0]["must_change_password"] == 1


def test_benutzername_mit_ungueltigen_zeichen_wird_abgelehnt(client):
    res = _neu(client, username="max mustermann")
    assert res.status_code == 400


def test_konto_ohne_email_ist_moeglich(client):
    """Der Mailserver ist optional — das Konto entsteht trotzdem."""
    res = client.post("/api/users", json={"username": "muenchen01",
                                          "role": "user",
                                          "password": "Startwort-Fuer-Muenchen"})
    assert res.status_code == 201
    data = res.get_json()
    assert data["email"] is None
    assert data["email_sent"] is False
    # Kein erzeugtes Passwort in der Antwort, weil der Administrator
    # selbst eines vergeben hat.
    assert data["generated_password"] is None

    rows = _rows(_db_path(),
                 "SELECT email FROM users WHERE username = 'muenchen01'")
    assert rows[0]["email"] is None


def test_vorgegebenes_passwort_erzwingt_wechsel_beim_ersten_login(client):
    start = "Startwort-Fuer-Muenchen"
    assert client.post("/api/users", json={
        "username": "muenchen01", "role": "user", "password": start},
    ).status_code == 201

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": start})
    # Erst wechseln, dann arbeiten.
    assert client.get("/api/kunden").status_code == 403
    assert client.post("/auth/password", json={
        "old_password": start, "new_password": USER_PW,
        "confirm_password": USER_PW}).status_code == 200
    assert client.get("/api/kunden").status_code == 200


def test_vorgegebenes_passwort_muss_die_richtlinie_erfuellen(client):
    res = client.post("/api/users", json={"username": "muenchen01",
                                          "role": "user", "password": "kurz"})
    assert res.status_code == 400


def test_ungueltige_email_wird_abgelehnt(client):
    res = _neu(client, email="keine-adresse")
    assert res.status_code == 400


def test_email_kann_nachtraeglich_geaendert_werden(client):
    _neu(client, email="tippfehler@musterbetrieb.de")
    uid = _id_of("muenchen01")

    res = client.put(f"/api/users/{uid}", json={"email": "richtig@musterbetrieb.de"})
    assert res.status_code == 200
    assert res.get_json()["email"] == "richtig@musterbetrieb.de"
    rows = _rows(_db_path(),
                 "SELECT email FROM users WHERE id = ?", (uid,))
    assert rows[0]["email"] == "richtig@musterbetrieb.de"


def test_geaenderte_email_wird_geprueft(client):
    _neu(client)
    uid = _id_of("muenchen01")
    res = client.put(f"/api/users/{uid}", json={"email": "keine-adresse"})
    assert res.status_code == 400
    # Unverändert geblieben.
    rows = _rows(_db_path(), "SELECT email FROM users WHERE id = ?", (uid,))
    assert rows[0]["email"] == MAIL


def test_email_kann_geleert_werden(client):
    _neu(client)
    uid = _id_of("muenchen01")
    res = client.put(f"/api/users/{uid}", json={"email": ""})
    assert res.status_code == 200
    rows = _rows(_db_path(), "SELECT email FROM users WHERE id = ?", (uid,))
    assert rows[0]["email"] is None


def test_email_aenderung_wird_protokolliert(client):
    _neu(client)
    uid = _id_of("muenchen01")
    client.put(f"/api/users/{uid}", json={"email": "neu@musterbetrieb.de"})
    eintraege = _rows(_db_path(),
                      "SELECT detail FROM audit_log WHERE entity = 'user' AND "
                      "entity_id = ? AND action = 'update'", (uid,))
    assert any("email" in (e["detail"] or "") for e in eintraege)


def test_doppelter_benutzername_wird_abgelehnt(client):
    _neu(client, username="koeln03")
    res = _neu(client, username="koeln03")
    assert res.status_code == 409


def test_nicht_admin_darf_keine_benutzer_anlegen(client):
    _neu(client)
    # Passwort setzen, solange die Administrator-Sitzung noch besteht.
    pw = _password_of(client, "muenchen01", USER_PW)

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})
    # Konto kommt mit fremd gesetztem Passwort → erst wechseln.
    client.post("/auth/password", json={"old_password": pw,
                                        "new_password": "Frisches-Wort-2026"})

    res = _neu(client, username="boese")
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
    _neu(client)
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
    _neu(client)
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
    _neu(client)
    pw = _password_of(client, "muenchen01")
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})

    res = client.post("/auth/password", json={"old_password": pw,
                                              "new_password": "kurz"})
    assert res.status_code == 400


# ─────────────────────────────────────────────────────────────────────
# Passwort zurücksetzen
# ─────────────────────────────────────────────────────────────────────

def test_zuruecksetzen_erzwingt_erneuten_wechsel(client):
    _neu(client)
    pw = _password_of(client, "muenchen01", USER_PW)
    # Nach dem Zurücksetzen ist der Wechsel wieder offen.
    rows = _rows(_db_path(),
                 "SELECT must_change_password FROM users WHERE username = 'muenchen01'")
    assert rows[0]["must_change_password"] == 1
    assert pw == USER_PW


# ─────────────────────────────────────────────────────────────────────
# Sperre gegen Passwortraten
# ─────────────────────────────────────────────────────────────────────

def test_zu_viele_fehlversuche_sperren_das_konto(client, monkeypatch):
    client.get("/auth/logout")
    monkeypatch.setattr(auth, "MAX_LOGIN_ATTEMPTS", 3)

    for _ in range(3):
        client.post("/auth/login", data={"username": "admin", "password": "falsch"})

    # Auch mit richtigem Passwort ist jetzt gesperrt.
    res = client.post("/auth/login", data={"username": "admin",
                                           "password": ADMIN_PW_NEU},
                      follow_redirects=True)
    assert "Fehlversuche" in res.get_data(as_text=True)


def test_erfolgreiche_anmeldung_setzt_zaehler_zurueck(client, monkeypatch):
    client.get("/auth/logout")
    monkeypatch.setattr(auth, "MAX_LOGIN_ATTEMPTS", 3)

    for _ in range(2):
        client.post("/auth/login", data={"username": "admin", "password": "falsch"})
    assert auth.count_recent_failures("admin") == 2

    client.post("/auth/login", data={"username": "admin", "password": ADMIN_PW_NEU})
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
    res = client.put(f"/api/users/{admin_id}", json={"active": False})
    assert res.status_code == 400


def test_letzter_admin_kann_sich_nicht_selbst_loeschen(client):
    admin_id = _id_of("admin")
    res = client.delete(f"/api/users/{admin_id}")
    assert res.status_code == 400
    assert _rows(_db_path(), "SELECT id FROM users WHERE id = ?", (admin_id,))


def test_zweiter_admin_erlaubt_herabstufung_des_ersten(client):
    """Ein zweiter Administrator darf den ersten herabstufen — aber nicht
    sich selbst, sobald er der letzte ist."""
    res = _neu(client, username="admin2", rolle="admin",
               email="admin2@musterbetrieb.de", password="Zweiter-Zugang-2026")
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


def test_letzter_aktiver_admin_kann_nicht_geloescht_werden(client):
    """Ein zweiter Administrator darf den ersten löschen — der letzte nicht."""
    _neu(client, username="admin2", rolle="admin",
         email="admin2@musterbetrieb.de", password="Zweiter-Zugang-2026")
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "admin2",
                                     "password": "Zweiter-Zugang-2026"})
    client.post("/auth/password", json={"old_password": "Zweiter-Zugang-2026",
                                        "new_password": "Zweiter-Zugang-2027"})

    # Der andere Administrator lässt sich löschen …
    assert client.delete(f"/api/users/{_id_of('admin')}").status_code == 200
    # … der eigene, nun letzte, nicht.
    assert client.delete(f"/api/users/{_id_of('admin2')}").status_code == 400


# ─────────────────────────────────────────────────────────────────────
# Deaktivieren und Löschen — zwei getrennte Vorgänge
# ─────────────────────────────────────────────────────────────────────

def test_deaktivieren_erhaelt_das_konto(client):
    _neu(client)
    pw = _password_of(client, "muenchen01", USER_PW)

    res = client.put(f"/api/users/{_id_of('muenchen01')}", json={"active": False})
    assert res.status_code == 200

    # Die Zeile bleibt bestehen — nur die Anmeldung ist gesperrt.
    rows = _rows(_db_path(),
                 "SELECT active FROM users WHERE username = 'muenchen01'")
    assert rows and rows[0]["active"] == 0

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})
    assert client.get("/api/kunden").status_code == 401


def test_wieder_aktivieren_ist_moeglich(client):
    _neu(client)
    _password_of(client, "muenchen01", USER_PW)
    uid = _id_of("muenchen01")
    client.put(f"/api/users/{uid}", json={"active": False})
    assert client.put(f"/api/users/{uid}", json={"active": True}).status_code == 200

    rows = _rows(_db_path(),
                 "SELECT active FROM users WHERE username = 'muenchen01'")
    assert rows[0]["active"] == 1


def test_loeschen_entfernt_die_zeile(client):
    _neu(client)
    uid = _id_of("muenchen01")

    res = client.delete(f"/api/users/{uid}")
    assert res.status_code == 200
    assert res.get_json()["deleted"] is True

    assert _rows(_db_path(), "SELECT id FROM users WHERE id = ?", (uid,)) == []
    # Aus der Liste verschwunden …
    namen = [u["username"] for u in client.get("/api/users").get_json()["users"]]
    assert "muenchen01" not in namen


def test_loeschen_wird_protokolliert(client):
    _neu(client, email="kollege@musterbetrieb.de")
    uid = _id_of("muenchen01")
    client.delete(f"/api/users/{uid}")

    eintraege = _rows(_db_path(),
                      "SELECT detail FROM audit_log WHERE entity = 'user' AND "
                      "entity_id = ? AND action = 'delete'", (uid,))
    assert eintraege
    assert "muenchen01" in eintraege[0]["detail"]
    assert "kollege@musterbetrieb.de" in eintraege[0]["detail"]


def test_geloeschtes_konto_kann_nicht_mehr_anmelden(client):
    _neu(client)
    pw = _password_of(client, "muenchen01", USER_PW)
    client.delete(f"/api/users/{_id_of('muenchen01')}")

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "muenchen01", "password": pw})
    assert client.get("/api/kunden").status_code == 401


def test_name_nach_dem_loeschen_wieder_verwendbar(client):
    """Ein gelöschtes Konto darf den Namen nicht dauerhaft blockieren."""
    _neu(client)
    uid = _id_of("muenchen01")
    # Fehlversuche erzeugen, damit auch die Sperrtabelle geprüft wird.
    auth.record_failed_attempt("muenchen01", "127.0.0.1")
    client.delete(f"/api/users/{uid}")

    assert _rows(_db_path(),
                 "SELECT id FROM login_attempts WHERE username = 'muenchen01'") == []
    # Neuanlage muss wieder möglich sein (kein 409).
    assert _neu(client, email="neu@musterbetrieb.de").status_code == 201


def test_loeschen_hinterlaesst_keine_waisen_in_der_liste(client):
    _neu(client)
    client.delete(f"/api/users/{_id_of('muenchen01')}")
    for u in client.get("/api/users").get_json()["users"]:
        assert u["username"] == "admin"
