import time
import datetime
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

# Target Universe: 500 Mock Stock Symbols
MOCK_500_UNIVERSE = [
    {"symbol": f"NSE_STOCK_{i+1:03d}", "instrument_key": f"NSE_EQ|INE{i+1:05d}"}
    for i in range(500)
]

def simulate_stock_candle_fetch(stock: Dict[str, Any], session: requests.Session) -> Dict[str, Any]:
    """
    Simulates fetching 5-minute OHLCV candles and computing volume spike & momentum.
    Includes a 15ms simulated HTTP I/O latency.
    """
    time.sleep(0.015) # 15ms simulated I/O network latency
    return {
        "symbol": stock["symbol"],
        "volume_spike": 3.4 if stock["symbol"] == "NSE_STOCK_042" else 1.1,
        "momentum_pct": 0.018 if stock["symbol"] == "NSE_STOCK_042" else 0.003
    }

def run_scanner_benchmark(workers: int) -> float:
    """
    Execute scanner benchmark for a specific thread pool worker size.
    """
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=workers, pool_maxsize=workers)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    start_time = time.time()
    matches = []
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(simulate_stock_candle_fetch, stock, session) for stock in MOCK_500_UNIVERSE]
        for future in as_completed(futures):
            res = future.result()
            if res["volume_spike"] >= 3.0 and abs(res["momentum_pct"]) >= 0.012:
                matches.append(res)
                
    elapsed = time.time() - start_time
    return round(elapsed, 3)

def main():
    print("=" * 75)
    print("      PARALLEL SCANNER PERFORMANCE BENCHMARK SUITE (500 STOCKS)       ")
    print("      Timestamp: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 75)
    
    thread_counts = [10, 25, 50, 75, 100]
    results = []
    
    for workers in thread_counts:
        print(f"Executing benchmark with {workers:3d} worker threads...", end="", flush=True)
        exec_time = run_scanner_benchmark(workers)
        status = "PASSED (< 5.0s)" if exec_time < 5.0 else "SLOW (> 5.0s)"
        print(f" Done in {exec_time:6.3f}s | Status: {status}")
        results.append({
            "Worker Threads": workers,
            "Total Stocks Scanned": 500,
            "Execution Time (sec)": exec_time,
            "Throughput (stocks/sec)": round(500 / exec_time, 1),
            "Benchmark Status": status
        })
        
    print("\n" + "=" * 75)
    print("                        BENCHMARK RESULTS TABLE                        ")
    print("=" * 75)
    df_res = pd.DataFrame(results)
    print(df_res.to_string(index=False))
    print("=" * 75)
    
    fastest = df_res.loc[df_res["Execution Time (sec)"].idxmin()]
    print(f"\nOptimal Thread Count : {fastest['Worker Threads']} Threads")
    print(f"Fastest Execution    : {fastest['Execution Time (sec)']} seconds (Throughput: {fastest['Throughput (stocks/sec)']} stocks/sec)")
    print("=" * 75)

if __name__ == "__main__":
    main()
