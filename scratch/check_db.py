import sqlite3, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DB_FILE_PATH, STATE_FILE_PATH
import json

print("=== DB PATH ===")
print("DB path:", DB_FILE_PATH)
print("Exists:", os.path.exists(DB_FILE_PATH))

if os.path.exists(DB_FILE_PATH):
    conn = sqlite3.connect(DB_FILE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print("Tables:", c.fetchall())
    try:
        c.execute("SELECT * FROM trades ORDER BY id DESC")
        rows = c.fetchall()
        print(f"\nAll trades ({len(rows)} total):")
        for r in rows:
            d = dict(r)
            print(f"  ID={d.get('id')} date={d.get('trade_date')} mode={d.get('execution_mode')} exch={d.get('exchange')} sym={d.get('option_symbol')} entry={d.get('entry_premium')} status={d.get('status')} pnl={d.get('net_pnl')}")
    except Exception as e:
        print("Error reading trades:", e)
    conn.close()
else:
    print("DB does NOT exist at above path!")
    # Find any db files
    for root, dirs, files in os.walk('.'):
        for f in files:
            if f.endswith('.db'):
                print("Found db at:", os.path.join(root, f))

print("\n=== STATE.JSON ===")
if os.path.exists(STATE_FILE_PATH):
    with open(STATE_FILE_PATH) as f:
        state = json.load(f)
    print(json.dumps(state, indent=2))
else:
    print("state.json not found at", STATE_FILE_PATH)
