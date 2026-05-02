import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from fpdf import FPDF
import base64

# --- DATABASE ARCHITECTURE ---
def init_db():
    conn = sqlite3.connect('rst_manager_v2.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts 
                 (code INTEGER PRIMARY KEY, name TEXT, category TEXT, balance REAL)''')
    c.execute('CREATE TABLE IF NOT EXISTS parties (id INTEGER PRIMARY KEY, name TEXT, type TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY, item_name TEXT, qty REAL, unit_price REAL)')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id PRIMARY KEY, date TEXT, account_name TEXT, description TEXT, 
                  debit REAL, credit REAL, voucher_type TEXT, reference TEXT)''')

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
    with sqlite3.connect('rst_manager_v2.db') as conn:
        if fetch: return pd.read_sql(query, conn, params=params)
        conn.execute(query, params); conn.commit()

def post_transaction(date, acc, desc, dr, cr, vtype, ref):
    run_query("INSERT INTO transactions (date, account_name, description, debit, credit, voucher_type, reference) VALUES (?,?,?,?,?,?,?)",
              (date, acc, desc, dr, cr, vtype, ref))
    if dr > 0: run_query("UPDATE accounts SET balance = balance + ? WHERE name = ?", (dr, acc))
    if cr > 0: run_query("UPDATE accounts SET balance = balance - ? WHERE name = ?", (cr, acc))

# --- PDF GENERATOR FUNCTION ---
def create_pdf(invoice_data):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, "SALE INVOICE - REHMAN SCRAP TRADER", ln=True, align='C')
    pdf.set_font("Arial", '', 12)
    pdf.cell(200, 10, f"Date: {invoice_data['date']}", ln=True)
    pdf.cell(200, 10, f"Customer: {invoice_data['customer']}", ln=True)
    pdf.line(10, 40, 200, 40)
    pdf.cell(100, 10, "Description", border=1)
    pdf.cell(40, 10, "Amount", border=1, ln=True)
    pdf.cell(100, 10, invoice_data['desc'], border=1)
    pdf.cell(40, 10, f"{invoice_data['amount']:,.2f}", border=1, ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- UI DESIGN (WAVE STYLE) ---
st.set_page_config(page_title="RST Manager", layout="wide")
st.markdown("""
    <style>
    :root { --wave-green: #00a85d; --wave-navy: #162d3d; --wave-bg: #f4f7f9; }
    .stApp { background-color: var(--wave-bg); }
    section[data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e3e9ed; }
    .stButton>button { background-color: var(--wave-green); color: white; border-radius: 4px; border:none; }
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
st.sidebar.title("Manager.io")
menu = ["Summary", "Bank and Cash", "Receipts", "Payments", "Inter Account Transfer", "Customers", "Sales Invoices", "Suppliers", "Purchase Invoices", "Inventory"]
choice = st.sidebar.radio("Navigate", menu)

# --- MODULES ---
if choice == "Summary":
    st.title("Summary")
    df = run_query("SELECT name, category, balance FROM accounts", fetch=True)
    c1, c2 = st.columns(2)
    c1.subheader("Assets")
    c1.table(df[df['category'] == 'Asset'])
    c2.subheader("Liabilities & Equity")
    c2.table(df[df['category'].isin(['Liability', 'Equity'])])

elif choice == "Bank and Cash":
    st.title("Bank and Cash Accounts")
    st.dataframe(run_query("SELECT name as Account, balance as Balance FROM accounts WHERE name IN ('Cash on Hand', 'Bank Account')", fetch=True), use_container_width=True, hide_index=True)

elif choice == "Receipts":
    st.title("Receipts")
    with st.expander("New Receipt"):
        with st.form("rec_f"):
            d = st.date_input("Date")
            acc = st.selectbox("Paid Into", ["Cash on Hand", "Bank Account"])
            party = st.text_input("Received From")
            amt = st.number_input("Amount", min_value=0.0)
            if st.form_submit_button("Save"):
                post_transaction(d, acc, f"Receipt: {party}", amt, 0, "Receipt", party)
                post_transaction(d, "Sales", f"Receipt: {party}", 0, amt, "Receipt", party)
                st.rerun()
    st.dataframe(run_query("SELECT * FROM transactions WHERE voucher_type='Receipt'", fetch=True), hide_index=True)

elif choice == "Payments":
    st.title("Payments")
    with st.expander("New Payment"):
        with st.form("pay_f"):
            d = st.date_input("Date")
            acc = st.selectbox("Paid From", ["Cash on Hand", "Bank Account"])
            party = st.text_input("Paid To")
            amt = st.number_input("Amount", min_value=0.0)
            head = st.selectbox("Account (Expense)", ["Operating Expenses", "Accounts Payable"])
            if st.form_submit_button("Save"):
                post_transaction(d, head, f"Payment: {party}", amt, 0, "Payment", party)
                post_transaction(d, acc, f"Payment: {party}", 0, amt, "Payment", party)
                st.rerun()
    st.dataframe(run_query("SELECT * FROM transactions WHERE voucher_type='Payment'", fetch=True), hide_index=True)

elif choice == "Sales Invoices":
    st.title("Sales Invoices")
    with st.expander("Create New Invoice"):
        with st.form("sale_f"):
            d = st.date_input("Date")
            cust = st.text_input("Customer Name")
            desc = st.text_input("Description")
            amt = st.number_input("Total Amount", min_value=0.0)
            if st.form_submit_button("Generate & Post"):
                post_transaction(d, "Accounts Receivable", f"Invoice: {desc}", amt, 0, "Sales Invoice", cust)
                post_transaction(d, "Sales", f"Invoice: {desc}", 0, amt, "Sales Invoice", cust)
                # Create PDF Download Link
                pdf_bytes = create_pdf({'date': d, 'customer': cust, 'desc': desc, 'amount': amt})
                st.download_button("Download PDF Invoice", data=pdf_bytes, file_name=f"Invoice_{cust}.pdf", mime="application/pdf")

elif choice == "Inventory":
    st.title("Inventory Items")
    with st.expander("Add New Item"):
        with st.form("inv_form"):
            n = st.text_input("Item Name")
            q = st.number_input("Opening Qty", min_value=0.0)
            if st.form_submit_button("Add Item"):
                run_query("INSERT INTO inventory (item_name, qty) VALUES (?,?)", (n, q))
                st.rerun()
    st.dataframe(run_query("SELECT item_name as Item, qty as Quantity FROM inventory", fetch=True), use_container_width=True, hide_index=True)