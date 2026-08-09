import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import time
import datetime
import difflib
import requests
from typing import List, Dict, Any, Optional
import feedparser

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader_analyzer = SentimentIntensityAnalyzer()
    
    financial_lexicon = {
        "rally": 2.5, "surge": 2.5, "breakout": 3.0, "upgraded": 2.0,
        "profit": 2.0, "record high": 3.0, "outperform": 2.5, "bullish": 2.5,
        "plunge": -3.0, "crash": -3.5, "downgraded": -2.5, "penalty": -2.5,
        "default": -3.5, "fraud": -3.5, "loss": -2.0, "bearish": -2.5
    }
    vader_analyzer.lexicon.update(financial_lexicon)
except Exception:
    vader_analyzer = None

from scanner.news_filter import is_news_blackout_active

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# High-Quality Active Indian Market RSS Feeds
RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/2146842.cms",
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://www.moneycontrol.com/rss/marketreports.xml"
]

def fetch_finnhub_news(symbol: str = "NIFTY") -> List[Dict[str, Any]]:
    """
    Source A: Queries Finnhub API for company news & general market headlines.
    """
    headlines: List[Dict[str, Any]] = []
    if not FINNHUB_API_KEY:
        return headlines

    today_str = datetime.date.today().isoformat()
    yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    try:
        url_company = f"https://finnhub.io/api/v1/company-news?symbol={symbol}&from={yesterday_str}&to={today_str}&token={FINNHUB_API_KEY}"
        resp = requests.get(url_company, timeout=2.5)
        if resp.status_code == 200:
            for item in resp.json()[:15]:
                headlines.append({
                    "title": item.get("headline", ""),
                    "summary": item.get("summary", ""),
                    "source": "Finnhub-Company",
                    "timestamp": item.get("datetime", time.time())
                })
    except Exception as e:
        print(f"[Realtime News Warning] Finnhub ingestion exception: {e}")

    return headlines

def fetch_rss_market_news() -> List[Dict[str, Any]]:
    """
    Source B: Parses live RSS feeds (Economic Times & Moneycontrol) using feedparser.
    """
    headlines: List[Dict[str, Any]] = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:25]:
                title = getattr(entry, "title", "")
                summary = getattr(entry, "summary", getattr(entry, "description", ""))
                pub_parsed = getattr(entry, "published_parsed", None)
                ts = time.mktime(pub_parsed) if pub_parsed else time.time()
                if title:
                    headlines.append({
                        "title": title,
                        "summary": summary,
                        "source": getattr(feed.feed, "title", "RSS Feed"),
                        "timestamp": ts
                    })
        except Exception as e:
            print(f"[Realtime News Warning] RSS Feed error ({feed_url}): {e}")

    return headlines

def deduplicate_and_filter_recent_news(raw_items: List[Dict[str, Any]], max_age_minutes: int = 60) -> List[Dict[str, Any]]:
    """
    Deduplicates headlines by computing string similarity on titles
    and filters out items older than `max_age_minutes`.
    """
    clean_items: List[Dict[str, Any]] = []
    now_ts = time.time()
    max_age_sec = max_age_minutes * 60

    for item in raw_items:
        title = item.get("title", "").strip()
        ts = item.get("timestamp", now_ts)
        if not title:
            continue

        # Outside trading hours / weekends: relax window if needed
        age_sec = now_ts - ts
        if age_sec > max_age_sec and age_sec > 86400:
            continue

        is_duplicate = any(difflib.SequenceMatcher(None, title.lower(), existing["title"].lower()).ratio() > 0.75 for existing in clean_items)
        if not is_duplicate:
            clean_items.append(item)

    return clean_items

