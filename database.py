"""
ADR 1000-Punkte-Rechner — Database Module
Creates and manages the SQLite database for dangerous goods transport calculations
per ADR 1.1.3.6 (1000-Punkte-Regel).

Transport Categories (ADR 1.1.3.6, Abschnitt 1.1.3.6.3, Tabelle):
  Category 0: factor 0    — Class 1 (1.1A/L, 1.2L, 1.3L, UN 0190), Class 6.2
                             (UN 2814/2900/3549), Class 7 (UN 2912–2919, 2977,
                             2978, 3321–3333), certain specific UN numbers
  Category 1: factor 50   — Class 1 (1.1B–1.1J, 1.2B–1.2J, 1.3C/G/H/J, 1.5D),
                             toxic gases (T, TC, TO, TFC, TOC), PG I substances,
                             organic peroxides Type B
                             (Fussnote a: UN 0081/0082/0084/0241/0331/0332/0482/
                              1005/1017 → Faktor 20, Höchstmenge 50 kg)
  Category 2: factor 3    — Flammable gases (group F), PG II substances,
                             Class 1 (1.4B–1.4G, 1.6N), Class 6.2 (UN 3291),
                             lithium batteries
  Category 3: factor 1    — PG III substances, non-toxic/non-flammable gases
                             (groups A, O)
  Category 4: unlimited   — Class 1.4S, Class 7 (UN 2908–2911), empty
                             uncleaned packagings
"""

import sqlite3
import os
import secrets
from datetime import datetime

from adr_rules import (
    FOOTNOTE_A_UN_NUMBERS,
    FOOTNOTE_A_FACTOR,
    FOOTNOTE_A_MAX_QTY,
)

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "adr.db")

# Maximale Wartezeit bei Schreibkonflikten (ms). Erhöht, weil mehrere
# Gunicorn-Worker gleichzeitig auf dieselbe SQLite-Datei zugreifen.
BUSY_TIMEOUT_MS = 15000

# ── ADR 1.1.3.6.3 — Beförderungskategorie → Punktfaktor bzw. Höchstmenge ──
# Kat. 4 ist «unbegrenzt»: Faktor 0 (keine Anrechnung), Höchstmenge None.
# Kat. 0 ist niemals freigestellt: Faktor 0, Höchstmenge 0.
FACTOR_BY_CATEGORY = {0: 0, 1: 50, 2: 3, 3: 1, 4: 0}
MAX_QTY_BY_CATEGORY = {0: 0, 1: 20, 2: 333, 3: 1000, 4: None}


