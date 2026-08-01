# Strategy Catalog

This project now exposes the same catalog in two places:
- The frontend `Catalog` tab.
- The backend endpoint `GET /api/strategies/catalog`.

Every directional strategy emits the same standardized columns: `signal`, `forecast`, `position`, `cost_estimate`, `unit_return`, and `gross_return`. That keeps the portfolio manager, paper broker, and dashboards reusable across strategy families.

Important: these are research strategies, not profit guarantees. Promote a strategy only after walk-forward testing, purged validation, DSR/PBO review, realistic cost assumptions, and shadow paper trading.

## Basic Strategies

### `buy_and_hold`

Long-only passive benchmark. Use it to answer: did the active model beat simply owning the asset?

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline buy_and_hold --symbols SPY QQQ TLT
```

### `ma_cross`

Simple moving-average trend following. It goes long when the fast average is above the slow average and short when below.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline ma_cross --symbols SPY QQQ TLT --fast-window 20 --slow-window 80
```

### `ema_cross`

Exponential moving-average trend following. Similar to `ma_cross`, but more responsive to recent prices.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline ema_cross --symbols SPY QQQ TLT --ema-fast-window 12 --ema-slow-window 48
```

### `rsi_mean_reversion`

Buys oversold RSI and sells overbought RSI. Best for range-bound assets, risky in persistent trends.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline rsi_mean_reversion --symbols SPY QQQ IWM --rsi-window 14 --lower-entry 30 --upper-entry 70
```

## Intermediate Strategies

### `sma_deviation`

Fades statistically large deviations from a moving average using a rolling z-score.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline sma_deviation --symbols SPY QQQ IWM --sma-window 40 --z-entry 1.25 --z-exit 0.25
```

### `stochastic_oscillator`

Looks at where price sits inside a recent high-low range. It buys low-channel exhaustion and sells high-channel exhaustion.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline stochastic_oscillator --symbols SPY QQQ IWM --stochastic-window 14 --stochastic-smooth-window 3
```

### `bollinger_mean_reversion`

Fades moves outside rolling volatility bands and exits near the center.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline bollinger_mean_reversion --symbols SPY QQQ IWM --bollinger-window 20 --bollinger-num-std 2
```

### `macd_trend`

Trades MACD histogram direction after signal-line confirmation.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline macd_trend --symbols SPY QQQ TLT --macd-fast-window 12 --macd-slow-window 26 --macd-signal-window 9
```

### `donchian_breakout`

Enters when price breaks a prior high or low channel and exits on a shorter opposite channel.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline donchian_breakout --symbols GLD TLT XLE --breakout-window 55 --breakout-exit-window 20
```

## Advanced Strategies

### `keltner_breakout`

Volatility-adjusted channel breakout using an EMA centerline and close-to-close ATR proxy.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline keltner_breakout --symbols SPY QQQ GLD --keltner-window 40 --keltner-atr-multiplier 1.5
```

### `volatility_target_trend`

Trend following that scales exposure toward a target annualized volatility.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline volatility_target_trend --symbols SPY QQQ TLT GLD --trend-window 120 --volatility-window 20 --target-volatility 0.15
```

### `time_series_momentum`

Multi-horizon momentum vote. Default horizons are roughly one, three, six, and twelve months.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline time_series_momentum --symbols SPY QQQ TLT GLD --momentum-lookbacks 21 63 126 252
```

### `adaptive_regime`

Switches between trend following and mean reversion based on training-window trend strength and volatility.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline adaptive_regime --symbols SPY QQQ IWM --regime-fast-window 30 --regime-slow-window 120 --regime-volatility-quantile 0.70
```

## Core Portfolio Sleeves

### `etf_trend`

The best first serious sleeve. It ranks liquid ETFs by momentum, applies a trend filter, and allocates across the strongest candidates.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline etf_trend --symbols SPY QQQ IWM DIA TLT IEF GLD XLE XLF XLK XLV
```

### `stat_arb`

Sector-neutral residual stat-arb with classic pairs as a sub-sleeve. This is more complex and needs very careful data, borrow, and cost controls.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline stat_arb --sector-map examples\sector_map.sample.json
```

### `edgar_event`

Event-driven drift sleeve using standardized event files or SEC company facts.

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --pipeline edgar_event --symbols AAPL MSFT NVDA --event-file examples\events.sample.csv
```

## Paper Config Example

Any directional strategy can be added to `examples/paper_deployment.sample.json`:

```json
{
  "name": "vol_target_trend_shadow",
  "pipeline": "volatility_target_trend",
  "symbols": ["SPY", "QQQ", "TLT", "GLD"],
  "lookback_bars": 360,
  "params": {
    "trend_window": 120,
    "volatility_window": 20,
    "target_volatility": 0.15
  }
}
```
