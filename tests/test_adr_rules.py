"""
Tests für die ADR-1.1.3.6-Regelengine.

Geprüft werden die Voraussetzungen der Freistellung nach dem amtlichen
ADR-2025-Text (Unterabschnitt 1.1.3.6, ASTRA Band I):

  1.1.3.6.1  Zuordnung zu Beförderungskategorien 0–4 (Tabelle A Spalte 15)
  1.1.3.6.2  Freistellung nur für Güter „in Versandstücken" (Stückgut)
  1.1.3.6.3  Höchstmenge je Beförderungseinheit (inkl. Fussnote a)
  1.1.3.6.4  Mischrechnung: Summe (Menge × Faktor) ≤ 1000

WICHTIG: Der Ausschluss von Klasse 1/6.2/7 erfolgt im ADR NICHT über eine
eigene „Klassen-Ausschlussregel", sondern allein über die Beförderungs-
kategorie 0 in Tabelle A. Klasse 1/6.2/7-Güter in den Kategorien 1, 2
oder 4 sind sehr wohl freistellungsfähig.
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
        "unit": "kg",
        "transport_category": category,
        "hazard_class": hazard_class,
        "substance_name": "Farbe",
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────
# Grundregel: 1000 Punkte (1.1.3.6.4)
# ─────────────────────────────────────────────────────────────────────

def test_unterhalb_der_punktgrenze_ist_freigestellt():
    result = evaluate_transport([_item(qty=500, category=3)])
    assert result.total_points == 500
    assert result.is_exempt is True


def test_exakt_1000_punkte_ist_freigestellt():
    result = evaluate_transport([_item(qty=1000, category=3)])
    assert result.total_points == POINT_LIMIT
    assert result.is_exempt is True


def test_ueber_1000_punkten_ist_nicht_freigestellt():
    result = evaluate_transport([_item(qty=1001, category=3)])
    assert result.is_exempt is False


def test_punkte_werden_ueber_positionen_summiert():
    result = evaluate_transport([
        _item(un="1263", qty=600, category=3),
        _item(un="1203", qty=500, category=3),
    ])
    assert result.total_points == 1100
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.3 — Beförderungskategorie 0 ist niemals freigestellt
# ─────────────────────────────────────────────────────────────────────

def test_kategorie_0_ist_niemals_freigestellt():
    result = evaluate_transport([_item(qty=1, category=0)])
    assert result.is_exempt is False
    assert any("Beförderungskategorie 0" in r for r in result.blocking_reasons)


# ─────────────────────────────────────────────────────────────────────
# Klasse 1: Kategorien 1/2/4 sind freistellungsfähig (kein Pauschalausschluss)
# ─────────────────────────────────────────────────────────────────────

def test_klasse_1_kategorie_2_freistellungsfaehig():
    """UN 0336 Feuerwerk 1.4G = Kat. 2 → 100 kg = 300 Punkte, freigestellt."""
    result = evaluate_transport([_item(un="0336", qty=100, category=2,
                                       hazard_class="1")])
    assert result.total_points == 300
    assert result.is_exempt is True


def test_klasse_1_kategorie_1_freistellungsfaehig():
    """UN 0333 Feuerwerk 1.1G = Kat. 1 → 5 kg = 250 Punkte, freigestellt."""
    result = evaluate_transport([_item(un="0333", qty=5, category=1,
                                       hazard_class="1")])
    assert result.total_points == 250
    assert result.is_exempt is True


def test_klasse_1_kategorie_4_freistellungsfaehig():
    """1.4S (z. B. UN 0337) = Kat. 4 → unbegrenzt freistellungsfähig."""
    result = evaluate_transport([_item(un="0337", qty=1, category=4,
                                       hazard_class="1")])
    assert result.is_exempt is True


def test_klasse_1_kategorie_0_nicht_freigestellt():
    """1.1A/1.1L etc. = Kat. 0 → nicht freigestellt (über Kategorie, nicht Klasse)."""
    result = evaluate_transport([_item(un="0075", qty=1, category=0,
                                       hazard_class="1")])
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# Klasse 6.2: UN 3291 (Kat. 2) freistellungsfähig, UN 2814 (Kat. 0) nicht
# ─────────────────────────────────────────────────────────────────────

def test_klasse_62_un3291_freistellungsfaehig():
    """Klinischer Abfall UN 3291 = Kat. 2 → freistellungsfähig."""
    result = evaluate_transport([_item(un="3291", qty=100, category=2,
                                       hazard_class="6.2")])
    assert result.total_points == 300
    assert result.is_exempt is True


def test_klasse_62_un2814_nicht_freigestellt():
    """UN 2814 = Kat. 0 → nicht freigestellt (über Kategorie 0)."""
    result = evaluate_transport([_item(un="2814", qty=1, category=0,
                                       hazard_class="6.2")])
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# Klasse 7: UN 2908–2911 (Kat. 4) freistellungsfähig, UN 2912+ (Kat. 0) nicht
# ─────────────────────────────────────────────────────────────────────

def test_klasse_7_un2908_freistellungsfaehig():
    """Freigestelltes Versandstück UN 2908 = Kat. 4 → freistellungsfähig."""
    result = evaluate_transport([_item(un="2908", qty=5, category=4,
                                       hazard_class="7")])
    assert result.is_exempt is True


def test_klasse_7_un2912_nicht_freigestellt():
    """UN 2912 = Kat. 0 → nicht freigestellt (über Kategorie 0)."""
    result = evaluate_transport([_item(un="2912", qty=1, category=0,
                                       hazard_class="7")])
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.3 Fussnote a) — Faktor 20, Höchstmenge 50 kg
# ─────────────────────────────────────────────────────────────────────

def test_fussnote_a_faktor_20():
    """UN 0081 (Sprengstoff Typ A): 25 kg → 500 Punkte (Faktor 20), freigestellt."""
    result = evaluate_transport([_item(un="0081", qty=25, category=1,
                                       hazard_class="1")])
    assert result.total_points == 500
    assert result.is_exempt is True


def test_fussnote_a_hoechstmenge_50():
    """UN 0081: 55 kg → 1100 Punkte und > 50 kg → nicht freigestellt."""
    result = evaluate_transport([_item(un="0081", qty=55, category=1,
                                       hazard_class="1")])
    assert result.items[0].limit_exceeded is True
    assert result.is_exempt is False


def test_fussnote_a_un1017_nicht_klasse_1():
    """UN 1017 (Chlor, Klasse 2) ist ebenfalls Fussnote a → Faktor 20."""
    result = evaluate_transport([_item(un="1017", qty=25, category=1,
                                       hazard_class="2")])
    assert result.total_points == 500


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.3 — Höchstmenge je Beförderungseinheit
# ─────────────────────────────────────────────────────────────────────

def test_kategorie_2_ueber_333_ist_nicht_freigestellt():
    result = evaluate_transport([_item(qty=800, category=2)])
    assert result.items[0].limit_exceeded is True
    assert result.is_exempt is False


def test_kategorie_1_menge_20_ueberschritten():
    result = evaluate_transport([_item(qty=25, category=1)])
    assert result.items[0].limit_exceeded is True
    assert result.is_exempt is False


def test_anzahl_packstuecke_zaehlt_fuer_hoechstmenge():
    """2 × 200 kg Kat. 2 = 400 kg > 333 kg → nicht freigestellt."""
    result = evaluate_transport([
        _item(qty=200, category=2, num_packages=2)
    ])
    assert result.items[0].total_quantity == 400
    assert result.items[0].limit_exceeded is True
    assert result.is_exempt is False


# ─────────────────────────────────────────────────────────────────────
# 1.1.3.6.2 — nur Versandstücke (kein Tank/Schüttgut)
# ─────────────────────────────────────────────────────────────────────

def test_tanktransport_ist_nicht_freigestellt():
    for form in (TRANSPORT_FORM_TANK, TRANSPORT_FORM_BULK):
        result = evaluate_transport([_item(qty=10, category=3)],
                                    transport_form=form)
        assert result.is_exempt is False, form
        assert any("1.1.3.6.2" in r for r in result.blocking_reasons)


def test_stueckgut_ist_freistellungsfaehig():
    result = evaluate_transport([_item(qty=10, category=3)],
                                transport_form=TRANSPORT_FORM_PACKAGE)
    assert result.is_exempt is True


# ─────────────────────────────────────────────────────────────────────
# Fail-Safe-Verhalten
# ─────────────────────────────────────────────────────────────────────

def test_unbekannte_kategorie_verhindert_freistellung():
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


def test_ergebnis_laesst_sich_serialisieren():
    import json
    result = evaluate_transport([_item(qty=10, category=3)])
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["is_exempt"] is True
    assert len(payload["items"]) == 1
