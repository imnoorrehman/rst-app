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

# --- FRESHBOOKS LOOK & FEEL (LOCKED DESIGN) ---
st.set_page_config(page_title="RST | Rehman Scrap Trader", layout="wide")

st.markdown("""
    <style>
    :root {
        --fb-blue: #0075dd;
        --fb-green: #00a85d;
        --fb-slate: #2d3e50;
        --fb-bg: #f4f7f9;
    }

    .stApp { background-color: var(--fb-bg); }

    section[data-testid="stSidebar"] {
        background-color: var(--fb-slate) !important;
    }
    section[data-testid="stSidebar"] * {
        color: #ffffff !important;
    }

    div[data-testid="stMetric"] {
        background-color: white;
        border: 1px solid #e1e8ed;
        border-radius: 12px;
        padding: 20px !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.02);
    }

    .stButton>button {
        background-color: var(--fb-blue);
        color: white;
        border-radius: 6px;
        border: none;
        padding: 10px 24px;
        font-weight: 600;
    }
    
    h1, h2, h3 {
        color: var(--fb-slate);
        font-family: 'Segoe UI', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR NAVIGATION ---
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
                st.write(f"Matches in {table.title()}")
                st.dataframe(res, use_container_width=True)
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
    with st.container():
        st.markdown('<div style="background-color:white; padding:25px; border-radius:12px; border:1px solid #e1e8ed;">', unsafe_allow_html=True)
        with st.form("bank_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            t_type = col1.selectbox("Transaction Type", ["Capital Injection", "Expense Payment"])
            target = col2.selectbox("Account", ["Cash", "Bank"])
            amt = st.number_input("Amount (PKR)", min_value=0.0)
            note = st.text_input("Remarks")
            if st.form_submit_button("Record Transaction"):
                if t_type == "Capital Injection":
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (amt, target))
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = 'Capital Account'", (amt,))
                else:
                    run_query("UPDATE accounts SET balance = balance - ? WHERE account_name = ?", (amt, target))
                    run_query("INSERT INTO transactions (date, description, credit, category) VALUES (?,?,?,?)", 
                              (datetime.now().date(), note, amt, "Expense"))
                st.success("Ledger updated successfully")
        st.markdown('</div>', unsafe_allow_html=True)

# --- 3. PARTIES AND CAPITAL ---
elif choice == "Parties and Capital":
    st.title("Contacts and Parties")
    with st.expander("Add New Contact"):
        with st.form("add_p"):
            n = st.text_input("Name")
            t = st.selectbox("Type", ["Customer", "Vendor"])
            if st.form_submit_button("Save"):
                run_query("INSERT INTO parties (name, type) VALUES (?,?)", (n, t))
    
    st.subheader("Directory")
    st.dataframe(run_query("SELECT * FROM parties", fetch=True), use_container_width=True)

# --- 4. INVENTORY AND SALES ---
elif choice == "Inventory and Sales":
    st.title("Inventory and Sales")
    
    tab1, tab2 = st.tabs(["Inventory", "Sales"])
    
    with tab1:
        with st.expander("Add Stock Item"):
            it = st.text_input("Item Name")
            iq = st.number_input("Quantity", min_value=0.0)
            ir = st.number_input("Rate", min_value=0.0)
            if st.button("Save Item"):
                run_query("INSERT INTO inventory (item_name, qty_kg, rate_per_kg) VALUES (?,?,?)", (it, iq, ir))
        st.dataframe(run_query("SELECT * FROM inventory", fetch=True), use_container_width=True)

    with tab2:
        df_inv = run_query("SELECT * FROM inventory", fetch=True)
        customers = run_query("SELECT name FROM parties WHERE type='Customer'", fetch=True)
        
        if not df_inv.empty and not customers.empty:
            with st.form("sale_f"):
                c = st.selectbox("Customer", customers['name'].tolist())
                i = st.selectbox("Item", df_inv['item_name'].tolist())
                q = st.number_input("Quantity", min_value=0.1)
                r = st.number_input("Price", min_value=1.0)
                p = st.selectbox("Payment Method", ["Cash", "Bank", "Credit"])
                if st.form_submit_button("Record Sale"):
                    total = q * r
                    run_query("UPDATE inventory SET qty_kg = qty_kg - ? WHERE item_name = ?", (q, i))
                    run_query("INSERT INTO transactions (date, party_name, description, debit, category) VALUES (?,?,?,?,?)",
                              (datetime.now().date(), c, f"Sold {i}", total, "Sale"))
                    if p != "Credit":
                        run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (total, p))
                    st.success(f"Transaction of Rs.{total:,.0f} complete")