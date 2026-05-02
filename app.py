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
        c.execute("INSERT INTO settings VALUES ('company_name', 'My Modoo Business')")
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
    prefix = "INV-" if doc_type == "Sale" else "BILL-"
    doc_number = f"{prefix}{uuid.uuid4().hex[:6].upper()}"
    total_amount = sum(l['subtotal'] for l in lines_data)
    
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO documents (doc_type, doc_number, contact_id, date, total_amount) VALUES (?,?,?,?,?)",
                  (doc_type, doc_number, contact_id, date, total_amount))
        doc_id = c.lastrowid
        
        total_cogs = 0.0
        
        for l in lines_data:
            # Insert document line
            c.execute("INSERT INTO document_lines (doc_id, item_id, qty, unit_price, subtotal) VALUES (?,?,?,?,?)",
                      (doc_id, l['item_id'], l['qty'], l['unit_price'], l['subtotal']))
            
            # Inventory logic
            if doc_type == "Sale":
                c.execute("UPDATE items SET qty_on_hand = qty_on_hand - ? WHERE id = ?", (l['qty'], l['item_id']))
                # Calculate COGS for this line
                c.execute("SELECT cost_price FROM items WHERE id = ?", (l['item_id'],))
                cost = c.fetchone()[0] or 0.0
                total_cogs += (cost * l['qty'])
            elif doc_type == "Purchase":
                c.execute("UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id = ?", (l['qty'], l['item_id']))
                # Update moving average cost or last purchase price (simplifying to last purchase price here)
                c.execute("UPDATE items SET cost_price = ? WHERE id = ?", (l['unit_price'], l['item_id']))
                
        conn.commit()

    # Accounting Logic
    journal_lines = []
    if doc_type == "Sale":
        # AR Debit, Sales Credit
        journal_lines.append({'account_code': 1200, 'contact_id': contact_id, 'debit': total_amount, 'credit': 0})
        journal_lines.append({'account_code': 4000, 'contact_id': None, 'debit': 0, 'credit': total_amount})
        # COGS Debit, Inventory Credit
        if total_cogs > 0:
            journal_lines.append({'account_code': 5000, 'contact_id': None, 'debit': total_cogs, 'credit': 0})
            journal_lines.append({'account_code': 1300, 'contact_id': None, 'debit': 0, 'credit': total_cogs})
    elif doc_type == "Purchase":
        # Inventory Debit, AP Credit
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
# 3. UI INITIALIZATION & SESSION
# ==========================================
st.set_page_config(page_title="Modoo ERP", layout="wide", page_icon="🏢")

