import hashlib
import hmac
import os
import sqlite3
from datetime import date, datetime

import pandas as pd
import streamlit as st


APP_NAME = "Modoo"
DB_FILE = "modoo.db"
ROLES = ["Admin", "Accountant", "Sales", "Purchase", "Inventory", "Viewer"]

MODULES = [
    ("Dashboard", "Business at a glance"),
    ("Contacts", "Customers, vendors, and people"),
    ("Sales", "Customer invoices"),
    ("Purchase", "Vendor bills"),
    ("Inventory", "Products and stock moves"),
    ("Accounting", "Cash, bank, and journals"),
    ("Reporting", "Financial and operational reports"),
    ("Settings", "Company, users, and documents"),
    ("Audit Log", "Who did what, and when"),
]

ACCESS = {
    "Admin": {name for name, _ in MODULES},
    "Accountant": {
        "Dashboard",
        "Contacts",
        "Sales",
        "Purchase",
        "Inventory",
        "Accounting",
        "Reporting",
        "Audit Log",
    },
    "Sales": {"Dashboard", "Contacts", "Sales", "Inventory", "Reporting"},
    "Purchase": {"Dashboard", "Contacts", "Purchase", "Inventory", "Reporting"},
    "Inventory": {"Dashboard", "Contacts", "Inventory", "Reporting"},
    "Viewer": {"Dashboard", "Reporting"},
}


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored_hash):
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, expected = stored_hash.split("$", 1)
    actual = hash_password(password, salt).split("$", 1)[1]
    return hmac.compare_digest(actual, expected)


def fetch_df(query, params=()):
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)


def fetch_one(query, params=()):
    with get_conn() as conn:
        return conn.execute(query, params).fetchone()


def money(value):
    currency = get_setting("currency", "Rs.")
    return f"{currency} {float(value or 0):,.2f}"


def today_iso():
    return date.today().isoformat()


def current_user():
    return st.session_state.get("user")


def log_action(conn, user, action, entity, entity_id=None, details=""):
    user = user or {}
    conn.execute(
        """
        INSERT INTO audit_log (at, user_id, user_name, action, entity, entity_id, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            user.get("id"),