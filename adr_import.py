"""
ADR PDF Import Module
Parses ADR Chapter 3.2 Table A PDFs and imports UN number data into the database.
"""

import io
import os
import re
import tempfile
from datetime import datetime

import fitz  # PyMuPDF

from database import get_db

# ─────────────────────────────────────────────────────────────────────
# PDF Parsing
# ─────────────────────────────────────────────────────────────────────

# Regex to match a UN number at the start of a line (4 digits, possibly
# preceded by spaces or line noise).  UN numbers in ADR are always 4-digit.
UN_NUMBER_RE = re.compile(
    r"^\s*(?:UN\s*)?(\d{4})\b\s*(.*)$"
)

# Hazard class patterns (e.g. "1.1", "2.3", "8", "3 (f1)")
HAZARD_CLASS_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:\([^)]*\))?\b"
)

# Packing group: I, II, III (but only standalone/near column boundaries)
PACKING_GROUP_RE = re.compile(r"\b(I{1,3})\b")

# Tunnel codes: (B), (C), (D), (E), (B/D), (C/D), (C/E), (D/E), (B/E), (B/C)
TUNNEL_CODE_RE = re.compile(r"\(([BCDE](/[BCDE])?)\)")

# Transport category: a standalone digit 0-4 — tricky because "9" is a class,
# so we look for category in appropriate positions
CATEGORY_RE = re.compile(r"\b([0-4])\b")

# Special provisions: comma-separated numbers with possible SP/SP prefixes
SPECIAL_PROVISIONS_RE = re.compile(r"\b(SP\s*)?(\d{2,4}(?:\s*,\s*\d{2,4})*)\b")


def _clean_line(line: str) -> str:
    """Remove excessive whitespace and normalize."""
    return " ".join(line.split())


def _is_likely_continuation(line: str, prev_line: str) -> bool:
    """Determine if a line is likely a continuation of the previous entry.

    ADR PDFs often wrap long substance names or special provisions across
    multiple lines. A line is a continuation if:
    - It does NOT start with a UN number pattern
    - The previous line WAS an entry (started with UN number)
    - It contains text (not just whitespace)
    """
    if not line.strip():
        return False
    if UN_NUMBER_RE.match(line):
        return False  # This is a new entry
    # If previous line started a UN entry and current line has text,
    # it's likely a continuation
    if prev_line and UN_NUMBER_RE.match(prev_line):
        return True
    return False


def _parse_entry_lines(lines: list[str]) -> list[dict]:
    """Parse concatenated entry lines into structured dicts.

    Each entry may span multiple lines. We first group lines into entries,
    then parse each entry group.
    """
    # Step 1: Group lines into entries
    entries = []  # List of lists of lines per entry
    current_entry = []

    for line in lines:
        line = _clean_line(line)
        if not line:
            if current_entry:
                entries.append(current_entry)
                current_entry = []
            continue

        if UN_NUMBER_RE.match(line):
            # New entry
            if current_entry:
                entries.append(current_entry)
            current_entry = [line]
        else:
            # Continuation or noise
            if current_entry:
                current_entry.append(line)
            # else: stray text before any UN number — ignore

    if current_entry:
        entries.append(current_entry)

    # Step 2: Parse each entry group
    parsed = []
    for entry_lines in entries:
        data = _parse_single_entry(entry_lines)
        if data and data.get("un_number"):
            parsed.append(data)

    return parsed


