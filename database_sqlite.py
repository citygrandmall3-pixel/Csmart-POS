# -*- coding: utf-8 -*-
"""قاعدة البيانات SQLite — نظام الحسابات المتكامل"""
import re
import shutil
import sqlite3

# يُصدَّر IntegrityError ليستخدمه باقي الكود عبر db.IntegrityError
# هذا يُسهّل التبديل بين SQLite وMySQL لاحقًا
IntegrityError = sqlite3.IntegrityError
import secrets
import sys
import json
from datetime import date, datetime
from pathlib import Path
from werkzeug.security import generate_password_hash

# في النسخة المجمّعة (EXE) البيانات تُحفظ بجوار الملف التنفيذي وليس داخل مجلد مؤقت
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = BASE_DIR / "accounting.db"
SECRET_PATH = INSTANCE_DIR / "secret.key"
BACKUP_DIR = INSTANCE_DIR / "backups"

# المناطق الافتراضية — تُنسخ لقاعدة البيانات أول تشغيل فقط
DEFAULT_REGIONS = [
    "المركز الرئيسي",
    "المنطقة المركزية",
    "المنطقة الشمالية",
    "المنطقة الشرقية",
    "المنطقة الجنوبية",
]

# يُحدّث ديناميكيًا عبر load_regions() بعد تهيئة القاعدة
REGIONS: list[str] = []

ACCOUNT_TYPES = ["أصول", "خصوم", "حقوق ملكية", "إيرادات", "مصروفات", "أخرى"]

STARTER_ACCOUNTS = [
    ("1101", "الصندوق", "أصول"),
    ("1102", "شام كاش", "أصول"),
    ("1103", "ماستر كارد", "أصول"),
    ("1201", "العملاء (المدينون)", "أصول"),
    ("1301", "المخزون", "أصول"),
    ("1401", "الأصول الثابتة", "أصول"),
    ("1402", "مجمع الإهلاك", "أصول"),
    ("2101", "الموردون (الدائنون)", "خصوم"),
    ("2102", "مصروفات مستحقة", "خصوم"),
    ("2201", "قروض طويلة الأجل", "خصوم"),
    ("3101", "رأس المال", "حقوق ملكية"),
    ("3102", "المسحوبات الشخصية", "حقوق ملكية"),
    ("3103", "الأرباح المحتجزة", "حقوق ملكية"),
    ("4101", "إيرادات المبيعات", "إيرادات"),
    ("4102", "إيرادات أخرى", "إيرادات"),
    ("5101", "رواتب وأجور", "مصروفات"),
    ("5102", "إيجارات", "مصروفات"),
    ("5103", "كهرباء ومياه", "مصروفات"),
    ("5104", "هاتف وإنترنت", "مصروفات"),
    ("5105", "صيانة", "مصروفات"),
    ("5106", "وقود ومواصلات", "مصروفات"),
    ("5107", "قرطاسية ومطبوعات", "مصروفات"),
    ("5108", "إعلانات ودعاية", "مصروفات"),
    ("5109", "ضيافة", "مصروفات"),
    ("5110", "مصروفات بنكية", "مصروفات"),
    ("5111", "إهلاك", "مصروفات"),
    ("5112", "ضرائب ورسوم", "مصروفات"),
    ("5113", "مصروفات متنوعة", "مصروفات"),
    ("9901", "حساب وسيط - سداد", "أخرى"),
    ("9902", "فروقات تسوية", "أخرى"),
]


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def query(sql, args=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def query_one(sql, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql, args=()):
    with connect() as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'accountant',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    acc_no TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT DEFAULT '',
    opening_balance REAL NOT NULL DEFAULT 0,
    budget REAL NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_no TEXT UNIQUE NOT NULL,
    entry_date TEXT NOT NULL,
    description TEXT DEFAULT '',
    region TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted')),
    entry_type TEXT DEFAULT 'عادي',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    partner_id INTEGER REFERENCES business_partners(id) ON DELETE RESTRICT,
    debit REAL NOT NULL DEFAULT 0,
    credit REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT,
    action TEXT,
    details TEXT,
    at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS regions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_lines_entry ON journal_lines(entry_id);
CREATE INDEX IF NOT EXISTS idx_lines_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_entries_date ON journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_entries_status ON journal_entries(status);

-- ===================== Accounting Core V2 =====================
-- الكيانات الأساسية التي تسمح بتوسعة النظام إلى ERP متعدد الشركات/الفروع
-- مع الحفاظ على التوافق مع قاعدة البيانات القديمة.
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    tax_no TEXT DEFAULT '',
    currency_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS activity_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    icon TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS company_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE CASCADE,
    activity_type_id INTEGER NOT NULL
        REFERENCES activity_types(id) ON DELETE RESTRICT,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, activity_type_id)
);

CREATE INDEX IF NOT EXISTS idx_company_activities_company
    ON company_activities(company_id);

CREATE INDEX IF NOT EXISTS idx_company_activities_activity
    ON company_activities(activity_type_id);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    region_id INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, code)
);

CREATE TABLE IF NOT EXISTS currencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT DEFAULT '',
    decimal_places INTEGER NOT NULL DEFAULT 2,
    is_base INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS fiscal_years (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, name)
);

CREATE TABLE IF NOT EXISTS accounting_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fiscal_year_id INTEGER NOT NULL REFERENCES fiscal_years(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(fiscal_year_id, name)
);

CREATE TABLE IF NOT EXISTS cost_centers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    parent_id INTEGER REFERENCES cost_centers(id) ON DELETE RESTRICT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, code)
);

CREATE TABLE IF NOT EXISTS document_sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id INTEGER REFERENCES branches(id) ON DELETE RESTRICT,
    document_type TEXT NOT NULL,
    prefix TEXT DEFAULT '',
    next_number INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id, branch_id, document_type)
);

