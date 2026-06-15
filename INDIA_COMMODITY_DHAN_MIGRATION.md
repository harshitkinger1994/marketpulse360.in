# India + Commodity Dhan Migration

## Goal
Move India and commodity scanner/data paths off Yahoo Finance and onto Dhan-backed data sources only.

## In Scope
- India scanners that still fetch Yahoo data directly.
- Commodity scanners that still fetch Yahoo data directly or use Yahoo as fallback.
- Validation that the same signal and UI outputs still render after the fetch layer changes.

## Keep
- `backend/pattern_oi_vwap_ema_scanner.py`
- `strategies/apollo_ema9_strategy.py`
- `strategies/quant_trend_breakout_strategy.py`

## Migrate
- `strategies/gold_breakout_retest_scan.py`
- `strategies/intraday_momentum_scan.py`
- `strategies/reliance_open_close.py`

## Retire
- Any Yahoo fallback used for India or commodity flows.

## Migration Plan
1. Replace Yahoo-based India/commodity fetchers with Dhan-backed frame loaders.
2. Keep the existing indicator and signal logic unchanged.
3. Preserve local cache only as a Dhan cache, not as a Yahoo fallback.
4. Re-run scanner outputs against known today / last-working-day snapshots.
5. Smoke test the live service end to end and confirm Telegram/UI outputs still arrive.

## Test Cases
1. India 15m scan loads a Dhan-backed frame for at least one F&O symbol.
2. Commodity scan loads a Dhan-backed frame for GOLD / SILVER / CRUDEOIL with no Yahoo call.
3. Reliance open/close scan uses Dhan-backed daily history and still computes the same indicators.
4. Scanner output still produces a signal JSON/alert payload when the data qualifies.
5. Missing or stale Dhan data fails cleanly without falling back to Yahoo.

## Validation Checklist
- No `yfinance` or Yahoo URL is used in India or commodity runtime paths.
- Scanner output still has valid OHLCV columns and timestamps.
- Dhan token/auth failures are surfaced clearly.
- UI data and scanner signals stay in sync with the same storage snapshot.
- Telegram dedupe still prevents repeated alerts for the same artifact.

## Rollout Order
1. Commodity scanner.
2. India open/close scanner.
3. India intraday momentum scanner.
4. Re-run smoke tests.
5. Deploy only after no Yahoo fallback remains in the targeted paths.

