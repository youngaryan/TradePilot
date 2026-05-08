from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd


BUY = "buy"
SELL = "sell"


@dataclass(frozen=True)
class LedgerConfig:
    initial_cash: float = 100_000.0
    execution_mode: str = "next_bar_close"
    bars_per_year: int = 252
    commission_bps: float = 0.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    market_impact_bps: float = 0.0
    risk_free_rate: float = 0.0
    allow_short: bool = True
    min_order_notional: float = 1e-8
    partial_fills_enabled: bool = False


@dataclass(frozen=True)
class Order:
    id: str
    timestamp: pd.Timestamp
    symbol: str
    side: str
    quantity: float
    order_type: str = "market"
    status: str = "submitted"
    reason: str | None = None
    target_weight: float | None = None


@dataclass(frozen=True)
class Fill:
    id: str
    order_id: str
    timestamp: pd.Timestamp
    symbol: str
    side: str
    quantity: float
    price: float
    raw_price: float
    commission: float
    slippage: float
    spread_cost: float
    cash_delta: float
    position_quantity: float
    average_cost: float
    realized_pnl: float


@dataclass(frozen=True)
class ClosedTrade:
    id: str
    symbol: str
    side: str
    entry_timestamp: pd.Timestamp
    exit_timestamp: pd.Timestamp
    quantity: float
    entry_price: float
    exit_price: float
    entry_commission: float
    exit_commission: float
    pnl: float
    return_pct: float
    holding_period_bars: int


@dataclass(frozen=True)
class CashLedgerEntry:
    timestamp: pd.Timestamp
    kind: str
    amount: float
    cash_after: float
    symbol: str | None = None
    fill_id: str | None = None


@dataclass(frozen=True)
class PositionSnapshot:
    timestamp: pd.Timestamp
    symbol: str
    quantity: float
    mark_price: float | None
    market_value: float
    average_cost: float
    unrealized_pnl: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: pd.Timestamp
    cash: float
    holdings_value: float
    long_market_value: float
    short_market_value: float
    portfolio_value: float
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float
    gross_exposure: float
    net_exposure: float
    turnover: float
    order_count: int
    fill_count: int


