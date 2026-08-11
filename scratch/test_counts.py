import os
import math
import numpy as np
import pandas as pd
import yfinance as yf

df_raw = yf.download("CL=F", period="2y", interval="1d", progress=False)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df_raw = df_raw.dropna()

usd_inr = 83.50
df = pd.DataFrame()
df['Open'] = df_raw['Open'] * usd_inr
df['High'] = df_raw['High'] * usd_inr
df['Low'] = df_raw['Low'] * usd_inr
df['Close'] = df_raw['Close'] * usd_inr
df['Volume'] = df_raw['Volume']

df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
typical_price = (df['High'] + df['Low'] + df['Close']) / 3.0
df['VWAP'] = (typical_price * df['Volume']).rolling(window=14, min_periods=1).sum() / df['Volume'].rolling(window=14, min_periods=1).sum()

high_low = df['High'] - df['Low']
high_close = (df['High'] - df['Close'].shift(1)).abs()
low_close = (df['Low'] - df['Close'].shift(1)).abs()
tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
df['ATR_14'] = tr.rolling(window=14, min_periods=1).mean()

multiplier = 3.0
period = 7
atr_st = tr.rolling(window=period, min_periods=1).mean()
hl2 = (df['High'] + df['Low']) / 2.0
upper_band = hl2 + (multiplier * atr_st)
lower_band = hl2 - (multiplier * atr_st)

supertrend = [True] * len(df)
for i in range(1, len(df)):
    if df['Close'].iloc[i] > upper_band.iloc[i-1]:
        supertrend[i] = True
    elif df['Close'].iloc[i] < lower_band.iloc[i-1]:
        supertrend[i] = False
    else:
        supertrend[i] = supertrend[i-1]

df['Supertrend_Green'] = supertrend

bull_count = 0
bear_count = 0

for i in range(20, len(df)):
    spot = df['Close'].iloc[i]
    open_p = df['Open'].iloc[i]
    vwap = df['VWAP'].iloc[i]
    ema = df['EMA_20'].iloc[i]
    atr = df['ATR_14'].iloc[i]
    st_green = df['Supertrend_Green'].iloc[i]
    wti_ret = (spot - open_p) / open_p if open_p > 0 else 0.0
    
    bull_score = 0.0
    if st_green: bull_score += 25.0
    if spot > vwap: bull_score += 25.0
    if spot > ema: bull_score += 20.0
    if atr >= 15.0: bull_score += 15.0
    if wti_ret >= 0.001: bull_score += 15.0
    
    bear_score = 0.0
    if not st_green: bear_score += 25.0
    if spot < vwap: bear_score += 25.0
    if spot < ema: bear_score += 20.0
    if atr >= 15.0: bear_score += 15.0
    if wti_ret <= -0.001: bear_score += 15.0
    
    if bull_score >= 80.0:
        bull_count += 1
    elif bear_score >= 80.0:
        bear_count += 1

print(f"Total Sessions: {len(df)-20}")
print(f"Bullish Signal Sessions (Score >= 80): {bull_count}")
print(f"Bearish Signal Sessions (Score >= 80): {bear_count}")
print(f"Total Qualified Signal Sessions: {bull_count + bear_count}")
