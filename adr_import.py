"""
adr_import.py — ADR-Datenimport und -verifikation

────────────────────────────────────────────────────────────────────────
WICHTIGER GRUNDSATZ (seit v3.0)
────────────────────────────────────────────────────────────────────────
Das ADR-PDF wird NICHT mehr als Datenquelle verwendet.

Warum: Tabelle A ist eine 20-spaltige Tabelle auf Doppelseiten. Ein
textbasiertes Parsen verliert die Spaltengrenzen; die Beförderungskategorie
musste geraten werden („nächstgelegene Ziffer 0–4", Default Kategorie 3).
Bei einer Freistellungsentscheidung nach ADR 1.1.3.6 ist ein stiller
Fehler in der Kategorie ein Rechtsverstoß.

Seit v3.0 gilt deshalb:
  • Datenquelle  = amtliche BAM-Datei (Datenbank GEFAHRGUT, dl-de/by-2-0)
  • ADR-PDF      = unabhängige Verifikation + Änderungsaufsicht
                   über die Vorschriftentexte

Das PDF leistet damit zwei Dinge, die keine strukturierte Datei kann:
  1. Abgleich: eigener, unabhängiger Parse von Tabelle A wird gegen die
     Datenbank gerechnet. Wo beide übereinstimmen, ist die Wahrscheinlichkeit
     eines Fehlers sehr gering. Abweichungen werden als Liste gemeldet.
  2. Änderungsaufsicht: die Abschnitte 1.1.3.6 (1000-Punkte-Regel) und
     5.4.1.1 (Beförderungspapier) werden aus dem PDF gezogen, auf
     Schlüsselwerte geprüft und per Prüfsumme mit dem Stand der letzten
     Prüfung verglichen. Ändert sich der Text, wird das gemeldet —
     der Mensch entscheidet, nicht das Programm.

Bekannte Grenze: ADR wird zweibändig veröffentlicht. Band 1 enthält die
Teile 1–3, daher liegt 1.1.3.6 dort, 5.4.1.1 jedoch in Band 2. Fehlt ein
Abschnitt, wird das ausdrücklich gemeldet (kein stilles „nichts gefunden").
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import fitz  # PyMuPDF

from database import get_db

# ─────────────────────────────────────────────────────────────────────
# Struktur von Tabelle A (verifiziert an adr-2025-band-1.pdf)
# ─────────────────────────────────────────────────────────────────────
# Tabelle A liegt auf Doppelseiten (Spreads):
#   linke Seite  = Spalten (1)–(11)
#   rechte Seite = Spalten (12)–(20) plus (1),(2) als Anker
# Spalte (15) enthält Beförderungskategorie UND Tunnelcode in einer Zelle,
# z. B. "2\n(D/E)" oder "1\n(B1000C)".
#
# Spaltenindizes der linken Seite (find_tables):
#   0=(1) UN  1=(2) Benennung  2=(3a) Klasse  3=(3b) Klassifizierungscode
#   4=(4) VG  5=(5) Gefahrzettel  6=(6) Sondervorschriften
#   7=(7a) begrenzte Mengen  8=(7b) freigestellte Mengen  9=(8) Verpackung
# Spaltenindizes der rechten Seite (ab (12)):
#   0=(12) Tankcode  2=(14) Tankfahrzeug  3=(15) Kategorie+Tunnel
#   8=(20) Kemler-Zahl  9=(1) UN-Anker  10=(2) Benennung
#
# ACHTUNG: Eine UN-Nummer kann mehrere Zeilen (Varianten) haben, z. B.
# UN 0015 mit Gefahrzettel 1 / 1+8 / 1+6.1. Die Zeilen dürfen deshalb
# nicht per zip() gepaart werden, sondern über die UN-Nummer — die k-te
# Zeile der linken Seite zur k-ten Zeile derselben UN-Nummer rechts.

UN_RE = re.compile(r"^\d{4}$")
_CAT_TUNNEL_RE = re.compile(
    r"^\s*(?P<cat>[0-4]|-)\s*"
    r"(?:\(\s*(?P<tunnel>[BCDE]\d*[BCDE]?(?:\s*/\s*[BCDE]\d*[BCDE]?)?|-)\s*\))?\s*$"
)

# Mindestens so viele Zeilen mit UN-Nummer muss eine Seite haben, um als
# Teil von Tabelle A zu gelten (Kapitel 3.2).
_MIN_UN_LINES = 8
# Kürzerer Abschnittstext ist kein Vorschriftentext, sondern bestenfalls
# ein Eintrag im Inhaltsverzeichnis.
MIN_SECTION_CHARS = 400
# Tabelle A umfasst in ADR 2025 gut 120 Doppelseiten; zur Sicherheit
# deutlich größer ansetzen, damit auch künftige Ausgaben erfasst werden.
_MAX_TABLE_A_PAGES = 400


# ─────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────

def _open_pdf(source) -> Tuple["fitz.Document", str]:
    """Öffnet eine PDF aus Pfad, FileStorage oder Bytes."""
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        doc = fitz.open(stream=data, filetype="pdf")
        return doc, getattr(source, "filename", "uploaded.pdf")
    return fitz.open(source), str(source)


def _rows_of(doc, page_no: int) -> List[List[Optional[str]]]:
    """Zeilen der ersten Tabelle einer Seite (1-basierte Seitenzahl)."""
    tables = doc[page_no - 1].find_tables().tables
    return tables[0].extract() if tables else []


def _locate_marker(rows: List[List[Optional[str]]], marker: str
                   ) -> Tuple[Optional[int], Optional[int]]:
    """Findet (Zeilenindex, Spaltenindex) der Spaltennummern-Zeile."""
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if (cell or "").strip() == marker:
                return i, j
    return None, None


def _cell(row: Optional[List[Optional[str]]], idx: Optional[int]) -> str:
    if row is None or idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _flat(text: str) -> str:
    return re.sub(r"\s*\n\s*", " ", text).strip()


# ─────────────────────────────────────────────────────────────────────
# Tabelle A auffinden und parsen
# ─────────────────────────────────────────────────────────────────────

def locate_table_a_pages(doc) -> Tuple[int, int]:
    """Bestimmt den Seitenbereich von Tabelle A (1-basiert, inklusiv).

    Es wird nicht fest davon ausgegangen, dass Tabelle A immer auf denselben
    Seiten beginnt — die Seitenzahl hängt von der Ausgabe ab. Erkannt wird
    der längste zusammenhängende Seitenbereich, auf dem viele Zeilen mit
    vierstelliger UN-Nummer stehen.

    Returns:
        (erste linke Seite, letzte rechte Seite) oder (0, 0), wenn nichts
        gefunden wurde.
    """
    un_line = re.compile(r"^\s*(\d{4})\s")
    candidates = []
    for i in range(doc.page_count):
        text = doc[i].get_text("text")
        hits = sum(1 for ln in text.split("\n") if un_line.match(ln))
        if hits >= _MIN_UN_LINES:
            candidates.append(i + 1)

    if not candidates:
        return 0, 0

    # Längsten zusammenhängenden Block bestimmen
    best: Tuple[int, int] = (candidates[0], candidates[0])
    start = prev = candidates[0]
    for page in candidates[1:]:
        if page == prev + 1:
            prev = page
        else:
            if prev - start > best[1] - best[0]:
                best = (start, prev)
            start = prev = page
    if prev - start > best[1] - best[0]:
        best = (start, prev)

    first, last = best
    # Nicht plausibel (zu kurz oder zu lang) → trotzdem liefern, aber die
    # Aufrufer prüfen die Plausibilität anhand der Trefferzahl.
    if last - first + 1 > _MAX_TABLE_A_PAGES:
        last = first + _MAX_TABLE_A_PAGES - 1
    # Bei ungerader Seitenzahl endet der Spread auf der nächsten Seite
    if (last - first + 1) % 2:
        last -= 1
    return first, last


def parse_table_a(doc, first_page: Optional[int] = None,
                  last_page: Optional[int] = None) -> Tuple[List[dict], List[str]]:
    """Extrahiert Tabelle A strukturiert (Spalten, nicht Textheuristik).

    Returns:
        (entries, warnings)
    """
    if first_page is None or last_page is None:
        first_page, last_page = locate_table_a_pages(doc)
    if not first_page:
        return [], ["Tabelle A nicht gefunden — keine Seite mit "
                    "mindestens %d UN-Zeilen." % _MIN_UN_LINES]

    entries: List[dict] = []
    warnings: List[str] = []

    for left_no in range(first_page, last_page + 1, 2):
        right_no = left_no + 1
        if right_no > last_page:
            break

        left = _rows_of(doc, left_no)
        right = _rows_of(doc, right_no)
        if not left or not right:
            warnings.append("Seiten %d/%d: keine Tabelle erkannt" % (left_no, right_no))
            continue

        li, lj = _locate_marker(left, "(1)")
        ri, rj = _locate_marker(right, "(12)")
        if li is None or ri is None:
            warnings.append("Seiten %d/%d: Spaltennummern nicht gefunden"
                            % (left_no, right_no))
            continue

        # Ankerspalte (1) der rechten Seite
        r_un = None
        for j, cell in enumerate(right[ri]):
            if (cell or "").strip() == "(1)":
                r_un = j
                break
        if r_un is None:
            warnings.append("Seite %d: keine UN-Ankerspalte" % right_no)
            continue

        # Rechte Seite: UN-Nummer → Liste der Zeilen (Varianten beachten!)
        right_by_un: Dict[str, List[List[Optional[str]]]] = {}
        for row in right[ri + 1:]:
            un = _cell(row, r_un)
            if UN_RE.match(un):
                right_by_un.setdefault(un, []).append(row)

        for row in left[li + 1:]:
            un = _cell(row, lj)
            if not UN_RE.match(un):
                continue  # Fortsetzungszeile oder Fußnote

            pending = right_by_un.get(un)
            if not pending:
                warnings.append("Seiten %d/%d: UN %s nur auf der linken Seite"
                                % (left_no, right_no, un))
                continue
            rrow = pending.pop(0)

            raw15 = _flat(_cell(rrow, rj + 3))
            m = _CAT_TUNNEL_RE.match(raw15)
            category: Optional[int] = None
            tunnel: Optional[str] = None
            if m:
                cat_raw = m.group("cat")
                category = int(cat_raw) if cat_raw.isdigit() else None
                tun_raw = m.group("tunnel")
                tunnel = None if (not tun_raw or tun_raw == "-") else tun_raw
            elif raw15:
                warnings.append("Seite %d, UN %s: Spalte (15) nicht lesbar: %r"
                                % (right_no, un, raw15))

            # Spalte (4) enthält bei Sondereinträgen keinen Verpackungsgruppe,
            # sondern einen Vermerk wie „BEFÖRDERUNG VERBOTEN" oder
            # „UNTERLIEGT NICHT DEN VORSCHRIFTEN DES ADR". Solche Vermerke
            # dürfen nicht als Verpackungsgruppe weitergegeben werden —
            # sonst entstehen Scheinabweichungen beim Abgleich.
            pg_raw = _flat(_cell(row, lj + 4))
            packing_group = pg_raw if pg_raw in ("I", "II", "III") else None

            entries.append({
                "un_number": un,
                "substance_name_de": _flat(_cell(row, lj + 1)),
                "hazard_class": _cell(row, lj + 2) or None,
                "classification_code": _cell(row, lj + 3) or None,
                "packing_group": packing_group,
                "labels": _flat(_cell(row, lj + 5)) or None,
                "special_provisions": _flat(_cell(row, lj + 6)) or None,
                "limited_quantity": _flat(_cell(row, lj + 7)) or None,
                "excepted_quantity": _flat(_cell(row, lj + 8)) or None,
                "tank_code": _cell(rrow, rj) or None,
                "transport_category": category,
                "tunnel_code": tunnel,
                "hazard_identification_no": _cell(rrow, rj + 8) or None,
                "_raw15": raw15,
                "_pg_raw": pg_raw or None,
                "_page": left_no,
            })

    return entries, warnings


def parse_adr_pdf(pdf_source, version_name: str) -> List[dict]:
    """Parst ein ADR-PDF und gibt die Einträge von Tabelle A zurück.

    Hinweis: Die Rückgabe dient der Verifikation, nicht mehr dem Import.
    """
    doc, _ = _open_pdf(pdf_source)
    try:
        entries, _warnings = parse_table_a(doc)
        for entry in entries:
            entry["adr_version"] = version_name
        return entries
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────
# Verifikation: PDF gegen Datenbank
# ─────────────────────────────────────────────────────────────────────

# Felder, die für die Freistellungsentscheidung tragend sind.
CRITICAL_FIELDS = ("transport_category", "tunnel_code", "packing_group",
                   "hazard_class")


@dataclass
class VerifyResult:
    total_pdf: int = 0
    total_db: int = 0
    matched: int = 0
    only_in_pdf: int = 0
    only_in_db: int = 0
    differences: List[dict] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)
    agreement: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total_pdf": self.total_pdf,
            "total_db": self.total_db,
            "matched": self.matched,
            "only_in_pdf": self.only_in_pdf,
            "only_in_db": self.only_in_db,
            "differences": self.differences,
            "parse_warnings": self.parse_warnings[:50],
            "agreement": round(self.agreement, 4),
        }


def _norm(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def verify_against_database(pdf_entries: List[dict],
                            parse_warnings: Optional[List[str]] = None
                            ) -> VerifyResult:
    """Vergleicht den PDF-Parse mit dem Datenbankbestand.

    Verglichen wird je UN-Nummer über die Menge der Variantenwerte, weil
    PDF und BAM eine UN-Nummer unterschiedlich fein aufteilen können
    (die BAM führt z. B. „trocken" und „angefeuchtet" getrennt, das PDF
    in einer Zeile).

    Nur Abweichungen werden gemeldet — eine lange Trefferliste nutzt
    niemandem.
    """
    result = VerifyResult(total_pdf=len(pdf_entries),
                          parse_warnings=list(parse_warnings or []))

    db = get_db()
    try:
        rows = db.execute(
            "SELECT un_number, hazard_class, packing_group, "
            "       transport_category, tunnel_code "
            "FROM un_numbers"
        ).fetchall()
    finally:
        db.close()

    db_by_un: Dict[str, Dict[str, set]] = {}
    for r in rows:
        bucket = db_by_un.setdefault(
            r["un_number"],
            {"hazard_class": set(), "packing_group": set(),
             "transport_category": set(), "tunnel_code": set()})
        for f in bucket:
            bucket[f].add(_norm(r[f]))

    pdf_by_un: Dict[str, Dict[str, set]] = {}
    for e in pdf_entries:
        bucket = pdf_by_un.setdefault(
            e["un_number"],
            {"hazard_class": set(), "packing_group": set(),
             "transport_category": set(), "tunnel_code": set()})
        for f in bucket:
            bucket[f].add(_norm(e.get(f)))

    result.total_db = len(db_by_un)
    all_un = set(db_by_un) | set(pdf_by_un)
    result.only_in_pdf = len(set(pdf_by_un) - set(db_by_un))
    result.only_in_db = len(set(db_by_un) - set(pdf_by_un))

    matched = 0
    for un in sorted(set(db_by_un) & set(pdf_by_un)):
        same = True
        for f in CRITICAL_FIELDS:
            a, b = db_by_un[un][f], pdf_by_un[un][f]
            if a == b:
                continue
            # Leere Mengen gelten als übereinstimmend (beide ohne Wert)
            if not (a - {None}) and not (b - {None}):
                continue
            same = False
            result.differences.append({
                "un_number": un,
                "field": f,
                "database": ", ".join(sorted(str(x) for x in a if x is not None)) or "(leer)",
                "pdf": ", ".join(sorted(str(x) for x in b if x is not None)) or "(leer)",
            })
        if same:
            matched += 1

    result.matched = matched
    comparable = len(set(db_by_un) & set(pdf_by_un))
    result.agreement = (matched / comparable) if comparable else 0.0
    return result


# ─────────────────────────────────────────────────────────────────────
# Änderungsaufsicht über die Vorschriftentexte
# ─────────────────────────────────────────────────────────────────────

# Abschnitte, die für diese Anwendung tragend sind.
REGULATION_SECTIONS = {
    "1.1.3.6": {
        "title": "Freistellungen in Zusammenhang mit Mengen, die je "
                 "Beförderungseinheit befördert werden (1000-Punkte-Regel)",
        "submarkers": ("1.1.3.6.3", "1.1.3.6.4"),
        "next_section": "1.1.3.7",
    },
    "5.4.1.1": {
        "title": "Angaben im Beförderungspapier",
        "submarkers": ("5.4.1.1.1", "5.4.1.1"),
        "next_section": "5.4.1.2",
    },
}

# Schlüsselwerte, deren Vorhandensein im Text geprüft wird.
# Kein Wert wird „geglaubt" — der Text wird zusätzlich im Klartext
# ausgegeben, damit der Mensch selbst nachsehen kann.
POINT_RULE_MARKERS = {
    "point_limit_1000": (r"\b1000\b", "Gesamtpunktgrenze 1000"),
    "max_qty_cat1_20": (r"\b20\b", "Höchstmenge Kategorie 1: 20"),
    "max_qty_cat2_333": (r"\b333\b", "Höchstmenge Kategorie 2: 333"),
    "max_qty_cat3_1000": (r"\b1000\b", "Höchstmenge Kategorie 3: 1000"),
    "unlimited_cat4": (r"unbegrenzt", "Kategorie 4: unbegrenzt"),
    "footnote_a_50kg": (r"\b50\s*kg\b", "Fussnote a): 50 kg"),
    "footnote_a_un_1017": (r"\b1017\b", "Fussnote a) enthält UN 1017"),
    "footnote_a_un_0081": (r"\b0081\b", "Fussnote a) enthält UN 0081"),
}

BEF_PAPIER_MARKERS = {
    "un_number": (r"UN-Nummer", "UN-Nummer"),
    "proper_shipping_name": (r"Benennung", "offizielle Benennung"),
    "class": (r"Klasse", "Klasse"),
    "packing_group": (r"Verpackungsgruppe", "Verpackungsgruppe"),
    "quantity": (r"Menge", "Menge"),
    "consignor": (r"Absender", "Absender"),
    "consignee": (r"Empfänger", "Empfänger"),
}


@dataclass
class SectionReport:
    section: str
    title: str = ""
    found: bool = False
    pages: List[int] = field(default_factory=list)
    text: str = ""
    checksum: str = ""
    markers: Dict[str, bool] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section,
            "title": self.title,
            "found": self.found,
            "pages": self.pages,
            "text": self.text,
            "checksum": self.checksum,
            "markers": self.markers,
            "note": self.note,
        }


def _longest_run(pages: List[int]) -> List[int]:
    """Längster zusammenhängender Seitenblock.

    Ein Abschnitt kann an späterer Stelle noch einmal zitiert werden
    (z. B. 1.1.3.6 im Kapitel 3.2). Solche Fundstellen gehören nicht zum
    Vorschriftentext und würden ihn verfälschen.
    """
    if not pages:
        return []
    best = [pages[0]]
    current = [pages[0]]
    for page in pages[1:]:
        if page == current[-1] + 1:
            current.append(page)
        else:
            if len(current) > len(best):
                best = current
            current = [page]
    return current if len(current) > len(best) else best


def _find_section_pages(doc, submarkers: Iterable[str]) -> List[int]:
    """Seiten, auf denen einer der Unterabschnitte als eigene Zeile steht.

    Unterabschnitte (z. B. 1.1.3.6.3) kommen im Inhaltsverzeichnis nicht
    vor — damit wird das Inhaltsverzeichnis zuverlässig ausgeschlossen.
    Zurückgegeben wird nur der längste zusammenhängende Block.
    """
    pattern = re.compile(r"(?m)^\s*(" + "|".join(
        re.escape(m) for m in submarkers) + r")\b")
    pages = []
    for i in range(doc.page_count):
        if pattern.search(doc[i].get_text("text")):
            pages.append(i + 1)
    return _longest_run(pages)


def _extract_section_text(doc, pages: List[int], first_marker: str,
                          next_section: str) -> str:
    """Fügt den Text der gefundenen Seiten zusammen und schneidet sauber ab."""
    if not pages:
        return ""

    chunks: List[str] = []
    for page_no in pages:
        chunks.append(doc[page_no - 1].get_text("text"))
    text = "\n".join(chunks)

    start = text.find(first_marker)
    if start >= 0:
        text = text[start:]

    end = text.find(next_section)
    if end > 0:
        text = text[:end]

    # Offensichtlichen Satzabbruch der PDF-Spalten etwas glätten
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def scan_regulation_sections(pdf_source) -> Dict[str, Any]:
    """Liest die tragenden ADR-Abschnitte aus dem PDF und prüft Schlüsselwerte.

    Zweck ist nicht, die Vorschrift „automatisch zu verstehen", sondern
    dem Menschen den aktuellen Wortlaut hinzulegen und ihn aufzufordern,
    genau die Stellen zu prüfen, von denen dieses Programm abhängt.

    Returns:
        dict mit je einem Abschnittsbericht, einer Änderungsmeldung
        (Vergleich mit der letzten Prüfung) und Hinweisen zu fehlenden
        Abschnitten.
    """
    doc, name = _open_pdf(pdf_source)
    try:
        reports: Dict[str, SectionReport] = {}
        for section, meta in REGULATION_SECTIONS.items():
            rep = SectionReport(section=section, title=meta["title"])
            pages = _find_section_pages(doc, meta["submarkers"])
            rep.pages = pages
            if not pages:
                rep.note = (
                    "Abschnitt %s wurde in dieser Datei nicht gefunden. "
                    "ADR wird zweibändig veröffentlicht: Band 1 enthält die "
                    "Teile 1–3 (dort liegt %s), Band 2 die Teile 4–9 "
                    "(dort liegt 5.4.1.1). Bitte den entsprechenden Band "
                    "hochladen."
                    % (section, "1.1.3.6" if section.startswith("1.") else "5.4.1.1")
                )
                reports[section] = rep
                continue

            rep.text = _extract_section_text(
                doc, pages, section, meta["next_section"])

            # Nur ein Inhaltsverzeichnis-Eintrag? Dann liegt der Abschnitt
            # in einem anderen Band. Das darf nicht als „gefunden" gelten,
            # sonst wiegt der Mensch sich in falscher Sicherheit.
            if len(rep.text) < MIN_SECTION_CHARS:
                rep.note = (
                    "Abschnitt %s steht in dieser Datei nur im "
                    "Inhaltsverzeichnis (%d Zeichen) — der eigentliche "
                    "Vorschriftentext ist nicht enthalten. ADR wird "
                    "zweibändig veröffentlicht: Band 1 enthält die Teile "
                    "1–3, Band 2 die Teile 4–9. Für %s bitte den Band "
                    "hochladen, der Teil 5 enthält."
                    % (section, len(rep.text),
                       "1.1.3.6" if section.startswith("1.") else "5.4.1.1")
                )
                reports[section] = rep
                continue

            rep.found = True
            rep.checksum = hashlib.sha256(
                rep.text.encode("utf-8")).hexdigest()[:16]

            markers = (POINT_RULE_MARKERS if section.startswith("1.")
                       else BEF_PAPIER_MARKERS)
            flat = re.sub(r"\s+", " ", rep.text)
            for key, (pattern, _label) in markers.items():
                rep.markers[key] = bool(re.search(pattern, flat))

            missing = [k for k, hit in rep.markers.items() if not hit]
            if missing:
                rep.note = (
                    "Folgende Schlüsselwerte wurden im Text nicht gefunden: "
                    + ", ".join(missing)
                    + ". Bitte den Wortlaut unten prüfen — entweder hat sich "
                      "die Vorschrift geändert, oder der Text wurde nicht "
                      "vollständig erfasst."
                )
            reports[section] = rep

        changes = _compare_with_last_scan(reports)
        return {
            "source": name,
            "sections": {k: v.as_dict() for k, v in reports.items()},
            "changes": changes,
        }
    finally:
        doc.close()


def _compare_with_last_scan(reports: Dict[str, SectionReport]) -> List[dict]:
    """Vergleicht die Prüfsummen mit der letzten gespeicherten Prüfung."""
    conn = get_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adr_section_scans ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " section VARCHAR(20) NOT NULL,"
            " checksum VARCHAR(32) NOT NULL,"
            " scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            " pages VARCHAR(100)"
            ")"
        )
        changes = []
        for section, rep in reports.items():
            if not rep.found:
                continue
            last = conn.execute(
                "SELECT checksum, pages FROM adr_section_scans "
                "WHERE section = ? ORDER BY id DESC LIMIT 1",
                (section,),
            ).fetchone()
            if last is None:
                changes.append({
                    "section": section,
                    "status": "first_scan",
                    "message": "Erste Erfassung — bitte den Wortlaut einmal "
                               "prüfen und als Ausgangsstand bestätigen.",
                })
            elif last["checksum"] != rep.checksum:
                changes.append({
                    "section": section,
                    "status": "changed",
                    "message": "Der Text von Abschnitt %s hat sich gegenüber "
                               "der letzten Prüfung geändert. Bitte prüfen, "
                               "ob sich die Berechnungsgrundlagen oder die "
                               "Angaben im Beförderungspapier geändert haben."
                               % section,
                })
            else:
                changes.append({
                    "section": section,
                    "status": "unchanged",
                    "message": "Unverändert gegenüber der letzten Prüfung.",
                })
            conn.execute(
                "INSERT INTO adr_section_scans (section, checksum, pages) "
                "VALUES (?, ?, ?)",
                (section, rep.checksum, ",".join(str(p) for p in rep.pages)),
            )
        conn.commit()
        return changes
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# Import: BAM-Daten in die Datenbank
# ─────────────────────────────────────────────────────────────────────

def import_bam_data(entries, version_name: str,
                    file_path: Optional[str] = None) -> Dict[str, Any]:
    """Schreibt BAM-Einträge in die Datenbank.

    Der natürliche Schlüssel ist (un_number, variant). Damit werden die
    Varianten einer UN-Nummer (Verpackungsgruppen!) nicht mehr gegenseitig
    überschrieben — das war der Fehler der PDF-Importfassung.
    """
    from datetime import datetime

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "INSERT INTO adr_versions (version, file_path) VALUES (?, ?)",
        (version_name, file_path or ""),
    )
    version_id = cursor.lastrowid

    from database import (FACTOR_BY_CATEGORY, MAX_QTY_BY_CATEGORY,
                          FOOTNOTE_A_MAX_QTY, FOOTNOTE_A_FACTOR,
                          FOOTNOTE_A_UN_NUMBERS)

    now = datetime.now().isoformat()
    imported = updated = unchanged = 0
    errors: List[str] = []

    for e in entries:
        try:
            un = (e.un_number or "").strip()
            if not un:
                errors.append("Eintrag ohne UN-Nummer — übersprungen")
                continue

            tc = e.transport_category
            mq = MAX_QTY_BY_CATEGORY.get(tc) if tc is not None else None
            factor = e.multiplier
            if factor is None and tc is not None:
                factor = FACTOR_BY_CATEGORY.get(tc)
            if un in FOOTNOTE_A_UN_NUMBERS:
                mq = FOOTNOTE_A_MAX_QTY
                factor = FOOTNOTE_A_FACTOR

            values = (
                e.variant, e.full_name_de[:200], e.spec_de, e.name_en, e.name_fr,
                e.hazard_class, e.classification_code, e.labels, e.packing_group,
                tc, e.tunnel_code, e.special_provisions, e.limited_quantity,
                e.excepted_quantity, e.packing_instructions, e.tank_code,
                e.vehicle_for_tank, e.hazard_identification_no,
                factor, e.multiplier, mq, e.prohibited,
                "BAM_DGG", 100, e.notes, version_name, now,
            )

            # IFNULL statt ISNULL: in SQLite ist ISNULL ein Postfix-Operator,
            # keine Funktion. NULL-Varianten müssen vergleichbar sein.
            existing = db.execute(
                "SELECT id FROM un_numbers WHERE un_number = ? "
                "AND IFNULL(variant, -1) = IFNULL(?, -1)",
                (un, e.variant),
            ).fetchone()

            if existing:
                cursor.execute(
                    """UPDATE un_numbers SET
                         variant=?, substance_name_de=?, specification_de=?,
                         substance_name_en=?, substance_name_fr=?, hazard_class=?,
                         classification_code=?, danger_label=?, packing_group=?,
                         transport_category=?, tunnel_code=?, special_provisions=?,
                         limited_quantity=?, excepted_quantity=?,
                         packing_instructions=?, tank_code=?, vehicle_for_tank=?,
                         hazard_identification_no=?, points_factor=?, multiplier=?,
                         max_quantity_per_transport=?, prohibited=?,
                         data_source=?, confidence=?, notes=?,
                         adr_version=?, updated_at=?
                       WHERE id = ?""",
                    values + (existing["id"],),
                )
                updated += 1
            else:
                cursor.execute(
                    """INSERT INTO un_numbers
                       (un_number, variant, substance_name_de, specification_de,
                        substance_name_en, substance_name_fr, hazard_class,
                        classification_code, danger_label, packing_group,
                        transport_category, tunnel_code, special_provisions,
                        limited_quantity, excepted_quantity, packing_instructions,
                        tank_code, vehicle_for_tank, hazard_identification_no,
                        points_factor, multiplier, max_quantity_per_transport,
                        prohibited, data_source, confidence, notes,
                        adr_version, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (un,) + values,
                )
                imported += 1
        except Exception as exc:  # pragma: no cover - defensiv
            errors.append(f"UN {getattr(e, 'un_number', '?')}: {exc}")

    cursor.execute(
        "UPDATE adr_versions SET entries_imported = ?, entries_updated = ? "
        "WHERE id = ?", (imported, updated, version_id))

    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_un_variant "
            "ON un_numbers(un_number, variant)")
    except sqlite3.IntegrityError:
        errors.append("Doppelte (UN-Nummer, Variante)-Paare — "
                      "eindeutiger Index konnte nicht angelegt werden.")

    db.commit()
    db.close()

    return {
        "imported": imported,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
        "version_id": version_id,
        "entries_imported": imported,
        "entries_updated": updated,
    }


def get_version_history() -> List[dict]:
    """Rückgabe des Importverlaufs."""
    db = get_db()
    rows = db.execute(
        "SELECT id, version, import_date, file_path, entries_imported, "
        "entries_updated FROM adr_versions ORDER BY import_date DESC"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_last_scan_history(limit: int = 20) -> List[dict]:
    """Verlauf der Vorschriftenprüfungen (Prüfsummen je Abschnitt)."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, section, checksum, scanned_at, pages "
            "FROM adr_section_scans ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    return [dict(r) for r in rows]