def get_db() -> sqlite3.Connection:
    """Return a SQLite connection with row factory and WAL mode enabled."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def init_db() -> None:
    """Create all tables if they do not exist."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        -- UN numbers database (Table A of ADR Chapter 3.2)
        CREATE TABLE IF NOT EXISTS un_numbers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            un_number       VARCHAR(10) NOT NULL,
            substance_name_de VARCHAR(200),
            substance_name_en VARCHAR(200),
            hazard_class    VARCHAR(10),
            danger_label    VARCHAR(10),
            packing_group   VARCHAR(5),
            transport_category INTEGER CHECK(transport_category BETWEEN 0 AND 4),
            tunnel_code     VARCHAR(10),
            special_provisions TEXT,
            points_factor   DECIMAL(5,2),
            max_quantity_per_transport DECIMAL(10,2),
            adr_version     VARCHAR(10),
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Customers (consignees / Empfänger)
        CREATE TABLE IF NOT EXISTS customers (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      VARCHAR(200) NOT NULL,
            street    VARCHAR(200),
            zip       VARCHAR(10),
            city      VARCHAR(100),
            country   VARCHAR(50) DEFAULT 'Deutschland',
            contact   VARCHAR(100),
            phone     VARCHAR(50),
            email     VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Shipping addresses (Versandadressen / Absender)
        CREATE TABLE IF NOT EXISTS shipping_addresses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       VARCHAR(200) NOT NULL,
            street     VARCHAR(200),
            zip        VARCHAR(10),
            city       VARCHAR(100),
            country    VARCHAR(50) DEFAULT 'Deutschland',
            is_default BOOLEAN DEFAULT 0
        );

        -- Shipments (Sendungen / Beförderungsvorgänge)
        CREATE TABLE IF NOT EXISTS shipments (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            customer_id          INTEGER REFERENCES customers(id),
            shipping_address_id  INTEGER REFERENCES shipping_addresses(id),
            total_points         DECIMAL(10,2),
            is_exempt            BOOLEAN,
            bef_papier_path      VARCHAR(500),
            adr_version          VARCHAR(10),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (shipping_address_id) REFERENCES shipping_addresses(id)
        );

        -- Shipment line items (Sendungspositionen)
        CREATE TABLE IF NOT EXISTS shipment_items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            shipment_id         INTEGER REFERENCES shipments(id),
            un_number           VARCHAR(10),
            un_db_id            INTEGER REFERENCES un_numbers(id),
            substance_name      VARCHAR(200),
            quantity            DECIMAL(10,3),
            unit                VARCHAR(10),
            transport_category  INTEGER,
            points_factor       DECIMAL(5,2),
            item_points         DECIMAL(10,2),
            num_packages        INTEGER DEFAULT 1,
            package_type        VARCHAR(50) DEFAULT 'Verpackung',
            FOREIGN KEY (shipment_id) REFERENCES shipments(id)
        );

        -- ADR version import history
        CREATE TABLE IF NOT EXISTS adr_versions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            version         VARCHAR(20) NOT NULL,
            import_date     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_path       VARCHAR(500),
            entries_imported INTEGER DEFAULT 0,
            entries_updated  INTEGER DEFAULT 0
        );

        -- Benutzer (Authentifizierung / Autorisierung)
        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            username       VARCHAR(100) NOT NULL UNIQUE,
            password_hash  VARCHAR(300) NOT NULL,
            role           VARCHAR(20) NOT NULL DEFAULT 'user',
            active         BOOLEAN NOT NULL DEFAULT 1,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login     TIMESTAMP
        );

        -- Änderungsprotokoll (append-only, DSGVO Art. 30 / GoBD)
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id     INTEGER,
            username    VARCHAR(100),
            action      VARCHAR(20) NOT NULL,
            entity      VARCHAR(50) NOT NULL,
            entity_id   INTEGER,
            detail      TEXT,
            ip_address  VARCHAR(50)
        );

        -- Index for fast UN number lookups
        CREATE INDEX IF NOT EXISTS idx_un_number ON un_numbers(un_number);
        CREATE INDEX IF NOT EXISTS idx_shipment_id ON shipment_items(shipment_id);
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_shipments_created ON shipments(created_at);
    """)

    # ── Migrations for columns added after initial schema ──
    try:
        cursor.execute("ALTER TABLE un_numbers ADD COLUMN danger_label VARCHAR(10)")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        cursor.execute("ALTER TABLE shipment_items ADD COLUMN num_packages INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE shipment_items ADD COLUMN package_type VARCHAR(50) DEFAULT 'Verpackung'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE shipment_items ADD COLUMN un_db_id INTEGER")
    except sqlite3.OperationalError:
        pass

    # ── v2.0: Sendungen um rechtlich relevante Felder erweitern ──
    V2_SHIPMENT_COLUMNS = {
        "doc_number": "VARCHAR(40)",
        "transport_form": "VARCHAR(20) DEFAULT 'package'",
        "exemption_reasons": "TEXT",
        "warnings": "TEXT",
        "created_by": "VARCHAR(100)",
        "notes": "TEXT",
    }
    for col, ddl in V2_SHIPMENT_COLUMNS.items():
        try:
            cursor.execute(f"ALTER TABLE shipments ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass

    V2_ITEM_COLUMNS = {
        "hazard_class": "VARCHAR(10)",
        "total_quantity": "DECIMAL(12,3)",
        "limit_exceeded": "BOOLEAN DEFAULT 0",
        "class_excluded": "BOOLEAN DEFAULT 0",
    }
    for col, ddl in V2_ITEM_COLUMNS.items():
        try:
            cursor.execute(f"ALTER TABLE shipment_items ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass

    # ── v3.0: Volldatensatz der BAM (Datenbank GEFAHRGUT) ──
    # Eine UN-Nummer hat in Tabelle A mehrere Varianten (Verpackungsgruppen,
    # Spezifikationen). Die Beförderungskategorie ist variantenabhängig:
    #   UN 1133  PG I → Kat 1 | PG II → Kat 2 | PG III → Kat 3
    # variant = lfd. Nr. der BAM (N_LFDNR). Damit wird (un_number, variant)
    # zum natürlichen Schlüssel — ein UPDATE nur auf un_number würde die
    # Kategorien der Verpackungsgruppen gegenseitig überschreiben.
    V3_UN_COLUMNS = {
        "variant": "INTEGER",
        "specification_de": "VARCHAR(200)",
        "substance_name_fr": "VARCHAR(200)",
        "classification_code": "VARCHAR(20)",
        "limited_quantity": "VARCHAR(20)",
        "excepted_quantity": "VARCHAR(20)",
        "packing_instructions": "VARCHAR(200)",
        "tank_code": "VARCHAR(30)",
        "vehicle_for_tank": "VARCHAR(30)",
        "hazard_identification_no": "VARCHAR(20)",
        "multiplier": "DECIMAL(5,2)",
        "prohibited": "VARCHAR(10)",
        "data_source": "VARCHAR(20)",
        "confidence": "INTEGER DEFAULT 100",
        "verified": "BOOLEAN DEFAULT 0",
        "notes": "TEXT",
    }
    for col, ddl in V3_UN_COLUMNS.items():
        try:
            cursor.execute(f"ALTER TABLE un_numbers ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def next_doc_number(conn: sqlite3.Connection = None) -> str:
    """
    Erzeugt eine fortlaufende, eindeutige Beförderungspapier-Nummer.

    Format: BP-YYYY-NNNNNN (z. B. BP-2026-000042)
    Die Nummer wird im Formularfeld des Beförderungspapiers ausgewiesen und
    dient der Zuordnung zum archivierten PDF.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        year = datetime.now().year
        prefix = f"BP-{year}-%"
        row = conn.execute(
            "SELECT doc_number FROM shipments "
            "WHERE doc_number LIKE ? ORDER BY doc_number DESC LIMIT 1",
            (prefix,),
        ).fetchone()
        if row and row["doc_number"]:
            try:
                last = int(str(row["doc_number"]).rsplit("-", 1)[-1])
            except ValueError:
                last = 0
        else:
            last = 0
        return f"BP-{year}-{last + 1:06d}"
    finally:
        if own:
            conn.close()



