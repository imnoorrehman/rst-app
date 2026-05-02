import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import hashlib
import uuid

# ==========================================
# 1. DATABASE SETUP & UTILITIES
# ==========================================
DB_NAME = 'modoo_erp.db'

def get_db_connection():
    """Returns a new database connection."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def run_query(query, params=(), fetch=False):
    """Executes a query and optionally returns data as a DataFrame."""
    with get_db_connection() as conn:
        if fetch:
            return pd.read_sql(query, conn, params=params)
        conn.execute(query, params)
        conn.commit()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def log_action(action, details=""):
    """Logs user actions into the audit log."""
    user = st.session_state.get('username', 'System')
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_query("INSERT INTO audit_logs (timestamp, user, action, details) VALUES (?, ?, ?, ?)",
              (timestamp, user, action, details))

def init_db():
    """Initializes the database schema and default records."""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Users & Settings
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY, timestamp TEXT, user TEXT, action TEXT, details TEXT)''')
    
    # Master Data
    c.execute('''CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, name TEXT, type TEXT, phone TEXT, email TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT, sku TEXT, type TEXT, qty_on_hand REAL, cost_price REAL, sales_price REAL)''')
    
    # Accounting Core
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (code INTEGER PRIMARY KEY, name TEXT, type TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS journal_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, description TEXT, reference TEXT, doc_type TEXT, doc_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS journal_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER, account_code INTEGER, contact_id INTEGER, debit REAL, credit REAL)''')
    
    # Documents (Invoices/Bills)
    c.execute('''CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT, doc_number TEXT, contact_id INTEGER, date TEXT, total_amount REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS document_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, item_id INTEGER, qty REAL, unit_price REAL, subtotal REAL)''')

    # Seed Default Data
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", hash_password("admin"), "Admin"))
        
    c.execute("SELECT count(*) FROM accounts")
    if c.fetchone()[0] == 0:
        coa = [
            (1000, 'Cash on Hand', 'Asset'), (1100, 'Bank Account', 'Asset'),
            (1200, 'Accounts Receivable', 'Asset'), (1300, 'Inventory', 'Asset'),
            (2000, 'Accounts Payable', 'Liability'),
            (3000, 'Retained Earnings', 'Equity'),
            (4000, 'Sales Revenue', 'Revenue'),
            (5000, 'Cost of Goods Sold', 'Expense'), (5100, 'Operating Expenses', 'Expense')
        ]
        c.executemany("INSERT INTO accounts VALUES (?,?,?)", coa)
        
    c.execute("SELECT count(*) FROM settings")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO settings VALUES ('company_name', 'Modoo Enterprise')")
        c.execute("INSERT INTO settings VALUES ('currency', 'USD')")
        
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# ==========================================
# 2. BUSINESS LOGIC ENGINE
# ==========================================
def post_journal_entry(date, description, reference, lines, doc_type=None, doc_id=None):
    """Posts a balanced double-entry journal."""
    total_dr = sum(l['debit'] for l in lines)
    total_cr = sum(l['credit'] for l in lines)
    if round(total_dr, 2) != round(total_cr, 2):
        st.error(f"Unbalanced Journal! Dr: {total_dr} | Cr: {total_cr}")
        return False

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO journal_entries (date, description, reference, doc_type, doc_id) VALUES (?,?,?,?,?)",
                  (date, description, reference, doc_type, doc_id))
        entry_id = c.lastrowid
        
        for l in lines:
            c.execute("INSERT INTO journal_lines (entry_id, account_code, contact_id, debit, credit) VALUES (?,?,?,?,?)",
                      (entry_id, l['account_code'], l.get('contact_id'), l.get('debit', 0.0), l.get('credit', 0.0)))
        conn.commit()
    return True

def create_document(doc_type, contact_id, date, lines_data):
    """Creates Invoice/Bill, updates Inventory, and posts Accounting Journals."""
    prefix = "INV/" if doc_type == "Sale" else "BILL/"
    year_prefix = datetime.now().year
    doc_number = f"{prefix}{year_prefix}/{uuid.uuid4().hex[:4].upper()}"
    total_amount = sum(l['subtotal'] for l in lines_data)
    
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO documents (doc_type, doc_number, contact_id, date, total_amount) VALUES (?,?,?,?,?)",
                  (doc_type, doc_number, contact_id, date, total_amount))
        doc_id = c.lastrowid
        
        total_cogs = 0.0
        
        for l in lines_data:
            c.execute("INSERT INTO document_lines (doc_id, item_id, qty, unit_price, subtotal) VALUES (?,?,?,?,?)",
                      (doc_id, l['item_id'], l['qty'], l['unit_price'], l['subtotal']))
            
            if doc_type == "Sale":
                c.execute("UPDATE items SET qty_on_hand = qty_on_hand - ? WHERE id = ?", (l['qty'], l['item_id']))
                c.execute("SELECT cost_price FROM items WHERE id = ?", (l['item_id'],))
                cost = c.fetchone()[0] or 0.0
                total_cogs += (cost * l['qty'])
            elif doc_type == "Purchase":
                c.execute("UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id = ?", (l['qty'], l['item_id']))
                c.execute("UPDATE items SET cost_price = ? WHERE id = ?", (l['unit_price'], l['item_id']))
                
        conn.commit()

    journal_lines = []
    if doc_type == "Sale":
        journal_lines.append({'account_code': 1200, 'contact_id': contact_id, 'debit': total_amount, 'credit': 0})
        journal_lines.append({'account_code': 4000, 'contact_id': None, 'debit': 0, 'credit': total_amount})
        if total_cogs > 0:
            journal_lines.append({'account_code': 5000, 'contact_id': None, 'debit': total_cogs, 'credit': 0})
            journal_lines.append({'account_code': 1300, 'contact_id': None, 'debit': 0, 'credit': total_cogs})
    elif doc_type == "Purchase":
        journal_lines.append({'account_code': 1300, 'contact_id': None, 'debit': total_amount, 'credit': 0})
        journal_lines.append({'account_code': 2000, 'contact_id': contact_id, 'debit': 0, 'credit': total_amount})

    post_journal_entry(date, f"{doc_type} {doc_number}", doc_number, journal_lines, doc_type, doc_id)
    log_action(f"Created {doc_type}", f"Doc: {doc_number}, Amount: {total_amount}")
    return doc_number

def get_account_balances():
    """Calculates balances based on standard accounting rules."""
    df = run_query("""
        SELECT a.code, a.name, a.type, 
               COALESCE(SUM(l.debit), 0) as total_dr, 
               COALESCE(SUM(l.credit), 0) as total_cr
        FROM accounts a
        LEFT JOIN journal_lines l ON a.code = l.account_code
        GROUP BY a.code, a.name, a.type
    """, fetch=True)
    
    def calc_balance(row):
        if row['type'] in ['Asset', 'Expense']: return row['total_dr'] - row['total_cr']
        else: return row['total_cr'] - row['total_dr']
        
    df['balance'] = df.apply(calc_balance, axis=1)
    return df

# ==========================================
# 3. UI INITIALIZATION (ODOO 19 STYLE)
# ==========================================
st.set_page_config(page_title="Modoo ERP", layout="wide", page_icon="🏢")

# Odoo 19 Design Tokens
st.markdown("""
    <style>
    /* Odoo 19 Primary Colors */
    :root { 
        --odoo-purple: #714B67; 
        --odoo-teal: #017E84; 
        --odoo-gray-bg: #F4F7F9; 
        --odoo-sidebar: #FFFFFF;
        --odoo-card-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    }
    
    .stApp { background-color: var(--odoo-gray-bg); }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] { 
        background-color: var(--odoo-sidebar) !important; 
        border-right: 1px solid #dee2e6;
    }
    
    /* Buttons */
    .stButton>button { 
        background-color: var(--odoo-purple); 
        color: white; 
        border-radius: 6px; 
        border: none; 
        font-weight: 500;
        padding: 0.5rem 1rem;
        transition: all 0.2s ease;
    }
    .stButton>button:hover { 
        background-color: #5a3c52; 
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(113, 75, 103, 0.2);
    }
    
    /* Kanban/Metric Cards */
    .metric-card { 
        background: white; 
        padding: 24px; 
        border-radius: 12px; 
        box-shadow: var(--odoo-card-shadow);
        border: 1px solid #edf2f7;
        margin-bottom: 1rem;
    }
    .metric-title { color: #718096; font-size: 0.875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.025em; }
    .metric-value { color: #1a202c; font-size: 1.5rem; font-weight: 700; margin-top: 4px; }
    
    /* Dataframe/Table styling */
    .stDataFrame { border-radius: 12px; overflow: hidden; box-shadow: var(--odoo-card-shadow); }
    
    /* Headers */
    h1, h2, h3 { color: #2d3748; font-family: 'Inter', sans-serif; font-weight: 700; }
    
    /* Navigation Active State (Simulated) */
    .stRadio [role="radiogroup"] { gap: 8px; }
    .stRadio label { 
        background: white; 
        padding: 10px 16px !important; 
        border-radius: 8px !important; 
        border: 1px solid #e2e8f0 !important;
        cursor: pointer;
    }
    
    /* Form fields */
    .stTextInput input, .stNumberInput input, .stSelectbox select {
        border-radius: 8px !important;
        border: 1px solid #cbd5e0 !important;
    }
    </style>
""", unsafe_allow_html=True)

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'cart' not in st.session_state:
    st.session_state.cart = []

# ==========================================
# 4. LOGIN MODULE
# ==========================================
if not st.session_state.logged_in:
    st.markdown("""
        <div style='display: flex; flex-direction: column; align-items: center; justify-content: center; height: 60vh;'>
            <h1 style='color: var(--odoo-purple); font-size: 3rem; margin-bottom: 0;'>Modoo</h1>
            <p style='color: #718096; margin-bottom: 2rem;'>Open Source ERP Simplified</p>
        </div>
    """, unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.5, 1, 1.5])
    with c2:
        with st.form("login_form"):
            user = st.text_input("Email / Username")
            pwd = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
            
            if submitted:
                res = run_query("SELECT id, username, role FROM users WHERE username=? AND password=?", 
                                (user, hash_password(pwd)), fetch=True)
                if not res.empty:
                    st.session_state.logged_in = True
                    st.session_state.username = res.iloc[0]['username']
                    st.session_state.role = res.iloc[0]['role']
                    log_action("Login", "User session started")
                    st.rerun()
                else:
                    st.error("Invalid credentials.")
    st.stop()

# ==========================================
# 5. SIDEBAR NAVIGATION
# ==========================================
company_name = run_query("SELECT value FROM settings WHERE key='company_name'", fetch=True).iloc[0]['value']
currency = run_query("SELECT value FROM settings WHERE key='currency'", fetch=True).iloc[0]['value']

with st.sidebar:
    st.markdown(f"<h2 style='color: var(--odoo-purple); margin-bottom: 0;'>{company_name}</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='color: #718096; font-size: 0.8rem;'>Enterprise Edition 19.0</p>", unsafe_allow_html=True)
    st.write("---")
    
    menus = ["Dashboard", "Contacts", "Inventory", "Sales", "Purchase", "Accounting", "Reporting"]
    if st.session_state.role == "Admin":
        menus.append("Settings")

    choice = st.radio("APPS", menus, label_visibility="collapsed")
    
    st.write("---")
    st.write(f"👤 **{st.session_state.username}**")
    if st.button("Logout", use_container_width=True):
        log_action("Logout", "User session ended")
        st.session_state.clear()
        st.rerun()

# ==========================================
# 6. MODULES IMPLEMENTATION
# ==========================================

if choice == "Dashboard":
    st.title("Welcome back!")
    
    # Calculate key metrics
    balances = get_account_balances()
    cash = balances[balances['code'].isin([1000, 1100])]['balance'].sum()
    ar = balances[balances['code'] == 1200]['balance'].sum()
    ap = balances[balances['code'] == 2000]['balance'].sum()
    sales = balances[balances['code'] == 4000]['balance'].sum()
    
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-title'>Liquidity</div>
            <div class='metric-value'>{currency} {cash:,.2f}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-title'>To Receive</div>
            <div class='metric-value'>{currency} {ar:,.2f}</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-title'>To Pay</div>
            <div class='metric-value'>{currency} {ap:,.2f}</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-title'>Annual Sales</div>
            <div class='metric-value'>{currency} {sales:,.2f}</div>
        </div>""", unsafe_allow_html=True)
    
    st.subheader("Recent Communications")
    logs = run_query("SELECT timestamp, user, action, details FROM audit_logs ORDER BY id DESC LIMIT 5", fetch=True)
    st.dataframe(logs, use_container_width=True, hide_index=True)


elif choice == "Contacts":
    st.title("Contacts")
    t1, t2 = st.tabs(["List View", "Create New"])
    
    with t1:
        contacts = run_query("SELECT name, type, phone, email FROM contacts", fetch=True)
        st.dataframe(contacts, use_container_width=True, hide_index=True)
        
    with t2:
        with st.form("new_contact", border=False):
            st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            name = c1.text_input("Name")
            ctype = c2.selectbox("Type", ["Customer", "Vendor", "Employee"])
            phone = c1.text_input("Mobile")
            email = c2.text_input("Email Address")
            st.markdown("</div>", unsafe_allow_html=True)
            if st.form_submit_button("Save Contact"):
                if name:
                    run_query("INSERT INTO contacts (name, type, phone, email) VALUES (?,?,?,?)", (name, ctype, phone, email))
                    log_action("Created Contact", f"{name} ({ctype})")
                    st.success("Contact added.")
                    st.rerun()

elif choice == "Inventory":
    st.title("Inventory")
    t1, t2 = st.tabs(["Stock Overview", "New Product"])
    
    with t1:
        items = run_query("SELECT sku as SKU, name as Product, type as Type, qty_on_hand as 'On Hand', cost_price as Cost, sales_price as Price FROM items", fetch=True)
        st.dataframe(items, use_container_width=True, hide_index=True)
        
    with t2:
        with st.form("new_item", border=False):
            st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            name = c1.text_input("Product Name")
            sku = c2.text_input("Internal Reference (SKU)")
            itype = c1.selectbox("Product Type", ["Storable Product", "Service", "Consumable"])
            cost = c1.number_input("Cost", min_value=0.0, format="%.2f")
            price = c2.number_input("Sales Price", min_value=0.0, format="%.2f")
            st.markdown("</div>", unsafe_allow_html=True)
            if st.form_submit_button("Create Product"):
                if name:
                    run_query("INSERT INTO items (name, sku, type, qty_on_hand, cost_price, sales_price) VALUES (?,?,?,0,?,?)",
                              (name, sku, itype, cost, price))
                    log_action("Created Product", name)
                    st.rerun()

def render_transaction_module(doc_type):
    is_sale = (doc_type == "Sale")
    st.title(f"{'Quotations / Invoices' if is_sale else 'Vendor Bills'}")
    
    t1, t2 = st.tabs(["New Document", "All Records"])
    
    with t1:
        contact_type = "Customer" if is_sale else "Vendor"
        contacts_df = run_query(f"SELECT id, name FROM contacts WHERE type='{contact_type}'", fetch=True)
        items_df = run_query("SELECT id, name, sales_price, cost_price FROM items", fetch=True)
        
        if contacts_df.empty or items_df.empty:
            st.warning(f"Configuration required: Add {contact_type} and Products first.")
            return

        c_options = dict(zip(contacts_df['name'], contacts_df['id']))
        i_options = dict(zip(items_df['name'], items_df['id']))
        
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        selected_contact = col1.selectbox(contact_type, list(c_options.keys()))
        doc_date = col2.date_input("Invoice Date")
        st.markdown("</div>", unsafe_allow_html=True)
        
        st.markdown("### Order Lines")
        with st.form("add_line_form", border=False):
            sc1, sc2, sc3, sc4 = st.columns([3, 1, 1, 1])
            sel_item = sc1.selectbox("Product", list(i_options.keys()))
            qty = sc2.number_input("Qty", min_value=1.0, value=1.0)
            
            def_price = items_df[items_df['name']==sel_item]['sales_price'].values[0] if is_sale else items_df[items_df['name']==sel_item]['cost_price'].values[0]
            price = sc3.number_input("Unit Price", min_value=0.0, value=float(def_price))
            
            if sc4.form_submit_button("➕ Add"):
                st.session_state.cart.append({
                    'item_id': i_options[sel_item], 'name': sel_item, 'qty': qty, 
                    'unit_price': price, 'subtotal': qty * price
                })
                st.rerun()
        
        if st.session_state.cart:
            cart_df = pd.DataFrame(st.session_state.cart)
            st.dataframe(cart_df[['name', 'qty', 'unit_price', 'subtotal']], use_container_width=True, hide_index=True)
            
            total = cart_df['subtotal'].sum()
            st.markdown(f"<h2 style='text-align: right;'>Total: {currency} {total:,.2f}</h2>", unsafe_allow_html=True)
            
            c_btn1, c_btn2 = st.columns([1, 5])
            if c_btn1.button("Confirm", use_container_width=True):
                doc_no = create_document(doc_type, c_options[selected_contact], doc_date, st.session_state.cart)
                st.session_state.cart = []
                st.success(f"Posted: {doc_no}")
                st.rerun()
            if c_btn2.button("Discard"):
                st.session_state.cart = []
                st.rerun()

    with t2:
        history = run_query(f"""
            SELECT d.doc_number as Number, d.date as Date, c.name as Partner, d.total_amount as Total 
            FROM documents d JOIN contacts c ON d.contact_id = c.id 
            WHERE d.doc_type='{doc_type}' ORDER BY d.id DESC
        """, fetch=True)
        st.dataframe(history, use_container_width=True, hide_index=True)

if choice == "Sales":
    render_transaction_module("Sale")
elif choice == "Purchase":
    render_transaction_module("Purchase")

elif choice == "Accounting":
    st.title("Invoicing & Accounting")
    t1, t2, t3 = st.tabs(["General Ledger", "Journal Entry", "Partner Balances"])
    
    with t1:
        st.subheader("Chart of Accounts")
        b_df = get_account_balances()
        st.dataframe(b_df[['code', 'name', 'type', 'balance']], use_container_width=True, hide_index=True)
        
    with t2:
        st.subheader("Post a Journal")
        accounts = run_query("SELECT code, name FROM accounts", fetch=True)
        acc_dict = {f"{r['code']} - {r['name']}": r['code'] for _, r in accounts.iterrows()}
        
        with st.form("j_form", border=False):
            st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
            j_date = st.date_input("Date")
            j_desc = st.text_input("Reference / Label")
            c1, c2 = st.columns(2)
            dr_acc = c1.selectbox("Debit Account", list(acc_dict.keys()))
            dr_amt = c1.number_input("Debit", min_value=0.0)
            cr_acc = c2.selectbox("Credit Account", list(acc_dict.keys()), index=1)
            cr_amt = c2.number_input("Credit", min_value=0.0)
            st.markdown("</div>", unsafe_allow_html=True)
            if st.form_submit_button("Confirm Entry"):
                if dr_amt != cr_amt or dr_amt == 0:
                    st.error("Entry must be balanced and greater than zero.")
                else:
                    lines = [
                        {'account_code': acc_dict[dr_acc], 'debit': dr_amt, 'credit': 0},
                        {'account_code': acc_dict[cr_acc], 'debit': 0, 'credit': cr_amt}
                    ]
                    if post_journal_entry(j_date, j_desc, "Manual", lines):
                        st.success("Entry Posted.")
                        st.rerun()

    with t3:
        contacts = run_query("SELECT id, name FROM contacts", fetch=True)
        if not contacts.empty:
            c_dict = dict(zip(contacts['name'], contacts['id']))
            sel_c = st.selectbox("Partner", list(c_dict.keys()))
            ledger = run_query(f"""
                SELECT e.date as Date, e.description as Label, l.debit as Debit, l.credit as Credit
                FROM journal_entries e 
                JOIN journal_lines l ON e.id = l.entry_id
                WHERE l.contact_id = {c_dict[sel_c]}
                ORDER BY e.date
            """, fetch=True)
            if not ledger.empty:
                ledger['Balance'] = ledger['Debit'].cumsum() - ledger['Credit'].cumsum()
                st.dataframe(ledger, use_container_width=True, hide_index=True)
                st.metric("Running Balance", f"{currency} {ledger['Balance'].iloc[-1]:,.2f}")

elif choice == "Reporting":
    st.title("Reports")
    rep_type = st.segmented_control("Selection", ["Profit & Loss", "Balance Sheet"], default="Profit & Loss")
    
    b_df = get_account_balances()
    
    if rep_type == "Profit & Loss":
        revenue = b_df[b_df['type'] == 'Revenue']['balance'].sum()
        cogs = b_df[b_df['code'] == 5000]['balance'].sum()
        expenses = b_df[(b_df['type'] == 'Expense') & (b_df['code'] != 5000)]['balance'].sum()
        st.markdown(f"""
        <div class='metric-card'>
            <p>Net Income</p>
            <h1 style='color: var(--odoo-teal);'>{currency} {revenue - cogs - expenses:,.2f}</h1>
            <hr>
            <div style='display: flex; justify-content: space-between;'>
                <span>Revenue</span><span>{currency} {revenue:,.2f}</span>
            </div>
            <div style='display: flex; justify-content: space-between; color: #e53e3e;'>
                <span>COGS</span><span>({currency} {cogs:,.2f})</span>
            </div>
            <div style='display: flex; justify-content: space-between; color: #e53e3e;'>
                <span>Expenses</span><span>({currency} {expenses:,.2f})</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
    elif rep_type == "Balance Sheet":
        assets = b_df[b_df['type'] == 'Asset']['balance'].sum()
        liab = b_df[b_df['type'] == 'Liability']['balance'].sum()
        equity = b_df[b_df['type'] == 'Equity']['balance'].sum()
        rev = b_df[b_df['type'] == 'Revenue']['balance'].sum()
        exp = b_df[b_df['type'] == 'Expense']['balance'].sum()
        current_profit = rev - exp
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"### Assets")
            st.dataframe(b_df[b_df['type'] == 'Asset'][['name', 'balance']], use_container_width=True, hide_index=True)
            st.markdown(f"**Total Assets: {currency} {assets:,.2f}**")
        with c2:
            st.markdown(f"### Liabilities & Equity")
            st.dataframe(b_df[b_df['type'].isin(['Liability', 'Equity'])][['name', 'balance']], use_container_width=True, hide_index=True)
            st.write(f"Current Profit: {currency} {current_profit:,.2f}")
            st.markdown(f"**Total: {currency} {liab + equity + current_profit:,.2f}**")

elif choice == "Settings":
    st.title("Settings")
    
    with st.expander("Company Configuration", expanded=True):
        with st.form("settings_form", border=False):
            new_comp = st.text_input("Company Name", value=company_name)
            new_curr = st.text_input("Currency", value=currency)
            if st.form_submit_button("Save Changes"):
                run_query("UPDATE settings SET value=? WHERE key='company_name'", (new_comp,))
                run_query("UPDATE settings SET value=? WHERE key='currency'", (new_curr,))
                st.success("Updated.")
                st.rerun()
                
    with st.expander("Users & Access"):
        st.dataframe(run_query("SELECT username, role FROM users", fetch=True), hide_index=True)
        with st.form("add_user", border=False):
            u = st.text_input("New Username")
            p = st.text_input("New Password", type="password")
            r = st.selectbox("Role", ["Admin", "User"])
            if st.form_submit_button("Invite User"):
                try:
                    run_query("INSERT INTO users (username, password, role) VALUES (?,?,?)", (u, hash_password(p), r))
                    st.success("User added.")
                    st.rerun()
                except: st.error("Duplicate user.")

    with st.expander("Developer Logs"):
        st.dataframe(run_query("SELECT * FROM audit_logs ORDER BY id DESC", fetch=True), hide_index=True)