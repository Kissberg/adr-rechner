"""
ADR Beförderungspapier PDF Generator — ADR 5.4.1 compliant transport document.

Generates a professional transport document strictly following ADR 5.4.1 format:
  (a) UN number with "UN" prefix
  (b) Official proper shipping name (supplemented with technical name if needed)
  (c) Hazard class (with subclass/compatibility group/classification code as applicable)
  (d) Packing group preceded by "VG"
  (e) Number and description of packages
  (f) Total quantity with unit
  (g) Name and address of consignor (Absender)
  (h) Name and address of consignee (Empfänger)
  (i) Declaration statement

Additional:
  - 1.1.3.6 exemption note if total points ≤ 1000
  - "UMWELTGEFÄHRDEND" for UN 3077 / UN 3082
  - Tunnel restriction code in brackets after each item line
  - A4 layout with 2 cm margins, professional styling
"""

import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.colors import black, HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

from database import get_db


EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")

# ── German pluralization map for common package types ──────────────────
_PLURAL_MAP = {
    "Fass": "Fässer",
    "Fass (Stahl)": "Fässer (Stahl)",
    "Fass (Kunststoff)": "Fässer (Kunststoff)",
    "Kanister": "Kanister",
    "Karton": "Kartons",
    "Palette": "Paletten",
    "Styroporbox": "Styroporboxen",
    "Flasche": "Flaschen",
    "Eimer": "Eimer",
    "Sack": "Säcke",
    "Dose": "Dosen",
    "Trommel": "Trommeln",
    "Container": "Container",
    "IBC": "IBC",
    "Tank": "Tanks",
    "Gasflasche": "Gasflaschen",
    "Big Bag": "Big Bags",
    "Kiste": "Kisten",
    "Beutel": "Beutel",
    "Rolle": "Rollen",
}


def _pluralize(word):
    """Return the German plural form for a package type description."""
    if not word:
        return "Verpackungen"
    # Exact match in known map
    if word in _PLURAL_MAP:
        return _PLURAL_MAP[word]
    # Try case-insensitive
    lower = word.lower()
    for k, v in _PLURAL_MAP.items():
        if k.lower() == lower:
            return v if k[0].isupper() else v.lower()
    # Heuristic rules
    if word.endswith(("s", "ß", "x", "z")):
        return word + "e"
    if word.endswith("e"):
        return word + "n"
    if word.endswith(("t", "d", "g", "k", "p")):
        return word + "e"
    return word + "s"


# ── ADR 5.4.1 label model numbers by class ─────────────────────────────
# In most cases the hazard class equals the label number.
_CLASS_LABELS = {
    "1": "1",
    "1.1": "1",
    "1.2": "1",
    "1.3": "1",
    "1.4": "1.4",
    "1.5": "1.5",
    "1.6": "1.6",
    "2.1": "2.1",
    "2.2": "2.2",
    "2.3": "2.3",
    "3": "3",
    "4.1": "4.1",
    "4.2": "4.2",
    "4.3": "4.3",
    "5.1": "5.1",
    "5.2": "5.2",
    "6.1": "6.1",
    "6.2": "6.2",
    "7": "7",
    "8": "8",
    "9": "9",
}


def _packing_group_display(pg):
    """Return VG display string: 'VG I', 'VG II', 'VG III', or 'VG —'."""
    if pg and str(pg).strip():
        return f"VG {str(pg).strip()}"
    return "VG —"


def _hazard_class_display(hc):
    """Return the hazard class display string with label numbers if different."""
    hc_str = str(hc).strip() if hc else "—"
    label = _CLASS_LABELS.get(hc_str, hc_str)
    if label != hc_str:
        return f"{hc_str} (Gefahrzettel {label})"
    return hc_str


def _is_environmentally_hazardous(un_number):
    """Check if a UN number refers to an environmentally hazardous substance."""
    return str(un_number).strip() in ("3077", "3082")


