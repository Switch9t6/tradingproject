import requests

url = "https://images.dhan.co/api-data/api-scrip-master.csv"
r = requests.get(url, timeout=15)
lines = r.text.splitlines()

instr_map = {}
for l in lines[1:]:
    parts = [p.strip('"') for p in l.split(',')]
    if len(parts) >= 11 and parts[0] == "NSE" and parts[10] in ["CE", "PE"]:
        try:
            sec_id_val = parts[2]
            tsym = parts[5]
            lot = int(float(parts[6])) if parts[6] else 25
            stk = float(parts[9]) if parts[9] else 0.0
            otype = parts[10].upper()
            und = tsym.split("-")[0].upper()
            key = f"{und}_{int(stk)}_{otype}"
            if key not in instr_map:
                instr_map[key] = {
                    "security_id": sec_id_val,
                    "instrument_key": f"NSE_FO|{sec_id_val}",
                    "tradingsymbol": tsym,
                    "lot_size": lot,
                    "strike": stk
                }
        except Exception:
            pass

def find_nearest_option(symbol: str, target_strike: float, otype: str) -> dict:
    lookup_key = f"{symbol}_{int(target_strike)}_{otype}"
    if lookup_key in instr_map:
        return instr_map[lookup_key]
    
    # Nearest strike fallback
    prefix = f"{symbol}_"
    candidates = []
    for k, v in instr_map.items():
        if k.startswith(prefix) and k.endswith(f"_{otype}"):
            candidates.append((abs(v["strike"] - target_strike), v))
    
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    return {}

print("Testing NIFTY 23500 CE lookup:")
print(find_nearest_option("NIFTY", 23500, "CE"))

print("\nTesting MIDCPNIFTY 12200 CE lookup:")
print(find_nearest_option("MIDCPNIFTY", 12200, "CE"))
