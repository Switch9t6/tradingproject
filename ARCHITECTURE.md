# Trading Bot — End-to-End Technical Analysis

> Generated: 2026-08-13. Verified against current source (option mapper post-refactor, commit `70ac36e`).

---

## 1. End-to-End Workflow

```
                     ┌────────────────────────────────────────────┐
                     │  main.py daemon loop (main.py:539-562)     │
                     │  ENABLE_AUTO_SCHEDULER=False → manual-only │
                     └────────────────────┬───────────────────────┘
          Manual /session triggers        │ auto_scheduler.py:157 daemon
                                          │ (only if flag True, auto_scheduler.py:137)
                                          ▼
                    ┌─────────────────────────────────────────────┐
                    │  run_daily_pipeline (NSE)   main.py:296     │
                    │  run_mcx_crude_pipeline (MCX) main.py:197   │
                    └───────────────┬─────────────────────────────┘
                                    ▼
  ┌─ GATE 1  Wallet pre-flight: verify_and_fetch_live_fyers_balance()
  │           fyers_trader.py:380 → halt via _halt_engine_and_alert_telegram
  │           if unverified (main.py:213-217 / 344-349). budget_cap =
  │           MICRO_CAPITAL_BUDGET_CAP(1000) if micro else live wallet
  │           (main.py:222, 345)
  ├─ GATE 2  News blackout + market hours: is_crude_news_blackout_window()
  │           (main.py:231, crude_news_engine.py:22); check_market_hours_and_calendar
  │           (main.py:167)
  ├─ GATE 3  Daily cap: is_trade_allowed_today(exchange=…)  state_manager.py:186
  │           (per-exchange; MAX_DAILY_TRADES=1, settings.py:37)
  ├─ GATE 4  OAuth token: run_oauth_flow (main.py:248/367, auth/oauth_server.py:23)
  ├─ GATE 5  SCANNER → synthetic candidate
  │           NSE: scan_nse_equities_and_indices (smart_scanner.py:58)
  │           MCX: scan_mcx_crude_oil_multifactor (smart_scanner.py:221)
  ├─ GATE 6  OPTION MAPPER → real Fyers contract
  │           NSE: resolve_atm_option_contract (main.py:415, option_mapper.py:825)
  │           MCX: get_mcx_crude_option_contract (main.py:266, option_mapper.py:755)
  ├─ GATE 7  Telegram kill-switch / approval: is_bot_disabled() (main.py:281);
  │           request_telegram_trade_approval (telegram_control.py:141)
  └─ GATE 8  EXECUTION + MONITOR
              FyersTrader.execute_option_trade (fyers_trader.py:639)
              place_aggressive_limit_order (fyers_trader.py:501)
              start_position_monitor (position_monitor.py:307) ← 5s LTP poll
                              │
              ┌───────────────┴──────────────────────────────────────┐
      EXIT PATHS                                                  FAILSAFES
      TARGET_HIT_+25%      position_monitor.py:49                  fill-poll timeout
      TSL_STEP1(+8%→BE)    position_monitor.py:50                  → PENDING (fyers_trader.py:498)
      TSL_STEP2(+15%→+10%) position_monitor.py:52                  → _close_uncovered_positions
      STOP_LOSS_HIT_-12%   position_monitor.py:48                  (fyers_trader.py:815) reconciles
      TIME_EXIT 20min      position_monitor.py:54                  broker positions() vs local state
      EOD square-off       15:15 NSE / 23:00 MCX (position_monitor.py:67)
      MANUAL_EXIT          detect_manual_exit_and_record (fyers_trader.py:1026)
      /stop kill switch    telegram_control.py:417 → BOT_DISABLED.flag
```

Persistence: every leg is written to `logs/trades.db` (SQLite) via `record_entry_trade`/`record_exit_trade` (state_manager.py:224/283); daily caps, wallet and segment locks live in `StateManager` JSON state (state_manager.py:65). EOD report via `generate_eod_report` (main.py:293).

---

## 2. NSE vs MCX Pipeline Comparison