CREATE INDEX IF NOT EXISTS idx_branches_company ON branches(company_id);
CREATE INDEX IF NOT EXISTS idx_fiscal_years_company ON fiscal_years(company_id);
CREATE INDEX IF NOT EXISTS idx_periods_year ON accounting_periods(fiscal_year_id);
CREATE INDEX IF NOT EXISTS idx_cost_centers_company ON cost_centers(company_id);
CREATE TABLE IF NOT EXISTS business_partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id INTEGER REFERENCES branches(id) ON DELETE RESTRICT,
    region_id INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    partner_type TEXT NOT NULL CHECK(partner_type IN ('customer','supplier','both')),
    receivable_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
    payable_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    address TEXT DEFAULT '',
    tax_no TEXT DEFAULT '',
    credit_limit REAL NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, code)
);

CREATE INDEX IF NOT EXISTS idx_partners_company
    ON business_partners(company_id);

CREATE INDEX IF NOT EXISTS idx_partners_type
    ON business_partners(partner_type);

CREATE INDEX IF NOT EXISTS idx_partners_receivable
    ON business_partners(receivable_account_id);

CREATE INDEX IF NOT EXISTS idx_partners_payable
    ON business_partners(payable_account_id);


CREATE INDEX IF NOT EXISTS idx_sequences_company ON document_sequences(company_id);

CREATE TABLE IF NOT EXISTS product_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,
    parent_id INTEGER
        REFERENCES product_categories(id) ON DELETE RESTRICT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, code)
);

CREATE INDEX IF NOT EXISTS idx_product_categories_company
    ON product_categories(company_id);

CREATE INDEX IF NOT EXISTS idx_product_categories_parent
    ON product_categories(parent_id);


CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT DEFAULT '',
    unit_type TEXT NOT NULL DEFAULT 'quantity',
    decimal_places INTEGER NOT NULL DEFAULT 2,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, code)
);

CREATE INDEX IF NOT EXISTS idx_units_company
    ON units(company_id);


CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    category_id INTEGER
        REFERENCES product_categories(id) ON DELETE RESTRICT,

    code TEXT NOT NULL,
    name TEXT NOT NULL,

    base_unit_id INTEGER NOT NULL
        REFERENCES units(id) ON DELETE RESTRICT,

    description TEXT DEFAULT '',
    brand TEXT DEFAULT '',
    model TEXT DEFAULT '',
    specifications TEXT DEFAULT '',

    is_stock_item INTEGER NOT NULL DEFAULT 1,
    allow_decimal_qty INTEGER NOT NULL DEFAULT 1,

    track_expiry INTEGER NOT NULL DEFAULT 0,
    track_lot INTEGER NOT NULL DEFAULT 0,
    track_serial INTEGER NOT NULL DEFAULT 0,

    min_stock REAL NOT NULL DEFAULT 0,
    max_stock REAL NOT NULL DEFAULT 0,

    is_active INTEGER NOT NULL DEFAULT 1,

    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),

    UNIQUE(company_id, code)
);

CREATE INDEX IF NOT EXISTS idx_products_company
    ON products(company_id);

CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category_id);

CREATE INDEX IF NOT EXISTS idx_products_unit
    ON products(base_unit_id);

CREATE INDEX IF NOT EXISTS idx_products_active
    ON products(is_active);


CREATE TABLE IF NOT EXISTS product_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE CASCADE,

    product_id INTEGER NOT NULL
        REFERENCES products(id) ON DELETE CASCADE,

    unit_id INTEGER NOT NULL
        REFERENCES units(id) ON DELETE RESTRICT,

    currency_id INTEGER NOT NULL
        REFERENCES currencies(id) ON DELETE RESTRICT,

    price_type TEXT NOT NULL
        CHECK(price_type IN ('sale', 'purchase')),

    price REAL NOT NULL DEFAULT 0,

    is_default INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,

    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),

    UNIQUE(
        company_id,
        product_id,
        unit_id,
        currency_id,
        price_type
    )
);

CREATE INDEX IF NOT EXISTS idx_product_prices_company
    ON product_prices(company_id);

CREATE INDEX IF NOT EXISTS idx_product_prices_product
    ON product_prices(product_id);

CREATE INDEX IF NOT EXISTS idx_product_prices_lookup
    ON product_prices(
        company_id,
        product_id,
        unit_id,
        currency_id,
        price_type,
        is_active
    );


CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    code TEXT NOT NULL,
    name TEXT NOT NULL,

    description TEXT DEFAULT '',
    address TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    manager_name TEXT DEFAULT '',

    is_active INTEGER NOT NULL DEFAULT 1,

    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),

    UNIQUE(company_id, code)
);

CREATE INDEX IF NOT EXISTS idx_warehouses_company
    ON warehouses(company_id);

CREATE INDEX IF NOT EXISTS idx_warehouses_active
    ON warehouses(is_active);



CREATE TABLE IF NOT EXISTS product_barcodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL
        REFERENCES products(id) ON DELETE CASCADE,
    unit_id INTEGER
        REFERENCES units(id) ON DELETE RESTRICT,
    barcode TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, barcode)
);

CREATE INDEX IF NOT EXISTS idx_product_barcodes_product
    ON product_barcodes(product_id);

CREATE INDEX IF NOT EXISTS idx_product_barcodes_barcode
    ON product_barcodes(company_id, barcode);

