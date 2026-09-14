"""Tests für den BAM-Import (amtliche Datenquelle der Datenbank GEFAHRGUT).

Getestet wird nur die reine Logik — ohne echte BAM-Datei, damit die Tests
schnell und überall lauffähig bleiben.
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bam_import import (  # noqa: E402
    BamEntry,
    _row_to_entry,
    check_entries,
    parse_bam_file,
    attribution,
)


def _row(**overrides):
    """Minimaler BAM-Datensatz (wie ADR25_csv.txt)."""
    base = {
        "S_UNNR": "1133",
        "N_LFDNR": "1",
        "S_NAME": "KLEBSTOFFE",
        "S_SPEZIFIKATION": "mit entzündbarem flüssigem Stoff",
        "S_KLASSE": "3",
        "S_KLASSIFIZIERUNGSCODE": "F1",
        "S_VP_GRUPPE": "I",
        "S_KENN1": "3",
        "S_KENN2": "",
        "S_SV1": "640D",
        "S_BEGRENZTE_MENGEN": "5 L",
        "S_FREIGEST_MENGEN": "E1",
        "S_VERPACKUNGSANW1": "P001",
        "S_TANK_CODE1": "T2",
        "S_BEFOERDERUNGSKATEGORIE": "1",
        "S_TUNNEL_CODE": "D/E",
        "S_GEFAHRNR": "33",
        "N_MULTIPLIKATOR": "50",
        "N_VERBOT": "",
        "S_NAME_E": "ADHESIVES",
        "S_SPEZIFIKATION_E": "containing flammable liquid",
        "S_BEMERKUNG": "",
    }
    base.update(overrides)
    return base


def test_row_mapping_basic():
    e = _row_to_entry(_row())
    assert e.un_number == "1133"
    assert e.variant == 1
    assert e.name_de == "KLEBSTOFFE"
    assert e.hazard_class == "3"
    assert e.classification_code == "F1"
    assert e.packing_group == "I"
    assert e.transport_category == 1
    assert e.tunnel_code == "D/E"
    assert e.multiplier == 50.0
    assert e.limited_quantity == "5 L"
    assert e.excepted_quantity == "E1"
    assert e.hazard_identification_no == "33"
    assert e.labels == "3"


def test_full_name_joins_specification():
    e = _row_to_entry(_row())
    assert e.full_name_de == "KLEBSTOFFE, mit entzündbarem flüssigem Stoff"


def test_prefix_is_prepended():
    e = _row_to_entry(_row(S_VORSILBE="(LQ)"))
    assert e.name_de.startswith("(LQ)")


def test_english_name_includes_specification():
    e = _row_to_entry(_row())
    assert e.name_en == "ADHESIVES, containing flammable liquid"


def test_placeholder_dash_becomes_none():
    """'-' ist bei der BAM das Zeichen für «nicht vorhanden»."""
    e = _row_to_entry(_row(
        S_VP_GRUPPE="-",
        S_BEFOERDERUNGSKATEGORIE="-",
        S_TUNNEL_CODE="-",
        S_GEFAHRNR="-",
    ))
    assert e.packing_group is None
    assert e.transport_category is None
    assert e.tunnel_code is None
    assert e.hazard_identification_no is None


def test_tunnel_code_parentheses_are_stripped():
    e = _row_to_entry(_row(S_TUNNEL_CODE="(B1000C)"))
    assert e.tunnel_code == "B1000C"


def test_invalid_category_becomes_none():
    """Eine Kategorie außerhalb 0–4 darf nicht ungeprüft übernommen werden."""
    assert _row_to_entry(_row(S_BEFOERDERUNGSKATEGORIE="7")).transport_category is None
    assert _row_to_entry(_row(S_BEFOERDERUNGSKATEGORIE="")).transport_category is None


def test_row_without_un_number_is_skipped():
    assert _row_to_entry(_row(S_UNNR="")) is None
    assert _row_to_entry(_row(S_UNNR=None)) is None


def test_check_entries_accepts_realistic_dataset():
    entries = [
        BamEntry(un_number=f"{i:04d}", variant=1, name_de="STOFF",
                 transport_category=2, multiplier=3.0)
        for i in range(1, 2400)
    ]
    chk = check_entries(entries)
    assert chk.ok, chk.problems
    assert chk.un_numbers == 2399
    assert chk.with_category == 2399


def test_check_entries_rejects_tiny_dataset():
    entries = [BamEntry(un_number="1203", variant=1, transport_category=2)]
    chk = check_entries(entries)
    assert not chk.ok
    assert any("unvollständig" in p for p in chk.problems)


def test_check_entries_detects_duplicate_variants():
    entries = [
        BamEntry(un_number="1203", variant=1),
        BamEntry(un_number="1203", variant=1),
    ]
    chk = check_entries(entries)
    assert not chk.ok
    assert any("doppelte" in p for p in chk.problems)


def test_check_entries_detects_bad_un_number():
    entries = [BamEntry(un_number="12", variant=1, transport_category=2)]
    chk = check_entries(entries)
    assert not chk.ok


# ── Datei-Parser ──────────────────────────────────────────────────────

HEADER = [
    "S_UNNR", "N_LFDNR", "S_NAME", "S_SPEZIFIKATION", "S_KLASSE",
    "S_KLASSIFIZIERUNGSCODE", "S_VP_GRUPPE", "S_BEGRENZTE_MENGEN",
    "S_FREIGEST_MENGEN", "S_BEFOERDERUNGSKATEGORIE", "S_TUNNEL_CODE",
    "N_MULTIPLIKATOR", "S_GEFAHRNR",
]

DATA = [
    ["1133", "1", "KLEBSTOFFE", "mit entzündbarem flüssigem Stoff", "3", "F1",
     "I", "5 L", "E1", "1", "D/E", "50", "33"],
    ["1133", "2", "KLEBSTOFFE", "mit entzündbarem flüssigem Stoff", "3", "F1",
     "II", "5 L", "E1", "2", "D/E", "3", "33"],
    ["1203", "1", "BENZIN", "", "3", "F1", "II", "1 L", "E2", "2", "D/E", "3", "33"],
]


def test_parse_tab_separated_csv(tmp_path):
    lines = ["\t".join(HEADER)] + ["\t".join(r) for r in DATA]
    # cp1252, wie die Originaldatei der BAM
    path = tmp_path / "ADR25_csv.txt"
    path.write_text("\n".join(lines), encoding="cp1252")

    entries = parse_bam_file(str(path), "ADR25_csv.txt")
    assert len(entries) == 3
    assert entries[0].un_number == "1133"
    assert entries[0].packing_group == "I"
    assert entries[0].transport_category == 1
    assert entries[1].packing_group == "II"
    assert entries[1].transport_category == 2


def test_parse_rejects_foreign_csv(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("a\tb\tc\n1\t2\t3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="S_UNNR"):
        parse_bam_file(str(path), "wrong.csv")


def test_parse_accepts_bytes_io():
    lines = ["\t".join(HEADER)] + ["\t".join(r) for r in DATA]

    class FakeUpload:
        filename = "ADR25_csv.txt"

        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def read(self):
            return self._buf.read()

    entries = parse_bam_file(FakeUpload("\n".join(lines).encode("cp1252")),
                             "ADR25_csv.txt")
    assert len(entries) == 3


def test_attribution_contains_required_elements():
    text = attribution()
    assert "Bundesanstalt für Materialforschung und -prüfung (BAM)" in text
    assert "attribution" in text
    assert "tes.bam.de" in text
