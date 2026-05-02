import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('rst_business.db')
    c = conn.cursor()
    # Updated Tables
    c.execute('CREATE TABLE IF NOT EXISTS parties (id INTEGER PRIMARY KEY, name TEXT, type TEXT, contact TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY, item_name TEXT, qty_kg REAL, rate_per_kg REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, date TEXT, party_name TEXT, description TEXT, debit REAL, credit REAL, account_type TEXT, category TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, account_name TEXT, balance REAL, type TEXT)')
    
    # Initialize Default Accounts if empty
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

# --- UI LOOK & FEEL ---
st.set_page_config(page_title="RST Pro Manager", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    [data-testid="stSidebar"] { background-color: #1e293b; color: white; }
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR NAVIGATION ---
st.sidebar.title("🛠 RST Operations")
menu = ["📊 Financial Dashboard", "💰 Cash & Bank", "🤝 Parties & Capital", "📦 Inventory & Sales"]
choice = st.sidebar.selectbox("Go to:", menu)

# --- 1. FINANCIAL DASHBOARD ---
if choice == "📊 Financial Dashboard":
    st.title("Financial Overview")
    
    # Financial Logic
    sales = run_query("SELECT SUM(debit) FROM transactions WHERE category='Sale'", fetch=True).iloc[0,0] or 0
    expenses = run_query("SELECT SUM(credit) FROM transactions WHERE category='Expense'", fetch=True).iloc[0,0] or 0
    cash_bal = run_query("SELECT balance FROM accounts WHERE account_name='Cash'", fetch=True).iloc[0,0] or 0
    bank_bal = run_query("SELECT balance FROM accounts WHERE account_name='Bank'", fetch=True).iloc[0,0] or 0
    capital = run_query("SELECT balance FROM accounts WHERE account_name='Capital Account'", fetch=True).iloc[0,0] or 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Cash in Hand", f"Rs. {cash_bal:,.0f}")
    col2.metric("Bank Balance", f"Rs. {bank_bal:,.0f}")
    col3.metric("Net Profit", f"Rs. {(sales - expenses):,.0f}")
    col4.metric("Owner's Equity", f"Rs. {capital:,.0f}")

    st.subheader("Balance Sheet Summary")
    bs_data = {
        "Assets": ["Cash", "Bank", "Inventory Value"],
        "Amount": [cash_bal, bank_bal, (run_query("SELECT SUM(qty_kg * rate_per_kg) FROM inventory", fetch=True).iloc[0,0] or 0)]
    }
    st.table(pd.DataFrame(bs_data))

# --- 2. CASH & BANK ---
elif choice == "💰 Cash & Bank":
    st.title("Cash & Bank Management")
    
    tab1, tab2 = st.tabs(["Record Transaction", "Account History"])
    
    with tab1:
        with st.form("cash_form"):
            t_type = st.selectbox("Transaction Type", ["Capital Injection", "Expense Payment", "Bank Transfer"])
            from_acc = st.selectbox("From/To Account", ["Cash", "Bank", "Capital Account"])
            amount = st.number_input("Amount (PKR)", min_value=0.0)
            note = st.text_input("Remarks")
            if st.form_submit_button("Record"):
                if t_type == "Capital Injection":
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (amount, from_acc))
                    run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = 'Capital Account'", (amount,))
                elif t_type == "Expense Payment":
                    run_query("UPDATE accounts SET balance = balance - ? WHERE account_name = ?", (amount, from_acc))
                    run_query("INSERT INTO transactions (date, description, credit, category) VALUES (?,?,?,?)", 
                              (datetime.now().date(), note, amount, "Expense"))
                st.success("Accounts Updated Successfully!")

# --- 3. PARTIES & CAPITAL ---
elif choice == "🤝 Parties & Capital":
    st.title("Parties Master Data")
    name = st.text_input("Party Name")
    p_type = st.selectbox("Type", ["Customer", "Vendor"])
    if st.button("Add Party"):
        run_query("INSERT INTO parties (name, type) VALUES (?,?)", (name, p_type))
        st.success(f"{name} added!")
    
    st.dataframe(run_query("SELECT * FROM parties", fetch=True), use_container_width=True)

# --- 4. INVENTORY & SALES ---
elif choice == "📦 Inventory & Sales":
    st.title("Inventory & Sales")
    # Quick Inventory View
    df_inv = run_query("SELECT * FROM inventory", fetch=True)
    st.dataframe(df_inv, use_container_width=True)
    
    with st.expander("Record New Sale"):
        cust = st.selectbox("Customer", run_query("SELECT name FROM parties WHERE type='Customer'", fetch=True))
        item = st.selectbox("Item", df_inv['item_name'].tolist() if not df_inv.empty else ["No Items"])
        s_qty = st.number_input("Qty (KG)", min_value=0.0)
        s_rate = st.number_input("Rate", min_value=0.0)
        pay_method = st.selectbox("Payment Received In", ["Cash", "Bank", "Pending/Credit"])
        
        if st.button("Finalize Sale"):
            total = s_qty * s_rate
            # 1. Deduct Stock
            run_query("UPDATE inventory SET qty_kg = qty_kg - ? WHERE item_name = ?", (s_qty, item))
            # 2. Record Transaction
            run_query("INSERT INTO transactions (date, party_name, description, debit, category) VALUES (?,?,?,?,?)",
                      (datetime.now().date(), cust, f"Sale: {item}", total, "Sale"))
            # 3. Update Cash/Bank
            if pay_method != "Pending/Credit":
                run_query("UPDATE accounts SET balance = balance + ? WHERE account_name = ?", (total, pay_method))
            st.success("Sale completed!")