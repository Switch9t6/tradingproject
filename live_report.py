import sys
from reporting.live_reporter import generate_live_market_report

if __name__ == "__main__":
    print("Fetching Pure Live Upstox Account Balance & Real Market Trade History...")
    generate_live_market_report()