CREATE TABLE IF NOT EXISTS warehouse_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    warehouse_id INTEGER NOT NULL
        REFERENCES warehouses(id) ON DELETE RESTRICT,

    product_id INTEGER NOT NULL
        REFERENCES products(id) ON DELETE RESTRICT,

    quantity REAL NOT NULL DEFAULT 0,

    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),

    UNIQUE(company_id, warehouse_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_warehouse_stock_company
    ON warehouse_stock(company_id);

CREATE INDEX IF NOT EXISTS idx_warehouse_stock_warehouse
    ON warehouse_stock(warehouse_id);

CREATE INDEX IF NOT EXISTS idx_warehouse_stock_product
    ON warehouse_stock(product_id);


CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    warehouse_id INTEGER NOT NULL
        REFERENCES warehouses(id) ON DELETE RESTRICT,

    product_id INTEGER NOT NULL
        REFERENCES products(id) ON DELETE RESTRICT,

    unit_id INTEGER NOT NULL
        REFERENCES units(id) ON DELETE RESTRICT,

    movement_type TEXT NOT NULL,

    quantity REAL NOT NULL,

    reference_type TEXT DEFAULT '',

    reference_id INTEGER,

    movement_date TEXT NOT NULL
        DEFAULT (datetime('now','localtime')),

    notes TEXT DEFAULT '',

    created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,

    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_company
    ON stock_movements(company_id);

CREATE INDEX IF NOT EXISTS idx_stock_movements_warehouse
    ON stock_movements(warehouse_id);

CREATE INDEX IF NOT EXISTS idx_stock_movements_product
    ON stock_movements(product_id);

CREATE INDEX IF NOT EXISTS idx_stock_movements_date
    ON stock_movements(movement_date);

CREATE INDEX IF NOT EXISTS idx_stock_movements_type
    ON stock_movements(movement_type);

CREATE TABLE IF NOT EXISTS purchase_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    branch_id INTEGER
        REFERENCES branches(id) ON DELETE RESTRICT,

    supplier_id INTEGER NOT NULL
        REFERENCES business_partners(id) ON DELETE RESTRICT,

    warehouse_id INTEGER
        REFERENCES warehouses(id) ON DELETE RESTRICT,

    fiscal_year_id INTEGER
        REFERENCES fiscal_years(id) ON DELETE RESTRICT,

    period_id INTEGER
        REFERENCES accounting_periods(id) ON DELETE RESTRICT,

    currency_id INTEGER NOT NULL
        REFERENCES currencies(id) ON DELETE RESTRICT,

    exchange_rate REAL NOT NULL DEFAULT 1,

    invoice_no TEXT NOT NULL,

    supplier_invoice_no TEXT DEFAULT '',

    invoice_date TEXT NOT NULL,

    payment_method TEXT NOT NULL DEFAULT 'credit',

    status TEXT NOT NULL DEFAULT 'draft',

    subtotal REAL NOT NULL DEFAULT 0,

    discount_total REAL NOT NULL DEFAULT 0,

    tax_total REAL NOT NULL DEFAULT 0,

    grand_total REAL NOT NULL DEFAULT 0,

    paid_amount REAL NOT NULL DEFAULT 0,

    remaining_amount REAL NOT NULL DEFAULT 0,

    notes TEXT DEFAULT '',

    journal_entry_id INTEGER
        REFERENCES journal_entries(id) ON DELETE SET NULL,

    created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,

    approved_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,

    created_at TEXT DEFAULT (datetime('now','localtime')),

    updated_at TEXT DEFAULT (datetime('now','localtime')),

    UNIQUE(company_id, invoice_no)
);

CREATE INDEX IF NOT EXISTS idx_purchase_invoices_company
    ON purchase_invoices(company_id);

CREATE INDEX IF NOT EXISTS idx_purchase_invoices_supplier
    ON purchase_invoices(supplier_id);

CREATE INDEX IF NOT EXISTS idx_purchase_invoices_warehouse
    ON purchase_invoices(warehouse_id);

CREATE INDEX IF NOT EXISTS idx_purchase_invoices_date
    ON purchase_invoices(invoice_date);

CREATE INDEX IF NOT EXISTS idx_purchase_invoices_status
    ON purchase_invoices(status);


CREATE TABLE IF NOT EXISTS purchase_invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    invoice_id INTEGER NOT NULL
        REFERENCES purchase_invoices(id) ON DELETE CASCADE,

    product_id INTEGER NOT NULL
        REFERENCES products(id) ON DELETE RESTRICT,

    unit_id INTEGER NOT NULL
        REFERENCES units(id) ON DELETE RESTRICT,

    quantity REAL NOT NULL,

    unit_price REAL NOT NULL DEFAULT 0,

    discount REAL NOT NULL DEFAULT 0,

    tax_rate REAL NOT NULL DEFAULT 0,

    tax_amount REAL NOT NULL DEFAULT 0,

    line_total REAL NOT NULL DEFAULT 0,

    notes TEXT DEFAULT '',

    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_purchase_invoice_items_invoice
    ON purchase_invoice_items(invoice_id);

CREATE INDEX IF NOT EXISTS idx_purchase_invoice_items_product
    ON purchase_invoice_items(product_id);

CREATE INDEX IF NOT EXISTS idx_purchase_invoice_items_unit
    ON purchase_invoice_items(unit_id);

CREATE TABLE IF NOT EXISTS sales_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,

    branch_id INTEGER
        REFERENCES branches(id) ON DELETE RESTRICT,

    customer_id INTEGER
        REFERENCES business_partners(id) ON DELETE RESTRICT,

    warehouse_id INTEGER
        REFERENCES warehouses(id) ON DELETE RESTRICT,

    fiscal_year_id INTEGER
        REFERENCES fiscal_years(id) ON DELETE RESTRICT,

    period_id INTEGER
        REFERENCES accounting_periods(id) ON DELETE RESTRICT,

    currency_id INTEGER NOT NULL
        REFERENCES currencies(id) ON DELETE RESTRICT,

    exchange_rate REAL NOT NULL DEFAULT 1,

    invoice_no TEXT NOT NULL,

    customer_invoice_no TEXT DEFAULT '',

    invoice_date TEXT NOT NULL,

    payment_method TEXT NOT NULL DEFAULT 'cash',

    status TEXT NOT NULL DEFAULT 'draft',

    subtotal REAL NOT NULL DEFAULT 0,

    discount_total REAL NOT NULL DEFAULT 0,

    tax_total REAL NOT NULL DEFAULT 0,

    grand_total REAL NOT NULL DEFAULT 0,

    paid_amount REAL NOT NULL DEFAULT 0,

    remaining_amount REAL NOT NULL DEFAULT 0,

    notes TEXT DEFAULT '',

    journal_entry_id INTEGER
        REFERENCES journal_entries(id) ON DELETE SET NULL,

    created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,

    approved_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,

    created_at TEXT DEFAULT (datetime('now','localtime')),

    updated_at TEXT DEFAULT (datetime('now','localtime')),

    UNIQUE(company_id, invoice_no)
);