def _parse_single_entry(lines: list[str]) -> dict | None:
    """Parse a group of lines representing one UN entry.

    ADR Table A columns (Chapter 3.2):
      Col 1:  UN number
      Col 2a: Proper shipping name (PSN)
      Col 3a: Class
      Col 4:  Packing group
      Col 5:  Labels
      Col 6:  Special provisions
      Col 7a: Limited quantities
      Col 8a: Packing instructions
      Col 9b: etc.
      Col 15: Transport category (0-4)
      Col 18: Tunnel restriction code

    PDFs have varied layouts; we use heuristic extraction.
    """
    if not lines:
        return None

    first_line = lines[0]
    full_text = " ".join(lines)

    # Extract UN number
    un_match = UN_NUMBER_RE.match(first_line)
    if not un_match:
        return None

    un_number = un_match.group(1)
    remainder = un_match.group(2).strip()
    if remainder:
        full_text = remainder + " " + " ".join(lines[1:]) if len(lines) > 1 else remainder

    full_text = full_text.strip()

    # ── Extract tunnel code ──
    tunnel_code = None
    tc_match = TUNNEL_CODE_RE.search(full_text)
    if tc_match:
        tunnel_code = tc_match.group(0)  # e.g. "(C/D)"

    # ── Extract special provisions ──
    special_provisions = None
    # Look for patterns like "SP 123, 456" or "188, 230, 310"
    sp_matches = re.findall(r"\b(?:SP\s*\d+|\d{2,4})(?:\s*,\s*(?:SP\s*\d+|\d{2,4}))*\b", full_text)
    if sp_matches:
        # Collect all potential SP numbers
        sp_nums = []
        for m in sp_matches:
            nums = re.findall(r"\d{2,4}", m)
            sp_nums.extend(nums)
        # Filter out clearly non-SP numbers (hazard class numbers etc.)
        # Special provisions are typically 2-4 digits, many 3-digit
        # Only filter single-digit numbers which can't be valid SP codes
        non_sp = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "0"}
        sp_nums_filtered = [n for n in sp_nums if len(n) >= 3 and n not in non_sp]
        if sp_nums_filtered:
            special_provisions = ", ".join(sp_nums_filtered)
        # Also check for 2-digit special provisions (e.g., "23", "16")
        sp_2digit = [n for n in sp_nums if len(n) == 2 and n not in non_sp]
        if sp_2digit:
            all_sp = sp_nums_filtered + sp_2digit
            special_provisions = ", ".join(dict.fromkeys(all_sp))  # dedup preserving order

    # ── Extract hazard class ──
    hazard_class = None
    # Hazard class is typically near the beginning after the UN number
    # Look for patterns: single digit with optional decimal (e.g. "3", "4.1", "2.3")
    hc_patterns = re.findall(r"\b(\d(?:\.\d)?)\b", full_text)
    # Filter: hazard classes are 2.1, 2.2, 2.3, 3, 4.1, 4.2, 4.3, 5.1, 5.2, 6.1, 6.2, 7, 8, 9
    valid_classes = {"2.1", "2.2", "2.3", "3", "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "6.2", "7", "8", "9"}
    for hc in hc_patterns:
        if hc in valid_classes:
            hazard_class = hc
            break

    # ── Extract packing group ──
    packing_group = None
    # Packing group is typically "I", "II", "III" standalone
    pg_match = re.search(r"\b(I{1,3})\b", full_text)
    if pg_match:
        pg_str = pg_match.group(1)
        # Verify it's a valid PG (I, II, or III)
        valid_pgs = {"I", "II", "III"}
        if pg_str in valid_pgs:
            packing_group = pg_str

    # ── Extract transport category ──
    transport_category = None
    # Transport category is a standalone digit 0-4.
    # It usually appears after packing group and before tunnel code.
    # Strategy: find all standalone digits 0-4, pick the one nearest
    # to a tunnel code or at the end of the text.
    cat_matches = list(re.finditer(r"\b([0-4])\b", full_text))
    if cat_matches:
        # Prefer the match closest to a tunnel code
        tc_pos = None
        if tunnel_code:
            tc_pos_in_text = full_text.find(tunnel_code)
            if tc_pos_in_text >= 0:
                # Find closest category before tunnel code
                best_dist = float("inf")
                for m in cat_matches:
                    if m.start() < tc_pos_in_text:
                        dist = tc_pos_in_text - m.start()
                        # Verify it's not part of a class number like "4.1"
                        if m.end() < len(full_text) and full_text[m.end()] != ".":
                            if dist < best_dist:
                                best_dist = dist
                                transport_category = int(m.group(1))

        # Fallback: use the last standalone 0-4 digit
        if transport_category is None:
            for m in reversed(cat_matches):
                if m.end() < len(full_text) and full_text[m.end()] != ".":
                    transport_category = int(m.group(1))
                    break

    # If transport_category not found, default to 3 (most common)
    if transport_category is None:
        transport_category = 3

    # ── Extract substance name ──
    # The substance name is everything between UN number and hazard-related
    # columns. Use heuristics to trim technical suffixes.
    substance_name = full_text

    # Try to strip known suffixes/technical data
    # Remove tunnel code from name
    if tunnel_code:
        substance_name = substance_name.replace(tunnel_code, "")

    # Remove special provisions from name area
    if special_provisions:
        for sp_num in special_provisions.split(", "):
            substance_name = substance_name.replace(sp_num, "")

    # Remove hazard class from name
    if hazard_class:
        substance_name = re.sub(
            r"\b" + re.escape(hazard_class) + r"\b",
            "", substance_name
        )

    # Remove packing group as a standalone word
    if packing_group:
        substance_name = re.sub(r"\b" + re.escape(packing_group) + r"\b", "", substance_name)

    # Clean up
    substance_name = re.sub(r"\s+", " ", substance_name).strip(" ,;:-")

    # If name is too short or empty, use the remainder from first line
    if not substance_name or len(substance_name) < 2:
        substance_name = remainder if remainder else full_text

    # Remove leftover artifacts
    substance_name = re.sub(r"\s{2,}", " ", substance_name).strip()

    return {
        "un_number": un_number,
        "substance_name_de": substance_name,
        "hazard_class": hazard_class,
        "packing_group": packing_group,
        "transport_category": transport_category,
        "tunnel_code": tunnel_code,
        "special_provisions": special_provisions,
    }


def parse_adr_pdf(pdf_path: str, version_name: str) -> list[dict]:
    """Parse an ADR PDF and extract UN number table data.

    Args:
        pdf_path: Path to the PDF file (or file-like object with .read())
        version_name: Version label (e.g. "ADR 2025")

    Returns:
        List of dicts with keys: un_number, substance_name_de,
        hazard_class, packing_group, transport_category, tunnel_code,
        special_provisions, adr_version
    """
    # Handle file uploads (FileStorage objects)
    if hasattr(pdf_path, "read"):
        pdf_bytes = pdf_path.read()
        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode("utf-8")
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        file_path = getattr(pdf_path, "filename", "uploaded.pdf")
    else:
        doc = fitz.open(pdf_path)
        file_path = pdf_path

    all_text_lines = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            # Split into lines, clean each
            for raw_line in text.split("\n"):
                cleaned = _clean_line(raw_line)
                if cleaned:
                    all_text_lines.append(cleaned)

        # Parse grouped entries
        entries = _parse_entry_lines(all_text_lines)

        # Add version info to each entry
        for entry in entries:
            entry["adr_version"] = version_name

        return entries

    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────
# Database Import
# ─────────────────────────────────────────────────────────────────────

# Points factor mapping per transport category
FACTOR_MAP = {0: 0, 1: 50, 2: 3, 3: 1, 4: None}


def import_adr_data(
    parsed_data: list[dict],
    version_name: str,
    file_path: str | None = None,
) -> dict:
    """Import parsed ADR data into the database.

    Args:
        parsed_data: List of parsed entry dicts from parse_adr_pdf()
        version_name: ADR version label (e.g. "ADR 2025")
        file_path: Path to the source PDF (for audit trail)

    Returns:
        dict: {imported: N, updated: N, errors: [...]}
    """
    db = get_db()
    cursor = db.cursor()

    # Create adr_versions record
    cursor.execute(
        "INSERT INTO adr_versions (version, file_path) VALUES (?, ?)",
        (version_name, file_path or "")
    )
    version_id = cursor.lastrowid

    imported = 0
    updated = 0
    errors = []

    for entry in parsed_data:
        try:
            un_number = entry.get("un_number")
            if not un_number:
                errors.append("Entry without UN number — skipped")
                continue

            substance_name = entry.get("substance_name_de", "") or ""
            hazard_class = entry.get("hazard_class") or None
            packing_group = entry.get("packing_group") or None
            transport_category = entry.get("transport_category", 3)
            tunnel_code = entry.get("tunnel_code") or None
            special_provisions = entry.get("special_provisions") or None

            # Compute points factor
            points_factor = FACTOR_MAP.get(transport_category)

            # Check if this UN number already exists
            existing = db.execute(
                "SELECT id FROM un_numbers WHERE un_number = ?",
                (un_number,)
            ).fetchone()

            if existing:
                # Update existing entry
                cursor.execute(
                    """UPDATE un_numbers
                       SET substance_name_de = ?,
                           hazard_class = ?,
                           packing_group = ?,
                           transport_category = ?,
                           tunnel_code = ?,
                           special_provisions = ?,
                           points_factor = ?,
                           adr_version = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE un_number = ?""",
                    (
                        substance_name,
                        hazard_class,
                        packing_group,
                        transport_category,
                        tunnel_code,
                        special_provisions,
                        points_factor,
                        version_name,
                        un_number,
                    )
                )
                updated += 1
            else:
                # Insert new entry
                cursor.execute(
                    """INSERT INTO un_numbers
                       (un_number, substance_name_de, hazard_class,
                        packing_group, transport_category, tunnel_code,
                        special_provisions, points_factor, adr_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        un_number,
                        substance_name,
                        hazard_class,
                        packing_group,
                        transport_category,
                        tunnel_code,
                        special_provisions,
                        points_factor,
                        version_name,
                    )
                )
                imported += 1

        except Exception as e:
            errors.append(f"UN {entry.get('un_number', '?')}: {e}")

    # Update version record with counts
    cursor.execute(
        "UPDATE adr_versions SET entries_imported = ?, entries_updated = ? WHERE id = ?",
        (imported, updated, version_id)
    )

    db.commit()
    db.close()

    return {
        "imported": imported,
        "updated": updated,
        "errors": errors,
        "version_id": version_id,
    }


def get_version_history() -> list[dict]:
    """Return the ADR version import history."""
    db = get_db()
    rows = db.execute(
        "SELECT id, version, import_date, file_path, entries_imported, entries_updated "
        "FROM adr_versions ORDER BY import_date DESC"
    ).fetchall()
    db.close()

    return [
        {
            "id": row["id"],
            "version": row["version"],
            "import_date": row["import_date"],
            "file_path": row["file_path"],
            "entries_imported": row["entries_imported"],
            "entries_updated": row["entries_updated"],
        }
        for row in rows
    ]


# ─────────────────────────────────────────────────────────────────────
# CLI entry point for testing
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python adr_import.py <pdf_path> <version_name>")
        print("Example: python adr_import.py ADR2025.pdf 'ADR 2025'")
        sys.exit(1)

    pdf_path = sys.argv[1]
    version_name = sys.argv[2]

    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    print(f"Parsing PDF: {pdf_path}")
    entries = parse_adr_pdf(pdf_path, version_name)
    print(f"Parsed {len(entries)} entries")

    if entries:
        print("\nFirst 5 entries:")
        for entry in entries[:5]:
            print(f"  UN {entry['un_number']}: {entry['substance_name_de'][:80]}...")
            print(f"    Class: {entry['hazard_class']}, PG: {entry['packing_group']}, "
                  f"Cat: {entry['transport_category']}, Tunnel: {entry['tunnel_code']}")

        # Optionally import
        if "--import" in sys.argv:
            result = import_adr_data(entries, version_name, pdf_path)
            print(f"\nImport result: {result['imported']} new, "
                  f"{result['updated']} updated, {len(result['errors'])} errors")
            if result["errors"]:
                print("Errors:")
                for err in result["errors"][:10]:
                    print(f"  - {err}")
