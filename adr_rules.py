"""
adr_rules.py — ADR 1.1.3.6 Regelengine (1000-Punkte-Regel)

Zentrale, testbare Domänenlogik für die Freistellungsprüfung nach ADR
Unterabschnitt 1.1.3.6. Dieses Modul ist bewusst frei von Flask- und
Datenbankabhängigkeiten, damit es unabhängig unit-getestet werden kann.

────────────────────────────────────────────────────────────────────────
WICHTIG — Fail-Safe-Prinzip
────────────────────────────────────────────────────────────────────────
Kann eine Voraussetzung nicht zweifelsfrei geprüft werden (z. B. weil die
Beförderungskategorie fehlt), wird NICHT freigestellt. Eine fälschlich
erteile Freistellung ist ein Rechtsverstoß; eine fälschlich verweigerte
Freistellung führt lediglich zu einer (legalen) Vollanwendung des ADR.

Rechtsgrundlagen
  1.1.3.6.1  Zuordnung zu Beförderungskategorien 0–4 (Tabelle A Spalte 15)
  1.1.3.6.2  Freistellung gilt nur für Güter in Versandstücken (Stückgut);
             Katalog der dann nicht anwendbaren Vorschriften
  1.1.3.6.3  Tabelle: Beförderungskategorie, Punktfaktor,
             Höchstmenge je Beförderungseinheit (inkl. Fussnote a)
  1.1.3.6.4  Mischrechnung (Faktoren 50/20/3/1, Summe ≤ 1000)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────
# ADR 1.1.3.6.3 — Beförderungskategorien
# ─────────────────────────────────────────────────────────────────────
#   Kat. | Faktor | Höchstmenge je Beförderungseinheit
#    0   |   0    |  0      → niemals freigestellt
#    1   |  50    |  20     kg/L
#    2   |   3    |  333    kg/L
#    3   |   1    |  1000   kg/L
#    4   |   0    |  unbegrenzt
TRANSPORT_CATEGORIES: Dict[int, Dict[str, Optional[float]]] = {
    0: {"factor": 0, "max_qty_per_tu": 0},
    1: {"factor": 50, "max_qty_per_tu": 20},
    2: {"factor": 3, "max_qty_per_tu": 333},
    3: {"factor": 1, "max_qty_per_tu": 1000},
    4: {"factor": 0, "max_qty_per_tu": None},  # None = unbegrenzt
}

# Freigrenze der Gesamtpunktzahl je Beförderungseinheit
POINT_LIMIT = 1000

# ─────────────────────────────────────────────────────────────────────
# ADR 1.1.3.6.3 Fussnote a) — abweichende Höchstmenge und Faktor
# ─────────────────────────────────────────────────────────────────────
# Für die UN-Nummern 0081, 0082, 0084, 0241, 0331, 0332, 0482, 1005 und
# 1017 (Beförderungskategorie 1) beträgt die höchstzulässige Gesamtmenge
# je Beförderungseinheit 50 kg und der Faktor in der Mischrechnung
# (1.1.3.6.4) 20 statt 50.
FOOTNOTE_A_UN_NUMBERS = frozenset({
    "0081", "0082", "0084", "0241", "0331", "0332", "0482", "1005", "1017",
})
FOOTNOTE_A_FACTOR = 20.0
FOOTNOTE_A_MAX_QTY = 50.0

# ─────────────────────────────────────────────────────────────────────
# ADR 1.1.3.6.2 — Freistellung gilt ausschließlich für Versandstücke
# ─────────────────────────────────────────────────────────────────────
TRANSPORT_FORM_PACKAGE = "package"   # Stückgut / Verpackung
TRANSPORT_FORM_TANK = "tank"         # Tank
TRANSPORT_FORM_BULK = "bulk"         # Schüttgut / loser Schüttgut
VALID_TRANSPORT_FORMS = frozenset({
    TRANSPORT_FORM_PACKAGE, TRANSPORT_FORM_TANK, TRANSPORT_FORM_BULK
})
# Nur Güter „in Versandstücken" können nach 1.1.3.6 freigestellt werden.
EXEMPTABLE_TRANSPORT_FORMS = frozenset({TRANSPORT_FORM_PACKAGE})

TRANSPORT_FORM_LABELS = {
    TRANSPORT_FORM_PACKAGE: "Stückgut (Verpackung)",
    TRANSPORT_FORM_TANK: "Tank",
    TRANSPORT_FORM_BULK: "Schüttgut / loser Schüttgut",
}


def normalize_hazard_class(hazard_class: Optional[str]) -> str:
    """Normalisiert eine Gefahrklasse: '4.1 ' -> '4.1'; None -> ''."""
    if hazard_class is None:
        return ""
    return str(hazard_class).strip().rstrip(".")


@dataclass
class EvaluatedItem:
    """Ergebnis der Prüfung einer einzelnen Gefahrgutposition."""
    un_number: str = ""
    un_db_id: Optional[int] = None
    substance_name: str = ""
    hazard_class: str = ""
    packing_group: Optional[str] = None
    transport_category: Optional[int] = None
    factor: float = 0.0
    quantity: float = 0.0            # Menge je Verpackung
    unit: str = "kg"
    num_packages: int = 1
    package_type: str = "Verpackung"
    total_quantity: float = 0.0      # Menge je Verpackung × Anzahl
    points: float = 0.0
    max_qty_per_tu: Optional[float] = None   # Höchstmenge je Beförderungseinheit
    limit_exceeded: bool = False
    class_excluded: bool = False     # 1.1.3.6.2 greift
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "un_number": self.un_number,
            "un_db_id": self.un_db_id,
            "substance_name": self.substance_name,
            "hazard_class": self.hazard_class,
            "packing_group": self.packing_group,
            "category": self.transport_category,
            "factor": self.factor,
            "quantity": self.quantity,
            "unit": self.unit,
            "num_packages": self.num_packages,
            "package_type": self.package_type,
            "total_quantity": round(self.total_quantity, 3),
            "points": round(self.points, 2),
            "max_qty_per_tu": self.max_qty_per_tu,
            "limit_exceeded": self.limit_exceeded,
            "class_excluded": self.class_excluded,
            "notes": list(self.notes),
        }


@dataclass
class ExemptionResult:
    """Gesamtergebnis der 1.1.3.6-Prüfung."""
    total_points: float = 0.0
    is_exempt: bool = False
    transport_form: str = TRANSPORT_FORM_PACKAGE
    items: List[EvaluatedItem] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def exemption_declaration_allowed(self) -> bool:
        """
        True, wenn auf dem Beförderungspapier die Erklärung
        'BEFÖRDERUNG IN UNTERSCHREITUNG DER FREIGRENZEN NACH ABSCHNITT
        1.1.3.6' gedruckt werden darf.
        """
        return self.is_exempt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_points": round(self.total_points, 2),
            "is_exempt": self.is_exempt,
            "transport_form": self.transport_form,
            "items": [it.to_dict() for it in self.items],
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
        }


def evaluate_transport(
    items: List[Dict[str, Any]],
    transport_form: str = TRANSPORT_FORM_PACKAGE,
) -> ExemptionResult:
    """
    Prüft eine Sendung vollständig gegen ADR 1.1.3.6.

    Eine Sendung ist NUR dann freigestellt, wenn alle Bedingungen erfüllt sind:
      1. Die Beförderung erfolgt als Stückgut (1.1.3.6.2 „in Versandstücken")
      2. Kein Gut hat die Beförderungskategorie 0 (1.1.3.6.3)
      3. Für jedes Gut wird die Höchstmenge je Beförderungseinheit
         eingehalten (1.1.3.6.3, inkl. Fussnote a)
      4. Die Gesamtpunktzahl überschreitet 1000 nicht (1.1.3.6.4)

    Args:
        items: Liste von Diktaten mit mindestens
               {un_number, quantity, num_packages, transport_category,
                hazard_class}. Optionale Schlüssel:
               un_db_id, substance_name, unit, package_type,
               max_quantity_per_transport, packing_group.
        transport_form: 'package' | 'tank' | 'bulk'

    Returns:
        ExemptionResult
    """
    result = ExemptionResult()
    result.transport_form = (
        transport_form if transport_form in VALID_TRANSPORT_FORMS
        else TRANSPORT_FORM_PACKAGE
    )

    if result.transport_form not in EXEMPTABLE_TRANSPORT_FORMS:
        result.blocking_reasons.append(
            f"1.1.3.6.2: Die Freistellung gilt ausschließlich für Güter in "
            f"Versandstücken (Stückgut). Gewählte Beförderungsart: "
            f"{TRANSPORT_FORM_LABELS.get(result.transport_form, result.transport_form)}."
        )

    total_points = 0.0

    for raw in items or []:
        try:
            quantity = float(raw.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError(
                f"Ungültige Menge für UN {raw.get('un_number', '?')!r}."
            )
        try:
            num_packages = int(raw.get("num_packages", 1) or 1)
        except (TypeError, ValueError):
            raise ValueError(
                f"Ungültige Packstückzahl für UN {raw.get('un_number', '?')!r}."
            )
        if num_packages < 1:
            num_packages = 1

        item = EvaluatedItem(
            un_number=str(raw.get("un_number", "")).strip(),
            un_db_id=raw.get("un_db_id"),
            substance_name=str(raw.get("substance_name", "") or ""),
            hazard_class=normalize_hazard_class(raw.get("hazard_class")),
            packing_group=raw.get("packing_group"),
            transport_category=_safe_int(raw.get("transport_category")),
            quantity=quantity,
            unit=str(raw.get("unit", "kg") or "kg").strip(),
            num_packages=num_packages,
            package_type=str(raw.get("package_type", "Verpackung") or "Verpackung"),
        )
        item.total_quantity = round(quantity * num_packages, 3)

        # ── Faktor & Höchstmenge aus der Beförderungskategorie ──
        cat = item.transport_category
        cat_spec = TRANSPORT_CATEGORIES.get(cat) if cat is not None else None
        if cat_spec is None:
            # Unbekannte Kategorie → Fail-Safe: kein Faktor, Kategorie 0 annehmen
            item.factor = 0.0
            item.max_qty_per_tu = 0
            item.class_excluded = True
            item.notes.append(
                "Unbekannte Beförderungskategorie — Freistellung nicht möglich."
            )
            result.blocking_reasons.append(
                f"Position {item.un_number}: Beförderungskategorie unbekannt "
                f"— Freistellung nach 1.1.3.6 nicht anwendbar."
            )
        else:
            factor = cat_spec["factor"]
            max_qty = cat_spec["max_qty_per_tu"]
            # Fussnote a) zu 1.1.3.6.3: abweichende Höchstmenge (50 kg) und
            # Faktor (20) für bestimmte UN-Nummern der Kategorie 1.
            if item.un_number in FOOTNOTE_A_UN_NUMBERS:
                factor = FOOTNOTE_A_FACTOR
                max_qty = FOOTNOTE_A_MAX_QTY
                item.notes.append(
                    "Fussnote a) zu 1.1.3.6.3: Höchstmenge 50 kg, Faktor 20."
                )
            # Kategorie 4 = unbegrenzt → für die 1000-Punkte-Regel Faktor 0
            item.factor = 0.0 if factor is None else float(factor)
            item.max_qty_per_tu = max_qty

            # ── 1.1.3.6.3: Kategorie 0 ist niemals freigestellt ──
            if cat == 0:
                item.class_excluded = True
                result.blocking_reasons.append(
                    f"1.1.3.6.3: UN {item.un_number} hat Beförderungskategorie 0 "
                    f"— niemals freigestellt."
                )

            # ── Höchstmenge je Beförderungseinheit (1.1.3.6.3) ──
            if item.max_qty_per_tu is not None and item.max_qty_per_tu > 0:
                if item.total_quantity > item.max_qty_per_tu:
                    item.limit_exceeded = True
                    result.blocking_reasons.append(
                        f"1.1.3.6.3: UN {item.un_number} überschreitet die "
                        f"Höchstmenge je Beförderungseinheit "
                        f"({item.total_quantity:g} {item.unit} > "
                        f"{item.max_qty_per_tu:g} {item.unit}, Kategorie {cat})."
                    )

        item.points = round(item.total_quantity * item.factor, 2)
        total_points += item.points
        result.items.append(item)

    result.total_points = round(total_points, 2)

    if result.total_points > POINT_LIMIT:
        result.blocking_reasons.append(
            f"1.1.3.6: Gesamtpunktzahl {result.total_points:g} überschreitet "
            f"die Freigrenze von {POINT_LIMIT} Punkten."
        )

    # ── Ohne erfasste Positionen gibt es nichts freizustellen ──
    if not result.items:
        result.blocking_reasons.append(
            "Keine Gefahrgutpositionen erfasst — eine Freistellung nach "
            "1.1.3.6 kann nicht festgestellt werden."
        )

    # ── Gesamtentscheidung: alle Bedingungen müssen erfüllt sein ──
    result.is_exempt = (
        result.transport_form in EXEMPTABLE_TRANSPORT_FORMS
        and bool(result.items)
        and not result.blocking_reasons
        and result.total_points <= POINT_LIMIT
    )

    # Hinweise, die nicht blockieren, aber für den Disponenten relevant sind
    if result.is_exempt:
        result.warnings.append(
            "Freistellung nach 1.1.3.6 festgestellt. Die vollständige "
            "Prüfung durch einen Gefahrgutbeauftragten bleibt erforderlich."
        )

    return result


def _safe_int(value: Any) -> Optional[int]:
    """Konvertiert sicher nach int, gibt bei Fehler None zurück."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