def seed_un_numbers() -> int:
    """
    Populate the un_numbers table with all dangerous goods from ADR 2025 Table A.

    Datenquelle ist seit v3.0 die amtliche Datei der BAM (Datenbank GEFAHRGUT,
    dl-de/by-2-0). Sie enthält Beförderungskategorie, Tunnelcode und den
    Punktfaktor nach 1.1.3.6 als eigene Felder — es wird nichts mehr geraten.

    Liegt keine BAM-Datei vor, wird auf das alte JSON-Seed zurückgefallen
    (aus dem PDF geparst, unvollständig — nur als Notbehelf gedacht).

    Returns the number of variants inserted.
    """
    bam_path = os.path.join(DB_DIR, "bam", "ADR25_csv.txt")
    if os.path.exists(bam_path):
        return _seed_from_bam(bam_path)
    return _seed_from_json()


def _clear_un_numbers(cursor) -> None:
    """Leert die UN-Tabelle und löst dabei die Referenzen der Positionen.

    shipment_items speichert alle berechnungsrelevanten Werte bereits als
    Snapshot (un_number, transport_category, points_factor, hazard_class),
    damit ein historisches Beförderungspapier auch nach einem Datenupdate
    reproduzierbar bleibt. Das Lösen der Referenz ist daher unbedenklich.
    """
    cursor.execute("UPDATE shipment_items SET un_db_id = NULL WHERE un_db_id IS NOT NULL")
    cursor.execute("DELETE FROM un_numbers")


