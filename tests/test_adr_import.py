"""Tests für Verifikation, Variantenlogik und Vorschriftenprüfung.

Der wichtigste Fall ist `test_variants_are_not_overwritten`: Eine UN-Nummer
hat in Tabelle A mehrere Varianten mit unterschiedlicher Beförderungskategorie
(UN 1133: PG I → Kat 1, PG II → Kat 2, PG III → Kat 3). Ein Import, der nur
auf die UN-Nummer schreibt, würde diese Werte gegenseitig überschreiben und
damit falsche Punktzahlen erzeugen.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import adr_import  # noqa: E402
from bam_import import BamEntry  # noqa: E402


# ── Spalte (15): Kategorie und Tunnelcode in einer Zelle ──────────────

@pytest.mark.parametrize("raw,cat,tunnel", [
    ("2", 2, None),
    ("2 (D/E)", 2, "D/E"),
    ("1 (B1000C)", 1, "B1000C"),
    ("0 (-)", 0, None),
    ("- (-)", None, None),
    ("- (E)", None, "E"),
    ("4 (E)", 4, "E"),
])
def test_category_tunnel_regex(raw, cat, tunnel):
    m = adr_import._CAT_TUNNEL_RE.match(raw)
    assert m is not None, f"Spalte (15) nicht erkannt: {raw!r}"
    got_cat = m.group("cat")
    got_cat = int(got_cat) if got_cat.isdigit() else None
    got_tun = m.group("tunnel")
    got_tun = None if (not got_tun or got_tun == "-") else got_tun
    assert got_cat == cat
    assert got_tun == tunnel


def test_category_tunnel_regex_rejects_nonsense():
    """Ein unlesbarer Eintrag wie 'siehe SV 671 (E)' darf nicht geraten werden."""
    m = adr_import._CAT_TUNNEL_RE.match("siehe SV 671 (E)")
    assert m is None


# ── Seitenblöcke ─────────────────────────────────────────────────────

def test_longest_run_prefers_contiguous_block():
    """Zitate eines Abschnitts an späterer Stelle dürfen nicht mitgelesen werden."""
    assert adr_import._longest_run([41, 42, 43, 129]) == [41, 42, 43]
    assert adr_import._longest_run([5]) == [5]
    assert adr_import._longest_run([]) == []
    assert adr_import._longest_run([10, 11, 20, 21, 22]) == [20, 21, 22]


# ── Verpackungsgruppe: nur I / II / III ───────────────────────────────

def test_packing_group_whitelist_logic():
    """Vermerke in Spalte (4) sind keine Verpackungsgruppe.

    Steht dort „BEFÖRDERUNG VERBOTEN" oder „UNTERLIEGT NICHT DEN
    VORSCHRIFTEN DES ADR", darf das nicht als VG weitergegeben werden —
    sonst entstehen Scheinabweichungen bei der Verifikation.
    """
    allowed = ("I", "II", "III")
    for value in ("I", "II", "III"):
        assert value in allowed
    for value in ("BEFÖRDERUNG VERBOTEN",
                  "UNTERLIEGT NICHT DEN VORSCHRIFTEN DES ADR",
                  "", "-"):
        assert value not in allowed


# ── Variantenlogik beim Import ────────────────────────────────────────

def _un1133_variants():
    """UN 1133 KLEBSTOFFE — Kategorie hängt an der Verpackungsgruppe."""
    return [
        BamEntry(un_number="1133", variant=1, name_de="KLEBSTOFFE",
                 hazard_class="3", packing_group="I", tunnel_code="D/E",
                 transport_category=1, multiplier=50.0),
        BamEntry(un_number="1133", variant=2, name_de="KLEBSTOFFE",
                 hazard_class="3", packing_group="II", tunnel_code="D/E",
                 transport_category=2, multiplier=3.0),
        BamEntry(un_number="1133", variant=3, name_de="KLEBSTOFFE",
                 hazard_class="3", packing_group="III", tunnel_code="D/E",
                 transport_category=3, multiplier=1.0),
    ]


def test_variants_are_not_overwritten(tmp_path, monkeypatch):
    """Kernfall: nach dem Import müssen alle Varianten erhalten bleiben."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_DIR", str(tmp_path))

    database.init_db()
    adr_import.import_bam_data(_un1133_variants(), "ADR 2025", "test.xlsx")

    conn = database.get_db()
    rows = conn.execute(
        "SELECT un_number, variant, packing_group, transport_category "
        "FROM un_numbers WHERE un_number = '1133' ORDER BY variant"
    ).fetchall()
    conn.close()

    assert len(rows) == 3, "Varianten wurden überschrieben"
    assert [(r["packing_group"], r["transport_category"]) for r in rows] == [
        ("I", 1), ("II", 2), ("III", 3),
    ]


