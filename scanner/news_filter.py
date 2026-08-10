import os
import datetime
import requests
from typing import List, Dict, Any, Optional

# High-Impact Macro Economic Event Keywords for Indian Intraday Options
HIGH_IMPACT_KEYWORDS = [
    "RBI", "INTEREST RATE", "REPO RATE", "MPC", "MONETARY POLICY",
    "INFLATION", "CPI", "GDP", "UNION BUDGET", "FED RATE", "FOMC", "IIP"
]

# Configurable Default News Blackout Window Buffer
DEFAULT_BLACKOUT_BUFFER_MINUTES = 30

def fetch_today_economic_events() -> List[Dict[str, Any]]:
    """
    Fetches daily economic calendar events for India and major global market drivers.
    Includes an online API fetch with fallback parsing.
    Returns a list of event dictionaries:
    [{"title": "RBI Interest Rate Decision", "impact": "HIGH", "event_time": datetime.time(10, 0), "country": "IN"}]
    """
    today_str = datetime.date.today().isoformat()
    events: List[Dict[str, Any]] = []

    try:
        # Fetching macro calendar events via public API endpoint (2.0s timeout limit)
        url = f"https://nifty-api.com/v1/economic-calendar?date={today_str}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AntigravityQuant/2.0"}
        
        response = requests.get(url, headers=headers, timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            raw_events = data.get("events", [])
            for item in raw_events:
                title = str(item.get("title", "")).upper()
                is_high = any(kw in title for kw in HIGH_IMPACT_KEYWORDS) or item.get("impact", "").upper() == "HIGH"
                if is_high:
                    t_str = item.get("time", "10:00")
                    hour, minute = map(int, t_str.split(":")[:2])
                    events.append({
                        "title": item.get("title", "Macro Event"),
                        "impact": "HIGH",
                        "event_time": datetime.time(hour, minute),
                        "country": item.get("country", "IN")
                    })
            return events
    except Exception as e:
        # Soft warning logged for API downtime fallback
        print(f"[News Filter Warning] External economic calendar API unavailable ({e}). Using static schedule audit.")

    # Local Macro Calendar Schedule (Static High-Impact Key Dates / MPC Windows)
    # Known major recurring announcements: e.g. RBI MPC Announcement ~ 10:00 AM IST
    now_date = datetime.date.today()
    
    # Example scheduled RBI MPC & CPI release dates (YYYY, MM, DD)
    rbi_mpc_dates = [
        datetime.date(2026, 2, 6),
        datetime.date(2026, 4, 9),
        datetime.date(2026, 6, 5),
        datetime.date(2026, 8, 7),
        datetime.date(2026, 10, 9),
        datetime.date(2026, 12, 4)
    ]
    
    if now_date in rbi_mpc_dates:
        events.append({
            "title": "RBI Monetary Policy Committee (MPC) Interest Rate Decision",
            "impact": "HIGH",
            "event_time": datetime.time(10, 0),
            "country": "IN"
        })

    return events

def is_news_blackout_active(buffer_minutes: int = DEFAULT_BLACKOUT_BUFFER_MINUTES, now_dt: Optional[datetime.datetime] = None) -> bool:
    """
    Event Proximity Safeguard Rule:
    Determines if a high-impact economic event is scheduled within `buffer_minutes`
    BEFORE or AFTER the current execution time.
    
    Returns:
        True: Blackout is ACTIVE (High-impact news event is imminent or recent).
        False: Safe to trade (No high-impact news in window).
    """
    if now_dt is None:
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_dt = datetime.datetime.now(ist_tz)
        
    current_time = now_dt.time()
    today_events = fetch_today_economic_events()
    
    if not today_events:
        return False

    for event in today_events:
        if event.get("impact") == "HIGH":
            event_t = event.get("event_time")
            if not isinstance(event_t, datetime.time):
                continue
                
            event_datetime = datetime.datetime.combine(now_dt.date(), event_t)
            
            window_start = event_datetime - datetime.timedelta(minutes=buffer_minutes)
            window_end = event_datetime + datetime.timedelta(minutes=buffer_minutes)
            
            if window_start <= now_dt <= window_end:
                print(f"\n[NEWS BLACKOUT DETECTED] High-Impact Event: '{event['title']}' at {event_t.strftime('%H:%M')} IST.")
                print(f"[NEWS BLACKOUT DETECTED] Current Time ({now_dt.strftime('%H:%M:%S')}) falls inside +- {buffer_minutes}m blackout window [{window_start.strftime('%H:%M')} - {window_end.strftime('%H:%M')}].")
                return True
                
    return False

def can_trade_during_news_window(buffer_minutes: int = DEFAULT_BLACKOUT_BUFFER_MINUTES) -> bool:
    """
    System Integration Gateway Function:
    Exported function to be called in main.py during market scan workflows.
    
    Returns:
        True: Trade Execution Approved (No news blackout).
        False: Trade Rejected (News blackout active).
    """
    try:
        blackout_active = is_news_blackout_active(buffer_minutes=buffer_minutes)
        if blackout_active:
            print("[NEWS GUARDRAIL] Trade rejected due to scheduled high-impact economic event.")
            return False
        return True
    except Exception as e:
        # Offline Fallback Guarantee: Core trading logic is NEVER blocked by news API downtime.
        print(f"[News Filter Fallback] Error evaluating news filter ({e}). Defaulting to Trade Approved.")
        return True

if __name__ == "__main__":
    print("=" * 75)
    print("      QUANTITATIVE NEWS FILTER & ECONOMIC CALENDAR TEST SUITE       ")
    print("=" * 75)
    
    events = fetch_today_economic_events()
    print(f"[Test] Today's High-Impact Macro Events Count: {len(events)}")
    for ev in events:
        print(f"  - [{ev['impact']}] {ev['title']} ({ev['country']}) at {ev['event_time'].strftime('%H:%M')} IST")
        
    trade_allowed = can_trade_during_news_window(buffer_minutes=30)
    print(f"\n[Test Result] Can Trade Now: {trade_allowed}")