def compute_composite_sentiment_score(symbol: str, news_items: List[Dict[str, Any]]) -> float:
    """
    Computes a composite normalized sentiment score between -1.0 (Extreme Bearish) and +1.0 (Extreme Bullish).
    """
    if not news_items:
        return 0.0

    symbol_clean = re.sub(r'[^A-ZA-Z0-9]', '', symbol.upper())
    relevant_scores: List[float] = []

    for item in news_items:
        text = f"{item.get('title', '')} {item.get('summary', '')}".upper()
        # Relevant if mentions symbol or general Indian market terms
        if (symbol_clean in text) or ("NIFTY" in text) or ("MARKET" in text) or ("BANK" in text) or ("STOCK" in text):
            score = vader_analyzer.polarity_scores(text).get("compound", 0.0) if vader_analyzer else 0.0
            relevant_scores.append(score)

    return float(round(sum(relevant_scores) / float(len(relevant_scores)), 2)) if relevant_scores else 0.0

def evaluate_symbol_realtime_news(symbol: str) -> Dict[str, Any]:
    """
    Main Entry Interface Function:
    Aggregates multi-source news, runs deduplication, computes sentiment scores,
    checks macro news blackouts, and returns actionable trade permissions.
    """
    default_response = {
        "symbol": symbol,
        "allow_bullish": True,
        "allow_bearish": True,
        "sentiment_score": 0.0,
        "news_halt_active": False,
        "reason": "API Downtime / Normal Market Operations (Trade Allowed)",
        "headline_count": 0
    }

    try:
        macro_blackout = is_news_blackout_active(buffer_minutes=30)
        if macro_blackout:
            return {
                "symbol": symbol,
                "allow_bullish": False,
                "allow_bearish": False,
                "sentiment_score": -1.0,
                "news_halt_active": True,
                "reason": "⚠️ [NEWS GUARDRAIL] Scheduled high-impact macro economic event blackout active.",
                "headline_count": 0
            }

        finnhub_items = fetch_finnhub_news(symbol=symbol)
        rss_items = fetch_rss_market_news()
        all_raw = finnhub_items + rss_items
        
        filtered_news = deduplicate_and_filter_recent_news(all_raw, max_age_minutes=1440)
        sentiment_score = compute_composite_sentiment_score(symbol, filtered_news)

        allow_bullish = True
        allow_bearish = True
        news_halt_active = False
        reason = "Normal Realtime News Context"

        if sentiment_score < -0.60:
            allow_bullish = False
            news_halt_active = True
            reason = f"⛔ [EXTREME BEARISH NEWS] Sentiment Score ({sentiment_score:.2f} < -0.60). Call (CE) buys blocked."
        elif sentiment_score > 0.60:
            allow_bearish = False
            reason = f"🟢 [EXTREME BULLISH NEWS] Sentiment Score ({sentiment_score:.2f} > +0.60). Put (PE) buys blocked."

        return {
            "symbol": symbol,
            "allow_bullish": allow_bullish,
            "allow_bearish": allow_bearish,
            "sentiment_score": sentiment_score,
            "news_halt_active": news_halt_active,
            "reason": reason,
            "headline_count": len(filtered_news),
            "sample_headlines": [item["title"] for item in filtered_news[:3]]
        }

    except Exception as e:
        print(f"[Realtime News Aggregator Fail-Safe] Error in news evaluation ({e}). Defaulting to Trade Allowed.")
        return default_response

if __name__ == "__main__":
    print("=" * 80)
    print("      REALTIME MULTI-SOURCE NEWS AGGREGATOR & SENTIMENT ENGINE       ")
    print("=" * 80)
    
    test_symbols = ["RELIANCE", "NIFTY", "SBIN", "HDFCBANK"]
    for sym in test_symbols:
        res = evaluate_symbol_realtime_news(sym)
        print(f"\n[Symbol Test: {sym}]")
        print(f"  Allow Bullish   : {res['allow_bullish']}")
        print(f"  Allow Bearish   : {res['allow_bearish']}")
        print(f"  Sentiment Score : {res['sentiment_score']}")
        print(f"  News Halt Active: {res['news_halt_active']}")
        print(f"  Reason          : {res['reason']}")
        print(f"  Headlines Count : {res['headline_count']}")
        print(f"  Sample Headlines:")
        for h in res.get('sample_headlines', []):
            print(f"    - {h}")
