# -*- coding: utf-8 -*-
"""نظام الحسابات المتكامل — محاسب / أحمد عبدالله"""
from datetime import date, datetime, timedelta
from functools import wraps
import collections
import math
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from dotenv import load_dotenv
load_dotenv()
from email_backup import send_backup_email
from pathlib import Path
import re
import sys
import time

from flask import (Flask, abort, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
import excel_tools as xl

ROLES = {"admin": "مدير النظام", "accountant": "محاسب", "cashier": "كاشير", "viewer": "مشاهدة فقط"}
ENTRY_TYPES = ["عادي", "تسوية", "افتتاحي", "إقفال"]

# في النسخة المجمّعة (EXE) القوالب والملفات الثابتة تُفك داخل مجلد مؤقت _MEIPASS
if getattr(sys, "frozen", False):
    _RES_DIR = sys._MEIPASS
else:
    _RES_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
            template_folder=os.path.join(_RES_DIR, "templates"),
            static_folder=os.path.join(_RES_DIR, "static"))
app.secret_key = db.load_secret()
app.permanent_session_lifetime = timedelta(hours=12)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10MB upload limit
)

# ------------------------------------------------------------------
# الحماية والأمان
# ------------------------------------------------------------------

# Rate limiting بسيط
_login_attempts = collections.defaultdict(lambda: collections.deque())
LOGIN_MAX = 5
LOGIN_WINDOW = 300  # 5 دقائق


def _check_rate_limit(ip):
    now = time.time()
    attempts = _login_attempts[ip]
    while attempts and attempts[0] < now - LOGIN_WINDOW:
        attempts.popleft()
    return len(attempts) < LOGIN_MAX


def _record_login_attempt(ip):
    _login_attempts[ip].append(time.time())


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'"
    )
    return response


def _sanitize(value):
    """إزالة محتوى ضار من المدخلات النصية"""
    if not isinstance(value, str):
        return value
    value = value.strip()
    value = re.sub(r'<script[^>]*>.*?</script>', '', value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r'on\w+\s*=', '', value, flags=re.IGNORECASE)
    return value


def _sanitize_dict(d):
    if not isinstance(d, dict):
        return d
    return {k: _sanitize(v) if isinstance(v, str) else v for k, v in d.items()}


# ------------------------------------------------------------------
# المصادقة والصلاحيات
# ------------------------------------------------------------------

@app.before_request
def before():
    allowed = {"login", "static"}
    if request.endpoint in allowed:
        return None
    uid = session.get("uid")
    g.user = db.query_one("SELECT * FROM users WHERE id=?", (uid,)) if uid else None
    if g.user is None:
        if request.path.startswith("/api/"):
            return jsonify(error="انتهت الجلسة، يرجى تسجيل الدخول"), 401
        return redirect(url_for("login"))
    try:
        db.auto_monthly_backup()
    except Exception:
        pass
    return None


def can_write():
    return g.user["role"] in ("admin", "accountant")


def require_write(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not can_write():
            return jsonify(error="صلاحيتك للمشاهدة فقط"), 403
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            return jsonify(error="هذه الصفحة لمدير النظام فقط"), 403
        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------
# سياق عام للقوالب
# ------------------------------------------------------------------

def _signatures():
    """قائمة التواقيع القابلة للتعديل من الإعدادات [{title, name}]"""
    try:
        return json.loads(db.get_setting("signatures") or "[]")
    except Exception:
        return []


@app.context_processor
def inject_globals():
    user = getattr(g, "user", None)
    return {
        "REGIONS": db.load_regions(),
        "user": user,
        "role_name": ROLES.get(user["role"], "") if user else "",
        "company_name": db.get_setting("company_name"),
        "tax_enabled": db.get_setting("tax_enabled", "0") == "1",
        "tax_name": db.get_setting("tax_name", "الضريبة"),
        "tax_rate": db.get_setting("tax_rate", "0"),
        "period_from": db.get_setting("period_from"),
        "period_to": db.get_setting("period_to"),
        "signatures": _signatures(),
        "invoice_store_name": db.get_setting("invoice_store_name", ""),
        "invoice_address": db.get_setting("invoice_address", ""),
        "invoice_phone": db.get_setting("invoice_phone", ""),
        "invoice_logo_text": db.get_setting("invoice_logo_text", ""),
        "invoice_logo_path": db.get_setting("invoice_logo_path", ""),
        "invoice_footer": db.get_setting("invoice_footer", ""),
        "invoice_paper_size": db.get_setting("invoice_paper_size", "A4"),
        "invoice_auto_print": db.get_setting("invoice_auto_print", "1") == "1",
        "invoice_show_cashier": db.get_setting("invoice_show_cashier", "1") == "1",
        "invoice_show_discount": db.get_setting("invoice_show_discount", "1") == "1",
        "today": date.today().isoformat(),
    }


# ------------------------------------------------------------------
# تسجيل الدخول
# ------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    ip = request.remote_addr or "0.0.0.0"
    if request.method == "POST":
        if not _check_rate_limit(ip):
            error = "تم حظر تسجيل الدخول مؤقتًا — كثرة المحاولات الخاطئة. حاول بعد 5 دقائق"
            return render_template("login.html", error=error)
        username = _sanitize(request.form.get("username", ""))
        password = request.form.get("password", "")
        user = db.query_one("SELECT * FROM users WHERE username=?", (username,))
        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True
            session["uid"] = user["id"]
            _login_attempts[ip].clear()
            db.audit(user["full_name"], "تسجيل دخول")
            return redirect(url_for("dashboard"))
        _record_login_attempt(ip)
        error = "اسم المستخدم أو كلمة المرور غير صحيحة"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    if getattr(g, "user", None):
        db.audit(g.user["full_name"], "تسجيل خروج")
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------
# الصفحات
# ------------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/accounts")
def accounts_page():
    return render_template("accounts.html")


@app.route("/accounts/tree")
def accounts_tree_page():
    return render_template("accounts_tree.html")


@app.route("/journal")
def journal_page():
    return render_template("journal.html")


@app.route("/ledger")
def ledger_page():
    return render_template("ledger.html")


@app.route("/trial-balance")
def trial_balance_page():
    return render_template("trial_balance.html")


@app.route("/budget")
def budget_page():
    return render_template("budget.html")


@app.route("/users")
@admin_required
def users_page():
    users = db.query("SELECT id, username, full_name, role, created_at FROM users ORDER BY id")
    return render_template("users.html", users=users)


@app.route("/settings")
@admin_required
def settings_page():
    try:
        backups_list = db.list_backups()
    except:
        backups_list = []
    currencies = db.query("""
        SELECT id, code, name, symbol, decimal_places, is_base, is_active
        FROM currencies
        WHERE is_active = 1
        ORDER BY is_base DESC, id
    """)

    company = db.query_one("""
        SELECT currency_id
        FROM companies
        WHERE is_active = 1
        ORDER BY id
        LIMIT 1
    """)

    current = None
    if company and company["currency_id"]:
        current = db.query_one("""
            SELECT id
            FROM currencies
            WHERE id = ? AND is_active = 1
            LIMIT 1
        """, (company["currency_id"],))

    # توافق مع قاعدة قديمة فقط إذا لم توجد عملة شركة صالحة.
    if not current:
        current = db.query_one("""
            SELECT id
            FROM currencies
            WHERE is_base = 1 AND is_active = 1
            ORDER BY id
            LIMIT 1
        """)

    system_users = db.query("""
        SELECT id, username, full_name, role
        FROM users
        ORDER BY
            CASE role
                WHEN 'admin' THEN 1
                WHEN 'accountant' THEN 2
                WHEN 'viewer' THEN 3
                ELSE 4
            END,
            id
    """)

    return render_template(
        "settings.html",
        system_currencies=currencies,
        current_system_currency_id=current["id"] if current else None,
        system_users=system_users,
        backups_list=backups_list
    )


@app.route("/partners")
def partners_page():
    return render_template("partners.html")


@app.route("/product-categories")
def product_categories_page():
    return render_template("product_categories.html")


@app.route("/units")
def units_page():
    return render_template("units.html")



# ------------------------------------------------------------------
# API — البحث الشامل في النظام
# ------------------------------------------------------------------
@app.route("/api/global-search")
def api_global_search():
    """بحث شامل في أهم بيانات النظام الحالية."""
    q = (request.args.get("q") or "").strip()

    if not q:
        return jsonify(ok=True, query="", groups=[], total=0)

    company_id = _current_company_id()
    if not company_id:
        return jsonify(
            ok=False,
            error="لا توجد شركة حالية"
        ), 400

    # منع البحث الضخم جدًا
    if len(q) > 100:
        q = q[:100]

    pattern = f"%{q}%"
    groups = []
    total = 0
    LIMIT = 8

    def add_group(group_type, title, icon, rows):
        nonlocal total
        if rows:
            clean = []
            for row in rows:
                clean.append(dict(row))
            groups.append({
                "type": group_type,
                "title": title,
                "icon": icon,
                "rows": clean
            })
            total += len(clean)

    # ------------------------------------------------------------
    # العملاء والموردون
    # ------------------------------------------------------------
    partners = db.query("""
        SELECT
            p.id,
            p.code,
            p.name,
            p.partner_type,
            p.phone,
            p.tax_no
        FROM business_partners p
        WHERE p.company_id=?
          AND (
                p.code LIKE ?
             OR p.name LIKE ?
             OR p.phone LIKE ?
             OR p.email LIKE ?
             OR p.tax_no LIKE ?
          )
        ORDER BY p.name COLLATE NOCASE, p.id
        LIMIT ?
    """, (
        company_id,
        pattern, pattern, pattern, pattern, pattern,
        LIMIT
    ))

    partner_rows = []
    for r in partners:
        kind = "عميل"
        if r["partner_type"] == "supplier":
            kind = "مورد"
        elif r["partner_type"] == "both":
            kind = "عميل / مورد"

        partner_rows.append({
            "id": r["id"],
            "title": r["name"],
            "subtitle": f"{kind} • {r['code']}"
                        + (f" • {r['phone']}" if r["phone"] else ""),
            "url": "/partners",
            "search": r["name"]
        })

    add_group("partners", "العملاء والموردون", "👤", partner_rows)

    # ------------------------------------------------------------
    # المنتجات + الباركود
    # ------------------------------------------------------------
    products = db.query("""
        SELECT DISTINCT
            p.id,
            p.code,
            p.name,
            p.brand,
            p.model,
            (
                SELECT GROUP_CONCAT(pb.barcode, ' | ')
                FROM product_barcodes pb
                WHERE pb.product_id=p.id
                  AND pb.company_id=p.company_id
                  AND pb.is_active=1
            ) AS barcodes
        FROM products p
        LEFT JOIN product_barcodes pbx
               ON pbx.product_id=p.id
              AND pbx.company_id=p.company_id
              AND pbx.is_active=1
        WHERE p.company_id=?
          AND (
                p.code LIKE ?
             OR p.name LIKE ?
             OR p.brand LIKE ?
             OR p.model LIKE ?
             OR p.description LIKE ?
             OR pbx.barcode LIKE ?
          )
        ORDER BY p.name COLLATE NOCASE, p.id
        LIMIT ?
    """, (
        company_id,
        pattern, pattern, pattern, pattern, pattern, pattern,
        LIMIT
    ))

    product_rows = []
    for r in products:
        subtitle = f"كود: {r['code']}"
        if r["brand"]:
            subtitle += f" • {r['brand']}"
        if r["model"]:
            subtitle += f" • {r['model']}"
        if r["barcodes"]:
            subtitle += f" • باركود: {r['barcodes']}"

        product_rows.append({
            "id": r["id"],
            "title": r["name"],
            "subtitle": subtitle,
            "url": "/products",
            "search": r["name"]
        })

    add_group("products", "المنتجات", "📦", product_rows)

    # ------------------------------------------------------------
    # فواتير المبيعات
    # ------------------------------------------------------------
    sales = db.query("""
        SELECT
            si.id,
            si.invoice_no,
            si.customer_invoice_no,
            si.invoice_date,
            si.grand_total,
            bp.name AS customer_name,
            bp.code AS customer_code
        FROM sales_invoices si
        LEFT JOIN business_partners bp
               ON bp.id=si.customer_id
              AND bp.company_id=si.company_id
        WHERE si.company_id=?
          AND (
                si.invoice_no LIKE ?
             OR si.customer_invoice_no LIKE ?
             OR bp.name LIKE ?
             OR bp.code LIKE ?
          )
        ORDER BY si.id DESC
        LIMIT ?
    """, (
        company_id,
        pattern, pattern, pattern, pattern,
        LIMIT
    ))

    sales_rows = []
    for r in sales:
        subtitle = f"فاتورة مبيعات • {r['invoice_date'] or ''}"
        if r["customer_name"]:
            subtitle += f" • {r['customer_name']}"
        if r["customer_code"]:
            subtitle += f" ({r['customer_code']})"

        sales_rows.append({
            "id": r["id"],
            "title": f"فاتورة {r['invoice_no']}",
            "subtitle": subtitle,
            "url": f"/sales?open={r['id']}",
            "search": r["invoice_no"]
        })

    add_group("sales", "فواتير المبيعات", "🧾", sales_rows)

    # ------------------------------------------------------------
    # فواتير المشتريات
    # ------------------------------------------------------------
    purchases = db.query("""
        SELECT
            pi.id,
            pi.invoice_no,
            pi.supplier_invoice_no,
            pi.invoice_date,
            pi.grand_total,
            bp.name AS supplier_name,
            bp.code AS supplier_code
        FROM purchase_invoices pi
        LEFT JOIN business_partners bp
               ON bp.id=pi.supplier_id
              AND bp.company_id=pi.company_id
        WHERE pi.company_id=?
          AND (
                pi.invoice_no LIKE ?
             OR pi.supplier_invoice_no LIKE ?
             OR bp.name LIKE ?
             OR bp.code LIKE ?
          )
        ORDER BY pi.id DESC
        LIMIT ?
    """, (
        company_id,
        pattern, pattern, pattern, pattern,
        LIMIT
    ))

    purchase_rows = []
    for r in purchases:
        subtitle = f"فاتورة مشتريات • {r['invoice_date'] or ''}"
        if r["supplier_name"]:
            subtitle += f" • {r['supplier_name']}"
        if r["supplier_code"]:
            subtitle += f" ({r['supplier_code']})"

        purchase_rows.append({
            "id": r["id"],
            "title": f"فاتورة {r['invoice_no']}",
            "subtitle": subtitle,
            "url": f"/purchases?open={r['id']}",
            "search": r["invoice_no"]
        })

    add_group("purchases", "فواتير المشتريات", "🛒", purchase_rows)

    # ------------------------------------------------------------
    # الحسابات
    # ------------------------------------------------------------
    accounts = db.query("""
        SELECT
            id,
            acc_no,
            name,
            type
        FROM accounts
        WHERE company_id=?
          AND (
                acc_no LIKE ?
             OR name LIKE ?
          )
        ORDER BY CAST(acc_no AS INTEGER), acc_no
        LIMIT ?
    """, (
        company_id,
        pattern, pattern,
        LIMIT
    ))

    account_rows = []
    for r in accounts:
        subtitle = f"رقم الحساب: {r['acc_no']}"
        if r["type"]:
            subtitle += f" • {r['type']}"

        account_rows.append({
            "id": r["id"],
            "title": r["name"],
            "subtitle": subtitle,
            "url": "/accounts",
            "search": r["name"]
        })

    add_group("accounts", "الحسابات", "📒", account_rows)

    # ------------------------------------------------------------
    # القيود اليومية
    # ------------------------------------------------------------
    journal = db.query("""
        SELECT
            je.id,
            je.movement_no,
            je.entry_date,
            je.description,
            je.entry_type,
            je.status
        FROM journal_entries je
        WHERE je.company_id=?
          AND (
                je.movement_no LIKE ?
             OR je.description LIKE ?
             OR je.entry_type LIKE ?
          )
        ORDER BY je.id DESC
        LIMIT ?
    """, (
        company_id,
        pattern, pattern, pattern,
        LIMIT
    ))

    journal_rows = []
    for r in journal:
        subtitle = f"قيد يومي • {r['entry_date'] or ''}"
        if r["entry_type"]:
            subtitle += f" • {r['entry_type']}"

        journal_rows.append({
            "id": r["id"],
            "title": f"حركة {r['movement_no']}",
            "subtitle": subtitle
                       + (f" • {r['description']}" if r["description"] else ""),
            "url": "/journal",
            "search": r["movement_no"]
        })

    add_group("journal", "القيود اليومية", "📘", journal_rows)

    # ------------------------------------------------------------
    # المستودعات
    # ------------------------------------------------------------
    warehouses = db.query("""
        SELECT
            id,
            code,
            name,
            phone,
            manager_name
        FROM warehouses
        WHERE company_id=?
          AND (
                code LIKE ?
             OR name LIKE ?
             OR address LIKE ?
             OR phone LIKE ?
             OR manager_name LIKE ?
          )
        ORDER BY name COLLATE NOCASE, id
        LIMIT ?
    """, (
        company_id,
        pattern, pattern, pattern, pattern, pattern,
        LIMIT
    ))

    warehouse_rows = []
    for r in warehouses:
        subtitle = f"كود: {r['code']}"
        if r["phone"]:
            subtitle += f" • {r['phone']}"
        if r["manager_name"]:
            subtitle += f" • المسؤول: {r['manager_name']}"

        warehouse_rows.append({
            "id": r["id"],
            "title": r["name"],
            "subtitle": subtitle,
            "url": "/warehouses",
            "search": r["name"]
        })

    add_group("warehouses", "المستودعات", "🏢", warehouse_rows)

    return jsonify(
        ok=True,
        query=q,
        groups=groups,
        total=total
    )


@app.route("/api/accounting-context")
def api_accounting_context():
    """السياق المحاسبي الحالي: الشركة/الفرع/السنة/الفترة/العملة ومراكز التكلفة."""
    company = db.query_one("SELECT * FROM companies WHERE is_active=1 ORDER BY id LIMIT 1")
    if not company:
        return jsonify(error="لم يتم تهيئة الشركة المحاسبية"), 500
    branch = db.query_one(
        "SELECT b.*, r.name AS region_name FROM branches b LEFT JOIN regions r ON r.id=b.region_id "
        "WHERE b.company_id=? AND b.is_active=1 ORDER BY b.id LIMIT 1",
        (company["id"],),
    )
    fiscal_year = db.query_one(
        "SELECT * FROM fiscal_years WHERE company_id=? ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END, id DESC LIMIT 1",
        (company["id"],),
    )
    period = None
    if fiscal_year:
        today = date.today().isoformat()
        period = db.query_one(
            "SELECT * FROM accounting_periods WHERE fiscal_year_id=? AND date_from<=? AND date_to>=? "
            "ORDER BY id LIMIT 1",
            (fiscal_year["id"], today, today),
        )
    # عملة الشركة هي المصدر الحقيقي للحالة الحالية.
    # is_base مجرد قيمة متزامنة للتوافق مع الأجزاء القديمة.
    currency = db.query_one(
        """SELECT c.*
           FROM currencies c
           WHERE c.id=? AND c.is_active=1
           LIMIT 1""",
        (company["currency_id"],),
    )

    # معالجة آمنة لقاعدة قديمة لا تحتوي على currency_id صالح.
    if not currency:
        currency = db.query_one(
            """SELECT *
               FROM currencies
               WHERE is_base=1 AND is_active=1
               ORDER BY id
               LIMIT 1"""
        )
    cost_centers = db.query(
        "SELECT id, code, name, parent_id, is_active FROM cost_centers WHERE company_id=? AND is_active=1 ORDER BY code",
        (company["id"],),
    )
    return jsonify(
        company=company, branch=branch, fiscal_year=fiscal_year, period=period,
        currency=currency, cost_centers=cost_centers,
    )


@app.route("/audit")
@admin_required
def audit_page():
    logs = db.query(
        "SELECT user, action, details, at FROM audit_log ORDER BY id DESC LIMIT 300"
    )
    return render_template("audit.html", logs=logs)


@app.route("/help")
def help_page():
    return render_template("help.html")


# ------------------------------------------------------------------

# ------------------------------------------------------------------
# APIs — العملاء والموردون
# ------------------------------------------------------------------


@app.route("/api/partners", methods=["POST"])
@require_write
def api_partners_create():
    """إنشاء عميل أو مورد أو طرف يجمع الاثنين."""
    d = request.json or {}

    code = (d.get("code") or "").strip()
    name = (d.get("name") or "").strip()
    partner_type = (d.get("partner_type") or "").strip()

    if not code or not name:
        return jsonify(error="كود الطرف واسم الطرف مطلوبان"), 400

    if partner_type not in ("customer", "supplier", "both"):
        return jsonify(error="نوع الطرف يجب أن يكون customer أو supplier أو both"), 400

    company = db.query_one(
        "SELECT * FROM companies WHERE is_active=1 ORDER BY id LIMIT 1"
    )
    if not company:
        return jsonify(error="لم يتم تهيئة الشركة المحاسبية"), 500

    company_id = company["id"]

    if db.query_one(
        "SELECT id FROM business_partners WHERE company_id=? AND code=?",
        (company_id, code),
    ):
        return jsonify(error=f"كود الطرف {code} موجود بالفعل"), 400

    branch_id = d.get("branch_id")
    region_id = d.get("region_id")

    if branch_id:
        branch = db.query_one(
            "SELECT id FROM branches WHERE id=? AND company_id=? AND is_active=1",
            (int(branch_id), company_id),
        )
        if not branch:
            return jsonify(error="الفرع غير موجود أو غير نشط"), 400
        branch_id = int(branch_id)
    else:
        branch = db.query_one(
            "SELECT id FROM branches WHERE company_id=? AND is_active=1 ORDER BY id LIMIT 1",
            (company_id,),
        )
        branch_id = branch["id"] if branch else None

    if region_id:
        region = db.query_one(
            "SELECT id FROM regions WHERE id=? AND is_active=1",
            (int(region_id),),
        )
        if not region:
            return jsonify(error="المنطقة غير موجودة أو غير نشطة"), 400
        region_id = int(region_id)

    receivable_account_id = None
    payable_account_id = None

    if partner_type in ("customer", "both"):
        acc = db.query_one(
            "SELECT id FROM accounts WHERE acc_no='1201' AND is_active=1 LIMIT 1"
        )
        if not acc:
            return jsonify(error="حساب العملاء 1201 غير موجود أو غير نشط"), 500
        receivable_account_id = acc["id"]

    if partner_type in ("supplier", "both"):
        acc = db.query_one(
            "SELECT id FROM accounts WHERE acc_no='2101' AND is_active=1 LIMIT 1"
        )
        if not acc:
            return jsonify(error="حساب الموردين 2101 غير موجود أو غير نشط"), 500
        payable_account_id = acc["id"]

    partner_id = db.execute(
        """
        INSERT INTO business_partners (
            company_id, branch_id, region_id, code, name, partner_type,
            receivable_account_id, payable_account_id,
            phone, email, address, tax_no, credit_limit, is_active, notes
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            company_id,
            branch_id,
            region_id,
            code,
            name,
            partner_type,
            receivable_account_id,
            payable_account_id,
            (d.get("phone") or "").strip(),
            (d.get("email") or "").strip(),
            (d.get("address") or "").strip(),
            (d.get("tax_no") or "").strip(),
            float(d.get("credit_limit") or 0),
            1 if d.get("is_active", True) else 0,
            (d.get("notes") or "").strip(),
        ),
    )

    db.audit(
        g.user["full_name"],
        "إضافة عميل/مورد",
        f"{code} - {name} ({partner_type})",
    )

    return jsonify(ok=True, id=partner_id), 201

@app.route("/api/partners/<int:partner_id>", methods=["PUT"])
@require_write
def api_partners_update(partner_id):
    """تعديل بيانات العميل/المورد مع الحفاظ على الحسابات المحاسبية."""
    d = request.json or {}

    partner = db.query_one(
        "SELECT * FROM business_partners WHERE id=?",
        (partner_id,),
    )
    if not partner:
        return jsonify(error="العميل/المورد غير موجود"), 404

    code = (d.get("code") or "").strip()
    name = (d.get("name") or "").strip()
    partner_type = (d.get("partner_type") or "").strip()

    if not code or not name:
        return jsonify(error="كود الطرف واسم الطرف مطلوبان"), 400

    if partner_type not in ("customer", "supplier", "both"):
        return jsonify(error="نوع الطرف يجب أن يكون customer أو supplier أو both"), 400

    company_id = partner["company_id"]

    duplicate = db.query_one(
        "SELECT id FROM business_partners WHERE company_id=? AND code=? AND id<>?",
        (company_id, code, partner_id),
    )
    if duplicate:
        return jsonify(error=f"كود الطرف {code} مستخدم بالفعل"), 400

    branch_id = d.get("branch_id")
    region_id = d.get("region_id")

    if branch_id:
        branch = db.query_one(
            "SELECT id FROM branches WHERE id=? AND company_id=? AND is_active=1",
            (int(branch_id), company_id),
        )
        if not branch:
            return jsonify(error="الفرع غير موجود أو غير نشط"), 400
        branch_id = int(branch_id)
    else:
        branch_id = partner["branch_id"]

    if region_id:
        region = db.query_one(
            "SELECT id FROM regions WHERE id=? AND is_active=1",
            (int(region_id),),
        )
        if not region:
            return jsonify(error="المنطقة غير موجودة أو غير نشطة"), 400
        region_id = int(region_id)
    else:
        region_id = partner["region_id"]

    receivable_account_id = None
    payable_account_id = None

    if partner_type in ("customer", "both"):
        acc = db.query_one(
            "SELECT id FROM accounts WHERE acc_no='1201' AND is_active=1 LIMIT 1"
        )
        if not acc:
            return jsonify(error="حساب العملاء 1201 غير موجود أو غير نشط"), 500
        receivable_account_id = acc["id"]

    if partner_type in ("supplier", "both"):
        acc = db.query_one(
            "SELECT id FROM accounts WHERE acc_no='2101' AND is_active=1 LIMIT 1"
        )
        if not acc:
            return jsonify(error="حساب الموردين 2101 غير موجود أو غير نشط"), 500
        payable_account_id = acc["id"]

    db.execute(
        """
        UPDATE business_partners
        SET branch_id=?, region_id=?, code=?, name=?, partner_type=?,
            receivable_account_id=?, payable_account_id=?, phone=?, email=?,
            address=?, tax_no=?, credit_limit=?, is_active=?, notes=?
        WHERE id=?
        """,
        (
            branch_id, region_id, code, name, partner_type,
            receivable_account_id, payable_account_id,
            (d.get("phone") or "").strip(),
            (d.get("email") or "").strip(),
            (d.get("address") or "").strip(),
            (d.get("tax_no") or "").strip(),
            float(d.get("credit_limit") or 0),
            1 if d.get("is_active", True) else 0,
            (d.get("notes") or "").strip(),
            partner_id,
        ),
    )

    db.audit(
        g.user["full_name"],
        "تعديل عميل/مورد",
        f"{code} - {name} ({partner_type})",
    )

    return jsonify(ok=True)

@app.route("/api/partners/<int:partner_id>", methods=["DELETE"])
@require_write
def api_partners_delete(partner_id):
    """حذف العميل/المورد مع منع الحذف إذا كان مرتبطًا بحركات محاسبية."""
    partner = db.query_one(
        "SELECT * FROM business_partners WHERE id=?",
        (partner_id,),
    )
    if not partner:
        return jsonify(error="العميل/المورد غير موجود"), 404

    used = db.query_one(
        "SELECT id FROM journal_lines WHERE partner_id=? LIMIT 1",
        (partner_id,),
    )
    if used:
        return jsonify(
            error="لا يمكن حذف العميل/المورد لوجود حركات محاسبية مرتبطة به"
        ), 400

    db.execute(
        "DELETE FROM business_partners WHERE id=?",
        (partner_id,),
    )

    db.audit(
        g.user["full_name"],
        "حذف عميل/مورد",
        f"{partner['code']} - {partner['name']}",
    )

    return jsonify(ok=True)

@app.route("/api/partners")
def api_partners():
    """قائمة العملاء والموردين مع البحث والتصفية."""
    q = request.args.get("q", "").strip()
    partner_type = request.args.get("type", "").strip()

    sql = """
        SELECT
            p.*,
            c.name AS company_name,
            b.name AS branch_name,
            r.name AS region_name,
            ra.acc_no AS receivable_account_no,
            ra.name AS receivable_account_name,
            pa.acc_no AS payable_account_no,
            pa.name AS payable_account_name
        FROM business_partners p
        LEFT JOIN companies c ON c.id = p.company_id
        LEFT JOIN branches b ON b.id = p.branch_id
        LEFT JOIN regions r ON r.id = p.region_id
        LEFT JOIN accounts ra ON ra.id = p.receivable_account_id
        LEFT JOIN accounts pa ON pa.id = p.payable_account_id
        WHERE 1=1
    """
    args = []

    if q:
        sql += """
            AND (
                p.code LIKE ?
                OR p.name LIKE ?
                OR p.phone LIKE ?
                OR p.tax_no LIKE ?
            )
        """
        pattern = f"%{q}%"
        args.extend([pattern, pattern, pattern, pattern])

    if partner_type in ("customer", "supplier", "both"):
        sql += " AND p.partner_type=?"
        args.append(partner_type)

    sql += " ORDER BY p.name COLLATE NOCASE, p.id"

    return jsonify(partners=db.query(sql, args))


# ------------------------------------------------------------------
# API — كشف حساب العميل / المورد
# ------------------------------------------------------------------

@app.route("/api/partners/<int:partner_id>/statement")
def api_partner_statement(partner_id):
    """كشف حساب طرف محدد اعتمادًا على القيود المرحلة المرتبطة به."""

    partner = db.query_one(
        """
        SELECT
            p.*,
            c.name AS company_name,
            b.name AS branch_name,
            r.name AS region_name,
            ra.acc_no AS receivable_account_no,
            ra.name AS receivable_account_name,
            pa.acc_no AS payable_account_no,
            pa.name AS payable_account_name
        FROM business_partners p
        LEFT JOIN companies c ON c.id = p.company_id
        LEFT JOIN branches b ON b.id = p.branch_id
        LEFT JOIN regions r ON r.id = p.region_id
        LEFT JOIN accounts ra ON ra.id = p.receivable_account_id
        LEFT JOIN accounts pa ON pa.id = p.payable_account_id
        WHERE p.id=?
        """,
        (partner_id,),
    )

    if not partner:
        return jsonify(error="العميل/المورد غير موجود"), 404

    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    for value, label in ((date_from, "from"), (date_to, "to")):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return jsonify(
                    error=f"التاريخ {label} غير صحيح، استخدم YYYY-MM-DD"
                ), 400

    if date_from and date_to and date_from > date_to:
        return jsonify(
            error="تاريخ البداية يجب أن يكون قبل أو مساويًا لتاريخ النهاية"
        ), 400

    # العميل: مدين - دائن
    # المورد: دائن - مدين
    is_supplier = partner["partner_type"] == "supplier"

    def signed(debit, credit):
        if is_supplier:
            return round(credit - debit, 2)
        return round(debit - credit, 2)

    # ---------------------------------------------------------
    # الرصيد الافتتاحي
    #
    # إذا لم يوجد تاريخ بداية:
    #   لا يوجد رصيد افتتاحي للفترة الحالية، لذلك = صفر.
    #
    # إذا وجد تاريخ بداية:
    #   نحسب فقط القيود المرحلة التي تسبق تاريخ البداية.
    # ---------------------------------------------------------
    opening_debit = 0.0
    opening_credit = 0.0
    opening_balance = 0.0

    if date_from:
        opening = db.query_one(
            """
            SELECT
                COALESCE(SUM(jl.debit), 0) AS debit,
                COALESCE(SUM(jl.credit), 0) AS credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id=jl.entry_id
            WHERE
                jl.partner_id=?
                AND je.status='posted'
                AND je.entry_date < ?
            """,
            (partner_id, date_from),
        )

        opening_debit = round(opening["debit"] or 0, 2)
        opening_credit = round(opening["credit"] or 0, 2)
        opening_balance = signed(opening_debit, opening_credit)

    # ---------------------------------------------------------
    # حركات الفترة
    # ---------------------------------------------------------
    args = [partner_id]

    where = """
        jl.partner_id=?
        AND je.status='posted'
    """

    if date_from:
        where += " AND je.entry_date >= ?"
        args.append(date_from)

    if date_to:
        where += " AND je.entry_date <= ?"
        args.append(date_to)

    rows = db.query(
        f"""
        SELECT
            jl.id AS line_id,
            je.id AS entry_id,
            je.entry_date,
            je.movement_no,
            je.description,
            je.region,
            je.entry_type,
            jl.account_id,
            a.acc_no,
            a.name AS account_name,
            jl.debit,
            jl.credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id=jl.entry_id
        JOIN accounts a ON a.id=jl.account_id
        WHERE {where}
        ORDER BY je.entry_date, je.id, jl.id
        """,
        args,
    )

    balance = opening_balance
    entries = []

    total_debit = 0.0
    total_credit = 0.0

    for row in rows:
        debit = round(row["debit"] or 0, 2)
        credit = round(row["credit"] or 0, 2)

        total_debit += debit
        total_credit += credit

        balance = round(balance + signed(debit, credit), 2)

        entries.append({
            "line_id": row["line_id"],
            "entry_id": row["entry_id"],
            "entry_date": row["entry_date"],
            "movement_no": row["movement_no"],
            "description": row["description"] or "",
            "region": row["region"] or "",
            "entry_type": row["entry_type"] or "",
            "account_id": row["account_id"],
            "acc_no": row["acc_no"],
            "account_name": row["account_name"],
            "debit": debit,
            "credit": credit,
            "balance": balance,
        })

    total_debit = round(total_debit, 2)
    total_credit = round(total_credit, 2)
    closing_balance = round(balance, 2)

    return jsonify(
        partner={
            "id": partner["id"],
            "code": partner["code"],
            "name": partner["name"],
            "partner_type": partner["partner_type"],
            "company_name": partner["company_name"],
            "branch_name": partner["branch_name"],
            "region_name": partner["region_name"],
            "receivable_account_no": partner["receivable_account_no"],
            "receivable_account_name": partner["receivable_account_name"],
            "payable_account_no": partner["payable_account_no"],
            "payable_account_name": partner["payable_account_name"],
        },
        filters={
            "from": date_from,
            "to": date_to,
        },
        summary={
            "opening_debit": opening_debit,
            "opening_credit": opening_credit,
            "opening_balance": opening_balance,
            "debit": total_debit,
            "credit": total_credit,
            "closing_balance": closing_balance,
        },
        entries=entries,
    )

@app.route("/api/accounts")
def api_accounts():
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM accounts"
    args = []
    if q:
        sql += " WHERE acc_no LIKE ? OR name LIKE ?"
        args = [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY CAST(acc_no AS INTEGER), acc_no"
    return jsonify(accounts=db.query(sql, args))


@app.route("/api/accounts", methods=["POST"])
@require_write
def api_accounts_create():
    d = request.json or {}
    acc_no = (d.get("acc_no") or "").strip()
    name = (d.get("name") or "").strip()
    if not acc_no or not name:
        return jsonify(error="رقم الحساب واسم الحساب مطلوبان"), 400
    if db.query_one("SELECT id FROM accounts WHERE acc_no=?", (acc_no,)):
        return jsonify(error=f"رقم الحساب {acc_no} موجود بالفعل"), 400
    acc_id = db.execute(
        "INSERT INTO accounts (acc_no, name, type, opening_balance, budget) VALUES (?,?,?,?,?)",
        (acc_no, name, (d.get("type") or "").strip(), float(d.get("opening_balance") or 0), float(d.get("budget") or 0)),
    )
    db.audit(g.user["full_name"], "إضافة حساب", f"{acc_no} - {name}")
    return jsonify(ok=True, id=acc_id)


@app.route("/api/accounts/<int:acc_id>", methods=["PUT"])
@require_write
def api_accounts_update(acc_id):
    d = request.json or {}
    acc_no = (d.get("acc_no") or "").strip()
    name = (d.get("name") or "").strip()
    if not acc_no or not name:
        return jsonify(error="رقم الحساب واسم الحساب مطلوبان"), 400
    dup = db.query_one("SELECT id FROM accounts WHERE acc_no=? AND id<>?", (acc_no, acc_id))
    if dup:
        return jsonify(error=f"رقم الحساب {acc_no} مستخدم بالفعل في حساب آخر"), 400
    db.execute(
        "UPDATE accounts SET acc_no=?, name=?, type=?, opening_balance=?, budget=? WHERE id=?",
        (acc_no, name, (d.get("type") or "").strip(), float(d.get("opening_balance") or 0), float(d.get("budget") or 0), acc_id),
    )
    db.audit(g.user["full_name"], "تعديل حساب", f"{acc_no} - {name}")
    return jsonify(ok=True)


@app.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
@require_write
def api_accounts_delete(acc_id):
    used = db.query_one("SELECT id FROM journal_lines WHERE account_id=? LIMIT 1", (acc_id,))
    if used:
        return jsonify(error="لا يمكن حذف الحساب لوجود حركات عليه في دفتر اليومية"), 400
    acc = db.query_one("SELECT * FROM accounts WHERE id=?", (acc_id,))
    if not acc:
        return jsonify(error="الحساب غير موجود"), 404
    db.execute("DELETE FROM accounts WHERE id=?", (acc_id,))
    db.audit(g.user["full_name"], "حذف حساب", f"{acc['acc_no']} - {acc['name']}")
    return jsonify(ok=True)


@app.route("/api/accounts/wipe", methods=["POST"])
@admin_required
def api_accounts_wipe():
    """مسح الشجرة بالكامل: كل الحسابات وكل القيود نهائيًا (الإدارة فقط)."""
    n_acc = db.query_one("SELECT COUNT(*) AS c FROM accounts")["c"]
    n_ent = db.query_one("SELECT COUNT(*) AS c FROM journal_entries")["c"]

    if not n_acc:
        return jsonify(error="لا توجد حسابات لمسحها"), 400

    # حذف القيود أولًا؛ journal_lines تُحذف تلقائيًا
    # بسبب ON DELETE CASCADE على journal_entries.
    db.execute("DELETE FROM journal_entries")

    # فصل حسابات العملاء والموردين عن دليل الحسابات.
    # لا نحذف العملاء/الموردين أنفسهم.
    db.execute("""
        UPDATE business_partners
        SET receivable_account_id = NULL,
            payable_account_id = NULL
    """)

    # إزالة العلاقات الأبوية داخل دليل الحسابات.
    db.execute("UPDATE accounts SET parent_id = NULL")

    # حذف دليل الحسابات بالكامل.
    db.execute("DELETE FROM accounts")

    db.audit(
        g.user["full_name"],
        "مسح الشجرة",
        f"حذف نهائي: {n_acc} حساب و{n_ent} قيد"
    )

    return jsonify(
        ok=True,
        deleted_accounts=n_acc,
        deleted_entries=n_ent
    )


@app.route("/api/accounts/budget", methods=["POST"])
@require_write
def api_accounts_bulk_budget():
    d = request.json or {}
    items = d.get("items") or []
    if not items:
        return jsonify(error="لا توجد بيانات للتحديث"), 400
    updated = 0
    for item in items:
        acc_id = item.get("id")
        budget = float(item.get("budget") or 0)
        if acc_id:
            db.execute("UPDATE accounts SET budget=? WHERE id=?", (budget, acc_id))
            updated += 1
    db.audit(g.user["full_name"], "تحديث ميزانيات", f"{updated} حساب")
    return jsonify(ok=True, updated=updated)


@app.route("/api/accounts/import", methods=["POST"])
@require_write
def api_accounts_import():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".xlsx"):
        return jsonify(error="اختر ملف إكسيل بصيغة .xlsx"), 400
    rows, errors = xl.read_accounts_template(f.stream)
    added, updated = 0, 0
    for r in rows:
        existing = db.query_one("SELECT id FROM accounts WHERE acc_no=?", (r["acc_no"],))
        if existing:
            db.execute(
                "UPDATE accounts SET name=?, type=?, opening_balance=? WHERE id=?",
                (r["name"], r["type"], r["opening"], existing["id"]),
            )
            updated += 1
        else:
            db.execute(
                "INSERT INTO accounts (acc_no, name, type, opening_balance) VALUES (?,?,?,?)",
                (r["acc_no"], r["name"], r["type"], r["opening"]),
            )
            added += 1
    db.audit(g.user["full_name"], "استيراد دليل حسابات",
             f"جديد: {added} | محدّث: {updated}")
    return jsonify(ok=True, added=added, updated=updated, errors=errors[:10])



# ------------------------------------------------------------------
# المنتجات والمخزون — وحدات القياس
# ------------------------------------------------------------------

@app.route("/api/units", methods=["GET"])
def api_units():
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    rows = db.query(
        """
        SELECT
            id,
            code,
            name,
            symbol,
            unit_type,
            decimal_places,
            is_active,
            created_at
        FROM units
        WHERE company_id=?
        ORDER BY code, id
        """,
        (company_id,),
    )

    return jsonify(items=rows)


@app.route("/api/units", methods=["POST"])
@require_write
def api_unit_create():
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    d = _sanitize_dict(request.json or {})

    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or "").strip()
    symbol = str(d.get("symbol") or "").strip()
    unit_type = str(d.get("unit_type") or "quantity").strip()

    if not code:
        return jsonify(error="كود الوحدة مطلوب"), 400

    if not name:
        return jsonify(error="اسم الوحدة مطلوب"), 400

    allowed_types = {"quantity", "length", "weight", "volume", "area", "time", "other"}

    if unit_type not in allowed_types:
        return jsonify(error="نوع الوحدة غير صحيح"), 400

    try:
        decimal_places = int(d.get("decimal_places", 2))
    except (TypeError, ValueError):
        return jsonify(error="عدد المنازل العشرية غير صحيح"), 400

    if decimal_places < 0 or decimal_places > 6:
        return jsonify(error="عدد المنازل العشرية يجب أن يكون بين 0 و6"), 400

    existing = db.query_one(
        """
        SELECT id
        FROM units
        WHERE company_id=? AND code=?
        """,
        (company_id, code),
    )

    if existing:
        return jsonify(error="كود الوحدة مستخدم مسبقًا"), 409

    try:
        new_id = db.execute(
            """
            INSERT INTO units
                (company_id, code, name, symbol, unit_type, decimal_places)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                code,
                name,
                symbol,
                unit_type,
                decimal_places,
            ),
        )
    except db.IntegrityError:
        return jsonify(error="تعذر إضافة الوحدة بسبب تعارض في البيانات"), 400

    db.audit(
        g.user["full_name"],
        "إضافة وحدة قياس",
        f"{code} - {name}",
    )

    item = db.query_one(
        """
        SELECT
            id,
            code,
            name,
            symbol,
            unit_type,
            decimal_places,
            is_active,
            created_at
        FROM units
        WHERE id=? AND company_id=?
        """,
        (new_id, company_id),
    )

    return jsonify(ok=True, item=item), 201


@app.route("/api/units/<int:unit_id>", methods=["PUT"])
@require_write
def api_unit_update(unit_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    unit = db.query_one(
        """
        SELECT *
        FROM units
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    if not unit:
        return jsonify(error="الوحدة غير موجودة"), 404

    d = _sanitize_dict(request.json or {})

    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or "").strip()
    symbol = str(d.get("symbol") or "").strip()
    unit_type = str(d.get("unit_type") or "quantity").strip()

    if not code:
        return jsonify(error="كود الوحدة مطلوب"), 400

    if not name:
        return jsonify(error="اسم الوحدة مطلوب"), 400

    allowed_types = {"quantity", "length", "weight", "volume", "area", "time", "other"}

    if unit_type not in allowed_types:
        return jsonify(error="نوع الوحدة غير صحيح"), 400

    try:
        decimal_places = int(d.get("decimal_places", 2))
    except (TypeError, ValueError):
        return jsonify(error="عدد المنازل العشرية غير صحيح"), 400

    if decimal_places < 0 or decimal_places > 6:
        return jsonify(error="عدد المنازل العشرية يجب أن يكون بين 0 و6"), 400

    existing = db.query_one(
        """
        SELECT id
        FROM units
        WHERE company_id=? AND code=? AND id<>?
        """,
        (company_id, code, unit_id),
    )

    if existing:
        return jsonify(error="كود الوحدة مستخدم مسبقًا"), 409

    try:
        db.execute(
            """
            UPDATE units
            SET code=?,
                name=?,
                symbol=?,
                unit_type=?,
                decimal_places=?
            WHERE id=? AND company_id=?
            """,
            (
                code,
                name,
                symbol,
                unit_type,
                decimal_places,
                unit_id,
                company_id,
            ),
        )
    except db.IntegrityError:
        return jsonify(error="تعذر تعديل الوحدة بسبب تعارض في البيانات"), 400

    db.audit(
        g.user["full_name"],
        "تعديل وحدة قياس",
        f"{code} - {name}",
    )

    item = db.query_one(
        """
        SELECT
            id,
            code,
            name,
            symbol,
            unit_type,
            decimal_places,
            is_active,
            created_at
        FROM units
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    return jsonify(ok=True, item=item)


@app.route("/api/units/<int:unit_id>/deactivate", methods=["POST"])
@require_write
def api_unit_deactivate(unit_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    unit = db.query_one(
        """
        SELECT id, code, name
        FROM units
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    if not unit:
        return jsonify(error="الوحدة غير موجودة"), 404

    db.execute(
        """
        UPDATE units
        SET is_active=0
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تعطيل وحدة قياس",
        f"{unit['code']} - {unit['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/units/<int:unit_id>/activate", methods=["POST"])
@require_write
def api_unit_activate(unit_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    unit = db.query_one(
        """
        SELECT id, code, name
        FROM units
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    if not unit:
        return jsonify(error="الوحدة غير موجودة"), 404

    db.execute(
        """
        UPDATE units
        SET is_active=1
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تفعيل وحدة قياس",
        f"{unit['code']} - {unit['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/units/<int:unit_id>", methods=["DELETE"])
@require_write
def api_unit_delete(unit_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    unit = db.query_one(
        """
        SELECT id, code, name
        FROM units
        WHERE id=? AND company_id=?
        """,
        (unit_id, company_id),
    )

    if not unit:
        return jsonify(error="الوحدة غير موجودة"), 404

    product = db.query_one(
        """
        SELECT id
        FROM products
        WHERE base_unit_id=? AND company_id=?
        LIMIT 1
        """,
        (unit_id, company_id),
    )

    if product:
        return jsonify(
            error="لا يمكن حذف الوحدة لأنها مستخدمة في منتجات"
        ), 409

    try:
        db.execute(
            """
            DELETE FROM units
            WHERE id=? AND company_id=?
            """,
            (unit_id, company_id),
        )
    except db.IntegrityError:
        return jsonify(
            error="لا يمكن حذف الوحدة لأنها مرتبطة ببيانات أخرى"
        ), 409

    db.audit(
        g.user["full_name"],
        "حذف وحدة قياس",
        f"{unit['code']} - {unit['name']}",
    )

    return jsonify(ok=True)


# ------------------------------------------------------------------
# المنتجات والمخزون — التصنيفات
# ------------------------------------------------------------------

def _current_company_id():
    company = db.query_one(
        "SELECT id FROM companies ORDER BY id LIMIT 1"
    )
    return company["id"] if company else None


@app.route("/api/product-categories", methods=["GET"])
def api_product_categories():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    rows = db.query(
        """
        SELECT
            pc.id,
            pc.code,
            pc.name,
            pc.parent_id,
            parent.name AS parent_name,
            pc.description,
            pc.is_active,
            pc.created_at
        FROM product_categories pc
        LEFT JOIN product_categories parent
               ON parent.id = pc.parent_id
        WHERE pc.company_id=?
        ORDER BY pc.code, pc.id
        """,
        (company_id,),
    )

    return jsonify(items=rows)


@app.route("/api/product-categories", methods=["POST"])
@require_write
def api_product_category_create():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    d = _sanitize_dict(request.json or {})

    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or "").strip()
    description = str(d.get("description") or "").strip()
    parent_id = d.get("parent_id")

    if not code:
        return jsonify(error="كود التصنيف مطلوب"), 400

    if not name:
        return jsonify(error="اسم التصنيف مطلوب"), 400

    if parent_id in ("", None):
        parent_id = None
    else:
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            return jsonify(error="التصنيف الأب غير صحيح"), 400

        parent = db.query_one(
            """
            SELECT id
            FROM product_categories
            WHERE id=? AND company_id=?
            """,
            (parent_id, company_id),
        )
        if not parent:
            return jsonify(error="التصنيف الأب غير موجود في الشركة"), 400

    existing = db.query_one(
        """
        SELECT id
        FROM product_categories
        WHERE company_id=? AND code=?
        """,
        (company_id, code),
    )

    if existing:
        return jsonify(error="كود التصنيف مستخدم مسبقًا"), 409

    try:
        new_id = db.execute(
            """
            INSERT INTO product_categories
                (company_id, parent_id, code, name, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                company_id,
                parent_id,
                code,
                name,
                description,
            ),
        )
    except db.IntegrityError:
        return jsonify(error="تعذر إضافة التصنيف بسبب تعارض في البيانات"), 400

    db.audit(
        g.user["full_name"],
        "إضافة تصنيف منتج",
        f"{code} - {name}",
    )

    item = db.query_one(
        """
        SELECT
            pc.id,
            pc.code,
            pc.name,
            pc.parent_id,
            parent.name AS parent_name,
            pc.description,
            pc.is_active,
            pc.created_at
        FROM product_categories pc
        LEFT JOIN product_categories parent
               ON parent.id = pc.parent_id
        WHERE pc.id=? AND pc.company_id=?
        """,
        (new_id, company_id),
    )

    return jsonify(ok=True, item=item), 201


@app.route("/api/product-categories/<int:category_id>", methods=["PUT"])
@require_write
def api_product_category_update(category_id):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    category = db.query_one(
        """
        SELECT *
        FROM product_categories
        WHERE id=? AND company_id=?
        """,
        (category_id, company_id),
    )

    if not category:
        return jsonify(error="التصنيف غير موجود"), 404

    d = _sanitize_dict(request.json or {})

    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or "").strip()
    description = str(d.get("description") or "").strip()
    parent_id = d.get("parent_id")

    if not code:
        return jsonify(error="كود التصنيف مطلوب"), 400

    if not name:
        return jsonify(error="اسم التصنيف مطلوب"), 400

    if parent_id in ("", None):
        parent_id = None
    else:
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            return jsonify(error="التصنيف الأب غير صحيح"), 400

        if parent_id == category_id:
            return jsonify(error="لا يمكن جعل التصنيف أبًا لنفسه"), 400

        parent = db.query_one(
            """
            SELECT id
            FROM product_categories
            WHERE id=? AND company_id=?
            """,
            (parent_id, company_id),
        )
        if not parent:
            return jsonify(error="التصنيف الأب غير موجود في الشركة"), 400

        # منع تكوين حلقة في شجرة التصنيفات.
        check_id = parent_id
        while check_id:
            if check_id == category_id:
                return jsonify(error="لا يمكن إنشاء حلقة في شجرة التصنيفات"), 400

            row = db.query_one(
                """
                SELECT parent_id
                FROM product_categories
                WHERE id=? AND company_id=?
                """,
                (check_id, company_id),
            )
            check_id = row["parent_id"] if row else None

    existing = db.query_one(
        """
        SELECT id
        FROM product_categories
        WHERE company_id=? AND code=? AND id<>?
        """,
        (company_id, code, category_id),
    )

    if existing:
        return jsonify(error="كود التصنيف مستخدم مسبقًا"), 409

    try:
        db.execute(
            """
            UPDATE product_categories
            SET code=?, name=?, parent_id=?, description=?
            WHERE id=? AND company_id=?
            """,
            (
                code,
                name,
                parent_id,
                description,
                category_id,
                company_id,
            ),
        )
    except db.IntegrityError:
        return jsonify(error="تعذر تعديل التصنيف بسبب تعارض في البيانات"), 400

    db.audit(
        g.user["full_name"],
        "تعديل تصنيف منتج",
        f"{code} - {name}",
    )

    item = db.query_one(
        """
        SELECT
            pc.id,
            pc.code,
            pc.name,
            pc.parent_id,
            parent.name AS parent_name,
            pc.description,
            pc.is_active,
            pc.created_at
        FROM product_categories pc
        LEFT JOIN product_categories parent
               ON parent.id = pc.parent_id
        WHERE pc.id=? AND pc.company_id=?
        """,
        (category_id, company_id),
    )

    return jsonify(ok=True, item=item)


@app.route("/api/product-categories/<int:category_id>/deactivate", methods=["POST"])
@require_write
def api_product_category_deactivate(category_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    category = db.query_one(
        """
        SELECT id, code, name
        FROM product_categories
        WHERE id=? AND company_id=?
        """,
        (category_id, company_id),
    )

    if not category:
        return jsonify(error="التصنيف غير موجود"), 404

    db.execute(
        """
        UPDATE product_categories
        SET is_active=0
        WHERE id=? AND company_id=?
        """,
        (category_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تعطيل تصنيف منتج",
        f"{category['code']} - {category['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/product-categories/<int:category_id>/activate", methods=["POST"])
@require_write
def api_product_category_activate(category_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    category = db.query_one(
        """
        SELECT id, code, name
        FROM product_categories
        WHERE id=? AND company_id=?
        """,
        (category_id, company_id),
    )

    if not category:
        return jsonify(error="التصنيف غير موجود"), 404

    db.execute(
        """
        UPDATE product_categories
        SET is_active=1
        WHERE id=? AND company_id=?
        """,
        (category_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تفعيل تصنيف منتج",
        f"{category['code']} - {category['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/product-categories/<int:category_id>", methods=["DELETE"])
@require_write
def api_product_category_delete(category_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    category = db.query_one(
        """
        SELECT id, code, name
        FROM product_categories
        WHERE id=? AND company_id=?
        """,
        (category_id, company_id),
    )

    if not category:
        return jsonify(error="التصنيف غير موجود"), 404

    child = db.query_one(
        """
        SELECT id
        FROM product_categories
        WHERE parent_id=? AND company_id=?
        LIMIT 1
        """,
        (category_id, company_id),
    )

    if child:
        return jsonify(
            error="لا يمكن حذف التصنيف لأنه يحتوي على تصنيفات فرعية"
        ), 409

    product = db.query_one(
        """
        SELECT id
        FROM products
        WHERE category_id=? AND company_id=?
        LIMIT 1
        """,
        (category_id, company_id),
    )

    if product:
        return jsonify(
            error="لا يمكن حذف التصنيف لأنه مستخدم في منتجات"
        ), 409

    try:
        db.execute(
            """
            DELETE FROM product_categories
            WHERE id=? AND company_id=?
            """,
            (category_id, company_id),
        )
    except db.IntegrityError:
        return jsonify(
            error="لا يمكن حذف التصنيف لأنه مرتبط ببيانات أخرى"
        ), 409

    db.audit(
        g.user["full_name"],
        "حذف تصنيف منتج",
        f"{category['code']} - {category['name']}",
    )

    return jsonify(ok=True)


# ------------------------------------------------------------------
# المنتجات والمخزون — المنتجات
# ------------------------------------------------------------------



# ============================================================
# PRODUCT BARCODE API
# ============================================================

@app.route("/api/product-barcodes", methods=["GET"])
@require_write
def api_product_barcodes_list():
    company_id = _current_company_id()

    product_id = request.args.get("product_id", type=int)
    barcode = (request.args.get("barcode") or "").strip()

    conn = db.connect()
    try:
        sql = """
            SELECT
                pb.id,
                pb.company_id,
                pb.product_id,
                pb.unit_id,
                pb.barcode,
                pb.is_primary,
                pb.is_active,
                pb.created_at,
                p.code AS product_code,
                p.name AS product_name,
                u.code AS unit_code,
                u.name AS unit_name,
                u.symbol AS unit_symbol
            FROM product_barcodes pb
            JOIN products p
              ON p.id = pb.product_id
            LEFT JOIN units u
              ON u.id = pb.unit_id
            WHERE pb.company_id = ?
        """

        args = [company_id]

        if product_id:
            sql += " AND pb.product_id = ?"
            args.append(product_id)

        if barcode:
            sql += " AND pb.barcode = ?"
            args.append(barcode)

        sql += " ORDER BY pb.product_id, pb.is_primary DESC, pb.id"

        rows = conn.execute(sql, args).fetchall()

        return jsonify({
            "items": [dict(row) for row in rows]
        })
    finally:
        conn.close()


@app.route("/api/product-barcodes", methods=["POST"])
@require_write
def api_product_barcode_create():
    company_id = _current_company_id()
    user_id = session.get("user_id")


    data = request.get_json(silent=True) or {}

    product_id = data.get("product_id")
    unit_id = data.get("unit_id")
    barcode = str(data.get("barcode") or "").strip()
    is_primary = 1 if data.get("is_primary") else 0

    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return jsonify({"error": "رقم الصنف غير صحيح"}), 400

    if unit_id in (None, "", 0):
        unit_id = None
    else:
        try:
            unit_id = int(unit_id)
        except (TypeError, ValueError):
            return jsonify({"error": "رقم الوحدة غير صحيح"}), 400

    if not barcode:
        return jsonify({"error": "الباركود مطلوب"}), 400

    if len(barcode) > 100:
        return jsonify({"error": "الباركود طويل جدًا"}), 400

    conn = db.connect()

    try:
        product = conn.execute("""
            SELECT id, name, base_unit_id
            FROM products
            WHERE id = ?
              AND company_id = ?
              AND is_active = 1
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({"error": "الصنف غير موجود أو غير نشط"}), 404

        if unit_id is not None:
            unit = conn.execute("""
                SELECT id
                FROM units
                WHERE id = ?
                  AND company_id = ?
                  AND is_active = 1
            """, (unit_id, company_id)).fetchone()

            if not unit:
                return jsonify({"error": "الوحدة غير موجودة أو غير نشطة"}), 404

        existing = conn.execute("""
            SELECT
                pb.id,
                pb.product_id,
                p.name AS product_name
            FROM product_barcodes pb
            JOIN products p ON p.id = pb.product_id
            WHERE pb.company_id = ?
              AND pb.barcode = ?
        """, (company_id, barcode)).fetchone()

        if existing:
            return jsonify({
                "error": "هذا الباركود مستخدم بالفعل",
                "barcode_id": existing["id"],
                "product_id": existing["product_id"],
                "product_name": existing["product_name"]
            }), 409

        if is_primary:
            conn.execute("""
                UPDATE product_barcodes
                SET is_primary = 0
                WHERE company_id = ?
                  AND product_id = ?
            """, (company_id, product_id))

        cur = conn.execute("""
            INSERT INTO product_barcodes (
                company_id,
                product_id,
                unit_id,
                barcode,
                is_primary,
                is_active
            )
            VALUES (?, ?, ?, ?, ?, 1)
        """, (
            company_id,
            product_id,
            unit_id,
            barcode,
            is_primary
        ))

        barcode_id = cur.lastrowid

        conn.execute("""
            INSERT INTO audit_log (
                user,
                action,
                details
            )
            VALUES (?, ?, ?)
        """, (
            str(user_id or ""),
            "CREATE_PRODUCT_BARCODE",
            f"barcode_id={barcode_id}, product_id={product_id}, barcode={barcode}"
        ))

        conn.commit()

        return jsonify({
            "success": True,
            "id": barcode_id,
            "product_id": product_id,
            "unit_id": unit_id,
            "barcode": barcode,
            "is_primary": is_primary
        }), 201

    except db.IntegrityError as exc:
        conn.rollback()

        if "product_barcodes" in str(exc):
            return jsonify({
                "error": "تعذر إضافة الباركود لأنه مستخدم بالفعل"
            }), 409

        return jsonify({
            "error": "تعذر حفظ الباركود"
        }), 400

    finally:
        conn.close()


@app.route("/api/product-barcodes/<int:barcode_id>", methods=["PUT"])
@require_write
def api_product_barcode_update(barcode_id):
    company_id = _current_company_id()
    user_id = session.get("user_id")


    data = request.get_json(silent=True) or {}

    barcode = str(data.get("barcode") or "").strip()
    unit_id = data.get("unit_id")
    is_primary = 1 if data.get("is_primary") else 0
    is_active = 1 if data.get("is_active", True) else 0

    if not barcode:
        return jsonify({"error": "الباركود مطلوب"}), 400

    if len(barcode) > 100:
        return jsonify({"error": "الباركود طويل جدًا"}), 400

    if unit_id in (None, "", 0):
        unit_id = None
    else:
        try:
            unit_id = int(unit_id)
        except (TypeError, ValueError):
            return jsonify({"error": "رقم الوحدة غير صحيح"}), 400

    conn = db.connect()

    try:
        old = conn.execute("""
            SELECT *
            FROM product_barcodes
            WHERE id = ?
              AND company_id = ?
        """, (barcode_id, company_id)).fetchone()

        if not old:
            return jsonify({"error": "الباركود غير موجود"}), 404

        product_id = old["product_id"]

        if unit_id is not None:
            unit = conn.execute("""
                SELECT id
                FROM units
                WHERE id = ?
                  AND company_id = ?
                  AND is_active = 1
            """, (unit_id, company_id)).fetchone()

            if not unit:
                return jsonify({"error": "الوحدة غير موجودة أو غير نشطة"}), 404

        duplicate = conn.execute("""
            SELECT id
            FROM product_barcodes
            WHERE company_id = ?
              AND barcode = ?
              AND id <> ?
        """, (company_id, barcode, barcode_id)).fetchone()

        if duplicate:
            return jsonify({"error": "هذا الباركود مستخدم بالفعل"}), 409

        if is_primary:
            conn.execute("""
                UPDATE product_barcodes
                SET is_primary = 0
                WHERE company_id = ?
                  AND product_id = ?
            """, (company_id, product_id))

        conn.execute("""
            UPDATE product_barcodes
            SET
                barcode = ?,
                unit_id = ?,
                is_primary = ?,
                is_active = ?
            WHERE id = ?
              AND company_id = ?
        """, (
            barcode,
            unit_id,
            is_primary,
            is_active,
            barcode_id,
            company_id
        ))

        conn.execute("""
            INSERT INTO audit_log (
                user,
                action,
                details
            )
            VALUES (?, ?, ?)
        """, (
            str(user_id or ""),
            "UPDATE_PRODUCT_BARCODE",
            f"barcode_id={barcode_id}, product_id={product_id}, barcode={barcode}"
        ))

        conn.commit()

        return jsonify({
            "success": True,
            "id": barcode_id,
            "product_id": product_id,
            "unit_id": unit_id,
            "barcode": barcode,
            "is_primary": is_primary,
            "is_active": is_active
        })

    finally:
        conn.close()


@app.route("/api/product-barcodes/<int:barcode_id>", methods=["DELETE"])
@require_write
def api_product_barcode_delete(barcode_id):
    company_id = _current_company_id()
    user_id = session.get("user_id")

    conn = db.connect()

    try:
        row = conn.execute("""
            SELECT *
            FROM product_barcodes
            WHERE id = ?
              AND company_id = ?
        """, (barcode_id, company_id)).fetchone()

        if not row:
            return jsonify({"error": "الباركود غير موجود"}), 404

        conn.execute("""
            UPDATE product_barcodes
            SET is_active = 0,
                is_primary = 0
            WHERE id = ?
              AND company_id = ?
        """, (barcode_id, company_id))

        conn.execute("""
            INSERT INTO audit_log (
                user,
                action,
                details
            )
            VALUES (?, ?, ?)
        """, (
            str(user_id or ""),
            "DISABLE_PRODUCT_BARCODE",
            f"barcode_id={barcode_id}, product_id={row['product_id']}, barcode={row['barcode']}"
        ))

        conn.commit()

        return jsonify({
            "success": True,
            "message": "تم تعطيل الباركود"
        })

    finally:
        conn.close()


@app.route("/api/product-barcodes/lookup", methods=["GET"])
@require_write
def api_product_barcode_lookup():
    company_id = _current_company_id()

    barcode = str(request.args.get("barcode") or "").strip()

    if not barcode:
        return jsonify({"error": "الباركود مطلوب"}), 400

    conn = db.connect()

    try:
        row = conn.execute("""
            SELECT
                pb.id AS barcode_id,
                pb.barcode,
                pb.product_id,
                pb.unit_id AS barcode_unit_id,
                p.code AS product_code,
                p.name AS product_name,
                p.base_unit_id,
                p.allow_decimal_qty,
                p.is_stock_item,
                u.code AS unit_code,
                u.name AS unit_name,
                u.symbol AS unit_symbol,
                COALESCE((
                    SELECT pp.price
                    FROM product_prices pp
                    WHERE pp.company_id = pb.company_id
                      AND pp.product_id = pb.product_id
                      AND pp.unit_id = COALESCE(pb.unit_id, p.base_unit_id)
                      AND pp.currency_id = (
                          SELECT si.currency_id
                          FROM sales_invoices si
                          WHERE si.company_id = pb.company_id
                            AND si.status = 'draft'
                          ORDER BY si.id DESC
                          LIMIT 1
                      )
                      AND pp.price_type = 'sale'
                      AND pp.is_active = 1
                    ORDER BY pp.is_default DESC, pp.id DESC
                    LIMIT 1
                ), 0) AS sale_price
            FROM product_barcodes pb
            JOIN products p
              ON p.id = pb.product_id
            LEFT JOIN units u
              ON u.id = pb.unit_id
            WHERE pb.company_id = ?
              AND pb.barcode = ?
              AND pb.is_active = 1
              AND p.is_active = 1
            LIMIT 1
        """, (company_id, barcode)).fetchone()

        if not row:
            return jsonify({
                "found": False,
                "error": "الباركود غير معروف"
            }), 404

        result = dict(row)

        if result["barcode_unit_id"] is None:
            result["unit_id"] = result["base_unit_id"]
        else:
            result["unit_id"] = result["barcode_unit_id"]

        result["sale_price"] = float(result.get("sale_price") or 0)

        return jsonify({
            "found": True,
            "item": result
        })

    finally:
        conn.close()


# ============================================================
# PRODUCT PRICING API
# ============================================================

@app.route("/api/product-prices", methods=["GET"])
def api_product_prices_list():
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    product_id = request.args.get("product_id")
    unit_id = request.args.get("unit_id")
    currency_id = request.args.get("currency_id")
    price_type = (request.args.get("price_type") or "").strip().lower()

    where = ["pp.company_id = ?"]
    args = [company_id]

    if product_id:
        try:
            where.append("pp.product_id = ?")
            args.append(int(product_id))
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "product_id غير صالح"
            }), 400

    if unit_id:
        try:
            where.append("pp.unit_id = ?")
            args.append(int(unit_id))
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "unit_id غير صالح"
            }), 400

    if currency_id:
        try:
            where.append("pp.currency_id = ?")
            args.append(int(currency_id))
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "currency_id غير صالح"
            }), 400

    if price_type:
        if price_type not in ("sale", "purchase"):
            return jsonify({
                "ok": False,
                "error": "price_type يجب أن يكون sale أو purchase"
            }), 400
        where.append("pp.price_type = ?")
        args.append(price_type)

    rows = db.query(f"""
        SELECT
            pp.id,
            pp.company_id,
            pp.product_id,
            p.code AS product_code,
            p.name AS product_name,
            pp.unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,
            pp.currency_id,
            c.code AS currency_code,
            c.name AS currency_name,
            c.symbol AS currency_symbol,
            pp.price_type,
            pp.price,
            pp.is_default,
            pp.is_active,
            pp.created_at,
            pp.updated_at
        FROM product_prices pp
        JOIN products p
          ON p.id = pp.product_id
         AND p.company_id = pp.company_id
        JOIN units u
          ON u.id = pp.unit_id
         AND u.company_id = pp.company_id
        JOIN currencies c
          ON c.id = pp.currency_id
        WHERE {' AND '.join(where)}
        ORDER BY
            pp.product_id,
            pp.unit_id,
            pp.price_type,
            pp.currency_id
    """, tuple(args))

    return jsonify({
        "ok": True,
        "rows": rows
    })


@app.route("/api/product-prices", methods=["POST"])
@require_write
def api_product_price_create():
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id"))
        unit_id = int(data.get("unit_id"))
        currency_id = int(data.get("currency_id"))
        price = float(data.get("price", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "بيانات المنتج أو الوحدة أو العملة أو السعر غير صالحة"
        }), 400

    price_type = (data.get("price_type") or "").strip().lower()

    is_default = 1 if data.get("is_default", True) else 0
    is_active = 1 if data.get("is_active", True) else 0

    if price_type not in ("sale", "purchase"):
        return jsonify({
            "ok": False,
            "error": "price_type يجب أن يكون sale أو purchase"
        }), 400

    if price < 0:
        return jsonify({
            "ok": False,
            "error": "السعر لا يمكن أن يكون سالبًا"
        }), 400

    with db.connect() as conn:

        product = conn.execute("""
            SELECT id, code, name, base_unit_id, is_active
            FROM products
            WHERE id = ?
              AND company_id = ?
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "المنتج غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if not product["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير نشط"
            }), 400

        unit = conn.execute("""
            SELECT id, code, name, symbol, is_active
            FROM units
            WHERE id = ?
              AND company_id = ?
        """, (unit_id, company_id)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "الوحدة غير موجودة أو لا تتبع الشركة الحالية"
            }), 404

        if not unit["is_active"]:
            return jsonify({
                "ok": False,
                "error": "الوحدة غير نشطة"
            }), 400

        currency = conn.execute("""
            SELECT id, code, name, symbol, is_active
            FROM currencies
            WHERE id = ?
        """, (currency_id,)).fetchone()

        if not currency:
            return jsonify({
                "ok": False,
                "error": "العملة غير موجودة"
            }), 404

        if not currency["is_active"]:
            return jsonify({
                "ok": False,
                "error": "العملة غير نشطة"
            }), 400

        existing = conn.execute("""
            SELECT id
            FROM product_prices
            WHERE company_id = ?
              AND product_id = ?
              AND unit_id = ?
              AND currency_id = ?
              AND price_type = ?
        """, (
            company_id,
            product_id,
            unit_id,
            currency_id,
            price_type
        )).fetchone()

        if existing:
            return jsonify({
                "ok": False,
                "error": "يوجد سعر مسجل بالفعل لهذا المنتج والوحدة والعملة ونوع السعر",
                "price_id": existing["id"]
            }), 409

        cur = conn.execute("""
            INSERT INTO product_prices (
                company_id,
                product_id,
                unit_id,
                currency_id,
                price_type,
                price,
                is_default,
                is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            company_id,
            product_id,
            unit_id,
            currency_id,
            price_type,
            price,
            is_default,
            is_active
        ))

        price_id = cur.lastrowid
        conn.commit()

    db.audit(
        g.user["full_name"],
        "إضافة سعر منتج",
        f"product_id={product_id}, unit_id={unit_id}, "
        f"currency_id={currency_id}, type={price_type}, price={price}"
    )

    return jsonify({
        "ok": True,
        "price_id": price_id,
        "product_id": product_id,
        "unit_id": unit_id,
        "currency_id": currency_id,
        "price_type": price_type,
        "price": price
    }), 201


@app.route("/api/product-prices/<int:price_id>", methods=["PUT"])
@require_write
def api_product_price_update(price_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    with db.connect() as conn:

        old = conn.execute("""
            SELECT *
            FROM product_prices
            WHERE id = ?
              AND company_id = ?
        """, (price_id, company_id)).fetchone()

        if not old:
            return jsonify({
                "ok": False,
                "error": "سجل السعر غير موجود"
            }), 404

        product_id = old["product_id"]
        unit_id = old["unit_id"]
        currency_id = old["currency_id"]
        price_type = old["price_type"]

        if "product_id" in data:
            try:
                product_id = int(data["product_id"])
            except (TypeError, ValueError):
                return jsonify({
                    "ok": False,
                    "error": "product_id غير صالح"
                }), 400

        if "unit_id" in data:
            try:
                unit_id = int(data["unit_id"])
            except (TypeError, ValueError):
                return jsonify({
                    "ok": False,
                    "error": "unit_id غير صالح"
                }), 400

        if "currency_id" in data:
            try:
                currency_id = int(data["currency_id"])
            except (TypeError, ValueError):
                return jsonify({
                    "ok": False,
                    "error": "currency_id غير صالح"
                }), 400

        if "price_type" in data:
            price_type = (data.get("price_type") or "").strip().lower()

        try:
            price = (
                float(data["price"])
                if "price" in data
                else float(old["price"])
            )
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "السعر غير صالح"
            }), 400

        is_default = (
            1 if data.get("is_default") else 0
        ) if "is_default" in data else int(old["is_default"])

        is_active = (
            1 if data.get("is_active") else 0
        ) if "is_active" in data else int(old["is_active"])

        if price_type not in ("sale", "purchase"):
            return jsonify({
                "ok": False,
                "error": "price_type يجب أن يكون sale أو purchase"
            }), 400

        if price < 0:
            return jsonify({
                "ok": False,
                "error": "السعر لا يمكن أن يكون سالبًا"
            }), 400

        product = conn.execute("""
            SELECT id
            FROM products
            WHERE id = ?
              AND company_id = ?
              AND is_active = 1
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "المنتج غير موجود أو غير نشط"
            }), 404

        unit = conn.execute("""
            SELECT id
            FROM units
            WHERE id = ?
              AND company_id = ?
              AND is_active = 1
        """, (unit_id, company_id)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "الوحدة غير موجودة أو غير نشطة"
            }), 404

        currency = conn.execute("""
            SELECT id
            FROM currencies
            WHERE id = ?
              AND is_active = 1
        """, (currency_id,)).fetchone()

        if not currency:
            return jsonify({
                "ok": False,
                "error": "العملة غير موجودة أو غير نشطة"
            }), 404

        duplicate = conn.execute("""
            SELECT id
            FROM product_prices
            WHERE company_id = ?
              AND product_id = ?
              AND unit_id = ?
              AND currency_id = ?
              AND price_type = ?
              AND id <> ?
        """, (
            company_id,
            product_id,
            unit_id,
            currency_id,
            price_type,
            price_id
        )).fetchone()

        if duplicate:
            return jsonify({
                "ok": False,
                "error": "يوجد سعر آخر بنفس بيانات المنتج والوحدة والعملة والنوع"
            }), 409

        conn.execute("""
            UPDATE product_prices
            SET
                product_id = ?,
                unit_id = ?,
                currency_id = ?,
                price_type = ?,
                price = ?,
                is_default = ?,
                is_active = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (
            product_id,
            unit_id,
            currency_id,
            price_type,
            price,
            is_default,
            is_active,
            price_id,
            company_id
        ))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "تعديل سعر منتج",
        f"price_id={price_id}, product_id={product_id}, "
        f"type={price_type}, price={price}"
    )

    return jsonify({
        "ok": True,
        "price_id": price_id,
        "price": price,
        "price_type": price_type,
        "is_active": is_active
    })


@app.route("/api/product-prices/<int:price_id>", methods=["DELETE"])
@require_write
def api_product_price_delete(price_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    with db.connect() as conn:

        row = conn.execute("""
            SELECT id, product_id, unit_id, price_type, price
            FROM product_prices
            WHERE id = ?
              AND company_id = ?
        """, (price_id, company_id)).fetchone()

        if not row:
            return jsonify({
                "ok": False,
                "error": "سجل السعر غير موجود"
            }), 404

        conn.execute("""
            UPDATE product_prices
            SET
                is_active = 0,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (price_id, company_id))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "تعطيل سعر منتج",
        f"price_id={price_id}, product_id={row['product_id']}, "
        f"type={row['price_type']}, price={row['price']}"
    )

    return jsonify({
        "ok": True,
        "price_id": price_id,
        "is_active": 0
    })


@app.route("/api/product-prices/lookup", methods=["GET"])
def api_product_price_lookup():
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    try:
        product_id = int(request.args.get("product_id"))
        unit_id = int(request.args.get("unit_id"))
        currency_id = int(request.args.get("currency_id"))
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "product_id و unit_id و currency_id مطلوبة"
        }), 400

    price_type = (
        request.args.get("price_type") or "sale"
    ).strip().lower()

    if price_type not in ("sale", "purchase"):
        return jsonify({
            "ok": False,
            "error": "price_type يجب أن يكون sale أو purchase"
        }), 400

    row = db.query_one("""
        SELECT
            pp.id,
            pp.product_id,
            pp.unit_id,
            pp.currency_id,
            pp.price_type,
            pp.price,
            pp.is_default,
            pp.is_active
        FROM product_prices pp
        WHERE pp.company_id = ?
          AND pp.product_id = ?
          AND pp.unit_id = ?
          AND pp.currency_id = ?
          AND pp.price_type = ?
          AND pp.is_active = 1
        ORDER BY pp.is_default DESC, pp.id DESC
        LIMIT 1
    """, (
        company_id,
        product_id,
        unit_id,
        currency_id,
        price_type
    ))

    if not row:
        return jsonify({
            "ok": True,
            "found": False,
            "price": 0
        })

    return jsonify({
        "ok": True,
        "found": True,
        "price": float(row["price"] or 0),
        "price_id": row["id"],
        "price_type": row["price_type"]
    })


# ============================================================
# END PRODUCT BARCODE API
# ============================================================


@app.route("/api/products", methods=["GET"])
def api_products():
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    rows = db.query(
        """
        SELECT
            p.id,
            p.company_id,
            p.category_id,
            pc.code AS category_code,
            pc.name AS category_name,
            p.code,
            p.name,
            p.base_unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,
            u.unit_type,
            u.decimal_places,
            p.description,
            p.brand,
            p.model,
            p.specifications,
            p.is_stock_item,
            p.allow_decimal_qty,
            p.track_expiry,
            p.track_lot,
            p.track_serial,
            p.min_stock,
            p.max_stock,
            p.is_active,
            p.created_at,
            p.updated_at
        FROM products p
        LEFT JOIN product_categories pc
               ON pc.id = p.category_id
              AND pc.company_id = p.company_id
        INNER JOIN units u
                ON u.id = p.base_unit_id
               AND u.company_id = p.company_id
        WHERE p.company_id=?
        ORDER BY p.code, p.id
        """,
        (company_id,),
    )

    return jsonify(items=rows)


@app.route("/api/products", methods=["POST"])
@require_write
def api_product_create():
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    d = _sanitize_dict(request.json or {})

    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or "").strip()

    if not code:
        return jsonify(error="كود المنتج مطلوب"), 400

    if not name:
        return jsonify(error="اسم المنتج مطلوب"), 400

    try:
        base_unit_id = int(d.get("base_unit_id"))
    except (TypeError, ValueError):
        return jsonify(error="وحدة القياس الأساسية مطلوبة"), 400

    category_id = d.get("category_id")

    if category_id in ("", None):
        category_id = None
    else:
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return jsonify(error="التصنيف غير صحيح"), 400

        category = db.query_one(
            """
            SELECT id
            FROM product_categories
            WHERE id=? AND company_id=?
            """,
            (category_id, company_id),
        )

        if not category:
            return jsonify(error="التصنيف غير موجود في الشركة"), 400

    unit = db.query_one(
        """
        SELECT id
        FROM units
        WHERE id=? AND company_id=?
        """,
        (base_unit_id, company_id),
    )

    if not unit:
        return jsonify(error="وحدة القياس غير موجودة في الشركة"), 400

    existing = db.query_one(
        """
        SELECT id
        FROM products
        WHERE company_id=? AND code=?
        """,
        (company_id, code),
    )

    if existing:
        return jsonify(error="كود المنتج مستخدم مسبقًا"), 409

    description = str(d.get("description") or "").strip()
    brand = str(d.get("brand") or "").strip()
    model = str(d.get("model") or "").strip()
    specifications = str(d.get("specifications") or "").strip()

    def bool_value(value, default):
        if value is None:
            return default
        if isinstance(value, bool):
            return 1 if value else 0
        if str(value).strip().lower() in ("1", "true", "yes", "on"):
            return 1
        if str(value).strip().lower() in ("0", "false", "no", "off"):
            return 0
        return default

    is_stock_item = bool_value(d.get("is_stock_item"), 1)
    allow_decimal_qty = bool_value(d.get("allow_decimal_qty"), 1)
    track_expiry = bool_value(d.get("track_expiry"), 0)
    track_lot = bool_value(d.get("track_lot"), 0)
    track_serial = bool_value(d.get("track_serial"), 0)

    try:
        min_stock = float(d.get("min_stock", 0) or 0)
        max_stock = float(d.get("max_stock", 0) or 0)
    except (TypeError, ValueError):
        return jsonify(error="حدود المخزون يجب أن تكون أرقامًا"), 400

    if min_stock < 0 or max_stock < 0:
        return jsonify(error="حدود المخزون لا يمكن أن تكون سالبة"), 400

    if max_stock > 0 and max_stock < min_stock:
        return jsonify(
            error="الحد الأقصى للمخزون لا يمكن أن يكون أقل من الحد الأدنى"
        ), 400

    try:
        new_id = db.execute(
            """
            INSERT INTO products (
                company_id,
                category_id,
                code,
                name,
                base_unit_id,
                description,
                brand,
                model,
                specifications,
                is_stock_item,
                allow_decimal_qty,
                track_expiry,
                track_lot,
                track_serial,
                min_stock,
                max_stock
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                category_id,
                code,
                name,
                base_unit_id,
                description,
                brand,
                model,
                specifications,
                is_stock_item,
                allow_decimal_qty,
                track_expiry,
                track_lot,
                track_serial,
                min_stock,
                max_stock,
            ),
        )
    except db.IntegrityError:
        return jsonify(
            error="تعذر إضافة المنتج بسبب تعارض في البيانات"
        ), 400

    db.audit(
        g.user["full_name"],
        "إضافة منتج",
        f"{code} - {name}",
    )

    item = db.query_one(
        """
        SELECT
            p.id,
            p.category_id,
            pc.code AS category_code,
            pc.name AS category_name,
            p.code,
            p.name,
            p.base_unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,
            u.unit_type,
            u.decimal_places,
            p.description,
            p.brand,
            p.model,
            p.specifications,
            p.is_stock_item,
            p.allow_decimal_qty,
            p.track_expiry,
            p.track_lot,
            p.track_serial,
            p.min_stock,
            p.max_stock,
            p.is_active,
            p.created_at,
            p.updated_at
        FROM products p
        LEFT JOIN product_categories pc
               ON pc.id = p.category_id
              AND pc.company_id = p.company_id
        INNER JOIN units u
                ON u.id = p.base_unit_id
               AND u.company_id = p.company_id
        WHERE p.id=? AND p.company_id=?
        """,
        (new_id, company_id),
    )

    return jsonify(ok=True, item=item), 201


@app.route("/api/products/<int:product_id>", methods=["PUT"])
@require_write
def api_product_update(product_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    product = db.query_one(
        """
        SELECT *
        FROM products
        WHERE id=? AND company_id=?
        """,
        (product_id, company_id),
    )

    if not product:
        return jsonify(error="المنتج غير موجود"), 404

    d = _sanitize_dict(request.json or {})

    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or "").strip()

    if not code:
        return jsonify(error="كود المنتج مطلوب"), 400

    if not name:
        return jsonify(error="اسم المنتج مطلوب"), 400

    try:
        base_unit_id = int(d.get("base_unit_id"))
    except (TypeError, ValueError):
        return jsonify(error="وحدة القياس الأساسية مطلوبة"), 400

    category_id = d.get("category_id")

    if category_id in ("", None):
        category_id = None
    else:
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return jsonify(error="التصنيف غير صحيح"), 400

        category = db.query_one(
            """
            SELECT id
            FROM product_categories
            WHERE id=? AND company_id=?
            """,
            (category_id, company_id),
        )

        if not category:
            return jsonify(error="التصنيف غير موجود في الشركة"), 400

    unit = db.query_one(
        """
        SELECT id
        FROM units
        WHERE id=? AND company_id=?
        """,
        (base_unit_id, company_id),
    )

    if not unit:
        return jsonify(error="وحدة القياس غير موجودة في الشركة"), 400

    existing = db.query_one(
        """
        SELECT id
        FROM products
        WHERE company_id=? AND code=? AND id<>?
        """,
        (company_id, code, product_id),
    )

    if existing:
        return jsonify(error="كود المنتج مستخدم مسبقًا"), 409

    description = str(d.get("description") or "").strip()
    brand = str(d.get("brand") or "").strip()
    model = str(d.get("model") or "").strip()
    specifications = str(d.get("specifications") or "").strip()

    def bool_value(value, default):
        if value is None:
            return default
        if isinstance(value, bool):
            return 1 if value else 0
        if str(value).strip().lower() in ("1", "true", "yes", "on"):
            return 1
        if str(value).strip().lower() in ("0", "false", "no", "off"):
            return 0
        return default

    is_stock_item = bool_value(
        d.get("is_stock_item"),
        product["is_stock_item"],
    )

    allow_decimal_qty = bool_value(
        d.get("allow_decimal_qty"),
        product["allow_decimal_qty"],
    )

    track_expiry = bool_value(
        d.get("track_expiry"),
        product["track_expiry"],
    )

    track_lot = bool_value(
        d.get("track_lot"),
        product["track_lot"],
    )

    track_serial = bool_value(
        d.get("track_serial"),
        product["track_serial"],
    )

    try:
        min_stock = float(
            d.get("min_stock", product["min_stock"]) or 0
        )
        max_stock = float(
            d.get("max_stock", product["max_stock"]) or 0
        )
    except (TypeError, ValueError):
        return jsonify(error="حدود المخزون يجب أن تكون أرقامًا"), 400

    if min_stock < 0 or max_stock < 0:
        return jsonify(error="حدود المخزون لا يمكن أن تكون سالبة"), 400

    if max_stock > 0 and max_stock < min_stock:
        return jsonify(
            error="الحد الأقصى للمخزون لا يمكن أن يكون أقل من الحد الأدنى"
        ), 400

    try:
        db.execute(
            """
            UPDATE products
            SET
                category_id=?,
                code=?,
                name=?,
                base_unit_id=?,
                description=?,
                brand=?,
                model=?,
                specifications=?,
                is_stock_item=?,
                allow_decimal_qty=?,
                track_expiry=?,
                track_lot=?,
                track_serial=?,
                min_stock=?,
                max_stock=?,
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=?
            """,
            (
                category_id,
                code,
                name,
                base_unit_id,
                description,
                brand,
                model,
                specifications,
                is_stock_item,
                allow_decimal_qty,
                track_expiry,
                track_lot,
                track_serial,
                min_stock,
                max_stock,
                product_id,
                company_id,
            ),
        )
    except db.IntegrityError:
        return jsonify(
            error="تعذر تعديل المنتج بسبب تعارض في البيانات"
        ), 400

    db.audit(
        g.user["full_name"],
        "تعديل منتج",
        f"{code} - {name}",
    )

    item = db.query_one(
        """
        SELECT
            p.id,
            p.category_id,
            pc.code AS category_code,
            pc.name AS category_name,
            p.code,
            p.name,
            p.base_unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,
            u.unit_type,
            u.decimal_places,
            p.description,
            p.brand,
            p.model,
            p.specifications,
            p.is_stock_item,
            p.allow_decimal_qty,
            p.track_expiry,
            p.track_lot,
            p.track_serial,
            p.min_stock,
            p.max_stock,
            p.is_active,
            p.created_at,
            p.updated_at
        FROM products p
        LEFT JOIN product_categories pc
               ON pc.id = p.category_id
              AND pc.company_id = p.company_id
        INNER JOIN units u
                ON u.id = p.base_unit_id
               AND u.company_id = p.company_id
        WHERE p.id=? AND p.company_id=?
        """,
        (product_id, company_id),
    )

    return jsonify(ok=True, item=item)


@app.route("/api/products/<int:product_id>/deactivate", methods=["POST"])
@require_write
def api_product_deactivate(product_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    product = db.query_one(
        """
        SELECT id, code, name
        FROM products
        WHERE id=? AND company_id=?
        """,
        (product_id, company_id),
    )

    if not product:
        return jsonify(error="المنتج غير موجود"), 404

    db.execute(
        """
        UPDATE products
        SET
            is_active=0,
            updated_at=datetime('now','localtime')
        WHERE id=? AND company_id=?
        """,
        (product_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تعطيل منتج",
        f"{product['code']} - {product['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/products/<int:product_id>/activate", methods=["POST"])
@require_write
def api_product_activate(product_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    product = db.query_one(
        """
        SELECT id, code, name
        FROM products
        WHERE id=? AND company_id=?
        """,
        (product_id, company_id),
    )

    if not product:
        return jsonify(error="المنتج غير موجود"), 404

    db.execute(
        """
        UPDATE products
        SET
            is_active=1,
            updated_at=datetime('now','localtime')
        WHERE id=? AND company_id=?
        """,
        (product_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تفعيل منتج",
        f"{product['code']} - {product['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
@admin_required
def api_product_delete(product_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    product = db.query_one(
        """
        SELECT id, code, name
        FROM products
        WHERE id=? AND company_id=?
        """,
        (product_id, company_id),
    )

    if not product:
        return jsonify(error="المنتج غير موجود"), 404

    try:
        db.execute(
            """
            DELETE FROM products
            WHERE id=? AND company_id=?
            """,
            (product_id, company_id),
        )
    except db.IntegrityError:
        return jsonify(
            error="لا يمكن حذف المنتج لأنه مرتبط ببيانات أخرى"
        ), 409

    db.audit(
        g.user["full_name"],
        "حذف منتج",
        f"{product['code']} - {product['name']}",
    )

    return jsonify(ok=True)



@app.route("/warehouses")
def warehouses_page():
    return render_template("warehouses.html")


@app.route("/products")
def products_page():
    return render_template("products.html")

# ------------------------------------------------------------------
# Warehouses API — المخازن
# ------------------------------------------------------------------

@app.route("/api/warehouses", methods=["GET"])
def api_warehouses_list():
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    search = (request.args.get("search") or "").strip()
    status = (request.args.get("status") or "all").strip()

    sql = """
        SELECT
            w.id,
            w.company_id,
            w.code,
            w.name,
            w.description,
            w.address,
            w.phone,
            w.manager_name,
            w.is_active,
            w.created_at,
            w.updated_at
        FROM warehouses w
        WHERE w.company_id=?
    """

    args = [company_id]

    if search:
        sql += """
            AND (
                w.code LIKE ?
                OR w.name LIKE ?
                OR w.address LIKE ?
                OR w.phone LIKE ?
                OR w.manager_name LIKE ?
            )
        """
        term = f"%{search}%"
        args.extend([term, term, term, term, term])

    if status == "active":
        sql += " AND w.is_active=1"
    elif status == "inactive":
        sql += " AND w.is_active=0"

    sql += " ORDER BY w.id DESC"

    rows = db.query(sql, tuple(args))

    return jsonify(rows=rows)


@app.route("/api/warehouses", methods=["POST"])
@require_write
def api_warehouse_create():
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400


    data = request.get_json(silent=True) or {}

    code = str(data.get("code") or "").strip()
    name = str(data.get("name") or "").strip()

    if not code:
        return jsonify(error="كود المخزن مطلوب"), 400

    if not name:
        return jsonify(error="اسم المخزن مطلوب"), 400

    description = str(data.get("description") or "").strip()
    address = str(data.get("address") or "").strip()
    phone = str(data.get("phone") or "").strip()
    manager_name = str(data.get("manager_name") or "").strip()

    existing = db.query_one(
        """
        SELECT id
        FROM warehouses
        WHERE company_id=? AND code=?
        """,
        (company_id, code),
    )

    if existing:
        return jsonify(error="كود المخزن مستخدم بالفعل"), 409

    try:
        warehouse_id = db.execute(
            """
            INSERT INTO warehouses (
                company_id,
                code,
                name,
                description,
                address,
                phone,
                manager_name,
                is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                company_id,
                code,
                name,
                description,
                address,
                phone,
                manager_name,
            ),
        )
    except db.IntegrityError:
        return jsonify(error="تعذر إنشاء المخزن بسبب بيانات مرتبطة"), 409

    db.audit(
        g.user["full_name"],
        "إضافة مخزن",
        f"{code} - {name}",
    )

    warehouse = db.query_one(
        """
        SELECT
            id,
            company_id,
            code,
            name,
            description,
            address,
            phone,
            manager_name,
            is_active,
            created_at,
            updated_at
        FROM warehouses
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    return jsonify(warehouse=warehouse), 201


@app.route("/api/warehouses/<int:warehouse_id>", methods=["PUT"])
@require_write
def api_warehouse_update(warehouse_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    warehouse = db.query_one(
        """
        SELECT *
        FROM warehouses
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    if not warehouse:
        return jsonify(error="المخزن غير موجود"), 404


    data = request.get_json(silent=True) or {}

    code = str(data.get("code") or "").strip()
    name = str(data.get("name") or "").strip()

    if not code:
        return jsonify(error="كود المخزن مطلوب"), 400

    if not name:
        return jsonify(error="اسم المخزن مطلوب"), 400

    description = str(data.get("description") or "").strip()
    address = str(data.get("address") or "").strip()
    phone = str(data.get("phone") or "").strip()
    manager_name = str(data.get("manager_name") or "").strip()

    duplicate = db.query_one(
        """
        SELECT id
        FROM warehouses
        WHERE company_id=? AND code=? AND id<>?
        """,
        (company_id, code, warehouse_id),
    )

    if duplicate:
        return jsonify(error="كود المخزن مستخدم بالفعل"), 409

    try:
        db.execute(
            """
            UPDATE warehouses
            SET
                code=?,
                name=?,
                description=?,
                address=?,
                phone=?,
                manager_name=?,
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=?
            """,
            (
                code,
                name,
                description,
                address,
                phone,
                manager_name,
                warehouse_id,
                company_id,
            ),
        )
    except db.IntegrityError:
        return jsonify(error="تعذر تعديل المخزن بسبب بيانات مرتبطة"), 409

    db.audit(
        g.user["full_name"],
        "تعديل مخزن",
        f"{code} - {name}",
    )

    warehouse = db.query_one(
        """
        SELECT
            id,
            company_id,
            code,
            name,
            description,
            address,
            phone,
            manager_name,
            is_active,
            created_at,
            updated_at
        FROM warehouses
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    return jsonify(warehouse=warehouse)


@app.route("/api/warehouses/<int:warehouse_id>/deactivate", methods=["POST"])
@require_write
def api_warehouse_deactivate(warehouse_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    warehouse = db.query_one(
        """
        SELECT id, code, name, is_active
        FROM warehouses
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    if not warehouse:
        return jsonify(error="المخزن غير موجود"), 404

    db.execute(
        """
        UPDATE warehouses
        SET
            is_active=0,
            updated_at=datetime('now','localtime')
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تعطيل مخزن",
        f"{warehouse['code']} - {warehouse['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/warehouses/<int:warehouse_id>/activate", methods=["POST"])
@require_write
def api_warehouse_activate(warehouse_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    warehouse = db.query_one(
        """
        SELECT id, code, name, is_active
        FROM warehouses
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    if not warehouse:
        return jsonify(error="المخزن غير موجود"), 404

    db.execute(
        """
        UPDATE warehouses
        SET
            is_active=1,
            updated_at=datetime('now','localtime')
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    db.audit(
        g.user["full_name"],
        "تفعيل مخزن",
        f"{warehouse['code']} - {warehouse['name']}",
    )

    return jsonify(ok=True)


@app.route("/api/warehouses/<int:warehouse_id>", methods=["DELETE"])
@require_write
def api_warehouse_delete(warehouse_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    warehouse = db.query_one(
        """
        SELECT id, code, name
        FROM warehouses
        WHERE id=? AND company_id=?
        """,
        (warehouse_id, company_id),
    )

    if not warehouse:
        return jsonify(error="المخزن غير موجود"), 404

    try:
        db.execute(
            """
            DELETE FROM warehouses
            WHERE id=? AND company_id=?
            """,
            (warehouse_id, company_id),
        )
    except db.IntegrityError:
        return jsonify(
            error="لا يمكن حذف المخزن لأنه مرتبط ببيانات أخرى"
        ), 409

    db.audit(
        g.user["full_name"],
        "حذف مخزن",
        f"{warehouse['code']} - {warehouse['name']}",
    )

    return jsonify(ok=True)



# ------------------------------------------------------------------

# =========================
# Stock Balances API
# =========================

@app.route("/api/stock-balances", methods=["GET"])
def api_stock_balances():
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    search = (request.args.get("search") or "").strip()
    warehouse_id = request.args.get("warehouse_id")
    product_id = request.args.get("product_id")

    sql = """
        SELECT
            ws.id,
            ws.company_id,
            ws.warehouse_id,
            w.code AS warehouse_code,
            w.name AS warehouse_name,
            ws.product_id,
            p.code AS product_code,
            p.name AS product_name,
            ws.quantity,
            p.base_unit_id AS unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,
            ws.created_at,
            ws.updated_at
        FROM warehouse_stock ws
        JOIN warehouses w
            ON w.id = ws.warehouse_id
        JOIN products p
            ON p.id = ws.product_id
        JOIN units u
            ON u.id = p.base_unit_id
        WHERE ws.company_id = ?
    """

    args = [company_id]

    if warehouse_id:
        try:
            warehouse_id = int(warehouse_id)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "warehouse_id غير صالح"
            }), 400

        sql += " AND ws.warehouse_id = ?"
        args.append(warehouse_id)

    if product_id:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "product_id غير صالح"
            }), 400

        sql += " AND ws.product_id = ?"
        args.append(product_id)

    if search:
        sql += """
            AND (
                p.code LIKE ?
                OR p.name LIKE ?
                OR w.code LIKE ?
                OR w.name LIKE ?
            )
        """

        like = f"%{search}%"
        args.extend([like, like, like, like])

    sql += """
        ORDER BY
            w.name COLLATE NOCASE,
            p.name COLLATE NOCASE,
            ws.id
    """

    rows = db.query(sql, args)

    return jsonify({
        "ok": True,
        "count": len(rows),
        "rows": rows
    })



# =========================
# Stock Movements API
# =========================

@app.route("/api/stock-movements", methods=["GET"])
def api_stock_movements():
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    search = (request.args.get("search") or "").strip()
    warehouse_id = request.args.get("warehouse_id")
    product_id = request.args.get("product_id")
    movement_type = (request.args.get("movement_type") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()

    sql = """
        SELECT
            sm.id,
            sm.company_id,

            sm.warehouse_id,
            w.code AS warehouse_code,
            w.name AS warehouse_name,

            sm.product_id,
            p.code AS product_code,
            p.name AS product_name,

            sm.unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,

            sm.movement_type,
            sm.quantity,

            sm.reference_type,
            sm.reference_id,

            sm.movement_date,
            sm.notes,

            sm.created_by,
            usr.username AS created_by_username,
            usr.full_name AS created_by_name,

            sm.created_at

        FROM stock_movements sm

        JOIN warehouses w
            ON w.id = sm.warehouse_id

        JOIN products p
            ON p.id = sm.product_id

        JOIN units u
            ON u.id = sm.unit_id

        LEFT JOIN users usr
            ON usr.id = sm.created_by

        WHERE sm.company_id = ?
    """

    args = [company_id]

    if warehouse_id:
        try:
            warehouse_id = int(warehouse_id)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "warehouse_id غير صالح"
            }), 400

        sql += " AND sm.warehouse_id = ?"
        args.append(warehouse_id)

    if product_id:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "product_id غير صالح"
            }), 400

        sql += " AND sm.product_id = ?"
        args.append(product_id)

    if movement_type:
        sql += " AND sm.movement_type = ?"
        args.append(movement_type)

    if date_from:
        sql += " AND date(sm.movement_date) >= date(?)"
        args.append(date_from)

    if date_to:
        sql += " AND date(sm.movement_date) <= date(?)"
        args.append(date_to)

    if search:
        sql += """
            AND (
                p.code LIKE ?
                OR p.name LIKE ?
                OR w.code LIKE ?
                OR w.name LIKE ?
                OR sm.notes LIKE ?
            )
        """

        like = f"%{search}%"
        args.extend([like, like, like, like, like])

    sql += """
        ORDER BY
            sm.movement_date DESC,
            sm.id DESC
    """

    rows = db.query(sql, args)

    return jsonify({
        "ok": True,
        "count": len(rows),
        "rows": rows
    })



# =========================
# Stock Movement Create API
# =========================

@app.route("/api/stock-movements", methods=["POST"])
@require_write
def api_stock_movement_create():

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    warehouse_id = data.get("warehouse_id")
    product_id = data.get("product_id")
    movement_type = (data.get("movement_type") or "").strip().lower()
    quantity = data.get("quantity")
    reference_type = (data.get("reference_type") or "").strip()
    reference_id = data.get("reference_id")
    movement_date = (data.get("movement_date") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not warehouse_id:
        return jsonify({
            "ok": False,
            "error": "المخزن مطلوب"
        }), 400

    if not product_id:
        return jsonify({
            "ok": False,
            "error": "المنتج مطلوب"
        }), 400

    if movement_type not in ("in", "out"):
        return jsonify({
            "ok": False,
            "error": "نوع الحركة يجب أن يكون in أو out"
        }), 400

    try:
        warehouse_id = int(warehouse_id)
        product_id = int(product_id)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "المخزن أو المنتج غير صالح"
        }), 400

    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "الكمية غير صالحة"
        }), 400

    if quantity <= 0:
        return jsonify({
            "ok": False,
            "error": "الكمية يجب أن تكون أكبر من صفر"
        }), 400

    with db.connect() as conn:

        warehouse = conn.execute("""
            SELECT id, code, name, is_active
            FROM warehouses
            WHERE id = ?
              AND company_id = ?
        """, (warehouse_id, company_id)).fetchone()

        if not warehouse:
            return jsonify({
                "ok": False,
                "error": "المخزن غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if not warehouse["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المخزن غير نشط"
            }), 400

        product = conn.execute("""
            SELECT
                id,
                code,
                name,
                base_unit_id,
                is_active,
                is_stock_item,
                allow_decimal_qty
            FROM products
            WHERE id = ?
              AND company_id = ?
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "المنتج غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if not product["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير نشط"
            }), 400

        if not product["is_stock_item"]:
            return jsonify({
                "ok": False,
                "error": "هذا المنتج غير معرف كصنف مخزني"
            }), 400

        if not product["allow_decimal_qty"]:
            if not float(quantity).is_integer():
                return jsonify({
                    "ok": False,
                    "error": "هذا المنتج لا يسمح بكميات عشرية"
                }), 400

        unit = conn.execute("""
            SELECT id, code, name, symbol, decimal_places
            FROM units
            WHERE id = ?
        """, (product["base_unit_id"],)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "وحدة المنتج غير موجودة"
            }), 400

        stock = conn.execute("""
            SELECT id, quantity
            FROM warehouse_stock
            WHERE company_id = ?
              AND warehouse_id = ?
              AND product_id = ?
        """, (company_id, warehouse_id, product_id)).fetchone()

        current_quantity = float(stock["quantity"]) if stock else 0.0

        if movement_type == "out":
            if quantity > current_quantity:
                return jsonify({
                    "ok": False,
                    "error": "الكمية المطلوبة للصرف أكبر من الرصيد الحالي",
                    "current_quantity": current_quantity
                }), 400

            new_quantity = current_quantity - quantity
        else:
            new_quantity = current_quantity + quantity

        if movement_date:
            movement_date_value = movement_date
        else:
            movement_date_value = None

        if stock:
            conn.execute("""
                UPDATE warehouse_stock
                SET
                    quantity = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
            """, (new_quantity, stock["id"]))
        else:
            conn.execute("""
                INSERT INTO warehouse_stock (
                    company_id,
                    warehouse_id,
                    product_id,
                    quantity
                )
                VALUES (?, ?, ?, ?)
            """, (
                company_id,
                warehouse_id,
                product_id,
                new_quantity
            ))

        if movement_date_value:
            cur = conn.execute("""
                INSERT INTO stock_movements (
                    company_id,
                    warehouse_id,
                    product_id,
                    unit_id,
                    movement_type,
                    quantity,
                    reference_type,
                    reference_id,
                    movement_date,
                    notes,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                company_id,
                warehouse_id,
                product_id,
                product["base_unit_id"],
                movement_type,
                quantity,
                reference_type,
                reference_id,
                movement_date_value,
                notes,
                g.user["id"]
            ))
        else:
            cur = conn.execute("""
                INSERT INTO stock_movements (
                    company_id,
                    warehouse_id,
                    product_id,
                    unit_id,
                    movement_type,
                    quantity,
                    reference_type,
                    reference_id,
                    notes,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                company_id,
                warehouse_id,
                product_id,
                product["base_unit_id"],
                movement_type,
                quantity,
                reference_type,
                reference_id,
                notes,
                g.user["id"]
            ))

        movement_id = cur.lastrowid

        conn.commit()

    db.audit(
        g.user["full_name"],
        "حركة مخزون",
        f"{movement_type} - {product['code']} - {quantity}"
    )

    return jsonify({
        "ok": True,
        "movement_id": movement_id,
        "movement_type": movement_type,
        "quantity": quantity,
        "previous_quantity": current_quantity,
        "new_quantity": new_quantity
    }), 201


@app.route("/api/purchases/<int:purchase_id>/items/<int:item_id>", methods=["PUT"])
@require_write
def api_purchase_item_update(purchase_id, item_id):

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id"))
        unit_id = int(data.get("unit_id"))
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "المنتج والوحدة مطلوبان"
        }), 400

    try:
        quantity = float(data.get("quantity"))
        unit_price = float(data.get("unit_price", 0) or 0)
        discount = float(data.get("discount", 0) or 0)
        tax_rate = float(data.get("tax_rate", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "الكمية والسعر والخصم والضريبة يجب أن تكون أرقامًا"
        }), 400

    if not all(math.isfinite(value) for value in (quantity, unit_price, discount, tax_rate)):
        return jsonify({
            "ok": False,
            "error": "القيم الرقمية يجب أن تكون أرقامًا محدودة وصالحة"
        }), 400

    notes = (data.get("notes") or "").strip()

    if quantity <= 0:
        return jsonify({
            "ok": False,
            "error": "الكمية يجب أن تكون أكبر من صفر"
        }), 400

    if unit_price < 0:
        return jsonify({
            "ok": False,
            "error": "سعر الوحدة لا يمكن أن يكون سالبًا"
        }), 400

    if discount < 0:
        return jsonify({
            "ok": False,
            "error": "الخصم لا يمكن أن يكون سالبًا"
        }), 400

    if tax_rate < 0:
        return jsonify({
            "ok": False,
            "error": "نسبة الضريبة لا يمكن أن تكون سالبة"
        }), 400

    with db.connect() as conn:

        invoice = conn.execute("""
            SELECT
                id,
                company_id,
                status
            FROM purchase_invoices
            WHERE id = ?
              AND company_id = ?
        """, (purchase_id, company_id)).fetchone()

        if not invoice:
            return jsonify({
                "ok": False,
                "error": "فاتورة الشراء غير موجودة"
            }), 404

        if invoice["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن تعديل فاتورة ليست في حالة مسودة"
            }), 400

        existing_item = conn.execute("""
            SELECT id
            FROM purchase_invoice_items
            WHERE id = ?
              AND invoice_id = ?
        """, (item_id, purchase_id)).fetchone()

        if not existing_item:
            return jsonify({
                "ok": False,
                "error": "صنف الفاتورة غير موجود"
            }), 404

        product = conn.execute("""
            SELECT
                id,
                code,
                name,
                base_unit_id,
                is_active,
                is_stock_item,
                allow_decimal_qty
            FROM products
            WHERE id = ?
              AND company_id = ?
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "المنتج غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if not product["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير نشط"
            }), 400

        if not product["is_stock_item"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير معرف كصنف مخزني"
            }), 400

        unit = conn.execute("""
            SELECT
                id,
                code,
                name,
                symbol,
                decimal_places
            FROM units
            WHERE id = ?
              AND company_id = ?
        """, (unit_id, company_id)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "وحدة القياس غير موجودة أو لا تتبع الشركة الحالية"
            }), 404

        if unit_id != product["base_unit_id"]:
            return jsonify({
                "ok": False,
                "error": "الوحدة الحالية يجب أن تكون وحدة المنتج الأساسية"
            }), 400

        if not product["allow_decimal_qty"] and quantity != int(quantity):
            return jsonify({
                "ok": False,
                "error": "هذا المنتج لا يسمح بالكميات العشرية"
            }), 400

        decimal_places = int(unit["decimal_places"] or 0)
        rounded_quantity = round(quantity, decimal_places)

        if abs(quantity - rounded_quantity) > 1e-9:
            return jsonify({
                "ok": False,
                "error": f"الكمية لا تتوافق مع عدد الخانات العشرية للوحدة ({decimal_places})"
            }), 400

        gross = round(quantity * unit_price, 2)

        if discount > gross:
            return jsonify({
                "ok": False,
                "error": "الخصم لا يمكن أن يتجاوز قيمة الصنف"
            }), 400

        net = round(gross - discount, 2)
        tax_amount = round(net * tax_rate / 100, 2)
        line_total = round(net + tax_amount, 2)
        if not all(math.isfinite(value) for value in (gross, net, tax_amount, line_total)):
            return jsonify({"ok": False, "error": "نتيجة حساب الصنف خارج النطاق الرقمي المسموح"}), 400

        conn.execute("""
            UPDATE purchase_invoice_items
            SET
                product_id = ?,
                unit_id = ?,
                quantity = ?,
                unit_price = ?,
                discount = ?,
                tax_rate = ?,
                tax_amount = ?,
                line_total = ?,
                notes = ?
            WHERE id = ?
              AND invoice_id = ?
        """, (
            product_id,
            unit_id,
            quantity,
            unit_price,
            discount,
            tax_rate,
            tax_amount,
            line_total,
            notes,
            item_id,
            purchase_id
        ))

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM purchase_invoice_items
            WHERE invoice_id = ?
        """, (purchase_id,)).fetchone()

        subtotal = round(float(totals["subtotal"] or 0), 2)
        discount_total = round(float(totals["discount_total"] or 0), 2)
        tax_total = round(float(totals["tax_total"] or 0), 2)
        grand_total = round(float(totals["grand_total"] or 0), 2)
        if not all(math.isfinite(value) for value in (subtotal, discount_total, tax_total, grand_total)):
            conn.rollback()
            return jsonify({"ok": False, "error": "إجماليات الفاتورة خارج النطاق الرقمي المسموح"}), 400

        current_paid = conn.execute("""
            SELECT paid_amount
            FROM purchase_invoices
            WHERE id = ?
              AND company_id = ?
        """, (purchase_id, company_id)).fetchone()

        paid_amount = float(current_paid["paid_amount"] or 0)

        if paid_amount > grand_total:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "لا يمكن جعل إجمالي الفاتورة أقل من المبلغ المدفوع"
            }), 400

        remaining_amount = round(grand_total - paid_amount, 2)

        conn.execute("""
            UPDATE purchase_invoices
            SET
                subtotal = ?,
                discount_total = ?,
                tax_total = ?,
                grand_total = ?,
                remaining_amount = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            remaining_amount,
            purchase_id,
            company_id
        ))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "تعديل صنف في فاتورة شراء",
        f"فاتورة {purchase_id} - الصنف {item_id}"
    )

    return jsonify({
        "ok": True,
        "item_id": item_id,
        "purchase_id": purchase_id,
        "quantity": quantity,
        "unit_price": unit_price,
        "discount": discount,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "line_total": line_total,
        "totals": {
            "subtotal": subtotal,
            "discount_total": discount_total,
            "tax_total": tax_total,
            "grand_total": grand_total,
            "remaining_amount": remaining_amount
        }
    })


@app.route("/api/purchases/<int:purchase_id>/items/<int:item_id>", methods=["DELETE"])
@require_write
def api_purchase_item_delete(purchase_id, item_id):

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    with db.connect() as conn:

        invoice = conn.execute("""
            SELECT
                id,
                company_id,
                status,
                paid_amount
            FROM purchase_invoices
            WHERE id = ?
              AND company_id = ?
        """, (purchase_id, company_id)).fetchone()

        if not invoice:
            return jsonify({
                "ok": False,
                "error": "فاتورة الشراء غير موجودة"
            }), 404

        if invoice["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن حذف صنف من فاتورة ليست في حالة مسودة"
            }), 400

        item = conn.execute("""
            SELECT id
            FROM purchase_invoice_items
            WHERE id = ?
              AND invoice_id = ?
        """, (item_id, purchase_id)).fetchone()

        if not item:
            return jsonify({
                "ok": False,
                "error": "صنف الفاتورة غير موجود"
            }), 404

        conn.execute("""
            DELETE FROM purchase_invoice_items
            WHERE id = ?
              AND invoice_id = ?
        """, (item_id, purchase_id))

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM purchase_invoice_items
            WHERE invoice_id = ?
        """, (purchase_id,)).fetchone()

        subtotal = float(totals["subtotal"] or 0)
        discount_total = float(totals["discount_total"] or 0)
        tax_total = float(totals["tax_total"] or 0)
        grand_total = float(totals["grand_total"] or 0)
        if not all(math.isfinite(value) for value in (subtotal, discount_total, tax_total, grand_total)):
            conn.rollback()
            return jsonify({"ok": False, "error": "إجماليات الفاتورة خارج النطاق الرقمي المسموح"}), 400

        paid_amount = float(invoice["paid_amount"] or 0)

        if paid_amount > grand_total:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "لا يمكن حذف الصنف لأن المبلغ المدفوع أكبر من الإجمالي الجديد"
            }), 400

        remaining_amount = round(grand_total - paid_amount, 2)

        conn.execute("""
            UPDATE purchase_invoices
            SET
                subtotal = ?,
                discount_total = ?,
                tax_total = ?,
                grand_total = ?,
                remaining_amount = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            remaining_amount,
            purchase_id,
            company_id
        ))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "حذف صنف من فاتورة شراء",
        f"فاتورة {purchase_id} - الصنف {item_id}"
    )

    return jsonify({
        "ok": True,
        "item_id": item_id,
        "purchase_id": purchase_id,
        "totals": {
            "subtotal": subtotal,
            "discount_total": discount_total,
            "tax_total": tax_total,
            "grand_total": grand_total,
            "remaining_amount": remaining_amount
        }
    })



# =========================
# Purchase Invoice Items API
# =========================

@app.route("/api/purchases/<int:purchase_id>/items", methods=["GET"])
def api_purchase_items_list(purchase_id):

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    invoice = db.query_one("""
        SELECT id, invoice_no, status
        FROM purchase_invoices
        WHERE id = ?
          AND company_id = ?
    """, (purchase_id, company_id))

    if not invoice:
        return jsonify({
            "ok": False,
            "error": "فاتورة الشراء غير موجودة"
        }), 404

    rows = db.query("""
        SELECT
            pii.id,
            pii.invoice_id,
            pii.product_id,
            p.code AS product_code,
            p.name AS product_name,
            pii.unit_id,
            u.code AS unit_code,
            u.name AS unit_name,
            u.symbol AS unit_symbol,
            u.decimal_places,
            pii.quantity,
            pii.unit_price,
            pii.discount,
            pii.tax_rate,
            pii.tax_amount,
            pii.line_total,
            pii.notes
        FROM purchase_invoice_items pii
        JOIN products p
            ON p.id = pii.product_id
        JOIN units u
            ON u.id = pii.unit_id
        WHERE pii.invoice_id = ?
        ORDER BY pii.id
    """, (purchase_id,))

    return jsonify({
        "ok": True,
        "purchase_id": purchase_id,
        "invoice_no": invoice["invoice_no"],
        "status": invoice["status"],
        "rows": rows
    })


@app.route("/api/purchases/<int:purchase_id>/items", methods=["POST"])
@require_write
def api_purchase_item_create(purchase_id):

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id"))
        unit_id = int(data.get("unit_id"))
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "المنتج والوحدة مطلوبان"
        }), 400

    try:
        quantity = float(data.get("quantity"))
        unit_price = float(data.get("unit_price", 0) or 0)
        discount = float(data.get("discount", 0) or 0)
        tax_rate = float(data.get("tax_rate", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "الكمية والسعر والخصم والضريبة يجب أن تكون أرقامًا"
        }), 400

    if not all(math.isfinite(value) for value in (quantity, unit_price, discount, tax_rate)):
        return jsonify({
            "ok": False,
            "error": "القيم الرقمية يجب أن تكون أرقامًا محدودة وصالحة"
        }), 400

    notes = (data.get("notes") or "").strip()

    if quantity <= 0:
        return jsonify({
            "ok": False,
            "error": "الكمية يجب أن تكون أكبر من صفر"
        }), 400

    if unit_price < 0:
        return jsonify({
            "ok": False,
            "error": "سعر الوحدة لا يمكن أن يكون سالبًا"
        }), 400

    if discount < 0:
        return jsonify({
            "ok": False,
            "error": "الخصم لا يمكن أن يكون سالبًا"
        }), 400

    if tax_rate < 0:
        return jsonify({
            "ok": False,
            "error": "نسبة الضريبة لا يمكن أن تكون سالبة"
        }), 400

    with db.connect() as conn:

        invoice = conn.execute("""
            SELECT
                id,
                company_id,
                status
            FROM purchase_invoices
            WHERE id = ?
              AND company_id = ?
        """, (purchase_id, company_id)).fetchone()

        if not invoice:
            return jsonify({
                "ok": False,
                "error": "فاتورة الشراء غير موجودة"
            }), 404

        if invoice["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن تعديل فاتورة ليست في حالة مسودة"
            }), 400

        product = conn.execute("""
            SELECT
                id,
                code,
                name,
                base_unit_id,
                is_active,
                is_stock_item,
                allow_decimal_qty
            FROM products
            WHERE id = ?
              AND company_id = ?
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "المنتج غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if not product["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير نشط"
            }), 400

        if not product["is_stock_item"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير معرف كصنف مخزني"
            }), 400

        unit = conn.execute("""
            SELECT
                id,
                code,
                name,
                symbol,
                decimal_places
            FROM units
            WHERE id = ?
              AND company_id = ?
        """, (unit_id, company_id)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "وحدة القياس غير موجودة أو لا تتبع الشركة الحالية"
            }), 404

        if unit_id != product["base_unit_id"]:
            return jsonify({
                "ok": False,
                "error": "الوحدة الحالية يجب أن تكون وحدة المنتج الأساسية"
            }), 400

        if not product["allow_decimal_qty"] and quantity != int(quantity):
            return jsonify({
                "ok": False,
                "error": "هذا المنتج لا يسمح بالكميات العشرية"
            }), 400

        decimal_places = int(unit["decimal_places"] or 0)

        if decimal_places >= 0:
            rounded_quantity = round(quantity, decimal_places)

            if abs(quantity - rounded_quantity) > 1e-9:
                return jsonify({
                    "ok": False,
                    "error": f"الكمية لا تتوافق مع عدد الخانات العشرية للوحدة ({decimal_places})"
                }), 400

        gross = round(quantity * unit_price, 2)

        if discount > gross:
            return jsonify({
                "ok": False,
                "error": "الخصم لا يمكن أن يتجاوز قيمة الصنف"
            }), 400

        net = round(gross - discount, 2)
        tax_amount = round(net * tax_rate / 100, 2)
        line_total = round(net + tax_amount, 2)
        if not all(math.isfinite(value) for value in (gross, net, tax_amount, line_total)):
            return jsonify({"ok": False, "error": "نتيجة حساب الصنف خارج النطاق الرقمي المسموح"}), 400

        cur = conn.execute("""
            INSERT INTO purchase_invoice_items (
                invoice_id,
                product_id,
                unit_id,
                quantity,
                unit_price,
                discount,
                tax_rate,
                tax_amount,
                line_total,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            purchase_id,
            product_id,
            unit_id,
            quantity,
            unit_price,
            discount,
            tax_rate,
            tax_amount,
            line_total,
            notes
        ))

        item_id = cur.lastrowid

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM purchase_invoice_items
            WHERE invoice_id = ?
        """, (purchase_id,)).fetchone()

        subtotal = round(float(totals["subtotal"] or 0), 2)
        discount_total = round(float(totals["discount_total"] or 0), 2)
        tax_total = round(float(totals["tax_total"] or 0), 2)
        grand_total = round(float(totals["grand_total"] or 0), 2)
        if not all(math.isfinite(value) for value in (subtotal, discount_total, tax_total, grand_total)):
            conn.rollback()
            return jsonify({"ok": False, "error": "إجماليات الفاتورة خارج النطاق الرقمي المسموح"}), 400
        paid_row = conn.execute("SELECT paid_amount FROM purchase_invoices WHERE id = ? AND company_id = ?", (purchase_id, company_id)).fetchone()
        paid_amount = round(float(paid_row["paid_amount"] or 0), 2)
        if paid_amount > grand_total:
            conn.rollback()
            return jsonify({"ok": False, "error": "لا يمكن جعل إجمالي الفاتورة أقل من المبلغ المدفوع"}), 400
        remaining_amount = round(grand_total - paid_amount, 2)

        conn.execute("""
            UPDATE purchase_invoices
            SET
                subtotal = ?,
                discount_total = ?,
                tax_total = ?,
                grand_total = ?,
                remaining_amount = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            remaining_amount,
            purchase_id,
            company_id
        ))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "إضافة صنف إلى فاتورة شراء",
        f"فاتورة {purchase_id} - صنف {product['code']} - كمية {quantity}"
    )

    return jsonify({
        "ok": True,
        "item_id": item_id,
        "purchase_id": purchase_id,
        "quantity": quantity,
        "unit_price": unit_price,
        "discount": discount,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "line_total": line_total,
        "totals": {
            "subtotal": subtotal,
            "discount_total": discount_total,
            "tax_total": tax_total,
            "grand_total": grand_total,
            "remaining_amount": remaining_amount
        }
    }), 201



@app.route("/purchases")
def purchases_page():
    if not getattr(g, "user", None):
        return redirect(url_for("login"))
    return render_template("purchases.html")


@app.route("/sales")

def sales_page():
    if not getattr(g, "user", None):
        return redirect(url_for("login"))
    return render_template("sales.html")

@app.route("/delivery")
def delivery_page():
    return render_template("delivery.html")

@app.route("/delivery/pending")
def delivery_pending_page():
    if not getattr(g, "user", None):
        return redirect(url_for("login"))
    return render_template("delivery_pending.html")


@app.route("/api/currencies", methods=["GET"])
def api_currencies_list():
    if not getattr(g, "user", None):
        return jsonify({"error": "غير مصرح"}), 401

    rows = db.query("""
        SELECT
            id,
            code,
            name,
            symbol,
            decimal_places,
            is_base,
            is_active
        FROM currencies
        WHERE is_active = 1
        ORDER BY is_base DESC, id
    """)

    return jsonify({"rows": rows})


# =========================
# Purchase Invoices API
# =========================

@app.route("/api/purchases", methods=["GET"])
def api_purchases_list():

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    rows = db.query("""
        SELECT
            pi.id,
            pi.invoice_no,
            pi.supplier_invoice_no,
            pi.invoice_date,
            pi.payment_method,
            pi.status,
            pi.currency_id,
            c.code AS currency_code,
            c.name AS currency_name,
            c.symbol AS currency_symbol,
            pi.exchange_rate,
            pi.subtotal,
            pi.discount_total,
            pi.tax_total,
            pi.grand_total,
            pi.paid_amount,
            pi.remaining_amount,
            pi.notes,
            pi.created_at,
            pi.updated_at,
            bp.id AS supplier_id,
            bp.code AS supplier_code,
            bp.name AS supplier_name,
            w.id AS warehouse_id,
            w.code AS warehouse_code,
            w.name AS warehouse_name
        FROM purchase_invoices pi
        JOIN business_partners bp
            ON bp.id = pi.supplier_id
        LEFT JOIN currencies c
            ON c.id = pi.currency_id
        LEFT JOIN warehouses w
            ON w.id = pi.warehouse_id
        WHERE pi.company_id = ?
        ORDER BY pi.id DESC
    """, (company_id,))

    return jsonify({
        "ok": True,
        "rows": rows
    })


@app.route("/api/purchases", methods=["POST"])
@require_write
def api_purchase_create():

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    supplier_id = data.get("supplier_id")
    branch_id = data.get("branch_id")
    warehouse_id = data.get("warehouse_id")
    fiscal_year_id = data.get("fiscal_year_id")
    period_id = data.get("period_id")
    currency_id = data.get("currency_id") or 3
    exchange_rate = data.get("exchange_rate", 1)

    invoice_no = (data.get("invoice_no") or "").strip()
    supplier_invoice_no = (data.get("supplier_invoice_no") or "").strip()
    invoice_date = (data.get("invoice_date") or "").strip()
    payment_method = (data.get("payment_method") or "credit").strip().lower()
    notes = (data.get("notes") or "").strip()

    if not supplier_id:
        return jsonify({
            "ok": False,
            "error": "المورد مطلوب"
        }), 400

    if not invoice_date:
        return jsonify({
            "ok": False,
            "error": "تاريخ الفاتورة مطلوب"
        }), 400

    if payment_method not in ("credit", "cash", "bank_ahly", "bank_misr"):
        return jsonify({
            "ok": False,
            "error": "طريقة الدفع غير صحيحة"
        }), 400

    try:
        supplier_id = int(supplier_id)
        branch_id = int(branch_id) if branch_id else None
        warehouse_id = int(warehouse_id) if warehouse_id else None
        fiscal_year_id = int(fiscal_year_id) if fiscal_year_id else None
        period_id = int(period_id) if period_id else None
        currency_id = int(currency_id) if currency_id else None
        exchange_rate = float(exchange_rate)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "أحد الحقول الرقمية غير صالح"
        }), 400

    if exchange_rate <= 0:
        return jsonify({
            "ok": False,
            "error": "سعر الصرف يجب أن يكون أكبر من صفر"
        }), 400

    with db.connect() as conn:

        supplier = conn.execute("""
            SELECT
                id,
                code,
                name,
                partner_type,
                payable_account_id,
                is_active
            FROM business_partners
            WHERE id = ?
              AND company_id = ?
        """, (supplier_id, company_id)).fetchone()

        if not supplier:
            return jsonify({
                "ok": False,
                "error": "المورد غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if supplier["partner_type"] != "supplier":
            return jsonify({
                "ok": False,
                "error": "الشريك المحدد ليس موردًا"
            }), 400

        if not supplier["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المورد غير نشط"
            }), 400

        if not supplier["payable_account_id"]:
            return jsonify({
                "ok": False,
                "error": "المورد غير مرتبط بحساب دائن"
            }), 400

        if branch_id:
            branch = conn.execute("""
                SELECT id, is_active
                FROM branches
                WHERE id = ?
                  AND company_id = ?
            """, (branch_id, company_id)).fetchone()

            if not branch:
                return jsonify({
                    "ok": False,
                    "error": "الفرع غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if not branch["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "الفرع غير نشط"
                }), 400

        if warehouse_id:
            warehouse = conn.execute("""
                SELECT id, is_active
                FROM warehouses
                WHERE id = ?
                  AND company_id = ?
            """, (warehouse_id, company_id)).fetchone()

            if not warehouse:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if not warehouse["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير نشط"
                }), 400

        if fiscal_year_id:
            fiscal_year = conn.execute("""
                SELECT id, status, date_from, date_to
                FROM fiscal_years
                WHERE id = ?
                  AND company_id = ?
            """, (fiscal_year_id, company_id)).fetchone()

            if not fiscal_year:
                return jsonify({
                    "ok": False,
                    "error": "السنة المالية غير موجودة"
                }), 404

            if fiscal_year["status"] != "open":
                return jsonify({
                    "ok": False,
                    "error": "السنة المالية مغلقة"
                }), 400

        if period_id:
            period = conn.execute("""
                SELECT
                    id,
                    fiscal_year_id,
                    status,
                    date_from,
                    date_to
                FROM accounting_periods
                WHERE id = ?
            """, (period_id,)).fetchone()

            if not period:
                return jsonify({
                    "ok": False,
                    "error": "الفترة المحاسبية غير موجودة"
                }), 404

            if period["status"] != "open":
                return jsonify({
                    "ok": False,
                    "error": "الفترة المحاسبية مغلقة"
                }), 400

        if not currency_id:
            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE is_base = 1
                  AND is_active = 1
                ORDER BY id
                LIMIT 1
            """).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "لا توجد عملة أساسية نشطة"
                }), 400

            currency_id = currency["id"]
        else:
            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE id = ?
                  AND is_active = 1
            """, (currency_id,)).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "العملة غير موجودة أو غير نشطة"
                }), 404

        if not invoice_no:
            year_text = invoice_date[:4]

            last_invoice = conn.execute("""
                SELECT invoice_no
                FROM purchase_invoices
                WHERE company_id = ?
                  AND invoice_no LIKE ?
                ORDER BY id DESC
                LIMIT 1
            """, (
                company_id,
                f"PUR-{year_text}-%"
            )).fetchone()

            next_number = 1

            if last_invoice and last_invoice["invoice_no"]:
                try:
                    next_number = int(
                        last_invoice["invoice_no"].rsplit("-", 1)[1]
                    ) + 1
                except (ValueError, IndexError):
                    next_number = 1

            invoice_no = f"PUR-{year_text}-{next_number:06d}"

        duplicate = conn.execute("""
            SELECT id
            FROM purchase_invoices
            WHERE company_id = ?
              AND invoice_no = ?
        """, (company_id, invoice_no)).fetchone()

        if duplicate:
            return jsonify({
                "ok": False,
                "error": "رقم فاتورة الشراء مستخدم بالفعل"
            }), 409

        cur = conn.execute("""
            INSERT INTO purchase_invoices (
                company_id,
                branch_id,
                supplier_id,
                warehouse_id,
                fiscal_year_id,
                period_id,
                currency_id,
                exchange_rate,
                invoice_no,
                supplier_invoice_no,
                invoice_date,
                payment_method,
                status,
                subtotal,
                discount_total,
                tax_total,
                grand_total,
                paid_amount,
                remaining_amount,
                notes,
                created_by
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, 'draft',
                0, 0, 0, 0, 0, 0,
                ?, ?
            )
        """, (
            company_id,
            branch_id,
            supplier_id,
            warehouse_id,
            fiscal_year_id,
            period_id,
            currency_id,
            exchange_rate,
            invoice_no,
            supplier_invoice_no,
            invoice_date,
            payment_method,
            notes,
            g.user["id"]
        ))

        purchase_id = cur.lastrowid

        conn.commit()

    db.audit(
        g.user["full_name"],
        "إنشاء مسودة فاتورة شراء",
        f"{invoice_no} - {supplier['name']}"
    )

    return jsonify({
        "ok": True,
        "purchase_id": purchase_id,
        "invoice_no": invoice_no,
        "status": "draft"
    }), 201


@app.route("/api/purchases/<int:purchase_id>", methods=["PUT"])
@require_write
def api_purchase_update(purchase_id):

    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    supplier_id = data.get("supplier_id")
    warehouse_id = data.get("warehouse_id")
    currency_id = data.get("currency_id") or 3
    exchange_rate = data.get("exchange_rate", 1)

    supplier_invoice_no = (
        data.get("supplier_invoice_no") or ""
    ).strip()

    invoice_date = (
        data.get("invoice_date") or ""
    ).strip()

    payment_method = (
        data.get("payment_method") or ""
    ).strip().lower()

    notes = (
        data.get("notes") or ""
    ).strip()

    if not supplier_id:
        return jsonify({
            "ok": False,
            "error": "المورد مطلوب"
        }), 400

    if not invoice_date:
        return jsonify({
            "ok": False,
            "error": "تاريخ الفاتورة مطلوب"
        }), 400

    if payment_method not in (
        "credit",
        "cash",
        "bank_ahly",
        "bank_misr"
    ):
        return jsonify({
            "ok": False,
            "error": "طريقة الدفع غير صحيحة"
        }), 400

    try:
        supplier_id = int(supplier_id)
        warehouse_id = (
            int(warehouse_id)
            if warehouse_id
            else None
        )
        currency_id = (
            int(currency_id)
            if currency_id
            else None
        )
        exchange_rate = float(exchange_rate)

    except (TypeError, ValueError):

        return jsonify({
            "ok": False,
            "error": "أحد الحقول الرقمية غير صالح"
        }), 400

    if exchange_rate <= 0:
        return jsonify({
            "ok": False,
            "error": "سعر الصرف يجب أن يكون أكبر من صفر"
        }), 400

    with db.connect() as conn:

        purchase = conn.execute("""
            SELECT *
            FROM purchase_invoices
            WHERE id = ?
              AND company_id = ?
        """, (
            purchase_id,
            company_id
        )).fetchone()

        if not purchase:
            return jsonify({
                "ok": False,
                "error": "فاتورة الشراء غير موجودة"
            }), 404

        if purchase["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن تعديل فاتورة غير مسودة"
            }), 400

        supplier = conn.execute("""
            SELECT
                id,
                name,
                partner_type,
                payable_account_id,
                is_active
            FROM business_partners
            WHERE id = ?
              AND company_id = ?
        """, (
            supplier_id,
            company_id
        )).fetchone()

        if not supplier:
            return jsonify({
                "ok": False,
                "error": "المورد غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if supplier["partner_type"] != "supplier":
            return jsonify({
                "ok": False,
                "error": "الشريك المحدد ليس موردًا"
            }), 400

        if not supplier["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المورد غير نشط"
            }), 400

        if not supplier["payable_account_id"]:
            return jsonify({
                "ok": False,
                "error": "المورد غير مرتبط بحساب دائن"
            }), 400

        if warehouse_id:

            warehouse = conn.execute("""
                SELECT id, is_active
                FROM warehouses
                WHERE id = ?
                  AND company_id = ?
            """, (
                warehouse_id,
                company_id
            )).fetchone()

            if not warehouse:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if not warehouse["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير نشط"
                }), 400

        if not currency_id:

            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE is_base = 1
                  AND is_active = 1
                ORDER BY id
                LIMIT 1
            """).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "لا توجد عملة أساسية نشطة"
                }), 400

            currency_id = currency["id"]

        else:

            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE id = ?
                  AND is_active = 1
            """, (
                currency_id,
            )).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "العملة غير موجودة أو غير نشطة"
                }), 404

        conn.execute("""
            UPDATE purchase_invoices
            SET
                supplier_id = ?,
                warehouse_id = ?,
                currency_id = ?,
                exchange_rate = ?,
                supplier_invoice_no = ?,
                invoice_date = ?,
                payment_method = ?,
                notes = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (
            supplier_id,
            warehouse_id,
            currency_id,
            exchange_rate,
            supplier_invoice_no,
            invoice_date,
            payment_method,
            notes,
            purchase_id,
            company_id
        ))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "تعديل مسودة فاتورة شراء",
        f"{purchase['invoice_no']} - {supplier['name']}"
    )

    return jsonify({
        "ok": True,
        "purchase_id": purchase_id,
        "invoice_no": purchase["invoice_no"],
        "status": "draft"
    }), 200


@app.route("/api/purchases/<int:purchase_id>/approve", methods=["POST"])
@require_write
def api_purchase_approve(purchase_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    conn = db.connect()

    try:
        with conn:
            purchase = conn.execute("""
                SELECT
                    pi.*,
                    bp.name AS supplier_name,
                    bp.payable_account_id,
                    w.name AS warehouse_name
                FROM purchase_invoices pi
                JOIN business_partners bp
                  ON bp.id = pi.supplier_id
                 AND bp.company_id = pi.company_id
                LEFT JOIN warehouses w
                  ON w.id = pi.warehouse_id
                 AND w.company_id = pi.company_id
                WHERE pi.id = ?
                  AND pi.company_id = ?
            """, (purchase_id, company_id)).fetchone()

            if not purchase:
                return jsonify({
                    "ok": False,
                    "error": "فاتورة الشراء غير موجودة"
                }), 404

            if purchase["status"] != "draft":
                return jsonify({
                    "ok": False,
                    "error": "لا يمكن اعتماد فاتورة غير مسودة"
                }), 400

            if not purchase["warehouse_id"]:
                return jsonify({
                    "ok": False,
                    "error": "يجب تحديد المخزن قبل اعتماد فاتورة الشراء"
                }), 400

            warehouse = conn.execute("""
                SELECT id, name, is_active
                FROM warehouses
                WHERE id = ?
                  AND company_id = ?
            """, (
                purchase["warehouse_id"],
                company_id
            )).fetchone()

            if not warehouse:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if not warehouse["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير نشط"
                }), 400

            if not purchase["payable_account_id"]:
                return jsonify({
                    "ok": False,
                    "error": "المورد غير مرتبط بحساب دائن"
                }), 400

            period = _period_for_date(
                purchase["invoice_date"],
                company_id
            )

            if not period:
                return jsonify({
                    "ok": False,
                    "error": "لا توجد فترة محاسبية تغطي تاريخ الفاتورة"
                }), 400

            period_error = _open_period_error(
                purchase["invoice_date"]
            )

            if period_error:
                return jsonify({
                    "ok": False,
                    "error": period_error
                }), 400

            items = conn.execute("""
                SELECT
                    pii.*,
                    p.name AS product_name,
                    p.code AS product_code,
                    p.base_unit_id,
                    p.is_stock_item,
                    p.allow_decimal_qty
                FROM purchase_invoice_items pii
                JOIN products p
                  ON p.id = pii.product_id
                 AND p.company_id = ?
                WHERE pii.invoice_id = ?
                ORDER BY pii.id
            """, (
                company_id,
                purchase_id
            )).fetchall()

            if not items:
                return jsonify({
                    "ok": False,
                    "error": "لا يمكن اعتماد فاتورة بدون أصناف"
                }), 400

            subtotal = 0.0
            discount_total = 0.0
            tax_total = 0.0
            grand_total = 0.0

            for item in items:
                if not item["is_stock_item"]:
                    return jsonify({
                        "ok": False,
                        "error": f"الصنف {item['product_name']} غير معرف كصنف مخزني"
                    }), 400

                quantity = float(item["quantity"] or 0)
                unit_price = float(item["unit_price"] or 0)
                discount = float(item["discount"] or 0)
                tax_rate = float(item["tax_rate"] or 0)

                if quantity <= 0:
                    return jsonify({
                        "ok": False,
                        "error": f"كمية الصنف {item['product_name']} غير صحيحة"
                    }), 400

                if unit_price < 0 or discount < 0 or tax_rate < 0:
                    return jsonify({
                        "ok": False,
                        "error": f"بيانات الصنف {item['product_name']} غير صحيحة"
                    }), 400

                if not item["allow_decimal_qty"] and abs(quantity - round(quantity)) > 0.000001:
                    return jsonify({
                        "ok": False,
                        "error": f"الصنف {item['product_name']} لا يقبل الكميات العشرية"
                    }), 400

                gross = round(quantity * unit_price, 2)
                net = round(gross - discount, 2)

                if net < 0:
                    return jsonify({
                        "ok": False,
                        "error": f"خصم الصنف {item['product_name']} أكبر من إجماليه"
                    }), 400

                tax_amount = round(net * tax_rate / 100, 2)
                line_total = round(net + tax_amount, 2)

                subtotal += gross
                discount_total += discount
                tax_total += tax_amount
                grand_total += line_total

            subtotal = round(subtotal, 2)
            discount_total = round(discount_total, 2)
            tax_total = round(tax_total, 2)
            grand_total = round(grand_total, 2)

            if grand_total <= 0:
                return jsonify({
                    "ok": False,
                    "error": "إجمالي الفاتورة يجب أن يكون أكبر من صفر"
                }), 400

            if purchase["payment_method"] == "credit":
                credit_account_id = purchase["payable_account_id"]

            elif purchase["payment_method"] == "cash":
                credit_account_id = 228

            elif purchase["payment_method"] == "bank_ahly":
                credit_account_id = 229

            elif purchase["payment_method"] == "bank_misr":
                credit_account_id = 230

            else:
                return jsonify({
                    "ok": False,
                    "error": "طريقة دفع الفاتورة غير صحيحة"
                }), 400

            inventory_account = conn.execute("""
                SELECT id
                FROM accounts
                WHERE company_id = ?
                  AND acc_no = '1301'
                  AND is_active = 1
                LIMIT 1
            """, (company_id,)).fetchone()

            vat_account = conn.execute("""
                SELECT id
                FROM accounts
                WHERE company_id = ?
                  AND acc_no = '1202'
                  AND is_active = 1
                LIMIT 1
            """, (company_id,)).fetchone()

            credit_account = conn.execute("""
                SELECT id
                FROM accounts
                WHERE id = ?
                  AND company_id = ?
                  AND is_active = 1
            """, (
                credit_account_id,
                company_id
            )).fetchone()

            if not inventory_account:
                return jsonify({
                    "ok": False,
                    "error": "حساب المخزون 1301 غير موجود أو غير نشط"
                }), 400

            if not vat_account:
                return jsonify({
                    "ok": False,
                    "error": "حساب ضريبة القيمة المضافة 1202 غير موجود أو غير نشط"
                }), 400

            if not credit_account:
                return jsonify({
                    "ok": False,
                    "error": "الحساب الدائن للفاتورة غير موجود أو غير نشط"
                }), 400

            movement_no = next_movement_no()

            while conn.execute(
                "SELECT id FROM journal_entries WHERE movement_no = ?",
                (movement_no,)
            ).fetchone():
                movement_no = str(int(movement_no) + 1)

            branch = conn.execute("""
                SELECT id
                FROM branches
                WHERE company_id = ?
                ORDER BY id
                LIMIT 1
            """, (company_id,)).fetchone()

            if not branch:
                return jsonify({
                    "ok": False,
                    "error": "لا يوجد فرع للشركة الحالية"
                }), 400

            entry_id = conn.execute("""
                INSERT INTO journal_entries
                (
                    movement_no,
                    entry_date,
                    description,
                    region,
                    status,
                    entry_type,
                    created_by,
                    company_id,
                    branch_id,
                    fiscal_year_id,
                    period_id,
                    currency_id,
                    exchange_rate
                )
                VALUES (?, ?, ?, ?, 'posted', 'عادي', ?, ?, ?, ?, ?, ?, ?)
            """, (
                movement_no,
                purchase["invoice_date"],
                f"اعتماد فاتورة شراء {purchase['invoice_no']} - {purchase['supplier_name']}",
                "المركز الرئيسي",
                g.user["id"],
                company_id,
                branch["id"],
                period["fiscal_year_id"],
                period["id"],
                purchase["currency_id"],
                purchase["exchange_rate"]
            )).lastrowid

            inventory_value = round(grand_total - tax_total, 2)

            if inventory_value > 0:
                conn.execute("""
                    INSERT INTO journal_lines
                    (
                        entry_id,
                        account_id,
                        partner_id,
                        debit,
                        credit
                    )
                    VALUES (?, ?, NULL, ?, 0)
                """, (
                    entry_id,
                    inventory_account["id"],
                    inventory_value
                ))

            if tax_total > 0:
                conn.execute("""
                    INSERT INTO journal_lines
                    (
                        entry_id,
                        account_id,
                        partner_id,
                        debit,
                        credit
                    )
                    VALUES (?, ?, NULL, ?, 0)
                """, (
                    entry_id,
                    vat_account["id"],
                    tax_total
                ))

            conn.execute("""
                INSERT INTO journal_lines
                (
                    entry_id,
                    account_id,
                    partner_id,
                    debit,
                    credit
                )
                VALUES (?, ?, ?, 0, ?)
            """, (
                entry_id,
                credit_account["id"],
                purchase["supplier_id"] if purchase["payment_method"] == "credit" else None,
                grand_total
            ))

            for item in items:
                quantity = float(item["quantity"] or 0)

                stock_row = conn.execute("""
                    SELECT id, quantity
                    FROM warehouse_stock
                    WHERE company_id = ?
                      AND warehouse_id = ?
                      AND product_id = ?
                """, (
                    company_id,
                    purchase["warehouse_id"],
                    item["product_id"]
                )).fetchone()

                if stock_row:
                    conn.execute("""
                        UPDATE warehouse_stock
                        SET quantity = quantity + ?,
                            updated_at = datetime('now','localtime')
                        WHERE id = ?
                    """, (
                        quantity,
                        stock_row["id"]
                    ))
                else:
                    conn.execute("""
                        INSERT INTO warehouse_stock
                        (
                            company_id,
                            warehouse_id,
                            product_id,
                            quantity,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))
                    """, (
                        company_id,
                        purchase["warehouse_id"],
                        item["product_id"],
                        quantity
                    ))

                conn.execute("""
                    INSERT INTO stock_movements
                    (
                        company_id,
                        warehouse_id,
                        product_id,
                        unit_id,
                        movement_type,
                        quantity,
                        reference_type,
                        reference_id,
                        movement_date,
                        notes,
                        created_by
                    )
                    VALUES (?, ?, ?, ?, 'in', ?, 'purchase', ?, ?, ?, ?)
                """, (
                    company_id,
                    purchase["warehouse_id"],
                    item["product_id"],
                    item["unit_id"],
                    quantity,
                    purchase_id,
                    purchase["invoice_date"],
                    f"اعتماد فاتورة شراء {purchase['invoice_no']}",
                    g.user["id"]
                ))

            if purchase["payment_method"] == "credit":
                paid_amount = 0
                remaining_amount = grand_total
            else:
                paid_amount = grand_total
                remaining_amount = 0

            conn.execute("""
                UPDATE purchase_invoices
                SET
                    subtotal = ?,
                    discount_total = ?,
                    tax_total = ?,
                    grand_total = ?,
                    paid_amount = ?,
                    remaining_amount = ?,
                    status = 'approved',
                    journal_entry_id = ?,
                    approved_by = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                  AND company_id = ?
            """, (
                subtotal,
                discount_total,
                tax_total,
                grand_total,
                paid_amount,
                remaining_amount,
                entry_id,
                g.user["id"],
                purchase_id,
                company_id
            ))

        db.audit(
            g.user["full_name"],
            "اعتماد فاتورة شراء",
            f"{purchase['invoice_no']} - قيد رقم {movement_no}"
        )

        return jsonify({
            "ok": True,
            "purchase_id": purchase_id,
            "invoice_no": purchase["invoice_no"],
            "status": "approved",
            "journal_entry_id": entry_id,
            "movement_no": movement_no,
            "grand_total": grand_total
        }), 200

    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"فشل اعتماد فاتورة الشراء: {exc}"
        }), 500

    finally:
        conn.close()


# =========================
# Sales Invoices API
# =========================
@app.route("/api/sales", methods=["GET"])
def api_sales_list():
    company_id = _current_company_id()
    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    rows = db.query("""
        SELECT
            si.id,
            si.invoice_no,
            si.customer_invoice_no,
            si.invoice_date,
            si.payment_method,
            si.status,
            si.session_id,
            si.currency_id,
            c.code AS currency_code,
            c.name AS currency_name,
            c.symbol AS currency_symbol,
            si.exchange_rate,
            si.subtotal,
            si.discount_total,
            si.tax_total,
            si.grand_total,
            si.paid_amount,
            si.remaining_amount,
            si.notes,
            si.created_at,
            si.updated_at,
            bp.id AS customer_id,
            bp.code AS customer_code,
            bp.name AS customer_name,
            w.id AS warehouse_id,
            w.code AS warehouse_code,
            w.name AS warehouse_name
        FROM sales_invoices si
        LEFT JOIN business_partners bp
            ON bp.id = si.customer_id
           AND bp.company_id = si.company_id
        LEFT JOIN currencies c
            ON c.id = si.currency_id
        LEFT JOIN warehouses w
            ON w.id = si.warehouse_id
           AND w.company_id = si.company_id
        WHERE si.company_id = ? AND si.is_held = 0
        ORDER BY si.id DESC
    """, (company_id,))

    return jsonify({
        "ok": True,
        "rows": rows
    })


@app.route("/api/sales", methods=["POST"])
@require_write
def api_sale_create():
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    
    _sr = db.query_one("SELECT id FROM cashier_sessions WHERE company_id=? AND user_id=? AND status='open' LIMIT 1", (company_id, g.user["id"]))
    _session_id = _sr["id"] if _sr else None

    data = request.get_json(silent=True) or {}

    customer_id = data.get("customer_id")
    branch_id = data.get("branch_id")
    warehouse_id = data.get("warehouse_id")
    fiscal_year_id = data.get("fiscal_year_id")
    period_id = data.get("period_id")
    currency_id = data.get("currency_id") or 3
    exchange_rate = data.get("exchange_rate", 1)

    invoice_no = (data.get("invoice_no") or "").strip()
    customer_invoice_no = (data.get("customer_invoice_no") or "").strip()
    invoice_date = (data.get("invoice_date") or "").strip()
    payment_method = (data.get("payment_method") or "cash").strip().lower()
    notes = (data.get("notes") or "").strip()

    if not invoice_date:
        return jsonify({
            "ok": False,
            "error": "تاريخ الفاتورة مطلوب"
        }), 400

    if payment_method not in ("cash", "credit", "bank_ahly", "bank_misr"):
        return jsonify({
            "ok": False,
            "error": "طريقة الدفع غير صحيحة"
        }), 400

    try:
        customer_id = int(customer_id) if customer_id else None
        branch_id = int(branch_id) if branch_id else None
        warehouse_id = int(warehouse_id) if warehouse_id else None
        fiscal_year_id = int(fiscal_year_id) if fiscal_year_id else None
        period_id = int(period_id) if period_id else None
        currency_id = int(currency_id) if currency_id else None
        exchange_rate = float(exchange_rate)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "أحد الحقول الرقمية غير صالح"
        }), 400

    if exchange_rate <= 0:
        return jsonify({
            "ok": False,
            "error": "سعر الصرف يجب أن يكون أكبر من صفر"
        }), 400

    with db.connect() as conn:

        if not warehouse_id:
            pref = conn.execute("SELECT default_warehouse_id FROM company_preferences WHERE company_id = ?", (company_id,)).fetchone()
            if pref and pref["default_warehouse_id"]:
                warehouse_id = int(pref["default_warehouse_id"])
            else:
                active_warehouses = conn.execute("SELECT id FROM warehouses WHERE company_id = ? AND is_active = 1 ORDER BY id", (company_id,)).fetchall()
                if len(active_warehouses) == 1:
                    warehouse_id = active_warehouses[0]["id"]
            if not warehouse_id:
                return jsonify({"ok": False, "error": "يرجى تعيين المخزن الافتراضي من إعدادات الشركة قبل البيع"}), 400

        customer = None

        if customer_id:
            customer = conn.execute("""
                SELECT
                    id,
                    code,
                    name,
                    partner_type,
                    receivable_account_id,
                    is_active
                FROM business_partners
                WHERE id = ?
                  AND company_id = ?
            """, (customer_id, company_id)).fetchone()

            if not customer:
                return jsonify({
                    "ok": False,
                    "error": "العميل غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if customer["partner_type"] != "customer":
                return jsonify({
                    "ok": False,
                    "error": "الشريك المحدد ليس عميلًا"
                }), 400

            if not customer["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "العميل غير نشط"
                }), 400

        if branch_id:
            branch = conn.execute("""
                SELECT id, is_active
                FROM branches
                WHERE id = ?
                  AND company_id = ?
            """, (branch_id, company_id)).fetchone()

            if not branch:
                return jsonify({
                    "ok": False,
                    "error": "الفرع غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if not branch["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "الفرع غير نشط"
                }), 400

        if warehouse_id:
            warehouse = conn.execute("""
                SELECT id, is_active
                FROM warehouses
                WHERE id = ?
                  AND company_id = ?
            """, (warehouse_id, company_id)).fetchone()

            if not warehouse:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير موجود أو لا يتبع الشركة الحالية"
                }), 404

            if not warehouse["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير نشط"
                }), 400

        if fiscal_year_id:
            fiscal_year = conn.execute("""
                SELECT id, status, date_from, date_to
                FROM fiscal_years
                WHERE id = ?
                  AND company_id = ?
            """, (fiscal_year_id, company_id)).fetchone()

            if not fiscal_year:
                return jsonify({
                    "ok": False,
                    "error": "السنة المالية غير موجودة"
                }), 404

            if fiscal_year["status"] != "open":
                return jsonify({
                    "ok": False,
                    "error": "السنة المالية مغلقة"
                }), 400

        if period_id:
            period = conn.execute("""
                SELECT
                    id,
                    fiscal_year_id,
                    status,
                    date_from,
                    date_to
                FROM accounting_periods
                WHERE id = ?
            """, (period_id,)).fetchone()

            if not period:
                return jsonify({
                    "ok": False,
                    "error": "الفترة المحاسبية غير موجودة"
                }), 404

            if period["status"] != "open":
                return jsonify({
                    "ok": False,
                    "error": "الفترة المحاسبية مغلقة"
                }), 400

        if not currency_id:
            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE is_base = 1
                  AND is_active = 1
                ORDER BY id
                LIMIT 1
            """).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "لا توجد عملة أساسية نشطة"
                }), 400

            currency_id = currency["id"]
        else:
            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE id = ?
                  AND is_active = 1
            """, (currency_id,)).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "العملة غير موجودة أو غير نشطة"
                }), 404

        if not invoice_no:
            year_text = invoice_date[:4]

            last_invoice = conn.execute("""
                SELECT invoice_no
                FROM sales_invoices
                WHERE company_id = ?
                  AND invoice_no LIKE ?
                ORDER BY id DESC
                LIMIT 1
            """, (
                company_id,
                f"SAL-{year_text}-%"
            )).fetchone()

            next_number = 1

            if last_invoice and last_invoice["invoice_no"]:
                try:
                    next_number = int(
                        last_invoice["invoice_no"].rsplit("-", 1)[1]
                    ) + 1
                except (ValueError, IndexError):
                    next_number = 1

            invoice_no = f"SAL-{year_text}-{next_number:06d}"

        duplicate = conn.execute("""
            SELECT id
            FROM sales_invoices
            WHERE company_id = ?
              AND invoice_no = ?
        """, (company_id, invoice_no)).fetchone()

        if duplicate:
            return jsonify({
                "ok": False,
                "error": "رقم فاتورة البيع مستخدم بالفعل"
            }), 409

        cur = conn.execute("""
            INSERT INTO sales_invoices (
                company_id,
                branch_id,
                customer_id,
                warehouse_id,
                fiscal_year_id,
                period_id,
                currency_id,
                exchange_rate,
                invoice_no,
                customer_invoice_no,
                invoice_date,
                payment_method,
                status,
                subtotal,
                discount_total,
                tax_total,
                grand_total,
                paid_amount,
                remaining_amount,
                notes,
                created_by,
                session_id
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, 'draft',
                0, 0, 0, 0, 0, 0,
                ?, ?, ?
            )
        """, (
            company_id,
            branch_id,
            customer_id,
            warehouse_id,
            fiscal_year_id,
            period_id,
            currency_id,
            exchange_rate,
            invoice_no,
            customer_invoice_no,
            invoice_date,
            payment_method,
            notes,
            g.user["id"],
            _session_id
        ))

        sale_id = cur.lastrowid

        conn.commit()

    customer_name = customer["name"] if customer else "عميل نقدي"

    db.audit(
        g.user["full_name"],
        "إنشاء مسودة فاتورة بيع",
        f"{invoice_no} - {customer_name}"
    )

    return jsonify({
        "ok": True,
        "sale_id": sale_id,
        "invoice_no": invoice_no,
        "status": "draft"
    }), 201




# =========================
# Sales Invoice Items API
# =========================

@app.route("/api/sales/<int:sale_id>/items", methods=["POST"])
@require_write
def api_sale_item_create(sale_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400


    data = request.get_json(silent=True) or {}

    product_id = data.get("product_id")
    unit_id = data.get("unit_id")
    quantity = data.get("quantity")
    unit_price = data.get("unit_price", 0)
    discount = data.get("discount", 0)
    tax_rate = 0.0 if db.get_setting("tax_enabled", "0") != "1" else db.get_setting("tax_rate", "0")
    notes = (data.get("notes") or "").strip()

    try:
        product_id = int(product_id)
        unit_id = int(unit_id)
        quantity = float(quantity)
        unit_price = float(unit_price)
        discount = float(discount)
        tax_rate = float(tax_rate)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "بيانات الصنف الرقمية غير صالحة"
        }), 400

    if quantity <= 0:
        return jsonify({
            "ok": False,
            "error": "الكمية يجب أن تكون أكبر من صفر"
        }), 400

    if unit_price < 0:
        return jsonify({
            "ok": False,
            "error": "سعر البيع لا يمكن أن يكون سالبًا"
        }), 400

    if discount < 0:
        return jsonify({
            "ok": False,
            "error": "الخصم لا يمكن أن يكون سالبًا"
        }), 400

    if tax_rate < 0 or tax_rate > 100:
        return jsonify({
            "ok": False,
            "error": "نسبة الضريبة يجب أن تكون بين 0 و100"
        }), 400

    with db.connect() as conn:

        sale = conn.execute("""
            SELECT
                id,
                company_id,
                status
            FROM sales_invoices
            WHERE id = ?
              AND company_id = ?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404


        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن إضافة صنف إلى فاتورة غير مسودة"
            }), 400

        product = conn.execute("""
            SELECT
                id,
                code,
                name,
                base_unit_id,
                is_active,
                is_stock_item
            FROM products
            WHERE id = ?
              AND company_id = ?
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "الصنف غير موجود أو لا يتبع الشركة الحالية"
            }), 404

        if not product["is_active"]:
            return jsonify({
                "ok": False,
                "error": "الصنف غير نشط"
            }), 400

        if not product["is_stock_item"]:
            return jsonify({
                "ok": False,
                "error": "الصنف المحدد ليس صنف مخزون"
            }), 400

        unit = conn.execute("""
            SELECT
                id,
                name,
                is_active
            FROM units
            WHERE id = ?
              AND company_id = ?
        """, (unit_id, company_id)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "الوحدة غير موجودة أو لا تتبع الشركة الحالية"
            }), 404

        if not unit["is_active"]:
            return jsonify({
                "ok": False,
                "error": "الوحدة غير نشطة"
            }), 400

        line_subtotal = quantity * unit_price

        if discount > line_subtotal:
            return jsonify({
                "ok": False,
                "error": "الخصم لا يمكن أن يتجاوز قيمة الصنف"
            }), 400

        taxable_amount = line_subtotal - discount
        tax_amount = taxable_amount * tax_rate / 100
        line_total = taxable_amount + tax_amount

        cur = conn.execute("""
            INSERT INTO sales_invoice_items (
                invoice_id,
                product_id,
                unit_id,
                quantity,
                unit_price,
                discount,
                tax_rate,
                tax_amount,
                line_total,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sale_id,
            product_id,
            unit_id,
            quantity,
            unit_price,
            discount,
            tax_rate,
            tax_amount,
            line_total,
            notes
        ))

        item_id = cur.lastrowid

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM sales_invoice_items
            WHERE invoice_id = ?
        """, (sale_id,)).fetchone()

        grand_total = float(totals["grand_total"] or 0)

        conn.execute("""
            UPDATE sales_invoices
            SET
                subtotal = ?,
                discount_total = ?,
                tax_total = ?,
                grand_total = ?,
                remaining_amount = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
              AND company_id = ?
        """, (
            float(totals["subtotal"] or 0),
            float(totals["discount_total"] or 0),
            float(totals["tax_total"] or 0),
            grand_total,
            grand_total,
            sale_id,
            company_id
        ))

        conn.commit()

    db.audit(
        g.user["full_name"],
        "إضافة صنف إلى فاتورة بيع",
        f"{sale['id']} - {product['code']} - {product['name']}"
    )

    return jsonify({
        "ok": True,
        "item_id": item_id,
        "sale_id": sale_id,
        "quantity": quantity,
        "unit_price": unit_price,
        "discount": discount,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "line_total": line_total,
        "totals": {
            "subtotal": float(totals["subtotal"] or 0),
            "discount_total": float(totals["discount_total"] or 0),
            "tax_total": float(totals["tax_total"] or 0),
            "grand_total": grand_total,
            "remaining_amount": grand_total
        }
    }), 201



@app.route("/api/sales/<int:sale_id>/items", methods=["GET"])
def api_sale_items(sale_id):
    company_id = _current_company_id()

    if not company_id:
        return jsonify({
            "ok": False,
            "error": "لا توجد شركة حالية"
        }), 400

    with db.connect() as conn:
        sale = conn.execute("""
            SELECT si.id, si.invoice_no, si.status, si.customer_id, bp.name AS customer_name
            FROM sales_invoices si
            LEFT JOIN business_partners bp ON bp.id = si.customer_id AND bp.company_id = si.company_id
            WHERE si.id = ? AND si.company_id = ?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404

 
        rows = conn.execute("""
            SELECT
                sii.id,
                sii.invoice_id,
                sii.product_id,
                p.code AS product_code,
                p.name AS product_name,
                p.category_id,
                pc.name AS category_name,
                sii.unit_id,
                u.code AS unit_code,
                u.name AS unit_name,
                u.symbol AS unit_symbol,
                sii.quantity,
                sii.unit_price,
                sii.discount,
                sii.tax_rate,
                sii.tax_amount,
                sii.line_total,
                sii.notes,
                COALESCE(ws.quantity, 0) AS stock_quantity,
                COALESCE((
                    SELECT pp.price
                    FROM product_prices pp
                    WHERE pp.company_id = si.company_id
                      AND pp.product_id = sii.product_id
                      AND pp.unit_id = sii.unit_id
                      AND pp.currency_id = si.currency_id
                      AND pp.price_type = 'purchase'
                      AND pp.is_active = 1
                    ORDER BY pp.is_default DESC, pp.id DESC
                    LIMIT 1
                ), 0) AS last_purchase_price
            FROM sales_invoice_items sii
            JOIN products p
              ON p.id = sii.product_id
            JOIN units u
              ON u.id = sii.unit_id
            JOIN sales_invoices si
              ON si.id = sii.invoice_id
             AND si.company_id = ?
            LEFT JOIN product_categories pc
              ON pc.id = p.category_id
             AND pc.company_id = si.company_id
            LEFT JOIN warehouse_stock ws
              ON ws.company_id = si.company_id
             AND ws.warehouse_id = si.warehouse_id
             AND ws.product_id = sii.product_id
            WHERE sii.invoice_id = ?
            ORDER BY sii.id
        """, (company_id, sale_id)).fetchall()


    items = []

    for row in rows:
        items.append({
            "id": row["id"],
            "invoice_id": row["invoice_id"],
            "product_id": row["product_id"],
            "product_code": row["product_code"],
            "product_name": row["product_name"],
            "category_id": row["category_id"],
            "category_name": row["category_name"] or "",
            "stock_quantity": float(row["stock_quantity"] or 0),
            "last_purchase_price": float(row["last_purchase_price"] or 0),
            "unit_id": row["unit_id"],
            "unit_code": row["unit_code"],
            "unit_name": row["unit_name"],
            "unit_symbol": row["unit_symbol"],
            "quantity": float(row["quantity"] or 0),
            "unit_price": float(row["unit_price"] or 0),
            "discount": float(row["discount"] or 0),
            "tax_rate": float(row["tax_rate"] or 0),
            "tax_amount": float(row["tax_amount"] or 0),
            "line_total": float(row["line_total"] or 0),
            "notes": row["notes"] or ""
        })

    return jsonify({
        "ok": True,
        "sale_id": sale_id,
        "invoice_no": sale["invoice_no"],
        "status": sale["status"],
        "customer_id": sale["customer_id"],
        "customer_name": sale["customer_name"] or "عميل نقدي",
        "items": items
    }), 200


@app.route("/stock")
def stock_page():
    return render_template("stock.html")


# Accounting Core V2 — السنة والفترات المحاسبية
# ------------------------------------------------------------------

def _period_for_date(entry_date, company_id=None):
    """يعيد الفترة المحاسبية التي تحتوي تاريخ القيد."""
    company_id = company_id or db.query_one("SELECT id FROM companies ORDER BY id LIMIT 1")["id"]
    return db.query_one(
        """SELECT ap.*, fy.company_id, fy.name AS fiscal_year_name, fy.status AS fiscal_year_status
           FROM accounting_periods ap
           JOIN fiscal_years fy ON fy.id=ap.fiscal_year_id
          WHERE fy.company_id=? AND ? BETWEEN ap.date_from AND ap.date_to
          ORDER BY ap.id LIMIT 1""",
        (company_id, entry_date),
    )


def _open_period_error(entry_date, company_id=None):
    p = _period_for_date(entry_date, company_id)
    if not p:
        return "لا توجد سنة مالية/فترة محاسبية تغطي هذا التاريخ"
    if p["fiscal_year_status"] != "open":
        return f"السنة المالية {p['fiscal_year_name']} مغلقة"
    if p["status"] != "open":
        return f"الفترة {p['name']} مغلقة ولا يمكن تسجيل أو ترحيل قيود فيها"
    return None


@app.route("/fiscal-periods")
@admin_required
def fiscal_periods_page():
    return render_template("fiscal_periods.html")


@app.route("/api/fiscal-years", methods=["GET"])
@admin_required
def api_fiscal_years():
    rows = db.query("SELECT * FROM fiscal_years ORDER BY date_from DESC")
    for r in rows:
        r["period_count"] = db.query_one("SELECT COUNT(*) c FROM accounting_periods WHERE fiscal_year_id=?", (r["id"],))["c"]
        r["closed_periods"] = db.query_one("SELECT COUNT(*) c FROM accounting_periods WHERE fiscal_year_id=? AND status='closed'", (r["id"],))["c"]
    return jsonify(fiscal_years=rows)


@app.route("/api/fiscal-years", methods=["POST"])
@admin_required
def api_fiscal_year_create():
    d = request.json or {}
    name = (d.get("name") or "").strip()
    date_from = (d.get("date_from") or "").strip()
    date_to = (d.get("date_to") or "").strip()
    if not name or not date_from or not date_to:
        return jsonify(error="اسم السنة وتاريخا البداية والنهاية مطلوبة"), 400
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d").date()
        dt = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(error="صيغة التاريخ غير صحيحة"), 400
    if dt < df:
        return jsonify(error="تاريخ النهاية يجب أن يكون بعد البداية"), 400
    company = db.query_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    if not company:
        return jsonify(error="لا توجد شركة أساسية"), 400
    overlap = db.query_one(
        """SELECT id FROM fiscal_years WHERE company_id=? AND date_from<=? AND date_to>=? LIMIT 1""",
        (company["id"], date_to, date_from),
    )
    if overlap:
        return jsonify(error="توجد سنة مالية متداخلة مع هذه الفترة"), 400
    conn = db.connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO fiscal_years(company_id,name,date_from,date_to) VALUES(?,?,?,?)",
                (company["id"], name, date_from, date_to),
            )
            fy_id = cur.lastrowid
            # تقسيم السنة إلى أشهر طبيعية، مع قص أول/آخر شهر عند الحاجة.
            import calendar
            y, m = df.year, df.month
            while (y, m) <= (dt.year, dt.month):
                first = date(y, m, 1)
                last = date(y, m, calendar.monthrange(y, m)[1])
                start = max(first, df)
                end = min(last, dt)
                conn.execute(
                    "INSERT INTO accounting_periods(fiscal_year_id,name,date_from,date_to) VALUES(?,?,?,?)",
                    (fy_id, f"{y}-{m:02d}", start.isoformat(), end.isoformat()),
                )
                m += 1
                if m == 13:
                    y, m = y + 1, 1
    except db.IntegrityError:
        return jsonify(error="تعذر إنشاء السنة المالية — تحقق من عدم تكرار الاسم أو الفترات"), 400
    finally:
        conn.close()
    db.audit(g.user["full_name"], "إنشاء سنة مالية", f"{name}: {date_from} إلى {date_to}")
    return jsonify(ok=True, id=fy_id)


@app.route("/api/fiscal-years/<int:fyid>/periods")
@admin_required
def api_fiscal_year_periods(fyid):
    fy = db.query_one("SELECT * FROM fiscal_years WHERE id=?", (fyid,))
    if not fy:
        return jsonify(error="السنة المالية غير موجودة"), 404
    return jsonify(periods=db.query("SELECT * FROM accounting_periods WHERE fiscal_year_id=? ORDER BY date_from", (fyid,)))


@app.route("/api/accounting-periods/<int:pid>/close", methods=["POST"])
@admin_required
def api_period_close(pid):
    p = db.query_one(
        "SELECT ap.*, fy.name fiscal_year_name, fy.status fiscal_year_status FROM accounting_periods ap JOIN fiscal_years fy ON fy.id=ap.fiscal_year_id WHERE ap.id=?",
        (pid,),
    )
    if not p:
        return jsonify(error="الفترة غير موجودة"), 404
    if p["status"] == "closed":
        return jsonify(error="الفترة مغلقة بالفعل"), 400
    if p["fiscal_year_status"] != "open":
        return jsonify(error="السنة المالية مغلقة"), 400
    drafts = db.query_one(
        "SELECT COUNT(*) c FROM journal_entries WHERE period_id=? AND status='draft'",
        (pid,),
    )["c"]
    if drafts:
        return jsonify(error=f"لا يمكن إغلاق الفترة: يوجد {drafts} قيد مسودة يجب ترحيله أو معالجته أولًا"), 400
    db.execute("UPDATE accounting_periods SET status='closed' WHERE id=?", (pid,))
    db.audit(g.user["full_name"], "إغلاق فترة محاسبية", p["name"])
    return jsonify(ok=True)


@app.route("/api/accounting-periods/<int:pid>/open", methods=["POST"])
@admin_required
def api_period_open(pid):
    p = db.query_one(
        "SELECT ap.*, fy.name fiscal_year_name, fy.status fiscal_year_status FROM accounting_periods ap JOIN fiscal_years fy ON fy.id=ap.fiscal_year_id WHERE ap.id=?",
        (pid,),
    )
    if not p:
        return jsonify(error="الفترة غير موجودة"), 404
    if p["fiscal_year_status"] != "open":
        return jsonify(error="لا يمكن فتح فترة داخل سنة مالية مغلقة"), 400
    db.execute("UPDATE accounting_periods SET status='open' WHERE id=?", (pid,))
    db.audit(g.user["full_name"], "فتح فترة محاسبية", p["name"])
    return jsonify(ok=True)


@app.route("/api/fiscal-years/<int:fyid>/close", methods=["POST"])
@admin_required
def api_fiscal_year_close(fyid):
    fy = db.query_one("SELECT * FROM fiscal_years WHERE id=?", (fyid,))
    if not fy:
        return jsonify(error="السنة المالية غير موجودة"), 404
    if fy["status"] == "closed":
        return jsonify(error="السنة مغلقة بالفعل"), 400
    open_periods = db.query_one("SELECT COUNT(*) c FROM accounting_periods WHERE fiscal_year_id=? AND status='open'", (fyid,))["c"]
    drafts = db.query_one("SELECT COUNT(*) c FROM journal_entries WHERE fiscal_year_id=? AND status='draft'", (fyid,))["c"]
    if open_periods:
        return jsonify(error=f"لا يمكن إغلاق السنة: ما زالت {open_periods} فترة مفتوحة"), 400
    if drafts:
        return jsonify(error=f"لا يمكن إغلاق السنة: يوجد {drafts} قيد مسودة"), 400
    db.execute("UPDATE fiscal_years SET status='closed' WHERE id=?", (fyid,))
    db.audit(g.user["full_name"], "إغلاق سنة مالية", fy["name"])
    return jsonify(ok=True)


# ------------------------------------------------------------------
# APIs — دفتر اليومية
# ------------------------------------------------------------------

def next_movement_no():
    rows = db.query("SELECT movement_no FROM journal_entries")
    mx = 0
    for r in rows:
        try:
            mx = max(mx, int(float(str(r["movement_no"]).replace(",", ""))))
        except (ValueError, TypeError):
            continue
    return str(mx + 1)


@app.route("/api/next-movement-no")
@require_write
def api_next_movement():
    return jsonify(no=next_movement_no())


def _validate_entry_payload(d):
    entry_date = (d.get("entry_date") or "").strip()
    region = (d.get("region") or "").strip()
    movement = (d.get("movement_no") or "").strip()
    lines = d.get("lines") or []
    if not entry_date:
        return None, "التاريخ مطلوب"
    try:
        datetime.strptime(entry_date, "%Y-%m-%d")
    except ValueError:
        return None, "صيغة التاريخ غير صحيحة"
    period_error = _open_period_error(entry_date)
    if period_error:
        return None, period_error
    if region not in db.load_regions():
        return None, "اختر المنطقة الصحيحة"
    if not movement:
        return None, "رقم الحركة مطلوب"
    entry_type = (d.get("entry_type") or "عادي").strip()
    if entry_type not in ENTRY_TYPES:
        return None, "نوع القيد غير صحيح"
    clean = []
    for ln in lines:
        try:
            acc_id = int(ln.get("account_id") or 0)
            debit = round(float(ln.get("debit") or 0), 2)
            credit = round(float(ln.get("credit") or 0), 2)
        except (TypeError, ValueError):
            return None, "قيم المدين والدائن غير صحيحة"
        if debit < 0 or credit < 0:
            return None, "لا يُسمح بقيم سالبة"
        if debit > 0 and credit > 0:
            return None, "لا يمكن أن يكون السطر مدينًا ودائنًا معًا"
        if debit == 0 and credit == 0:
            continue
        if not db.query_one("SELECT id FROM accounts WHERE id=?", (acc_id,)):
            return None, "أحد السطور بدون حساب صحيح"

        partner_id = ln.get("partner_id")
        if partner_id not in (None, "", 0, "0"):
            try:
                partner_id = int(partner_id)
            except (TypeError, ValueError):
                return None, "رقم العميل/المورد غير صحيح"

            partner = db.query_one(
                """SELECT id, partner_type, receivable_account_id, payable_account_id
                   FROM business_partners
                   WHERE id=? AND is_active=1""",
                (partner_id,),
            )
            if not partner:
                return None, "العميل/المورد غير موجود أو غير نشط"

            account = db.query_one(
                "SELECT acc_no FROM accounts WHERE id=?",
                (acc_id,),
            )

            if account and account["acc_no"] == "1201":
                if partner["partner_type"] not in ("customer", "both"):
                    return None, "هذا الطرف ليس عميلًا ولا يمكن ربطه بحساب العملاء 1201"
                if partner["receivable_account_id"] != acc_id:
                    return None, "حساب العملاء المرتبط بالطرف لا يطابق الحساب 1201"

            elif account and account["acc_no"] == "2101":
                if partner["partner_type"] not in ("supplier", "both"):
                    return None, "هذا الطرف ليس موردًا ولا يمكن ربطه بحساب الموردين 2101"
                if partner["payable_account_id"] != acc_id:
                    return None, "حساب الموردين المرتبط بالطرف لا يطابق الحساب 2101"
        else:
            partner_id = None

        clean.append({
            "account_id": acc_id,
            "partner_id": partner_id,
            "debit": debit,
            "credit": credit,
        })
    if len(clean) < 2:
        return None, "القيد يحتاج سطرين على الأقل (مدين ودائن)"
    td = sum(l["debit"] for l in clean)
    tc = sum(l["credit"] for l in clean)
    if abs(td - tc) > 0.004:
        return None, f"القيد غير متوازن: مدين {td:,.2f} ≠ دائن {tc:,.2f}"
    if td <= 0:
        return None, "إجمالي القيد يجب أن يكون أكبر من صفر"
    return {
        "movement_no": movement,
        "entry_date": entry_date,
        "region": region,
        "description": (d.get("description") or "").strip(),
        "status": "posted" if d.get("status") == "posted" else "draft",
        "entry_type": entry_type,
        "lines": clean,
    }, None


def _entry_full(entry_id):
    e = db.query_one("SELECT * FROM journal_entries WHERE id=?", (entry_id,))
    if not e:
        return None
    e["lines"] = db.query(
        """SELECT jl.id, jl.account_id, jl.partner_id,
                  p.code AS partner_code, p.name AS partner_name,
                  a.acc_no, a.name, jl.debit, jl.credit
           FROM journal_lines jl
           JOIN accounts a ON a.id = jl.account_id
           LEFT JOIN business_partners p ON p.id = jl.partner_id
           WHERE jl.entry_id=? ORDER BY jl.id""",
        (entry_id,),
    )
    e["total_debit"] = round(sum(l["debit"] for l in e["lines"]), 2)
    e["total_credit"] = round(sum(l["credit"] for l in e["lines"]), 2)
    return e


def journal_filters(args):
    where, args_ = [], []
    f, t = (args.get("from") or "").strip(), (args.get("to") or "").strip()
    if f:
        where.append("je.entry_date >= ?")
        args_.append(f)
    if t:
        where.append("je.entry_date <= ?")
        args_.append(t)
    if args.get("region"):
        where.append("je.region = ?")
        args_.append(args["region"])
    if args.get("status") in ("draft", "posted"):
        where.append("je.status = ?")
        args_.append(args["status"])
    if args.get("type") in ENTRY_TYPES:
        where.append("je.entry_type = ?")
        args_.append(args["type"])
    if args.get("acc_no"):
        where.append("""EXISTS (SELECT 1 FROM journal_lines jl
                       JOIN accounts a ON a.id=jl.account_id
                       WHERE jl.entry_id=je.id AND a.acc_no LIKE ?)""")
        args_.append(f"%{args['acc_no']}%")
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        where.append("(je.movement_no LIKE ? OR je.description LIKE ?)")
        args_.extend([like, like])
    return ("WHERE " + " AND ".join(where)) if where else "", args_


@app.route("/api/journal")
def api_journal_list():
    where, args_ = journal_filters(request.args)
    limit = min(int(request.args.get("limit", 500)), 2000)
    ids = db.query(
        f"""SELECT je.id FROM journal_entries je {where}
            ORDER BY je.entry_date DESC, CAST(je.movement_no AS INTEGER) DESC
            LIMIT {limit}""",
        args_,
    )
    entries = [_entry_full(r["id"]) for r in ids]
    return jsonify(entries=[e for e in entries if e], truncated=len(ids) >= limit)


@app.route("/api/journal", methods=["POST"])
@require_write
def api_journal_create():
    data, err = _validate_entry_payload(request.json or {})
    if err:
        return jsonify(error=err), 400
    if db.query_one("SELECT id FROM journal_entries WHERE movement_no=?", (data["movement_no"],)):
        return jsonify(error=f"رقم الحركة {data['movement_no']} مستخدم بالفعل"), 400
    company = db.query_one("SELECT id, currency_id FROM companies ORDER BY id LIMIT 1")
    period = _period_for_date(data["entry_date"], company["id"] if company else None)
    if not company or not period:
        return jsonify(error="تعذر تحديد الشركة أو الفترة المحاسبية للقيد"), 400
    eid = db.execute(
        """INSERT INTO journal_entries
           (movement_no, entry_date, description, region, status, entry_type, created_by,
            company_id, branch_id, fiscal_year_id, period_id, currency_id, exchange_rate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (data["movement_no"], data["entry_date"], data["description"],
         data["region"], data["status"], data["entry_type"], g.user["id"],
         company["id"], db.query_one("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company["id"],))["id"],
         period["fiscal_year_id"], period["id"], company["currency_id"]),
    )
    for ln in data["lines"]:
        db.execute(
            "INSERT INTO journal_lines (entry_id, account_id, partner_id, debit, credit) VALUES (?,?,?,?,?)",
            (eid, ln["account_id"], ln["partner_id"], ln["debit"], ln["credit"]),
        )
    db.audit(g.user["full_name"],
             "إنشاء قيد " + ("ومرحلته" if data["status"] == "posted" else "(مسودة)"),
             f"رقم الحركة {data['movement_no']}")
    return jsonify(ok=True, id=eid)


@app.route("/api/journal/<int:eid>", methods=["PUT"])
@require_write
def api_journal_update(eid):
    e = db.query_one("SELECT * FROM journal_entries WHERE id=?", (eid,))
    if not e:
        return jsonify(error="القيد غير موجود"), 404
    if e["status"] == "posted":
        return jsonify(error="لا يمكن تعديل قيد مرحل — قم بإلغاء الترحيل أولًا"), 400

    period_error = _entry_period_error(e)
    if period_error:
        return jsonify(error=period_error), 400

    data, err = _validate_entry_payload(request.json or {})
    if err:
        return jsonify(error=err), 400
    dup = db.query_one(
        "SELECT id FROM journal_entries WHERE movement_no=? AND id<>?",
        (data["movement_no"], eid),
    )
    if dup:
        return jsonify(error=f"رقم الحركة {data['movement_no']} مستخدم بالفعل"), 400
    conn = db.connect()
    with conn:
        conn.execute(
            """UPDATE journal_entries SET movement_no=?, entry_date=?, description=?, region=?, status=?, entry_type=?,
               company_id=?, branch_id=?, fiscal_year_id=?, period_id=?, currency_id=?, exchange_rate=1
               WHERE id=?""",
            (data["movement_no"], data["entry_date"], data["description"],
             data["region"], data["status"], data["entry_type"],
             db.query_one("SELECT id FROM companies ORDER BY id LIMIT 1")["id"],
             db.query_one("SELECT id FROM branches ORDER BY id LIMIT 1")["id"],
             _period_for_date(data["entry_date"])["fiscal_year_id"],
             _period_for_date(data["entry_date"])["id"],
             db.query_one("SELECT currency_id FROM companies ORDER BY id LIMIT 1")["currency_id"], eid),
        )
        conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (eid,))
        for ln in data["lines"]:
            conn.execute(
                "INSERT INTO journal_lines (entry_id, account_id, partner_id, debit, credit) VALUES (?,?,?,?,?)",
                (eid, ln["account_id"], ln["partner_id"], ln["debit"], ln["credit"]),
            )
    finally_close(conn)
    db.audit(g.user["full_name"], "تعديل قيد", f"رقم الحركة {data['movement_no']}")
    return jsonify(ok=True)


def _entry_period_error(entry):
    """التحقق من أن الفترة المحاسبية للقيد مفتوحة."""
    if not entry:
        return "القيد غير موجود"
    return _open_period_error(entry["entry_date"])


def finally_close(conn):
    try:
        conn.close()
    except Exception:
        pass


@app.route("/api/journal/<int:eid>", methods=["DELETE"])
@require_write
def api_journal_delete(eid):
    e = db.query_one("SELECT * FROM journal_entries WHERE id=?", (eid,))
    if not e:
        return jsonify(error="القيد غير موجود"), 404

    period_error = _entry_period_error(e)
    if period_error:
        return jsonify(error=period_error), 400

    if e["status"] == "posted" and g.user["role"] != "admin":
        return jsonify(
            error="حذف القيود المرحلة لمدير النظام فقط — قم بإلغاء الترحيل أولًا"
        ), 403

    db.execute("DELETE FROM journal_entries WHERE id=?", (eid,))
    db.audit(
        g.user["full_name"],
        "حذف قيد",
        f"رقم الحركة {e['movement_no']}"
    )
    return jsonify(ok=True)


@app.route("/api/journal/bulk-delete", methods=["POST"])
@admin_required
def api_journal_bulk_delete():
    d = request.get_json(silent=True) or {}
    scope = (d.get("scope") or "").strip()

    if scope == "month":
        month = (d.get("month") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            return jsonify(error="اختر الشهر بشكل صحيح (YYYY-MM)"), 400

        where = "strftime('%Y-%m', entry_date)=?"
        args = (month,)
        label = f"شهر {month}"

    elif scope == "all":
        where = "1=1"
        args = ()
        label = "جميع القيود"

    else:
        return jsonify(error="نطاق غير صحيح"), 400

    entries = db.query(
        f"SELECT id, entry_date, movement_no FROM journal_entries WHERE {where}",
        args
    )

    if not entries:
        return jsonify(error=f"لا توجد قيود في {label}"), 400

    # رفض العملية بالكامل إذا كان أي قيد تابعًا لفترة مغلقة.
    for e in entries:
        period_error = _entry_period_error(e)
        if period_error:
            return jsonify(
                error=(
                    f"لا يمكن المسح الجماعي: القيد {e['movement_no']} — "
                    f"{period_error}"
                )
            ), 400

    n = len(entries)

    db.execute(
        f"DELETE FROM journal_entries WHERE {where}",
        args
    )

    db.audit(
        g.user["full_name"],
        "مسح قيود",
        f"حذف {n} قيد ({label})"
    )

    return jsonify(ok=True, deleted=n)


@app.route("/api/journal/<int:eid>/post", methods=["POST"])
@require_write
def api_journal_post(eid):
    e = db.query_one("SELECT * FROM journal_entries WHERE id=?", (eid,))
    if not e:
        return jsonify(error="القيد غير موجود"), 404
    if e["status"] == "posted":
        return jsonify(error="القيد مرحل بالفعل"), 400
    period_error = _open_period_error(e["entry_date"])
    if period_error:
        return jsonify(error=period_error), 400
    lines = db.query(
        "SELECT debit, credit FROM journal_lines WHERE entry_id=?", (eid,)
    )
    td = round(sum(l["debit"] for l in lines), 2)
    tc = round(sum(l["credit"] for l in lines), 2)
    if abs(td - tc) > 0.004:
        return jsonify(error="لا يمكن ترحيل قيد غير متوازن"), 400
    db.execute("UPDATE journal_entries SET status='posted' WHERE id=?", (eid,))
    db.audit(g.user["full_name"], "ترحيل قيد", f"رقم الحركة {e['movement_no']}")
    return jsonify(ok=True)


@app.route("/api/journal/<int:eid>/unpost", methods=["POST"])
@require_write
def api_journal_unpost(eid):
    e = db.query_one("SELECT * FROM journal_entries WHERE id=?", (eid,))
    if not e:
        return jsonify(error="القيد غير موجود"), 404

    if e["status"] != "posted":
        return jsonify(error="القيد غير مرحل أصلًا"), 400

    period_error = _entry_period_error(e)
    if period_error:
        return jsonify(error=period_error), 400

    db.execute(
        "UPDATE journal_entries SET status='draft' WHERE id=?",
        (eid,)
    )

    db.audit(
        g.user["full_name"],
        "إلغاء ترحيل قيد",
        f"رقم الحركة {e['movement_no']}"
    )

    return jsonify(ok=True)


@app.route("/api/journal/import", methods=["POST"])
@require_write
def api_journal_import():
    f = request.files.get("file")

    if not f or not f.filename.lower().endswith(".xlsx"):
        return jsonify(error="اختر ملف إكسيل بصيغة .xlsx"), 400

    region = request.form.get("region", "")
    post_flag = request.form.get("post") == "1"

    regions = db.load_regions()
    if not regions:
        return jsonify(error="لا توجد مناطق معرفة في النظام"), 400

    if region not in regions:
        region = regions[0]

    groups, errors = xl.read_journal_template(f.stream)

    if not groups:
        errors.insert(0, "لم يتم العثور على أي بيانات في الملف")
        return jsonify(
            ok=False,
            added=0,
            drafts=0,
            new_accounts=0,
            skipped=0,
            errors=errors[:10]
        )

    # نفس الشركة والعملة المستخدمة في إنشاء القيد العادي.
    company = db.query_one(
        "SELECT id, currency_id FROM companies ORDER BY id LIMIT 1"
    )

    if not company:
        return jsonify(error="تعذر تحديد الشركة للقيد المستورد"), 400

    branch = db.query_one(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
        (company["id"],)
    )

    if not branch:
        return jsonify(error="تعذر تحديد الفرع للقيد المستورد"), 400

    # ========================================================
    # فحص جميع القيود قبل إنشاء أي حسابات أو قيود.
    # هذا يمنع الاستيراد الجزئي إذا وجد قيد في فترة مغلقة.
    # ========================================================
    prepared_groups = []

    for g_ in groups:
        dt = g_["date"] or date.today()

        period = _period_for_date(dt, company["id"])
        if not period:
            return jsonify(
                error=(
                    f"لا توجد سنة مالية/فترة محاسبية تغطي "
                    f"تاريخ القيد {dt}"
                )
            ), 400

        period_error = _open_period_error(dt)
        if period_error:
            return jsonify(
                error=(
                    f"لا يمكن استيراد القيد {g_['movement']} "
                    f"بتاريخ {dt}: {period_error}"
                )
            ), 400

        prepared_groups.append((g_, dt, period))

    added = 0
    drafts_count = 0
    new_accounts = 0
    skipped = len(errors)

    for g_, dt, period in prepared_groups:
        lines = []

        for ln in g_["lines"]:
            acc = db.query_one(
                "SELECT id FROM accounts WHERE acc_no=?",
                (ln["acc_no"],)
            )

            if not acc:
                name = ln["name"] or f"حساب مستورد {ln['acc_no']}"
                acc_id = db.execute(
                    "INSERT INTO accounts (acc_no, name, type) VALUES (?,?,'')",
                    (ln["acc_no"], name)
                )
                new_accounts += 1
            else:
                acc_id = acc["id"]

            lines.append({
                "account_id": acc_id,
                "debit": ln["debit"],
                "credit": ln["credit"]
            })

        td = round(sum(l["debit"] for l in lines), 2)
        tc = round(sum(l["credit"] for l in lines), 2)
        balanced = abs(td - tc) <= 0.004

        status = "posted" if (balanced and post_flag) else "draft"

        if not balanced:
            drafts_count += 1

            if g_["movement"].startswith("#تلقائي"):
                errors.append(
                    f"قيد بتاريخ {g_['date']} غير متوازن "
                    f"(مدين {td:,.2f} / دائن {tc:,.2f}) وأُضيف كمسودة"
                )
            else:
                errors.append(
                    f"رقم الحركة {g_['movement']} غير متوازن "
                    f"(مدين {td:,.2f} / دائن {tc:,.2f}) وأُضيف كمسودة"
                )

        # ضمان عدم تكرار رقم الحركة.
        mv = g_["movement"]
        seq = 2

        while db.query_one(
            "SELECT id FROM journal_entries WHERE movement_no=?",
            (mv,)
        ):
            mv = f"{g_['movement']}-{seq}"
            seq += 1

        entry_region = (g_.get("region") or "").strip()

        if entry_region and entry_region not in regions:
            errors.append(
                f"رقم الحركة {mv}: منطقة «{entry_region}» غير معروفة "
                f"— استُخدمت {region}"
            )
            entry_region = ""

        if not entry_region:
            entry_region = region

        eid = db.execute(
            """INSERT INTO journal_entries
               (movement_no, entry_date, description, region, status,
                entry_type, created_by, company_id, branch_id,
                fiscal_year_id, period_id, currency_id, exchange_rate)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                mv,
                dt.isoformat(),
                g_["desc"],
                entry_region,
                status,
                "عادي",
                g.user["id"],
                company["id"],
                branch["id"],
                period["fiscal_year_id"],
                period["id"],
                company["currency_id"]
            )
        )

        for ln in lines:
            db.execute(
                """INSERT INTO journal_lines
                   (entry_id, account_id, debit, credit)
                   VALUES (?,?,?,?)""",
                (
                    eid,
                    ln["account_id"],
                    ln["debit"],
                    ln["credit"]
                )
            )

        added += 1

    db.audit(
        g.user["full_name"],
        "استيراد دفتر يومية",
        f"قيود: {added} | حسابات جديدة: {new_accounts}"
    )

    return jsonify(
        ok=True,
        added=added,
        drafts=drafts_count,
        new_accounts=new_accounts,
        skipped=skipped,
        errors=errors[:10]
    )


# ------------------------------------------------------------------
# APIs — الأستاذ العام
# ------------------------------------------------------------------

def ledger_query(acc_no, f, t, region):
    acc = db.query_one("SELECT * FROM accounts WHERE acc_no=?", (acc_no,))
    if not acc:
        return None, "رقم الحساب غير موجود"
    opening_net = acc["opening_balance"]
    sql_open = """SELECT COALESCE(SUM(jl.debit - jl.credit),0) s
                  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
                  WHERE jl.account_id=? AND je.status='posted'"""
    args_o = [acc["id"]]
    if region:
        sql_open += " AND je.region=?"
        args_o.append(region)
    if f:
        sql_open += " AND je.entry_date < ?"
        args_o.append(f)
    opening_net += (db.query_one(sql_open, args_o)["s"] or 0)

    where_p = ["jl.account_id = ?", "je.status = 'posted'"]
    args_p = [acc["id"]]
    if region:
        where_p.append("je.region = ?")
        args_p.append(region)
    if f:
        where_p.append("je.entry_date >= ?")
        args_p.append(f)
    if t:
        where_p.append("je.entry_date <= ?")
        args_p.append(t)
    rows = db.query(
        f"""SELECT je.entry_date AS date, je.movement_no, je.description, je.region,
                   jl.debit, jl.credit
            FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
            WHERE {' AND '.join(where_p)}
            ORDER BY je.entry_date, je.id, jl.id""",
        args_p,
    )
    balance = round(opening_net, 2)
    td = tc = 0.0
    out = []
    for rw in rows:
        rw["debit"] = round(rw["debit"], 2)
        rw["credit"] = round(rw["credit"], 2)
        td += rw["debit"]
        tc += rw["credit"]
        balance = round(balance + rw["debit"] - rw["credit"], 2)
        rw["balance"] = balance
        out.append(rw)
    totals = {"debit": round(td, 2), "credit": round(tc, 2), "balance": round(balance, 2)}
    return {"account": acc, "opening": round(opening_net, 2), "rows": out, "totals": totals}, None


@app.route("/api/ledger")
def api_ledger():
    acc_no = (request.args.get("acc_no") or "").strip()
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    if not acc_no:
        return jsonify(error="اختر رقم الحساب"), 400
    result, err = ledger_query(acc_no, f, t, region)
    if err:
        return jsonify(error=err), 404
    return jsonify(result)


# ------------------------------------------------------------------
# APIs — ميزان المراجعة
# ------------------------------------------------------------------

def trial_query(f, t, region):
    region_sql = "AND je.region=?" if region else ""
    base_args = [region] if region else []

    if f:
        openings = db.query(
            f"""SELECT a.id, a.acc_no, a.name, a.opening_balance ob,
                       COALESCE((
                           SELECT SUM(jl.debit - jl.credit)
                           FROM journal_lines jl
                           JOIN journal_entries je ON je.id=jl.entry_id
                           WHERE jl.account_id=a.id
                             AND je.status='posted'
                             AND je.entry_date<?
                             {('AND je.region=?' if region else '')}
                       ),0) pre
                FROM accounts a
                ORDER BY CAST(a.acc_no AS INTEGER), a.acc_no""",
            [f] + ([region] if region else []),
        )
    else:
        openings = db.query(
            """SELECT a.id, a.acc_no, a.name, a.opening_balance ob,
                      0 pre
               FROM accounts a""",
            [],
        )

    period = {
        r["account_id"]: r
        for r in db.query(
            f"""SELECT jl.account_id, SUM(jl.debit) d, SUM(jl.credit) c
                FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
                WHERE je.status='posted'
                  {('AND je.entry_date>=?' if f else '')}
                  {('AND je.entry_date<=?' if t else '')}
                  {region_sql}
                GROUP BY jl.account_id""",
            ([f] if f else []) + ([t] if t else []) + base_args,
        )
    }

    rows, tot = [], {"o_debit": 0.0, "o_credit": 0.0, "p_debit": 0.0,
                     "p_credit": 0.0, "f_debit": 0.0, "f_credit": 0.0}
    for o in openings:
        pd = round((period.get(o["id"], {}).get("d") or 0), 2)
        pc = round((period.get(o["id"], {}).get("c") or 0), 2)
        net_open = round((o["ob"] or 0) + (o["pre"] or 0), 2)
        od = net_open if net_open > 0 else 0.0
        oc = -net_open if net_open < 0 else 0.0
        net_final = round(net_open + pd - pc, 2)
        fd = net_final if net_final > 0 else 0.0
        fc = -net_final if net_final < 0 else 0.0
        if od == 0 and oc == 0 and pd == 0 and pc == 0:
            continue
        rows.append([o["acc_no"], o["name"], od, oc, pd, pc, fd, fc])
        tot["o_debit"] += od
        tot["o_credit"] += oc
        tot["p_debit"] += pd
        tot["p_credit"] += pc
        tot["f_debit"] += fd
        tot["f_credit"] += fc

    rows.sort(key=lambda r: (len(r[0]), r[0]))
    for k in tot:
        tot[k] = round(tot[k], 2)
    balanced = abs(tot["p_debit"] - tot["p_credit"]) <= 0.01
    return {"rows": rows, "totals": tot, "balanced": balanced}


@app.route("/api/trial-balance")
def api_trial():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    return jsonify(trial_query(f, t, region))


# ------------------------------------------------------------------
# الموازنة التقديرية
# ------------------------------------------------------------------

@app.route("/api/budget")
def api_budget():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    acc_type = (request.args.get("type") or "").strip()

    accounts = db.query(
        "SELECT id, acc_no, name, type, budget FROM accounts ORDER BY CAST(acc_no AS INTEGER), acc_no"
    )

    where_parts = ["je.status='posted'"]
    params: list = []
    if f:
        where_parts.append("je.entry_date >= ?")
        params.append(f)
    if t:
        where_parts.append("je.entry_date <= ?")
        params.append(t)
    if region:
        where_parts.append("je.region = ?")
        params.append(region)

    where = " AND ".join(where_parts)

    actuals = {}
    if accounts:
        placeholders = ",".join("?" * len(accounts))
        rows = db.query(
            f"""SELECT a.id,
                       COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE 0 END), 0) actual_debit,
                       COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE 0 END), 0) actual_credit
                FROM accounts a
                LEFT JOIN journal_lines jl ON jl.account_id = a.id
                LEFT JOIN journal_entries je ON je.id = jl.entry_id AND {where}
                WHERE a.id IN ({placeholders})
                GROUP BY a.id""",
            params + [a["id"] for a in accounts],
        )
        actuals = {r["id"]: {"debit": round(r["actual_debit"], 2), "credit": round(r["actual_credit"], 2)} for r in rows}

    results = []
    total_budget = 0
    total_actual = 0
    for a in accounts:
        if acc_type and a["type"] != acc_type:
            continue
        budget = a["budget"]
        act = actuals.get(a["id"], {"debit": 0, "credit": 0})
        if a["type"] in ("مصروفات",):
            actual = act["debit"]
        elif a["type"] in ("إيرادات",):
            actual = act["credit"]
        else:
            actual = act["debit"] - act["credit"]
        actual = round(actual, 2)
        variance = round(budget - actual, 2)
        pct = round((actual / budget * 100) if budget else 0, 1)
        if budget == 0:
            status = "بدون ميزانية"
        elif pct <= 70:
            status = "أقل من المتوقع"
        elif pct <= 90:
            status = "ضمن الخطة"
        elif pct <= 100:
            status = "قريب من الحد"
        elif pct <= 110:
            status = "يزيد قليلاً"
        else:
            status = "تجاوز الحد"
        total_budget += budget
        total_actual += actual
        results.append({
            "id": a["id"], "acc_no": a["acc_no"], "name": a["name"],
            "type": a["type"], "budget": budget, "actual": actual,
            "variance": variance, "pct": pct, "status": status,
        })

    total_var = round(total_budget - total_actual, 2)
    total_pct = round((total_actual / total_budget * 100) if total_budget else 0, 1)
    return jsonify(
        entries=results,
        summary={"budget": total_budget, "actual": total_actual,
                 "variance": total_var, "pct": total_pct},
    )


# ------------------------------------------------------------------
# التقارير المالية: قائمة الدخل / المركز المالي / التدفقات النقدية
# ------------------------------------------------------------------

def _posted_filter_parts(f, t, region):
    parts = ["je.status='posted'"]
    params: list = []
    if region:
        parts.append("je.region=?")
        params.append(region)
    if f:
        parts.append("je.entry_date>=?")
        params.append(f)
    if t:
        parts.append("je.entry_date<=?")
        params.append(t)
    return parts, params


def income_query(f, t, region):
    where, params = _posted_filter_parts(f, t, region)
    rows = db.query(
        f"""SELECT a.id, a.acc_no, a.name, a.type,
                   COALESCE(SUM(jl.debit), 0) d, COALESCE(SUM(jl.credit), 0) c
            FROM accounts a
            LEFT JOIN journal_lines jl ON jl.account_id = a.id
            LEFT JOIN journal_entries je ON je.id = jl.entry_id AND {' AND '.join(where)}
            WHERE a.type IN ('إيرادات', 'مصروفات')
            GROUP BY a.id
            ORDER BY CAST(a.acc_no AS INTEGER), a.acc_no""",
        params,
    )
    revenues, expenses = [], []
    tot_r = tot_e = 0.0
    for r in rows:
        if r["type"] == "إيرادات":
            amt = round(r["c"] - r["d"], 2)
            revenues.append({"acc_no": r["acc_no"], "name": r["name"], "amount": amt})
            tot_r += amt
        else:
            amt = round(r["d"] - r["c"], 2)
            expenses.append({"acc_no": r["acc_no"], "name": r["name"], "amount": amt})
            tot_e += amt
    tot_r, tot_e = round(tot_r, 2), round(tot_e, 2)
    return {"revenues": revenues, "revenues_total": tot_r,
            "expenses": expenses, "expenses_total": tot_e,
            "net": round(tot_r - tot_e, 2)}


def _balances_asof(t, region):
    """الرصيد النهائي لكل الحسابات حتى تاريخ t (+ المنطقة)"""
    where, params = [], []
    if region:
        where.append("je.region=?")
        params.append(region)
    if t:
        where.append("je.entry_date<=?")
        params.append(t)
    cond = (" AND " + " AND ".join(where)) if where else ""
    rows = db.query(
        f"""SELECT a.id, a.acc_no, a.name, a.type, a.opening_balance ob,
                   COALESCE(SUM(jl.debit - jl.credit), 0) mv
            FROM accounts a
            LEFT JOIN journal_lines jl ON jl.account_id = a.id
            LEFT JOIN journal_entries je ON je.id = jl.entry_id
                 AND je.status='posted'{cond}
            GROUP BY a.id
            ORDER BY CAST(a.acc_no AS INTEGER), a.acc_no""",
        params,
    )
    return {r["acc_no"]: {"id": r["id"], "acc_no": r["acc_no"], "name": r["name"],
                          "type": r["type"], "amount": round((r["ob"] or 0) + (r["mv"] or 0), 2)}
            for r in rows}


def balance_query(f, t, region):
    bal = _balances_asof(t, region)
    assets, liab_eq = [], []
    tot_a = tot_l = 0.0
    for a in bal.values():
        if a["type"] == "أصول":
            assets.append(a)
            tot_a += a["amount"]
        elif a["type"] in ("خصوم", "حقوق ملكية"):
            # رصيد الخصوم/حقوق الملكية الطبيعي دائن (سالب في الاتجاه المحاسبي)
            # فيُعرض بقيمته الموجبة في التقرير
            row = dict(a, amount=abs(a["amount"]))
            liab_eq.append(row)
            tot_l += row["amount"]
    net_cum = 0.0
    for a in bal.values():
        if a["type"] == "إيرادات":
            net_cum += -a["amount"]   # الرصيد الدائن لإيراد = قيمة موجبة
        elif a["type"] == "مصروفات":
            net_cum -= a["amount"]    # الرصيد المدين لمصروف
    net_cum = round(net_cum, 2)
    net_period = income_query(f, t, region)["net"]
    total_with_income = round(tot_l + net_cum, 2)
    diff = round(tot_a - total_with_income, 2)
    return {"assets": assets, "assets_total": round(tot_a, 2),
            "liabilities_equity": liab_eq, "liab_equity_total": round(tot_l, 2),
            "net_cumulative": net_cum, "net_period": net_period,
            "total_with_income": total_with_income, "difference": diff,
            "balanced": abs(diff) <= 0.02}


def cashflow_query(f, t, region):
    cash = db.query(
        """SELECT id, acc_no, name FROM accounts
           WHERE type='أصول'
             AND (name LIKE '%صندوق%' OR name LIKE '%بنك%' OR name LIKE '%نقد%')
           ORDER BY CAST(acc_no AS INTEGER), acc_no"""
    )
    rows, tot = [], {"opening": 0.0, "inflow": 0.0, "outflow": 0.0, "closing": 0.0}
    for a in cash:
        ob = db.query_one("SELECT opening_balance FROM accounts WHERE id=?", (a["id"],))["opening_balance"] or 0
        opening = ob
        if region or f:
            cond, params = ["jl.account_id=?", "je.status='posted'"], [a["id"]]
            if f:
                cond.append("je.entry_date<?")
                params.append(f)
            if region:
                cond.append("je.region=?")
                params.append(region)
            opening += (db.query_one(
                f"SELECT COALESCE(SUM(jl.debit - jl.credit), 0) s FROM journal_lines jl "
                f"JOIN journal_entries je ON je.id = jl.entry_id WHERE {' AND '.join(cond)}",
                params)["s"] or 0)
        inflow = outflow = 0.0
        cond, params = ["jl.account_id=?", "je.status='posted'"], [a["id"]]
        if f:
            cond.append("je.entry_date>=?")
            params.append(f)
        if t:
            cond.append("je.entry_date<=?")
            params.append(t)
        if region:
            cond.append("je.region=?")
            params.append(region)
        s = db.query_one(
            f"""SELECT COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE 0 END), 0) i,
                       COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE 0 END), 0) o
                FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
                WHERE {' AND '.join(cond)}""",
            params,
        )
        inflow, outflow = round(s["i"], 2), round(s["o"], 2)
        opening = round(opening, 2)
        closing = round(opening + inflow - outflow, 2)
        rows.append({"acc_no": a["acc_no"], "name": a["name"], "opening": opening,
                     "inflow": inflow, "outflow": outflow, "closing": closing})
        tot["opening"] += opening
        tot["inflow"] += inflow
        tot["outflow"] += outflow
        tot["closing"] += closing
    for k in tot:
        tot[k] = round(tot[k], 2)
    return {"rows": rows, "totals": tot, "net_flow": round(tot["inflow"] - tot["outflow"], 2)}


@app.route("/income-statement")
def income_statement_page():
    return render_template("income_statement.html")


@app.route("/balance-sheet")
def balance_sheet_page():
    return render_template("balance_sheet.html")


@app.route("/cash-flow")
def cash_flow_page():
    return render_template("cash_flow.html")


@app.route("/api/income-statement")
def api_income():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    return jsonify(income_query(f, t, region))


@app.route("/api/balance-sheet")
def api_balance():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    return jsonify(balance_query(f, t, region))


@app.route("/api/cash-flow")
def api_cashflow():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    return jsonify(cashflow_query(f, t, region))


@app.route("/income-statement/export.xlsx")
def export_income_xlsx():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    data = income_query(f, t, region)
    extra = f"المنطقة: {region}" if region else ""
    buf = xl.export_income(data, db.get_setting("company_name"), extra,
                           acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="قائمة الدخل.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/balance-sheet/export.xlsx")
def export_balance_xlsx():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    data = balance_query(f, t, region)
    extra = f"المنطقة: {region}" if region else ""
    buf = xl.export_balance(data, db.get_setting("company_name"), extra,
                            acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="المركز المالي.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/cash-flow/export.xlsx")
def export_cashflow_xlsx():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    data = cashflow_query(f, t, region)
    extra = f"المنطقة: {region}" if region else ""
    buf = xl.export_cashflow(data, db.get_setting("company_name"), extra,
                             acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="التدفقات النقدية.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------
# شجرة الحسابات
# ------------------------------------------------------------------

TYPE_LABELS = ["أصول", "خصوم", "حقوق ملكية", "إيرادات", "مصروفات"]

SUB_GROUP_LABELS = {
    "أصول": {"11": "أصول متداولة", "12": "حسابات مدينة", "13": "مخزون",
              "14": "أصول ثابتة", "15": "أخرى"},
    "خصوم": {"21": "خصوم متداولة", "22": "خصوم طويلة الأجل", "25": "أخرى"},
    "حقوق ملكية": {"31": "رأس المال والأرباح", "39": "أخرى"},
    "إيرادات": {"41": "إيرادات التشغيل", "42": "إيرادات أخرى", "49": "أخرى"},
    "مصروفات": {"51": "رواتب وأجور", "52": "خدمات", "53": "مصاريف إدارية",
                 "54": "سفر ومهمات", "55": "صرفيات أخرى", "59": "أخرى"},
}


@app.route("/api/accounts/tree")
def api_accounts_tree():
    accounts = db.query(
        "SELECT id, acc_no, name, type, budget FROM accounts ORDER BY CAST(acc_no AS INTEGER), acc_no"
    )
    tree = []
    for t in TYPE_LABELS:
        type_accounts = [a for a in accounts if a["type"] == t]
        sort_key = type(t).__name__
        if not type_accounts:
            continue
        subgroups = {}
        for a in type_accounts:
            code = str(a["acc_no"])[:2]
            label = SUB_GROUP_LABELS.get(t, {}).get(code, "حسابات عامة")
            subgroups.setdefault((code, label), []).append(a)
        nodes = []
        for (code, label), items in subgroups.items():
            nodes.append({
                "code": code, "label": label, "is_group": True, "children": [
                    {"id": a["id"], "acc_no": a["acc_no"], "name": a["name"],
                     "type": a["type"], "budget": a["budget"], "is_group": False}
                    for a in items
                ]
            })
        tree.append({"type": t, "is_type": True, "children": nodes})
    return jsonify(tree=tree)


# ------------------------------------------------------------------
# لوحة التحكم
# ------------------------------------------------------------------

@app.route("/api/dashboard")
def api_dashboard():
    stats = {
        "accounts": db.query_one("SELECT COUNT(*) c FROM accounts")["c"],
        "entries": db.query_one("SELECT COUNT(*) c FROM journal_entries")["c"],
        "drafts": db.query_one("SELECT COUNT(*) c FROM journal_entries WHERE status='draft'")["c"],
    }
    sums = db.query_one(
        """SELECT COALESCE(SUM(jl.debit),0) d, COALESCE(SUM(jl.credit),0) c
           FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
           WHERE je.status='posted'"""
    )
    stats["total_debit"] = round(sums["d"], 2)
    stats["total_credit"] = round(sums["c"], 2)
    recent_ids = db.query(
        "SELECT id FROM journal_entries ORDER BY id DESC LIMIT 8"
    )
    recent = [_entry_full(r["id"]) for r in recent_ids]
    return jsonify(stats=stats, recent=[e for e in recent if e])


# ------------------------------------------------------------------
# المستخدمون والإعدادات
# ------------------------------------------------------------------

@app.route("/api/users", methods=["POST"])
@admin_required
def api_users_create():
    d = request.json or {}
    username = _sanitize((d.get("username") or "").strip().lower())
    full_name = _sanitize((d.get("full_name") or "").strip())
    role = d.get("role") if d.get("role") in ROLES else "accountant"
    password = d.get("password") or ""
    if not username or not full_name or len(password) < 8:
        return jsonify(error="بيانات غير مكتملة (كلمة المرور 8 أحرف على الأقل)"), 400
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return jsonify(error="كلمة المرور يجب أن تحتوي على حروف وأرقام"), 400
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        return jsonify(error="اسم المستخدم موجود بالفعل"), 400
    db.execute(
        "INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
        (username, generate_password_hash(password), full_name, role),
    )
    db.audit(g.user["full_name"], "إضافة مستخدم", f"{username} ({ROLES[role]})")
    return jsonify(ok=True)


@app.route("/api/users/<int:uid>/password", methods=["POST"])
@admin_required
def api_users_password(uid):
    d = request.json or {}
    password = d.get("password") or ""
    if len(password) < 8:
        return jsonify(error="كلمة المرور 8 أحرف على الأقل"), 400
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return jsonify(error="كلمة المرور يجب أن تحتوي على حروف وأرقام"), 400
    u = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return jsonify(error="المستخدم غير موجود"), 404
    db.execute("UPDATE users SET password_hash=? WHERE id=?",
               (generate_password_hash(password), uid))
    db.audit(g.user["full_name"], "تغيير كلمة مرور", u["username"])
    return jsonify(ok=True)


@app.route("/api/users/<int:uid>/role", methods=["PUT", "POST"])
@admin_required
def api_users_role(uid):
    d = request.json or {}
    role = d.get("role")

    if role not in ROLES:
        return jsonify(error="الصلاحية غير صحيحة"), 400

    u = db.query_one(
        "SELECT * FROM users WHERE id=?",
        (uid,)
    )

    if not u:
        return jsonify(error="المستخدم غير موجود"), 404

    # منع المدير من تخفيض صلاحية حسابه الحالي
    if uid == g.user["id"] and role != "admin":
        return jsonify(error="لا يمكنك تخفيض صلاحية حسابك الحالي"), 400

    # منع إزالة صلاحية آخر مدير
    if u["role"] == "admin" and role != "admin":
        admins = db.query(
            "SELECT id FROM users WHERE role='admin'"
        )

        if len(admins) <= 1:
            return jsonify(error="لا يمكن تخفيض صلاحية آخر مدير نظام"), 400

    old_role = u["role"]

    if old_role == role:
        return jsonify(
            ok=True,
            message="الصلاحية لم تتغير"
        )

    db.execute(
        "UPDATE users SET role=? WHERE id=?",
        (role, uid)
    )

    db.audit(
        g.user["full_name"],
        "تغيير صلاحية مستخدم",
        f"{u['username']}: {ROLES.get(old_role, old_role)} ← {ROLES[role]}"
    )

    return jsonify(
        ok=True,
        username=u["username"],
        old_role=old_role,
        role=role
    )


@app.route("/api/users/<int:uid>", methods=["DELETE"])
@admin_required
def api_users_delete(uid):
    if uid == g.user["id"]:
        return jsonify(error="لا يمكنك حذف نفسك"), 400
    admins = db.query("SELECT id FROM users WHERE role='admin'")
    u = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return jsonify(error="المستخدم غير موجود"), 404
    if u["role"] == "admin" and len(admins) <= 1:
        return jsonify(error="لا يمكن حذف آخر مدير نظام"), 400
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.audit(g.user["full_name"], "حذف مستخدم", u["username"])
    return jsonify(ok=True)


@app.route("/api/me/password", methods=["POST"])
def api_me_password():
    d = request.json or {}
    old = d.get("old") or ""
    new = d.get("new") or ""
    if not check_password_hash(g.user["password_hash"], old):
        return jsonify(error="كلمة المرور الحالية غير صحيحة"), 400
    if len(new) < 8:
        return jsonify(error="كلمة المرور الجديدة 8 أحرف على الأقل"), 400
    if new == old:
        return jsonify(error="كلمة المرور الجديدة مختلفة عن الحالية"), 400
    if not re.search(r'[A-Za-z]', new) or not re.search(r'\d', new):
        return jsonify(error="كلمة المرور يجب أن تحتوي على حروف وأرقام"), 400
    db.execute("UPDATE users SET password_hash=? WHERE id=?",
               (generate_password_hash(new), g.user["id"]))
    db.audit(g.user["full_name"], "تغيير كلمة مروره الشخصية")
    return jsonify(ok=True)


# ------------------------------------------------------------------
# APIs — إدارة المناطق
# ------------------------------------------------------------------

@app.route("/api/regions", methods=["GET"])
@admin_required
def api_regions_list():
    return jsonify(regions=db.get_all_regions())


@app.route("/api/regions", methods=["POST"])
@admin_required
def api_regions_add():
    d = request.json or {}
    rid, err = db.add_region(d.get("name", ""))
    if err:
        return jsonify(error=err), 400
    db.audit(g.user["full_name"], "إضافة منطقة", d.get("name", "").strip())
    return jsonify(ok=True, id=rid)


@app.route("/api/regions/<int:rid>", methods=["PUT"])
@admin_required
def api_regions_rename(rid):
    d = request.json or {}
    err = db.rename_region(rid, d.get("name", ""))
    if err:
        return jsonify(error=err), 400
    db.audit(g.user["full_name"], "تعديل منطقة", d.get("name", "").strip())
    return jsonify(ok=True)


@app.route("/api/regions/<int:rid>/deactivate", methods=["POST"])
@admin_required
def api_regions_deactivate(rid):
    err = db.deactivate_region(rid)
    if err:
        return jsonify(error=err), 400
    db.audit(g.user["full_name"], "تعطيل منطقة", str(rid))
    return jsonify(ok=True)


@app.route("/api/regions/<int:rid>/activate", methods=["POST"])
@admin_required
def api_regions_activate(rid):
    err = db.activate_region(rid)
    if err:
        return jsonify(error=err), 400
    db.audit(g.user["full_name"], "تفعيل منطقة", str(rid))
    return jsonify(ok=True)


@app.route("/api/regions/<int:rid>", methods=["DELETE"])
@admin_required
def api_regions_delete(rid):
    err = db.delete_region(rid)
    if err:
        return jsonify(error=err), 400
    db.audit(g.user["full_name"], "حذف منطقة", str(rid))
    return jsonify(ok=True)



@app.route("/api/pos/default-warehouse", methods=["GET"])
def api_pos_default_warehouse():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400
    pref = db.query_one("SELECT default_warehouse_id FROM company_preferences WHERE company_id=?", (company_id,))
    warehouses = db.query("SELECT id, code, name FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY id", (company_id,))
    default_id = pref["default_warehouse_id"] if pref else None
    if len(warehouses) == 1:
        default_id = warehouses[0]["id"]
    warehouse = next((w for w in warehouses if default_id is not None and int(w["id"]) == int(default_id)), None)
    return jsonify(ok=True, default_warehouse_id=warehouse["id"] if warehouse else None, warehouse=warehouse)


@app.route("/api/company-configuration", methods=["GET", "POST"])
@admin_required
def api_company_configuration():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(error="لا توجد شركة في النظام"), 400

    import json

    allowed_modules = {
        "accounting", "inventory", "sales", "purchases",
        "customers_suppliers", "reports", "contracting",
        "real_estate"
    }
    allowed_activities = {
        "trade", "warehousing", "contracting", "real_estate"
    }

    if request.method == "GET":
        pref = db.query_one(
            """
            SELECT warehouse_mode, default_warehouse_id,
                   enabled_modules_json, activity_codes_json
            FROM company_preferences
            WHERE company_id=?
            """,
            (company_id,)
        )

        warehouses = db.query(
            """
            SELECT id, code, name, is_active
            FROM warehouses
            WHERE company_id=? AND is_active=1
            ORDER BY id
            """,
            (company_id,)
        )

        if pref:
            try:
                modules = json.loads(pref["enabled_modules_json"] or "[]")
            except (ValueError, TypeError):
                modules = []

            try:
                activities = json.loads(pref["activity_codes_json"] or "[]")
            except (ValueError, TypeError):
                activities = []

            mode = pref["warehouse_mode"] or "single"
            default_id = pref["default_warehouse_id"]
        else:
            modules = []
            activities = []
            mode = "single"
            default_id = None

        # اختيار تلقائي للعرض فقط عند وجود مخزن نشط واحد.
        if len(warehouses) == 1:
            default_id = warehouses[0]["id"]
        elif default_id is not None:
            valid_ids = {w["id"] for w in warehouses}
            if default_id not in valid_ids:
                default_id = None

        return jsonify(
            warehouse_mode=mode,
            default_warehouse_id=default_id,
            warehouses=warehouses,
            enabled_modules=[
                x for x in modules
                if isinstance(x, str) and x in allowed_modules
            ],
            activity_codes=[
                x for x in activities
                if isinstance(x, str) and x in allowed_activities
            ],
            available_modules=[
                {"code": "accounting", "name": "المحاسبة والقيود"},
                {"code": "inventory", "name": "إدارة المخزون والمخازن"},
                {"code": "sales", "name": "المبيعات ونقاط البيع"},
                {"code": "purchases", "name": "المشتريات"},
                {"code": "customers_suppliers", "name": "العملاء والموردون"},
                {"code": "reports", "name": "التقارير"},
                {"code": "contracting", "name": "المقاولات والمشروعات"},
                {"code": "real_estate", "name": "العقارات والبيع والتأجير"}
            ],
            available_activities=[
                {"code": "trade", "name": "التجارة والمبيعات"},
                {"code": "warehousing", "name": "إدارة المخازن"},
                {"code": "contracting", "name": "المقاولات"},
                {"code": "real_estate", "name": "العقارات"}
            ]
        )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="بيانات الإعدادات غير صحيحة"), 400

    mode = str(data.get("warehouse_mode") or "single").strip()
    if mode not in ("single", "multiple"):
        return jsonify(error="نمط المخازن غير صحيح"), 400

    modules = data.get("enabled_modules", [])
    activities = data.get("activity_codes", [])

    if not isinstance(modules, list) or any(
        not isinstance(x, str) or x not in allowed_modules for x in modules
    ):
        return jsonify(error="توجد وحدة غير معروفة ضمن الاختيارات"), 400

    if not isinstance(activities, list) or any(
        not isinstance(x, str) or x not in allowed_activities
        for x in activities
    ):
        return jsonify(error="يوجد نشاط غير معروف ضمن الاختيارات"), 400

    # إزالة التكرار مع الحفاظ على ترتيب الاختيارات.
    modules = list(dict.fromkeys(modules))
    activities = list(dict.fromkeys(activities))

    requested_id = data.get("default_warehouse_id")
    if requested_id in ("", None):
        requested_id = None
    else:
        try:
            requested_id = int(requested_id)
        except (TypeError, ValueError):
            return jsonify(error="المخزن الافتراضي غير صحيح"), 400

        warehouse = db.query_one(
            """
            SELECT id FROM warehouses
            WHERE id=? AND company_id=? AND is_active=1
            """,
            (requested_id, company_id)
        )
        if not warehouse:
            return jsonify(
                error="المخزن الافتراضي يجب أن يكون نشطًا وتابعًا للشركة الحالية"
            ), 400

    active_warehouses = db.query(
        """
        SELECT id FROM warehouses
        WHERE company_id=? AND is_active=1
        ORDER BY id
        """,
        (company_id,)
    )

    # عند وجود مخزن نشط وحيد نثبته تلقائيًا كافتراضي.
    if len(active_warehouses) == 1:
        requested_id = active_warehouses[0]["id"]

    try:
        db.execute(
            """
            INSERT INTO company_preferences (
                company_id, warehouse_mode, default_warehouse_id,
                enabled_modules_json, activity_codes_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(company_id) DO UPDATE SET
                warehouse_mode=excluded.warehouse_mode,
                default_warehouse_id=excluded.default_warehouse_id,
                enabled_modules_json=excluded.enabled_modules_json,
                activity_codes_json=excluded.activity_codes_json,
                updated_at=datetime('now','localtime')
            """,
            (
                company_id, mode, requested_id,
                json.dumps(modules, ensure_ascii=False),
                json.dumps(activities, ensure_ascii=False)
            )
        )
    except Exception:
        app.logger.exception("تعذر حفظ إعدادات الشركة")
        return jsonify(error="تعذر حفظ الإعدادات؛ راجع سجل البرنامج"), 500

    try:
        db.audit(
            g.user["full_name"],
            "تحديث إعدادات النشاط والوحدات والمخازن",
            f"company_id={company_id}; warehouse_mode={mode}"
        )
    except Exception:
        app.logger.exception("تعذر تسجيل سجل تدقيق إعدادات الشركة")

    return jsonify(ok=True, message="تم حفظ إعدادات الشركة بنجاح")


@app.route("/api/settings", methods=["POST"])
@admin_required
def api_settings():
    d = request.json or {}

    company = (d.get("company_name") or "").strip() or "اسم الشركة"
    pf = (d.get("period_from") or "").strip()
    pt = (d.get("period_to") or "").strip()

    tax_enabled_value = d.get("tax_enabled", False)
    if not isinstance(tax_enabled_value, bool):
        return jsonify(error="حالة تفعيل الضريبة غير صحيحة"), 400
    tax_name = (d.get("tax_name") or "الضريبة").strip() or "الضريبة"
    try:
        tax_rate = float(d.get("tax_rate", 0) or 0)
    except (TypeError, ValueError):
        return jsonify(error="نسبة الضريبة غير صحيحة"), 400
    if not math.isfinite(tax_rate) or not 0 <= tax_rate <= 100:
        return jsonify(error="نسبة الضريبة يجب أن تكون بين 0 و100"), 400

    # --------------------------------------------------------
    # التحقق من التواريخ
    # --------------------------------------------------------
    for v in (pf, pt):
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                return jsonify(error="صيغة تاريخ الفترة غير صحيحة"), 400

    # --------------------------------------------------------
    # العملة الأساسية للنظام
    # يتم تحديدها من إعدادات المسؤول فقط.
    # --------------------------------------------------------
    currency_id = d.get("currency_id")

    if currency_id in (None, ""):
        company_row = db.query_one(
            "SELECT currency_id FROM companies ORDER BY id LIMIT 1"
        )
        currency_id = company_row["currency_id"] if company_row else None
    else:
        try:
            currency_id = int(currency_id)
        except (TypeError, ValueError):
            return jsonify(error="العملة المحددة غير صالحة"), 400

    currency = db.query_one(
        """
        SELECT id, code, name, symbol, decimal_places, is_active
        FROM currencies
        WHERE id=?
        """,
        (currency_id,),
    )

    if not currency:
        return jsonify(error="العملة المحددة غير موجودة"), 400

    if not currency["is_active"]:
        return jsonify(error="لا يمكن اختيار عملة غير نشطة"), 400

    # --------------------------------------------------------
    # حفظ الإعدادات العامة
    # --------------------------------------------------------
    db.set_setting("company_name", company)
    db.set_setting("tax_enabled", "1" if tax_enabled_value else "0")
    db.set_setting("tax_name", tax_name)
    db.set_setting("tax_rate", str(tax_rate))
    db.set_setting("invoice_store_name", (d.get("invoice_store_name") or "").strip())
    db.set_setting("invoice_address", (d.get("invoice_address") or "").strip())
    db.set_setting("invoice_phone", (d.get("invoice_phone") or "").strip())
    db.set_setting("invoice_logo_text", (d.get("invoice_logo_text") or "").strip())
    db.set_setting("invoice_footer", (d.get("invoice_footer") or "").strip())
    db.set_setting("invoice_paper_size", (d.get("invoice_paper_size") or "A4").strip())
    db.set_setting("invoice_auto_print", "1" if str(d.get("invoice_auto_print") or "0") == "1" else "0")
    db.set_setting("invoice_show_cashier", "1" if str(d.get("invoice_show_cashier") or "0") == "1" else "0")
    db.set_setting("invoice_show_discount", "1" if str(d.get("invoice_show_discount") or "0") == "1" else "0")

    if pf:
        db.set_setting("period_from", pf)

    if pt:
        db.set_setting("period_to", pt)

    # --------------------------------------------------------
    # تحديث العملة الأساسية
    #
    # is_base = 1 للعملة المختارة فقط
    # companies.currency_id = العملة المختارة
    #
    # هذا لا يغير عملة القيود أو الفواتير التاريخية.
    # --------------------------------------------------------
    db.execute(
        """
        UPDATE currencies
        SET is_base = CASE WHEN id=? THEN 1 ELSE 0 END
        """,
        (currency_id,),
    )

    db.execute(
        """
        UPDATE companies
        SET currency_id=?
        WHERE id=(SELECT id FROM companies ORDER BY id LIMIT 1)
        """,
        (currency_id,),
    )

    # --------------------------------------------------------
    # التواقيع
    # --------------------------------------------------------
    sigs = d.get("signatures")

    if isinstance(sigs, list):
        clean = []

        for s in sigs:
            if not isinstance(s, dict):
                continue

            title = (s.get("title") or "").strip()
            name = (s.get("name") or "").strip()

            if title and name:
                clean.append({
                    "title": title,
                    "name": name,
                })

        db.set_setting(
            "signatures",
            json.dumps(clean, ensure_ascii=False)
        )

    # --------------------------------------------------------
    # سجل التدقيق
    # --------------------------------------------------------
    db.audit(
        g.user["full_name"],
        "تعديل الإعدادات",
        f"{company} | العملة الأساسية: {currency['code']} - {currency['name']}"
    )

    return jsonify(
        ok=True,
        currency={
            "id": currency["id"],
            "code": currency["code"],
            "name": currency["name"],
            "symbol": currency["symbol"],
            "decimal_places": currency["decimal_places"],
        }
    )


# ------------------------------------------------------------------
# النسخ الاحتياطي
# ------------------------------------------------------------------

@app.route("/api/backup", methods=["POST"])
@admin_required
def api_backup_create():
    try:
        name = db.create_backup(prefix="manual")
    except Exception as e:
        return jsonify(error=f"فشل إنشاء النسخة: {e}"), 500
    db.audit(g.user["full_name"], "نسخة احتياطية يدوية", name)
    return jsonify(ok=True, name=name)


@app.route("/api/backups")
@admin_required
def api_backups_list():
    return jsonify(backups=db.list_backups())


@app.route("/api/backups/<name>")
@admin_required
def api_backup_download(name):
    import re as _re
    if not db.SAFE_BACKUP_NAME.match(name):
        abort(400)
    path = db.BACKUP_DIR / name
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


@app.route("/api/restore/<name>", methods=["POST"])
@admin_required
def api_backup_restore(name):
    try:
        db.restore_backup(name)
    except (ValueError, FileNotFoundError) as e:
        return jsonify(error=f"فشل الاستعادة: {e}"), 400
    except Exception as e:
        return jsonify(error=f"فشل الاستعادة: {e}"), 500
    db.audit(g.user["full_name"], "استعادة نسخة احتياطية", name)
    return jsonify(ok=True, message="تمت الاستعادة بنجاح — أعد تسجيل الدخول")


@app.route("/api/restore-upload", methods=["POST"])
@admin_required
def api_backup_restore_upload():
    import sqlite3
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(error="اختر ملف نسخة احتياطية أولاً"), 400
    if Path(upload.filename).suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        return jsonify(error="صيغة الملف غير مدعومة"), 400
    if getattr(db, "DB_TYPE", "sqlite") != "sqlite":
        return jsonify(error="استعادة الملفات الخارجية متاحة لقاعدة SQLite فقط"), 400
    temp_name = f"manual-import-{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    temp_path = db.BACKUP_DIR / temp_name
    try:
        db.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        upload.save(temp_path)
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise ValueError("الملف فارغ أو غير صالح")
        with sqlite3.connect(str(temp_path)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not integrity or integrity[0] != "ok":
            raise ValueError("فشل فحص سلامة قاعدة البيانات")
        required = {"users", "products", "journal_entries"}
        missing = required - tables
        if missing:
            raise ValueError("الملف ليس نسخة متوافقة مع النظام؛ الجداول الناقصة: " + ", ".join(sorted(missing)))
        db.restore_backup(temp_name)
        db.audit(g.user["full_name"], "استعادة نسخة خارجية", temp_name)
        return jsonify(ok=True, message="تمت استعادة النسخة الخارجية بنجاح — أعد تسجيل الدخول")
    except (ValueError, FileNotFoundError) as e:
        return jsonify(error=f"فشل الاستعادة: {e}"), 400
    except Exception as e:
        return jsonify(error=f"فشل الاستعادة: {e}"), 500
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ------------------------------------------------------------------
# التصدير والقوالب
# ------------------------------------------------------------------

def _subtitle(extra_parts):
    pf, pt = db.get_setting("period_from"), db.get_setting("period_to")
    parts = [db.get_setting("company_name")]
    if pf or pt:
        parts.append(f"الفترة من {xl.fmt_date(pf)} إلى {xl.fmt_date(pt)}")
    for p in extra_parts:
        if p:
            parts.append(p)
    return parts


@app.route("/journal/export.xlsx")
def export_journal_xlsx():
    where, args_ = journal_filters(request.args)
    ids = db.query(
        f"""SELECT je.id FROM journal_entries je {where}
            ORDER BY je.entry_date, CAST(je.movement_no AS INTEGER)""",
        args_,
    )
    entries = [e for e in (_entry_full(r["id"]) for r in ids) if e]
    region = request.args.get("region", "")
    buf = xl.export_journal(entries, db.get_setting("company_name"),
                            f"المنطقة: {region}" if region else "",
                            acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="دفتر اليومية.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/ledger/export.xlsx")
def export_ledger_xlsx():
    acc_no = (request.args.get("acc_no") or "").strip()
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    result, err = ledger_query(acc_no, f, t, region)
    if err:
        abort(404)
    extra = f"المنطقة: {region}" if region else ""
    buf = xl.export_ledger(result["account"], result["opening"], result["rows"],
                           result["totals"], db.get_setting("company_name"), extra,
                           acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name=f"الأستاذ العام - {result['account']['name']}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/trial-balance/export.xlsx")
def export_trial_xlsx():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    data = trial_query(f, t, region)
    extra = f"المنطقة: {region}" if region else ""
    buf = xl.export_trial(data["rows"], data["totals"],
                          db.get_setting("company_name"), extra,
                          acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="ميزان المراجعة.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/budget/export.xlsx")
def export_budget_xlsx():
    f = (request.args.get("from") or "").strip()
    t = (request.args.get("to") or "").strip()
    region = (request.args.get("region") or "").strip()
    acc_type = (request.args.get("type") or "").strip()

    accounts = db.query(
        "SELECT id, acc_no, name, type, budget FROM accounts ORDER BY CAST(acc_no AS INTEGER), acc_no"
    )
    where_parts = ["je.status='posted'"]
    params: list = []
    if f:
        where_parts.append("je.entry_date >= ?")
        params.append(f)
    if t:
        where_parts.append("je.entry_date <= ?")
        params.append(t)
    if region:
        where_parts.append("je.region = ?")
        params.append(region)
    where = " AND ".join(where_parts)

    actuals = {}
    if accounts:
        placeholders = ",".join("?" * len(accounts))
        rows = db.query(
            f"""SELECT a.id,
                       COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE 0 END), 0) actual_debit,
                       COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE 0 END), 0) actual_credit
                FROM accounts a
                LEFT JOIN journal_lines jl ON jl.account_id = a.id
                LEFT JOIN journal_entries je ON je.id = jl.entry_id AND {where}
                WHERE a.id IN ({placeholders})
                GROUP BY a.id""",
            params + [a["id"] for a in accounts],
        )
        actuals = {r["id"]: {"debit": round(r["actual_debit"], 2), "credit": round(r["actual_credit"], 2)} for r in rows}

    entries = []
    total_budget = 0
    total_actual = 0
    for a in accounts:
        if acc_type and a["type"] != acc_type:
            continue
        budget = a["budget"]
        act = actuals.get(a["id"], {"debit": 0, "credit": 0})
        if a["type"] in ("مصروفات",):
            actual = act["debit"]
        elif a["type"] in ("إيرادات",):
            actual = act["credit"]
        else:
            actual = act["debit"] - act["credit"]
        actual = round(actual, 2)
        variance = round(budget - actual, 2)
        pct = round((actual / budget * 100) if budget else 0, 1)
        if budget == 0:
            status = "بدون ميزانية"
        elif pct <= 70:
            status = "أقل من المتوقع"
        elif pct <= 90:
            status = "ضمن الخطة"
        elif pct <= 100:
            status = "قريب من الحد"
        elif pct <= 110:
            status = "يزيد قليلاً"
        else:
            status = "تجاوز الحد"
        total_budget += budget
        total_actual += actual
        entries.append({"acc_no": a["acc_no"], "name": a["name"], "type": a["type"],
                         "budget": budget, "actual": actual, "variance": variance,
                         "pct": pct, "status": status})

    total_var = round(total_budget - total_actual, 2)
    total_pct = round((total_actual / total_budget * 100) if total_budget else 0, 1)
    summary = {"budget": total_budget, "actual": total_actual, "variance": total_var, "pct": total_pct}
    extra = f"المنطقة: {region}" if region else ""
    buf = xl.export_budget(entries, summary, db.get_setting("company_name"), extra,
                           acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="الموازنة التقديرية.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/accounts/export.xlsx")
def export_accounts_xlsx():
    accounts = db.query(
        "SELECT * FROM accounts ORDER BY CAST(acc_no AS INTEGER), acc_no"
    )
    buf = xl.export_accounts(accounts, db.get_setting("company_name"),
                             acc_user=g.user["full_name"], signatures=_signatures())
    return send_file(buf, as_attachment=True,
                     download_name="دليل حسابات.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/templates/accounts.xlsx")
def template_accounts():
    return send_file(xl.blank_template_accounts(), as_attachment=True,
                     download_name="دليل حسابات.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/templates/journal.xlsx")
def template_journal():
    return send_file(xl.blank_template_journal(), as_attachment=True,
                     download_name="دفتر اليومية.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------


# ===== SALES_FULL_BUILD_20261008 =====

@app.route("/api/sales/<int:sale_id>", methods=["PUT"])
@require_write
def api_sales_update(sale_id):
    company_id = _current_company_id()

    data = request.get_json(silent=True) or {}

    conn = db.connect()
    try:
        sale = conn.execute("""
            SELECT *
            FROM sales_invoices
            WHERE id = ?
              AND company_id = ?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({"ok": False, "error": "فاتورة البيع غير موجودة"}), 404

        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن تعديل فاتورة غير مسودة"
            }), 400

        customer_id = data.get("customer_id", sale["customer_id"])
        warehouse_id = data.get("warehouse_id", sale["warehouse_id"])
        branch_id = data.get("branch_id", sale["branch_id"])
        currency_id = data.get("currency_id", sale["currency_id"])
        fiscal_year_id = data.get("fiscal_year_id", sale["fiscal_year_id"])
        period_id = data.get("period_id", sale["period_id"])

        invoice_date = str(
            data.get("invoice_date", sale["invoice_date"]) or ""
        ).strip()

        payment_method = str(
            data.get("payment_method", sale["payment_method"]) or ""
        ).strip().lower()

        customer_invoice_no = str(
            data.get(
                "customer_invoice_no",
                sale["customer_invoice_no"] or ""
            ) or ""
        ).strip()

        notes = str(
            data.get("notes", sale["notes"] or "") or ""
        ).strip()

        if not invoice_date:
            return jsonify({"ok": False, "error": "تاريخ الفاتورة مطلوب"}), 400

        if payment_method not in (
            "cash", "credit", "bank_ahly", "bank_misr"
        ):
            return jsonify({
                "ok": False,
                "error": "طريقة الدفع غير صحيحة"
            }), 400

        if payment_method == "credit" and not customer_id:
            return jsonify({
                "ok": False,
                "error": "العميل مطلوب في البيع الآجل"
            }), 400

        if customer_id:
            customer = conn.execute("""
                SELECT id, name, partner_type, receivable_account_id, is_active
                FROM business_partners
                WHERE id=? AND company_id=?
            """, (customer_id, company_id)).fetchone()

            if not customer:
                return jsonify({
                    "ok": False,
                    "error": "العميل غير موجود"
                }), 404

            if customer["partner_type"] not in ("customer", "both"):
                return jsonify({
                    "ok": False,
                    "error": "الشريك المحدد ليس عميلًا"
                }), 400

            if not customer["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "العميل غير نشط"
                }), 400

        if warehouse_id:
            warehouse = conn.execute("""
                SELECT id, is_active
                FROM warehouses
                WHERE id=? AND company_id=?
            """, (warehouse_id, company_id)).fetchone()

            if not warehouse:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير موجود"
                }), 404

            if not warehouse["is_active"]:
                return jsonify({
                    "ok": False,
                    "error": "المخزن غير نشط"
                }), 400

        if branch_id:
            branch = conn.execute("""
                SELECT id
                FROM branches
                WHERE id=? AND company_id=?
            """, (branch_id, company_id)).fetchone()

            if not branch:
                return jsonify({
                    "ok": False,
                    "error": "الفرع غير موجود"
                }), 404

        if currency_id:
            currency = conn.execute("""
                SELECT id
                FROM currencies
                WHERE id=? AND is_active=1
            """, (currency_id,)).fetchone()

            if not currency:
                return jsonify({
                    "ok": False,
                    "error": "العملة غير موجودة أو غير نشطة"
                }), 404

        if fiscal_year_id:
            fy = conn.execute("""
                SELECT id
                FROM fiscal_years
                WHERE id=? AND company_id=?
            """, (fiscal_year_id, company_id)).fetchone()

            if not fy:
                return jsonify({
                    "ok": False,
                    "error": "السنة المالية غير موجودة"
                }), 404

        if period_id:
            period = conn.execute("""
                SELECT id, fiscal_year_id
                FROM accounting_periods
                WHERE id=?
            """, (period_id,)).fetchone()

            if not period:
                return jsonify({
                    "ok": False,
                    "error": "الفترة المحاسبية غير موجودة"
                }), 404

            if fiscal_year_id and int(period["fiscal_year_id"]) != int(fiscal_year_id):
                return jsonify({
                    "ok": False,
                    "error": "الفترة المحاسبية لا تتبع السنة المالية المحددة"
                }), 400

        exchange_rate = data.get("exchange_rate", sale["exchange_rate"])

        try:
            exchange_rate = float(exchange_rate)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "سعر الصرف غير صالح"
            }), 400

        if exchange_rate <= 0:
            return jsonify({
                "ok": False,
                "error": "سعر الصرف يجب أن يكون أكبر من صفر"
            }), 400

        conn.execute("""
            UPDATE sales_invoices
            SET
                customer_id=?,
                warehouse_id=?,
                branch_id=?,
                fiscal_year_id=?,
                period_id=?,
                currency_id=?,
                exchange_rate=?,
                customer_invoice_no=?,
                invoice_date=?,
                payment_method=?,
                notes=?,
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=?
        """, (
            customer_id,
            warehouse_id,
            branch_id,
            fiscal_year_id,
            period_id,
            currency_id,
            exchange_rate,
            customer_invoice_no,
            invoice_date,
            payment_method,
            notes,
            sale_id,
            company_id
        ))

        conn.execute(
            "INSERT INTO audit_log (user, action, details) VALUES (?,?,?)",
            (
                g.user["full_name"],
                "تعديل فاتورة بيع",
                f"تعديل بيانات فاتورة البيع {sale['invoice_no']}"
            )
        )

        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id,
            "invoice_no": sale["invoice_no"],
            "status": "draft"
        })

    except Exception as e:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"فشل تعديل فاتورة البيع: {e}"
        }), 500
    finally:
        conn.close()


@app.route("/api/sales/<int:sale_id>", methods=["DELETE"])
@require_write
def api_sales_delete(sale_id):
    company_id = _current_company_id()

    conn = db.connect()
    try:
        sale = conn.execute("""
            SELECT *
            FROM sales_invoices
            WHERE id=? AND company_id=?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404

        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن حذف فاتورة معتمدة"
            }), 400

        conn.execute("DELETE FROM sales_invoice_items WHERE invoice_id=?", (sale_id,))
        conn.execute("DELETE FROM sale_delivery_details WHERE sale_id=?", (sale_id,))

        conn.execute("""
            DELETE FROM sales_invoices
            WHERE id=? AND company_id=?
        """, (sale_id, company_id))

        conn.execute(
            "INSERT INTO audit_log (user, action, details) VALUES (?,?,?)",
            (
                g.user["full_name"],
                "حذف فاتورة بيع",
                f"حذف مسودة فاتورة البيع {sale['invoice_no']}"
            )
        )

        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id
        })

    except Exception as e:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"فشل حذف فاتورة البيع: {e}"
        }), 500
    finally:
        conn.close()


@app.route("/api/sales/<int:sale_id>/items/<int:item_id>", methods=["PUT"])
@require_write
def api_sales_item_update(sale_id, item_id):
    company_id = _current_company_id()

    data = request.get_json(silent=True) or {}

    conn = db.connect()
    try:
        sale = conn.execute("""
            SELECT id, status
            FROM sales_invoices
            WHERE id=? AND company_id=?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404

        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن تعديل أصناف فاتورة معتمدة"
            }), 400

        old_item = conn.execute("""
            SELECT *
            FROM sales_invoice_items
            WHERE id=? AND invoice_id=?
        """, (item_id, sale_id)).fetchone()

        if not old_item:
            return jsonify({
                "ok": False,
                "error": "الصنف غير موجود في الفاتورة"
            }), 404

        product_id = data.get("product_id", old_item["product_id"])
        unit_id = data.get("unit_id", old_item["unit_id"])
        quantity = data.get("quantity", old_item["quantity"])
        unit_price = data.get("unit_price", old_item["unit_price"])
        discount = data.get("discount", old_item["discount"])
        tax_rate = 0.0 if db.get_setting("tax_enabled", "0") != "1" else db.get_setting("tax_rate", "0")
        notes = data.get("notes", old_item["notes"] or "")

        try:
            product_id = int(product_id)
            unit_id = int(unit_id)
            quantity = float(quantity)
            unit_price = float(unit_price)
            discount = float(discount)
            tax_rate = float(tax_rate)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "بيانات الصنف غير صالحة"
            }), 400

        if quantity <= 0:
            return jsonify({
                "ok": False,
                "error": "الكمية يجب أن تكون أكبر من صفر"
            }), 400

        if unit_price < 0 or discount < 0:
            return jsonify({
                "ok": False,
                "error": "السعر والخصم لا يمكن أن يكونا سالبين"
            }), 400

        if tax_rate < 0 or tax_rate > 100:
            return jsonify({
                "ok": False,
                "error": "نسبة الضريبة يجب أن تكون بين 0 و100"
            }), 400

        product = conn.execute("""
            SELECT id, name, is_active, is_stock_item, allow_decimal_qty
            FROM products
            WHERE id=? AND company_id=?
        """, (product_id, company_id)).fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "المنتج غير موجود"
            }), 404

        if not product["is_active"] or not product["is_stock_item"]:
            return jsonify({
                "ok": False,
                "error": "المنتج غير صالح للبيع المخزني"
            }), 400

        if not product["allow_decimal_qty"] and not quantity.is_integer():
            return jsonify({
                "ok": False,
                "error": "هذا المنتج لا يسمح بكمية عشرية"
            }), 400

        unit = conn.execute("""
            SELECT id
            FROM units
            WHERE id=? AND company_id=? AND is_active=1
        """, (unit_id, company_id)).fetchone()

        if not unit:
            return jsonify({
                "ok": False,
                "error": "الوحدة غير موجودة أو غير نشطة"
            }), 404

        gross = round(quantity * unit_price, 2)
        if discount > gross:
            return jsonify({
                "ok": False,
                "error": "الخصم أكبر من إجمالي السطر"
            }), 400

        taxable = round(gross - discount, 2)
        tax_amount = round(taxable * tax_rate / 100, 2)
        line_total = round(taxable + tax_amount, 2)

        conn.execute("""
            UPDATE sales_invoice_items
            SET
                product_id=?,
                unit_id=?,
                quantity=?,
                unit_price=?,
                discount=?,
                tax_rate=?,
                tax_amount=?,
                line_total=?,
                notes=?
            WHERE id=? AND invoice_id=?
        """, (
            product_id,
            unit_id,
            quantity,
            unit_price,
            discount,
            tax_rate,
            tax_amount,
            line_total,
            str(notes or "").strip(),
            item_id,
            sale_id
        ))

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM sales_invoice_items
            WHERE invoice_id=?
        """, (sale_id,)).fetchone()

        subtotal = round(float(totals["subtotal"]), 2)
        discount_total = round(float(totals["discount_total"]), 2)
        tax_total = round(float(totals["tax_total"]), 2)
        grand_total = round(float(totals["grand_total"]), 2)

        conn.execute("""
            UPDATE sales_invoices
            SET
                subtotal=?,
                discount_total=?,
                tax_total=?,
                grand_total=?,
                remaining_amount=?
                    - COALESCE(paid_amount, 0),
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=?
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            grand_total,
            sale_id,
            company_id
        ))

        conn.commit()

        return jsonify({
            "ok": True,
            "item_id": item_id,
            "sale_id": sale_id,
            "quantity": quantity,
            "unit_price": unit_price,
            "discount": discount,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "line_total": line_total,
            "totals": {
                "subtotal": subtotal,
                "discount_total": discount_total,
                "tax_total": tax_total,
                "grand_total": grand_total
            }
        })

    except Exception as e:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"فشل تعديل صنف البيع: {e}"
        }), 500
    finally:
        conn.close()


@app.route("/api/sales/<int:sale_id>/items/<int:item_id>", methods=["DELETE"])
@require_write
def api_sales_item_delete(sale_id, item_id):
    company_id = _current_company_id()

    conn = db.connect()
    try:
        sale = conn.execute("""
            SELECT id, status
            FROM sales_invoices
            WHERE id=? AND company_id=?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404

        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن حذف صنف من فاتورة معتمدة"
            }), 400

        item = conn.execute("""
            SELECT id
            FROM sales_invoice_items
            WHERE id=? AND invoice_id=?
        """, (item_id, sale_id)).fetchone()

        if not item:
            return jsonify({
                "ok": False,
                "error": "الصنف غير موجود في الفاتورة"
            }), 404

        conn.execute("""
            DELETE FROM sales_invoice_items
            WHERE id=? AND invoice_id=?
        """, (item_id, sale_id))

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM sales_invoice_items
            WHERE invoice_id=?
        """, (sale_id,)).fetchone()

        subtotal = round(float(totals["subtotal"]), 2)
        discount_total = round(float(totals["discount_total"]), 2)
        tax_total = round(float(totals["tax_total"]), 2)
        grand_total = round(float(totals["grand_total"]), 2)

        conn.execute("""
            UPDATE sales_invoices
            SET
                subtotal=?,
                discount_total=?,
                tax_total=?,
                grand_total=?,
                remaining_amount=MAX(? - COALESCE(paid_amount,0), 0),
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=?
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            grand_total,
            sale_id,
            company_id
        ))

        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id,
            "item_id": item_id,
            "totals": {
                "subtotal": subtotal,
                "discount_total": discount_total,
                "tax_total": tax_total,
                "grand_total": grand_total
            }
        })

    except Exception as e:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"فشل حذف صنف البيع: {e}"
        }), 500
    finally:
        conn.close()


@app.route("/api/sales/<int:sale_id>/approve", methods=["POST"])
@require_write
def api_sales_approve(sale_id):
    company_id = _current_company_id()
    req_data = request.get_json(silent=True) or {}
    requested_paid = req_data.get("paid_amount")
    invoice_discount = req_data.get("invoice_discount", 0)
    requested_payments = req_data.get("payments") or {}
    override_payment_method = req_data.get("payment_method")
    delivery_data = req_data.get("delivery_data")

    conn = db.connect()
    try:
        # قفل الكتابة مبكرًا حتى لا يعتمد طلبان الفاتورة المسودة نفسها بالتوازي.
        conn.execute("BEGIN IMMEDIATE")
        sale = conn.execute("""
            SELECT
                si.*,
                bp.name AS customer_name,
                bp.receivable_account_id,
                w.name AS warehouse_name
            FROM sales_invoices si
            LEFT JOIN business_partners bp
              ON bp.id=si.customer_id
             AND bp.company_id=si.company_id
            LEFT JOIN warehouses w
              ON w.id=si.warehouse_id
             AND w.company_id=si.company_id
            WHERE si.id=? AND si.company_id=?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404

        effective_payment_method = override_payment_method or sale["payment_method"]
        delivery_info = None
        if delivery_data is not None:
            if not isinstance(delivery_data, dict): return jsonify({"ok": False, "error": "بيانات التوصيل غير صحيحة"}), 400
            try:
                driver_id = int(delivery_data.get("driver_id") or 0)
                region_id = int(delivery_data.get("region_id") or 0)
            except (TypeError, ValueError): return jsonify({"ok": False, "error": "المندوب أو المنطقة غير صحيحة"}), 400
            driver = conn.execute("SELECT id, name FROM delivery_drivers WHERE id=? AND company_id=? AND is_active=1", (driver_id, company_id)).fetchone()
            region = conn.execute("SELECT id, name, price FROM delivery_services WHERE id=? AND company_id=? AND is_active=1", (region_id, company_id)).fetchone()
            phone = str(delivery_data.get("phone") or "").strip()
            address = str(delivery_data.get("addr") or "").strip()
            if not driver or not region or not phone or not address: return jsonify({"ok": False, "error": "بيانات المندوب والمنطقة والعنوان غير مكتملة"}), 400
            delivery_fee = float(region["price"] or 0)
            if not math.isfinite(delivery_fee) or delivery_fee < 0: return jsonify({"ok": False, "error": "رسوم المنطقة غير صالحة"}), 400
            delivery_info = {"driver_id": driver_id, "region_id": region_id, "rep": driver["name"], "region_name": region["name"], "phone": phone, "address": address, "fees": round(delivery_fee, 2)}
            effective_payment_method = "credit"
            requested_payments = {}
            requested_paid = 0

        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع ليست مسودة"
            }), 400

        if not sale["warehouse_id"]:
            return jsonify({
                "ok": False,
                "error": "المخزن مطلوب لاعتماد فاتورة البيع"
            }), 400

        warehouse = conn.execute("""
            SELECT id, is_active
            FROM warehouses
            WHERE id=? AND company_id=?
        """, (sale["warehouse_id"], company_id)).fetchone()

        if not warehouse or not warehouse["is_active"]:
            return jsonify({
                "ok": False,
                "error": "المخزن غير موجود أو غير نشط"
            }), 400

        if effective_payment_method == "credit":
            if not sale["customer_id"]:
                return jsonify({
                    "ok": False,
                    "error": "العميل مطلوب للبيع الآجل"
                }), 400

            if not sale["receivable_account_id"]:
                return jsonify({
                    "ok": False,
                    "error": "العميل لا يملك حسابًا للذمم المدينة"
                }), 400

        items = conn.execute("""
            SELECT
                sii.*,
                p.name AS product_name,
                p.code AS product_code,
                p.base_unit_id,
                p.allow_decimal_qty,
                p.is_stock_item
            FROM sales_invoice_items sii
            JOIN products p ON p.id=sii.product_id
            WHERE sii.invoice_id=?
            ORDER BY sii.id
        """, (sale_id,)).fetchall()

        if not items:
            return jsonify({
                "ok": False,
                "error": "لا يمكن اعتماد فاتورة بدون أصناف"
            }), 400

        # يتم ربط الوردية وتحديث إجماليها بعد حساب الإجمالي النهائي والتحقق من الاعتماد.

        subtotal = 0.0
        discount_total = 0.0
        tax_total = 0.0
        grand_total = 0.0

        for item in items:
            qty = float(item["quantity"])
            price = float(item["unit_price"])
            discount = float(item["discount"])
            tax_rate = float(item["tax_rate"])

            if qty <= 0:
                return jsonify({
                    "ok": False,
                    "error": f"الكمية غير صالحة للصنف {item['product_name']}"
                }), 400

            if price < 0 or discount < 0:
                return jsonify({
                    "ok": False,
                    "error": f"السعر أو الخصم غير صالح للصنف {item['product_name']}"
                }), 400

            if tax_rate < 0 or tax_rate > 100:
                return jsonify({
                    "ok": False,
                    "error": f"الضريبة غير صالحة للصنف {item['product_name']}"
                }), 400

            if not item["is_stock_item"]:
                return jsonify({
                    "ok": False,
                    "error": f"الصنف {item['product_name']} ليس صنفًا مخزنيًا"
                }), 400

            if not item["allow_decimal_qty"] and not qty.is_integer():
                return jsonify({
                    "ok": False,
                    "error": f"الصنف {item['product_name']} لا يسمح بكمية عشرية"
                }), 400

            gross = round(qty * price, 2)

            if discount > gross:
                return jsonify({
                    "ok": False,
                    "error": f"خصم الصنف {item['product_name']} أكبر من إجماليه"
                }), 400

            net = round(gross - discount, 2)
            tax = round(net * tax_rate / 100, 2)
            total = round(net + tax, 2)

            subtotal += gross
            discount_total += discount
            tax_total += tax
            grand_total += total

        subtotal = round(subtotal, 2)
        discount_total = round(discount_total, 2)
        tax_total = round(tax_total, 2)
        grand_total = round(grand_total, 2)

        try:
            invoice_discount = float(invoice_discount or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "قيمة خصم الفاتورة غير صالحة"}), 400

        if not math.isfinite(invoice_discount) or invoice_discount < 0 or invoice_discount > grand_total:
            return jsonify({"ok": False, "error": "خصم الفاتورة غير صالح"}), 400

        grand_total = round(grand_total - invoice_discount, 2)
        discount_total = round(discount_total + invoice_discount, 2)
        if delivery_info is not None:
            grand_total = round(grand_total + delivery_info["fees"], 2)

        if grand_total <= 0:
            return jsonify({
                "ok": False,
                "error": "إجمالي الفاتورة يجب أن يكون أكبر من صفر"
            }), 400

        # التحقق من إجمالي الكمية المطلوبة لكل صنف قبل أي حركة.
        requested_by_product = {}
        for item in items:
            product_id = int(item["product_id"])
            requested_by_product[product_id] = (
                requested_by_product.get(product_id, 0.0)
                + float(item["quantity"])
            )

        stock_by_product = {}
        for product_id, requested_qty in requested_by_product.items():
            stock = conn.execute("""
                SELECT id, quantity
                FROM warehouse_stock
                WHERE company_id=?
                  AND warehouse_id=?
                  AND product_id=?
            """, (
                company_id,
                sale["warehouse_id"],
                product_id
            )).fetchone()

            current_qty = float(stock["quantity"]) if stock else 0.0

            if requested_qty > current_qty:
                product_name = next(
                    (
                        item["product_name"]
                        for item in items
                        if int(item["product_id"]) == product_id
                    ),
                    str(product_id)
                )
                return jsonify({"ok": False, "error": f"المخزون غير كافٍ للصنف {product_name}. المتاح: {current_qty:g}، المطلوب: {requested_qty:g}"}), 400

            stock_by_product[product_id] = (stock, current_qty)

        # الاحتفاظ برصيد متدرج حتى لا يطغى بند مكرر على تحديث بند آخر.
        stock_checks = []
        running_qty_by_product = {
            product_id: current_qty
            for product_id, (stock, current_qty) in stock_by_product.items()
        }

        for item in items:
            product_id = int(item["product_id"])
            stock, current_qty = stock_by_product[product_id]
            stock_checks.append((
                item,
                stock,
                running_qty_by_product[product_id]
            ))
            running_qty_by_product[product_id] -= float(item["quantity"])

        # الحسابات المدينة: نقدي وشام كاش وبطاقة
        debit_lines = []
        debit_partner_id = None

        if effective_payment_method == "credit":
            try:
                cash_amount = float(requested_payments.get("cash", 0) or 0)
                sham_amount = float(requested_payments.get("sham_cash", 0) or 0)
                card_amount = float(requested_payments.get("card", 0) or 0)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "تفاصيل الدفع غير صالحة"}), 400

            amounts = (cash_amount, sham_amount, card_amount)
            if not all(math.isfinite(v) and v >= 0 for v in amounts):
                return jsonify({"ok": False, "error": "مبالغ الدفع غير صالحة"}), 400

            paid_now = round(sum(amounts), 2)
            if paid_now > grand_total + 0.01:
                return jsonify({"ok": False, "error": "الدفعة أكبر من إجمالي الفاتورة الآجلة"}), 400

            remaining_due = round(max(0.0, grand_total - paid_now), 2)
            debit_lines = [
                (account_id, amount, None)
                for account_id, amount in (
                    (228, cash_amount),
                    (229, sham_amount),
                    (230, card_amount),
                )
                if amount > 0
            ]
            if remaining_due > 0:
                debit_lines.append((
                    int(sale["receivable_account_id"]),
                    remaining_due,
                    sale["customer_id"]
                ))
        elif effective_payment_method == "mixed":
            try:
                cash_amount = float(requested_payments.get("cash", 0) or 0)
                sham_amount = float(requested_payments.get("sham_cash", 0) or 0)
                card_amount = float(requested_payments.get("card", 0) or 0)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "تفاصيل الدفع غير صالحة"}), 400

            amounts = (cash_amount, sham_amount, card_amount)
            if not all(math.isfinite(v) and v >= 0 for v in amounts):
                return jsonify({"ok": False, "error": "مبالغ الدفع غير صالحة"}), 400

            if sum(amounts) + 0.01 < grand_total:
                return jsonify({"ok": False, "error": "إجمالي الدفع أقل من قيمة الفاتورة"}), 400
            change_amount = max(0.0, round(sum(amounts) - grand_total, 2))
            if change_amount > cash_amount + 0.01:
                return jsonify({"ok": False, "error": "النقد المقبوض غير كافٍ لإعادة الباقي نقدًا"}), 400

            debit_lines = [
                (account_id, amount, None)
                for account_id, amount in (
                    (228, max(0.0, cash_amount - change_amount)),
                    (229, sham_amount),
                    (230, card_amount),
                )
                if amount > 0
            ]
            if not debit_lines:
                return jsonify({"ok": False, "error": "أدخل مبلغ الدفع"}), 400
        elif effective_payment_method == "cash":
            debit_lines = [(228, grand_total, None)]
        elif effective_payment_method == "bank_ahly":
            debit_lines = [(229, grand_total, None)]
        elif effective_payment_method == "bank_misr":
            debit_lines = [(230, grand_total, None)]
        else:
            return jsonify({"ok": False, "error": "طريقة الدفع غير مدعومة"}), 400

        for account_id, amount, partner_id in debit_lines:
            account = conn.execute("""
                SELECT id FROM accounts
                WHERE id=? AND company_id=? AND is_active=1
            """, (account_id, company_id)).fetchone()
            if not account:
                return jsonify({"ok": False, "error": f"حساب الدفع {account_id} غير موجود أو غير نشط"}), 400

        sales_account = conn.execute("""
            SELECT id
            FROM accounts
            WHERE acc_no='4101'
              AND company_id=?
              AND is_active=1
        """, (company_id,)).fetchone()

        vat_account = conn.execute("""
            SELECT id
            FROM accounts
            WHERE acc_no='2202'
              AND company_id=?
              AND is_active=1
        """, (company_id,)).fetchone()

        if not sales_account:
            return jsonify({
                "ok": False,
                "error": "حساب إيرادات المبيعات 4101 غير موجود"
            }), 400

        if not vat_account:
            return jsonify({
                "ok": False,
                "error": "حساب ضريبة المبيعات 2202 غير موجود"
            }), 400

        # الفترة المحاسبية
        period = _period_for_date(
            sale["invoice_date"],
            company_id
        )

        if not period:
            return jsonify({
                "ok": False,
                "error": "لا توجد فترة محاسبية لهذا التاريخ"
            }), 400

        period_error = _open_period_error(
            sale["invoice_date"]
        )

        if period_error:
            return jsonify(ok=False, error=period_error), 400

        fiscal_year_id = sale["fiscal_year_id"] or period["fiscal_year_id"]
        period_id = sale["period_id"] or period["id"]

        branch = conn.execute("""
            SELECT id
            FROM branches
            WHERE company_id=?
            ORDER BY id
            LIMIT 1
        """, (company_id,)).fetchone()

        branch_id = sale["branch_id"] or (
            branch["id"] if branch else None
        )

        if not branch_id:
            return jsonify({
                "ok": False,
                "error": "لا يوجد فرع للشركة"
            }), 400

        # رقم الحركة المحاسبية
        movement_no = next_movement_no()

        while conn.execute("""
            SELECT 1
            FROM journal_entries
            WHERE movement_no=?
        """, (movement_no,)).fetchone():
            movement_no += 1

        description = f"اعتماد فاتورة بيع {sale['invoice_no']}"

        cur = conn.execute("""
            INSERT INTO journal_entries (
                movement_no,
                entry_date,
                description,
                region,
                status,
                entry_type,
                created_by,
                company_id,
                branch_id,
                fiscal_year_id,
                period_id,
                currency_id,
                exchange_rate
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            movement_no,
            sale["invoice_date"],
            description,
            "المركز الرئيسي",
            "posted",
            "عادي",
            g.user["id"],
            company_id,
            branch_id,
            fiscal_year_id,
            period_id,
            sale["currency_id"],
            sale["exchange_rate"]
        ))

        entry_id = cur.lastrowid

        # مدين: سطر لكل وسيلة دفع
        for account_id, amount, partner_id in debit_lines:
            conn.execute("""
                INSERT INTO journal_lines (
                    entry_id, account_id, debit, credit, partner_id
                ) VALUES (?, ?, ?, ?, ?)
            """, (entry_id, account_id, amount, 0, partner_id))

        # دائن: المبيعات قبل الضريبة
        sales_net = round(grand_total - tax_total, 2)

        conn.execute("""
            INSERT INTO journal_lines (
                entry_id,
                account_id,
                debit,
                credit,
                partner_id
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            entry_id,
            sales_account["id"],
            0,
            sales_net,
            None
        ))

        # دائن: ضريبة المبيعات
        if tax_total > 0:
            conn.execute("""
                INSERT INTO journal_lines (
                    entry_id,
                    account_id,
                    debit,
                    credit,
                    partner_id
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                entry_id,
                vat_account["id"],
                0,
                tax_total,
                None
            ))

        # خصم المخزون وإنشاء حركة out
        for item, stock, current_qty in stock_checks:
            qty = float(item["quantity"])
            new_qty = round(current_qty - qty, 6)

            if stock:
                conn.execute("""
                    UPDATE warehouse_stock
                    SET
                        quantity=?,
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                """, (
                    new_qty,
                    stock["id"]
                ))
            else:
                return jsonify({
                    "ok": False,
                    "error": "رصيد المخزون غير موجود"
                }), 400

            conn.execute("""
                INSERT INTO stock_movements (
                    company_id,
                    warehouse_id,
                    product_id,
                    unit_id,
                    movement_type,
                    quantity,
                    reference_type,
                    reference_id,
                    movement_date,
                    notes,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                company_id,
                sale["warehouse_id"],
                item["product_id"],
                item["unit_id"],
                "out",
                qty,
                "sale",
                sale_id,
                sale["invoice_date"],
                f"اعتماد فاتورة بيع {sale['invoice_no']}",
                g.user["id"]
            ))

        if effective_payment_method == "credit":
            paid_amount = round(sum((
                float(requested_payments.get("cash", 0) or 0),
                float(requested_payments.get("sham_cash", 0) or 0),
                float(requested_payments.get("card", 0) or 0),
            )), 2)
            remaining_amount = round(max(0.0, grand_total - paid_amount), 2)
        else:
            try:
                paid_amount = float(requested_paid) if requested_paid is not None else grand_total
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "المبلغ المدفوع غير صالح"}), 400
            if not math.isfinite(paid_amount) or paid_amount + 0.01 < grand_total:
                return jsonify({"ok": False, "error": "المبلغ المدفوع يجب أن يساوي إجمالي الفاتورة بعد الخصم"}), 400
            paid_amount = grand_total
            remaining_amount = 0.0

        approval_update = conn.execute("""
            UPDATE sales_invoices
            SET
                subtotal=?,
                discount_total=?,
                tax_total=?,
                grand_total=?,
                paid_amount=?,
                remaining_amount=?,
                payment_method=?,
                status='approved',
                journal_entry_id=?,
                approved_by=?,
                fiscal_year_id=?,
                period_id=?,
                branch_id=?,
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=? AND status='draft'
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            paid_amount,
            remaining_amount,
            effective_payment_method,
            entry_id,
            g.user["id"],
            fiscal_year_id,
            period_id,
            branch_id,
            sale_id,
            company_id
        ))
        if approval_update.rowcount != 1:
            raise RuntimeError("تعذر اعتماد الفاتورة؛ ربما تم اعتمادها مسبقًا")

        # تحديث الوردية بالقيمة النهائية بعد الخصومات، داخل نفس المعاملة لمنع العد المسبق أو المكرر.
        current_session = conn.execute(
            "SELECT id FROM cashier_sessions WHERE company_id=? AND user_id=? AND status='open' LIMIT 1",
            (company_id, g.user["id"])
        ).fetchone()
        if current_session:
            conn.execute("UPDATE sales_invoices SET session_id=? WHERE id=? AND company_id=?", (current_session["id"], sale_id, company_id))
            conn.execute(
                "UPDATE cashier_sessions SET total_sales=total_sales+?, invoice_count=invoice_count+1 WHERE id=?",
                (grand_total, current_session["id"])
            )

        conn.execute(
            "INSERT INTO audit_log (user, action, details) VALUES (?,?,?)",
            (
                g.user["full_name"],
                "اعتماد فاتورة بيع",
                f"اعتماد فاتورة البيع {sale['invoice_no']}"
            )
        )

        if delivery_info is not None:
            conn.execute("INSERT INTO sale_delivery_details (sale_id,company_id,rep,phone,address,fees,driver_id,region_id,region_name,delivery_status,collection_status) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sale_id) DO UPDATE SET company_id=excluded.company_id,rep=excluded.rep,phone=excluded.phone,address=excluded.address,fees=excluded.fees,driver_id=excluded.driver_id,region_id=excluded.region_id,region_name=excluded.region_name,delivery_status=excluded.delivery_status,collection_status=excluded.collection_status,receipt_entry_id=NULL,delivered_at=NULL,collected_at=NULL,collection_method=''",(sale_id,company_id,delivery_info["rep"],delivery_info["phone"],delivery_info["address"],delivery_info["fees"],delivery_info["driver_id"],delivery_info["region_id"],delivery_info["region_name"],"pending","pending"))

        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id,
            "invoice_no": sale["invoice_no"],
            "status": "approved",
            "journal_entry_id": entry_id,
            "movement_no": movement_no,
            "subtotal": subtotal,
            "discount_total": discount_total,
            "tax_total": tax_total,
            "grand_total": grand_total,
            "paid_amount": paid_amount,
            "remaining_amount": remaining_amount
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"فشل اعتماد فاتورة البيع: {e}"
        }), 500
    finally:
        conn.close()


@app.route("/api/sales/<int:sale_id>/recalculate", methods=["POST"])
@require_write
def api_sales_recalculate(sale_id):
    company_id = _current_company_id()
    req_data = request.get_json(silent=True) or {}

    conn = db.connect()
    try:
        sale = conn.execute("""
            SELECT id, status, paid_amount
            FROM sales_invoices
            WHERE id=? AND company_id=?
        """, (sale_id, company_id)).fetchone()

        if not sale:
            return jsonify({
                "ok": False,
                "error": "فاتورة البيع غير موجودة"
            }), 404

        if sale["status"] != "draft":
            return jsonify({
                "ok": False,
                "error": "لا يمكن إعادة حساب فاتورة معتمدة"
            }), 400

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                COALESCE(SUM(discount), 0) AS discount_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COALESCE(SUM(line_total), 0) AS grand_total
            FROM sales_invoice_items
            WHERE invoice_id=?
        """, (sale_id,)).fetchone()

        subtotal = round(float(totals["subtotal"]), 2)
        discount_total = round(float(totals["discount_total"]), 2)
        tax_total = round(float(totals["tax_total"]), 2)
        grand_total = round(float(totals["grand_total"]), 2)
        paid_amount = round(float(sale["paid_amount"] or 0), 2)
        remaining = max(round(grand_total - paid_amount, 2), 0)

        conn.execute("""
            UPDATE sales_invoices
            SET
                subtotal=?,
                discount_total=?,
                tax_total=?,
                grand_total=?,
                remaining_amount=?,
                updated_at=datetime('now','localtime')
            WHERE id=? AND company_id=?
        """, (
            subtotal,
            discount_total,
            tax_total,
            grand_total,
            remaining,
            sale_id,
            company_id
        ))

        delivery_data = req_data.get("delivery_data")
        if delivery_data is not None:
            if not isinstance(delivery_data, dict):
                raise ValueError("بيانات التوصيل غير صحيحة")

            rep = str(delivery_data.get("rep") or "").strip()
            phone = str(delivery_data.get("phone") or "").strip()
            address = str(delivery_data.get("addr") or "").strip()
            try:
                fees = float(delivery_data.get("fees") if delivery_data.get("fees") not in (None, "") else 0)
            except (TypeError, ValueError):
                raise ValueError("رسوم التوصيل غير صحيحة")

            if not math.isfinite(fees) or fees < 0:
                raise ValueError("رسوم التوصيل يجب أن تكون رقمًا محدودًا غير سالب")
            if not rep or not phone or not address:
                raise ValueError("بيانات التوصيل غير مكتملة")

            conn.execute("""
                INSERT INTO sale_delivery_details
                    (sale_id, company_id, rep, phone, address, fees)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(sale_id) DO UPDATE SET
                    company_id=excluded.company_id,
                    rep=excluded.rep,
                    phone=excluded.phone,
                    address=excluded.address,
                    fees=excluded.fees
            """, (sale_id, company_id, rep, phone, address, fees))
        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id,
            "totals": {
                "subtotal": subtotal,
                "discount_total": discount_total,
                "tax_total": tax_total,
                "grand_total": grand_total,
                "paid_amount": paid_amount,
                "remaining_amount": remaining
            }
        })

    except Exception as e:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"فشل إعادة حساب الفاتورة: {e}"
        }), 500
    finally:
        conn.close()


@app.route("/print/sale/<int:sale_id>")
def print_sale(sale_id):
    company_id = _current_company_id()
    if not company_id:
        abort(404)
    sale = db.query_one("""
        SELECT
            si.*,
            bp.name AS customer_name,
            w.name AS warehouse_name
        FROM sales_invoices si
        LEFT JOIN business_partners bp
          ON bp.id = si.customer_id
         AND bp.company_id = si.company_id
        LEFT JOIN warehouses w
          ON w.id = si.warehouse_id
         AND w.company_id = si.company_id
        WHERE si.id = ? AND si.company_id = ?
    """, (sale_id, company_id))
    if not sale:
        abort(404)
    if sale["status"] != "approved":
        abort(403)
    items = db.query("""
        SELECT
            sii.*,
            p.code AS product_code,
            p.name AS product_name,
            u.name AS unit_name
        FROM sales_invoice_items sii
        LEFT JOIN products p ON p.id = sii.product_id
        LEFT JOIN units u ON u.id = sii.unit_id
        WHERE sii.invoice_id = ?
        ORDER BY sii.id
    """, (sale_id,))
    delivery_details = db.query_one("""
        SELECT rep, phone, address, fees, region_name
        FROM sale_delivery_details
        WHERE sale_id = ? AND company_id = ?
    """, (sale_id, company_id))

    return render_template(
        "sale_print.html",
        sale=sale,
        items=items,
        delivery_details=delivery_details
    )


@app.route("/api/settings/upload-logo", methods=["POST"])
@admin_required
def api_upload_logo():
    if "logo" not in request.files:
        return jsonify(error="لم يتم إرسال ملف"), 400
    file = request.files["logo"]
    if not file.filename:
        return jsonify(error="اسم الملف فارغ"), 400
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "webp"):
        return jsonify(error="صيغة الصورة غير مدعومة"), 400
    upload_dir = Path("static/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = "logo." + ("png" if ext == "png" else ("jpg" if ext in ("jpg", "jpeg") else "webp"))
    filepath = upload_dir / filename
    # حذف الشعار القديم بكل الصيغ
    for old_ext in ("png", "jpg", "jpeg", "webp"):
        old = upload_dir / ("logo." + old_ext)
        if old.exists():
            try: old.unlink()
            except Exception: pass
    file.save(str(filepath))
    rel_path = "uploads/" + filename
    db.set_setting("invoice_logo_path", rel_path)
    db.audit(g.user["full_name"], "رفع شعار الفاتورة", filename)
    return jsonify(ok=True, path=rel_path)


@app.route("/api/settings/remove-logo", methods=["POST"])
@admin_required
def api_remove_logo():
    upload_dir = Path("static/uploads")
    for ext in ("png", "jpg", "jpeg", "webp"):
        f = upload_dir / ("logo." + ext)
        if f.exists():
            try: f.unlink()
            except Exception: pass
    db.set_setting("invoice_logo_path", "")
    db.audit(g.user["full_name"], "حذف شعار الفاتورة", "")
    return jsonify(ok=True)


# ============================================================
# A2.1 — جلسات الكاشير (الورديات)
# ============================================================

@app.route("/api/cashier-sessions/open", methods=["POST"])
@require_write
def api_session_open():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    d = request.get_json(silent=True) or {}
    try:
        opening_cash = float(d.get("opening_cash") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="رصيد الافتتاح غير صالح"), 400
    if opening_cash < 0:
        return jsonify(ok=False, error="رصيد الافتتاح لا يمكن أن يكون سالبًا"), 400
    cashier_id = d.get("cashier_id")
    if cashier_id is not None:
        try:
            cashier_id = int(cashier_id)
        except (TypeError, ValueError):
            cashier_id = None
    # التحقق من وجود كاشير نشط
    cashier = None
    if cashier_id:
        cashier = db.query_one("SELECT id, full_name FROM cashiers WHERE id=? AND company_id=? AND is_active=1", (cashier_id, company_id))
        if not cashier:
            return jsonify(ok=False, error="الكاشير غير موجود أو معطّل"), 400
    # التحقق من عدم وجود وردية مفتوحة لنفس الكاشير
    if cashier_id:
        existing = db.query_one("SELECT id FROM cashier_sessions WHERE company_id=? AND cashier_id=? AND status=\x27open\x27", (company_id, cashier_id))
        if existing:
            return jsonify(ok=False, error="هذا الكاشير لديه وردية مفتوحة بالفعل"), 400
    else:
        existing = db.query_one("SELECT id FROM cashier_sessions WHERE company_id=? AND user_id=? AND status=\x27open\x27", (company_id, g.user["id"]))
        if existing:
            return jsonify(ok=False, error="لديك وردية مفتوحة بالفعل"), 400
    notes = (d.get("notes") or "").strip()
    if cashier_id:
        session_id = db.execute(
            "INSERT INTO cashier_sessions (company_id, user_id, cashier_id, opening_cash, status, notes) VALUES (?, ?, ?, ?, \x27open\x27, ?)",
            (company_id, g.user["id"], cashier_id, opening_cash, notes)
        )
    else:
        session_id = db.execute(
            "INSERT INTO cashier_sessions (company_id, user_id, opening_cash, status, notes) VALUES (?, ?, ?, \x27open\x27, ?)",
            (company_id, g.user["id"], opening_cash, notes)
        )
    cashier_name = cashier["full_name"] if cashier else g.user["full_name"]
    db.audit(g.user["full_name"], "فتح وردية كاشير", f"#{session_id} - {cashier_name} - رصيد: {opening_cash}")
    return jsonify(ok=True, session_id=session_id, cashier_name=cashier_name)


@app.route("/api/cashier-sessions/current", methods=["GET"])
def api_session_current():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one(
        """SELECT cs.*, u.full_name AS user_name,
                  cz.full_name AS cashier_name,
                  cz.permissions_json AS cashier_permissions
           FROM cashier_sessions cs
           LEFT JOIN users u ON u.id = cs.user_id
           LEFT JOIN cashiers cz ON cz.id = cs.cashier_id
           WHERE cs.company_id = ? AND cs.user_id = ? AND cs.status = \x27open\x27
           LIMIT 1""",
        (company_id, g.user["id"])
    )
    if not s:
        return jsonify(ok=True, session=None)
    # حساب الإجماليات الحية من الفواتير المعتمدة في هذه الوردية
    totals = db.query_one(
        """SELECT
               COUNT(*) AS invoice_count,
               COALESCE(SUM(grand_total), 0) AS total_sales,
               COALESCE(SUM(discount_total), 0) AS total_discount,
               COALESCE(SUM(tax_total), 0) AS total_tax
           FROM sales_invoices
           WHERE session_id = ? AND status = \x27approved\x27""",
        (s["id"],)
    ) or {}
    cash_sales = db.query_one(
        """SELECT COALESCE(SUM(grand_total), 0) AS total_cash
           FROM sales_invoices
           WHERE session_id = ? AND status = \x27approved\x27 AND payment_method = \x27cash\x27""",
        (s["id"],)
    ) or {}
    cash_returns = db.query_one(
        """SELECT COALESCE(SUM(refund_amount), 0) AS total_returns
           FROM sales_returns
           WHERE session_id = ?""",
        (s["id"],)
    ) or {}
    cash_expenses = db.query_one(
        """SELECT COALESCE(SUM(amount), 0) AS total_expenses
           FROM expenses
           WHERE session_id = ?
             AND description != 'مصروف إغلاق وردية'""",
        (s["id"],)
    ) or {}
    expected_cash = (
        float(s["opening_cash"] or 0)
        + float(cash_sales.get("total_cash") or 0)
        - float(cash_returns.get("total_returns") or 0)
        - float(cash_expenses.get("total_expenses") or 0)
    )
    return jsonify(
        ok=True,
        session={
            "id": s["id"],
            "opened_at": s["opened_at"],
            "opening_cash": float(s["opening_cash"] or 0),
            "user_name": s.get("user_name", ""),
            "cashier_name": s.get("cashier_name") or "",
            "cashier_permissions": s.get("cashier_permissions") or "{}",
            "notes": s.get("notes") or "",
            "invoice_count": int(totals.get("invoice_count") or 0),
            "total_sales": float(totals.get("total_sales") or 0),
            "total_discount": float(totals.get("total_discount") or 0),
            "total_tax": float(totals.get("total_tax") or 0),
            "cash_sales": float(cash_sales.get("total_cash") or 0),
            "expected_cash": expected_cash,
        }
    )


@app.route("/api/cashier-sessions/<int:sid>/close", methods=["POST"])
@require_write
def api_session_close(sid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one(
        "SELECT * FROM cashier_sessions WHERE id=? AND company_id=?",
        (sid, company_id)
    )
    if not s:
        return jsonify(ok=False, error="الوردية غير موجودة"), 404
    if s["status"] != "open":
        return jsonify(ok=False, error="الوردية مغلقة بالفعل"), 400
    # فقط صاحب الوردية أو المدير
    if s["user_id"] != g.user["id"] and g.user["role"] not in ("admin", "accountant"):
        return jsonify(ok=False, error="غير مصرح بإغلاق هذه الوردية"), 403
    d = request.get_json(silent=True) or {}
    try:
        actual_cash = float(d.get("actual_cash") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="النقد الفعلي غير صالح"), 400
    if actual_cash < 0:
        return jsonify(ok=False, error="النقد الفعلي لا يمكن أن يكون سالبًا"), 400
    notes = (d.get("notes") or "").strip()
    try:
        expense_amt = float(d.get("expenses_amount", 0) or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="قيمة مصروف الإغلاق غير صالحة"), 400
    if not math.isfinite(expense_amt) or expense_amt < 0:
        return jsonify(ok=False, error="مصروف الإغلاق يجب أن يكون رقمًا صالحًا غير سالب"), 400
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM cashier_sessions WHERE id=? AND company_id=?",
            (sid, company_id)
        ).fetchone()
        if not current:
            conn.rollback()
            return jsonify(ok=False, error="الوردية غير موجودة"), 404
        if current["status"] != "open":
            conn.rollback()
            return jsonify(ok=False, error="الوردية مغلقة بالفعل"), 400
        if current["user_id"] != g.user["id"] and g.user["role"] not in ("admin", "accountant"):
            conn.rollback()
            return jsonify(ok=False, error="غير مصرح بإغلاق هذه الوردية"), 403
        s = dict(current)
        if expense_amt > 0:
            conn.execute(
                "INSERT INTO expenses (company_id, session_id, user_id, description, amount) VALUES (?, ?, ?, ?, ?)",
                (company_id, sid, g.user["id"], "مصروف إغلاق وردية", expense_amt)
            )
        totals_row = conn.execute(
            """SELECT COUNT(*) AS invoice_count, COALESCE(SUM(grand_total), 0) AS total_sales
               FROM sales_invoices WHERE session_id = ? AND status = 'approved'""",
            (sid,)
        ).fetchone()
        totals = dict(totals_row) if totals_row else {}
        cash_sales_row = conn.execute(
            """SELECT COALESCE(SUM(grand_total), 0) AS total_cash
               FROM sales_invoices WHERE session_id = ? AND status = 'approved' AND payment_method = 'cash'""",
            (sid,)
        ).fetchone()
        cash_sales = dict(cash_sales_row) if cash_sales_row else {}
        returns_row = conn.execute(
            """SELECT COALESCE(SUM(refund_amount), 0) AS total_returns
               FROM sales_returns WHERE session_id = ?""",
            (s["id"],)
        ).fetchone()
        cash_returns = dict(returns_row) if returns_row else {}
        returns_total = float(cash_returns.get("total_returns") or 0)
        expected_cash = (
            float(s["opening_cash"] or 0)
            + float(cash_sales.get("total_cash") or 0)
            - expense_amt
            - returns_total
        )
        difference = actual_cash - expected_cash
        conn.execute(
            """UPDATE cashier_sessions
               SET closed_at = datetime('now','localtime'),
                   expected_cash = ?, actual_cash = ?, difference = ?,
                   total_sales = ?, invoice_count = ?, status = 'closed', notes = ?
               WHERE id = ? AND company_id = ? AND status = 'open'""",
            (expected_cash, actual_cash, difference,
             float(totals.get("total_sales") or 0),
             int(totals.get("invoice_count") or 0), notes, sid, company_id)
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise RuntimeError("لم يتم تحديث الوردية؛ ربما أُغلقت بالتزامن")
        conn.execute(
            "INSERT INTO audit_log (user, action, details) VALUES (?, ?, ?)",
            (g.user["full_name"], "إغلاق وردية كاشير", f"#{sid} - الفرق: {difference:+.2f}")
        )
        conn.commit()
    except Exception:
        conn.rollback()
        app.logger.exception("Cashier session close transaction failed")
        return jsonify(ok=False, error="تعذر إغلاق الوردية؛ تم التراجع عن جميع التغييرات"), 500
    finally:
        conn.close()

    # --- بداية إرسال تقرير الوردية بالبريد الإلكتروني ---
    try:
        import database as _db
        b_settings = _db.query_one("SELECT * FROM backup_settings WHERE company_id=?", (company_id,))
        if b_settings and b_settings.get("email_enabled") == 1 and b_settings.get("email_on_session_close") == 1:
            to_email = (b_settings.get("email_to") or "").strip()
            if to_email:
                import os
                import smtplib
                from email.message import EmailMessage
                
                subj = f"تقرير إغلاق وردية رقم #{sid}"
                body = f"""تم إغلاق الوردية بنجاح.
                
الموظف: {g.user['full_name']}
إجمالي المبيعات: {float(totals.get('total_sales') or 0):.2f}
عدد الفواتير: {int(totals.get('invoice_count') or 0)}
النقد المتوقع: {expected_cash:.2f}
النقد الفعلي: {actual_cash:.2f}
الفرق: {difference:+.2f}
إجمالي المرتجعات: {returns_total:.2f}
ملاحظات: {notes}"""
                
                msg = EmailMessage()
                msg.set_content(body)
                msg['Subject'] = subj
                msg['From'] = os.getenv('EMAIL_FROM', to_email)
                msg['To'] = to_email
                
                smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
                smtp_port = int(os.getenv('SMTP_PORT', 587))
                smtp_user = os.getenv('EMAIL_FROM', '')
                smtp_pass = os.getenv('EMAIL_PASSWORD', '')
                
                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
                print(f"Session close email sent to {to_email}")
    except Exception as e:
        print(f"Error sending session close email: {e}")
    # --- نهاية إرسال تقرير الوردية بالبريد الإلكتروني ---

    return jsonify(
        ok=True,
        session={
            "id": sid,
            "expected_cash": expected_cash,
            "actual_cash": actual_cash,
            "difference": difference,
            "total_sales": float(totals.get("total_sales") or 0),
            "invoice_count": int(totals.get("invoice_count") or 0),
        }
    )


@app.route("/api/cashier-sessions", methods=["GET"])
def api_sessions_list():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    # admin/accountant: كل الورديات. غيرهم: وردياتهم فقط.
    is_admin = g.user["role"] in ("admin", "accountant")
    if is_admin:
        rows = db.query(
            """SELECT cs.*, u.full_name AS user_name
               FROM cashier_sessions cs
               LEFT JOIN users u ON u.id = cs.user_id
               WHERE cs.company_id = ?
               ORDER BY cs.id DESC LIMIT 100""",
            (company_id,)
        )
    else:
        rows = db.query(
            """SELECT cs.*, u.full_name AS user_name
               FROM cashier_sessions cs
               LEFT JOIN users u ON u.id = cs.user_id
               WHERE cs.company_id = ? AND cs.user_id = ?
               ORDER BY cs.id DESC LIMIT 50""",
            (company_id, g.user["id"])
        )
    return jsonify(ok=True, sessions=rows)


@app.route("/api/cashier-sessions/<int:sid>", methods=["GET"])
def api_session_detail(sid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one(
        """SELECT cs.*, u.full_name AS user_name
           FROM cashier_sessions cs
           LEFT JOIN users u ON u.id = cs.user_id
           WHERE cs.id = ? AND cs.company_id = ?""",
        (sid, company_id)
    )
    if not s:
        return jsonify(ok=False, error="الوردية غير موجودة"), 404
    if s["user_id"] != g.user["id"] and g.user["role"] not in ("admin", "accountant"):
        return jsonify(ok=False, error="غير مصرح"), 403
    invoices = db.query(
        """SELECT id, invoice_no, invoice_date, grand_total, paid_amount, remaining_amount, payment_method, status
           FROM sales_invoices
           WHERE session_id = ?
           ORDER BY id""",
        (sid,)
    )
    return jsonify(ok=True, session=s, invoices=invoices)


# ============================================================
# A4.2 — تعليق واستئناف الفواتير
# ============================================================

@app.route("/api/sales/<int:sid>/hold", methods=["POST"])
@require_write
def api_sale_hold(sid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one(
        "SELECT id, status, is_held FROM sales_invoices WHERE id=? AND company_id=?",
        (sid, company_id)
    )
    if not s:
        return jsonify(ok=False, error="الفاتورة غير موجودة"), 404
    if s["status"] != "draft":
        return jsonify(ok=False, error="لا يمكن تعليق فاتورة معتمدة"), 400
    if s["is_held"]:
        return jsonify(ok=False, error="الفاتورة معلقة بالفعل"), 400
    db.execute("UPDATE sales_invoices SET is_held=1 WHERE id=?", (sid,))
    db.audit(g.user["full_name"], "تعليق فاتورة", f"#{sid}")
    return jsonify(ok=True)

@app.route("/api/sales/<int:sid>/resume", methods=["POST"])
@require_write
def api_sale_resume(sid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one(
        "SELECT id, status, is_held FROM sales_invoices WHERE id=? AND company_id=?",
        (sid, company_id)
    )
    if not s:
        return jsonify(ok=False, error="الفاتورة غير موجودة"), 404
    if s["status"] != "draft":
        return jsonify(ok=False, error="لا يمكن استئناف فاتورة معتمدة"), 400
    if not s["is_held"]:
        return jsonify(ok=False, error="الفاتورة غير معلقة"), 400
    db.execute("UPDATE sales_invoices SET is_held=0 WHERE id=?", (sid,))
    db.audit(g.user["full_name"], "استئناف فاتورة", f"#{sid}")
    return jsonify(ok=True)


@app.route("/api/sales/held", methods=["GET"])
def api_sales_held():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    rows = db.query(
        """SELECT id, invoice_no, invoice_date, grand_total, customer_id,
                  created_at, notes
           FROM sales_invoices
           WHERE company_id=? AND status=\x27draft\x27 AND is_held=1
           ORDER BY id DESC LIMIT 100""",
        (company_id,)
    )
    return jsonify(ok=True, sales=rows)

@app.route("/api/sales/<int:sid>/held", methods=["DELETE"])
@require_write
def api_sale_held_delete(sid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one(
        "SELECT id, status, is_held FROM sales_invoices WHERE id=? AND company_id=?",
        (sid, company_id)
    )
    if not s:
        return jsonify(ok=False, error="الفاتورة غير موجودة"), 404
    if s["status"] != "draft" or not s["is_held"]:
        return jsonify(ok=False, error="الفاتورة ليست معلقة"), 400
    db.execute("DELETE FROM sales_invoice_items WHERE invoice_id=?", (sid,))
    db.execute("DELETE FROM sales_invoices WHERE id=?", (sid,))
    db.audit(g.user["full_name"], "حذف فاتورة معلقة", f"#{sid}")
    return jsonify(ok=True)

# ============================================================
# A5 — APIs إدارة الكاشيرين
# ============================================================

DEFAULT_CASHIER_PERMS = {
    "can_sell": True,
    "can_open_session": True,
    "can_hold_sale": True,
    "can_cancel_draft": True,
    "can_view_own_reports": True,
    "can_apply_discount": False,
    "max_discount_pct": 0,
    "can_view_stock": False,
    "can_view_others_sessions": False,
}


@app.route("/api/cashiers", methods=["GET"])
@admin_required
def api_cashiers_list():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    rows = db.query(
        """SELECT id, username, full_name, phone, permissions_json,
                  is_active, created_at
           FROM cashiers
           WHERE company_id=? ORDER BY id DESC""",
        (company_id,)
    )
    return jsonify(ok=True, cashiers=rows)



@app.route("/api/cashiers", methods=["POST"])
@admin_required
def api_cashier_create():
    import time
    # حماية من الطلب المزدوج (الطلب الثاني في نفس الثانية)
    if not hasattr(api_cashier_create, "last_request"):
        api_cashier_create.last_request = {}
    
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip().lower()
    
    now = time.time()
    if username in api_cashier_create.last_request:
        if now - api_cashier_create.last_request[username] < 1.0:
            # تجاهل الطلب المكرر بصمت (إرجاع نجاح وهمي)
            return jsonify(ok=True, message="تمت الإضافة بنجاح (تم تجاهل الطلب المكرر)"), 200
    api_cashier_create.last_request[username] = now

    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip().lower()
    password = (d.get("password") or "").strip()
    full_name = (d.get("full_name") or "").strip()
    phone = (d.get("phone") or "").strip()
    permissions = d.get("permissions") or {}
    if not username or len(username) < 3:
        return jsonify(ok=False, error="اسم المستخدم 3 أحرف على الأقل"), 400
    if not password or len(password) < 4:
        return jsonify(ok=False, error="كلمة المرور 4 أحرف على الأقل"), 400
    if not full_name:
        return jsonify(ok=False, error="الاسم الكامل مطلوب"), 400
    existing = db.query_one(
        "SELECT id FROM cashiers WHERE company_id=? AND username=?",
        (company_id, username)
    )
    if existing:
        return jsonify(ok=False, error="اسم المستخدم موجود مسبقًا"), 400
    # دمج الصلاحيات الافتراضية مع المخصصة
    perms = dict(DEFAULT_CASHIER_PERMS)
    if isinstance(permissions, dict):
        for k, v in permissions.items():
            if k in perms:
                perms[k] = v
    pw_hash = generate_password_hash(password)
    try:
        cid = db.execute(
            """INSERT INTO cashiers (company_id, username, password_hash, full_name, phone, permissions_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (company_id, username, pw_hash, full_name, phone, json.dumps(perms, ensure_ascii=False))
        )
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            return jsonify(ok=False, error="اسم المستخدم موجود مسبقاً (خطأ تكرار)"), 400
        return jsonify(ok=False, error=f"خطأ في قاعدة البيانات: {str(e)}"), 500
    db.audit(g.user["full_name"], "إضافة كاشير", f"{username} / {full_name}")
    return jsonify(ok=True, id=cid)



@app.route("/api/cashiers/<int:cid>", methods=["PUT"])
@admin_required
def api_cashier_update(cid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    c = db.query_one("SELECT id FROM cashiers WHERE id=? AND company_id=?", (cid, company_id))
    if not c:
        return jsonify(ok=False, error="الكاشير غير موجود"), 404
    d = request.get_json(silent=True) or {}
    full_name = (d.get("full_name") or "").strip()
    phone = (d.get("phone") or "").strip()
    username = (d.get("username") or "").strip().lower()
    password = (d.get("password") or "").strip()
    permissions = d.get("permissions") or {}
    if not full_name:
        return jsonify(ok=False, error="الاسم الكامل مطلوب"), 400
    if not username or len(username) < 3:
        return jsonify(ok=False, error="اسم المستخدم 3 أحرف على الأقل"), 400
    existing = db.query_one(
        "SELECT id FROM cashiers WHERE company_id=? AND username=? AND id<>?",
        (company_id, username, cid)
    )
    if existing:
        return jsonify(ok=False, error="اسم المستخدم مستخدم من كاشير آخر"), 400
    perms = dict(DEFAULT_CASHIER_PERMS)
    if isinstance(permissions, dict):
        for k, v in permissions.items():
            if k in perms:
                perms[k] = v
    if password:
        if len(password) < 4:
            return jsonify(ok=False, error="كلمة المرور 4 أحرف على الأقل"), 400
        pw_hash = generate_password_hash(password)
        db.execute(
            "UPDATE cashiers SET username=?, password_hash=?, full_name=?, phone=?, permissions_json=?, updated_at=datetime(\x27now\x27,\x27localtime\x27) WHERE id=?",
            (username, pw_hash, full_name, phone, json.dumps(perms, ensure_ascii=False), cid)
        )
    else:
        db.execute(
            "UPDATE cashiers SET username=?, full_name=?, phone=?, permissions_json=?, updated_at=datetime(\x27now\x27,\x27localtime\x27) WHERE id=?",
            (username, full_name, phone, json.dumps(perms, ensure_ascii=False), cid)
        )
    db.audit(g.user["full_name"], "تعديل كاشير", f"#{cid} - {username}")
    return jsonify(ok=True)


@app.route("/api/cashiers/<int:cid>/toggle", methods=["POST"])
@admin_required
def api_cashier_toggle(cid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    c = db.query_one("SELECT id, is_active FROM cashiers WHERE id=? AND company_id=?", (cid, company_id))
    if not c:
        return jsonify(ok=False, error="الكاشير غير موجود"), 404
    new_state = 0 if c["is_active"] else 1
    db.execute("UPDATE cashiers SET is_active=? WHERE id=?", (new_state, cid))
    db.audit(g.user["full_name"], "تغيير حالة كاشير", f"#{cid} -> {new_state}")
    return jsonify(ok=True, is_active=new_state)



@app.route("/api/cashiers/<int:cid>/reset-password", methods=["POST"])
@admin_required
def api_cashier_reset_pw(cid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    c = db.query_one("SELECT id FROM cashiers WHERE id=? AND company_id=?", (cid, company_id))
    if not c:
        return jsonify(ok=False, error="الكاشير غير موجود"), 404
    d = request.get_json(silent=True) or {}
    password = (d.get("password") or "").strip()
    if not password or len(password) < 4:
        return jsonify(ok=False, error="كلمة المرور 4 أحرف على الأقل"), 400
    pw_hash = generate_password_hash(password)
    db.execute(
        "UPDATE cashiers SET password_hash=?, updated_at=datetime(\x27now\x27,\x27localtime\x27) WHERE id=?",
        (pw_hash, cid)
    )
    db.audit(g.user["full_name"], "إعادة تعيين كلمة سر كاشير", f"#{cid}")

@app.route("/api/cashier-login", methods=["POST"])
def api_cashier_login():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip().lower()
    password = (d.get("password") or "").strip()
    if not username or not password:
        return jsonify(ok=False, error="اسم المستخدم وكلمة المرور مطلوبان"), 400
    cashier = db.query_one(
        "SELECT id, username, password_hash, full_name, is_active, permissions_json FROM cashiers WHERE company_id=? AND username=?",
        (company_id, username)
    )
    if not cashier:
        return jsonify(ok=False, error="اسم المستخدم غير موجود"), 401
    if not cashier["is_active"]:
        return jsonify(ok=False, error="هذا الحساب معطّل"), 401
    if not check_password_hash(cashier["password_hash"], password):
        return jsonify(ok=False, error="كلمة المرور غير صحيحة"), 401
    return jsonify(
        ok=True,
        cashier={
            "id": cashier["id"],
            "username": cashier["username"],
            "full_name": cashier["full_name"],
            "permissions": cashier["permissions_json"]
        }
    )

    return jsonify(ok=True)


@app.route("/api/cashiers/<int:cid>", methods=["DELETE"])
@admin_required
def api_cashier_delete(cid):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    c = db.query_one("SELECT id FROM cashiers WHERE id=? AND company_id=?", (cid, company_id))
    if not c:
        return jsonify(ok=False, error="الكاشير غير موجود"), 404
    # هل له جلسات؟ إن نعم، نُعطّله فقط بدل الحذف
    sess = db.query_one("SELECT id FROM cashier_sessions WHERE cashier_id=? LIMIT 1", (cid,))
    if sess:
        db.execute("UPDATE cashiers SET is_active=0 WHERE id=?", (cid,))
        return jsonify(ok=True, message="تم تعطيل الكاشير (له سجل جلسات)")
    db.execute("DELETE FROM cashiers WHERE id=?", (cid,))
    db.audit(g.user["full_name"], "حذف كاشير", f"#{cid}")
    return jsonify(ok=True, message="تم الحذف")



@app.route("/api/send-backup", methods=["POST"])
@require_write
def api_send_backup():
    success, message = send_backup_email()
    return jsonify(ok=success, message=message)

@app.route("/api/backup-settings", methods=["GET"])
@admin_required
def api_get_backup_settings():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    s = db.query_one("SELECT * FROM backup_settings WHERE company_id=?", (company_id,))
    if not s:
        db.execute("INSERT INTO backup_settings (company_id) VALUES (?)", (company_id,))
        s = db.query_one("SELECT * FROM backup_settings WHERE company_id=?", (company_id,))
    return jsonify(ok=True, settings=s)

@app.route("/api/backup-settings", methods=["POST"])
@admin_required
def api_save_backup_settings():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة"), 400
    d = request.get_json(silent=True) or {}
    db.execute("""UPDATE backup_settings SET
        email_enabled=?, email_to=?, email_on_session_close=?, email_daily=?,
        backup_type=?, local_enabled=?, updated_at=datetime("now","localtime")
        WHERE company_id=?""", (
        int(d.get("email_enabled", 0)),
        (d.get("email_to") or "").strip(),
        int(d.get("email_on_session_close", 0)),
        int(d.get("email_daily", 0)),
        (d.get("backup_type") or "full").strip(),
        int(d.get("local_enabled", 0)),
        company_id
    ))
    return jsonify(ok=True)

@app.route("/api/backup-test", methods=["POST"])
@admin_required
def api_backup_test():
    from email_backup import send_backup_email
    try:
        ok, msg = send_backup_email()
        return jsonify(ok=ok, message=msg if ok else "", error=msg if not ok else "")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

if __name__ == "__main__":
    app.config["TESTING"] = True
    from scheduler import start_scheduler
    import logging
    from logging.handlers import RotatingFileHandler

    db.init_db()
    db.auto_monthly_backup()

    # تسجيل الأحداث في ملف بدل الشاشة (للتشغيل بدون نافذة CMD)
    INSTANCE_DIR = db.INSTANCE_DIR
    _handler = RotatingFileHandler(
        INSTANCE_DIR / "server.log", maxBytes=1_000_000,
        backupCount=2, encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("werkzeug").addHandler(_handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "127.0.0.1"

    import os as _os
    PORT = int(_os.environ.get("PORT", "5000"))

    # في النسخة المجمّعة (EXE) نفتح المتصفح تلقائيًا بعد إقلاع السيرفر
    if getattr(sys, "frozen", False):
        import threading
        import webbrowser
        threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    start_scheduler(24)

    

@app.route("/api/create-backup", methods=["POST"])
def api_create_backup():
    try:
        import database as db
        name = db.create_backup(prefix="manual")
        return jsonify(ok=True, message="تم إنشاء النسخة الاحتياطية بنجاح: " + name)
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/local-backup", methods=["POST"])
def api_local_backup():
    try:
        # 1. إنشاء نسخة في مجلد البرنامج (لتظهر في القائمة)
        name = db.create_backup(prefix="manual")
        # 2. إنشاء نسخة على الجهاز (المسار المحلي)
        from email_backup import local_backup
        ok, msg = local_backup()
        return jsonify(ok=True, message=f"تم إنشاء النسخة {name} ونسخة محلية")
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/pos-inventory", methods=["GET"])
def pos_inventory_page():
    return render_template("pos_inventory.html")


@app.route("/api/pos-inventory", methods=["GET"])
def api_pos_inventory_list():
    try:
        company_id = _current_company_id()
        if not company_id:
            return jsonify(ok=False, error="لا توجد شركة حالية"), 400

        warehouse = db.query_one(
            "SELECT id FROM warehouses WHERE id=3 AND company_id=? AND is_active=1",
            (company_id,),
        )
        if not warehouse:
            return jsonify(ok=False, error="المخزن الرئيسي غير موجود أو غير نشط"), 400

        prods = db.query(
            """
            SELECT p.id, p.name, p.code,
                   COALESCE(ws.quantity, 0) AS qty,
                   (SELECT pp.price FROM product_prices pp
                    WHERE pp.company_id=p.company_id
                      AND pp.product_id=p.id
                      AND pp.price_type='sale' AND pp.is_active=1
                    ORDER BY pp.is_default DESC, pp.id DESC LIMIT 1) AS sell_price
            FROM products p
            LEFT JOIN warehouse_stock ws
              ON ws.company_id=p.company_id
             AND ws.product_id=p.id
             AND ws.warehouse_id=?
            WHERE p.company_id=? AND p.is_active=1
            ORDER BY p.name
            """,
            (warehouse["id"], company_id),
        )
        result = [
            {"id": p["id"], "name": p["name"], "code": p["code"] or "-",
             "qty": p["qty"], "sell_price": p["sell_price"] or 0}
            for p in prods
        ]
        return jsonify(ok=True, products=result)
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/pos-inventory/purchase-return-options/<int:product_id>", methods=["GET"])
@require_write
def api_pos_purchase_return_options(product_id):
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة حالية"), 400
    try:
        with db.connect() as conn:
            warehouse = conn.execute(
                "SELECT id FROM warehouses WHERE id=3 AND company_id=? AND is_active=1",
                (company_id,),
            ).fetchone()
            if not warehouse:
                return jsonify(ok=False, error="مخزن نقطة البيع رقم 3 غير موجود أو غير نشط"), 400
            product = conn.execute(
                "SELECT id, is_stock_item FROM products WHERE id=? AND company_id=? AND is_active=1",
                (product_id, company_id),
            ).fetchone()
            if not product or not product["is_stock_item"]:
                return jsonify(ok=False, error="الصنف غير موجود أو غير مخزني"), 404
            stock = conn.execute(
                "SELECT COALESCE(quantity,0) AS qty FROM warehouse_stock WHERE company_id=? AND warehouse_id=3 AND product_id=?",
                (company_id, product_id),
            ).fetchone()
            pos_stock_qty = float(stock["qty"] or 0) if stock else 0.0
            rows = conn.execute("""
                SELECT pii.id AS purchase_item_id, pii.quantity AS original_qty,
                       pii.unit_price, pii.discount, pii.tax_rate, pii.tax_amount, pii.line_total,
                       pi.id AS purchase_id, pi.invoice_no, pi.invoice_date, pi.currency_id,
                       bp.name AS supplier_name, u.name AS unit_name,
                       u.decimal_places AS unit_decimal_places,
                       p.allow_decimal_qty, c.symbol AS currency_symbol,
                       pii.quantity - COALESCE((
                           SELECT SUM(pri.quantity)
                           FROM purchase_return_items pri
                           JOIN purchase_returns pr ON pr.id=pri.return_id
                           WHERE pri.purchase_item_id=pii.id
                       ),0) AS remaining_qty
                FROM purchase_invoice_items pii
                JOIN purchase_invoices pi ON pi.id=pii.invoice_id
                JOIN business_partners bp ON bp.id=pi.supplier_id AND bp.company_id=pi.company_id
                JOIN products p ON p.id=pii.product_id AND p.company_id=pi.company_id
                JOIN units u ON u.id=pii.unit_id AND u.company_id=pi.company_id
                LEFT JOIN currencies c ON c.id=pi.currency_id
                WHERE pi.company_id=? AND pi.status='approved'
                  AND pii.product_id=? AND pii.quantity > COALESCE((
                      SELECT SUM(pri.quantity)
                      FROM purchase_return_items pri
                      JOIN purchase_returns pr ON pr.id=pri.return_id
                      WHERE pri.purchase_item_id=pii.id
                  ),0)
                ORDER BY pi.invoice_date DESC, pi.id DESC, pii.id DESC
            """, (company_id, product_id)).fetchall()
            options = [{
                "purchase_item_id": int(row["purchase_item_id"]),
                "purchase_id": int(row["purchase_id"]),
                "invoice_no": row["invoice_no"],
                "invoice_date": row["invoice_date"],
                "supplier_name": row["supplier_name"],
                "original_qty": float(row["original_qty"] or 0),
                "remaining_qty": float(row["remaining_qty"] or 0),
                "unit_price": float(row["unit_price"] or 0),
                "discount": float(row["discount"] or 0),
                "tax_rate": float(row["tax_rate"] or 0),
                "tax_amount": float(row["tax_amount"] or 0),
                "line_total": float(row["line_total"] or 0),
                "unit_name": row["unit_name"] or "",
                "unit_decimal_places": int(row["unit_decimal_places"] or 0),
                "allow_decimal_qty": int(row["allow_decimal_qty"] or 0),
                "currency_symbol": row["currency_symbol"] or "",
            } for row in rows]
        return jsonify(ok=True, product_id=product_id, pos_stock_qty=pos_stock_qty, options=options)
    except Exception as exc:
        return jsonify(ok=False, error=f"تعذر تحميل فواتير الشراء: {exc}"), 500


@app.route("/api/pos-inventory/purchase-return", methods=["POST"])
@require_write
def api_pos_purchase_return():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة حالية"), 400
    data = request.get_json(silent=True) or {}
    try:
        purchase_item_id = int(data.get("purchase_item_id"))
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="بند فاتورة الشراء أو الكمية غير صالح"), 400
    request_id = str(data.get("request_id") or "").strip()
    reason = _sanitize(str(data.get("reason") or ""))[:250]
    if purchase_item_id <= 0 or not math.isfinite(quantity) or quantity <= 0:
        return jsonify(ok=False, error="الكمية أو بند الفاتورة غير صحيح"), 400
    if not request_id or len(request_id) > 120:
        return jsonify(ok=False, error="معرّف الطلب مطلوب وغير صالح"), 400
    payload_json = json.dumps({
        "purchase_item_id": purchase_item_id,
        "quantity": round(quantity, 6),
        "reason": reason,
    }, ensure_ascii=False, sort_keys=True)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous_request = conn.execute(
            "SELECT request_payload_json, response_json FROM purchase_returns WHERE company_id=? AND created_by=? AND request_id=?",
            (company_id, g.user["id"], request_id),
        ).fetchone()
        if previous_request:
            if previous_request["request_payload_json"] != payload_json:
                conn.rollback()
                return jsonify(ok=False, error="معرّف الطلب مستخدم لعملية مختلفة؛ أعد المحاولة بمعرّف جديد"), 409
            saved_response = json.loads(previous_request["response_json"] or "{}")
            conn.commit()
            return jsonify(saved_response), 200

        warehouse = conn.execute(
            "SELECT id FROM warehouses WHERE id=3 AND company_id=? AND is_active=1",
            (company_id,),
        ).fetchone()
        if not warehouse:
            conn.rollback()
            return jsonify(ok=False, error="مخزن نقطة البيع رقم 3 غير موجود أو غير نشط"), 400

        item = conn.execute("""
            SELECT pii.*, pi.id AS purchase_id, pi.invoice_no, pi.invoice_date,
                   pi.supplier_id, pi.warehouse_id AS invoice_warehouse_id,
                   pi.currency_id, pi.exchange_rate, pi.grand_total AS invoice_total,
                   pi.paid_amount, pi.payment_method, pi.branch_id,
                   pi.status AS invoice_status, bp.name AS supplier_name,
                   bp.payable_account_id, p.name AS product_name,
                   p.is_stock_item, p.allow_decimal_qty, u.decimal_places
            FROM purchase_invoice_items pii
            JOIN purchase_invoices pi ON pi.id=pii.invoice_id
            JOIN business_partners bp ON bp.id=pi.supplier_id AND bp.company_id=pi.company_id
            JOIN products p ON p.id=pii.product_id AND p.company_id=pi.company_id
            JOIN units u ON u.id=pii.unit_id AND u.company_id=pi.company_id
            WHERE pii.id=? AND pi.company_id=?
        """, (purchase_item_id, company_id)).fetchone()
        if not item or item["invoice_status"] != "approved" or not item["is_stock_item"]:
            conn.rollback()
            return jsonify(ok=False, error="بند فاتورة الشراء غير موجود أو الفاتورة غير معتمدة"), 404
        original_qty = float(item["quantity"] or 0)
        if original_qty <= 0:
            conn.rollback()
            return jsonify(ok=False, error="كمية بند الفاتورة الأصلية غير صحيحة"), 400
        decimal_places = max(0, min(6, int(item["decimal_places"] or 0)))
        rounded_quantity = round(quantity, decimal_places)
        if (not item["allow_decimal_qty"] and abs(quantity - round(quantity)) > 0.000001) or abs(quantity - rounded_quantity) > 0.000001:
            conn.rollback()
            return jsonify(ok=False, error=f"كمية الصنف يجب ألا تتجاوز {decimal_places} منازل عشرية"), 400
        already_returned = float(conn.execute(
            "SELECT COALESCE(SUM(quantity),0) AS qty FROM purchase_return_items WHERE purchase_item_id=?",
            (purchase_item_id,),
        ).fetchone()["qty"] or 0)
        if quantity > original_qty - already_returned + 0.000001:
            conn.rollback()
            return jsonify(ok=False, error=f"الكمية تتجاوز المتبقي من الفاتورة: {max(0, original_qty-already_returned):g}"), 400

        stock = conn.execute(
            "SELECT id, quantity FROM warehouse_stock WHERE company_id=? AND warehouse_id=3 AND product_id=?",
            (company_id, item["product_id"]),
        ).fetchone()
        if not stock or float(stock["quantity"] or 0) + 0.000001 < quantity:
            conn.rollback()
            return jsonify(ok=False, error="رصيد مخزن نقطة البيع لا يكفي لتنفيذ المرتجع"), 400
        if not item["payable_account_id"]:
            conn.rollback()
            return jsonify(ok=False, error="المورد غير مرتبط بحساب دائن"), 400

        return_date = date.today().isoformat()
        period = _period_for_date(return_date, company_id)
        period_error = _open_period_error(return_date, company_id)
        if not period or period_error:
            conn.rollback()
            return jsonify(ok=False, error=period_error or "لا توجد فترة محاسبية مفتوحة لتاريخ اليوم"), 400
        inventory_account = conn.execute(
            "SELECT id FROM accounts WHERE company_id=? AND acc_no='1301' AND is_active=1 LIMIT 1",
            (company_id,),
        ).fetchone()
        vat_account = conn.execute(
            "SELECT id FROM accounts WHERE company_id=? AND acc_no='1202' AND is_active=1 LIMIT 1",
            (company_id,),
        ).fetchone()
        supplier_account = conn.execute(
            "SELECT id FROM accounts WHERE id=? AND company_id=? AND is_active=1",
            (item["payable_account_id"], company_id),
        ).fetchone()
        if not inventory_account or not supplier_account:
            conn.rollback()
            return jsonify(ok=False, error="حساب المخزون أو حساب المورد غير موجود أو غير نشط"), 400
        branch_id = item["branch_id"]
        if not branch_id:
            branch = conn.execute(
                "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                (company_id,),
            ).fetchone()
            branch_id = branch["id"] if branch else None
        if not branch_id:
            conn.rollback()
            return jsonify(ok=False, error="لا يوجد فرع للشركة الحالية"), 400

        line_total = float(item["line_total"] or 0)
        tax_total = float(item["tax_amount"] or 0)
        discount_total = float(item["discount"] or 0)
        if line_total < 0 or tax_total < 0 or discount_total < 0:
            conn.rollback()
            return jsonify(ok=False, error="قيم بند فاتورة الشراء غير صحيحة"), 400
        before_ratio = already_returned / original_qty
        after_ratio = min(1.0, (already_returned + quantity) / original_qty)
        total_amount = round(round(line_total * after_ratio, 2) - round(line_total * before_ratio, 2), 2)
        tax_amount = round(round(tax_total * after_ratio, 2) - round(tax_total * before_ratio, 2), 2)
        discount_amount = round(round(discount_total * after_ratio, 2) - round(discount_total * before_ratio, 2), 2)
        net_amount = round(total_amount - tax_amount, 2)
        if net_amount < -0.005 or tax_amount < -0.005 or total_amount < -0.005:
            conn.rollback()
            return jsonify(ok=False, error="تعذر احتساب قيمة المرتجع من بيانات بند الفاتورة"), 400
        net_amount = max(0.0, net_amount)
        tax_amount = max(0.0, tax_amount)
        total_amount = round(net_amount + tax_amount, 2)
        if tax_amount > 0 and not vat_account:
            conn.rollback()
            return jsonify(ok=False, error="حساب ضريبة المدخلات 1202 مطلوب لأن قيمة ضريبة المرتجع أكبر من صفر"), 400

        movement_no = next_movement_no()
        while conn.execute("SELECT id FROM journal_entries WHERE movement_no=?", (movement_no,)).fetchone():
            movement_no = str(int(movement_no) + 1)
        entry_id = conn.execute("""
            INSERT INTO journal_entries
            (movement_no, entry_date, description, region, status, entry_type, created_by,
             company_id, branch_id, fiscal_year_id, period_id, currency_id, exchange_rate)
            VALUES (?, ?, ?, ?, 'posted', 'عادي', ?, ?, ?, ?, ?, ?, ?)
        """, (
            movement_no, return_date,
            f"مرتجع شراء {item['invoice_no']} - {item['supplier_name']}",
            "المركز الرئيسي", g.user["id"], company_id, branch_id,
            period["fiscal_year_id"], period["id"], item["currency_id"],
            float(item["exchange_rate"] or 1),
        )).lastrowid

        return_no = "PR-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        return_id = conn.execute("""
            INSERT INTO purchase_returns
            (company_id, purchase_id, supplier_id, warehouse_id, return_no, return_date,
             reason, net_amount, tax_amount, total_amount, currency_id, exchange_rate,
             journal_entry_id, request_id, request_payload_json, response_json, created_by)
            VALUES (?, ?, ?, 3, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
        """, (
            company_id, item["purchase_id"], item["supplier_id"], return_no,
            return_date, reason, net_amount, tax_amount, total_amount,
            item["currency_id"], float(item["exchange_rate"] or 1),
            entry_id, request_id, payload_json, g.user["id"],
        )).lastrowid

        conn.execute("""
            INSERT INTO purchase_return_items
            (return_id, purchase_item_id, product_id, unit_id, quantity, unit_price,
             discount_amount, net_amount, tax_rate, tax_amount, total_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            return_id, purchase_item_id, item["product_id"], item["unit_id"],
            quantity, float(item["unit_price"] or 0), discount_amount,
            net_amount, float(item["tax_rate"] or 0), tax_amount, total_amount,
        ))
        conn.execute(
            "UPDATE warehouse_stock SET quantity=quantity-?, updated_at=datetime('now','localtime') WHERE id=? AND quantity>=?",
            (quantity, stock["id"], quantity),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            conn.rollback()
            return jsonify(ok=False, error="تغير رصيد المخزون أثناء التنفيذ؛ أعد المحاولة"), 409
        conn.execute("""
            INSERT INTO stock_movements
            (company_id, warehouse_id, product_id, unit_id, movement_type, quantity,
             reference_type, reference_id, movement_date, notes, created_by)
            VALUES (?, 3, ?, ?, 'out', ?, 'purchase_return', ?, ?, ?, ?)
        """, (
            company_id, item["product_id"], item["unit_id"], quantity,
            return_id, return_date, f"مرتجع شراء {return_no} من مخزن POS", g.user["id"],
        ))

        # المرتجع يخفض التزام المورد؛ لا نغيّر المدفوعات ولا نسمح بمتبقٍ سالب.
        cumulative_returns = float(conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) AS total FROM purchase_returns WHERE purchase_id=?",
            (item["purchase_id"],),
        ).fetchone()["total"] or 0)
        remaining_amount = max(0.0, round(float(item["invoice_total"] or 0) -
                                         float(item["paid_amount"] or 0) -
                                         cumulative_returns, 2))
        conn.execute(
            "UPDATE purchase_invoices SET remaining_amount=?, updated_at=datetime('now','localtime') WHERE id=? AND company_id=?",
            (remaining_amount, item["purchase_id"], company_id),
        )

        # قيد المرتجع: مدين المورد، ودائن المخزون وضريبة المدخلات.
        conn.execute("""
            INSERT INTO journal_lines (entry_id, account_id, partner_id, debit, credit)
            VALUES (?, ?, ?, ?, 0)
        """, (entry_id, supplier_account["id"], item["supplier_id"], total_amount))
        if net_amount > 0:
            conn.execute("""
                INSERT INTO journal_lines (entry_id, account_id, partner_id, debit, credit)
                VALUES (?, ?, NULL, 0, ?)
            """, (entry_id, inventory_account["id"], net_amount))
        if tax_amount > 0:
            conn.execute("""
                INSERT INTO journal_lines (entry_id, account_id, partner_id, debit, credit)
                VALUES (?, ?, NULL, 0, ?)
            """, (entry_id, vat_account["id"], tax_amount))

        response_data = {
            "ok": True,
            "message": f"تم تسجيل مرتجع الشراء {return_no} بنجاح",
            "return_id": return_id,
            "return_no": return_no,
            "purchase_id": int(item["purchase_id"]),
            "journal_entry_id": entry_id,
            "movement_no": movement_no,
            "quantity": quantity,
            "net_amount": net_amount,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "remaining_amount": remaining_amount,
        }
        conn.execute(
            "UPDATE purchase_returns SET response_json=? WHERE id=?",
            (json.dumps(response_data, ensure_ascii=False), return_id),
        )
        conn.commit()
        try:
            db.audit(g.user["full_name"], "مرتجع شراء", f"{return_no} - {item['supplier_name']} - كمية {quantity:g}")
        except Exception:
            pass
        return jsonify(response_data), 200
    except Exception as exc:
        conn.rollback()
        return jsonify(ok=False, error=f"فشل تسجيل مرتجع الشراء: {exc}"), 500
    finally:
        conn.close()


@app.route("/api/pos-inventory/quick-add", methods=["POST"])
@require_write
def api_pos_inventory_quick_add():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة حالية"), 400

    d = request.get_json(silent=True) or {}
    name = str(d.get("name") or "").strip()
    code = str(d.get("code") or "").strip()
    if not name:
        return jsonify(ok=False, error="اسم المنتج مطلوب"), 400

    try:
        import time
        unit_id = int(d.get("unit_id", 2))
        buy_price = float(d.get("buy_price", 0) or 0)
        sell_price = float(d.get("sell_price", 0) or 0)
        qty = float(d.get("qty", 0) or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="بيانات المنتج أو الأسعار أو الكمية غير صالحة"), 400

    if buy_price < 0 or sell_price < 0 or qty < 0:
        return jsonify(ok=False, error="لا يمكن إدخال أسعار أو كميات سالبة"), 400
    if not code:
        code = f"POS-{int(time.time())}"

    try:
        with db.connect() as conn:
            warehouse = conn.execute(
                "SELECT id FROM warehouses WHERE id=3 AND company_id=? AND is_active=1",
                (company_id,),
            ).fetchone()
            if not warehouse:
                return jsonify(ok=False, error="المخزن الرئيسي غير موجود أو غير نشط"), 400

            unit = conn.execute(
                "SELECT id FROM units WHERE id=? AND company_id=? AND is_active=1",
                (unit_id, company_id),
            ).fetchone()
            if not unit:
                return jsonify(ok=False, error="وحدة القياس غير موجودة"), 400

            duplicate = conn.execute(
                "SELECT id FROM products WHERE company_id=? AND code=?",
                (company_id, code),
            ).fetchone()
            if duplicate:
                return jsonify(ok=False, error="كود المنتج مستخدم بالفعل"), 409

            cur = conn.execute(
                """INSERT INTO products
                   (company_id, name, code, base_unit_id, is_stock_item)
                   VALUES (?, ?, ?, ?, 1)""",
                (company_id, name, code, unit_id),
            )
            pid = cur.lastrowid

            conn.execute(
                """INSERT INTO product_barcodes
                   (company_id, product_id, unit_id, barcode, is_primary)
                   VALUES (?, ?, ?, ?, 1)""",
                (company_id, pid, unit_id, code),
            )

            if sell_price > 0:
                conn.execute(
                    """INSERT INTO product_prices
                       (company_id, product_id, unit_id, currency_id,
                        price_type, price, is_default, is_active)
                       VALUES (?, ?, ?, 3, 'sale', ?, 1, 1)""",
                    (company_id, pid, unit_id, sell_price),
                )
            if buy_price > 0:
                conn.execute(
                    """INSERT INTO product_prices
                       (company_id, product_id, unit_id, currency_id,
                        price_type, price, is_default, is_active)
                       VALUES (?, ?, ?, 3, 'purchase', ?, 1, 1)""",
                    (company_id, pid, unit_id, buy_price),
                )

            if qty > 0:
                conn.execute(
                    """INSERT INTO warehouse_stock
                       (company_id, warehouse_id, product_id, quantity)
                       VALUES (?, ?, ?, ?)""",
                    (company_id, warehouse["id"], pid, qty),
                )
                conn.execute(
                    """INSERT INTO stock_movements
                       (company_id, warehouse_id, product_id, unit_id,
                        movement_type, quantity, reference_type, reference_id,
                        notes, created_by)
                       VALUES (?, ?, ?, ?, 'in', ?, 'pos_quick_add', ?,
                               'رصيد افتتاحي عند إضافة المنتج من شاشة نقطة البيع', ?)""",
                    (company_id, warehouse["id"], pid, unit_id, qty, pid, g.user["id"]),
                )

        db.audit(g.user["full_name"], "إضافة منتج من نقطة البيع",
                 f"{code} - {name} | كمية افتتاحية: {qty}")
        return jsonify(ok=True, message=f"تمت الإضافة بنجاح. الكود: {code}", product_id=pid)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/pos-inventory/restock", methods=["POST"])
@require_write
def api_pos_inventory_restock():
    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="لا توجد شركة حالية"), 400

    d = request.get_json(silent=True) or {}
    try:
        pid = int(d.get("product_id"))
        qty_to_add = float(d.get("qty", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="المنتج أو الكمية غير صالح"), 400

    if qty_to_add <= 0:
        return jsonify(ok=False, error="الكمية يجب أن تكون أكبر من صفر"), 400

    try:
        with db.connect() as conn:
            warehouse = conn.execute(
                "SELECT id FROM warehouses WHERE id=3 AND company_id=? AND is_active=1",
                (company_id,),
            ).fetchone()
            if not warehouse:
                return jsonify(ok=False, error="المخزن الرئيسي غير موجود أو غير نشط"), 400

            product = conn.execute(
                """SELECT id, base_unit_id, allow_decimal_qty, is_stock_item, is_active
                   FROM products WHERE id=? AND company_id=?""",
                (pid, company_id),
            ).fetchone()
            if not product or not product["is_active"] or not product["is_stock_item"]:
                return jsonify(ok=False, error="المنتج غير موجود أو غير نشط أو غير مخزني"), 404
            if not product["allow_decimal_qty"] and not qty_to_add.is_integer():
                return jsonify(ok=False, error="هذا المنتج لا يسمح بكميات عشرية"), 400

            stock = conn.execute(
                """SELECT id, quantity FROM warehouse_stock
                   WHERE company_id=? AND warehouse_id=? AND product_id=?""",
                (company_id, warehouse["id"], pid),
            ).fetchone()
            if stock:
                conn.execute(
                    """UPDATE warehouse_stock
                       SET quantity=quantity+?, updated_at=datetime('now','localtime')
                       WHERE id=?""",
                    (qty_to_add, stock["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO warehouse_stock
                       (company_id, warehouse_id, product_id, quantity)
                       VALUES (?, ?, ?, ?)""",
                    (company_id, warehouse["id"], pid, qty_to_add),
                )

            conn.execute(
                """INSERT INTO stock_movements
                   (company_id, warehouse_id, product_id, unit_id,
                    movement_type, quantity, reference_type, reference_id,
                    notes, created_by)
                   VALUES (?, ?, ?, ?, 'in', ?, 'pos_restock', ?,
                           'إضافة كمية من شاشة نقطة البيع', ?)""",
                (company_id, warehouse["id"], pid, product["base_unit_id"],
                 qty_to_add, pid, g.user["id"]),
            )

        db.audit(g.user["full_name"], "إضافة مخزون من نقطة البيع",
                 f"المنتج #{pid} | كمية: {qty_to_add}")
        return jsonify(ok=True, message="تمت إضافة الكمية وتسجيل حركة المخزون بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/pos/expense", methods=["POST"])
def api_pos_expense():
    try:
        d = request.get_json()
        desc = d.get("description", "").strip()
        amount = float(d.get("amount", 0))
        if not desc or amount <= 0:
            return jsonify(ok=False, error="بيانات المصروف غير صالحة")
        
        # البحث عن الوردية المفتوحة الحالية
        session = db.query_one("SELECT id FROM cashier_sessions WHERE company_id=1 AND user_id=? AND status='open' LIMIT 1", (g.user["id"],))
        sid = session["id"] if session else None
        
        db.execute("INSERT INTO expenses (company_id, session_id, user_id, description, amount) VALUES (1, ?, ?, ?, ?)", (sid, g.user["id"], desc, amount))
        return jsonify(ok=True, message="تم تسجيل المصروف بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))




@app.route("/api/pos/expenses/list", methods=["GET"])
def api_pos_expenses_list():
    try:
        exps = db.query("SELECT id, description, amount, created_at FROM expenses ORDER BY id DESC LIMIT 10")
        return jsonify(ok=True, expenses=exps)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/api/session/<int:sid>/expenses", methods=["GET"])
def api_session_expenses(sid):
    try:
        res = db.query_one("SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE session_id=?", (sid,))
        total = float(res["total"]) if res and res["total"] else 0.0
        return jsonify(ok=True, total=total)
    except Exception as e:
        return jsonify(ok=False, error=str(e))




@app.route("/api/sales/<int:sale_id>/return", methods=["POST"])
@require_write
def api_sale_return(sale_id):
    return jsonify(ok=False, error="تم إيقاف مسار المرتجع القديم مؤقتًا لحماية البيانات."), 410

@app.route("/api/products/<int:pid>/latest_sale", methods=["GET"])
def api_product_latest_sale(pid):
    try:
        res = db.query_one("SELECT si.id as sale_id FROM sales_invoices si JOIN sales_invoice_items sii ON si.id = sii.invoice_id WHERE sii.product_id=? AND si.status='approved' ORDER BY si.id DESC LIMIT 1", (pid,))
        return jsonify(ok=True, sale_id=res["sale_id"] if res else None)
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/reports/sales", methods=["GET"])
def api_report_sales():
    try:
        today = db.query("SELECT id, invoice_no, grand_total, created_at FROM sales_invoices WHERE status='approved' ORDER BY id DESC LIMIT 20")
        total = db.query_one("SELECT COALESCE(SUM(grand_total), 0) as total FROM sales_invoices WHERE status='approved'")
        return jsonify(ok=True, sales=today, total=total['total'])
    except Exception as e: return jsonify(ok=False, error=str(e))

@app.route("/api/reports/expenses", methods=["GET"])
def api_report_expenses():
    try:
        exps = db.query("SELECT e.id, e.description, e.amount, e.created_at, u.full_name as user_name FROM expenses e LEFT JOIN users u ON e.user_id = u.id ORDER BY e.id DESC LIMIT 20")
        total = db.query_one("SELECT COALESCE(SUM(amount), 0) as total FROM expenses")
        return jsonify(ok=True, expenses=exps, total=total['total'])
    except Exception as e: return jsonify(ok=False, error=str(e))

@app.route("/api/reports/stock", methods=["GET"])
def api_report_stock():
    try:
        stock = db.query("SELECT p.name, p.code, ws.quantity FROM products p LEFT JOIN warehouse_stock ws ON ws.product_id = p.id AND ws.warehouse_id = 3 WHERE p.is_active=1 ORDER BY ws.quantity ASC LIMIT 20")
        return jsonify(ok=True, stock=stock)
    except Exception as e: return jsonify(ok=False, error=str(e))

@app.route("/reports", methods=["GET"])
def reports_page():
    is_admin = (g.user.get("role") == "admin")
    return render_template("reports.html", is_admin=is_admin)



@app.route("/api/reports/download", methods=["GET"])
def api_reports_download():
    import io
    from flask import send_file
    rep_type = request.args.get("type", "daily")
    
    # التحقق من الصلاحيات
    user_role = g.user.get("role") if hasattr(g, "user") else None
    if rep_type in ("weekly", "monthly") and user_role != "admin":
        return jsonify(error="غير مصرح لك بالوصول لهذا التقرير"), 403

    output = io.StringIO()
    output.write("\ufeff") # لدعم اللغة العربية في Excel
    output.write("التقرير,المبلغ,التاريخ\n")
    
    if rep_type == "daily":
        rows = db.query("SELECT invoice_no, grand_total, created_at FROM sales_invoices WHERE date(created_at) = date('now')")
        for r in rows:
            output.write(f"فاتورة {r['invoice_no']},{r['grand_total']},{r['created_at']}\n")
    elif rep_type == "weekly":
        rows = db.query("SELECT invoice_no, grand_total, created_at FROM sales_invoices WHERE created_at >= date('now', '-7 days')")
        for r in rows:
            output.write(f"فاتورة {r['invoice_no']},{r['grand_total']},{r['created_at']}\n")
    elif rep_type == "monthly":
        rows = db.query("SELECT invoice_no, grand_total, created_at FROM sales_invoices WHERE created_at >= date('now', '-30 days')")
        for r in rows:
            output.write(f"فاتورة {r['invoice_no']},{r['grand_total']},{r['created_at']}\n")
            
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name=f"report_{rep_type}.csv")


@app.route("/api/users", methods=["GET"])
@admin_required
def api_users_list():
    try:
        users = db.query("SELECT id, full_name, username, role FROM users")
        return jsonify(users)
    except Exception as e:
        return jsonify(error=str(e)), 500




@app.route("/api/invoice-settings", methods=["POST"])
def api_invoice_settings():
    try:
        d = request.get_json()
        for key, val in d.items():
            db.set_setting(key, str(val))
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/general-settings", methods=["POST"])
def api_general_settings():
    try:
        d = request.get_json()
        for k, v in d.items():
            db.set_setting(k, str(v))
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/invoice-settings/get", methods=["GET"])
def api_invoice_settings_get():
    try:
        return jsonify(ok=True, invoice_paper_size=db.get_setting("invoice_paper_size", "A4"), invoice_font_size=db.get_setting("invoice_font_size", "medium"), invoice_align=db.get_setting("invoice_align", "center"), invoice_show_cashier=db.get_setting("invoice_show_cashier", "1"), invoice_show_tax=db.get_setting("invoice_show_tax", "0"), invoice_copies=db.get_setting("invoice_copies", "1"), invoice_footer_msg=db.get_setting("invoice_footer_msg", ""))
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/backups", methods=["GET"])
def backups_page():
    try:
        backups_list = db.list_backups()
    except:
        backups_list = []
    return render_template("backups.html", backups_list=backups_list)



@app.route("/api/settings/data", methods=["GET"])
def api_settings_data():
    try:
        warehouses = db.query("SELECT id, name FROM warehouses WHERE company_id=1")
        currencies = db.query("SELECT id, code, name FROM currencies")
        activities = db.query("SELECT id, name FROM activity_types")
        return jsonify(ok=True, warehouses=warehouses, currencies=currencies, activities=activities)
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/settings/warehouse/add", methods=["POST"])
def api_warehouse_add():
    try:
        d = request.get_json()
        name = d.get("name", "").strip()
        if not name:
            return jsonify(ok=False, error="اسم المخزن مطلوب")
        import time
        code = "WH-" + str(int(time.time()))[-6:]
        db.execute("INSERT INTO warehouses (company_id, code, name) VALUES (1, ?, ?)", (code, name))
        return jsonify(ok=True, message="تم إضافة المخزن بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/settings/currency/add", methods=["POST"])
def api_currency_add():
    try:
        d = request.get_json()
        code = d.get("code", "").strip().upper()
        name = d.get("name", "").strip()
        if not code or not name:
            return jsonify(ok=False, error="كود واسم العملة مطلوبان")
        db.execute("INSERT INTO currencies (code, name) VALUES (?, ?)", (code, name))
        return jsonify(ok=True, message="تم إضافة العملة بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/settings/activity/add", methods=["POST"])
def api_activity_add():
    try:
        d = request.get_json()
        name = d.get("name", "").strip()
        if not name:
            return jsonify(ok=False, error="اسم النشاط مطلوب")
        db.execute("INSERT INTO activity_types (name) VALUES (?)", (name,))
        return jsonify(ok=True, message="تم إضافة النشاط بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



    try:
        d = request.get_json()
        name = d.get("name", "").strip()
        pin = d.get("pin", "").strip()
        if not name:
            return jsonify(ok=False, error="اسم الكاشير مطلوب")
        db.execute("INSERT INTO cashiers (company_id, name, pin_hash, is_active) VALUES (1, ?, ?, 1)", (name, pin))
        return jsonify(ok=True, message="تم إضافة الكاشير بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



        return jsonify(ok=True, message="تم إضافة الكاشير بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/settings/tax", methods=["GET"])
def api_tax_get():
    try:
        return jsonify(ok=True, tax_enabled=db.get_setting("tax_enabled", "0"), tax_rate=db.get_setting("tax_rate", "0"))
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/settings/tax", methods=["POST"])
def api_tax_post():
    try:
        d = request.get_json()
        db.set_setting("tax_enabled", str(d.get("tax_enabled", "0")))
        db.set_setting("tax_rate", str(d.get("tax_rate", "0")))
        return jsonify(ok=True, message="تم الحفظ")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/settings/permissions", methods=["GET"])
def api_permissions_get():
    try:
        import json
        perms_json = db.get_setting("role_permissions", "{}")
        perms = json.loads(perms_json) if perms_json else {}
        return jsonify(ok=True, permissions=perms)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/settings/permissions", methods=["POST"])
def api_permissions_post():
    try:
        import json
        d = request.get_json()
        perms = d.get("permissions", {})
        db.set_setting("role_permissions", json.dumps(perms, ensure_ascii=False))
        return jsonify(ok=True, message="تم حفظ الصلاحيات")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/settings/signatures", methods=["GET"])
def api_signatures_get():
    try:
        return jsonify(ok=True, sig1=db.get_setting("sig_manager", ""), sig2=db.get_setting("sig_accountant", ""))
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/settings/signatures", methods=["POST"])
def api_signatures_post():
    try:
        d = request.get_json()
        db.set_setting("sig_manager", d.get("sig1", ""))
        db.set_setting("sig_accountant", d.get("sig2", ""))
        return jsonify(ok=True, message="تم حفظ التواقيع")
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/settings/regions", methods=["GET"])
def api_regions_get():
    try:
        regions = db.query("SELECT id, name FROM regions WHERE company_id=1")
        return jsonify(ok=True, regions=regions)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/settings/region/add", methods=["POST"])
def api_region_add():
    try:
        d = request.get_json()
        name = d.get("name", "").strip()
        if not name:
            return jsonify(ok=False, error="اسم المنطقة مطلوب")
        db.execute("INSERT INTO regions (company_id, name) VALUES (1, ?)", (name,))
        return jsonify(ok=True, message="تم إضافة المنطقة")
    except Exception as e:
        return jsonify(ok=False, error=str(e))





@app.route("/api/returns/search_invoice", methods=["GET"])
def api_returns_search_invoice():
    try:
        q = request.args.get("q", "").strip()
        search_type = request.args.get("type", "invoice").strip().lower()

        if not q:
            return jsonify(ok=False, error="أدخل بيانات البحث")

        company_id = _current_company_id()
        if not company_id:
            return jsonify(ok=False, error="تعذر تحديد الشركة الحالية")

        if search_type in ("name", "phone"):
            if search_type == "name":
                customers = db.query("""
                    SELECT id, name, phone
                    FROM business_partners
                    WHERE company_id = ?
                      AND partner_type = 'customer'
                      AND is_active = 1
                      AND name LIKE ?
                    ORDER BY name
                    LIMIT 20
                """, (company_id, "%" + q + "%"))
            else:
                customers = db.query("""
                    SELECT id, name, phone
                    FROM business_partners
                    WHERE company_id = ?
                      AND partner_type = 'customer'
                      AND is_active = 1
                      AND phone LIKE ?
                    ORDER BY name
                    LIMIT 20
                """, (company_id, "%" + q + "%"))

            if not customers:
                return jsonify(ok=False, error="لم يتم العثور على عميل مطابق")

            results = []
            for customer in customers:
                customer_data = dict(customer)
                invoices = db.query("""
                    SELECT id, invoice_no, invoice_date, grand_total
                    FROM sales_invoices
                    WHERE company_id = ?
                      AND customer_id = ?
                      AND status = 'approved'
                    ORDER BY id DESC
                    LIMIT 20
                """, (company_id, customer_data["id"]))

                for invoice in invoices:
                    inv = dict(invoice)
                    results.append({
                        "customer_name": customer_data["name"],
                        "phone": customer_data["phone"],
                        "id": inv["id"],
                        "invoice_no": inv["invoice_no"],
                        "invoice_date": inv["invoice_date"],
                        "grand_total": inv["grand_total"]
                    })

            if not results:
                return jsonify(
                    ok=False,
                    error="تم العثور على العميل، لكن لا توجد له فواتير معتمدة"
                )

            return jsonify(ok=True, search_results=results)

        sale = db.query_one("""
            SELECT s.id, s.company_id, s.invoice_no, s.invoice_date,
                   s.customer_id, s.status, s.subtotal, s.discount_total,
                   s.tax_total, s.grand_total,
                   bp.name AS customer_name
            FROM sales_invoices s
            LEFT JOIN business_partners bp
              ON bp.id = s.customer_id
             AND bp.company_id = s.company_id
            WHERE s.invoice_no = ?
              AND s.company_id = ?
              AND s.status = 'approved'
            LIMIT 1
        """, (q, company_id))

        if not sale:
            return jsonify(
                ok=False,
                error="الفاتورة غير موجودة أو غير معتمدة ضمن الشركة الحالية"
            )

        sale_data = dict(sale)

        items = db.query("""
            SELECT sii.id AS item_id, sii.product_id, sii.unit_id,
                   p.name AS product_name, sii.quantity,
                   sii.unit_price, sii.discount, sii.tax_rate,
                   sii.tax_amount, sii.line_total
            FROM sales_invoice_items sii
            JOIN products p ON p.id = sii.product_id
            WHERE sii.invoice_id = ?
            ORDER BY sii.id
        """, (sale_data["id"],))

        previous_returns = db.query("""
            SELECT id, item_id, product_id, quantity,
                   refund_amount, created_at, reason
            FROM sales_returns
            WHERE company_id = ? AND sale_id = ?
            ORDER BY id DESC
        """, (company_id, sale_data["id"]))

        return jsonify(
            ok=True,
            sale=sale_data,
            sale_id=sale_data["id"],
            invoice_no=sale_data["invoice_no"],
            items=items,
            previous_returns=previous_returns
        )

    except Exception:
        app.logger.exception("Returns invoice search failed")
        return jsonify(ok=False, error="حدث خطأ أثناء البحث"), 500



@app.route("/api/returns/execute", methods=["POST"])
@require_write
def api_returns_execute():
    data = request.get_json(silent=True) or {}
    try:
        sale_id = int(data.get("sale_id", 0))
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            # التوافق مع أي عميل قديم يرسل صنفًا واحدًا
            raw_items = [{
                "item_id": data.get("item_id", 0),
                "quantity": data.get("quantity", 0)
            }]
        if not raw_items:
            raise ValueError
        requested = {}
        for row in raw_items:
            if not isinstance(row, dict):
                raise ValueError
            item_id = int(row.get("item_id", row.get("id", 0)))
            qty = float(row.get("quantity", 0))
            if item_id <= 0 or not math.isfinite(qty) or qty <= 0:
                raise ValueError
            # يمنع تكرار الصنف في الطلب، أو يجمعه بطريقة واضحة
            requested[item_id] = requested.get(item_id, 0.0) + qty
    except (TypeError, ValueError, OverflowError):
        return jsonify(ok=False, error="بيانات المرتجع أو كميات الأصناف غير صالحة"), 400

    if sale_id <= 0 or not requested:
        return jsonify(ok=False, error="حدد الفاتورة والأصناف والكميات الصحيحة"), 400

    company_id = _current_company_id()
    if not company_id:
        return jsonify(ok=False, error="تعذر تحديد الشركة الحالية"), 400

    reason = str(data.get("reason") or "مرتجع نقدي").strip()[:250]
    request_id = str(data.get("request_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", request_id):
        return jsonify(ok=False, error="معرّف طلب المرتجع غير صالح؛ حدّث صفحة المبيعات ثم أعد المحاولة"), 400
    payload_json = json.dumps({
        "sale_id": sale_id,
        "items": [{"item_id": k, "quantity": requested[k]} for k in sorted(requested)],
        "reason": reason
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS return_idempotency (
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (company_id, user_id, request_id)
            )
        """)
        previous_request = conn.execute("""
            SELECT payload_json, response_json
            FROM return_idempotency
            WHERE company_id=? AND user_id=? AND request_id=?
        """, (company_id, g.user["id"], request_id)).fetchone()
        if previous_request:
            if previous_request["payload_json"] != payload_json:
                conn.rollback()
                return jsonify(ok=False, error="معرّف الطلب مستخدم لبيانات مرتجع مختلفة؛ أعد تحميل الصفحة"), 409
            previous_response = json.loads(previous_request["response_json"])
            conn.commit()
            return jsonify(**previous_response)

        sale = conn.execute("""
            SELECT *
            FROM sales_invoices
            WHERE id=? AND company_id=? AND status='approved'
        """, (sale_id, company_id)).fetchone()
        if not sale:
            conn.rollback()
            return jsonify(ok=False, error="الفاتورة غير موجودة أو غير معتمدة"), 404

        # المرحلة الأولى: رد نقدي من الصندوق بصرف النظر عن وسيلة دفع الفاتورة الأصلية.
        session = conn.execute("""
            SELECT id
            FROM cashier_sessions
            WHERE company_id=? AND user_id=? AND status='open'
            ORDER BY id DESC LIMIT 1
        """, (company_id, g.user["id"])).fetchone()
        if not session:
            conn.rollback()
            return jsonify(ok=False, error="افتح وردية نقدية قبل تنفيذ المرتجع"), 400

        return_date = datetime.now().strftime("%Y-%m-%d")
        period = _period_for_date(return_date, company_id)
        if not period:
            conn.rollback()
            return jsonify(ok=False, error="لا توجد فترة محاسبية مفتوحة لتاريخ المرتجع"), 400
        period_error = _open_period_error(return_date, company_id)
        if period_error:
            conn.rollback()
            return jsonify(ok=False, error=period_error), 400

        branch = conn.execute("""
            SELECT id FROM branches
            WHERE company_id=? ORDER BY id LIMIT 1
        """, (company_id,)).fetchone()
        branch_id = sale["branch_id"] or (branch["id"] if branch else None)
        if not branch_id:
            conn.rollback()
            return jsonify(ok=False, error="لا يوجد فرع للشركة"), 400

        cash_account = conn.execute("""
            SELECT id FROM accounts
            WHERE company_id=? AND acc_no='1101' AND is_active=1 LIMIT 1
        """, (company_id,)).fetchone()
        sales_account = conn.execute("""
            SELECT id FROM accounts
            WHERE company_id=? AND acc_no='4101' AND is_active=1 LIMIT 1
        """, (company_id,)).fetchone()
        vat_account = conn.execute("""
            SELECT id FROM accounts
            WHERE company_id=? AND acc_no='2202' AND is_active=1 LIMIT 1
        """, (company_id,)).fetchone()
        if not cash_account or not sales_account:
            conn.rollback()
            return jsonify(ok=False, error="حساب الصندوق أو المبيعات غير موجود"), 400

        prepared = []
        total_refund = 0.0
        total_tax_refund = 0.0
        total_sales_refund = 0.0

        # التحقق من جميع الأصناف قبل إنشاء أي حركة، داخل معاملة واحدة.
        for item_id, qty in requested.items():
            item = conn.execute("""
                SELECT sii.*, p.name AS product_name
                FROM sales_invoice_items sii
                JOIN products p ON p.id=sii.product_id
                WHERE sii.id=? AND sii.invoice_id=?
            """, (item_id, sale_id)).fetchone()
            if not item:
                conn.rollback()
                return jsonify(ok=False, error=f"الصنف رقم {item_id} لا يتبع هذه الفاتورة"), 404

            sold_qty = float(item["quantity"] or 0)
            if sold_qty <= 0:
                conn.rollback()
                return jsonify(ok=False, error=f"كمية الصنف {item['product_name']} الأصلية غير صالحة"), 400

            returned_row = conn.execute("""
                SELECT COALESCE(SUM(quantity), 0)
                FROM sales_returns
                WHERE company_id=? AND sale_id=? AND item_id=?
            """, (company_id, sale_id, item_id)).fetchone()
            available = round(sold_qty - float(returned_row[0] or 0), 6)
            if qty > available + 0.000001:
                conn.rollback()
                return jsonify(ok=False, error=f"الكمية المتاحة لإرجاع {item['product_name']} هي {max(0, available):g}"), 400

            refund = round(float(item["line_total"] or 0) * qty / sold_qty, 2)
            tax_refund = round(float(item["tax_amount"] or 0) * qty / sold_qty, 2)
            sales_refund = round(refund - tax_refund, 2)
            if refund <= 0 or sales_refund < 0:
                conn.rollback()
                return jsonify(ok=False, error=f"قيمة مرتجع الصنف {item['product_name']} غير صالحة"), 400
            if tax_refund > 0 and not vat_account:
                conn.rollback()
                return jsonify(ok=False, error="حساب ضريبة المبيعات غير موجود"), 400

            prepared.append({
                "item_id": item_id, "item": item, "qty": qty,
                "refund": refund, "tax_refund": tax_refund,
                "sales_refund": sales_refund
            })
            total_refund = round(total_refund + refund, 2)
            total_tax_refund = round(total_tax_refund + tax_refund, 2)
            total_sales_refund = round(total_sales_refund + sales_refund, 2)

        if total_refund <= 0:
            conn.rollback()
            return jsonify(ok=False, error="إجمالي قيمة المرتجع غير صالح"), 400

        if round(total_sales_refund + total_tax_refund, 2) != round(total_refund, 2):
            conn.rollback()
            return jsonify(ok=False, error="القيد المحاسبي غير متوازن؛ تم إلغاء المرتجع"), 400

        # إنشاء المرتجعات وإعادة المخزون لكل الأصناف داخل نفس المعاملة.
        return_ids = []
        for entry in prepared:
            item = entry["item"]
            qty = entry["qty"]
            refund = entry["refund"]
            stock = conn.execute("""
                SELECT id, quantity
                FROM warehouse_stock
                WHERE company_id=? AND warehouse_id=? AND product_id=?
                LIMIT 1
            """, (company_id, sale["warehouse_id"], item["product_id"])).fetchone()
            if stock:
                conn.execute("""
                    UPDATE warehouse_stock
                    SET quantity=quantity+?, updated_at=datetime('now','localtime')
                    WHERE id=?
                """, (qty, stock["id"]))
            else:
                conn.execute("""
                    INSERT INTO warehouse_stock
                        (company_id, warehouse_id, product_id, quantity)
                    VALUES (?, ?, ?, ?)
                """, (company_id, sale["warehouse_id"], item["product_id"], qty))

            cur = conn.execute("""
                INSERT INTO sales_returns
                    (company_id, sale_id, product_id, quantity,
                     refund_amount, session_id, reason, item_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (company_id, sale_id, item["product_id"], qty,
                  refund, session["id"], reason, entry["item_id"]))
            return_id = cur.lastrowid
            return_ids.append(return_id)

            conn.execute("""
                INSERT INTO stock_movements
                    (company_id, warehouse_id, product_id, unit_id,
                     movement_type, quantity, reference_type, reference_id,
                     movement_date, notes, created_by)
                VALUES (?, ?, ?, ?, 'in', ?, 'sales_return', ?, ?, ?, ?)
            """, (company_id, sale["warehouse_id"], item["product_id"],
                  item["unit_id"], qty, return_id, return_date,
                  f"مرتجع نقدي للفاتورة {sale['invoice_no']}", g.user["id"]))

        movement_no = int(next_movement_no())
        while conn.execute(
            "SELECT 1 FROM journal_entries WHERE movement_no=?", (movement_no,)
        ).fetchone():
            movement_no += 1

        cur = conn.execute("""
            INSERT INTO journal_entries
                (movement_no, entry_date, description, region, status,
                 entry_type, created_by, company_id, branch_id,
                 fiscal_year_id, period_id, currency_id, exchange_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            movement_no, return_date,
            f"مرتجع نقدي للفاتورة {sale['invoice_no']} - مرتجعات {', '.join(map(str, return_ids))}",
            "المركز الرئيسي", "posted", "عادي", g.user["id"],
            company_id, branch_id, period["fiscal_year_id"],
            period["id"], sale["currency_id"], sale["exchange_rate"]
        ))
        entry_id = cur.lastrowid

        if total_sales_refund > 0:
            conn.execute("""
                INSERT INTO journal_lines
                    (entry_id, account_id, debit, credit, partner_id)
                VALUES (?, ?, ?, ?, ?)
            """, (entry_id, sales_account["id"], total_sales_refund, 0, None))
        if total_tax_refund > 0:
            conn.execute("""
                INSERT INTO journal_lines
                    (entry_id, account_id, debit, credit, partner_id)
                VALUES (?, ?, ?, ?, ?)
            """, (entry_id, vat_account["id"], total_tax_refund, 0, None))
        conn.execute("""
            INSERT INTO journal_lines
                (entry_id, account_id, debit, credit, partner_id)
            VALUES (?, ?, ?, ?, ?)
        """, (entry_id, cash_account["id"], 0, total_refund, None))

        conn.execute("""
            INSERT INTO audit_log (user, action, details)
            VALUES (?, ?, ?)
        """, (
            g.user["full_name"], "مرتجع نقدي",
            f"الفاتورة {sale['invoice_no']} - المرتجعات {','.join(map(str, return_ids))} - الإجمالي {total_refund}"
        ))
        response_data = {
            "ok": True, "return_ids": return_ids, "refund_amount": total_refund,
            "journal_entry_id": entry_id, "movement_no": movement_no,
            "items_count": len(prepared)
        }
        conn.execute("""
            INSERT INTO return_idempotency
                (company_id, user_id, request_id, payload_json, response_json)
            VALUES (?, ?, ?, ?, ?)
        """, (company_id, g.user["id"], request_id, payload_json,
              json.dumps(response_data, ensure_ascii=False, separators=(",", ":"))))
        conn.commit()
        return jsonify(**response_data)
    except Exception:
        conn.rollback()
        app.logger.exception("Cash return failed")
        return jsonify(ok=False, error="تعذر تنفيذ المرتجع؛ تم التراجع عن العملية"), 500
    finally:
        conn.close()


@app.route("/api/sales/return", methods=["POST"])
@require_write
def api_sales_return():
    return jsonify(ok=False, error="تم إيقاف مسار المرتجع القديم مؤقتًا لحماية البيانات."), 410

@app.route("/api/session/<int:sid>/returns", methods=["GET"])
def api_session_returns(sid):
    try:
        res = db.query_one("SELECT COALESCE(SUM(refund_amount), 0) as total FROM sales_returns WHERE session_id=?", (sid,))
        total = float(res["total"]) if res and res["total"] else 0.0
        return jsonify(ok=True, total=total)
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/delivery/orders", methods=["GET"])
def api_delivery_orders():
    try:
        orders = db.query("SELECT * FROM delivery_orders ORDER BY id DESC LIMIT 50")
        return jsonify(ok=True, orders=orders)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/delivery/order/add", methods=["POST"])
def api_delivery_add():
    try:
        d = request.get_json()
        db.execute("""INSERT INTO delivery_orders 
            (company_id, customer_name, customer_phone, customer_address, total_amount, notes) 
            VALUES (1, ?, ?, ?, ?, ?)""", 
            (d.get("name"), d.get("phone"), d.get("address"), d.get("amount"), d.get("notes")))
        return jsonify(ok=True, message="تم إضافة الطلب بنجاح")
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/delivery/order/update_status", methods=["POST"])
def api_delivery_update_status():
    try:
        d = request.get_json()
        db.execute("UPDATE delivery_orders SET status=?, driver_name=? WHERE id=?", 
                   (d.get("status"), d.get("driver"), d.get("id")))
        return jsonify(ok=True, message="تم تحديث حالة الطلب")
    except Exception as e:
        return jsonify(ok=False, error=str(e))






@app.route("/api/delivery/drivers", methods=["GET"])
def api_delivery_drivers():
    try:
        drivers = db.query("SELECT id, name, phone FROM delivery_drivers WHERE is_active=1")
        return jsonify(ok=True, drivers=drivers)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/delivery/driver/add", methods=["POST"])
def api_delivery_driver_add():
    try:
        d = request.get_json()
        name = d.get("name", "").strip()
        phone = d.get("phone", "").strip()
        if not name:
            return jsonify(ok=False, error="اسم السائق مطلوب")
        db.execute("INSERT INTO delivery_drivers (company_id, name, phone) VALUES (1, ?, ?)", (name, phone))
        return jsonify(ok=True, message="تم إضافة السائق")
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/delivery/services", methods=["GET"])
def api_delivery_services():
    try:
        services = db.query("SELECT id, name, price FROM delivery_services WHERE is_active=1")
        return jsonify(ok=True, services=services)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/api/delivery/service/add", methods=["POST"])
def api_delivery_service_add():
    try:
        d = request.get_json()
        name = d.get("name", "").strip()
        price = float(d.get("price", 0))
        if not name:
            return jsonify(ok=False, error="اسم الخدمة مطلوب")
        db.execute("INSERT INTO delivery_services (company_id, name, price) VALUES (1, ?, ?)", (name, price))
        return jsonify(ok=True, message="تم إضافة خدمة التوصيل")
    except Exception as e:
        return jsonify(ok=False, error=str(e))



@app.route("/api/delivery/order/mark_delivered", methods=["POST"])
def api_delivery_mark_delivered():
    conn = None
    try:
        d = request.get_json(silent=True) or {}
        order_id = d.get("id")

        if not order_id:
            return jsonify(ok=False, error="رقم الطلب مطلوب"), 400

        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")

        order = conn.execute(
            "SELECT * FROM delivery_orders WHERE id=? AND company_id=1",
            (order_id,)
        ).fetchone()

        if not order:
            conn.rollback()
            return jsonify(ok=False, error="الطلب غير موجود"), 404

        if order["status"] == "delivered":
            conn.rollback()
            return jsonify(
                ok=False,
                error="تم تسجيل تسليم هذا الطلب سابقًا"
            ), 409

        cur = conn.execute(
            """UPDATE delivery_orders
               SET status='delivered',
                   delivered_at=datetime('now','localtime')
               WHERE id=? AND company_id=1 AND status!='delivered'""",
            (order_id,)
        )

        if cur.rowcount != 1:
            conn.rollback()
            return jsonify(
                ok=False,
                error="تعذر تسجيل التسليم؛ ربما سُجل الطلب سابقًا"
            ), 409

        session = conn.execute(
            """SELECT id FROM cashier_sessions
               WHERE company_id=1 AND status='open'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()

        if session:
            conn.execute(
                "UPDATE cashier_sessions SET total_sales=total_sales+? WHERE id=?",
                (order["total_amount"], session["id"])
            )

        conn.commit()
        return jsonify(
            ok=True,
            message="تم تسجيل التسليم وإضافة المبلغ مرة واحدة"
        )

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/sales/delivery/pending", methods=["GET"])
@require_write
def api_sales_delivery_pending():
    conn = None
    try:
        company_id = _current_company_id()
        conn = db.connect()
        rows = conn.execute("""
            SELECT si.id AS sale_id, si.invoice_no, si.invoice_date,
                   si.customer_id, bp.name AS customer_name,
                   si.grand_total, si.paid_amount, si.remaining_amount,
                   sd.rep, sd.phone, sd.address, sd.fees, sd.driver_id,
                   sd.region_id, sd.region_name, sd.delivery_status,
                   sd.collection_status, sd.created_at AS delivery_created_at
            FROM sale_delivery_details sd
            JOIN sales_invoices si ON si.id=sd.sale_id AND si.company_id=sd.company_id
            LEFT JOIN business_partners bp ON bp.id=si.customer_id AND bp.company_id=si.company_id
            WHERE sd.company_id=? AND si.status='approved'
              AND (COALESCE(sd.delivery_status,'pending')!='delivered'
                   OR COALESCE(sd.collection_status,'pending')!='collected')
            ORDER BY sd.id DESC
        """, (company_id,)).fetchall()
        return jsonify(ok=True, orders=[dict(row) for row in rows])
    except Exception as e:
        app.logger.exception("Failed to load pending delivery sales")
        return jsonify(ok=False, error=str(e)), 500
    finally:
        if conn:
            conn.close()

@app.route("/api/sales/<int:sale_id>/delivery/collect", methods=["POST"])
@require_write
def api_sales_delivery_collect(sale_id):
    conn = None
    try:
        company_id = _current_company_id()
        data = request.get_json(silent=True) or {}
        method = str(data.get("payment_method") or "").strip()
        accounts = {"cash": 228, "bank_ahly": 229, "bank_misr": 230}
        if method not in accounts:
            return jsonify(ok=False, error="وسيلة الدفع غير صحيحة"), 400
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        sale = conn.execute("""SELECT si.*,bp.receivable_account_id FROM sales_invoices si LEFT JOIN business_partners bp ON bp.id=si.customer_id AND bp.company_id=si.company_id WHERE si.id=? AND si.company_id=?""", (sale_id, company_id)).fetchone()
        if not sale:
            conn.rollback()
            return jsonify(ok=False, error="الفاتورة غير موجودة"), 404
        delivery = conn.execute("SELECT * FROM sale_delivery_details WHERE sale_id=? AND company_id=?", (sale_id, company_id)).fetchone()
        if not delivery:
            conn.rollback()
            return jsonify(ok=False, error="بيانات التوصيل غير موجودة"), 404
        if sale["status"] != "approved" or delivery["collection_status"] == "collected" or delivery["receipt_entry_id"] is not None:
            conn.rollback()
            return jsonify(ok=False, error="الفاتورة غير مؤهلة للتحصيل أو تم تحصيلها مسبقاً"), 409
        amount = float(sale["remaining_amount"] or 0)
        receivable_id = sale["receivable_account_id"]
        if not math.isfinite(amount) or amount <= 0 or not sale["customer_id"] or not receivable_id:
            conn.rollback()
            return jsonify(ok=False, error="رصيد الفاتورة أو حساب العميل غير صالح"), 400
        payment_id = accounts[method]
        for account_id in (receivable_id, payment_id):
            account = conn.execute("SELECT id FROM accounts WHERE id=? AND company_id=? AND is_active=1", (account_id, company_id)).fetchone()
            if not account:
                conn.rollback()
                return jsonify(ok=False, error="حساب القبض أو الذمم غير نشط"), 400
        receipt_date = conn.execute("SELECT date('now','localtime')").fetchone()[0]
        period_error = _open_period_error(receipt_date, company_id)
        if period_error:
            conn.rollback()
            return jsonify(ok=False, error=period_error), 400
        period = _period_for_date(receipt_date, company_id)
        branch = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone() if not sale["branch_id"] else None
        branch_id = sale["branch_id"] or (branch["id"] if branch else None)
        if not period or not branch_id:
            conn.rollback()
            return jsonify(ok=False, error="تعذر تحديد الفترة أو الفرع"), 400
        movement_no = next_movement_no()
        while conn.execute("SELECT 1 FROM journal_entries WHERE movement_no=?", (movement_no,)).fetchone():
            movement_no += 1
        cur = conn.execute("""INSERT INTO journal_entries (movement_no,entry_date,description,region,status,entry_type,created_by,company_id,branch_id,fiscal_year_id,period_id,currency_id,exchange_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (movement_no, receipt_date, f"تحصيل فاتورة دليفري {sale['invoice_no']}", "المركز الرئيسي", "posted", "عادي", g.user["id"], company_id, branch_id, sale["fiscal_year_id"] or period["fiscal_year_id"], sale["period_id"] or period["id"], sale["currency_id"], sale["exchange_rate"]))
        entry_id = cur.lastrowid
        conn.execute("INSERT INTO journal_lines (entry_id,account_id,partner_id,debit,credit) VALUES (?,?,NULL,?,0)", (entry_id, payment_id, amount))
        conn.execute("INSERT INTO journal_lines (entry_id,account_id,partner_id,debit,credit) VALUES (?,?,?,?,?)", (entry_id, receivable_id, sale["customer_id"], 0, amount))
        updated = conn.execute("UPDATE sales_invoices SET paid_amount=COALESCE(paid_amount,0)+?,remaining_amount=0 WHERE id=? AND company_id=? AND status='approved' AND COALESCE(remaining_amount,0)>0", (amount, sale_id, company_id))
        if updated.rowcount != 1:
            conn.rollback()
            return jsonify(ok=False, error="تعذر تحديث رصيد الفاتورة"), 409
        marked = conn.execute("UPDATE sale_delivery_details SET delivery_status='delivered',delivered_at=COALESCE(delivered_at,datetime('now','localtime')),collection_status='collected',receipt_entry_id=?,collected_at=datetime('now','localtime'),collection_method=? WHERE sale_id=? AND company_id=? AND collection_status!='collected' AND receipt_entry_id IS NULL", (entry_id, method, sale_id, company_id))
        if marked.rowcount != 1:
            conn.rollback()
            return jsonify(ok=False, error="تم التحصيل مسبقاً أو تعذر تحديث التوصيل"), 409
        conn.execute("INSERT INTO audit_log (user,action,details) VALUES (?,?,?)", (g.user["full_name"], "تحصيل فاتورة دليفري", f"تحصيل الفاتورة {sale['invoice_no']} بمبلغ {amount:.2f}"))
        conn.commit()
        return jsonify(ok=True, amount=amount, remaining=0, receipt_entry_id=entry_id, message="تم التحصيل وتأكيد التسليم")
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
