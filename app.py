# app.py
# Modoo — Mini Odoo-like app (single-file Streamlit app)
# Replaces and upgrades your existing app.py with:
# - modular DB schema (users, partners, products, stock, invoices, invoice_lines, journals, journal_entries, journal_lines, audit_log)
# - role-based login
# - sales invoices and purchase bills that update inventory and post journal entries
# - audit log (who/when/what)
# - Odoo-like sidebar modules and list+form patterns
#
# Run: streamlit run app.py

import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
import hashlib
import os

DB_FILE = "modoo_app.db"

# -------------------------
# Utilities and DB helpers
# -------------------------
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def run_sql(query, params=(), fetch=False):
    conn = get_conn()
    if fetch:
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        return df
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    conn.close()

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def now_iso():
    return datetime.utcnow().isoformat()

# -------------------------
# Initialize database
# -------------------------
def init_db():
    conn = get_conn()
    c = conn.cursor()

    # users
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        full_name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT,
        role TEXT,
        active INTEGER DEFAULT 1
    )""")

    # partners (contacts)
    c.execute("""
    CREATE TABLE IF NOT EXISTS partners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        partner_type TEXT, -- customer / supplier
        email TEXT,
        phone TEXT,
        notes TEXT
    )""")

    # products and stock
    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT UNIQUE,
        name TEXT,
        uom TEXT,
        cost_price REAL DEFAULT 0,
        sale_price REAL DEFAULT 0
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        qty REAL DEFAULT 0,
        last_updated TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id)
    )""")

    # invoices (sales & purchase) and lines
    c.execute("""
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT UNIQUE,
        date TEXT,
        partner_id INTEGER,
        total REAL,
        status TEXT, -- draft/posted/cancelled
        type TEXT, -- sale / purchase
        reference TEXT,
        posted_by INTEGER,
        posted_at TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS invoice_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        product_id INTEGER,
        description TEXT,
        qty REAL,
        unit_price REAL,
        line_total REAL
    )""")

    # journals and ledger
    c.execute("""
    CREATE TABLE IF NOT EXISTS journals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        code TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        journal_id INTEGER,
        date TEXT,
        ref TEXT,
        narration TEXT,
        posted_by INTEGER,
        posted_at TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS journal_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER,
        account TEXT,
        debit REAL DEFAULT 0,
        credit REAL DEFAULT 0,
        party_id INTEGER
    )""")

    # audit log
    c.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        module TEXT,
        object_type TEXT,
        object_id INTEGER,
        timestamp TEXT,
        details TEXT
    )""")

    conn.commit()

    # seed admin user and journals if empty
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        admin_pw = hash_pw("admin")
        c.execute("INSERT INTO users (username, full_name, email, password_hash, role) VALUES (?,?,?,?,?)",
                  ("admin", "Administrator", "admin@example.com", admin_pw, "admin"))
    c.execute("SELECT count(*) FROM journals")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO journals (name, code) VALUES (?,?)",
                      [("Sales Journal", "SALES"), ("Purchase Journal", "PUR"), ("General Journal", "GEN")])
    conn.commit()
    conn.close()

init_db()

# -------------------------
# Audit helper
# -------------------------
def audit(user_id, action, module, object_type, object_id, details=""):
    ts = now_iso()
    run_sql("INSERT INTO audit_log (user_id, action, module, object_type, object_id, timestamp, details) VALUES (?,?,?,?,?,?,?)",
            (user_id, action, module, object_type, object_id, ts, details))

# -------------------------
# Business logic
# -------------------------
def next_number(prefix):
    df = run_sql("SELECT number FROM invoices WHERE number LIKE ? ORDER BY id DESC LIMIT 1", (f"{prefix}-%",), fetch=True)
    if df.empty:
        return f"{prefix}-0001"
    last = df['number'].iloc[0]
    try:
        n = int(last.split("-")[-1]) + 1
    except:
        n = 1
    return f"{prefix}-{n:04d}"

def post_sale_invoice(invoice_id, user_id):
    inv_df = run_sql("SELECT * FROM invoices WHERE id=?", (invoice_id,), fetch=True)
    if inv_df.empty:
        raise ValueError("Invoice not found")
    inv = inv_df.iloc[0].to_dict()
    if inv['status'] == 'posted':
        return "Already posted"

    lines = run_sql("SELECT * FROM invoice_lines WHERE invoice_id=?", (invoice_id,), fetch=True)
    total = 0.0
    conn = get_conn()
    cur = conn.cursor()
    try:
        # update stock (decrease) and compute totals
        for _, row in lines.iterrows():
            pid = int(row['product_id'])
            qty = float(row['qty'])
            unit_price = float(row['unit_price'])
            total += qty * unit_price
            cur.execute("SELECT qty FROM stock WHERE product_id=?", (pid,))
            r = cur.fetchone()
            if r:
                new_qty = r[0] - qty
                cur.execute("UPDATE stock SET qty=?, last_updated=? WHERE product_id=?", (new_qty, now_iso(), pid))
            else:
                # insert negative stock record
                cur.execute("INSERT INTO stock (product_id, qty, last_updated) VALUES (?,?,?)", (pid, -qty, now_iso()))

        # create journal entry: Debit AR, Credit Sales
        je_date = now_iso()
        cur.execute("INSERT INTO journal_entries (journal_id, date, ref, narration, posted_by, posted_at) VALUES (?,?,?,?,?,?)",
                    (1, je_date, inv['number'], f"Invoice {inv['number']}", user_id, je_date))
        entry_id = cur.lastrowid
        # Debit Accounts Receivable
        cur.execute("INSERT INTO journal_lines (entry_id, account, debit, credit, party_id) VALUES (?,?,?,?,?)",
                    (entry_id, "Accounts Receivable", total, 0, inv['partner_id']))
        # Credit Sales
        cur.execute("INSERT INTO journal_lines (entry_id, account, debit, credit, party_id) VALUES (?,?,?,?,?)",
                    (entry_id, "Sales", 0, total, inv['partner_id']))

        # mark invoice posted
        cur.execute("UPDATE invoices SET status='posted', posted_by=?, posted_at=? WHERE id=?", (user_id, je_date, invoice_id))
        conn.commit()
        audit(user_id, "post", "sales", "invoice", invoice_id, f"Posted invoice {inv['number']}")
        return "posted"
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

def post_purchase_bill(invoice_id, user_id):
    inv_df = run_sql("SELECT * FROM invoices WHERE id=?", (invoice_id,), fetch=True)
    if inv_df.empty:
        raise ValueError("Bill not found")
    inv = inv_df.iloc[0].to_dict()
    if inv['status'] == 'posted':
        return "Already posted"

    lines = run_sql("SELECT * FROM invoice_lines WHERE invoice_id=?", (invoice_id,), fetch=True)
    total = 0.0
    conn = get_conn()
    cur = conn.cursor()
    try:
        # update stock (increase)
        for _, row in lines.iterrows():
            pid = int(row['product_id'])
            qty = float(row['qty'])
            unit_price = float(row['unit_price'])
            total += qty * unit_price
            cur.execute("SELECT qty FROM stock WHERE product_id=?", (pid,))
            r = cur.fetchone()
            if r:
                new_qty = r[0] + qty
                cur.execute("UPDATE stock SET qty=?, last_updated=? WHERE product_id=?", (new_qty, now_iso(), pid))
            else:
                cur.execute("INSERT INTO stock (product_id, qty, last_updated) VALUES (?,?,?)", (pid, qty, now_iso()))

        # create journal entry: Debit Inventory, Credit Accounts Payable
        je_date = now_iso()
        cur.execute("INSERT INTO journal_entries (journal_id, date, ref, narration, posted_by, posted_at) VALUES (?,?,?,?,?,?)",
                    (2, je_date, inv['number'], f"Bill {inv['number']}", user_id, je_date))
        entry_id = cur.lastrowid
        cur.execute("INSERT INTO journal_lines (entry_id, account, debit, credit, party_id) VALUES (?,?,?,?,?)",
                    (entry_id, "Inventory", total, 0, inv['partner_id']))
        cur.execute("INSERT INTO journal_lines (entry_id, account, debit, credit, party_id) VALUES (?,?,?,?,?)",
                    (entry_id, "Accounts Payable", 0, total, inv['partner_id']))

        # mark bill posted
        cur.execute("UPDATE invoices SET status='posted', posted_by=?, posted_at=? WHERE id=?", (user_id, je_date, invoice_id))
        conn.commit()
        audit(user_id, "post", "purchase", "bill", invoice_id, f"Posted bill {inv['number']}")
        return "posted"
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(page_title="Modoo — Mini Odoo", layout="wide")
st.markdown("""
    <style>
    :root { --modoo-green: #0b8457; --modoo-navy: #0b2b3a; --modoo-bg: #f7fafb; }
    .stApp { background-color: var(--modoo-bg); }
    section[data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e6eef2; }
    .stButton>button { background-color: var(--modoo-green); color: white; border-radius: 6px; border: none; }
    h1, h2, h3 { color: var(--modoo-navy); font-family: Inter, sans-serif; }
    </style>
""", unsafe_allow_html=True)

# session
if 'user' not in st.session_state:
    st.session_state.user = None

# --- Authentication ---
def login_ui():
    st.sidebar.subheader("Sign in")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        df = run_sql("SELECT id, username, full_name, role, password_hash FROM users WHERE username=?", (username,), fetch=True)
        if not df.empty and hash_pw(password) == df['password_hash'].iloc[0]:
            st.session_state.user = dict(df.iloc[0])
            audit(st.session_state.user['id'], "login", "auth", "user", st.session_state.user['id'], "User logged in")
            st.experimental_rerun()
        else:
            st.sidebar.error("Invalid credentials")

def logout_ui():
    if st.sidebar.button("Logout"):
        if st.session_state.user:
            audit(st.session_state.user['id'], "logout", "auth", "user", st.session_state.user['id'], "User logged out")
        st.session_state.user = None
        st.experimental_rerun()

if not st.session_state.user:
    login_ui()
    st.sidebar.markdown("Default admin credentials: **admin / admin**")
    st.title("Modoo — Mini Odoo (Login required)")
    st.info("Please login to access modules.")
    st.stop()
else:
    st.sidebar.write(f"**{st.session_state.user['full_name']}** — {st.session_state.user['role']}")
    logout_ui()

# Sidebar modules (Odoo-like)
modules = [
    "Dashboard",
    "Contacts",
    "Products & Inventory",
    "Sales Invoices",
    "Purchase Bills",
    "Journal Entries",
    "Audit Log",
    "Settings"
]
choice = st.sidebar.radio("Modules", modules)

# -------------------------
# Dashboard
# -------------------------
if choice == "Dashboard":
    st.title("Dashboard")
    # KPIs
    ar = run_sql("SELECT SUM(debit - credit) as ar FROM journal_lines WHERE account='Accounts Receivable'", fetch=True)
    ap = run_sql("SELECT SUM(credit - debit) as ap FROM journal_lines WHERE account='Accounts Payable'", fetch=True)
    inv_val = run_sql("""SELECT SUM(IFNULL(s.qty,0) * IFNULL(p.cost_price,0)) as inv_val
                        FROM products p LEFT JOIN stock s ON p.id=s.product_id""", fetch=True)
    col1, col2, col3 = st.columns(3)
    col1.metric("Accounts Receivable (approx)", f"Rs. {float(ar['ar'].iloc[0] or 0):,.2f}")
    col2.metric("Accounts Payable (approx)", f"Rs. {float(ap['ap'].iloc[0] or 0):,.2f}")
    col3.metric("Inventory Value", f"Rs. {float(inv_val['inv_val'].iloc[0] or 0):,.2f}")

    st.markdown("### Recent Activity")
    recent = run_sql("SELECT a.id, u.full_name as user, a.action, a.module, a.object_type, a.object_id, a.timestamp, a.details FROM audit_log a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.timestamp DESC LIMIT 20", fetch=True)
    st.dataframe(recent)

# -------------------------
# Contacts
# -------------------------
elif choice == "Contacts":
    st.title("Contacts")
    with st.form("add_contact", clear_on_submit=True):
        name = st.text_input("Name")
        ptype = st.selectbox("Type", ["customer", "supplier"])
        email = st.text_input("Email")
        phone = st.text_input("Phone")
        notes = st.text_area("Notes")
        if st.form_submit_button("Save"):
            run_sql("INSERT INTO partners (name, partner_type, email, phone, notes) VALUES (?,?,?,?,?)", (name, ptype, email, phone, notes))
            audit(st.session_state.user['id'], "create", "contacts", "partner", None, f"Created partner {name}")
            st.success("Contact saved")
            st.experimental_rerun()
    st.markdown("### All Contacts")
    df = run_sql("SELECT id, name, partner_type, email, phone FROM partners ORDER BY name", fetch=True)
    st.dataframe(df)

# -------------------------
# Products & Inventory
# -------------------------
elif choice == "Products & Inventory":
    st.title("Products & Inventory")
    with st.form("add_product", clear_on_submit=True):
        sku = st.text_input("SKU")
        name = st.text_input("Name")
        uom = st.text_input("UOM", value="pcs")
        cost = st.number_input("Cost Price", value=0.0, format="%.2f")
        sale = st.number_input("Sale Price", value=0.0, format="%.2f")
        if st.form_submit_button("Add Product"):
            run_sql("INSERT INTO products (sku, name, uom, cost_price, sale_price) VALUES (?,?,?,?,?)", (sku, name, uom, cost, sale))
            audit(st.session_state.user['id'], "create", "inventory", "product", None, f"Created product {name}")
            st.success("Product added")
            st.experimental_rerun()

    st.markdown("### Stock")
    df = run_sql("""SELECT p.id as product_id, p.sku, p.name, p.uom, p.cost_price, p.sale_price,
                   IFNULL(s.qty,0) as qty FROM products p LEFT JOIN stock s ON p.id=s.product_id ORDER BY p.name""", fetch=True)
    st.dataframe(df, use_container_width=True)

    st.markdown("### Adjust Stock (quick)")
    with st.form("adjust_stock"):
        products = run_sql("SELECT id, name FROM products ORDER BY name", fetch=True)
        prod_options = products['id'].tolist() if not products.empty else []
        prod = st.selectbox("Product", options=prod_options, format_func=lambda x: products[products['id']==x]['name'].iloc[0] if not products.empty else "")
        adj_qty = st.number_input("Adjust quantity (use negative to reduce)", value=0.0)
        if st.form_submit_button("Apply Adjustment"):
            if prod == "" or prod is None:
                st.error("Select a product first")
            else:
                cur = get_conn().cursor()
                cur.execute("SELECT qty FROM stock WHERE product_id=?", (prod,))
                r = cur.fetchone()
                if r:
                    new_qty = r[0] + adj_qty
                    cur.execute("UPDATE stock SET qty=?, last_updated=? WHERE product_id=?", (new_qty, now_iso(), prod))
                else:
                    cur.execute("INSERT INTO stock (product_id, qty, last_updated) VALUES (?,?,?)", (prod, adj_qty, now_iso()))
                get_conn().commit()
                audit(st.session_state.user['id'], "adjust", "inventory", "stock", prod, f"Adjusted stock by {adj_qty}")
                st.success("Stock adjusted")
                st.experimental_rerun()

# -------------------------
# Sales Invoices
# -------------------------
elif choice == "Sales Invoices":
    st.title("Sales Invoices")
    st.markdown("Create and post sales invoices. Posting will reduce stock and create journal entries.")
    partners = run_sql("SELECT id, name FROM partners WHERE partner_type='customer' ORDER BY name", fetch=True)
    products = run_sql("SELECT id, name, sale_price FROM products ORDER BY name", fetch=True)

    with st.expander("New Sales Invoice"):
        with st.form("new_sale"):
            inv_date = st.date_input("Date", value=date.today())
            partner = st.selectbox("Customer", options=partners['id'].tolist() if not partners.empty else [], format_func=lambda x: partners[partners['id']==x]['name'].iloc[0] if not partners.empty else "")
            # simple single-line invoice for brevity; extend to multiple lines as needed
            prod = st.selectbox("Product", options=products['id'].tolist() if not products.empty else [], format_func=lambda x: products[products['id']==x]['name'].iloc[0] if not products.empty else "")
            qty = st.number_input("Qty", value=1.0)
            unit_price = st.number_input("Unit Price", value=float(products[products['id']==prod]['sale_price'].iloc[0]) if not products.empty else 0.0, format="%.2f")
            if st.form_submit_button("Create Invoice"):
                if partner == "" or partner is None:
                    st.error("Add/select a customer first.")
                else:
                    number = next_number("INV")
                    total = qty * unit_price
                    run_sql("INSERT INTO invoices (number, date, partner_id, total, status, type) VALUES (?,?,?,?,?,?)",
                            (number, inv_date.isoformat(), partner, total, "draft", "sale"))
                    inv_id = run_sql("SELECT id FROM invoices WHERE number=?", (number,), fetch=True)['id'].iloc[0]
                    run_sql("INSERT INTO invoice_lines (invoice_id, product_id, description, qty, unit_price, line_total) VALUES (?,?,?,?,?,?)",
                            (inv_id, prod, "", qty, unit_price, total))
                    audit(st.session_state.user['id'], "create", "sales", "invoice", inv_id, f"Created invoice {number}")
                    st.success(f"Invoice {number} created (draft).")
                    st.experimental_rerun()

    st.markdown("### Draft Invoices")
    drafts = run_sql("SELECT i.id, i.number, i.date, i.total, p.name as partner FROM invoices i LEFT JOIN partners p ON i.partner_id=p.id WHERE i.status='draft' AND i.type='sale' ORDER BY i.date DESC", fetch=True)
    st.dataframe(drafts)

    if not drafts.empty:
        sel = st.selectbox("Select draft to Post", options=drafts['id'].tolist(), format_func=lambda x: drafts[drafts['id']==x]['number'].iloc[0])
        if st.button("Post Selected Invoice"):
            try:
                post_sale_invoice(sel, st.session_state.user['id'])
                st.success("Invoice posted: inventory and journal updated.")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"Error posting invoice: {e}")

# -------------------------
# Purchase Bills
# -------------------------
elif choice == "Purchase Bills":
    st.title("Purchase Bills")
    st.markdown("Create and post purchase bills. Posting will increase stock and create journal entries.")
    partners = run_sql("SELECT id, name FROM partners WHERE partner_type='supplier' ORDER BY name", fetch=True)
    products = run_sql("SELECT id, name, cost_price FROM products ORDER BY name", fetch=True)

    with st.expander("New Purchase Bill"):
        with st.form("new_bill"):
            bill_date = st.date_input("Date", value=date.today())
            supplier = st.selectbox("Supplier", options=partners['id'].tolist() if not partners.empty else [], format_func=lambda x: partners[partners['id']==x]['name'].iloc[0] if not partners.empty else "")
            prod = st.selectbox("Product", options=products['id'].tolist() if not products.empty else [], format_func=lambda x: products[products['id']==x]['name'].iloc[0] if not products.empty else "")
            qty = st.number_input("Qty", value=1.0)
            unit_cost = st.number_input("Unit Cost", value=float(products[products['id']==prod]['cost_price'].iloc[0]) if not products.empty else 0.0, format="%.2f")
            if st.form_submit_button("Create Bill"):
                if supplier == "" or supplier is None:
                    st.error("Add/select a supplier first.")
                else:
                    number = next_number("BILL")
                    total = qty * unit_cost
                    run_sql("INSERT INTO invoices (number, date, partner_id, total, status, type) VALUES (?,?,?,?,?,?)",
                            (number, bill_date.isoformat(), supplier, total, "draft", "purchase"))
                    inv_id = run_sql("SELECT id FROM invoices WHERE number=?", (number,), fetch=True)['id'].iloc[0]
                    run_sql("INSERT INTO invoice_lines (invoice_id, product_id, description, qty, unit_price, line_total) VALUES (?,?,?,?,?,?)",
                            (inv_id, prod, "", qty, unit_cost, total))
                    audit(st.session_state.user['id'], "create", "purchase", "bill", inv_id, f"Created bill {number}")
                    st.success(f"Bill {number} created (draft).")
                    st.experimental_rerun()

    st.markdown("### Draft Bills")
    drafts = run_sql("SELECT i.id, i.number, i.date, i.total, p.name as partner FROM invoices i LEFT JOIN partners p ON i.partner_id=p.id WHERE i.status='draft' AND i.type='purchase' ORDER BY i.date DESC", fetch=True)
    st.dataframe(drafts)

    if not drafts.empty:
        sel = st.selectbox("Select draft bill to Post", options=drafts['id'].tolist(), format_func=lambda x: drafts[drafts['id']==x]['number'].iloc[0])
        if st.button("Post Selected Bill"):
            try:
                post_purchase_bill(sel, st.session_state.user['id'])
                st.success("Bill posted: inventory and journal updated.")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"Error posting bill: {e}")

# -------------------------
# Journal Entries
# -------------------------
elif choice == "Journal Entries":
    st.title("Journal Entries")
    df = run_sql("""SELECT je.id, je.date, je.ref, je.narration, u.full_name as posted_by, je.posted_at
                   FROM journal_entries je LEFT JOIN users u ON je.posted_by=u.id ORDER BY je.date DESC""", fetch=True)
    st.dataframe(df)

# -------------------------
# Audit Log
# -------------------------
elif choice == "Audit Log":
    st.title("Audit Log")
    df = run_sql("""SELECT a.id, u.full_name as user, a.action, a.module, a.object_type, a.object_id, a.timestamp, a.details
                    FROM audit_log a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.timestamp DESC LIMIT 500""", fetch=True)
    st.dataframe(df)

# -------------------------
# Settings
# -------------------------
elif choice == "Settings":
    st.title("Settings")
    st.subheader("Users")
    with st.form("add_user", clear_on_submit=True):
        uname = st.text_input("Username")
        fname = st.text_input("Full name")
        email = st.text_input("Email")
        role = st.selectbox("Role", ["admin", "accountant", "sales", "purchasing", "user"])
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Create User"):
            if not uname or not pw:
                st.error("Username and password required")
            else:
                run_sql("INSERT INTO users (username, full_name, email, password_hash, role) VALUES (?,?,?,?,?)",
                        (uname, fname, email, hash_pw(pw), role))
                st.success("User created")
                st.experimental_rerun()
    st.dataframe(run_sql("SELECT id, username, full_name, email, role, active FROM users ORDER BY username", fetch=True))

# -------------------------
# End of file
# -------------------------