CREATE INDEX IF NOT EXISTS idx_sales_invoices_company
    ON sales_invoices(company_id);

CREATE INDEX IF NOT EXISTS idx_sales_invoices_customer
    ON sales_invoices(customer_id);

CREATE INDEX IF NOT EXISTS idx_sales_invoices_warehouse
    ON sales_invoices(warehouse_id);

CREATE INDEX IF NOT EXISTS idx_sales_invoices_date
    ON sales_invoices(invoice_date);

CREATE INDEX IF NOT EXISTS idx_sales_invoices_status
    ON sales_invoices(status);


CREATE TABLE IF NOT EXISTS sales_invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    invoice_id INTEGER NOT NULL
        REFERENCES sales_invoices(id) ON DELETE CASCADE,

    product_id INTEGER NOT NULL
        REFERENCES products(id) ON DELETE RESTRICT,

    unit_id INTEGER NOT NULL
        REFERENCES units(id) ON DELETE RESTRICT,

    quantity REAL NOT NULL,

    unit_price REAL NOT NULL DEFAULT 0,

    discount REAL NOT NULL DEFAULT 0,

    tax_rate REAL NOT NULL DEFAULT 0,

    tax_amount REAL NOT NULL DEFAULT 0,

    line_total REAL NOT NULL DEFAULT 0,

    notes TEXT DEFAULT '',

    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sales_invoice_items_invoice
    ON sales_invoice_items(invoice_id);

CREATE INDEX IF NOT EXISTS idx_sales_invoice_items_product
    ON sales_invoice_items(product_id);

CREATE INDEX IF NOT EXISTS idx_sales_invoice_items_unit
    ON sales_invoice_items(unit_id);




CREATE TABLE IF NOT EXISTS cashier_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE RESTRICT,
    opened_at TEXT NOT NULL DEFAULT (datetime(\x27now\x27,\x27localtime\x27)),
    closed_at TEXT,
    opening_cash REAL NOT NULL DEFAULT 0,
    expected_cash REAL NOT NULL DEFAULT 0,
    actual_cash REAL,
    difference REAL,
    total_sales REAL NOT NULL DEFAULT 0,
    total_returns REAL NOT NULL DEFAULT 0,
    invoice_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT \x27open\x27,
    notes TEXT DEFAULT \x27\x27
);

CREATE INDEX IF NOT EXISTS idx_cashier_sessions_company
    ON cashier_sessions(company_id);
CREATE INDEX IF NOT EXISTS idx_cashier_sessions_user
    ON cashier_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_cashier_sessions_status
    ON cashier_sessions(status);




CREATE TABLE IF NOT EXISTS cashiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL
        REFERENCES companies(id) ON DELETE RESTRICT,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT DEFAULT '',
    permissions_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, username)
);

CREATE INDEX IF NOT EXISTS idx_cashiers_company
    ON cashiers(company_id);
CREATE INDEX IF NOT EXISTS idx_cashiers_active
    ON cashiers(company_id, is_active);


CREATE TABLE IF NOT EXISTS backup_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    email_enabled INTEGER NOT NULL DEFAULT 1,
    email_to TEXT NOT NULL DEFAULT "",
    email_on_session_close INTEGER NOT NULL DEFAULT 1,
    email_daily INTEGER NOT NULL DEFAULT 1,
    last_email_at TEXT,
    local_enabled INTEGER NOT NULL DEFAULT 1,
    local_path TEXT NOT NULL DEFAULT "",
    local_daily INTEGER NOT NULL DEFAULT 1,
    local_keep INTEGER NOT NULL DEFAULT 7,
    last_local_at TEXT,
    backup_type TEXT NOT NULL DEFAULT "full",
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id)
);

CREATE INDEX IF NOT EXISTS idx_backup_settings_company
    ON backup_settings(company_id);
