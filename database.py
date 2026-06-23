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
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "adr.db")


def get_db() -> sqlite3.Connection:
    """Return a SQLite connection with row factory and WAL mode enabled."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Create all tables if they do not exist."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        -- UN numbers database (Table A of ADR Chapter 3.2)
        CREATE TABLE IF NOT EXISTS un_numbers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            un_number       VARCHAR(10) UNIQUE NOT NULL,
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

        -- Index for fast UN number lookups
        CREATE INDEX IF NOT EXISTS idx_un_number ON un_numbers(un_number);
        CREATE INDEX IF NOT EXISTS idx_shipment_id ON shipment_items(shipment_id);
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

    conn.commit()
    conn.close()


def seed_un_numbers() -> int:
    """
    Populate the un_numbers table with common dangerous goods.
    Returns the number of UN numbers inserted.
    All data based on ADR 2025 Table A and ADR 1.1.3.6 transport categories.
    """
    conn = get_db()
    cursor = conn.cursor()

    # Delete existing data before seeding
    cursor.execute("DELETE FROM un_numbers")

    # ── Transport Category → Points Factor mapping ──
    FACTOR = {0: 0, 1: 50, 2: 3, 3: 1, 4: None}  # 4 = unlimited, no 1000-point limit

    # Each entry: (un_number, name_de, name_en, hazard_class, packing_group,
    #              transport_category, tunnel_code, special_provisions, max_qty)
    # max_quantity_per_transport in kg or L (ADR 1.1.3.6.3 table upper limits)
    un_data = [
        # ═══ Category 1 (factor 50) ════════════════════════════════════
        # Toxic & corrosive gases (T, TC, TO, TFC, TOC)
        ("1005", "Ammoniak, wasserfrei", "Ammonia, anhydrous",
         "2.3", None, 1, "(C/D)", "23, 653", 50),
        ("1017", "Chlor", "Chlorine",
         "2.3", None, 1, "(C/D)", "23, 653", 50),
        ("1040", "Ethylenoxid mit Stickstoff", "Ethylene oxide with nitrogen",
         "2.3", None, 1, "(C/D)", "653", 50),
        ("1067", "Stickstoffdioxid", "Dinitrogen tetroxide / Nitrogen dioxide",
         "2.3", None, 1, "(C/D)", "23", 50),
        ("1076", "Phosgen", "Phosgene",
         "2.3", None, 1, "(C/D)", "23, 653", 50),

        # Desensitized explosives (Class 4.1 with special provisions)
        ("1310", "Ammoniumpikrat, angefeuchtet", "Ammonium picrate, wetted",
         "4.1", "I", 1, "(B)", "23, 653", 20),
        ("1320", "Dinitrophenol, angefeuchtet", "Dinitrophenol, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("1321", "Dinitrophenolate, angefeuchtet", "Dinitrophenolates, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("1322", "Dinitroresorcin, angefeuchtet", "Dinitroresorcinol, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("1344", "Trinitrophenol (Pikrinsäure), angefeuchtet", "Trinitrophenol, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("1517", "Zirkoniumpikramat, angefeuchtet", "Zirconium picramate, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("1571", "Bariumazid, angefeuchtet", "Barium azide, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("2555", "Nitrocellulose mit Wasser (min. 25%)", "Nitrocellulose with water",
         "4.1", "II", 1, "(B)", "23", 20),
        ("2556", "Nitrocellulose mit Alkohol (min. 25%)", "Nitrocellulose with alcohol",
         "4.1", "II", 1, "(B)", "23", 20),
        ("2557", "Nitrocellulose-Gemisch (min. 25%)", "Nitrocellulose mixture",
         "4.1", "II", 1, "(B)", "23", 20),
        ("2852", "Dipicrylsulfid, angefeuchtet", "Dipicryl sulphide, wetted",
         "4.1", "I", 1, "(B)", "23", 20),
        ("3317", "2-Amino-4,6-dinitrophenol, angefeuchtet", "2-Amino-4,6-dinitrophenol, wetted",
         "4.1", "I", 1, "(B)", "23", 20),

        # Organic peroxides Type B
        ("3101", "Organisches Peroxid Typ B, flüssig", "Organic peroxide type B, liquid",
         "5.2", None, 1, "(B)", "122, 653", 20),
        ("3102", "Organisches Peroxid Typ B, fest", "Organic peroxide type B, solid",
         "5.2", None, 1, "(B)", "122, 653", 20),
        ("3111", "Organisches Peroxid Typ B, flüssig, temperaturgeregelt",
         "Organic peroxide type B, liquid, temperature controlled",
         "5.2", None, 1, "(B)", "122, 653", 20),
        ("3112", "Organisches Peroxid Typ B, fest, temperaturgeregelt",
         "Organic peroxide type B, solid, temperature controlled",
         "5.2", None, 1, "(B)", "122, 653", 20),

        # ═══ Category 2 (factor 3) ══════════════════════════════════════
        # Flammable gases
        ("1001", "Acetylen, gelöst", "Acetylene, dissolved",
         "2.1", None, 2, "(B/D)", "653", 333),
        ("1010", "Butadiene, stabilisiert", "Butadienes, stabilized",
         "2.1", None, 2, "(B/D)", "653", 333),
        ("1011", "Butan", "Butane",
         "2.1", None, 2, "(B/D)", "653", 333),
        ("1020", "Chlorpentafluorethan (R115)", "Chloropentafluoroethane",
         "2.2", None, 3, "(C/E)", "653", 1000),  # Non-flammable, non-toxic
        # UN 1046 Helium is Class 2.2 (non-flammable, non-toxic) — Cat 3
        # Listed once below in Category 3 section

        # PG I flammable liquids
        ("1093", "Acrylnitril, stabilisiert", "Acrylonitrile, stabilized",
         "3", "I", 2, "(C/D)", "653, 802", 333),
        ("1993", "Entzündbarer flüssiger Stoff, n.a.g. (VG I)",
         "Flammable liquid, n.o.s. (PG I)",
         "3", "I", 2, "(C/D)", "274, 653", 333),

        # Class 4.2 PG I  (pyrophoric / self-heating)
        ("1363", "Kopra", "Copra",
         "4.2", "III", 3, "(C/E)", "653", None),  # PG III → Cat 3

        # Class 5.1 PG I
        ("1479", "Oxidierender fester Stoff, n.a.g.", "Oxidizing solid, n.o.s.",
         "5.1", "I", 2, "(B)", "274, 653", 50),
        ("1486", "Kaliumnitrat", "Potassium nitrate",
         "5.1", "III", 3, "(E)", None, None),

        # Class 6.1 PG I (toxic by inhalation)
        ("1092", "Acrolein, stabilisiert", "Acrolein, stabilized",
         "6.1", "I", 2, "(C/D)", "653, 802", 50),
        ("1098", "Allylalkohol", "Allyl alcohol",
         "6.1", "I", 2, "(C/D)", "653, 802", 50),
        ("1143", "Crotonaldehyd, stabilisiert", "Crotonaldehyde, stabilized",
         "6.1", "I", 2, "(C/D)", "653, 802", 50),
        ("1185", "Ethylenimin, stabilisiert", "Ethyleneimine, stabilized",
         "6.1", "I", 2, "(C/D)", "653, 802", 50),
        ("1238", "Methylchlorformiat", "Methyl chloroformate",
         "6.1", "I", 2, "(C/D)", "653", 50),
        ("1541", "Acetoncyanhydrin, stabilisiert", "Acetone cyanohydrin, stabilized",
         "6.1", "I", 2, "(C/D)", "653, 802", 50),

        # Class 8 PG I
        ("1789", "Salzsäure (VG I)", "Hydrochloric acid (PG I)",
         "8", "I", 2, "(C/D)", "653", 50),
        ("1796", "Nitriersäure, Gemisch (VG I)", "Nitrating acid, mixture (PG I)",
         "8", "I", 2, "(C/D)", "653", 50),
        ("1826", "Nitriersäure, verbraucht (VG I)", "Nitrating acid, spent (PG I)",
         "8", "I", 2, "(C/D)", "653", 50),
        ("2031", "Salpetersäure (ausgenommen rotrauchend) über 70%",
         "Nitric acid, other than red fuming, >70%",
         "8", "I", 2, "(C/D)", "653", 50),

        # Lithium batteries
        ("3480", "Lithium-Ionen-Batterien", "Lithium ion batteries",
         "9", "II", 2, "(E)", "188, 230, 310, 376, 377, 636", 333),
        ("3481", "Lithium-Ionen-Batterien in Ausrüstung",
         "Lithium ion batteries contained in equipment",
         "9", "II", 2, "(E)", "188, 230, 310, 376, 377, 636", 333),
        ("3090", "Lithium-Metall-Batterien", "Lithium metal batteries",
         "9", "II", 2, "(E)", "188, 230, 310, 376, 377, 636", 333),
        ("3091", "Lithium-Metall-Batterien in Ausrüstung",
         "Lithium metal batteries contained in equipment",
         "9", "II", 2, "(E)", "188, 230, 310, 376, 377, 636", 333),
        ("3490", "Lithium-Ionen-Batterien (beschädigt/defekt)",
         "Lithium ion batteries (damaged/defective)",
         "9", "II", 2, "(E)", "376, 377", 333),
        ("3491", "Natrium-Ionen-Batterien",
         "Sodium ion batteries",
         "9", "II", 2, "(E)", "188, 230, 310, 376, 377, 636", 333),

        # ═══ Category 3 (factor 1) — Workhorse category ═══════════════
        # Non-flammable, non-toxic gases (group A, O)
        ("1002", "Luft, verdichtet", "Air, compressed",
         "2.2", None, 3, "(E)", "653", 1000),
        ("1006", "Argon, verdichtet", "Argon, compressed",
         "2.2", None, 3, "(E)", "653", 1000),
        ("1013", "Kohlendioxid", "Carbon dioxide",
         "2.2", None, 3, "(C/E)", "653", 1000),
        ("1046", "Helium, verdichtet (Verdrängungsgas)",
         "Helium, compressed",
         "2.2", None, 3, "(E)", "653", 1000),
        ("1056", "Krypton, verdichtet", "Krypton, compressed",
         "2.2", None, 3, "(E)", "653", 1000),
        ("1066", "Stickstoff, verdichtet", "Nitrogen, compressed",
         "2.2", None, 3, "(E)", "653", 1000),
        ("1072", "Sauerstoff, verdichtet", "Oxygen, compressed",
         "2.2", None, 3, "(E)", "653", 1000),  # Oxidizing gas → Cat 3
        ("1950", "Druckgaspackungen (Aerosole), nicht entzündbar",
         "Aerosols, non-flammable",
         "2.2", None, 3, "(E)", "190, 327, 344, 625", 1000),
        ("1977", "Stickstoff, tiefgekühlt, flüssig", "Nitrogen, refrigerated liquid",
         "2.2", None, 3, "(E)", "653", 1000),
        ("2187", "Kohlendioxid, tiefgekühlt, flüssig", "Carbon dioxide, refrigerated liquid",
         "2.2", None, 3, "(C/E)", "653", 1000),
        ("3159", "1,1,1,2-Tetrafluorethan (R134a)", "1,1,1,2-Tetrafluoroethane",
         "2.2", None, 3, "(C/E)", "653", 1000),

        # Aerosols — non-flammable (only flammable aerosols in group A → Cat 2)
        ("1950", "Druckgaspackungen, entzündbar", "Aerosols, flammable",
         "2.1", None, 3, "(B/D)", "190, 327, 344, 625", 333),

        # Flammable liquids PG II
        ("1090", "Aceton", "Acetone",
         "3", "II", 3, "(D/E)", None, 333),
        ("1114", "Benzol", "Benzene",
         "3", "II", 3, "(D/E)", "653", 333),
        ("1120", "Butanole", "Butanols",
         "3", "II", 3, "(D/E)", None, 333),
        ("1133", "Klebstoffe, entzündbare Flüssigkeit enthaltend",
         "Adhesives, containing flammable liquid",
         "3", "II", 3, "(D/E)", "640D", 333),
        ("1169", "Extrakte, aromatisch, flüssig", "Extracts, aromatic, liquid",
         "3", "II", 3, "(D/E)", "653", 333),
        ("1170", "Ethanol (Ethylalkohol)", "Ethanol (Ethyl alcohol)",
         "3", "II", 3, "(D/E)", None, 333),
        ("1202", "Dieselkraftstoff", "Diesel fuel",
         "3", "III", 3, "(D/E)", "640K", 1000),
        ("1203", "Benzin (Ottokraftstoff)", "Motor spirit / Gasoline",
         "3", "II", 3, "(D/E)", "653", 333),
        ("1219", "Isopropanol (Isopropylalkohol)", "Isopropanol (Isopropyl alcohol)",
         "3", "II", 3, "(D/E)", None, 333),
        ("1230", "Methanol", "Methanol",
         "3", "II", 3, "(D/E)", "653", 333),
        ("1263", "Farbe (einschließlich Farbverdünnung)", "Paint (including paint thinner)",
         "3", "II", 3, "(D/E)", "163, 640D", 333),
        ("1267", "Erdöl, roh", "Petroleum crude oil",
         "3", "II", 3, "(D/E)", "653", 333),
        ("1268", "Erdöldestillate, n.a.g.", "Petroleum distillates, n.o.s.",
         "3", "II", 3, "(D/E)", "640D", 333),
        ("1294", "Toluol", "Toluene",
         "3", "II", 3, "(D/E)", None, 333),
        ("1863", "Turbinenkraftstoff (Kerosin)", "Fuel, aviation, turbine engine",
         "3", "II", 3, "(D/E)", "640K", 333),
        ("1993", "Entzündbarer flüssiger Stoff, n.a.g. (VG II/III)",
         "Flammable liquid, n.o.s. (PG II/III)",
         "3", "II", 3, "(D/E)", "274, 640D", 333),

        # PG III flammable liquids
        ("1202", "Dieselkraftstoff (Heizöl)", "Diesel fuel / Heating oil",
         "3", "III", 3, "(D/E)", "640K", 1000),
        ("1223", "Petroleum (Kerosin)", "Kerosene",
         "3", "III", 3, "(D/E)", "640K", 1000),
        ("1999", "Teere, flüssig", "Tars, liquid",
         "3", "III", 3, "(D/E)", "653", 1000),

        # Flammable solids (Class 4.1)
        ("1325", "Entzündbarer fester Stoff, n.a.g.", "Flammable solid, organic, n.o.s.",
         "4.1", "II", 3, "(D/E)", "274", 333),
        ("1334", "Naphthalin, roh", "Naphthalene, crude",
         "4.1", "III", 3, "(D/E)", "653", 1000),
        ("1941", "Dibromdifluormethan", "Dibromodifluoromethane",
         "9", "III", 3, "(E)", "653", None),
        ("3175", "Feste Stoffe, die entzündbare flüssige Stoffe enthalten",
         "Solids containing flammable liquid, n.o.s.",
         "4.1", "II", 3, "(D/E)", "216, 274", 333),

        # Pyrophoric / self-heating (Class 4.2) PG II/III
        ("1361", "Kohle, tierischen oder pflanzlichen Ursprungs", "Carbon, animal or vegetable origin",
         "4.2", "II", 3, "(D/E)", "653", 333),
        ("1362", "Kohle, aktiviert", "Carbon, activated",
         "4.2", "III", 3, "(E)", "653", None),
        ("1364", "Baumwollabfälle, ölig", "Cotton waste, oily",
         "4.2", "III", 3, "(E)", None, None),
        ("1376", "Eisenoxid, verbraucht", "Iron oxide, spent",
         "4.2", "III", 3, "(E)", "653", None),
        ("1386", "Ölkuchen", "Seed cake",
         "4.2", "III", 3, "(E)", "653", None),
        ("3088", "Selbstzersetzlicher fester Stoff, n.a.g.",
         "Self-reactive solid, n.o.s.",
         "4.2", "II", 3, "(D/E)", "274", 333),

        # Dangerous when wet (Class 4.3) PG II/III
        ("1400", "Barium", "Barium",
         "4.3", "II", 3, "(D/E)", "653", 333),
        ("1428", "Natrium", "Sodium",
         "4.3", "I", 2, "(D/E)", "653", 50),  # PG I → Cat 2
        ("2257", "Kalium", "Potassium",
         "4.3", "I", 2, "(D/E)", "653", 50),  # PG I → Cat 2
        ("3170", "Aluminiumschmelze", "Aluminium smelting by-products",
         "4.3", "II", 3, "(D/E)", "244", 333),

        # Oxidizers (Class 5.1) PG II/III
        ("1479", "Oxidierender fester Stoff, n.a.g. (VG II)", "Oxidizing solid, n.o.s. (PG II)",
         "5.1", "II", 3, "(D/E)", "274", 333),
        ("1490", "Kaliumpermanganat", "Potassium permanganate",
         "5.1", "II", 3, "(D/E)", "653", 333),
        ("1495", "Natriumchlorat", "Sodium chlorate",
         "5.1", "II", 3, "(D/E)", "653", 333),
        ("1505", "Natriumpersulfat", "Sodium persulphate",
         "5.1", "III", 3, "(E)", "653", None),
        ("1942", "Ammoniumnitrat (weniger gefährlich)", "Ammonium nitrate (less hazardous)",
         "5.1", "III", 3, "(E)", "653", None),
        ("2067", "Ammoniumnitrat-Düngemittel", "Ammonium nitrate based fertilizer",
         "5.1", "III", 3, "(D/E)", "186, 653", 1000),
        ("2468", "Trichlorisocyanursäure, trocken", "Trichloroisocyanuric acid, dry",
         "5.1", "II", 3, "(D/E)", None, 333),
        ("3212", "Hypochlorite, anorganisch, n.a.g.", "Hypochlorites, inorganic, n.o.s.",
         "5.1", "II", 3, "(D/E)", "274", 333),

        # Toxic substances (Class 6.1) PG II/III
        ("1544", "Alkaloide, fest, n.a.g.", "Alkaloids, solid, n.o.s.",
         "6.1", "II", 3, "(D/E)", "274, 802", 333),
        ("1547", "Anilin", "Aniline",
         "6.1", "II", 3, "(D/E)", "802", 333),
        ("1888", "Chloroform", "Chloroform",
         "6.1", "III", 3, "(E)", None, None),
        ("2020", "Chlorphenole, fest", "Chlorophenols, solid",
         "6.1", "III", 3, "(E)", "653", None),
        ("2076", "Kresole, flüssig", "Cresols, liquid",
         "6.1", "II", 3, "(D/E)", None, 333),
        ("2811", "Giftiger fester organischer Stoff, n.a.g.",
         "Toxic solid, organic, n.o.s.",
         "6.1", "II", 3, "(D/E)", "274, 802", 333),

        # Corrosive substances (Class 8) PG II/III
        ("1719", "Ätzender alkalischer flüssiger Stoff, n.a.g.",
         "Caustic alkali liquid, n.o.s.",
         "8", "II", 3, "(D/E)", "274", 333),
        ("1789", "Salzsäure (VG II/III)", "Hydrochloric acid (PG II/III)",
         "8", "II", 3, "(D/E)", None, 333),
        ("1805", "Phosphorsäure, flüssig", "Phosphoric acid, solution",
         "8", "III", 3, "(E)", None, None),
        ("1823", "Natriumhydroxid, fest (Ätznatron)", "Sodium hydroxide, solid",
         "8", "II", 3, "(D/E)", None, 333),
        ("1824", "Natronlauge (Natriumhydroxid-Lösung)", "Sodium hydroxide solution",
         "8", "II", 3, "(D/E)", None, 333),
        ("1830", "Schwefelsäure, über 51%", "Sulphuric acid, >51%",
         "8", "II", 3, "(D/E)", None, 333),
        ("2031", "Salpetersäure (außer rotrauchend) bis 70%",
         "Nitric acid, other than red fuming, ≤70%",
         "8", "II", 3, "(D/E)", None, 333),
        ("2491", "Ethanolamin", "Ethanolamine",
         "8", "III", 3, "(E)", None, None),
        ("2582", "Eisen(III)-chlorid, Lösung", "Ferric chloride, solution",
         "8", "III", 3, "(E)", None, None),
        ("2586", "Alkylsulfonsäuren, flüssig", "Alkylsulphonic acids, liquid",
         "8", "III", 3, "(E)", "653", None),
        ("2794", "Batterien, nass, mit Säure gefüllt",
         "Batteries, wet, filled with acid",
         "8", None, 3, "(E)", "295", None),
        ("2795", "Batterien, nass, mit Alkali gefüllt",
         "Batteries, wet, filled with alkali",
         "8", None, 3, "(E)", "295", None),
        ("3264", "Ätzender saurer anorganischer flüssiger Stoff, n.a.g.",
         "Corrosive liquid, acidic, inorganic, n.o.s.",
         "8", "II", 3, "(D/E)", "274", 333),
        ("3265", "Ätzender saurer organischer flüssiger Stoff, n.a.g.",
         "Corrosive liquid, acidic, organic, n.o.s.",
         "8", "II", 3, "(D/E)", "274", 333),
        ("3266", "Ätzender alkalischer anorganischer flüssiger Stoff, n.a.g.",
         "Corrosive liquid, basic, inorganic, n.o.s.",
         "8", "II", 3, "(D/E)", "274", 333),

        # Miscellaneous (Class 9)
        ("1845", "Kohlendioxid, fest (Trockeneis)", "Carbon dioxide, solid (Dry ice)",
         "9", "III", 3, "(E)", "653", 200),
        ("2212", "Asbest, blau (Krokydolith)", "Asbestos, blue (crocidolite)",
         "9", "II", 3, "(E)", "168, 653", 333),
        ("2590", "Asbest, weiß (Chrysotil)", "Asbestos, white (chrysotile)",
         "9", "III", 3, "(E)", "168, 653", None),
        ("2807", "Magnetisierte Stoffe", "Magnetized material",
         "9", "III", 3, "(E)", None, None),
        ("3077", "Umweltgefährdender Stoff, fest, n.a.g.",
         "Environmentally hazardous substance, solid, n.o.s.",
         "9", "III", 3, "(E)", "274, 375, 653", None),
        ("3082", "Umweltgefährdender Stoff, flüssig, n.a.g.",
         "Environmentally hazardous substance, liquid, n.o.s.",
         "9", "III", 3, "(E)", "274, 375, 653", None),
        ("3166", "Fahrzeug mit Verbrennungsmotor", "Vehicle, internal combustion engine",
         "9", None, 3, "(E)", "388", None),
        ("3171", "Batteriebetriebenes Fahrzeug", "Battery-powered vehicle",
         "9", None, 3, "(E)", "388", None),
        ("3245", "Gentechnisch veränderte Mikroorganismen",
         "Genetically modified micro-organisms",
         "9", None, 3, "(E)", "219, 653", None),
        ("3268", "Airbag-Gasgeneratoren", "Air bag inflators",
         "9", "III", 3, "(E)", "280, 289, 653", None),
        ("3363", "Gefährliche Güter in Maschinen oder Geräten",
         "Dangerous goods in machinery or apparatus",
         "9", None, 3, "(E)", "301, 653", None),

        # ═══ Category 4 (unlimited — no 1000-point limit) ═══════════
        ("1327", "Heu, Stroh oder Bhusa", "Hay, straw or bhusa",
         "4.1", None, 4, "(E)", None, None),
        ("2796", "Batteriesäure (Schwefelsäure, bis 51%)",
         "Battery fluid, acid (sulphuric acid ≤51%)",
         "8", "II", 4, "(E)", None, None),
        ("2800", "Batterien, nass, auslaufsicher",
         "Batteries, wet, non-spillable",
         "8", None, 4, "(E)", "238, 295", None),
        ("3166", "Verbrennungsmotor (innerhalb Maschine)",
         "Engine, internal combustion",
         "9", None, 4, "(E)", "388", None),
        ("3334", "Flüssiger Stoff, für die Luftfahrt geregelt, n.a.g.",
         "Aviation regulated liquid, n.o.s.",
         "9", None, 4, "(E)", "653", None),
        ("3335", "Fester Stoff, für die Luftfahrt geregelt, n.a.g.",
         "Aviation regulated solid, n.o.s.",
         "9", None, 4, "(E)", "653", None),
        ("3508", "Kondensator, asymmetrisch (EDLC ≤20 Wh)",
         "Capacitor, asymmetric (EDLC ≤20 Wh)",
         "9", None, 4, "(E)", "372, 653", None),
        ("3509", "Verpackungen, entsorgt, leer, ungereinigt",
         "Packagings, discarded, empty, uncleaned",
         "9", None, 4, "(E)", "653", None),

        # ═══ Category 0 (factor 0) — exempt ════════════════════════════
        # Class 7 excepted packages
        ("2908", "Radioaktiver Stoff, freigestelltes Versandstück — leere Verpackung",
         "Radioactive material, excepted package — empty packaging",
         "7", None, 0, "(E)", "290, 369", 0),
        ("2909", "Radioaktiver Stoff, freigestelltes Versandstück — hergestellte Gegenstände",
         "Radioactive material, excepted package — manufactured articles",
         "7", None, 0, "(E)", "290, 369", 0),
        ("2910", "Radioaktiver Stoff, freigestelltes Versandstück — begrenzte Stoffmenge",
         "Radioactive material, excepted package — limited quantity of material",
         "7", None, 0, "(E)", "290, 369", 0),
        ("2911", "Radioaktiver Stoff, freigestelltes Versandstück — Instrumente/Gegenstände",
         "Radioactive material, excepted package — instruments/articles",
         "7", None, 0, "(E)", "290, 369", 0),

        # Radioactive, LSA / SCO
        ("2912", "Radioaktiver Stoff, geringe spezifische Aktivität (LSA-I)",
         "Radioactive material, low specific activity (LSA-I)",
         "7", None, 0, "(E)", "172, 325, 326, 369", 0),
        ("2913", "Radioaktiver Stoff, oberflächenkontaminierter Gegenstand (SCO-I/II)",
         "Radioactive material, surface contaminated objects (SCO-I/II)",
         "7", None, 0, "(E)", "172, 325, 326, 369", 0),
        ("2915", "Radioaktiver Stoff, Versandstück Typ A",
         "Radioactive material, Type A package",
         "7", None, 0, "(E)", "172, 325, 326, 369", 0),
    ]

    # Deduplicate — keep only first occurrence per UN number
    seen = set()
    deduped = []
    for row in un_data:
        key = row[0]  # un_number is unique per ADR Table A
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    now = datetime.now().isoformat()

    inserted = 0
    for row in deduped:
        un_number, name_de, name_en, hazard_class, packing_group, \
            transport_category, tunnel_code, special_provisions, max_qty = row
        danger_label = hazard_class  # danger label number (hazard diamond label)
        points_factor = FACTOR[transport_category]

        cursor.execute(
            """INSERT INTO un_numbers
               (un_number, substance_name_de, substance_name_en, hazard_class,
                danger_label, packing_group, transport_category, tunnel_code,
                special_provisions, points_factor, max_quantity_per_transport,
                adr_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ADR 2025', ?)""",
            (un_number, name_de, name_en, hazard_class, danger_label,
             packing_group, transport_category, tunnel_code, special_provisions,
             points_factor, max_qty, now)
        )
        inserted += 1

    conn.commit()
    conn.close()
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
