"""
Tests für die Datenschutz-Funktionen.

Deckt die drei Stellen ab, an denen die Anwendung personenbezogene Daten
über die eigentlichen Stammdaten hinaus speicherte oder liegen ließ:

  1. Das Audit-Log schrieb bei Kundenänderungen die vollständigen Werte
     mit (Name, Ansprechpartner, Telefon, E-Mail) — eine Löschung nach
     Art. 17 DSGVO lief dadurch ins Leere.
  2. Erzeugte Beförderungspapier-PDFs blieben nach dem Löschen einer
     Sendung im Exportverzeichnis liegen.
  3. Eine Auskunft nach Art. 15 / Herausgabe nach Art. 20 DSGVO war gar
     nicht möglich; es gab nur eine Importvorlage.
"""

import json
import os
import sqlite3
import tempfile

import pytest

import audit
import befoerderungspapier
import database
import auth


ADMIN_PW = "Streng-Vertraulich-2026"


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    db_path = f"{tmp}\\adr-test.db" if "\\" in str(tmp) else f"{tmp}/adr-test.db"
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setenv("ADR_ADMIN_USER", "admin")
    monkeypatch.setenv("ADR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.delenv("ADR_REQUIRE_ADMIN_PASSWORD", raising=False)

    export_dir = os.path.join(tmp, "exports")
    os.makedirs(export_dir, exist_ok=True)
    monkeypatch.setattr(befoerderungspapier, "EXPORT_DIR", export_dir)

    database.init_db()
    auth.ensure_default_admin()

    import app as app_module
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key")
    c = app_module.app.test_client()
    c.post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    return c


def _rows(sql, params=()):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _neuer_kunde(name="Muster GmbH", kontakt="Frau Schmidt",
                 telefon="+49 89 123", email="s@muster.de"):
    conn = database.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO customers (name, street, zip, city, contact, phone, email) "
            "VALUES (?, 'Hauptstr. 1', '80331', 'Muenchen', ?, ?, ?)",
            (name, kontakt, telefon, email))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# 1. Audit-Log ohne personenbezogene Werte
# ─────────────────────────────────────────────────────────────────────

def test_changed_fields_nennt_nur_feldnamen():
    before = {"name": "A", "contact": "Frau Schmidt", "phone": "111"}
    after = {"name": "A", "contact": "Herr Meier", "phone": "222"}
    ergebnis = audit.changed_fields(before, after, ("name", "contact", "phone"))
    assert ergebnis == "contact, phone"
    assert "Frau Schmidt" not in ergebnis
    assert "Herr Meier" not in ergebnis


def test_kundenänderung_schreibt_keine_werte_ins_audit_log(client):
    kid = _neuer_kunde()
    res = client.put(f"/api/kunden/{kid}", json={
        "name": "Muster GmbH", "street": "Hauptstr. 1", "zip": "80331",
        "city": "Muenchen", "contact": "Herr Meier",
        "phone": "+49 89 999", "email": "neu@muster.de"})
    assert res.status_code == 200

    eintraege = _rows("SELECT detail FROM audit_log WHERE entity = 'customer'")
    assert eintraege, "Änderung wurde nicht protokolliert"
    zusammen = " ".join(e["detail"] or "" for e in eintraege)

    # Das Log muss die Änderung belegen …
    assert "contact" in zusammen
    # … darf aber keine personenbezogenen Werte enthalten.
    for wert in ("Frau Schmidt", "Herr Meier", "+49 89 999",
                 "neu@muster.de", "s@muster.de"):
        assert wert not in zusammen, f"{wert!r} steht im Audit-Log"


def test_kundenlöschung_hinterlaesst_keinen_namen_im_log(client):
    kid = _neuer_kunde(name="Einzelunternehmen Schmidt")
    assert client.delete(f"/api/kunden/{kid}").status_code == 200

    zusammen = " ".join(
        (e["detail"] or "") for e in _rows("SELECT detail FROM audit_log"))
    assert "Schmidt" not in zusammen


def test_adressänderung_schreibt_keine_werte_ins_audit_log(client):
    conn = database.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO shipping_addresses (name, street, zip, city) "
            "VALUES ('Lager Nord', 'Weg 5', '20095', 'Hamburg')")
        conn.commit()
        aid = cur.lastrowid
    finally:
        conn.close()

    client.put(f"/api/shipping-addresses/{aid}", json={
        "name": "Lager Nord", "street": "Weg 7", "zip": "20095",
        "city": "Hamburg"})

    eintraege = _rows("SELECT detail FROM audit_log WHERE entity = 'shipping_address'")
    zusammen = " ".join(e["detail"] or "" for e in eintraege)
    assert "street" in zusammen
    assert "Weg 7" not in zusammen


# ─────────────────────────────────────────────────────────────────────
# 2. Aufbewahrungsfrist
# ─────────────────────────────────────────────────────────────────────

