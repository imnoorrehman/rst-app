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

# --- WAVE ACCOUNTING DESIGN SYSTEM (LOCKED DESIGN) ---
st.set_page_config(page_title="RST | Rehman Scrap Trader", layout="wide")

st.markdown("""
    <style>
    :root {
        --wave-green: #00a85d; /* Wave's signature emerald accent */
        --wave-navy: #0e223a; /* Deep navy blue for headings */
        --wave-bg: #f9fbfd;   /* Light, soft blue-gray background */
        --wave-card: #ffffff;  /* Solid white cards */
        --wave-sidebar: #ffffff; /* Clean white sidebar */
    }

    /* Set global background */
    .stApp { background-color: var(--wave-bg); }

    /* Clean, paper-white sidebar with subtle border */
    section[data-testid="stSidebar"] {
        background-color: var(--wave-sidebar) !important;
        border-right: 1px solid #e2e8f0;
    }
    
    /* Sidebar typography & elements */
    section[data-testid="stSidebar"] * {
        color: #1a202c !important;
    }

    /* Wave Styled Cards */
    div[data-testid="stMetric"] {
        background-color: var(--wave-card);
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 20px !important;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
    }
    
    /* Buttons - Wave Green */
    .stButton>button {
        background-color: var(--wave-green);
        color: white;
        border-radius: 4px;
        border: none;
        padding: 8px 20px;
        font-weight: 500;
    }
    
    .stButton>button:hover {
        background-color: #008f4e;
        color: white;
    }

    /* Typography */
    h1, h2, h3 {
        color: var(--wave-navy);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR & GLOBAL SEARCH ---
st.sidebar.markdown("### RST TRADER")
st.sidebar.markdown("---")
search_term = st.sidebar.text_input("Global Search", placeholder="Find Invoices or Names")
menu = ["Dashboard", "Cash and Bank", "Parties and Capital", "Inventory and Sales"]
choice = st.sidebar.radio("Navigation Menu", menu)

# --- UNIVERSAL SEARCH LOGIC ---
if search_term:
    st.markdown(f"### Search Results: {search_term}")
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
                    'account_name': 'Account Name', 'balance': 'Current Balance'
                })
                st.write(f"Matches in {table.title()}")
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                found = True
    if not found: st.info("No matching records found.")
    st.markdown("---")

# --- 1. DASHBOARD ---
if choice == "Dashboard":
    st.title("Business Overview")
    def get_val(query):
        val = run_query(query, fetch=True).iloc[0,0]
        return float(val) if val is not None else 0.0

    sales = get_val("SELECT SUM(debit) FROM transactions WHERE category='Sale'")
    expenses = get_val("SELECT SUM(credit) FROM transactions WHERE category='Expense'")
    cash = get_val("SELECT balance FROM accounts WHERE account_name='Cash'")
    bank = get_val("SELECT balance FROM accounts WHERE account_name='Bank'")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Revenue", f"Rs. {sales:,.0f}")
    c2.metric("Total Expenses", f"Rs. {expenses:,.0f}")
    c3.metric("Net Profit", f"Rs. {(sales - expenses):,.0f}")

    st.markdown("### Liquidity Status")
    c1, c2 = st.columns(2)
    c1.metric("Cash in Hand", f"Rs. {cash:,.0f}")
    c2.metric("Bank Balance", f"Rs. {bank:,.0f}")

# --- 2. CASH AND BANK ---
elif choice == "Cash and Bank":
    st.title("Banking and Capital")
    with st.expander("Record New Banking Transaction", expanded=False):
        with st.form("bank_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            t_type = col1.selectbox("Transaction Type", ["Capital Injection", "Expense Payment"])
            target = col2.selectbox("Account", ["Cash", "Bank"])
            amt = st.number_input("Amount (PKR)", min_value=0.0)
            note = st.text_input("Remarks")
            if st.form_submit_button("Save Transaction"):
                if t_type == "Capital Injection":
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (amt, target))
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = 'Capital Account'", (amt,))
                else:
                    run_query("UPDATE accounts SET balance = balance - ? WHERE account_name = ?", (amt, target))
                    run_query("INSERT INTO transactions (date, description, credit, category) VALUES (?,?,?,?)", 
                              (datetime.now().date(), note, amt, "Expense"))
                st.rerun()

    st.subheader("Account Balances")
    acc_df = run_query("SELECT account_name, balance, type FROM accounts", fetch=True)
    st.dataframe(acc_df.rename(columns={'account_name': 'Account Name', 'balance': 'Balance (PKR)', 'type': 'Classification'}), use_container_width=True, hide_index=True)

# --- 3. PARTIES AND CAPITAL ---
elif choice == "Parties and Capital":
    st.title("Contacts and Parties")
    with st.expander("Add New Contact", expanded=False):
        with st.form("add_p"):
            n = st.text_input("Name")
            t = st.selectbox("Type", ["Customer", "Vendor"])
            if st.form_submit_button("Save Contact"):
                run_query("INSERT INTO parties (name, type) VALUES (?,?)", (n, t))
                st.rerun()
    
    st.subheader("Directory")
    p_df = run_query("SELECT name, type, contact FROM parties", fetch=True)
    st.dataframe(p_df.rename(columns={'name': 'Full Name', 'type': 'Relationship Type', 'contact': 'Contact Info'}), use_container_width=True, hide_index=True)

# --- 4. INVENTORY AND SALES ---
elif choice == "Inventory and Sales":
    st.title("Inventory and Sales")
    tab1, tab2 = st.tabs(["Stock Manager", "Sales Invoicing"])
    
    with tab1:
        with st.expander("Add Stock Item", expanded=False):
            with st.form("add_inv_form"):
                it = st.text_input("Item Name")
                iq = st.number_input("Quantity (Kg)", min_value=0.0)
                ir = st.number_input("Purchase Rate", min_value=0.0)
                if st.form_submit_button("Save Item"):
                    run_query("INSERT INTO inventory (item_name, qty_kg, rate_per_kg) VALUES (?,?,?)", (it, iq, ir))
                    st.rerun()
        
        inv_df = run_query("SELECT item_name, qty_kg, rate_per_kg FROM inventory", fetch=True)
        st.dataframe(inv_df.rename(columns={'item_name': 'Item Name', 'qty_kg': 'Qty (Kg)', 'rate_per_kg': 'Rate (Kg)'}), use_container_width=True, hide_index=True)

    with tab2:
        df_inv = run_query("SELECT * FROM inventory", fetch=True)
        customers = run_query("SELECT name FROM parties WHERE type='Customer'", fetch=True)
        
        if not df_inv.empty and not customers.empty:
            with st.expander("Create New Sale Invoice", expanded=False):
                with st.form("sale_f"):
                    c = st.selectbox("Customer", customers['name'].tolist())
                    i = st.selectbox("Item", df_inv['item_name'].tolist())
                    q = st.number_input("Quantity (Kg)", min_value=0.1)
                    r = st.number_input("Sale Price", min_value=1.0)
                    p = st.selectbox("Payment Method", ["Cash", "Bank", "Credit"])
                    if st.form_submit_button("Record Sale"):
                        total = q * r
                        run_query("UPDATE inventory SET qty_kg = qty_kg - ? WHERE item_name = ?", (q, i))
                        run_query("INSERT INTO transactions (date, party_name, description, debit, category) VALUES (?,?,?,?,?)",
                                  (datetime.now().date(), c, f"Sold {i}", total, "Sale"))
                        if p != "Credit":
                            run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (total, p))
                        st.rerun()
        else:
            st.warning("Please add at least one Customer and one Inventory item first.")