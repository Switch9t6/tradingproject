import sqlite3
import json
import os

print("=== CHECKING TODAY'S TRADES & EXECUTIONS ===")

if os.path.exists("trades.db"):
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()
    tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print("Tables in trades.db:", tables)
    
    for t in tables:
        tbl_name = t[0]
        rows = c.execute(f"SELECT * FROM {tbl_name}").fetchall()
        print(f"\nTable '{tbl_name}' contains {len(rows)} records:")
        for r in rows:
            print(" ", r)
    conn.close()
else:
    print("No trades.db file found.")

print("\n=== CURRENT STATE.JSON ===")
if os.path.exists("state.json"):
    with open("state.json", "r") as f:
        print(f.read())

print("\n=== RECENT REPORTS IN REPORTS/ ===")
if os.path.exists("reports"):
    for f in os.listdir("reports"):
        print(" -", f)
