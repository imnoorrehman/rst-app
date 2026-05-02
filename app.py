import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('rst_business.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS parties (id INTEGER PRIMARY KEY, name TEXT, type TEXT, contact TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY, item_name TEXT, qty_kg REAL, rate_per_kg REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, date TEXT, party_name TEXT, description TEXT, debit REAL, credit REAL, account_type TEXT, category TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, account_name TEXT, balance REAL, type TEXT)')
    
    c.execute("SELECT count(*) FROM accounts")
    if c.fetchone()[0] == 0:
        default_accounts = [('Cash', 0, 'Asset'), ('Bank', 0, 'Asset'), ('Capital Account', 0, 'Equity')]
        c.executemany("INSERT INTO accounts (account_name, balance, type) VALUES (?,?,?)", default_accounts)
    conn.commit()
    conn.close()

init_db()

def run_query(query, params=(), fetch=False):
    with sqlite3.connect('rst_business.db') as conn:
        if fetch: return pd.read_sql(query, conn, params=params)
        conn.execute(query, params); conn.commit()

# --- WAVE ACCOUNTING DESIGN SYSTEM (REFINED) ---
st.set_page_config(page_title="RST Business Manager", layout="wide")

st.markdown("""
    <style>
    /* Wave Color Palette */
    :root {
        --wave-primary: #00a85d; 
        --wave-navy: #162d3d;
        --wave-light-text: #5d7079;
        --wave-bg: #f4f7f9;
        --border-color: #e3e9ed;
    }

    /* Background and Font */
    .stApp { 
        background-color: var(--wave-bg);
        font-family: 'Inter', -apple-system, sans-serif;
    }

    /* White Sidebar with minimal design */
    section[data-testid="stSidebar"] {
        background-color: #ffffff !important;
        border-right: 1px solid var(--border-color);
    }
    
    section[data-testid="stSidebar"] .stMarkdown h3 {
        color: var(--wave-navy) !important;
        font-size: 1.2rem;
        font-weight: 700;
        margin-bottom: 20px;
    }

    /* Metric Cards - Wave Style */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid var(--border-color);
        border-radius: 6px;
        padding: 15px !important;
        box-shadow: none !important;
    }
    
    div[data-testid="stMetricLabel"] {
        color: var(--wave-light-text) !important;
        font-weight: 500;
    }

    /* Button Styling */
    .stButton>button {
        background-color: var(--wave-primary);
        color: white;
        border-radius: 4px;
        border: 1px solid var(--wave-primary);
        padding: 0.5rem 1.5rem;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    
    .stButton>button:hover {
        background-color: #008f4e;
        border-color: #008f4e;
        color: white;
    }

    /* Input Field Styling */
    .stTextInput input, .stNumberInput input, .stSelectbox div {
        border-radius: 4px !important;
        border: 1px solid var(--border-color) !important;
    }

    /* Table Headers */
    h1, h2, h3 {
        color: var(--wave-navy);
        font-weight: 700;
    }
    
    hr {
        margin: 2rem 0;
        border: 0;
        border-top: 1px solid var(--border-color);
    }
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR & GLOBAL SEARCH ---
st.sidebar.markdown("### REHMAN SCRAP TRADER")
st.sidebar.markdown("---")
search_term = st.sidebar.text_input("Global Search", placeholder="Find invoices, customers...")

menu = ["Dashboard", "Cash and Bank", "Parties and Capital", "Inventory and Sales"]
choice = st.sidebar.radio("Main Menu", menu)

# --- UNIVERSAL SEARCH LOGIC ---
if search_term:
    st.markdown(f"## Search Results for '{search_term}'")
    found = False
    for table in ["parties", "transactions", "inventory", "accounts"]:
        df = run_query(f"SELECT * FROM {table}", fetch=True)
        if not df.empty:
            mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
            res = df[mask]
            if not res.empty:
                display_df = res.rename(columns={
                    'name': 'Name', 'type': 'Type', 'contact': 'Contact',
                    'item_name': 'Item Name', 'qty_kg': 'Qty (Kg)', 'rate_per_kg': 'Rate (Kg)',
                    'date': 'Date', 'party_name': 'Party Name', 'description': 'Description',
                    'debit': 'Debit (In)', 'credit': 'Credit (Out)', 'category': 'Category',
                    'account_name': 'Account Name', 'balance': 'Balance'
                })
                st.markdown(f"**Found in {table.title()}**")
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                found = True
    if not found: st.info("No records match your search criteria.")
    st.divider()

# --- 1. DASHBOARD ---
if choice == "Dashboard":
    st.title("Dashboard")
    
    def get_val(query):
        val = run_query(query, fetch=True).iloc[0,0]
        return float(val) if val is not None else 0.0

    sales = get_val("SELECT SUM(debit) FROM transactions WHERE category='Sale'")
    expenses = get_val("SELECT SUM(credit) FROM transactions WHERE category='Expense'")
    cash = get_val("SELECT balance FROM accounts WHERE account_name='Cash'")
    bank = get_val("SELECT balance FROM accounts WHERE account_name='Bank'")

    st.markdown("### Business Performance")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Revenue", f"Rs. {sales:,.0f}")
    c2.metric("Total Expenses", f"Rs. {expenses:,.0f}")
    c3.metric("Net Profit", f"Rs. {(sales - expenses):,.0f}")

    st.divider()
    
    st.markdown("### Liquidity & Cash Flow")
    c1, c2 = st.columns(2)
    c1.metric("Cash on Hand", f"Rs. {cash:,.0f}")
    c2.metric("Bank Balance", f"Rs. {bank:,.0f}")

# --- 2. CASH AND BANK ---
elif choice == "Cash and Bank":
    st.title("Cash and Bank")
    with st.expander("Record a Transaction", expanded=False):
        with st.form("bank_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            t_type = col1.selectbox("Type", ["Capital Injection", "Expense Payment"])
            target = col2.selectbox("Account", ["Cash", "Bank"])
            amt = st.number_input("Amount (PKR)", min_value=0.0)
            note = st.text_input("Remarks / Description")
            if st.form_submit_button("Save Transaction"):
                if t_type == "Capital Injection":
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (amt, target))
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = 'Capital Account'", (amt,))
                else:
                    run_query("UPDATE accounts SET balance = balance - ? WHERE account_name = ?", (amt, target))
                    run_query("INSERT INTO transactions (date, description, credit, category) VALUES (?,?,?,?)", 
                              (datetime.now().date(), note, amt, "Expense"))
                st.rerun()

    st.markdown("### Current Balances")
    acc_df = run_query("SELECT account_name, balance FROM accounts", fetch=True)
    st.dataframe(acc_df.rename(columns={'account_name': 'Account', 'balance': 'Balance (PKR)'}), use_container_width=True, hide_index=True)

# --- 3. PARTIES AND CAPITAL ---
elif choice == "Parties and Capital":
    st.title("Contacts")
    with st.expander("Add New Contact", expanded=False):
        with st.form("add_p"):
            n = st.text_input("Legal Name")
            t = st.selectbox("Relation Type", ["Customer", "Vendor"])
            if st.form_submit_button("Save Contact"):
                run_query("INSERT INTO parties (name, type) VALUES (?,?)", (n, t))
                st.rerun()
    
    st.markdown("### Customer & Vendor List")
    p_df = run_query("SELECT name, type FROM parties", fetch=True)
    st.dataframe(p_df.rename(columns={'name': 'Name', 'type': 'Type'}), use_container_width=True, hide_index=True)

# --- 4. INVENTORY AND SALES ---
elif choice == "Inventory and Sales":
    st.title("Inventory and Sales")
    tab1, tab2 = st.tabs(["Manage Inventory", "Create Sale"])
    
    with tab1:
        with st.expander("Add Stock Item", expanded=False):
            with st.form("add_inv_form"):
                it = st.text_input("Item Name")
                iq = st.number_input("Stock Quantity (Kg)", min_value=0.0)
                ir = st.number_input("Cost per Kg", min_value=0.0)
                if st.form_submit_button("Add to Stock"):
                    run_query("INSERT INTO inventory (item_name, qty_kg, rate_per_kg) VALUES (?,?,?)", (it, iq, ir))
                    st.rerun()
        
        st.markdown("### Current Stock Levels")
        inv_df = run_query("SELECT item_name, qty_kg, rate_per_kg FROM inventory", fetch=True)
        st.dataframe(inv_df.rename(columns={'item_name': 'Item', 'qty_kg': 'Qty (Kg)', 'rate_per_kg': 'Avg Cost'}), use_container_width=True, hide_index=True)

    with tab2:
        df_inv = run_query("SELECT * FROM inventory", fetch=True)
        customers = run_query("SELECT name FROM parties WHERE type='Customer'", fetch=True)
        
        if not df_inv.empty and not customers.empty:
            with st.expander("Record a Sale", expanded=False):
                with st.form("sale_f"):
                    c = st.selectbox("Customer", customers['name'].tolist())
                    i = st.selectbox("Item", df_inv['item_name'].tolist())
                    q = st.number_input("Quantity (Kg)", min_value=0.1)
                    r = st.number_input("Selling Price", min_value=1.0)
                    p = st.selectbox("Payment Mode", ["Cash", "Bank", "Credit (Account Receivable)"])
                    if st.form_submit_button("Record Sale"):
                        total = q * r
                        run_query("UPDATE inventory SET qty_kg = qty_kg - ? WHERE item_name = ?", (q, i))
                        run_query("INSERT INTO transactions (date, party_name, description, debit, category) VALUES (?,?,?,?,?)",
                                  (datetime.now().date(), c, f"Sale of {i}", total, "Sale"))
                        if "Credit" not in p:
                            mode = "Cash" if "Cash" in p else "Bank"
                            run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (total, mode))
                        st.rerun()
        else:
            st.warning("Please ensure you have added a Customer and Inventory items first.")