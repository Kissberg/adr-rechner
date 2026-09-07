"""
ADR 1000-Punkte-Rechner — Database Module
Creates and manages the SQLite database for dangerous goods transport calculations
per ADR 1.1.3.6 (1000-Punkte-Regel).

Transport Categories (ADR 1.1.3.6, Abschnitt 1.1.3.6.3, Tabelle):
  Category 0: factor 0    — Class 7 (excepted), certain specific UN numbers
  Category 1: factor 50   — Class 1 (1.1, 1.2, 1.5), toxic gases (T, TC, TO, TFC, TOC),
                             desensitized explosives, organic peroxides Type B
  Category 2: factor 3    — Flammable gases (group F), PG I substances (except Cat 1),
                             Class 6.1 PG I (inhalation), Class 8 PG I, lithium batteries
  Category 3: factor 1    — PG II/III of Classes 3, 4.1, 4.2, 4.3, 5.1, 6.1, 8,
                             Class 9 (most), non-toxic/non-flammable gases (A, O groups)
  Category 4: unlimited   — Class 1.4S, certain Class 9, empty uncleaned packagings
"""

import sqlite3
import os
import secrets
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "adr.db")

# Maximale Wartezeit bei Schreibkonflikten (ms). Erhöht, weil mehrere
# Gunicorn-Worker gleichzeitig auf dieselbe SQLite-Datei zugreifen.
BUSY_TIMEOUT_MS = 15000


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
    Loads data from the JSON seed file (extracted from official ADR 2025 PDF, Band I).
    Returns the number of UN numbers inserted.
    """
    import json

    conn = get_db()
    cursor = conn.cursor()

    # Delete existing data before seeding
    cursor.execute("DELETE FROM un_numbers")

    # ── Transport Category → Points Factor mapping ──
    FACTOR = {0: 0, 1: 50, 2: 3, 3: 1, 4: None}  # 4 = unlimited, no 1000-point limit
    # ── max quantity per transport unit (ADR 1.1.3.6.3) ──
    MAX_QTY = {0: 0, 1: 20, 2: 333, 3: 1000, 4: None}

    # Load seed data from JSON
    seed_path = os.path.join(DB_DIR, "adr_2025_seed.json")
    if not os.path.exists(seed_path):
        print(f"WARNING: ADR seed file not found: {seed_path}")
        conn.close()
        return 0

    with open(seed_path, "r", encoding="utf-8") as f:
        raw_entries = json.load(f)

    now = datetime.now().isoformat()
    inserted = 0
    seen = set()
    duplicates = 0
    incomplete = 0

    for e in raw_entries:
        un = e.get("un_number", "").strip()
        if not un:
            continue

        name_de = (e.get("substance_name_de") or "").strip()[:200]
        hc = (e.get("hazard_class") or "").strip() or None
        pg = (e.get("packing_group") or "").strip() or None
        tunnel = (e.get("tunnel_code") or "").strip() or None

        # ── Beförderungskategorie: KEIN gefährlicher Standardwert ──
        # Fehlt die Kategorie, wird NULL gespeichert. Die Regelengine
        # (adr_rules.py) behandelt NULL als "nicht freistellungsfähig"
        # (Fail-Safe), anstatt stillschweigend Kategorie 3 anzunehmen.
        try:
            tc = int(e.get("transport_category"))
        except (TypeError, ValueError):
            tc = None
        if tc not in FACTOR:
            tc = None
        if tc is None:
            incomplete += 1

        # ── Duplikate entfernen (gleiche UN + Klasse + VG + Kategorie) ──
        key = (un, hc, pg, tc, tunnel)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)

        mq = MAX_QTY.get(tc) if tc is not None else None
        points_factor = FACTOR.get(tc) if tc is not None else None

        cursor.execute(
            """INSERT INTO un_numbers
               (un_number, substance_name_de, substance_name_en, hazard_class,
                danger_label, packing_group, transport_category, tunnel_code,
                special_provisions, points_factor, max_quantity_per_transport,
                adr_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ADR 2025', ?)""",
            (un, name_de, "", hc, hc,
             pg, tc, tunnel, None,
             points_factor, mq, now)
        )
        inserted += 1

    conn.commit()
    conn.close()

    if duplicates:
        print(f"[seed] {duplicates} doppelte Einträge übersprungen.")
    if incomplete:
        print(f"[seed] WARNUNG: {incomplete} Einträge ohne gültige "
              f"Beförderungskategorie — diese sind nicht freistellungsfähig. "
              f"Bitte mit der amtlichen ADR-Tabelle A abgleichen.")

    return inserted



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
        factor_map = {0: "0", 1: "50", 2: "3", 3: "1", 4: "unbegrenzt"}
        print(f"  Kategorie {cat_num} (Faktor {factor_map[cat_num]}): {count} Einträge")

    total = conn.execute("SELECT COUNT(*) FROM un_numbers").fetchone()[0]
    print(f"\nGesamt: {total} UN-Nummern in der Datenbank")

    addr = conn.execute("SELECT * FROM shipping_addresses").fetchall()
    print(f"\nVersandadressen ({len(addr)}):")
    for a in addr:
        print(f"  {a['name']}, {a['street']}, {a['zip']} {a['city']}")

    conn.close()