| Dimension | NSE Engine A | MCX Engine B |
|---|---|---|
| Pipeline entry | `run_daily_pipeline` main.py:296 | `run_mcx_crude_pipeline` main.py:197 |
| Session window | 09:15–15:30 IST (settings.py:199) | 17:00–23:15 IST (settings.py:200) |
| Scanner | `scan_nse_equities_and_indices` smart_scanner.py:58 — 15-stock sector universe, 100-pt matrix (Vol 25 + RS 25 + VWAP/EMA 25 + Sentiment 25) | `scan_mcx_crude_oil_multifactor` smart_scanner.py:221 — 100-pt matrix (Tech 25 + WTI lead-lag 25 + ATR 15 + USD/INR 15 + Spread 20) |
| Score gate | 75 in scanner, `QUALIFICATION_SCORE_THRESHOLD=80` enforced in mapper | 75 in scanner (smart_scanner.py:294 checks ≥80) |
| Data source | **Synthetic** — `np.random` per symbol (smart_scanner.py:103-118) | **Synthetic** — `np.random` (smart_scanner.py:253-263) |
| Contract mapping | `resolve_atm_option_contract` option_mapper.py:825 → real `NSE_FO.csv` master, **lot from master parts[3]** (e.g. NIFTY 65, ICICIBANK 700) | `get_mcx_crude_option_contract` option_mapper.py:755 → `MCX_COM.csv`; **no option chain in master** → premium **estimated at 1.5% of spot** (option_mapper.py:640) |
| Strike step | heuristic: NIFTY/FINNIFTY 50, MIDCPNIFTY 25, BANKNIFTY 100, equity 2.5(<300)/5.0 (smart_scanner.py:138) | config `MCX_CRUDE_STRIKE_STEP=50` (settings.py:184) |
| Delta/OI gates | delta 0.25–0.70 + OI filter via `_qualifies` (option_mapper.py:230) | **Not enforceable** — no live chain; OI filter off (`ENABLE_OI_FILTER=False`) |
| Lot size | master parts[3] (overrides scanner heuristic) | config 100 std / 10 mini (`MCX_CRUDE_LOT_SIZE`/`MINI`, settings.py:182-183) |
| Micro-capital mode | `budget_cap` clamped to 1000 (main.py:345); **no lot downgrade** | clamp to 1000 (main.py:222) **+ std→mini 10-lot downgrade** in mapper |
| Margin model | premium × lot (master tick/lot) | total_lot_cost = ask × 100; re-check std vs mini vs budget |
| Order | BUY, INTRADAY, **limit at ask**, type=1, validity DAY, offlineOrder False (fyers_trader.py:557-568) | identical path |
| EOD cut-off | 15:15 IST (position_monitor.py:68) | 23:00 IST (position_monitor.py:69) |
| Exits | shared TSL ladder | shared TSL ladder |

**The core architectural split:** the *scanner layer* produces a synthetic "target" strike (Engine A) / spot+direction (Engine B); the *mapper layer* is the only component that talks to real broker data (`NSE_FO.csv`/`MCX_COM.csv` + live option chain via `_live_option_chain` option_mapper.py:155). For NSE the mapper re-resolves everything against the real master; for MCX it cannot, so it falls back to estimates.

---

## 3. Step-by-Step Walkthrough (option selection deep-dive)

