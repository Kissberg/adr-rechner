"""
Tests der Regelbasis-Anzeige (Forderung aus dem Konzern-Review: das Tool
muss nachvollziehen lassen, auf welchem rechtlichen Stand gerechnet
wurde).

Geprüft: der aktuelle Datenstand für die Oberfläche (Fußzeile, Rechner)
und die bei der Sendung festgehaltene Basis auf dem Beförderungspapier-PDF
— auch dann, wenn inzwischen ein neuerer Datenstand importiert wurde.
"""

import tempfile

import pytest

import database
import adr_import
from database import get_db


@pytest.fixture()
def db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(database, "DB_DIR", tmp)
    monkeypatch.setattr(database, "DB_PATH", f"{tmp}/adr-test.db")
    monkeypatch.setenv("SECRET_KEY", "test-key")
    database.init_db()
    database.seed_un_numbers()
    return tmp


def test_ohne_import_ist_regelbasis_leer(db):
    assert adr_import.get_current_regelbasis() is None


def test_import_setzt_regelbasis(db):
    adr_import.import_bam_data(
        [], "ADR 2025", "ADR25.xlsx")  # leere Menge → nur Versionseintrag
    basis = adr_import.get_current_regelbasis()
    assert basis is not None
    assert basis["version"] == "ADR 2025"
    assert basis["import_date"]


def _sendung_anlegen(adr_version: str) -> int:
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO customers (name) VALUES ('Kunde KG')")
        kunde = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO shipping_addresses (name) VALUES ('Werk Nord')")
        adresse = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO shipments (customer_id, shipping_address_id, "
            "total_points, is_exempt, adr_version, created_by) "
            "VALUES (?, ?, 35, 1, ?, 'test')",
            (kunde, adresse, adr_version))
        sendung = cur.lastrowid
        conn.execute(
            "INSERT INTO shipment_items (shipment_id, un_number, "
            "substance_name, quantity, unit, transport_category, "
            "points_factor, item_points, num_packages, package_type, "
            "hazard_class, total_quantity) "
            "VALUES (?, '1263', 'PETROLEUM DESTILLATE', 5, 'l', 3, 3, 15, "
            "1, 'Kanister', '3', 5)",
            (sendung,))
        conn.commit()
        return sendung
    finally:
        conn.close()


def test_pdf_weist_festgehaltene_regelbasis_aus(db):
    import fitz  # PyMuPDF
    from befoerderungspapier import generate_befoerderungspapier

    adr_import.import_bam_data([], "ADR 2025", "ADR25.xlsx")
    sendung = _sendung_anlegen("ADR 2025")

    # Ein späterer Import ändert die Anzeige für **neue** Berechnungen,
    # nicht die festgehaltene Basis alter Sendungen.
    adr_import.import_bam_data([], "ADR 2026", "ADR26.xlsx")

    pfad = generate_befoerderungspapier(sendung)
    assert adr_import.get_current_regelbasis()["version"] == "ADR 2026"

    text = ""
    with fitz.open(pfad) as dok:
        for seite in dok:
            text += seite.get_text()
    assert "Regelbasis: ADR 2025" in text
    assert "ADR 2026" not in text  # neue Basis gehört nicht in das alte Dokument


def test_pdf_ohne_gespeicherte_version_nennt_standard(db):
    import fitz  # PyMuPDF
    from befoerderungspapier import generate_befoerderungspapier

    sendung = _sendung_anlegen("")  # Altdaten ohne Versionseintrag
    pfad = generate_befoerderungspapier(sendung)
    text = ""
    with fitz.open(pfad) as dok:
        for seite in dok:
            text += seite.get_text()
    assert "Regelbasis: ADR 2025" in text
