import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import datetime
from typing import Dict, List, Any, Optional

from scanner.realtime_news_aggregator import fetch_rss_market_news, fetch_finnhub_news, vader_analyzer
from learning.adaptive_config import adaptive_score_threshold

# Quantitative Sector Asset Mapping
SECTOR_MAP = {
    "BANKING": ["BANKBARODA", "SBIN", "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "BANKNIFTY"],
    "IT": ["INFY", "TCS", "WIPRO", "HCLTECH", "TECHM", "LTIM"],
    "AUTO": ["HEROMOTOCO", "TATAMOTORS", "MARUTI", "M&M", "BAJAJ-AUTO"],
    "PHARMA": ["DIVISLAB", "SUNPHARMA", "DRREDDY", "CIPLA"],
    "METALS": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "ENERGY": ["RELIANCE", "NTPC", "POWERGRID", "ONGC"]
}

class MacroSectorNewsEngine:
    """
    Step 2 [09:05 AM]: Macro & Sector News Scorer
    Aggregates financial news feeds, computes Sector Sentiment Index (-1.00 to +1.00),
    filters out choppy/stagnant sectors, and selects the TOP 3 SECTORS for intraday focus.
    """
    def __init__(self):
        self.sector_map = SECTOR_MAP

    def calculate_sector_sentiment_index(self) -> Dict[str, Any]:
        print("\n[09:05 AM] STEP 2: MACRO & SECTOR NEWS SCORER (MacroSectorNewsEngine)")
        print("  |-- Ingesting Multi-Source Feeds (Finnhub API + RSS Markets)...")
        
        rss_news = fetch_rss_market_news()
        finnhub_news = fetch_finnhub_news("NIFTY")
        combined_news = rss_news + finnhub_news

        sector_scores: Dict[str, float] = {}
        bullish_sectors: List[str] = []
        bearish_sectors: List[str] = []
        stagnant_sectors: List[str] = []

        for sector, symbols in self.sector_map.items():
            scores: List[float] = []
            for item in combined_news:
                text = f"{item.get('title', '')} {item.get('summary', '')}".upper()
                
                # Check if text mentions sector name or any symbol in sector
                mentions_sector = (sector in text) or any(s in text for s in symbols)
                if mentions_sector:
                    score = vader_analyzer.polarity_scores(text).get("compound", 0.0) if vader_analyzer else 0.0
                    scores.append(score)

            sector_index = round(sum(scores) / float(len(scores)), 2) if scores else 0.0
            sector_scores[sector] = sector_index

            if sector_index >= 0.30:
                bullish_sectors.append(sector)
            elif sector_index <= -0.30:
                bearish_sectors.append(sector)
            else:
                stagnant_sectors.append(sector)

        # Rank all sectors by absolute sentiment strength
        ranked_sectors = sorted(sector_scores.items(), key=lambda x: abs(x[1]), reverse=True)
        top_3_sectors = [s[0] for s in ranked_sectors[:3]]

        print(f"  |-- Sector Sentiment Index:")
        for sec, score in sector_scores.items():
            tag = "BULLISH (+0.30 to +1.00)" if score >= 0.30 else ("BEARISH (-0.30 to -1.00)" if score <= -0.30 else "STAGNANT (Filtered)")
            print(f"  |   |-- {sec:10s} : {score:+.2f} [{tag}]")

        print(f"  |-- Bullish Focus List (+0.30 to +1.00) : {bullish_sectors or 'None'}")
        print(f"  |-- Bearish Focus List (-0.30 to -1.00) : {bearish_sectors or 'None'}")
        print(f"  |-- Stagnant/Choppy (-0.29 to +0.29)  : {stagnant_sectors} (Filtered Out)")
        print(f"  \\-- SELECTED TOP 3 SECTORS FOR DAY   : {top_3_sectors}\n")

        return {
            "sector_scores": sector_scores,
            "bullish_sectors": bullish_sectors,
            "bearish_sectors": bearish_sectors,
            "stagnant_sectors": stagnant_sectors,
            "top_3_sectors": top_3_sectors
        }

def calculate_composite_opportunity_rating(
    symbol: str,
    price_change_pct: float,
    vol_spike: float,
    is_vwap_ema_aligned: bool,
    sector_sentiment_score: float,
    ticker_headline_score: float
) -> Dict[str, Any]:
    """
    Step 3 [09:30 AM]: Factor Matrix Scoring Engine
    Calculates Composite Opportunity Rating (0 to 100 Points):
    - Technical Score (Max 50 pts): Price Momentum (15) + Volume Spike (20) + VWAP/EMA Alignment (15)
    - News Score (Max 50 pts): Sector Sentiment (25) + Ticker Headline Score (25)
    - Execution Threshold: Requires Composite Score >= 75 / 100
    """
    # 1. Technical Sub-Scores (Max 50 Pts)
    momentum_score = min(15.0, round((abs(price_change_pct) / 0.02) * 15.0, 2))
    volume_score = min(20.0, round((max(0.0, vol_spike - 1.0) / 3.0) * 20.0, 2))
    vwap_ema_score = 15.0 if is_vwap_ema_aligned else 0.0
    tech_total_score = round(momentum_score + volume_score + vwap_ema_score, 2)

    # 2. News Sub-Scores (Max 50 Pts)
    normalized_sec = max(-1.0, min(1.0, sector_sentiment_score))
    sector_news_score = round(((normalized_sec + 1.0) / 2.0) * 25.0, 2)

    normalized_headline = max(-1.0, min(1.0, ticker_headline_score))
    ticker_news_score = round(((normalized_headline + 1.0) / 2.0) * 25.0, 2)
    news_total_score = round(sector_news_score + ticker_news_score, 2)

    # 3. Total Composite Score (Max 100 Pts) - qualified vs anti-overfit adaptive threshold
    composite_score = round(tech_total_score + news_total_score, 2)
    is_qualified = composite_score >= adaptive_score_threshold()

    return {
        "symbol": symbol,
        "composite_score": composite_score,
        "is_qualified": is_qualified,
        "tech_score": tech_total_score,
        "news_score": news_total_score,
        "breakdown": {
            "price_momentum_pts": momentum_score,
            "volume_spike_pts": volume_score,
            "vwap_ema_alignment_pts": vwap_ema_score,
            "sector_sentiment_pts": sector_news_score,
            "ticker_headline_pts": ticker_news_score
        }
    }

if __name__ == "__main__":
    print("=" * 80)
    print("      QUANTITATIVE MACRO SECTOR ENGINE & FACTOR MATRIX TEST SUITE       ")
    print("=" * 80)
    
    engine = MacroSectorNewsEngine()
    sector_res = engine.calculate_sector_sentiment_index()
    
    # Test Factor Scoring Matrix
    rating = calculate_composite_opportunity_rating(
        symbol="SBIN",
        price_change_pct=0.024,
        vol_spike=4.2,
        is_vwap_ema_aligned=True,
        sector_sentiment_score=0.45,
        ticker_headline_score=0.30
    )
    print(f"\n[Factor Matrix Test: SBIN]")
    print(f"  Composite Score  : {rating['composite_score']} / 100 Pts")
    print(f"  Qualified (>=75) : {rating['is_qualified']}")
    print(f"  Tech Sub-Score   : {rating['tech_score']} / 50 Pts")
    print(f"  News Sub-Score   : {rating['news_score']} / 50 Pts")
    print(f"  Breakdown        : {rating['breakdown']}")