**NSE chain** (option_mapper.py):
1. `_snap_to_strike(spot, step)` — half-up rounding to strike grid (line 67).
2. `_momentum_strike_offset(momentum_pct)` — 0 / 1 / 2 strikes OTM, capped `MAX_STRIKE_OFFSET=3` (line 78), with `MOMENTUM_MODERATE_PCT=1.0` / `STRONG=2.0` triggers.
3. `_dynamic_usable_budget(cap, token)` — `cash × 0.80 × 0.98` (max-allocation 80% minus 2% slippage buffer) (line 138).
4. `_live_option_chain` — quotes current strikes/`bid`/`ask`/`lp` from Fyers API (line 155).
5. `_pick_live_strike` — walks OTM from base strike, applies delta band `[0.25, 0.70]` + OI gate via `_qualifies` (lines 211, 230); picks the best live strike and its **real** premium.
6. `get_fyers_instrument_csv` — downloads `NSE_FO.csv` (~14.4 MB gzip) into memory-cache, stale-cache guard `_warn_stale_cache` (lines 258, 311); parses rows: `parts[3]=lot`, `parts[4]=tick`, `parts[8]/[18]=expiry epoch`, `parts[9]=fyers_symbol`, `parts[13]=underlying`, `parts[15]=strike`, `parts[16]=CE/PE`.
7. `_expiry_iso_from_parts` — probes index 8 then 18 (line 356); `_nearest_expiry` picks the ATM expiry for the strike (line 386).
8. `_lookup_with_deviation_guard` (line 572) — finds the real strike closest to target; `allowed_dev = MAX_STRIKE_DEVIATION_STEPS(=2) × max(real_spacing, strike_step)` where `real_spacing` is the gap between the two nearest master strikes. On equal deviation it prefers the **OTM side** (higher for CE, lower for PE). Failure → `last_mapping_error()` (line 59) set to `STRIKE_OUT_OF_BOUNDS_OR_MISSING`/`STALE_OR_MISSING_CACHE`/`REJECTED_GUARDRAILS`; on success `_clear_mapping_error()` (line 567).
9. `target_strike` is overwritten with the **actual resolved entry strike**; contract keys (`fyers_symbol`, `instrument_key`, `lot_size`, `ask_price`, `underlying_symbol`, `option_type`, `strike_price`) are preserved for the executor.
10. Budget re-check: `premium × lot > usable_budget` → `INSUFFICIENT_WALLET_BALANCE` → `NO_CONTRACT`.

**MCX** (option_mapper.py:640 `_resolve_mcx_underlying`, 755 `get_mcx_crude_option_contract`): same snap→offset→budget logic, but premium is **estimated at 1.5% of spot** with `simulated_spread_pct=0.008`; delta/OI checks are skipped; lot starts at 100 (std) and falls back to 10 (mini) if `ask×100 > budget`.

**Execution** (fyers_trader.py): `execute_option_trade` (639) → `place_aggressive_limit_order` (501, tick-rounded limit at ask) → `_poll_order_fill` (472, 2 s cadence, `LIMIT_ORDER_TIMEOUT_SECONDS`) → TRADED/REJECTED/PENDING. Live wallet verified pre-order (380); on broker-side failure (non-`ok`, segment disabled) → `handle_execution_issue_and_halt` (771) writes `BOT_DISABLED.flag`, locks both segments, requires Telegram `/resume`.

**Monitor** (position_monitor.py): 5 s LTP poll (line 64); rule ladder `evaluate_tick` (95) with `-12%` SL, `+25%` target, `+8%`→breakeven, `+15%`→lock `+10%`, 20 min `MAX_HOLD_SECONDS` stagnation exit; exits are tick-aligned marketable SELLs (`_execute_exit`, 246); EOD hand-off at cutoffs (67). Settlement: `record_exit_trade` (state_manager.py:283).

**State/control**: `/stop` (telegram_control.py:417), `/resume` (464), `/status` (570), `/squareoff` (870); `square_off_active_position` (fyers_trader.py:1106); `detect_manual_exit_and_record` (1026) settles externally-closed positions from the tradebook.

---