def _format_quantity(qty):
    """Format quantity: remove trailing zeros, keep up to 3 decimals."""
    if qty is None:
        return "0"
    if qty == int(qty):
        return str(int(qty))
    return f"{qty:.3f}".rstrip("0")


def _build_address_html(record, label):
    """Build an HTML string for an address block."""
    if record is None:
        return f"<b>{label}:</b><br/>—"

    def _g(key):
        try:
            return record[key]
        except (KeyError, IndexError):
            return None

    lines = [f"<b>{label}:</b>"]
    name = _g("name")
    if name:
        lines.append(name)

    street = _g("street")
    zip_code = _g("zip") or ""
    city = _g("city") or ""
    country = _g("country") or ""

    addr_line = []
    if street:
        addr_line.append(street)
    city_part = " ".join(p for p in [zip_code, city] if p)
    if city_part:
        addr_line.append(city_part)
    if country:
        addr_line.append(country)

    if addr_line:
        lines.append("<br/>".join(addr_line))

    return "<br/>".join(lines)


def generate_befoerderungspapier(shipment_id):
    """
    Generate an ADR 5.4.1 compliant Beförderungspapier (transport document) as PDF.

    Args:
        shipment_id: Primary key of the shipment in the database.

    Returns:
        Absolute file path of the generated PDF.

    Raises:
        ValueError: If the shipment is not found or has no items.
    """
    # ── 1. Load data from database ─────────────────────────────────────
    db = get_db()

    shipment = db.execute(
        "SELECT * FROM shipments WHERE id = ?", (shipment_id,)
    ).fetchone()
    if shipment is None:
        db.close()
        raise ValueError(f"Sendung mit ID {shipment_id} nicht gefunden.")

    # Join with un_numbers to get authoritative ADR data.
    # Use un_db_id for exact variant match (previously ROW_NUMBER() hack
    # always picked the first variant, causing wrong VG on Beförderungspapier).
    items = db.execute(
        "SELECT si.*, "
        "un.hazard_class AS un_hazard_class, "
        "un.packing_group AS un_packing_group, "
        "un.tunnel_code, "
        "un.substance_name_de AS un_substance_name "
        "FROM shipment_items si "
        "LEFT JOIN un_numbers un ON si.un_db_id = un.id "
        "WHERE si.shipment_id = ? "
        "ORDER BY si.id",
        (shipment_id,),
    ).fetchall()

    if not items:
        db.close()
        raise ValueError(f"Sendung {shipment_id} enthält keine Gefahrgutpositionen.")

    customer = db.execute(
        "SELECT * FROM customers WHERE id = ?", (shipment["customer_id"],)
    ).fetchone()

    shipping = db.execute(
        "SELECT * FROM shipping_addresses WHERE id = ?",
        (shipment["shipping_address_id"],),
    ).fetchone()

    db.close()

    # ── 2. Prepare output path ─────────────────────────────────────────
    os.makedirs(EXPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"befoerderungspapier_{shipment_id}_{timestamp}.pdf"
    filepath = os.path.join(EXPORT_DIR, filename)

    # ── 3. Build the PDF document ──────────────────────────────────────
    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2.5 * cm,
        title="Beförderungspapier",
        author="ADR 1000-Punkte-Rechner",
        subject="Gefahrgut-Transportdokument gemäß ADR 5.4.1",
    )

    # ── Styles ─────────────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "ADR_Title",
        fontName="Helvetica-Bold",
        fontSize=18,
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    subtitle_style = ParagraphStyle(
        "ADR_Subtitle",
        fontName="Helvetica-Oblique",
        fontSize=10,
        alignment=TA_CENTER,
        spaceAfter=6,
        textColor=HexColor("#555555"),
    )
    exemption_style = ParagraphStyle(
        "ADR_Exemption",
        fontName="Helvetica-Bold",
        fontSize=9,
        alignment=TA_CENTER,
        spaceAfter=8,
        spaceBefore=2,
        textColor=HexColor("#CC0000"),
    )
    item_style = ParagraphStyle(
        "ADR_Item",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        spaceAfter=2,
    )
    env_note_style = ParagraphStyle(
        "ADR_EnvNote",
        fontName="Helvetica-Bold",
        fontSize=8,
        leftIndent=12,
        spaceAfter=4,
        textColor=HexColor("#006600"),
    )
    address_style = ParagraphStyle(
        "ADR_Address",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
    )
    total_style = ParagraphStyle(
        "ADR_Total",
        fontName="Helvetica-Bold",
        fontSize=9,
        spaceBefore=4,
        spaceAfter=4,
    )
    decl_style = ParagraphStyle(
        "ADR_Decl",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        spaceBefore=4,
    )
    sig_style = ParagraphStyle(
        "ADR_Sig",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
    )
    footer_style = ParagraphStyle(
        "ADR_Footer",
        fontName="Helvetica",
        fontSize=6.5,
        textColor=HexColor("#999999"),
        alignment=TA_CENTER,
    )

    story = []

    # ── 3a. Title block ────────────────────────────────────────────────
    story.append(Paragraph("BEFÖRDERUNGSPAPIER", title_style))
    story.append(Paragraph("gemäß ADR 5.4.1", subtitle_style))

    # ── 3b. Exemption note (1.1.3.6) ───────────────────────────────────
    total_points = float(shipment["total_points"] or 0)
    is_exempt = total_points <= 1000

    if is_exempt:
        story.append(
            Paragraph(
                "BEFÖRDERUNG IN UNTERSCHREITUNG DER FREIGRENZEN "
                "NACH ABSCHNITT 1.1.3.6",
                exemption_style,
            )
        )

    story.append(Spacer(1, 0.3 * cm))

    # ── 3c. Dangerous goods lines per ADR 5.4.1 (a)–(f) ────────────────
    for item in items:
        un_number = str(item["un_number"] or "").strip()

        # (a) UN number
        un_part = f"UN {un_number}"

        # (b) Proper shipping name — use official name from UN table,
        #     fall back to what was stored in the shipment item
        substance_name = (
            item["un_substance_name"]
            or item["substance_name"]
            or ""
        ).strip()

        # Safe accessor for sqlite3.Row (which lacks .get)
        def _val(key):
            try:
                v = item[key]
                return v if v is not None else ""
            except (KeyError, IndexError):
                return ""

        # (c) Hazard class
        hazard_class = (_val("un_hazard_class") or _val("hazard_class")).strip()
        if hazard_class:
            hazard_display = _hazard_class_display(hazard_class)
        else:
            hazard_display = "—"

        # (d) Packing group
        pg = (_val("un_packing_group") or _val("packing_group")).strip()
        pg_display = _packing_group_display(pg) if pg else "VG —"

        # (e) Number and description of packages
        num_pkg = int(item["num_packages"] or 1)
        pkg_type = (item["package_type"] or "Verpackung").strip()
        if num_pkg == 1:
            pkg_display = f"1 {pkg_type}"
        else:
            plural = _pluralize(pkg_type)
            pkg_display = f"{num_pkg} {plural}"

        # (f) Total quantity with unit
        qty = float(item["quantity"] or 0)
        unit = (item["unit"] or "").strip()
        qty_display = f"{_format_quantity(qty)} {unit}"

        # Tunnel code
        tunnel = (item["tunnel_code"] or "").strip()

        # Item points (Punktzahl)
        item_pts = float(item["item_points"] or 0)

        # Build the item line
        parts = [
            un_part,
            substance_name.upper(),
            hazard_display,
            pg_display,
            pkg_display,
            qty_display,
        ]
        item_line = ", ".join(parts)

        if tunnel:
            item_line += f" {tunnel}"

        item_line += f"  —  {_format_quantity(item_pts)} Punkte"

        story.append(Paragraph(item_line, item_style))

        # UMWELTGEFÄHRDEND note for UN 3077 / 3082
        if _is_environmentally_hazardous(un_number):
            story.append(Paragraph("UMWELTGEFÄHRDEND", env_note_style))

    story.append(Spacer(1, 0.5 * cm))

    # ── 3d. Consignor / Consignee (g) and (h) — side by side boxes ────
    sender_html = _build_address_html(shipping, "Absender (Versender)")
    receiver_html = _build_address_html(customer, "Empfänger")

    addr_table = Table(
        [
            [
                Paragraph(sender_html, address_style),
                Paragraph(receiver_html, address_style),
            ]
        ],
        colWidths=[8.2 * cm, 8.2 * cm],
    )
    addr_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("BOX", (0, 0), (-1, -1), 0.6, black),
            ("LINEBEFORE", (1, 0), (1, 0), 0.6, black),
        ])
    )
    story.append(addr_table)
    story.append(Spacer(1, 0.4 * cm))

    # ── 3e. Total quantity summary ─────────────────────────────────────
    total_qty = sum(
        float(it["quantity"] or 0) * int(it["num_packages"] or 1)
        for it in items
    )
    # Collect unique units
    units = set((it["unit"] or "").strip() for it in items)
    unit_summary = " / ".join(sorted(u for u in units if u))
    story.append(
        Paragraph(
            f"Gesamtmenge gefährlicher Güter: {_format_quantity(total_qty)} {unit_summary}",
            total_style,
        )
    )
    story.append(
        Paragraph(
            f"Gesamtpunktzahl: {_format_quantity(total_points)} Punkte"
            + (f"  (≤ 1000 → freigestellt nach 1.1.3.6)" if is_exempt else f"  (> 1000 → nicht freigestellt)"),
            total_style,
        )
    )
    story.append(Spacer(1, 0.5 * cm))

    # ── 3f. Declaration (i) ────────────────────────────────────────────
    story.append(
        Paragraph(
            "Der Absender erklärt, dass die gefährlichen Güter gemäß den "
            "Vorschriften des ADR verpackt, gekennzeichnet und bezettelt sind.",
            decl_style,
        )
    )
    story.append(Spacer(1, 0.8 * cm))

    # ── 3g. Date and signature ─────────────────────────────────────────
    today_str = datetime.now().strftime("%d.%m.%Y")
    available_width = A4[0] - 4 * cm  # 17 cm

    sig_table = Table(
        [
            [
                Paragraph(f"<b>Datum:</b> {today_str}", sig_style),
                Paragraph(
                    "<b>Unterschrift des Absenders:</b> ____________________________",
                    sig_style,
                ),
            ]
        ],
        colWidths=[available_width * 0.35, available_width * 0.65],
    )
    sig_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(sig_table)

    # ── 4. Page footer ─────────────────────────────────────────────────
    def _draw_footer(canvas, doc):
        """Draw footer with receiver info and page number on every page."""
        canvas.saveState()
        # Separator line
        canvas.setStrokeColor(HexColor("#CCCCCC"))
        canvas.setLineWidth(0.3)
        canvas.line(2 * cm, 1.8 * cm, A4[0] - 2 * cm, 1.8 * cm)
        # Page number
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(HexColor("#999999"))
        canvas.drawCentredString(A4[0] / 2.0, 1.1 * cm, f"Seite {doc.page}")
        # Shipment reference on the left
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(
            2 * cm,
            0.6 * cm,
            f"Sendung #{shipment_id}  •  erstellt am {today_str}  •  "
            f"ADR 1000-Punkte-Rechner",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)

    # ── 5. Update database ─────────────────────────────────────────────
    db = get_db()
    db.execute(
        "UPDATE shipments SET bef_papier_path = ? "
        "WHERE id = ?",
        (filepath, shipment_id),
    )
    db.commit()
    db.close()

    return filepath


# ── CLI testing entry point ────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    sid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    path = generate_befoerderungspapier(sid)
    print(f"PDF generated: {path}")