@dataclass
class PositionState:
    quantity: float = 0.0
    average_cost: float = 0.0
    opened_at: pd.Timestamp | None = None
    opened_bar: int | None = None
    open_commission: float = 0.0
    realized_pnl: float = 0.0

    def market_value(self, price: float | None) -> float:
        if price is None or not np.isfinite(price):
            return 0.0
        return self.quantity * float(price)

    def unrealized_pnl(self, price: float | None) -> float:
        if price is None or not np.isfinite(price) or self.quantity == 0.0:
            return 0.0
        if self.quantity > 0:
            return (float(price) - self.average_cost) * abs(self.quantity)
        return (self.average_cost - float(price)) * abs(self.quantity)

    def apply_fill(
        self,
        *,
        timestamp: pd.Timestamp,
        bar_number: int,
        signed_quantity: float,
        price: float,
        commission: float,
        trade_id_start: int,
    ) -> tuple[list[ClosedTrade], float]:
        if signed_quantity == 0.0:
            return [], 0.0

        prior_quantity = self.quantity
        fill_abs = abs(signed_quantity)
        remaining_quantity = prior_quantity + signed_quantity
        closed_trades: list[ClosedTrade] = []
        realized_from_fill = 0.0

        same_direction = prior_quantity == 0.0 or np.sign(prior_quantity) == np.sign(signed_quantity)
        if same_direction:
            new_abs = abs(prior_quantity) + fill_abs
            if new_abs > 0:
                self.average_cost = ((abs(prior_quantity) * self.average_cost) + (fill_abs * price)) / new_abs
            if prior_quantity == 0.0:
                self.opened_at = timestamp
                self.opened_bar = bar_number
            self.quantity = remaining_quantity
            self.open_commission += commission
            return closed_trades, realized_from_fill

        closing_abs = min(abs(prior_quantity), fill_abs)
        closing_commission = commission * (closing_abs / fill_abs) if fill_abs else 0.0
        entry_commission = self.open_commission * (closing_abs / abs(prior_quantity)) if prior_quantity else 0.0
        if prior_quantity > 0:
            gross_pnl = (price - self.average_cost) * closing_abs
            side = "long"
        else:
            gross_pnl = (self.average_cost - price) * closing_abs
            side = "short"
        pnl = gross_pnl - entry_commission - closing_commission
        realized_from_fill += pnl
        self.realized_pnl += pnl
        capital_at_risk = self.average_cost * closing_abs + entry_commission
        return_pct = pnl / capital_at_risk if capital_at_risk > 0 else 0.0
        closed_trades.append(
            ClosedTrade(
                id=f"trade-{trade_id_start}",
                symbol="",
                side=side,
                entry_timestamp=self.opened_at or timestamp,
                exit_timestamp=timestamp,
                quantity=closing_abs,
                entry_price=self.average_cost,
                exit_price=price,
                entry_commission=entry_commission,
                exit_commission=closing_commission,
                pnl=pnl,
                return_pct=return_pct,
                holding_period_bars=max(0, bar_number - int(self.opened_bar or bar_number)),
            )
        )

        self.open_commission = max(0.0, self.open_commission - entry_commission)
        if abs(remaining_quantity) < 1e-12:
            self.quantity = 0.0
            self.average_cost = 0.0
            self.opened_at = None
            self.opened_bar = None
            self.open_commission = 0.0
            return closed_trades, realized_from_fill

        if np.sign(remaining_quantity) == np.sign(prior_quantity):
            self.quantity = remaining_quantity
            return closed_trades, realized_from_fill

        opening_abs = abs(remaining_quantity)
        opening_commission = commission - closing_commission
        self.quantity = remaining_quantity
        self.average_cost = price
        self.opened_at = timestamp
        self.opened_bar = bar_number
        self.open_commission = opening_commission
        return closed_trades, realized_from_fill


@dataclass
class LedgerBacktestResult:
    snapshots: pd.DataFrame
    position_snapshots: pd.DataFrame
    benchmark_snapshots: pd.DataFrame
    orders: list[Order] = field(default_factory=list)
    rejected_orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    trades: list[ClosedTrade] = field(default_factory=list)
    cash_ledger: list[CashLedgerEntry] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    config: LedgerConfig = field(default_factory=LedgerConfig)

    def payload(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "orders": [_record_to_dict(order) for order in self.orders],
            "rejected_orders": [_record_to_dict(order) for order in self.rejected_orders],
            "fills": [_record_to_dict(fill) for fill in self.fills],
            "trades": [_record_to_dict(trade) for trade in self.trades],
            "cash_ledger": [_record_to_dict(entry) for entry in self.cash_ledger],
            "metrics": self.metrics,
        }


def _record_to_dict(record: Any) -> dict[str, Any]:
    payload = asdict(record)
    for key, value in list(payload.items()):
        if isinstance(value, pd.Timestamp):
            payload[key] = value.isoformat()
    return payload