## 4. Risk Assessment — Hidden Vulnerabilities

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **Both engines run on synthetic data.** Engine A seeds `np.random` per symbol (smart_scanner.py:103) and Engine B seeds `np.random.seed(int(time.time())%10000)` then hardcodes direction/supertrend to BULLISH (smart_scanner.py:253-263). Scores are near-guaranteed ≥80 → the "signal" is effectively random noise gated by a coin flip, not the market. Real scanners (`crude_scanner.py:185`, `nse500_scanner.py:159`, `fast_scanner.py:49`) exist but are **unwired** (main.py:18 import is dead; only used in `scratch/backtest_engine_b_real_data.py`). | Critical | smart_scanner.py:103, 253-263, 294 |
| 2 | **MCX premium/delta are fabricated.** `MCX_COM.csv` has no option chain → premium = 1.5%-of-spot estimate, `simulated_spread_pct=0.008`, delta/OI gates silently skipped (`ENABLE_OI_FILTER=False`). A 1.5% estimate vs real ask can be off by multiples; the `+25%` target / `-12%` SL are computed off the *estimate*, not the fill — so TSL levels may be meaningless on day one. | High | option_mapper.py:640, 755; settings.py `ENABLE_OI_FILTER` |
| 3 | **Scanner and broker live in different data worlds.** Master strikes vs scanner `strike_interval` mismatch (e.g. scanner says 5.0 for equities, real spacing is 50 for MARUTI) is now handled by the real-spacing guard — but the observed master subset means most Engine A universe symbols have **no tradable options** (KOTAKBANK master tops out at strike 580 vs scanner spot ~1268) → `REJECTED_GUARDRAILS`, i.e. expected outcome is *no trades*, and the fallback path (smart_scanner.py:207) still returns a candidate without budget/chain validation. | High | option_mapper.py:572; smart_scanner.py:207 |
| 4 | **Silent capital fiction.** `verify_and_fetch_live_fyers_balance` returns `(True, INITIAL_WALLET_CAPITAL=10000, "VERIFIED_DEFAULT")` when the API works but reports 0 balance (fyers_trader.py:423) — the engine believes it has ₹10,000. Orders then fail at the broker, tripping the full halt (safe but confusing), and `get_live_wallet_balance` falls back to `state_manager.get_current_wallet_balance()` on any exception (fyers_trader.py:466-469). | Medium | fyers_trader.py:411-423, 466 |
| 5 | **Partial-fill gap.** `_poll_order_fill` returns TRADED only when `filled_qty >= qty`; anything less stays PENDING through timeout (fyers_trader.py:491-498). A partial fill isn't closed by the normal exit path; only the EOD `_close_uncovered_positions` reconciliation (fyers_trader.py:815) covers it — a position can sit unmonitored intraday. | Medium | fyers_trader.py:491, 815 |
| 6 | **Single position slot.** State tracks one `active_position`; the "covered" netting and `_close_uncovered_positions` assume no concurrent entries. With per-exchange caps (NSE+MCX = up to 2 trades/day) overlap at 15:15 is theoretically possible. | Low | state_manager.py:65; settings.py:37 |
| 7 | **SL exit is a limit, not a stop.** Exits SELL at `LTP - max(tick, LTP×0.5%)` (fyers_trader.py:891, position_monitor.py:246). On fast crashes the limit won't fill and the monitor just keeps polling; no market-order fallback (deliberate SEBI compliance, but it means realized loss can exceed −12%). | Medium | fyers_trader.py:553-568, 891 |
| 8 | **No price realism in the P&L fiction.** Since entry price = ask of a synthetic chain and exits use real LTP of a *different* instrument, realized P&L in `logs/trades.db` is only meaningful once the mapper resolves against a live chain (NSE only). | High | fyers_trader.py:592-600 |
| 9 | **Broad fallback acceptance.** After the budget-filtered loop fails, Engine A returns `qualified_candidates[0]` unconditionally (smart_scanner.py:207-212) — the mapper's `NO_CONTRACT`/budget result from pass 1 is ignored in pass 2; the main pipeline's `max_budget` check (main.py:417/429) is the only remaining guard. | Medium | smart_scanner.py:207 |

**Summary of what's actually safe:** SEBI-compliant limit-only orders, strict pre-order wallet verification with halt-on-failure, one-trade/day caps, Telegram kill switch, TSL ladder, EOD broker reconciliation for untracked fills, and the hardened deviation guard. **What's not safe yet:** both signal engines and the MCX pricing layer are simulated — until Engine A/B consume real candles (crude_scanner/fast_scanner paths) and MCX premiums come from a real chain, the bot is a rigorously-safe *simulator* of random entries.

---

*No code was changed for this analysis.*