def test_purge_entfernt_nur_alte_einträge(client):
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO audit_log (created_at, action, entity, detail) "
            "VALUES (datetime('now', '-4000 days'), 'update', 'test', 'uralt')")
        conn.execute(
            "INSERT INTO audit_log (created_at, action, entity, detail) "
            "VALUES (datetime('now', '-1 days'), 'update', 'test', 'frisch')")
        conn.commit()
    finally:
        conn.close()

    entfernt = audit.purge_old_entries(3650)
    assert entfernt == 1

    übrig = [e["detail"] for e in _rows("SELECT detail FROM audit_log")]
    assert "uralt" not in übrig
    assert "frisch" in übrig


def test_ip_adresse_kann_abgeschaltet_werden(client, monkeypatch):
    monkeypatch.setenv("ADR_AUDIT_LOG_IP", "0")
    kid = _neuer_kunde()
    client.put(f"/api/kunden/{kid}", json={
        "name": "Muster GmbH", "contact": "Neu", "street": "s",
        "zip": "1", "city": "c", "phone": "p", "email": "e"})

    zeilen = _rows("SELECT ip_address FROM audit_log")
    assert all(not (z["ip_address"] or "") for z in zeilen), \
        "IP-Adresse wurde trotz ADR_AUDIT_LOG_IP=0 gespeichert"


# ─────────────────────────────────────────────────────────────────────
# 3. Löschung räumt die PDF-Datei mit ab
# ─────────────────────────────────────────────────────────────────────

def test_pdf_wird_mit_der_sendung_gelöscht(client):
    kid = _neuer_kunde()
    export_dir = befoerderungspapier.EXPORT_DIR
    pfad = os.path.join(export_dir, "befoerderungspapier_1_test.pdf")
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write("Empfänger: Muster GmbH, Hauptstr. 1, 80331 Muenchen")

    conn = database.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO shipments (customer_id, doc_number, bef_papier_path, "
            "total_points, is_exempt) VALUES (?, 'BP-2026-000001', ?, 0, 1)",
            (kid, pfad))
        conn.commit()
        sid = cur.lastrowid
    finally:
        conn.close()

    assert os.path.exists(pfad)
    res = client.delete(f"/api/shipments/{sid}")
    assert res.status_code == 200
    assert res.get_json()["pdf_removed"] is True
    assert not os.path.exists(pfad), "PDF blieb nach dem Löschen liegen"


def test_loeschfunktion_verlaesst_das_exportverzeichnis_nicht():
    """Ein manipulierter Pfad darf keine Fremddatei löschen."""
    aussen = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    aussen.close()
    try:
        assert befoerderungspapier.delete_export_file(aussen.name) is False
        assert os.path.exists(aussen.name)
    finally:
        os.unlink(aussen.name)


# ─────────────────────────────────────────────────────────────────────
# 4. Auskunft nach Art. 15 / 20
# ─────────────────────────────────────────────────────────────────────

def test_auskunft_enthaelt_alle_gespeicherten_daten(client):
    kid = _neuer_kunde()
    conn = database.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO shipments (customer_id, doc_number, total_points, "
            "is_exempt) VALUES (?, 'BP-2026-000042', 40, 1)", (kid,))
        conn.commit()
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO shipment_items (shipment_id, un_number, substance_name, "
            "quantity, unit) VALUES (?, '1203', 'BENZIN', 20, 'L')", (sid,))
        conn.commit()
    finally:
        conn.close()

    res = client.get(f"/api/kunden/{kid}/export")
    assert res.status_code == 200
    assert "attachment" in res.headers.get("Content-Disposition", "")

    daten = json.loads(res.get_data(as_text=True))
    assert daten["kunde"]["contact"] == "Frau Schmidt"
    assert daten["kunde"]["email"] == "s@muster.de"
    assert len(daten["sendungen"]) == 1
    assert daten["sendungen"][0]["doc_number"] == "BP-2026-000042"
    assert daten["sendungen"][0]["items"][0]["substance_name"] == "BENZIN"


def test_auskunft_gibt_keine_internen_pfade_preis(client):
    kid = _neuer_kunde()
    daten = json.loads(client.get(f"/api/kunden/{kid}/export").get_data(as_text=True))
    assert "bef_papier_path" not in daten["kunde"]


def test_auskunft_ist_administratoren_vorbehalten(client):
    kid = _neuer_kunde()
    # Passwort darf den Benutzernamen nicht enthalten.
    start_pw = "Nordlicht-Buero-2026"
    client.post("/api/users", json={"username": "sachbearbeiter",
                                    "role": "user", "password": start_pw})
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "sachbearbeiter",
                                     "password": start_pw})
    client.post("/auth/password", json={"old_password": start_pw,
                                        "new_password": "Eigenes-Wort-2026"})

    assert client.get(f"/api/kunden/{kid}/export").status_code == 403


def test_auskunft_wird_protokolliert(client):
    kid = _neuer_kunde()
    client.get(f"/api/kunden/{kid}/export")
    zeilen = _rows("SELECT action, entity, entity_id FROM audit_log "
                   "WHERE action = 'export'")
    assert any(z["entity"] == "customer" and z["entity_id"] == kid for z in zeilen)


def test_auskunft_ueber_unbekannten_kunden_ist_404(client):
    assert client.get("/api/kunden/99999/export").status_code == 404