# Custom Styling (Odoo-like professional tone with user's Wave style)
st.markdown("""
    <style>
    :root { --primary: #714B67; --secondary: #017E84; --bg: #f9f9f9; --text: #333333; }
    .stApp { background-color: var(--bg); color: var(--text); }
    section[data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e3e9ed; }
    .stButton>button { background-color: var(--secondary); color: white; border-radius: 4px; border:none; width: 100%;}
    .stButton>button:hover { background-color: #016368; color: white; }
    h1, h2, h3 { color: var(--primary); font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .metric-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; border-top: 4px solid var(--secondary);}
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
    st.markdown("<h1 style='text-align: center; margin-top: 10%;'>🏢 Modoo ERP</h1>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,1,1])
    with c2:
        with st.form("login_form"):
            user = st.text_input("Username")
            pwd = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
            
            if submitted:
                res = run_query("SELECT id, username, role FROM users WHERE username=? AND password=?", 
                                (user, hash_password(pwd)), fetch=True)
                if not res.empty:
                    st.session_state.logged_in = True
                    st.session_state.username = res.iloc[0]['username']
                    st.session_state.role = res.iloc[0]['role']
                    log_action("Login", "User logged in successfully")
                    st.rerun()
                else:
                    st.error("Invalid credentials. Default is admin/admin")
    st.stop()

# ==========================================
# 5. SIDEBAR NAVIGATION
# ==========================================
company_name = run_query("SELECT value FROM settings WHERE key='company_name'", fetch=True).iloc[0]['value']
currency = run_query("SELECT value FROM settings WHERE key='currency'", fetch=True).iloc[0]['value']

st.sidebar.markdown(f"<h2>{company_name}</h2>", unsafe_allow_html=True)
st.sidebar.write(f"Logged in as: **{st.session_state.username}** ({st.session_state.role})")

menus = ["Dashboard", "Contacts", "Inventory", "Sales", "Purchase", "Accounting", "Reporting"]
if st.session_state.role == "Admin":
    menus.append("Settings")

if st.sidebar.button("Logout"):
    log_action("Logout", "User logged out")
    st.session_state.clear()
    st.rerun()

choice = st.sidebar.radio("Navigation", menus)

# ==========================================
# 6. MODULES IMPLEMENTATION
# ==========================================

if choice == "Dashboard":
    st.title("Dashboard")
    st.markdown("### Your business at a glance")
    
    # Calculate key metrics
    balances = get_account_balances()
    cash = balances[balances['code'].isin([1000, 1100])]['balance'].sum()
    ar = balances[balances['code'] == 1200]['balance'].sum()
    ap = balances[balances['code'] == 2000]['balance'].sum()
    sales = balances[balances['code'] == 4000]['balance'].sum()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"<div class='metric-card'><h4>Cash & Bank</h4><h2>{currency} {cash:,.2f}</h2></div>", unsafe_allow_html=True)
    c2.markdown(f"<div class='metric-card'><h4>Receivables (AR)</h4><h2>{currency} {ar:,.2f}</h2></div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='metric-card'><h4>Payables (AP)</h4><h2>{currency} {ap:,.2f}</h2></div>", unsafe_allow_html=True)
    c4.markdown(f"<div class='metric-card'><h4>Total Sales</h4><h2>{currency} {sales:,.2f}</h2></div>", unsafe_allow_html=True)
    
    st.markdown("---")
    st.subheader("Recent Activity Log")
    logs = run_query("SELECT timestamp, user, action, details FROM audit_logs ORDER BY id DESC LIMIT 5", fetch=True)
    st.dataframe(logs, use_container_width=True, hide_index=True)


elif choice == "Contacts":
    st.title("Contacts (CRM)")
    t1, t2 = st.tabs(["Directory", "Add Contact"])
    
    with t1:
        contacts = run_query("SELECT * FROM contacts", fetch=True)
        st.dataframe(contacts, use_container_width=True, hide_index=True)
        
    with t2:
        with st.form("new_contact"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Name/Company")
            ctype = c2.selectbox("Type", ["Customer", "Vendor", "Employee"])
            phone = c1.text_input("Phone")
            email = c2.text_input("Email")
            if st.form_submit_button("Save Contact"):
                if name:
                    run_query("INSERT INTO contacts (name, type, phone, email) VALUES (?,?,?,?)", (name, ctype, phone, email))
                    log_action("Created Contact", f"Name: {name}, Type: {ctype}")
                    st.success("Contact added successfully!")
                    st.rerun()

elif choice == "Inventory":
    st.title("Inventory Management")
    t1, t2 = st.tabs(["Stock Levels", "Add New Product"])
    
    with t1:
        items = run_query("SELECT id, sku, name, type, qty_on_hand, cost_price, sales_price FROM items", fetch=True)
        st.dataframe(items, use_container_width=True, hide_index=True)
        
    with t2:
        with st.form("new_item"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Product Name")
            sku = c2.text_input("SKU / Internal Reference")
            itype = c1.selectbox("Product Type", ["Storable Product", "Service"])
            cost = c1.number_input("Cost Price", min_value=0.0)
            price = c2.number_input("Sales Price", min_value=0.0)
            if st.form_submit_button("Save Product"):
                if name:
                    run_query("INSERT INTO items (name, sku, type, qty_on_hand, cost_price, sales_price) VALUES (?,?,?,0,?,?)",
                              (name, sku, itype, cost, price))
                    log_action("Created Product", f"Name: {name}")
                    st.success("Product saved!")
                    st.rerun()

def render_transaction_module(doc_type):
    is_sale = (doc_type == "Sale")
    st.title(f"{'Sales Invoices' if is_sale else 'Purchase Bills'}")
    
    t1, t2 = st.tabs(["Create New", "History"])
    
    with t1:
        # Fetch data for dropdowns
        contact_type = "Customer" if is_sale else "Vendor"
        contacts = run_query(f"SELECT id, name FROM contacts WHERE type='{contact_type}'", fetch=True)
        items = run_query("SELECT id, name, sales_price, cost_price, qty_on_hand FROM items", fetch=True)
        
        if contacts.empty or items.empty:
            st.warning(f"Please ensure you have created at least one {contact_type} and one Product.")
            return

        c_options = dict(zip(contacts['name'], contacts['id']))
        i_options = dict(zip(items['name'], items['id']))
        
        col1, col2 = st.columns([1, 3])
        with col1:
            selected_contact = st.selectbox(contact_type, list(c_options.keys()))
            doc_date = st.date_input("Date")
        
        with col2:
            st.markdown("**Cart Details**")
            with st.form("add_to_cart_form"):
                sc1, sc2, sc3 = st.columns(3)
                sel_item = sc1.selectbox("Product", list(i_options.keys()))
                qty = sc2.number_input("Quantity", min_value=1.0, value=1.0)
                
                # Default price logic based on type
                def_price = items[items['name']==sel_item]['sales_price'].values[0] if is_sale else items[items['name']==sel_item]['cost_price'].values[0]
                price = sc3.number_input("Unit Price", min_value=0.0, value=float(def_price))
                
                if st.form_submit_button("Add to Cart"):
                    item_id = i_options[sel_item]
                    st.session_state.cart.append({
                        'item_id': item_id, 'name': sel_item, 'qty': qty, 
                        'unit_price': price, 'subtotal': qty * price
                    })
                    st.rerun()
            
            if st.session_state.cart:
                cart_df = pd.DataFrame(st.session_state.cart)
                st.dataframe(cart_df[['name', 'qty', 'unit_price', 'subtotal']], use_container_width=True)
                st.markdown(f"### Total: {currency} {cart_df['subtotal'].sum():,.2f}")
                
                if st.button(f"Confirm & Post {doc_type}"):
                    doc_no = create_document(doc_type, c_options[selected_contact], doc_date, st.session_state.cart)
                    st.session_state.cart = [] # clear cart
                    st.success(f"{doc_type} {doc_no} posted successfully! Inventory and Accounts updated.")
                if st.button("Clear Cart"):
                    st.session_state.cart = []
                    st.rerun()

    with t2:
        history = run_query(f"""
            SELECT d.doc_number, d.date, c.name as contact, d.total_amount 
            FROM documents d JOIN contacts c ON d.contact_id = c.id 
            WHERE d.doc_type='{doc_type}' ORDER BY d.id DESC
        """, fetch=True)
        st.dataframe(history, use_container_width=True, hide_index=True)

if choice == "Sales":
    render_transaction_module("Sale")

elif choice == "Purchase":
    render_transaction_module("Purchase")

elif choice == "Accounting":
    st.title("Accounting")
    t1, t2, t3 = st.tabs(["Chart of Accounts", "Manual Journal Entry", "Partner Ledger"])
    
    with t1:
        st.subheader("Chart of Accounts & Balances")
        b_df = get_account_balances()
        st.dataframe(b_df[['code', 'name', 'type', 'balance']], use_container_width=True, hide_index=True)
        
    with t2:
        st.subheader("Post Manual Journal (e.g., Cash Receipt, Payments)")
        accounts = run_query("SELECT code, name FROM accounts", fetch=True)
        acc_dict = {f"{r['code']} - {r['name']}": r['code'] for _, r in accounts.iterrows()}
        
        with st.form("journal_form"):
            j_date = st.date_input("Date")
            j_desc = st.text_input("Description / Memo")
            
            c1, c2 = st.columns(2)
            dr_acc = c1.selectbox("Debit Account", list(acc_dict.keys()))
            dr_amt = c1.number_input("Debit Amount", min_value=0.0)
            
            cr_acc = c2.selectbox("Credit Account", list(acc_dict.keys()))
            cr_amt = c2.number_input("Credit Amount", min_value=0.0)
            
            if st.form_submit_button("Post Journal"):
                if dr_amt != cr_amt:
                    st.error("Debit and Credit must be equal.")
                elif dr_amt > 0:
                    lines = [
                        {'account_code': acc_dict[dr_acc], 'debit': dr_amt, 'credit': 0},
                        {'account_code': acc_dict[cr_acc], 'debit': 0, 'credit': cr_amt}
                    ]
                    if post_journal_entry(j_date, j_desc, "Manual", lines):
                        log_action("Manual Journal", f"Amt: {dr_amt}, Dr: {dr_acc}, Cr: {cr_acc}")
                        st.success("Journal Posted Successfully")
                        st.rerun()

    with t3:
        st.subheader("Partner Ledger (Customer/Vendor Account)")
        contacts = run_query("SELECT id, name FROM contacts", fetch=True)
        if not contacts.empty:
            c_dict = dict(zip(contacts['name'], contacts['id']))
            sel_c = st.selectbox("Select Partner", list(c_dict.keys()))
            
            ledger = run_query(f"""
                SELECT e.date, e.description, e.reference, l.debit, l.credit
                FROM journal_entries e 
                JOIN journal_lines l ON e.id = l.entry_id
                WHERE l.contact_id = {c_dict[sel_c]}
                ORDER BY e.date
            """, fetch=True)
            
            if not ledger.empty:
                ledger['Balance'] = ledger['debit'].cumsum() - ledger['credit'].cumsum()
                st.dataframe(ledger, use_container_width=True, hide_index=True)
                st.metric("Net Balance", f"{currency} {ledger['Balance'].iloc[-1]:,.2f}")
            else:
                st.info("No transactions found for this partner.")

elif choice == "Reporting":
    st.title("Financial Reporting")
    rep_type = st.radio("Select Report", ["Profit & Loss", "Balance Sheet"])
    
    b_df = get_account_balances()
    
    if rep_type == "Profit & Loss":
        st.subheader("Profit & Loss Statement")
        revenue = b_df[b_df['type'] == 'Revenue']['balance'].sum()
        cogs = b_df[b_df['code'] == 5000]['balance'].sum()
        gross_profit = revenue - cogs
        expenses = b_df[(b_df['type'] == 'Expense') & (b_df['code'] != 5000)]['balance'].sum()
        net_profit = gross_profit - expenses
        
        st.markdown(f"""
        <div style="background:white; padding:20px; border-radius:8px; border-left:5px solid var(--secondary);">
            <h4>Total Revenue: {currency} {revenue:,.2f}</h4>
            <h4 style="color:red;">Cost of Goods Sold: {currency} {cogs:,.2f}</h4>
            <h3>Gross Profit: {currency} {gross_profit:,.2f}</h3>
            <h4 style="color:red;">Operating Expenses: {currency} {expenses:,.2f}</h4>
            <hr>
            <h2 style="color:var(--primary);">Net Profit: {currency} {net_profit:,.2f}</h2>
        </div>
        """, unsafe_allow_html=True)
        
    elif rep_type == "Balance Sheet":
        st.subheader("Balance Sheet")
        assets = b_df[b_df['type'] == 'Asset']['balance'].sum()
        liab = b_df[b_df['type'] == 'Liability']['balance'].sum()
        equity = b_df[b_df['type'] == 'Equity']['balance'].sum()
        
        # Add current year net profit to equity
        rev = b_df[b_df['type'] == 'Revenue']['balance'].sum()
        exp = b_df[b_df['type'] == 'Expense']['balance'].sum()
        current_earnings = rev - exp
        total_equity_liab = liab + equity + current_earnings
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"### Assets")
            st.dataframe(b_df[b_df['type'] == 'Asset'][['name', 'balance']], hide_index=True)
            st.markdown(f"**Total Assets: {currency} {assets:,.2f}**")
        with c2:
            st.markdown(f"### Liabilities & Equity")
            st.dataframe(b_df[b_df['type'].isin(['Liability', 'Equity'])][['name', 'balance']], hide_index=True)
            st.markdown(f"Current Year Earnings: {currency} {current_earnings:,.2f}")
            st.markdown(f"**Total Liab & Equity: {currency} {total_equity_liab:,.2f}**")

elif choice == "Settings":
    st.title("System Settings")
    
    with st.expander("Company Settings", expanded=True):
        with st.form("settings_form"):
            new_comp = st.text_input("Company Name", value=company_name)
            new_curr = st.text_input("Currency Symbol", value=currency)
            if st.form_submit_button("Update Details"):
                run_query("UPDATE settings SET value=? WHERE key='company_name'", (new_comp,))
                run_query("UPDATE settings SET value=? WHERE key='currency'", (new_curr,))
                log_action("Updated Settings", "Company Details")
                st.success("Settings updated! Please refresh the page.")
                
    with st.expander("User Management"):
        users = run_query("SELECT id, username, role FROM users", fetch=True)
        st.dataframe(users, hide_index=True)
        st.markdown("**Add New User**")
        with st.form("add_user"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            r = st.selectbox("Role", ["Admin", "User"])
            if st.form_submit_button("Create User"):
                try:
                    run_query("INSERT INTO users (username, password, role) VALUES (?,?,?)", (u, hash_password(p), r))
                    log_action("Created User", f"Username: {u}")
                    st.success("User created.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Username already exists!")

    with st.expander("System Logs"):
        logs = run_query("SELECT * FROM audit_logs ORDER BY id DESC", fetch=True)
        st.dataframe(logs, use_container_width=True, hide_index=True)