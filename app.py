import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from fpdf import FPDF

# --- DATABASE ARCHITECTURE ---
def init_db():
    conn = sqlite3.connect('rst_manager_v3.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts 
                 (code INTEGER PRIMARY KEY, name TEXT, category TEXT, balance REAL)''')
    c.execute('CREATE TABLE IF NOT EXISTS parties (id INTEGER PRIMARY KEY, name TEXT, type TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY, item_name TEXT, qty REAL, unit_price REAL)')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, account_name TEXT, 
                  party_name TEXT, description TEXT, debit REAL, credit REAL, 
                  voucher_type TEXT, reference TEXT)''')

    c.execute("SELECT count(*) FROM accounts")
    if c.fetchone()[0] == 0:
        coa = [
            (101, 'Cash on Hand', 'Asset', 0), (102, 'Bank Account', 'Asset', 0),
            (103, 'Accounts Receivable', 'Asset', 0), (104, 'Inventory on Hand', 'Asset', 0),
            (201, 'Accounts Payable', 'Liability', 0), (301, 'Capital Account', 'Equity', 0),
            (401, 'Sales', 'Income', 0), (501, 'Cost of Goods Sold', 'Expense', 0),
            (502, 'Operating Expenses', 'Expense', 0)
        ]
        c.executemany("INSERT INTO accounts VALUES (?,?,?,?)", coa)
    conn.commit()
    conn.close()

init_db()

def run_query(query, params=(), fetch=False):
    with sqlite3.connect('rst_manager_v3.db') as conn:
        if fetch: return pd.read_sql(query, conn, params=params)
        conn.execute(query, params); conn.commit()

def post_transaction(date, acc, party, desc, dr, cr, vtype, ref):
    run_query("""INSERT INTO transactions 
                 (date, account_name, party_name, description, debit, credit, voucher_type, reference) 
                 VALUES (?,?,?,?,?,?,?,?)""",
              (date, acc, party, desc, dr, cr, vtype, ref))
    if dr > 0: run_query("UPDATE accounts SET balance = balance + ? WHERE name = ?", (dr, acc))
    if cr > 0: run_query("UPDATE accounts SET balance = balance - ? WHERE name = ?", (cr, acc))

# --- UI DESIGN (WAVE STYLE) ---
st.set_page_config(page_title="RST Manager", layout="wide")
st.markdown("""
    <style>
    :root { --wave-green: #00a85d; --wave-navy: #162d3d; --wave-bg: #f4f7f9; }
    .stApp { background-color: var(--wave-bg); }
    section[data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e3e9ed; }
    .stButton>button { background-color: var(--wave-green); color: white; border-radius: 4px; border:none; }
    h1, h2, h3 { color: var(--wave-navy); font-family: sans-serif; }
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
st.sidebar.title("Manager.io")
menu = ["Summary", "P&L Statement", "Partner Ledger", "Bank and Cash", "Receipts", "Payments", "Sales Invoices", "Inventory", "Contacts"]
choice = st.sidebar.radio("Navigate", menu)

# --- 1. SUMMARY ---
if choice == "Summary":
    st.title("Summary")
    df = run_query("SELECT name, category, balance FROM accounts", fetch=True)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Assets")
        st.table(df[df['category'] == 'Asset'].rename(columns={'name':'Account','balance':'Balance'}))
    with c2:
        st.subheader("Liabilities & Equity")
        st.table(df[df['category'].isin(['Liability', 'Equity'])])

# --- 2. PROFIT & LOSS (P&L) ---
elif choice == "P&L Statement":
    st.title("Profit and Loss Statement")
    df = run_query("SELECT name, category, balance FROM accounts", fetch=True)
    
    income = df[df['category'] == 'Income']['balance'].sum() * -1 # Income usually has credit balance
    expenses = df[df['category'] == 'Expense']['balance'].sum()
    net_profit = income - expenses

    st.markdown(f"""
    <div style="background:white; padding:20px; border-radius:8px; border:1px solid #e3e9ed;">
        <h3>Net Profit: Rs. {net_profit:,.2f}</h3>
        <p>Total Revenue: Rs. {income:,.2f}</p>
        <p>Total Expenses: Rs. {expenses:,.2f}</p>
    </div>
    """, unsafe_allow_html=True)
    
    st.subheader("Details")
    st.write("**Revenue Accounts**")
    st.table(df[df['category'] == 'Income'])
    st.write("**Expense Accounts**")
    st.table(df[df['category'] == 'Expense'])

# --- 3. PARTNER LEDGER ---
elif choice == "Partner Ledger":
    st.title("Partner Ledger")
    parties = run_query("SELECT name FROM parties", fetch=True)['name'].tolist()
    
    if parties:
        selected_party = st.selectbox("Select Partner (Customer/Supplier)", parties)
        ledger = run_query("""SELECT date, description, voucher_type, debit, credit 
                             FROM transactions WHERE party_name = ?""", (selected_party,), fetch=True)
        
        if not ledger.empty:
            ledger['Balance'] = ledger['debit'].cumsum() - ledger['credit'].cumsum()
            st.dataframe(ledger.rename(columns={'date':'Date','description':'Description','debit':'Dr','credit':'Cr'}), 
                         use_container_width=True, hide_index=True)
            st.metric(f"Current Balance for {selected_party}", f"Rs. {ledger['Balance'].iloc[-1]:,.2f}")
        else:
            st.info("No transactions found for this partner.")
    else:
        st.warning("No parties found. Add a Customer or Supplier first.")

# --- 4. RECEIPTS ---
elif choice == "Receipts":
    st.title("Receipts")
    parties = run_query("SELECT name FROM parties", fetch=True)['name'].tolist()
    with st.expander("New Receipt"):
        with st.form("rec_f"):
            d = st.date_input("Date")
            acc = st.selectbox("Received In", ["Cash on Hand", "Bank Account"])
            party = st.selectbox("Received From", parties) if parties else st.text_input("Received From")
            amt = st.number_input("Amount", min_value=0.0)
            if st.form_submit_button("Save Receipt"):
                post_transaction(d, acc, party, f"Receipt from {party}", amt, 0, "Receipt", "REC-001")
                post_transaction(d, "Accounts Receivable", party, f"Payment received", 0, amt, "Receipt", "REC-001")
                st.success("Receipt Posted")
                st.rerun()

# --- 5. CONTACTS (Customers/Suppliers) ---
elif choice == "Contacts":
    st.title("Contacts")
    with st.form("add_contact"):
        n = st.text_input("Contact Name")
        t = st.selectbox("Type", ["Customer", "Supplier"])
        if st.form_submit_button("Save"):
            run_query("INSERT INTO parties (name, type) VALUES (?,?)", (n, t))
            st.rerun()
    st.dataframe(run_query("SELECT name as Name, type as Type FROM parties", fetch=True), use_container_width=True)

# --- 6. SALES INVOICES ---
elif choice == "Sales Invoices":
    st.title("Sales Invoices")
    parties = run_query("SELECT name FROM parties WHERE type='Customer'", fetch=True)['name'].tolist()
    with st.expander("New Sales Invoice"):
        with st.form("sale_f"):
            d = st.date_input("Date")
            cust = st.selectbox("Customer", parties) if parties else st.text_input("Customer")
            desc = st.text_input("Description")
            amt = st.number_input("Amount", min_value=0.0)
            if st.form_submit_button("Issue Invoice"):
                post_transaction(d, "Accounts Receivable", cust, desc, amt, 0, "Invoice", "INV-001")
                post_transaction(d, "Sales", cust, desc, 0, amt, "Invoice", "INV-001")
                st.success("Invoice Saved")
                st.rerun()