def _seed_from_bam(bam_path: str) -> int:
    """Befüllt un_numbers aus der BAM-Datei (amtlich, dl-de/by-2-0)."""
    from bam_import import parse_bam_file, check_entries

    conn = get_db()
    cursor = conn.cursor()
    _clear_un_numbers(cursor)

    entries = parse_bam_file(bam_path, os.path.basename(bam_path))
    check = check_entries(entries)
    for problem in check.problems:
        print(f"[seed] WARNUNG: {problem}")

    now = datetime.now().isoformat()
    inserted = 0
    seen = set()
    duplicates = 0
    incomplete = 0

    for e in entries:
        un = e.un_number
        if not un:
            continue

        # Natürlicher Schlüssel: (UN-Nummer, Variante).
        # NICHT nur un_number — die Kategorie hängt an der Variante.
        key = (un, e.variant)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)

        tc = e.transport_category
        if tc is None:
            incomplete += 1

        # Höchstmenge je Beförderungseinheit (ADR 1.1.3.6.3)
        mq = MAX_QTY_BY_CATEGORY.get(tc) if tc is not None else None
        # Faktor: die BAM liefert ihn mit; fehlt er, aus der Kategorie ableiten.
        factor = e.multiplier
        if factor is None and tc is not None:
            factor = FACTOR_BY_CATEGORY.get(tc)

        # Fussnote a) zu 1.1.3.6.3: Höchstmenge 50 kg und Faktor 20 für
        # die UN-Nummern 0081, 0082, 0084, 0241, 0331, 0332, 0482, 1005, 1017.
        if un in FOOTNOTE_A_UN_NUMBERS:
            mq = FOOTNOTE_A_MAX_QTY
            factor = FOOTNOTE_A_FACTOR

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
            (un, e.variant, e.full_name_de[:200], e.spec_de,
             e.name_en, e.name_fr, e.hazard_class,
             e.classification_code, e.labels, e.packing_group,
             tc, e.tunnel_code, e.special_provisions,
             e.limited_quantity, e.excepted_quantity, e.packing_instructions,
             e.tank_code, e.vehicle_for_tank, e.hazard_identification_no,
             factor, e.multiplier, mq,
             e.prohibited, "BAM_DGG", 100, e.notes,
             "ADR 2025", now)
        )
        inserted += 1

    _ensure_unique_index(cursor)
    conn.commit()
    conn.close()

    if duplicates:
        print(f"[seed] {duplicates} doppelte (UN, Variante)-Paare übersprungen.")
    if incomplete:
        print(f"[seed] HINWEIS: {incomplete} Varianten ohne Beförderungskategorie "
              f"(z. B. nicht dem ADR unterliegende Stoffe oder "
              f"Beförderung verboten). Diese sind nicht freistellungsfähig "
              f"(Fail-Safe) - das ist korrekt so und kein Datenfehler.")
    print(f"[seed] {inserted} Varianten aus BAM-Datenbank GEFAHRGUT geladen.")
    return inserted