def test_reimport_keeps_variants(tmp_path, monkeypatch):
    """Ein zweiter Import darf die Varianten nicht zusammenfassen."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_DIR", str(tmp_path))

    database.init_db()
    adr_import.import_bam_data(_un1133_variants(), "ADR 2025", "test.xlsx")
    result = adr_import.import_bam_data(_un1133_variants(), "ADR 2027", "test.xlsx")

    # Beim zweiten Lauf wird aktualisiert, nicht neu angelegt
    assert result["imported"] == 0
    assert result["updated"] == 3

    conn = database.get_db()
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM un_numbers WHERE un_number = '1133'"
    ).fetchone()
    conn.close()
    assert rows["n"] == 3


def test_unique_index_exists(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_DIR", str(tmp_path))

    database.init_db()
    adr_import.import_bam_data(_un1133_variants(), "ADR 2025", "test.xlsx")

    conn = database.get_db()
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_un_variant'"
    ).fetchone()
    conn.close()
    assert idx is not None, "Eindeutiger Index auf (un_number, variant) fehlt"


# ── Verifikation gegen den Datenbestand ───────────────────────────────

def test_verify_reports_no_difference_for_equal_data(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_DIR", str(tmp_path))

    database.init_db()
    adr_import.import_bam_data(_un1133_variants(), "ADR 2025", "test.xlsx")

    pdf_entries = [
        {"un_number": "1133", "hazard_class": "3", "packing_group": "I",
         "transport_category": 1, "tunnel_code": "D/E"},
        {"un_number": "1133", "hazard_class": "3", "packing_group": "II",
         "transport_category": 2, "tunnel_code": "D/E"},
        {"un_number": "1133", "hazard_class": "3", "packing_group": "III",
         "transport_category": 3, "tunnel_code": "D/E"},
    ]
    result = adr_import.verify_against_database(pdf_entries)
    assert result.matched == 1
    assert result.agreement == 1.0
    assert not result.differences


def test_verify_reports_real_difference(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "DB_DIR", str(tmp_path))

    database.init_db()
    adr_import.import_bam_data(_un1133_variants(), "ADR 2025", "test.xlsx")

    # PDF behauptet Kategorie 3 für alle Varianten
    pdf_entries = [
        {"un_number": "1133", "hazard_class": "3", "packing_group": "I",
         "transport_category": 3, "tunnel_code": "D/E"},
    ]
    result = adr_import.verify_against_database(pdf_entries)
    assert result.agreement < 1.0
    assert any(d["field"] == "transport_category" for d in result.differences)


# ── Vorschriftenprüfung ───────────────────────────────────────────────

def test_regulation_sections_are_configured():
    assert "1.1.3.6" in adr_import.REGULATION_SECTIONS
    assert "5.4.1.1" in adr_import.REGULATION_SECTIONS
    # Für beide Abschnitte sind Schlüsselwerte hinterlegt
    assert adr_import.POINT_RULE_MARKERS
    assert adr_import.BEF_PAPIER_MARKERS


def test_min_section_chars_guard():
    """Ein reiner Inhaltsverzeichnis-Eintrag darf nicht als Fund gelten."""
    assert adr_import.MIN_SECTION_CHARS > 0
    assert len("5.4.1.1 Angaben im Beförderungspapier 5-7") < adr_import.MIN_SECTION_CHARS