"""


def load_secret():
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key)
    return key


def init_db():
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    # الزراعة تتم مرة واحدة فقط عند إنشاء ملف القاعدة من الصفر.
    # أي مسح/حذف لاحق (قيود/حسابات/مستخدمين/مناطق/إعدادات) يبقى نهائيًا
    # لأن وجود الملف نفسه يمنع إعادة الزرع مهما طُمست البيانات.
    brand_new = not DB_PATH.exists()
    conn = connect()
    conn.executescript(SCHEMA)

    # Purchase return tables: safe migration for both new and existing databases.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS purchase_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            purchase_id INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE RESTRICT,
            supplier_id INTEGER NOT NULL REFERENCES business_partners(id) ON DELETE RESTRICT,
            warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
            return_no TEXT NOT NULL,
            return_date TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            net_amount REAL NOT NULL DEFAULT 0,
            tax_amount REAL NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL DEFAULT 0,
            currency_id INTEGER NOT NULL REFERENCES currencies(id) ON DELETE RESTRICT,
            exchange_rate REAL NOT NULL DEFAULT 1,
            journal_entry_id INTEGER REFERENCES journal_entries(id) ON DELETE RESTRICT,
            request_id TEXT NOT NULL,
            request_payload_json TEXT NOT NULL,
            response_json TEXT NOT NULL DEFAULT '{}',
            created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(company_id, return_no),
            UNIQUE(company_id, created_by, request_id)
        );
        CREATE TABLE IF NOT EXISTS purchase_return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER NOT NULL REFERENCES purchase_returns(id) ON DELETE CASCADE,
            purchase_item_id INTEGER NOT NULL REFERENCES purchase_invoice_items(id) ON DELETE RESTRICT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            quantity REAL NOT NULL CHECK(quantity > 0),
            unit_price REAL NOT NULL DEFAULT 0,
            discount_amount REAL NOT NULL DEFAULT 0,
            net_amount REAL NOT NULL DEFAULT 0,
            tax_rate REAL NOT NULL DEFAULT 0,
            tax_amount REAL NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(return_id, purchase_item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_purchase_returns_purchase ON purchase_returns(purchase_id);
        CREATE INDEX IF NOT EXISTS idx_purchase_returns_supplier ON purchase_returns(supplier_id);
        CREATE INDEX IF NOT EXISTS idx_purchase_return_items_purchase_item ON purchase_return_items(purchase_item_id);
    """)


    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_preferences (
            company_id INTEGER PRIMARY KEY
                REFERENCES companies(id) ON DELETE CASCADE,
            warehouse_mode TEXT NOT NULL DEFAULT 'single'
                CHECK (warehouse_mode IN ('single', 'multiple')),
            default_warehouse_id INTEGER
                REFERENCES warehouses(id) ON DELETE SET NULL,
            enabled_modules_json TEXT NOT NULL DEFAULT '[]',
            activity_codes_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)


    # ---------------- Accounting Core V2 migrations ----------------
    # نضيف الأعمدة الجديدة دون كسر قواعد البيانات الموجودة.
    acc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    for col, definition in [
        ("parent_id", "INTEGER REFERENCES accounts(id) ON DELETE RESTRICT"),
        ("normal_balance", "TEXT NOT NULL DEFAULT ''"),
        ("is_group", "INTEGER NOT NULL DEFAULT 0"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ("company_id", "INTEGER REFERENCES companies(id) ON DELETE RESTRICT"),
    ]:
        if col not in acc_cols:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {definition}")

    # Step 4.1 — العملاء والموردون
    jl_cols = {r["name"] for r in conn.execute("PRAGMA table_info(journal_lines)")}
    if "partner_id" not in jl_cols:
        conn.execute(
            "ALTER TABLE journal_lines ADD COLUMN partner_id "
            "INTEGER REFERENCES business_partners(id) ON DELETE RESTRICT"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lines_partner ON journal_lines(partner_id)")

    je_cols = {r["name"] for r in conn.execute("PRAGMA table_info(journal_entries)")}
    for col, definition in [
        ("company_id", "INTEGER REFERENCES companies(id) ON DELETE RESTRICT"),
        ("branch_id", "INTEGER REFERENCES branches(id) ON DELETE RESTRICT"),
        ("fiscal_year_id", "INTEGER REFERENCES fiscal_years(id) ON DELETE RESTRICT"),
        ("period_id", "INTEGER REFERENCES accounting_periods(id) ON DELETE RESTRICT"),
        ("cost_center_id", "INTEGER REFERENCES cost_centers(id) ON DELETE RESTRICT"),
        ("currency_id", "INTEGER REFERENCES currencies(id) ON DELETE RESTRICT"),
        ("exchange_rate", "REAL NOT NULL DEFAULT 1"),
    ]:
        if col not in je_cols:
            conn.execute(f"ALTER TABLE journal_entries ADD COLUMN {col} {definition}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_parent ON accounts(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_company ON accounts(company_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_company ON journal_entries(company_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_branch ON journal_entries(branch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_fiscal_year ON journal_entries(fiscal_year_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_period ON journal_entries(period_id)")

    # ====================================================
    # A1 — ربط الفواتير بجلسة الكاشير
    # ====================================================
    si_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sales_invoices)")}
    for col, definition in [
        ("session_id", "INTEGER REFERENCES cashier_sessions(id) ON DELETE SET NULL"),
        ("is_held", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col not in si_cols:
            conn.execute(f"ALTER TABLE sales_invoices ADD COLUMN {col} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_invoices_session ON sales_invoices(session_id)")


    # A5 — ربط الجلسة بكاشير محدد
    cs_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cashier_sessions)")}
    for col, definition in [
        ("cashier_id", "INTEGER REFERENCES cashiers(id) ON DELETE SET NULL"),
    ]:
        if col not in cs_cols:
            conn.execute(f"ALTER TABLE cashier_sessions ADD COLUMN {col} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cashier_sessions_cashier ON cashier_sessions(cashier_id)")


    # =========================================================
    # العملة الحالية للشركة
    #
    # المصدر الوحيد للحالة الحالية هو companies.currency_id.
    # is_base مجرد قيمة متزامنة للتوافق مع الأجزاء القديمة من النظام.
    # لا نسمح لبدء تشغيل البرنامج بتغيير العملة التي اختارها المسؤول.
    # =========================================================

    company = conn.execute(
        "SELECT id, currency_id FROM companies ORDER BY id LIMIT 1"
    ).fetchone()

    if not company:
        # عند إنشاء الشركة لأول مرة فقط نبحث عن عملة أساسية،
        # ثم USD كافتراضي أخير.
        base_currency = conn.execute("""
            SELECT id
            FROM currencies
            WHERE is_base=1 AND is_active=1
            ORDER BY id
            LIMIT 1
        """).fetchone()

        if not base_currency:
            base_currency = conn.execute(
                "SELECT id FROM currencies WHERE code='USD' AND is_active=1 LIMIT 1"
            ).fetchone()

        if not base_currency:
            conn.execute(
                "INSERT INTO currencies(code,name,symbol,is_base,is_active) "
                "VALUES('USD','الدولار الأمريكي','$',1,1)"
            )
            base_currency = conn.execute(
                "SELECT id FROM currencies WHERE code='USD' LIMIT 1"
            ).fetchone()

        currency_id = base_currency[0]

        conn.execute(
            "UPDATE currencies SET is_base=0"
        )
        conn.execute(
            "UPDATE currencies SET is_base=1 WHERE id=?",
            (currency_id,)
        )

        conn.execute(
            "INSERT INTO companies(code,name,currency_id) "
            "VALUES('MAIN','الشركة الرئيسية',?)",
            (currency_id,)
        )

        company = conn.execute(
            "SELECT id, currency_id FROM companies ORDER BY id LIMIT 1"
        ).fetchone()

    else:
        # الشركة موجودة: عملتها المحفوظة هي الحقيقة.
        company_currency = None

        if company["currency_id"] is not None:
            company_currency = conn.execute(
                """SELECT id
                   FROM currencies
                   WHERE id=? AND is_active=1
                   LIMIT 1""",
                (company["currency_id"],)
            ).fetchone()

        if company_currency:
            currency_id = company_currency[0]
        else:
            # معالجة قاعدة قديمة لم تكن الشركة فيها مرتبطة بعملة صحيحة.
            base_currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE is_base=1 AND is_active=1
                ORDER BY id
                LIMIT 1
            """).fetchone()

            if not base_currency:
                base_currency = conn.execute(
                    "SELECT id FROM currencies WHERE code='USD' AND is_active=1 LIMIT 1"
                ).fetchone()

            if not base_currency:
                conn.execute(
                    "INSERT INTO currencies(code,name,symbol,is_base,is_active) "
                    "VALUES('USD','الدولار الأمريكي','$',1,1)"
                )
                base_currency = conn.execute(
                    "SELECT id FROM currencies WHERE code='USD' LIMIT 1"
                ).fetchone()

            currency_id = base_currency[0]

            conn.execute(
                "UPDATE companies SET currency_id=? WHERE id=?",
                (currency_id, company["id"])
            )

            company = conn.execute(
                "SELECT id, currency_id FROM companies WHERE id=?",
                (company["id"],)
            ).fetchone()

        # مزامنة is_base مع عملة الشركة دون تغيير عملة الشركة.
        conn.execute(
            "UPDATE currencies SET is_base=CASE WHEN id=? THEN 1 ELSE 0 END",
            (currency_id,)
        )

    # الشركة الموجودة تحتفظ بعملة المسؤول.
    company_id = company["id"]

    branch = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()
    if not branch:
        region = conn.execute("SELECT id FROM regions ORDER BY id LIMIT 1").fetchone()
        conn.execute("INSERT INTO branches(company_id,code,name,region_id) VALUES(?,?,?,?)",
                     (company_id, 'MAIN', 'الفرع الرئيسي', region[0] if region else None))
        branch = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()
    branch_id = branch[0]

    fy = conn.execute("SELECT id FROM fiscal_years WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()
    year = datetime_now().year
    if not fy:
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        conn.execute("INSERT INTO fiscal_years(company_id,name,date_from,date_to) VALUES(?,?,?,?)",
                     (company_id, str(year), start, end))
        fy = conn.execute("SELECT id FROM fiscal_years WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()
    fiscal_year_id = fy[0]

    # 12 فترة شهرية للسنة المالية الحالية.
    import calendar
    existing_periods = conn.execute("SELECT COUNT(*) FROM accounting_periods WHERE fiscal_year_id=?", (fiscal_year_id,)).fetchone()[0]
    if existing_periods == 0:
        fyrow = conn.execute("SELECT date_from,date_to FROM fiscal_years WHERE id=?", (fiscal_year_id,)).fetchone()
        fy_year = int(str(fyrow[0])[:4])
        for month in range(1, 13):
            last = calendar.monthrange(fy_year, month)[1]
            conn.execute("INSERT INTO accounting_periods(fiscal_year_id,name,date_from,date_to) VALUES(?,?,?,?)",
                         (fiscal_year_id, f"{fy_year}-{month:02d}", f"{fy_year}-{month:02d}-01", f"{fy_year}-{month:02d}-{last:02d}"))

    # مراكز تكلفة افتراضية: عامة فقط، ويمكن بناء الشجرة لاحقًا.
    cc = conn.execute("SELECT id FROM cost_centers WHERE company_id=? AND code='GENERAL'", (company_id,)).fetchone()
    if not cc:
        conn.execute("INSERT INTO cost_centers(company_id,code,name) VALUES(?,?,?)",
                     (company_id, 'GENERAL', 'عام'))

    # ربط الحسابات والقيود القديمة بالسياق الافتراضي.
    conn.execute("UPDATE accounts SET company_id=? WHERE company_id IS NULL", (company_id,))
    conn.execute("UPDATE journal_entries SET company_id=?, branch_id=?, fiscal_year_id=? WHERE company_id IS NULL",
                 (company_id, branch_id, fiscal_year_id))
    conn.execute("UPDATE journal_entries SET currency_id=? WHERE currency_id IS NULL", (currency_id,))
    conn.execute("UPDATE journal_entries SET exchange_rate=1 WHERE exchange_rate IS NULL OR exchange_rate<=0")

    # ربط القيود القديمة بالفترة المحاسبية المناسبة حسب تاريخ القيد.
    conn.execute("""
        UPDATE journal_entries
           SET period_id = (
               SELECT ap.id FROM accounting_periods ap
               WHERE ap.fiscal_year_id = journal_entries.fiscal_year_id
                 AND journal_entries.entry_date BETWEEN ap.date_from AND ap.date_to
               ORDER BY ap.id LIMIT 1
           )
         WHERE period_id IS NULL
    """)

    # تعيين طبيعة الحساب تلقائيًا للحسابات الحالية.
    conn.execute("""UPDATE accounts SET normal_balance = CASE
        WHEN type IN ('أصول','مصروفات') THEN 'debit'
        WHEN type IN ('خصوم','حقوق ملكية','إيرادات') THEN 'credit'
        ELSE '' END
        WHERE normal_balance='' OR normal_balance IS NULL""")

    conn.commit()

    # ترقية قواعد البيانات القديمة: إضافة عمود نوع القيد
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(journal_entries)")}
    if "entry_type" not in existing_cols:
        conn.execute("ALTER TABLE journal_entries ADD COLUMN entry_type TEXT DEFAULT 'عادي'")
        conn.commit()

    # ترقية: إضافة عمود الميزانية التقديرية للحسابات
    acc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    if "budget" not in acc_cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN budget REAL NOT NULL DEFAULT 0")
        conn.commit()

    # إذا كانت القاعدة جديدة، نزرع جميع الافتراضيات.
    if brand_new:
        # المستخدمون الافتراضيون
        default_users = [
            ("admin", "admin123", "مدير النظام", "admin"),
            ("ahmed", "ahmed123", "محاسب / أحمد عبدالله", "accountant"),
            ("viewer", "viewer123", "مستخدم مشاهدة", "viewer"),
        ]
        for username, pwd, full_name, role in default_users:
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
                (username, generate_password_hash(pwd), full_name, role),
            )

        # دليل حسابات ابتدائي (رقم، اسم، نوع، ميزانية تقديرية)
        BUDGET_ACCOUNTS = [
            ("5101", "رواتب وأجور", "مصروفات", 480000),
            ("5102", "إيجارات", "مصروفات", 120000),
            ("5103", "كهرباء ومياه", "مصروفات", 36000),
            ("5104", "هاتف وإنترنت", "مصروفات", 18000),
            ("5105", "صيانة", "مصروفات", 24000),
            ("5106", "وقود ومواصلات", "مصروفات", 30000),
            ("5107", "قرطاسية ومطبوعات", "مصروفات", 12000),
            ("5108", "إعلانات ودعاية", "مصروفات", 15000),
            ("5109", "ضيافة", "مصروفات", 10000),
            ("5110", "صيانة سيرفر وشبكات", "مصروفات", 20000),
            ("5201", "تأمينات", "مصروفات", 25000),
            ("5202", "استشارات قانونية", "مصروفات", 8000),
            ("5301", "اشتراكات برامج", "مصروفات", 15000),
            ("5401", "مهمات سفر", "مصروفات", 20000),
        ]
        budget_map = {a[0]: a[3] for a in BUDGET_ACCOUNTS}
        for acc_no, name, acc_type in STARTER_ACCOUNTS:
            budget = budget_map.get(acc_no, 0)
            conn.execute(
                "INSERT OR IGNORE INTO accounts (acc_no, name, type, budget) VALUES (?,?,?,?)",
                (acc_no, name, acc_type, budget),
            )

        # الحسابات المزروعة حديثًا تُربط بالشركة الافتراضية وطبيعة الرصيد.
        conn.execute("UPDATE accounts SET company_id=? WHERE company_id IS NULL", (company_id,))
        conn.execute("""UPDATE accounts SET normal_balance = CASE
            WHEN type IN ('أصول','مصروفات') THEN 'debit'
            WHEN type IN ('خصوم','حقوق ملكية','إيرادات') THEN 'credit'
            ELSE '' END
            WHERE normal_balance='' OR normal_balance IS NULL""")

        # الإعدادات الافتراضية
        year = datetime_now().year
        defaults = {
            "company_name": "نظام محاسبي متكامل",
            "period_from": f"{year}-01-01",
            "period_to": f"{year}-12-31",
            "signatures": json.dumps([
                {"title": "مدير إدارة الميزانية", "name": "محاسب / احمد عبدالله"},
                {"title": "مدير الإدارة العامة المالية", "name": "محاسب / محمد احمد سيد"},
            ], ensure_ascii=False),
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))

        # المناطق الافتراضية
        for region_name in DEFAULT_REGIONS:
            conn.execute("INSERT OR IGNORE INTO regions (name) VALUES (?)", (region_name,))

    # إصلاح القواعد القديمة التي تحتوي جدول حسابات فارغًا.
    # لا نعيد زرع المستخدمين أو المناطق أو الإعدادات حتى لا نكرر بيانات موجودة.
    accounts_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    if accounts_count == 0:
        BUDGET_ACCOUNTS = [
            ("5101", "رواتب وأجور", "مصروفات", 480000),
            ("5102", "إيجارات", "مصروفات", 120000),
            ("5103", "كهرباء ومياه", "مصروفات", 36000),
            ("5104", "هاتف وإنترنت", "مصروفات", 18000),
            ("5105", "صيانة", "مصروفات", 24000),
            ("5106", "وقود ومواصلات", "مصروفات", 30000),
            ("5107", "قرطاسية ومطبوعات", "مصروفات", 12000),
            ("5108", "إعلانات ودعاية", "مصروفات", 15000),
            ("5109", "ضيافة", "مصروفات", 10000),
            ("5110", "صيانة سيرفر وشبكات", "مصروفات", 20000),
            ("5201", "تأمينات", "مصروفات", 25000),
            ("5202", "استشارات قانونية", "مصروفات", 8000),
            ("5301", "اشتراكات برامج", "مصروفات", 15000),
            ("5401", "مهمات سفر", "مصروفات", 20000),
        ]
        budget_map = {a[0]: a[3] for a in BUDGET_ACCOUNTS}

        for acc_no, name, acc_type in STARTER_ACCOUNTS:
            budget = budget_map.get(acc_no, 0)
            normal_balance = (
                "debit" if acc_type in ("أصول", "مصروفات")
                else "credit" if acc_type in ("خصوم", "حقوق ملكية", "إيرادات")
                else ""
            )
            conn.execute(
                """INSERT OR IGNORE INTO accounts
                   (acc_no, name, type, budget, company_id, normal_balance)
                   VALUES (?,?,?,?,?,?)""",                (acc_no, name, acc_type, budget, company_id, normal_balance),
            )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sale_delivery_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL UNIQUE,
            company_id INTEGER NOT NULL,
            rep TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            fees REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (sale_id) REFERENCES sales_invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)
    # Delivery workflow migration
    delivery_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sale_delivery_details)")}
    for col, definition in [("driver_id", "INTEGER"), ("region_id", "INTEGER"), ("region_name", "TEXT NOT NULL DEFAULT ''"), ("delivery_status", "TEXT NOT NULL DEFAULT 'pending'"), ("collection_status", "TEXT NOT NULL DEFAULT 'pending'"), ("receipt_entry_id", "INTEGER"), ("delivered_at", "TEXT"), ("collected_at", "TEXT"), ("collection_method", "TEXT NOT NULL DEFAULT ''")]:
        if col not in delivery_cols:
            conn.execute(f"ALTER TABLE sale_delivery_details ADD COLUMN {col} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sale_delivery_receipt ON sale_delivery_details(receipt_entry_id) WHERE receipt_entry_id IS NOT NULL")

    conn.commit()
    conn.close()
    load_regions()


def datetime_now():
    from datetime import datetime
    return datetime.now()


def get_setting(key, default=""):
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    execute(
        "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


# ------------------------------------------------------------------
# المناطق
# ------------------------------------------------------------------

def load_regions():
    """يحمّل المناطق النشطة من قاعدة البيانات ويحدّث المتغير العام REGIONS"""
    global REGIONS
    try:
        REGIONS = [r["name"] for r in query("SELECT name FROM regions WHERE is_active=1 ORDER BY id")]
    except Exception:
        REGIONS = list(DEFAULT_REGIONS)
    return REGIONS


def get_all_regions():
    """يرجع جميع المناطق مع عدد القيود المرتبطة بها (للإعدادات)"""
    rows = query("""
        SELECT r.id, r.name, r.is_active, r.created_at,
               (SELECT COUNT(*) FROM journal_entries WHERE region=r.name) AS usage_count
        FROM regions r ORDER BY r.id
    """)
    return rows


def add_region(name):
    name = (name or "").strip()
    if not name or len(name) > 120:
        return None, "اسم المنطقة مطلوب (حد أقصى 120 حرف)"
    try:
        rid = execute("INSERT INTO regions (name) VALUES (?)", (name,))
        load_regions()
        return rid, None
    except Exception:
        return None, f"المنطقة «{name}» موجودة بالفعل"


def rename_region(rid, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        return "اسم المنطقة مطلوب"
    existing = query_one("SELECT name FROM regions WHERE id=?", (rid,))
    if not existing:
        return "المنطقة غير موجودة"
    old_name = existing["name"]
    try:
        execute("UPDATE regions SET name=? WHERE id=?", (new_name, rid))
    except Exception:
        return f"المنطقة «{new_name}» موجودة بالفعل"
    if old_name != new_name:
        execute("UPDATE journal_entries SET region=? WHERE region=?", (new_name, old_name))
    load_regions()
    return None


def deactivate_region(rid):
    r = query_one("SELECT name FROM regions WHERE id=?", (rid,))
    if not r:
        return "المنطقة غير موجودة"
    execute("UPDATE regions SET is_active=0 WHERE id=?", (rid,))
    load_regions()
    return None


def activate_region(rid):
    r = query_one("SELECT name FROM regions WHERE id=?", (rid,))
    if not r:
        return "المنطقة غير موجودة"
    execute("UPDATE regions SET is_active=1 WHERE id=?", (rid,))
    load_regions()
    return None


def delete_region(rid):
    """حذف نهائي فقط لو لا قيود مرتبطة"""
    r = query_one(
        "SELECT name FROM regions WHERE id=?", (rid,))
    if not r:
        return "المنطقة غير موجودة"
    cnt = query_one(
        "SELECT COUNT(*) AS c FROM journal_entries WHERE region=?", (r["name"],))["c"]
    if cnt:
        return f"لا يمكن الحذف — في {cnt} قيد مرتبط بهذه المنطقة. عطّلها بدلاً من الحذف"
    execute("DELETE FROM regions WHERE id=?", (rid,))
    load_regions()
    return None


def audit(user, action, details=""):
    execute("INSERT INTO audit_log (user, action, details) VALUES (?,?,?)", (user, action, details))


# ------------------------------------------------------------------
# النسخ الاحتياطي
# ------------------------------------------------------------------


_last_auto_check = None


def create_backup(prefix="manual"):
    if not re.fullmatch(r"(?:manual|pre-restore|auto-\d{4}-\d{2}-\d{2})", prefix):
        raise ValueError("بادئة النسخة غير صالحة")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{prefix}-{datetime_now():%Y-%m-%d_%H%M%S_%f}.db"
    source = connect()
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("النسخة الاحتياطية غير سليمة")
    except Exception:
        target.close()
        source.close()
        dest.unlink(missing_ok=True)
        raise
    target.close()
    source.close()
    return dest.name


def auto_monthly_backup():
    global _last_auto_check
    today = date.today()
    if _last_auto_check == today:
        return
    _last_auto_check = today
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    marker = today.strftime("%Y-%m")
    if not any(p.name.startswith(f"auto-{marker}") for p in BACKUP_DIR.glob("auto-*.db")):
        create_backup(prefix=f"auto-{today.isoformat()}")
    automatic = sorted(BACKUP_DIR.glob("auto-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in automatic[24:]:
        try:
            old.unlink()
        except OSError:
            pass


SAFE_BACKUP_NAME = re.compile(r"^(?:auto|manual|pre-restore)-[A-Za-z0-9_.-]+\.db$")


def _check_backup_integrity(path):
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError(f"النسخة تالفة: {result}")
    finally:
        conn.close()


def _apply_backup(path):
    source = sqlite3.connect(str(path))
    target = connect()
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise sqlite3.DatabaseError(f"فشل فحص القاعدة بعد الاستعادة: {result}")
    finally:
        target.close()
        source.close()


def restore_backup(name):
    if not isinstance(name, str) or not SAFE_BACKUP_NAME.fullmatch(name):
        raise ValueError("اسم النسخة غير صالح")
    src = BACKUP_DIR / name
    if not src.is_file():
        raise FileNotFoundError("النسخة غير موجودة")
    _check_backup_integrity(src)
    safe_name = create_backup(prefix="pre-restore")
    safe_path = BACKUP_DIR / safe_name
    try:
        _apply_backup(src)
    except Exception as error:
        try:
            _apply_backup(safe_path)
        except Exception as recovery_error:
            raise RuntimeError(f"فشلت الاستعادة والرجوع لنسخة الأمان: {recovery_error}") from error
        raise RuntimeError(f"فشلت الاستعادة؛ أُعيدت نسخة الأمان {safe_name}") from error
    return safe_name


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(BACKUP_DIR.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        if SAFE_BACKUP_NAME.fullmatch(p.name) and p.is_file():
            out.append({
                "name": p.name,
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return out

if __name__ == "__main__":
    init_db()
    print("تم إنشاء قاعدة البيانات بنجاح")
