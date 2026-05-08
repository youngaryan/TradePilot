# Ledger-Driven Backtesting

The backtest engine accounts for strategy results from portfolio ledger records rather than inferred return series.

## Accounting Contract

For every portfolio snapshot:

```text
portfolio_value = cash + sum(position_quantity * current_mark_price)
```

Fills debit or credit cash immediately. Commission, spread, slippage, and market-impact adjustments are reflected in fill price or commission and therefore affect cash and portfolio value. Closed-trade P&L is calculated from actual entry and exit fills using average-cost accounting.

## Execution Timing

The default execution mode is `next_bar_close`: target weights emitted for timestamp `t` are converted to market orders at the next available bar. This prevents using the same close both to create a signal and to fill the order. `close_to_close` is available for research compatibility, but it should be treated as a less conservative mode.

The current market data provider supplies adjusted close prices, not full OHLCV bars. Until open/high/low/volume data is available, `next_open` is treated as next-bar close and partial fills are disabled.

## Benchmark

The benchmark is a separate fixed-share buy-and-hold ledger. It uses the same initial cash, timestamps, fill-price adjustment, and commission assumptions as the strategy ledger. Multi-asset benchmarks buy equal notional allocations at the first backtest timestamp and do not rebalance.

## Walk-Forward Aggregation

Ledger accounting rejects overlapping out-of-sample windows. Set `step_bars >= test_bars`, plus any purge/embargo needed for the experiment. This avoids double-counting returns and ledger events.

## Current Limitations

- Partial fills are disabled because volume is not available in the current close-price data model.
- Margin, settlement, and short-borrow availability are simplified.
- `next_open` requires OHLC data before it can be distinct from next-bar-close execution.
- Strategies that do not emit symbol-level `target_weight_*` columns can only be ledger-accounted when their `weight_*` columns map directly to traded symbols or when they are single-symbol outputs.
