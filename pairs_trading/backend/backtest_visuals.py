from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def _iso_timestamp(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(None)
    return timestamp.isoformat()


def _series_or_default(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce").fillna(default)
    return pd.Series(default, index=frame.index, dtype=float)


def _as_numeric_frame(prices: Any) -> pd.DataFrame:
    if isinstance(prices, pd.Series):
        return prices.to_frame("price").sort_index()
    if not isinstance(prices, pd.DataFrame) or prices.empty:
        return pd.DataFrame()
    numeric = prices.apply(pd.to_numeric, errors="coerce").sort_index()
    numeric = numeric.loc[:, numeric.notna().sum(axis=0) > 1]
    return numeric


def _primary_symbol(prices: pd.DataFrame, requested: str | None = None) -> str | None:
    if prices.empty:
        return None
    if requested and requested in prices.columns and prices[requested].notna().sum() > 1:
        return requested
    scored = [
        (str(column), int(prices[column].notna().sum()))
        for column in prices.columns
        if prices[column].notna().sum() > 1
    ]
    if not scored:
        return None
    return max(scored, key=lambda item: item[1])[0]


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative_strength = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    return rsi.fillna(50.0)


def _baseline_returns(prices: pd.DataFrame, index: pd.Index) -> tuple[pd.Series, str]:
    if prices.empty or len(index) == 0:
        return pd.Series(0.0, index=index, dtype=float), "Buy and hold"
    aligned = prices.reindex(index).ffill()
    valid_columns = [column for column in aligned.columns if aligned[column].notna().sum() > 1]
    if not valid_columns:
        return pd.Series(0.0, index=index, dtype=float), "Buy and hold"
    returns = aligned[valid_columns].pct_change().replace([np.inf, -np.inf], np.nan).mean(axis=1).fillna(0.0)
    if len(returns) > 0:
        returns.iloc[0] = 0.0
    label = "Equal-weight buy and hold" if len(valid_columns) > 1 else f"{valid_columns[0]} buy and hold"
    return returns, label


def _equity_from_returns(returns: pd.Series) -> pd.Series:
    return (1.0 + returns.fillna(0.0)).cumprod()


def _drawdown(equity: pd.Series) -> pd.Series:
    if equity.empty:
        return equity
    return equity / equity.cummax().replace(0.0, np.nan) - 1.0


def _annualized_return(equity: pd.Series, bars_per_year: int) -> float:
    if equity.empty:
        return 0.0
    ending = _safe_float(equity.iloc[-1], 1.0) or 1.0
    years = max(len(equity) / max(bars_per_year, 1), 1 / max(bars_per_year, 1))
    if ending <= 0:
        return -1.0
    return float(ending ** (1.0 / years) - 1.0)


def _sharpe(returns: pd.Series, bars_per_year: int) -> float:
    clean = returns.fillna(0.0)
    vol = float(clean.std(ddof=0))
    if vol <= 0.0:
        return 0.0
    return float(clean.mean() / vol * np.sqrt(max(bars_per_year, 1)))


def _profit_factor(returns: pd.Series) -> float | None:
    clean = returns.fillna(0.0)
    gains = float(clean[clean > 0.0].sum())
    losses = float(clean[clean < 0.0].sum())
    if losses == 0.0:
        return None
    return gains / abs(losses)


def _signed_exposure(frame: pd.DataFrame) -> pd.Series:
    weight_columns = [column for column in frame.columns if str(column).startswith("weight_")]
    if weight_columns:
        return frame[weight_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)
    position = _series_or_default(frame, "position", 0.0)
    signal = _series_or_default(frame, "signal", 0.0)
    return np.sign(signal).replace({-0.0: 0.0}) * position.abs()


def _trade_events_and_summary(
    frame: pd.DataFrame,
    *,
    strategy_equity: pd.Series,
    baseline_equity: pd.Series,
    price: pd.Series | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if frame.empty:
        return [], []

    exposure = _signed_exposure(frame)
    events: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    previous_signed = 0.0
    previous_side = 0
    event_id = 1
    trade_id = 1
    threshold = 1e-5

    def price_at(timestamp: Any) -> float | None:
        if price is None or timestamp not in price.index:
            return None
        return _safe_float(price.loc[timestamp], None)

    def add_event(timestamp: Any, event_type: str, side: int, label: str, delta: float = 0.0, pnl: float | None = None, return_pct: float | None = None) -> None:
        nonlocal event_id
        events.append(
            {
                "id": f"event-{event_id}",
                "timestamp": _iso_timestamp(timestamp),
                "type": event_type,
                "side": "long" if side > 0 else "short" if side < 0 else "flat",
                "label": label,
                "exposure": _safe_float(exposure.loc[timestamp], 0.0),
                "exposure_change": float(delta),
                "price": price_at(timestamp),
                "strategy_equity": _safe_float(strategy_equity.loc[timestamp], None),
                "baseline_equity": _safe_float(baseline_equity.loc[timestamp], None),
                "pnl": pnl,
                "return_pct": return_pct,
            }
        )
        event_id += 1

    def close_active(timestamp: Any, status: str) -> None:
        nonlocal active, trade_id
        if active is None:
            return
        entry_equity = float(active["entry_equity"])
        exit_equity = float(_safe_float(strategy_equity.loc[timestamp], entry_equity) or entry_equity)
        return_pct = exit_equity / entry_equity - 1.0 if entry_equity else 0.0
        pnl = exit_equity - entry_equity
        start_position = frame.index.get_loc(active["entry_raw_timestamp"])
        end_position = frame.index.get_loc(timestamp)
        trade = {
            "id": f"trade-{trade_id}",
            "side": active["side"],
            "entry_timestamp": active["entry_timestamp"],
            "exit_timestamp": _iso_timestamp(timestamp) if status == "closed" else None,
            "entry_price": active["entry_price"],
            "exit_price": price_at(timestamp) if status == "closed" else None,
            "entry_equity": entry_equity,
            "exit_equity": exit_equity if status == "closed" else None,
            "pnl": pnl,
            "return_pct": return_pct,
            "holding_period_bars": max(0, int(end_position - start_position)),
            "status": status,
        }
        trades.append(trade)
        if status == "closed":
            add_event(timestamp, "exit", -1 if active["side"] == "long" else 1, "Exit", pnl=pnl, return_pct=return_pct)
        trade_id += 1
        active = None

    for timestamp, signed_value in exposure.items():
        signed = float(_safe_float(signed_value, 0.0) or 0.0)
        side = 1 if signed > threshold else -1 if signed < -threshold else 0
        delta = signed - previous_signed

        if previous_side != 0 and side != previous_side:
            close_active(timestamp, "closed")

        if side != 0 and (previous_side == 0 or side != previous_side):
            active = {
                "entry_raw_timestamp": timestamp,
                "entry_timestamp": _iso_timestamp(timestamp),
                "entry_price": price_at(timestamp),
                "entry_equity": float(_safe_float(strategy_equity.loc[timestamp], 1.0) or 1.0),
                "side": "long" if side > 0 else "short",
            }
            add_event(timestamp, "entry", side, "Entry", delta=delta)
        elif side == 0 and previous_side != 0:
            pass
        elif abs(delta) >= 0.10 and side != 0:
            add_event(timestamp, "buy" if delta > 0 else "sell", side, "Increase" if abs(signed) > abs(previous_signed) else "Reduce", delta=delta)

        previous_signed = signed
        previous_side = side

    if active is not None:
        close_active(frame.index[-1], "open")

    return events[-250:], trades[-250:]


def _sample_positions(index: pd.Index, max_points: int, preserve: Iterable[Any] = ()) -> list[int]:
    count = len(index)
    if count <= max_points:
        return list(range(count))
    positions = set(np.linspace(0, count - 1, max_points, dtype=int).tolist())
    lookup = {value: idx for idx, value in enumerate(index)}
    for timestamp in preserve:
        if timestamp in lookup:
            positions.add(lookup[timestamp])
    return sorted(positions)


def _ledger_trade_events_and_summary(
    *,
    ledger: dict[str, Any],
    strategy_equity: pd.Series,
    baseline_equity: pd.Series,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fills = ledger.get("fills", []) if isinstance(ledger, dict) else []
    trades = ledger.get("trades", []) if isinstance(ledger, dict) else []
    events: list[dict[str, Any]] = []
    for index, fill in enumerate(fills[-250:], start=max(1, len(fills) - 249)):
        timestamp = pd.Timestamp(fill.get("timestamp"))
        side = str(fill.get("side") or "")
        events.append(
            {
                "id": fill.get("id") or f"fill-{index}",
                "timestamp": _iso_timestamp(timestamp),
                "type": "buy" if side == "buy" else "sell",
                "side": "long" if side == "buy" else "short",
                "label": "Buy fill" if side == "buy" else "Sell fill",
                "exposure": None,
                "exposure_change": None,
                "price": _safe_float(fill.get("price"), None),
                "strategy_equity": _safe_float(strategy_equity.reindex([timestamp]).ffill().iloc[-1], None) if not strategy_equity.empty else None,
                "baseline_equity": _safe_float(baseline_equity.reindex([timestamp]).ffill().iloc[-1], None) if not baseline_equity.empty else None,
                "quantity": _safe_float(fill.get("quantity"), None),
                "commission": _safe_float(fill.get("commission"), None),
                "pnl": _safe_float(fill.get("realized_pnl"), None),
                "return_pct": None,
            }
        )

    summary: list[dict[str, Any]] = []
    for index, trade in enumerate(trades[-250:], start=max(1, len(trades) - 249)):
        summary.append(
            {
                "id": trade.get("id") or f"trade-{index}",
                "symbol": trade.get("symbol"),
                "side": trade.get("side"),
                "entry_timestamp": trade.get("entry_timestamp"),
                "exit_timestamp": trade.get("exit_timestamp"),
                "entry_price": _safe_float(trade.get("entry_price"), None),
                "exit_price": _safe_float(trade.get("exit_price"), None),
                "quantity": _safe_float(trade.get("quantity"), None),
                "pnl": _safe_float(trade.get("pnl"), None),
                "return_pct": _safe_float(trade.get("return_pct"), None),
                "holding_period_bars": int(trade.get("holding_period_bars") or 0),
                "status": "closed",
                "entry_commission": _safe_float(trade.get("entry_commission"), None),
                "exit_commission": _safe_float(trade.get("exit_commission"), None),
            }
        )
    return events, summary


def build_backtest_visualization(
    *,
    equity_curve: Any,
    prices: Any,
    ledger: dict[str, Any] | None = None,
    bars_per_year: int = 252,
    completed_folds: int | None = None,
    total_folds: int | None = None,
    status: str = "completed",
    primary_symbol: str | None = None,
    max_points: int = 1200,
) -> dict[str, Any]:
    if not isinstance(equity_curve, pd.DataFrame) or equity_curve.empty:
        return {
            "status": status,
            "completed_folds": int(completed_folds or 0),
            "total_folds": int(total_folds or 0),
            "primary_symbol": primary_symbol,
            "baseline_label": "Buy and hold",
            "equity": [],
            "price": [],
            "indicators": [],
            "trade_events": [],
            "trade_summary": [],
            "metrics": {},
            "sampled": False,
            "source_points": 0,
        }

    raw_frame = equity_curve.copy()
    ledger_config = ledger.get("config", {}) if isinstance(ledger, dict) else {}
    initial_cash = float(ledger_config.get("initial_cash") or 1.0)
    if "portfolio_value" in raw_frame.columns:
        portfolio_value = pd.to_numeric(raw_frame["portfolio_value"], errors="coerce").ffill()
        raw_returns = portfolio_value.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        raw_strategy_equity = portfolio_value / max(initial_cash, 1e-12)
    else:
        raw_returns = _series_or_default(raw_frame, "net_return", 0.0)
        raw_strategy_equity = _equity_from_returns(raw_returns)
    raw_strategy_drawdown = _drawdown(raw_strategy_equity)
    frame = raw_frame.assign(_strategy_equity=raw_strategy_equity, _strategy_drawdown=raw_strategy_drawdown).sort_index(kind="mergesort")
    if frame.index.has_duplicates:
        frame = frame.groupby(level=0, sort=True).last()
    net_returns = _series_or_default(frame, "net_return", 0.0)
    strategy_equity = pd.to_numeric(frame["_strategy_equity"], errors="coerce").ffill().fillna(1.0)
    strategy_drawdown = pd.to_numeric(frame["_strategy_drawdown"], errors="coerce").ffill().fillna(0.0)

    price_frame = _as_numeric_frame(prices)
    if "benchmark_equity" in frame.columns:
        baseline_equity = pd.to_numeric(frame["benchmark_equity"], errors="coerce").ffill().fillna(1.0)
        baseline_returns = pd.to_numeric(frame.get("benchmark_return", baseline_equity.pct_change()), errors="coerce").fillna(0.0)
        baseline_drawdown = pd.to_numeric(frame.get("benchmark_drawdown", _drawdown(baseline_equity)), errors="coerce").fillna(0.0)
        baseline_label = "Fixed-share buy and hold"
    else:
        baseline_returns, baseline_label = _baseline_returns(price_frame, frame.index)
        baseline_equity = _equity_from_returns(baseline_returns)
        baseline_drawdown = _drawdown(baseline_equity)

    symbol = _primary_symbol(price_frame, primary_symbol)
    primary_price = None
    indicator_source = pd.Series(dtype=float)
    if symbol and symbol in price_frame.columns:
        indicator_source = price_frame[symbol].ffill()
        primary_price = indicator_source.reindex(frame.index).ffill()

    if isinstance(ledger, dict) and ledger.get("fills") is not None:
        trade_events, trade_summary = _ledger_trade_events_and_summary(
            ledger=ledger,
            strategy_equity=strategy_equity,
            baseline_equity=baseline_equity,
        )
    else:
        trade_events, trade_summary = _trade_events_and_summary(
            frame,
            strategy_equity=strategy_equity,
            baseline_equity=baseline_equity,
            price=primary_price,
        )
    event_timestamps = [pd.Timestamp(event["timestamp"]) for event in trade_events]
    sample = _sample_positions(frame.index, max_points=max_points, preserve=event_timestamps)
    sampled_index = frame.index.take(sample)

    close = indicator_source.reindex(sampled_index).ffill() if not indicator_source.empty else pd.Series(dtype=float)
    full_close = indicator_source if not indicator_source.empty else pd.Series(index=frame.index, dtype=float)
    full_returns = full_close.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    sma_20 = full_close.rolling(20, min_periods=3).mean()
    sma_50 = full_close.rolling(50, min_periods=5).mean()
    sma_200 = full_close.rolling(200, min_periods=20).mean()
    rsi = _rsi(full_close) if not full_close.empty else pd.Series(index=frame.index, dtype=float)
    macd_fast = full_close.ewm(span=12, adjust=False, min_periods=12).mean()
    macd_slow = full_close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = macd_fast - macd_slow
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_histogram = macd - macd_signal
    realized_volatility = full_returns.rolling(20, min_periods=5).std(ddof=0) * np.sqrt(max(bars_per_year, 1))

    equity_points: list[dict[str, Any]] = []
    price_points: list[dict[str, Any]] = []
    indicator_points: list[dict[str, Any]] = []
    forecast_series = _series_or_default(frame, "forecast", 0.0)
    signal_series = _series_or_default(frame, "signal", 0.0)
    position_series = _series_or_default(frame, "position", 0.0)
    turnover_series = _series_or_default(frame, "turnover", 0.0)
    gross_exposure_series = _series_or_default(frame, "gross_exposure", 0.0)
    risk_scale_series = _series_or_default(frame, "risk_scale", 1.0)
    sentiment_strength_series = _series_or_default(frame, "sentiment_strength", 0.0)
    sentiment_confidence_series = _series_or_default(frame, "sentiment_confidence", 0.0)

    for timestamp in sampled_index:
        equity_points.append(
            {
                "timestamp": _iso_timestamp(timestamp),
                "equity": _safe_float(strategy_equity.loc[timestamp], 1.0),
                "drawdown": _safe_float(strategy_drawdown.loc[timestamp], 0.0),
                "net_return": _safe_float(net_returns.loc[timestamp], 0.0),
                "baseline_equity": _safe_float(baseline_equity.loc[timestamp], 1.0),
                "baseline_drawdown": _safe_float(baseline_drawdown.loc[timestamp], 0.0),
                "baseline_return": _safe_float(baseline_returns.loc[timestamp], 0.0),
            }
        )
        price_points.append(
            {
                "timestamp": _iso_timestamp(timestamp),
                "close": _safe_float(close.loc[timestamp], None) if timestamp in close.index else None,
                "sma_20": _safe_float(sma_20.reindex([timestamp]).iloc[0], None) if not sma_20.empty else None,
                "sma_50": _safe_float(sma_50.reindex([timestamp]).iloc[0], None) if not sma_50.empty else None,
                "sma_200": _safe_float(sma_200.reindex([timestamp]).iloc[0], None) if not sma_200.empty else None,
            }
        )
        indicator_points.append(
            {
                "timestamp": _iso_timestamp(timestamp),
                "forecast": _safe_float(forecast_series.loc[timestamp], 0.0),
                "signal": _safe_float(signal_series.loc[timestamp], 0.0),
                "position": _safe_float(position_series.loc[timestamp], 0.0),
                "turnover": _safe_float(turnover_series.loc[timestamp], 0.0),
                "gross_exposure": _safe_float(gross_exposure_series.loc[timestamp], 0.0),
                "risk_scale": _safe_float(risk_scale_series.loc[timestamp], 1.0),
                "rsi": _safe_float(rsi.reindex([timestamp]).iloc[0], None) if not rsi.empty else None,
                "macd": _safe_float(macd.reindex([timestamp]).iloc[0], None) if not macd.empty else None,
                "macd_signal": _safe_float(macd_signal.reindex([timestamp]).iloc[0], None) if not macd_signal.empty else None,
                "macd_histogram": _safe_float(macd_histogram.reindex([timestamp]).iloc[0], None) if not macd_histogram.empty else None,
                "realized_volatility": _safe_float(realized_volatility.reindex([timestamp]).iloc[0], None) if not realized_volatility.empty else None,
                "strategy_drawdown": _safe_float(strategy_drawdown.loc[timestamp], 0.0),
                "baseline_drawdown": _safe_float(baseline_drawdown.loc[timestamp], 0.0),
                "sentiment_strength": _safe_float(sentiment_strength_series.loc[timestamp], 0.0),
                "sentiment_confidence": _safe_float(sentiment_confidence_series.loc[timestamp], 0.0),
            }
        )

    strategy_total_return = float(strategy_equity.iloc[-1] - 1.0)
    baseline_total_return = float(baseline_equity.iloc[-1] - 1.0) if not baseline_equity.empty else 0.0
    metrics = {
        "total_return": strategy_total_return,
        "cagr": _annualized_return(strategy_equity, bars_per_year),
        "sharpe": _sharpe(raw_returns, bars_per_year),
        "max_drawdown": float(raw_strategy_drawdown.min()) if not raw_strategy_drawdown.empty else 0.0,
        "win_rate": float((raw_returns > 0.0).mean()) if len(raw_returns) else 0.0,
        "profit_factor": _profit_factor(raw_returns),
        "baseline_total_return": baseline_total_return,
        "baseline_cagr": _annualized_return(baseline_equity, bars_per_year),
        "baseline_sharpe": _sharpe(baseline_returns, bars_per_year),
        "baseline_max_drawdown": float(baseline_drawdown.min()) if not baseline_drawdown.empty else 0.0,
        "benchmark_outperformance": strategy_total_return - baseline_total_return,
        "completed_folds": int(completed_folds or 0),
        "total_folds": int(total_folds or 0),
    }
    if isinstance(ledger, dict) and isinstance(ledger.get("metrics"), dict):
        ledger_metrics = dict(ledger["metrics"])
        metrics.update(ledger_metrics)
        if "benchmark_total_return" in ledger_metrics:
            metrics["baseline_total_return"] = ledger_metrics.get("benchmark_total_return")
        if "benchmark_relative_return" in ledger_metrics:
            metrics["benchmark_outperformance"] = ledger_metrics.get("benchmark_relative_return")

    return {
        "status": status,
        "completed_folds": int(completed_folds or 0),
        "total_folds": int(total_folds or 0),
        "primary_symbol": symbol,
        "baseline_label": baseline_label,
        "equity": equity_points,
        "price": price_points,
        "indicators": indicator_points,
        "trade_events": trade_events,
        "trade_summary": trade_summary,
        "metrics": metrics,
        "sampled": len(frame.index) > len(sampled_index),
        "source_points": int(len(frame.index)),
    }