def _numeric_prices(prices: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(str(prices.name or "price"))
    frame = prices.apply(pd.to_numeric, errors="coerce").sort_index()
    return frame.loc[:, frame.notna().sum(axis=0) > 0]


def extract_target_weights(strategy_frame: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    price_symbols = {str(column) for column in prices.columns}
    targets = pd.DataFrame(0.0, index=strategy_frame.index, columns=sorted(price_symbols), dtype=float)

    explicit_columns = [column for column in strategy_frame.columns if str(column).startswith("target_weight_")]
    for column in explicit_columns:
        symbol = str(column).removeprefix("target_weight_")
        if symbol in targets.columns:
            targets[symbol] = pd.to_numeric(strategy_frame[column], errors="coerce").fillna(0.0)

    weight_columns = [column for column in strategy_frame.columns if str(column).startswith("weight_")]
    for column in weight_columns:
        symbol = str(column).removeprefix("weight_")
        if symbol in targets.columns and f"target_weight_{symbol}" not in strategy_frame.columns:
            targets[symbol] = pd.to_numeric(strategy_frame[column], errors="coerce").fillna(0.0)

    if (targets.abs().sum(axis=1) == 0.0).all() and len(price_symbols) == 1:
        symbol = next(iter(price_symbols))
        position = pd.to_numeric(strategy_frame.get("position", 0.0), errors="coerce").fillna(0.0)
        signal = pd.to_numeric(strategy_frame.get("signal", 0.0), errors="coerce").fillna(0.0)
        targets[symbol] = np.sign(signal).replace({-0.0: 0.0}) * position.abs()

    return targets.replace([np.inf, -np.inf], np.nan).fillna(0.0)


class LedgerBacktestSimulator:
    def __init__(self, config: LedgerConfig = LedgerConfig()) -> None:
        self.config = config
        self._order_id = 1
        self._fill_id = 1
        self._trade_id = 1

    def run(self, *, strategy_frame: pd.DataFrame, prices: pd.DataFrame | pd.Series) -> LedgerBacktestResult:
        price_frame = _numeric_prices(prices)
        if strategy_frame.empty or price_frame.empty:
            empty = pd.DataFrame(index=strategy_frame.index)
            return LedgerBacktestResult(
                snapshots=empty,
                position_snapshots=pd.DataFrame(),
                benchmark_snapshots=empty,
                config=self.config,
            )
        if strategy_frame.index.has_duplicates:
            raise ValueError("Ledger simulation requires unique timestamps. Fix overlapping folds before accounting.")

        index = pd.DatetimeIndex(strategy_frame.index).sort_values()
        aligned_prices = price_frame.reindex(index)
        mark_prices = aligned_prices.ffill()
        target_weights = extract_target_weights(strategy_frame.reindex(index), price_frame)

        positions = {symbol: PositionState() for symbol in target_weights.columns}
        cash = float(self.config.initial_cash)
        total_fees = 0.0
        orders: list[Order] = []
        rejected_orders: list[Order] = []
        fills: list[Fill] = []
        trades: list[ClosedTrade] = []
        cash_ledger = [CashLedgerEntry(timestamp=index[0], kind="initial_cash", amount=cash, cash_after=cash)]
        snapshots: list[PortfolioSnapshot] = []
        position_snapshots: list[PositionSnapshot] = []

        for bar_number, timestamp in enumerate(index):
            executable_prices = aligned_prices.loc[timestamp]
            marks = mark_prices.loc[timestamp]
            fills_before = len(fills)
            orders_before = len(orders) + len(rejected_orders)

            target_row: pd.Series | None = None
            if self.config.execution_mode == "close_to_close":
                target_row = target_weights.loc[timestamp]
            elif self.config.execution_mode in {"next_bar_close", "next_open"}:
                if bar_number > 0:
                    target_row = target_weights.iloc[bar_number - 1]
            else:
                raise ValueError(f"Unsupported execution mode: {self.config.execution_mode}")

            if target_row is not None:
                cash, total_fees = self._rebalance(
                    timestamp=timestamp,
                    bar_number=bar_number,
                    target_weights=target_row,
                    executable_prices=executable_prices,
                    mark_prices=marks,
                    positions=positions,
                    cash=cash,
                    total_fees=total_fees,
                    orders=orders,
                    rejected_orders=rejected_orders,
                    fills=fills,
                    trades=trades,
                    cash_ledger=cash_ledger,
                )

            snapshot = self._snapshot(
                timestamp=timestamp,
                positions=positions,
                mark_prices=marks,
                cash=cash,
                total_fees=total_fees,
                turnover_notional=sum(abs(fill.quantity * fill.raw_price) for fill in fills[fills_before:]),
                order_count=(len(orders) + len(rejected_orders)) - orders_before,
                fill_count=len(fills) - fills_before,
            )
            snapshots.append(snapshot)
            position_snapshots.extend(self._position_snapshots(timestamp, positions, marks))

        snapshots_frame = pd.DataFrame([asdict(snapshot) for snapshot in snapshots]).set_index("timestamp")
        position_frame = pd.DataFrame([asdict(snapshot) for snapshot in position_snapshots])
        benchmark = build_buy_and_hold_benchmark(price_frame.reindex(index), config=self.config)
        metrics = calculate_ledger_metrics(
            snapshots=snapshots_frame,
            benchmark=benchmark,
            trades=trades,
            fills=fills,
            bars_per_year=self.config.bars_per_year,
            risk_free_rate=self.config.risk_free_rate,
            initial_cash=self.config.initial_cash,
        )
        for trade_index, trade in enumerate(trades):
            if not trade.symbol:
                trades[trade_index] = ClosedTrade(**{**asdict(trade), "symbol": ""})
        return LedgerBacktestResult(
            snapshots=snapshots_frame,
            position_snapshots=position_frame,
            benchmark_snapshots=benchmark,
            orders=orders,
            rejected_orders=rejected_orders,
            fills=fills,
            trades=trades,
            cash_ledger=cash_ledger,
            metrics=metrics,
            config=self.config,
        )

    def _rebalance(
        self,
        *,
        timestamp: pd.Timestamp,
        bar_number: int,
        target_weights: pd.Series,
        executable_prices: pd.Series,
        mark_prices: pd.Series,
        positions: dict[str, PositionState],
        cash: float,
        total_fees: float,
        orders: list[Order],
        rejected_orders: list[Order],
        fills: list[Fill],
        trades: list[ClosedTrade],
        cash_ledger: list[CashLedgerEntry],
    ) -> tuple[float, float]:
        portfolio_value = cash + sum(state.market_value(_safe_price(mark_prices.get(symbol))) for symbol, state in positions.items())
        planned: list[tuple[str, float, float]] = []
        for symbol, target_weight in target_weights.items():
            raw_price = _safe_price(executable_prices.get(symbol))
            mark_price = _safe_price(mark_prices.get(symbol))
            if raw_price is None or raw_price <= 0:
                if abs(float(target_weight)) > 0:
                    rejected_orders.append(self._new_order(timestamp, symbol, BUY, 0.0, "rejected", "missing_or_invalid_fill_price", float(target_weight)))
                continue
            current_value = positions[symbol].quantity * float(mark_price if mark_price is not None else raw_price)
            target_value = float(target_weight) * portfolio_value
            quantity = (target_value - current_value) / raw_price
            if abs(quantity * raw_price) < self.config.min_order_notional:
                continue
            planned.append((symbol, quantity, float(target_weight)))

        planned.sort(key=lambda item: item[1] > 0.0)
        for symbol, quantity, target_weight in planned:
            side = BUY if quantity > 0 else SELL
            if not self.config.allow_short and positions[symbol].quantity + quantity < -1e-12:
                rejected_orders.append(self._new_order(timestamp, symbol, side, quantity, "rejected", "short_sales_disabled", target_weight))
                continue
            raw_price = _safe_price(executable_prices.get(symbol))
            if raw_price is None or raw_price <= 0 or not np.isfinite(quantity):
                rejected_orders.append(self._new_order(timestamp, symbol, side, quantity, "rejected", "invalid_quantity_or_price", target_weight))
                continue

            fill_price = _execution_price(raw_price, side, self.config)
            commission = abs(quantity * fill_price) * (self.config.commission_bps / 10_000.0)
            cash_delta = -quantity * fill_price - commission
            if cash + cash_delta < -1e-9 and quantity > 0:
                commission_rate = self.config.commission_bps / 10_000.0
                affordable_quantity = cash / (fill_price * (1.0 + commission_rate)) if fill_price > 0 else 0.0
                if abs(float(target_weight)) <= 1.0 + 1e-9 and affordable_quantity * raw_price >= self.config.min_order_notional:
                    quantity = affordable_quantity
                    commission = abs(quantity * fill_price) * commission_rate
                    cash_delta = -quantity * fill_price - commission
                else:
                    rejected_orders.append(self._new_order(timestamp, symbol, side, quantity, "rejected", "insufficient_cash", target_weight))
                    continue

            order = self._new_order(timestamp, symbol, side, quantity, "filled", None, target_weight)
            orders.append(order)
            state = positions[symbol]
            closed, realized = state.apply_fill(
                timestamp=timestamp,
                bar_number=bar_number,
                signed_quantity=quantity,
                price=fill_price,
                commission=commission,
                trade_id_start=self._trade_id,
            )
            fixed_closed: list[ClosedTrade] = []
            for trade in closed:
                fixed = ClosedTrade(**{**asdict(trade), "id": f"trade-{self._trade_id}", "symbol": symbol})
                self._trade_id += 1
                fixed_closed.append(fixed)
            trades.extend(fixed_closed)
            cash += cash_delta
            total_fees += commission
            fill = Fill(
                id=f"fill-{self._fill_id}",
                order_id=order.id,
                timestamp=timestamp,
                symbol=symbol,
                side=side,
                quantity=abs(quantity),
                price=fill_price,
                raw_price=raw_price,
                commission=commission,
                slippage=abs(quantity) * raw_price * ((self.config.slippage_bps + self.config.market_impact_bps) / 10_000.0),
                spread_cost=abs(quantity) * raw_price * ((self.config.spread_bps / 2.0) / 10_000.0),
                cash_delta=cash_delta,
                position_quantity=state.quantity,
                average_cost=state.average_cost,
                realized_pnl=realized,
            )
            fills.append(fill)
            cash_ledger.append(CashLedgerEntry(timestamp=timestamp, kind="fill", amount=cash_delta, cash_after=cash, symbol=symbol, fill_id=fill.id))
            self._fill_id += 1
        return cash, total_fees

    def _new_order(
        self,
        timestamp: pd.Timestamp,
        symbol: str,
        side: str,
        quantity: float,
        status: str,
        reason: str | None,
        target_weight: float | None,
    ) -> Order:
        order = Order(
            id=f"order-{self._order_id}",
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            quantity=abs(float(quantity)) if np.isfinite(quantity) else 0.0,
            status=status,
            reason=reason,
            target_weight=target_weight,
        )
        self._order_id += 1
        return order

    def _snapshot(
        self,
        *,
        timestamp: pd.Timestamp,
        positions: dict[str, PositionState],
        mark_prices: pd.Series,
        cash: float,
        total_fees: float,
        turnover_notional: float,
        order_count: int,
        fill_count: int,
    ) -> PortfolioSnapshot:
        market_values = [state.market_value(_safe_price(mark_prices.get(symbol))) for symbol, state in positions.items()]
        holdings_value = float(sum(market_values))
        long_market_value = float(sum(value for value in market_values if value > 0))
        short_market_value = float(sum(value for value in market_values if value < 0))
        portfolio_value = cash + holdings_value
        unrealized = float(sum(state.unrealized_pnl(_safe_price(mark_prices.get(symbol))) for symbol, state in positions.items()))
        realized = float(sum(state.realized_pnl for state in positions.values()))
        gross_notional = long_market_value + abs(short_market_value)
        denominator = abs(portfolio_value) if abs(portfolio_value) > 1e-12 else 1.0
        return PortfolioSnapshot(
            timestamp=timestamp,
            cash=cash,
            holdings_value=holdings_value,
            long_market_value=long_market_value,
            short_market_value=short_market_value,
            portfolio_value=portfolio_value,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_fees=total_fees,
            gross_exposure=gross_notional / denominator,
            net_exposure=holdings_value / denominator,
            turnover=turnover_notional / denominator,
            order_count=order_count,
            fill_count=fill_count,
        )

    @staticmethod
    def _position_snapshots(timestamp: pd.Timestamp, positions: dict[str, PositionState], mark_prices: pd.Series) -> list[PositionSnapshot]:
        snapshots: list[PositionSnapshot] = []
        for symbol, state in positions.items():
            price = _safe_price(mark_prices.get(symbol))
            snapshots.append(
                PositionSnapshot(
                    timestamp=timestamp,
                    symbol=symbol,
                    quantity=state.quantity,
                    mark_price=price,
                    market_value=state.market_value(price),
                    average_cost=state.average_cost,
                    unrealized_pnl=state.unrealized_pnl(price),
                )
            )
        return snapshots


def _safe_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if np.isfinite(price) and price > 0 else None


def _execution_price(raw_price: float, side: str, config: LedgerConfig) -> float:
    bps = (config.spread_bps / 2.0) + config.slippage_bps + config.market_impact_bps
    adjustment = bps / 10_000.0
    if side == BUY:
        return raw_price * (1.0 + adjustment)
    return raw_price * (1.0 - adjustment)


def build_buy_and_hold_benchmark(prices: pd.DataFrame, *, config: LedgerConfig) -> pd.DataFrame:
    price_frame = _numeric_prices(prices)
    if price_frame.empty:
        return pd.DataFrame(index=prices.index if hasattr(prices, "index") else None)
    index = pd.DatetimeIndex(price_frame.index)
    first_prices = price_frame.iloc[0].dropna()
    valid_symbols = [symbol for symbol, price in first_prices.items() if _safe_price(price) is not None]
    cash = float(config.initial_cash)
    quantities = pd.Series(0.0, index=price_frame.columns, dtype=float)
    total_fees = 0.0
    if valid_symbols:
        cash_per_symbol = cash / len(valid_symbols)
        for symbol in valid_symbols:
            raw_price = float(first_prices[symbol])
            fill_price = _execution_price(raw_price, BUY, config)
            commission_rate = config.commission_bps / 10_000.0
            quantity = cash_per_symbol / (fill_price * (1.0 + commission_rate))
            commission = quantity * fill_price * commission_rate
            cash -= quantity * fill_price + commission
            total_fees += commission
            quantities.loc[symbol] = quantity

    marks = price_frame.ffill()
    rows: list[dict[str, Any]] = []
    for timestamp, row in marks.iterrows():
        holdings_value = float((quantities * row.fillna(0.0)).sum())
        value = cash + holdings_value
        rows.append(
            {
                "timestamp": timestamp,
                "benchmark_value": value,
                "benchmark_cash": cash,
                "benchmark_holdings_value": holdings_value,
                "benchmark_total_fees": total_fees,
            }
        )
    frame = pd.DataFrame(rows).set_index("timestamp")
    frame["benchmark_return"] = frame["benchmark_value"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    frame["benchmark_equity"] = frame["benchmark_value"] / max(config.initial_cash, 1e-12)
    frame["benchmark_drawdown"] = frame["benchmark_equity"] / frame["benchmark_equity"].cummax().replace(0.0, np.nan) - 1.0
    return frame


def calculate_ledger_metrics(
    *,
    snapshots: pd.DataFrame,
    benchmark: pd.DataFrame,
    trades: Iterable[ClosedTrade],
    fills: Iterable[Fill],
    bars_per_year: int,
    risk_free_rate: float,
    initial_cash: float,
) -> dict[str, Any]:
    if snapshots.empty or "portfolio_value" not in snapshots.columns:
        return {}
    values = pd.to_numeric(snapshots["portfolio_value"], errors="coerce").ffill().dropna()
    returns = values.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    equity = values / max(initial_cash, 1e-12)
    drawdown = equity / equity.cummax().replace(0.0, np.nan) - 1.0
    total_return = float(values.iloc[-1] / max(initial_cash, 1e-12) - 1.0)
    years = _calendar_years(values.index, bars_per_year=bars_per_year)
    cagr = float((values.iloc[-1] / max(initial_cash, 1e-12)) ** (1.0 / years) - 1.0) if values.iloc[-1] > 0 else -1.0
    excess = returns - (risk_free_rate / max(bars_per_year, 1))
    volatility = float(returns.std(ddof=0) * np.sqrt(max(bars_per_year, 1)))
    sharpe = float(excess.mean() / returns.std(ddof=0) * np.sqrt(max(bars_per_year, 1))) if returns.std(ddof=0) > 0 else 0.0
    downside = excess[excess < 0.0]
    downside_std = float(downside.std(ddof=0))
    sortino = float(excess.mean() / downside_std * np.sqrt(max(bars_per_year, 1))) if downside_std > 0 else 0.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    closed_trades = list(trades)
    wins = [trade.pnl for trade in closed_trades if trade.pnl > 0]
    losses = [trade.pnl for trade in closed_trades if trade.pnl < 0]
    profit_factor = float(sum(wins) / abs(sum(losses))) if losses else None
    benchmark_return = 0.0
    alpha = None
    beta = None
    if not benchmark.empty and "benchmark_value" in benchmark.columns:
        benchmark_values = pd.to_numeric(benchmark["benchmark_value"], errors="coerce").reindex(values.index).ffill()
        benchmark_return = float(benchmark_values.iloc[-1] / max(benchmark_values.iloc[0], 1e-12) - 1.0)
        benchmark_returns = benchmark_values.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        variance = float(benchmark_returns.var(ddof=0))
        if variance > 0:
            beta = float(returns.cov(benchmark_returns) / variance)
            alpha = float((returns.mean() - beta * benchmark_returns.mean()) * max(bars_per_year, 1))
    fill_notional = sum(abs(fill.quantity * fill.raw_price) for fill in fills)
    avg_value = float(values.mean()) if not values.empty else initial_cash
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_return": cagr,
        "annualized_vol": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": cagr / abs(max_drawdown) if max_drawdown < 0 else None,
        "win_rate": len(wins) / len(closed_trades) if closed_trades else 0.0,
        "profit_factor": profit_factor,
        "average_win": float(np.mean(wins)) if wins else 0.0,
        "average_loss": float(np.mean(losses)) if losses else 0.0,
        "exposure_time": float((pd.to_numeric(snapshots["gross_exposure"], errors="coerce").fillna(0.0) > 0.0).mean()),
        "turnover": fill_notional / max(avg_value, 1e-12),
        "alpha": alpha,
        "beta": beta,
        "benchmark_total_return": benchmark_return,
        "benchmark_relative_return": total_return - benchmark_return,
        "drawdown_duration": _max_drawdown_duration(drawdown),
        "closed_trade_count": len(closed_trades),
        "fill_count": len(list(fills)),
        "initial_cash": initial_cash,
        "final_value": float(values.iloc[-1]),
    }


def _calendar_years(index: pd.Index, *, bars_per_year: int) -> float:
    if len(index) < 2:
        return 1.0 / max(bars_per_year, 1)
    start = pd.Timestamp(index[0])
    end = pd.Timestamp(index[-1])
    days = max((end - start).total_seconds() / 86_400.0, 0.0)
    if days > 0:
        return max(days / 365.25, 1.0 / max(bars_per_year, 1))
    return max(len(index) / max(bars_per_year, 1), 1.0 / max(bars_per_year, 1))


def _max_drawdown_duration(drawdown: pd.Series) -> int:
    max_duration = 0
    current = 0
    for value in drawdown.fillna(0.0):
        if value < 0.0:
            current += 1
            max_duration = max(max_duration, current)
        else:
            current = 0
    return int(max_duration)
