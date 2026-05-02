import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from fpdf import FPDF
import os

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('rst_business.db')
    c = conn.cursor()
    # Parties Master
    c.execute('''CREATE TABLE IF NOT EXISTS parties 
                 (id INTEGER PRIMARY KEY, name TEXT, type TEXT, contact TEXT)''')
    # Inventory
    c.execute('''CREATE TABLE IF NOT EXISTS inventory 
                 (id INTEGER PRIMARY KEY, item_name TEXT, qty_kg REAL, rate_per_kg REAL)''')
    # Transactions (The Ledger)
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id INTEGER PRIMARY KEY, date TEXT, party_name TEXT, description TEXT, 
                  debit REAL, credit REAL, account_type TEXT)''')
    # Expenses
    c.execute('''CREATE TABLE IF NOT EXISTS expenses 
                 (id INTEGER PRIMARY KEY, date TEXT, category TEXT, amount REAL, method TEXT)''')
    conn.commit()
    conn.close()

init_db()

def run_query(query, params=(), fetch=False):
    with sqlite3.connect('rst_business.db') as conn:
        if fetch:
            return pd.read_sql(query, conn, params=params)
        conn.execute(query, params)
        conn.commit()

# --- UI LOGIC ---
st.set_page_config(page_title="RST Business Manager", layout="wide")

# 1. UNIVERSAL SEARCH BAR
st.sidebar.title("🔍 Global Search")
search_term = st.sidebar.text_input("Find anything (Bill, Party, Amount)...")

if search_term:
    st.header(f"Search Results for: '{search_term}'")
    tables = ["parties", "transactions", "inventory", "expenses"]
    for table in tables:
        results = run_query(f"SELECT * FROM {table} WHERE LOWER(CAST(id AS TEXT) || ' ' || printf('%s', (SELECT group_concat(name) FROM pragma_table_info('{table}'))) || ' ' || *) LIKE ?", 
                            (f'%{search_term.lower()}%',), fetch=True)
        if not results.empty:
            st.subheader(f"Results in {table.capitalize()}")
            st.dataframe(results, use_container_width=True)

# --- NAVIGATION ---
menu = ["Dashboard", "Sales Module", "Expenses & Purchases", "Parties Master", "Inventory"]
choice = st.sidebar.selectbox("Navigation", menu)

# 2. DASHBOARD & REPORTS
if choice == "Dashboard":
    st.title("📊 RST Financial Dashboard")
    
    # Metrics Calculation
    sales = run_query("SELECT SUM(debit) FROM transactions WHERE account_type='Sale'", fetch=True).iloc[0,0] or 0
    exp = run_query("SELECT SUM(amount) FROM expenses", fetch=True).iloc[0,0] or 0
    purchases = run_query("SELECT SUM(credit) FROM transactions WHERE account_type='Purchase'", fetch=True).iloc[0,0] or 0
    net_profit = sales - exp - purchases

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Sales (Revenue)", f"PKR {sales:,.2f}")
    col2.metric("Total Expenses", f"PKR {exp:,.2f}", delta_color="inverse")
    col3.metric("Net Profit", f"PKR {net_profit:,.2f}")

    st.divider()
    st.subheader("Recent Transactions")
    st.table(run_query("SELECT * FROM transactions ORDER BY id DESC LIMIT 5", fetch=True))

# 3. SALES MODULE (The Auto-Update Engine)
elif choice == "Sales Module":
    st.title("📝 Record New Sale")
    
    parties = run_query("SELECT name FROM parties WHERE type='Customer'", fetch=True)['name'].tolist()
    items = run_query("SELECT item_name FROM inventory", fetch=True)['item_name'].tolist()
    
    with st.form("sale_form"):
        party = st.selectbox("Select Customer", parties)
        item = st.selectbox("Select Item", items)
        qty = st.number_input("Quantity (KG)", min_value=0.1)
        rate = st.number_input("Rate per KG (PKR)", min_value=1.0)
        total = qty * rate
        submitted = st.form_submit_button("Complete Sale & Update Ledger")
        
        if submitted:
            # 1. Update Inventory
            run_query("UPDATE inventory SET qty_kg = qty_kg - ? WHERE item_name = ?", (qty, item))
            # 2. Update Ledger
            run_query("INSERT INTO transactions (date, party_name, description, debit, credit, account_type) VALUES (?,?,?,?,?,?)",
                      (datetime.now().strftime("%Y-%m-%d"), party, f"Sale of {item}", total, 0, "Sale"))
            st.success(f"Sale recorded! Inventory updated and PKR {total} added to {party} outstanding.")

# 4. PARTIES MASTER
elif choice == "Parties Master":
    st.title("👥 Party Management")
    with st.expander("Add New Party"):
        name = st.text_input("Party Name")
        p_type = st.selectbox("Type", ["Customer", "Vendor"])
        contact = st.text_input("Contact Info")
        if st.button("Save Party"):
            run_query("INSERT INTO parties (name, type, contact) VALUES (?,?,?)", (name, p_type, contact))
            st.rerun()
    
    st.dataframe(run_query("SELECT * FROM parties", fetch=True), use_container_width=True)

# 5. EXPENSES
elif choice == "Expenses & Purchases":
    st.title("💸 Expense & Cash Book")
    with st.form("exp_form"):
        cat = st.selectbox("Category", ["Rent", "Electricity", "Labor", "Purchase", "Other"])
        amt = st.number_input("Amount (PKR)")
        method = st.selectbox("Payment Method", ["Cash", "Bank Transfer", "Cheque"])
        if st.form_submit_button("Record Expense"):
            run_query("INSERT INTO expenses (date, category, amount, method) VALUES (?,?,?,?)",
                      (datetime.now().strftime("%Y-%m-%d"), cat, amt, method))
            st.success("Expense deducted from Cash/Bank summary.")

# 6. INVENTORY
elif choice == "Inventory":
    st.title("📦 Stock Control")
    # Quick Inventory Add (For initial setup)
    with st.expander("Add New Item Stock"):
        i_name = st.text_input("Item Name")
        i_qty = st.number_input("Initial Qty", min_value=0.0)
        if st.button("Add Item"):
            run_query("INSERT INTO inventory (item_name, qty_kg, rate_per_kg) VALUES (?,?,0)", (i_name, i_qty))
    
    st.dataframe(run_query("SELECT * FROM inventory", fetch=True), use_container_width=True)