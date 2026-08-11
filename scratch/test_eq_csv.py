import requests

url = "https://images.dhan.co/api-data/api-scrip-master.csv"
r = requests.get(url, timeout=15)
lines = r.text.splitlines()

for l in lines[1:]:
    if "WIPRO" in l:
        parts = [p.strip('"') for p in l.split(',')]
        print(f"Exch={parts[0]}, Seg={parts[1]}, SecID={parts[2]}, Inst={parts[3]}, Symbol={parts[5]}, Custom={parts[7]}, Name={parts[15] if len(parts)>15 else ''}")
