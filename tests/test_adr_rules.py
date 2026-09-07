"""
Tests für die ADR-1.1.3.6-Regelengine.

Abgedeckt werden insbesondere die Fälle, die in der Version 1.x zu
rechtlich falschen Freistellungen geführt haben:
  - Ausschluss nach 1.1.3.6.2 (Klasse 1, 6.2, Klasse 7)
  - Höchstmenge je Beförderungseinheit (1.1.3.6.3)
  - Beschränkung auf Stückgut (1.1.3.6.1)
  - Überschreiten der 1000-Punkte-Grenze
"""

import pytest

from adr_rules import (
    POINT_LIMIT,
    TRANSPORT_FORM_BULK,
    TRANSPORT_FORM_PACKAGE,
    TRANSPORT_FORM_TANK,
    evaluate_transport,
)


def _item(un="1263", qty=1.0, category=3, hazard_class="3", **kwargs):
    """Hilfsfunktion für eine einzelne Gefahrgutposition."""
    base = {
        "un_number": un,
        "quantity": qty,
        "num_packages": 1,
        "unit": "L",
        "transport_category": category,
        "hazard_class": hazard_class,
        "substance_name": "Farbe",
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────
# Grundregel: 1000 Punkte
# ─────────────────────────────────────────────────────────────────────

def test_unterhalb_der_punktgrenze_ist_freigestellt():
    """500 L Kat. 3 → 500 Punkte → freigestellt."""
    result = evaluate_transport([_item(qty=500, category=3)])
    assert result.total_points == 500
    assert result.is_exempt is True
    assert result.blocking_reasons == []


def test_exakt_1000_punkte_ist_freigestellt():
    """Die Grenze gilt als eingehalten (≤ 1000)."""
    result = evaluate_transport([_item(qty=1000, category=3)])
    assert result.total_points == POINT_LIMIT
    assert result.is_exempt is True


def test_ueber_1000_punkten_ist_nicht_freigestellt():
    """1001 Punkte → nicht freigestellt."""
    result = evaluate_transport([_item(qty=1001, category=3)])
    assert result.is_exempt is False
    assert any("überschreitet die Freigrenze" in r
               for r in result.blocking_reasons)


def test_punkte_werden_ueber_positionen_summiert():
    """Mehrere Positionen werden addiert und können die Grenze überschreiten."""
    result = evaluate_transport([
        _item(un="1263", qty=600, category=3),
        _item(un="1203", qty=500, category=3),
    ])
    assert result.total_points == 1100
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.2 — Ausgeschlossene Klassen
# ─────────────────────────────────────────────────────────────────────

def test_klasse_7_ist_immer_ausgeschlossen():
    """Radioaktive Stoffe sind nie freigestellt (1.1.3.6.2)."""
    result = evaluate_transport([_item(un="2910", qty=1, category=3,
                                       hazard_class="7")])
    assert result.is_exempt is False
    assert any("1.1.3.6.2" in r for r in result.blocking_reasons)


def test_klasse_62_ist_immer_ausgeschlossen():
    """Infektiöse Stoffe sind nie freigestellt."""
    result = evaluate_transport([_item(un="2814", qty=1, category=3,
                                       hazard_class="6.2")])
    assert result.is_exempt is False
    assert any("1.1.3.6.2" in r for r in result.blocking_reasons)


def test_klasse_1_ohne_14s_ist_ausgeschlossen():
    """Klasse 1 ist ausgeschlossen, sofern kein 1.4S."""
    result = evaluate_transport([_item(un="0336", qty=1, category=1,
                                       hazard_class="1")])
    assert result.is_exempt is False
    assert any("1.4S" in r for r in result.blocking_reasons)


def test_klasse_1_mit_code_14s_ist_freistellungsfaehig():
    """Mit Klassifizierungscode 1.4S greift der Ausschluss nicht."""
    result = evaluate_transport([_item(un="0503", qty=1, category=4,
                                       hazard_class="1",
                                       classification_code="1.4S")])
    assert result.is_exempt is True


def test_klasse_1_kategorie_4_wird_als_14s_behandelt():
    """
    Ohne Klassifizierungscode wird Kategorie 4 als Hinweis auf 1.4S
    gewertet (Kat. 4 umfasst u. a. 1.4S).
    """
    result = evaluate_transport([_item(un="0503", qty=1, category=4,
                                       hazard_class="1")])
    assert result.is_exempt is True


def test_kategorie_0_ist_niemals_freigestellt():
    """Kategorie 0 → keine Freistellung."""
    result = evaluate_transport([_item(qty=1, category=0)])
    assert result.is_exempt is False
    assert any("Beförderungskategorie 0" in r
               for r in result.blocking_reasons)


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.3 — Höchstmenge je Beförderungseinheit
# ─────────────────────────────────────────────────────────────────────

def test_kategorie_2_ueber_333_ist_nicht_freigestellt():
    """
    800 kg Kat. 2 → 2400 Punkte, aber bereits die Höchstmenge von
    333 ist überschritten. Beide Gründe müssen gemeldet werden.
    """
    result = evaluate_transport([_item(qty=800, category=2, unit="kg")])
    assert result.is_exempt is False
    assert result.items[0].limit_exceeded is True
    assert any("Höchstmenge je Beförderungseinheit" in r
               for r in result.blocking_reasons)


def test_kategorie_2_unter_333_bleibt_freigestellt():
    """300 kg Kat. 2 → 900 Punkte, Höchstmenge eingehalten."""
    result = evaluate_transport([_item(qty=300, category=2, unit="kg")])
    assert result.total_points == 900
    assert result.is_exempt is True


def test_kategorie_1_menge_20_eingehalten():
    """15 kg Kat. 1 → 750 Punkte, Höchstmenge 20 kg eingehalten."""
    result = evaluate_transport([_item(qty=15, category=1, unit="kg")])
    assert result.total_points == 750
    assert result.is_exempt is True


def test_kategorie_1_menge_20_ueberschritten():
    """
    25 kg Kat. 1 → 1250 Punkte UND Höchstmenge 20 kg überschritten.
    """
    result = evaluate_transport([_item(qty=25, category=1, unit="kg")])
    assert result.is_exempt is False
    assert result.items[0].limit_exceeded is True


def test_anzahl_packstuecke_zaehlt_fuer_hoechstmenge():
    """2 × 200 kg Kat. 2 = 400 kg > 333 kg → nicht freigestellt."""
    result = evaluate_transport([
        _item(qty=200, category=2, unit="kg", num_packages=2)
    ])
    assert result.items[0].total_quantity == 400
    assert result.items[0].limit_exceeded is True
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.1 — Nur Stückgut
# ─────────────────────────────────────────────────────────────────────

def test_tanktransport_ist_nicht_freigestellt():
    """Tank-/Schüttguttransport ist nie nach 1.1.3.6 freigestellt."""
    for form in (TRANSPORT_FORM_TANK, TRANSPORT_FORM_BULK):
        result = evaluate_transport([_item(qty=10, category=3)],
                                    transport_form=form)
        assert result.is_exempt is False, form
        assert any("1.1.3.6.1" in r for r in result.blocking_reasons)


def test_stueckgut_ist_freistellungsfaehig():
    result = evaluate_transport([_item(qty=10, category=3)],
                                transport_form=TRANSPORT_FORM_PACKAGE)
    assert result.is_exempt is True


# ─────────────────────────────────────────────────────────────────────
# Fail-Safe-Verhalten
# ─────────────────────────────────────────────────────────────────────

def test_unbekannte_kategorie_verhindert_freistellung():
    """Fehlende Beförderungskategorie darf nicht zur Freistellung führen."""
    result = evaluate_transport([_item(qty=1, category=None)])
    assert result.is_exempt is False
    assert result.items[0].class_excluded is True


def test_ungueltige_kategorie_verhindert_freistellung():
    result = evaluate_transport([_item(qty=1, category=99)])
    assert result.is_exempt is False


def test_ungueltige_menge_liefert_valueerror():
    with pytest.raises(ValueError):
        evaluate_transport([_item(qty="abc")])


def test_leere_sendung_ist_nicht_freigestellt():
    result = evaluate_transport([])
    assert result.total_points == 0
    assert result.is_exempt is False
    assert any("Keine Gefahrgutpositionen" in r
               for r in result.blocking_reasons)


def test_ergebnis_laesst_sich_serialisieren():
    """Das Ergebnis muss vollständig JSON-serialisierbar sein."""
    import json
    result = evaluate_transport([_item(qty=10, category=3)])
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["is_exempt"] is True
    assert len(payload["items"]) == 1