def _seed_from_json() -> int:
    """Notbehelf: altes JSON-Seed (aus dem PDF geparst, unvollständig)."""
    import json

    conn = get_db()
    cursor = conn.cursor()
    _clear_un_numbers(cursor)

    seed_path = os.path.join(DB_DIR, "adr_2025_seed.json")
    if not os.path.exists(seed_path):
        print(f"WARNING: weder BAM-Datei noch JSON-Seed gefunden: {seed_path}")
        conn.close()
        return 0

    print("[seed] WARNUNG: verwende altes JSON-Seed. Für korrekte Daten bitte "
          "die BAM-Datei unter data/bam/ ablegen oder im Import-Dialog "
          "hochladen (Quelle: tes.bam.de, Datenbank GEFAHRGUT).")

    with open(seed_path, "r", encoding="utf-8") as f:
        raw_entries = json.load(f)

    now = datetime.now().isoformat()
    inserted = 0
    seen = set()
    incomplete = 0

    for idx, e in enumerate(raw_entries, start=1):
        un = e.get("un_number", "").strip()
        if not un:
            continue
        key = (un, idx)
        if key in seen:
            continue
        seen.add(key)

        try:
            tc = int(e.get("transport_category"))
        except (TypeError, ValueError):
            tc = None
        if tc not in FACTOR_BY_CATEGORY:
            tc = None
            incomplete += 1

        mq = MAX_QTY_BY_CATEGORY.get(tc) if tc is not None else None
        factor = FACTOR_BY_CATEGORY.get(tc) if tc is not None else None
        if un in FOOTNOTE_A_UN_NUMBERS:
            mq = FOOTNOTE_A_MAX_QTY
            factor = FOOTNOTE_A_FACTOR

        hc = (e.get("hazard_class") or "").strip() or None
        cursor.execute(
            """INSERT INTO un_numbers
               (un_number, variant, substance_name_de, hazard_class,
                danger_label, packing_group, transport_category, tunnel_code,
                points_factor, max_quantity_per_transport, data_source,
                confidence, adr_version, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (un, idx, (e.get("substance_name_de") or "").strip()[:200], hc, hc,
             (e.get("packing_group") or "").strip() or None,
             tc, (e.get("tunnel_code") or "").strip() or None,
             factor, mq, "PDF_LEGACY", 60, "ADR 2025", now)
        )
        inserted += 1

    _ensure_unique_index(cursor)
    conn.commit()
    conn.close()
    return inserted


def _ensure_unique_index(cursor) -> None:
    """Erzwingt die Eindeutigkeit von (un_number, variant).

    Ohne diesen Index kann ein UPDATE auf un_number allein die Varianten
    einer UN-Nummer überschreiben. Altdaten werden vorher bereinigt.
    """
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_un_variant "
            "ON un_numbers(un_number, variant)"
        )
    except sqlite3.IntegrityError:
        # Altdaten mit doppelten Paaren: Duplikate entfernen
        cursor.execute("""
            DELETE FROM un_numbers WHERE id NOT IN (
                SELECT MIN(id) FROM un_numbers GROUP BY un_number, variant
            )
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_un_variant "
            "ON un_numbers(un_number, variant)"
        )



def seed_shipping_addresses() -> int:
    """Insert a default shipping address for testing."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM shipping_addresses")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """INSERT INTO shipping_addresses (name, street, zip, city, country, is_default)
               VALUES (?, ?, ?, ?, ?, 1)""",
            ("Musterfirma GmbH", "Industriestraße 42", "80331", "München", "Deutschland")
        )
        conn.commit()
        inserted = 1
    else:
        inserted = 0

    conn.close()
    return inserted


def seed_all() -> None:
    """Initialize DB, seed all lookup data, and add default address."""
    init_db()
    un_count = seed_un_numbers()
    addr_count = seed_shipping_addresses()
    print(f"Database initialized: {un_count} UN numbers seeded, "
          f"{addr_count} default address(es) created.")


# ── CLI entry point ──────────────────────────────────────────────────
if __name__ == "__main__":
    seed_all()

    # Verify
    conn = get_db()
    cat = conn.execute(
        "SELECT transport_category, COUNT(*) FROM un_numbers GROUP BY transport_category ORDER BY transport_category"
    ).fetchall()
    print("\nVerteilung der Transportkategorien:")
    for row in cat:
        cat_num, count = row[0], row[1]
        factor_map = {0: "0", 1: "50", 2: "3", 3: "1", 4: "0 (unbegrenzt)"}
        label = factor_map.get(cat_num, "-")
        name = "ohne Kategorie" if cat_num is None else f"Kategorie {cat_num}"
        print(f"  {name} (Faktor {label}): {count} Einträge")

    total = conn.execute("SELECT COUNT(*) FROM un_numbers").fetchone()[0]
    distinct = conn.execute("SELECT COUNT(DISTINCT un_number) FROM un_numbers").fetchone()[0]
    print(f"\nGesamt: {total} Varianten / {distinct} UN-Nummern in der Datenbank")

    src = conn.execute(
        "SELECT data_source, COUNT(*) FROM un_numbers GROUP BY data_source"
    ).fetchall()
    print("Datenquelle:", ", ".join(f"{r[0] or 'unbekannt'}={r[1]}" for r in src))

    addr = conn.execute("SELECT * FROM shipping_addresses").fetchall()
    print(f"\nVersandadressen ({len(addr)}):")
    for a in addr:
        print(f"  {a['name']}, {a['street']}, {a['zip']} {a['city']}")

    conn.close()
